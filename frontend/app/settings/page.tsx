'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useCurrency } from '@/lib/currency';
import type { Health, TradeLimits, Goal, AccountBalances, LiveMode } from '@/lib/types';
import { NotificationPermissionButton, NotificationFilterSelector } from '@/components/Toast';

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className="inline-block w-2 h-2 rounded-full"
      style={{ background: ok ? '#0ecb81' : '#f6465d' }}
    />
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid #1a1a1a' }}>
      <span className="text-xs" style={{ color: '#848e9c' }}>{label}</span>
      <span className="text-xs font-medium">{value}</span>
    </div>
  );
}

function Stepper({
  label, value, min, max, step, zeroLabel, suffix, onChange, disabled, suggestions,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  zeroLabel?: string;
  suffix?: string;
  onChange: (v: number) => void;
  disabled?: boolean;
  suggestions?: number[];
}) {
  const s = step ?? 1;
  const disp = value === 0 && zeroLabel ? zeroLabel : `${value}${suffix || ''}`;
  return (
    <div className="py-2" style={{ borderBottom: '1px solid #1a1a1a' }}>
      <div className="flex items-center justify-between">
        <span className="text-xs" style={{ color: '#848e9c' }}>{label}</span>
        <div className="flex items-center gap-2">
          {suggestions && suggestions.length > 0 && suggestions.map((sg) => (
            <button
              key={sg}
              onClick={() => onChange(sg)}
              disabled={disabled}
              className="px-1.5 py-0.5 rounded"
              style={{
                fontSize: 10,
                background: value === sg ? 'rgba(240,185,11,0.15)' : '#0d0d0d',
                color: value === sg ? '#f0b90b' : '#555',
                border: `1px solid ${value === sg ? 'rgba(240,185,11,0.3)' : '#2a2a2a'}`,
                cursor: disabled ? 'not-allowed' : 'pointer',
              }}
            >{sg}{suffix || ''}</button>
          ))}
          <button
            onClick={() => onChange(Math.max(min, parseFloat((value - s).toFixed(2))))}
            disabled={disabled || value <= min}
            className="w-6 h-6 rounded text-xs font-bold flex items-center justify-center"
            style={{
              background: '#1a1a1a',
              color: disabled || value <= min ? '#444' : '#eaecef',
              border: '1px solid #2a2a2a',
              cursor: disabled || value <= min ? 'not-allowed' : 'pointer',
            }}
          >-</button>
          <span className="text-xs font-medium w-16 text-center">{disp}</span>
          <button
            onClick={() => onChange(Math.min(max, parseFloat((value + s).toFixed(2))))}
            disabled={disabled || value >= max}
            className="w-6 h-6 rounded text-xs font-bold flex items-center justify-center"
            style={{
              background: '#1a1a1a',
              color: disabled || value >= max ? '#444' : '#eaecef',
              border: '1px solid #2a2a2a',
              cursor: disabled || value >= max ? 'not-allowed' : 'pointer',
            }}
          >+</button>
        </div>
      </div>
    </div>
  );
}

