"""Market mode classifier.

Decides whether a trade should use SPOT or FUTURES mode based on
model confidence, volatility, and trend strength.

All three conditions must pass for FUTURES; any failure defaults to SPOT.
"""

from bot.config import settings


def classify_mode(
    confidence: float,
    atr_pct: float,
    adx: float,
    trading_mode: str | None = None,
    confidence_threshold: float | None = None,
) -> str:
    """Return 'FUTURES' or 'SPOT' for a given market condition.

    Args:
        confidence:           Model confidence score (0.0 – 1.0).
        atr_pct:              ATR as % of current price (volatility measure).
        adx:                  Average Directional Index (trend strength).
        trading_mode:         Override for settings.trading_mode (e.g. from active level preset).
        confidence_threshold: Override for settings.futures_confidence_threshold (e.g. from active level preset).
    """
    _trading_mode = trading_mode or settings.trading_mode
    _conf_threshold = confidence_threshold if confidence_threshold is not None else settings.futures_confidence_threshold

    if _trading_mode in ("SPOT_ONLY", "SPOT"):
        return "SPOT"
    if _trading_mode == "FUTURES_ONLY":
        return "FUTURES"

    high_confidence = confidence >= _conf_threshold
    # FUTURES is the primary mode — only fall back to SPOT if volatility is extreme
    # or confidence is too low. ADX is not required; moderate trends are fine with leverage.
    extreme_volatility = atr_pct > settings.volatility_futures_cap_percent * 1.5  # > 7.5%

    if high_confidence and not extreme_volatility:
        return "FUTURES"

    return "SPOT"


def mode_reason(
    confidence: float,
    atr_pct: float,
    adx: float,
    trading_mode: str | None = None,
    confidence_threshold: float | None = None,
) -> dict:
    """Return a breakdown of why SPOT or FUTURES was chosen."""
    _conf_threshold = confidence_threshold if confidence_threshold is not None else settings.futures_confidence_threshold
    return {
        "mode": classify_mode(confidence, atr_pct, adx, trading_mode, confidence_threshold),
        "confidence_ok": confidence >= _conf_threshold,
        "volatility_ok": atr_pct <= settings.volatility_futures_cap_percent * 1.5,
        "thresholds": {
            "confidence": _conf_threshold,
            "max_atr_pct": settings.volatility_futures_cap_percent * 1.5,
        },
    }