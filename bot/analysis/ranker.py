"""Pair profitability ranker.

Scores all analysed symbols by opportunity using:
  score = predicted_change_pct * confidence / max(atr_pct, 0.1)

Higher score = higher expected return relative to volatility risk.

Also applies a real Pearson correlation filter (30-day 1h returns from Binance candle data)
to avoid opening highly correlated pairs simultaneously.
"""

import asyncio
import json
from dataclasses import dataclass
from loguru import logger

import pandas as pd
from sqlalchemy import text

from bot.db.connection import async_session as _db_session

CORRELATION_THRESHOLD = 0.75  # Pairs above this Pearson r are filtered


@dataclass
class RankedPair:
    symbol: str
    score: float
    predicted_change_pct: float
    confidence: float
    atr_pct: float
    adx: float
    mode: str
    rsi: float = 50.0
    bb_pct: float = 0.5
    bb_width_pct: float = 0.05
    funding_rate: float = 0.0
    funding_side_to_collect: str = "NONE"


async def _fetch_close_rows(symbols: list[str]) -> list:
    """Query 30-day 1h close prices for all symbols from the Binance candle store."""
    if not symbols:
        return []
    placeholders = ", ".join(f":s{i}" for i in range(len(symbols)))
    params = {f"s{i}": sym for i, sym in enumerate(symbols)}
    async with _db_session() as session:
        result = await session.execute(
            text(f"""
                SELECT symbol, open_time, close
                FROM candles
                WHERE symbol IN ({placeholders})
                  AND timeframe = '1h'
                  AND open_time >= NOW() - INTERVAL '30 days'
                ORDER BY open_time ASC
            """),
            params,
        )
        return result.fetchall()


def _compute_correlation_matrix(rows: list) -> dict[str, dict[str, float]]:
    """Build Pearson correlation matrix from raw (symbol, open_time, close) rows."""
    df = pd.DataFrame(rows, columns=["symbol", "open_time", "close"])
    pivot = df.pivot_table(index="open_time", columns="symbol", values="close", aggfunc="last")
    returns = pivot.pct_change().dropna(how="all")
    corr = returns.corr(method="pearson")
    out: dict[str, dict[str, float]] = {}
    for s1 in corr.columns:
        out[s1] = {}
        for s2 in corr.columns:
            val = corr.loc[s1, s2]
            out[s1][s2] = float(val) if not pd.isna(val) else 0.0
    return out


async def fetch_pair_correlations(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Compute 30-day Pearson return correlations for a list of Binance USDT pairs.

    Returns a dict: symbol -> {other_symbol: pearson_r}
    Uses 1h close returns from the local candle DB (populated by the fetcher).
    CPU-bound pandas step runs in a thread to avoid blocking the event loop.
    """
    if len(symbols) < 2:
        return {}
    try:
        rows = await _fetch_close_rows(symbols)
        if not rows:
            return {}
        return await asyncio.to_thread(_compute_correlation_matrix, rows)
    except Exception as e:
        logger.warning(f"Correlation matrix computation failed: {e}")
        return {}


def filter_correlated_pairs(
    ranked_pairs: list[RankedPair],
    max_concurrent: int = 3,
    correlation_threshold: float = CORRELATION_THRESHOLD,
    correlation_matrix: dict[str, dict[str, float]] | None = None,
) -> list[RankedPair]:
    """Filter highly correlated pairs, keeping the highest-scoring from each group.

    Uses the real Pearson correlation matrix when available; falls back to
    same-base-asset exclusion (e.g. BTC/USDT vs BTCDOM/USDT) when not.
    """
    if not ranked_pairs:
        return []

    filtered = []
    excluded_symbols: set[str] = set()

    for pair in ranked_pairs:
        if pair.symbol in excluded_symbols:
            continue
        filtered.append(pair)
        for other in ranked_pairs:
            if other.symbol == pair.symbol or other.symbol in excluded_symbols:
                continue
            if correlation_matrix:
                r = correlation_matrix.get(pair.symbol, {}).get(other.symbol, 0.0)
                correlated = r >= correlation_threshold  # Only filter positive correlation; negative = diversification
            else:
                correlated = pair.symbol.split("/")[0] == other.symbol.split("/")[0]
            if correlated:
                excluded_symbols.add(other.symbol)
                logger.debug(f"Excluded {other.symbol} (r={correlation_matrix.get(pair.symbol, {}).get(other.symbol, '?'):.2f} with {pair.symbol})" if correlation_matrix else f"Excluded {other.symbol} (same base as {pair.symbol})")

    return filtered[:max_concurrent] if len(filtered) > max_concurrent else filtered


def rank_pairs(
    predictions: list[dict],
    max_concurrent: int = 3,
    funding_rates: dict[str, dict] | None = None,
) -> list[RankedPair]:
    """Sort predictions by opportunity score descending.

    Args:
        predictions: List of prediction dicts with symbol, predicted_change_pct,
                     confidence, atr_pct, adx, mode, rsi
        max_concurrent: Unused — kept for API compatibility. Correlation filtering
                        is done in the executor with the live setting.
        funding_rates: Optional dict from fetch_funding_rates() to annotate pairs

    Returns:
        All ranked RankedPair objects sorted by score descending (no cap).
    """
    ranked = []
    for p in predictions:
        change = p.get("predicted_change_pct", 0.0)
        confidence = p.get("confidence", 0.0)
        atr_pct = max(p.get("atr_pct", 0.1), 0.1)

        if confidence <= 0:
            continue

        score = (abs(change) * confidence) / atr_pct

        # Adjust score based on funding rate (bonus for collecting funding)
        symbol = p.get("symbol", "")
        funding_bonus = 0.0
        if funding_rates and symbol in funding_rates:
            fr_data = funding_rates[symbol]
            funding_rate = fr_data.get("funding_rate", 0.0)
            side_to_collect = fr_data.get("side_to_collect", "NONE")
            funding_bonus = abs(funding_rate) * 1000  # Small bonus for funding collection
            if side_to_collect != "NONE":
                score += funding_bonus  # Boost score for funding-collecting side

        ranked.append(
            RankedPair(
                symbol=p["symbol"],
                score=score,
                predicted_change_pct=change,
                confidence=confidence,
                atr_pct=atr_pct,
                adx=p.get("adx", 0.0),
                mode=p.get("mode", "SPOT"),
                rsi=p.get("rsi", 50.0),
                bb_pct=p.get("bb_pct", 0.5),
                bb_width_pct=p.get("bb_width_pct", 0.05),
                funding_rate=funding_rates.get(symbol, {}).get("funding_rate", 0.0) if funding_rates else 0.0,
                funding_side_to_collect=funding_rates.get(symbol, {}).get("side_to_collect", "NONE") if funding_rates else "NONE",
            )
        )

    return sorted(ranked, key=lambda r: r.score, reverse=True)