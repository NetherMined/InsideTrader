"""Trend sleeve paper engine (Phase 6.2): fills, funding, stops, rebalance, kill switch, run-once rebalances.

Run: python -m pytest bot/tests/test_trend_engine.py
"""
import math
from datetime import date, datetime, timedelta, timezone

from bot.trend import engine as E
from bot.trend import strategy as T

NOW = datetime(2026, 10, 17, 0, 5, tzinfo=timezone.utc)


def _book(cash=1000.0):
    return E.Book(cash=cash, peak_equity=cash)


def test_buy_then_sell_moves_cash_by_fills_and_fees():
    b = _book()
    buy = E.execute(b, "BTCUSDT", "BUY", 2.0, 100.0, "rebalance", NOW)
    assert math.isclose(buy.fill_price, 100.0 * (1 + E.SLIP))
    assert math.isclose(b.cash, 1000.0 - 2 * buy.fill_price - buy.fee_usdt)
    sell = E.execute(b, "BTCUSDT", "SELL", 2.0, 110.0, "rebalance", NOW)
    assert "BTCUSDT" not in b.positions
    expected = 1000.0 - 2 * buy.fill_price - buy.fee_usdt + 2 * sell.fill_price - sell.fee_usdt
    assert math.isclose(b.cash, expected)


def test_adding_to_a_position_averages_entry_and_moves_stop():
    b = _book()
    E.execute(b, "ETHUSDT", "BUY", 1.0, 100.0, "rebalance", NOW)
    E.execute(b, "ETHUSDT", "BUY", 1.0, 200.0, "rebalance", NOW)
    p = b.positions["ETHUSDT"]
    assert math.isclose(p.entry_price, 150.0 * (1 + E.SLIP))
    assert math.isclose(p.stop_price, T.stop_price(p.entry_price))


def test_funding_long_pays_positive_rate_once_and_only_after_open():
    b = _book()
    E.execute(b, "SOLUSDT", "BUY", 10.0, 50.0, "rebalance", NOW)
    cash = b.cash
    before_open = (("SOLUSDT", NOW - timedelta(hours=1), 0.001, 50.0))
    after_open = (("SOLUSDT", NOW + timedelta(hours=8), 0.0001, 60.0))
    charges = E.apply_funding(b, [before_open, after_open], {})
    assert len(charges) == 1
    assert math.isclose(charges[0].amount_usdt, 10.0 * 60.0 * 0.0001)
    assert math.isclose(b.cash, cash - charges[0].amount_usdt)
    again = E.apply_funding(b, [after_open], {"SOLUSDT": after_open[1]})
    assert again == [] and math.isclose(b.cash, cash - charges[0].amount_usdt)


def test_negative_funding_pays_the_long():
    b = _book()
    E.execute(b, "SOLUSDT", "BUY", 10.0, 50.0, "rebalance", NOW)
    cash = b.cash
    E.apply_funding(b, [("SOLUSDT", NOW + timedelta(hours=8), -0.0002, 50.0)], {})
    assert b.cash > cash


def test_stop_fills_at_stop_or_at_a_gapped_open():
    b = _book()
    E.execute(b, "AVAXUSDT", "BUY", 1.0, 100.0, "rebalance", NOW)
    stop = b.positions["AVAXUSDT"].stop_price
    fills = E.check_stops(b, {"AVAXUSDT": [(NOW, 100.0, stop + 1)]}, NOW)
    assert fills == [] and "AVAXUSDT" in b.positions
    fills = E.check_stops(b, {"AVAXUSDT": [(NOW, 100.0, stop - 1)]}, NOW)
    assert fills[0].reason == "stop" and math.isclose(fills[0].ref_price, stop)
    E.execute(b, "AVAXUSDT", "BUY", 1.0, 100.0, "rebalance", NOW)
    stop = b.positions["AVAXUSDT"].stop_price
    gap = E.check_stops(b, {"AVAXUSDT": [(NOW, stop - 5, stop - 6)]}, NOW)
    assert math.isclose(gap[0].ref_price, stop - 5)
    assert math.isclose(gap[0].fill_price, (stop - 5) * (1 - E.SLIP - E.STOP_SLIP))


def test_rebalance_reaches_targets_and_sells_before_buying():
    b = _book()
    E.execute(b, "ETHUSDT", "BUY", 3.0, 100.0, "rebalance", NOW)
    prices = {"ETHUSDT": 100.0, "BTCUSDT": 50.0}
    equity_start = b.equity(prices)
    fills = E.rebalance(b, {"BTCUSDT": 0.1, "ETHUSDT": 0.0}, prices, {}, NOW)
    assert [f.side for f in fills] == ["SELL", "BUY"]
    assert "ETHUSDT" not in b.positions
    assert math.isclose(b.positions["BTCUSDT"].qty * 50.0, 0.1 * equity_start, rel_tol=1e-9)


def test_kill_switch_closes_everything_and_halts_once():
    b = _book()
    E.execute(b, "DOGEUSDT", "BUY", 1000.0, 0.9, "rebalance", NOW)
    assert E.update_peak_and_kill(b, {"DOGEUSDT": 0.9}, NOW) == [] and not b.halted
    fills = E.update_peak_and_kill(b, {"DOGEUSDT": 0.01}, NOW)
    assert b.halted and b.positions == {} and fills[0].reason == "kill"
    assert E.update_peak_and_kill(b, {}, NOW) == []


def test_rebalance_runs_once_per_signal_day():
    day = T.EPOCH + timedelta(days=5 * 400)
    assert E.rebalance_due(day, set(), False) == "initial"
    assert E.rebalance_due(day, {day.isoformat()}, False) is None
    assert E.rebalance_due(day, {"2026-01-01"}, False) == "rebalance"
    assert E.rebalance_due(day + timedelta(days=1), {"2026-01-01"}, False) is None
    assert E.rebalance_due(day, set(), True) is None