function ModeToggle({
  value, onChange, disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const MODES: { key: string; label: string; desc: string }[] = [
    { key: 'SPOT', label: 'Spot Only', desc: 'BUY only, no leverage' },
    { key: 'DYNAMIC', label: 'Dynamic', desc: 'ML picks Spot or Futures' },
    { key: 'FUTURES', label: 'Futures Only', desc: 'All trades use leverage' },
  ];
  return (
    <div className="py-2" style={{ borderBottom: '1px solid #1a1a1a' }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs" style={{ color: '#848e9c' }}>Trading Mode</span>
      </div>
      <div className="flex items-center gap-1">
        {MODES.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => onChange(key)}
            disabled={disabled}
            className="px-2.5 py-1 rounded text-xs font-medium transition-all"
            style={{
              background: value === key ? 'rgba(240,185,11,0.15)' : '#1a1a1a',
              color: value === key ? '#f0b90b' : '#555',
              border: `1px solid ${value === key ? 'rgba(240,185,11,0.3)' : '#2a2a2a'}`,
              cursor: disabled ? 'wait' : 'pointer',
            }}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

const DEFAULT_LIMITS: TradeLimits = {
  max_concurrent_trades: 3,
  confidence_threshold: 0.75,
  stop_loss_percent: 2.0,
  take_profit_percent: 3.0,
  daily_loss_limit_percent: 10.0,
  futures_leverage: 2,
  negative_trade_timeout_minutes: 30,
  trading_mode: 'DYNAMIC',
  min_daily_trades: 0,
  min_concurrent_trades: 0,
  max_daily_trades: 200,
  force_trade_mode: 'DYNAMIC',
  disable_futures_buy: true,
};

export default function SettingsPage() {
  const { display, tooltip, zarRate, currency } = useCurrency();
  const [health, setHealth] = useState<Health | null>(null);
  const [limits, setLimits] = useState<TradeLimits>(DEFAULT_LIMITS);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [goal, setGoal] = useState<Goal>({ amount_usdt: 0, period_hours: 168, max_allowed_usdt: 0 });
  const [goalInput, setGoalInput] = useState('');
  const [savingGoal, setSavingGoal] = useState(false);

  const [capital, setCapital] = useState<number | null>(null);
  const [capitalInput, setCapitalInput] = useState('');
  const [savingCapital, setSavingCapital] = useState(false);
  const [showRestartModal, setShowRestartModal] = useState(false);
  const [showHardResetModal, setShowHardResetModal] = useState(false);
  const [hardResetting, setHardResetting] = useState(false);
  const [hardResetMsg, setHardResetMsg] = useState('');

  const [liveMode, setLiveMode] = useState<LiveMode | null>(null);
  const [balances, setBalances] = useState<AccountBalances | null>(null);
  const [loadingBalances, setLoadingBalances] = useState(false);
  const [converting, setConverting] = useState(false);
  const [showLiveModeModal, setShowLiveModeModal] = useState(false);
  const [switchingMode, setSwitchingMode] = useState(false);

  const handleHardReset = async () => {
    setHardResetting(true);
    try {
      const r = await api.hardReset();
      setHardResetMsg(r.message || 'Reset complete');
      setShowHardResetModal(false);
    } catch {
      setHardResetMsg('Reset failed — check server logs');
    } finally {
      setHardResetting(false);
    }
  };
  const [resettingKillSwitch, setResettingKillSwitch] = useState(false);
  const [killSwitchActive, setKillSwitchActive] = useState(false);

  useEffect(() => {
    Promise.all([
      api.health().catch(() => null),
      api.getTradeLimits().catch(() => null),
      api.getGoal().catch(() => null),
      api.getCapital().catch(() => null),
      api.botStatus().catch(() => null),
      api.getLiveMode().catch(() => null),
    ]).then(([h, tl, g, c, bs, lm]) => {
      if (h) setHealth(h);
      if (tl) setLimits(tl);
      if (g) {
        setGoal(g);
        if (g.amount_usdt > 0) {
          setGoalInput(currency === 'ZAR' && zarRate ? (g.amount_usdt * zarRate).toFixed(0) : g.amount_usdt.toFixed(2));
        }
      }
      if (c) {
        setCapital(c.capital_usdt);
        setCapitalInput(c.capital_usdt.toFixed(2));
      }
      if (bs) setKillSwitchActive(bs.kill_switch);
      if (lm) setLiveMode(lm);
      setLoading(false);
    });

    const interval = setInterval(() => {
      api.health().then(setHealth).catch(() => {});
      api.botStatus().then(bs => setKillSwitchActive(bs.kill_switch)).catch(() => {});
    }, 15000);

    return () => clearInterval(interval);
  }, []);

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  }

  async function handleLimitChange(field: keyof TradeLimits, value: number | string | boolean) {
    const updated = { ...limits, [field]: value };
    setLimits(updated);
    setSaving(true);
    try {
      const saved = await api.setTradeLimits({ [field]: value });
      setLimits(saved);
      showToast('Settings updated');
    } catch {
      showToast('Failed to save settings');
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveGoal() {
    const raw = parseFloat(goalInput.replace(',', '.'));
    if (isNaN(raw) || raw < 0) { showToast('Enter a valid goal amount'); return; }
    const amount_usdt = currency === 'ZAR' && zarRate ? raw / zarRate : raw;
    setSavingGoal(true);
    try {
      if (goal.max_allowed_usdt > 0 && amount_usdt > goal.max_allowed_usdt) {
        showToast(`Max realistic goal is ${display(goal.max_allowed_usdt)}`);
        setSavingGoal(false);
        return;
      }
      const saved = await api.setGoal(amount_usdt);
      setGoal(saved);
      showToast('Goal saved');
    } catch {
      showToast('Failed to save goal');
    } finally {
      setSavingGoal(false);
    }
  }

  async function handleRestartCapital() {
    const raw = parseFloat(capitalInput.replace(',', '.'));
    if (isNaN(raw) || raw <= 0) {
      showToast('Enter a valid capital amount');
      return;
    }
    const amount = currency === 'ZAR' && zarRate ? raw / zarRate : raw;
    setSavingCapital(true);
    setShowRestartModal(false);
    try {
      await api.setCapital(amount);
      setCapital(amount);
      showToast(`Bot restarting with ${display(amount)} capital`);
    } catch {
      showToast('Failed to restart bot');
    } finally {
      setSavingCapital(false);
    }
  }

  async function handleResetKillSwitch() {
    setResettingKillSwitch(true);
    try {
      await api.resetKillSwitch();
      setKillSwitchActive(false);
      showToast('Kill switch reset — trading will resume');
    } catch {
      showToast('Failed to reset kill switch');
    } finally {
      setResettingKillSwitch(false);
    }
  }

  async function fetchBalances() {
    setLoadingBalances(true);
    try {
      const b = await api.getAccountBalances();
      setBalances(b);
    } catch {
      showToast('Failed to fetch Binance balances');
    } finally {
      setLoadingBalances(false);
    }
  }

  async function handleConvertAll() {
    setConverting(true);
    try {
      const r = await api.convertAllToUsdt();
      const successes = r.conversions.filter(c => c.ok);
      const totalReceived = successes.reduce((sum, c) => sum + (c.usdt_received || 0), 0);
      if (successes.length > 0) {
        showToast(`Converted ${successes.length} assets — +$${totalReceived.toFixed(2)} USDT`);
      } else {
        showToast('No assets to convert');
      }
      fetchBalances();
    } catch {
      showToast('Conversion failed — check server logs');
    } finally {
      setConverting(false);
    }
  }

  async function handleSwitchToLive() {
    setSwitchingMode(true);
    setShowLiveModeModal(false);
    try {
      const result = await api.setLiveMode({
        paper_trading_mode: false,
        live_trading_enabled: true,
        use_testnet: false,
      });
      setLiveMode(result);
      showToast('Switched to LIVE trading — bot restarting');
    } catch {
      showToast('Failed to switch mode');
    } finally {
      setSwitchingMode(false);
    }
  }

  async function handleSwitchToPaper() {
    setSwitchingMode(true);
    try {
      const result = await api.setLiveMode({
        paper_trading_mode: true,
        live_trading_enabled: false,
        use_testnet: true,
      });
      setLiveMode(result);
      showToast('Switched to PAPER trading — bot restarting');
    } catch {
      showToast('Failed to switch mode');
    } finally {
      setSwitchingMode(false);
    }
  }

  const capitalChanged = capital !== null && parseFloat(capitalInput || '0') !== capital;
  const isLive = liveMode ? !liveMode.paper_trading_mode && liveMode.live_trading_enabled : false;

  const cap = capital ?? 100;
  const maxSensibleConcurrent = Math.min(50, Math.max(1, Math.floor(cap / 11)));
  const concurrentSuggestions = Array.from(new Set([1, 3, 5, 10, maxSensibleConcurrent])).filter(v => v >= 1 && v <= 50);
  const minConcurrentSuggestions = Array.from(new Set([0, 1, 2, 3, Math.min(5, Math.floor(maxSensibleConcurrent / 2))])).filter(v => v >= 0 && v <= 50);
  const minDailySuggestions = [0, 10, 20, 50, 100];
  const stopLossSuggestions = [0.5, 1, 1.5, 2, 3];
  const takeProfitSuggestions = [1, 2, 3, 5, 7];

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      {toast && (
        <div
          className="fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg"
          style={{ background: '#1a1a1a', border: '1px solid #2a2a2a', color: '#eaecef' }}
        >
          {toast}
        </div>
      )}

      <div>
        <h1 className="text-lg font-bold">Settings</h1>
        <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
          System status and configuration overview
        </p>
      </div>

      <div className="rounded-xl p-4 space-y-1" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-3">System Health</div>
        {loading ? (
          <div className="text-xs" style={{ color: '#848e9c' }}>Checking...</div>
        ) : health ? (
          <>
            <div className="flex items-center gap-2 py-2" style={{ borderBottom: '1px solid #1a1a1a' }}>
              <StatusDot ok={health.status === 'ok'} />
              <span className="text-xs" style={{ color: '#848e9c' }}>API</span>
              <span className="text-xs font-medium ml-auto">{health.status.toUpperCase()}</span>
            </div>
            <div className="flex items-center gap-2 py-2" style={{ borderBottom: '1px solid #1a1a1a' }}>
              <StatusDot ok={health.db === 'ok'} />
              <span className="text-xs" style={{ color: '#848e9c' }}>PostgreSQL</span>
              <span className="text-xs font-medium ml-auto">{health.db.toUpperCase()}</span>
            </div>
            <div className="flex items-center gap-2 py-2">
              <StatusDot ok={health.redis === 'ok'} />
              <span className="text-xs" style={{ color: '#848e9c' }}>Redis</span>
              <span className="text-xs font-medium ml-auto">{health.redis.toUpperCase()}</span>
            </div>
          </>
        ) : (
          <div className="text-xs" style={{ color: '#f6465d' }}>API unreachable</div>
        )}
      </div>

      {killSwitchActive && (
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid rgba(246,70,93,0.4)' }}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: '#f6465d' }} />
              <span className="text-sm font-semibold" style={{ color: '#f6465d' }}>Kill Switch Active</span>
            </div>
            <button
              onClick={handleResetKillSwitch}
              disabled={resettingKillSwitch}
              className="px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40"
              style={{ background: 'rgba(14,203,129,0.15)', color: '#0ecb81', border: '1px solid rgba(14,203,129,0.3)' }}
            >
              {resettingKillSwitch ? 'Resetting...' : 'Reset & Resume Trading'}
            </button>
          </div>
          <p className="text-xs" style={{ color: '#848e9c' }}>
            Daily loss limit was hit. All new trades are blocked until the kill switch is reset or a new day begins (midnight UTC).
          </p>
        </div>
      )}

      <div className="rounded-xl p-4" style={{
        background: '#111111',
        border: isLive ? '1px solid rgba(14,203,129,0.4)' : '1px solid rgba(240,185,11,0.3)',
      }}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-sm font-semibold flex items-center gap-2">
              Trading Mode
              <span
                className="text-xs px-2 py-0.5 rounded font-medium"
                style={{
                  background: isLive ? 'rgba(14,203,129,0.15)' : 'rgba(240,185,11,0.15)',
                  color: isLive ? '#0ecb81' : '#f0b90b',
                }}
              >
                {isLive ? 'LIVE' : 'PAPER'}
              </span>
              {liveMode && !liveMode.use_testnet && (
                <span className="text-xs px-2 py-0.5 rounded font-medium" style={{ background: 'rgba(14,203,129,0.1)', color: '#0ecb81' }}>
                  Mainnet
                </span>
              )}
              {liveMode?.use_testnet && (
                <span className="text-xs px-2 py-0.5 rounded font-medium" style={{ background: 'rgba(240,185,11,0.1)', color: '#f0b90b' }}>
                  Testnet
                </span>
              )}
            </div>
            <p className="text-xs mt-1" style={{ color: '#848e9c' }}>
              {isLive
                ? 'Real money trading active — bot uses your Binance account balance.'
                : 'Simulated trading — no real orders are placed.'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isLive ? (
            <button
              onClick={handleSwitchToPaper}
              disabled={switchingMode}
              className="px-3 py-2 rounded-lg text-xs font-medium disabled:opacity-40"
              style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.3)' }}
            >
              {switchingMode ? 'Switching...' : 'Switch to Paper Trading'}
            </button>
          ) : (
            <button
              onClick={() => setShowLiveModeModal(true)}
              disabled={switchingMode}
              className="px-3 py-2 rounded-lg text-xs font-medium disabled:opacity-40"
              style={{ background: 'rgba(14,203,129,0.15)', color: '#0ecb81', border: '1px solid rgba(14,203,129,0.3)' }}
            >
              {switchingMode ? 'Switching...' : 'Switch to Live Trading'}
            </button>
          )}
        </div>
      </div>

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-sm font-semibold">Binance Account</div>
            <p className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
              Your real Binance wallet balances
            </p>
          </div>
          <button
            onClick={fetchBalances}
            disabled={loadingBalances}
            className="px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40"
            style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.3)' }}
          >
            {loadingBalances ? 'Loading...' : balances ? 'Refresh' : 'Fetch Balances'}
          </button>
        </div>

        {balances && (
          <>
            <div className="flex items-center gap-4 mb-3 p-3 rounded-lg" style={{ background: '#0d0d0d' }}>
              <div>
                <div className="text-xs" style={{ color: '#848e9c' }}>Total Value</div>
                <div className="text-lg font-bold" style={{ color: '#eaecef' }}>{display(balances.total_usdt)}</div>
              </div>
              <div>
                <div className="text-xs" style={{ color: '#848e9c' }}>Free USDT</div>
                <div className="text-lg font-bold" style={{ color: '#0ecb81' }}>{display(balances.free_usdt)}</div>
              </div>
              {balances.is_testnet && (
                <span className="text-xs px-2 py-0.5 rounded" style={{ background: 'rgba(240,185,11,0.1)', color: '#f0b90b' }}>
                  Testnet
                </span>
              )}
            </div>

            {balances.assets.length > 0 && (
              <div className="mb-3">
                <div className="text-xs font-medium mb-2" style={{ color: '#848e9c' }}>Assets</div>
                <div className="space-y-1">
                  {balances.assets.map((a) => (
                    <div key={a.asset} className="flex items-center justify-between py-1.5 px-2 rounded" style={{ background: '#0d0d0d' }}>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold" style={{ color: '#eaecef' }}>{a.asset}</span>
                        <span className="text-xs" style={{ color: '#848e9c' }}>{a.total}</span>
                      </div>
                      <span className="text-xs font-medium" style={{ color: a.usdt_value > 0 ? '#eaecef' : '#555' }}>
                        {display(a.usdt_value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {balances.assets.filter(a => a.asset !== 'USDT' && a.usdt_value > 5).length > 0 && (
              <button
                onClick={handleConvertAll}
                disabled={converting}
                className="w-full px-3 py-2 rounded-lg text-xs font-medium disabled:opacity-40"
                style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.3)' }}
              >
                {converting ? 'Converting...' : 'Convert All Assets to USDT'}
              </button>
            )}
          </>
        )}
      </div>

      {health && (
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-sm font-semibold mb-3">Configuration</div>
          <InfoRow label="Trading Mode" value={health.mode} />
          <InfoRow label="Paper Trading" value={isLive ? 'Disabled' : 'Enabled'} />
          <InfoRow label="Testnet" value={liveMode?.use_testnet ? 'Yes' : 'No (Live)'} />
          <InfoRow label="Last Health Check" value={new Date(health.timestamp).toLocaleString()} />
        </div>
      )}

      {!isLive && (
        <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
          <div className="text-sm font-semibold mb-1">Paper Capital</div>
          <p className="text-xs mb-4" style={{ color: '#848e9c' }}>
            Set the starting USDT balance for paper trading. Restarting closes all open positions.
          </p>
          <div className="flex items-center gap-3 mb-3">
            <div className="flex items-center gap-1.5 flex-1">
              <span className="text-xs font-medium" style={{ color: '#848e9c' }}>$</span>
              <input
                type="number"
                min="0"
                step="10"
                placeholder="1000"
                value={capitalInput}
                onChange={(e) => setCapitalInput(e.target.value)}
                onBlur={() => {
                  const raw = parseFloat(capitalInput.replace(',', '.'));
                  if (!isNaN(raw) && raw > 0 && capital !== null && raw !== capital) {
                    setSavingCapital(true);
                    api.setCapital(raw).then(() => {
                      setCapital(raw);
                      showToast(`Capital updated to ${raw.toFixed(2)} USDT`);
                    }).catch(() => {
                      showToast('Failed to update capital');
                      setCapitalInput(capital.toFixed(2));
                    }).finally(() => setSavingCapital(false));
                  }
                }}
                className="flex-1 text-xs px-3 py-2 rounded-lg"
                style={{
                  background: '#0d0d0d',
                  border: `1px solid ${savingCapital ? 'rgba(240,185,11,0.4)' : '#2a2a2a'}`,
                  color: '#eaecef',
                  outline: 'none',
                }}
              />
            </div>
            {savingCapital && (
              <span className="text-xs" style={{ color: '#f0b90b' }}>Saving...</span>
            )}
          </div>
          {capital !== null && (
            <div className="text-xs px-3 py-2 rounded-lg" style={{ background: 'rgba(14,203,129,0.06)', color: '#848e9c' }}>
              Current capital: {display(capital)}
            </div>
          )}
        </div>
      )}

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-1">Trading Parameters</div>
        <p className="text-xs mb-4" style={{ color: '#848e9c' }}>
          Changes take effect on the next 60-second bot cycle — no restart required.
        </p>
        <ModeToggle
          value={limits.trading_mode}
          onChange={(v) => handleLimitChange('trading_mode', v)}
          disabled={saving}
        />
        <div className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid #1a1a1a' }}>
          <div>
            <span className="text-xs" style={{ color: '#848e9c' }}>Futures BUY Gate</span>
            <div className="text-xs mt-0.5" style={{ color: '#555', fontSize: 10 }}>
              Auto-managed by ML — bot gates futures longs if win rate falls below 45%
            </div>
          </div>
          <span
            className="px-3 py-1 rounded text-xs font-semibold"
            style={{
              background: limits.disable_futures_buy ? 'rgba(246,70,93,0.12)' : 'rgba(14,203,129,0.1)',
              color: limits.disable_futures_buy ? '#f6465d' : '#0ecb81',
              border: `1px solid ${limits.disable_futures_buy ? 'rgba(246,70,93,0.3)' : 'rgba(14,203,129,0.25)'}`,
            }}
          >
            {limits.disable_futures_buy ? 'Gated by ML' : 'Allowed'}
          </span>
        </div>
        <Stepper
          label="Max concurrent trades"
          value={limits.max_concurrent_trades}
          min={1} max={50}
          onChange={(v) => handleLimitChange('max_concurrent_trades', v)}
          disabled={saving}
          suggestions={concurrentSuggestions}
        />
        <Stepper
          label="Confidence threshold"
          value={limits.confidence_threshold}
          min={0.40} max={0.95} step={0.05}
          suffix=""
          onChange={(v) => handleLimitChange('confidence_threshold', v)}
          disabled={saving}
        />
        <Stepper
          label="Stop loss"
          value={limits.stop_loss_percent}
          min={0.5} max={10} step={0.5}
          suffix="%"
          onChange={(v) => handleLimitChange('stop_loss_percent', v)}
          disabled={saving}
          suggestions={stopLossSuggestions}
        />
        <Stepper
          label="Take profit"
          value={limits.take_profit_percent}
          min={0.5} max={15} step={0.5}
          suffix="%"
          onChange={(v) => handleLimitChange('take_profit_percent', v)}
          disabled={saving}
          suggestions={takeProfitSuggestions}
        />
        <Stepper
          label="Kill switch (daily loss)"
          value={limits.daily_loss_limit_percent}
          min={2} max={25} step={1}
          suffix="%"
          onChange={(v) => handleLimitChange('daily_loss_limit_percent', v)}
          disabled={saving}
        />
        <Stepper
          label="Futures leverage"
          value={limits.futures_leverage}
          min={1} max={5}
          suffix="x"
          onChange={(v) => handleLimitChange('futures_leverage', v)}
          disabled={saving}
        />
        <Stepper
          label="Negative trade timeout"
          value={limits.negative_trade_timeout_minutes}
          min={5} max={120} step={5}
          suffix=" min"
          onChange={(v) => handleLimitChange('negative_trade_timeout_minutes', v)}
          disabled={saving}
        />
      </div>

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-1">Trade Targets</div>
        <p className="text-xs mb-4" style={{ color: '#848e9c' }}>
          When below target, the bot lowers its confidence threshold to find more trades. Set to 0 to disable.
        </p>
        <Stepper
          label="Min trades per 24h"
          value={limits.min_daily_trades}
          min={0}
          max={100}
          zeroLabel="No minimum"
          onChange={(v) => handleLimitChange('min_daily_trades', v)}
          disabled={saving}
          suggestions={minDailySuggestions}
        />
        <Stepper
          label="Min simultaneous trades"
          value={limits.min_concurrent_trades}
          min={0}
          max={50}
          zeroLabel="No minimum"
          onChange={(v) => handleLimitChange('min_concurrent_trades', v)}
          disabled={saving}
          suggestions={minConcurrentSuggestions}
        />
        <Stepper
          label="Max trades per 24h (cap)"
          value={limits.max_daily_trades}
          min={0}
          max={500}
          step={10}
          zeroLabel="No cap"
          onChange={(v) => handleLimitChange('max_daily_trades', v)}
          disabled={saving}
        />
      </div>

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-1">Trading Goal</div>
        <p className="text-xs mb-4" style={{ color: '#848e9c' }}>
          Set a profit target. The bot will adaptively lower its confidence threshold to find more trades when behind pace, and raise it when the goal is achieved.
        </p>
        <div className="flex items-center gap-3 mb-3">
          <div className="flex items-center gap-1.5 flex-1">
            <span className="text-xs font-medium" style={{ color: '#848e9c' }}>
              {currency === 'ZAR' ? 'R' : '$'}
            </span>
            <input
              type="number"
              min="0"
              step="1"
              placeholder={currency === 'ZAR' ? '200' : '10'}
              value={goalInput}
              onChange={(e) => setGoalInput(e.target.value)}
              className="flex-1 text-xs px-3 py-2 rounded-lg"
              style={{
                background: '#0d0d0d',
                border: '1px solid #2a2a2a',
                color: '#eaecef',
                outline: 'none',
              }}
            />
          </div>
          <span className="text-xs px-3 py-1.5 rounded-lg" style={{ background: '#0d0d0d', border: '1px solid #2a2a2a', color: '#848e9c', whiteSpace: 'nowrap' }}>
            7 days
          </span>
          <button
            onClick={handleSaveGoal}
            disabled={savingGoal}
            className="px-3 py-2 rounded-lg text-xs font-medium disabled:opacity-40"
            style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.3)' }}
          >
            {savingGoal ? 'Saving...' : 'Save'}
          </button>
        </div>
        {goal.max_allowed_usdt > 0 && (
          <div className="text-xs mb-2" style={{ color: '#555' }}>
            Max realistic: {display(goal.max_allowed_usdt)} based on current capital
          </div>
        )}
        {goal.amount_usdt > 0 && (
          <div className="text-xs px-3 py-2 rounded-lg" style={{ background: 'rgba(14,203,129,0.06)', color: '#848e9c' }}>
            Active goal: {display(goal.amount_usdt)} over 7 days
            {tooltip(goal.amount_usdt) && <span className="ml-1" style={{ color: '#555' }}>· {tooltip(goal.amount_usdt)}</span>}
          </div>
        )}
      </div>

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-3">Risk Parameters</div>
        <InfoRow label="Starting Capital" value={capital !== null ? display(capital) : 'Loading...'} />
        <InfoRow label="Daily Profit Target" value="+2% / day" />
        <InfoRow label="Confidence Threshold" value={`>=${(limits.confidence_threshold * 100).toFixed(0)}%`} />
        <InfoRow label="Stop Loss" value={`${limits.stop_loss_percent}%`} />
        <InfoRow label="Take Profit" value={`${limits.take_profit_percent}%`} />
        <InfoRow label="Kill Switch" value={`-${limits.daily_loss_limit_percent}% daily P&L`} />
        <InfoRow label="Trading Mode" value={limits.trading_mode === 'SPOT' ? 'Spot only' : `Dynamic ${limits.futures_leverage}x leverage`} />
        <InfoRow label="Max Concurrent Trades" value={String(limits.max_concurrent_trades)} />
        <InfoRow label="Loss Timeout" value={`${limits.negative_trade_timeout_minutes} min`} />
        <InfoRow label="Min Notional" value="$11 USDT" />
      </div>

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-3">Futures Mode Conditions</div>
        <InfoRow label="Min Confidence" value=">= 75%" />
        <InfoRow label="Max ATR%" value="<= 5%" />
        <InfoRow label="Min ADX" value=">= 25" />
        <div className="mt-3 text-xs p-3 rounded-lg" style={{ background: 'rgba(240,185,11,0.08)', color: '#848e9c' }}>
          All 3 conditions must pass simultaneously to use FUTURES mode. Otherwise SPOT is used.
        </div>
      </div>

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-1">Notifications</div>
        <p className="text-xs mb-4" style={{ color: '#848e9c' }}>
          Choose which events trigger browser notifications and toasts.
        </p>
        <NotificationFilterSelector />
        <div className="mt-3">
          <NotificationPermissionButton />
        </div>
      </div>

      <div className="rounded-xl p-4" style={{ background: '#111111', border: '1px solid #1f1f1f' }}>
        <div className="text-sm font-semibold mb-3">About</div>
        <InfoRow label="Version" value="v1.0.0" />
        <InfoRow label="Exchange" value="Binance (ccxt)" />
        <InfoRow label="ML Model" value="XGBoost Regressor + Classifier" />
      </div>

      <div className="rounded-xl p-5" style={{ background: '#111111', border: '1px solid rgba(246,70,93,0.3)' }}>
        <div className="flex items-center justify-between mb-1">
          <div className="text-sm font-semibold" style={{ color: '#f6465d' }}>Hard Reset</div>
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: 'rgba(246,70,93,0.1)', color: '#f6465d' }}>Destructive</span>
        </div>
        <p className="text-xs mb-3" style={{ color: '#848e9c' }}>
          Closes all open positions and resets paper capital, daily P&amp;L, and bot state. Trade history and ML learning data are preserved.
        </p>
        {hardResetMsg && (
          <div className="text-xs mb-3 px-3 py-2 rounded" style={{ background: 'rgba(14,203,129,0.08)', color: '#0ecb81', border: '1px solid rgba(14,203,129,0.2)' }}>
            {hardResetMsg}
          </div>
        )}
        <button
          onClick={() => setShowHardResetModal(true)}
          className="px-4 py-2 rounded-lg text-xs font-semibold"
          style={{ background: 'rgba(246,70,93,0.12)', color: '#f6465d', border: '1px solid rgba(246,70,93,0.3)' }}
        >
          Reset Bot State
        </button>
      </div>

      {showLiveModeModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.7)' }}
          onClick={() => setShowLiveModeModal(false)}
        >
          <div
            className="rounded-xl p-6 max-w-sm w-full mx-4"
            style={{ background: '#111111', border: '1px solid rgba(14,203,129,0.4)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-sm font-semibold mb-2" style={{ color: '#0ecb81' }}>Switch to Live Trading?</div>
            <p className="text-xs mb-2" style={{ color: '#848e9c' }}>
              This will enable real money trading on Binance mainnet. The bot will place actual market orders using your account balance.
            </p>
            <div className="text-xs mb-4 p-3 rounded-lg" style={{ background: 'rgba(246,70,93,0.08)', color: '#f6465d', border: '1px solid rgba(246,70,93,0.2)' }}>
              Real money is at risk. Make sure you understand the bot{"'"}s strategy and have reviewed your risk parameters before proceeding.
            </div>
            <div className="flex items-center gap-3 justify-end">
              <button
                onClick={() => setShowLiveModeModal(false)}
                className="px-3 py-2 rounded-lg text-xs font-medium"
                style={{ background: '#1a1a1a', color: '#848e9c', border: '1px solid #2a2a2a' }}
              >
                Cancel
              </button>
              <button
                onClick={handleSwitchToLive}
                disabled={switchingMode}
                className="px-3 py-2 rounded-lg text-xs font-medium disabled:opacity-50"
                style={{ background: 'rgba(14,203,129,0.2)', color: '#0ecb81', border: '1px solid rgba(14,203,129,0.4)' }}
              >
                {switchingMode ? 'Switching...' : 'Yes, Go Live'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showHardResetModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.7)' }}
          onClick={() => setShowHardResetModal(false)}
        >
          <div
            className="rounded-xl p-6 max-w-sm w-full mx-4"
            style={{ background: '#111111', border: '1px solid rgba(246,70,93,0.4)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-sm font-semibold mb-2" style={{ color: '#f6465d' }}>Reset Paper Session?</div>
            <p className="text-xs mb-2" style={{ color: '#848e9c' }}>
              This resets your paper trading session — clears positions, P&L, and capital back to the starting value.
            </p>
            <div className="text-xs mb-4 space-y-1">
              <div className="flex items-center gap-2" style={{ color: '#0ecb81' }}>
                <span>✓</span><span>Trade history archived — bot keeps learning from it</span>
              </div>
              <div className="flex items-center gap-2" style={{ color: '#0ecb81' }}>
                <span>✓</span><span>ML models and candle data preserved</span>
              </div>
              <div className="flex items-center gap-2" style={{ color: '#f6465d' }}>
                <span>✗</span><span>Open positions closed and removed from view</span>
              </div>
              <div className="flex items-center gap-2" style={{ color: '#f6465d' }}>
                <span>✗</span><span>Paper capital, P&L, and daily stats reset to zero</span>
              </div>
            </div>
            <div className="flex items-center gap-3 justify-end">
              <button
                onClick={() => setShowHardResetModal(false)}
                className="px-3 py-2 rounded-lg text-xs font-medium"
                style={{ background: '#1a1a1a', color: '#848e9c', border: '1px solid #2a2a2a' }}
              >
                Cancel
              </button>
              <button
                onClick={handleHardReset}
                disabled={hardResetting}
                className="px-3 py-2 rounded-lg text-xs font-medium disabled:opacity-50"
                style={{ background: 'rgba(246,70,93,0.2)', color: '#f6465d', border: '1px solid rgba(246,70,93,0.4)' }}
              >
                {hardResetting ? 'Resetting...' : 'Yes, Reset Bot State'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showRestartModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.7)' }}
          onClick={() => setShowRestartModal(false)}
        >
          <div
            className="rounded-xl p-6 max-w-sm w-full mx-4"
            style={{ background: '#111111', border: '1px solid #1f1f1f' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-sm font-semibold mb-2">Restart Bot?</div>
            <p className="text-xs mb-4" style={{ color: '#848e9c' }}>
              Reset paper capital to {display(parseFloat(capitalInput || '0'))} and restart the bot? All open positions will be closed.
            </p>
            <div className="flex items-center gap-3 justify-end">
              <button
                onClick={() => setShowRestartModal(false)}
                className="px-3 py-2 rounded-lg text-xs font-medium"
                style={{ background: '#1a1a1a', color: '#848e9c', border: '1px solid #2a2a2a' }}
              >
                Cancel
              </button>
              <button
                onClick={handleRestartCapital}
                className="px-3 py-2 rounded-lg text-xs font-medium"
                style={{ background: 'rgba(246,70,93,0.15)', color: '#f6465d', border: '1px solid rgba(246,70,93,0.3)' }}
              >
                Restart
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
