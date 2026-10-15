"""Ensemble stacking pipeline for FeaturesGrok.

Three classifiers (XGBoost, LightGBM, CatBoost) vote on whether the price
will rise beyond fees, fall beyond fees, or stay flat within the next horizon.
Labels are -1 / 0 / +1, encoded internally as 0 / 1 / 2.

Additional models:
- LightGBM momentum score from short-term price/volume features
  (proxy for 1m order-book imbalance until L2 depth is available)
- CatBoost regime classifier (Trend vs Range) on higher-timeframe features
"""

from __future__ import annotations

import os
import time
from typing import Any

import joblib
import numpy as np
import pandas as pd
from loguru import logger
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

try:
    from xgboost import XGBClassifier
except ImportError:  # pragma: no cover
    XGBClassifier = None  # type: ignore

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None  # type: ignore

try:
    from catboost import CatBoostClassifier
except ImportError:  # pragma: no cover
    CatBoostClassifier = None  # type: ignore

from bot.analysis.features import FEATURE_COLS
from bot.config import settings

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
MIN_TRAIN_ROWS = 400
MODEL_MAX_AGE_HOURS = 24

# Short-term features used for the LightGBM momentum score
MOMENTUM_COLS = [
    "ret_1", "ret_4", "vol_ratio", "atr_pct", "rsi", "bb_pct",
    "macd_hist", "adx", "vol_surge", "stoch_k",
]


