'use client';

import { CurrencyProvider } from '@/lib/currency';
import { SymbolModalProvider } from '@/components/SymbolLink';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <CurrencyProvider>
      <SymbolModalProvider>{children}</SymbolModalProvider>
    </CurrencyProvider>
  );
}
