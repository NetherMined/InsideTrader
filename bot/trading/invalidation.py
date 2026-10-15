"""Researcher invalidation levels for open and pending trades.

A packet's invalidation level belongs to the side the researcher predicted:
hour_low for a bull packet, hour_high for a bear packet. It means nothing for
a trade on the other side, so it is only applied when the sides match.
"""

from typing import Any

_SIDE_BY_PACKET = {"bull": "BUY", "bear": "SELL"}


def packet_invalidation(packet: dict[str, Any] | None, side: str) -> float | None:
    """Return the packet's invalidation level if the packet agrees with `side`, else None."""
    if not packet:
        return None
    if _SIDE_BY_PACKET.get(str(packet.get("pred_1h_close_side", ""))) != side:
        return None
    inv = packet.get("invalidation")
    try:
        inv = float(inv)
    except (TypeError, ValueError):
        return None
    return inv if inv > 0 else None


def is_past_invalidation(side: str, price: float, invalidation: float, buffer_pct: float = 0.0) -> bool:
    """True once price has broken the level by more than buffer_pct (BUY below, SELL above)."""
    if side == "BUY":
        return price < invalidation * (1 - buffer_pct / 100)
    return price > invalidation * (1 + buffer_pct / 100)
