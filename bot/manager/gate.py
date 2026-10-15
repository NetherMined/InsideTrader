"""Manager entry gate — trend alignment + researcher agreement filter.

Futures only. Side comes from the manager's ensemble prediction and is never flipped.
  - 1h trend (structure / EMA) against the prediction → skip
  - 1h trend with the prediction → confidence ×1.15
The researcher packet only scales the trade:
  - agrees + armed   → confidence ×1.10, full size
  - agrees + unarmed → confidence ×0.90, size ×0.70
  - missing or stale → size ×0.70
  - contradicts      → size ×0.50
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    confidence_adj: float = 1.0
    size_adj: float = 1.0


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
    if structure and structure.confirmed and structure.trend == "UP":
        base_trend = "BULL"
    elif structure and structure.confirmed and structure.trend == "DOWN":
        base_trend = "BEAR"
    elif ema21 > 0 and ema50 > 0:
        ema_spread = abs(ema21 - ema50) / ema50 * 100
        if ema_spread >= 0.3:
            base_trend = "BULL" if ema21 > ema50 else "BEAR"
        else:
            base_trend = "NONE"
    else:
        base_trend = "NONE"

    if base_trend == "NONE":
        return "NONE"

    if regime == "RANGING" or (structure and structure.trend == "RANGE"):
        if bot_prediction == base_trend:
            return base_trend
        return "NONE"

    return base_trend


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
    if bot_pred == "NONE":
        return Decision(False, "alignment_skip:no_prediction", packet=packet, rules=rules)
    side = "BUY" if bot_pred == "BULL" else "SELL"

    # 1h trend can veto the prediction, never flip it
    market = compute_market_trend(
        ema21, ema50, packet, regime=regime, mode="FUTURES", bot_prediction=bot_pred,
        structure=structure,
    )
    if market != "NONE" and market != bot_pred:
        return Decision(False, f"trend_contradicts:{market.lower()}", packet=packet, rules=rules)
    conf_adj = 1.15 if market == bot_pred else 1.0

    # Researcher only scales the trade
    pkt_side_raw = packet.get("pred_1h_close_side", "") if packet else ""
    researcher_side = "BUY" if pkt_side_raw == "bull" else ("SELL" if pkt_side_raw == "bear" else "")
    if not researcher_side:
        size_adj = 0.70
    elif researcher_side != side:
        # Its invalidation level is for the other side, so keep it off this trade
        size_adj = 0.50
        packet = None
    elif packet.get("arm", False):
        conf_adj *= 1.10
        size_adj = 1.0
    else:
        conf_adj *= 0.90
        size_adj = 0.70

    return Decision(
        True, "aligned",
        packet=packet, rules=rules,
        venue="FUTURES", side=side,
        confidence_adj=conf_adj, size_adj=size_adj,
    )
