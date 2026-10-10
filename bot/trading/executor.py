"""Multi-trade executor.

Main trading loop that:
  1. Monitors open positions every 5s — closes on SL/TP hit or daily target
  2. Tries to open new trades from ranked pairs when slots are available
  3. Respects all risk rules from RiskManager
  4. Supports LONG and SHORT positions based on ML prediction sign
  5. Supports paper trading (default) and live trading
  6. Reads bot commands from Redis (stop / pause / resume)
  7. Integrates funding rate monitoring and regime detection

Per `strat.md` improvements:
  - Funding rate awareness (collect rather than pay)
  - Regime detection (trend-following vs mean-reversion)
  - Correlation filter (avoid correlated pairs)
  - Fee tracking (gross vs net P&L validation)
"""

import asyncio
import json
from datetime import datetime, timezone

import redis.asyncio as aioredis
from loguru import logger

from bot.config import settings
from bot.analysis.ranker import RankedPair, filter_correlated_pairs, fetch_pair_correlations
from bot.analysis.mode_classifier import classify_mode
from bot.analysis.regime_detector import detect_regime, RegimeResult
from bot.analysis.funding_rate import fetch_funding_rates, get_funding_signal
from bot.analysis.structure import (
    classify_structure, detect_zones, calculate_zone_sl, calculate_zone_tp,
    is_near_zone, compute_rr_ratio, StructureState, Zone,
)
from bot.trading.risk import RiskManager, COMMAND_KEY
from bot.trading.sizing import calculate_position_size, calculate_sl_tp_prices, calculate_trade_heat
from bot.trading.journal import (
    open_position, close_position, update_position_price, get_open_positions, update_position_tp, get_pnl_summary,
)
from bot.trading.paper import simulate_buy, simulate_sell, simulate_buy_back, simulate_sell_short
from bot.trading.orders import (
    place_spot_market_buy, place_spot_market_sell,
    place_futures_market_buy, place_futures_market_close, place_futures_market_sell,
    get_current_price, get_account_balance,
)
from bot.notifications import telegram, events as ev

LOOP_INTERVAL = 5

_PAPER_MODE_KEY = "bot:paper_trading_mode"
_LIVE_ENABLED_KEY = "bot:live_trading_enabled"
_USE_TESTNET_KEY = "bot:use_testnet"
_REGIME_KEY = "bot:current_regime"
_FUNDING_CACHE_KEY = "bot:funding_rates_cache"
_FUNDING_CACHE_TTL = 300  # 5 minutes
_CORR_CACHE_KEY = "bot:correlation_matrix"
_CORR_CACHE_TTL = 3600  # 1 hour
_SYMBOL_COOLDOWN_SECONDS = 300  # 5 min cooldown after closing a symbol before reopening
_MACRO_TREND_CACHE_KEY = "bot:macro_trend_cache"
_MACRO_TREND_CACHE_TTL = 120  # 2 minutes
_STRUCTURE_CACHE_PREFIX = "structure:"


async def _load_structure_cached(
    redis: aioredis.Redis, symbol: str
) -> tuple[StructureState | None, list[Zone]]:
    """Load structure state + zones from Redis cache, or compute from 1h candles."""
    cache_key = f"{_STRUCTURE_CACHE_PREFIX}{symbol}"
    cached = await redis.get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            state = StructureState.from_dict(data["state"])
            zones = [Zone.from_dict(z) for z in data.get("zones", [])]
            return state, zones
        except Exception:
            pass

    try:
        from bot.research.packets import load_candles
        df = await load_candles(symbol, "1h", limit=200)
        if df is None or len(df) < 30:
            return None, []
        state = classify_structure(df, lookback=settings.swing_lookback)
        zones = detect_zones(df, state)
        payload = json.dumps({
            "state": state.to_dict(),
            "zones": [z.to_dict() for z in zones],
        })
        await redis.setex(cache_key, settings.structure_cache_ttl, payload)
        return state, zones
    except Exception as e:
        logger.debug(f"{symbol}: structure load failed ({e})")
        return None, []


async def _get_macro_trend(redis: aioredis.Redis) -> str:
    """Detect overall market direction from tracked pair 24h changes.

    Returns "BULLISH", "BEARISH", or "NEUTRAL".
    Cached for 2 minutes to avoid per-loop overhead.
    """
    cached = await redis.get(_MACRO_TREND_CACHE_KEY)
    if cached:
        return cached.decode() if isinstance(cached, bytes) else cached

    keys = await redis.keys("price:*")
    changes = []
    for key in keys:
        raw = await redis.get(key)
        if not raw:
            continue
        try:
            import json as _json
            d = _json.loads(raw)
            chg = d.get("change_pct")
            if chg is not None:
                changes.append(float(chg))
        except Exception:
            pass

    if not changes:
        return "NEUTRAL"

    mean_chg = sum(changes) / len(changes)
    pct_up = sum(1 for c in changes if c > 0) / len(changes)

    if mean_chg > 1.5 and pct_up > 0.55:
        trend = "BULLISH"
    elif mean_chg < -1.5 and pct_up < 0.45:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    await redis.setex(_MACRO_TREND_CACHE_KEY, _MACRO_TREND_CACHE_TTL, trend)
    return trend
_TRAIL_ACTIVATION_PCT = 4.0    # arm the trail at +4% effective (0.8% price at 5x); replay beat the old 1.0
_TRAIL_REVERSAL_PCT = 2.0      # give back 2% effective (0.4% price) from the peak before closing
_MIN_PROFIT_USD = 2.00         # never close a winning trade below $2 PnL (non-SL)
_MAX_LOSS_PCT = 6.0            # leveraged loss cap (6% at 5x = 1.2% price move)
_EMERGENCY_LOSS_PCT = 10.0     # leveraged loss cap during the entry grace window
_SL_GRACE_SECONDS = 90         # ignore normal SL right after entry to ride out entry noise
_INVALIDATION_MIN_HOLD_S = 300  # structure invalidation only counts after 5 min
_INVALIDATION_BUFFER = 0.003   # price must break the level by 0.3%
_BREAKER_TRADES = 8            # symbol circuit breaker: look at the last N closed trades
_BREAKER_MIN_TRADES = 4        # ...needing at least this many inside the window
_BREAKER_LOSS_USD = 40.0       # ...block the symbol once their net loss reaches this
_BREAKER_WINDOW_HOURS = 24     # ...and only count trades from this window
_VIABILITY_WINDOW_HOURS = 72   # viability block only counts trades from this window, so blocked symbols retry
_TP_TRAIL_PREFIX = "bot:tp_trail:"  # TP follower anchor per position
_FORCE_TRADE_MODE_KEY = "bot:force_trade_mode"  # SPOT / FUTURES / DYNAMIC
_DISABLE_FUTURES_BUY_KEY = "bot:disable_futures_buy"  # 1 = gate FUTURES BUY to SPOT BUY


async def _fetch_funding_rates_cached(redis: aioredis.Redis, symbols: list[str]) -> dict[str, dict]:
    """Fetch funding rates with caching to avoid excessive API calls."""
    cached = await redis.get(_FUNDING_CACHE_KEY)
    if cached:
        try:
            import json as _json
            cached_data = _json.loads(cached)
            if datetime.now(timezone.utc).timestamp() - cached_data.get("timestamp", 0) < _FUNDING_CACHE_TTL:
                return cached_data.get("rates", {})
        except Exception:
            pass

    rates = await fetch_funding_rates(symbols)
    await redis.setex(
        _FUNDING_CACHE_KEY,
        _FUNDING_CACHE_TTL * 2,
        json.dumps({"timestamp": datetime.now(timezone.utc).timestamp(), "rates": rates}),
    )
    return rates


