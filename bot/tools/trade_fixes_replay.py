"""Replay the live paper trades under different exit rules and entry filters (PLAN_TRADE_FIXES.md, Phase 3).

Run: python -m bot.tools.trade_fixes_replay [--start 2026-10-14T20:00] [--end 2026-10-21T08:00]

Each closed trade is re-run on 1m candles from its real entry time, side, stop, target and size. Filters drop
trades; they cannot add new ones, so a filter that trades less loses fewer dollars without having an edge. A change
only counts if its net per trade beats the current rules in both halves of the period.
Writes bot/results/trade_fixes_replay.json for the Test Results page.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt.async_support as ccxt
from sqlalchemy import text

from bot.db.connection import async_session
from bot.tools.baseline import Candles, Packets, Rules, current_rules, simulate, _load

OUT = Path(__file__).resolve().parent.parent / "results" / "trade_fixes_replay.json"
BTC_SMA_DAYS = 20
CONTROL = "A. Current rules"

EXIT_RULES = {  # name -> raw price move that switches the trailing stop on (None = live rule)
    CONTROL: None,
    "B. Trail starts at +0.6%": 0.6,
    "B. Trail starts at +0.8%": 0.8,
    "B. Trail starts at +1.0%": 1.0,
    "C. No trailing stop": 1e9,
}

FILTERS = {
    "D. Swing <= 1.0x stop": lambda t: t["atr_to_stop"] is not None and t["atr_to_stop"] <= 1.0,
    "D. Swing <= 1.5x stop": lambda t: t["atr_to_stop"] is not None and t["atr_to_stop"] <= 1.5,
    "E. Side follows BTC trend": lambda t: t["btc_below_sma"] is not None
        and (t["side"] == "SELL") == t["btc_below_sma"],
}


async def _btc_daily() -> list[tuple[datetime, float]]:
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    try:
        rows = await ex.fetch_ohlcv("BTC/USDT:USDT", "1d", limit=90)
    finally:
        await ex.close()
    return [(datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc), float(r[4])) for r in rows]


def _btc_below_sma(daily: list[tuple[datetime, float]], ts: datetime) -> bool | None:
    """True if BTC's last completed daily close before `ts` is below its 20-day average."""
    closed = [c for t, c in daily if t + timedelta(days=1) <= ts]
    if len(closed) < BTC_SMA_DAYS:
        return None
    return closed[-1] < sum(closed[-BTC_SMA_DAYS:]) / BTC_SMA_DAYS


async def _prepare(start: datetime, end: datetime) -> tuple[dict, list[dict]]:
    data = await _load(start, end)
    trades = [t for t in data["trades"] if t["closed_at"] is not None]
    async with async_session() as s:
        inds = dict((await s.execute(text(
            "SELECT id, extra->'entry_indicators' FROM trades WHERE id = ANY(:ids)"
        ), {"ids": [t["id"] for t in trades]})).all())
    btc = await _btc_daily()
    for t in trades:
        ind = inds.get(t["id"]) or {}
        t["entry_model"] = ind.get("entry_model")
        sl_pct = abs(t["stop_loss_price"] - t["entry_price"]) / t["entry_price"] * 100
        atr = ind.get("atr_pct")
        t["atr_to_stop"] = float(atr) / sl_pct if atr is not None and sl_pct > 0 else None
        t["btc_below_sma"] = _btc_below_sma(btc, t["opened_at"])
    return data, trades


def _replay(trades: list[dict], data: dict, rules: Rules) -> list[dict]:
    out = []
    for t in trades:
        candles: Candles | None = data["candles"].get(t["symbol"])
        i0 = candles.index_at_or_after(t["opened_at"]) if candles else None
        if i0 is None:
            continue
        side = t["side"]
        # Recorded entry already includes paper slippage; simulate() adds it back
        quote = t["entry_price"] / (1 + rules.slip) if side == "BUY" else t["entry_price"] / (1 - rules.slip)
        # Path-model trades skip researcher invalidation live, so they do here too
        packets: Packets | None = None if t["entry_model"] == "path" else data["packets"].get(t["symbol"])
        sim = simulate(side, candles, i0, quote, t["stop_loss_price"], t["take_profit_price"],
                       replace(rules, leverage=t["leverage"] or rules.leverage), packets, None, t["quantity"])
        out.append({**t, "sim": sim})
    return out


