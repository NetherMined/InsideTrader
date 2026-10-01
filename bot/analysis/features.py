"""ML feature engineering.

Transforms indicator-enriched OHLCV data into a normalised feature
matrix suitable for XGBoost, and computes forward-return targets.

Features cover four comparison windows:
  - 24h  : short-term momentum (ret_1, ret_4, ret_24)
  - 7 day: medium-term trend (ret_7d, vol_ratio_7d, 7d high/low position)
  - 30 day: monthly context (ret_30d, vol_ratio_30d, 30d high/low position)
  - Calendar: seasonal/bonus patterns (day_of_month, day_of_week, month_of_year, etc.)
"""

import numpy as np
import pandas as pd
from loguru import logger

FEATURE_COLS = [
    # Short-term technical
    "rsi",
    "macd_hist",
    "bb_pct",
    "bb_width_pct",
    "atr_pct",
    "adx",
    "ema9_ratio",
    "ema21_ratio",
    "ema50_ratio",
    "vol_ratio",
    "ret_1",
    "ret_4",
    "ret_24",
    # Multi-timeframe returns
    "ret_7d",
    "ret_30d",
    "ret_1y",
    # Volume context
    "vol_ratio_7d",
    "vol_ratio_30d",
    "vol_ratio_1y",
    # Price position within range
    "pct_from_7d_high",
    "pct_from_7d_low",
    "pct_from_30d_high",
    "pct_from_30d_low",
    "pct_from_1y_high",
    "pct_from_1y_low",
    # Trend alignment across timeframes (-5 to +5)
    "trend_alignment",
    # Calendar / seasonal features
    "hour_of_day",
    "day_of_week",
    "day_of_month",
    "month_of_year",
    "is_weekend",
    "is_month_start",
    "is_month_end",
    "is_quarter_end_month",
    "regime_trending",
    "above_ema200",
    "ema50_200_ratio",
    "bb_squeeze",
    "vol_surge",
    "stoch_k",
    "stoch_d",
    "obv_ratio",
    "near_support",
    "rsi_oversold",
    "rsi_overbought",
    "structure_trend",
    "structure_confirmed",
    "distance_to_demand_pct",
    "distance_to_supply_pct",
    "zone_rr_potential",
    "swings_since_bos",
    "zone_touch_count",
]

FORWARD_HOURS = 24

_7D_PERIODS = 7 * 24    # 168 hourly candles
_30D_PERIODS = 30 * 24  # 720 hourly candles
_1Y_PERIODS = 365 * 24  # 8760 hourly candles


def _add_structure_features(df: pd.DataFrame) -> pd.DataFrame:
    """Append structure-derived features. Zero-filled if data is insufficient."""
    try:
        from bot.analysis.structure import (
            classify_structure, detect_zones, compute_rr_ratio,
        )
    except ImportError:
        for col in ("structure_trend", "structure_confirmed", "distance_to_demand_pct",
                     "distance_to_supply_pct", "zone_rr_potential", "swings_since_bos",
                     "zone_touch_count"):
            df[col] = 0.0
        return df

    trend_map = {"UP": 1.0, "DOWN": -1.0, "RANGE": 0.0}
    defaults = {
        "structure_trend": 0.0, "structure_confirmed": 0.0,
        "distance_to_demand_pct": 0.0, "distance_to_supply_pct": 0.0,
        "zone_rr_potential": 0.0, "swings_since_bos": 0.0, "zone_touch_count": 0.0,
    }
    n = len(df)
    if n < 60:
        for col, val in defaults.items():
            df[col] = val
        return df

    try:
        state = classify_structure(df, lookback=3)
        zones = detect_zones(df, state)
    except Exception:
        for col, val in defaults.items():
            df[col] = val
        return df

    sv = trend_map.get(state.trend, 0.0)
    sc = 1.0 if state.confirmed else 0.0
    sb = float(state.swings_since_bos)
    df["structure_trend"] = sv
    df["structure_confirmed"] = sc
    df["swings_since_bos"] = sb

    closes = df["close"].values
    d_demand = np.zeros(n)
    d_supply = np.zeros(n)
    rr_pot = np.zeros(n)
    z_touch = np.zeros(n)

    demand_zones = [z for z in zones if z.type == "DEMAND" and not z.invalidated]
    supply_zones = [z for z in zones if z.type == "SUPPLY" and not z.invalidated]

    for i in range(n):
        c = float(closes[i])
        if c <= 0:
            continue
        if demand_zones:
            nearest_d = min(demand_zones, key=lambda z: abs(c - z.top))
            d_demand[i] = (c - nearest_d.top) / c * 100
            z_touch[i] = float(nearest_d.touches)
        if supply_zones:
            nearest_s = min(supply_zones, key=lambda z: abs(z.bottom - c))
            d_supply[i] = (nearest_s.bottom - c) / c * 100
        if demand_zones and supply_zones:
            sl = min(demand_zones, key=lambda z: abs(c - z.top)).bottom
            tp = min(supply_zones, key=lambda z: abs(z.bottom - c)).bottom
            rr_pot[i] = compute_rr_ratio(c, sl, tp)

    df["distance_to_demand_pct"] = d_demand
    df["distance_to_supply_pct"] = d_supply
    df["zone_rr_potential"] = rr_pot
    df["zone_touch_count"] = z_touch
    return df


