'use client';

import { Card, DataTable, int, num, pct, plainPct, tone, type Col, type Row } from './ui';
import { TREND } from './OverviewTab';

const YEARS = ['2021', '2022', '2023', '2024', '2025', '2026'];

export function StrategiesTab({ sets }: { sets: Record<string, Row[]> }) {
  const yearCols: Col<Row>[] = YEARS.map((y) => ({ key: y, label: y, render: (r) => pct(r[y]), color: (r) => tone(r[y]) }));
  return (
    <div className="space-y-4">
      <Card title="Five shortlisted strategies, head to head"
        note="Daily Binance perpetual data, Mar 2021 – Oct 2026, net of fees, slippage and funding. Year columns are returns in percent. 'Sharpe at 3× costs' re-runs the strategy with triple trading costs.">
        <DataTable<Row> rows={sets.daily_strategies ?? []} rowKey={(r) => String(r.strategy)} highlight={(r) => r.strategy === TREND} cols={[
          { key: 'strategy', label: 'Strategy' },
          { key: 'ann', label: 'Yearly', render: (r) => pct(r.ann), color: (r) => tone(r.ann) },
          { key: 'sharpe', label: 'Sharpe', render: (r) => num(r.sharpe), color: (r) => tone(r.sharpe) },
          { key: 'maxdd', label: 'Worst DD', render: (r) => pct(r.maxdd) },
          { key: 'sh_pre24', label: 'Sharpe pre-2024', render: (r) => num(r.sh_pre24) },
          { key: 'sh_post24', label: 'Sharpe since 2024', render: (r) => num(r.sh_post24) },
          ...yearCols,
          { key: 'sharpe_cost_x3', label: 'Sharpe at 3× costs', render: (r) => num(r.sharpe_cost_x3) },
          { key: 'trades_yr', label: 'Trades / yr', render: (r) => int(r.trades_yr) },
          { key: 'min_capital', label: 'Min capital' },
        ]} />
      </Card>
      <Card title="Does it survive different settings?"
        note="Each strategy re-run across a grid of lookbacks, holding periods and thresholds. A robust strategy keeps a decent Sharpe in most settings instead of one lucky one.">
        <DataTable<Row> rows={sets.robustness ?? []} rowKey={(r) => String(r.strategy)} highlight={(r) => r.strategy === TREND} cols={[
          { key: 'strategy', label: 'Strategy' },
          { key: 'variants', label: 'Settings tested', render: (r) => int(r.variants) },
          { key: 'median_sharpe', label: 'Median Sharpe', render: (r) => num(r.median_sharpe), color: (r) => tone(r.median_sharpe) },
          { key: 'pct_variants_sharpe_ge_0_5', label: 'Settings with Sharpe 0.5+', render: (r) => plainPct(r.pct_variants_sharpe_ge_0_5, 0) },
        ]} />
      </Card>
    </div>
  );
}