def _stats(rows: list[dict]) -> dict:
    nets = [r["sim"].net_usdt for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    return {
        "trades": len(nets),
        "net_usd": round(sum(nets), 2),
        "win_pct": round(100 * len(wins) / len(nets), 1) if nets else 0.0,
        "avg_win_usd": round(sum(wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_usd": round(sum(losses) / len(losses), 3) if losses else 0.0,
        "stop_outs": sum(r["sim"].exit_reason == "stop_loss" for r in rows),
        "per_trade_usd": round(sum(nets) / len(nets), 3) if nets else 0.0,
    }


def _row(test: str, exit_rule: str, filters: list[str], replayed: list[dict], mid: datetime) -> dict:
    kept = [r for r in replayed if all(FILTERS[f](r) for f in filters)]
    first = _stats([r for r in kept if r["opened_at"] < mid])
    second = _stats([r for r in kept if r["opened_at"] >= mid])
    return {
        "test": test,
        "exit_rule": exit_rule,
        "filter": " + ".join(filters) or "none",
        **_stats(kept),
        "first_half_usd": first["net_usd"],
        "second_half_usd": second["net_usd"],
        "first_half_per_trade": first["per_trade_usd"],
        "second_half_per_trade": second["per_trade_usd"],
    }


async def run(start: datetime, end: datetime) -> dict:
    data, trades = await _prepare(start, end)
    mid = trades[len(trades) // 2]["opened_at"]
    base = current_rules()
    replays = {name: _replay(trades, data, replace(base, trail_activation_raw_pct=act))
               for name, act in EXIT_RULES.items()}

    rows = [_row(name, name, [], replays[name], mid) for name in EXIT_RULES]
    rows += [_row(f, CONTROL, [f], replays[CONTROL], mid) for f in FILTERS]
    control = rows[0]

    def passes(r: dict) -> bool:
        return (r["first_half_per_trade"] > control["first_half_per_trade"]
                and r["second_half_per_trade"] > control["second_half_per_trade"])

    for r in rows:
        r["beats_control_both_halves"] = r is not control and passes(r)

    # F: best exit rule plus every filter that passed on its own.
    # Ties go to the current rules, which come first
    best_exit = max(rows[:len(EXIT_RULES)], key=lambda r: r["per_trade_usd"])["exit_rule"]
    good = [r["filter"] for r in rows[len(EXIT_RULES):] if r["beats_control_both_halves"]]
    combo = _row("F. " + " + ".join([best_exit] + good), best_exit, good, replays[best_exit], mid)
    combo["beats_control_both_halves"] = passes(combo)
    rows.append(combo)

    matched = sum(r["sim"].exit_reason == r["close_reason"] for r in replays[CONTROL])
    actual = {
        "trades": len(trades),
        "net_usd": round(sum(t["pnl_usdt_true"] or 0.0 for t in trades), 2),
        "exit_match_pct": round(100 * matched / len(replays[CONTROL]), 1),
    }
    return {"rows": rows, "actual": actual, "mid": mid}


async def main_async(start: datetime, end: datetime) -> None:
    res = await run(start, end)
    a = res["actual"]
    doc = {
        "key": "trade_fixes_replay",
        "title": "Exit and filter replay of live trades",
        "description": (
            f"The bot's {a['trades']} live paper trades from {start:%b %d} to {end:%b %d} (actual net "
            f"{'-' if a['net_usd'] < 0 else ''}${abs(a['net_usd']):.2f}) replayed on 1-minute candles. The current rules reproduce {a['exit_match_pct']}% "
            f"of the real exit reasons. A change only counts if its net per trade beats the current rules in both halves "
            f"(split at {res['mid']:%b %d %H:%M} UTC)."
        ),
        "phase": "Phase 7",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "rows": res["rows"],
    }
    OUT.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    for r in res["rows"]:
        print(f"{r['test']:<58} n={r['trades']:<4} net={r['net_usd']:>7} win={r['win_pct']:>5}% "
              f"avgW={r['avg_win_usd']:>6} avgL={r['avg_loss_usd']:>7} SL={r['stop_outs']:<3} "
              f"per={r['per_trade_usd']:>7} h1={r['first_half_per_trade']:>7} h2={r['second_half_per_trade']:>7} "
              f"pass={r['beats_control_both_halves']}")
    print(json.dumps(a))
    print(f"Wrote {OUT}")


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-10-14T20:00")
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"))
    args = ap.parse_args()
    asyncio.run(main_async(_parse(args.start), _parse(args.end)))


if __name__ == "__main__":
    main()
