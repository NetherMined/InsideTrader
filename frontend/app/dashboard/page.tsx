'use client';

import { useEffect, useState, useRef } from 'react';
import { api, WS_URL } from '@/lib/api';
import { BotControls } from '@/components/BotControls';
import { PositionChart } from '@/components/PositionCharts';
import { fmt, fmtPct, fmtPrice } from '@/lib/utils';
import type { BotStatus, Position, LivePrice, Candle, PnlPeriodStats } from '@/lib/types';
import { useCurrency } from '@/lib/currency';
import { ExternalLink } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

function StatCard({
  label,
  value,
  sub,
  positive,
}: {
  label: string;
  value: string;
  sub?: string;
  positive?: boolean;
}) {
  return (
    <div
      className="rounded-xl p-4"
      style={{ background: '#111111', border: '1px solid #1f1f1f' }}
    >
      <div className="text-xs mb-1" style={{ color: '#848e9c' }}>
        {label}
      </div>
      <div
        className="text-xl font-bold"
        style={{ color: positive === undefined ? '#e8e8e8' : positive ? '#0ecb81' : '#f6465d' }}
      >
        {value}
      </div>
      {sub && (
        <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
          {sub}
        </div>
      )}
    </div>
  );
}

type PnlPeriod = '1h' | '24h' | '7d' | '30d' | 'all';
const PNL_PERIODS: { label: string; value: PnlPeriod }[] = [
  { label: '1h', value: '1h' },
  { label: '24h', value: '24h' },
  { label: '7d', value: '7d' },
  { label: '30d', value: '30d' },
  { label: 'ALL', value: 'all' },
];

