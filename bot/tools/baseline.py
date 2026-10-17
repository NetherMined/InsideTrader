"""Hindsight baseline: replay a day's signals through the bot's exit rules on 1m candles.

Outputs to <baseline_dir>/<YYYY-MM-DD>/:
  signals.csv        every directional prediction, simulated with the current rules
  opportunities.csv  clean moves that were available (target hit before the stop)
  summary.json       hit rates and simulated P&L by group, opportunity coverage
  validation.json    (--validate) real trades replayed with the rules they ran under

Usage (inside the bot container):
  python -m bot.tools.baseline                      # previous UTC day
  python -m bot.tools.baseline --date 2026-10-13
  python -m bot.tools.baseline --from 2026-10-13T19:20 --to 2026-10-14T17:22 --validate

1m candles are kept for about 48h, so a day must be built within a day of closing.

Not simulated: zone-based stops/targets (the fixed ATR path is used), the stale-packet
1h structure flip, and sub-minute price moves (each minute is walked open→low→high→close
for green candles and open→high→low→close for red ones).
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import csv
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import text

from bot.config import settings
from bot.db.connection import async_session
from bot.trading.executor import _TRAIL_ACTIVATION_PCT
from bot.trading.invalidation import packet_invalidation, is_past_invalidation
from bot.trading.pnl import compute_pnl
from bot.trading.sizing import calculate_sl_tp_prices, cap_take_profit, stop_distance_pct

SLIP = settings.paper_slippage_pct / 100            # per fill; no historical order books, so a flat measured rate
STOP_SLIP = settings.paper_stop_slippage_pct / 100  # extra on stop-loss exits
MAX_HOLD_MIN = 120
SCRATCH_TIMEOUT_MIN = 45
OPP_HORIZON_MIN = 60
OPP_COST_PCT = 2 * settings.taker_fee_rate * 100 + 2 * SLIP * 100
SIM_NOTIONAL = 100.0         # simulated $ per trade, so P&L reads as % of notional
KEEP_DAYS = 30


@dataclass
class Rules:
    leverage: int
    trail_min_raw_pct: float
    trail_reversal_raw_pct: float = settings.trail_reversal_raw_pct
    inv_side_check: bool = True
    inv_min_hold_min: float = settings.invalidation_min_hold_minutes
    inv_buffer_pct: float = settings.invalidation_buffer_pct
    stale_min: float = settings.research_stale_minutes
    slip: float = SLIP
    stop_slip: float = STOP_SLIP
    fee_rate: float = settings.taker_fee_rate


def current_rules() -> Rules:
    return Rules(leverage=settings.futures_leverage, trail_min_raw_pct=settings.trail_min_profit_raw_pct)


@dataclass
class Sim:
    exit_reason: str
    exit_price: float
    minutes_held: float
    raw_pct: float
    net_usdt: float
    mfe15: float
    mae15: float


class Candles:
    """1m candles for one symbol, indexed by open time."""

    def __init__(self, rows: list[tuple]):
        self.t = [r[0] for r in rows]
        self.o = [r[1] for r in rows]
        self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]
        self.c = [r[4] for r in rows]

    def index_at_or_after(self, ts: datetime) -> int | None:
        i = bisect.bisect_left(self.t, ts)
        return i if i < len(self.t) else None

    def path(self, i: int) -> list[float]:
        o, h, l, c = self.o[i], self.h[i], self.l[i], self.c[i]
        return [o, l, h, c] if c >= o else [o, h, l, c]

    def close_after(self, i0: int, minutes: int) -> float | None:
        j = i0 + minutes
        return self.c[j] if j < len(self.c) else None


class Packets:
    """Research packets for one symbol, looked up by time."""

    def __init__(self, rows: list[dict]):
        self.rows = sorted(rows, key=lambda r: r["packet_time"])
        self.t = [r["packet_time"] for r in self.rows]

    def latest(self, ts: datetime) -> dict | None:
        i = bisect.bisect_right(self.t, ts) - 1
        return self.rows[i] if i >= 0 else None


def _as_packet(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "pred_1h_close_side": row["pred_side"],
        "invalidation": row["invalidation"],
        "arm": row["arm"],
        "confidence": row["confidence"],
        "packet_time": row["packet_time"],
    }


def simulate(
    side: str, candles: Candles, i0: int, quote: float, stop_loss: float, take_profit: float,
    rules: Rules, packets: Packets | None, entry_packet: dict | None, quantity: float,
) -> Sim:
    """Walk the live exit loop over 1m price paths, starting at candle i0's open."""
    lev = rules.leverage
    entry = quote * (1 + rules.slip) if side == "BUY" else quote * (1 - rules.slip)
    sign = 1.0 if side == "BUY" else -1.0
    max_raw_loss = 3.8 / max(lev, 1)
    trail_raw = rules.trail_reversal_raw_pct
    peak: float | None = None
    mfe15 = mae15 = 0.0
    t0 = candles.t[i0]
    exit_reason, exit_quote, held = "end_of_data", candles.c[-1], 0.0

    last = min(len(candles.t) - 1, i0 + MAX_HOLD_MIN)
    done = False
    for i in range(i0, last + 1):
        held = (candles.t[i] - t0).total_seconds() / 60 + 1
        pkt = packets.latest(candles.t[i] + timedelta(minutes=1)) if packets else None
        for k, p in enumerate(candles.path(i)):
            # The live loop polls every few seconds, so a level crossed inside a candle fills near
            # the level; only the open can gap through it.
            gap = k == 0
            raw = (p / entry - 1) * 100 * sign
            if held <= 15:
                mfe15, mae15 = max(mfe15, raw), min(mae15, raw)
            # Stop loss (or the 3.8% leveraged cap)
            sl_hit = (p <= stop_loss) if side == "BUY" else (p >= stop_loss)
            if sl_hit or raw <= -max_raw_loss:
                cap = entry * (1 - max_raw_loss / 100) if side == "BUY" else entry * (1 + max_raw_loss / 100)
                exit_quote = max(stop_loss, cap) if side == "BUY" else min(stop_loss, cap)
                exit_reason, done = "stop_loss", True
                break
            if (p >= take_profit) if side == "BUY" else (p <= take_profit):
                exit_quote, exit_reason, done = take_profit, "take_profit", True
                break
            # Trailing stop
            if raw * lev >= _TRAIL_ACTIVATION_PCT:
                peak = p if peak is None else (max(peak, p) if side == "BUY" else min(peak, p))
            if peak is not None:
                trigger = peak * (1 - trail_raw / 100) if side == "BUY" else peak * (1 + trail_raw / 100)
                crossed = (p < trigger) if side == "BUY" else (p > trigger)
                fill_at = p if gap else trigger
                if crossed and (fill_at / entry - 1) * 100 * sign >= rules.trail_min_raw_pct:
                    exit_quote, exit_reason, done = fill_at, "trailing_stop", True
                    break
            # Researcher invalidation
            live = _as_packet(pkt)
            if live and (candles.t[i] - live["packet_time"]).total_seconds() / 60 <= rules.stale_min:
                if rules.inv_side_check:
                    inv = packet_invalidation(live, side) or packet_invalidation(entry_packet, side)
                else:
                    inv = float(live["invalidation"]) if live.get("invalidation") else None
                if inv and held >= rules.inv_min_hold_min and is_past_invalidation(side, p, inv, rules.inv_buffer_pct):
                    level = inv * (1 - rules.inv_buffer_pct / 100) if side == "BUY" else inv * (1 + rules.inv_buffer_pct / 100)
                    exit_quote, exit_reason, done = (p if gap else level), "structure_invalidation", True
                    break
        if done:
            break
        c_raw = (candles.c[i] / entry - 1) * 100 * sign
        if held >= SCRATCH_TIMEOUT_MIN and c_raw < 0 and c_raw * lev <= -1.0:
            exit_quote, exit_reason = candles.c[i], "negative_timeout"
            break
        if held >= MAX_HOLD_MIN:
            exit_quote, exit_reason = candles.c[i], "max_hold"
            break

    slip = rules.slip + (rules.stop_slip if exit_reason == "stop_loss" else 0.0)
    fill = exit_quote * (1 - slip) if side == "BUY" else exit_quote * (1 + slip)
    pnl = compute_pnl(side, entry, fill, quantity, lev, rules.fee_rate)
    return Sim(exit_reason, fill, round(held, 1), pnl.raw_pct, pnl.net_usdt, mfe15, mae15)


