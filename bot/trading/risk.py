"""Risk manager with daily kill-switch.

Tracks:
  - Daily P&L % (resets at midnight UTC)
  - Open position count
  - Per-asset exposure
  - Kill-switch state (halts all trading when daily loss limit is hit)

Uses Redis for state persistence so the API can read live status
and the risk state survives within-day bot restarts.
"""

import json
from datetime import datetime, timezone
from loguru import logger
import redis.asyncio as aioredis

from bot.config import settings, GOAL_PERIOD_HOURS, MAX_GOAL_FACTOR
from bot.notifications import telegram, events as ev

STATUS_KEY = "bot:status"
DAILY_PNL_KEY = "bot:daily_pnl"
DAILY_DATE_KEY = "bot:daily_date"
KILL_KEY = "bot:kill_switch"
OPEN_COUNT_KEY = "bot:open_count"
DAILY_TRADE_COUNT_KEY = "bot:daily_trade_count"
COMMAND_KEY = "bot:command"
MIN_DAILY_TRADES_KEY = "bot:min_daily_trades"
MIN_CONCURRENT_KEY = "bot:min_concurrent_trades"
MAX_CONCURRENT_KEY = "bot:max_concurrent_trades"
CONFIDENCE_KEY = "bot:confidence_threshold"
SL_PCT_KEY = "bot:stop_loss_percent"
TP_PCT_KEY = "bot:take_profit_percent"
DAILY_LOSS_LIMIT_KEY = "bot:daily_loss_limit_percent"
LEVERAGE_KEY = "bot:futures_leverage"
NEG_TIMEOUT_KEY = "bot:negative_trade_timeout_minutes"
MODE_KEY = "bot:trading_mode"
DAILY_PNL_USDT_KEY = "bot:daily_pnl_usdt"
GOAL_KEY = "bot:goal"
GOAL_ENABLED_KEY = "bot:goal_enabled"
DEFENSIVE_KEY = "bot:defensive_mode"
FEES_KEY = "bot:daily_fees"
GROSS_PNL_KEY = "bot:daily_gross_pnl"
TOTAL_FEES_KEY = "bot:cumulative_fees"
MAX_DAILY_TRADES_KEY = "bot:max_daily_trades"
TRADE_RETURNS_KEY = "bot:trade_returns"

DEFENSIVE_PARAMS_OVERRIDE = {
    "mode": "SPOT",
    "confidence_threshold": 0.80,
    "stop_loss_percent": 1.0,
    "take_profit_percent": 1.5,
    "max_concurrent_trades": 3,
    "futures_leverage": 1,
    "negative_trade_timeout_minutes": 8,
    "min_daily_trades": 0,
    "min_concurrent_trades": 0,
}
DEFENSIVE_RECOVERY_FACTOR = 0.5


