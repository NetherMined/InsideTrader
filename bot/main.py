"""InsideTrader Bot — entry point.

Startup sequence:
  1. Verify DB and Redis connections (with retry)
  2. Initialise database schema
  3. Scan all Binance markets and persist results
  4. Fetch 90 days of historical OHLCV data
  5. Run analysis: indicators → ML prediction → mode classification → ranking
  6. Start trading engine (every 5s loop for continuous 24/7 trading)
  7. Start live price polling loop
  8. Schedule hourly refresh
"""

import asyncio
import json
import sys

import ccxt.async_support as ccxt
import redis.asyncio as aioredis
from loguru import logger

from bot.config import settings
from bot.db.connection import init_db, check_db
from bot.data.scanner import scan_markets, save_scanned_markets, get_top_pairs
from bot.data.fetcher import fetch_historical, fetch_recent
from bot.data.stream import poll_prices, check_redis
from bot.analysis.trainer import run_analysis, force_retrain_models
from bot.trading.executor import run_trading_engine
from bot.research.loop import run_researcher_loop

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>"
)


def setup_logging() -> None:
    logger.remove()
    logger.add(sys.stdout, format=LOG_FORMAT, level="INFO", colorize=True)
    logger.add(
        "/app/logs/bot_{time:YYYY-MM-DD}.log",
        format=LOG_FORMAT,
        level="DEBUG",
        rotation="00:00",
        retention="30 days",
        compression="gz",
    )


async def check_futures_position_mode() -> None:
    """Warn and exit if Binance futures account is in Hedge Mode (One-Way required)."""
    if settings.paper_trading_mode or settings.use_testnet:
        return
    exchange = ccxt.binance({
        "apiKey": settings.binance_api_key,
        "secret": settings.binance_api_secret,
        "options": {"defaultType": "future"},
    })
    try:
        result = await exchange.fapiPrivateGetPositionSideDual()
        if result.get("dualSidePosition"):
            logger.critical(
                "Binance futures account is in Hedge Mode. "
                "InsideTrader requires One-Way mode. "
                "Disable Hedge Mode in Binance > Preferences > Position Mode, then restart."
            )
            sys.exit(1)
        logger.info("Futures position mode: One-Way (OK)")
    except Exception as e:
        logger.warning(f"Could not verify futures position mode: {e}")
    finally:
        await exchange.close()


async def startup_checks() -> None:
    logger.info("Running startup checks...")
    for attempt in range(1, 11):
        db_ok = await check_db()
        redis_ok = await check_redis()
        if db_ok and redis_ok:
            logger.info("All connections healthy")
            return
        if not db_ok:
            logger.warning(f"PostgreSQL not ready (attempt {attempt}/10), retrying in 5s...")
        if not redis_ok:
            logger.warning(f"Redis not ready (attempt {attempt}/10), retrying in 5s...")
        await asyncio.sleep(5)
    logger.critical("Could not connect after 10 attempts. Exiting.")
    sys.exit(1)


def _make_redis() -> aioredis.Redis:
    url = settings.redis_url
    return aioredis.from_url(url, encoding="utf-8", decode_responses=True)


async def hourly_refresh(
    pairs: list[str], redis: aioredis.Redis, stop_event: asyncio.Event, ranked_store: dict
) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(3600)
        if stop_event.is_set():
            break
        logger.info("Hourly refresh: scanning + re-analysis...")
        try:
            markets = await scan_markets()
            await save_scanned_markets(markets)
            refreshed = await get_top_pairs(100)
            await fetch_recent(refreshed, settings.analysis_timeframe)
            if settings.research_enabled:
                await fetch_recent(refreshed, settings.research_timeframe)
            ranked = await run_analysis(refreshed)
            ranked_store["pairs"] = ranked
            logger.info(f"Hourly refresh complete — {len(ranked)} ranked pairs (engine updated)")
            try:
                from bot.analysis.ranker import fetch_pair_correlations
                corr = await fetch_pair_correlations([p.symbol for p in ranked])
                if corr:
                    await redis.setex("bot:correlation_matrix", 3600, json.dumps(corr))
                    logger.debug(f"Correlation matrix refreshed ({len(corr)} symbols)")
            except Exception as corr_err:
                logger.warning(f"Correlation matrix refresh failed: {corr_err}")
        except Exception as e:
            logger.error(f"Hourly refresh error: {e}")


async def periodic_retrain(
    pairs: list[str], stop_event: asyncio.Event
) -> None:
    """Force retrain all models every 6 hours with latest data."""
    while not stop_event.is_set():
        await asyncio.sleep(21600)
        if stop_event.is_set():
            break
        logger.info("Periodic model retraining...")
        try:
            await force_retrain_models(pairs)
            logger.info("Periodic retrain complete")
        except Exception as e:
            logger.error(f"Periodic retrain error: {e}")


async def main() -> None:
    setup_logging()

    logger.info("=" * 60)
    logger.info("InsideTrader Bot starting")
    logger.info(f"Network: {'TESTNET' if settings.use_testnet else 'LIVE BINANCE'}")
    logger.info(f"Orders:  {'PAPER (simulated)' if settings.paper_trading_mode else 'LIVE'}")
    logger.info(f"Capital: ${settings.starting_capital_usdt:.2f} USDT (config default — actual set via settings page)")
    logger.info(f"Target:  {settings.daily_target_percent}% / day")
    logger.info(f"Mode:    {settings.trading_mode}")
    logger.info("=" * 60)

    await startup_checks()
    await check_futures_position_mode()
    await init_db()

    logger.info("Scanning Binance markets...")
    markets = await scan_markets()
    await save_scanned_markets(markets)

    top_pairs = await get_top_pairs(100)
    pairs = settings.pairs_list or top_pairs
    logger.info(f"Using {len(pairs)} pairs")

    logger.info("Fetching historical OHLCV data (1h manager)...")
    await fetch_historical(pairs, settings.analysis_timeframe)
    if settings.research_enabled:
        logger.info(f"Fetching {settings.research_timeframe} candles for researcher (capped lookback)...")
        await fetch_historical(pairs, settings.research_timeframe)

    logger.info("Running analysis engine...")
    ranked = await run_analysis(pairs)
    logger.info(f"Analysis complete — {len(ranked)} ranked opportunities")

    redis = _make_redis()
    stop_event = asyncio.Event()
    ranked_store = {"pairs": ranked}

    logger.info("Computing initial correlation matrix...")
    try:
        from bot.analysis.ranker import fetch_pair_correlations
        corr = await fetch_pair_correlations([p.symbol for p in ranked])
        if corr:
            await redis.setex("bot:correlation_matrix", 3600, json.dumps(corr))
            logger.info(f"Correlation matrix ready ({len(corr)} symbols)")
    except Exception as corr_err:
        logger.warning(f"Initial correlation matrix failed: {corr_err}")

    async def _guarded(name: str, coro):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Task '{name}' crashed: {exc}")
            stop_event.set()

    tasks = [
        asyncio.create_task(_guarded("trading_engine", run_trading_engine(ranked_store, redis, stop_event))),
        asyncio.create_task(_guarded("price_stream", poll_prices(pairs, stop_event))),
        asyncio.create_task(_guarded("hourly_refresh", hourly_refresh(pairs, redis, stop_event, ranked_store))),
        asyncio.create_task(_guarded("periodic_retrain", periodic_retrain(pairs, stop_event))),
        asyncio.create_task(_guarded("researcher", run_researcher_loop(ranked_store, redis, stop_event))),
    ]
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown signal received")
        stop_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await redis.aclose()

    logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
