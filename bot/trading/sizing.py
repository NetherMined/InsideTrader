"""Heat-based position sizing.

Experiments: one position, full capital as futures margin.
Heat limit and per-symbol cap are 100%. Per-trade heat is the free heat,
which is the whole account when nothing is open.
"""

from loguru import logger
from bot.config import settings

MIN_NOTIONAL_FUTURES = 35.0
MIN_NOTIONAL_SPOT = 10.0


def calculate_trade_heat(
    capital_usdt: float,
    open_heat_usdt: float,
    symbol_heat_usdt: float,
    params: dict,
) -> float:
    """Return the max USDT heat (cash/margin) this trade may use, or 0 if illegal."""
    heat_limit = capital_usdt * params["heat_limit_pct"] / 100
    free_heat = max(0.0, heat_limit - open_heat_usdt)

    max_sim = params["max_concurrent_trades"]
    per_trade_heat = heat_limit / max_sim if max_sim > 0 else free_heat

    max_sym_heat = capital_usdt * params["max_single_symbol_heat_pct"] / 100
    sym_remaining = max(0.0, max_sym_heat - symbol_heat_usdt)

    trade_heat = min(per_trade_heat, free_heat, sym_remaining)
    return trade_heat


def calculate_position_size(
    capital_usdt: float,
    price: float,
    mode: str,
    trade_heat: float,
    leverage: int | None = None,
) -> tuple[float, float]:
    """Return (quantity, notional_usdt) sized by heat budget.

    For SPOT: heat = notional (cash locked).
    For FUTURES: heat = margin = notional / leverage.
    """
    if trade_heat <= 0 or price <= 0:
        return 0.0, 0.0

    _leverage = leverage if leverage is not None else settings.futures_leverage

    if mode == "FUTURES":
        margin = trade_heat
        notional = margin * _leverage
    else:
        notional = trade_heat

    min_notional = MIN_NOTIONAL_FUTURES if mode == "FUTURES" else MIN_NOTIONAL_SPOT
    if notional < min_notional:
        logger.debug(
            f"Position notional ${notional:.2f} below minimum ${min_notional} — skipping"
        )
        return 0.0, 0.0

    quantity = notional / price
    return round(quantity, 8), round(notional, 4)


def calculate_sl_tp_prices(
    entry_price: float,
    mode: str,
    side: str = "BUY",
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    leverage: int | None = None,
    atr_pct: float | None = None,
) -> tuple[float, float]:
    """Return (stop_loss_price, take_profit_price).

    Both SL and TP stretch with ATR so volatile pairs get wider stops.
    SL is capped so leveraged loss never exceeds 6%.
    TP: 1.5x ATR floored at config default, capped at 8%.
    """
    _lev = leverage if leverage is not None else settings.futures_leverage
    _default_sl = sl_pct if sl_pct is not None else settings.stop_loss_percent
    _default_tp = tp_pct if tp_pct is not None else settings.take_profit_percent
    # Max SL price move so leveraged loss stays under ~4%
    # At 5x: 0.76% price → 3.8% leveraged + slippage ≈ 4%
    max_sl_pct = max(0.5, 3.8 / max(_lev, 1))

    if atr_pct is not None and atr_pct > 0:
        _sl_pct = max(_default_sl, min(max_sl_pct, atr_pct * 1.2))
        _tp_pct = max(_default_tp, min(8.0, atr_pct * 1.5))
    else:
        _sl_pct = min(_default_sl, max_sl_pct)
        _tp_pct = _default_tp

    if side == "BUY":
        stop_loss = entry_price * (1 - _sl_pct / 100)
        take_profit = entry_price * (1 + _tp_pct / 100)
    else:
        stop_loss = entry_price * (1 + _sl_pct / 100)
        take_profit = entry_price * (1 - _tp_pct / 100)

    return round(stop_loss, 8), round(take_profit, 8)