class RiskManager:
    def __init__(self, redis: aioredis.Redis):
        self._redis = redis

    async def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def _reset_if_new_day(self) -> None:
        today = await self._today()
        stored_date = await self._redis.get(DAILY_DATE_KEY)
        if stored_date != today:
            await self._redis.set(DAILY_DATE_KEY, today)
            await self._redis.set(DAILY_PNL_KEY, "0.0")
            await self._redis.set(DAILY_PNL_USDT_KEY, "0.0")
            await self._redis.set(KILL_KEY, "0")
            await self._redis.set(DEFENSIVE_KEY, "0")
            await self._redis.set(DAILY_TRADE_COUNT_KEY, "0")
            logger.info(f"New trading day: {today} — daily stats reset")

    async def get_daily_pnl(self) -> float:
        await self._reset_if_new_day()
        val = await self._redis.get(DAILY_PNL_KEY)
        return float(val or 0.0)

    async def get_daily_pnl_usdt(self) -> float:
        val = await self._redis.get(DAILY_PNL_USDT_KEY)
        return float(val or 0.0)

    async def get_goal(self) -> dict | None:
        raw = await self._redis.get(GOAL_KEY)
        if not raw:
            return None
        try:
            import json as _json
            return _json.loads(raw)
        except Exception:
            return None

    async def set_goal(self, amount_usdt: float) -> None:
        import json as _json
        await self._redis.set(GOAL_KEY, _json.dumps({
            "amount_usdt": amount_usdt,
            "period_hours": GOAL_PERIOD_HOURS,
        }))

    async def get_goal_enabled(self) -> bool:
        val = await self._redis.get(GOAL_ENABLED_KEY)
        return val != "0"

    async def set_goal_enabled(self, enabled: bool) -> None:
        await self._redis.set(GOAL_ENABLED_KEY, "1" if enabled else "0")

    async def is_kill_switch_active(self) -> bool:
        await self._reset_if_new_day()
        val = await self._redis.get(KILL_KEY)
        return val == "1"

    async def reset_kill_switch(self) -> None:
        await self._redis.set(KILL_KEY, "0")
        await self._redis.set(DAILY_PNL_KEY, "0.0")
        await self._redis.set(DAILY_PNL_USDT_KEY, "0.0")
        logger.info("Kill switch manually reset — daily P&L counters cleared")

    async def is_defensive_mode(self) -> bool:
        val = await self._redis.get(DEFENSIVE_KEY)
        return val == "1"

    async def activate_defensive_mode(self) -> None:
        already_defensive = (await self._redis.get(DEFENSIVE_KEY)) == "1"
        await self._redis.set(DEFENSIVE_KEY, "1")
        if already_defensive:
            logger.debug("Defensive mode already active — skipping param backup to avoid overwriting originals")
            return
        for key, redis_key in [
            ("mode", MODE_KEY),
            ("confidence_threshold", CONFIDENCE_KEY),
            ("stop_loss_percent", SL_PCT_KEY),
            ("take_profit_percent", TP_PCT_KEY),
            ("max_concurrent_trades", MAX_CONCURRENT_KEY),
            ("futures_leverage", LEVERAGE_KEY),
            ("negative_trade_timeout_minutes", NEG_TIMEOUT_KEY),
            ("min_daily_trades", MIN_DAILY_TRADES_KEY),
            ("min_concurrent_trades", MIN_CONCURRENT_KEY),
        ]:
            val = DEFENSIVE_PARAMS_OVERRIDE.get(key)
            if val is not None:
                await self._redis.set(f"bot:pre_defensive:{key}", await self._redis.get(redis_key) or "")
                await self._redis.set(redis_key, str(val))
        logger.warning("Defensive mode activated — switching to conservative SPOT-only strategy")

    async def exit_defensive_mode(self) -> None:
        for key, redis_key in [
            ("mode", MODE_KEY),
            ("confidence_threshold", CONFIDENCE_KEY),
            ("stop_loss_percent", SL_PCT_KEY),
            ("take_profit_percent", TP_PCT_KEY),
            ("max_concurrent_trades", MAX_CONCURRENT_KEY),
            ("futures_leverage", LEVERAGE_KEY),
            ("negative_trade_timeout_minutes", NEG_TIMEOUT_KEY),
            ("min_daily_trades", MIN_DAILY_TRADES_KEY),
            ("min_concurrent_trades", MIN_CONCURRENT_KEY),
        ]:
            saved = await self._redis.get(f"bot:pre_defensive:{key}")
            if saved:
                await self._redis.set(redis_key, saved)
                await self._redis.delete(f"bot:pre_defensive:{key}")
        await self._redis.set(DEFENSIVE_KEY, "0")
        await self._redis.set(KILL_KEY, "0")
        logger.info("Defensive mode exited — portfolio recovered, resuming normal strategy")

    async def check_defensive_recovery(self, daily_loss_limit: float) -> bool:
        """Return True if P&L has recovered enough to exit defensive mode."""
        daily_pnl = float(await self._redis.get(DAILY_PNL_KEY) or 0.0)
        recovery_threshold = -(daily_loss_limit * DEFENSIVE_RECOVERY_FACTOR)
        if daily_pnl >= recovery_threshold:
            await self.exit_defensive_mode()
            logger.info(f"Recovery detected: daily P&L {daily_pnl:.2f}% >= {recovery_threshold:.2f}% threshold")
            return True
        return False

    async def get_open_count(self) -> int:
        val = await self._redis.get(OPEN_COUNT_KEY)
        return int(val or 0)

    async def _redis_float(self, key: str, default: float) -> float:
        val = await self._redis.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            logger.warning(f"Invalid float in Redis[{key}]={val!r}, using default={default}")
            return default

    async def _redis_int(self, key: str, default: int) -> int:
        val = await self._redis.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            logger.warning(f"Invalid int in Redis[{key}]={val!r}, using default={default}")
            return default

    async def get_effective_params(self) -> dict:
        mode_raw = await self._redis.get(MODE_KEY)
        params = {
            "mode": mode_raw if mode_raw in ("SPOT", "DYNAMIC") else settings.trading_mode,
            "confidence_threshold": await self._redis_float(CONFIDENCE_KEY, settings.confidence_threshold),
            "stop_loss_percent": await self._redis_float(SL_PCT_KEY, settings.stop_loss_percent),
            "take_profit_percent": await self._redis_float(TP_PCT_KEY, settings.take_profit_percent),
            "daily_loss_limit_percent": await self._redis_float(DAILY_LOSS_LIMIT_KEY, settings.daily_loss_limit_percent),
            "futures_leverage": await self._redis_int(LEVERAGE_KEY, settings.futures_leverage),
            "negative_trade_timeout_minutes": await self._redis_int(NEG_TIMEOUT_KEY, settings.negative_trade_timeout_minutes),
            "max_concurrent_trades": await self._redis_int(MAX_CONCURRENT_KEY, settings.max_concurrent_trades),
            "min_concurrent_trades": await self._redis_int(MIN_CONCURRENT_KEY, 0),
            "min_daily_trades": await self._redis_int(MIN_DAILY_TRADES_KEY, 0),
        }

        goal = await self.get_goal()
        if goal:
            params["goal_usdt"] = goal.get("amount_usdt", 0.0)
        else:
            params["goal_usdt"] = 0.0
        params["goal_period_hours"] = GOAL_PERIOD_HOURS
        params["max_daily_trades"] = await self._redis_int(MAX_DAILY_TRADES_KEY, settings.max_daily_trades)

        if await self.is_defensive_mode():
            params.update(DEFENSIVE_PARAMS_OVERRIDE)

        return params

    async def get_max_goal_usdt(self, capital_usdt: float) -> float:
        return round(capital_usdt * MAX_GOAL_FACTOR, 2)

    async def can_open_trade(
        self, symbol: str, open_symbols: list[str], max_trades: int | None = None
    ) -> tuple[bool, str]:
        await self._reset_if_new_day()

        if await self.is_kill_switch_active():
            return False, "Kill switch active — emergency stop"

        cmd = await self._redis.get(COMMAND_KEY)
        if cmd in ("stop", "pause"):
            return False, f"Bot is {cmd}ped"

        limit = max_trades if max_trades is not None else settings.max_concurrent_trades
        open_count = len(open_symbols)
        if open_count >= limit:
            return False, f"Max concurrent trades reached ({limit})"

        if symbol in open_symbols:
            return False, f"Already holding position in {symbol}"

        daily_count = int(await self._redis.get(DAILY_TRADE_COUNT_KEY) or 0)
        max_daily = await self._redis_int(MAX_DAILY_TRADES_KEY, settings.max_daily_trades)
        if max_daily > 0 and daily_count >= max_daily:
            return False, f"Daily trade cap reached ({daily_count}/{max_daily})"

        return True, "ok"

    async def get_daily_trade_count(self) -> int:
        val = await self._redis.get(DAILY_TRADE_COUNT_KEY)
        return int(val or 0)

    async def on_trade_opened(self) -> None:
        await self._redis.incr(DAILY_TRADE_COUNT_KEY)
        # Note: open count is tracked by DB position count, not Redis counter

    async def on_trade_closed(
        self,
        pnl_pct: float,
        daily_loss_limit: float | None = None,
        pnl_usdt: float = 0.0,
        capital_usdt: float = 0.0,
    ) -> bool:
        """Record closed trade P&L. Returns True if kill switch was triggered."""
        await self._reset_if_new_day()

        val = float(await self._redis.get(DAILY_PNL_KEY) or 0.0)
        new_val = val + pnl_pct
        await self._redis.set(DAILY_PNL_KEY, str(new_val))

        usdt_val = float(await self._redis.get(DAILY_PNL_USDT_KEY) or 0.0)
        new_usdt_val = usdt_val + pnl_usdt
        await self._redis.set(DAILY_PNL_USDT_KEY, str(new_usdt_val))

        await self._redis.rpush(TRADE_RETURNS_KEY, str(pnl_pct))
        await self._redis.ltrim(TRADE_RETURNS_KEY, -500, -1)

        limit_pct = daily_loss_limit if daily_loss_limit is not None else settings.daily_loss_limit_percent
        if capital_usdt > 0:
            limit_usdt = capital_usdt * limit_pct / 100
            triggered = new_usdt_val <= -limit_usdt
        else:
            triggered = new_val <= -limit_pct

        if triggered:
            await self._redis.set(KILL_KEY, "1")
            await self.activate_defensive_mode()
            await self._redis.set(STATUS_KEY, json.dumps({
                "state": "defensive",
                "reason": f"Daily loss limit hit: ${new_usdt_val:.2f} ({new_val:.2f}%) — switched to defensive strategy",
                "ts": datetime.now(timezone.utc).isoformat(),
            }))
            logger.critical(
                f"DEFENSIVE MODE TRIGGERED — daily loss ${new_usdt_val:.2f} ({new_val:.2f}%) "
                f"exceeded limit of -{limit_pct}% — switching to conservative strategy"
            )
            await telegram.notify_kill_switch(new_val)
            await ev.publish_kill_switch(self._redis, new_val)
            return True

        return False

    async def record_fee(self, fee_usdt: float) -> None:
        """Record estimated trading fee for the current day."""
        await self._reset_if_new_day()
        val = float(await self._redis.get(FEES_KEY) or 0.0)
        await self._redis.set(FEES_KEY, str(val + fee_usdt))

        gross = float(await self._redis.get(GROSS_PNL_KEY) or 0.0)
        await self._redis.set(GROSS_PNL_KEY, str(gross + fee_usdt))  # Track gross as PnL+fees

    async def record_gross_pnl(self, pnl_pct: float, pnl_usdt: float) -> None:
        """Record gross P&L before fees for net/gross comparison."""
        await self._reset_if_new_day()
        gross = float(await self._redis.get(GROSS_PNL_KEY) or 0.0)
        await self._redis.set(GROSS_PNL_KEY, str(gross + pnl_usdt))

    async def get_daily_fees(self) -> float:
        """Return total estimated fees for the current day."""
        await self._reset_if_new_day()
        val = await self._redis.get(FEES_KEY)
        return float(val or 0.0)

    async def get_daily_gross_pnl_usdt(self) -> float:
        """Return gross P&L (before fees) for the current day."""
        await self._reset_if_new_day()
        val = await self._redis.get(GROSS_PNL_KEY)
        return float(val or 0.0)

    async def get_net_pnl_usdt(self) -> float:
        """Return net P&L (gross - fees) for the current day."""
        gross = await self.get_daily_gross_pnl_usdt()
        fees = await self.get_daily_fees()
        return gross - fees

    async def get_net_pnl_percent(self) -> float:
        """Return net P&L as percentage (gross - fees) for the current day."""
        net = await self.get_net_pnl_usdt()
        return net

    async def get_cumulative_fees(self) -> float:
        """Return cumulative fees across all days."""
        val = await self._redis.get(TOTAL_FEES_KEY)
        return float(val or 0.0)

    async def check_fee_impact(self, fee_threshold_usdt: float = 5.0) -> dict:
        """Check if fees are eroding profitability.

        Returns:
            dict with gross_pnl, fees, net_pnl, and a warning flag
        """
        gross = await self.get_daily_gross_pnl_usdt()
        fees = await self.get_daily_fees()
        net = gross - fees

        # Warning if gross is positive but net is negative
        warning = (gross > 0 and net < 0)
        # Warning if fees exceed 10% of gross P&L
        fee_ratio = (fees / abs(gross) * 100) if gross != 0 else 0.0
        high_fee_ratio = fee_ratio > 10.0

        return {
            "gross_pnl_usdt": round(gross, 4),
            "fees_usdt": round(fees, 4),
            "net_pnl_usdt": round(net, 4),
            "fee_ratio_pct": round(fee_ratio, 2),
            "warning_eroding": warning,
            "warning_high_fees": high_fee_ratio,
        }

    async def get_sharpe_ratio(self) -> float:
        """Calculate Sharpe ratio from last 500 trade returns (annualised).

        Assumes ~100 trades/day * 365 = 36 500 trades/year for annualisation.
        Returns 0.0 if fewer than 10 trades are available.
        """
        import numpy as np
        raw = await self._redis.lrange(TRADE_RETURNS_KEY, 0, -1)
        if len(raw) < 10:
            return 0.0
        returns = np.array([float(r) for r in raw])
        std = float(returns.std())
        if std == 0:
            return 0.0
        return float(returns.mean() / std * np.sqrt(36500))

    async def set_status(self, state: str, extra: dict | None = None) -> None:
        payload = {"state": state, "ts": datetime.now(timezone.utc).isoformat()}
        if extra:
            payload.update(extra)
        await self._redis.set(STATUS_KEY, json.dumps(payload))

    async def get_status(self) -> dict:
        raw = await self._redis.get(STATUS_KEY)
        if raw:
            return json.loads(raw)
        return {"state": "unknown"}
