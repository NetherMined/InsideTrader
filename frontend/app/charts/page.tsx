'use client';

import { useEffect, useState, useRef, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import type { Position, Candle } from '@/lib/types';
import { PositionChart } from '@/components/PositionCharts';
import { fmtPrice } from '@/lib/utils';
import { useCurrency } from '@/lib/currency';

function ChartsContent() {
  const searchParams = useSearchParams();
  const symbolFilter = searchParams.get('symbol');

  const { display } = useCurrency();
  const [positions, setPositions] = useState<Position[]>([]);
  const [candles, setCandles] = useState<Record<string, Candle[]>>({});
  const [loading, setLoading] = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadPositions = async () => {
    try {
      const all = await api.positions();
      const filtered = symbolFilter
        ? all.filter((p) => p.symbol === symbolFilter)
        : all;
      setPositions(filtered);
      return filtered;
    } catch {
      return [];
    }
  };

  const loadCandles = async (pos: Position[]) => {
    if (pos.length === 0) return;
    const symbols = [...new Set(pos.map((p) => p.symbol))];
    const pairs = await Promise.all(
      symbols.map(async (sym) => {
        try {
          const data = await api.candles(sym, '1h', 48);
          return [sym, [...data].reverse()] as const;
        } catch {
          return [sym, []] as const;
        }
      })
    );
    setCandles(Object.fromEntries(pairs));
  };

  useEffect(() => {
    const init = async () => {
      const pos = await loadPositions();
      await loadCandles(pos);
      setLoading(false);
    };
    init();

    intervalRef.current = setInterval(async () => {
      const pos = await loadPositions();
      await loadCandles(pos);
    }, 10000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [symbolFilter]);

  const totalPnl = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0);
  const profitable = positions.filter((p) => p.unrealized_pnl >= 0).length;

  return (
    <div style={{ background: '#0b0b0b', minHeight: '100vh', padding: '20px 24px' }}>
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-base font-bold" style={{ color: '#e8e8e8' }}>
            {symbolFilter ? `${symbolFilter} Chart` : 'Active Trade Charts'}
          </h1>
          {!symbolFilter && (
            <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
              {positions.length} open &middot; {profitable} profitable &middot; Net{' '}
              <span style={{ color: totalPnl >= 0 ? '#0ecb81' : '#f6465d', fontWeight: 600 }}>
                {totalPnl >= 0 ? '+' : ''}{display(totalPnl)}
              </span>
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {symbolFilter && (
            <button
              onClick={() => window.location.href = '/charts'}
              className="text-xs px-3 py-1.5 rounded-lg font-medium"
              style={{ background: 'rgba(240,185,11,0.1)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.2)' }}
            >
              All Charts
            </button>
          )}
          <button
            onClick={() => window.close()}
            className="text-xs px-3 py-1.5 rounded-lg font-medium"
            style={{ background: 'rgba(132,142,156,0.1)', color: '#848e9c', border: '1px solid #2a2a2a' }}
          >
            Close Window
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center" style={{ height: 200, color: '#848e9c', fontSize: 13 }}>
          Loading charts...
        </div>
      ) : positions.length === 0 ? (
        <div className="flex items-center justify-center" style={{ height: 200, color: '#848e9c', fontSize: 13 }}>
          {symbolFilter ? `No open position for ${symbolFilter}` : 'No open positions'}
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: symbolFilter
              ? '1fr'
              : 'repeat(auto-fill, minmax(420px, 1fr))',
            gap: 16,
          }}
        >
          {positions.map((p) => (
            <PositionChart
              key={p.id}
              position={p}
              candles={candles[p.symbol] ?? []}
              showPopOut={false}
            />
          ))}
        </div>
      )}

      {symbolFilter && positions.length > 0 && (
        <div className="mt-4 rounded-xl p-4 text-xs" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="grid grid-cols-4 gap-4" style={{ color: '#848e9c' }}>
            <div>
              <div className="mb-0.5">Entry</div>
              <div className="font-semibold" style={{ color: '#f0b90b' }}>{fmtPrice(positions[0].entry_price)}</div>
            </div>
            <div>
              <div className="mb-0.5">Current</div>
              <div className="font-semibold" style={{ color: '#e8e8e8' }}>{fmtPrice(positions[0].current_price)}</div>
            </div>
            <div>
              <div className="mb-0.5">Stop Loss</div>
              <div className="font-semibold" style={{ color: '#f6465d' }}>
                {positions[0].stop_loss_price ? fmtPrice(positions[0].stop_loss_price) : '—'}
              </div>
            </div>
            <div>
              <div className="mb-0.5">Take Profit</div>
              <div className="font-semibold" style={{ color: '#0ecb81' }}>
                {positions[0].take_profit_price ? fmtPrice(positions[0].take_profit_price) : '—'}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ChartsPage() {
  return (
    <Suspense>
      <ChartsContent />
    </Suspense>
  );
}
