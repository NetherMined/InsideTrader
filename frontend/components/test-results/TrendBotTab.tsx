'use client';

import { useEffect, useState } from 'react';
import type { TrendDetail, TrendHolding } from '@/lib/types';
import { fmtPrice } from '@/lib/utils';
import { C, Card, DataTable, Pill, Stat, int, num, plainPct, signed, tone, usd } from './ui';

type Fill = TrendDetail['fills'][number];
type Rebalance = TrendDetail['rebalances'][number];
type FundingSym = TrendDetail['funding_by_symbol'][number];
type FundingRow = TrendDetail['funding_recent'][number];

const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const day = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
const coin = (s: string) => s.replace(/USDT$/, '');
const smallUsd = (v: number, d: number) => {
  const r = Number(v.toFixed(d));
  return `${r < 0 ? '−' : ''}$${num(Math.abs(r), d)}`;
};
const REASON_COLOR: Record<string, string> = { stop: C.loss, kill: C.loss, initial: C.accent };

function countdown(to: string, now: number) {
  const ms = new Date(to).getTime() - now;
  if (ms <= 0) return 'due now';
  const h = Math.floor(ms / 3_600_000), m = Math.floor((ms % 3_600_000) / 60_000);
  return h >= 24 ? `in ${Math.floor(h / 24)}d ${h % 24}h` : `in ${h}h ${m}m`;
}

