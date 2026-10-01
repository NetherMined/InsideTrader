"""Propose tighten-only rule patches from rolling hour reviews."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.config import settings
from bot.research import PENDING_PATCH_KEY, RULES_KEY, dumps, loads


DEFAULT_RULES = {
    "rule_version": 1,
    "require_15m_confirm": True,
    "research_min_confidence": 0.55,
    "allow_1h_only_if_research_stale": True,
}


async def load_rules(redis) -> dict[str, Any]:
    data = loads(await redis.get(RULES_KEY))
    if not isinstance(data, dict):
        return dict(DEFAULT_RULES)
    merged = dict(DEFAULT_RULES)
    merged.update(data)
    return merged


async def save_rules(redis, rules: dict[str, Any]) -> None:
    await redis.set(RULES_KEY, dumps(rules))


def maybe_patch(reviews: list[dict[str, Any]], rules: dict[str, Any]) -> dict[str, Any] | None:
    window = int(getattr(settings, "patch_window_hours", 10))
    min_count = int(getattr(settings, "patch_min_label_count", 4))
    recent = reviews[-window:]
    labels = Counter()
    for rev in recent:
        for lab in rev.get("labels") or []:
            labels[lab] += 1
    if not labels:
        return None
    top, count = labels.most_common(1)[0]
    if count < min_count:
        return None

    tighten_map = {
        "wrong_bias": "raise research_min_confidence by 0.03 and require 15m confirm",
        "right_bias_bad_timing": "require 15m reclaim of sweep level before arm=true",
        "stop_too_tight": "widen 15m invalidation by 0.3 ATR (do not auto-apply — human defer)",
        "chased_bad_packet": "block entry when packet confidence < 0.60",
        "ignored_good_packet": "keep require_15m_confirm but surface packet on dashboard",
        "overfit_15m": "ignore packets that flipped side twice inside the same hour",
        "ignored_invalidation": "flatten when packet invalidation prints",
        "entered_away_from_zone": "raise confidence threshold +0.03 when no zone within 1 ATR",
        "structure_disagreed": "block entry when structure trend opposes ML prediction",
        "rr_violated": "enforce 2.5:1 minimum R:R on all new trades",
        "zone_sl_failed": "widen zone SL buffer by 0.05 ATR",
        "bos_against_after_entry": "add structure_confirmed requirement before entry",
    }
    loosen = top in {"stop_too_tight", "ignored_good_packet", "zone_sl_failed"}
    return {
        "from_rule_version": rules.get("rule_version", 1),
        "change": tighten_map.get(top, f"review label {top}"),
        "because": f"{count} of last {len(recent)} hours labelled {top}",
        "scope": "all sessions",
        "labels": [top],
        "expires_after_hours": int(getattr(settings, "patch_expiry_hours", 48)),
        "proposed_at": datetime.now(timezone.utc).isoformat(),
        "tighten": not loosen,
    }


async def write_pending(redis, patch: dict[str, Any]) -> None:
    await redis.setex(PENDING_PATCH_KEY, 48 * 3600, dumps(patch))


async def apply_tighten_if_allowed(redis, patch: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    if not patch.get("tighten"):
        patch["status"] = "deferred"
        return patch
    if not getattr(settings, "patch_auto_accept_tighten_only", True):
        patch["status"] = "pending"
        return patch
    version = int(rules.get("rule_version", 1)) + 1
    rules["rule_version"] = version
    if "require 15m confirm" in patch.get("change", "").lower():
        rules["require_15m_confirm"] = True
    if "research_min_confidence" in patch.get("change", ""):
        rules["research_min_confidence"] = min(0.80, float(rules.get("research_min_confidence", 0.55)) + 0.03)
    if "confidence threshold +0.03" in patch.get("change", ""):
        rules["confidence_boost_no_zone"] = min(0.15, float(rules.get("confidence_boost_no_zone", 0.0)) + 0.03)
    if "structure trend opposes" in patch.get("change", ""):
        rules["require_structure_agreement"] = True
    if "structure_confirmed requirement" in patch.get("change", ""):
        rules["require_structure_confirmed"] = True
    if "2.5:1 minimum R:R" in patch.get("change", ""):
        rules["enforce_min_rr"] = True
    if "zone SL buffer" in patch.get("change", ""):
        rules["zone_sl_buffer_atr"] = min(0.40, float(rules.get("zone_sl_buffer_atr", 0.15)) + 0.05)
    rules["patch_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=int(patch.get("expires_after_hours", 48)))
    ).isoformat()
    await save_rules(redis, rules)
    patch["status"] = "accepted"
    patch["to_version"] = version
    patch["decided_by"] = "manager"
    patch["decided_at"] = datetime.now(timezone.utc).isoformat()
    return patch
