'use client';

import type { RecoveryStats } from '@/lib/types';

const REASON_LABELS: Record<string, string> = {
  stop_loss: 'Stop loss',
  structure_invalidation: 'Structure invalidation',
  structure_flip: 'Structure flip',
  trailing_stop: 'Trailing stop',
  take_profit: 'Take profit',
  negative_timeout: 'Negative timeout',
  max_hold: 'Max hold',
  manual: 'Manual',
};

const GREEN = '#0ecb81';
const RED = '#f6465d';
const MUTED = '#848e9c';

function pnlColor(v: number | null | undefined) {
  if (v == null) return MUTED;
  return v >= 0 ? GREEN : RED;
}

function pct(n: number, d: number) {
  return d > 0 ? `${Math.round((n / d) * 100)}%` : '—';
}

export default function RecoverySection({
  data,
  display,
}: {
  data: RecoveryStats | null;
  display: (usdt: number) => string;
}) {
  const money = (v: number | null | undefined) => (v == null ? '—' : display(v));
  const reasons = data?.by_reason ?? [];
  const recent = data?.recent ?? [];

  return (
    <div className="space-y-3">
      <div className="text-xs" style={{ color: MUTED }}>
        What price did in the 60 minutes after each exit (1m researcher, last {data?.hours ?? 24}h). "Held" columns
        show the PnL if the trade had stayed open with no stop. Recovered means price got back to the entry price.
      </div>

      <div className="rounded-2xl overflow-x-auto" style={{ border: '1px solid #1f1f1f' }}>
        {reasons.length === 0 ? (
          <div className="text-xs text-center py-8" style={{ color: MUTED }}>
            No recovery data yet. It fills in about a minute after each trade closes.
          </div>
        ) : (
          <table className="w-full text-xs" style={{ minWidth: 760 }}>
            <thead>
              <tr style={{ color: MUTED, background: '#111111' }}>
                {['Exit reason', 'Trades', 'Recovered', 'Hit TP', 'Held better (60m)', 'Avg best move', 'Avg worst move', 'Actual PnL', 'Held 5m', 'Held 15m', 'Held 60m'].map((h) => (
                  <th key={h} className="text-left font-medium px-3 py-2 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {reasons.map((r) => (
                <tr key={r.close_reason} style={{ borderTop: '1px solid #1a1a1a' }}>
                  <td className="px-3 py-2 font-semibold">{REASON_LABELS[r.close_reason] ?? r.close_reason}</td>
                  <td className="px-3 py-2">{r.trades}</td>
                  <td className="px-3 py-2">{r.recovered} ({pct(r.recovered, r.trades)})</td>
                  <td className="px-3 py-2">{r.hit_tp}</td>
                  <td className="px-3 py-2">{r.held_better_60m}/{r.with_60m} ({pct(r.held_better_60m, r.with_60m)})</td>
                  <td className="px-3 py-2" style={{ color: GREEN }}>
                    {r.avg_best_fav_pct == null ? '—' : `+${r.avg_best_fav_pct.toFixed(2)}%`}
                  </td>
                  <td className="px-3 py-2" style={{ color: RED }}>
                    {r.avg_worst_adv_pct == null ? '—' : `${r.avg_worst_adv_pct.toFixed(2)}%`}
                  </td>
                  <td className="px-3 py-2" style={{ color: pnlColor(r.actual_pnl) }}>{money(r.actual_pnl)}</td>
                  <td className="px-3 py-2" style={{ color: pnlColor(r.hold_pnl_5m) }}>{money(r.hold_pnl_5m)}</td>
                  <td className="px-3 py-2" style={{ color: pnlColor(r.hold_pnl_15m) }}>{money(r.hold_pnl_15m)}</td>
                  <td className="px-3 py-2" style={{ color: pnlColor(r.hold_pnl_60m) }}>{money(r.hold_pnl_60m)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {recent.length > 0 && (
        <div className="rounded-2xl overflow-x-auto" style={{ border: '1px solid #1f1f1f' }}>
          <table className="w-full text-xs" style={{ minWidth: 760 }}>
            <thead>
              <tr style={{ color: MUTED, background: '#111111' }}>
                {['Trade', 'Exit', 'Held for', 'Actual', 'Recovered', 'Best move', 'Worst move', 'Held 15m', 'Held 60m'].map((h) => (
                  <th key={h} className="text-left font-medium px-3 py-2 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recent.map((t) => (
                <tr key={t.trade_id} style={{ borderTop: '1px solid #1a1a1a' }}>
                  <td className="px-3 py-2">
                    <span className="font-semibold">{t.symbol}</span>{' '}
                    <span style={{ color: t.side === 'BUY' ? GREEN : RED }}>{t.side === 'BUY' ? 'Long' : 'Short'}</span>
                  </td>
                  <td className="px-3 py-2">{REASON_LABELS[t.close_reason ?? ''] ?? t.close_reason ?? '—'}</td>
                  <td className="px-3 py-2">{t.hold_seconds == null ? '—' : t.hold_seconds < 120 ? `${t.hold_seconds}s` : `${Math.round(t.hold_seconds / 60)}m`}</td>
                  <td className="px-3 py-2" style={{ color: pnlColor(t.pnl_usdt) }}>{money(t.pnl_usdt)}</td>
                  <td className="px-3 py-2" style={{ color: t.recovered_to_entry ? GREEN : MUTED }}>
                    {t.recovered_to_entry ? `Yes, ${t.minutes_to_entry}m` : t.final ? 'No' : `Watching (${t.minutes_observed}m)`}
                  </td>
                  <td className="px-3 py-2" style={{ color: GREEN }}>
                    {t.best_fav_pct == null ? '—' : `+${t.best_fav_pct.toFixed(2)}%`}
                  </td>
                  <td className="px-3 py-2" style={{ color: RED }}>
                    {t.worst_adv_pct == null ? '—' : `${t.worst_adv_pct.toFixed(2)}%`}
                  </td>
                  <td className="px-3 py-2" style={{ color: pnlColor(t.hold_pnl_15m) }}>{money(t.hold_pnl_15m)}</td>
                  <td className="px-3 py-2" style={{ color: pnlColor(t.hold_pnl_60m) }}>{money(t.hold_pnl_60m)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
