'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { Comparison, TrendDetail } from '@/lib/types';
import { C, type Row } from '@/components/test-results/ui';
import { OverviewTab } from '@/components/test-results/OverviewTab';
import { HistoryTab } from '@/components/test-results/HistoryTab';
import { BotTab } from '@/components/test-results/BotTab';
import { StrategiesTab } from '@/components/test-results/StrategiesTab';
import { DayTradeTab } from '@/components/test-results/DayTradeTab';
import { SideBySideTab } from '@/components/test-results/SideBySideTab';
import { TrendBotTab } from '@/components/test-results/TrendBotTab';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'history', label: 'Testing history' },
  { id: 'bot', label: 'Current bot' },
  { id: 'strategies', label: 'Strategy research' },
  { id: 'daytrade', label: 'Day-trading setups' },
  { id: 'side-by-side', label: 'Side-by-side test' },
  { id: 'trend-bot', label: 'Trend bot' },
] as const;
type TabId = (typeof TABS)[number]['id'];

const SETS = ['phases', 'bot_exits', 'bot_daily', 'exit_replay', 'signal_scan', 'horizon_scan',
  'daily_strategies', 'robustness', 'equity_curves', 'daytrade_setups', 'trade_fixes_replay'];

export default function TestResultsPage() {
  const [tab, setTab] = useState<TabId>('overview');
  const [sets, setSets] = useState<Record<string, Row[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [cmpError, setCmpError] = useState<string | null>(null);
  const [trend, setTrend] = useState<TrendDetail | null>(null);
  const [trendError, setTrendError] = useState<string | null>(null);

  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (TABS.some((t) => t.id === hash)) setTab(hash as TabId);
    Promise.allSettled(SETS.map((k) => api.researchSet(k))).then((results) => {
      const loaded: Record<string, Row[]> = {};
      results.forEach((r, i) => { if (r.status === 'fulfilled') loaded[SETS[i]] = r.value.rows; });
      setSets(loaded);
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed) setError(`${failed} of ${SETS.length} result sets didn't load. Run "python -m bot.tools.publish_research" in the bot container to publish them.`);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    const load = () => {
      api.comparison().then((c) => { setComparison(c); setCmpError(null); }).catch((e: Error) => setCmpError(e.message));
      api.trend().then((t) => { setTrend(t); setTrendError(null); }).catch((e: Error) => setTrendError(e.message));
    };
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  const choose = (id: TabId) => {
    setTab(id);
    window.history.replaceState(null, '', `#${id}`);
  };
  const plan = (sets.phases ?? []).find((p) => p.status === 'Running' || p.status === 'Planned');

  return (
    <div className="p-4 sm:p-6 space-y-4 min-w-0">
      <div>
        <h1 className="text-lg font-bold">Test Results</h1>
        <p className="text-xs mt-0.5 max-w-3xl" style={{ color: C.muted }}>
          Every test run on the bot and the strategies tested against it, plus the live side-by-side paper test.
          Only one approach held up across six years of data: weekly trend-following on the 10 major coins.
          The current bot and every day-trading setup lose money after costs.
        </p>
      </div>

      <nav role="tablist" aria-label="Test results sections" className="flex flex-wrap gap-1 border-b" style={{ borderColor: C.border }}>
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => choose(t.id)}
            className="px-3 py-2 text-sm -mb-px border-b-2 transition-colors"
            style={{ borderColor: tab === t.id ? C.accent : 'transparent', color: tab === t.id ? C.text : C.muted }}>
            {t.label}
          </button>
        ))}
      </nav>

      {error && <p className="text-xs" style={{ color: C.loss }}>{error}</p>}
      {loading && tab !== 'side-by-side' && tab !== 'trend-bot'
        ? <p className="text-sm" style={{ color: C.muted }}>Loading test results…</p>
        : (
          <div role="tabpanel">
            {tab === 'overview' && <OverviewTab sets={sets} />}
            {tab === 'history' && <HistoryTab phases={sets.phases ?? []} />}
            {tab === 'bot' && <BotTab sets={sets} />}
            {tab === 'strategies' && <StrategiesTab sets={sets} />}
            {tab === 'daytrade' && <DayTradeTab rows={sets.daytrade_setups ?? []} />}
            {tab === 'side-by-side' && <SideBySideTab data={comparison} error={cmpError} plan={plan} />}
            {tab === 'trend-bot' && <TrendBotTab data={trend} error={trendError} />}
          </div>
        )}
    </div>
  );
}
