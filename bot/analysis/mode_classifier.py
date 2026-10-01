"""Market mode classifier.

Futures-only. Spot routing has been removed; every signal trades USDM futures.
"""

from bot.config import settings


def classify_mode(
    confidence: float,
    atr_pct: float,
    adx: float,
    trading_mode: str | None = None,
    confidence_threshold: float | None = None,
) -> str:
    """Always FUTURES. Spot is disabled."""
    return "FUTURES"


def mode_reason(
    confidence: float,
    atr_pct: float,
    adx: float,
    trading_mode: str | None = None,
    confidence_threshold: float | None = None,
) -> dict:
    """Return a breakdown of why FUTURES was chosen."""
    _conf_threshold = confidence_threshold if confidence_threshold is not None else settings.futures_confidence_threshold
    return {
        "mode": "FUTURES",
        "confidence_ok": confidence >= _conf_threshold,
        "volatility_ok": atr_pct <= settings.volatility_futures_cap_percent * 1.5,
        "thresholds": {
            "confidence": _conf_threshold,
            "max_atr_pct": settings.volatility_futures_cap_percent * 1.5,
        },
    }