async def _fetch_correlations_cached(redis: aioredis.Redis, symbols: list[str]) -> dict[str, dict[str, float]]:
    """Fetch Pearson correlation matrix with 1-hour Redis caching."""
    if len(symbols) < 2:
        return {}
    cached = await redis.get(_CORR_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass
    corr = await fetch_pair_correlations(symbols)
    if corr:
        try:
            await redis.setex(_CORR_CACHE_KEY, _CORR_CACHE_TTL, json.dumps(corr))
        except Exception:
            pass
    return corr


async def _detect_regime_cached(
    redis: aioredis.Redis,
    symbol: str,
    adx: float,
    atr_pct: float,
    bb_width_pct: float = 0.05,
) -> RegimeResult | None:
    """Detect market regime with Redis caching.

    detect_regime() is synchronous and needs indicator values (not just a
    symbol), so callers must pass adx/atr from the ranked pair.
    """
    cache_key = f"bot:regime:{symbol}"
    cached = await redis.get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            return RegimeResult(
                regime=data["regime"],
                adx=float(data.get("adx", adx)),
                atr_pct=float(data.get("atr_pct", atr_pct)),
                bb_width_pct=float(data.get("bb_width_pct", bb_width_pct)),
                confidence_multiplier=float(data.get("confidence_multiplier", 1.0)),
                strategy=data.get("strategy", "trend_following"),
            )
        except Exception:
            pass
    try:
        regime = detect_regime(float(adx), float(atr_pct), float(bb_width_pct))
    except Exception as e:
        logger.warning(f"{symbol}: regime detection failed ({e})")
        return None
    try:
        await redis.setex(
            cache_key,
            600,
            json.dumps({
                "regime": regime.regime,
                "adx": regime.adx,
                "atr_pct": regime.atr_pct,
                "bb_width_pct": regime.bb_width_pct,
                "confidence_multiplier": regime.confidence_multiplier,
                "strategy": regime.strategy,
            }),
        )
    except Exception:
        pass
    return regime


async def _get_trading_mode_flags(redis: aioredis.Redis) -> dict:
    paper_raw = await redis.get(_PAPER_MODE_KEY)
    live_raw = await redis.get(_LIVE_ENABLED_KEY)
    testnet_raw = await redis.get(_USE_TESTNET_KEY)
    return {
        "paper": (paper_raw == "true") if paper_raw is not None else settings.paper_trading_mode,
        "live_enabled": (live_raw == "true") if live_raw is not None else settings.live_trading_enabled,
        "use_testnet": (testnet_raw == "true") if testnet_raw is not None else settings.use_testnet,
    }


async def _get_live_price(symbol: str, redis: aioredis.Redis, max_age_seconds: int = 30) -> float | None:
    try:
        raw = await redis.get(f"price:{symbol}")
    except Exception:
        raw = None
    if raw:
        try:
            data = json.loads(raw)
            ts = data.get("ts")
            if ts:
                age = datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(ts).timestamp()
                if age > max_age_seconds:
                    logger.debug(f"{symbol}: cached price is {age:.0f}s old, fetching fresh")
                    return await get_current_price(symbol)
            return float(data["price"])
        except Exception:
            pass
    return await get_current_price(symbol)


def _determine_side(predicted_change_pct: float) -> str:
    if predicted_change_pct > 0:
        return "BUY"
    return "SELL"


async def _get_market_sentiment(redis: aioredis.Redis) -> tuple[str, float]:
    """Return (sentiment, advance_ratio) from Redis. Defaults to NEUTRAL/0.5 if unavailable."""
    raw = await redis.get("market:sentiment")
    if raw:
        try:
            data = json.loads(raw)
            return data.get("sentiment", "NEUTRAL"), float(data.get("advance_ratio", 0.5))
        except Exception:
            pass
    return "NEUTRAL", 0.5


async def _check_and_close_positions(redis: aioredis.Redis, risk: RiskManager, params: dict, use_testnet: bool | None = None, capital: float = 0.0) -> None:
    positions = await get_open_positions()

    for pos in positions:
        symbol = pos["symbol"]
        current_price = await _get_live_price(symbol, redis)
        if current_price is None:
            logger.warning(f"{symbol}: price fetch failed — skipping position check (SL/TP frozen until price returns)")
            continue

        entry_price = float(pos["entry_price"])
        quantity = float(pos["quantity"])
        mode = pos["mode"]
        leverage = int(pos.get("leverage") or 1)
        stop_loss = float(pos["stop_loss_price"])
        take_profit = float(pos["take_profit_price"])
        pos_id = pos["id"]
        trade_id = pos.get("trade_id")
        paper = bool(pos["paper_trade"])
        side = pos.get("side", "BUY")

        if side == "BUY":
            raw_pnl_pct = (current_price / entry_price - 1) * 100
        else:
            raw_pnl_pct = (entry_price - current_price) / entry_price * 100
        effective_pnl_pct = raw_pnl_pct * leverage
        notional = quantity * entry_price
        pnl_usdt = notional * raw_pnl_pct / 100  # notional already carries the leverage

        try:
            await update_position_price(pos_id, current_price, pnl_usdt)
        except Exception as e:
            logger.error(f"Failed to update position {pos_id} price: {e}")

        recovery_key = "bot:recovery_mode"
        in_recovery_raw = await redis.sismember(recovery_key, str(pos_id))
        be_eligible = (side == "BUY" and take_profit > entry_price) or (side == "SELL" and take_profit < entry_price)
        prev_price_key = f"bot:prev_price:{pos_id}"
        prev_price_raw = await redis.get(prev_price_key)
        prev_price = float(prev_price_raw) if prev_price_raw else current_price
        await redis.setex(prev_price_key, 3600, str(current_price))
        price_moving_toward_profit = (
            (side == "BUY" and current_price > prev_price) or
            (side == "SELL" and current_price < prev_price)
        )
        regime_val = pos.get("regime", "")
        is_trending = str(regime_val).upper() in ("TRENDING", "TRANSITION")
        # Zone-based SL trades skip break-even — the zone thesis must play out
        _entry_ind = pos.get("entry_indicators") or {}
        if isinstance(_entry_ind, str):
            try:
                _entry_ind = json.loads(_entry_ind)
            except Exception:
                _entry_ind = {}
        _zone_sl = _entry_ind.get("zone_based_sl", False)
        # Break-even recovery disabled — at 5x leverage the -1.5% trigger fires
        # on 0.3% price dips, moving TP to entry and closing for pennies on any bounce.

        manual_close = await redis.get(f"close_position:{pos_id}")
        if manual_close:
            await redis.delete(f"close_position:{pos_id}")
            close_reason = "manual"
        else:
            close_reason = None

            # Trailing stop: track best (peak) price and close on reversal when profitable
            trail_key = f"bot:trail:{pos_id}"
            tp_trail_key = _TP_TRAIL_PREFIX + str(pos_id)
            if effective_pnl_pct >= _TRAIL_ACTIVATION_PCT:
                trail_raw = await redis.get(trail_key)
                trail_best = float(trail_raw) if trail_raw else None
                if side == "BUY":
                    new_best = max(current_price, trail_best or current_price)
                else:
                    new_best = min(current_price, trail_best or current_price)
                if trail_best is None or new_best != trail_best:
                    await redis.setex(trail_key, 3600, str(new_best))
                trail_raw_pct = _TRAIL_REVERSAL_PCT / max(leverage, 1)
                if side == "BUY":
                    trail_trigger = new_best * (1 - trail_raw_pct / 100)
                    if current_price < trail_trigger and pnl_usdt >= _MIN_PROFIT_USD:
                        close_reason = "trailing_stop"
                        logger.info(
                            f"{symbol}: trailing stop triggered (peak=${new_best:.4f} "
                            f"trigger=${trail_trigger:.4f} effective={effective_pnl_pct:+.2f}%)"
                        )
                else:
                    trail_trigger = new_best * (1 + trail_raw_pct / 100)
                    if current_price > trail_trigger and pnl_usdt >= _MIN_PROFIT_USD:
                        close_reason = "trailing_stop"
                        logger.info(
                            f"{symbol}: trailing stop triggered (peak=${new_best:.4f} "
                            f"trigger=${trail_trigger:.4f} effective={effective_pnl_pct:+.2f}%)"
                        )
            elif effective_pnl_pct < 0:
                trail_raw = await redis.get(trail_key)
                if trail_raw:
                    await redis.delete(trail_key)

            # SL / TP checks
            if close_reason is None:
                opened_ts = pos.get("opened_at")
                held_s = None
                if isinstance(opened_ts, datetime):
                    if opened_ts.tzinfo is None:
                        opened_ts = opened_ts.replace(tzinfo=timezone.utc)
                    held_s = (datetime.now(timezone.utc) - opened_ts).total_seconds()
                in_grace = held_s is not None and held_s < _SL_GRACE_SECONDS
                loss_cap = _EMERGENCY_LOSS_PCT if in_grace else _MAX_LOSS_PCT
                max_loss_pct = -loss_cap
                if side == "BUY":
                    sl_hit = (current_price <= stop_loss and not in_grace) or effective_pnl_pct <= max_loss_pct
                    tp_hit = current_price >= take_profit
                else:
                    sl_hit = (current_price >= stop_loss and not in_grace) or effective_pnl_pct <= max_loss_pct
                    tp_hit = current_price <= take_profit

                if sl_hit:
                    # Cap exit so leveraged loss never exceeds the active cap
                    max_price_loss_pct = loss_cap / max(leverage, 1)
                    if side == "BUY":
                        cap_price = entry_price * (1 - max_price_loss_pct / 100)
                        safe_exit = max(current_price, stop_loss, cap_price)
                    else:
                        cap_price = entry_price * (1 + max_price_loss_pct / 100)
                        safe_exit = min(current_price, stop_loss, cap_price)
                    current_price = safe_exit
                    # Paper slippage is applied once, by the simulated fill below
                    # Recalculate pnl with the capped exit price so DB records the correct value
                    if side == "BUY":
                        raw_pnl_pct = (current_price / entry_price - 1) * 100
                    else:
                        raw_pnl_pct = (entry_price - current_price) / entry_price * 100
                    effective_pnl_pct = raw_pnl_pct * leverage
                    pnl_usdt = notional * raw_pnl_pct / 100  # notional already carries the leverage
                    close_reason = "stop_loss"
                    logger.info(f"{symbol}: SL triggered - exit capped at ${safe_exit:.4f} (effective {effective_pnl_pct:.2f}%)")
                elif tp_hit:
                    close_reason = "take_profit"
                    logger.info(f"{symbol}: TP hit at ${take_profit:.4f} — closing")
                else:
                    # TP follower retrace check
                    tp_trail_raw = await redis.get(tp_trail_key)
                    if tp_trail_raw:
                        tp_anchor = float(tp_trail_raw)
                        retrace = (side == "BUY" and current_price < tp_anchor) or (side == "SELL" and current_price > tp_anchor)
                        if retrace and pnl_usdt >= _MIN_PROFIT_USD:
                            close_reason = "take_profit"
                            logger.info(f"{symbol}: TP follower - retraced below anchor ${tp_anchor:.4f}, closing at ${current_price:.4f}")
                            await redis.delete(tp_trail_key)

        if close_reason is None:
            # Structure invalidation exit — primary mechanism replacing the 15m timeout
            try:
                pkt_raw = await redis.get(f"research:packet:{symbol}")
                if pkt_raw:
                    pkt = json.loads(pkt_raw)
                    pkt_time_raw = pkt.get("packet_time")
                    pkt_stale = True
                    if pkt_time_raw:
                        try:
                            pkt_ts = datetime.fromisoformat(str(pkt_time_raw).replace("Z", "+00:00"))
                            pkt_stale = (datetime.now(timezone.utc) - pkt_ts).total_seconds() / 60 > settings.research_stale_minutes
                        except Exception:
                            pass
                    inv = pkt.get("invalidation")
                    inv_held_s = None
                    _inv_opened = pos.get("opened_at")
                    if isinstance(_inv_opened, datetime):
                        if _inv_opened.tzinfo is None:
                            _inv_opened = _inv_opened.replace(tzinfo=timezone.utc)
                        inv_held_s = (datetime.now(timezone.utc) - _inv_opened).total_seconds()
                    inv_armed = inv_held_s is None or inv_held_s >= _INVALIDATION_MIN_HOLD_S
                    if not pkt_stale and inv is not None and inv_armed:
                        inv = float(inv)
                        if side == "BUY" and current_price < inv * (1 - _INVALIDATION_BUFFER):
                            close_reason = "structure_invalidation"
                            logger.info(f"{symbol}: BUY invalidation — price ${current_price:.4f} below hour_low ${inv:.4f}")
                        elif side == "SELL" and current_price > inv * (1 + _INVALIDATION_BUFFER):
                            close_reason = "structure_invalidation"
                            logger.info(f"{symbol}: SELL invalidation — price ${current_price:.4f} above hour_high ${inv:.4f}")
                    elif pkt_stale:
                        # Fallback: 1h structure flip when packet is stale
                        try:
                            struct_state_close, _ = await _load_structure_cached(redis, symbol)
                            if struct_state_close and struct_state_close.confirmed:
                                if side == "BUY" and struct_state_close.trend == "DOWN":
                                    close_reason = "structure_flip"
                                    logger.info(f"{symbol}: stale packet + 1h confirmed DOWN — closing BUY")
                                elif side == "SELL" and struct_state_close.trend == "UP":
                                    close_reason = "structure_flip"
                                    logger.info(f"{symbol}: stale packet + 1h confirmed UP — closing SELL")
                        except Exception:
                            pass
            except Exception as _inv_exc:
                logger.debug(f"{symbol}: invalidation check failed ({_inv_exc})")

        if close_reason is None:
            opened_at = pos.get("opened_at")
            if opened_at is not None:
                if isinstance(opened_at, datetime) and opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                minutes_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60
                scratch_timeout = max(45, params.get("negative_trade_timeout_minutes", 45))
                if minutes_open >= scratch_timeout and pnl_usdt < 0 and effective_pnl_pct <= -1.0:
                    close_reason = "negative_timeout"
                    logger.info(f"{symbol}: scratch timeout {minutes_open:.0f}min (${pnl_usdt:.4f}, {effective_pnl_pct:.2f}%)")
                elif minutes_open >= 120:  # day trading hard max: 2 hours
                    close_reason = "max_hold"
                    logger.info(f"{symbol}: max hold 2h reached after {minutes_open:.0f}min — closing at {effective_pnl_pct:+.2f}%")

        if close_reason:
            close_ok = True
            if paper:
                if side == "BUY":
                    sim = await simulate_sell(redis, symbol, quantity, entry_price, current_price, mode, leverage)
                else:
                    sim = await simulate_buy_back(redis, symbol, quantity, entry_price, current_price, mode, leverage)
                # Record the price the paper account was actually filled at, so the DB matches capital
                fill_price = (sim or {}).get("fill_price")
                if fill_price:
                    current_price = float(fill_price)
                    if side == "BUY":
                        raw_pnl_pct = (current_price / entry_price - 1) * 100
                    else:
                        raw_pnl_pct = (entry_price - current_price) / entry_price * 100
                    effective_pnl_pct = raw_pnl_pct * leverage
                    pnl_usdt = notional * raw_pnl_pct / 100
            else:
                close_result = None
                if mode == "FUTURES":
                    if side == "BUY":
                        close_result = await place_futures_market_close(symbol, quantity, use_testnet=use_testnet)
                    else:
                        close_result = await place_futures_market_buy(symbol, quantity, leverage, use_testnet=use_testnet)
                else:
                    close_result = await place_spot_market_sell(symbol, quantity, use_testnet=use_testnet)
                if close_result and not close_result.get("ok"):
                    logger.critical(
                        f"CLOSE ORDER FAILED for {symbol} (reason={close_reason}): "
                        f"{close_result.get('error')} — position remains open on exchange, skipping DB close"
                    )
                    close_ok = False

            if not close_ok:
                continue

            # Per-symbol cooldown to prevent churn (re-opening immediately after close)
            await redis.setex(f"bot:cooldown:{symbol}", _SYMBOL_COOLDOWN_SECONDS, "1")

            # Track consecutive loss streak per symbol (2h TTL — day trader memory)
            streak_key = f"bot:loss_streak:{symbol}"
            if pnl_usdt < 0:
                await redis.incr(streak_key)
                await redis.expire(streak_key, 7200)
            else:
                await redis.delete(streak_key)

            await redis.srem(recovery_key, str(pos_id))
            await redis.delete(f"bot:trail:{pos_id}")

            # Estimate and record fee for this trade
            estimated_fee = abs(notional * settings.taker_fee_rate)  # Binance taker fee ~0.04%
            await risk.record_fee(estimated_fee)

            await close_position(
                pos_id, symbol, current_price, pnl_usdt, effective_pnl_pct, close_reason,
                trade_id=trade_id,
                estimated_fee_usdt=estimated_fee,
                gross_pnl_usdt=pnl_usdt + estimated_fee,
            )
            await ev.publish_trade_closed(redis, symbol, mode, pnl_usdt, effective_pnl_pct, close_reason, paper)

            if close_reason == "daily_target_reached":
                daily_pnl = await risk.get_daily_pnl()
                await telegram.notify_daily_target(daily_pnl)
                await ev.publish_daily_target(redis, daily_pnl)

            triggered = await risk.on_trade_closed(
                effective_pnl_pct, params["daily_loss_limit_percent"],
                pnl_usdt=pnl_usdt, capital_usdt=capital,
            )

            if triggered:
                logger.critical("Kill switch activated — halting all trading")
                break


async def _is_symbol_viable(redis: aioredis.Redis, symbol: str) -> bool:
    """Return False for symbols with win rate <35% and negative net PnL over their last 20 trades in the viability window."""
    cache_key = f"bot:sym_viable:{symbol}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return cached == "1"
    try:
        from bot.db.connection import async_session
        from sqlalchemy import text as _text
        async with async_session() as session:
            row = (await session.execute(_text("""
                SELECT COUNT(*) as total,
                       COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) as wins,
                       COALESCE(SUM(pnl_usdt), 0) as net_pnl
                FROM (
                    SELECT pnl_usdt FROM trades
                    WHERE symbol = :symbol AND status = 'CLOSED'
                      AND closed_at > NOW() - make_interval(hours => :hours)
                    ORDER BY closed_at DESC LIMIT 20
                ) t
            """), {"symbol": symbol, "hours": _VIABILITY_WINDOW_HOURS})).mappings().first()
        viable = True
        if row and int(row["total"]) >= 10:
            win_rate = int(row["wins"]) / int(row["total"])
            net_pnl = float(row["net_pnl"])
            if win_rate < 0.35 and net_pnl < 0:
                viable = False
                logger.info(f"{symbol}: viability block — win_rate={win_rate:.0%} net=${net_pnl:.2f} ({row['total']} trades)")
        if viable:
            # Circuit breaker: heavy net loss over the last few trades inside the window.
            # Losses age out of the window, so a blocked symbol is retried later.
            async with async_session() as session:
                brk = (await session.execute(_text("""
                    SELECT COUNT(*) AS total, COALESCE(SUM(pnl_usdt), 0) AS net_pnl
                    FROM (
                        SELECT pnl_usdt FROM trades
                        WHERE symbol = :symbol AND status = 'CLOSED'
                          AND closed_at > NOW() - make_interval(hours => :hours)
                        ORDER BY closed_at DESC LIMIT :n
                    ) t
                """), {"symbol": symbol, "hours": _BREAKER_WINDOW_HOURS, "n": _BREAKER_TRADES})).mappings().first()
            if brk and int(brk["total"]) >= _BREAKER_MIN_TRADES and float(brk["net_pnl"]) <= -_BREAKER_LOSS_USD:
                viable = False
                logger.info(
                    f"{symbol}: circuit breaker — net ${float(brk['net_pnl']):.2f} over last "
                    f"{brk['total']} trades in {_BREAKER_WINDOW_HOURS}h"
                )
    except Exception:
        viable = True
    await redis.setex(cache_key, 1800, "1" if viable else "0")
    return viable


async def _momentum_1h_pct(symbol: str, price: float) -> float | None:
    """Price change over roughly the last hour, from the stored 15m candles."""
    try:
        from bot.db.connection import async_session
        from sqlalchemy import text as _text
        async with async_session() as session:
            row = (await session.execute(_text("""
                SELECT close FROM candles
                WHERE symbol = :symbol AND timeframe = '15m'
                  AND open_time <= NOW() - interval '1 hour'
                ORDER BY open_time DESC LIMIT 1
            """), {"symbol": symbol})).first()
        if not row or not row[0] or float(row[0]) <= 0:
            return None
        return (price / float(row[0]) - 1) * 100
    except Exception as exc:
        logger.debug(f"{symbol}: 1h momentum lookup failed ({exc})")
        return None


async def _try_open_trade(
    pair: RankedPair,
    open_symbols: list[str],
    capital: float,
    redis: aioredis.Redis,
    risk: RiskManager,
    params: dict,
    funding_rates: dict[str, dict] | None = None,
    regime_result: RegimeResult | None = None,
    sell_threshold: float | None = None,
    manual_side: str | None = None,
    reject: list[str] | None = None,
) -> bool:
    symbol = pair.symbol
    manual = manual_side is not None

    def _no(reason: str) -> bool:
        if reject is not None:
            reject.append(reason)
        return False

    # Per-symbol cooldown check — don't reopen a recently closed symbol
    cooldown_active = await redis.get(f"bot:cooldown:{symbol}")
    if cooldown_active and not manual:
        logger.debug(f"{symbol}: cooldown active — skipping")
        return _no("Cooldown after a recent close")

    # Symbol viability check — block persistent losers (win rate <35%, negative PnL over last 20 trades)
    if not manual and not await _is_symbol_viable(redis, symbol):
        return _no("Symbol blocked (viability / circuit breaker)")

    # Minimum prediction gate — must predict at least 1% move to cover fees and TP distance
    if not manual and abs(pair.predicted_change_pct) < 1.0:
        return _no(f"Predicted move {pair.predicted_change_pct:+.2f}% under 1%")

    # Per-symbol learning — throttle size on poor performers, block only the worst
    try:
        if manual:
            raise RuntimeError("manual entry bypasses per-symbol learning")
        from bot.research.loop import get_symbol_multiplier
        sym_mult = await get_symbol_multiplier(redis, symbol)
        if sym_mult <= 0.50:
            logger.info(f"{symbol}: blocked by per-symbol learning (mult={sym_mult:.2f})")
            return _no("Per-symbol learning block")
    except Exception:
        pass

    # Day trader blacklist — skip symbols that lost 3+ consecutive trades recently
    try:
        streak_raw = None if manual else await redis.get(f"bot:loss_streak:{symbol}")
        if streak_raw and int(streak_raw) >= 3:
            logger.debug(f"{symbol}: blacklisted — {streak_raw} consecutive losses")
            return _no(f"Blacklisted after {streak_raw} straight losses")
    except Exception:
        pass

    side = manual_side if manual else _determine_side(pair.predicted_change_pct)

    # Per-symbol regime detection using pair's own indicators
    if regime_result is None:
        try:
            regime_result = await _detect_regime_cached(
                redis, symbol, pair.adx, pair.atr_pct, bb_width_pct=pair.bb_width_pct
            )
        except Exception as e:
            logger.debug(f"{symbol}: regime detection failed ({e})")

    # Apply regime confidence multiplier
    effective_confidence = pair.confidence
    if regime_result is not None:
        effective_confidence *= regime_result.confidence_multiplier
    fr_rate = pair.funding_rate if pair.funding_rate else 0.0
    fr_side = pair.funding_side_to_collect if pair.funding_side_to_collect else "NONE"
    if fr_side != "NONE":
        if fr_side == side:
            effective_confidence = min(1.0, effective_confidence + 0.05)
        elif abs(fr_rate) > 0.001:
            effective_confidence = max(0.0, effective_confidence - 0.10)

    # Day trading: SELL (SHORT) uses same confidence floor as BUY
    sell_floor = sell_threshold if sell_threshold is not None else params["confidence_threshold"]
    if not manual and side == "SELL" and effective_confidence < sell_floor:
        logger.debug(
            f"{symbol}: skip SELL — confidence {effective_confidence:.2f} < {sell_floor:.2f}"
        )
        return _no(f"SELL confidence {effective_confidence:.2f} under {sell_floor:.2f}")

    # Resolve effective mode early — needed by the alignment gate
    force_mode_raw = await redis.get(_FORCE_TRADE_MODE_KEY)
    force_mode = force_mode_raw if force_mode_raw in ("SPOT", "FUTURES", "DYNAMIC") else None
    effective_mode_param = force_mode if force_mode else params["mode"]

    # Load structure early so both the gate and the entry filter can use it
    struct_state, zones = await _load_structure_cached(redis, symbol)

    # Dual-bot alignment gate owns venue and side. Spot is not a venue.
    regime_name = regime_result.regime if regime_result else "UNKNOWN"
    market_sentiment, advance_ratio = await _get_market_sentiment(redis)
    if not manual:
        try:
            from bot.manager.gate import should_enter
            decision = await should_enter(
                redis, symbol, side,
                predicted_change_pct=pair.predicted_change_pct,
                confidence=pair.confidence,
                ema21=getattr(pair, "ema21", 0.0),
                ema50=getattr(pair, "ema50", 0.0),
                adx=pair.adx,
                regime=regime_name,
                mode="FUTURES",
                structure=struct_state,
                sentiment=market_sentiment,
            )
            if not decision.ok or decision.side not in ("BUY", "SELL"):
                logger.debug(f"{symbol}: gate reject — {decision.reason}")
                return _no(f"Gate: {decision.reason}")
            side = decision.side
            # Apply researcher confidence adjustment from the gate
            effective_confidence *= decision.confidence_adj
            if decision.confidence_adj != 1.0:
                logger.debug(f"{symbol}: researcher conf adj {decision.confidence_adj:.2f} → effective {effective_confidence:.2f}")
            if decision.packet:
                try:
                    pair.research_packet = decision.packet  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception as gate_exc:
            logger.warning(f"{symbol}: alignment gate error ({gate_exc}) — standing down")
            return _no("Alignment gate error")

    # Funding rate awareness: prefer the side that collects funding
    funding_signal = None
    if funding_rates and symbol in funding_rates:
        fr_data = funding_rates[symbol]
        funding_rate = fr_data.get("funding_rate", 0.0)
        funding_signal = get_funding_signal(funding_rate)
        if funding_signal != "NONE":
            if funding_signal == side:
                logger.info(f"{symbol}: funding signal {funding_signal} aligns with {side} side")
            else:
                logger.debug(f"{symbol}: funding signal {funding_signal} opposes predicted {side}")

    mode = "FUTURES"
    logger.debug(f"{symbol}: market sentiment={market_sentiment} ({advance_ratio:.0%} advancing) side={side} venue=FUTURES")

    mode_flags = await _get_trading_mode_flags(redis)
    paper = mode_flags["paper"]

    if side not in ("BUY", "SELL"):
        return False

    # Compute heat for this trade
    positions_all = await get_open_positions()
    open_heat_usdt = 0.0
    symbol_heat_usdt = 0.0
    lev = params["futures_leverage"]
    for p in positions_all:
        p_notional = float(p["quantity"]) * float(p["entry_price"])
        p_lev = int(p.get("leverage") or 1)
        p_heat = p_notional / p_lev if p.get("mode") == "FUTURES" else p_notional
        open_heat_usdt += p_heat
        if p["symbol"] == symbol:
            symbol_heat_usdt += p_heat

    trade_heat = calculate_trade_heat(capital, open_heat_usdt, symbol_heat_usdt, params)
    # Experiments: one position, full capital as margin. Do not apply the
    # dollar cap or the researcher size throttle. After a loss streak, halve the size.
    if not manual:
        loss_mult = await risk.get_size_multiplier()
        if loss_mult < 1.0:
            trade_heat *= loss_mult
            logger.info(f"{symbol}: loss-streak size reduction x{loss_mult:.2f}")

    can_open, reason = await risk.can_open_trade(
        symbol, open_symbols, params, capital, open_heat_usdt,
        symbol_heat_usdt=symbol_heat_usdt, trade_heat_usdt=trade_heat, manual=manual,
    )
    if not can_open:
        logger.debug(f"Skip {symbol}: {reason}")
        return _no(reason)

    # Check correlation against already-open positions
    try:
        corr_raw = None if manual else await redis.get(_CORR_CACHE_KEY)
        if corr_raw:
            corr_matrix = json.loads(corr_raw)
            for open_sym in open_symbols:
                r = abs(corr_matrix.get(symbol, {}).get(open_sym, 0.0))
                if r >= 0.95:
                    logger.debug(f"Skip {symbol}: correlated with open {open_sym} (r={r:.2f})")
                    return _no(f"Correlated with open {open_sym}")
    except Exception:
        pass

    current_price = await _get_live_price(symbol, redis)
    if not current_price or current_price <= 0:
        return _no("No fresh live price for this symbol")

    if not manual and settings.momentum_filter_enabled:
        mom_1h = await _momentum_1h_pct(symbol, current_price)
        if mom_1h is not None:
            flat = settings.momentum_flat_pct
            if side == "SELL" and mom_1h < -flat:
                logger.info(f"{symbol}: SELL skipped — already fell {mom_1h:+.2f}% over 1h (chasing the move)")
                return _no(f"Momentum filter: SELL after a {mom_1h:+.2f}% 1h drop")
            if side == "BUY" and abs(mom_1h) <= flat:
                logger.info(f"{symbol}: BUY skipped — flat market ({mom_1h:+.2f}% over 1h)")
                return _no(f"Momentum filter: BUY in a flat market ({mom_1h:+.2f}% 1h)")

    if struct_state and struct_state.confirmed:
        if (side == "BUY" and struct_state.trend == "DOWN") or (side == "SELL" and struct_state.trend == "UP"):
            effective_confidence *= 0.93
            logger.debug(f"{symbol}: counter-trend {side} vs {struct_state.trend} — confidence penalised to {effective_confidence:.2f}")

    quantity, notional = calculate_position_size(
        capital, current_price, mode, trade_heat, leverage=lev,
    )
    if quantity <= 0:
        logger.debug(f"Skip {symbol}: position size too small")
        return _no("Position size too small for available capital")

    if not manual and settings.cost_gate_enabled and mode == "FUTURES":
        from bot.trading.costs import get_slippage, roundtrip_cost_pct
        slip_est = await get_slippage(redis, symbol, notional)
        if slip_est is not None:
            if slip_est.get("thin"):
                logger.info(f"{symbol}: skipped — order book too thin for ${notional:.0f}")
                return _no("Cost gate: order book too thin for this size")
            rt_cost = roundtrip_cost_pct(slip_est)
            if rt_cost > settings.max_roundtrip_cost_pct:
                logger.info(f"{symbol}: skipped — round-trip cost {rt_cost:.3f}% > {settings.max_roundtrip_cost_pct:.2f}%")
                return _no(f"Cost gate: round-trip cost {rt_cost:.2f}% above {settings.max_roundtrip_cost_pct:.2f}%")

    atr_for_tp = pair.atr_pct
    atr_abs = pair.atr_pct / 100 * current_price if pair.atr_pct > 0 else 0

    # Phase 2: Zone-based SL/TP with R:R gate
    zone_based_sl = False
    if zones and atr_abs > 0:
        stop_loss, zone_based_sl = calculate_zone_sl(
            current_price, side, zones, atr_abs,
            fallback_sl_pct=params["stop_loss_percent"],
            max_sl_pct=settings.zone_sl_max_pct,
        )
        take_profit = calculate_zone_tp(
            current_price, side, zones, atr_for_tp,
            fallback_tp_pct=params["take_profit_percent"],
        )
    else:
        stop_loss, take_profit = calculate_sl_tp_prices(
            current_price, mode,
            side=side,
            sl_pct=params["stop_loss_percent"],
            tp_pct=params["take_profit_percent"],
            leverage=lev,
            atr_pct=atr_for_tp,
        )

    # TP distance gate — must be at least 1.2% from entry to clear round-trip fees
    tp_dist_pct = abs(take_profit - current_price) / current_price * 100
    if not manual and tp_dist_pct < 1.2:
        logger.debug(f"{symbol}: skip — TP only {tp_dist_pct:.2f}% from entry (need >=1.2%)")
        return _no(f"TP only {tp_dist_pct:.2f}% away")

    # Fee-based required edge: round-trip taker fee × leverage + 0.8pp slippage margin
    round_trip_cost_pct = settings.taker_fee_rate * 2 * 100
    required_edge_pct = round_trip_cost_pct * lev + 0.8
    if not manual and tp_dist_pct < required_edge_pct:
        logger.debug(f"{symbol}: skip — TP {tp_dist_pct:.2f}% below required edge {required_edge_pct:.2f}%")
        return _no(f"TP {tp_dist_pct:.2f}% below required edge {required_edge_pct:.2f}%")

    # R:R gate — hard 2.5:1 minimum for zone-based entries only
    # Fixed SL/TP trades skip the R:R gate (they use the old proven ratios)
    rr = compute_rr_ratio(current_price, stop_loss, take_profit)
    if not manual and zone_based_sl and rr < settings.min_rr_ratio:
        logger.debug(
            f"{symbol}: skip {side} — zone R:R {rr:.2f} < {settings.min_rr_ratio} "
            f"(SL=${stop_loss:.4f} TP=${take_profit:.4f})"
        )
        return _no(f"Zone R:R {rr:.2f} under {settings.min_rr_ratio}")

    leverage = lev if mode == "FUTURES" else 1

    logger.info(
        f"Opening {'[PAPER] ' if paper else ''}{mode} {side} trade: {symbol} "
        f"@ ${current_price:.4f} | qty={quantity:.6f} | "
        f"SL=${stop_loss:.4f} TP=${take_profit:.4f} | R:R={rr:.1f}:1 | "
        f"struct={'zone' if zone_based_sl else 'fixed'} | "
        f"conf={pair.confidence:.2f} pred={pair.predicted_change_pct:+.2f}%"
    )

    if paper:
        if side == "BUY":
            result = await simulate_buy(redis, symbol, quantity, current_price, mode, leverage=lev)
        else:
            result = await simulate_sell_short(redis, symbol, quantity, current_price, mode, leverage=lev)
    else:
        if not mode_flags["live_enabled"]:
            logger.warning("Live trading disabled — enable via Settings page")
            return _no("Live trading is disabled in Settings")
        if side == "BUY":
            result = await place_futures_market_buy(symbol, quantity, lev, use_testnet=mode_flags["use_testnet"])
        else:
            result = await place_futures_market_sell(symbol, quantity, lev, use_testnet=mode_flags["use_testnet"])
        if not result.get("ok"):
            logger.warning(f"Futures {side} failed for {symbol}: {result.get('error')}")
            return _no(f"Exchange order failed: {result.get('error')}")

    if not result.get("ok"):
        error_msg = result.get("error", "")
        if "fill price unavailable" in error_msg.lower():
            logger.warning(f"Order placed but fill price unavailable for {symbol} — recording with current price")
        else:
            logger.error(f"Order failed for {symbol}: {error_msg}")
            return _no(f"Order failed: {error_msg}")

    fill_price = result.get("fill_price") or current_price
    order_id = result.get("order_id")

    entry_indicators = {
        "adx": round(pair.adx, 2),
        "atr_pct": round(pair.atr_pct, 4),
        "rsi": round(pair.rsi, 2),
        "bb_pct": round(pair.bb_pct, 4),
        "confidence": round(pair.confidence, 4),
        "predicted_change_pct": round(pair.predicted_change_pct, 4),
        "regime": regime_result.regime if regime_result else "UNKNOWN",
        "funding_rate": round(pair.funding_rate, 6) if pair.funding_rate else 0.0,
        "zone_based_sl": zone_based_sl,
        "rr_ratio": rr,
        "structure_trend": struct_state.trend if struct_state else "UNKNOWN",
        "structure_confirmed": struct_state.confirmed if struct_state else False,
        "manual": manual,
    }
    pkt = getattr(pair, "research_packet", None)
    if isinstance(pkt, dict):
        entry_indicators["research"] = {
            "pred_side": pkt.get("pred_1h_close_side"),
            "confidence": pkt.get("confidence"),
            "arm": pkt.get("arm"),
            "hour_start": pkt.get("hour_start"),
            "setup": pkt.get("setup"),
        }
    await open_position(
        symbol=symbol, side=side, mode=mode,
        entry_price=fill_price, quantity=quantity,
        stop_loss_price=stop_loss, take_profit_price=take_profit,
        leverage=leverage, paper_trade=paper, binance_order_id=order_id,
        regime=regime_result.regime if regime_result else "UNKNOWN",
        entry_indicators=entry_indicators,
    )
    await risk.on_trade_opened()
    await ev.publish_trade_opened(redis, symbol, side, mode, fill_price, quantity, paper)

    return True


_last_reject_summary = ""
_MANUAL_OPEN_QUEUE = "manual_open:queue"
_MANUAL_OPEN_MAX_AGE_S = 120


async def _manual_pair(symbol: str, ranked_store: dict) -> RankedPair:
    for p in ranked_store.get("pairs", []):
        if p.symbol == symbol:
            return p
    from sqlalchemy import text
    from bot.db.connection import async_session as _db_session
    change, conf = 0.0, 0.0
    try:
        async with _db_session() as session:
            row = (await session.execute(
                text("""
                    SELECT predicted_change_pct, confidence FROM predictions
                    WHERE symbol = :s ORDER BY prediction_date DESC LIMIT 1
                """),
                {"s": symbol},
            )).first()
        if row:
            change, conf = float(row[0]), float(row[1])
    except Exception as e:
        logger.debug(f"{symbol}: manual pair prediction lookup failed ({e})")
    return RankedPair(
        symbol=symbol, score=0.0, predicted_change_pct=change, confidence=conf,
        atr_pct=0.0, adx=0.0, mode="FUTURES",
    )


async def _process_manual_opens(
    ranked_store: dict,
    open_symbols: list[str],
    capital: float,
    redis: aioredis.Redis,
    risk: RiskManager,
    params: dict,
    funding_rates: dict[str, dict] | None,
) -> None:
    """Drain dashboard manual-open requests through the normal entry path."""
    while True:
        raw = await redis.lpop(_MANUAL_OPEN_QUEUE)
        if not raw:
            return
        try:
            req = json.loads(raw)
            req_id, symbol, side = req["id"], req["symbol"], req["side"]
        except Exception:
            logger.warning(f"Discarding malformed manual open request: {raw!r}")
            continue

        async def _result(status: str, reason: str = "") -> None:
            await redis.set(
                f"manual_open:result:{req_id}",
                json.dumps({"status": status, "reason": reason}),
                ex=300,
            )

        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(req["requested_at"])).total_seconds()
            if age > _MANUAL_OPEN_MAX_AGE_S:
                await _result("rejected", "Request expired before the bot picked it up")
                continue
            if symbol in open_symbols:
                await _result("rejected", f"{symbol} already has an open position")
                continue
            pair = await _manual_pair(symbol, ranked_store)
            if funding_rates and symbol in funding_rates:
                pair.funding_rate = funding_rates[symbol].get("funding_rate", 0.0)
                pair.funding_side_to_collect = funding_rates[symbol].get("side_to_collect", "NONE")
            reasons: list[str] = []
            opened = await _try_open_trade(
                pair, open_symbols, capital, redis, risk, params,
                funding_rates=funding_rates, manual_side=side, reject=reasons,
            )
            if opened:
                open_symbols.append(symbol)
                logger.info(f"Manual {side} {symbol} opened from dashboard")
                await _result("opened")
            else:
                await _result("rejected", reasons[0] if reasons else "Entry was rejected")
        except Exception as e:
            logger.error(f"Manual open {symbol} failed: {e}")
            await _result("rejected", f"Internal error: {e}")


