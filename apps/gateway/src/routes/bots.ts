import type { FastifyInstance } from 'fastify';
import { BotEngineClient, BotEngineError, StartBotRequest } from '../clients/bot.js';
import type { RequireUser } from '../auth/middleware.js';
import type { KeyStore, SubscriptionStore, UserStore } from '../db/repos.js';

interface Deps {
  bot: BotEngineClient;
  requireUser: RequireUser;
  keys: KeyStore;
  users: UserStore;
  subs: SubscriptionStore;
}

function upstreamError(reply: import('fastify').FastifyReply, e: unknown): never | void {
  if (e instanceof BotEngineError) {
    return void reply.status(e.status).send({ error: 'bot_engine_error', upstream: e.upstream });
  }
  throw e;
}

export async function registerBotsRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  const { requireUser } = deps;

  app.post('/v1/bots/:id/start', { preHandler: requireUser }, async (req, reply) => {
    const userId = req.userId!;
    const parsed = StartBotRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'invalid_request', details: parsed.error.format() });
    }
    let body = parsed.data;

    // Freemium gate: live trading and ai_signal (which calls the LLM even in
    // paper mode) require an active subscription. Paper bots on the built-in
    // deterministic strategies stay free.
    if (body.mode === 'live' || body.strategy.strategy_type === 'ai_signal') {
      if (!(await deps.subs.isActive(userId))) {
        return reply.status(403).send({ error: 'subscription_required' });
      }
    }

    // Live trading: require 2FA + a stored key, and inject the encrypted envelope
    // for the chosen exchange. The Bot Engine is the only thing that decrypts it.
    if (body.mode === 'live') {
      const user = await deps.users.findById(userId);
      if (!user?.totpEnabled) {
        return reply.status(403).send({ error: '2fa_required_for_live' });
      }
      const exchange = body.exchange ?? 'binance';
      const envelope = await deps.keys.getEnvelope(userId, exchange);
      if (!envelope) {
        return reply.status(400).send({ error: 'no_api_key_for_exchange', exchange });
      }
      body = { ...body, credentials: envelope };
    }

    const botId = (req.params as { id: string }).id;
    try {
      return reply.send(await deps.bot.startBot({ userId, botId, body }));
    } catch (e) {
      return upstreamError(reply, e);
    }
  });

  app.post('/v1/bots/:id/stop', { preHandler: requireUser }, async (req, reply) => {
    const botId = (req.params as { id: string }).id;
    try {
      return reply.send(await deps.bot.stopBot({ userId: req.userId!, botId }));
    } catch (e) {
      return upstreamError(reply, e);
    }
  });

  // Global kill switch. Static path — registered before `:id` so the radix
  // router never treats "kill-all" as a bot id.
  app.post('/v1/bots/kill-all', { preHandler: requireUser }, async (req, reply) => {
    try {
      return reply.send(await deps.bot.killAll({ userId: req.userId! }));
    } catch (e) {
      return upstreamError(reply, e);
    }
  });

  app.post('/v1/bots/:id/kill', { preHandler: requireUser }, async (req, reply) => {
    const botId = (req.params as { id: string }).id;
    try {
      return reply.send(await deps.bot.killBot({ userId: req.userId!, botId }));
    } catch (e) {
      return upstreamError(reply, e);
    }
  });

  app.get('/v1/bots/:id', { preHandler: requireUser }, async (req, reply) => {
    const botId = (req.params as { id: string }).id;
    try {
      return reply.send(await deps.bot.getBot({ userId: req.userId!, botId }));
    } catch (e) {
      return upstreamError(reply, e);
    }
  });
}
