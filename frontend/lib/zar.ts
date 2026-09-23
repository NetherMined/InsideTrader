import { useState, useEffect } from 'react';

let _cachedRate: number | null = null;
let _cacheTime = 0;
const CACHE_TTL = 3_600_000;

export function useZarRate(): number | null {
  const [rate, setRate] = useState<number | null>(_cachedRate);

  useEffect(() => {
    const now = Date.now();
    if (_cachedRate && now - _cacheTime < CACHE_TTL) {
      setRate(_cachedRate);
      return;
    }
    fetch('https://open.er-api.com/v6/latest/USD')
      .then((r) => r.json())
      .then((data) => {
        if (data.result === 'success' && data.rates?.ZAR) {
          _cachedRate = data.rates.ZAR;
          _cacheTime = Date.now();
          setRate(_cachedRate);
        }
      })
      .catch(() => {});
  }, []);

  return rate;
}

export function fmtZar(usd: number | null | undefined, rate: number | null): string {
  if (usd == null || rate == null) return '';
  const zar = usd * rate;
  if (Math.abs(zar) >= 1000) {
    return `R${zar.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  return `R${zar.toFixed(2)}`;
}
