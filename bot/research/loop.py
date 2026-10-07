"""15m researcher loop. Feedback only. Never places or blocks orders."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from loguru import logger

from bot.config import settings
from bot.data.fetcher import fetch_recent
from bot.research import PACKET_HOUR_KEY, loads
from bot.research.packets import build_packet, hour_start_utc, persist_packet
from bot.research.patcher import apply_tighten_if_allowed, load_rules, maybe_patch, write_pending
from bot.research.scorer import (
    load_closed_1h, manager_action_for_hour, persist_review, score_packet, score_structure,
    update_setup_scorecard,
)


def _seconds_to_next_15m(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    minute = ((now.minute // 15) + 1) * 15
    if minute >= 60:
        nxt = (now + timedelta(hours=1)).replace(minute=0, second=2, microsecond=0)
    else:
        nxt = now.replace(minute=minute, second=2, microsecond=0)
    return max(1.0, (nxt - now).total_seconds())


async def _score_closed_hour(redis, pairs: list[str], closed_hour: datetime) -> None:
    reviews: list[dict] = []
    hour_iso = closed_hour.isoformat()
    for symbol in pairs:
        raw = await redis.get(PACKET_HOUR_KEY.format(symbol=symbol, hour=hour_iso))
        packet = loads(raw)
        if not packet:
            raw = await redis.get(f"research:packet:{symbol}")
            packet = loads(raw)
            if not packet or packet.get("hour_start") != hour_iso:
                continue
        actual = await load_closed_1h(symbol, closed_hour)
        if not actual:
            continue
        action = await manager_action_for_hour(symbol, closed_hour)
        review = score_packet(packet, actual, action)

        # Score trades from this hour against market structure
        if action == "enter":
            try:
                from bot.db.connection import async_session
                from sqlalchemy import text
                async with async_session() as session:
                    row = (await session.execute(
                        text("""
                            SELECT side, entry_price, stop_loss_price, take_profit_price, extra
                            FROM trades
                            WHERE symbol = :symbol
                              AND opened_at >= :start
                              AND opened_at < :start + interval '1 hour'
                            ORDER BY opened_at ASC LIMIT 1
                        """),
                        {"symbol": symbol, "start": closed_hour},
                    )).mappings().first()
                if row:
                    import json as _json
                    extra = row["extra"]
                    if isinstance(extra, str):
                        try:
                            extra = _json.loads(extra)
                        except Exception:
                            extra = {}
                    entry_ind = (extra or {}).get("entry_indicators", {})
                    struct_labels = await score_structure(
                        symbol,
                        trade_side=row["side"],
                        entry_price=float(row["entry_price"]),
                        sl_price=float(row["stop_loss_price"]),
                        tp_price=float(row["take_profit_price"]),
                        entry_indicators=entry_ind,
                    )
                    review["labels"].extend(struct_labels)
                    review["structure_labels"] = struct_labels
            except Exception as exc:
                logger.debug(f"{symbol}: structure scoring in loop failed ({exc})")

        await persist_review(redis, review)

        # Update per-setup scorecard with trade PnL if manager entered
        if action == "enter":
            try:
                from bot.db.connection import async_session
                from sqlalchemy import text as _text
                async with async_session() as _sess:
                    _trade = (await _sess.execute(
                        _text("""
                            SELECT pnl_usdt FROM trades
                            WHERE symbol = :symbol AND status = 'CLOSED'
                              AND opened_at >= :start AND opened_at < :start + interval '1 hour'
                            ORDER BY opened_at ASC LIMIT 1
                        """),
                        {"symbol": symbol, "start": closed_hour},
                    )).mappings().first()
                if _trade and _trade["pnl_usdt"] is not None:
                    await update_setup_scorecard(
                        redis, symbol, packet.get("setup", "unknown"),
                        followed=True, pnl_usdt=float(_trade["pnl_usdt"]),
                    )
            except Exception as _exc:
                logger.debug(f"{symbol}: scorecard update failed ({_exc})")

        reviews.append(review)
    if not reviews:
        return
    await write_trade_feedback(redis, reviews)


async def run_researcher_loop(
    ranked_store: dict,
    redis,
    stop_event: asyncio.Event,
) -> None:
    if not settings.research_enabled:
        logger.info("Researcher disabled by config")
        await stop_event.wait()
        return

    logger.info(
        f"Researcher started — {settings.research_timeframe} packets, "
        f"manager stays on {settings.analysis_timeframe}"
    )
    last_scored_hour: datetime | None = None

    while not stop_event.is_set():
        try:
            pairs = [p.symbol for p in ranked_store.get("pairs", [])][:40]
            if pairs:
                await fetch_recent(pairs, settings.research_timeframe)
            now = datetime.now(timezone.utc)
            hour = hour_start_utc(now)
            built = 0
            for symbol in pairs:
                packet = await build_packet(symbol, now)
                if not packet:
                    continue
                await persist_packet(redis, packet)
                built += 1
            logger.info(f"Researcher wrote {built} packets for hour {hour.isoformat()}")

            # Score the previous hour once we are inside a new hour
            closed = hour - timedelta(hours=1)
            if now.minute >= 1 and last_scored_hour != closed:
                await _score_closed_hour(redis, pairs, closed)
                last_scored_hour = closed
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Researcher loop error: {exc}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_seconds_to_next_15m())
        except asyncio.TimeoutError:
            pass


async def _scan_keys(redis, pattern: str) -> list:
    found = []
    cursor = 0
    while True:
        cursor, batch = await redis.scan(cursor=cursor, match=pattern, count=50)
        found.extend(batch)
        if cursor == 0 or cursor == "0":
            break
    return found


def _size_multiplier(match_rate: float, sample: int) -> float:
    """Throttle size when recent entries disagreed with the market. Never zero."""
    if sample < 4:
        return 1.0
    if match_rate < 0.40:
        return 0.60
    if match_rate < 0.50:
        return 0.80
    return 1.0


_SYM_KEY = "research:sym:{}"
_SYM_TTL = 24 * 3600  # per-symbol stats expire after 24h of no updates


def _symbol_multiplier(matches: int, total: int) -> float:
    """Per-symbol size multiplier based on that symbol's match history."""
    if total < 3:
        return 1.0
    rate = matches / total
    if rate < 0.45:
        return 0.50
    if rate < 0.55:
        return 0.85
    return 1.0


