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
from bot.trading.risk import RiskManager, COMMAND_KEY
from bot.trading.sizing import calculate_position_size, calculate_sl_tp_prices
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
_SYMBOL_COOLDOWN_SECONDS = 900  # 15 min cooldown after closing a symbol before reopening


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
        pnl_usdt = notional * effective_pnl_pct / 100

        try:
            await update_position_price(pos_id, current_price, pnl_usdt)
        except Exception as e:
            logger.error(f"Failed to update position {pos_id} price: {e}")

        recovery_key = "bot:recovery_mode"
        in_recovery_raw = await redis.sismember(recovery_key, str(pos_id))
        be_eligible = (side == "BUY" and take_profit > entry_price) or (side == "SELL" and take_profit < entry_price)
        if not in_recovery_raw and effective_pnl_pct <= -2.0 and be_eligible:
            new_tp = entry_price
            await update_position_tp(pos_id, new_tp)
            await redis.sadd(recovery_key, str(pos_id))
            logger.info(f"{symbol}: break-even recovery activated (PnL={effective_pnl_pct:.2f}%) — TP moved to entry ${entry_price:.4f}")
            take_profit = new_tp

        manual_close = await redis.get(f"close_position:{pos_id}")
        if manual_close:
            await redis.delete(f"close_position:{pos_id}")
            close_reason = "manual"
        else:
            close_reason = None
            if side == "BUY":
                sl_hit = current_price <= stop_loss
                tp_hit = current_price >= take_profit
            else:
                sl_hit = current_price >= stop_loss
                tp_hit = current_price <= take_profit

            if sl_hit:
                close_reason = "stop_loss"
            elif tp_hit:
                close_reason = "take_profit"

        if close_reason is None:
            opened_at = pos.get("opened_at")
            if opened_at is not None:
                if isinstance(opened_at, datetime) and opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                minutes_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60
                if minutes_open >= params["negative_trade_timeout_minutes"] and pnl_usdt < 0:
                    close_reason = "negative_timeout"
                    logger.info(f"{symbol}: closing after {minutes_open:.0f}min in loss (${pnl_usdt:.4f})")
                elif minutes_open >= 15 and pnl_usdt > 0:
                    close_reason = "profitable_timeout"
                    logger.info(f"{symbol}: closing stagnant profit after {minutes_open:.0f}min (${pnl_usdt:.4f})")

        if close_reason:
            close_ok = True
            if paper:
                if side == "BUY":
                    await simulate_sell(redis, symbol, quantity, entry_price, current_price, mode, leverage)
                else:
                    await simulate_buy_back(redis, symbol, quantity, entry_price, current_price, mode, leverage)
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

            await redis.srem(recovery_key, str(pos_id))

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