async def _load(start: datetime, end: datetime) -> dict:
    horizon = end + timedelta(minutes=MAX_HOLD_MIN + 5)
    async with async_session() as s:
        preds = (await s.execute(text("""
            SELECT id, symbol, created_at, current_price, predicted_change_pct, confidence,
                   (features->>'atr_pct')::float AS atr_pct, (features->>'adx')::float AS adx,
                   COALESCE(features->>'entry_model', 'ensemble') AS entry_model,
                   (features->>'path_score')::float AS path_score
            FROM predictions
            WHERE created_at >= :a AND created_at < :b AND predicted_change_pct <> 0
            ORDER BY created_at
        """), {"a": start, "b": end})).mappings().all()
        symbols = sorted({p["symbol"] for p in preds})
        trades = (await s.execute(text("""
            SELECT id, symbol, side, entry_price, stop_loss_price, take_profit_price, quantity, leverage,
                   opened_at, closed_at, exit_price, pnl_usdt_true, extra->>'close_reason' AS close_reason
            FROM trades WHERE opened_at >= :a AND opened_at < :b AND status = 'CLOSED'
            ORDER BY opened_at
        """), {"a": start, "b": end})).mappings().all()
        symbols = sorted(set(symbols) | {t["symbol"] for t in trades})
        candle_rows = (await s.execute(text("""
            SELECT symbol, open_time, open, high, low, close FROM candles
            WHERE timeframe = '1m' AND symbol = ANY(:syms) AND open_time >= :a AND open_time < :b
            ORDER BY symbol, open_time
        """), {"syms": symbols, "a": start - timedelta(minutes=5), "b": horizon})).all()
        pkt_rows = (await s.execute(text("""
            SELECT symbol, packet_time, pred_side, invalidation, arm, confidence FROM research_packets
            WHERE symbol = ANY(:syms) AND packet_time >= :a AND packet_time < :b
        """), {"syms": symbols, "a": start - timedelta(hours=1), "b": horizon})).mappings().all()

    by_sym: dict[str, list] = defaultdict(list)
    for r in candle_rows:
        by_sym[r[0]].append((r[1], r[2], r[3], r[4], r[5]))
    pk_sym: dict[str, list] = defaultdict(list)
    for r in pkt_rows:
        pk_sym[r["symbol"]].append(dict(r))
    return {
        "predictions": [dict(p) for p in preds],
        "trades": [dict(t) for t in trades],
        "candles": {k: Candles(v) for k, v in by_sym.items()},
        "packets": {k: Packets(v) for k, v in pk_sym.items()},
    }


