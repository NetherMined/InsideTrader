import type { Metadata } from 'next';
import './globals.css';
import { Sidebar } from '@/components/Sidebar';
import { ToastProvider } from '@/components/Toast';
import { Providers } from '@/components/Providers';

export const metadata: Metadata = {
  title: 'InsideTrader',
  description: 'Binance auto trading bot management dashboard',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{ background: '#0a0a0a', color: '#e8e8e8', margin: 0 }}
        className="flex min-h-screen"
      >
        <Providers>
          <Sidebar />
          <main className="flex-1 min-h-screen overflow-auto">{children}</main>
          <ToastProvider />
        </Providers>
      </body>
    </html>
  );
}
