'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { fmt, fmtPrice, timeAgo } from '@/lib/utils';
import { useCurrency } from '@/lib/currency';
import type { Position } from '@/lib/types';
import { X } from 'lucide-react';

export default function PositionsPage() {
  const { display, tooltip } = useCurrency();
  const [positions, setPositions] = useState<Position[]>([]);
  const [sortKey, setSortKey] = useState<string>('opened_at');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const toggleSort = (key: string) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  };
  const sortedPositions = [...positions].sort((a, b) => {
    const av = (a as any)[sortKey] ?? '';
    const bv = (b as any)[sortKey] ?? '';
    const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });
  const [loading, setLoading] = useState(true);
  const [closing, setClosing] = useState<number | null>(null);

  const load = async () => {
    try {
      setPositions(await api.positions());
    } catch {}
    setLoading(false);
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  const closePosition = async (id: number) => {
    if (!confirm('Close this position at market price?')) return;
    setClosing(id);
    try {
      await api.closePosition(id);
      await load();
    } catch (e) {
      console.error(e);
    } finally {
      setClosing(null);
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold">Positions</h1>
          <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
            Currently open positions with live P&L
          </p>
        </div>
        <span className="text-xs px-2 py-1 rounded" style={{ background: 'rgba(240,185,11,0.12)', color: '#f0b90b' }}>
          {positions.length} open
        </span>
      </div>

      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid #1f1f1f' }}>
        {loading ? (
          <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>Loading…</div>
        ) : positions.length === 0 ? (
          <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>No open positions</div>
        ) : (
          <table className="w-full text-xs">
            <thead style={{ background: '#111111' }}>
              <tr style={{ color: '#848e9c' }}>
                {([
                  ['symbol','Symbol','left'],['side','Side','left'],['mode','Mode','left'],
                  ['entry_price','Entry','right'],['current_price','Current','right'],
                  ['quantity','Qty','right'],['leverage','Lev','right'],
                  ['stop_loss_price','SL','right'],['take_profit_price','TP','right'],
                  ['unrealized_pnl','Unreal. P&L','right'],['opened_at','Opened','left'],
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
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {sortedPositions.map((p) => (
                <tr key={p.id} style={{ borderTop: '1px solid #1a1a1a' }}>
                  <td className="px-4 py-2 font-medium">{p.symbol}</td>
                  <td className="px-4 py-2" style={{ color: p.side === 'BUY' ? '#0ecb81' : '#f6465d' }}>
                    {p.side}
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className="px-1.5 py-0.5 rounded font-medium"
                      style={{
                        background: p.mode === 'FUTURES' ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.12)',
                        color: p.mode === 'FUTURES' ? '#f0b90b' : '#0ecb81',
                      }}
                    >
                      {p.mode}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">{fmtPrice(p.entry_price)}</td>
                  <td className="px-4 py-2 text-right">{fmtPrice(p.current_price)}</td>
                  <td className="px-4 py-2 text-right">{fmt(p.quantity, 4)}</td>
                  <td className="px-4 py-2 text-right" style={{ color: '#848e9c' }}>
                    {p.leverage}x
                  </td>
                  <td className="px-4 py-2 text-right" style={{ color: '#f6465d' }}>
                    {p.stop_loss_price ? fmtPrice(p.stop_loss_price) : '—'}
                  </td>
                  <td className="px-4 py-2 text-right" style={{ color: '#0ecb81' }}>
                    {p.take_profit_price ? fmtPrice(p.take_profit_price) : '—'}
                  </td>
                  <td
                    className="px-4 py-2 text-right font-semibold"
                    style={{ color: p.unrealized_pnl >= 0 ? '#0ecb81' : '#f6465d' }}
                    title={tooltip(p.unrealized_pnl)}
                  >
                    {p.unrealized_pnl >= 0 ? '+' : ''}{display(p.unrealized_pnl)}
                  </td>
                  <td className="px-4 py-2" style={{ color: '#848e9c' }}>
                    {timeAgo(p.opened_at)}
                  </td>
                  <td className="px-4 py-2">
                    <button
                      onClick={() => closePosition(p.id)}
                      disabled={closing === p.id}
                      className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium disabled:opacity-40"
                      style={{ background: 'rgba(246,70,93,0.1)', color: '#f6465d' }}
                    >
                      <X size={11} />
                      Close
                    </button>
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
