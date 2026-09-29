"""Analysis training orchestrator.

For each symbol:
  1. Load candles from PostgreSQL
  2. Calculate technical indicators
  3. Engineer ML features
  4. Train (or reload) XGBoost models
  5. Generate today's prediction
  6. Classify trade mode (SPOT / FUTURES)
  7. Save prediction to DB
  8. Run backtest (first time only)

Returns a ranked list of trading opportunities.
"""

import asyncio
import json
from datetime import datetime, timezone

import pandas as pd
from loguru import logger
from sqlalchemy import text

from bot.config import settings
from bot.db.connection import async_session
from bot.analysis.indicators import add_indicators, MIN_ROWS
from bot.analysis.features import engineer_features, get_X, get_X_y
from bot.analysis.model import PricePredictor
from bot.analysis.mode_classifier import classify_mode, mode_reason
from bot.analysis.ranker import rank_pairs, RankedPair
from bot.analysis.backtest import run_backtest
from bot.analysis.performance_tracker import get_symbol_confidence_factors, get_direction_factors, get_regime_factors

CONCURRENCY = 8


async def _load_candles(symbol: str, timeframe: str) -> pd.DataFrame:
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT open_time, open, high, low, close, volume
                FROM candles
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY open_time ASC
            """),
            {"symbol": symbol, "timeframe": timeframe},
        )
        rows = result.mappings().all()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


async def _save_prediction(symbol: str, current_price: float, target_price: float,
                            predicted_change_pct: float, confidence: float,
                            mode: str, features_snapshot: dict) -> None:
    async with async_session() as session:
        await session.execute(
            text("""
                INSERT INTO predictions
                    (symbol, prediction_date, current_price, target_price,
                     predicted_change_pct, confidence, mode_recommendation, features)
                VALUES
                    (:symbol, :prediction_date, :current_price, :target_price,
                     :predicted_change_pct, :confidence, :mode_recommendation, CAST(:features AS jsonb))
            """),
            {
                "symbol": symbol,
                "prediction_date": datetime.now(timezone.utc),
                "current_price": current_price,
                "target_price": target_price,
                "predicted_change_pct": predicted_change_pct,
                "confidence": confidence,
                "mode_recommendation": mode,
                "features": json.dumps(features_snapshot),
            },
        )
        await session.commit()


def _prepare_features(df_raw: pd.DataFrame, target_pct: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    df = add_indicators(df_raw)
    feat_df = engineer_features(df, target_pct=target_pct)
    X, y_reg, y_cls = get_X_y(feat_df)
    return df, feat_df, X, y_reg, y_cls


async def _analyse_symbol(symbol: str) -> dict | None:
    df_raw = await _load_candles(symbol, settings.analysis_timeframe)
    if len(df_raw) < MIN_ROWS:
        logger.debug(f"{symbol}: not enough candles ({len(df_raw)}), skipping")
        return None

    _timeframe_target = {"5m": 0.1, "15m": 0.25, "30m": 0.5, "1h": 1.0, "4h": 4.0}
    _target_pct = _timeframe_target.get(settings.analysis_timeframe, settings.daily_target_percent / 2)
    df, feat_df, X, y_reg, y_cls = await asyncio.to_thread(
        _prepare_features, df_raw, _target_pct
    )
    if len(X) < 100:
        logger.debug(f"{symbol}: not enough clean rows ({len(X)}), skipping")
        return None

    predictor = PricePredictor(symbol)
    loaded = predictor.load()

    # Force retrain if model is stale (>24h old) — prevents regime-bias from persisting
    if loaded and predictor.is_stale():
        logger.info(f"{symbol}: model is stale (>24h), retraining with latest data")
        loaded = False

    if not loaded:
        split = int(len(X) * 0.8)
        X_train, y_reg_train, y_cls_train = X.iloc[:split], y_reg.iloc[:split], y_cls.iloc[:split]
        X_test = X.iloc[split:]
        df_test = feat_df.loc[X_test.index].reset_index(drop=True)

        metrics = await asyncio.to_thread(predictor.train, X_train, y_reg_train, y_cls_train)
        if not metrics:
            return None

        predictor.save()

        if len(X_test) > 10 and len(df_test) > 10:
            try:
                pred_reg_test = pd.Series(predictor._regressor.predict(X_test), index=range(len(X_test)))
                cls_idx = list(predictor._classifier.classes_).index(1) if 1 in predictor._classifier.classes_ else 1
                pred_proba_test = pd.Series(
                    predictor._classifier.predict_proba(X_test)[:, cls_idx], index=range(len(X_test))
                )
                await run_backtest(
                    symbol, df_test, pred_reg_test, pred_proba_test,
                    settings.stop_loss_percent, settings.take_profit_percent, settings.futures_leverage,
                )
            except Exception as e:
                logger.warning(f"{symbol}: backtest failed ({e}), continuing")

    X_latest = get_X(feat_df.dropna(subset=["rsi", "adx", "atr"])).tail(1)
    if X_latest.empty or X_latest.isnull().any().any():
        logger.debug(f"{symbol}: latest feature row has NaN values, skipping prediction")
        return None

    predicted_change_pct, confidence = await asyncio.to_thread(predictor.predict, X_latest)

    latest = feat_df.dropna(subset=["atr", "adx"]).iloc[-1]
    atr_pct = float(latest.get("atr_pct", 0.0)) if "atr_pct" in latest else 0.0
    adx = float(latest.get("adx", 0.0)) if "adx" in latest else 0.0
    current_price = float(df_raw["close"].iloc[-1])
    target_price = current_price * (1 + predicted_change_pct / 100)

    mode = classify_mode(confidence, atr_pct, adx)

    def _safe(key, decimals=2):
        v = latest.get(key)
        return round(float(v), decimals) if v is not None and not (isinstance(v, float) and (v != v)) else None

    features_snapshot = {
        "atr_pct": _safe("atr_pct", 4),
        "adx": _safe("adx", 2),
        "rsi": _safe("rsi", 2),
        "mode_reason": mode_reason(confidence, atr_pct, adx),
        "ret_24h": _safe("ret_24", 3),
        "ret_7d": _safe("ret_7d", 3),
        "ret_30d": _safe("ret_30d", 3),
        "ret_1y": _safe("ret_1y", 3),
        "vol_ratio_7d": _safe("vol_ratio_7d", 3),
        "pct_from_7d_high": _safe("pct_from_7d_high", 3),
        "pct_from_30d_high": _safe("pct_from_30d_high", 3),
        "pct_from_1y_high": _safe("pct_from_1y_high", 3),
        "trend_alignment": _safe("trend_alignment", 0),
        "day_of_week": int(latest["day_of_week"]) if "day_of_week" in latest else None,
        "day_of_month": int(latest["day_of_month"]) if "day_of_month" in latest else None,
        "month_of_year": int(latest["month_of_year"]) if "month_of_year" in latest else None,
    }

    await _save_prediction(
        symbol, current_price, target_price,
        predicted_change_pct, confidence, mode, features_snapshot,
    )

    ret_7d_val = features_snapshot.get("ret_7d")
    ret_30d_val = features_snapshot.get("ret_30d")
    ret_1y_val = features_snapshot.get("ret_1y")
    logger.info(
        f"{symbol}: {predicted_change_pct:+.2f}% target | "
        f"conf={confidence:.2f} | mode={mode} | "
        f"ATR={atr_pct:.1f}% ADX={adx:.1f} | "
        f"7d={ret_7d_val:+.1f}% 30d={ret_30d_val:+.1f}% 1y={ret_1y_val:+.1f}%"
        if ret_7d_val is not None and ret_30d_val is not None and ret_1y_val is not None
        else f"{symbol}: {predicted_change_pct:+.2f}% target | conf={confidence:.2f} | mode={mode} | ATR={atr_pct:.1f}% ADX={adx:.1f}"
    )

    return {
        "symbol": symbol,
        "predicted_change_pct": predicted_change_pct,
        "confidence": confidence,
        "atr_pct": atr_pct,
        "adx": adx,
        "mode": mode,
        "rsi": float(latest.get("rsi", 50.0) or 50.0),
        "bb_pct": float(latest.get("bb_pct", 0.5) or 0.5),
        "bb_width_pct": float(latest.get("bb_width_pct", 0.05) or 0.05),
        "ema21_ratio": float(latest.get("ema21_ratio", 0.0) or 0.0),
        "vol_ratio": float(latest.get("vol_ratio", 1.0) or 1.0),
        "current_price": current_price,
        "target_price": target_price,
    }


async def run_analysis(symbols: list[str]) -> list[RankedPair]:
    """Run full analysis pipeline for all symbols. Returns ranked pairs."""
    logger.info(f"Starting analysis for {len(symbols)} pairs...")

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def analyse_direct(symbol: str) -> dict | None:
        async with semaphore:
            try:
                return await _analyse_symbol(symbol)
            except Exception as e:
                logger.error(f"{symbol}: analysis error — {e}")
                return None

    perf_factors = await get_symbol_confidence_factors()
    dir_factors = await get_direction_factors()
    regime_factors = await get_regime_factors()

    results = await asyncio.gather(*[analyse_direct(s) for s in symbols])
    predictions = [r for r in results if r is not None]

    for pred in predictions:
        sym = pred["symbol"]
        conf = pred["confidence"]

        sym_factor = perf_factors.get(sym, 1.0)
        conf *= sym_factor

        side = "BUY" if pred["predicted_change_pct"] >= 0 else "SELL"
        d_factor = dir_factors.get(sym, {}).get(side, 1.0)
        conf *= d_factor

        mode = pred.get("mode", "SPOT")
        if mode == "FUTURES" or mode == "DYNAMIC":
            r_factor = regime_factors.get("TRENDING", 1.0)
        else:
            r_factor = 1.0
        conf *= r_factor

        pred["confidence"] = min(1.0, max(0.0, conf))
        if sym_factor != 1.0 or d_factor != 1.0 or r_factor != 1.0:
            logger.debug(
                f"{sym} {side}: conf adjusted {pred['confidence']:.3f} "
                f"(sym={sym_factor} dir={d_factor} regime={r_factor})"
            )

    ranked = rank_pairs(predictions)
    logger.info(f"Analysis complete: {len(predictions)}/{len(symbols)} pairs analysed, {len(ranked)} ranked")

    if ranked:
        top = ranked[:5]
        logger.info("Top 5 opportunities:")
        for i, p in enumerate(top, 1):
            logger.info(f"  {i}. {p.symbol}: {p.predicted_change_pct:+.2f}% | conf={p.confidence:.2f} | {p.mode} | score={p.score:.3f}")

    return ranked


async def force_retrain_models(symbols: list[str]) -> None:
    """Force retrain all symbol models with latest data."""
    from bot.analysis.model import PricePredictor
    from bot.analysis.features import engineer_features, get_X, get_X_y
    from bot.analysis.indicators import add_indicators
    from bot.db.connection import async_session
    from sqlalchemy import text
    import pandas as pd
    import asyncio

    for symbol in symbols:
        try:
            async with async_session() as session:
                result = await session.execute(
                    text("""
                        SELECT open_time, open, high, low, close, volume
                        FROM candles
                        WHERE symbol = :symbol AND timeframe = :timeframe
                        ORDER BY open_time ASC
                    """),
                    {"symbol": symbol, "timeframe": settings.analysis_timeframe},
                )
                rows = result.mappings().all()

            if len(rows) < 300:
                continue

            df_raw = pd.DataFrame([dict(r) for r in rows])
            df_raw["open_time"] = pd.to_datetime(df_raw["open_time"], utc=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df_raw[col] = df_raw[col].astype(float)

            df = add_indicators(df_raw)
            _tf_target = {"5m": 0.1, "15m": 0.25, "30m": 0.5, "1h": 1.0, "4h": 4.0}
            feat_df = engineer_features(df, target_pct=_tf_target.get(settings.analysis_timeframe, 1.0))

            X, y_reg, y_cls = get_X_y(feat_df)
            if len(X) < 300:
                continue

            predictor = PricePredictor(symbol)
            split = int(len(X) * 0.8)
            X_train, y_reg_train, y_cls_train = X.iloc[:split], y_reg.iloc[:split], y_cls.iloc[:split]

            metrics = predictor.train(X_train, y_reg_train, y_cls_train)
            if metrics:
                predictor.save()
                logger.info(f"Retrained model for {symbol}: {metrics}")
        except Exception as e:
            logger.error(f"Retrain error for {symbol}: {e}")