"""Ensemble stacking pipeline for FeaturesGrok.

Three classifiers (XGBoost, LightGBM, CatBoost) vote on whether the price
will move more than the round-trip fee within the next horizon.

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
    direction: +1 / -1 / 0
    confidence: fraction of models agreeing with the majority side
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
                eval_metric="logloss",
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

        X_train, X_test, y_train, y_test = train_test_split(
            X, y_cls, test_size=0.2, shuffle=False
        )

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
                self._momentum_model.fit(X_train[mom_cols], y_train)
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
        probs: list[float] = []
        for name, model in self._models.items():
            try:
                proba = model.predict_proba(row)[0]
                classes = list(model.classes_)
                up_idx = classes.index(1) if 1 in classes else -1
                p_up = float(proba[up_idx]) if 0 <= up_idx < len(proba) else float(max(proba))
                side = 1 if p_up >= 0.5 else -1
                votes[name] = side
                probs.append(p_up if side == 1 else 1 - p_up)
            except Exception:
                continue

        if not votes:
            return empty

        up_votes = sum(1 for v in votes.values() if v > 0)
        down_votes = sum(1 for v in votes.values() if v < 0)
        if up_votes > down_votes:
            direction = 1
        elif down_votes > up_votes:
            direction = -1
        else:
            direction = 0

        confidence = float(np.mean(probs)) if probs else 0.0
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
        return os.path.join(MODELS_DIR, f"{safe}_ens_{name}.pkl")

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
