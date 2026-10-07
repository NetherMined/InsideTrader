"""Build falsifiable 15m research packets for the current 1h candle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger
from sqlalchemy import text

from bot.analysis.indicators import add_indicators, MIN_ROWS
from bot.db.connection import async_session
import json
from datetime import datetime as _dt

from bot.research import dumps, packet_hour_key, packet_key


def hour_start_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(minute=0, second=0, microsecond=0)


def last_closed_15m(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    minute = (now.minute // 15) * 15
    aligned = now.replace(minute=minute, second=0, microsecond=0)
    from datetime import timedelta
    return aligned - timedelta(minutes=15)


async def load_candles(symbol: str, timeframe: str, limit: int = 400) -> pd.DataFrame:
    async with async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY open_time DESC
                LIMIT :limit
                """
            ),
            {"symbol": symbol, "timeframe": timeframe, "limit": limit},
        )
        rows = result.mappings().all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df.sort_values("open_time").reset_index(drop=True)


def _build_from_frame(symbol: str, df_15m: pd.DataFrame, hour: datetime, now: datetime) -> dict[str, Any] | None:
    if len(df_15m) < MIN_ROWS:
        return None
    df = add_indicators(df_15m)
    row = df.dropna(subset=["ema21", "rsi", "atr", "adx"]).iloc[-1] if "ema21" in df.columns else None
    if row is None:
        return None

    hour_bars = df[df["open_time"] >= hour]
    if hour_bars.empty:
        hour_open = float(row["open"])
        hour_high = float(row["high"])
        hour_low = float(row["low"])
    else:
        hour_open = float(hour_bars.iloc[0]["open"])
        hour_high = float(hour_bars["high"].max())
        hour_low = float(hour_bars["low"].min())

    close = float(row["close"])
    ema21 = float(row["ema21"])
    rsi = float(row["rsi"])
    atr = float(row["atr"])
    adx = float(row.get("adx") or 0.0)

    swept_low = hour_low < hour_open and close > hour_low + (atr * 0.25)
    swept_high = hour_high > hour_open and close < hour_high - (atr * 0.25)

    if close >= hour_open and close >= ema21:
        side = "bull"
        setup = "15m holding above hour open and EMA21"
    elif close < hour_open and close <= ema21:
        side = "bear"
        setup = "15m holding below hour open and EMA21"
    else:
        side = "bull" if close >= ema21 else "bear"
        setup = "mixed 15m structure — EMA side only"

    if swept_low and side == "bull":
        setup = "15m sweep of hour low then reclaim"
    if swept_high and side == "bear":
        setup = "15m sweep of hour high then reject"

    remaining_frac = max(0.15, 1.0 - (now.minute / 60.0))
    band = atr * 1.3 * remaining_frac
    pred_low = hour_open - band if side == "bear" else min(close, hour_open) - band * 0.4
    pred_high = hour_open + band if side == "bull" else max(close, hour_open) + band * 0.4

    invalidation = hour_low if side == "bull" else hour_high
    invalidation_hit = close < hour_low if side == "bull" else close > hour_high

    votes = 0.0
    votes += 0.25 if (side == "bull" and close >= hour_open) or (side == "bear" and close < hour_open) else 0.0
    votes += 0.25 if (side == "bull" and close >= ema21) or (side == "bear" and close <= ema21) else 0.0
    votes += 0.20 if (side == "bull" and rsi >= 50) or (side == "bear" and rsi <= 50) else 0.0
    votes += 0.15 if adx >= 18 else 0.0
    votes += 0.15 if swept_low or swept_high else 0.0
    confidence = max(0.35, min(0.85, 0.35 + votes))

    arm = bool(confidence >= 0.55 and not invalidation_hit)

    return {
        "schema": 1,
        "symbol": symbol,
        "hour_start": hour.isoformat(),
        "packet_time": now.isoformat(),
        "pred_1h_close_side": side,
        "pred_1h_range": {"low": round(pred_low, 8), "high": round(pred_high, 8)},
        "invalidation": round(invalidation, 8),
        "confidence": round(confidence, 4),
        "setup": setup,
        "features": {
            "rsi_15m": round(rsi, 2),
            "adx_15m": round(adx, 2),
            "atr_15m": round(atr, 8),
            "ema_side": "above" if close >= ema21 else "below",
            "hour_open": hour_open,
            "sweep": "hour_low" if swept_low else ("hour_high" if swept_high else "none"),
        },
        "advice_to_manager": (
            f"if 1h confirms {side} and holds vs {invalidation:.6g}, "
            f"{'long' if side == 'bull' else 'short'} on 1h close; else stand down"
        ),
        "arm": arm,
        "invalidation_hit": invalidation_hit,
    }


async def build_packet(symbol: str, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or datetime.now(timezone.utc)
    hour = hour_start_utc(now)
    df = await load_candles(symbol, "15m", limit=400)
    try:
        return _build_from_frame(symbol, df, hour, now)
    except Exception as exc:
        logger.debug(f"{symbol}: packet build failed ({exc})")
        return None


async def persist_packet(redis, packet: dict[str, Any]) -> None:
    symbol = packet["symbol"]
    hour = packet["hour_start"]
    raw = dumps(packet)
    await redis.setex(packet_key(symbol), 4 * 3600, raw)
    await redis.setex(packet_hour_key(symbol, hour), 7 * 24 * 3600, raw)
    try:
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    text("""
                        INSERT INTO research_packets
                            (symbol, hour_start, packet_time, pred_side,
                             pred_low, pred_high, invalidation, confidence,
                             setup, features, arm, raw)
                        VALUES
                            (:symbol, :hour_start, :packet_time, :pred_side,
                             :pred_low, :pred_high, :invalidation, :confidence,
                             :setup, CAST(:features AS jsonb), :arm, CAST(:raw AS jsonb))
                        ON CONFLICT (symbol, hour_start, packet_time) DO UPDATE
                        SET pred_side = EXCLUDED.pred_side,
                            confidence = EXCLUDED.confidence,
                            arm = EXCLUDED.arm,
                            raw = EXCLUDED.raw
                    """),
                    {
                        "symbol": symbol,
                        "hour_start": _dt.fromisoformat(packet["hour_start"]),
                        "packet_time": _dt.fromisoformat(packet["packet_time"]),
                        "pred_side": packet["pred_1h_close_side"],
                        "pred_low": (packet.get("pred_1h_range") or {}).get("low"),
                        "pred_high": (packet.get("pred_1h_range") or {}).get("high"),
                        "invalidation": packet.get("invalidation"),
                        "confidence": packet["confidence"],
                        "setup": packet.get("setup"),
                        "features": json.dumps(packet.get("features")),
                        "arm": packet.get("arm", False),
                        "raw": raw,
                    },
                )
    except Exception as exc:
        logger.warning(f"{symbol}: DB persist_packet failed ({exc})")
