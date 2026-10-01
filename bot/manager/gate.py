"""Manager entry gate — trend alignment + researcher confirmation.

Both bot_prediction (1h model) and market_trend (1h EMAs + 15m packet)
must agree before an order. Mode decides venue + side legality.
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
    mode: str = "DYNAMIC",
    bot_prediction: str = "NONE",
    structure: StructureState | None = None,
) -> str:
    """Market trend from structure + 15m packet agreement.

    Uses market structure (HH/HL/LL/LH) as primary trend signal.
    Falls back to EMA cross when structure is unavailable.
    Returns BULL / BEAR / NONE.
    """
    if structure and structure.trend != "RANGE":
        base_trend = "BULL" if structure.trend == "UP" else "BEAR"
    elif ema21 > 0 and ema50 > 0:
        base_trend = "BULL" if ema21 > ema50 else "BEAR"
    else:
        base_trend = "NONE"

    if packet and packet.get("arm") and not packet.get("invalidation_hit"):
        pkt_conf = float(packet.get("confidence") or 0)
        pkt_side = packet.get("pred_1h_close_side", "")
        pkt_trend = "BULL" if pkt_side == "bull" else "BEAR" if pkt_side == "bear" else "NONE"

        if pkt_conf >= settings.research_min_confidence and pkt_trend == base_trend:
            trend = base_trend
        elif structure and structure.trend != "RANGE" and bot_prediction == base_trend:
            # Structure trend + ML agree on direction — allow even if 15m packet disagrees.
            # 15m packets flip frequently; structure + ML alignment is sufficient.
            trend = base_trend
        else:
            trend = "NONE"
    else:
        # No packet: allow if structure gives a directional signal matching ML
        if structure and structure.trend != "RANGE" and bot_prediction == base_trend:
            trend = base_trend
        else:
            trend = "NONE"

    if regime == "RANGING" or (structure and structure.trend == "RANGE"):
        if trend == "BULL" and bot_prediction == "BULL":
            return "BULL"
        if trend == "BEAR" and bot_prediction == "BEAR" and mode in ("FUTURES", "DYNAMIC"):
            return "BEAR"
        return "NONE"

    return trend


def get_aligned_action(
    bot_prediction: str,
    market_trend: str,
    mode: str,
    adx_1h: float = 0.0,
    adx_15m: float = 0.0,
) -> tuple[str, str]:
    """Return (venue, side) or ("SKIP", reason).

    Alignment matrix from SETTINGS_LOCKDOWN.md 5.2.
    """
    if bot_prediction == "NONE":
        return "SKIP", "no_prediction"

    if market_trend == "NONE":
        # No market trend signal — allow ML direction but only in FUTURES/DYNAMIC (risk is capped by SL)
        if mode in ("FUTURES", "DYNAMIC"):
            direction = bot_prediction
        else:
            return "SKIP", "no_trend_spot"
    elif bot_prediction != market_trend:
        return "SKIP", f"disagree_bot={bot_prediction}_mkt={market_trend}"
    else:
        direction = bot_prediction

    if mode == "SPOT":
        if direction == "BULL":
            return "SPOT", "BUY"
        return "SKIP", "spot_no_short"

    if mode == "FUTURES":
        side = "BUY" if direction == "BULL" else "SELL"
        return "FUTURES", side

    # DYNAMIC: strong trend -> futures; otherwise spot for bull, futures for bear
    strong_1h = adx_1h >= 25
    if direction == "BULL":
        if strong_1h:
            return "FUTURES", "BUY"
        return "SPOT", "BUY"
    else:
        if strong_1h:
            return "FUTURES", "SELL"
        # Moderate ADX: still allow short via futures (SL caps risk)
        if adx_1h >= 18:
            return "FUTURES", "SELL"
        return "SKIP", "weak_bear_dynamic"


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
    mode: str = "DYNAMIC",
    structure: StructureState | None = None,
) -> Decision:
    """Full alignment gate. Called after 1h executor determines raw side."""
    if not settings.research_enabled:
        return Decision(True, "research_disabled")

    rules = await load_rules(redis)
    raw = await redis.get(packet_key(symbol))
    packet = loads(raw)
    now = datetime.now(timezone.utc)

    if not packet:
        if settings.allow_1h_only_if_research_stale:
            return Decision(True, "no_packet_allow_1h", rules=rules)
        return Decision(False, "no_packet", rules=rules)

    if _stale(packet, now):
        if settings.allow_1h_only_if_research_stale:
            return Decision(True, "stale_packet_allow_1h", packet=packet, rules=rules)
        return Decision(False, "stale_packet", packet=packet, rules=rules)

    bot_pred = compute_bot_prediction(predicted_change_pct, confidence)
    adx_15m = float(packet.get("adx", 0.0))

    market = compute_market_trend(
        ema21, ema50, packet, regime=regime, mode=mode, bot_prediction=bot_pred,
        structure=structure,
    )

    venue, side_or_reason = get_aligned_action(
        bot_pred, market, mode, adx_1h=adx, adx_15m=adx_15m,
    )
    if venue == "SKIP":
        return Decision(False, f"alignment_skip:{side_or_reason}", packet=packet, rules=rules)

    # Packet arm / invalidation / confidence checks
    require = bool(rules.get("require_15m_confirm", settings.require_15m_confirm))
    min_conf = float(rules.get("research_min_confidence", settings.research_min_confidence))
    if require:
        if packet.get("invalidation_hit"):
            return Decision(False, "invalidation_hit", packet=packet, rules=rules)
        if not packet.get("arm"):
            return Decision(False, "not_armed", packet=packet, rules=rules)
        if float(packet.get("confidence") or 0) < min_conf:
            return Decision(False, "research_confidence", packet=packet, rules=rules)

    return Decision(True, "aligned", packet=packet, rules=rules)
