'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { api } from '@/lib/api';
import { fmt, fmtPct, fmtPrice } from '@/lib/utils';
import { useCurrency } from '@/lib/currency';
import { CandleChart } from '@/components/CandleChart';
import type { Candle, SymbolOverview } from '@/lib/types';

type Side = 'BUY' | 'SELL';
type OpenState =
  | { phase: 'idle' }
  | { phase: 'confirm'; side: Side }
  | { phase: 'pending'; side: Side }
  | { phase: 'opened'; side: Side }
  | { phase: 'rejected'; reason: string };

const GREEN = '#0ecb81';
const RED = '#f6465d';
const MUTED = '#848e9c';
const POLL_MS = 1000;
const POLL_MAX = 20;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg p-3" style={{ background: '#0b0b0b', border: '1px solid #1f1f1f' }}>
      <div className="text-xs font-semibold mb-2" style={{ color: MUTED }}>{title}</div>
      {children}
    </div>
  );
}

export function SymbolModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const { display } = useCurrency();
  const [overview, setOverview] = useState<SymbolOverview | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [error, setError] = useState('');
  const [openState, setOpenState] = useState<OpenState>({ phase: 'idle' });
  const [flip, setFlip] = useState(false);
  const alive = useRef(true);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const load = useCallback(async () => {
    try {
      const [o, c] = await Promise.all([api.symbolOverview(symbol), api.candles(symbol, '1h', 24)]);
      if (!alive.current) return;
      setOverview(o);
      setCandles(c);
      setError('');
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : 'Failed to load');
    }
  }, [symbol]);

  useEffect(() => {
    alive.current = true;
    load();
    const id = setInterval(load, 5000);
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCloseRef.current();
    window.addEventListener('keydown', onKey);
    return () => {
      alive.current = false;
      clearInterval(id);
      window.removeEventListener('keydown', onKey);
    };
  }, [load]);

  const suggested = overview?.suggested_side ?? null;
  const side: Side | null = suggested ? (flip ? (suggested === 'BUY' ? 'SELL' : 'BUY') : suggested) : null;
  const hasOpen = (overview?.positions.length ?? 0) > 0;
  const busy = openState.phase === 'pending' || openState.phase === 'confirm';

  const submit = async (s: Side) => {
    setOpenState({ phase: 'pending', side: s });
    try {
      const { request_id } = await api.manualOpen(symbol, s);
      for (let i = 0; i < POLL_MAX; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (!alive.current) return;
        const r = await api.manualOpenResult(request_id);
        if (r.status === 'opened') {
          setOpenState({ phase: 'opened', side: s });
          load();
          return;
        }
        if (r.status === 'rejected') {
          setOpenState({ phase: 'rejected', reason: r.reason || 'Entry was rejected' });
          return;
        }
      }
      setOpenState({ phase: 'rejected', reason: 'No response from the bot. Is it running and started?' });
    } catch (e) {
      setOpenState({ phase: 'rejected', reason: e instanceof Error ? e.message : 'Request failed' });
    }
  };

  const pred = overview?.prediction;
  const predUp = (pred?.predicted_change_pct ?? 0) > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.75)' }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={`${symbol} details`}
        className="w-full max-w-4xl max-h-[90vh] overflow-y-auto rounded-xl p-5 space-y-3"
        style={{ background: '#111111', border: '1px solid #1f1f1f' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-3">
            <h2 className="text-lg font-bold">{symbol}</h2>
            {overview?.price && (
              <>
                <span className="text-lg">{fmtPrice(overview.price.price)}</span>
                <span style={{ color: overview.price.change_pct >= 0 ? GREEN : RED }}>
                  {fmtPct(overview.price.change_pct)}
                </span>
              </>
            )}
            {overview && (
              <span
                className="text-[10px] px-1.5 py-0.5 rounded font-semibold"
                style={{ background: overview.paper ? '#2b2410' : '#2a1215', color: overview.paper ? '#f0b90b' : RED }}
              >
                {overview.paper ? 'PAPER' : 'LIVE'}
              </span>
            )}
          </div>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded hover:bg-white/10">
            <X size={18} />
          </button>
        </div>

        {error && <div className="text-xs" style={{ color: RED }}>{error}</div>}

        <Section title="ML prediction (1h)">
          {pred ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <div>
                <div className="text-xs" style={{ color: MUTED }}>Direction</div>
                <div className="font-bold" style={{ color: predUp ? GREEN : RED }}>
                  {predUp ? 'BULLISH' : 'BEARISH'}
                </div>
              </div>
              <div>
                <div className="text-xs" style={{ color: MUTED }}>Predicted move</div>
                <div style={{ color: predUp ? GREEN : RED }}>{fmtPct(pred.predicted_change_pct)}</div>
              </div>
              <div>
                <div className="text-xs" style={{ color: MUTED }}>Target</div>
                <div>{fmtPrice(pred.target_price)}</div>
              </div>
              <div>
                <div className="text-xs" style={{ color: MUTED }}>Confidence</div>
                <div>{fmt(pred.confidence * 100, 0)}%</div>
              </div>
            </div>
          ) : (
            <div className="text-xs" style={{ color: MUTED }}>
              {overview ? 'No ML prediction for this symbol yet.' : 'Loading…'}
            </div>
          )}
        </Section>

        <Section title="Chart">
          <CandleChart
            symbol={symbol}
            livePrice={overview?.price?.price ?? null}
            positions={overview?.positions ?? []}
            targetPrice={overview?.prediction?.target_price ?? null}
          />
          <details className="mt-3">
            <summary className="text-xs cursor-pointer select-none" style={{ color: MUTED }}>
              Candle table (1h, last 24)
            </summary>
            <div className="overflow-x-auto max-h-56 overflow-y-auto mt-2">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: MUTED }} className="text-left">
                  <th className="pr-3 font-normal">Time</th>
                  <th className="pr-3 font-normal">Open</th>
                  <th className="pr-3 font-normal">High</th>
                  <th className="pr-3 font-normal">Low</th>
                  <th className="pr-3 font-normal">Close</th>
                  <th className="font-normal">Chg</th>
                </tr>
              </thead>
              <tbody>
                {candles.map((c) => {
                  const chg = c.open ? ((c.close - c.open) / c.open) * 100 : 0;
                  return (
                    <tr key={c.open_time}>
                      <td className="pr-3 py-0.5">
                        {new Date(c.open_time).toLocaleString('en-GB', {
                          day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
                        })}
                      </td>
                      <td className="pr-3">{fmtPrice(c.open)}</td>
                      <td className="pr-3">{fmtPrice(c.high)}</td>
                      <td className="pr-3">{fmtPrice(c.low)}</td>
                      <td className="pr-3">{fmtPrice(c.close)}</td>
                      <td style={{ color: chg >= 0 ? GREEN : RED }}>{fmtPct(chg)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {candles.length === 0 && <div className="text-xs" style={{ color: MUTED }}>No candle data.</div>}
            </div>
          </details>
        </Section>

        <Section title="Trading activity">
          {overview && overview.positions.length > 0 && (
            <div className="mb-2 space-y-1">
              {overview.positions.map((p) => (
                <div key={p.id} className="flex flex-wrap justify-between gap-2 text-xs">
                  <span className="font-semibold" style={{ color: p.side === 'BUY' ? GREEN : RED }}>
                    OPEN {p.side} x{p.leverage}
                  </span>
                  <span>Entry {fmtPrice(p.entry_price)}</span>
                  <span>SL {fmtPrice(p.stop_loss_price)}</span>
                  <span>TP {fmtPrice(p.take_profit_price)}</span>
                  <span style={{ color: p.unrealized_pnl >= 0 ? GREEN : RED }}>{display(p.unrealized_pnl)}</span>
                </div>
              ))}
            </div>
          )}
          {overview && overview.trades.length > 0 ? (
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {overview.trades.map((t) => (
                <div key={t.id} className="flex flex-wrap justify-between gap-2 text-xs">
                  <span style={{ color: t.side === 'BUY' ? GREEN : RED }}>{t.side}</span>
                  <span style={{ color: MUTED }}>{t.status}</span>
                  <span>{fmtPrice(t.entry_price)} → {t.exit_price ? fmtPrice(t.exit_price) : '—'}</span>
                  <span style={{ color: (t.pnl_usdt ?? 0) >= 0 ? GREEN : RED }}>
                    {t.pnl_usdt != null ? display(t.pnl_usdt) : '—'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs" style={{ color: MUTED }}>No trades for this symbol yet.</div>
          )}
        </Section>

        <Section title="Open position from ML prediction">
          {openState.phase === 'opened' && (
            <div className="text-sm mb-2" style={{ color: GREEN }}>{openState.side} {symbol} opened.</div>
          )}
          {openState.phase === 'rejected' && (
            <div className="text-sm mb-2" style={{ color: RED }}>Not opened: {openState.reason}</div>
          )}
          {openState.phase === 'pending' && (
            <div className="text-sm" style={{ color: MUTED }}>Waiting for the bot to open {openState.side}…</div>
          )}
          {openState.phase === 'confirm' && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>
                Open {openState.side} {symbol} {overview?.paper ? '(paper)' : 'with REAL funds'}?
              </span>
              <button
                onClick={() => submit(openState.side)}
                className="px-3 py-1.5 rounded font-semibold text-black"
                style={{ background: openState.side === 'BUY' ? GREEN : RED }}
              >
                Confirm
              </button>
              <button onClick={() => setOpenState({ phase: 'idle' })} className="px-3 py-1.5 rounded border border-white/20">
                Cancel
              </button>
            </div>
          )}
          {!busy && (
            <div className="flex flex-wrap items-center gap-3">
              {side ? (
                <button
                  disabled={hasOpen || openState.phase === 'opened'}
                  onClick={() => setOpenState({ phase: 'confirm', side })}
                  className="px-4 py-2 rounded font-semibold text-black disabled:opacity-40"
                  style={{ background: side === 'BUY' ? GREEN : RED }}
                >
                  Open {side} ({flip ? 'opposite of ML' : 'ML suggested'})
                </button>
              ) : (
                <span className="text-xs" style={{ color: MUTED }}>No ML signal to follow.</span>
              )}
              {side && (
                <label className="text-xs flex items-center gap-1.5 cursor-pointer" style={{ color: MUTED }}>
                  <input type="checkbox" checked={flip} onChange={(e) => setFlip(e.target.checked)} />
                  Take the opposite side
                </label>
              )}
              {hasOpen && (
                <span className="text-xs" style={{ color: MUTED }}>This symbol already has an open position.</span>
              )}
            </div>
          )}
          <div className="text-[11px] mt-2" style={{ color: MUTED }}>
            Manual entries bypass the confidence and alignment gates. Sizing, stop-loss and risk limits still apply.
          </div>
        </Section>
      </div>
    </div>
  );
}