async def _maybe_switch_trade(
    ranked_store: dict,
    positions: list[dict],
    capital: float,
    redis: aioredis.Redis,
    risk: RiskManager,
    params: dict,
    funding_rates: dict[str, dict] | None = None,
    regime_result: RegimeResult | None = None,
) -> None:
    """Close the worst losing position only when all seats are full and free heat is 0."""
    if len(positions) < params["max_concurrent_trades"]:
        return

    # Only switch when heat is fully consumed (need a seat)
    heat_limit = capital * params["heat_limit_pct"] / 100
    open_heat = 0.0
    for p in positions:
        p_notional = float(p["quantity"]) * float(p["entry_price"])
        p_lev = int(p.get("leverage") or 1)
        open_heat += p_notional / p_lev if p.get("mode") == "FUTURES" else p_notional
    if open_heat < heat_limit * 0.95:
        return

    open_symbols = {p["symbol"] for p in positions}
    ranked_pairs = ranked_store.get("pairs", [])

    best_unopen = next(
        (p for p in ranked_pairs
         if p.symbol not in open_symbols
         and p.confidence >= params["confidence_threshold"]
         and abs(p.predicted_change_pct) > 0),
        None,
    )
    if best_unopen is None:
        return

    if best_unopen.confidence < 0.92:
        return

    worst_pos = min(positions, key=lambda p: float(p["unrealized_pnl"]))
    if float(worst_pos["unrealized_pnl"]) >= 0:
        return

    worst_notional = float(worst_pos["entry_price"]) * float(worst_pos["quantity"])
    worst_loss_pct = abs(float(worst_pos["unrealized_pnl"])) / max(worst_notional, 1.0) * 100
    if worst_loss_pct < 1.5:
        return

    symbol = worst_pos["symbol"]
    pos_id = worst_pos["id"]
    trade_id = worst_pos.get("trade_id")
    entry_price = float(worst_pos["entry_price"])
    quantity = float(worst_pos["quantity"])
    mode = worst_pos["mode"]
    leverage = int(worst_pos.get("leverage") or 1)
    paper = bool(worst_pos["paper_trade"])

    current_price = await _get_live_price(symbol, redis)
    if current_price is None:
        return

    if paper:
        if worst_pos.get("side", "BUY") == "BUY":
            await simulate_sell(redis, symbol, quantity, entry_price, current_price, mode, leverage)
        else:
            await simulate_buy_back(redis, symbol, quantity, entry_price, current_price, mode, leverage)
    else:
        mf = await _get_trading_mode_flags(redis)
        if mode == "FUTURES":
            await place_futures_market_close(symbol, quantity, use_testnet=mf["use_testnet"])
        else:
            await place_spot_market_sell(symbol, quantity, use_testnet=mf["use_testnet"])

    side = worst_pos.get("side", "BUY")
    if side == "BUY":
        raw_pnl_pct = (current_price / entry_price - 1) * 100
    else:
        raw_pnl_pct = (entry_price - current_price) / entry_price * 100
    effective_pnl_pct = raw_pnl_pct * leverage
    notional = quantity * entry_price
    pnl_usdt = notional * raw_pnl_pct / 100  # notional already carries the leverage
    estimated_fee = abs(notional * settings.taker_fee_rate)
    await risk.record_fee(estimated_fee)

    await close_position(
        pos_id, symbol, current_price, pnl_usdt, effective_pnl_pct, "trade_switch",
        trade_id=trade_id, estimated_fee_usdt=estimated_fee, gross_pnl_usdt=pnl_usdt + estimated_fee,
    )
    kill_triggered = await risk.on_trade_closed(
        effective_pnl_pct, params["daily_loss_limit_percent"],
        pnl_usdt=pnl_usdt, capital_usdt=capital,
    )
    if kill_triggered:
        logger.warning("Kill switch triggered during trade switch — skipping replacement trade")
        return
    await _try_open_trade(
        best_unopen, list(open_symbols - {symbol}), capital, redis, risk, params,
        funding_rates=funding_rates, regime_result=regime_result,
    )



