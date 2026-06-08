import type { FastifyInstance } from 'fastify';
import {
  AiEngineClient,
  AiEngineError,
  AuthorStrategyRequest,
  BacktestRequest,
  CopilotChatRequest,
} from '../clients/ai.js';
import type { RequireUser } from '../auth/middleware.js';

interface Deps {
  ai: AiEngineClient;
  requireUser: RequireUser;
}

export async function registerAiRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  const { requireUser } = deps;

  app.post('/v1/ai/copilot/chat', { preHandler: requireUser }, async (req, reply) => {
    const parsed = CopilotChatRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'invalid_request', details: parsed.error.format() });
    }
    try {
      return reply.send(await deps.ai.chat({ userId: req.userId!, body: parsed.data }));
    } catch (e) {
      if (e instanceof AiEngineError) {
        return reply.status(e.status).send({ error: 'ai_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });

  app.post('/v1/ai/backtest', { preHandler: requireUser }, async (req, reply) => {
    const parsed = BacktestRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'invalid_request', details: parsed.error.format() });
    }
    try {
      return reply.send(await deps.ai.backtest({ userId: req.userId!, body: parsed.data }));
    } catch (e) {
      if (e instanceof AiEngineError) {
        return reply.status(e.status).send({ error: 'ai_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });

  app.post('/v1/ai/strategy/author', { preHandler: requireUser }, async (req, reply) => {
    const parsed = AuthorStrategyRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'invalid_request', details: parsed.error.format() });
    }
    try {
      return reply.send(await deps.ai.author({ userId: req.userId!, body: parsed.data }));
    } catch (e) {
      if (e instanceof AiEngineError) {
        return reply.status(e.status).send({ error: 'ai_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });
}