function PnlSplitCard() {
  const { display } = useCurrency();
  const [period, setPeriod] = useState<PnlPeriod>('all');
  const [stats, setStats] = useState<PnlPeriodStats | null>(null);

  useEffect(() => {
    api.pnlStats(period).then(setStats).catch(() => null);
  }, [period]);

  const profit = stats?.total_profit_usdt ?? 0;
  const loss = stats?.total_loss_usdt ?? 0;
  const wins = stats?.win_count ?? 0;
  const losses = stats?.loss_count ?? 0;

  return (
    <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs" style={{ color: '#848e9c' }}>Realized P&amp;L</div>
        <div className="flex gap-1">
          {PNL_PERIODS.map(({ label, value }) => (
            <button
              key={value}
              onClick={() => setPeriod(value)}
              className="text-xs px-1.5 py-0.5 rounded"
              style={{
                background: period === value ? '#2a2a2a' : 'transparent',
                color: period === value ? '#e8e8e8' : '#555',
                border: period === value ? '1px solid #333' : '1px solid transparent',
                cursor: 'pointer',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <div className="text-xs mb-0.5" style={{ color: '#848e9c' }}>Profit</div>
          <div className="text-base font-bold" style={{ color: '#0ecb81' }}>+{display(profit)}</div>
          <div className="text-xs mt-0.5" style={{ color: '#555' }}>{wins} wins</div>
        </div>
        <div style={{ width: 1, background: '#1f1f1f', alignSelf: 'stretch' }} />
        <div className="flex-1">
          <div className="text-xs mb-0.5" style={{ color: '#848e9c' }}>Loss</div>
          <div className="text-base font-bold" style={{ color: '#f6465d' }}>{display(loss)}</div>
          <div className="text-xs mt-0.5" style={{ color: '#555' }}>{losses} losses</div>
        </div>
      </div>
    </div>
  );
}

function GoalCard({
  goalUsdt,
  goalMaxUsdt,
  progressUsdt,
  display,
  tooltip,
  goalEnabled,
  onToggleGoal,
}: {
  goalUsdt: number;
  goalMaxUsdt: number;
  progressUsdt: number;
  display: (n: number | null | undefined, d?: number) => string;
  tooltip: (n: number | null | undefined, d?: number) => string;
  goalEnabled: boolean;
  onToggleGoal: () => void;
}) {
  if (goalUsdt <= 0) {
    return (
      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Goal</div>
        <div className="text-sm font-semibold" style={{ color: '#444' }}>No goal set</div>
        <div className="text-xs mt-1" style={{ color: '#444' }}>Set in Settings</div>
      </div>
    );
  }

  const pct = Math.min((progressUsdt / goalUsdt) * 100, 100);
  const achieved = progressUsdt >= goalUsdt;
  const onTrack = pct >= 10;
  const goalStatus = achieved ? 'Achieved' : onTrack ? 'On Track' : 'Behind';
  const statusColor = achieved ? '#0ecb81' : onTrack ? '#f0b90b' : '#f6465d';

  return (
    <div className="rounded-xl p-4" style={{ background: '#111111', border: `1px solid ${achieved ? '#0ecb8133' : '#1f1f1f'}` }}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs" style={{ color: '#848e9c' }}>Goal &middot; 7 days</div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold" style={{ color: statusColor }}>{goalStatus}</span>
          <button
            onClick={onToggleGoal}
            className="text-xs px-2 py-0.5 rounded font-semibold"
            style={{
              background: goalEnabled ? 'rgba(14,203,129,0.15)' : 'rgba(132,142,156,0.12)',
              color: goalEnabled ? '#0ecb81' : '#848e9c',
              border: `1px solid ${goalEnabled ? '#0ecb8133' : '#333'}`,
              cursor: 'pointer',
            }}
          >
            {goalEnabled ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>
      <div className="text-xl font-bold" style={{ color: '#e8e8e8' }} title={tooltip(goalUsdt)}>
        {display(goalUsdt)}
      </div>
      <div className="mt-2 rounded-full overflow-hidden h-1.5" style={{ background: '#1f1f1f' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: statusColor }}
        />
      </div>
      <div className="flex items-center justify-between mt-1.5">
        <span className="text-xs" style={{ color: statusColor }} title={tooltip(progressUsdt)}>
          {display(progressUsdt)} ({pct.toFixed(0)}%)
        </span>
        {goalMaxUsdt > 0 && (
          <span className="text-xs" style={{ color: '#444' }}>max {display(goalMaxUsdt)}</span>
        )}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { display, tooltip } = useCurrency();
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [prices, setPrices] = useState<LivePrice[]>([]);
  const [pnlHistory, setPnlHistory] = useState<{ t: string; pnl: number }[]>([]);
  const [candles, setCandles] = useState<Record<string, Candle[]>>({});
  const [clock, setClock] = useState('');
  const [effectiveMinTrades, setEffectiveMinTrades] = useState(0);
  const [goalEnabled, setGoalEnabled] = useState(true);
  const wsRef = useRef<WebSocket | null>(null);

  const loadStatus = async () => {
    try {
      const s = await api.botStatus();
      setStatus(s);
      setPnlHistory((prev) => {
        const next = [...prev, { t: new Date().toLocaleTimeString(), pnl: s.daily_pnl_pct }];
        return next.slice(-30);
      });
    } catch {}
  };

  const loadPositions = async () => {
    try {
      setPositions(await api.positions());
    } catch {}
  };

  useEffect(() => {
    const tick = () => {
      const d = new Date();
      setClock(
        d.toLocaleTimeString('en-GB', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        })
      );
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (positions.length === 0) {
      setCandles({});
      return;
    }
    const symbols = [...new Set(positions.map((p) => p.symbol))];
    Promise.all(
      symbols.map(async (sym) => {
        try {
          const data = await api.candles(sym, '1h', 48);
          return [sym, [...data].reverse()] as const;
        } catch {
          return [sym, []] as const;
        }
      })
    ).then((pairs) => setCandles(Object.fromEntries(pairs)));
  }, [positions]);

  useEffect(() => {
    loadStatus();
    loadPositions();

    api.getTradeLimits().catch(() => null).then((limits) => {
      if (limits) setEffectiveMinTrades(limits.min_concurrent_trades);
    });

    api.getGoalEnabled().catch(() => null).then((r) => { if (r) setGoalEnabled(r.enabled); });

    const interval = setInterval(() => {
      loadStatus();
      loadPositions();
    }, 10000);

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data);
        const data: LivePrice[] = Array.isArray(parsed) ? parsed : parsed?.data ?? [];
        if (Array.isArray(data)) setPrices(data);
      } catch {}
    };

    return () => {
      clearInterval(interval);
      ws.close();
    };
  }, []);

  const handleToggleGoal = async () => {
    const newVal = !goalEnabled;
    setGoalEnabled(newVal);
    await api.setGoalEnabled(newVal).catch(() => null);
  };

  const freeCapital = status?.capital_usdt ?? 0;
  const totalPortfolio =
    freeCapital +
    positions.reduce(
      (sum, p) => sum + (p.entry_price * p.quantity) / p.leverage + p.unrealized_pnl,
      0
    );

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold">Dashboard</h1>
          <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
            Live overview of bot performance
          </p>
        </div>
        <BotControls status={status} onUpdate={loadStatus} />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <PnlSplitCard />
        <StatCard
          label="Portfolio Value"
          value={display(totalPortfolio, 2)}
          sub={`${display(freeCapital, 2)} free · ${clock}`}
        />
        <StatCard
          label="Open Trades"
          value={String(status?.open_trades ?? 0)}
          sub={effectiveMinTrades > 0 ? `Min: ${effectiveMinTrades}` : undefined}
        />
        <StatCard
          label="Kill Switch"
          value={status?.kill_switch ? 'ACTIVE' : 'OK'}
          sub="Trips at -10%"
          positive={!status?.kill_switch}
        />
        <GoalCard
          goalUsdt={status?.goal_amount_usdt ?? 0}
          goalMaxUsdt={status?.goal_max_usdt ?? 0}
          progressUsdt={status?.goal_progress_usdt ?? 0}
          display={display}
          tooltip={tooltip}
          goalEnabled={goalEnabled}
          onToggleGoal={handleToggleGoal}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div
          className="rounded-xl p-4"
          style={{ background: '#111111', border: '1px solid #1f1f1f' }}
        >
          <div className="text-sm font-semibold mb-3">Daily P&L History</div>
          {pnlHistory.length > 1 ? (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={pnlHistory}>
                <XAxis
                  dataKey="t"
                  tick={{ fontSize: 10, fill: '#848e9c' }}
                  tickLine={false}
                  axisLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tick={{ fontSize: 10, fill: '#848e9c' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `${v.toFixed(1)}%`}
                />
                <Tooltip
                  contentStyle={{
                    background: '#1a1a1a',
                    border: '1px solid #2a2a2a',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(v: number) => [`${v.toFixed(2)}%`, 'P&L']}
                />
                <Line
                  type="monotone"
                  dataKey="pnl"
                  stroke="#f0b90b"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div
              className="h-40 flex items-center justify-center text-sm"
              style={{ color: '#848e9c' }}
            >
              Collecting data...
            </div>
          )}
        </div>

        <div
          className="rounded-xl p-4"
          style={{ background: '#111111', border: '1px solid #1f1f1f' }}
        >
          <div className="text-sm font-semibold mb-3">Live Prices</div>
          <div className="space-y-1.5 max-h-[168px] overflow-y-auto">
            {prices.slice(0, 10).map((p) => (
              <div key={p.symbol} className="flex items-center justify-between text-xs">
                <span className="font-medium">{p.symbol}</span>
                <span>{fmtPrice(p.price)}</span>
                <span style={{ color: p.change_pct >= 0 ? '#0ecb81' : '#f6465d' }}>
                  {fmtPct(p.change_pct)}
                </span>
              </div>
            ))}
            {prices.length === 0 && (
              <div className="text-xs" style={{ color: '#848e9c' }}>
                Waiting for WebSocket…
              </div>
            )}
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: '#111111', border: '1px solid #1f1f1f' }}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-semibold">Open Positions</div>
          <a
            href="/positions"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs"
            style={{ color: '#555' }}
            onMouseEnter={(e) => (e.currentTarget.style.color = '#848e9c')}
            onMouseLeave={(e) => (e.currentTarget.style.color = '#555')}
          >
            <ExternalLink size={12} />
            Full Screen
          </a>
        </div>
        {positions.length === 0 ? (
          <div className="text-xs py-6 text-center" style={{ color: '#848e9c' }}>
            No open positions
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr style={{ color: '#848e9c' }}>
                <th className="text-left pb-2 font-medium">Symbol</th>
                <th className="text-left pb-2 font-medium">Side</th>
                <th className="text-left pb-2 font-medium">Method</th>
                <th className="text-right pb-2 font-medium">Entry</th>
                <th className="text-right pb-2 font-medium">Current</th>
                <th className="text-right pb-2 font-medium">Qty</th>
                <th className="text-right pb-2 font-medium">P&L</th>
                <th className="text-right pb-2 font-medium">Next Action</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.id} style={{ borderTop: '1px solid #1a1a1a' }}>
                  <td className="py-1.5 font-medium">{p.symbol}</td>
                  <td className="py-1.5" style={{ color: p.side === 'BUY' ? '#0ecb81' : '#f6465d' }}>
                    {p.side}
                  </td>
                  <td className="py-1.5">
                    <span className="text-xs px-1 rounded" style={{
                      background: p.mode === 'FUTURES' ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.10)',
                      color: p.mode === 'FUTURES' ? '#f0b90b' : '#0ecb81',
                    }}>
                      {p.mode === 'FUTURES' ? `Futures ${p.leverage}x` : 'Spot'}
                    </span>
                  </td>
                  <td className="py-1.5 text-right">{fmtPrice(p.entry_price)}</td>
                  <td className="py-1.5 text-right">{fmtPrice(p.current_price)}</td>
                  <td className="py-1.5 text-right">{fmt(p.quantity, 4)}</td>
                  <td
                    className="py-1.5 text-right font-semibold"
                    style={{ color: p.unrealized_pnl >= 0 ? '#0ecb81' : '#f6465d' }}
                    title={tooltip(p.unrealized_pnl)}
                  >
                    {p.unrealized_pnl >= 0 ? '+' : ''}{display(p.unrealized_pnl)}
                  </td>
                  <td className="py-1.5 text-right">
                    {(() => {
                      const cur = p.current_price ?? p.entry_price;
                      const isBreakEven = p.take_profit_price !== null && p.take_profit_price !== undefined &&
                        Math.abs(p.take_profit_price - p.entry_price) / p.entry_price < 0.002;
                      const nearSL = p.stop_loss_price !== null && Math.abs((cur - p.stop_loss_price!) / cur * 100) < 0.8;
                      const nearTP = p.take_profit_price !== null && Math.abs((p.take_profit_price! - cur) / cur * 100) < 0.8;
                      const label = isBreakEven ? 'Break-Even' : nearSL ? 'Near SL' : nearTP ? 'Near TP' : 'Hold';
                      const color = isBreakEven ? '#f0b90b' : nearSL ? '#f6465d' : nearTP ? '#0ecb81' : '#555';
                      return <span style={{ color, fontSize: '11px' }}>{label}</span>;
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {positions.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-semibold">Active Trade Charts</div>
            <a
              href="/charts"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs"
              style={{ color: '#555' }}
              onMouseEnter={(e) => (e.currentTarget.style.color = '#848e9c')}
              onMouseLeave={(e) => (e.currentTarget.style.color = '#555')}
            >
              <ExternalLink size={12} />
              Separate Screen
            </a>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {positions.map((p) => (
              <PositionChart
                key={p.id}
                position={p}
                candles={candles[p.symbol] ?? []}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}