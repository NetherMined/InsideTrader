"""Manager entry gate — trend alignment + researcher confirmation.

Futures only. Both bot_prediction (1h model) and market_trend (structure,
then EMA) must agree before an order. The returned side is the order side.
Researcher never calls this with an order.
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

    A fresh 15m packet can confirm the structure trend. It cannot flip it.
    A missing or stale packet does not invent a trend.
    """
    if structure and structure.trend == "UP":
        base_trend = "BULL"
    elif structure and structure.trend == "DOWN":
        base_trend = "BEAR"
    elif ema21 > 0 and ema50 > 0:
        base_trend = "BULL" if ema21 > ema50 else "BEAR"
    else:
        base_trend = "NONE"

    if base_trend == "NONE":
        return "NONE"

    if packet and packet.get("arm") and not packet.get("invalidation_hit"):
        pkt_conf = float(packet.get("confidence") or 0)
        pkt_side = packet.get("pred_1h_close_side", "")
        pkt_trend = "BULL" if pkt_side == "bull" else "BEAR" if pkt_side == "bear" else "NONE"
        if pkt_conf >= settings.research_min_confidence and pkt_trend not in ("NONE", base_trend):
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
    if market_trend == "NONE":
        return "SKIP", "no_trend"
    if bot_prediction != market_trend:
        return "SKIP", f"disagree_bot={bot_prediction}_mkt={market_trend}"

    if sentiment == "BULLISH" and bot_prediction == "BEAR":
        return "SKIP", "sentiment_blocks_short"
    if sentiment == "BEARISH" and bot_prediction == "BULL":
        return "SKIP", "sentiment_blocks_long"

    side = "BUY" if bot_prediction == "BULL" else "SELL"
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

    if structure and structure.confirmed:
        if side_or_reason == "SELL" and structure.trend == "UP":
            return Decision(False, "structure_blocks_short", packet=packet, rules=rules)
        if side_or_reason == "BUY" and structure.trend == "DOWN":
            return Decision(False, "structure_blocks_long", packet=packet, rules=rules)

    if settings.research_enabled and packet:
        require = bool(rules.get("require_15m_confirm", settings.require_15m_confirm))
        min_conf = float(rules.get("research_min_confidence", settings.research_min_confidence))
        if require:
            if packet.get("invalidation_hit"):
                return Decision(False, "invalidation_hit", packet=packet, rules=rules)
            if not packet.get("arm"):
                return Decision(False, "not_armed", packet=packet, rules=rules)
            if float(packet.get("confidence") or 0) < min_conf:
                return Decision(False, "research_confidence", packet=packet, rules=rules)

    return Decision(True, "aligned", packet=packet, rules=rules, venue="FUTURES", side=side_or_reason)
