"""Score a frozen 15m packet against the closed 1h candle and manager action."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import text

from bot.db.connection import async_session
from bot.research import ERRORS_KEY, dumps, review_key
from bot.research.packets import load_candles
from bot.analysis.structure import classify_structure, detect_zones, is_near_zone, compute_rr_ratio


async def load_closed_1h(symbol: str, hour_start: datetime) -> dict[str, float] | None:
    df = await load_candles(symbol, "1h", limit=8)
    if df.empty:
        return None
    hour_start = hour_start.replace(tzinfo=timezone.utc) if hour_start.tzinfo is None else hour_start
    match = df[df["open_time"] == hour_start]
    if match.empty:
        # last fully closed hour may be stored as previous row
        match = df[df["open_time"] <= hour_start].tail(1)
    if match.empty:
        return None
    row = match.iloc[-1]
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    return {"o": o, "h": h, "l": l, "c": c, "side": "bull" if c >= o else "bear"}


async def manager_action_for_hour(symbol: str, hour_start: datetime) -> str:
    async with async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT side, status, opened_at
                FROM trades
                WHERE symbol = :symbol
                  AND opened_at >= :start
                  AND opened_at < :start + interval '1 hour'
                ORDER BY opened_at ASC
                LIMIT 1
                """
            ),
            {"symbol": symbol, "start": hour_start},
        )
        row = result.mappings().first()
    if not row:
        return "skip"
    return "enter"


def score_packet(packet: dict[str, Any], actual: dict[str, float], manager_action: str) -> dict[str, Any]:
    pred_side = packet.get("pred_1h_close_side")
    rng = packet.get("pred_1h_range") or {}
    side_ok = pred_side == actual["side"]
    range_ok = True
    if rng.get("low") is not None:
        range_ok = range_ok and actual["l"] >= float(rng["low"]) * 0.997
    if rng.get("high") is not None:
        range_ok = range_ok and actual["h"] <= float(rng["high"]) * 1.003

    inv = packet.get("invalidation")
    invalidation_hit = False
    if inv is not None:
        if pred_side == "bull":
            invalidation_hit = actual["l"] < float(inv)
        else:
            invalidation_hit = actual["h"] > float(inv)

    followed = False
    if manager_action == "skip":
        followed = not packet.get("arm")
    elif manager_action == "enter":
        followed = bool(packet.get("arm")) and side_ok is not False

    labels: list[str] = []
    if not side_ok:
        labels.append("wrong_bias")
    if side_ok and invalidation_hit:
        labels.append("right_bias_bad_timing")
    if packet.get("arm") is False and side_ok and not invalidation_hit:
        labels.append("stop_too_tight")
    if manager_action == "skip" and packet.get("arm") and side_ok:
        labels.append("ignored_good_packet")
    if manager_action == "enter" and not side_ok:
        labels.append("chased_bad_packet")
    if manager_action == "enter" and packet.get("invalidation_hit"):
        labels.append("ignored_invalidation")

    if manager_action == "enter" and side_ok:
        usefulness = "helped"
    elif manager_action == "enter" and not side_ok:
        usefulness = "hurt"
    elif manager_action == "skip" and side_ok and packet.get("arm"):
        usefulness = "missed"
    else:
        usefulness = "neutral"

    return {
        "schema": 1,
        "symbol": packet.get("symbol"),
        "hour_start": packet.get("hour_start"),
        "actual_1h": actual,
        "forecast": {
            "side_ok": side_ok,
            "range_ok": range_ok,
            "invalidation_hit": invalidation_hit,
        },
        "manager": {
            "action": manager_action,
            "followed_advice": followed,
        },
        "labels": labels,
        "usefulness": usefulness,
        "patch": None,
    }


async def score_structure(
    symbol: str,
    trade_side: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    entry_indicators: dict,
) -> list[str]:
    """Score a closed trade against market structure. Returns structure labels."""
    labels: list[str] = []
    try:
        df = await load_candles(symbol, "1h", limit=200)
        if df is None or len(df) < 30:
            return labels
        state = classify_structure(df, lookback=3)
        zones = detect_zones(df, state)

        atr_col = df["high"] - df["low"]
        atr_val = float(atr_col.rolling(14).mean().iloc[-1]) if len(df) >= 14 else 0.0

        if atr_val > 0 and is_near_zone(entry_price, zones, trade_side, atr_val):
            labels.append("entered_at_zone")
        elif atr_val > 0:
            labels.append("entered_away_from_zone")

        if state.trend != "RANGE":
            struct_side = "BUY" if state.trend == "UP" else "SELL"
            if trade_side == struct_side:
                labels.append("structure_agreed")
            else:
                labels.append("structure_disagreed")

        rr = compute_rr_ratio(entry_price, sl_price, tp_price)
        if rr >= 2.5:
            labels.append("rr_honoured")
        else:
            labels.append("rr_violated")

        zone_sl = entry_indicators.get("zone_based_sl", False)
        if zone_sl:
            current_close = float(df["close"].iloc[-1])
            if trade_side == "BUY":
                sl_held = current_close > sl_price
            else:
                sl_held = current_close < sl_price
            labels.append("zone_sl_held" if sl_held else "zone_sl_failed")

        if state.confirmed:
            if state.bos_side == "BULL" and trade_side == "BUY":
                labels.append("bos_confirmed_after_entry")
            elif state.bos_side == "BEAR" and trade_side == "SELL":
                labels.append("bos_confirmed_after_entry")
            elif state.bos_side == "BULL" and trade_side == "SELL":
                labels.append("bos_against_after_entry")
            elif state.bos_side == "BEAR" and trade_side == "BUY":
                labels.append("bos_against_after_entry")
    except Exception as exc:
        logger.debug(f"{symbol}: structure scoring failed ({exc})")

    return labels


