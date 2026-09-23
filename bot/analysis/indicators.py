"""Technical indicator pipeline.

Adds RSI, MACD, Bollinger Bands, EMAs, ATR, ADX, and Volume SMA
to an OHLCV DataFrame using pandas-ta.
"""

import warnings
import pandas as pd
import pandas_ta as ta

MIN_ROWS = 300


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate and append technical indicators to an OHLCV DataFrame.

    Expects columns: open_time, open, high, low, close, volume
    Returns the same DataFrame with indicator columns added.
    Rows with insufficient history are left as NaN.
    """
    if len(df) < MIN_ROWS:
        return df

    df = df.copy().sort_values("open_time").reset_index(drop=True)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        df["rsi"] = ta.rsi(df["close"], length=14)

        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            cols = macd.columns.tolist()
            df["macd"] = macd[cols[0]]
            df["macd_hist"] = macd[cols[1]]
            df["macd_signal"] = macd[cols[2]]

        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is not None and not bb.empty:
            cols = bb.columns.tolist()
            df["bb_lower"] = bb[cols[0]]
            df["bb_mid"] = bb[cols[1]]
            df["bb_upper"] = bb[cols[2]]
            df["bb_width"] = bb[cols[3]]
            df["bb_pct"] = bb[cols[4]]

        df["ema9"] = ta.ema(df["close"], length=9)
        df["ema21"] = ta.ema(df["close"], length=21)
        df["ema50"] = ta.ema(df["close"], length=50)
        df["ema200"] = ta.ema(df["close"], length=200)

        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        adx = ta.adx(df["high"], df["low"], df["close"], length=14)
        if adx is not None and not adx.empty:
            df["adx"] = adx[adx.columns[0]]

        df["volume_sma"] = ta.sma(df["volume"], length=20)

        stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
        if stoch is not None and not stoch.empty:
            cols = stoch.columns.tolist()
            df["stoch_k"] = stoch[cols[0]]
            df["stoch_d"] = stoch[cols[1]]

        obv = ta.obv(df["close"], df["volume"])
        if obv is not None:
            df["obv"] = obv
            df["obv_sma"] = ta.sma(obv, length=20)

    return df
