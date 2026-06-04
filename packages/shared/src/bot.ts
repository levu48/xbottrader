import { z } from 'zod';
import { Decimal, Exchange, IsoTimestamp, Mode, Symbol_, Uuid } from './primitives.js';

export const DcaStrategy = z.object({
  strategy_type: z.literal('dca'),
  symbol: Symbol_,
  quote_amount: Decimal,
  interval_minutes: z.number().int().positive(),
});

export const GridStrategy = z.object({
  strategy_type: z.literal('grid'),
  symbol: Symbol_,
  lower_price: Decimal,
  upper_price: Decimal,
  grid_levels: z.number().int().min(2).max(200),
  total_quote: Decimal,
});

export const MaCrossoverStrategy = z.object({
  strategy_type: z.literal('ma_crossover'),
  symbol: Symbol_,
  fast_period: z.number().int().min(2),
  slow_period: z.number().int().min(3),
  position_quote: Decimal,
});

export const StrategyConfig = z.discriminatedUnion('strategy_type', [
  DcaStrategy,
  GridStrategy,
  MaCrossoverStrategy,
]);
export type StrategyConfig = z.infer<typeof StrategyConfig>;

export const RiskLimits = z.object({
  max_drawdown_pct: z.number().positive().max(100),
  max_position_quote: Decimal,
});

export const BotStatus = z.enum(['draft', 'active', 'paused', 'stopped', 'errored']);
export type BotStatus = z.infer<typeof BotStatus>;

export const BotConfig = z.object({
  id: Uuid,
  user_id: Uuid,
  name: z.string().min(1).max(80),
  exchange: Exchange,
  mode: Mode,
  strategy: StrategyConfig,
  risk: RiskLimits,
  status: BotStatus,
  created_at: IsoTimestamp,
});
export type BotConfig = z.infer<typeof BotConfig>;
