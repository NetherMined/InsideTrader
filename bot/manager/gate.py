"""Manager entry gate — trend alignment + researcher learning loop.

Futures only. Side comes from the manager and the market trend.
The researcher adjusts confidence but does not veto an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bot.config import settings
from bot.analysis.structure import StructureState
from bot.research import packet_key, loads
from bot.research.patcher import load_rules


@dataclass
class Decision:
    ok: bool
    reason: str
    packet: dict[str, Any] | None = None
    rules: dict[str, Any] | None = None
    venue: str = "FUTURES"
    side: str = ""
    confidence_adj: float = 1.0  # multiplier the executor applies to confidence


def _stale(packet: dict[str, Any], now: datetime) -> bool:
    raw = packet.get("packet_time")
    if not raw:
        return True
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    age_min = (now - ts).total_seconds() / 60.0
    return age_min > float(settings.research_stale_minutes)


def compute_bot_prediction(predicted_change_pct: float, confidence: float) -> str:
    """1h model view: BULL / BEAR / NONE."""
    threshold = settings.confidence_threshold
    if predicted_change_pct > 0 and confidence >= threshold:
        return "BULL"
    if predicted_change_pct < 0 and confidence >= threshold:
        return "BEAR"
    return "NONE"


def compute_market_trend(
    ema21: float,
    ema50: float,
    packet: dict[str, Any] | None,
    regime: str = "UNKNOWN",
    mode: str = "FUTURES",
    bot_prediction: str = "NONE",
    structure: StructureState | None = None,
) -> str:
    """Market trend from structure, then EMA. Returns BULL / BEAR / NONE.

    Structure and EMA set the trend. The researcher packet is feedback only
    and cannot flip, veto, or invent a trend.
    """
    # Only trust confirmed structure trends
    if structure and structure.confirmed and structure.trend == "UP":
        base_trend = "BULL"
    elif structure and structure.confirmed and structure.trend == "DOWN":
        base_trend = "BEAR"
    elif ema21 > 0 and ema50 > 0:
        # Only trust EMA when separation is meaningful (>0.3%)
        ema_spread = abs(ema21 - ema50) / ema50 * 100
        if ema_spread >= 0.3:
            base_trend = "BULL" if ema21 > ema50 else "BEAR"
        else:
            base_trend = "NONE"  # EMAs too close — no clear trend, let ML decide
    else:
        base_trend = "NONE"

    if base_trend == "NONE":
        return "NONE"

    if regime == "RANGING" or (structure and structure.trend == "RANGE"):
        if bot_prediction == base_trend:
            return base_trend
        return "NONE"

    return base_trend


def get_aligned_action(
    bot_prediction: str,
    market_trend: str,
    mode: str = "FUTURES",
    adx_1h: float = 0.0,
    adx_15m: float = 0.0,
    sentiment: str = "NEUTRAL",
) -> tuple[str, str]:
    """Return (FUTURES, side) or (SKIP, reason). Spot is not a venue."""
    if bot_prediction == "NONE":
        return "SKIP", "no_prediction"
    # When ML disagrees with trend, follow the trend (the market is right)
    # The confidence penalty will reduce position size for counter-trend signals
    # Follow the trend when available; fall back to ML when no trend
    if market_trend != "NONE":
        direction = market_trend
    else:
        direction = bot_prediction

    side = "BUY" if direction == "BULL" else "SELL"
    return "FUTURES", side


async def should_enter(
    redis,
    symbol: str,
    manager_side: str,
    predicted_change_pct: float = 0.0,
    confidence: float = 0.0,
    ema21: float = 0.0,
    ema50: float = 0.0,
    adx: float = 0.0,
    regime: str = "UNKNOWN",
    mode: str = "FUTURES",
    structure: StructureState | None = None,
    sentiment: str = "NEUTRAL",
) -> Decision:
    """Full alignment gate. Caller must place decision.side on FUTURES."""
    rules = await load_rules(redis)
    raw = await redis.get(packet_key(symbol)) if settings.research_enabled else None
    packet = loads(raw) if raw else None
    now = datetime.now(timezone.utc)

    if packet and _stale(packet, now):
        packet = None

    bot_pred = compute_bot_prediction(predicted_change_pct, confidence)
    adx_15m = float(packet.get("adx", 0.0)) if packet else 0.0

    market = compute_market_trend(
        ema21, ema50, packet, regime=regime, mode="FUTURES", bot_prediction=bot_pred,
        structure=structure,
    )

    venue, side_or_reason = get_aligned_action(
        bot_pred, market, "FUTURES", adx_1h=adx, adx_15m=adx_15m, sentiment=sentiment,
    )
    if venue == "SKIP":
        return Decision(False, f"alignment_skip:{side_or_reason}", packet=packet, rules=rules)

    # Researcher override — 15m data is more current for day trading.
    # When researcher strongly disagrees with the gate's side, trust it.
    if packet and settings.research_enabled:
        pkt_side_raw = packet.get("pred_1h_close_side", "")
        pkt_conf = float(packet.get("confidence") or 0)
        pkt_armed = packet.get("arm", False)
        researcher_side = "BUY" if pkt_side_raw == "bull" else ("SELL" if pkt_side_raw == "bear" else "")

        if researcher_side and researcher_side != side_or_reason:
            if pkt_armed and pkt_conf >= 0.70:
                # Armed researcher with high confidence overrides gate side
                side_or_reason = researcher_side
            elif pkt_conf >= 0.80:
                # Very confident unarmed researcher blocks contradicting trade
                return Decision(False, f"researcher_veto:{pkt_side_raw}@{pkt_conf:.2f}",
                                packet=packet, rules=rules)

    # Confidence adjustments — trend alignment + researcher.
    conf_adj = 1.0
    if market != "NONE" and bot_pred == market:
        conf_adj *= 1.15  # ML agrees with trend — high conviction
    elif market != "NONE" and bot_pred != market:
        conf_adj *= 0.85  # ML disagrees — mild penalty (trend may be stale)

    # Researcher packet adjustment
    if packet and settings.research_enabled:
        pkt_conf = float(packet.get("confidence") or 0)
        pkt_side = packet.get("pred_1h_close_side", "")
        want = "bull" if side_or_reason == "BUY" else "bear"
        if pkt_side == want and pkt_conf >= 0.65:
            conf_adj *= 1.15  # researcher agrees with conviction → strong boost
        elif pkt_side == want:
            conf_adj *= 1.05  # researcher agrees weakly → small boost
        elif pkt_side and pkt_side != want and pkt_conf >= 0.75:
            conf_adj *= 0.80  # researcher strongly disagrees → significant penalty
        elif pkt_side and pkt_side != want:
            conf_adj *= 0.90  # researcher disagrees → mild penalty

    return Decision(True, "aligned", packet=packet, rules=rules,
                    venue="FUTURES", side=side_or_reason, confidence_adj=conf_adj)
