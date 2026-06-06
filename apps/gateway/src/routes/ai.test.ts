import Fastify, { type FastifyInstance } from 'fastify';
import { describe, expect, it } from 'vitest';
import type { RequireUser } from '../auth/middleware.js';
import { AiEngineClient } from '../clients/ai.js';
import { InternalAuthSigner } from '../clients/internal-auth.js';
import { registerAiRoutes } from './ai.js';

const devRequireUser: RequireUser = async (req, reply) => {
  const u = req.headers['x-dev-user'];
  if (typeof u === 'string' && u) req.userId = u;
  else await reply.status(401).send({ error: 'unauthenticated' });
};

const reg = (app: FastifyInstance, ai: AiEngineClient): Promise<void> =>
  registerAiRoutes(app, { ai, requireUser: devRequireUser });

const makeClient = (
  responder: (path: string, init: { body?: string }) => { status: number; body: string },
): AiEngineClient => {
  return new AiEngineClient('http://stub', new InternalAuthSigner('s'), async (url, init) => {
    const path = new URL(url).pathname;
    const r = responder(path, init);
    return { status: r.status, text: async () => r.body };
  });
};

describe('ai routes', () => {
  it('forwards a copilot chat and returns the upstream reply', async () => {
    const seen: { path: string; body: string }[] = [];
    const client = makeClient((path, init) => {
      seen.push({ path, body: init.body ?? '' });
      return { status: 200, body: '{"reply":"hello","usage":{}}' };
    });
    const app = Fastify();
    await reg(app, client);

    const res = await app.inject({
      method: 'POST',
      url: '/v1/ai/copilot/chat',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: { messages: [{ role: 'user', content: 'explain my bot' }], context: 'PnL +5%' },
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body).reply).toBe('hello');
    expect(seen[0]!.path).toBe('/copilot/chat');
    expect(JSON.parse(seen[0]!.body).messages[0].content).toBe('explain my bot');
    await app.close();
  });

  it('forwards a backtest and returns upstream stats', async () => {
    const seen: { path: string }[] = [];
    const client = makeClient((path) => {
      seen.push({ path });
      return { status: 200, body: '{"symbol":"BTC/USDT","bars":5,"stats":{},"equity_curve":[]}' };
    });
    const app = Fastify();
    await reg(app, client);

    const res = await app.inject({
      method: 'POST',
      url: '/v1/ai/backtest',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: {
          strategy_type: 'ma_crossover',
          symbol: 'BTC/USDT',
          fast_period: 10,
          slow_period: 30,
          position_quote: '1000',
        },
        timeframe: '1h',
        limit: 500,
      },
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body).bars).toBe(5);
    expect(seen[0]!.path).toBe('/backtest/run');
    await app.close();
  });

  it('rejects requests without an authenticated user', async () => {
    const client = makeClient(() => ({ status: 200, body: '{}' }));
    const app = Fastify();
    await reg(app, client);
    const res = await app.inject({
      method: 'POST',
      url: '/v1/ai/copilot/chat',
      payload: { messages: [{ role: 'user', content: 'hi' }] },
    });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('returns 400 on an invalid backtest body', async () => {
    const client = makeClient(() => ({ status: 200, body: '{}' }));
    const app = Fastify();
    await reg(app, client);
    const res = await app.inject({
      method: 'POST',
      url: '/v1/ai/backtest',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: { strategy: { strategy_type: 'dca' } },
    });
    expect(res.statusCode).toBe(400);
    await app.close();
  });

  it('propagates upstream 4xx responses', async () => {
    const client = makeClient(() => ({ status: 400, body: 'unknown exchange' }));
    const app = Fastify();
    await reg(app, client);
    const res = await app.inject({
      method: 'POST',
      url: '/v1/ai/copilot/chat',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: { messages: [{ role: 'user', content: 'hi' }] },
    });
    expect(res.statusCode).toBe(400);
    expect(JSON.parse(res.body).upstream).toBe('unknown exchange');
    await app.close();
  });
});