class EnsemblePredictor:
    """Voting ensemble of XGB / LGBM / CatBoost classifiers.

    predict() returns (direction, confidence, votes, momentum_score, regime).
    direction: +1 / -1 / 0 (0 = flat or no majority)
    confidence: chosen side's share of the up+down probability (0.5-1.0 when a side is chosen)
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._models: dict[str, Any] = {}
        self._momentum_model: Any = None
        self._regime_model: Any = None
        self._feature_cols: list[str] = []
        self._trained = False

    def _make_classifiers(self) -> dict[str, Any]:
        models: dict[str, Any] = {}
        if XGBClassifier is not None:
            models["xgb"] = XGBClassifier(
                n_estimators=250,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                n_jobs=-1,
                verbosity=0,
                early_stopping_rounds=20,
                eval_metric="mlogloss",
            )
        if lgb is not None:
            models["lgbm"] = lgb.LGBMClassifier(
                n_estimators=250,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                n_jobs=-1,
                verbose=-1,
            )
        if CatBoostClassifier is not None:
            models["cat"] = CatBoostClassifier(
                iterations=250,
                depth=6,
                learning_rate=0.05,
                random_seed=42,
                verbose=False,
                early_stopping_rounds=20,
            )
        if not models:
            raise RuntimeError("No gradient boosting libraries available")
        return models

    def train(
        self,
        X: pd.DataFrame,
        y_cls: pd.Series,
        regime_y: pd.Series | None = None,
    ) -> dict:
        if len(X) < MIN_TRAIN_ROWS:
            logger.warning(f"{self.symbol}: insufficient data ({len(X)} < {MIN_TRAIN_ROWS})")
            return {}

        self._feature_cols = [c for c in FEATURE_COLS if c in X.columns]
        X = X[self._feature_cols]
        y_enc = y_cls.astype(int) + 1

        X_train, X_test, y_train, y_test = train_test_split(
            X, y_enc, test_size=0.2, shuffle=False
        )
        if set(y_train.unique()) != {0, 1, 2}:
            logger.warning(f"{self.symbol}: ensemble needs up/flat/down examples, got {sorted(y_train.unique())}")
            return {}

        self._models = self._make_classifiers()
        metrics: dict[str, float] = {}

        for name, model in self._models.items():
            try:
                if name == "xgb":
                    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
                elif name == "cat":
                    model.fit(X_train, y_train, eval_set=(X_test, y_test), verbose=False)
                else:
                    model.fit(X_train, y_train, eval_set=[(X_test, y_test)])
                preds = model.predict(X_test)
                metrics[f"{name}_acc"] = float(accuracy_score(y_test, preds))
            except Exception as exc:
                logger.warning(f"{self.symbol}: {name} train failed ({exc})")

        # LightGBM momentum model on short-term subset (proxy for order-flow score)
        mom_cols = [c for c in MOMENTUM_COLS if c in X.columns]
        if lgb is not None and len(mom_cols) >= 4:
            try:
                self._momentum_model = lgb.LGBMClassifier(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.08,
                    random_state=42,
                    n_jobs=-1,
                    verbose=-1,
                )
                # Probability of any fee-sized move (up or down)
                self._momentum_model.fit(X_train[mom_cols], (y_train != 1).astype(int))
            except Exception as exc:
                logger.debug(f"{self.symbol}: momentum model failed ({exc})")

        # CatBoost regime classifier (Trend=1 vs Range=0) when labels provided
        if CatBoostClassifier is not None and regime_y is not None and len(regime_y) == len(X):
            try:
                r_train, r_test = train_test_split(regime_y, test_size=0.2, shuffle=False)
                self._regime_model = CatBoostClassifier(
                    iterations=150,
                    depth=5,
                    learning_rate=0.06,
                    random_seed=42,
                    verbose=False,
                )
                self._regime_model.fit(X_train, r_train.iloc[: len(X_train)], verbose=False)
            except Exception as exc:
                logger.debug(f"{self.symbol}: regime model failed ({exc})")

        self._trained = bool(self._models)
        logger.info(
            f"{self.symbol}: ensemble trained — "
            + ", ".join(f"{k}={v:.2%}" for k, v in metrics.items())
        )
        return metrics

    def predict(self, X: pd.DataFrame) -> dict[str, Any]:
        """Return ensemble decision.

        Keys: direction (-1/0/+1), confidence (0-1), votes, momentum_score, regime
        """
        empty = {
            "direction": 0,
            "confidence": 0.0,
            "votes": {},
            "momentum_score": 0.0,
            "regime": "UNKNOWN",
        }
        if not self._trained or not self._models:
            return empty

        row = X.tail(1)[self._feature_cols] if self._feature_cols else X.tail(1)
        if row.isnull().any().any():
            return empty

        votes: dict[str, int] = {}
        side_probs: dict[str, dict[int, float]] = {}
        for name, model in self._models.items():
            try:
                proba = model.predict_proba(row)[0]
                classes = [int(c) for c in np.ravel(model.classes_)]
                p = {cls - 1: float(proba[i]) for i, cls in enumerate(classes)}
                votes[name] = max(p, key=p.get)
                side_probs[name] = p
            except Exception:
                continue

        if not votes:
            return empty

        # A side needs a strict majority of all models; flat or a split vote means no trade
        direction = 0
        for side in (1, -1):
            if sum(1 for v in votes.values() if v == side) * 2 > len(votes):
                direction = side

        # Flat is settled by the vote; confidence is P(our side | a fee-sized move happens),
        # which keeps the 0.5-1.0 scale the binary up/down thresholds were tuned for.
        confidence = 0.0
        if direction != 0:
            p_side = float(np.mean([p.get(direction, 0.0) for p in side_probs.values()]))
            p_opp = float(np.mean([p.get(-direction, 0.0) for p in side_probs.values()]))
            confidence = p_side / (p_side + p_opp) if p_side + p_opp > 0 else 0.0
        confidence = max(0.0, min(confidence, 0.92))

        momentum_score = 0.0
        if self._momentum_model is not None:
            mom_cols = [c for c in MOMENTUM_COLS if c in row.columns]
            if mom_cols:
                try:
                    mproba = self._momentum_model.predict_proba(row[mom_cols])[0]
                    momentum_score = float(mproba[1] if len(mproba) > 1 else mproba[0])
                except Exception:
                    pass

        regime = "UNKNOWN"
        if self._regime_model is not None:
            try:
                rpred = int(self._regime_model.predict(row)[0])
                regime = "TREND" if rpred == 1 else "RANGE"
            except Exception:
                pass

        return {
            "direction": direction,
            "confidence": confidence,
            "votes": votes,
            "momentum_score": momentum_score,
            "regime": regime,
        }

    def is_stale(self, max_age_hours: int = MODEL_MAX_AGE_HOURS) -> bool:
        path = self._path("xgb")
        if not os.path.exists(path):
            return True
        age = (time.time() - os.path.getmtime(path)) / 3600
        return age > max_age_hours

    def _path(self, name: str) -> str:
        safe = self.symbol.replace("/", "_")
        return os.path.join(MODELS_DIR, f"{safe}_ens3_{name}.pkl")

    def save(self) -> None:
        os.makedirs(MODELS_DIR, exist_ok=True)
        for name, model in self._models.items():
            joblib.dump(model, self._path(name))
        if self._momentum_model is not None:
            joblib.dump(self._momentum_model, self._path("momentum"))
        if self._regime_model is not None:
            joblib.dump(self._regime_model, self._path("regime"))
        joblib.dump(self._feature_cols, self._path("cols"))

    def load(self) -> bool:
        cols_path = self._path("cols")
        if not os.path.exists(cols_path):
            return False
        self._feature_cols = joblib.load(cols_path)
        loaded = False
        for name in ("xgb", "lgbm", "cat"):
            p = self._path(name)
            if os.path.exists(p):
                self._models[name] = joblib.load(p)
                loaded = True
        mom_path = self._path("momentum")
        if os.path.exists(mom_path):
            self._momentum_model = joblib.load(mom_path)
        reg_path = self._path("regime")
        if os.path.exists(reg_path):
            self._regime_model = joblib.load(reg_path)
        self._trained = loaded
        return loaded
