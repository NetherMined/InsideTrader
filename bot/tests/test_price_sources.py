import asyncio

from bot.analysis import funding_rate
from bot.config import settings
from bot.trading import orders


def _fake_fetch(prices):
    calls = []

    async def fetch(default_type, symbol):
        calls.append((default_type, symbol))
        if (default_type, symbol) in prices:
            return prices[(default_type, symbol)]
        raise Exception(f"binance does not have market symbol {symbol}")

    return fetch, calls


def test_spot_price_used_when_available(monkeypatch):
    fetch, calls = _fake_fetch({("spot", "BTC/USDT"): 83000.0})
    monkeypatch.setattr(orders, "_fetch_last_price", fetch)
    assert asyncio.run(orders.get_current_price("BTC/USDT")) == 83000.0
    assert calls == [("spot", "BTC/USDT")]


def test_futures_only_coin_falls_back_to_perpetual(monkeypatch):
    fetch, calls = _fake_fetch({("future", "4/USDT:USDT"): 0.0134})
    monkeypatch.setattr(orders, "_fetch_last_price", fetch)
    assert asyncio.run(orders.get_current_price("4/USDT")) == 0.0134
    assert calls == [("spot", "4/USDT"), ("future", "4/USDT:USDT")]


def test_unknown_coin_returns_none(monkeypatch):
    fetch, _ = _fake_fetch({})
    monkeypatch.setattr(orders, "_fetch_last_price", fetch)
    assert asyncio.run(orders.get_current_price("NOPE/USDT")) is None


def test_funding_rates_read_real_market_even_on_testnet(monkeypatch):
    monkeypatch.setattr(settings, "use_testnet", True)
    ex = funding_rate._make_exchange()
    try:
        assert "testnet" not in ex.urls["api"]["fapiPublic"]
        assert not ex.apiKey
    finally:
        asyncio.run(ex.close())
