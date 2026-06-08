import { z } from 'zod';
import { Fill, Order } from './order.js';
import { Decimal, IsoTimestamp, Side, Symbol_, Uuid } from './primitives.js';

export const SignalEvent = z.object({
  event_type: z.literal('signal'),
  user_id: Uuid,
  bot_id: Uuid,
  ts: IsoTimestamp,
  payload: z.object({
    symbol: Symbol_,
    side: Side,
    confidence: z.number().min(0).max(1).optional(),
    reason: z.string(),
  }),
});

export const OrderSubmittedEvent = z.object({
  event_type: z.literal('order_submitted'),
  user_id: Uuid,
  bot_id: Uuid,
  ts: IsoTimestamp,
  payload: Order,
});

export const FillEvent = z.object({
  event_type: z.literal('fill'),
  user_id: Uuid,
  bot_id: Uuid,
  ts: IsoTimestamp,
  payload: Fill,
});

export const BotLifecycleEvent = z.object({
  // bot_idle / bot_resumed are emitted by equity bots when the venue's market
  // closes/reopens (the bot is alive but not trading while closed).
  event_type: z.enum([
    'bot_started',
    'bot_stopped',
    'bot_paused',
    'bot_killed',
    'bot_idle',
    'bot_resumed',
  ]),
  user_id: Uuid,
  bot_id: Uuid,
  ts: IsoTimestamp,
  payload: z.object({ reason: z.string().optional() }),
});

export const BotErrorEvent = z.object({
  event_type: z.literal('bot_error'),
  user_id: Uuid,
  bot_id: Uuid,
  ts: IsoTimestamp,
  payload: z.object({
    error_class: z.string(),
    message: z.string(),
    fatal: z.boolean(),
  }),
});

export const LogLineEvent = z.object({
  event_type: z.literal('log_line'),
  user_id: Uuid,
  bot_id: Uuid,
  ts: IsoTimestamp,
  payload: z.object({
    level: z.enum(['debug', 'info', 'warn', 'error']),
    message: z.string(),
  }),
});

export const EquityPointEvent = z.object({
  event_type: z.literal('equity_point'),
  user_id: Uuid,
  bot_id: Uuid,
  ts: IsoTimestamp,
  payload: z.object({
    equity_quote: Decimal,
    realized_pnl_quote: Decimal,
    unrealized_pnl_quote: Decimal,
  }),
});

export const EventEnvelope = z.discriminatedUnion('event_type', [
  SignalEvent,
  OrderSubmittedEvent,
  FillEvent,
  BotLifecycleEvent,
  BotErrorEvent,
  LogLineEvent,
  EquityPointEvent,
]);
export type EventEnvelope = z.infer<typeof EventEnvelope>;
