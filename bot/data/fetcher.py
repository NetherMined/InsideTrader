"""Historical OHLCV data fetcher.

Uses the public Binance API (no auth required) to pull up to 90 days
of candle data per pair and store it in PostgreSQL.
"""

import asyncio
from datetime import datetime, timezone, timedelta

import ccxt.async_support as ccxt
import pandas as pd
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from bot.config import settings
from bot.db.connection import async_session
from bot.db.models import Candle

LOOKBACK_DAYS = 365
BATCH_SIZE = 1000
REQUEST_DELAY = 0.2


def _futures_symbol(symbol: str) -> str:
    """BTC/USDT → BTC/USDT:USDT for USDM candles. Storage key stays BTC/USDT."""
    return symbol if ":" in symbol else f"{symbol}:USDT"


def _make_exchange() -> ccxt.binance:
    return ccxt.binance({"options": {"defaultType": "future"}})


def _timeframe_to_ms(timeframe: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    return value * units[unit]


async def _get_last_candle_time(symbol: str, timeframe: str) -> datetime | None:
    async with async_session() as session:
        result = await session.execute(
            select(func.max(Candle.open_time)).where(
                Candle.symbol == symbol,
                Candle.timeframe == timeframe,
            )
        )
        return result.scalar()


async def _upsert_candles(rows: list[dict]) -> None:
    if not rows:
        return
    async with async_session() as session:
        stmt = (
            insert(Candle)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_candle")
        )
        await session.execute(stmt)
        await session.commit()


async def fetch_pair(symbol: str, timeframe: str, exchange: ccxt.binance) -> int:
    """Fetch and store missing candles for a single symbol. Returns count stored."""
    last_time = await _get_last_candle_time(symbol, timeframe)

    if last_time:
        since_dt = last_time + timedelta(milliseconds=_timeframe_to_ms(timeframe))
    else:
        # Sub-hourly timeframes cap at 30 days to avoid fetching years of dense candles on first run
        _tf_max = {"5m": 30, "15m": 30, "30m": 30}
        max_days = _tf_max.get(timeframe, LOOKBACK_DAYS)
        since_dt = datetime.now(timezone.utc) - timedelta(days=max_days)

    since_ms = int(since_dt.timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    if since_ms >= now_ms:
        return 0

    total_stored = 0

    while since_ms < now_ms:
        try:
            ohlcv = await exchange.fetch_ohlcv(
                _futures_symbol(symbol), timeframe, since=since_ms, limit=BATCH_SIZE
            )
        except ccxt.BadSymbol:
            logger.warning(f"Symbol {symbol} not available, skipping")
            break
        except ccxt.NetworkError as e:
            logger.warning(f"Network error fetching {symbol}: {e}")
            await asyncio.sleep(5)
            continue
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            break

        if not ohlcv:
            break

        rows = [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "close_time": datetime.fromtimestamp((c[0] + _timeframe_to_ms(timeframe) - 1) / 1000, tz=timezone.utc),
            }
            for c in ohlcv
        ]

        await _upsert_candles(rows)
        total_stored += len(rows)

        last_open_time = ohlcv[-1][0]
        since_ms = last_open_time + _timeframe_to_ms(timeframe)

        await asyncio.sleep(REQUEST_DELAY)

    return total_stored


async def fetch_historical(pairs: list[str], timeframe: str | None = None) -> None:
    """Fetch historical data for a list of pairs. Runs pairs concurrently in batches."""
    tf = timeframe or settings.analysis_timeframe
    exchange = _make_exchange()

    try:
        semaphore = asyncio.Semaphore(5)

        async def fetch_with_sem(symbol: str) -> tuple[str, int]:
            async with semaphore:
                count = await fetch_pair(symbol, tf, exchange)
                if count:
                    logger.info(f"Fetched {count} candles for {symbol}")
                return symbol, count

        tasks = [fetch_with_sem(s) for s in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if isinstance(r, tuple) and r[1] >= 0)
        logger.info(f"Historical fetch complete: {success}/{len(pairs)} pairs processed")
    finally:
        await exchange.close()


async def fetch_recent(pairs: list[str], timeframe: str | None = None) -> None:
    """Fetch only the latest candles (since last stored) for all pairs."""
    await fetch_historical(pairs, timeframe)