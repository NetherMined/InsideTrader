"""Researcher invalidation rules (Phase 2 of FIX_PLAN_OCT14).

Run: python -m bot.tests.test_invalidation   (or pytest bot/tests)
"""

from datetime import datetime, timedelta, timezone

from bot.trading.invalidation import packet_invalidation, is_past_invalidation
from bot.trading.executor import _minutes_open

BULL = {"pred_1h_close_side": "bull", "invalidation": 3.113}
BEAR = {"pred_1h_close_side": "bear", "invalidation": 0.0320}


def test_contradicting_packet_has_no_level():
    # Oct 14 #6757: BUY checked against a bear packet's hour_high and closed after 5s
    assert packet_invalidation(BEAR, "BUY") is None
    assert packet_invalidation(BULL, "SELL") is None


def test_agreeing_packet_level():
    assert packet_invalidation(BULL, "BUY") == 3.113
    assert packet_invalidation(BEAR, "SELL") == 0.0320


def test_missing_or_bad_packet():
    assert packet_invalidation(None, "BUY") is None
    assert packet_invalidation({}, "BUY") is None
    assert packet_invalidation({"pred_1h_close_side": "bull", "invalidation": None}, "BUY") is None
    assert packet_invalidation({"pred_1h_close_side": "bull", "invalidation": "x"}, "BUY") is None


def test_pre_entry_already_broken():
    # Entry at 3.061 with a bull level of 3.113 is already invalidated -> skip entry
    assert is_past_invalidation("BUY", 3.061, 3.113)
    assert not is_past_invalidation("BUY", 3.20, 3.113)
    assert is_past_invalidation("SELL", 0.0325, 0.0320)


def test_buffer():
    # 0.3% buffer: BUY at 0.2% under the level holds, 0.4% under closes
    assert not is_past_invalidation("BUY", 100 * 0.998, 100, 0.3)
    assert is_past_invalidation("BUY", 100 * 0.996, 100, 0.3)
    assert not is_past_invalidation("SELL", 100 * 1.002, 100, 0.3)
    assert is_past_invalidation("SELL", 100 * 1.004, 100, 0.3)


def test_minutes_open():
    now = datetime.now(timezone.utc)
    assert abs(_minutes_open({"opened_at": now - timedelta(minutes=3)}) - 3) < 0.05
    naive = (now - timedelta(minutes=10)).replace(tzinfo=None)
    assert abs(_minutes_open({"opened_at": naive}) - 10) < 0.05
    assert _minutes_open({}) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
