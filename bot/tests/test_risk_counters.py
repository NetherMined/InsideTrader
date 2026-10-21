import asyncio

from bot.trading import risk as risk_mod
from bot.trading.risk import RiskManager


class FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value):
        self.kv[key] = str(value)

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(str(value))

    async def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        self.lists[key] = items[start:] if end == -1 else items[start:end + 1]


def _run(coro):
    return asyncio.run(coro)


def test_daily_pnl_pct_is_measured_against_capital():
    redis = FakeRedis()
    risk = RiskManager(redis)
    # Two trades at 3x: -3.3% and -3.6% on margin, but only -$1.10 and -$1.20 on $500 capital
    _run(risk.on_trade_closed(-3.3, 5.0, pnl_usdt=-1.10, capital_usdt=500.0))
    _run(risk.on_trade_closed(-3.6, 5.0, pnl_usdt=-1.20, capital_usdt=500.0))
    assert abs(float(redis.kv[risk_mod.DAILY_PNL_KEY]) - (-0.46)) < 1e-9
    assert redis.lists[risk_mod.TRADE_RETURNS_KEY] == ["-3.3", "-3.6"]


def test_gross_minus_fees_equals_net():
    redis = FakeRedis()
    risk = RiskManager(redis)
    for gross, fee in [(1.0, 0.07), (-0.8, 0.06)]:
        _run(risk.record_fee(fee))
        _run(risk.record_gross_pnl(0.0, gross))
    assert abs(_run(risk.get_daily_gross_pnl_usdt()) - 0.2) < 1e-9
    assert abs(_run(risk.get_daily_fees()) - 0.13) < 1e-9
    assert abs(_run(risk.get_net_pnl_usdt()) - 0.07) < 1e-9
    assert abs(float(redis.kv[risk_mod.TOTAL_FEES_KEY]) - 0.13) < 1e-9


def test_fee_and_gross_counters_reset_on_new_day():
    redis = FakeRedis()
    risk = RiskManager(redis)
    redis.kv[risk_mod.DAILY_DATE_KEY] = "2000-01-01"
    redis.kv[risk_mod.FEES_KEY] = "147.4"
    redis.kv[risk_mod.GROSS_PNL_KEY] = "147.4"
    redis.kv[risk_mod.TOTAL_FEES_KEY] = "200.0"
    assert _run(risk.get_daily_fees()) == 0.0
    assert _run(risk.get_daily_gross_pnl_usdt()) == 0.0
    assert redis.kv[risk_mod.TOTAL_FEES_KEY] == "200.0"
