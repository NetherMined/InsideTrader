"""Trend sleeve rules (Phase 6.1).

Run: python -m pytest bot/tests/test_trend_strategy.py
"""
import math
import statistics
from datetime import date, timedelta

from bot.trend import strategy as T


def _trend(n, start=100.0, step=0.01, wiggle=0.02):
    """Rising (step > 0) or falling series with alternating noise so volatility is non-zero."""
    out, p = [], start
    for i in range(n):
        p *= 1 + step + (wiggle if i % 2 else -wiggle)
        out.append(p)
    return out


def test_rebalance_calendar_counts_from_epoch():
    assert T.is_rebalance_day(T.EPOCH)
    assert T.is_rebalance_day(T.EPOCH + timedelta(days=5))
    assert not T.is_rebalance_day(T.EPOCH + timedelta(days=3))
    assert T.is_rebalance_day(date(2026, 10, 12)) == ((date(2026, 10, 12) - T.EPOCH).days % 5 == 0)


def test_momentum_uses_close_lookback_days_back():
    closes = list(range(1, 31))  # 30 closes
    assert T.momentum(closes) == closes[-1] / closes[-1 - T.LOOKBACK_DAYS] - 1
    assert T.momentum(closes[: T.LOOKBACK_DAYS]) is None


def test_annual_vol_matches_sample_std_of_last_window_returns():
    closes = _trend(60)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - T.VOL_WINDOW, len(closes))]
    assert math.isclose(T.annual_vol(closes), statistics.stdev(rets) * math.sqrt(365), rel_tol=1e-12)
    assert T.annual_vol(closes[: T.VOL_WINDOW]) is None


def test_flat_when_trend_is_down_or_history_short():
    assert T.target_weight(_trend(60, step=-0.01)) == 0.0
    assert T.target_weight(_trend(20)) == 0.0


def test_long_weight_is_vol_scaled_and_capped():
    closes = _trend(60)
    vol = T.annual_vol(closes)
    assert math.isclose(T.target_weight(closes), min(T.MAX_SCALE, T.VOL_TARGET / vol) / len(T.MAJORS))
    calm = _trend(60, step=0.002, wiggle=0.0005)
    assert T.target_weight(calm) == T.MAX_SCALE / len(T.MAJORS)


def test_target_weights_cover_universe_and_missing_symbols_are_flat():
    w = T.target_weights({"BTCUSDT": _trend(60)})
    assert set(w) == set(T.MAJORS)
    assert w["BTCUSDT"] > 0 and w["ETHUSDT"] == 0.0


def test_orders_move_positions_to_targets():
    orders = T.orders_for({"BTCUSDT": 0.1, "ETHUSDT": 0.0}, 1000.0, {"BTCUSDT": 50.0, "ETHUSDT": 10.0},
                          {"ETHUSDT": 3.0})
    by = {o.symbol: o for o in orders}
    assert by["BTCUSDT"].side == "BUY" and math.isclose(by["BTCUSDT"].qty, 2.0)
    assert by["ETHUSDT"].side == "SELL" and math.isclose(by["ETHUSDT"].qty, 3.0)


def test_no_order_when_already_on_target():
    assert T.orders_for({"BTCUSDT": 0.1}, 1000.0, {"BTCUSDT": 50.0}, {"BTCUSDT": 2.0}) == []


def test_target_below_min_notional_is_treated_as_flat():
    orders = T.orders_for({"BTCUSDT": 0.04}, 450.0, {"BTCUSDT": 100.0}, {"BTCUSDT": 0.1}, {"BTCUSDT": 50.0})
    assert len(orders) == 1 and orders[0].side == "SELL" and math.isclose(orders[0].qty, 0.1)


def test_stop_and_kill_switch_levels():
    assert math.isclose(T.stop_price(100.0), 100.0 * (1 - T.STOP_PCT))
    assert T.kill_switch_hit(64.0, 100.0)
    assert not T.kill_switch_hit(70.0, 100.0)
