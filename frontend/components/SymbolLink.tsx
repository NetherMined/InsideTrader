'use client';

import { createContext, useContext, useState } from 'react';
import { SymbolModal } from '@/components/SymbolModal';

const SymbolModalContext = createContext<(symbol: string) => void>(() => {});

export function useSymbolModal() {
  return useContext(SymbolModalContext);
}

export function SymbolModalProvider({ children }: { children: React.ReactNode }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  return (
    <SymbolModalContext.Provider value={setSymbol}>
      {children}
      {symbol && <SymbolModal symbol={symbol} onClose={() => setSymbol(null)} />}
    </SymbolModalContext.Provider>
  );
}

export function SymbolLink({ symbol, className }: { symbol: string; className?: string }) {
  const open = useSymbolModal();
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        open(symbol);
      }}
      title={`View ${symbol} details and open a position`}
      className={`cursor-pointer hover:underline focus:outline-none focus-visible:underline ${className ?? ''}`}
    >
      {symbol}
    </button>
  );
}
