"""Test Results comparison builder.

Run (api container): python -m pytest api/tests/test_research.py
"""
import math
from datetime import datetime, timezone

from api.research import build_comparison, build_trend, next_trend_rebalance

UTC = timezone.utc

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



def test_next_rebalance_is_the_next_close_whose_run_is_ahead():
    # Oct 17, 2026 is a rebalance close (days since 2021-01-01 divisible by 5); it runs Oct 18 00:05 UTC.
    assert next_trend_rebalance(datetime(2026, 10, 17, 12, 0, tzinfo=UTC)) == {
        "signal_day": "2026-10-17", "runs_at": "2026-10-18T00:05:00+00:00"}
    assert next_trend_rebalance(datetime(2026, 10, 18, 0, 3, tzinfo=UTC))["signal_day"] == "2026-10-17"
    assert next_trend_rebalance(datetime(2026, 10, 18, 0, 6, tzinfo=UTC))["signal_day"] == "2026-10-22"
    assert next_trend_rebalance(datetime(2026, 10, 15, 9, 0, tzinfo=UTC))["signal_day"] == "2026-10-17"


ACCT = {"start_capital": 500.0, "cash": 300.0, "peak_equity": 520.0, "halted": False, "halted_reason": None,
        "started_at": "2026-10-16T22:41:00+00:00"}
TLIVE = {"trend": {"equity": 494.0}, "holdings": [
    {"symbol": "BTCUSDT", "qty": 0.001, "entry_price": 80000.0, "price": 120000.0, "value_usd": 120.0, "pnl_pct": 50.0,
     "weight_pct": 24.29, "stop_price": 48000.0, "opened": "2026-10-16"},
    {"symbol": "SOLUSDT", "qty": 0.5, "entry_price": 140.0, "price": 148.0, "value_usd": 74.0, "pnl_pct": 5.7,
     "weight_pct": 14.98, "stop_price": 84.0, "opened": "2026-10-16"}]}
FUNDING = [{"time": "2026-10-17T00:00:00+00:00", "symbol": "BTCUSDT", "rate": 0.0001, "amount_usdt": 0.012},
           {"time": "2026-10-17T08:00:00+00:00", "symbol": "BTCUSDT", "rate": 0.0001, "amount_usdt": 0.012},
           {"time": "2026-10-17T00:00:00+00:00", "symbol": "SOLUSDT", "rate": -0.0001, "amount_usdt": -0.007}]


def test_trend_not_running_without_account():
    out = build_trend(None, None, None, [], [], [], None, datetime(2026, 10, 17, tzinfo=UTC))
    assert out["running"] is False and out["status"] is None


def test_trend_status_maths_targets_and_funding():
    last = {"signal_day": "2026-10-15", "targets": {"BTCUSDT": 0.25, "SOLUSDT": 0.15, "ETHUSDT": 0.0}}
    fills = [{"time": "2026-10-16T22:41:00+00:00", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.001,
              "fill_price": 80000.0, "fee_usdt": 0.04, "reason": "initial"}]
    out = build_trend(ACCT, TLIVE, last, [], fills, FUNDING, "2026-10-17T12:05:00+00:00",
                      datetime(2026, 10, 17, 12, 10, tzinfo=UTC))
    st = out["status"]
    assert st["state"] == "running" and math.isclose(st["equity"], 494.0)
    assert math.isclose(st["invested"], 194.0) and math.isclose(st["invested_pct"], 194.0 / 494.0 * 100)
    assert math.isclose(st["return_pct"], -1.2) and math.isclose(st["drawdown_pct"], (494.0 / 520.0 - 1) * 100)
    assert math.isclose(st["kill_equity"], 520.0 * 0.65) and math.isclose(st["funding_paid"], 0.017)
    t = {r["symbol"]: r for r in out["targets"]}
    assert len(t) == 10 and t["BTCUSDT"]["signal"] == "long" and math.isclose(t["BTCUSDT"]["target_pct"], 25.0)
    assert t["ETHUSDT"]["signal"] == "flat" and t["DOGEUSDT"]["held_pct"] == 0.0
    assert out["funding_by_symbol"][0] == {"symbol": "BTCUSDT", "payments": 2, "amount_usdt": 0.024}
    assert out["funding_recent"][0]["time"] == "2026-10-17T08:00:00+00:00"
    assert math.isclose(out["fills"][0]["notional"], 80.0)
    assert out["next_rebalance"]["signal_day"] == "2026-10-17"
