'use client';

import { useEffect, useRef, useState } from 'react';
import { useCurrency } from '@/lib/currency';
import { WS_EVENTS_URL } from '@/lib/api';

export type NotifFilter = 'all' | 'wins' | 'goal' | 'none';
const NOTIF_FILTER_KEY = 'notif_filter';

function getFilter(): NotifFilter {
  if (typeof window === 'undefined') return 'all';
  return (localStorage.getItem(NOTIF_FILTER_KEY) as NotifFilter) ?? 'all';
}

function shouldShow(filter: NotifFilter, type: string, data: Record<string, unknown>): boolean {
  if (filter === 'none') return false;
  if (filter === 'goal') return type === 'daily_target' || type === 'kill_switch';
  if (filter === 'wins') {
    if (type === 'trade_opened') return false;
    if (type === 'trade_closed') return Number(data.pnl_usdt) >= 0;
    return true; // kill_switch, daily_target always shown
  }
  return true; // 'all'
}

interface ToastItem {
  id: number;
  type: string;
  title: string;
  body: string;
  color: string;
}

const EVENT_CONFIG: Record<string, { title: string; color: string; icon: string }> = {
  trade_opened: { title: 'Trade Opened', color: '#0ecb81', icon: '📈' },
  trade_closed: { title: 'Trade Closed', color: '#f0b90b', icon: '📊' },
  kill_switch:  { title: 'Kill Switch!', color: '#f6465d', icon: '🚨' },
  daily_target: { title: 'Target Reached', color: '#0ecb81', icon: '🎯' },
};

function formatBody(type: string, data: Record<string, unknown>, display: (n: number) => string): string {
  if (type === 'trade_opened') {
    return `${data.symbol} ${data.mode} @ $${Number(data.price).toFixed(4)}`;
  }
  if (type === 'trade_closed') {
    const pnl = Number(data.pnl_usdt);
    const sign = pnl >= 0 ? '+' : '';
    return `${data.symbol} ${sign}${display(pnl)} (${sign}${Number(data.pnl_pct).toFixed(2)}%)`;
  }
  if (type === 'kill_switch') {
    return `Daily loss: ${Number(data.daily_loss_pct).toFixed(2)}% — defensive mode active`;
  }
  if (type === 'daily_target') {
    return `P&L: +${Number(data.daily_pnl_pct).toFixed(2)}%`;
  }
  return '';
}

let _toastId = 0;

export function ToastProvider() {
  const { display } = useCurrency();
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const notifPermission = useRef<NotificationPermission>('default');

  useEffect(() => {
    if (typeof Notification !== 'undefined') {
      notifPermission.current = Notification.permission;
    }

    const connect = () => {
      const ws = new WebSocket(WS_EVENTS_URL);
      wsRef.current = ws;

      ws.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          const cfg = EVENT_CONFIG[event.type];
          if (!cfg) return;

          const filter = getFilter();
          if (!shouldShow(filter, event.type, event)) return;

          const body = formatBody(event.type, event, (n) => display(n));
          const id = ++_toastId;

          setToasts((prev) => [
            ...prev,
            { id, type: event.type, title: `${cfg.icon} ${cfg.title}`, body, color: cfg.color },
          ]);

          setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 6000);

          if (notifPermission.current === 'granted') {
            new Notification(`InsideTrader — ${cfg.title}`, { body, icon: '/favicon.ico' });
          }
        } catch {}
      };

      ws.onclose = () => {
        setTimeout(connect, 5000);
      };
    };

    connect();
    return () => wsRef.current?.close();
  }, []);

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 16,
        right: 16,
        zIndex: 9999,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        pointerEvents: 'none',
      }}
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          style={{
            background: '#1a1a1a',
            border: `1px solid ${t.color}44`,
            borderLeft: `3px solid ${t.color}`,
            borderRadius: 10,
            padding: '10px 14px',
            minWidth: 240,
            maxWidth: 320,
            boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
            animation: 'slideIn 0.2s ease',
            pointerEvents: 'auto',
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, color: t.color }}>{t.title}</div>
          <div style={{ fontSize: 11, color: '#848e9c', marginTop: 2 }}>{t.body}</div>
        </div>
      ))}
      <style>{`
        @keyframes slideIn {
          from { transform: translateX(20px); opacity: 0; }
          to   { transform: translateX(0);   opacity: 1; }
        }
      `}</style>
    </div>
  );
}

export function NotificationPermissionButton() {
  const [permission, setPermission] = useState<NotificationPermission>('default');

  useEffect(() => {
    if (typeof Notification !== 'undefined') {
      setPermission(Notification.permission);
    }
  }, []);

  if (typeof Notification === 'undefined' || permission === 'granted') return null;

  const request = async () => {
    const result = await Notification.requestPermission();
    setPermission(result);
  };

  return (
    <button
      onClick={request}
      className="px-3 py-1.5 rounded-lg text-xs font-medium"
      style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', border: '1px solid rgba(240,185,11,0.3)' }}
    >
      {permission === 'denied' ? 'Notifications blocked in browser' : '🔔 Enable Browser Notifications'}
    </button>
  );
}

const FILTER_OPTIONS: { value: NotifFilter; label: string; desc: string }[] = [
  { value: 'all',  label: 'All',        desc: 'Trade opens, closes, kill switch, goal hit' },
  { value: 'wins', label: 'Wins only',  desc: 'Profitable closes, kill switch, goal hit' },
  { value: 'goal', label: 'Goal & alerts', desc: 'Goal reached and kill switch only' },
  { value: 'none', label: 'Off',        desc: 'No notifications' },
];

export function NotificationFilterSelector() {
  const [filter, setFilter] = useState<NotifFilter>('all');

  useEffect(() => {
    setFilter(getFilter());
  }, []);

  const select = (v: NotifFilter) => {
    setFilter(v);
    localStorage.setItem(NOTIF_FILTER_KEY, v);
  };

  return (
    <div className="flex flex-col gap-2">
      {FILTER_OPTIONS.map((opt) => {
        const active = filter === opt.value;
        return (
          <button
            key={opt.value}
            onClick={() => select(opt.value)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '8px 12px',
              borderRadius: 8,
              border: active ? '1px solid rgba(240,185,11,0.4)' : '1px solid #2a2a2a',
              background: active ? 'rgba(240,185,11,0.08)' : 'transparent',
              cursor: 'pointer',
              textAlign: 'left',
              width: '100%',
            }}
          >
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: active ? '#f0b90b' : '#eaecef' }}>
                {opt.label}
              </div>
              <div style={{ fontSize: 11, color: '#555', marginTop: 1 }}>{opt.desc}</div>
            </div>
            <div
              style={{
                width: 14,
                height: 14,
                borderRadius: '50%',
                border: active ? '4px solid #f0b90b' : '2px solid #444',
                flexShrink: 0,
              }}
            />
          </button>
        );
      })}
    </div>
  );
}
