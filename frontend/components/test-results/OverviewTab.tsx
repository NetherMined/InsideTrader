'use client';

import { useMemo } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { C, Card, DataTable, Stat, axisTick, chartTooltip, n, num, pct, plainPct, tone, usd, type Row } from './ui';

export const TREND = '1 TSMOM majors long/flat (vol-target)';

const VERDICTS: Record<string, string> = {
  '1 TSMOM majors long/flat (vol-target)': 'Robust edge',
  '1b TSMOM majors long/short (vol-target)': 'Weaker than long/flat',
  '2 Cash&carry BTC+ETH (enter APR>10%)': 'Safe, low return',
  '3 XS momentum top-20 long/short': 'Unstable',
  '3b XS momentum top-20 long-only': 'Fails',
  '4a TSMOM long/flat WITHOUT vol-target': 'Bigger drawdowns',
  '4b XS momentum long/short WITH vol-target': 'Fails',
  '5 BTC->alt lead-lag (rolling OLS, daily)': 'Fails',
  '5b BTC->alt catch-up rule (daily)': 'Fails',
  'ref Buy&hold 10 majors': 'Reference',
  'combo 1+2+3 equal-risk': 'Mostly carry',
};

const CURVES: { key: string; name: string; color: string; width: number }[] = [
  { key: TREND, name: 'Trend on majors', color: C.accent, width: 2.5 },
  { key: 'ref Buy&hold 10 majors', name: 'Buy and hold majors', color: '#5b8def', width: 1.5 },
  { key: '3 XS momentum top-20 long/short', name: 'Cross-coin momentum', color: '#5e6673', width: 1.5 },
  { key: '2 Cash&carry BTC+ETH (enter APR>10%)', name: 'Cash and carry', color: '#3d434c', width: 1.5 },
  { key: '5 BTC->alt lead-lag (rolling OLS, daily)', name: 'BTC→alt lead-lag', color: '#7a3b45', width: 1.5 },
];

type Ranked = { name: string; family: string; verdict: string; sharpe: number; ann: number | null; maxdd: number | null };

export function OverviewTab({ sets }: { sets: Record<string, Row[]> }) {
  const daily = sets.daily_strategies ?? [];
  const robust = sets.robustness ?? [];
  const exits = sets.bot_exits ?? [];
  const dt = sets.daytrade_setups ?? [];
  const trend = daily.find((r) => r.strategy === TREND);
  const trendRobust = robust.find((r) => r.strategy === TREND);
  const botTrades = exits.reduce((s, r) => s + (n(r, 'trades') ?? 0), 0);
  const botWins = exits.reduce((s, r) => s + (n(r, 'wins') ?? 0), 0);
  const botNet = exits.reduce((s, r) => s + (n(r, 'total_pnl_usd') ?? 0), 0);
  const dtRuns = dt.filter((r) => !String(r.setup).startsWith('0 RANDOM') && n(r, 'total') != null);
  const dtProfitable = dtRuns.filter((r) => (n(r, 'total') ?? 0) > 0).length;

  const curveData = useMemo(() => {
    const byWeek = new Map<string, Record<string, number | string>>();
    for (const r of sets.equity_curves ?? []) {
      const week = String(r.week);
      const row = byWeek.get(week) ?? { week };
      row[String(r.strategy)] = Number(r.growth);
      byWeek.set(week, row);
    }
    return [...byWeek.values()].sort((a, b) => String(a.week).localeCompare(String(b.week)));
  }, [sets.equity_curves]);

  const ranked: Ranked[] = useMemo(() => {
    const a = daily.map((r) => ({ name: String(r.strategy), family: 'Daily strategy', verdict: VERDICTS[String(r.strategy)] ?? '',
      sharpe: n(r, 'sharpe') ?? NaN, ann: n(r, 'ann'), maxdd: n(r, 'maxdd') }));
    const b = dt.filter((r) => /\[(fixed|mr)\]$/.test(String(r.setup)) && n(r, 'sharpe') != null)
      .map((r) => ({ name: String(r.setup), family: 'Day-trading setup',
        verdict: String(r.setup).startsWith('0 RANDOM') ? 'Benchmark' : 'Fails', sharpe: n(r, 'sharpe') ?? NaN, ann: n(r, 'ann'), maxdd: n(r, 'maxdd') }));
    return [...a, ...b].filter((r) => Number.isFinite(r.sharpe)).sort((x, y) => y.sharpe - x.sharpe);
  }, [daily, dt]);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Trend on 10 majors · yearly return 2021–2026" value={pct(trend?.ann)} color={tone(trend?.ann)}
          sub={<>Sharpe {num(trend?.sharpe)} · worst drawdown {pct(trend?.maxdd)}</>} />
        <Stat label="Trend on majors · settings that held up" value={plainPct(trendRobust?.pct_variants_sharpe_ge_0_5, 0)}
          sub="Share of tested settings with Sharpe 0.5 or better" />
        <Stat label="Current bot · Oct 14–16 (48h)" value={usd(botNet)} color={tone(botNet)}
          sub={<>Won {botTrades ? Math.round((botWins / botTrades) * 100) : 0}% of {botTrades} trades and still lost</>} />
        <Stat label="Day-trading setups · 2024–2026" value={`${dtProfitable} of ${dtRuns.length}`}
          color={dtProfitable ? undefined : C.loss} sub="Variants that ended profitable after costs" />
      </div>

      <Card title="Growth of $1, daily strategies (2021–2026)"
        note="Net of fees, slippage and funding. Log scale, so equal heights mean equal percentage moves. Trend on majors sidesteps most of the 2022 crash that buy-and-hold takes.">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={curveData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="week" tick={axisTick} tickLine={false} axisLine={false} minTickGap={40}
              tickFormatter={(v: string) => v.slice(0, 7)} />
            <YAxis scale="log" domain={['auto', 'auto']} allowDataOverflow tick={axisTick} tickLine={false} axisLine={false}
              width={44} tickFormatter={(v: number) => `$${v >= 10 ? v.toFixed(0) : v.toFixed(1)}`} />
            <Tooltip {...chartTooltip} formatter={(v: number, name: string) => [`$${v.toFixed(2)}`, name]} />
            <Legend wrapperStyle={{ fontSize: 11, color: C.muted }} />
            {CURVES.map((c) => (
              <Line key={c.key} dataKey={c.key} name={c.name} stroke={c.color} strokeWidth={c.width} dot={false}
                isAnimationActive={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <Card title="Every strategy tested, ranked by Sharpe ratio"
        note="Sharpe is return per unit of risk: above 1 is strong, below 0 loses money. Cash-and-carry ranks first because its returns are tiny but smooth. Daily strategies cover Mar 2021 – Oct 2026; day-trading setups cover Jan 2024 – Oct 2026.">
        <DataTable<Ranked> rows={ranked} rowKey={(r) => r.name} highlight={(r) => r.name === TREND} cols={[
          { key: 'name', label: 'Strategy' },
          { key: 'family', label: 'Type', left: true },
          { key: 'verdict', label: 'Verdict', left: true,
            color: (r) => (r.verdict === 'Robust edge' ? C.profit : r.verdict === 'Fails' ? C.loss : undefined) },
          { key: 'sharpe', label: 'Sharpe', render: (r) => num(r.sharpe), color: (r) => tone(r.sharpe) },
          { key: 'ann', label: 'Yearly return', render: (r) => pct(r.ann), color: (r) => tone(r.ann) },
          { key: 'maxdd', label: 'Worst drawdown', render: (r) => pct(r.maxdd) },
        ]} />
      </Card>
    </div>
  );
}