async def _try_open_trade(
    pair: RankedPair,
    open_symbols: list[str],
    capital: float,
    redis: aioredis.Redis,
    risk: RiskManager,
    params: dict,
    funding_rates: dict[str, dict] | None = None,
    regime_result: RegimeResult | None = None,
) -> bool:
    symbol = pair.symbol

    # Per-symbol cooldown check — don't reopen a recently closed symbol
    cooldown_active = await redis.get(f"bot:cooldown:{symbol}")
    if cooldown_active:
        logger.debug(f"{symbol}: cooldown active — skipping")
        return False

    side = _determine_side(pair.predicted_change_pct)

    # Per-symbol regime detection using pair's own indicators
    if regime_result is None:
        try:
            regime_result = await _detect_regime_cached(
                redis, symbol, pair.adx, pair.atr_pct, bb_width_pct=pair.bb_width_pct
            )
        except Exception as e:
            logger.debug(f"{symbol}: regime detection failed ({e})")

    # Phase 2: Mean-reversion signal override when in RANGING regime
    if regime_result is not None and regime_result.strategy == "mean_reversion":
        from bot.analysis.regime_detector import generate_mean_reversion_signal
        mr_signal = generate_mean_reversion_signal(pair.rsi, pair.bb_pct)
        if mr_signal["side"] != "NONE" and mr_signal["strength"] >= 0.5:
            side = mr_signal["side"]
            logger.info(f"{symbol}: mean-reversion override → {side} ({mr_signal['reason']})")
        else:
            logger.debug(f"{symbol}: RANGING regime but no MR signal (rsi={pair.rsi:.1f} bb_pct={pair.bb_pct:.2f}), skipping")
            return False

    # Extreme funding arb: override ML direction to collect funding when rate >= 0.05%/8h
    from bot.analysis.funding_rate import is_extreme_funding
    if pair.funding_rate and is_extreme_funding(pair.funding_rate):
        collecting_side = pair.funding_side_to_collect
        if collecting_side and collecting_side != "NONE" and collecting_side != side:
            logger.info(
                f"{symbol}: extreme funding ({pair.funding_rate:.4%}/8h) "
                f"→ overriding {side} to {collecting_side} for arb"
            )
            side = collecting_side

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

    # Require higher confidence for SELL (SHORT) — except for mean-reversion signals
    is_mr_signal = regime_result is not None and regime_result.strategy == "mean_reversion"
    if side == "SELL" and not is_mr_signal:
        sell_floor = max(params["confidence_threshold"], 0.75)
        if effective_confidence < sell_floor:
            logger.debug(
                f"{symbol}: skip SELL — confidence {effective_confidence:.2f} < {sell_floor:.2f}"
            )
            return False

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

    # Read market sentiment once — used for both mode selection and direction filter
    market_sentiment, advance_ratio = await _get_market_sentiment(redis)

    # Gate FUTURES mode if funding is unfavorable and confidence is low
    if params["mode"] != "SPOT":
        funding_rate = funding_rates.get(symbol, {}).get("funding_rate", 0.0) if funding_rates else 0.0
        from bot.analysis.funding_rate import should_avoid_futures
        if should_avoid_futures(funding_rate, effective_confidence, side=side):
            logger.info(f"{symbol}: avoiding FUTURES due to unfavorable funding ({funding_rate:.6f})")
            mode = "SPOT"
        else:
            mode = classify_mode(
                effective_confidence, pair.atr_pct, pair.adx,
                trading_mode=params["mode"],
                confidence_threshold=params["confidence_threshold"],
            )
    else:
        mode = "SPOT"

    logger.debug(f"{symbol}: market sentiment={market_sentiment} ({advance_ratio:.0%} advancing) side={side}")

    mode_flags = await _get_trading_mode_flags(redis)
    paper = mode_flags["paper"]

    if side == "SELL" and mode == "SPOT":
        logger.debug(f"Skip {symbol}: SHORT not supported in SPOT mode")
        return False

    can_open, reason = await risk.can_open_trade(symbol, open_symbols, params["max_concurrent_trades"])
    if not can_open:
        logger.debug(f"Skip {symbol}: {reason}")
        return False

    # Check correlation against already-open positions
    try:
        corr_raw = await redis.get(_CORR_CACHE_KEY)
        if corr_raw:
            corr_matrix = json.loads(corr_raw)
            for open_sym in open_symbols:
                r = abs(corr_matrix.get(symbol, {}).get(open_sym, 0.0))
                if r >= 0.75:
                    logger.debug(f"Skip {symbol}: correlated with open {open_sym} (r={r:.2f})")
                    return False
    except Exception:
        pass

    current_price = await _get_live_price(symbol, redis)
    if not current_price or current_price <= 0:
        return False

    existing_exposure = sum(
        float(p["quantity"]) * float(p["entry_price"])
        for p in await get_open_positions()
        if p["symbol"] == symbol
    )

    lev = params["futures_leverage"]
    trade_divisor = params.get("min_daily_trades") or params["max_concurrent_trades"]
    risk_pct = 100.0 / trade_divisor if trade_divisor > 0 else settings.max_risk_per_trade_percent

    # Apply regime-based position size multiplier
    regime_mult = 1.0
    if regime_result is not None:
        try:
            from bot.analysis.regime_detector import get_regime_based_position_multiplier
            regime_mult = get_regime_based_position_multiplier(regime_result)
            regime_name = getattr(regime_result, "regime", regime_result.get("regime") if isinstance(regime_result, dict) else "?")
            if regime_mult < 1.0:
                logger.debug(f"{symbol}: regime {regime_name} — position size reduced to {regime_mult}")
        except Exception as e:
            logger.warning(f"{symbol}: regime multiplier failed ({e}), using 1.0")
            regime_mult = 1.0

    # Adjust risk_pct by regime multiplier
    adjusted_risk_pct = risk_pct * regime_mult
    quantity, notional = calculate_position_size(
        capital, current_price, mode, existing_exposure, leverage=lev, max_risk_per_trade_pct=adjusted_risk_pct
    )
    if quantity <= 0:
        logger.debug(f"Skip {symbol}: position size too small")
        return False

    # In ranging markets use a tighter TP (0.6× ATR) for faster grid-style cycling
    atr_for_tp = pair.atr_pct
    if regime_result is not None and regime_result.regime == "RANGING":
        atr_for_tp = pair.atr_pct * 0.6

    stop_loss, take_profit = calculate_sl_tp_prices(
        current_price, mode,
        side=side,
        sl_pct=params["stop_loss_percent"],
        tp_pct=params["take_profit_percent"],
        leverage=lev,
        atr_pct=atr_for_tp,
    )
    leverage = lev if mode == "FUTURES" else 1

    logger.info(
        f"Opening {'[PAPER] ' if paper else ''}{mode} {side} trade: {symbol} "
        f"@ ${current_price:.4f} | qty={quantity:.6f} | "
        f"SL=${stop_loss:.4f} TP=${take_profit:.4f} | "
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
            return False
        if mode == "FUTURES":
            if side == "BUY":
                result = await place_futures_market_buy(symbol, quantity, lev, use_testnet=mode_flags["use_testnet"])
            else:
                result = await place_futures_market_sell(symbol, quantity, lev, use_testnet=mode_flags["use_testnet"])
            if not result.get("ok"):
                if side == "SELL":
                    # Cannot short on spot — no fallback for short positions
                    logger.warning(f"Futures SHORT failed for {symbol}, no SPOT fallback: {result.get('error')}")
                    return False
                logger.warning(f"Futures order failed for {symbol}, falling back to SPOT: {result.get('error')}")
                mode = "SPOT"
                leverage = 1
                quantity = quantity / lev  # margin-equivalent qty: don't spend full leveraged notional
                # Recalculate SL/TP for SPOT (no leverage division)
                stop_loss, take_profit = calculate_sl_tp_prices(
                    current_price, "SPOT",
                    side=side,
                    sl_pct=params["stop_loss_percent"],
                    tp_pct=params["take_profit_percent"],
                    leverage=1,
                    atr_pct=atr_for_tp,
                )
                result = await place_spot_market_buy(symbol, quantity, use_testnet=mode_flags["use_testnet"])
        else:
            if side == "BUY":
                result = await place_spot_market_buy(symbol, quantity, use_testnet=mode_flags["use_testnet"])
            else:
                result = await place_spot_market_sell(symbol, quantity, use_testnet=mode_flags["use_testnet"])

    if not result.get("ok"):
        error_msg = result.get("error", "")
        if "fill price unavailable" in error_msg.lower():
            logger.warning(f"Order placed but fill price unavailable for {symbol} — recording with current price")
        else:
            logger.error(f"Order failed for {symbol}: {error_msg}")
            return False

    fill_price = result.get("fill_price") or current_price
    order_id = result.get("order_id")

    await open_position(
        symbol=symbol, side=side, mode=mode,
        entry_price=fill_price, quantity=quantity,
        stop_loss_price=stop_loss, take_profit_price=take_profit,
        leverage=leverage, paper_trade=paper, binance_order_id=order_id,
    )
    await risk.on_trade_opened()
    await ev.publish_trade_opened(redis, symbol, side, mode, fill_price, quantity, paper)
    return True


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
    """Close the worst losing position if a significantly better pair is available."""
    if len(positions) < params["max_concurrent_trades"]:
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

    # Require very high confidence on the new pair to justify disrupting an existing position
    if best_unopen.confidence < 0.92:
        return

    worst_pos = min(positions, key=lambda p: float(p["unrealized_pnl"]))
    if float(worst_pos["unrealized_pnl"]) >= 0:
        return

    # Only switch if the loss is substantial (>= 1.5% of notional) — avoids churning on small fluctuations
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
    pnl_usdt = notional * effective_pnl_pct / 100
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
    logger.info("Trading engine ready — awaiting user confirmation to start trading")

    while not stop_event.is_set():
        cmd = await redis.get(COMMAND_KEY)
        if cmd == "confirmed":
            await redis.delete(COMMAND_KEY)
            logger.info("User confirmed startup — beginning trading")
            break
        if cmd == "stop":
            await risk.set_status("stopped")
            logger.info("Stop command received during startup wait")
            return
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
    logger.info(
        f"Config: target={settings.daily_target_percent}% | "
        f"risk_per_trade={settings.max_risk_per_trade_percent}% | "
        f"min_daily_trades={settings.min_daily_trades} | "
        f"min_concurrent={settings.min_concurrent_trades}"
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

            await _maybe_switch_trade(ranked_store, positions, capital, redis, risk, params, funding_rates=funding_rates, regime_result=None)

            positions = await get_open_positions()
            open_symbols = [p["symbol"] for p in positions]

            min_concurrent = params.get("min_concurrent_trades", 0)
            min_daily = params.get("min_daily_trades", 0)

            adjusted_confidence = confidence_threshold
            if min_concurrent > 0 and open_count < min_concurrent:
                adjusted_confidence = max(0.45, confidence_threshold - 0.10)
                logger.debug(f"Below min concurrent ({open_count}/{min_concurrent}) — lowering confidence to {adjusted_confidence:.2f}")
            elif min_daily > 0 and daily_trade_count < min_daily:
                adjusted_confidence = max(0.45, confidence_threshold - 0.08)
                logger.debug(f"Below min daily trades ({daily_trade_count}/{min_daily}) — lowering confidence to {adjusted_confidence:.2f}")

            goal_enabled = await risk.get_goal_enabled()
            if goal_enabled:
                goal_data = await risk.get_goal()
                if goal_data and goal_data.get("amount_usdt", 0) > 0:
                    goal_progress = await risk.get_daily_pnl_usdt()
                    goal_target = float(goal_data["amount_usdt"])
                    if goal_progress < goal_target * 0.5:
                        adjusted_confidence = max(0.45, adjusted_confidence - 0.05)
                        logger.debug(f"Goal pacing: behind schedule ({goal_progress:.2f}/{goal_target:.2f}) — lowering confidence to {adjusted_confidence:.2f}")

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
                max_concurrent=params["max_concurrent_trades"],
                correlation_matrix=correlation_matrix or None,
            )

            for pair in ranked_filtered:
                if len(open_symbols) >= params["max_concurrent_trades"]:
                    break
                if pair.confidence >= adjusted_confidence:
                    opened = await _try_open_trade(
                        pair, open_symbols, capital, redis, risk, params,
                        funding_rates=funding_rates,
                    )
                    if opened:
                        open_symbols.append(pair.symbol)

        except Exception as e:
            logger.error(f"Trading engine error: {e}")

        _loop_count += 1
        if _loop_count % _HOURLY_LOOPS == 0:
            await telegram.notify_hourly_stats(redis)
        await asyncio.sleep(LOOP_INTERVAL)

    await risk.set_status("stopped")
    logger.info("Trading engine stopped")