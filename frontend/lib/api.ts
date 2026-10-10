import type {
  Health, Market, Prediction, Position, Trade,
  BotStatus, Backtest, LivePrice, Candle,
  TradeLimits, PnlPeriodStats, ModeBreakdown, RecoveryStats,
  AccountBalances, LiveMode, MarketSentiment, StartupStatus,
  SymbolOverview, ManualOpenResult,
} from './types';

const BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const API_KEY = process.env.NEXT_PUBLIC_API_KEY || '';
const mutationHeaders = (): HeadersInit => ({ 'X-Api-Key': API_KEY });

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers: mutationHeaders(), cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...mutationHeaders() },
    body: JSON.stringify(body),
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE', headers: mutationHeaders(), cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

export const api = {
  health: () => get<Health>('/health'),
  markets: (limit = 200) => get<Market[]>(`/api/v1/markets?limit=${limit}`),
  topMarkets: (limit = 50) => get<Market[]>(`/api/v1/markets/top?limit=${limit}`),
  predictions: () => get<Prediction[]>('/api/v1/predictions?limit=100'),
  prediction: (symbol: string) => get<Prediction>(`/api/v1/predictions/${encodeURIComponent(symbol)}`),
  prices: () => get<LivePrice[]>('/api/v1/prices'),
  positions: () => get<Position[]>('/api/v1/positions'),
  trades: (status?: string) =>
    get<Trade[]>(`/api/v1/trades${status ? `?status=${status}` : ''}` ),
  botStatus: () => get<BotStatus>('/api/v1/bot/status'),
  botCommand: (cmd: 'stop' | 'pause' | 'resume') =>
    post<{ ok: boolean; command: string }>(`/api/v1/bot/command/${cmd}`),
  resetKillSwitch: () =>
    post<{ ok: boolean; message: string }>('/api/v1/bot/reset-killswitch'),
  resetLossResponse: () =>
    post<{ ok: boolean; message: string }>('/api/v1/bot/reset-loss-response'),
  emergencyStop: () =>
    post<{ ok: boolean; message: string }>('/api/v1/bot/emergency-stop'),
  getCurrency: () =>
    get<{ currency: string; zar_rate: number }>('/api/v1/settings/currency'),
  setCurrency: (currency: string, zar_rate: number) =>
    postJson<{ ok: boolean }>('/api/v1/settings/currency', { currency, zar_rate }),
  hardReset: () =>
    post<{ ok: boolean; message: string }>('/api/v1/admin/hard-reset'),
  closePosition: (id: number) =>
    del<{ ok: boolean; message: string }>(`/api/v1/positions/${id}`),
  backtests: (symbol?: string) =>
    get<Backtest[]>(`/api/v1/backtests${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''}`),
  candles: (symbol: string, timeframe = '1h', limit = 48) =>
    get<Candle[]>(`/api/v1/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`),
  getTradeLimits: () => get<TradeLimits>('/api/v1/settings/trade-limits'),
  setTradeLimits: (updates: Partial<TradeLimits>) =>
    postJson<TradeLimits>('/api/v1/settings/trade-limits', updates),
  getCapital: () => get<{ capital_usdt: number; starting_capital_usdt: number }>('/api/v1/settings/capital'),
  setCapital: (capital_usdt: number) =>
    postJson<{ ok: boolean; capital_usdt: number }>('/api/v1/settings/capital', { capital_usdt }),
  pnlStats: (period: '1h' | '24h' | '7d' | '30d' | 'all') =>
    get<PnlPeriodStats>(`/api/v1/stats/pnl?period=${period}`),
  modeBreakdown: () => get<ModeBreakdown>('/api/v1/stats/mode-breakdown'),
  recoveryStats: (hours = 24) => get<RecoveryStats>(`/api/v1/stats/recovery?hours=${hours}`),
  getAccountBalances: () => get<AccountBalances>('/api/v1/account/balances'),
  convertAllToUsdt: () => post<{ ok: boolean; conversions: Array<{ asset: string; ok: boolean; usdt_received?: number; error?: string }> }>('/api/v1/account/convert-all'),
  getLiveMode: () => get<LiveMode>('/api/v1/settings/live-mode'),
  setLiveMode: (mode: Partial<LiveMode>) => postJson<LiveMode>('/api/v1/settings/live-mode', mode),
  marketSentiment: () => get<MarketSentiment>('/api/v1/market/sentiment'),
  startupStatus: () => get<StartupStatus>('/api/v1/bot/startup-status'),
  symbolOverview: (symbol: string) =>
    get<SymbolOverview>(`/api/v1/symbol/overview?symbol=${encodeURIComponent(symbol)}`),
  manualOpen: (symbol: string, side: 'BUY' | 'SELL') =>
    postJson<{ ok: boolean; request_id: string; message: string }>('/api/v1/positions/manual-open', { symbol, side }),
  manualOpenResult: (requestId: string) =>
    get<ManualOpenResult>(`/api/v1/positions/manual-open/${requestId}`),
  confirmStartup: () => post<{ ok: boolean; message: string }>('/api/v1/bot/confirm-startup'),
};

export const WS_URL = BASE.replace(/^http/, 'ws') + '/ws/prices';
export const WS_EVENTS_URL = BASE.replace(/^http/, 'ws') + '/ws/events';
