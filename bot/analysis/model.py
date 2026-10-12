"""XGBoost price prediction model.

Trains a regression model (predicted hold-horizon % change) alongside a
classification model (probability the price moves in that direction).
"""

import os
import time
import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor, XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, accuracy_score
from loguru import logger

from bot.analysis.features import FEATURE_COLS
from bot.config import settings

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
MIN_TRAIN_ROWS = 500
MODEL_MAX_AGE_HOURS = 24


class PricePredictor:
    def __init__(self, symbol: str, timeframe: str | None = None):
        self.symbol = symbol
        self.timeframe = timeframe or settings.analysis_timeframe
        self._regressor = XGBRegressor(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            n_jobs=-1,
            verbosity=0,
            early_stopping_rounds=30,
        )
        self._classifier = XGBClassifier(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            n_jobs=-1,
            eval_metric="logloss",
            early_stopping_rounds=30,
            verbosity=0,
        )
        self._trained = False
        self._feature_cols: list[str] = []
        self._train_count = 0

    def _paths(self) -> tuple[str, str, str]:
        safe_symbol = self.symbol.replace("/", "_")
        tf = self.timeframe.replace("/", "_")
        return (
            os.path.join(MODELS_DIR, f"{safe_symbol}_{tf}_reg.pkl"),
            os.path.join(MODELS_DIR, f"{safe_symbol}_{tf}_cls.pkl"),
            os.path.join(MODELS_DIR, f"{safe_symbol}_{tf}_cols.pkl"),
        )

    def train(self, X: pd.DataFrame, y_reg: pd.Series, y_cls: pd.Series) -> dict:
        if len(X) < MIN_TRAIN_ROWS:
            logger.warning(f"{self.symbol}: insufficient data ({len(X)} rows < {MIN_TRAIN_ROWS})")
            return {}

        self._feature_cols = X.columns.tolist()

        X_train, X_test, yr_train, yr_test, yc_train, yc_test = train_test_split(
            X, y_reg, y_cls, test_size=0.2, shuffle=False
        )

        pos_count = int((yc_train == 1).sum())
        neg_count = int((yc_train == 0).sum())
        if pos_count > 0 and neg_count > 0:
            scale_pos_weight = neg_count / pos_count
            self._classifier.set_params(scale_pos_weight=scale_pos_weight)
            logger.debug(f"{self.symbol}: class balance UP={pos_count} DOWN={neg_count} scale_pos_weight={scale_pos_weight:.2f}")

        self._regressor.fit(
            X_train, yr_train,
            eval_set=[(X_test, yr_test)],
            verbose=False,
        )
        self._classifier.fit(
            X_train, yc_train,
            eval_set=[(X_test, yc_test)],
            verbose=False,
        )
        self._trained = True

        reg_pred = self._regressor.predict(X_test)
        cls_pred = self._classifier.predict(X_test)

        metrics = {
            "mae": float(mean_absolute_error(yr_test, reg_pred)),
            "direction_accuracy": float(accuracy_score(yc_test, cls_pred)),
            "train_rows": len(X_train),
            "test_rows": len(X_test),
        }

        logger.info(
            f"{self.symbol}: trained — MAE={metrics['mae']:.4f}%, "
            f"dir_acc={metrics['direction_accuracy']:.2%}, "
            f"rows={len(X)}"
        )
        return metrics

    def predict(self, X: pd.DataFrame) -> tuple[float, float]:
        if not self._trained:
            return 0.0, 0.0

        row = X.tail(1)[self._feature_cols] if self._feature_cols else X.tail(1)
        if row.isnull().any().any():
            return 0.0, 0.0

        predicted_change = float(self._regressor.predict(row)[0])

        if not (-50 < predicted_change < 50):
            logger.warning(f"{self.symbol}: invalid prediction change={predicted_change:.2f}, returning 0")
            return 0.0, 0.0

        proba = self._classifier.predict_proba(row)[0]
        classes = list(self._classifier.classes_)

        if predicted_change >= 0:
            idx = classes.index(1) if 1 in classes else -1
        else:
            idx = classes.index(0) if 0 in classes else 0

        if 0 <= idx < len(proba):
            confidence = float(proba[idx])
        else:
            confidence = float(max(proba))

        confidence = max(0.0, min(confidence, 0.92))

        return predicted_change, confidence

    def is_stale(self, max_age_hours: int = MODEL_MAX_AGE_HOURS) -> bool:
        reg_path, _, _ = self._paths()
        if not os.path.exists(reg_path):
            return True
        age_hours = (time.time() - os.path.getmtime(reg_path)) / 3600
        return age_hours > max_age_hours

    def save(self) -> None:
        os.makedirs(MODELS_DIR, exist_ok=True)
        reg_path, cls_path, cols_path = self._paths()
        joblib.dump(self._regressor, reg_path)
        joblib.dump(self._classifier, cls_path)
        joblib.dump(self._feature_cols, cols_path)

    def load(self) -> bool:
        reg_path, cls_path, cols_path = self._paths()

        if not all(os.path.exists(p) for p in [reg_path, cls_path, cols_path]):
            return False

        loaded_cols = joblib.load(cols_path)
        if set(loaded_cols) != set(FEATURE_COLS):
            logger.info(f"{self.symbol}: feature set changed ({len(loaded_cols)} → {len(FEATURE_COLS)} cols), retraining")
            return False
        if loaded_cols != FEATURE_COLS:
            logger.warning(f"{self.symbol}: feature column order differs from current code, retraining for consistency")
            return False

        self._regressor = joblib.load(reg_path)
        self._classifier = joblib.load(cls_path)
        self._feature_cols = loaded_cols
        self._trained = True
        return True