def _research_view(packets: Packets | None, ts: datetime, side: str) -> tuple[str, dict | None]:
    pkt = _as_packet(packets.latest(ts)) if packets else None
    if not pkt or (ts - pkt["packet_time"]).total_seconds() / 60 > settings.research_stale_minutes:
        return "none", None
    agrees = packet_invalidation(pkt, side) is not None or (
        {"bull": "BUY", "bear": "SELL"}.get(pkt["pred_1h_close_side"]) == side
    )
    if not agrees:
        return "contradicts", None
    return ("agree_armed" if pkt.get("arm") else "agree_unarmed"), pkt


def build_signals(data: dict, rules: Rules) -> list[dict]:
    trades_by_sym: dict[str, list] = defaultdict(list)
    for t in data["trades"]:
        trades_by_sym[t["symbol"]].append(t)
    out = []
    for p in data["predictions"]:
        sym, ts = p["symbol"], p["created_at"]
        candles = data["candles"].get(sym)
        if not candles:
            continue
        i0 = candles.index_at_or_after(ts + timedelta(seconds=1))
        if i0 is None or i0 + 1 >= len(candles.t):
            continue
        side = "BUY" if p["predicted_change_pct"] > 0 else "SELL"
        quote = candles.o[i0]
        atr = p["atr_pct"] or 0.0
        sl, tp = calculate_sl_tp_prices(quote, "FUTURES", side=side, tp_pct=settings.take_profit_percent,
                                        leverage=rules.leverage, atr_pct=atr)
        tp = cap_take_profit(quote, side, sl, tp)
        packets = data["packets"].get(sym)
        research, entry_pkt = _research_view(packets, ts, side)
        sim = simulate(side, candles, i0, quote, sl, tp, rules, packets, entry_pkt, SIM_NOTIONAL / quote)
        c15, c60 = candles.close_after(i0, 15), candles.close_after(i0, 60)
        sign = 1 if side == "BUY" else -1
        traded = any(
            t["side"] == side and ts <= t["opened_at"] < ts + timedelta(minutes=15)
            for t in trades_by_sym.get(sym, [])
        )
        out.append({
            "prediction_id": p["id"], "symbol": sym, "time": ts.isoformat(), "hour": ts.hour, "side": side,
            "entry_model": p["entry_model"], "path_score": p["path_score"],
            "confidence": round(p["confidence"], 4),
            "above_threshold": p["confidence"] >= settings.confidence_threshold,
            "atr_pct": round(atr, 3), "adx": round(p["adx"] or 0.0, 1), "research": research,
            "traded": traded, "entry_quote": quote,
            "sl_pct": round(abs(quote - sl) / quote * 100, 3), "tp_pct": round(abs(tp - quote) / quote * 100, 3),
            "exit_reason": sim.exit_reason, "minutes_held": sim.minutes_held,
            "sim_raw_pct": round(sim.raw_pct, 3), "sim_net_usdt": round(sim.net_usdt, 4),
            "move15_pct": round((c15 / quote - 1) * 100 * sign, 3) if c15 else None,
            "move60_pct": round((c60 / quote - 1) * 100 * sign, 3) if c60 else None,
            "mfe15_pct": round(sim.mfe15, 3), "mae15_pct": round(sim.mae15, 3),
        })
    return out


