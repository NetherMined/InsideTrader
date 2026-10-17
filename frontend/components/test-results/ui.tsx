'use client';

import { useMemo, useState, type ReactNode } from 'react';
import type { ResearchCell, ResearchRow } from '@/lib/types';

export const C = {
  surface: '#111111',
  border: '#1f1f1f',
  accent: '#f0b90b',
  profit: '#0ecb81',
  loss: '#f6465d',
  muted: '#848e9c',
  text: '#e9e9e9',
  grid: '#1f1f1f',
};

export const num = (v: ResearchCell | undefined, d = 2) =>
  typeof v === 'number' && Number.isFinite(v)
    ? v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
    : '—';
export const signed = (v: ResearchCell | undefined, d = 2, suffix = '') => {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  const r = Number(v.toFixed(d));
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${num(Math.abs(r), d)}${suffix}`;
};
export const pct = (v: ResearchCell | undefined, d = 1) => signed(v, d, '%');
export const plainPct = (v: ResearchCell | undefined, d = 1) => (typeof v === 'number' ? `${num(v, d)}%` : '—');
export const usd = (v: ResearchCell | undefined) =>
  typeof v === 'number' && Number.isFinite(v) ? `${v < 0 ? '−' : ''}$${num(Math.abs(v), 2)}` : '—';
export const int = (v: ResearchCell | undefined) => (typeof v === 'number' ? Math.round(v).toLocaleString() : '—');
export const tone = (v: ResearchCell | undefined) =>
  typeof v === 'number' ? (v > 0 ? C.profit : v < 0 ? C.loss : undefined) : undefined;

export function Card({ title, note, children, id }: { title: string; note?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section id={id} className="rounded-xl p-4 min-w-0" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <h2 className="text-sm font-semibold">{title}</h2>
      {note && <p className="text-xs mt-1 mb-3 max-w-3xl leading-relaxed" style={{ color: C.muted }}>{note}</p>}
      {!note && <div className="mb-3" />}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, color }: { label: string; value: ReactNode; sub?: ReactNode; color?: string }) {
  return (
    <div className="rounded-xl p-4 min-w-0" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
      <div className="text-xs" style={{ color: C.muted }}>{label}</div>
      <div className="text-2xl font-semibold mt-1 tabular-nums" style={{ color }}>{value}</div>
      {sub && <div className="text-xs mt-1" style={{ color: C.muted }}>{sub}</div>}
    </div>
  );
}

export function Pill({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span className="inline-block text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap"
      style={{ border: `1px solid ${C.border}`, color: color ?? C.muted }}>
      {children}
    </span>
  );
}

export type Col<T> = {
  key: string;
  label: string;
  render?: (row: T) => ReactNode;
  color?: (row: T) => string | undefined;
  sort?: (row: T) => number | string;
  left?: boolean;
};

export function DataTable<T extends object>({
  rows, cols, rowKey, highlight, maxHeight = 520, defaultSort,
}: {
  rows: T[];
  cols: Col<T>[];
  rowKey: (row: T, i: number) => string;
  highlight?: (row: T) => boolean;
  maxHeight?: number;
  defaultSort?: { key: string; dir: 'asc' | 'desc' };
}) {
  const [sort, setSort] = useState(defaultSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = cols.find((c) => c.key === sort.key);
    const val = (r: T) => (col?.sort ? col.sort(r) : ((r as Record<string, unknown>)[sort.key] as number | string));
    return [...rows].sort((a, b) => {
      const av = val(a), bv = val(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv));
      return sort.dir === 'asc' ? cmp : -cmp;
    });
  }, [rows, cols, sort]);

  const click = (key: string) =>
    setSort((s) => (s?.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' }));

  return (
    <div className="overflow-auto" style={{ maxHeight }}>
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr>
            {cols.map((c, i) => (
              <th key={c.key}
                aria-sort={sort?.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                className={`sticky top-0 font-medium px-2 py-2 whitespace-nowrap ${i === 0 || c.left ? 'text-left' : 'text-right'}`}
                style={{ background: C.surface, color: C.muted, borderBottom: `1px solid ${C.border}` }}>
                <button type="button" onClick={() => click(c.key)} className="hover:text-white">
                  {c.label}{sort?.key === c.key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, ri) => (
            <tr key={rowKey(r, ri)} style={highlight?.(r) ? { background: 'rgba(240,185,11,0.07)' } : undefined}>
              {cols.map((c, i) => {
                const raw = (r as Record<string, unknown>)[c.key] as ResearchCell;
                return (
                  <td key={c.key}
                    className={`px-2 py-1.5 ${i === 0 || c.left ? 'text-left min-w-[12rem]' : 'text-right whitespace-nowrap'}`}
                    style={{ borderBottom: `1px solid ${C.border}`, color: c.color?.(r) }}>
                    {c.render ? c.render(r) : raw == null ? '—' : String(raw)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const chartTooltip = {
  contentStyle: { background: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: C.muted },
};
export const axisTick = { fontSize: 10, fill: C.muted };

export type Row = ResearchRow;
export const n = (r: Row, k: string) => (typeof r[k] === 'number' ? (r[k] as number) : null);