_SCORECARD_KEY = "research:scorecard:{}:{}"
_SCORECARD_TTL = 7 * 24 * 3600


async def update_setup_scorecard(redis, symbol: str, setup: str, followed: bool, pnl_usdt: float) -> None:
    """Track per-symbol per-setup PnL for followed vs skipped packets (rolling 50)."""
    import json as _json
    key = _SCORECARD_KEY.format(symbol, setup or "unknown")
    raw = await redis.get(key)
    data = _json.loads(raw) if raw else {"followed": [], "skipped": []}
    bucket = "followed" if followed else "skipped"
    data[bucket].append(round(pnl_usdt, 4))
    data[bucket] = data[bucket][-50:]
    await redis.setex(key, _SCORECARD_TTL, _json.dumps(data))


async def get_setup_scorecard(redis, symbol: str, setup: str) -> dict:
    """Return scorecard for this symbol+setup. override_allowed only when sample>=30 and followed beats skipped."""
    import json as _json
    key = _SCORECARD_KEY.format(symbol, setup or "unknown")
    raw = await redis.get(key)
    if not raw:
        return {"override_allowed": False, "sample": 0}
    data = _json.loads(raw)
    followed = data.get("followed", [])
    skipped = data.get("skipped", [])
    sample = len(followed)
    followed_avg = sum(followed) / len(followed) if followed else 0.0
    skipped_avg = sum(skipped) / len(skipped) if skipped else 0.0
    override_allowed = sample >= 30 and followed_avg > skipped_avg
    return {
        "override_allowed": override_allowed,
        "sample": sample,
        "followed_avg_pnl": round(followed_avg, 4),
        "skipped_avg_pnl": round(skipped_avg, 4),
    }


async def persist_review(redis, review: dict[str, Any]) -> None:
    hour = review.get("hour_start")
    symbol = review.get("symbol")
    if not hour or not symbol:
        return
    await redis.setex(review_key(symbol, hour), 14 * 24 * 3600, dumps(review))
    raw = await redis.lrange(ERRORS_KEY, 0, 0)
    try:
        await redis.lpush(ERRORS_KEY, dumps({"symbol": symbol, "hour": hour, "labels": review.get("labels")}))
        await redis.ltrim(ERRORS_KEY, 0, 199)
    except Exception as exc:
        logger.debug(f"error roll write failed: {exc}")
        _ = raw
    try:
        import json
        forecast = review.get("forecast") or {}
        manager = review.get("manager") or {}
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        INSERT INTO hour_reviews
                            (symbol, hour_start, side_ok, range_ok,
                             invalidation_hit, manager_action, followed_advice,
                             labels, usefulness, actual)
                        VALUES
                            (:symbol, :hour_start, :side_ok, :range_ok,
                             :invalidation_hit, :manager_action, :followed_advice,
                             CAST(:labels AS jsonb), :usefulness, CAST(:actual AS jsonb))
                        ON CONFLICT (symbol, hour_start) DO UPDATE
                        SET side_ok = EXCLUDED.side_ok,
                            range_ok = EXCLUDED.range_ok,
                            labels = EXCLUDED.labels,
                            usefulness = EXCLUDED.usefulness,
                            actual = EXCLUDED.actual
                    """),
                    {
                        "symbol": symbol,
                        "hour_start": datetime.fromisoformat(hour) if isinstance(hour, str) else hour,
                        "side_ok": forecast.get("side_ok"),
                        "range_ok": forecast.get("range_ok"),
                        "invalidation_hit": forecast.get("invalidation_hit"),
                        "manager_action": manager.get("action"),
                        "followed_advice": manager.get("followed_advice"),
                        "labels": json.dumps(review.get("labels")),
                        "usefulness": review.get("usefulness"),
                        "actual": json.dumps(review.get("actual_1h")),
                    },
                )
    except Exception as exc:
        logger.warning(f"{symbol}: DB persist_review failed ({exc})")
