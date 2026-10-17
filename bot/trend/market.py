"""Public Binance USDT-M futures data for the trend sleeve (no API key)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

import aiohttp

BASE = "https://fapi.binance.com"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


async def _get(session: aiohttp.ClientSession, path: str, **params):
    async with session.get(f"{BASE}{path}", params=params, timeout=_TIMEOUT) as r:
        r.raise_for_status()
        return await r.json()


async def daily_closes(session: aiohttp.ClientSession, symbols: Iterable[str], days: int = 45
                       ) -> dict[str, list[tuple[date, float]]]:
    """Completed daily closes per symbol, oldest first (today's still-open candle is dropped)."""
    out = {}
    now = datetime.now(timezone.utc)
    for sym in symbols:
        rows = await _get(session, "/fapi/v1/klines", symbol=sym, interval="1d", limit=days + 1)
        out[sym] = [(_ts(r[0]).date(), float(r[4])) for r in rows if _ts(r[6]) < now]
    return out


async def prices(session: aiohttp.ClientSession, symbols: Iterable[str]) -> dict[str, float]:
    wanted = set(symbols)
    rows = await _get(session, "/fapi/v1/ticker/price")
    return {r["symbol"]: float(r["price"]) for r in rows if r["symbol"] in wanted}


async def hourly_bars(session: aiohttp.ClientSession, symbol: str, since: datetime
                      ) -> list[tuple[datetime, float, float]]:
    """(open_time, open, low) of 1h bars that end after `since`, including the bar still forming."""
    start = int(since.timestamp() * 1000) - 3_600_000
    rows = await _get(session, "/fapi/v1/klines", symbol=symbol, interval="1h", startTime=start, limit=500)
    return [(_ts(r[0]), float(r[1]), float(r[3])) for r in rows if _ts(r[6]) > since]


async def funding_events(session: aiohttp.ClientSession, symbol: str, since: datetime
                         ) -> list[tuple[str, datetime, float, float]]:
    """(symbol, time, rate, mark price) for funding settled after `since`."""
    rows = await _get(session, "/fapi/v1/fundingRate", symbol=symbol,
                      startTime=int(since.timestamp() * 1000) + 1, limit=1000)
    out = []
    for r in rows:
        mark = float(r.get("markPrice") or 0.0)
        out.append((symbol, _ts(r["fundingTime"]), float(r["fundingRate"]), mark))
    return out


async def min_notional(session: aiohttp.ClientSession, symbols: Iterable[str]) -> dict[str, float]:
    wanted = set(symbols)
    info = await _get(session, "/fapi/v1/exchangeInfo")
    return {s["symbol"]: float(f["notional"]) for s in info["symbols"] if s["symbol"] in wanted
            for f in s["filters"] if f["filterType"] == "MIN_NOTIONAL"}
