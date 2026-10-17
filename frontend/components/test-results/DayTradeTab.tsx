'use client';

import { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { C, Card, DataTable, axisTick, chartTooltip, int, n, num, pct, plainPct, signed, tone, type Col, type Row } from './ui';

const yearCols: Col<Row>[] = ['y2024', 'y2025', 'y2026'].map((k) => ({
  key: k, label: k.slice(1), render: (r: Row) => pct(r[k]), color: (r: Row) => tone(r[k]),
}));

export function DayTradeTab({ rows }: { rows: Row[] }) {
  const [q, setQ] = useState('');
  const chart = useMemo(() => rows
    .filter((r) => /\[(fixed|mr)\]$/.test(String(r.setup)) && n(r, 'gross_r') != null)
    .map((r) => ({ name: String(r.setup).replace(/ \[(fixed|mr)\]$/, '').replace('0 RANDOM entries (benchmark)', 'Random entries (benchmark)'),
      gross: n(r, 'gross_r'), net: n(r, 'net_r') }))
    .sort((a, b) => (b.gross ?? 0) - (a.gross ?? 0)), [rows]);
  const filtered = rows.filter((r) => !q || String(r.setup).toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="space-y-4">
      <Card title="Edge before and after costs, per setup"
        note="Average result per trade in R (R = the amount risked). Before costs every setup sits near zero, like random entries; after fees, slippage and funding they all lose. Fixed 2R exits, 1% risk, BTC/ETH/SOL 5-minute candles.">
        <ResponsiveContainer width="100%" height={Math.max(320, chart.length * 30)}>
          <BarChart data={chart} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 0 }} barGap={1}>
            <CartesianGrid stroke={C.grid} horizontal={false} />
            <XAxis type="number" tick={axisTick} tickLine={false} axisLine={false} tickFormatter={(v: number) => `${signed(v, 1)}R`} />
            <YAxis type="category" dataKey="name" tick={{ ...axisTick, fontSize: 10 }} width={230} tickLine={false} axisLine={false} interval={0} />
            <Tooltip {...chartTooltip} formatter={(v: number, name: string) => [`${signed(v, 2)}R`, name]} />
            <Legend wrapperStyle={{ fontSize: 11, color: C.muted }} />
            <ReferenceLine x={0} stroke={C.muted} strokeDasharray="4 4" />
            <Bar dataKey="gross" name="Before costs" fill={C.accent} radius={[0, 3, 3, 0]} maxBarSize={10} isAnimationActive={false} />
            <Bar dataKey="net" name="After costs" fill={C.loss} radius={[0, 3, 3, 0]} maxBarSize={10} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card title="All day-trading runs"
        note="Every run: 1% risk (0.5% where marked), hard stop, 5× total exposure cap, 3R daily loss limit, 07:00–20:00 UTC only and flat at the close, funding charged at 08:00 and 16:00 UTC, $450 start. Exits: fixed = 2R target; be = breakeven at 1R; partial = half off at 1R and the rest to 3R; mr = target the VWAP.">
        <div className="mb-3">
          <label htmlFor="q-dt" className="sr-only">Search runs</label>
          <input id="q-dt" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search, e.g. VWAP, rulebook, partial"
            className="px-3 py-1.5 rounded-lg text-xs outline-none w-full sm:w-72"
            style={{ background: '#0a0a0a', border: `1px solid ${C.border}`, color: C.text }} />
        </div>
        <DataTable<Row> rows={filtered} rowKey={(r) => String(r.setup)} highlight={(r) => String(r.setup).startsWith('0 RANDOM')} cols={[
          { key: 'setup', label: 'Setup and exit' },
          { key: 'trades', label: 'Trades', render: (r) => int(r.trades) },
          { key: 'per_yr', label: 'Per year', render: (r) => int(r.per_yr) },
          { key: 'win', label: 'Win rate', render: (r) => plainPct(r.win) },
          { key: 'gross_r', label: 'R before costs', render: (r) => signed(r.gross_r), color: (r) => tone(r.gross_r) },
          { key: 'net_r', label: 'R after costs', render: (r) => signed(r.net_r), color: (r) => tone(r.net_r) },
          { key: 'pf', label: 'Profit factor', render: (r) => num(r.pf) },
          { key: 'total', label: 'Total return', render: (r) => pct(r.total), color: (r) => tone(r.total) },
          { key: 'maxdd', label: 'Worst DD', render: (r) => pct(r.maxdd) },
          { key: 'sharpe', label: 'Sharpe', render: (r) => num(r.sharpe), color: (r) => tone(r.sharpe) },
          ...yearCols,
        ]} />
      </Card>
    </div>
  );
}
