'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { fmt } from '@/lib/utils';
import type { Backtest } from '@/lib/types';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts';

export default function BacktestsPage() {
  const [backtests, setBacktests] = useState<Backtest[]>([]);
  const [sortKey, setSortKey] = useState<string>('total_return_pct');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const toggleSort = (key: string) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  };
  const sortedBacktests = [...backtests].sort((a, b) => {
    const av = (a as any)[sortKey] ?? '';
    const bv = (b as any)[sortKey] ?? '';
    const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    api.backtests().then((b) => {
      setBacktests(b);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const filtered = backtests.filter((b) =>
    !search || b.symbol.toLowerCase().includes(search.toLowerCase())
  );

  const chartData = filtered.slice(0, 15).map((b) => ({
    symbol: b.symbol.replace('USDT', ''),
    return: b.total_return_pct ?? 0,
    winRate: b.win_rate ? b.win_rate * 100 : 0,
    drawdown: b.max_drawdown ? Math.abs(b.max_drawdown) * 100 : 0,
  }));

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-lg font-bold">Backtests</h1>
        <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
          Historical strategy simulation results
        </p>
      </div>

      {!loading && backtests.length > 0 && (
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-sm font-semibold mb-3">Total Return % by Symbol (Top 15)</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
              <XAxis dataKey="symbol" tick={{ fontSize: 10, fill: '#848e9c' }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#848e9c' }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}%`} />
              <Tooltip
                contentStyle={{ background: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: 8, fontSize: 12 }}
                formatter={(v: number, name: string) => [`${v.toFixed(2)}%`, name]}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: '#848e9c' }} />
              <Bar dataKey="return" name="Return %" fill="#f0b90b" radius={[3, 3, 0, 0]} />
              <Bar dataKey="drawdown" name="Drawdown %" fill="#f6465d" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="flex gap-3 items-center">
        <input
          type="text"
          placeholder="Filter by symbol…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm outline-none"
          style={{ background: '#111111', border: '1px solid #1f1f1f', color: '#e8e8e8', width: 200 }}
        />
        <span className="text-xs ml-auto" style={{ color: '#848e9c' }}>
          {filtered.length} results
        </span>
      </div>

      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid #1f1f1f' }}>
        {loading ? (
          <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>No backtest results yet</div>
        ) : (
          <table className="w-full text-xs">
            <thead style={{ background: '#111111' }}>
              <tr>
                {([
                  ['symbol','Symbol','left'],['strategy','Strategy','left'],['timeframe','Timeframe','left'],
                  ['total_trades','Trades','right'],['win_rate','Win Rate','right'],
                  ['sharpe_ratio','Sharpe','right'],['max_drawdown','Max DD','right'],
                  ['total_return_pct','Total Return','right'],['start_date','Period','left'],
                ] as [string,string,string][]).map(([col,label,align]) => (
                  <th
                    key={label}
                    className={`text-${align} px-4 py-2.5 font-medium`}
                    style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap',
                      color: sortKey === col ? '#f0b90b' : '#848e9c' }}
                    onClick={() => toggleSort(col)}
                  >
                    {label}
                    <span style={{ marginLeft: 3, fontSize: 9, opacity: sortKey === col ? 1 : 0.35 }}>
                      {sortKey === col ? (sortDir === 'asc' ? '▲' : '▼') : '▼'}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((b, i) => (
                <tr key={i} style={{ borderTop: '1px solid #1a1a1a' }}>
                  <td className="px-4 py-2 font-medium">{b.symbol}</td>
                  <td className="px-4 py-2">
                    <span
                      className="px-1.5 py-0.5 rounded font-medium"
                      style={{
                        background: b.strategy === 'dynamic' ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.12)',
                        color: b.strategy === 'dynamic' ? '#f0b90b' : '#0ecb81',
                      }}
                    >
                      {b.strategy}
                    </span>
                  </td>
                  <td className="px-4 py-2" style={{ color: '#848e9c' }}>{b.timeframe}</td>
                  <td className="px-4 py-2 text-right">{b.total_trades}</td>
                  <td className="px-4 py-2 text-right">
                    {b.win_rate != null ? `${fmt(b.win_rate * 100, 1)}%` : '—'}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {b.sharpe_ratio != null ? fmt(b.sharpe_ratio, 2) : '—'}
                  </td>
                  <td className="px-4 py-2 text-right" style={{ color: '#f6465d' }}>
                    {b.max_drawdown != null ? `-${fmt(Math.abs(b.max_drawdown) * 100, 1)}%` : '—'}
                  </td>
                  <td
                    className="px-4 py-2 text-right font-semibold"
                    style={{
                      color: b.total_return_pct == null ? '#848e9c' : b.total_return_pct >= 0 ? '#0ecb81' : '#f6465d',
                    }}
                  >
                    {b.total_return_pct != null
                      ? `${b.total_return_pct >= 0 ? '+' : ''}${fmt(b.total_return_pct, 2)}%`
                      : '—'}
                  </td>
                  <td className="px-4 py-2" style={{ color: '#848e9c' }}>
                    {new Date(b.start_date).toLocaleDateString()} – {new Date(b.end_date).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
