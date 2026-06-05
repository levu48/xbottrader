import type { FastifyInstance } from 'fastify';
import { AiEngineClient, AiEngineError, BacktestRequest, CopilotChatRequest } from '../clients/ai.js';

interface Deps {
  ai: AiEngineClient;
}

// Placeholder session lookup — mirrors routes/bots.ts. Real auth (Lucia/Clerk)
// validates a session cookie; the scaffold accepts `x-dev-user: <id>`.
function userFromRequest(headers: Record<string, string | string[] | undefined>): string | null {
  const raw = headers['x-dev-user'];
  if (typeof raw === 'string' && raw.length > 0) return raw;
  return null;
}

export async function registerAiRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  app.post('/v1/ai/copilot/chat', async (req, reply) => {
    const userId = userFromRequest(req.headers);
    if (!userId) return reply.status(401).send({ error: 'unauthenticated' });

    const parsed = CopilotChatRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'invalid_request', details: parsed.error.format() });
    }
    try {
      return reply.send(await deps.ai.chat({ userId, body: parsed.data }));
    } catch (e) {
      if (e instanceof AiEngineError) {
        return reply.status(e.status).send({ error: 'ai_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });

  app.post('/v1/ai/backtest', async (req, reply) => {
    const userId = userFromRequest(req.headers);
    if (!userId) return reply.status(401).send({ error: 'unauthenticated' });

    const parsed = BacktestRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'invalid_request', details: parsed.error.format() });
    }
    try {
      return reply.send(await deps.ai.backtest({ userId, body: parsed.data }));
    } catch (e) {
      if (e instanceof AiEngineError) {
        return reply.status(e.status).send({ error: 'ai_engine_error', upstream: e.upstream });
      }
      throw e;
    }
  });
}
