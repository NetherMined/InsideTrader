'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { fmt, fmtPct, fmtPrice, timeAgo } from '@/lib/utils';
import { useCurrency } from '@/lib/currency';
import type { Trade } from '@/lib/types';

export default function HistoryPage() {
  const { display, tooltip } = useCurrency();
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<'all' | 'closed' | 'open'>('all');

  useEffect(() => {
    const status = filter === 'all' ? undefined : filter;
    api.trades(status).then((t) => {
      setTrades(t);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [filter]);

  const totalPnl = trades.reduce((s, t) => s + (t.pnl_usdt ?? 0), 0);
  const closed = trades.filter((t) => t.status === 'CLOSED');
  const wins = closed.filter((t) => (t.pnl_usdt ?? 0) > 0);
  const winRate = closed.length ? (wins.length / closed.length) * 100 : 0;
  const totalProfit = closed.filter(t => (t.pnl_usdt ?? 0) > 0).reduce((s, t) => s + (t.pnl_usdt ?? 0), 0);
  const totalLoss = closed.filter(t => (t.pnl_usdt ?? 0) < 0).reduce((s, t) => s + (t.pnl_usdt ?? 0), 0);
  const losses = closed.filter(t => (t.pnl_usdt ?? 0) < 0);
  const avgWin = wins.length ? totalProfit / wins.length : 0;
  const avgLoss = losses.length ? totalLoss / losses.length : 0;

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-lg font-bold">Trade History</h1>
        <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
          All trades executed by the bot
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Total P&L</div>
          <div className="text-lg font-bold" title={tooltip(totalPnl)} style={{ color: totalPnl >= 0 ? '#0ecb81' : '#f6465d' }}>
            {totalPnl >= 0 ? '+' : ''}{display(totalPnl)}
          </div>
        </div>
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Win Rate</div>
          <div className="text-lg font-bold">{fmt(winRate, 1)}%</div>
          <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>{wins.length}/{closed.length} closed</div>
        </div>
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-xs mb-1" style={{ color: '#848e9c' }}>Total Trades</div>
          <div className="text-lg font-bold">{trades.length}</div>
        </div>
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-xs mb-1" style={{ color: '#848e9c' }}>P&L Split</div>
          <div className="grid grid-cols-2 gap-2 mt-2 text-xs">
            <div title={tooltip(totalProfit)} style={{ color: '#0ecb81' }}>
              <div className="font-bold">{totalProfit >= 0 ? '+' : ''}{display(totalProfit)}</div>
              <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>Profit</div>
            </div>
            <div title={tooltip(avgWin)} style={{ color: '#0ecb81' }}>
              <div className="font-bold">{avgWin >= 0 ? '+' : ''}{display(avgWin)}</div>
              <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>Avg Win</div>
            </div>
            <div title={tooltip(totalLoss)} style={{ color: '#f6465d' }}>
              <div className="font-bold">{totalLoss >= 0 ? '+' : ''}{display(totalLoss)}</div>
              <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>Loss</div>
            </div>
            <div title={tooltip(avgLoss)} style={{ color: '#f6465d' }}>
              <div className="font-bold">{avgLoss >= 0 ? '+' : ''}{display(avgLoss)}</div>
              <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>Avg Loss</div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex gap-2">
        {(['all', 'closed', 'open'] as const).map((f) => (
          <button
            key={f}
            onClick={() => { setLoading(true); setFilter(f); }}
            className="px-3 py-1.5 rounded-lg text-xs font-medium capitalize"
            style={{
              background: filter === f ? 'rgba(240,185,11,0.15)' : '#111111',
              color: filter === f ? '#f0b90b' : '#848e9c',
              border: '1px solid #1f1f1f',
            }}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid #1f1f1f' }}>
        {loading ? (
          <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>Loading…</div>
        ) : trades.length === 0 ? (
          <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>No trades found</div>
        ) : (
          <table className="w-full text-xs">
            <thead style={{ background: '#111111' }}>
              <tr style={{ color: '#848e9c' }}>
                <th className="text-left px-4 py-2.5 font-medium">Symbol</th>
                <th className="text-left px-4 py-2.5 font-medium">Side</th>
                <th className="text-left px-4 py-2.5 font-medium">Mode</th>
                <th className="text-right px-4 py-2.5 font-medium">Entry</th>
                <th className="text-right px-4 py-2.5 font-medium">Exit</th>
                <th className="text-right px-4 py-2.5 font-medium">Qty</th>
                <th className="text-right px-4 py-2.5 font-medium">P&L $</th>
                <th className="text-right px-4 py-2.5 font-medium">P&L %</th>
                <th className="text-left px-4 py-2.5 font-medium">Status</th>
                <th className="text-left px-4 py-2.5 font-medium">Paper</th>
                <th className="text-left px-4 py-2.5 font-medium">Opened</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} style={{ borderTop: '1px solid #1a1a1a' }}>
                  <td className="px-4 py-2 font-medium">{t.symbol}</td>
                  <td className="px-4 py-2" style={{ color: t.side === 'BUY' ? '#0ecb81' : '#f6465d' }}>
                    {t.side}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className="px-1.5 py-0.5 rounded font-medium"
                      style={{
                        background: t.mode === 'FUTURES' ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.12)',
                        color: t.mode === 'FUTURES' ? '#f0b90b' : '#0ecb81',
                      }}
                    >
                      {t.mode}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">{fmtPrice(t.entry_price)}</td>
                  <td className="px-4 py-2 text-right">{t.exit_price ? fmtPrice(t.exit_price) : '—'}</td>
                  <td className="px-4 py-2 text-right">{fmt(t.quantity, 4)}</td>
                  <td
                    className="px-4 py-2 text-right font-semibold"
                    style={{ color: t.pnl_usdt == null ? '#848e9c' : t.pnl_usdt >= 0 ? '#0ecb81' : '#f6465d' }}
                    title={t.pnl_usdt != null ? tooltip(t.pnl_usdt) : undefined}
                  >
                    {t.pnl_usdt != null ? `${t.pnl_usdt >= 0 ? '+' : ''}${display(t.pnl_usdt)}` : '—'}
                  </td>
                  <td
                    className="px-4 py-2 text-right"
                    style={{ color: t.pnl_percent == null ? '#848e9c' : t.pnl_percent >= 0 ? '#0ecb81' : '#f6465d' }}
                  >
                    {t.pnl_percent != null ? fmtPct(t.pnl_percent) : '—'}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className="px-1.5 py-0.5 rounded font-medium"
                      style={{
                        background: t.status === 'open' ? 'rgba(14,203,129,0.12)' : 'rgba(132,142,156,0.12)',
                        color: t.status === 'open' ? '#0ecb81' : '#848e9c',
                      }}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td className="px-4 py-2" style={{ color: t.paper_trade ? '#f0b90b' : '#848e9c' }}>
                    {t.paper_trade ? 'Paper' : 'Live'}
                  </td>
                  <td className="px-4 py-2" style={{ color: '#848e9c' }}>
                    {timeAgo(t.opened_at)}
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
