"""Per-symbol trading cost model built from the live Binance futures order book."""

from __future__ import annotations

import json

import aiohttp
from loguru import logger
import redis.asyncio as aioredis

from bot.config import settings

_DEPTH_URL = "https://fapi.binance.com/fapi/v1/depth"
_KEY = "cost:slip:{symbol}:{bucket}"
_TTL = 300
_BUCKET_USD = 500.0


def _sweep(levels: list, notional: float) -> float | None:
    """VWAP price for market-filling `notional` USDT against `levels`, or None if the book is too thin."""
    need, cost, qty = notional, 0.0, 0.0
    for price, size in levels:
        price, size = float(price), float(size)
        take = min(need, price * size)
        cost += take
        qty += take / price
        need -= take
        if need <= 1e-9:
            break
    if need > 1e-9 or qty <= 0:
        return None
    return cost / qty


async def get_slippage(redis: aioredis.Redis, symbol: str, notional: float) -> dict | None:
    """Estimated one-way slippage for a market order of `notional` USDT.

    Returns {"buy_pct", "sell_pct"} (percent of mid), {"thin": True} when the top 100
    levels cannot absorb the size, or None if the book could not be read.
    """
    bucket = int(max(notional, 1.0) // _BUCKET_USD)
    key = _KEY.format(symbol=symbol, bucket=bucket)
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(_DEPTH_URL, params={"symbol": symbol.replace("/", ""), "limit": 100}) as resp:
                resp.raise_for_status()
                book = await resp.json()
        bids, asks = book.get("bids"), book.get("asks")
        if not bids or not asks:
            return None
        size = (bucket + 1) * _BUCKET_USD  # price for the top of the bucket so the estimate is not optimistic
        mid = (float(bids[0][0]) + float(asks[0][0])) / 2
        buy_vwap, sell_vwap = _sweep(asks, size), _sweep(bids, size)
        if buy_vwap is None or sell_vwap is None:
            result = {"thin": True}
        else:
            result = {"buy_pct": (buy_vwap / mid - 1) * 100, "sell_pct": (1 - sell_vwap / mid) * 100}
        await redis.setex(key, _TTL, json.dumps(result))
        return result
    except Exception as exc:
        logger.debug(f"{symbol}: order book cost lookup failed ({exc})")
        return None


def roundtrip_cost_pct(slip: dict) -> float:
    """Entry + exit slippage plus both taker fees, in percent of notional."""
    return slip["buy_pct"] + slip["sell_pct"] + 2 * settings.taker_fee_rate * 100
