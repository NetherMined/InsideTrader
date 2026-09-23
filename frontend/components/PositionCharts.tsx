'use client';

import { fmtPrice, fmt } from '@/lib/utils';
import { useCurrency } from '@/lib/currency';
import { ExternalLink } from 'lucide-react';
import type { Position, Candle } from '@/lib/types';

export function MiniCandleChart({
  candles,
  entryPrice,
  currentPrice,
  openedAt,
}: {
  candles: Candle[];
  entryPrice: number;
  currentPrice: number | null;
  openedAt: string;
}) {
  const openedMs = new Date(openedAt).getTime();
  const relevant = candles.filter(
    (c) => new Date(c.open_time).getTime() >= openedMs - 3_600_000
  );
  const chartData = relevant.length >= 2 ? relevant : candles.slice(-8);

  if (chartData.length === 0) {
    return (
      <div className="h-28 flex items-center justify-center text-xs" style={{ color: '#848e9c' }}>
        Loading chart...
      </div>
    );
  }

  const W = 400;
  const H = 112;
  const PAD = 6;
  const cW = W - PAD * 2;
  const cH = H - PAD * 2;

  const allPrices = chartData.flatMap((c) => [c.high, c.low]);
  allPrices.push(entryPrice);
  if (currentPrice != null) allPrices.push(currentPrice);

  const pMin = Math.min(...allPrices) * 0.9997;
  const pMax = Math.max(...allPrices) * 1.0003;
  const range = pMax - pMin || 1;

  const toY = (p: number) => PAD + cH - ((p - pMin) / range) * cH;
  const barW = Math.max(2, (cW / chartData.length) * 0.55);

  const entryY = toY(entryPrice);
  const curY = currentPrice != null ? toY(currentPrice) : null;

  return (
    <svg
      width="100%"
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ display: 'block' }}
    >
      <line
        x1={PAD} y1={entryY} x2={W - PAD} y2={entryY}
        stroke="#f0b90b" strokeWidth={1.5} strokeDasharray="5,4" opacity={0.9}
      />
      {chartData.map((c, i) => {
        const x = PAD + (i + 0.5) * (cW / chartData.length);
        const isUp = c.close >= c.open;
        const color = isUp ? '#0ecb81' : '#f6465d';
        const bTop = toY(Math.max(c.open, c.close));
        const bBot = toY(Math.min(c.open, c.close));
        const bH = Math.max(1, bBot - bTop);
        return (
          <g key={i}>
            <line x1={x} y1={toY(c.high)} x2={x} y2={toY(c.low)} stroke={color} strokeWidth={1} />
            <rect x={x - barW / 2} y={bTop} width={barW} height={bH} fill={color} />
          </g>
        );
      })}
      {curY !== null && (
        <line
          x1={PAD} y1={curY} x2={W - PAD} y2={curY}
          stroke="#e8e8e8" strokeWidth={1} strokeDasharray="2,5" opacity={0.6}
        />
      )}
    </svg>
  );
}

