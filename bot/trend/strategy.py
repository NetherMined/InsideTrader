"""Trend sleeve rules as pure functions, shared by the backtest and the live paper runner.

Every 5th day (counted from EPOCH) the sleeve looks at each coin's last completed daily close:
long if it is above the close LOOKBACK days earlier, otherwise flat. A long position is sized to
equity / N x min(MAX_SCALE, VOL_TARGET / annualised 30-day volatility) and held at that quantity
until the next rebalance (or until its disaster stop is hit).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

MAJORS: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT",
)
EPOCH = date(2021, 1, 1)
HOLD_DAYS = 5
LOOKBACK_DAYS = 28
VOL_WINDOW = 30
VOL_TARGET = 0.40
MAX_SCALE = 2.0
STOP_PCT = 0.40        # disaster stop below entry; sits inside the ~-45% liquidation of a 2x isolated long
KILL_DRAWDOWN = 0.35   # close everything when equity is this far below its peak


def is_rebalance_day(signal_day: date) -> bool:
    """True when the daily candle closing on `signal_day` is a rebalance close."""
    return (signal_day - EPOCH).days % HOLD_DAYS == 0


def momentum(closes: Sequence[float], lookback: int = LOOKBACK_DAYS) -> float | None:
    """Return over `lookback` days ending at the last close, or None without enough history."""
    if len(closes) <= lookback or closes[-1 - lookback] <= 0:
        return None
    return closes[-1] / closes[-1 - lookback] - 1


def annual_vol(closes: Sequence[float], window: int = VOL_WINDOW) -> float | None:
    """Annualised sample std of the last `window` daily returns (365-day year)."""
    if len(closes) <= window:
        return None
    tail = closes[-window - 1:]
    rets = [tail[i] / tail[i - 1] - 1 for i in range(1, len(tail))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(365)


def target_weight(closes: Sequence[float], n_coins: int = len(MAJORS)) -> float:
    """Fraction of equity to hold long in one coin (0 when flat or history is too short)."""
    mom, vol = momentum(closes), annual_vol(closes)
    if mom is None or vol is None or vol <= 0 or mom <= 0:
        return 0.0
    return min(MAX_SCALE, VOL_TARGET / vol) / n_coins


def target_weights(closes_by_symbol: Mapping[str, Sequence[float]]) -> dict[str, float]:
    """Target weight per symbol of the universe; symbols without data get 0."""
    return {s: target_weight(closes_by_symbol.get(s, ())) for s in MAJORS}


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str        # "BUY" or "SELL"
    qty: float
    notional: float  # qty x reference price


def orders_for(
    targets: Mapping[str, float],
    equity: float,
    prices: Mapping[str, float],
    positions: Mapping[str, float],
    min_notional: Mapping[str, float] | None = None,
) -> list[Order]:
    """Orders that move current quantities to target weights at the given prices.

    A target below the symbol's exchange minimum notional is treated as flat, so the sleeve never
    holds a position it could not open on Binance.
    """
    min_notional = min_notional or {}
    orders = []
    for sym in sorted(set(targets) | set(positions)):
        price = prices.get(sym)
        if not price:
            continue
        want = targets.get(sym, 0.0) * equity
        if want < min_notional.get(sym, 0.0):
            want = 0.0
        delta = want / price - positions.get(sym, 0.0)
        if abs(delta) * price < 1e-9:
            continue
        side = "BUY" if delta > 0 else "SELL"
        orders.append(Order(sym, side, abs(delta), abs(delta) * price))
    return orders


def stop_price(entry_price: float) -> float:
    return entry_price * (1 - STOP_PCT)


def kill_switch_hit(equity: float, peak_equity: float) -> bool:
    return peak_equity > 0 and equity <= peak_equity * (1 - KILL_DRAWDOWN)
