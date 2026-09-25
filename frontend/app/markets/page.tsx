'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { fmtVol } from '@/lib/utils';
import type { Market } from '@/lib/types';

export default function MarketsPage() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [sortKey, setSortKey] = useState<string>('volume_24h');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const toggleSort = (key: string) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  };
  const sortedMarkets = [...markets].sort((a, b) => {
    const av = (a as any)[sortKey] ?? '';
    const bv = (b as any)[sortKey] ?? '';
    const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? cmp : -cmp;
  });
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<'all' | 'spot' | 'future'>('all');

  useEffect(() => {
    api.markets(500).then((m) => {
      setMarkets(m);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const filtered = markets.filter((m) => {
    if (search && !m.symbol.toLowerCase().includes(search.toLowerCase())) return false;
    if (typeFilter !== 'all' && m.market_type !== typeFilter) return false;
    return true;
  });

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-lg font-bold">Markets</h1>
        <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
          All scanned Binance markets eligible for trading
        </p>
      </div>

      <div className="flex gap-3 items-center">
        <input
          type="text"
          placeholder="Search symbol…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm outline-none"
          style={{ background: '#111111', border: '1px solid #1f1f1f', color: '#e8e8e8', width: 200 }}
        />
        {(['all', 'spot', 'future'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTypeFilter(t)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium capitalize"
            style={{
              background: typeFilter === t ? 'rgba(240,185,11,0.15)' : '#111111',
              color: typeFilter === t ? '#f0b90b' : '#848e9c',
              border: '1px solid #1f1f1f',
            }}
          >
            {t}
          </button>
        ))}
        <span className="text-xs ml-auto" style={{ color: '#848e9c' }}>
          {filtered.length} markets
        </span>
      </div>

      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid #1f1f1f' }}>
        {loading ? (
          <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>
            Loading markets…
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead style={{ background: '#111111' }}>
              <tr>
                {([
                  ['symbol','Symbol','left'],['base','Base','left'],['market_type','Type','left'],
                  ['volume_24h','24h Volume','right'],['enabled_for_trading','Trading','right'],
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
              {filtered.map((m) => (
                <tr key={m.symbol + m.market_type} style={{ borderTop: '1px solid #1a1a1a' }}>
                  <td className="px-4 py-2 font-medium">{m.symbol}</td>
                  <td className="px-4 py-2" style={{ color: '#848e9c' }}>{m.base}</td>
                  <td className="px-4 py-2">
                    <span
                      className="px-1.5 py-0.5 rounded text-xs font-medium"
                      style={{
                        background: m.market_type === 'future' ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.12)',
                        color: m.market_type === 'future' ? '#f0b90b' : '#0ecb81',
                      }}
                    >
                      {m.market_type.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">{fmtVol(m.volume_24h_usdt)}</td>
                  <td className="px-4 py-2 text-right">
                    {m.enabled_for_trading ? (
                      <span style={{ color: '#0ecb81' }}>Yes</span>
                    ) : (
                      <span style={{ color: '#848e9c' }}>No</span>
                    )}
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
