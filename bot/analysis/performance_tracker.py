"""Per-symbol trading performance tracker.

Queries recent closed trades to compute per-symbol win rate and avg P&L.
Returns a confidence adjustment factor used to boost/penalise predictions
based on actual trading history.
"""

from datetime import datetime, timedelta, timezone
from loguru import logger
from sqlalchemy import text

from bot.db.connection import async_session


async def get_symbol_confidence_factors(lookback_days: int = 30) -> dict[str, float]:
    """Return a confidence multiplier per symbol based on recent trade history.

    Factors nudge ranking priority — they must not block trades entirely.
      win_rate >= 0.50 → 1.05 (slight boost)
      win_rate >= 0.35 → 1.00 (neutral)
      win_rate >= 0.20 → 0.95 (mild penalty)
      win_rate <  0.20 → 0.90 (moderate penalty)

    Symbols with fewer than 10 closed trades get 1.00 (neutral — insufficient data).
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT
                        symbol,
                        COUNT(*) AS total,
                        COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS wins
                    FROM trades
                    WHERE status = 'CLOSED' AND closed_at >= :since
                    GROUP BY symbol
                """),
                {"since": since},
            )
            rows = result.mappings().all()
    except Exception as e:
        logger.warning(f"performance_tracker: DB query failed — {e}")
        return {}

    factors: dict[str, float] = {}
    for row in rows:
        total = int(row["total"])
        if total < 10:
            continue
        win_rate = int(row["wins"]) / total
        if win_rate >= 0.50:
            factor = 1.05
        elif win_rate >= 0.35:
            factor = 1.00
        elif win_rate >= 0.20:
            factor = 0.95
        else:
            factor = 0.90
        factors[row["symbol"]] = factor
        logger.debug(
            f"perf [{row['symbol']}]: win_rate={win_rate:.0%} ({row['wins']}/{total}) → factor={factor}"
        )

    return factors
