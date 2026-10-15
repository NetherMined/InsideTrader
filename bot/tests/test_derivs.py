"""Derivatives collector (Phase 8.2a of FIX_PLAN_OCT14).

Run: python -m bot.tests.test_derivs   (or pytest bot/tests)
The upsert test writes and deletes rows for the symbol "_TEST_/USDT" in the real database.
"""

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import text

from bot.data import derivs
from bot.db.connection import async_session

BAR = derivs.BAR_MS
T0 = 1_792_000_800_000  # a 15m boundary


def _kline(ts, closed=True, now=T0 + 10 * BAR):
    close_time = ts + BAR - 1 if closed else now + 1
    return [ts, "1", "1", "1", "1", "10", close_time, "1000.5", 5, "4", "400.25", "0"]


def test_rows_merge_and_alignment():
    data = {
        "fapiDataGetOpenInterestHist": [{"timestamp": T0, "sumOpenInterestValue": "5000"},
                                        {"timestamp": T0 + BAR, "sumOpenInterestValue": "5100"}],
        "fapiDataGetTakerlongshortRatio": [{"timestamp": T0, "buySellRatio": "0.9"}],
        "fapiDataGetGlobalLongShortAccountRatio": [{"timestamp": T0 + BAR, "longShortRatio": "1.2"}],
        "fapiDataGetTopLongShortPositionRatio": [],
    }
    now = T0 + 2 * BAR + 60_000
    klines = [_kline(T0), _kline(T0 + BAR), _kline(T0 + 2 * BAR, closed=False, now=now)]
    funding = [{"fundingTime": T0 - 8 * 4 * BAR, "fundingRate": "0.0001"},
               {"fundingTime": T0 + BAR, "fundingRate": "0.0003"}]
    rows = derivs.rows_from_payloads("X/USDT", data, klines, funding, now)
    assert [r["open_time"] for r in rows] == [datetime.fromtimestamp((T0 + i * BAR) / 1000, tz=timezone.utc) for i in (0, 1)]
    r0, r1 = rows
    assert (r0["oi_value"], r0["taker_ratio"], r0["gl_ratio"], r0["top_pos_ratio"]) == (5000.0, 0.9, None, None)
    assert (r1["oi_value"], r1["gl_ratio"]) == (5100.0, 1.2)
    assert r0["quote_volume"] == 1000.5 and r0["taker_buy_quote"] == 400.25
    assert r0["funding"] == 0.0001 and r1["funding"] == 0.0003   # as-of: settled at or before the bar


def test_forming_kline_is_dropped():
    now = T0 + 60_000
    rows = derivs.rows_from_payloads("X/USDT", {}, [_kline(T0, closed=False, now=now)], [], now)
    assert rows == []


def test_throttle_spacing():
    async def run():
        th = derivs.Throttle(per_min=600)   # 0.1 s gap
        t0 = time.monotonic()
        for _ in range(4):
            await th.wait()
        return time.monotonic() - t0
    assert asyncio.run(run()) >= 0.29


def test_paging_stops_when_endpoint_repeats_latest_row():
    """Past the newest period, the taker endpoint returns its latest row again (Oct 15 hang)."""
    latest = T0 + 3 * BAR

    class Ex:
        calls = 0

        async def _page(self, params):
            Ex.calls += 1
            assert Ex.calls < 50, "paging did not terminate"
            start = params["startTime"]
            if start > latest:
                return [{"timestamp": latest, "buySellRatio": "1", "sumOpenInterestValue": "1", "longShortRatio": "1"}]
            return [{"timestamp": ts, "buySellRatio": "1", "sumOpenInterestValue": "1", "longShortRatio": "1"}
                    for ts in range(start, latest + 1, BAR)]

        def __getattr__(self, name):
            if name.startswith("fapiData"):
                return self._page
            raise AttributeError(name)

        async def fapiPublicGetKlines(self, params):
            return []

        async def fapiPublicGetFundingRate(self, params):
            return []

    rows = asyncio.run(derivs._fetch_symbol(Ex(), derivs.Throttle(per_min=60000), "X/USDT", T0, latest + 2 * BAR + 1))
    assert len(rows) == 4 and Ex.calls < 50


def test_upsert_idempotent_and_never_nulls():
    sym = "_TEST_/USDT"
    t = datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    base = {"symbol": sym, "open_time": t, **{c: None for c in derivs.COLUMNS}}

    async def run():
        async with async_session() as s:
            async with s.begin():
                await s.execute(text("DELETE FROM derivs_15m WHERE symbol = :s"), {"s": sym})
        try:
            await derivs.upsert([{**base, "oi_value": 1.0, "taker_ratio": 0.8}])
            await derivs.upsert([{**base, "oi_value": 1.0, "taker_ratio": 0.8}])      # same again
            await derivs.upsert([{**base, "gl_ratio": 1.5}])                          # fills, keeps others
            async with async_session() as s:
                rows = (await s.execute(text(
                    "SELECT oi_value, taker_ratio, gl_ratio FROM derivs_15m WHERE symbol = :s"), {"s": sym})).all()
            return rows
        finally:
            async with async_session() as s:
                async with s.begin():
                    await s.execute(text("DELETE FROM derivs_15m WHERE symbol = :s"), {"s": sym})

    rows = asyncio.run(run())
    assert rows == [(1.0, 0.8, 1.5)]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
