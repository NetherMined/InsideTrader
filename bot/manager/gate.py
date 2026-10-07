"""Manager entry gate — trend alignment + researcher agreement filter.

Futures only. Side comes from the manager (1h ML + market structure).
The researcher packet is required for entry:
  - missing or stale → skip
  - agrees + armed   → confidence ×1.10, full size
  - agrees + unarmed → confidence ×0.90, size ×0.70
  - contradicts      → skip (never flip side)
Override is disabled until the per-setup scorecard shows positive edge.
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

    # Require a fresh researcher packet — no packet means no entry
    if not packet or not settings.research_enabled:
        return Decision(False, "no_research_packet", packet=None, rules=rules)

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

    # Researcher agreement check — contradictions block, never flip side
    pkt_side_raw = packet.get("pred_1h_close_side", "")
    pkt_conf = float(packet.get("confidence") or 0)
    pkt_armed = packet.get("arm", False)
    researcher_side = "BUY" if pkt_side_raw == "bull" else ("SELL" if pkt_side_raw == "bear" else "")

    if not researcher_side:
        return Decision(False, "no_research_packet:no_side", packet=packet, rules=rules)

    if researcher_side != side_or_reason:
        return Decision(
            False,
            f"researcher_contradicts:{pkt_side_raw}@{pkt_conf:.2f}",
            packet=packet, rules=rules,
        )

    # Researcher agrees — armed gets a boost, unarmed gets a penalty + smaller size
    if pkt_armed:
        conf_adj_researcher = 1.10
        size_adj = 1.0
    else:
        conf_adj_researcher = 0.90
        size_adj = 0.70

    # Trend alignment confidence multiplier
    conf_adj = 1.0
    if market != "NONE" and bot_pred == market:
        conf_adj *= 1.15
    elif market != "NONE" and bot_pred != market:
        conf_adj *= 0.85

    conf_adj *= conf_adj_researcher

    return Decision(
        True, "aligned",
        packet=packet, rules=rules,
        venue="FUTURES", side=side_or_reason,
        confidence_adj=conf_adj, size_adj=size_adj,
    )
