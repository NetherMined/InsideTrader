"""Live price streaming via polling.

Fetches current ticker prices from Binance every POLL_INTERVAL seconds
and stores them in Redis for the API and bot engine to consume.
"""

import asyncio
import json
from datetime import datetime, timezone

import ccxt.async_support as ccxt
import redis.asyncio as aioredis
from loguru import logger

from bot.config import settings

POLL_INTERVAL = 10
PRICE_TTL = 60


def _make_exchange() -> ccxt.binance:
    return ccxt.binance({"options": {"defaultType": "future"}})


async def _get_redis() -> aioredis.Redis:
    return await aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


async def poll_prices(pairs: list[str], stop_event: asyncio.Event) -> None:
    """Continuously poll Binance for current prices and write to Redis."""
    exchange = _make_exchange()
    redis = await _get_redis()

    logger.info(f"Starting price stream for {len(pairs)} pairs (poll every {POLL_INTERVAL}s)")

    active_pairs = list(pairs)

    try:
        while not stop_event.is_set():
            try:
                futures_pairs = [s if ":" in s else f"{s}:USDT" for s in active_pairs]
                tickers = await exchange.fetch_tickers(futures_pairs)
                pipe = redis.pipeline()

                for symbol, ticker in tickers.items():
                    price = ticker.get("last") or ticker.get("close")
                    if price is None:
                        continue
                    symbol = symbol.split(":")[0]

                    payload = json.dumps({
                        "symbol": symbol,
                        "price": float(price),
                        "bid": float(ticker.get("bid") or price),
                        "ask": float(ticker.get("ask") or price),
                        "change_pct": float(ticker.get("percentage") or 0.0),
                        "volume_24h": float(ticker.get("quoteVolume") or 0.0),
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })

                    key = f"price:{symbol}"
                    pipe.set(key, payload, ex=PRICE_TTL)

                await pipe.execute()
                await redis.set("bot:price_stream_active", str(len(tickers)), ex=30)

                # Compute and cache market sentiment from live ticker data
                advancing = sum(1 for t in tickers.values() if (t.get("percentage") or 0.0) > 0)
                declining = sum(1 for t in tickers.values() if (t.get("percentage") or 0.0) < 0)
                _total = max(len(tickers), 1)
                _advance_ratio = advancing / _total
                if _advance_ratio > 0.55:
                    _sentiment = "BULLISH"
                elif _advance_ratio < 0.45:
                    _sentiment = "BEARISH"
                else:
                    _sentiment = "NEUTRAL"
                await redis.set(
                    "market:sentiment",
                    json.dumps({
                        "sentiment": _sentiment,
                        "advance_ratio": round(_advance_ratio, 4),
                        "advancing": advancing,
                        "declining": declining,
                        "total": _total,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }),
                    ex=120,
                )

                await redis.set(
                    "prices:last_update",
                    datetime.now(timezone.utc).isoformat(),
                    ex=PRICE_TTL,
                )

            except ccxt.NetworkError as e:
                logger.warning(f"Network error during price poll: {e}")
                await redis.set("bot:price_stream_active", str(len(active_pairs)), ex=30)
            except Exception as e:
                msg = str(e)
                if "does not have market symbol" in msg:
                    bad = msg.split("does not have market symbol")[-1].strip().split()[0]
                    if bad in active_pairs:
                        active_pairs.remove(bad)
                        logger.warning(f"Dropping unknown market {bad} from price poll ({len(active_pairs)} left)")
                    else:
                        logger.warning(f"Price poll skipped unknown market: {msg}")
                else:
                    logger.warning(f"Price poll error: {e}")
                await redis.set("bot:price_stream_active", str(len(active_pairs)), ex=30)

            await asyncio.sleep(POLL_INTERVAL)

    finally:
        await exchange.close()
        await redis.aclose()
        logger.info("Price stream stopped")


async def get_price(symbol: str) -> float | None:
    """Get current price for a symbol from Redis."""
    redis = await _get_redis()
    try:
        raw = await redis.get(f"price:{symbol}")
        if raw:
            data = json.loads(raw)
            return data.get("price")
        return None
    finally:
        await redis.aclose()


async def check_redis() -> bool:
    try:
        redis = await _get_redis()
        await redis.ping()
        await redis.aclose()
        return True
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return False