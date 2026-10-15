"""Path-label entry model v2 (Phase 8.2b of FIX_PLAN_OCT14).

Run: python -m bot.tests.test_path_model   (or pytest bot/tests)
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from bot.analysis import path_model as pm
from bot.tools import baseline as b
from bot.trading.sizing import calculate_sl_tp_prices, cap_take_profit
from bot.config import settings


def _bars(n=400, seed=1):
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open_time": pd.to_datetime([t0 + timedelta(minutes=15 * i) for i in range(n)], utc=True),
        "open": open_, "close": close,
        "high": np.maximum(open_, close) * (1 + rng.uniform(0, 0.003, n)),
        "low": np.minimum(open_, close) * (1 - rng.uniform(0, 0.003, n)),
        "oi_value": 1e6 * (1 + np.cumsum(rng.normal(0, 0.01, n))),
        "taker_ratio": rng.uniform(0.8, 1.2, n), "gl_ratio": rng.uniform(0.8, 2, n),
        "top_pos_ratio": rng.uniform(0.8, 2, n), "quote_volume": rng.uniform(1e5, 2e5, n),
        "taker_buy_quote": rng.uniform(4e4, 1e5, n), "funding": rng.normal(1e-4, 5e-5, n),
        "volume": rng.uniform(1e3, 2e3, n),
    })


def test_labels_match_baseline_simulate():
    bars = _bars()
    atr = pd.Series(0.9, index=bars.index)
    net_buy, net_sell = pm.build_labels(bars, atr)
    candles = b.Candles([(t.to_pydatetime(), o, h, l, c) for t, o, h, l, c in
                         zip(bars["open_time"], bars["open"], bars["high"], bars["low"], bars["close"])])
    rules = b.current_rules()
    for row in (10, 150, 300):
        q = candles.o[row + 1]
        for side, labels in (("BUY", net_buy), ("SELL", net_sell)):
            sl, tp = calculate_sl_tp_prices(q, "FUTURES", side=side, tp_pct=settings.take_profit_percent,
                                            leverage=rules.leverage, atr_pct=0.9)
            tp = cap_take_profit(q, side, sl, tp)
            expected = b.simulate(side, candles, row + 1, q, sl, tp, rules, None, None, 100.0 / q).net_usdt
            assert abs(labels[row] - expected) < 1e-12
    assert np.isnan(net_buy[-1])  # no next bar to enter on


def test_derivs_features_are_lagged():
    bars = _bars()
    f = pm.build_features(bars)
    i, lag = 200, pm.DERIVS_LAG
    assert f.loc[i, "taker_ratio"] == bars.loc[i - lag, "taker_ratio"]
    assert f.loc[i, "gl_ratio"] == bars.loc[i - lag, "gl_ratio"]
    assert abs(f.loc[i, "funding"] - bars.loc[i - lag, "funding"] * 100) < 1e-15
    # price features use the row's own (closed) bar
    assert abs(f.loc[i, "ret_1"] - (bars.loc[i, "close"] / bars.loc[i - 1, "close"] - 1) * 100) < 1e-9
    # changing derivs values within the lag window must not change row i's features
    b2 = bars.copy()
    b2.loc[i - lag + 1:i, ["taker_ratio", "gl_ratio", "oi_value", "funding"]] *= 3
    f2 = pm.build_features(b2)
    pd.testing.assert_series_equal(f.loc[i, pm.DERIVS_FEATS], f2.loc[i, pm.DERIVS_FEATS], check_names=False)
    assert "hour" not in f.columns


def test_newest_bars_need_no_derivs_rows():
    """Live analysis runs before the newest bars' derivs rows are published / collected."""
    bars = _bars()
    full = pm.build_features(bars).iloc[-1]
    b2 = bars.copy()
    b2.loc[b2.index[-pm.DERIVS_LAG:], ["oi_value", "taker_ratio", "gl_ratio", "top_pos_ratio",
                          "taker_buy_quote", "quote_volume", "funding"]] = np.nan
    partial = pm.build_features(b2).iloc[-1]
    pd.testing.assert_series_equal(full[pm.FEATURES], partial[pm.FEATURES], check_names=False)


def test_to_prediction():
    assert pm.to_prediction(None) == (0.0, 0.0)
    assert pm.to_prediction({"score": 0.60, "threshold": 0.62, "side": "BUY"}) == (0.0, 0.0)
    pct, conf = pm.to_prediction({"score": 0.62, "threshold": 0.62, "side": "SELL"})
    assert pct == -0.5 and conf == 0.60
    pct, conf = pm.to_prediction({"score": 1.0, "threshold": 0.62, "side": "BUY"})
    assert pct == 0.5 and conf == 0.92


def _panel(n_per_symbol=1500, seed=2):
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    parts = []
    for s in range(3):
        X = pd.DataFrame(rng.normal(size=(n_per_symbol, len(pm.FEATURES))), columns=pm.FEATURES)
        signal = X[pm.FEATURES[0]].values
        X["time"] = pd.to_datetime([t0 + timedelta(minutes=15 * i) for i in range(n_per_symbol)])
        X["symbol"] = f"S{s}/USDT"
        X["net_buy"] = signal * 0.3 + rng.normal(0, 1, n_per_symbol)
        X["net_sell"] = -signal * 0.3 + rng.normal(0, 1, n_per_symbol)
        parts.append(X)
    return pd.concat(parts, ignore_index=True)


def test_walk_forward_folds_are_ordered_with_gaps():
    panel = _panel()
    prev_test_hi = None
    for _, tr, va, te in pm.fold_slices(panel):
        assert tr["time"].max() + pm.SPLIT_GAP <= va["time"].min() + timedelta(minutes=15)
        assert va["time"].max() + pm.SPLIT_GAP <= te["time"].min() + timedelta(minutes=15)
        if prev_test_hi is not None:
            assert te["time"].min() > prev_test_hi   # test windows don't overlap
        prev_test_hi = te["time"].max()
    assert prev_test_hi == panel["time"].max()       # last fold reaches the newest bar


def test_train_save_load_round_trip():
    m = pm.PathModel()
    report = m.train(_panel())
    assert m.is_trained() and 0 < m.threshold < 1
    assert len(report["walk_forward"]) == 3
    assert report["walk_forward"][0]["auc_buy"] > 0.55  # the synthetic signal is learnable
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "path_model.pkl")
        m.save(path)
        m2 = pm.PathModel.load(path)
    assert m2.threshold == m.threshold and m2.feature_cols == m.feature_cols
    X = _panel(50, seed=9)
    np.testing.assert_allclose(m.score(X)[0], m2.score(X)[0])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