def build_opportunities(data: dict, signals: list[dict], rules: Rules, start: datetime, end: datetime) -> list[dict]:
    """Every 15m slot per symbol: did a clean move (2 stops + costs before 1 stop) happen within 60m?"""
    atr_by_sym: dict[str, list] = defaultdict(list)
    for p in data["predictions"]:
        if p["atr_pct"]:
            atr_by_sym[p["symbol"]].append((p["created_at"], p["atr_pct"]))
    sig_by_sym: dict[str, list] = defaultdict(list)
    for s in signals:
        sig_by_sym[s["symbol"]].append(s)
    out = []
    for sym, candles in data["candles"].items():
        atrs = atr_by_sym.get(sym)
        if not atrs:
            continue
        atr_t = [a[0] for a in atrs]
        for i0, ts in enumerate(candles.t):
            if ts < start or ts >= end or ts.minute % 15:
                continue
            j = bisect.bisect_right(atr_t, ts) - 1
            atr = atrs[max(j, 0)][1]
            sl_pct = stop_distance_pct(atr, rules.leverage)
            target_pct = 2 * sl_pct + OPP_COST_PCT
            quote = candles.o[i0]
            for side in ("BUY", "SELL"):
                sign = 1 if side == "BUY" else -1
                hit_min, best = None, 0.0
                for k in range(i0, min(len(candles.t), i0 + OPP_HORIZON_MIN)):
                    stopped = False
                    for p in candles.path(k):
                        move = (p / quote - 1) * 100 * sign
                        best = max(best, move)
                        if move <= -sl_pct:
                            stopped = True
                            break
                        if move >= target_pct:
                            hit_min = k - i0 + 1
                            break
                    if stopped or hit_min:
                        break
                if not hit_min:
                    continue
                near = [s for s in sig_by_sym.get(sym, [])
                        if abs((datetime.fromisoformat(s["time"]) - ts).total_seconds()) <= 15 * 60]
                if any(s["side"] == side and s["traded"] for s in near):
                    coverage = "traded"
                elif any(s["side"] == side for s in near):
                    coverage = "signaled_not_traded"
                elif near:
                    coverage = "signaled_opposite"
                else:
                    coverage = "no_signal"
                out.append({
                    "symbol": sym, "time": ts.isoformat(), "hour": ts.hour, "side": side,
                    "atr_pct": round(atr, 3), "sl_pct": round(sl_pct, 3), "target_pct": round(target_pct, 3),
                    "minutes_to_target": hit_min, "best_move_pct": round(best, 3), "coverage": coverage,
                })
    out.sort(key=lambda r: r["time"])
    return out


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    wins = [r["sim_net_usdt"] for r in rows if r["sim_net_usdt"] > 0]
    losses = [r["sim_net_usdt"] for r in rows if r["sim_net_usdt"] <= 0]
    m15 = [r["move15_pct"] for r in rows if r["move15_pct"] is not None]
    m60 = [r["move60_pct"] for r in rows if r["move60_pct"] is not None]
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 3),
        "hit15": round(sum(1 for m in m15 if m > 0) / len(m15), 3) if m15 else None,
        "hit60": round(sum(1 for m in m60 if m > 0) / len(m60), 3) if m60 else None,
        "sim_net_sum": round(sum(r["sim_net_usdt"] for r in rows), 3),
        "sim_net_avg": round(sum(r["sim_net_usdt"] for r in rows) / n, 4),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
    }