def engineer_features(df: pd.DataFrame, target_pct: float = 1.0) -> pd.DataFrame:
    df = df.copy()

    df["atr_pct"] = (df["atr"] / df["close"] * 100).replace([np.inf, -np.inf], np.nan)
    df["bb_width_pct"] = (df["bb_width"] / df["close"] * 100).replace([np.inf, -np.inf], np.nan)

    for col, ema in [("ema9_ratio", "ema9"), ("ema21_ratio", "ema21"), ("ema50_ratio", "ema50")]:
        df[col] = (df["close"] / df[ema] - 1).replace([np.inf, -np.inf], np.nan)

    df["vol_ratio"] = (df["volume"] / df["volume_sma"]).replace([np.inf, -np.inf], np.nan)

    # Short-term returns
    df["ret_1"] = df["close"].pct_change(1) * 100
    df["ret_4"] = df["close"].pct_change(4) * 100
    df["ret_24"] = df["close"].pct_change(24) * 100

    # Multi-timeframe returns
    df["ret_7d"] = df["close"].pct_change(_7D_PERIODS) * 100
    df["ret_30d"] = df["close"].pct_change(_30D_PERIODS) * 100
    df["ret_1y"] = df["close"].pct_change(_1Y_PERIODS) * 100

    # Volume over 7d, 30d, and 1y rolling windows
    vol_sma_7d = df["volume"].rolling(_7D_PERIODS).mean()
    vol_sma_30d = df["volume"].rolling(_30D_PERIODS).mean()
    vol_sma_1y = df["volume"].rolling(_1Y_PERIODS).mean()
    df["vol_ratio_7d"] = (df["volume"] / vol_sma_7d).replace([np.inf, -np.inf], np.nan)
    df["vol_ratio_30d"] = (df["volume"] / vol_sma_30d).replace([np.inf, -np.inf], np.nan)
    df["vol_ratio_1y"] = (df["volume"] / vol_sma_1y).replace([np.inf, -np.inf], np.nan)

    # Price position within 7d, 30d, and 1y high/low range
    high_7d = df["high"].rolling(_7D_PERIODS).max()
    low_7d = df["low"].rolling(_7D_PERIODS).min()
    high_30d = df["high"].rolling(_30D_PERIODS).max()
    low_30d = df["low"].rolling(_30D_PERIODS).min()
    high_1y = df["high"].rolling(_1Y_PERIODS).max()
    low_1y = df["low"].rolling(_1Y_PERIODS).min()
    df["pct_from_7d_high"] = (df["close"] / high_7d - 1) * 100
    df["pct_from_7d_low"] = (df["close"] / low_7d - 1) * 100
    df["pct_from_30d_high"] = (df["close"] / high_30d - 1) * 100
    df["pct_from_30d_low"] = (df["close"] / low_30d - 1) * 100
    df["pct_from_1y_high"] = (df["close"] / high_1y - 1) * 100
    df["pct_from_1y_low"] = (df["close"] / low_1y - 1) * 100

    # Fill yearly features with neutral values when < 365 days of data exist
    df["ret_1y"] = df["ret_1y"].fillna(0.0)
    df["vol_ratio_1y"] = df["vol_ratio_1y"].fillna(1.0)
    df["pct_from_1y_high"] = df["pct_from_1y_high"].fillna(0.0)
    df["pct_from_1y_low"] = df["pct_from_1y_low"].fillna(0.0)

    # Trend alignment: how many timeframes agree on direction (-5 to +5)
    df["trend_alignment"] = (
        np.sign(df["ret_1"])
        + np.sign(df["ret_24"])
        + np.sign(df["ret_7d"].fillna(0))
        + np.sign(df["ret_30d"].fillna(0))
        + np.sign(df["ret_1y"].fillna(0))
    )

    # Calendar features — captures bonus/salary cycles, weekday patterns, seasonality
    dt = df["open_time"].dt
    df["hour_of_day"] = dt.hour
    df["day_of_week"] = dt.dayofweek          # 0=Mon, 6=Sun
    df["day_of_month"] = dt.day
    df["month_of_year"] = dt.month
    df["is_weekend"] = (dt.dayofweek >= 5).astype(int)
    df["is_month_start"] = (dt.day <= 5).astype(int)     # days 1-5: post-salary investment
    df["is_month_end"] = (dt.day >= 25).astype(int)      # days 25-31: pre-salary anticipation
    df["is_quarter_end_month"] = dt.month.isin([3, 6, 9, 12]).astype(int)

    df["regime_trending"] = (df["adx"] > 25).astype(float)
    if "ema200" in df.columns and df["ema200"].notna().any():
        df["above_ema200"] = (df["close"] > df["ema200"]).fillna(False).astype(float)
        df["ema50_200_ratio"] = ((df["ema50"] / df["ema200"]) - 1).replace([np.inf, -np.inf], np.nan)
    else:
        df["above_ema200"] = 0.5
        df["ema50_200_ratio"] = 0.0

    bb_width_sma = df["bb_width_pct"].rolling(20).mean()
    df["bb_squeeze"] = (df["bb_width_pct"] < bb_width_sma).astype(float)

    df["vol_surge"] = (df["vol_ratio"] > 1.5).astype(float)

    df["stoch_k"] = df["stoch_k"] if "stoch_k" in df.columns else pd.Series(50.0, index=df.index)
    df["stoch_d"] = df["stoch_d"] if "stoch_d" in df.columns else pd.Series(50.0, index=df.index)

    if "obv" in df.columns and "obv_sma" in df.columns:
        df["obv_ratio"] = (df["obv"] / df["obv_sma"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    else:
        df["obv_ratio"] = pd.Series(1.0, index=df.index)

    if "pct_from_7d_low" in df.columns:
        df["near_support"] = (df["pct_from_7d_low"].abs() < 3.0).fillna(False).astype(float)
    else:
        df["near_support"] = 0.0
    df["rsi_oversold"] = (df["rsi"] < 35).astype(float)
    df["rsi_overbought"] = (df["rsi"] > 70).astype(float)

    df = _add_structure_features(df)

    # Forward return targets
    df["fwd_ret"] = df["close"].pct_change(FORWARD_HOURS).shift(-FORWARD_HOURS) * 100
    df["fwd_up"] = (df["fwd_ret"] > target_pct).astype(int)

    df = df.replace([np.inf, -np.inf], np.nan)

    return df


def get_X(df: pd.DataFrame) -> pd.DataFrame:
    available = [c for c in FEATURE_COLS if c in df.columns]
    return df[available]


def get_X_y(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    feat_df = engineer_features(df)
    cols = [c for c in FEATURE_COLS if c in feat_df.columns] + ["fwd_ret", "fwd_up"]
    clean = feat_df[cols].dropna()
    X = clean[[c for c in FEATURE_COLS if c in clean.columns]]
    y_reg = clean["fwd_ret"]
    y_cls = clean["fwd_up"]
    return X, y_reg, y_cls