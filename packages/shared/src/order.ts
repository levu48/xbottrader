import { z } from 'zod';
import { Decimal, Exchange, IsoTimestamp, Side, Symbol_, Uuid } from './primitives.js';

export const OrderType = z.enum(['market', 'limit']);
export type OrderType = z.infer<typeof OrderType>;

export const OrderStatus = z.enum([
  'pending',
  'submitted',
  'filled',
  'partially_filled',
  'cancelled',
  'rejected',
]);
export type OrderStatus = z.infer<typeof OrderStatus>;

export const Order = z.object({
  id: Uuid,
  bot_id: Uuid,
  user_id: Uuid,
  exchange: Exchange,
  symbol: Symbol_,
  side: Side,
  type: OrderType,
  quantity: Decimal,
  limit_price: Decimal.nullable(),
  status: OrderStatus,
  exchange_order_id: z.string().nullable(),
  created_at: IsoTimestamp,
});
export type Order = z.infer<typeof Order>;

export const Fill = z.object({
  id: Uuid,
  order_id: Uuid,
  bot_id: Uuid,
  exchange: Exchange,
  symbol: Symbol_,
  side: Side,
  quantity: Decimal,
  price: Decimal,
  fee: Decimal,
  fee_currency: z.string(),
  exchange_fill_id: z.string(),
  filled_at: IsoTimestamp,
});
export type Fill = z.infer<typeof Fill>;
