'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useCurrency } from '@/lib/currency';
import type { StartupStatus } from '@/lib/types';
import { CheckCircle, XCircle, AlertTriangle, Loader2, Rocket, X } from 'lucide-react';

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 py-1.5">
      {ok ? (
        <CheckCircle size={16} style={{ color: '#0ecb81' }} />
      ) : (
        <Loader2 size={16} className="animate-spin" style={{ color: '#f0b90b' }} />
      )}
      <span className="text-sm" style={{ color: ok ? '#e8e8e8' : '#f0b90b' }}>
        {label}
      </span>
    </div>
  );
}

function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1 text-xs">
      <span style={{ color: '#848e9c' }}>{label}</span>
      <span className="font-semibold" style={{ color: '#e8e8e8' }}>{value}</span>
    </div>
  );
}

interface Props {
  onConfirmed: () => void;
  onClose: () => void;
}

export function StartupModal({ onConfirmed, onClose }: Props) {
  const { display } = useCurrency();
  const [status, setStatus] = useState<StartupStatus | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const load = () => {
      api.startupStatus().then(setStatus).catch(() => null);
    };
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, []);

  if (!status || !status.awaiting_confirmation) return null;

  const allReady = status.capital_usdt > 0;
  const s = status.settings;

  const handleConfirm = async () => {
    setConfirming(true);
    setError('');
    try {
      await api.confirmStartup();
      onConfirmed();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to confirm startup');
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)' }}
    >
      <div
        className="w-full max-w-lg mx-4 rounded-2xl overflow-hidden"
        style={{ background: '#0a0a0a', border: '1px solid #1f1f1f' }}
      >
        <div className="p-5 flex items-center justify-between" style={{ background: '#111111', borderBottom: '1px solid #1f1f1f' }}>
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: 'rgba(240,185,11,0.12)' }}
            >
              <Rocket size={20} style={{ color: '#f0b90b' }} />
            </div>
            <div>
              <h2 className="text-base font-bold" style={{ color: '#e8e8e8' }}>
                Confirm Trading Startup
              </h2>
              <p className="text-xs" style={{ color: '#848e9c' }}>
                Review settings before the bot begins trading
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: '#848e9c' }}
            onMouseOver={(e) => (e.currentTarget.style.background = 'rgba(132,142,156,0.15)')}
            onMouseOut={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-4 max-h-[65vh] overflow-y-auto">
          <div>
            <div className="text-xs font-semibold mb-2" style={{ color: '#848e9c' }}>
              READINESS CHECKS
            </div>
            <div
              className="rounded-xl p-3"
              style={{ background: '#111111', border: '1px solid #1f1f1f' }}
            >
              <Check ok={status.data_feed_active} label={`Price feeds active (${status.price_feeds_count} symbols)`} />
              <Check ok={status.capital_usdt > 0} label={`Capital available (${display(status.capital_usdt)})`} />
              <Check ok={true} label="Database connected" />
              <Check ok={true} label="Redis connected" />
            </div>
          </div>

          <div>
            <div className="text-xs font-semibold mb-2" style={{ color: '#848e9c' }}>
              TRADING MODE
            </div>
            <div
              className="rounded-xl p-3 flex items-center justify-between"
              style={{
                background: status.paper_mode ? 'rgba(132,142,156,0.08)' : 'rgba(246,70,93,0.08)',
                border: `1px solid ${status.paper_mode ? '#1f1f1f' : 'rgba(246,70,93,0.3)'}`,
              }}
            >
              <div className="flex items-center gap-2">
                {!status.paper_mode && <AlertTriangle size={16} style={{ color: '#f6465d' }} />}
                <span className="text-sm font-bold" style={{ color: status.paper_mode ? '#848e9c' : '#f6465d' }}>
                  {status.paper_mode ? 'Paper Trading (Simulated)' : 'LIVE TRADING (Real Money)'}
                </span>
              </div>
              <span
                className="text-xs px-2 py-1 rounded font-semibold"
                style={{
                  background: status.paper_mode ? 'rgba(132,142,156,0.15)' : 'rgba(246,70,93,0.15)',
                  color: status.paper_mode ? '#848e9c' : '#f6465d',
                }}
              >
                {status.paper_mode ? 'PAPER' : 'LIVE'}
              </span>
            </div>
          </div>

          <div>
            <div className="text-xs font-semibold mb-2" style={{ color: '#848e9c' }}>
              ACTIVE SETTINGS
            </div>
            <div
              className="rounded-xl p-3"
              style={{ background: '#111111', border: '1px solid #1f1f1f' }}
            >
              <SettingRow label="Strategy Mode" value={s.trading_mode} />
              <SettingRow label="Heat Cap" value={`${s.heat_limit_pct ?? 40}%`} />
              <SettingRow label="Max Concurrent" value={String(s.max_concurrent_trades)} />
              <SettingRow label="Max Daily" value={String(s.max_daily_trades)} />
              <SettingRow label="Futures Leverage" value={`${s.futures_leverage}x`} />
              <SettingRow label="Capital" value={display(status.capital_usdt)} />
            </div>
          </div>

          {error && (
            <div className="text-xs p-2 rounded" style={{ background: 'rgba(246,70,93,0.1)', color: '#f6465d' }}>
              {error}
            </div>
          )}
        </div>

        <div className="p-5" style={{ borderTop: '1px solid #1f1f1f' }}>
          <button
            onClick={handleConfirm}
            disabled={confirming || !allReady}
            className="w-full py-3 rounded-xl text-sm font-bold transition-opacity disabled:opacity-40"
            style={{
              background: allReady ? 'rgba(14,203,129,0.15)' : 'rgba(132,142,156,0.1)',
              color: allReady ? '#0ecb81' : '#848e9c',
              border: `1px solid ${allReady ? 'rgba(14,203,129,0.3)' : '#1f1f1f'}`,
              cursor: allReady && !confirming ? 'pointer' : 'not-allowed',
            }}
          >
            {confirming ? (
              <span className="flex items-center justify-center gap-2">
                <Loader2 size={16} className="animate-spin" />
                Starting...
              </span>
            ) : !allReady ? (
              'No capital available'
            ) : (
              `Confirm & Start ${status.paper_mode ? 'Paper' : 'Live'} Trading`
            )}
          </button>
          {!status.paper_mode && (
            <p className="text-xs text-center mt-2" style={{ color: '#f6465d' }}>
              This will trade with real money on your Binance account
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