def _bucket(v: float, edges: list[float]) -> str:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"{lo}-{hi}"
    return f">={edges[-1]}" if v >= edges[-1] else f"<{edges[0]}"


def build_summary(signals: list[dict], opps: list[dict], data: dict, rules: Rules,
                  start: datetime, end: datetime) -> dict:
    groupers = {
        "confidence": lambda r: _bucket(r["confidence"], [0.5, 0.55, 0.6, 0.65, 0.7, 0.8]),
        "side": lambda r: r["side"],
        "atr_band": lambda r: _bucket(r["atr_pct"], [0, 0.5, 1.0, 1.5, 2.5]),
        "adx_band": lambda r: _bucket(r["adx"], [0, 20, 25, 35, 50]),
        "research": lambda r: r["research"],
        "hour_utc": lambda r: str(r["hour"]).zfill(2),
        "exit_reason": lambda r: r["exit_reason"],
        "traded": lambda r: str(r["traded"]),
        "entry_model": lambda r: r["entry_model"],
    }
    groups = {}
    for name, key in groupers.items():
        g: dict[str, list] = defaultdict(list)
        for r in signals:
            g[key(r)].append(r)
        groups[name] = {k: _stats(v) for k, v in sorted(g.items())}
    by_sym: dict[str, list] = defaultdict(list)
    for r in signals:
        by_sym[r["symbol"]].append(r)
    sym_stats = {k: _stats(v) for k, v in by_sym.items() if len(v) >= 3}
    ranked = sorted(sym_stats.items(), key=lambda kv: kv[1]["sim_net_sum"])
    above = [r for r in signals if r["above_threshold"]]
    coverage: dict[str, int] = defaultdict(int)
    for o in opps:
        coverage[o["coverage"]] += 1
    real_net = sum((t["pnl_usdt_true"] or 0.0) for t in data["trades"])
    return {
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "rules": {**asdict(rules), "sl_min_pct": settings.sl_min_pct, "sl_max_pct": settings.sl_max_pct,
                  "tp_max_sl_multiple": settings.tp_max_sl_multiple, "sim_notional_usdt": SIM_NOTIONAL,
                  "confidence_threshold": settings.confidence_threshold,
                  "not_simulated": ["zone stops/targets", "stale-packet 1h structure flip", "sub-minute moves"]},
        "all_signals": _stats(signals),
        "above_threshold": _stats(above),
        "groups": groups,
        "symbols_worst": dict(ranked[:10]),
        "symbols_best": dict(reversed(ranked[-10:])),
        "opportunities": {"n": len(opps), "by_coverage": dict(coverage),
                          "by_side": {s: sum(1 for o in opps if o["side"] == s) for s in ("BUY", "SELL")}},
        "real_trades": {"n": len(data["trades"]), "net_usdt_true": round(real_net, 3)},
    }


