'use client';

import React, { createContext, useContext, useState, useEffect } from 'react';
import { api } from '@/lib/api';

export type Currency = 'USD' | 'ZAR';

interface CurrencyCtx {
  currency: Currency;
  toggle: () => void;
  zarRate: number | null;
  display: (usd: number | null | undefined, decimals?: number) => string;
  tooltip: (usd: number | null | undefined, decimals?: number) => string;
}

const CurrencyContext = createContext<CurrencyCtx>({
  currency: 'USD',
  toggle: () => {},
  zarRate: null,
  display: (n) => (n == null ? '—' : `$${n.toFixed(2)}`),
  tooltip: () => '',
});

function fmt(n: number, dec: number): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

export function CurrencyProvider({ children }: { children: React.ReactNode }) {
  const [currency, setCurrency] = useState<Currency>('USD');
  const [zarRate, setZarRate] = useState<number | null>(null);

  useEffect(() => {
    try {
      const stored = localStorage.getItem('it_currency') as Currency | null;
      if (stored === 'USD' || stored === 'ZAR') setCurrency(stored);
    } catch {}

    fetch('https://open.er-api.com/v6/latest/USD')
      .then((r) => r.json())
      .then((data) => {
        if (data.result === 'success' && data.rates?.ZAR) {
          setZarRate(data.rates.ZAR);
        }
      })
      .catch(() => {});
  }, []);

  const toggle = () => {
    setCurrency((prev) => {
      const next = prev === 'USD' ? 'ZAR' : 'USD';
      try { localStorage.setItem('it_currency', next); } catch {}
      api.setCurrency(next, zarRate ?? 18.5).catch(() => {});
      return next;
    });
  };

  function display(usd: number | null | undefined, decimals = 2): string {
    if (usd == null) return '—';
    const abs = Math.abs(usd);
    const sign = usd < 0 ? '-' : '';
    if (currency === 'ZAR' && zarRate) {
      return `${sign}R${fmt(abs * zarRate, decimals)}`;
    }
    return `${sign}$${fmt(abs, decimals)}`;
  }

  function tooltip(usd: number | null | undefined, decimals = 2): string {
    if (usd == null || !zarRate) return '';
    const abs = Math.abs(usd);
    if (currency === 'ZAR') return `$${fmt(abs, decimals)}`;
    return `R${fmt(abs * zarRate, decimals)}`;
  }

  return (
    <CurrencyContext.Provider value={{ currency, toggle, zarRate, display, tooltip }}>
      {children}
    </CurrencyContext.Provider>
  );
}

export const useCurrency = () => useContext(CurrencyContext);
