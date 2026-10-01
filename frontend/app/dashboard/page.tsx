'use client';

import { useEffect, useState, useRef } from 'react';
import { api, WS_URL } from '@/lib/api';
import { BotControls } from '@/components/BotControls';
import { PositionChart } from '@/components/PositionCharts';
import { fmt, fmtPct, fmtPrice } from '@/lib/utils';
import type { BotStatus, Position, LivePrice, Candle, PnlPeriodStats, MarketSentiment } from '@/lib/types';
import { useCurrency } from '@/lib/currency';
import { ExternalLink } from 'lucide-react';
import { StartupModal } from '@/components/StartupModal';
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

function HeatCard({
  heatLimitPct,
  openHeat,
  freeHeat,
  capitalUsdt,
}: {
  heatLimitPct: number;
  openHeat: number;
  freeHeat: number;
  capitalUsdt: number;
}) {
  const heatLimit = capitalUsdt * heatLimitPct / 100;
  const pct = heatLimit > 0 ? Math.min((openHeat / heatLimit) * 100, 100) : 0;
  const color = pct >= 90 ? '#f6465d' : pct >= 70 ? '#f0b90b' : '#0ecb81';

  return (
    <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs" style={{ color: '#848e9c' }}>Portfolio Heat</div>
        <span className="text-xs font-semibold" style={{ color }}>{pct.toFixed(0)}% / {heatLimitPct}%</span>
      </div>
      <div className="text-xl font-bold" style={{ color: '#e8e8e8' }}>
        ${openHeat.toFixed(2)}
      </div>
      <div className="mt-2 rounded-full overflow-hidden h-1.5" style={{ background: '#1f1f1f' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <div className="flex items-center justify-between mt-1.5">
        <span className="text-xs" style={{ color: '#848e9c' }}>Used</span>
        <span className="text-xs" style={{ color: '#848e9c' }}>Free: ${freeHeat.toFixed(2)}</span>
      </div>
    </div>
  );
}

function MarketBanner() {
  const { display } = useCurrency();
  const [data, setData] = useState<MarketSentiment | null>(null);

  useEffect(() => {
    api.marketSentiment().then(setData).catch(() => null);
    const id = setInterval(() => {
      api.marketSentiment().then(setData).catch(() => null);
    }, 60000);
    return () => clearInterval(id);
  }, []);

  if (!data) return null;

  const sentimentColor =
    data.sentiment === 'BULLISH' ? '#0ecb81' :
    data.sentiment === 'BEARISH' ? '#f6465d' : '#f0b90b';

  const sentimentBg =
    data.sentiment === 'BULLISH' ? 'rgba(14,203,129,0.08)' :
    data.sentiment === 'BEARISH' ? 'rgba(246,70,93,0.08)' : 'rgba(240,185,11,0.08)';

  const advancePct = Math.round(data.advance_ratio * 100);

  return (
    <div
      className="rounded-xl px-5 py-4"
      style={{ background: sentimentBg, border: `1px solid ${sentimentColor}22` }}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:gap-6">
        <div className="flex items-center gap-3 shrink-0">
          <div
            className="px-3 py-1.5 rounded-lg font-bold text-sm tracking-wide"
            style={{ background: `${sentimentColor}20`, color: sentimentColor, border: `1px solid ${sentimentColor}40` }}
          >
            {data.strength.toUpperCase()}
          </div>
          <div className="text-xs" style={{ color: '#848e9c' }}>
            <span style={{ color: '#0ecb81' }}>{data.advancing} up</span>
            {' / '}
            <span style={{ color: '#f6465d' }}>{data.declining} down</span>
            {' / '}
            <span style={{ color: '#555' }}>{data.neutral_count} flat</span>
            <span style={{ color: '#444' }}> &middot; {data.total_symbols} pairs</span>
          </div>
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: '#1f1f1f' }}>
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${advancePct}%`, background: sentimentColor }}
              />
            </div>
            <span className="text-xs font-semibold shrink-0" style={{ color: sentimentColor }}>
              {advancePct}% advancing
            </span>
          </div>
          <div className="text-xs" style={{ color: '#848e9c', lineHeight: 1.5 }}>
            {data.strategy}
          </div>
        </div>

        <div className="flex items-center gap-4 shrink-0">
          {data.top_gainers.length > 0 && (
            <div className="text-xs">
              <div className="mb-1" style={{ color: '#555' }}>Top Gainers</div>
              {data.top_gainers.map((s) => (
                <div key={s} style={{ color: '#0ecb81' }}>{s.replace('/USDT', '')}</div>
              ))}
            </div>
          )}
          {data.top_losers.length > 0 && (
            <div className="text-xs">
              <div className="mb-1" style={{ color: '#555' }}>Top Losers</div>
              {data.top_losers.map((s) => (
                <div key={s} style={{ color: '#f6465d' }}>{s.replace('/USDT', '')}</div>
              ))}
            </div>
          )}
          <div
            className="rounded-lg px-3 py-2 text-right"
            style={{ background: '#111111', border: '1px solid #1f1f1f', minWidth: 100 }}
          >
            <div className="text-xs mb-0.5" style={{ color: '#848e9c' }}>Est. Daily Profit</div>
            <div className="text-base font-bold" style={{ color: sentimentColor }}>
              {display(data.estimated_daily_profit_usdt)}
            </div>
            <div className="text-xs" style={{ color: '#555' }}>
              ~{data.estimated_daily_profit_pct.toFixed(2)}%
            </div>
          </div>
        </div>
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

    if (!startupDismissed) {
      api.startupStatus().then((s) => {
        if (s.awaiting_confirmation) setShowStartupModal(true);
      }).catch(() => null);
    }



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

  const [startupDismissed, setStartupDismissed] = useState(false);
  const [showStartupModal, setShowStartupModal] = useState(false);

  const freeCapital = status?.capital_usdt ?? 0;
  const futuresBalance = status?.futures_usdt ?? 0;
  const totalPortfolio =
    freeCapital +
    futuresBalance +
    positions.reduce(
      (sum, p) => sum + (p.entry_price * p.quantity) / p.leverage + p.unrealized_pnl,
      0
    );

  return (
    <div className="p-6 space-y-6">
      {showStartupModal && (
        <StartupModal
          onConfirmed={() => { setShowStartupModal(false); setStartupDismissed(true); loadStatus(); }}
          onClose={() => { setShowStartupModal(false); setStartupDismissed(true); }}
        />
      )}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold">Dashboard</h1>
          <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
            Live overview of bot performance
          </p>
        </div>
        <BotControls
          status={status}
          onUpdate={loadStatus}
          onRequestStartup={() => setShowStartupModal(true)}
        />
      </div>

      <MarketBanner />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <PnlSplitCard />
        <StatCard
          label="Portfolio"
          value={display(totalPortfolio, 2)}
          sub={`Spot: ${display(freeCapital, 2)}${futuresBalance > 0 ? ` · Futures: ${display(futuresBalance, 2)}` : ''} · Started: ${display(status?.started_with_usdt ?? 0, 2)}`}
        />
        <StatCard
          label="Open Trades"
          value={String(status?.open_trades ?? 0)}
          sub={undefined}
        />
        <StatCard
          label="Kill Switch"
          value={status?.kill_switch ? 'ACTIVE' : 'OK'}
          sub="Trips at -10%"
          positive={!status?.kill_switch}
        />
        <HeatCard
          heatLimitPct={status?.heat_limit_pct ?? 40}
          openHeat={status?.open_heat_usdt ?? 0}
          freeHeat={status?.free_heat_usdt ?? 0}
          capitalUsdt={status?.capital_usdt ?? 0}
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
                <th className="text-right pb-2 font-medium">Invested</th>
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
                  <td className="py-1.5 text-right" style={{ color: '#848e9c' }}>
                    {display(p.quantity * p.entry_price / (p.leverage || 1))}
                  </td>
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