'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useCurrency } from '@/lib/currency';
import { AlertTriangle, Loader2, RotateCcw, X } from 'lucide-react';

interface Props {
  currentCapital: number | null;
  onClose: () => void;
  onReset: (amount: number) => void;
}

export function ResetCapitalModal({ currentCapital, onClose, onReset }: Props) {
  const { display } = useCurrency();
  const [amountInput, setAmountInput] = useState('');
  const [openCount, setOpenCount] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const load = () => api.positions().then((p) => setOpenCount(p.length)).catch(() => setOpenCount(null));
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const amount = parseFloat(amountInput.replace(',', '.'));
  const validAmount = !isNaN(amount) && amount > 0;
  const canSubmit = validAmount && openCount === 0 && !submitting;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError('');
    try {
      await api.setCapital(amount);
      onReset(amount);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to reset capital');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)' }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-md mx-4 rounded-2xl overflow-hidden"
        style={{ background: '#0a0a0a', border: '1px solid #1f1f1f' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 flex items-center justify-between" style={{ background: '#111111', borderBottom: '1px solid #1f1f1f' }}>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'rgba(240,185,11,0.12)' }}>
              <RotateCcw size={20} style={{ color: '#f0b90b' }} />
            </div>
            <div>
              <h2 className="text-base font-bold" style={{ color: '#e8e8e8' }}>Reset Paper Capital</h2>
              <p className="text-xs" style={{ color: '#848e9c' }}>Start paper trading from a new balance</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg" style={{ color: '#848e9c' }}>
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="rounded-xl p-3 text-xs" style={{ background: '#111111', border: '1px solid #1f1f1f', color: '#848e9c' }}>
            <div className="flex items-center justify-between">
              <span>Current capital</span>
              <span className="font-semibold" style={{ color: '#e8e8e8' }}>
                {currentCapital !== null ? display(currentCapital) : '—'}
              </span>
            </div>
            <p className="mt-2">
              Trades closed before the Oct 14 P&amp;L fix were recorded with leverage counted twice, so this balance is overstated.
            </p>
          </div>

          <div>
            <label htmlFor="reset-capital" className="text-xs font-semibold mb-2 block" style={{ color: '#848e9c' }}>
              TRADE CAPITAL (USDT)
            </label>
            <input
              id="reset-capital"
              type="number"
              min="0"
              step="10"
              inputMode="decimal"
              autoFocus
              placeholder="e.g. 500"
              value={amountInput}
              onChange={(e) => setAmountInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
              className="w-full text-sm px-3 py-2.5 rounded-lg"
              style={{ background: '#0d0d0d', border: '1px solid #2a2a2a', color: '#eaecef', outline: 'none' }}
            />
            {amountInput && !validAmount && (
              <p className="text-xs mt-1.5" style={{ color: '#f6465d' }}>Enter an amount greater than 0</p>
            )}
          </div>

          {openCount !== null && openCount > 0 && (
            <div className="flex items-start gap-2 text-xs p-3 rounded-lg" style={{ background: 'rgba(246,70,93,0.08)', color: '#f6465d' }}>
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>
                {openCount} open position{openCount === 1 ? '' : 's'}. Close {openCount === 1 ? 'it' : 'them'} on the Positions page before resetting.
              </span>
            </div>
          )}

          <p className="text-xs" style={{ color: '#848e9c' }}>
            Resetting clears today&apos;s P&amp;L and the kill switch, then restarts the bot. Confirm the startup on the Dashboard to resume trading.
          </p>

          {error && (
            <div className="text-xs p-2 rounded" style={{ background: 'rgba(246,70,93,0.1)', color: '#f6465d' }}>{error}</div>
          )}
        </div>

        <div className="p-5 flex items-center gap-3 justify-end" style={{ borderTop: '1px solid #1f1f1f' }}>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-xs font-medium"
            style={{ background: '#1a1a1a', color: '#848e9c', border: '1px solid #2a2a2a' }}
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-4 py-2 rounded-lg text-xs font-semibold disabled:opacity-40"
            style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.3)' }}
          >
            {submitting ? (
              <span className="flex items-center gap-2"><Loader2 size={14} className="animate-spin" />Resetting...</span>
            ) : validAmount ? (
              `Reset to ${amount.toFixed(2)} USDT`
            ) : (
              'Reset capital'
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