export function TrendBotTab({ data, error }: { data: TrendDetail | null; error: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  if (error && !data) return <p className="text-sm" style={{ color: C.loss }}>Couldn&apos;t load the trend bot: {error}</p>;
  if (!data) return <p className="text-sm" style={{ color: C.muted }}>Loading the trend bot…</p>;
  if (!data.running || !data.status) {
    return <p className="text-sm" style={{ color: C.muted }}>The trend bot hasn&apos;t started yet. Start the <code>trend</code> service to begin paper trading.</p>;
  }
  const s = data.status;
  const ddUsed = Math.min(100, Math.max(0, (s.drawdown_pct / s.kill_drawdown_pct) * 100));
  const longs = data.targets.filter((t) => t.signal === 'long').length;

  return (
    <div className="space-y-4">
      <p className="text-xs" style={{ color: C.muted }}>
        Weekly trend-following on the 10 major coins, paper trading since {when(s.started_at)}. Every 5 days it holds each coin
        whose 28-day return is positive, sized by volatility, and stays in cash for the rest. Last checked {when(s.last_cycle)}; refreshes every minute.
      </p>
      {s.state === 'halted' && (
        <div className="rounded-xl p-3 text-sm" style={{ border: `1px solid ${C.loss}`, color: C.loss }}>
          Stopped by the kill switch: {s.halted_reason}. Restart with <code>python -m bot.trend.runner --resume</code>.
        </div>
      )}

      <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Equity" value={usd(s.equity)}
          sub={<><span style={{ color: tone(s.return_pct) }}>{signed(s.return_pct, 2, '%')}</span> since start ({usd(s.start_capital)}) · <Pill color={s.state === 'running' ? C.profit : C.loss}>{s.state === 'running' ? 'Running' : 'Halted'}</Pill></>} />
        <Stat label="Invested vs cash" value={plainPct(s.invested_pct)}
          sub={<>{usd(s.invested)} in {data.holdings.length} coins · {usd(s.cash)} cash</>} />
        <div className="rounded-xl p-4 min-w-0" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
          <div className="text-xs" style={{ color: C.muted }}>Drawdown from peak</div>
          <div className="text-2xl font-semibold mt-1 tabular-nums" style={{ color: s.drawdown_pct < 0 ? C.loss : undefined }}>{signed(s.drawdown_pct, 2, '%')}</div>
          <div className="h-1.5 rounded-full mt-2 overflow-hidden" style={{ background: C.border }}
            role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(ddUsed)} aria-label="Share of the kill-switch limit used">
            <div className="h-full rounded-full" style={{ width: `${ddUsed}%`, background: ddUsed > 66 ? C.loss : ddUsed > 33 ? C.accent : C.profit }} />
          </div>
          <div className="text-xs mt-1" style={{ color: C.muted }}>Kill switch at {num(s.kill_drawdown_pct, 0)}% ({usd(s.kill_equity)}) · peak {usd(s.peak_equity)}</div>
        </div>
        <Stat label="Next rebalance" value={data.next_rebalance ? countdown(data.next_rebalance.runs_at, now) : '—'}
          sub={data.next_rebalance ? <>Uses the {day(data.next_rebalance.signal_day)} close · runs {when(data.next_rebalance.runs_at)}</> : undefined} />
      </div>

      <Card title={`Signal now: ${longs} of 10 coins long`}
        note={`From the ${data.last_signal_day ? day(data.last_signal_day) : 'latest'} close. Target is the weight the strategy set at that rebalance; held is the weight now, after price moves. Flat coins sit in cash until their 28-day trend turns up.`}>
        <div className="grid gap-2 grid-cols-2 sm:grid-cols-5">
          {data.targets.map((t) => (
            <div key={t.symbol} className="rounded-lg p-3"
              style={{ border: `1px solid ${t.signal === 'long' ? 'rgba(14,203,129,0.35)' : C.border}`, background: '#0d0d0d' }}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold">{coin(t.symbol)}</span>
                <Pill color={t.signal === 'long' ? C.profit : C.muted}>{t.signal === 'long' ? 'Long' : 'Flat'}</Pill>
              </div>
              <div className="text-xs mt-2 tabular-nums" style={{ color: C.muted }}>
                Target {plainPct(t.target_pct)} · held {plainPct(t.held_pct)}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Holdings" note="Open longs at live prices. The stop is the −40% emergency exit.">
        {data.holdings.length === 0
          ? <p className="text-xs" style={{ color: C.muted }}>No open positions: every coin is flat, so the strategy is fully in cash.</p>
          : <DataTable<TrendHolding> rows={data.holdings} rowKey={(r) => r.symbol} defaultSort={{ key: 'weight_pct', dir: 'desc' }} cols={[
              { key: 'symbol', label: 'Coin', render: (r) => coin(r.symbol) },
              { key: 'weight_pct', label: 'Weight', render: (r) => plainPct(r.weight_pct) },
              { key: 'value_usd', label: 'Value', render: (r) => usd(r.value_usd) },
              { key: 'qty', label: 'Quantity', render: (r) => num(r.qty, r.qty < 1 ? 5 : 3) },
              { key: 'entry_price', label: 'Entry', render: (r) => fmtPrice(r.entry_price) },
              { key: 'price', label: 'Price now', render: (r) => fmtPrice(r.price) },
              { key: 'pnl_pct', label: 'P&L', render: (r) => signed(r.pnl_pct, 2, '%'), color: (r) => tone(r.pnl_pct) },
              { key: 'stop_price', label: 'Stop', render: (r) => fmtPrice(r.stop_price) },
              { key: 'opened', label: 'Opened' },
            ]} />}
      </Card>

      <div className="grid gap-4 grid-cols-1 xl:grid-cols-2">
        <Card title="Rebalances" note="Each run uses the daily close of its signal day and executes at 00:05 UTC the next day.">
          <DataTable<Rebalance> rows={data.rebalances} rowKey={(r) => r.signal_day} cols={[
            { key: 'signal_day', label: 'Signal close', render: (r) => day(r.signal_day) },
            { key: 'executed_at', label: 'Ran at', render: (r) => when(r.executed_at) },
            { key: 'equity_before', label: 'Equity before', render: (r) => usd(r.equity_before) },
            { key: 'orders', label: 'Orders', render: (r) => int(r.orders) },
          ]} />
        </Card>
        <Card title="Funding by coin" note={`Paid (+) or received (−) on open longs at each funding time. Total ${usd(s.funding_paid)}.`}>
          {data.funding_by_symbol.length === 0
            ? <p className="text-xs" style={{ color: C.muted }}>No funding charged yet.</p>
            : <DataTable<FundingSym> rows={data.funding_by_symbol} rowKey={(r) => r.symbol} cols={[
                { key: 'symbol', label: 'Coin', render: (r) => coin(r.symbol) },
                { key: 'payments', label: 'Payments', render: (r) => int(r.payments) },
                { key: 'amount_usdt', label: 'Net paid', render: (r) => smallUsd(r.amount_usdt, 4),
                  color: (r) => tone(-r.amount_usdt) },
              ]} />}
        </Card>
      </div>

      <Card title="Fills" note="Every simulated order, newest first. Prices include 0.03% slippage (0.08% on stop exits); the fee is the 0.05% taker fee.">
        <DataTable<Fill> rows={data.fills} rowKey={(r, i) => `${r.time}-${r.symbol}-${i}`} cols={[
          { key: 'time', label: 'Time', render: (r) => when(r.time), sort: (r) => r.time },
          { key: 'symbol', label: 'Coin', render: (r) => coin(r.symbol), left: true },
          { key: 'side', label: 'Side', color: (r) => (r.side === 'BUY' ? C.profit : C.loss) },
          { key: 'reason', label: 'Reason', color: (r) => REASON_COLOR[r.reason] },
          { key: 'qty', label: 'Quantity', render: (r) => num(r.qty, r.qty < 1 ? 5 : 3) },
          { key: 'fill_price', label: 'Price', render: (r) => fmtPrice(r.fill_price) },
          { key: 'notional', label: 'Value', render: (r) => usd(r.notional) },
          { key: 'fee_usdt', label: 'Fee', render: (r) => `$${num(r.fee_usdt, 4)}` },
        ]} />
      </Card>

      {data.funding_recent.length > 0 && (
        <Card title="Latest funding charges" note="Funding settles every 8 hours (00:00, 08:00, 16:00 UTC) on Binance perpetuals.">
          <DataTable<FundingRow> rows={data.funding_recent} rowKey={(r) => `${r.time}-${r.symbol}`} maxHeight={320} cols={[
            { key: 'time', label: 'Time', render: (r) => when(r.time), sort: (r) => r.time },
            { key: 'symbol', label: 'Coin', render: (r) => coin(r.symbol), left: true },
            { key: 'rate', label: 'Rate', render: (r) => signed(r.rate * 100, 4, '%') },
            { key: 'amount_usdt', label: 'Paid', render: (r) => smallUsd(r.amount_usdt, 5),
              color: (r) => tone(-r.amount_usdt) },
          ]} />
        </Card>
      )}
    </div>
  );
}
