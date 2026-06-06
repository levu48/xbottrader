import { z } from 'zod';
import { Decimal, Exchange, IsoTimestamp, Mode, Side, Symbol_, Uuid } from './primitives.js';

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

// Custom rule-engine strategy: user-authored logic as data. Mirrors the DSL in
// xbt_core.strategies.rule_engine and the pydantic models in the Bot Engine.
// Shape only — the Bot Engine is the source of truth for deeper checks (e.g.
// that every referenced indicator is defined) and surfaces them as a 400.
export const IndicatorSpec = z.object({
  name: z.string().min(1),
  fn: z.enum(['price', 'value', 'sma', 'rsi']),
  period: z.number().int().min(0).default(0),
  value: Decimal.optional(),
});

// Recursive: a condition is either a comparison or a boolean combinator over
// sub-conditions. The explicit type annotation is required for z.lazy recursion.
export type Condition =
  | { op: '<' | '<=' | '>' | '>=' | '==' | 'crossover' | 'crossunder'; left: string; right: string }
  | { op: 'and' | 'or' | 'not'; terms: Condition[] };

export const Condition: z.ZodType<Condition> = z.lazy(() =>
  z.union([
    z.object({
      op: z.enum(['<', '<=', '>', '>=', '==', 'crossover', 'crossunder']),
      left: z.string(),
      right: z.string(),
    }),
    z.object({ op: z.enum(['and', 'or', 'not']), terms: z.array(Condition).min(1) }),
  ]),
);

export const ActionSpec = z.object({
  side: Side,
  type: z.enum(['market', 'limit']).default('market'),
  quote: Decimal,
  limit_offset_pct: Decimal.optional(),
});

export const RuleSpec = z.object({
  when: Condition,
  do: ActionSpec,
  cooldown_minutes: z.number().int().min(0).default(0),
});

export const CustomRulesStrategy = z.object({
  strategy_type: z.literal('custom_rules'),
  symbol: Symbol_,
  indicators: z.array(IndicatorSpec).default([]),
  rules: z.array(RuleSpec).min(1),
});

export const StrategyConfig = z.discriminatedUnion('strategy_type', [
  DcaStrategy,
  GridStrategy,
  MaCrossoverStrategy,
  CustomRulesStrategy,
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
