"""Backtesting engine.

Evaluates strategy performance on the held-out 20% test split.
Simulates both SPOT-only and DYNAMIC mode (SPOT + FUTURES) strategies
and stores results in the backtest_results table.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone
from loguru import logger
from sqlalchemy import text

from bot.db.connection import async_session


def _simulate_trades(
    df: pd.DataFrame,
    predictions: pd.Series,
    confidences: pd.Series,
    stop_loss_pct: float,
    take_profit_pct: float,
    min_confidence: float = 0.5,
    use_futures: bool = False,
    leverage: int = 2,
    fee_rate: float = 0.001,
    slippage: float = 0.0005,
) -> dict:
    """Simulate trading on test data and return performance metrics.

    Binance costs modelled:
      fee_rate=0.001  → 0.1% taker fee per side (0.2% round-trip)
      slippage=0.0005 → 0.05% per side (0.1% round-trip)
    Total round-trip cost: ~0.3% of notional.
    """
    trades = []
    capital = 100.0
    peak = 100.0
    round_trip_cost_pct = (fee_rate + slippage) * 2 * 100

    for i in range(len(df) - 1):
        pred = predictions.iloc[i]
        conf = confidences.iloc[i]

        if pred <= 0 or conf < min_confidence:
            continue

        entry_price = df["close"].iloc[i]
        effective_leverage = leverage if use_futures else 1.0

        target_price = entry_price * (1 + take_profit_pct / 100)
        stop_price = entry_price * (1 - stop_loss_pct / 100)

        exit_price = None
        for j in range(i + 1, min(i + 25, len(df))):
            high = df["high"].iloc[j]
            low = df["low"].iloc[j]
            if high >= target_price:
                exit_price = target_price
                break
            if low <= stop_price:
                exit_price = stop_price
                break
        else:
            exit_price = df["close"].iloc[min(i + 24, len(df) - 1)]

        price_return_pct = (exit_price / entry_price - 1) * 100
        pnl_pct = price_return_pct * effective_leverage - round_trip_cost_pct
        capital *= (1 + pnl_pct / 100)
        peak = max(peak, capital)
        trades.append(pnl_pct)

    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
        }

    wins = sum(1 for t in trades if t > 0)
    returns = np.array(trades)
    sharpe = float(returns.mean() / (returns.std() + 1e-9) * np.sqrt(252))

    running_capital = 100.0
    peak_cap = 100.0
    max_dd = 0.0
    for t in trades:
        running_capital *= (1 + t / 100)
        peak_cap = max(peak_cap, running_capital)
        dd = (peak_cap - running_capital) / peak_cap * 100
        max_dd = max(max_dd, dd)

    return {
        "total_trades": len(trades),
        "win_rate": wins / len(trades),
        "total_return_pct": capital - 100.0,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
    }


async def run_backtest(
    symbol: str,
    df_test: pd.DataFrame,
    pred_reg: pd.Series,
    pred_cls_proba: pd.Series,
    stop_loss_pct: float,
    take_profit_pct: float,
    leverage: int = 2,
) -> dict:
    """Run spot-only and dynamic mode backtests and save to DB."""
    spot_metrics = _simulate_trades(
        df_test, pred_reg, pred_cls_proba,
        stop_loss_pct, take_profit_pct,
        min_confidence=0.5, use_futures=False,
    )
    dynamic_metrics = _simulate_trades(
        df_test, pred_reg, pred_cls_proba,
        stop_loss_pct, take_profit_pct,
        min_confidence=0.5, use_futures=True, leverage=leverage,
    )

    logger.info(
        f"{symbol} backtest — "
        f"SPOT: {spot_metrics['total_return_pct']:.1f}% return, "
        f"{spot_metrics['win_rate']:.0%} win rate | "
        f"DYNAMIC: {dynamic_metrics['total_return_pct']:.1f}% return, "
        f"{dynamic_metrics['win_rate']:.0%} win rate"
    )

    start = df_test["open_time"].iloc[0]
    end = df_test["open_time"].iloc[-1]

    async with async_session() as session:
        for strategy, metrics in [("spot_only", spot_metrics), ("dynamic", dynamic_metrics)]:
            await session.execute(
                text("""
                    INSERT INTO backtest_results
                        (strategy, symbol, timeframe, start_date, end_date,
                         total_trades, win_rate, sharpe_ratio, max_drawdown,
                         total_return_pct, params)
                    VALUES
                        (:strategy, :symbol, '1h', :start_date, :end_date,
                         :total_trades, :win_rate, :sharpe_ratio, :max_drawdown,
                         :total_return_pct, CAST(:params AS jsonb))
                    ON CONFLICT DO NOTHING
                """),
                {
                    "strategy": strategy,
                    "symbol": symbol,
                    "start_date": start,
                    "end_date": end,
                    "total_trades": metrics["total_trades"],
                    "win_rate": metrics["win_rate"],
                    "sharpe_ratio": metrics["sharpe_ratio"],
                    "max_drawdown": metrics["max_drawdown"],
                    "total_return_pct": metrics["total_return_pct"],
                    "params": f'{{"stop_loss": {stop_loss_pct}, "take_profit": {take_profit_pct}}}',
                },
            )
        await session.commit()

    return {"spot": spot_metrics, "dynamic": dynamic_metrics}