export function PositionChart({
  position,
  candles,
  showPopOut = true,
}: {
  position: Position;
  candles: Candle[];
  showPopOut?: boolean;
}) {
  const { display, tooltip } = useCurrency();
  const pnl = position.unrealized_pnl;
  const pos = pnl >= 0;
  const entryVal = position.entry_price * position.quantity;
  const currentVal = (position.current_price ?? position.entry_price) * position.quantity;
  const currentP = position.current_price ?? position.entry_price;

  const pctToSL = position.stop_loss_price
    ? Math.abs((currentP - position.stop_loss_price) / currentP * 100)
    : null;
  const pctToTP = position.take_profit_price
    ? Math.abs((position.take_profit_price - currentP) / currentP * 100)
    : null;

  const isBreakEven = position.take_profit_price !== null &&
    position.take_profit_price !== undefined &&
    Math.abs(position.take_profit_price - position.entry_price) / position.entry_price < 0.002;
  const nearSL = pctToSL !== null && pctToSL < 0.8;
  const nearTP = pctToTP !== null && pctToTP < 0.8;

  const nextAction = isBreakEven ? 'Break-Even' : nearSL ? 'Near SL' : nearTP ? 'Near TP' : 'Hold';
  const actionColor = isBreakEven ? '#f0b90b' : nearSL ? '#f6465d' : nearTP ? '#0ecb81' : '#555';
  const methodLabel = position.mode === 'FUTURES' ? `Futures ${position.leverage}x` : 'Spot';

  const potentialProfit = position.take_profit_price
    ? (position.take_profit_price - position.entry_price) * position.quantity * position.leverage
    : null;

  const handlePopOut = () => {
    const sym = encodeURIComponent(position.symbol);
    window.open(
      `/charts?symbol=${sym}`,
      `chart_${position.symbol}`,
      'width=920,height=680,resizable=yes,scrollbars=yes'
    );
  };

  return (
    <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm">{position.symbol}</span>
          <span
            className="text-xs px-1.5 py-0.5 rounded font-medium"
            style={{
              background: position.side === 'BUY' ? 'rgba(14,203,129,0.15)' : 'rgba(246,70,93,0.15)',
              color: position.side === 'BUY' ? '#0ecb81' : '#f6465d',
            }}
          >
            {position.side}
          </span>
          <span
            className="text-xs px-1.5 py-0.5 rounded font-medium"
            style={{
              background: position.mode === 'FUTURES' ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.10)',
              color: position.mode === 'FUTURES' ? '#f0b90b' : '#0ecb81',
            }}
          >
            {methodLabel}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-1.5 py-0.5 rounded font-semibold" style={{ background: 'rgba(132,142,156,0.1)', color: actionColor }}>
            {nextAction}
          </span>
          <span className="text-sm font-bold" style={{ color: pos ? '#0ecb81' : '#f6465d' }} title={tooltip(pnl)}>
            {pos ? '+' : ''}{display(pnl)}
          </span>
          {showPopOut && (
            <button
              onClick={handlePopOut}
              title="Open in separate window"
              className="p-1 rounded transition-colors"
              style={{ color: '#555', background: 'transparent' }}
              onMouseEnter={(e) => (e.currentTarget.style.color = '#848e9c')}
              onMouseLeave={(e) => (e.currentTarget.style.color = '#555')}
            >
              <ExternalLink size={13} />
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 mb-2 text-xs">
        <span>
          <span style={{ color: '#848e9c' }}>Entry </span>
          <span style={{ color: '#f0b90b', fontWeight: 600 }}>{fmtPrice(position.entry_price)}</span>
        </span>
        <span style={{ color: '#848e9c' }}>&rarr;</span>
        <span>
          <span style={{ color: '#848e9c' }}>Now </span>
          <span style={{ color: '#e8e8e8', fontWeight: 600 }}>{fmtPrice(position.current_price)}</span>
        </span>
        {potentialProfit !== null && (
          <span className="ml-auto" style={{ color: '#848e9c' }}>
            Target <span style={{ color: '#0ecb81', fontWeight: 600 }}>+{display(potentialProfit)}</span>
          </span>
        )}
      </div>

      <div className="flex gap-4 mb-3 text-xs">
        {pctToSL !== null && (
          <span style={{ color: '#848e9c' }}>
            SL <span style={{ color: pctToSL < 0.8 ? '#f6465d' : '#555' }}>{pctToSL.toFixed(2)}% away</span>
          </span>
        )}
        {pctToTP !== null && (
          <span style={{ color: '#848e9c' }}>
            TP <span style={{ color: pctToTP < 0.8 ? '#0ecb81' : '#555' }}>{pctToTP.toFixed(2)}% away</span>
          </span>
        )}
      </div>

      <MiniCandleChart
        candles={candles}
        entryPrice={position.entry_price}
        currentPrice={position.current_price}
        openedAt={position.opened_at}
      />

      <div className="flex justify-between text-xs mt-2.5" style={{ color: '#848e9c' }}>
        <span>
          Start <span style={{ color: '#e8e8e8' }} title={tooltip(entryVal)}>{display(entryVal)}</span>
        </span>
        <span>
          Now <span style={{ color: pos ? '#0ecb81' : '#f6465d' }} title={tooltip(currentVal)}>{display(currentVal)}</span>
        </span>
      </div>
    </div>
  );
}
