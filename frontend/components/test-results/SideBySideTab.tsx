'use client';

import { useMemo } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { Comparison, TrendHolding } from '@/lib/types';
import { fmtPrice } from '@/lib/utils';
import { C, Card, DataTable, Stat, axisTick, chartTooltip, int, plainPct, signed, tone, usd, type Row } from './ui';
import { PhaseCard } from './HistoryTab';

const COLORS: Record<string, string> = { 'Trend strategy': C.accent, 'Current bot': '#5b8def' };

export function SideBySideTab({ data, error, plan }: { data: Comparison | null; error: string | null; plan?: Row }) {
  const chart = useMemo(() => {
    const byDay = new Map<string, Record<string, number | string>>();
    for (const p of data?.daily ?? []) {
      const row = byDay.get(p.day) ?? { day: p.day };
      row[p.strategy] = p.equity;
      byDay.set(p.day, row);
    }
    return [...byDay.values()].sort((a, b) => String(a.day).localeCompare(String(b.day)));
  }, [data]);

  if (error && !data) return <p className="text-sm" style={{ color: C.loss }}>Couldn&apos;t load the live comparison: {error}</p>;
  if (!data) return <p className="text-sm" style={{ color: C.muted }}>Loading the live comparison…</p>;
  if (!data.running) {
    return (
      <div className="space-y-4">
        <p className="text-sm" style={{ color: C.muted }}>The trend strategy isn&apos;t running yet, so there is nothing to compare.</p>
        {plan && <PhaseCard phase={plan} />}
      </div>
    );
  }

  const asOf = data.as_of ? new Date(data.as_of).toLocaleString() : '—';
  const started = data.started_at ? new Date(data.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—';

  return (
    <div className="space-y-4">
      <p className="text-xs" style={{ color: C.muted }}>
        Both bots started from {usd(data.start_capital)} on {started}. Valued at live prices as of {asOf}. Refreshes every minute.
      </p>
      {data.halted && (
        <div className="rounded-xl p-3 text-sm" style={{ border: `1px solid ${C.loss}`, color: C.loss }}>
          Trend strategy stopped by its kill switch: {data.halted_reason}
        </div>
      )}
      <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
        {data.summary.map((s) => (
          <Stat key={s.strategy} label={`${s.strategy} · equity now`} value={usd(s.equity_now)}
            sub={<>
              <span style={{ color: tone(s.return_pct) }}>{signed(s.return_pct, 2, '%')}</span> since start · worst drawdown {signed(s.max_drawdown_pct, 2, '%')}
              <br />{int(s.trades)} trades · {int(s.open_positions)} open now
            </>} />
        ))}
        <Stat label="Test progress" value={`Day ${data.days}`} sub="Of a 6 to 8 week test. The trend strategy rebalances every 5 days." />
      </div>

      <Card title="Balance per day"
        note="One snapshot per UTC day, with today's value live. The trend strategy holds between rebalances, so expect slow moves.">
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={chart} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="day" tick={axisTick} tickLine={false} axisLine={false} tickFormatter={(v: string) => v.slice(5)} />
            <YAxis domain={['auto', 'auto']} tick={axisTick} tickLine={false} axisLine={false} width={60} tickFormatter={(v: number) => usd(v)} />
            <Tooltip {...chartTooltip} formatter={(v: number, name: string) => [usd(v), name]} />
            <Legend wrapperStyle={{ fontSize: 11, color: C.muted }} />
            {Object.entries(COLORS).map(([name, color]) => (
              <Line key={name} dataKey={name} name={name} stroke={color} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <Card title="Trend strategy holdings"
        note="Open longs at live prices. Weight is the share of the trend strategy's equity; the stop is the −40% emergency exit.">
        {data.holdings.length === 0
          ? <p className="text-xs" style={{ color: C.muted }}>No open positions: every coin is below its 28-day level, so the strategy is in cash.</p>
          : <DataTable<TrendHolding> rows={data.holdings} rowKey={(r) => r.symbol} defaultSort={{ key: 'weight_pct', dir: 'desc' }} cols={[
              { key: 'symbol', label: 'Coin' },
              { key: 'weight_pct', label: 'Weight', render: (r) => plainPct(r.weight_pct) },
              { key: 'value_usd', label: 'Value', render: (r) => usd(r.value_usd) },
              { key: 'entry_price', label: 'Entry', render: (r) => fmtPrice(r.entry_price) },
              { key: 'price', label: 'Price now', render: (r) => fmtPrice(r.price) },
              { key: 'pnl_pct', label: 'P&L', render: (r) => signed(r.pnl_pct, 2, '%'), color: (r) => tone(r.pnl_pct) },
              { key: 'stop_price', label: 'Stop', render: (r) => fmtPrice(r.stop_price) },
              { key: 'opened', label: 'Opened' },
            ]} />}
      </Card>

      {plan && <PhaseCard phase={plan} />}
    </div>
  );
}
