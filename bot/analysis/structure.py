"""Market structure detection — swing points, trend classification, demand/supply zones.

Identifies Higher Highs / Higher Lows (uptrend) and Lower Lows / Lower Highs
(downtrend) from OHLCV candles. A swing low is only validated once price breaks
the previous swing high (Break of Structure).

Demand zones form at confirmed higher lows during uptrends.
Supply zones form at confirmed lower highs during downtrends.
Zones are invalidated when price closes through them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger


@dataclass
class SwingPoint:
    index: int
    price: float
    time: datetime
    type: str  # "HIGH" or "LOW"


@dataclass
class StructureState:
    trend: str  # "UP", "DOWN", "RANGE"
    last_hh: float = 0.0
    last_hl: float = 0.0
    last_ll: float = 0.0
    last_lh: float = 0.0
    bos_price: float = 0.0
    bos_side: str = "NONE"  # "BULL" or "BEAR" or "NONE"
    confirmed: bool = False
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    swings_since_bos: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend": self.trend,
            "last_hh": self.last_hh,
            "last_hl": self.last_hl,
            "last_ll": self.last_ll,
            "last_lh": self.last_lh,
            "bos_price": self.bos_price,
            "bos_side": self.bos_side,
            "confirmed": self.confirmed,
            "swings_since_bos": self.swings_since_bos,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "StructureState":
        return StructureState(
            trend=d.get("trend", "RANGE"),
            last_hh=float(d.get("last_hh", 0)),
            last_hl=float(d.get("last_hl", 0)),
            last_ll=float(d.get("last_ll", 0)),
            last_lh=float(d.get("last_lh", 0)),
            bos_price=float(d.get("bos_price", 0)),
            bos_side=d.get("bos_side", "NONE"),
            confirmed=bool(d.get("confirmed", False)),
            swings_since_bos=int(d.get("swings_since_bos", 0)),
        )


@dataclass
class Zone:
    type: str  # "DEMAND" or "SUPPLY"
    top: float
    bottom: float
    origin_time: datetime
    invalidated: bool = False
    touches: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "top": self.top,
            "bottom": self.bottom,
            "origin_time": self.origin_time.isoformat(),
            "invalidated": self.invalidated,
            "touches": self.touches,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Zone":
        ot = d.get("origin_time", "")
        if isinstance(ot, str):
            try:
                ot = datetime.fromisoformat(ot.replace("Z", "+00:00"))
            except ValueError:
                ot = datetime.now(timezone.utc)
        return Zone(
            type=d.get("type", "DEMAND"),
            top=float(d.get("top", 0)),
            bottom=float(d.get("bottom", 0)),
            origin_time=ot,
            invalidated=bool(d.get("invalidated", False)),
            touches=int(d.get("touches", 0)),
        )


def find_swing_points(df: pd.DataFrame, lookback: int = 3) -> list[SwingPoint]:
    """Identify swing highs and swing lows from OHLCV data.

    A swing high: candle high > high of N candles on each side.
    A swing low: candle low < low of N candles on each side.
    """
    if len(df) < lookback * 2 + 1:
        return []

    highs = df["high"].values
    lows = df["low"].values
    times = df["open_time"].values
    points: list[SwingPoint] = []

    for i in range(lookback, len(df) - lookback):
        is_sh = True
        is_sl = True

        for j in range(1, lookback + 1):
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_sh = False
            if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                is_sl = False
            if not is_sh and not is_sl:
                break

        t = pd.Timestamp(times[i]).to_pydatetime()
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)

        if is_sh:
            points.append(SwingPoint(index=i, price=float(highs[i]), time=t, type="HIGH"))
        if is_sl:
            points.append(SwingPoint(index=i, price=float(lows[i]), time=t, type="LOW"))

    points.sort(key=lambda p: p.index)
    return points


def classify_structure(df: pd.DataFrame, lookback: int = 3) -> StructureState:
    """Classify market structure from swing points.

    Key rule: a swing low is only validated once price breaks the previous
    swing high (Break of Structure / BOS).
    """
    swings = find_swing_points(df, lookback)
    if len(swings) < 4:
        return StructureState(trend="RANGE")

    swing_highs = [s for s in swings if s.type == "HIGH"]
    swing_lows = [s for s in swings if s.type == "LOW"]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return StructureState(trend="RANGE", swing_highs=swing_highs, swing_lows=swing_lows)

    sh1, sh2 = swing_highs[-2], swing_highs[-1]
    sl1, sl2 = swing_lows[-2], swing_lows[-1]

    higher_highs = sh2.price > sh1.price
    higher_lows = sl2.price > sl1.price
    lower_lows = sl2.price < sl1.price
    lower_highs = sh2.price < sh1.price

    # BOS validation: check if the current price has broken past the reference swing
    current_close = float(df["close"].iloc[-1])

    # Bullish BOS: price breaks above previous swing high → validates the higher low
    bull_bos = current_close > sh1.price and higher_lows
    # Bearish BOS: price breaks below previous swing low → validates the lower high
    bear_bos = current_close < sl1.price and lower_highs

    swings_since = 0
    if bull_bos or bear_bos:
        bos_ref = sh1 if bull_bos else sl1
        for s in reversed(swings):
            if s.index <= bos_ref.index:
                break
            swings_since += 1

    if bull_bos:
        return StructureState(
            trend="UP",
            last_hh=sh2.price,
            last_hl=sl2.price,
            last_ll=min(sl1.price, sl2.price),
            last_lh=min(sh1.price, sh2.price),
            bos_price=sh1.price,
            bos_side="BULL",
            confirmed=True,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            swings_since_bos=swings_since,
        )

    if bear_bos:
        return StructureState(
            trend="DOWN",
            last_hh=max(sh1.price, sh2.price),
            last_hl=max(sl1.price, sl2.price),
            last_ll=sl2.price,
            last_lh=sh2.price,
            bos_price=sl1.price,
            bos_side="BEAR",
            confirmed=True,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            swings_since_bos=swings_since,
        )

    if higher_highs and higher_lows:
        return StructureState(
            trend="UP",
            last_hh=sh2.price,
            last_hl=sl2.price,
            last_ll=min(sl1.price, sl2.price),
            last_lh=min(sh1.price, sh2.price),
            bos_price=sh1.price,
            bos_side="BULL",
            confirmed=False,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
        )

    if lower_lows and lower_highs:
        return StructureState(
            trend="DOWN",
            last_hh=max(sh1.price, sh2.price),
            last_hl=max(sl1.price, sl2.price),
            last_ll=sl2.price,
            last_lh=sh2.price,
            bos_price=sl1.price,
            bos_side="BEAR",
            confirmed=False,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
        )

    return StructureState(
        trend="RANGE",
        last_hh=max(sh1.price, sh2.price),
        last_hl=max(sl1.price, sl2.price),
        last_ll=min(sl1.price, sl2.price),
        last_lh=min(sh1.price, sh2.price),
        swing_highs=swing_highs,
        swing_lows=swing_lows,
    )


def detect_zones(df: pd.DataFrame, structure: StructureState, max_zones: int = 6) -> list[Zone]:
    """Detect demand and supply zones from confirmed swing points.

    Demand zone (uptrend): candle body range of the last confirmed higher low(s).
    Supply zone (downtrend): candle body range of the last confirmed lower high(s).
    Zones are invalidated when price closes through them.
    """
    if not structure.swing_highs and not structure.swing_lows:
        return []

    zones: list[Zone] = []
    current_close = float(df["close"].iloc[-1])

    for sl_point in reversed(structure.swing_lows):
        if len(zones) >= max_zones:
            break
        idx = sl_point.index
        if idx < 0 or idx >= len(df):
            continue
        row = df.iloc[idx]
        candle_open = float(row["open"])
        candle_close = float(row["close"])
        body_top = max(candle_open, candle_close)
        body_bottom = min(candle_open, candle_close)
        zone_bottom = float(row["low"])

        invalidated = current_close < zone_bottom
        touches = 0
        for j in range(idx + 1, len(df)):
            bar_low = float(df.iloc[j]["low"])
            bar_close = float(df.iloc[j]["close"])
            if bar_low <= body_top and bar_low >= zone_bottom:
                touches += 1
            if bar_close < zone_bottom:
                invalidated = True
                break

        zones.append(Zone(
            type="DEMAND",
            top=body_top,
            bottom=zone_bottom,
            origin_time=sl_point.time,
            invalidated=invalidated,
            touches=touches,
        ))

    for sh_point in reversed(structure.swing_highs):
        if len(zones) >= max_zones * 2:
            break
        idx = sh_point.index
        if idx < 0 or idx >= len(df):
            continue
        row = df.iloc[idx]
        candle_open = float(row["open"])
        candle_close = float(row["close"])
        body_top = max(candle_open, candle_close)
        body_bottom = min(candle_open, candle_close)
        zone_top = float(row["high"])

        invalidated = current_close > zone_top
        touches = 0
        for j in range(idx + 1, len(df)):
            bar_high = float(df.iloc[j]["high"])
            bar_close = float(df.iloc[j]["close"])
            if bar_high >= body_bottom and bar_high <= zone_top:
                touches += 1
            if bar_close > zone_top:
                invalidated = True
                break

        zones.append(Zone(
            type="SUPPLY",
            top=zone_top,
            bottom=body_bottom,
            origin_time=sh_point.time,
            invalidated=invalidated,
            touches=touches,
        ))

    return zones


def calculate_zone_sl(
    entry_price: float,
    side: str,
    zones: list[Zone],
    atr: float,
    fallback_sl_pct: float = 2.0,
    max_sl_pct: float = 3.0,
) -> tuple[float, bool]:
    """Return (stop_loss_price, is_zone_based).

    For BUY: SL below nearest valid demand zone - ATR buffer.
    For SELL: SL above nearest valid supply zone + ATR buffer.
    Falls back to fixed % if no zone within range.
    """
    buffer = atr * 0.15
    max_distance = entry_price * max_sl_pct / 100

    if side == "BUY":
        candidates = [
            z for z in zones
            if z.type == "DEMAND" and not z.invalidated and z.bottom < entry_price
        ]
        candidates.sort(key=lambda z: entry_price - z.bottom)
        for z in candidates:
            sl = z.bottom - buffer
            if entry_price - sl <= max_distance and sl > 0:
                return round(sl, 8), True
        return round(entry_price * (1 - fallback_sl_pct / 100), 8), False

    else:
        candidates = [
            z for z in zones
            if z.type == "SUPPLY" and not z.invalidated and z.top > entry_price
        ]
        candidates.sort(key=lambda z: z.top - entry_price)
        for z in candidates:
            sl = z.top + buffer
            if sl - entry_price <= max_distance:
                return round(sl, 8), True
        return round(entry_price * (1 + fallback_sl_pct / 100), 8), False


def calculate_zone_tp(
    entry_price: float,
    side: str,
    zones: list[Zone],
    atr_pct: float,
    fallback_tp_pct: float = 3.0,
) -> float:
    """Return take_profit_price targeting the nearest opposing zone.

    For BUY: TP at nearest unbroken supply zone bottom.
    For SELL: TP at nearest unbroken demand zone top.
    Falls back to ATR-based TP.
    """
    if side == "BUY":
        candidates = [
            z for z in zones
            if z.type == "SUPPLY" and not z.invalidated and z.bottom > entry_price
        ]
        candidates.sort(key=lambda z: z.bottom - entry_price)
        if candidates:
            return round(candidates[0].bottom, 8)
        tp_pct = max(3.0, min(6.0, atr_pct * 1.5)) if atr_pct > 0 else fallback_tp_pct
        return round(entry_price * (1 + tp_pct / 100), 8)

    else:
        candidates = [
            z for z in zones
            if z.type == "DEMAND" and not z.invalidated and z.top < entry_price
        ]
        candidates.sort(key=lambda z: entry_price - z.top)
        if candidates:
            return round(candidates[0].top, 8)
        tp_pct = max(3.0, min(6.0, atr_pct * 1.5)) if atr_pct > 0 else fallback_tp_pct
        return round(entry_price * (1 - tp_pct / 100), 8)


def is_near_zone(price: float, zones: list[Zone], side: str, atr: float) -> bool:
    """Check if price is within 1 ATR of a relevant zone.

    BUY: near a demand zone. SELL: near a supply zone.
    """
    if atr <= 0:
        return False

    if side == "BUY":
        for z in zones:
            if z.type == "DEMAND" and not z.invalidated:
                if abs(price - z.top) <= atr or (z.bottom <= price <= z.top):
                    return True
    else:
        for z in zones:
            if z.type == "SUPPLY" and not z.invalidated:
                if abs(price - z.bottom) <= atr or (z.bottom <= price <= z.top):
                    return True

    return False


def compute_rr_ratio(entry: float, sl: float, tp: float) -> float:
    """Compute risk-to-reward ratio. Returns 0 if risk is zero."""
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return 0.0
    return round(reward / risk, 2)
