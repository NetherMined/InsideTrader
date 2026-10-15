"""P&L must equal quantity * price change, independent of leverage.

Run: python -m bot.tests.test_pnl   (or pytest bot/tests)
"""

import asyncio

from bot.trading.pnl import compute_pnl
from bot.trading import paper


def _close(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def test_buy_win_any_leverage():
    for lev in (1, 3, 5):
        p = compute_pnl("BUY", 100.0, 101.0, 2.0, lev)
        assert _close(p.gross_usdt, 2.0)
        assert _close(p.raw_pct, 1.0)
        assert _close(p.effective_pct, 1.0 * lev)


def test_buy_loss():
    p = compute_pnl("BUY", 100.0, 99.3, 1.0, 5)
    assert _close(p.gross_usdt, -0.7)


def test_sell_win_and_loss():
    assert _close(compute_pnl("SELL", 100.0, 98.0, 1.5, 5).gross_usdt, 3.0)
    assert _close(compute_pnl("SELL", 100.0, 100.5, 1.0, 3).gross_usdt, -0.5)


def test_round_trip_fee():
    p = compute_pnl("BUY", 100.0, 102.0, 1.0, 5, fee_rate=0.0004)
    assert _close(p.fee_usdt, (100.0 + 102.0) * 0.0004)
    assert _close(p.net_usdt, 2.0 - p.fee_usdt)


def test_trade_6740_regression():
    # Oct 13: $136.61 notional, -0.701% move was recorded as -$4.79 (leverage counted twice)
    qty = 136.61 / 1.0
    p = compute_pnl("SELL", 1.0, 1.00701, qty, 5)
    assert abs(p.gross_usdt - (-0.958)) < 0.01


class _FakeRedis:
    def __init__(self, capital: float):
        self.store = {paper.PAPER_CAPITAL_KEY: str(capital)}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v):
        self.store[k] = v


def test_paper_capital_round_trip():
    async def no_network_slip(symbol, side, notional, stop=False):
        return 0.0002 + (0.0005 if stop else 0.0)

    paper.fill_slippage = no_network_slip

    async def run():
        r = _FakeRedis(1000.0)
        opened = await paper.simulate_buy(r, "X/USDT", 1.0, 100.0, "FUTURES", leverage=5)
        entry = opened["fill_price"]
        closed = await paper.simulate_sell(r, "X/USDT", 1.0, entry, entry * 1.01, "FUTURES", leverage=5)
        fill_exit = closed["fill_price"]
        open_fee = entry * paper.settings.taker_fee_rate
        close_fee = fill_exit * paper.settings.taker_fee_rate
        expected = 1000.0 + (fill_exit - entry) - open_fee - close_fee
        assert abs(float(r.store[paper.PAPER_CAPITAL_KEY]) - expected) < 1e-6
    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