async def _update_symbol_stats(redis, symbol: str, matched: bool) -> float:
    """Append one entry result to the per-symbol rolling window (last 10). Returns multiplier."""
    key = _SYM_KEY.format(symbol)
    raw = await redis.get(key)
    history = json.loads(raw) if raw else {"results": []}
    history["results"].append(1 if matched else 0)
    history["results"] = history["results"][-10:]  # rolling window
    matches = sum(history["results"])
    total = len(history["results"])
    mult = _symbol_multiplier(matches, total)
    history["matches"] = matches
    history["total"] = total
    history["multiplier"] = mult
    await redis.setex(key, _SYM_TTL, json.dumps(history))
    return mult


async def get_symbol_multiplier(redis, symbol: str) -> float:
    """Read the per-symbol size multiplier. Returns 1.0 if unknown."""
    raw = await redis.get(_SYM_KEY.format(symbol))
    if not raw:
        return 1.0
    try:
        data = json.loads(raw)
        return float(data.get("multiplier", 1.0))
    except Exception:
        return 1.0


async def write_trade_feedback(redis, reviews: list[dict]) -> None:
    """Score the hour, update global + per-symbol multipliers, and log the researcher's view."""
    past = []
    sym_updates = []
    for review in reviews:
        manager = review.get("manager") or {}
        forecast = review.get("forecast") or {}
        action = manager.get("action")
        labels = review.get("labels") or []
        matched = bool(forecast.get("side_ok"))
        symbol = review.get("symbol")
        past.append({
            "symbol": symbol,
            "action": action,
            "matched_market": matched,
            "usefulness": review.get("usefulness"),
            "labels": labels[:6],
        })
        if action == "enter" and symbol:
            mult = await _update_symbol_stats(redis, symbol, matched)
            sym_updates.append((symbol, matched, mult))

    entered = [p for p in past if p.get("action") == "enter"]
    sample = len(entered)
    matched_n = sum(1 for p in entered if p["matched_market"])
    match_rate = (matched_n / sample) if sample else 1.0
    multiplier = _size_multiplier(match_rate, sample)

    forward = []
    try:
        keys = await _scan_keys(redis, "research:packet:*")
        for key in keys[:12]:
            raw = await redis.get(key)
            if not raw:
                continue
            pkt = loads(raw)
            if not pkt:
                continue
            forward.append({
                "symbol": pkt.get("symbol"),
                "market_side": pkt.get("pred_1h_close_side"),
                "setup": pkt.get("setup"),
                "confidence": pkt.get("confidence"),
            })
    except Exception as exc:
        logger.debug(f"forward feedback failed ({exc})")

    payload = {
        "role": "learning_loop",
        "blocks_entries": False,
        "match_rate": round(match_rate, 3),
        "sample": sample,
        "size_multiplier": multiplier,
        "per_symbol": {s: {"matched": m, "mult": mu} for s, m, mu in sym_updates},
        "past_vs_market": past,
        "forward": forward,
    }
    await redis.set("research:feedback", json.dumps(payload))
    await redis.setex("research:size_multiplier", 3 * 3600, str(multiplier))

    sym_log = ", ".join(f"{s}={mu:.2f}" for s, _, mu in sym_updates) if sym_updates else "none"
    logger.info(
        f"Researcher feedback: {matched_n}/{sample} matched, "
        f"global={multiplier:.2f}, per-symbol: [{sym_log}]"
    )
