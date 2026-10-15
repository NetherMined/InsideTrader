"""Path-label entry model v2: will the bot's own exits make money on a BUY / SELL entered at the next bar?

Labels come from bot.tools.baseline.simulate with the live stop, trailing-stop and cost rules,
so training and live trading can't drift apart. Two pooled LightGBM classifiers give
P(BUY profitable) and P(SELL profitable); score = the higher of the two, side = its side.

Features (FIX_PLAN_OCT14 Phase 8.1/8.2b): price features without hour-of-day, plus Binance
derivatives / order-flow series from derivs_15m (open interest, taker ratio, long/short
ratios, taker-buy share, funding). Each derivs value describes the bar it is stamped with
and is lagged DERIVS_LAG bars, so only data that is public — and already collected — at
decision time is used, in training and live alike.

Training:
  1. walk-forward report: three consecutive test windows over the last 45% of history,
     each trained only on earlier data, early-stopped on the 15% just before it
  2. live model refit on all rows with the median number of trees the folds chose
  3. entry threshold = (100 - TOP_PCT)th percentile of the live model's scores on the newest 20%

Run inside the bot container:
  python -m bot.analysis.path_model --train [--report-dir /app/baseline/<day>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import text

from bot.config import settings

VERSION = "v2"
TIMEFRAME = "15m"
MIN_BARS = 1000
TOP_PCT = 2.0                           # ~150 candidate entries/day over 80 symbols ≈ bot capacity
# Derivs lag in bars. Binance publishes some open-interest / taker periods >15 min late, so the
# previous bar is often missing when live analysis runs; two bars back is reliably collected.
DERIVS_LAG = 2
SPLIT_GAP = timedelta(hours=2)          # one max hold, so no label window crosses a split
FOLDS = ((0.55, 0.70), (0.70, 0.85), (0.85, 1.0))
VALID_SPAN = 0.15
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH = os.path.join(MODELS_DIR, "path_model.pkl")
REPORT_TOPS = (0.10, 0.05, 0.02)
LGBM_PARAMS = dict(learning_rate=0.03, num_leaves=31, min_child_samples=300, subsample=0.8,
                   subsample_freq=1, colsample_bytree=0.8, n_jobs=4, verbose=-1)

PRICE_FEATS = ["ret_1", "ret_4", "ret_16", "ret_96", "atr_pct", "vol_z"]
# Open-interest features are left out: Binance publishes OI history ~2h late for many symbols
# (e.g. TradFi-linked ones), so they would be complete in training but missing at decision time.
DERIVS_FEATS = ["taker_ratio", "taker_ratio_z", "taker_ratio_4", "buy_share", "buy_share_4", "cvd_16",
                "gl_ratio", "gl_chg_4", "top_pos_ratio", "top_pos_chg_4", "top_vs_global", "funding", "funding_z"]
FEATURES = PRICE_FEATS + DERIVS_FEATS


def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    """bars: one symbol, ascending open_time, with candle OHLC and the derivs_15m columns."""
    d = pd.DataFrame(index=bars.index)
    c = bars["close"]
    for n in (1, 4, 16, 96):
        d[f"ret_{n}"] = c.pct_change(n) * 100
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - c.shift()).abs(),
                    (bars["low"] - c.shift()).abs()], axis=1).max(axis=1)
    d["atr_pct"] = tr.rolling(14).mean() / c * 100
    # The bar's own volume comes from the candle: its derivs row is only collected ~2 min after the
    # close, while live analysis runs ~1 min after it.
    cqv = bars["volume"] * c
    d["vol_z"] = cqv / cqv.rolling(96).mean()
    qv = bars["quote_volume"]

    oi = bars["oi_value"].ffill()
    new = pd.DataFrame(index=bars.index)
    for n in (1, 4, 16):
        new[f"oi_chg_{n}"] = oi.pct_change(n) * 100
    new["oi_price_div"] = new["oi_chg_4"] - d["ret_4"]
    tk = bars["taker_ratio"]
    new["taker_ratio"] = tk
    new["taker_ratio_z"] = (tk - tk.rolling(96).mean()) / tk.rolling(96).std()
    new["taker_ratio_4"] = tk.rolling(4).mean()
    share = bars["taker_buy_quote"] / qv.replace(0, np.nan)
    new["buy_share"] = share
    new["buy_share_4"] = share.rolling(4).mean()
    new["cvd_16"] = ((2 * share - 1) * qv).rolling(16).sum() / qv.rolling(16).sum()
    new["gl_ratio"] = bars["gl_ratio"]
    new["gl_chg_4"] = bars["gl_ratio"].pct_change(4) * 100
    new["top_pos_ratio"] = bars["top_pos_ratio"]
    new["top_pos_chg_4"] = bars["top_pos_ratio"].pct_change(4) * 100
    new["top_vs_global"] = bars["top_pos_ratio"] / bars["gl_ratio"]
    new["funding"] = bars["funding"] * 100
    fr = bars["funding"]
    new["funding_z"] = (fr - fr.rolling(288).mean()) / fr.rolling(288).std()
    d = d.join(new.shift(DERIVS_LAG))
    return d.replace([np.inf, -np.inf], np.nan)


def build_labels(bars: pd.DataFrame, atr_pct: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Simulated net % (per $100) of a BUY and a SELL entered at the next bar's open, per row."""
    from bot.tools import baseline as b
    from bot.trading.sizing import calculate_sl_tp_prices, cap_take_profit

    rules = b.current_rules()
    candles = b.Candles([(t.to_pydatetime(), o, h, l, c) for t, o, h, l, c in
                         zip(bars["open_time"], bars["open"], bars["high"], bars["low"], bars["close"])])
    net_buy = np.full(len(bars), np.nan)
    net_sell = np.full(len(bars), np.nan)
    for i in range(len(bars) - 1):
        atr = atr_pct.iloc[i]
        if not np.isfinite(atr) or atr <= 0:
            continue
        quote = candles.o[i + 1]
        for side, out in (("BUY", net_buy), ("SELL", net_sell)):
            sl, tp = calculate_sl_tp_prices(quote, "FUTURES", side=side, tp_pct=settings.take_profit_percent,
                                            leverage=rules.leverage, atr_pct=atr)
            tp = cap_take_profit(quote, side, sl, tp)
            out[i] = b.simulate(side, candles, i + 1, quote, sl, tp, rules, None, None, 100.0 / quote).net_usdt
    return net_buy, net_sell


