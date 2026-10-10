'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useCurrency } from '@/lib/currency';
import type { BotStatus, PnlPeriodStats, Trade, Position, ModeBreakdown, RecoveryStats } from '@/lib/types';
import RecoverySection from '@/components/RecoverySection';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
  CartesianGrid,
} from 'recharts';

function BigStat({
  label,
  value,
  sub,
  color,
  huge,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  huge?: boolean;
}) {
  return (
    <div className="rounded-2xl p-5" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
      <div className="text-xs mb-2" style={{ color: '#848e9c' }}>{label}</div>
      <div
        style={{
          fontSize: huge ? 32 : 22,
          fontWeight: 700,
          color: color ?? '#e8e8e8',
          letterSpacing: '-0.5px',
        }}
      >
        {value}
      </div>
      {sub && <div className="text-xs mt-1" style={{ color: '#555' }}>{sub}</div>}
    </div>
  );
}

function ProgressBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div className="w-full rounded-full overflow-hidden" style={{ height: 8, background: '#1f1f1f' }}>
      <div
        className="h-full rounded-full transition-all duration-700"
        style={{ width: `${Math.min(pct, 100)}%`, background: color }}
      />
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-sm font-bold mb-3" style={{ color: '#e8e8e8', letterSpacing: '0.3px' }}>
      {children}
    </h2>
  );
}

function friendlyState(state: string, defensive?: boolean): { label: string; desc: string; color: string } {
  if (defensive || state === 'defensive') return {
    label: 'Defending Portfolio',
    desc: 'Your bot hit some losses and automatically switched to safer, smaller trades to protect your money.',
    color: '#f0b90b',
  };
  if (state === 'running') return {
    label: 'Active & Trading',
    desc: 'Your bot is live and actively looking for profitable opportunities.',
    color: '#0ecb81',
  };
  if (state === 'paused') return {
    label: 'Paused',
    desc: 'Your bot is temporarily paused. It will resume when you click Resume.',
    color: '#f0b90b',
  };
  if (state === 'stopped' || state === 'kill_switch') return {
    label: 'Stopped',
    desc: 'Trading is stopped. Click Start to resume.',
    color: '#f6465d',
  };
  return { label: 'Connecting…', desc: 'Waiting for bot status.', color: '#848e9c' };
}

function friendlyStrategy(status: BotStatus | null): { name: string; desc: string } {
  if (!status) return { name: '—', desc: '' };
  if (status.defensive_mode || status.state === 'defensive') return {
    name: 'Conservative (Auto-Defence)',
    desc: 'Spot trading only · High confidence required · Smaller positions · Short timeout on bad trades',
  };
  return {
    name: 'Dynamic (Normal)',
    desc: 'Mix of spot & futures trading · Standard confidence · Leveraged positions to maximize returns',
  };
}

