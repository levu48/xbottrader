import { z } from 'zod';

export const Uuid = z.string().uuid();
export type Uuid = z.infer<typeof Uuid>;

export const Decimal = z
  .string()
  .regex(/^-?\d+(\.\d+)?$/, 'expected decimal string (e.g., "0.001")');
export type Decimal = z.infer<typeof Decimal>;

export const IsoTimestamp = z.string().datetime();
export type IsoTimestamp = z.infer<typeof IsoTimestamp>;

export const Exchange = z.enum(['binance', 'coinbase', 'alpaca']);
export type Exchange = z.infer<typeof Exchange>;

export const AssetClass = z.enum(['crypto', 'us_equity']);
export type AssetClass = z.infer<typeof AssetClass>;

// Crypto venues uniquely identify themselves; everything else here is crypto.
export const EQUITY_EXCHANGES: ReadonlySet<string> = new Set(['alpaca']);
export function assetClassFor(exchange: string): AssetClass {
  return EQUITY_EXCHANGES.has(exchange) ? 'us_equity' : 'crypto';
}

// Crypto symbols are BASE/QUOTE (e.g. BTC/USDT); equity symbols are bare tickers
// (e.g. AAPL, BRK.B). `Symbol_` accepts either shape — the venue-aware check
// (which format is valid for which exchange) lives at the request boundary.
export const CryptoSymbol = z.string().regex(/^[A-Z0-9]+\/[A-Z0-9]+$/, 'expected "BASE/QUOTE"');
export type CryptoSymbol = z.infer<typeof CryptoSymbol>;

export const EquitySymbol = z.string().regex(/^[A-Z]{1,5}(\.[A-Z])?$/, 'expected a ticker like AAPL');
export type EquitySymbol = z.infer<typeof EquitySymbol>;

export const Symbol_ = z.union([CryptoSymbol, EquitySymbol]);
export type Symbol_ = z.infer<typeof Symbol_>;

export const Side = z.enum(['buy', 'sell']);
export type Side = z.infer<typeof Side>;

export const Mode = z.enum(['paper', 'live']);
export type Mode = z.infer<typeof Mode>;
