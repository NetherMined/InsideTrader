"""Score a frozen 15m packet against the closed 1h candle and manager action."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import text

from bot.db.connection import async_session
from bot.research import ERRORS_KEY, dumps, review_key
from bot.research.packets import load_candles


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
