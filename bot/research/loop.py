"""15m researcher loop. Never places orders."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger

from bot.config import settings
from bot.data.fetcher import fetch_recent
from bot.research import PACKET_HOUR_KEY, loads
from bot.research.packets import build_packet, hour_start_utc, persist_packet
from bot.research.patcher import apply_tighten_if_allowed, load_rules, maybe_patch, write_pending
from bot.research.scorer import load_closed_1h, manager_action_for_hour, persist_review, score_packet, score_structure


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
        reviews.append(review)
    if not reviews:
        return
    rules = await load_rules(redis)
    patch = maybe_patch(reviews, rules)
    if patch:
        patch = await apply_tighten_if_allowed(redis, patch, rules)
        await write_pending(redis, patch)
        logger.info(f"Researcher patch {patch.get('status')}: {patch.get('because')}")


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
