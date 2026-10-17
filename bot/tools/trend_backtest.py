"""Trend sleeve backtest: parity with the Oct 16 research, then a day-by-day simulation through bot.trend.strategy.

Run: python -m bot.tools.trend_backtest [--cache /tmp/trend_cache.pkl]

A) reference      - research version: weight fixed between rebalances, return = w x daily return
B) daily resize   - research headline version: weight re-sized every day as volatility moves
C) live sim       - quantities held between rebalances, fills at the next open with taker fee + slippage,
                    real funding, optional disaster stop / Binance minimum notional / drift resize / kill switch
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import time
import urllib.parse
import urllib.request
from datetime import timedelta

import numpy as np
import pandas as pd

from bot.trend import strategy as T

BASE = "https://fapi.binance.com"
RESEARCH_RT = 0.0013          # round-trip cost used in the research
TAKER, SLIP, STOP_SLIP = 0.0005, 0.0003, 0.0005
DEFAULT_FUND_DAY = 0.0003     # research fallback when a day has no funding record


def _get(path: str, **params):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(f"{BASE}{path}?{urllib.parse.urlencode(params)}", timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Binance request failed: {path} {params}")


def load_data(cache: str | None) -> dict:
    if cache and os.path.exists(cache):
        return pickle.load(open(cache, "rb"))
    start = int(pd.Timestamp(T.EPOCH, tz="UTC").timestamp() * 1000)
    candles, funding = {}, {}
    for sym in T.MAJORS:
        rows, t = [], start
        while True:
            b = _get("/fapi/v1/klines", symbol=sym, interval="1d", startTime=t, limit=1500)
            if not b:
                break
            rows += b
            t = b[-1][0] + 1
            if len(b) < 1500:
                break
        candles[sym] = pd.DataFrame(
            {k: [float(r[i]) for r in rows] for k, i in (("open", 1), ("high", 2), ("low", 3), ("close", 4))},
            index=pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)).iloc[:-1]  # drop today's open candle
        frows, t = [], start
        while True:
            b = _get("/fapi/v1/fundingRate", symbol=sym, startTime=t, limit=1000)
            if not b:
                break
            frows += b
            t = b[-1]["fundingTime"] + 1
            if len(b) < 1000:
                break
            time.sleep(0.5)
        funding[sym] = pd.Series([float(r["fundingRate"]) for r in frows],
                                 index=pd.to_datetime([r["fundingTime"] for r in frows], unit="ms", utc=True))
    info = _get("/fapi/v1/exchangeInfo")
    min_notional = {s["symbol"]: float(f["notional"]) for s in info["symbols"] if s["symbol"] in T.MAJORS
                    for f in s["filters"] if f["filterType"] == "MIN_NOTIONAL"}
    data = {"candles": candles, "funding": funding, "min_notional": min_notional}
    if cache:
        pickle.dump(data, open(cache, "wb"))
    return data


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    eq = (1 + r).cumprod()
    years = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
    return {"ann_pct": ((1 + r).prod() ** (365 / len(r)) - 1) * 100,
            "sharpe": r.mean() / r.std() * np.sqrt(365),
            "maxdd_pct": (eq / eq.cummax() - 1).min() * 100,
            **{str(y): v * 100 for y, v in years.items()}}


def research(data: dict, daily_resize: bool) -> pd.Series:
    """The research backtest, reproduced (strats.py / compare.py tsmom)."""
    legs = []
    for sym in T.MAJORS:
        c = data["candles"][sym]["close"]
        r = c.pct_change()
        past = c / c.shift(T.LOOKBACK_DAYS) - 1
        sig = pd.Series(np.nan, index=c.index)
        sig.iloc[::T.HOLD_DAYS] = np.where(past.iloc[::T.HOLD_DAYS] > 0, 1.0, 0.0)
        vol = r.rolling(T.VOL_WINDOW).std() * np.sqrt(365)
        scale = (T.VOL_TARGET / vol).clip(upper=T.MAX_SCALE)
        w = (sig.ffill() * scale).shift(1) if daily_resize else (sig * scale).ffill().shift(1)
        f = data["funding"][sym].resample("1D").sum().reindex(c.index).fillna(DEFAULT_FUND_DAY)
        legs.append(w * r - w * f - w.diff().abs() * RESEARCH_RT / 2)
    return pd.concat(legs, axis=1).mean(axis=1)


def live_sim(data: dict, equity0: float = 450.0, stop: bool = False, min_notional: bool = False,
             drift_resize: float | None = None, kill: bool = False) -> tuple[pd.Series, dict]:
    """Day-by-day paper simulation through bot.trend.strategy. Returns daily returns and event counts."""
    syms = T.MAJORS
    C = pd.concat({s: data["candles"][s]["close"] for s in syms}, axis=1)
    O = pd.concat({s: data["candles"][s]["open"] for s in syms}, axis=1).reindex(C.index)
    L = pd.concat({s: data["candles"][s]["low"] for s in syms}, axis=1).reindex(C.index)
    F = pd.concat({s: data["funding"][s].resample("1D").sum() for s in syms}, axis=1).reindex(C.index).fillna(0.0)
    mins = data["min_notional"] if min_notional else {}
    cash, qty, entry, weight = equity0, {s: 0.0 for s in syms}, {}, {}
    peak, halted, curve = equity0, False, {}
    ev = {"rebalances": 0, "orders": 0, "stops": 0, "drift_resizes": 0, "kill": None, "fees": 0.0, "funding": 0.0}

    def fill(sym, side, q, price, extra=0.0):
        nonlocal cash
        px = price * (1 + (TAKER + SLIP + extra) if side == "BUY" else 1 - (TAKER + SLIP + extra))
        cash -= q * px if side == "BUY" else -q * px
        ev["fees"] += q * price * (TAKER + SLIP + extra)
        qty[sym] += q if side == "BUY" else -q
        ev["orders"] += 1

    dates = C.index
    for i in range(1, len(dates)):
        d, prev = dates[i], dates[i - 1]
        opens = O.loc[d]
        if not halted:
            equity_open = cash + sum(qty[s] * opens[s] for s in syms if qty[s])
            if T.is_rebalance_day(prev.date()):
                hist = {s: C[s].loc[:prev].dropna().tolist() for s in syms}
                targets = T.target_weights(hist)
                orders = T.orders_for(targets, equity_open, opens.to_dict(), qty, mins)
                for o in sorted(orders, key=lambda o: o.side != "SELL"):
                    fill(o.symbol, o.side, o.qty, opens[o.symbol])
                    if qty[o.symbol] > 1e-12 and o.side == "BUY":
                        entry[o.symbol] = opens[o.symbol]
                weight = dict(targets)
                ev["rebalances"] += 1
            elif drift_resize:
                for s in syms:
                    if qty[s] <= 0 or not weight.get(s):
                        continue
                    want = weight[s] * equity_open
                    have = qty[s] * opens[s]
                    if abs(have - want) / want > drift_resize and abs(have - want) >= mins.get(s, 0.0):
                        side = "BUY" if want > have else "SELL"
                        fill(s, side, abs(want - have) / opens[s], opens[s])
                        ev["drift_resizes"] += 1
            if stop:
                for s in syms:
                    if qty[s] > 0 and s in entry:
                        sp = T.stop_price(entry[s])
                        if L.loc[d, s] <= sp:
                            fill(s, "SELL", qty[s], min(opens[s], sp), STOP_SLIP)
                            qty[s] = 0.0
                            ev["stops"] += 1
            for s in syms:
                if qty[s]:
                    paid = qty[s] * C.loc[d, s] * F.loc[d, s]
                    cash -= paid
                    ev["funding"] += paid
        equity = cash + sum(qty[s] * C.loc[d, s] for s in syms if qty[s])
        peak = max(peak, equity)
        if kill and not halted and T.kill_switch_hit(equity, peak):
            nxt = dates[i + 1] if i + 1 < len(dates) else d
            for s in syms:
                if qty[s] > 0:
                    fill(s, "SELL", qty[s], O.loc[nxt, s])
                    qty[s] = 0.0
            halted, ev["kill"] = True, str(d.date())
            equity = cash
        curve[d] = equity
    eq = pd.Series(curve)
    return eq.pct_change().dropna(), ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="/tmp/trend_cache.pkl")
    args = ap.parse_args()
    data = load_data(args.cache)
    print(f"data: {min(c.index[0] for c in data['candles'].values()):%Y-%m-%d} -> "
          f"{max(c.index[-1] for c in data['candles'].values()):%Y-%m-%d}; min notional {data['min_notional']}")

    rows = []
    ref = research(data, daily_resize=False)
    rows.append({"run": "A research: fixed weights (target 28.5% / 1.21 / -22.3%)", **stats(ref)})
    hd = research(data, daily_resize=True)
    rows.append({"run": "B research: daily resize from 2021-03 (target 22.7% / 1.02 / -24.2%)",
                 **stats(hd[hd.index >= "2021-03-01"])})
    runs = [
        ("C1 live sim: plain", {}),
        (f"C2 + disaster stop -{T.STOP_PCT:.0%}", {"stop": True}),
        ("C3 + Binance min notional @ $450", {"min_notional": True}),
        ("C4 + drift resize 25%", {"drift_resize": 0.25}),
        ("C5 all rules @ $450 (stop, min notional, kill switch)", {"stop": True, "min_notional": True, "kill": True}),
        ("C6 all rules + drift resize @ $450", {"stop": True, "min_notional": True, "kill": True, "drift_resize": 0.25}),
        ("C7 all rules @ $5,000", {"stop": True, "min_notional": True, "kill": True, "equity0": 5000.0}),
    ]
    events = []
    for name, kw in runs:
        r, ev = live_sim(data, **kw)
        rows.append({"run": name, **stats(r)})
        events.append({"run": name, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in ev.items()}})
    pd.set_option("display.width", 220)
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print()
    print(pd.DataFrame(events).to_string(index=False))


if __name__ == "__main__":
    main()