_ML_LEARN_INTERVAL = 60  # check every 60 loops (~5 min)
_FUTURES_BUY_MIN_TRADES = 20  # need at least this many trades before gating
_FUTURES_BUY_MAX_WINRATE = 0.45  # gate if win rate below this
_FUTURES_BUY_RECOVER_RATE = 0.52  # ungate if win rate recovers above this


async def _update_futures_buy_gate(redis: aioredis.Redis) -> None:
    """ML self-learning: auto-gate FUTURES BUY if it is consistently losing."""
    try:
        from bot.db.connection import get_session
        from sqlalchemy import text
        async with get_session() as session:
            row = (await session.execute(text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) as wins,
                    COALESCE(SUM(pnl_usdt), 0) as net_pnl
                FROM trades
                WHERE status = 'CLOSED' AND mode = 'FUTURES' AND side = 'BUY'
                ORDER BY closed_at DESC
                LIMIT 50
            """))).mappings().first()
        if not row or int(row["total"]) < _FUTURES_BUY_MIN_TRADES:
            return
        total = int(row["total"])
        wins = int(row["wins"])
        net_pnl = float(row["net_pnl"])
        win_rate = wins / total if total > 0 else 0.0
        currently_disabled = (await redis.get(_DISABLE_FUTURES_BUY_KEY)) == "1"

        if not currently_disabled and win_rate < _FUTURES_BUY_MAX_WINRATE and net_pnl < 0:
            await redis.set(_DISABLE_FUTURES_BUY_KEY, "1")
            logger.info(
                f"ML gate: FUTURES BUY auto-disabled — win_rate={win_rate:.1%} net_pnl=${net_pnl:.2f} "
                f"over {total} trades (threshold {_FUTURES_BUY_MAX_WINRATE:.0%})"
            )
        elif currently_disabled and win_rate >= _FUTURES_BUY_RECOVER_RATE and net_pnl > 0:
            await redis.set(_DISABLE_FUTURES_BUY_KEY, "0")
            logger.info(
                f"ML gate: FUTURES BUY auto-enabled — win_rate={win_rate:.1%} net_pnl=${net_pnl:.2f} "
                f"recovered (threshold {_FUTURES_BUY_RECOVER_RATE:.0%})"
            )
    except Exception as e:
        logger.debug(f"_update_futures_buy_gate error: {e}")

async def run_trading_engine(
    ranked_store: dict,
    redis: aioredis.Redis,
    stop_event: asyncio.Event,
) -> None:
    risk = RiskManager(redis)
    mode_flags = await _get_trading_mode_flags(redis)

    await risk.set_status("awaiting_confirmation", {
        "paper": mode_flags["paper"],
        "testnet": mode_flags["use_testnet"],
    })
    # Always clear any stale command from previous session — require fresh confirmation
    stale_cmd = await redis.get(COMMAND_KEY)
    if stale_cmd:
        logger.info(f"Clearing stale bot:command='{stale_cmd}' from previous session")
        await redis.delete(COMMAND_KEY)

    logger.info("Trading engine ready — awaiting user confirmation to start trading")

    while not stop_event.is_set():
        cmd = await redis.get(COMMAND_KEY)
        if cmd == "confirmed":
            await redis.delete(COMMAND_KEY)
            logger.info("User confirmed startup — beginning trading")
            break
        if cmd == "stop":
            # Nothing is trading yet. Returning here would end the engine for good and make
            # Start impossible, so drop the command and keep waiting for confirmation.
            await redis.delete(COMMAND_KEY)
            logger.info("Stop command received during startup wait — ignored, still awaiting confirmation")
        await asyncio.sleep(LOOP_INTERVAL)

    mode_flags = await _get_trading_mode_flags(redis)
    if not mode_flags["paper"]:
        try:
            start_cap = await get_account_balance(use_testnet=mode_flags["use_testnet"])
            await redis.set("bot:session_start_capital", str(start_cap))
            logger.info(f"Session start capital recorded: ${start_cap:.2f} USDT")
        except Exception as e:
            logger.warning(f"Could not record session start capital: {e}")
    await risk.set_status("running", {
        "paper": mode_flags["paper"],
        "testnet": mode_flags["use_testnet"],
    })
    _loop_count = 0
    _HOURLY_LOOPS = 3600 // LOOP_INTERVAL

    mode_label = "PAPER TRADING" if mode_flags["paper"] else "LIVE TRADING"
    logger.info(f"Trading engine started — {mode_label}")
    await telegram.notify_bot_started(mode_flags["paper"], mode_flags["use_testnet"])
    params = await risk.get_effective_params()
    await risk.write_locked_defaults(params["mode"])
    logger.info(
        f"Config: heat={params['heat_limit_pct']}% | "
        f"confidence={params['confidence_threshold']} | "
        f"max_sim={params['max_concurrent_trades']} | "
        f"leverage={params['futures_leverage']}"
    )

    while not stop_event.is_set():
        try:
            cmd = await redis.get(COMMAND_KEY)
            if cmd == "stop":
                await risk.set_status("stopped")
                await asyncio.sleep(LOOP_INTERVAL)
                continue

            if cmd == "restart":
                await redis.delete(COMMAND_KEY)
                mode_flags = await _get_trading_mode_flags(redis)
                logger.info("Bot restarting — kill switch and daily stats cleared")
                await risk.set_status("running", {
                    "paper": mode_flags["paper"],
                    "testnet": mode_flags["use_testnet"],
                })
                await asyncio.sleep(LOOP_INTERVAL)
                continue

            if cmd == "pause":
                await risk.set_status("paused")
                await asyncio.sleep(LOOP_INTERVAL)
                continue

            kill_active = await risk.is_kill_switch_active()
            defensive_active = await risk.is_defensive_mode()

            params = await risk.get_effective_params()

            if defensive_active:
                recovered = await risk.check_defensive_recovery(params["daily_loss_limit_percent"])
                if recovered:
                    defensive_active = False
                    kill_active = False
                    logger.info("Defensive mode cleared — resuming normal strategy")
                else:
                    await risk.set_status("defensive", {
                        "paper": mode_flags["paper"],
                        "strategy": "Conservative SPOT-only (auto-recovery active)",
                    })
                    logger.debug("Defensive mode: trading with conservative params")

            elif kill_active:
                await risk.set_status("kill_switch")
                logger.warning("Kill switch active (emergency) — closing positions only")
                await _check_and_close_positions(redis, risk, params, use_testnet=mode_flags["use_testnet"])
                await asyncio.sleep(LOOP_INTERVAL)
                continue

            mode_flags = await _get_trading_mode_flags(redis)
            if mode_flags["paper"]:
                from bot.trading.paper import get_paper_capital
                capital = await get_paper_capital(redis)
            else:
                live_bal = await get_account_balance(use_testnet=mode_flags["use_testnet"])
                if live_bal is not None:
                    capital = live_bal
                else:
                    logger.warning(f"Balance fetch failed — using last known capital ${capital:.2f}")

            await _check_and_close_positions(redis, risk, params, use_testnet=mode_flags["use_testnet"], capital=capital)

            kill_active = await risk.is_kill_switch_active()
            defensive_active = await risk.is_defensive_mode()
            if kill_active and not defensive_active:
                await asyncio.sleep(LOOP_INTERVAL)
                continue

            positions = await get_open_positions()
            open_symbols = [p["symbol"] for p in positions]

            daily_pnl = await risk.get_daily_pnl()
            daily_trade_count = await risk.get_daily_trade_count()
            open_count = len(positions)

            confidence_threshold = params["confidence_threshold"]

            # ML self-learning: update FUTURES BUY gate periodically
            if _loop_count % _ML_LEARN_INTERVAL == 0:
                await _update_futures_buy_gate(redis)

            # Fetch funding rates periodically (every 60s = ~12 loops)
            funding_rates = {}
            if _loop_count % 12 == 0:
                all_symbols = [p.symbol for p in ranked_store.get("pairs", [])]
                if all_symbols:
                    funding_rates = await _fetch_funding_rates_cached(redis, all_symbols)

            # Regime is now detected per-symbol inside _try_open_trade (not once for all)

            await risk.set_status("running", {
                "daily_pnl_pct": round(daily_pnl, 4),
                "open_trades": open_count,
                "capital_usdt": round(capital, 4),
                "paper": mode_flags["paper"],
                "daily_trades": daily_trade_count,
                "funding_rates_count": len(funding_rates),
            })

            # Check fee impact every 100 trades
            if daily_trade_count > 0 and daily_trade_count % 100 == 0:
                fee_check = await risk.check_fee_impact()
                if fee_check.get("warning_eroding"):
                    logger.warning(
                        f"Fee erosion detected! Gross: ${fee_check['gross_pnl_usdt']:.2f}, "
                        f"Fees: ${fee_check['fees_usdt']:.2f}, Net: ${fee_check['net_pnl_usdt']:.2f}"
                    )

            # Trade-switch disabled — closing a losing position to free a slot locks in losses
            # (71 switches in history were all losses, -$51 net). Re-enable only if scorecard improves.

            positions = await get_open_positions()
            open_symbols = [p["symbol"] for p in positions]

            adjusted_confidence = confidence_threshold
            sell_threshold = confidence_threshold

            # Publish heat metrics for dashboard
            positions_for_heat = await get_open_positions()
            open_heat_usdt = 0.0
            for p in positions_for_heat:
                p_notional = float(p["quantity"]) * float(p["entry_price"])
                p_lev = int(p.get("leverage") or 1)
                open_heat_usdt += p_notional / p_lev if p.get("mode") == "FUTURES" else p_notional
            await risk.publish_heat(capital, open_heat_usdt, params)

            pairs_list = ranked_store.get("pairs", [])
            # Annotate pairs with funding rate info
            for pair in pairs_list:
                if funding_rates and pair.symbol in funding_rates:
                    fr_data = funding_rates[pair.symbol]
                    pair.funding_rate = fr_data.get("funding_rate", 0.0)
                    pair.funding_side_to_collect = fr_data.get("side_to_collect", "NONE")

            # Fetch Pearson correlation matrix (1h cache) and filter correlated pairs
            all_symbols = [p.symbol for p in pairs_list]
            correlation_matrix = await _fetch_correlations_cached(redis, all_symbols) if len(all_symbols) >= 2 else {}

            ranked_filtered = filter_correlated_pairs(
                pairs_list,
                max_concurrent=max(params["max_concurrent_trades"] * 5, 20),
                correlation_matrix=correlation_matrix or None,
            )

            await _process_manual_opens(
                ranked_store, open_symbols, capital, redis, risk, params, funding_rates,
            )

            _tried = 0
            _rejects: dict[str, str] = {}
            for pair in ranked_filtered:
                if len(open_symbols) >= params["max_concurrent_trades"]:
                    break
                if pair.confidence >= adjusted_confidence:
                    _tried += 1
                    _why: list[str] = []
                    opened = await _try_open_trade(
                        pair, open_symbols, capital, redis, risk, params,
                        funding_rates=funding_rates,
                        sell_threshold=sell_threshold,
                        reject=_why,
                    )
                    if opened:
                        open_symbols.append(pair.symbol)
                    elif _why:
                        _rejects[pair.symbol] = _why[0]
            if _tried > 0 and len(open_symbols) < params["max_concurrent_trades"]:
                summary = "; ".join(f"{sym}: {why}" for sym, why in _rejects.items())
                global _last_reject_summary
                if summary != _last_reject_summary:
                    _last_reject_summary = summary
                    logger.info(f"Tried {_tried} pairs, {len(open_symbols)} open — rejected: {summary or 'no reason recorded'}")

        except Exception as e:
            logger.error(f"Trading engine error: {e}")

        _loop_count += 1
        if _loop_count % _HOURLY_LOOPS == 0:
            await telegram.notify_hourly_stats(redis)
        await asyncio.sleep(LOOP_INTERVAL)

    await risk.set_status("stopped")
    logger.info("Trading engine stopped")