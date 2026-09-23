'use client';

import { CurrencyProvider } from '@/lib/currency';

export function Providers({ children }: { children: React.ReactNode }) {
  return <CurrencyProvider>{children}</CurrencyProvider>;
}