export default function UserStatsPage() {
  const { display } = useCurrency();
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [allTime, setAllTime] = useState<PnlPeriodStats | null>(null);
  const [today, setToday] = useState<PnlPeriodStats | null>(null);
  const [recentTrades, setRecentTrades] = useState<Trade[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [startingCapital, setStartingCapital] = useState<number>(0);
  const [modeBreakdown, setModeBreakdown] = useState<ModeBreakdown | null>(null);
  const [allTrades, setAllTrades] = useState<Trade[]>([]);
  const [recovery, setRecovery] = useState<RecoveryStats | null>(null);

  const load = async () => {
    const [s, a, t, trades, cap, pos, mb, at, rec] = await Promise.allSettled([
      api.botStatus(),
      api.pnlStats('all'),
      api.pnlStats('24h'),
      api.trades('CLOSED'),
      api.getCapital(),
      api.positions(),
      api.modeBreakdown(),
      api.trades('CLOSED'),
      api.recoveryStats(24),
    ]);
    if (rec.status === 'fulfilled') setRecovery(rec.value);
    if (s.status === 'fulfilled') setStatus(s.value);
    if (a.status === 'fulfilled') setAllTime(a.value);
    if (t.status === 'fulfilled') setToday(t.value);
    if (trades.status === 'fulfilled') setRecentTrades(trades.value.slice(0, 8));
    if (cap.status === 'fulfilled') setStartingCapital(cap.value.starting_capital_usdt || cap.value.capital_usdt);
    if (pos.status === 'fulfilled') setPositions(pos.value);
    if (mb.status === 'fulfilled') setModeBreakdown(mb.value);
    if (at.status === 'fulfilled') setAllTrades(at.value.slice().reverse());
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const freeCapital = status?.capital_usdt ?? 0;
  const futuresBalance = status?.futures_usdt ?? 0;
  const openPositions = status?.open_trades ?? 0;

  const positionsValue = positions.reduce(
    (sum, p) => sum + (p.entry_price * p.quantity) / p.leverage + (p.unrealized_pnl ?? 0),
    0
  );
  const totalPortfolio = freeCapital + futuresBalance + positionsValue;

  const netRealizedProfit = (allTime?.total_profit_usdt ?? 0) + (allTime?.total_loss_usdt ?? 0);
  const portfolioGainPct = startingCapital > 0 ? ((totalPortfolio - startingCapital) / startingCapital) * 100 : 0;

  const wins = allTime?.win_count ?? 0;
  const losses = allTime?.loss_count ?? 0;
  const totalTrades = wins + losses;
  const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;

  const todayNet = (today?.total_profit_usdt ?? 0) + (today?.total_loss_usdt ?? 0);
  const todayWins = today?.win_count ?? 0;
  const todayLosses = today?.loss_count ?? 0;

  const heatLimitPct = status?.heat_limit_pct ?? 40;
  const openHeat = status?.open_heat_usdt ?? 0;
  const capitalUsdt = status?.capital_usdt ?? 0;
  const heatLimit = capitalUsdt * heatLimitPct / 100;
  const heatPct = heatLimit > 0 ? Math.min((openHeat / heatLimit) * 100, 100) : 0;

  const stateInfo = friendlyState(status?.state ?? 'unknown', status?.defensive_mode);
  const stratInfo = friendlyStrategy(status);

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-xl font-bold">My Trading Stats</h1>
        <p className="text-xs mt-1" style={{ color: '#848e9c' }}>
          A plain-English summary of how your bot is doing
        </p>
      </div>

      <div
        className="rounded-2xl p-5 flex items-center justify-between"
        style={{ background: `${stateInfo.color}12`, border: `1px solid ${stateInfo.color}33` }}
      >
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: stateInfo.color }} />
            <span className="text-sm font-bold" style={{ color: stateInfo.color }}>
              {stateInfo.label}
            </span>
            {status?.paper && (
              <span className="text-xs px-2 py-0.5 rounded font-medium" style={{ background: 'rgba(132,142,156,0.15)', color: '#848e9c' }}>
                Paper Mode
              </span>
            )}
          </div>
          <p className="text-xs" style={{ color: '#848e9c' }}>{stateInfo.desc}</p>
        </div>
        <div className="text-right text-xs" style={{ color: '#848e9c' }}>
          <div>{openPositions} open trades</div>
          <div>{display(freeCapital)} available</div>
        </div>
      </div>

      <div
        className="rounded-2xl p-5"
        style={{ background: netRealizedProfit >= 0 ? 'rgba(14,203,129,0.07)' : 'rgba(246,70,93,0.07)', border: `1px solid ${netRealizedProfit >= 0 ? 'rgba(14,203,129,0.2)' : 'rgba(246,70,93,0.2)'}` }}
      >
        <div className="text-xs mb-3" style={{ color: '#848e9c' }}>Profit on Initial Investment</div>
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Started with</div>
            <div className="text-lg font-bold" style={{ color: '#e8e8e8' }}>{display(startingCapital)}</div>
          </div>
          <div className="text-2xl font-bold" style={{ color: '#555' }}>→</div>
          <div>
            <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Portfolio now</div>
            <div className="text-lg font-bold" style={{ color: '#e8e8e8' }}>{display(totalPortfolio)}</div>
          </div>
          <div>
            <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Spot</div>
            <div className="text-lg font-bold" style={{ color: '#e8e8e8' }}>{display(freeCapital)}</div>
          </div>
          {futuresBalance > 0 && (
            <div>
              <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Futures</div>
              <div className="text-lg font-bold" style={{ color: '#e8e8e8' }}>{display(futuresBalance)}</div>
            </div>
          )}
          <div className="ml-auto text-right">
            <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Total profit made</div>
            <div
              style={{ fontSize: 28, fontWeight: 800, letterSpacing: '-1px', color: netRealizedProfit >= 0 ? '#0ecb81' : '#f6465d' }}
            >
              {netRealizedProfit >= 0 ? '+' : ''}{display(netRealizedProfit)}
            </div>
            <div className="text-sm font-semibold mt-0.5" style={{ color: netRealizedProfit >= 0 ? '#0ecb81' : '#f6465d' }}>
              {portfolioGainPct >= 0 ? '+' : ''}{portfolioGainPct.toFixed(2)}% return
            </div>
          </div>
        </div>
      </div>

      <div>
        <SectionTitle>Overall Portfolio</SectionTitle>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <BigStat
            label="Net Profit (All Time)"
            value={`${netRealizedProfit >= 0 ? '+' : ''}${display(netRealizedProfit)}`}
            sub={`${portfolioGainPct >= 0 ? '+' : ''}${portfolioGainPct.toFixed(1)}% since start`}
            color={netRealizedProfit >= 0 ? '#0ecb81' : '#f6465d'}
            huge
          />
          <BigStat
            label="Total Wins"
            value={display(allTime?.total_profit_usdt ?? 0)}
            sub={`${wins} winning trades`}
            color="#0ecb81"
          />
          <BigStat
            label="Total Losses"
            value={display(Math.abs(allTime?.total_loss_usdt ?? 0))}
            sub={`${losses} losing trades`}
            color="#f6465d"
          />
          <BigStat
            label="Win Rate"
            value={`${winRate.toFixed(0)}%`}
            sub={`${totalTrades} total trades`}
            color={winRate >= 50 ? '#0ecb81' : winRate >= 35 ? '#f0b90b' : '#f6465d'}
          />
        </div>
        <div className="mt-2 rounded-xl p-3 text-xs" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="mb-1" style={{ color: '#848e9c' }}>
            Win rate explained: your bot wins {winRate.toFixed(0)}% of trades, but each win tends to be{' '}
            {wins > 0 && losses > 0
              ? `${((allTime?.total_profit_usdt ?? 0) / wins / Math.abs((allTime?.total_loss_usdt ?? 0) / losses)).toFixed(1)}x bigger than each loss`
              : 'larger than each loss'}
            . That&apos;s why the net is still {netRealizedProfit >= 0 ? 'positive' : 'negative'}.
          </div>
        </div>
      </div>


      <div>
        <SectionTitle>Spot vs Futures Breakdown</SectionTitle>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 mb-4">
          <BigStat
            label="Spot Profit"
            value={display(modeBreakdown?.spot.total_profit_usdt ?? 0)}
            sub={`${modeBreakdown?.spot.win_count ?? 0} winning trades`}
            color="#0ecb81"
          />
          <BigStat
            label="Spot Loss"
            value={display(Math.abs(modeBreakdown?.spot.total_loss_usdt ?? 0))}
            sub={`${modeBreakdown?.spot.loss_count ?? 0} losing trades`}
            color="#f6465d"
          />
          <BigStat
            label="Futures Profit"
            value={display(modeBreakdown?.futures.total_profit_usdt ?? 0)}
            sub={`${modeBreakdown?.futures.win_count ?? 0} winning trades`}
            color="#0ecb81"
          />
          <BigStat
            label="Futures Loss"
            value={display(Math.abs(modeBreakdown?.futures.total_loss_usdt ?? 0))}
            sub={`${modeBreakdown?.futures.loss_count ?? 0} losing trades`}
            color="#f6465d"
          />
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-2xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
            <div className="text-xs font-semibold mb-3" style={{ color: '#848e9c' }}>Wins vs Losses by Mode</div>
            <ResponsiveContainer width="100%" height={200}>
              <ComposedChart
                data={[
                  {
                    name: 'Spot',
                    Wins: modeBreakdown?.spot.win_count ?? 0,
                    Losses: modeBreakdown?.spot.loss_count ?? 0,
                  },
                  {
                    name: 'Futures',
                    Wins: modeBreakdown?.futures.win_count ?? 0,
                    Losses: modeBreakdown?.futures.loss_count ?? 0,
                  },
                ]}
                margin={{ top: 4, right: 8, bottom: 0, left: -10 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#1f1f1f" />
                <XAxis dataKey="name" tick={{ fill: '#848e9c', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#848e9c', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#111', border: '1px solid #2a2a2a', borderRadius: 8 }}
                  labelStyle={{ color: '#e8e8e8', fontWeight: 600 }}
                  itemStyle={{ color: '#848e9c' }}
                />
                <Legend wrapperStyle={{ fontSize: 11, color: '#848e9c' }} />
                <Bar dataKey="Wins" fill="#0ecb81" radius={[4, 4, 0, 0]} maxBarSize={40} />
                <Bar dataKey="Losses" fill="#f6465d" radius={[4, 4, 0, 0]} maxBarSize={40} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="rounded-2xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
            <div className="text-xs font-semibold mb-3" style={{ color: '#848e9c' }}>Profit vs Loss by Mode (USDT)</div>
            <ResponsiveContainer width="100%" height={200}>
              <ComposedChart
                data={[
                  {
                    name: 'Spot',
                    Profit: +(modeBreakdown?.spot.total_profit_usdt ?? 0).toFixed(2),
                    Loss: +Math.abs(modeBreakdown?.spot.total_loss_usdt ?? 0).toFixed(2),
                    Net: +(modeBreakdown?.spot.net_usdt ?? 0).toFixed(2),
                  },
                  {
                    name: 'Futures',
                    Profit: +(modeBreakdown?.futures.total_profit_usdt ?? 0).toFixed(2),
                    Loss: +Math.abs(modeBreakdown?.futures.total_loss_usdt ?? 0).toFixed(2),
                    Net: +(modeBreakdown?.futures.net_usdt ?? 0).toFixed(2),
                  },
                ]}
                margin={{ top: 4, right: 8, bottom: 0, left: -10 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#1f1f1f" />
                <XAxis dataKey="name" tick={{ fill: '#848e9c', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#848e9c', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#111', border: '1px solid #2a2a2a', borderRadius: 8 }}
                  labelStyle={{ color: '#e8e8e8', fontWeight: 600 }}
                  itemStyle={{ color: '#848e9c' }}
                  formatter={(v: number) => `$${v.toFixed(2)}`}
                />
                <Legend wrapperStyle={{ fontSize: 11, color: '#848e9c' }} />
                <Bar dataKey="Profit" fill="#0ecb81" radius={[4, 4, 0, 0]} maxBarSize={40} />
                <Bar dataKey="Loss" fill="#f6465d" radius={[4, 4, 0, 0]} maxBarSize={40} />
                <Line dataKey="Net" stroke="#f0b90b" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="rounded-2xl p-4 mt-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-xs font-semibold mb-3" style={{ color: '#848e9c' }}>Cumulative P&L Trend (last 100 trades)</div>
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart
              data={(() => {
                let spotCum = 0, futCum = 0, idx = 0;
                return allTrades.slice(0, 100).map((t) => {
                  const pnl = t.pnl_usdt ?? 0;
                  const isSpot = !t.mode?.toUpperCase().includes('FUTURES');
                  if (isSpot) spotCum = +(spotCum + pnl).toFixed(4);
                  else futCum = +(futCum + pnl).toFixed(4);
                  idx++;
                  return { trade: idx, Spot: spotCum, Futures: futCum };
                });
              })()}
              margin={{ top: 4, right: 8, bottom: 0, left: -10 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#1f1f1f" />
              <XAxis dataKey="trade" tick={{ fill: '#848e9c', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#848e9c', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#111', border: '1px solid #2a2a2a', borderRadius: 8 }}
                labelStyle={{ color: '#e8e8e8', fontWeight: 600 }}
                itemStyle={{ color: '#848e9c' }}
                formatter={(v: number) => `$${v.toFixed(2)}`}
                labelFormatter={(l) => `Trade #${l}`}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: '#848e9c' }} />
              <Line dataKey="Spot" stroke="#0ecb81" strokeWidth={2} dot={false} />
              <Line dataKey="Futures" stroke="#9b59b6" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div>
        <SectionTitle>Today</SectionTitle>
        <div className="grid grid-cols-3 gap-3">
          <BigStat
            label="Today's P&L"
            value={`${todayNet >= 0 ? '+' : ''}${display(todayNet)}`}
            sub={`${display(today?.total_profit_usdt ?? 0)} profit · ${display(Math.abs(today?.total_loss_usdt ?? 0))} loss`}
            color={todayNet >= 0 ? '#0ecb81' : '#f6465d'}
          />
          <BigStat
            label="Today's Trades"
            value={String(todayWins + todayLosses)}
            sub={`${todayWins} wins · ${todayLosses} losses`}
          />
          <BigStat
            label="Daily P&L %"
            value={`${startingCapital > 0 ? ((status?.daily_pnl_usdt ?? 0) / startingCapital * 100).toFixed(2) : '0.00'}%`}
            sub={`${display(status?.daily_pnl_usdt ?? 0)} today`}
            color={(status?.daily_pnl_usdt ?? 0) >= 0 ? '#0ecb81' : '#f6465d'}
          />
        </div>
      </div>

      <div>
        <SectionTitle>Portfolio Heat</SectionTitle>
        <div className="rounded-2xl p-5" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="flex justify-between items-end mb-3">
            <div>
              <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Heat Limit ({heatLimitPct}%)</div>
              <div className="text-xl font-bold">${heatLimit.toFixed(2)}</div>
            </div>
            <div className="text-right">
              <div className="text-xs mb-1" style={{ color: '#848e9c' }}>In Use</div>
              <div
                className="text-xl font-bold"
                style={{ color: heatPct >= 90 ? '#f6465d' : heatPct >= 70 ? '#f0b90b' : '#0ecb81' }}
              >
                ${openHeat.toFixed(2)}
              </div>
            </div>
          </div>
          <ProgressBar pct={heatPct} color={heatPct >= 90 ? '#f6465d' : heatPct >= 70 ? '#f0b90b' : '#0ecb81'} />
          <div className="flex justify-between mt-2 text-xs" style={{ color: '#848e9c' }}>
            <span>{heatPct.toFixed(0)}% used</span>
            <span>Free: ${(status?.free_heat_usdt ?? 0).toFixed(2)}</span>
          </div>
        </div>
      </div>

      <div>
        <SectionTitle>Current Strategy</SectionTitle>
        <div className="rounded-2xl p-5" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="flex items-center gap-2 mb-2">
            <div
              className="text-xs px-2 py-1 rounded font-semibold"
              style={{
                background: status?.defensive_mode ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.12)',
                color: status?.defensive_mode ? '#f0b90b' : '#0ecb81',
              }}
            >
              {stratInfo.name}
            </div>
          </div>
          <p className="text-xs mb-4" style={{ color: '#848e9c' }}>{stratInfo.desc}</p>
          <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
            <div>
              <div style={{ color: '#848e9c' }}>Stop Loss</div>
              <div className="font-semibold mt-0.5" style={{ color: '#f6465d' }}>
                {status?.defensive_mode ? '1.0%' : '1.5%'} per trade
              </div>
              <div style={{ color: '#555', fontSize: 10 }}>Exit if price drops this much</div>
            </div>
            <div>
              <div style={{ color: '#848e9c' }}>Take Profit</div>
              <div className="font-semibold mt-0.5" style={{ color: '#0ecb81' }}>
                {status?.defensive_mode ? '1.5%' : '2.0%'} per trade
              </div>
              <div style={{ color: '#555', fontSize: 10 }}>Lock in gains at this level</div>
            </div>
            <div>
              <div style={{ color: '#848e9c' }}>Max Open Trades</div>
              <div className="font-semibold mt-0.5">{status?.defensive_mode ? '3' : '20'}</div>
              <div style={{ color: '#555', fontSize: 10 }}>Active at once</div>
            </div>
            <div>
              <div style={{ color: '#848e9c' }}>Confidence Needed</div>
              <div className="font-semibold mt-0.5">{status?.defensive_mode ? '80%' : '55%'}+</div>
              <div style={{ color: '#555', fontSize: 10 }}>ML signal strength required</div>
            </div>
          </div>
          {status?.defensive_mode && (
            <div className="mt-4 text-xs p-3 rounded-xl" style={{ background: 'rgba(240,185,11,0.06)', border: '1px solid rgba(240,185,11,0.2)' }}>
              <span style={{ color: '#f0b90b', fontWeight: 600 }}>Auto-recovery active</span>
              <span style={{ color: '#848e9c' }}> — the bot will automatically return to normal strategy once daily performance improves.</span>
            </div>
          )}
        </div>
      </div>

      <div>
        <SectionTitle>Exit Recovery</SectionTitle>
        <RecoverySection data={recovery} display={display} />
      </div>

      <div>
        <SectionTitle>Recent Trades</SectionTitle>
        <div className="rounded-2xl overflow-hidden" style={{ border: '1px solid #1f1f1f' }}>
          {recentTrades.length === 0 ? (
            <div className="text-xs text-center py-8" style={{ color: '#848e9c' }}>No recent trades</div>
          ) : (
            <div className="divide-y" style={{ borderColor: '#1a1a1a' }}>
              {recentTrades.map((t) => {
                const profit = (t.pnl_usdt ?? 0) >= 0;
                const closedAt = t.closed_at ? new Date(t.closed_at).toLocaleString() : '—';
                return (
                  <div key={t.id} className="flex items-center justify-between px-4 py-3 text-xs">
                    <div className="flex items-center gap-3">
                      <div
                        className="w-1.5 h-6 rounded-full"
                        style={{ background: profit ? '#0ecb81' : '#f6465d' }}
                      />
                      <div>
                        <div className="font-semibold">{t.symbol}</div>
                        <div style={{ color: '#848e9c' }}>
                          {t.side === 'BUY' ? 'Bought & sold' : 'Shorted & covered'} · {t.mode} · {closedAt}
                        </div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-bold" style={{ color: profit ? '#0ecb81' : '#f6465d' }}>
                        {profit ? '+' : ''}{display(t.pnl_usdt ?? 0)}
                      </div>
                      <div style={{ color: '#555' }}>
                        {profit ? '+' : ''}{(t.pnl_percent ?? 0).toFixed(2)}%
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="text-center text-xs pb-4" style={{ color: '#333' }}>
        Refreshes every 15 seconds · Paper trading mode · All amounts in simulated USDT
      </div>
    </div>
  );
}
