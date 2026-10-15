"""Paper fill slippage from the order book (Phase 7.2 cost calibration).

Run: python -m bot.tests.test_slippage   (or pytest bot/tests)
"""

import asyncio

from bot.trading import paper


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
    paper.ccxt.binance = lambda *_a, **_k: ex


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
