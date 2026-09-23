"""XGBoost price prediction model.

Trains a regression model (predicted 24h % change) alongside a
classification model (probability the price moves in the predicted
direction) which serves as the confidence score.
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

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
MIN_TRAIN_ROWS = 500  # raised from 300 — avoids overfitting to short bearish/bullish windows
MODEL_MAX_AGE_HOURS = 24  # force retrain after 24h so models don't stay biased to old regimes


class PricePredictor:
    def __init__(self, symbol: str):
        self.symbol = symbol
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

    def train(self, X: pd.DataFrame, y_reg: pd.Series, y_cls: pd.Series) -> dict:
        """Fit both models. Returns evaluation metrics."""
        if len(X) < MIN_TRAIN_ROWS:
            logger.warning(f"{self.symbol}: insufficient data ({len(X)} rows < {MIN_TRAIN_ROWS})")
            return {}

        self._feature_cols = X.columns.tolist()

        X_train, X_test, yr_train, yr_test, yc_train, yc_test = train_test_split(
            X, y_reg, y_cls, test_size=0.2, shuffle=False
        )

        # Balance classes so model doesn't learn to predict whichever direction dominates recent history
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
        """Return (predicted_change_pct, confidence).

        Confidence is the classifier's probability of a correct directional move,
        capped at 0.92 to prevent overconfident predictions from dominating sizing.
        """
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
        """Return True if the saved model is older than max_age_hours."""
        safe_symbol = self.symbol.replace("/", "_")
        reg_path = os.path.join(MODELS_DIR, f"{safe_symbol}_reg.pkl")
        if not os.path.exists(reg_path):
            return True
        age_hours = (time.time() - os.path.getmtime(reg_path)) / 3600
        return age_hours > max_age_hours

    def save(self) -> None:
        os.makedirs(MODELS_DIR, exist_ok=True)
        safe_symbol = self.symbol.replace("/", "_")
        joblib.dump(self._regressor, os.path.join(MODELS_DIR, f"{safe_symbol}_reg.pkl"))
        joblib.dump(self._classifier, os.path.join(MODELS_DIR, f"{safe_symbol}_cls.pkl"))
        joblib.dump(self._feature_cols, os.path.join(MODELS_DIR, f"{safe_symbol}_cols.pkl"))

    def load(self) -> bool:
        safe_symbol = self.symbol.replace("/", "_")
        reg_path = os.path.join(MODELS_DIR, f"{safe_symbol}_reg.pkl")
        cls_path = os.path.join(MODELS_DIR, f"{safe_symbol}_cls.pkl")
        cols_path = os.path.join(MODELS_DIR, f"{safe_symbol}_cols.pkl")

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