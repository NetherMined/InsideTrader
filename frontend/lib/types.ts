export interface Health {
  status: string;
  db: string;
  redis: string;
  mode: string;
  paper_trading: boolean;
  testnet: boolean;
  timestamp: string;
}

export interface Market {
  symbol: string;
  base: string;
  quote: string;
  market_type: string;
  volume_24h_usdt: number;
  active: boolean;
  enabled_for_trading: boolean;
}

export interface Prediction {
  symbol: string;
  prediction_date: string;
  current_price: number;
  target_price: number;
  predicted_change_pct: number;
  confidence: number;
  mode_recommendation: string;
  features?: {
    atr_pct?: number;
    adx?: number;
    rsi?: number;
    mode_reason?: {
      confidence_ok: boolean;
      volatility_ok: boolean;
      trend_ok: boolean;
    };
  };
}

export interface Position {
  id: number;
  symbol: string;
  side: string;
  mode: string;
  entry_price: number;
  current_price: number | null;
  quantity: number;
  leverage: number;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  unrealized_pnl: number;
  paper_trade: boolean;
  opened_at: string;
}

export interface Trade {
  id: number;
  symbol: string;
  side: string;
  mode: string;
  entry_price: number;
  exit_price?: number | null;
  quantity: number;
  leverage: number;
  pnl_usdt?: number | null;
  pnl_percent?: number | null;
  status: string;
  paper_trade: boolean;
  opened_at: string;
  closed_at?: string | null;
}

export interface BotStatus {
  state: string;
  daily_pnl_pct: number;
  daily_pnl_usdt: number;
  open_trades: number;
  capital_usdt: number;
  paper: boolean;
  kill_switch: boolean;
  goal_amount_usdt: number;
  goal_period_hours: number;
  goal_progress_usdt: number;
  goal_max_usdt: number;
  total_profit_usdt: number;
  total_loss_usdt: number;
  win_count: number;
  loss_count: number;
  defensive_mode?: boolean;
  started_with_usdt?: number;
  futures_usdt?: number;
}

export interface Goal {
  amount_usdt: number;
  period_hours: number;
  max_allowed_usdt: number;
  goal_enabled?: boolean;
}

export interface Candle {
  symbol: string;
  timeframe: string;
  open_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Backtest {
  strategy: string;
  symbol: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  total_trades: number;
  win_rate?: number | null;
  sharpe_ratio?: number | null;
  max_drawdown?: number | null;
  total_return_pct?: number | null;
}

export interface TradeLimits {
  max_concurrent_trades: number;
  confidence_threshold: number;
  stop_loss_percent: number;
  take_profit_percent: number;
  daily_loss_limit_percent: number;
  futures_leverage: number;
  negative_trade_timeout_minutes: number;
  trading_mode: string;
  min_daily_trades: number;
  min_concurrent_trades: number;
  max_daily_trades: number;
}

export interface LivePrice {
  symbol: string;
  price: number;
  bid: number;
  ask: number;
  change_pct: number;
  volume_24h: number;
  ts: string;
}

export interface PnlPeriodStats {
  period: string;
  total_profit_usdt: number;
  total_loss_usdt: number;
  win_count: number;
  loss_count: number;
  net_usdt: number;
}

export interface AssetBalance {
  asset: string;
  free: number;
  used: number;
  total: number;
  usdt_value: number;
}

export interface AccountBalances {
  total_usdt: number;
  free_usdt: number;
  assets: AssetBalance[];
  is_testnet: boolean;
}

export interface LiveMode {
  paper_trading_mode: boolean;
  live_trading_enabled: boolean;
  use_testnet: boolean;
}

export interface StartupStatus {
  state: string;
  awaiting_confirmation: boolean;
  data_feed_active: boolean;
  price_feeds_count: number;
  paper_mode: boolean;
  live_enabled: boolean;
  use_testnet: boolean;
  capital_usdt: number;
  settings: {
    trading_mode: string;
    max_concurrent_trades: number;
    confidence_threshold: number;
    stop_loss_percent: number;
    take_profit_percent: number;
    daily_loss_limit_percent: number;
    futures_leverage: number;
    min_daily_trades: number;
  };
}

export interface MarketSentiment {
  sentiment: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  strength: string;
  advance_ratio: number;
  advancing: number;
  declining: number;
  neutral_count: number;
  total_symbols: number;
  avg_change_pct: number;
  top_gainers: string[];
  top_losers: string[];
  strategy: string;
  estimated_daily_profit_usdt: number;
  estimated_daily_profit_pct: number;
}
