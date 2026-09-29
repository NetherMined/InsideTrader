"""Manager entry gate. Researcher never calls this with an order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bot.config import settings
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


async def should_enter(redis, symbol: str, manager_side: str) -> Decision:
    """manager_side is BUY or SELL from the 1h executor."""
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

    pred = packet.get("pred_1h_close_side")
    want = "bull" if manager_side == "BUY" else "bear"
    if pred and pred != want:
        return Decision(False, f"tf_disagree_15m={pred}_1h={want}", packet=packet, rules=rules)

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