def build_validation(data: dict) -> dict:
    """Replay real trades with the rules they ran under (old side-blind invalidation, $2 trail gate)."""
    rows = []
    for t in data["trades"]:
        candles = data["candles"].get(t["symbol"])
        if not candles or t["exit_price"] is None:
            continue
        i0 = candles.index_at_or_after(t["opened_at"])
        if i0 is None:
            continue
        lev = int(t["leverage"] or 1)
        quote = t["entry_price"] / (1 + 0.001) if t["side"] == "BUY" else t["entry_price"] / (1 - 0.001)
        notional = t["quantity"] * t["entry_price"]
        legacy = Rules(
            leverage=lev,
            trail_min_raw_pct=2.0 / (notional * lev) * 100 if notional > 0 else 0.0,
            inv_side_check=False, inv_min_hold_min=0.0, inv_buffer_pct=0.0,
            slip=0.001, stop_slip=0.001, fee_rate=0.0004,
        )
        sim = simulate(t["side"], candles, i0, quote, t["stop_loss_price"], t["take_profit_price"],
                       legacy, data["packets"].get(t["symbol"]), None, t["quantity"])
        rows.append({
            "trade_id": t["id"], "symbol": t["symbol"], "side": t["side"],
            "real_reason": t["close_reason"], "sim_reason": sim.exit_reason,
            "real_net": round(t["pnl_usdt_true"] or 0.0, 4), "sim_net": round(sim.net_usdt, 4),
        })
    n = len(rows)
    real = sum(r["real_net"] for r in rows)
    simd = sum(r["sim_net"] for r in rows)
    return {
        "n": n,
        "exit_reason_match": round(sum(r["real_reason"] == r["sim_reason"] for r in rows) / n, 3) if n else None,
        "real_net_sum": round(real, 3),
        "sim_net_sum": round(simd, 3),
        "mean_abs_error_per_trade": round(sum(abs(r["real_net"] - r["sim_net"]) for r in rows) / n, 4) if n else None,
        "real_wins": sum(r["real_net"] > 0 for r in rows),
        "sim_wins": sum(r["sim_net"] > 0 for r in rows),
        "trades": rows,
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _prune(root: Path) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date()
    for d in root.iterdir():
        try:
            if d.is_dir() and datetime.strptime(d.name, "%Y-%m-%d").date() < cutoff:
                shutil.rmtree(d)
        except ValueError:
            continue


async def run_baseline(start: datetime, end: datetime, label: str, validate: bool = False) -> Path:
    data = await _load(start, end)
    rules = current_rules()

    def _compute():
        signals = build_signals(data, rules)
        opps = build_opportunities(data, signals, rules, start, end)
        summary = build_summary(signals, opps, data, rules, start, end)
        return signals, opps, summary, (build_validation(data) if validate else None)

    signals, opps, summary, validation = await asyncio.to_thread(_compute)
    root = Path(settings.baseline_dir)
    out = root / label
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "signals.csv", signals)
    _write_csv(out / "opportunities.csv", opps)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    if validation is not None:
        (out / "validation.json").write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")
    _prune(root)
    logger.info(
        f"Baseline {label}: {len(signals)} signals, {len(opps)} opportunities, "
        f"sim net ${summary['all_signals'].get('sim_net_sum', 0)} per ${SIM_NOTIONAL:.0f}/trade → {out}"
    )
    return out


async def daily_baseline(stop_event: asyncio.Event) -> None:
    """Build the previous UTC day once its last signal has had MAX_HOLD_MIN to play out."""
    run_at = timedelta(hours=2, minutes=15)
    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_run = day + run_at if now < day + run_at else day + timedelta(days=1) + run_at
        # Poll the wall clock: a long sleep does not count time while the host is suspended.
        while not stop_event.is_set() and datetime.now(timezone.utc) < next_run:
            await asyncio.sleep(60)
        if stop_event.is_set():
            break
        target = (next_run - run_at) - timedelta(days=1)
        label = target.strftime("%Y-%m-%d")
        if (Path(settings.baseline_dir) / label / "summary.json").exists():
            continue
        try:
            await run_baseline(target, target + timedelta(days=1), label)
        except Exception as e:
            logger.error(f"Daily baseline {label} failed: {e}")


def _parse(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="UTC day YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--from", dest="start", help="window start, ISO UTC")
    ap.add_argument("--to", dest="end", help="window end, ISO UTC")
    ap.add_argument("--validate", action="store_true", help="also replay real trades under their own rules")
    a = ap.parse_args()
    if a.start and a.end:
        start, end = _parse(a.start), _parse(a.end)
        label = f"{start:%Y-%m-%dT%H%M}_{end:%Y-%m-%dT%H%M}"
    else:
        day = _parse(a.date) if a.date else (datetime.now(timezone.utc) - timedelta(days=1))
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end, label = start + timedelta(days=1), start.strftime("%Y-%m-%d")
    asyncio.run(run_baseline(start, end, label, validate=a.validate))


if __name__ == "__main__":
    main()
