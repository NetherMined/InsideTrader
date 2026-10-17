"""Paper fill slippage from the order book (Phase 7.2 cost calibration).

Run: python -m bot.tests.test_slippage   (or pytest bot/tests)
"""

import asyncio

from bot.trading import costs, paper


class _FakeExchange:
    def __init__(self, book=None, fail=False):
        self.book, self.fail = book, fail

    async def fetch_order_book(self, symbol, limit):
        if self.fail:
            raise RuntimeError("down")
        return self.book

    async def close(self):
        pass


def _with_exchange(ex):
    costs.ccxt.binance = lambda *_a, **_k: ex


BOOK = {"bids": [[99.9, 1.0], [99.8, 10.0]], "asks": [[100.1, 1.0], [100.2, 10.0]]}  # mid 100


def test_small_order_pays_half_spread():
    _with_exchange(_FakeExchange(BOOK))
    slip = asyncio.run(paper.fill_slippage("X/USDT", "BUY", 50.0))
    assert abs(slip - 0.001) < 1e-9  # 100.1 vs mid 100


def test_large_order_walks_the_book():
    _with_exchange(_FakeExchange(BOOK))
    slip = asyncio.run(paper.fill_slippage("X/USDT", "SELL", 300.0))
    # 99.9 for the first ~$99.9, the rest at 99.8
    expected = (99.9 * 0.001 + (300.0 - 99.9) * 0.002) / 300.0
    assert abs(slip - expected) < 1e-9


def test_stop_adds_extra():
    _with_exchange(_FakeExchange(BOOK))
    slip = asyncio.run(paper.fill_slippage("X/USDT", "BUY", 50.0, stop=True))
    assert abs(slip - (0.001 + paper.settings.paper_stop_slippage_pct / 100)) < 1e-9


def test_fallback_when_book_unavailable():
    _with_exchange(_FakeExchange(fail=True))
    slip = asyncio.run(paper.fill_slippage("X/USDT", "BUY", 50.0))
    assert abs(slip - paper.settings.paper_slippage_pct / 100) < 1e-9


def test_cost_check_passes_tight_book():
    _with_exchange(_FakeExchange(BOOK))
    c = asyncio.run(costs.entry_cost_check("X/USDT", 50.0))
    assert abs(c["roundtrip_slippage_pct"] - 0.2) < 1e-9   # 0.1% each side
    assert c["would_skip"] is (0.2 > costs.settings.entry_max_slippage_pct)
    assert abs(c["roundtrip_cost_pct"] - (0.2 + 2 * costs.settings.taker_fee_rate * 100)) < 1e-9


def test_cost_check_flags_wide_and_thin_books():
    tight = {"bids": [[99.99, 100.0]], "asks": [[100.01, 100.0]]}
    _with_exchange(_FakeExchange(tight))
    assert asyncio.run(costs.entry_cost_check("X/USDT", 50.0))["would_skip"] is False
    _with_exchange(_FakeExchange(BOOK))   # ~$100 per side at the touch, then $1000
    thin = asyncio.run(costs.entry_cost_check("X/USDT", 5000.0))
    assert thin["thin"] is True and thin["would_skip"] is True and thin["roundtrip_slippage_pct"] is None


def test_cost_check_none_when_book_unreadable():
    _with_exchange(_FakeExchange(fail=True))
    assert asyncio.run(costs.entry_cost_check("X/USDT", 50.0)) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
