"""Stop, take-profit and leverage geometry (Phase 3 of FIX_PLAN_OCT14).

Run: python -m bot.tests.test_risk_geometry   (or pytest bot/tests)
"""

from bot.trading.sizing import (
    stop_distance_pct, max_stop_pct, clamp_stop, cap_take_profit, calculate_sl_tp_prices,
)


def _pct(entry: float, price: float) -> float:
    return abs(entry - price) / entry * 100


def test_max_stop_respects_leverage_cap():
    assert abs(max_stop_pct(3) - 1.25) < 1e-9      # 3.8/3 = 1.27, sl_max_pct 1.25 wins
    assert abs(max_stop_pct(5) - 0.76) < 1e-9      # 3.8/5 = 0.76 wins at 5x


def test_stop_distance_clamped_to_atr_band():
    assert abs(stop_distance_pct(0.4, 3) - 0.8) < 1e-9    # quiet coin -> floor
    assert abs(stop_distance_pct(1.0, 3) - 1.0) < 1e-9    # 1x ATR
    assert abs(stop_distance_pct(4.9, 3) - 1.25) < 1e-9   # 龙虾-style ATR -> cap
    assert abs(stop_distance_pct(None, 3) - 0.8) < 1e-9


def test_leveraged_loss_never_above_3_8pct():
    for lev in (1, 2, 3, 5, 10):
        for atr in (0.2, 1.0, 3.0, 10.0):
            assert stop_distance_pct(atr, lev) * lev <= 3.8 + 1e-9


def test_clamp_zone_stop():
    # A zone stop 0.3% away is inside noise -> widened to 0.8%
    assert abs(_pct(100, clamp_stop(100, "BUY", 99.7, 3)) - 0.8) < 1e-6
    # A zone stop 2.5% away -> pulled in to 1.25%
    assert abs(_pct(100, clamp_stop(100, "SELL", 102.5, 3)) - 1.25) < 1e-6
    # In-band stop untouched
    assert abs(clamp_stop(100, "BUY", 99.0, 3) - 99.0) < 1e-6


def test_tp_capped_at_three_stops():
    # Oct 14 #6757-style zone TP 40% away -> capped at 3 x 1% = 3%
    assert abs(cap_take_profit(100, "BUY", 99.0, 140.0) - 103.0) < 1e-6
    assert abs(cap_take_profit(100, "SELL", 101.0, 60.0) - 97.0) < 1e-6
    # A nearer TP is kept
    assert abs(cap_take_profit(100, "BUY", 99.0, 102.0) - 102.0) < 1e-6


def test_fixed_path_uses_atr_stop():
    sl, tp = calculate_sl_tp_prices(100.0, "FUTURES", side="BUY", sl_pct=0.5, tp_pct=1.2, leverage=3, atr_pct=1.1)
    assert abs(_pct(100, sl) - 1.1) < 1e-6
    assert tp > 100


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
