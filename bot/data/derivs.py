"""Collector for Binance futures derivatives / order-flow series (FIX_PLAN_OCT14 Phase 8.2a).

Every 15m, for the top futures symbols by volume, stores into derivs_15m:
open interest, taker buy/sell ratio, global and top-trader long/short ratios,
kline taker-buy and quote volume, and the last settled funding rate.

Binance keeps only ~30 days of the futures/data series, so the first run backfills
29 days and each later run continues from the newest stored bar. History older than
that exists only if this collector stored it.
"""

from __future__ import annotations

import asyncio
import time
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

import ccxt.async_support as ccxt
from loguru import logger
from sqlalchemy import text

from bot.db.connection import async_session

UNIVERSE_SIZE = 80
BAR_MS = 15 * 60 * 1000
MAX_HISTORY_DAYS = 29          # futures/data rejects startTime older than ~30 days
PAGE = 500
DATA_REQUESTS_PER_MIN = 180    # futures/data allows ~1000 requests / 5 min per IP
RUN_AFTER_CLOSE = timedelta(minutes=2)

DATA_ENDPOINTS = {
    "fapiDataGetOpenInterestHist": ("sumOpenInterestValue", "oi_value"),
    "fapiDataGetTakerlongshortRatio": ("buySellRatio", "taker_ratio"),
    "fapiDataGetGlobalLongShortAccountRatio": ("longShortRatio", "gl_ratio"),
    "fapiDataGetTopLongShortPositionRatio": ("longShortRatio", "top_pos_ratio"),
}
COLUMNS = ("oi_value", "taker_ratio", "gl_ratio", "top_pos_ratio", "taker_buy_quote", "quote_volume", "funding")


class Throttle:
    def __init__(self, per_min: float = DATA_REQUESTS_PER_MIN):
        self.gap = 60.0 / per_min
        self.last = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            delay = self.last + self.gap - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.last = time.monotonic()


def rows_from_payloads(
    symbol: str,
    data: dict[str, list[dict]],
    klines: list[list],
    funding: list[dict],
    now_ms: int,
) -> list[dict]:
    """Merge raw endpoint payloads into one row per 15m timestamp.

    Klines are kept only once closed. Funding is the last settled rate at or before each bar.
    """
    rows: dict[int, dict] = {}

    def row(ts: int) -> dict:
        return rows.setdefault(ts, {"symbol": symbol, "ts": ts, **{c: None for c in COLUMNS}})

    for method, items in data.items():
        src, dst = DATA_ENDPOINTS[method]
        for it in items:
            row(int(it["timestamp"]))[dst] = float(it[src])
    for k in klines:
        if int(k[6]) >= now_ms:      # close time not reached: still forming
            continue
        r = row(int(k[0]))
        r["quote_volume"], r["taker_buy_quote"] = float(k[7]), float(k[10])
    f_times = sorted((int(f["fundingTime"]), float(f["fundingRate"])) for f in funding)
    f_ts = [t for t, _ in f_times]
    for ts, r in rows.items():
        i = bisect_right(f_ts, ts) - 1
        if i >= 0:
            r["funding"] = f_times[i][1]
    out = []
    for ts in sorted(rows):
        r = rows[ts]
        r["open_time"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        del r["ts"]
        out.append(r)
    return out


async def _fetch_symbol(ex, throttle: Throttle, symbol: str, start_ms: int, now_ms: int) -> list[dict]:
    market = symbol.split(":")[0].replace("/", "")
    data: dict[str, list[dict]] = {}
    for method in DATA_ENDPOINTS:
        items, t = [], start_ms
        while t < now_ms:
            await throttle.wait()
            page = await getattr(ex, method)({"symbol": market, "period": "15m", "limit": PAGE,
                                              "startTime": t, "endTime": min(now_ms, t + PAGE * BAR_MS)})
            if not page:
                t += PAGE * BAR_MS
                continue
            items += page
            last = int(page[-1]["timestamp"])
            # Past the newest period some endpoints return their latest row again (older than t)
            if last < t:
                break
            t = last + BAR_MS
        data[method] = items
    klines, t = [], start_ms
    while t < now_ms:
        page = await ex.fapiPublicGetKlines({"symbol": market, "interval": "15m", "limit": 1500, "startTime": t})
        if not page:
            break
        klines += page
        t = int(page[-1][0]) + BAR_MS
        if len(page) < 1500:
            break
    funding = await ex.fapiPublicGetFundingRate({"symbol": market, "startTime": start_ms - 86_400_000, "limit": 1000})
    return rows_from_payloads(symbol, data, klines, funding, now_ms)


async def upsert(rows: list[dict]) -> int:
    """Insert or fill rows. A NULL never overwrites a stored value."""
    if not rows:
        return 0
    sets = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, derivs_15m.{c})" for c in COLUMNS)
    sql = text(f"""
        INSERT INTO derivs_15m (symbol, open_time, {", ".join(COLUMNS)})
        VALUES (:symbol, :open_time, {", ".join(":" + c for c in COLUMNS)})
        ON CONFLICT (symbol, open_time) DO UPDATE SET {sets}
    """)
    async with async_session() as s:
        async with s.begin():
            await s.execute(sql, rows)
    return len(rows)


async def _latest_stored() -> dict[str, datetime]:
    async with async_session() as s:
        res = await s.execute(text("SELECT symbol, max(open_time) FROM derivs_15m GROUP BY symbol"))
        return {r[0]: r[1] for r in res.all()}


async def collect(symbols: list[str]) -> dict:
    """One cycle: continue every symbol from its newest stored bar (or 29 days back)."""
    now_ms = int(time.time() * 1000)
    floor_ms = (now_ms - MAX_HISTORY_DAYS * 86_400_000) // BAR_MS * BAR_MS
    latest = await _latest_stored()
    throttle = Throttle()
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    stored, failed = 0, []
    t0 = time.monotonic()
    try:
        for sym in symbols:
            last = latest.get(sym)
            # Re-read the last 2 bars: their kline / ratio values may have been incomplete
            start_ms = max(floor_ms, int(last.timestamp() * 1000) - 2 * BAR_MS) if last else floor_ms
            try:
                stored += await upsert(await _fetch_symbol(ex, throttle, sym, start_ms, now_ms))
            except Exception as e:
                failed.append(sym)
                logger.debug(f"{sym}: derivs fetch failed ({e})")
    finally:
        await ex.close()
    summary = {"symbols": len(symbols), "rows": stored, "failed": failed, "seconds": round(time.monotonic() - t0, 1)}
    logger.info(f"Derivs collector: {stored} rows for {len(symbols) - len(failed)}/{len(symbols)} symbols "
                f"in {summary['seconds']}s" + (f" — failed: {', '.join(failed[:5])}" if failed else ""))
    return summary


async def run_collector(stop_event: asyncio.Event) -> None:
    """Collect right away (backfill on first run), then 2 min after every 15m close."""
    from bot.data.scanner import get_top_pairs

    while not stop_event.is_set():
        try:
            symbols = await get_top_pairs(UNIVERSE_SIZE, market_type="future")
            await collect(symbols)
        except Exception as e:
            logger.error(f"Derivs collector cycle failed: {e}")
        now = datetime.now(timezone.utc)
        slot = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
        next_run = slot + timedelta(minutes=15) + RUN_AFTER_CLOSE
        # Poll the wall clock: a long sleep does not count time while the host is suspended.
        while not stop_event.is_set() and datetime.now(timezone.utc) < next_run:
            await asyncio.sleep(15)
