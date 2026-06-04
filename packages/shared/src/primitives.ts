import { z } from 'zod';

export const Uuid = z.string().uuid();
export type Uuid = z.infer<typeof Uuid>;

export const Decimal = z
  .string()
  .regex(/^-?\d+(\.\d+)?$/, 'expected decimal string (e.g., "0.001")');
export type Decimal = z.infer<typeof Decimal>;

export const IsoTimestamp = z.string().datetime();
export type IsoTimestamp = z.infer<typeof IsoTimestamp>;

export const Exchange = z.enum(['binance', 'coinbase']);
export type Exchange = z.infer<typeof Exchange>;

export const Symbol_ = z.string().regex(/^[A-Z0-9]+\/[A-Z0-9]+$/, 'expected "BASE/QUOTE"');
export type Symbol_ = z.infer<typeof Symbol_>;

export const Side = z.enum(['buy', 'sell']);
export type Side = z.infer<typeof Side>;

export const Mode = z.enum(['paper', 'live']);
export type Mode = z.infer<typeof Mode>;
