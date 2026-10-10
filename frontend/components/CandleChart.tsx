'use client';

import { useEffect, useRef, useState } from 'react';
import type {
  IChartApi,
  IPriceLine,
  ISeriesApi,
  CandlestickData,
  HistogramData,
  LineData,
  UTCTimestamp,
} from 'lightweight-charts';
import { api } from '@/lib/api';
import type { Candle, Position } from '@/lib/types';

type TF = '1m' | '5m' | '15m' | '1h' | '4h' | '1d';
const TF_LIST: TF[] = ['1m', '5m', '15m', '1h', '4h', '1d'];
const INITIAL_LIMIT = 300;
const REFRESH_MS = 10000;
const REFRESH_LIMIT = 3;

const GREEN = '#0ecb81';
const RED = '#f6465d';
const GOLD = '#f0b90b';
const BLUE = '#4f8cff';

const MA_DEFS = [
  { period: 7, color: '#f0b90b', label: 'MA(7)' },
  { period: 25, color: '#ff6ec7', label: 'MA(25)' },
  { period: 99, color: '#8b7bff', label: 'MA(99)' },
] as const;
type MaSeries = Record<number, ISeriesApi<'Line'>>;

function smaSeries(candles: Candle[], period: number): LineData[] {
  const out: LineData[] = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;
    if (i >= period) sum -= candles[i - period].close;
    if (i >= period - 1) out.push({ time: toTime(candles[i].open_time), value: sum / period });
  }
  return out;
}

function latestSma(candles: Candle[], period: number): number | null {
  if (candles.length < period) return null;
  let sum = 0;
  for (let i = candles.length - period; i < candles.length; i++) sum += candles[i].close;
  return sum / period;
}

// Keeps the candle list and the MA lines in step with a new or updated last candle.
function pushCandle(candles: Candle[], series: MaSeries, c: Candle) {
  const last = candles[candles.length - 1];
  if (last && toTime(last.open_time) === toTime(c.open_time)) candles[candles.length - 1] = c;
  else candles.push(c);
  for (const { period } of MA_DEFS) {
    const value = latestSma(candles, period);
    if (value != null) series[period]?.update({ time: toTime(c.open_time), value });
  }
}

const toTime = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

const toCandle = (c: Candle): CandlestickData => ({
  time: toTime(c.open_time),
  open: c.open,
  high: c.high,
  low: c.low,
  close: c.close,
});

const toVolume = (c: Candle): HistogramData => ({
  time: toTime(c.open_time),
  value: c.volume,
  color: c.close >= c.open ? 'rgba(14,203,129,0.35)' : 'rgba(246,70,93,0.35)',
});

