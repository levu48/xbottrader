import type { FastifyInstance, FastifyReply } from 'fastify';
import { BotEngineClient, BotEngineError } from '../clients/bot.js';
import type { RequireUser } from '../auth/middleware.js';

interface Deps {
  bot: BotEngineClient;
  requireUser: RequireUser;
}

function upstreamError(reply: FastifyReply, e: unknown): never | void {
  if (e instanceof BotEngineError) {
    return void reply.status(e.status).send({ error: 'bot_engine_error', upstream: e.upstream });
  }
  throw e;
}

const MAX_LIMIT = 1000;
const DEFAULT_LIMIT = 200;

export async function registerMarketRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  const { requireUser } = deps;

  // Public OHLCV candles for the dashboard chart. Proxies to the Bot Engine,
  // which owns the ccxt clients; the candles themselves are public market data.
  app.get('/v1/market/ohlcv', { preHandler: requireUser }, async (req, reply) => {
    const q = req.query as Record<string, string | undefined>;
    const symbol = q.symbol;
    if (!symbol) {
      return reply.status(400).send({ error: 'symbol_required' });
    }
    const exchange = q.exchange || 'binance';
    const timeframe = q.timeframe || '1m';
    const parsed = Number(q.limit);
    const limit = Number.isFinite(parsed)
      ? Math.min(Math.max(Math.trunc(parsed), 1), MAX_LIMIT)
      : DEFAULT_LIMIT;

    try {
      return reply.send(
        await deps.bot.getOhlcv({ userId: req.userId!, exchange, symbol, timeframe, limit }),
      );
    } catch (e) {
      return upstreamError(reply, e);
    }
  });
}
