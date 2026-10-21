'use client';

import { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { C, Card, DataTable, axisTick, chartTooltip, int, n, num, plainPct, signed, tone, usd, type Col, type Row } from './ui';

const LABELS: Record<string, string> = {
  group: 'Group', n: 'Count', hit: 'Hit rate', vs_drift: 'vs drift', bps: 'Avg move (bps)', net_bps: 'Net (bps)',
  h1_bps: '1st half (bps)', h2_bps: '2nd half (bps)', win_pct: 'Win rate', avg_pnl_usd: 'Avg P&L',
  hit60m: 'Hit 1h', net60m: 'Net 1h', hit4h: 'Hit 4h', net4h: 'Net 4h', hit24h: 'Hit 24h', net24h: 'Net 24h',
  h1: '1st half', h2: '2nd half', avg_pnl: 'Avg P&L', win: 'Win rate', hit60: 'Hit 1h',
  quarters_pos: 'Positive quarters', worst_q: 'Worst quarter', best_q: 'Best quarter',
};
const SIGNED = /net|bps|vs_drift|avg_pnl|^h[12]$|worst_q|best_q/;

function cellText(k: string, v: Row[string]) {
  if (v == null) return '—';
  if (typeof v !== 'number') return String(v);
  if (k === 'n' || k === 'trades') return int(v);
  if (/^(hit|win|win_pct|hit60m|hit4h|hit24h|hit60)$/.test(k)) return plainPct(v);
  if (k === 'vs_drift') return signed(v, 1, ' pts');
  if (/avg_pnl/.test(k)) return usd(v);
  return signed(v, 1);
}

function Explorer({ rows }: { rows: Row[] }) {
  const tables = useMemo(() => [...new Set(rows.map((r) => String(r.table)))], [rows]);
  const [table, setTable] = useState(tables[0] ?? '');
  const current = rows.filter((r) => r.table === table);
  const keys = [...new Set(current.flatMap((r) => Object.keys(r)))]
    .filter((k) => !['table', 'row_id', 'rule', 'side', 'group'].includes(k) && current.some((r) => r[k] != null));
  const cols: Col<Row>[] = [
    { key: 'group', label: 'Group' },
    ...keys.map((k) => ({ key: k, label: LABELS[k] ?? k, render: (r: Row) => cellText(k, r[k]),
      color: SIGNED.test(k) ? (r: Row) => tone(r[k]) : undefined })),
  ];
  return (
    <>
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <label htmlFor={`sel-${tables[0]}`} className="text-xs" style={{ color: C.muted }}>Breakdown</label>
        <select id={`sel-${tables[0]}`} value={table} onChange={(e) => setTable(e.target.value)}
          className="px-2 py-1.5 rounded-lg text-xs outline-none max-w-full"
          style={{ background: '#0a0a0a', border: `1px solid ${C.border}`, color: C.text }}>
          {tables.map((t) => <option key={t} value={t}>{t.replace(/\s+\[.*\]$/, '')}</option>)}
        </select>
      </div>
      <DataTable<Row> rows={current} cols={cols} rowKey={(r) => String(r.row_id)} maxHeight={440} />
    </>
  );
}

export function BotTab({ sets }: { sets: Record<string, Row[]> }) {
  const daily = (sets.bot_daily ?? []).map((r) => ({ day: String(r.day), pnl: n(r, 'pnl_usd') ?? 0, trades: n(r, 'trades') ?? 0 }));
  return (
    <div className="space-y-4">
      <div className="grid gap-4 grid-cols-1 xl:grid-cols-2">
        <Card title="How the bot's trades ended (Oct 14–16)"
          note="Trailing stops banked small wins; stop-losses took bigger losses. Average move is the price move as a percent of entry.">
          <DataTable<Row> rows={sets.bot_exits ?? []} rowKey={(r) => String(r.exit_reason)} cols={[
            { key: 'exit_reason', label: 'Exit' },
            { key: 'trades', label: 'Trades', render: (r) => int(r.trades) },
            { key: 'avg_pnl_usd', label: 'Avg P&L', render: (r) => usd(r.avg_pnl_usd), color: (r) => tone(r.avg_pnl_usd) },
            { key: 'total_pnl_usd', label: 'Total P&L', render: (r) => usd(r.total_pnl_usd), color: (r) => tone(r.total_pnl_usd) },
            { key: 'avg_move_pct', label: 'Avg move', render: (r) => plainPct(r.avg_move_pct, 2) },
            { key: 'avg_minutes', label: 'Avg minutes', render: (r) => num(r.avg_minutes, 0) },
          ]} />
        </Card>
        <Card title="Bot P&L per day (launch to Oct 16)"
          note="Closed paper trades in USDT. The Oct 9–10 losses came before the accounting and exit fixes.">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={daily} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="day" tick={axisTick} tickLine={false} axisLine={false} minTickGap={24} tickFormatter={(v: string) => v.slice(5)} />
              <YAxis tick={axisTick} tickLine={false} axisLine={false} width={52} tickFormatter={(v: number) => usd(v)} />
              <Tooltip {...chartTooltip} formatter={(v: number, _k, p) => [`${usd(v)} · ${p.payload.trades} trades`, 'P&L']} />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]} maxBarSize={28} isAnimationActive={false}>
                {daily.map((d) => <Cell key={d.day} fill={d.pnl < 0 ? C.loss : C.profit} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <Card title="Exit rule replay (Phase 1)"
        note="The same 95 trades replayed on 1-minute candles under different trailing stops and stop distances. The replay assumes the worst order of events inside each candle, so totals are more negative than real results; compare rows with each other. Wider trails raise the average win but add stop-outs.">
        <DataTable<Row> rows={sets.exit_replay ?? []} rowKey={(r) => `${r.stop_scale}-${r.trail}`}
          highlight={(r) => r.stop_scale === 1 && String(r.trail).startsWith('Current')} cols={[
            { key: 'trail', label: 'Trailing stop' },
            { key: 'stop_scale', label: 'Stop distance', render: (r) => (r.stop_scale === 1 ? 'Current' : `${Math.round((n(r, 'stop_scale') ?? 0) * 100)}% of current`) },
            { key: 'net_usd', label: 'Net', render: (r) => usd(r.net_usd), color: (r) => tone(r.net_usd) },
            { key: 'win_pct', label: 'Win rate', render: (r) => plainPct(r.win_pct) },
            { key: 'avg_win_usd', label: 'Avg win', render: (r) => usd(r.avg_win_usd) },
            { key: 'avg_loss_usd', label: 'Avg loss', render: (r) => usd(r.avg_loss_usd) },
            { key: 'stop_outs', label: 'Stop-outs', render: (r) => int(r.stop_outs) },
          ]} />
      </Card>

      <Card title="Live trade replay: exits and filters (Phase 7)"
        note="The 185 paper trades from Oct 14–21 replayed with their real entries, stops, targets and sizes. The current rules reproduce 84% of the real exits. Filters only remove trades, so they lose fewer dollars just by trading less; compare net per trade. A change passes only if its net per trade beats the current rules in both halves of the week.">
        <DataTable<Row> rows={sets.trade_fixes_replay ?? []} rowKey={(r) => String(r.test)}
          highlight={(r) => r.test === 'A. Current rules'} cols={[
            { key: 'test', label: 'Change' },
            { key: 'trades', label: 'Trades', render: (r) => int(r.trades) },
            { key: 'net_usd', label: 'Net', render: (r) => usd(r.net_usd), color: (r) => tone(r.net_usd) },
            { key: 'per_trade_usd', label: 'Per trade', render: (r) => usd(r.per_trade_usd), color: (r) => tone(r.per_trade_usd) },
            { key: 'win_pct', label: 'Win rate', render: (r) => plainPct(r.win_pct) },
            { key: 'avg_win_usd', label: 'Avg win', render: (r) => usd(r.avg_win_usd) },
            { key: 'avg_loss_usd', label: 'Avg loss', render: (r) => usd(r.avg_loss_usd) },
            { key: 'first_half_per_trade', label: '1st half / trade', render: (r) => usd(r.first_half_per_trade), color: (r) => tone(r.first_half_per_trade) },
            { key: 'second_half_per_trade', label: '2nd half / trade', render: (r) => usd(r.second_half_per_trade), color: (r) => tone(r.second_half_per_trade) },
            { key: 'beats_control_both_halves', label: 'Passes', render: (r) => (r.beats_control_both_halves ? 'Yes' : 'No') },
          ]} />
      </Card>

      <Card title="Signal scan (Phase 3)"
        note="Each row is a group of researcher packets, ML predictions or real trades. Hit rate is how often price moved the predicted way; 'vs drift' removes the effect of the market simply falling. Net subtracts the 13 bps (0.13%) round-trip cost.">
        <Explorer rows={sets.signal_scan ?? []} />
      </Card>

      <Card title="BTC trend filter and longer holds (Phase 4)"
        note="The same signals over 1h, 4h and 24h, split by whether the trade went with BTC's trend, plus simple trend rules on a year of 1h candles for 440 coins. Net columns are bps after costs.">
        <Explorer rows={sets.horizon_scan ?? []} />
      </Card>
    </div>
  );
}
