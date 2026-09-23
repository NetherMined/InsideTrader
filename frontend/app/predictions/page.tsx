'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { fmt, fmtPct, fmtPrice } from '@/lib/utils';
import type { Prediction } from '@/lib/types';

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 75 ? '#0ecb81' : pct >= 55 ? '#f0b90b' : '#f6465d';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 rounded-full h-1.5" style={{ background: '#1f1f1f' }}>
        <div className="h-1.5 rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs w-8 text-right" style={{ color }}>{pct}%</span>
    </div>
  );
}

export default function PredictionsPage() {
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Prediction | null>(null);

  useEffect(() => {
    api.predictions().then((p) => {
      setPredictions(p);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const sorted = [...predictions].sort((a, b) =>
    b.confidence * Math.abs(b.predicted_change_pct) - a.confidence * Math.abs(a.predicted_change_pct)
  );

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-lg font-bold">Predictions</h1>
        <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
          ML 24h price forecasts sorted by confidence × magnitude
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2 rounded-xl overflow-hidden" style={{ border: '1px solid #1f1f1f' }}>
          {loading ? (
            <div className="text-sm text-center py-12" style={{ color: '#848e9c' }}>
              Loading predictions…
            </div>
          ) : (
            <table className="w-full text-xs">
              <thead style={{ background: '#111111' }}>
                <tr style={{ color: '#848e9c' }}>
                  <th className="text-left px-4 py-2.5 font-medium">Symbol</th>
                  <th className="text-right px-4 py-2.5 font-medium">Current</th>
                  <th className="text-right px-4 py-2.5 font-medium">Target</th>
                  <th className="text-right px-4 py-2.5 font-medium">Change</th>
                  <th className="text-left px-4 py-2.5 font-medium w-36">Confidence</th>
                  <th className="text-left px-4 py-2.5 font-medium">Mode</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((p) => (
                  <tr
                    key={p.symbol}
                    onClick={() => setSelected(p)}
                    className="cursor-pointer"
                    style={{
                      borderTop: '1px solid #1a1a1a',
                      background: selected?.symbol === p.symbol ? 'rgba(240,185,11,0.05)' : 'transparent',
                    }}
                  >
                    <td className="px-4 py-2 font-medium">{p.symbol}</td>
                    <td className="px-4 py-2 text-right">{fmtPrice(p.current_price)}</td>
                    <td className="px-4 py-2 text-right">{fmtPrice(p.target_price)}</td>
                    <td
                      className="px-4 py-2 text-right font-semibold"
                      style={{ color: p.predicted_change_pct >= 0 ? '#0ecb81' : '#f6465d' }}
                    >
                      {fmtPct(p.predicted_change_pct)}
                    </td>
                    <td className="px-4 py-2 w-36">
                      <ConfidenceBar value={p.confidence} />
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className="px-1.5 py-0.5 rounded text-xs font-medium"
                        style={{
                          background: p.mode_recommendation === 'FUTURES' ? 'rgba(240,185,11,0.12)' : 'rgba(14,203,129,0.12)',
                          color: p.mode_recommendation === 'FUTURES' ? '#f0b90b' : '#0ecb81',
                        }}
                      >
                        {p.mode_recommendation}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="rounded-xl p-4 space-y-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          {selected ? (
            <>
              <div>
                <div className="text-base font-bold">{selected.symbol}</div>
                <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
                  {new Date(selected.prediction_date).toLocaleDateString()}
                </div>
              </div>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span style={{ color: '#848e9c' }}>Current Price</span>
                  <span>{fmtPrice(selected.current_price)}</span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: '#848e9c' }}>Target Price</span>
                  <span>{fmtPrice(selected.target_price)}</span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: '#848e9c' }}>Predicted Change</span>
                  <span style={{ color: selected.predicted_change_pct >= 0 ? '#0ecb81' : '#f6465d' }}>
                    {fmtPct(selected.predicted_change_pct)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: '#848e9c' }}>Confidence</span>
                  <span>{Math.round(selected.confidence * 100)}%</span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: '#848e9c' }}>Recommended Mode</span>
                  <span style={{ color: selected.mode_recommendation === 'FUTURES' ? '#f0b90b' : '#0ecb81' }}>
                    {selected.mode_recommendation}
                  </span>
                </div>
              </div>
              {selected.features && (
                <>
                  <div className="text-xs font-semibold pt-2" style={{ borderTop: '1px solid #1f1f1f' }}>
                    Feature Signals
                  </div>
                  <div className="space-y-2 text-xs">
                    {selected.features.rsi !== undefined && (
                      <div className="flex justify-between">
                        <span style={{ color: '#848e9c' }}>RSI</span>
                        <span>{fmt(selected.features.rsi, 1)}</span>
                      </div>
                    )}
                    {selected.features.adx !== undefined && (
                      <div className="flex justify-between">
                        <span style={{ color: '#848e9c' }}>ADX</span>
                        <span>{fmt(selected.features.adx, 1)}</span>
                      </div>
                    )}
                    {selected.features.atr_pct !== undefined && (
                      <div className="flex justify-between">
                        <span style={{ color: '#848e9c' }}>ATR%</span>
                        <span>{fmt(selected.features.atr_pct, 2)}%</span>
                      </div>
                    )}
                    {selected.features.mode_reason && (
                      <div className="pt-1 space-y-1">
                        {Object.entries(selected.features.mode_reason).map(([k, v]) => (
                          <div key={k} className="flex justify-between">
                            <span style={{ color: '#848e9c' }}>{k.replace(/_/g, ' ')}</span>
                            <span style={{ color: v ? '#0ecb81' : '#f6465d' }}>{v ? 'Pass' : 'Fail'}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          ) : (
            <div className="text-xs text-center py-8" style={{ color: '#848e9c' }}>
              Click a row to see details
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
