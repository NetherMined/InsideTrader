'use client';

import { useEffect, useRef, useState } from 'react';
import { ExternalLink, ZoomIn, ZoomOut } from 'lucide-react';
import { fmtPrice } from '@/lib/utils';
import { useCurrency } from '@/lib/currency';
import { api } from '@/lib/api';
import type { Position, Candle } from '@/lib/types';

type TF = '1m' | '15m' | '1h';
const TF_LIST: TF[] = ['1m', '15m', '1h'];
const TF_LIMIT: Record<TF, number> = { '1m': 200, '15m': 200, '1h': 200 };

function calcSMA(candles: Candle[], period: number): { time: number; value: number }[] {
  const result: { time: number; value: number }[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
    result.push({
      time: Math.floor(new Date(candles[i].open_time).getTime() / 1000),
      value: sum / period,
    });
  }
  return result;
}

export function PositionChart({
  position,
  candles: _passedCandles,
  showPopOut = true,
}: {
  position: Position;
  candles: Candle[];
  showPopOut?: boolean;
}) {
  const { display, tooltip } = useCurrency();
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const [tf, setTf] = useState<TF>('15m');
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .candles(position.symbol, tf, TF_LIMIT[tf])
      .then((data) => {
        if (!cancelled) setCandles([...data].reverse());
      })
      .catch(() => null)
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tf, position.symbol]);

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return;

    if (chartRef.current) {
      try {
        chartRef.current._ro?.disconnect();
        chartRef.current.remove();
      } catch {}
      chartRef.current = null;
    }

    import('lightweight-charts').then(({ createChart, LineStyle }) => {
      if (!containerRef.current) return;

      const chart = createChart(containerRef.current, {
        layout: {
          background: { color: '#0b0b0b' } as any,
          textColor: '#848e9c',
          fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
          fontSize: 11,
        },
        grid: {
          vertLines: { color: '#161616' },
          horzLines: { color: '#161616' },
        },
        crosshair: {
          vertLine: { color: '#444', labelBackgroundColor: '#1f1f1f' },
          horzLine: { color: '#444', labelBackgroundColor: '#1f1f1f' },
        },
        rightPriceScale: { borderColor: '#1f1f1f' },
        timeScale: {
          borderColor: '#1f1f1f',
          timeVisible: true,
          secondsVisible: tf === '1m',
          fixLeftEdge: false,
          fixRightEdge: false,
        },
        width: containerRef.current.clientWidth,
        height: 260,
        handleScroll: true,
        handleScale: true,
      });

      chartRef.current = chart;

      const candleSeries = chart.addCandlestickSeries({
        upColor: '#0ecb81',
        downColor: '#f6465d',
        borderVisible: false,
        wickUpColor: '#0ecb81',
        wickDownColor: '#f6465d',
      });

      candleSeries.setData(
        candles.map((c) => ({
          time: Math.floor(new Date(c.open_time).getTime() / 1000) as any,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))
      );

      candleSeries.createPriceLine({
        price: position.entry_price,
        color: '#f0b90b',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'Entry',
      });
      if (position.stop_loss_price) {
        candleSeries.createPriceLine({
          price: position.stop_loss_price,
          color: '#f6465d',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'SL',
        });
      }
      if (position.take_profit_price) {
        candleSeries.createPriceLine({
          price: position.take_profit_price,
          color: '#0ecb81',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'TP',
        });
      }

      const ma7 = chart.addLineSeries({
        color: '#f0b90b',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      ma7.setData(calcSMA(candles, 7) as any);

      const ma25 = chart.addLineSeries({
        color: '#e84393',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      ma25.setData(calcSMA(candles, 25) as any);

      const ma99 = chart.addLineSeries({
        color: '#6c5ce7',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      ma99.setData(calcSMA(candles, 99) as any);

      const volSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
      });
      chart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volSeries.setData(
        candles.map((c) => ({
          time: Math.floor(new Date(c.open_time).getTime() / 1000) as any,
          value: c.volume,
          color: c.close >= c.open ? 'rgba(14,203,129,0.35)' : 'rgba(246,70,93,0.35)',
        }))
      );

      chart.timeScale().fitContent();

      const ro = new ResizeObserver(() => {
        if (containerRef.current) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      });
      ro.observe(containerRef.current);
      chartRef.current._ro = ro;
    });

    return () => {
      if (chartRef.current) {
        try {
          chartRef.current._ro?.disconnect();
          chartRef.current.remove();
        } catch {}
        chartRef.current = null;
      }
    };
  }, [candles]);

  const zoomIn = () => {
    if (!chartRef.current) return;
    const ts = chartRef.current.timeScale();
    const r = ts.getVisibleLogicalRange();
    if (r) {
      const c = (r.from + r.to) / 2;
      const h = (r.to - r.from) / 4;
      ts.setVisibleLogicalRange({ from: c - h, to: c + h });
    }
  };

  const zoomOut = () => {
    if (!chartRef.current) return;
    const ts = chartRef.current.timeScale();
    const r = ts.getVisibleLogicalRange();
    if (r) {
      const c = (r.from + r.to) / 2;
      const h = r.to - r.from;
      ts.setVisibleLogicalRange({ from: c - h, to: c + h });
    }
  };

  const handlePopOut = () => {
    window.open(
      `/charts?symbol=${encodeURIComponent(position.symbol)}`,
      `chart_${position.symbol}`,
      'width=960,height=700,resizable=yes,scrollbars=yes'
    );
  };

  const pnl = position.unrealized_pnl;
  const isProfit = pnl >= 0;
  const curP = position.current_price ?? position.entry_price;
  const pctToSL = position.stop_loss_price
    ? Math.abs(((curP - position.stop_loss_price) / curP) * 100)
    : null;
  const pctToTP = position.take_profit_price
    ? Math.abs(((position.take_profit_price - curP) / curP) * 100)
    : null;

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: '#0b0b0b', border: '1px solid #1f1f1f' }}
    >
      <div className="px-3 pt-3 pb-2">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-2">
            <span className="font-bold text-sm">{position.symbol}</span>
            <span
              className="text-xs px-1.5 py-0.5 rounded font-medium"
              style={{
                background:
                  position.side === 'BUY'
                    ? 'rgba(14,203,129,0.15)'
                    : 'rgba(246,70,93,0.15)',
                color: position.side === 'BUY' ? '#0ecb81' : '#f6465d',
              }}
            >
              {position.side}
            </span>
            <span
              className="text-xs px-1.5 py-0.5 rounded font-medium"
              style={{
                background:
                  position.mode === 'FUTURES'
                    ? 'rgba(240,185,11,0.12)'
                    : 'rgba(14,203,129,0.10)',
                color: position.mode === 'FUTURES' ? '#f0b90b' : '#0ecb81',
              }}
            >
              {position.mode === 'FUTURES'
                ? `Futures ${position.leverage}x`
                : 'Spot'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="text-sm font-bold"
              style={{ color: isProfit ? '#0ecb81' : '#f6465d' }}
              title={tooltip(pnl)}
            >
              {isProfit ? '+' : ''}
              {display(pnl)}
            </span>
            {showPopOut && (
              <button
                onClick={handlePopOut}
                style={{
                  color: '#555',
                  background: 'transparent',
                  cursor: 'pointer',
                  border: 'none',
                  padding: 0,
                }}
                onMouseEnter={(e) => (e.currentTarget.style.color = '#848e9c')}
                onMouseLeave={(e) => (e.currentTarget.style.color = '#555')}
              >
                <ExternalLink size={13} />
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 text-xs">
            <span style={{ color: '#f0b90b' }}>MA7</span>
            <span style={{ color: '#e84393' }}>MA25</span>
            <span style={{ color: '#6c5ce7' }}>MA99</span>
          </div>
          <div className="flex items-center gap-0.5">
            {TF_LIST.map((t) => (
              <button
                key={t}
                onClick={() => setTf(t)}
                className="text-xs px-2 py-0.5 rounded"
                style={{
                  background: tf === t ? '#222' : 'transparent',
                  color: tf === t ? '#e8e8e8' : '#555',
                  border: tf === t ? '1px solid #333' : '1px solid transparent',
                  cursor: 'pointer',
                }}
              >
                {t}
              </button>
            ))}
            <div
              style={{
                width: 1,
                background: '#2a2a2a',
                height: 14,
                margin: '0 4px',
              }}
            />
            <button
              onClick={zoomIn}
              title="Zoom in"
              style={{
                color: '#555',
                padding: '2px 4px',
                background: 'transparent',
                cursor: 'pointer',
                border: 'none',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.color = '#848e9c')}
              onMouseLeave={(e) => (e.currentTarget.style.color = '#555')}
            >
              <ZoomIn size={12} />
            </button>
            <button
              onClick={zoomOut}
              title="Zoom out"
              style={{
                color: '#555',
                padding: '2px 4px',
                background: 'transparent',
                cursor: 'pointer',
                border: 'none',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.color = '#848e9c')}
              onMouseLeave={(e) => (e.currentTarget.style.color = '#555')}
            >
              <ZoomOut size={12} />
            </button>
          </div>
        </div>

        <div
          className="flex items-center gap-4 mt-1.5 text-xs"
          style={{ color: '#848e9c' }}
        >
          <span>
            Entry{' '}
            <span style={{ color: '#f0b90b', fontWeight: 600 }}>
              {fmtPrice(position.entry_price)}
            </span>
          </span>
          <span style={{ color: '#333' }}>→</span>
          <span>
            Now{' '}
            <span style={{ color: '#e8e8e8', fontWeight: 600 }}>
              {fmtPrice(curP)}
            </span>
          </span>
          {pctToSL !== null && (
            <span>
              SL{' '}
              <span style={{ color: pctToSL < 0.8 ? '#f6465d' : '#555' }}>
                {pctToSL.toFixed(2)}%
              </span>
            </span>
          )}
          {pctToTP !== null && (
            <span>
              TP{' '}
              <span style={{ color: pctToTP < 0.8 ? '#0ecb81' : '#555' }}>
                {pctToTP.toFixed(2)}%
              </span>
            </span>
          )}
        </div>
      </div>

      <div style={{ position: 'relative' }}>
        {loading && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              height: 260,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: '#0b0b0b',
              zIndex: 10,
            }}
          >
            <span className="text-xs" style={{ color: '#444' }}>
              Loading...
            </span>
          </div>
        )}
        <div ref={containerRef} style={{ width: '100%' }} />
      </div>
    </div>
  );
}

export { PositionChart as MiniCandleChart };
