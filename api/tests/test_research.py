"""Test Results comparison builder.

Run (api container): python -m pytest api/tests/test_research.py
"""
import math

from api.research import build_comparison

LIVE = {"as_of": "2026-10-18T06:05:00+00:00", "halted": False, "halted_reason": None,
        "trend": {"equity": 520.0, "open_positions": 7}, "current_bot": {"equity": 490.0, "open_positions": 2},
        "holdings": [{"symbol": "BTCUSDT", "qty": 0.001, "entry_price": 80000.0, "price": 81000.0, "value_usd": 81.0,
                      "pnl_pct": 1.25, "weight_pct": 15.6, "stop_price": 48000.0, "opened": "2026-10-16"}]}
SNAPS = [("2026-10-16", "trend", 500.0, 7), ("2026-10-16", "current_bot", 500.0, 0),
         ("2026-10-17", "trend", 530.0, 7), ("2026-10-17", "current_bot", 480.0, 1),
         ("2026-10-18", "trend", 999.0, 7), ("2026-10-18", "current_bot", 999.0, 0)]


def test_not_running_without_an_account():
    out = build_comparison(None, None, [], None, {}, "2026-10-18")
    assert out["running"] is False and out["summary"] == [] and out["daily"] == []


def test_live_value_replaces_todays_snapshot_and_drives_summary():
    out = build_comparison(500.0, "2026-10-16T22:41:00", SNAPS, LIVE,
                           {"trend": {"2026-10-16": 7, "2026-10-18": 3}, "current_bot": {"2026-10-17": 4}}, "2026-10-18")
    trend = [d for d in out["daily"] if d["strategy"] == "Trend strategy"]
    assert [d["equity"] for d in trend] == [500.0, 530.0, 520.0]
    s = {r["strategy"]: r for r in out["summary"]}
    assert math.isclose(s["Trend strategy"]["return_pct"], 4.0)
    assert math.isclose(s["Current bot"]["return_pct"], -2.0)
    assert s["Trend strategy"]["trades"] == 10 and s["Current bot"]["open_positions"] == 2
    assert out["days"] == 3 and out["holdings"][0]["symbol"] == "BTCUSDT"


def test_drawdown_runs_from_the_peak_including_the_start():
    out = build_comparison(500.0, None, SNAPS[:4], None, {}, "2026-10-17")
    bot = [d for d in out["daily"] if d["strategy"] == "Current bot"]
    assert math.isclose(bot[-1]["drawdown_pct"], -4.0)
    trend = {r["strategy"]: r for r in out["summary"]}["Trend strategy"]
    assert trend["max_drawdown_pct"] == 0.0 and math.isclose(trend["return_pct"], 6.0)
    assert [d["trades"] for d in bot] == [0, 0]
