"""Market regime detector.

Classifies the current market state as TRENDING, RANGING, or TRANSITION
based on ADX, ATR%, Bollinger Band width, and volatility.

Used by the mode classifier and executor to switch strategy logic:
  - TRENDING: use trend-following signals (existing behavior)
  - RANGING: use mean-reversion signals (RSI/Bollinger extremes)
  - TRANSITION: reduced position size, higher confidence threshold
"""

from dataclasses import dataclass
from loguru import logger


@dataclass
class RegimeResult:
    regime: str  # "TRENDING", "RANGING", or "TRANSITION"
    adx: float
    atr_pct: float
    bb_width_pct: float
    confidence_multiplier: float  # 1.0 = normal, <1.0 = reduce risk
    strategy: str  # "trend_following", "mean_reversion", or "conservative"


# Thresholds (tunable via config)
ADX_TRENDING = 25.0
ADX_RANGING = 15.0
ATR_PCT_HIGH = 5.0  # High volatility = trending or transition
BB_WIDTH_LOW = 0.02  # Narrow bands = ranging
BB_WIDTH_HIGH = 0.10  # Wide bands = trending


def detect_regime(adx: float, atr_pct: float, bb_width_pct: float) -> RegimeResult:
    """Detect market regime from technical indicators.

    Args:
        adx: Average Directional Index (0-100)
        atr_pct: ATR as percentage of price
        bb_width_pct: Bollinger Band width as percentage of price

    Returns:
        RegimeResult with regime classification and recommendations
    """
    # TRENDING: strong trend + reasonable volatility or wide bands
    if adx >= ADX_TRENDING:
        regime = "TRENDING"
        strategy = "trend_following"
        confidence_mult = 1.0
    # RANGING: weak trend + narrow bands
    elif adx < ADX_RANGING and bb_width_pct < BB_WIDTH_LOW:
        regime = "RANGING"
        strategy = "mean_reversion"
        confidence_mult = 0.8  # Mean-reversion is lower confidence
    # TRANSITION: everything else
    else:
        regime = "TRANSITION"
        strategy = "conservative"
        confidence_mult = 0.7  # Be more conservative in transitions

    # Adjust confidence multiplier based on volatility
    if atr_pct > ATR_PCT_HIGH:
        confidence_mult = min(confidence_mult, 0.6)

    return RegimeResult(
        regime=regime,
        adx=adx,
        atr_pct=atr_pct,
        bb_width_pct=bb_width_pct,
        confidence_multiplier=confidence_mult,
        strategy=strategy,
    )


def get_regime_based_position_multiplier(regime_result: RegimeResult) -> float:
    """Return position size multiplier based on regime.

    TRENDING: 1.0 (full size)
    RANGING: 0.7 (reduced size for mean-reversion)
    TRANSITION: 0.5 (half size)
    """
    multipliers = {
        "TRENDING": 1.0,
        "RANGING": 0.7,
        "TRANSITION": 0.5,
    }
    return multipliers.get(regime_result.regime, 0.5)


def generate_mean_reversion_signal(rsi: float, bb_pct: float) -> dict:
    """Generate mean-reversion signal from RSI and Bollinger Band %.

    Args:
        rsi: RSI value (0-100)
        bb_pct: Bollinger Band % position (0-1, where 0=lower band, 1=upper band)

    Returns:
        dict with side ("BUY", "SELL", or "NONE"), strength (0-1), and reason
    """
    side = "NONE"
    strength = 0.0
    reason = ""

    if rsi < 30 and bb_pct < 0.2:
        side = "BUY"
        strength = (30 - rsi) / 30 * 0.5 + (0.2 - bb_pct) / 0.2 * 0.5
        reason = f"oversold RSI={rsi:.1f} bb_pct={bb_pct:.2f}"
    elif rsi > 70 and bb_pct > 0.8:
        side = "SELL"
        strength = (rsi - 70) / 30 * 0.5 + (bb_pct - 0.8) / 0.2 * 0.5
        reason = f"overbought RSI={rsi:.1f} bb_pct={bb_pct:.2f}"

    return {"side": side, "strength": min(strength, 1.0), "reason": reason}


def get_regime_based_confidence_threshold(regime_result: RegimeResult, base_threshold: float) -> float:
    """Adjust the confidence threshold based on regime.

    TRENDING: use base threshold
    RANGING: raise threshold (mean-reversion needs stronger signals)
    TRANSITION: raise threshold (be conservative)
    """
    multipliers = {
        "TRENDING": 1.0,
        "RANGING": 1.1,  # Higher threshold in ranges
        "TRANSITION": 1.2,  # Even higher in transitions
    }
    mult = multipliers.get(regime_result.regime, 1.2)
    return min(base_threshold * mult, 0.95)  # Cap at 95%
