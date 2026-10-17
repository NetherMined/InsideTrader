'use client';

import { C, Pill, type Row } from './ui';

const STATUS_COLOR: Record<string, string> = { Live: C.profit, Running: C.profit, Planned: C.accent, Done: C.muted };

export function PhaseCard({ phase }: { phase: Row }) {
  const status = String(phase.status ?? '');
  const date = phase.date ? new Date(`${phase.date}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : '';
  return (
    <article className="rounded-xl p-4 grid gap-4 sm:grid-cols-[9rem_1fr]" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="flex sm:flex-col gap-2 sm:gap-1 items-center sm:items-start">
        <div className="text-base font-semibold" style={{ color: C.accent }}>{String(phase.phase)}</div>
        <div className="text-xs" style={{ color: C.muted }}>{date}</div>
        <Pill color={STATUS_COLOR[status]}>{status}</Pill>
      </div>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold mb-2">{String(phase.name)}</h3>
        <dl className="grid gap-x-4 gap-y-1.5 text-xs sm:grid-cols-[6rem_1fr] max-w-4xl leading-relaxed">
          {(['question', 'method', 'finding', 'decision'] as const).map((k) => (
            <div key={k} className="contents">
              <dt className="capitalize" style={{ color: C.muted }}>{k}</dt>
              <dd className="mb-1 sm:mb-0">{String(phase[k] ?? '—')}</dd>
            </div>
          ))}
        </dl>
      </div>
    </article>
  );
}

export function HistoryTab({ phases }: { phases: Row[] }) {
  const ordered = [...phases].sort((a, b) => Number(a.order) - Number(b.order));
  if (!ordered.length) return <p className="text-sm" style={{ color: C.muted }}>No testing history has been published yet.</p>;
  return <div className="space-y-3">{ordered.map((p) => <PhaseCard key={String(p.phase)} phase={p} />)}</div>;
}
