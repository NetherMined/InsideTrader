'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard, TrendingUp, Sparkles,
  Activity, History, BarChart2, Settings, CandlestickChart, UserCircle,
} from 'lucide-react';
import { useCurrency } from '@/lib/currency';

const NAV = [
  { href: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { href: '/user-stats', icon: UserCircle, label: 'My Stats' },
  { href: '/markets', icon: TrendingUp, label: 'Markets' },
  { href: '/predictions', icon: Sparkles, label: 'Predictions' },
  { href: '/positions', icon: Activity, label: 'Positions' },
  { href: '/history', icon: History, label: 'History' },
  { href: '/charts', icon: CandlestickChart, label: 'Charts' },
  { href: '/backtests', icon: BarChart2, label: 'Backtests' },
  { href: '/settings', icon: Settings, label: 'Settings' },
];

export function Sidebar() {
  const pathname = usePathname();
  const { currency, toggle } = useCurrency();

  return (
    <aside
      className="w-52 shrink-0 flex flex-col min-h-screen sticky top-0"
      style={{ background: '#111111', borderRight: '1px solid #1f1f1f' }}
    >
      <div className="p-4 pb-3 border-b" style={{ borderColor: '#1f1f1f' }}>
        <div className="text-base font-bold tracking-tight">
          <span style={{ color: '#f0b90b' }}>Inside</span>Trader
        </div>
        <div className="text-xs mt-0.5" style={{ color: '#848e9c' }}>
          Binance Auto Bot
        </div>
      </div>

      <nav className="flex-1 p-2 space-y-0.5">
        {NAV.map(({ href, icon: Icon, label }) => {
          const active = pathname === href || pathname.startsWith(href + '/');
          return (
            <Link
              key={href}
              href={href}
              className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all"
              style={{
                background: active ? 'rgba(240,185,11,0.12)' : 'transparent',
                color: active ? '#f0b90b' : '#848e9c',
              }}
            >
              <Icon size={15} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="p-3 border-t space-y-2" style={{ borderColor: '#1f1f1f' }}>
        <button
          onClick={toggle}
          title={currency === 'USD' ? 'Switch to ZAR (South African Rand)' : 'Switch to USD (US Dollar)'}
          className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-all"
          style={{ background: 'rgba(240,185,11,0.08)', border: '1px solid rgba(240,185,11,0.2)', color: '#f0b90b' }}
        >
          <span>{currency === 'USD' ? '$ USD' : 'R ZAR'}</span>
          <span style={{ color: '#848e9c', fontSize: 10 }}>click to switch</span>
        </button>
        <div className="text-xs px-1" style={{ color: '#848e9c' }}>
          <div>Phase 5 · Paper Mode</div>
          <div className="mt-0.5">v1.0.0</div>
        </div>
      </div>
    </aside>
  );
}
