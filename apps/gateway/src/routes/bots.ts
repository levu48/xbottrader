import type { FastifyInstance } from 'fastify';
import { BotEngineClient, BotEngineError, StartBotRequest } from '../clients/bot.js';

interface Deps {
  bot: BotEngineClient;
}

// Placeholder session lookup. Real implementation will read a session cookie
// validated via Lucia/Clerk. For the scaffold we accept `x-dev-user: <id>`.
function userFromRequest(headers: Record<string, string | string[] | undefined>): string | null {
  const raw = headers['x-dev-user'];
  if (typeof raw === 'string' && raw.length > 0) return raw;
  return null;
}

export async function registerBotsRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  app.post('/v1/bots/:id/start', async (req, reply) => {
    const userId = userFromRequest(req.headers);
    if (!userId) return reply.status(401).send({ error: 'unauthenticated' });

    const parsed = StartBotRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'invalid_request', details: parsed.error.format() });
    }
    const botId = (req.params as { id: string }).id;
    try {
      const res = await deps.bot.startBot({ userId, botId, body: parsed.data });
      return reply.send(res);
    } catch (e) {
      if (e instanceof BotEngineError) {
        return reply.status(e.status).send({ error: 'bot_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });

  app.post('/v1/bots/:id/stop', async (req, reply) => {
    const userId = userFromRequest(req.headers);
    if (!userId) return reply.status(401).send({ error: 'unauthenticated' });
    const botId = (req.params as { id: string }).id;
    try {
      const res = await deps.bot.stopBot({ userId, botId });
      return reply.send(res);
    } catch (e) {
      if (e instanceof BotEngineError) {
        return reply.status(e.status).send({ error: 'bot_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });

  // Global kill switch. Static path — registered before `:id` routes so the
  // radix router never treats "kill-all" as a bot id.
  app.post('/v1/bots/kill-all', async (req, reply) => {
    const userId = userFromRequest(req.headers);
    if (!userId) return reply.status(401).send({ error: 'unauthenticated' });
    try {
      const res = await deps.bot.killAll({ userId });
      return reply.send(res);
    } catch (e) {
      if (e instanceof BotEngineError) {
        return reply.status(e.status).send({ error: 'bot_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });

  app.post('/v1/bots/:id/kill', async (req, reply) => {
    const userId = userFromRequest(req.headers);
    if (!userId) return reply.status(401).send({ error: 'unauthenticated' });
    const botId = (req.params as { id: string }).id;
    try {
      const res = await deps.bot.killBot({ userId, botId });
      return reply.send(res);
    } catch (e) {
      if (e instanceof BotEngineError) {
        return reply.status(e.status).send({ error: 'bot_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });

  app.get('/v1/bots/:id', async (req, reply) => {
    const userId = userFromRequest(req.headers);
    if (!userId) return reply.status(401).send({ error: 'unauthenticated' });
    const botId = (req.params as { id: string }).id;
    try {
      const res = await deps.bot.getBot({ userId, botId });
      return reply.send(res);
    } catch (e) {
      if (e instanceof BotEngineError) {
        return reply.status(e.status).send({ error: 'bot_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });
}