export function CandleChart({
  symbol,
  livePrice,
  positions,
  targetPrice,
}: {
  symbol: string;
  livePrice: number | null;
  positions: Position[];
  targetPrice: number | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const lastRef = useRef<Candle | null>(null);
  const maRef = useRef<MaSeries>({});
  const listRef = useRef<Candle[]>([]);
  const [tf, setTf] = useState<TF>('15m');
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [legend, setLegend] = useState<CandlestickData | null>(null);
  const [maOn, setMaOn] = useState<Record<number, boolean>>({ 7: true, 25: true, 99: true });
  const [maValues, setMaValues] = useState<Record<number, number | null>>({});

  useEffect(() => {
    let disposed = false;
    let ro: ResizeObserver | null = null;
    import('lightweight-charts').then(({ createChart, CrosshairMode }) => {
      if (disposed || !containerRef.current) return;
      const chart = createChart(containerRef.current, {
        layout: {
          background: { color: '#0b0b0b' } as never,
          textColor: '#848e9c',
          fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
          fontSize: 11,
        },
        grid: { vertLines: { color: '#161616' }, horzLines: { color: '#161616' } },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: '#555', labelBackgroundColor: '#1f1f1f' },
          horzLine: { color: '#555', labelBackgroundColor: '#1f1f1f' },
        },
        rightPriceScale: { borderColor: '#1f1f1f' },
        timeScale: { borderColor: '#1f1f1f', timeVisible: true, secondsVisible: false },
        width: containerRef.current.clientWidth,
        height: 340,
      });
      const candles = chart.addCandlestickSeries({
        upColor: GREEN,
        downColor: RED,
        borderVisible: false,
        wickUpColor: GREEN,
        wickDownColor: RED,
      });
      const volume = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      });
      chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      const maSeries: MaSeries = {};
      for (const { period, color } of MA_DEFS) {
        maSeries[period] = chart.addLineSeries({
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      }
      maRef.current = maSeries;
      chart.subscribeCrosshairMove((p) => {
        const d = p.seriesData.get(candles) as CandlestickData | undefined;
        setLegend(d ?? null);
        if (!d) return;
        const next: Record<number, number | null> = {};
        for (const { period } of MA_DEFS) {
          const v = p.seriesData.get(maSeries[period]) as LineData | undefined;
          next[period] = v ? (v.value as number) : null;
        }
        setMaValues(next);
      });
      chartRef.current = chart;
      candleRef.current = candles;
      volumeRef.current = volume;
      ro = new ResizeObserver(() => {
        if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
      });
      ro.observe(containerRef.current);
      setReady(true);
    });
    return () => {
      disposed = true;
      ro?.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      maRef.current = {};
      listRef.current = [];
      linesRef.current = [];
      setReady(false);
    };
  }, []);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setStatus('loading');
    lastRef.current = null;
    api
      .candles(symbol, tf, INITIAL_LIMIT)
      .then((data) => {
        if (cancelled || !candleRef.current || !volumeRef.current) return;
        const asc = [...data].reverse();
        candleRef.current.setData(asc.map(toCandle));
        volumeRef.current.setData(asc.map(toVolume));
        listRef.current = asc;
        for (const { period } of MA_DEFS) maRef.current[period]?.setData(smaSeries(asc, period));
        lastRef.current = asc[asc.length - 1] ?? null;
        chartRef.current?.timeScale().fitContent();
        setStatus(asc.length ? 'ok' : 'error');
      })
      .catch(() => {
        if (!cancelled) setStatus('error');
      });

    const id = setInterval(() => {
      api
        .candles(symbol, tf, REFRESH_LIMIT)
        .then((data) => {
          if (cancelled || !candleRef.current || !volumeRef.current) return;
          for (const c of [...data].reverse()) {
            candleRef.current.update(toCandle(c));
            volumeRef.current.update(toVolume(c));
            pushCandle(listRef.current, maRef.current, c);
            lastRef.current = c;
          }
        })
        .catch(() => null);
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [ready, symbol, tf]);

  useEffect(() => {
    const last = lastRef.current;
    if (!ready || !last || !livePrice || !candleRef.current) return;
    const updated: Candle = {
      ...last,
      close: livePrice,
      high: Math.max(last.high, livePrice),
      low: Math.min(last.low, livePrice),
    };
    candleRef.current.update(toCandle(updated));
    pushCandle(listRef.current, maRef.current, updated);
    lastRef.current = updated;
  }, [ready, livePrice]);

  useEffect(() => {
    if (!ready) return;
    for (const { period } of MA_DEFS) maRef.current[period]?.applyOptions({ visible: !!maOn[period] });
  }, [ready, maOn]);

  useEffect(() => {
    const series = candleRef.current;
    if (!ready || !series) return;
    for (const l of linesRef.current) series.removePriceLine(l);
    linesRef.current = [];
    const add = (price: number | null | undefined, color: string, title: string, dashed = true) => {
      if (!price) return;
      linesRef.current.push(
        series.createPriceLine({
          price,
          color,
          lineWidth: 1,
          lineStyle: dashed ? 2 : 0,
          axisLabelVisible: true,
          title,
        }),
      );
    };
    for (const p of positions) {
      add(p.entry_price, GOLD, `${p.side} entry`);
      add(p.stop_loss_price, RED, 'SL');
      add(p.take_profit_price, GREEN, 'TP');
    }
    add(targetPrice, BLUE, 'ML target');
  }, [ready, positions, targetPrice]);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div className="flex gap-1">
          {TF_LIST.map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className="px-2 py-1 rounded text-xs font-medium transition-colors"
              style={{
                background: tf === t ? '#1f1f1f' : 'transparent',
                color: tf === t ? '#e8e8e8' : '#848e9c',
              }}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          {MA_DEFS.map(({ period, color, label }) => {
            const value = legend ? maValues[period] : latestSma(listRef.current, period);
            return (
              <button
                key={period}
                onClick={() => setMaOn((m) => ({ ...m, [period]: !m[period] }))}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded transition-opacity"
                style={{ color, opacity: maOn[period] ? 1 : 0.35, border: `1px solid ${color}33` }}
                title={`${label} — click to ${maOn[period] ? 'hide' : 'show'}`}
              >
                <span>{label}</span>
                {value != null && maOn[period] && <span>{value.toPrecision(6)}</span>}
              </button>
            );
          })}
        </div>
        {legend && (
          <div className="text-[11px] flex gap-3" style={{ color: legend.close >= legend.open ? GREEN : RED }}>
            <span>O {legend.open}</span>
            <span>H {legend.high}</span>
            <span>L {legend.low}</span>
            <span>C {legend.close}</span>
          </div>
        )}
      </div>
      <div className="relative">
        <div ref={containerRef} className="w-full rounded overflow-hidden" style={{ height: 340 }} />
        {status !== 'ok' && (
          <div
            className="absolute inset-0 flex items-center justify-center text-xs pointer-events-none"
            style={{ color: '#848e9c' }}
          >
            {status === 'loading' ? 'Loading chart…' : 'No candle data for this timeframe.'}
          </div>
        )}
      </div>
    </div>
  );
}
