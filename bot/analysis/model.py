"""XGBoost price prediction model.

Trains a regression model (predicted 24h % change) alongside a
classification model (probability the price moves in the predicted
direction) which serves as the confidence score.
"""

import os
import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor, XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, accuracy_score
from loguru import logger

from bot.analysis.features import FEATURE_COLS

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
MIN_TRAIN_ROWS = 300


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

        self._regressor.fit(X_train, yr_train)
        self._classifier.fit(X_train, yc_train)
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

        Confidence is the classifier's probability of a correct directional move.
        """
        if not self._trained:
            return 0.0, 0.0

        row = X.tail(1)[self._feature_cols] if self._feature_cols else X.tail(1)
        if row.isnull().any().any():
            return 0.0, 0.0

        predicted_change = float(self._regressor.predict(row)[0])
        proba = self._classifier.predict_proba(row)[0]

        if predicted_change >= 0:
            confidence = float(proba[1])
        else:
            confidence = float(proba[0])

        return predicted_change, confidence

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
        if loaded_cols != FEATURE_COLS:
            logger.info(f"{self.symbol}: feature set changed ({len(loaded_cols)} → {len(FEATURE_COLS)} cols), retraining")
            return False

        self._regressor = joblib.load(reg_path)
        self._classifier = joblib.load(cls_path)
        self._feature_cols = loaded_cols
        self._trained = True
        return True