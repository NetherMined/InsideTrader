'use client';

import { useState } from 'react';
import { Play, Pause, Square, AlertTriangle, RotateCcw } from 'lucide-react';
import { api } from '@/lib/api';
import type { BotStatus } from '@/lib/types';

interface Props {
  status: BotStatus | null;
  onUpdate: () => void;
}

export function BotControls({ status, onUpdate }: Props) {
  const [loading, setLoading] = useState(false);

  const send = async (cmd: 'stop' | 'pause' | 'resume') => {
    setLoading(true);
    try {
      await api.botCommand(cmd);
      setTimeout(onUpdate, 800);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const resetKillSwitch = async () => {
    setLoading(true);
    try {
      await api.resetKillSwitch();
      setTimeout(onUpdate, 800);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const state = status?.state ?? 'unknown';
  const running = state === 'running';
  const paused = state === 'paused';
  const stopped = state === 'stopped' || state === 'unknown';
  const defensive = state === 'defensive' || status?.defensive_mode;
  const killSwitch = state === 'kill_switch' || (status?.kill_switch && !defensive);

  return (
    <div className="flex items-center gap-2">
      {defensive && (
        <button
          onClick={resetKillSwitch}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-opacity disabled:opacity-50"
          style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.3)', cursor: 'pointer' }}
          title="Bot switched to defensive strategy — click to restore normal strategy now"
        >
          <AlertTriangle size={13} />
          DEFENSIVE
          <RotateCcw size={11} style={{ marginLeft: 2 }} />
        </button>
      )}
      {killSwitch && (
        <div
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
          style={{ background: 'rgba(246,70,93,0.15)', color: '#f6465d' }}
        >
          <AlertTriangle size={13} />
          EMERGENCY STOP
        </div>
      )}

      {!running && !killSwitch && (
        <button
          onClick={() => send('resume')}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-opacity disabled:opacity-50"
          style={{ background: 'rgba(14,203,129,0.15)', color: '#0ecb81' }}
        >
          <Play size={13} />
          {stopped ? 'Start' : 'Resume'}
        </button>
      )}

      {running && (
        <button
          onClick={() => send('pause')}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-opacity disabled:opacity-50"
          style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b' }}
        >
          <Pause size={13} />
          Pause
        </button>
      )}

      {(running || paused || defensive) && (
        <button
          onClick={() => send('stop')}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-opacity disabled:opacity-50"
          style={{ background: 'rgba(246,70,93,0.1)', color: '#f6465d' }}
        >
          <Square size={13} />
          Stop
        </button>
      )}

      <StateBadge state={state} paper={status?.paper ?? true} />
    </div>
  );
}

function StateBadge({ state, paper }: { state: string; paper: boolean }) {
  const cfg: Record<string, { bg: string; color: string; label: string }> = {
    running: { bg: 'rgba(14,203,129,0.12)', color: '#0ecb81', label: 'RUNNING' },
    paused: { bg: 'rgba(240,185,11,0.12)', color: '#f0b90b', label: 'PAUSED' },
    stopped: { bg: 'rgba(132,142,156,0.12)', color: '#848e9c', label: 'STOPPED' },
    defensive: { bg: 'rgba(240,185,11,0.12)', color: '#f0b90b', label: 'DEFENSIVE' },
    kill_switch: { bg: 'rgba(246,70,93,0.12)', color: '#f6465d', label: 'EMERGENCY STOP' },
    unknown: { bg: 'rgba(132,142,156,0.12)', color: '#848e9c', label: 'OFFLINE' },
  };
  const c = cfg[state] ?? cfg.unknown;

  return (
    <div className="flex items-center gap-1.5">
      <span
        className="px-2 py-1 rounded text-xs font-semibold"
        style={{ background: c.bg, color: c.color }}
      >
        {c.label}
      </span>
      {paper && (
        <span
          className="px-2 py-1 rounded text-xs font-medium"
          style={{ background: 'rgba(132,142,156,0.1)', color: '#848e9c' }}
        >
          PAPER
        </span>
      )}
    </div>
  );
}
