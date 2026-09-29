"""Redis key helpers and JSON codec for the dual-bot bus."""

from __future__ import annotations

import json
from typing import Any

PACKET_KEY = "research:packet:{symbol}"
PACKET_HOUR_KEY = "research:packet:{symbol}:{hour}"
REVIEW_KEY = "research:review:{symbol}:{hour}"
ERRORS_KEY = "research:errors:rolling"
RULES_KEY = "manager:rules"
PENDING_PATCH_KEY = "manager:patches:pending"
PATCH_HISTORY_KEY = "manager:patches:history"
BOOK_KEY = "manager:book"


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def packet_key(symbol: str) -> str:
    return PACKET_KEY.format(symbol=symbol)


def packet_hour_key(symbol: str, hour_iso: str) -> str:
    return PACKET_HOUR_KEY.format(symbol=symbol, hour=hour_iso)


def review_key(symbol: str, hour_iso: str) -> str:
    return REVIEW_KEY.format(symbol=symbol, hour=hour_iso)
