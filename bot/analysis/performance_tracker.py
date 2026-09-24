"""Per-symbol trading performance tracker with trade-outcome feedback.

Queries recent closed trades to compute granular performance metrics:
  - Per-symbol expected value (avg PnL, not just win rate)
  - Per-symbol, per-direction (BUY/SELL) win rate and EV
  - Per-regime win rate
  - Recency-weighted via exponential decay

Returns confidence adjustment factors used to boost/penalise predictions
based on actual trading outcomes.
"""

import math
from datetime import datetime, timedelta, timezone
from loguru import logger
from sqlalchemy import text

from bot.db.connection import async_session

MIN_TRADES = 5
DECAY_HALFLIFE_DAYS = 7


def _exp_decay_weight(days_ago: float) -> float:
    """Exponential decay: half-life of DECAY_HALFLIFE_DAYS days."""
    return math.exp(-0.693 * days_ago / DECAY_HALFLIFE_DAYS)


async def get_symbol_confidence_factors(lookback_days: int = 30) -> dict[str, float]:
    """Return a confidence multiplier per symbol based on recent trade outcomes.

    Uses expected value (avg PnL) and win rate with recency weighting.
    Factor range: 0.85 to 1.10 (never blocks trades entirely).
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT symbol, side, pnl_percent, closed_at,
                           regime,
                           EXTRACT(EPOCH FROM (NOW() - closed_at)) / 86400.0 AS days_ago
                    FROM trades
                    WHERE status = 'CLOSED' AND closed_at >= :since
                    ORDER BY closed_at DESC
                """),
                {"since": since},
            )
            rows = result.mappings().all()
    except Exception as e:
        logger.warning(f"performance_tracker: DB query failed - {e}")
        return {}

    symbol_data: dict[str, list] = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in symbol_data:
            symbol_data[sym] = []
        symbol_data[sym].append({
            "side": row["side"],
            "pnl_pct": float(row["pnl_percent"] or 0),
            "days_ago": max(float(row["days_ago"] or 0), 0.01),
            "regime": row["regime"] or "UNKNOWN",
        })

    factors: dict[str, float] = {}
    for sym, trades in symbol_data.items():
        if len(trades) < MIN_TRADES:
            continue

        weighted_pnl_sum = 0.0
        weight_sum = 0.0
        weighted_wins = 0.0

        for t in trades:
            w = _exp_decay_weight(t["days_ago"])
            weighted_pnl_sum += t["pnl_pct"] * w
            weight_sum += w
            if t["pnl_pct"] > 0:
                weighted_wins += w

        if weight_sum <= 0:
            continue

        weighted_ev = weighted_pnl_sum / weight_sum
        weighted_wr = weighted_wins / weight_sum

        if weighted_ev > 0.5 and weighted_wr >= 0.45:
            factor = 1.10
        elif weighted_ev > 0.0 and weighted_wr >= 0.40:
            factor = 1.05
        elif weighted_ev > -0.3:
            factor = 1.00
        elif weighted_ev > -0.8:
            factor = 0.95
        elif weighted_ev > -1.5:
            factor = 0.90
        else:
            factor = 0.85

        factors[sym] = factor
        logger.debug(
            f"perf [{sym}]: EV={weighted_ev:+.2f}% WR={weighted_wr:.0%} "
            f"({len(trades)} trades) -> factor={factor}"
        )

    return factors


async def get_direction_factors(lookback_days: int = 30) -> dict[str, dict[str, float]]:
    """Return per-symbol, per-direction confidence factors.

    Returns: {symbol: {"BUY": factor, "SELL": factor}}
    Symbols/directions with insufficient data return 1.0.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT symbol, side,
                           COUNT(*) AS total,
                           COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS wins,
                           AVG(pnl_percent) AS avg_pnl
                    FROM trades
                    WHERE status = 'CLOSED' AND closed_at >= :since
                    GROUP BY symbol, side
                """),
                {"since": since},
            )
            rows = result.mappings().all()
    except Exception as e:
        logger.warning(f"performance_tracker direction: DB query failed - {e}")
        return {}

    dir_factors: dict[str, dict[str, float]] = {}
    for row in rows:
        sym = row["symbol"]
        side = row["side"]
        total = int(row["total"])
        if total < MIN_TRADES:
            continue

        avg_pnl = float(row["avg_pnl"] or 0)
        win_rate = int(row["wins"]) / total

        if avg_pnl > 0.3 and win_rate >= 0.45:
            factor = 1.10
        elif avg_pnl > 0.0:
            factor = 1.00
        elif avg_pnl > -0.5:
            factor = 0.95
        else:
            factor = 0.85

        if sym not in dir_factors:
            dir_factors[sym] = {}
        dir_factors[sym][side] = factor

    return dir_factors


async def get_regime_factors(lookback_days: int = 30) -> dict[str, float]:
    """Return per-regime confidence factors.

    Returns: {"TRENDING": factor, "RANGING": factor, "TRANSITION": factor}
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT regime,
                           COUNT(*) AS total,
                           COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS wins,
                           AVG(pnl_percent) AS avg_pnl
                    FROM trades
                    WHERE status = 'CLOSED' AND closed_at >= :since
                      AND regime IS NOT NULL AND regime != 'UNKNOWN'
                    GROUP BY regime
                """),
                {"since": since},
            )
            rows = result.mappings().all()
    except Exception as e:
        logger.warning(f"performance_tracker regime: DB query failed - {e}")
        return {}

    regime_factors: dict[str, float] = {}
    for row in rows:
        total = int(row["total"])
        if total < MIN_TRADES:
            continue

        avg_pnl = float(row["avg_pnl"] or 0)
        win_rate = int(row["wins"]) / total

        if avg_pnl > 0.3 and win_rate >= 0.45:
            factor = 1.05
        elif avg_pnl > 0.0:
            factor = 1.00
        elif avg_pnl > -0.5:
            factor = 0.95
        else:
            factor = 0.90

        regime_factors[row["regime"]] = factor
        logger.debug(
            f"perf [regime={row['regime']}]: EV={avg_pnl:+.2f}% WR={win_rate:.0%} "
            f"({total} trades) -> factor={factor}"
        )

    return regime_factors