def fold_slices(panel: pd.DataFrame):
    """Yield (fold, train, valid, test) for the walk-forward windows, with gaps at each boundary."""
    t = panel["time"]
    t0, span = t.min(), t.max() - t.min()
    for i, (a, z) in enumerate(FOLDS, 1):
        test_lo, test_hi = t0 + span * a, t0 + span * z
        valid_lo = t0 + span * (a - VALID_SPAN)
        train = panel[t < valid_lo - SPLIT_GAP]
        valid = panel[(t >= valid_lo) & (t < test_lo - SPLIT_GAP)]
        test = panel[(t >= test_lo) & ((t < test_hi) if z < 1.0 else (t <= test_hi))]
        yield i, train, valid, test


def top_stats(net: np.ndarray, score: np.ndarray, times: pd.Series, mask: np.ndarray) -> dict:
    n = int(mask.sum())
    if n == 0:
        return {"n": 0}
    sel = net[mask]
    days = pd.Series(sel, index=pd.to_datetime(times[mask].values)).groupby(lambda d: d.date()).mean()
    ci = float(1.96 * sel.std() / np.sqrt(n)) if n > 1 else float("nan")
    return {
        "n": n,
        "win_rate": round(float(np.mean(sel > 0)), 3),
        "avg_net_pct": round(float(sel.mean()), 4),
        "ci95_pct": round(ci, 4),
        "ci_low_pct": round(float(sel.mean()) - ci, 4),
        "positive_days": f"{int((days > 0).sum())}/{len(days)}",
    }


