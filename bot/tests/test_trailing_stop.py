from datetime import datetime, timedelta, timezone

from bot.tools.baseline import Candles, Rules, simulate


def _candles(ohlc):
    t0 = datetime(2026, 10, 16, tzinfo=timezone.utc)
    return Candles([(t0 + timedelta(minutes=i), *row) for i, row in enumerate(ohlc)])


# Long from 100: runs to 101, dips 0.3% to 100.7, then rallies through TP at 103.
PATH = _candles([
    (100.0, 101.0, 100.0, 101.0),
    (101.0, 101.0, 100.7, 100.7),
    (100.7, 103.5, 100.6, 103.0),
])


def _run(reversal_pct, leverage=5):
    rules = Rules(leverage=leverage, trail_min_raw_pct=0.40, trail_reversal_raw_pct=reversal_pct)
    return simulate("BUY", PATH, 0, 100.0, 99.0, 103.0, rules, None, None, 1.0)


def test_raw_trail_rides_through_small_pullback():
    assert _run(0.50).exit_reason == "take_profit"


def test_tight_trail_exits_on_small_pullback():
    assert _run(0.10).exit_reason == "trailing_stop"


def test_trail_width_does_not_depend_on_leverage():
    assert _run(0.50, leverage=3).exit_reason == _run(0.50, leverage=10).exit_reason == "take_profit"