class PathModel:
    def __init__(self):
        self.buy = None
        self.sell = None
        self.feature_cols: list[str] = list(FEATURES)
        self.threshold: float | None = None
        self.report: dict = {}
        self.trained_at: datetime | None = None
        self.version = VERSION

    @staticmethod
    def _fit(X, y, X_val=None, y_val=None, n_estimators: int = 2000):
        import lightgbm as lgb
        m = lgb.LGBMClassifier(n_estimators=n_estimators, **LGBM_PARAMS)
        if X_val is not None:
            m.fit(X, y, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(100, verbose=False)])
        else:
            m.fit(X, y)
        return m

    def train(self, panel: pd.DataFrame) -> dict:
        from sklearn.metrics import roc_auc_score

        panel = panel.dropna(subset=PRICE_FEATS + ["net_buy", "net_sell"]).sort_values("time")
        cols = self.feature_cols

        # 1. Walk-forward report on windows each model never saw
        folds, trees_buy, trees_sell, oos = [], [], [], []
        for i, tr, va, te in fold_slices(panel):
            eb = self._fit(tr[cols], tr["net_buy"] > 0, va[cols], va["net_buy"] > 0)
            es = self._fit(tr[cols], tr["net_sell"] > 0, va[cols], va["net_sell"] > 0)
            trees_buy.append(max(eb.best_iteration_ or 1, 1))
            trees_sell.append(max(es.best_iteration_ or 1, 1))
            pb, ps = eb.predict_proba(te[cols])[:, 1], es.predict_proba(te[cols])[:, 1]
            score = np.maximum(pb, ps)
            net = np.where(pb >= ps, te["net_buy"].values, te["net_sell"].values)
            fold = {"fold": i, "test_from": str(te["time"].min()), "test_to": str(te["time"].max()),
                    "rows": len(te), "auc_buy": round(float(roc_auc_score(te["net_buy"] > 0, pb)), 3),
                    "auc_sell": round(float(roc_auc_score(te["net_sell"] > 0, ps)), 3)}
            for top in REPORT_TOPS:
                fold[f"top{int(top * 100)}pct"] = top_stats(net, score, te["time"], score >= np.quantile(score, 1 - top))
            folds.append(fold)
            # Rank within the fold so the pooled out-of-sample view uses each fold's own cut
            oos.append(pd.DataFrame({"time": te["time"].values, "net": net,
                                     "rank": pd.Series(score).rank(pct=True).values}))

        pooled = pd.concat(oos, ignore_index=True)
        pooled_report = {f"top{int(top * 100)}pct": top_stats(pooled["net"].values, pooled["rank"].values,
                                                                pooled["time"], pooled["rank"].values >= 1 - top)
                         for top in REPORT_TOPS}

        # 2. Live model on everything, with the trees the folds chose
        nb, ns = int(np.median(trees_buy)), int(np.median(trees_sell))
        self.buy = self._fit(panel[cols], panel["net_buy"] > 0, n_estimators=nb)
        self.sell = self._fit(panel[cols], panel["net_sell"] > 0, n_estimators=ns)

        # 3. Threshold from the live model's scores on the newest 20%
        recent = panel[panel["time"] >= panel["time"].quantile(0.8)]
        p_buy, p_sell = self.score(recent)
        self.threshold = float(np.percentile(np.maximum(p_buy, p_sell), 100 - TOP_PCT))
        self.trained_at = datetime.now(timezone.utc)
        self.report = {
            "version": VERSION,
            "trained_at": self.trained_at.isoformat(),
            "symbols": int(panel["symbol"].nunique()),
            "rows": len(panel),
            "history": {"from": str(panel["time"].min()), "to": str(panel["time"].max())},
            "trees": {"buy": nb, "sell": ns},
            "top_pct": TOP_PCT,
            "threshold": round(self.threshold, 4),
            "costs": {"fee_pct": settings.taker_fee_rate * 100, "slip_pct": settings.paper_slippage_pct,
                      "stop_extra_pct": settings.paper_stop_slippage_pct},
            "walk_forward": folds,
            "walk_forward_pooled": pooled_report,
        }
        return self.report

    def score(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = X[self.feature_cols]
        return self.buy.predict_proba(X)[:, 1], self.sell.predict_proba(X)[:, 1]

    def is_trained(self) -> bool:
        return self.buy is not None and self.sell is not None and self.threshold is not None

    def save(self, path: str = MODEL_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump({"version": self.version, "buy": self.buy, "sell": self.sell, "feature_cols": self.feature_cols,
                     "threshold": self.threshold, "report": self.report, "trained_at": self.trained_at}, path)

    @classmethod
    def load(cls, path: str = MODEL_PATH) -> "PathModel | None":
        if not os.path.exists(path):
            return None
        d = joblib.load(path)
        if d.get("version") != VERSION:
            return None
        m = cls()
        m.buy, m.sell, m.feature_cols = d["buy"], d["sell"], d["feature_cols"]
        m.threshold, m.report, m.trained_at = d["threshold"], d["report"], d["trained_at"]
        return m


async def load_bars(
    symbols: list[str] | None = None, since: datetime | None = None, min_bars: int = MIN_BARS,
) -> dict[str, pd.DataFrame]:
    """Closed 15m candles with their derivs_15m row (when collected), per symbol.

    Defaults to the collector's universe. The candle side drives the rows, so the newest closed
    bar is kept even before its own derivs row exists (features only read the previous one).
    """
    from bot.db.connection import async_session

    async with async_session() as s:
        if symbols is None:
            symbols = [r[0] for r in (await s.execute(text("SELECT DISTINCT symbol FROM derivs_15m"))).all()]
        rows = (await s.execute(text("""
            SELECT c.symbol, c.open_time, c.open, c.high, c.low, c.close, c.volume,
                   d.oi_value, d.taker_ratio, d.gl_ratio, d.top_pos_ratio,
                   d.taker_buy_quote, d.quote_volume, d.funding
            FROM candles c
            LEFT JOIN derivs_15m d ON d.symbol = c.symbol AND d.open_time = c.open_time
            WHERE c.timeframe = :tf AND c.symbol = ANY(:syms)
              AND c.open_time <= now() - interval '15 minutes'
              AND c.open_time >= :since
              AND c.open_time >= (SELECT min(open_time) FROM derivs_15m x WHERE x.symbol = c.symbol)
            ORDER BY c.symbol, c.open_time
        """), {"tf": TIMEFRAME, "syms": symbols,
               "since": since or datetime(2000, 1, 1, tzinfo=timezone.utc)})).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return {}
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    return {sym: g.reset_index(drop=True) for sym, g in df.groupby("symbol") if len(g) >= min_bars}


_live: dict = {"model": None, "mtime": None}


def _live_model() -> "PathModel | None":
    """The saved model, reloaded whenever nightly training replaces the file."""
    try:
        mtime = os.path.getmtime(MODEL_PATH)
    except OSError:
        return None
    if _live["mtime"] != mtime:
        _live["model"], _live["mtime"] = PathModel.load(), mtime
    return _live["model"]


async def latest_signal(symbol: str) -> dict | None:
    """Score the newest closed 15m bar. None when the model, the bar or the derivs history is missing."""
    model = _live_model()
    if model is None or not model.is_trained():
        return None
    now = datetime.now(timezone.utc)
    bars = (await load_bars([symbol], since=now - timedelta(days=5), min_bars=300)).get(symbol)
    if bars is None:
        return None
    last = bars.iloc[-1]
    if last["open_time"] < now - timedelta(minutes=31):
        return None                                   # newest closed bar missing — stale data
    lagged = bars.iloc[-1 - DERIVS_LAG]
    if pd.isna(lagged["taker_ratio"]) or pd.isna(lagged["gl_ratio"]):
        return None                                   # derivs for the lagged bar not collected
    feat = build_features(bars).iloc[[-1]]
    if feat[PRICE_FEATS].isna().any(axis=None):
        return None
    p_buy, p_sell = model.score(feat)
    p_buy, p_sell = float(p_buy[0]), float(p_sell[0])
    return {
        "score": max(p_buy, p_sell), "side": "BUY" if p_buy >= p_sell else "SELL",
        "p_buy": p_buy, "p_sell": p_sell, "threshold": model.threshold,
        "atr_pct": float(feat["atr_pct"].iloc[0]), "bar_time": last["open_time"].isoformat(),
    }


def to_prediction(sig: dict | None) -> tuple[float, float]:
    """(predicted_change_pct, confidence) for the existing pipeline. Below threshold means no trade.

    The ±0.5 is a nominal size; confidence maps score above threshold onto [0.60, 0.92] so it
    clears the old confidence checks and ranks stronger scores first.
    """
    if not sig or sig["score"] < sig["threshold"]:
        return 0.0, 0.0
    above = (sig["score"] - sig["threshold"]) / max(1e-6, 1.0 - sig["threshold"])
    return (0.5 if sig["side"] == "BUY" else -0.5), round(0.60 + 0.32 * min(1.0, above), 4)


async def build_panel(symbols: list[str] | None = None) -> pd.DataFrame:
    parts = []
    for sym, bars in (await load_bars(symbols)).items():
        feat = build_features(bars)
        net_buy, net_sell = build_labels(bars, feat["atr_pct"])
        feat["time"], feat["symbol"] = bars["open_time"].values, sym
        feat["net_buy"], feat["net_sell"] = net_buy, net_sell
        parts.append(feat)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


async def train_and_save(report_dir: str | None = None) -> dict:
    panel = await build_panel()
    if panel.empty:
        raise RuntimeError("no symbols with enough joined 15m candles + derivs history")
    model = PathModel()
    report = await asyncio.to_thread(model.train, panel)
    model.save()
    if report_dir:
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        (Path(report_dir) / "path_model.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    p = report["walk_forward_pooled"]
    logger.info(
        f"Path model {VERSION} trained on {report['symbols']} symbols / {report['rows']} rows — "
        f"threshold {report['threshold']} (top {TOP_PCT}%); walk-forward pooled top2% "
        f"{p['top2pct'].get('avg_net_pct')}% ±{p['top2pct'].get('ci95_pct')}, top10% "
        f"{p['top10pct'].get('avg_net_pct')}% ±{p['top10pct'].get('ci95_pct')}"
    )
    return report


async def daily_path_training(stop_event: asyncio.Event) -> None:
    """Retrain at 01:30 UTC in a child process (label building is pure Python and would hold
    the GIL, stalling the trading loop if run in a thread)."""
    import sys

    run_at = timedelta(hours=1, minutes=30)
    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_run = day + run_at if now < day + run_at else day + timedelta(days=1) + run_at
        # Poll the wall clock: a long sleep does not count time while the host is suspended.
        while not stop_event.is_set() and datetime.now(timezone.utc) < next_run:
            await asyncio.sleep(60)
        if stop_event.is_set():
            break
        report_dir = os.path.join(settings.baseline_dir, (next_run - timedelta(days=1)).strftime("%Y-%m-%d"))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "bot.analysis.path_model", "--train", "--report-dir", report_dir,
        )
        code = await proc.wait()
        if code != 0:
            logger.error(f"Path model training failed (exit {code})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--report-dir")
    a = ap.parse_args()
    if a.train:
        report = asyncio.run(train_and_save(a.report_dir))
        print(json.dumps({k: report[k] for k in ("walk_forward", "walk_forward_pooled", "threshold", "trees")},
                         indent=2, default=str))


if __name__ == "__main__":
    main()
