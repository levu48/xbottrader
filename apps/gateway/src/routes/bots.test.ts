import Fastify from 'fastify';
import { describe, expect, it } from 'vitest';
import { BotEngineClient } from '../clients/bot.js';
import { InternalAuthSigner } from '../clients/internal-auth.js';
import { registerBotsRoutes } from './bots.js';

class FakeUpstream {
  startCalls: { userId: string; botId: string; body: unknown }[] = [];
  stopCalls: { userId: string; botId: string }[] = [];

  async start(): Promise<{ status: number; text: () => Promise<string> }> {
    return { status: 200, text: async () => '{"bot_id":"b1","state":"starting"}' };
  }
}

const makeClient = (responder: (path: string, init: { body: string }) => { status: number; body: string }): BotEngineClient => {
  return new BotEngineClient('http://stub', new InternalAuthSigner('s'), async (url, init) => {
    const path = new URL(url).pathname;
    const r = responder(path, init);
    return { status: r.status, text: async () => r.body };
  });
};

describe('bots routes', () => {
  it('forwards a start request to the bot engine and returns the upstream response', async () => {
    const seen: { path: string; body: string }[] = [];
    const client = makeClient((path, init) => {
      seen.push({ path, body: init.body });
      return { status: 200, body: '{"bot_id":"b1","state":"starting"}' };
    });

    const app = Fastify();
    await registerBotsRoutes(app, { bot: client });

    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: {
          strategy_type: 'dca',
          symbol: 'BTC/USDT',
          quote_amount: '100',
          interval_minutes: 60,
        },
        mode: 'paper',
      },
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body)).toEqual({ bot_id: 'b1', state: 'starting' });
    expect(seen[0]!.path).toBe('/bots/b1/start');
    const upstreamBody = JSON.parse(seen[0]!.body);
    expect(upstreamBody.strategy.strategy_type).toBe('dca');
    expect(upstreamBody.mode).toBe('paper');

    await app.close();
  });

  it('rejects requests without an authenticated user', async () => {
    const client = makeClient(() => ({ status: 200, body: '{}' }));
    const app = Fastify();
    await registerBotsRoutes(app, { bot: client });

    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/stop',
      payload: '',
    });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('returns 400 when start body is missing required fields', async () => {
    const client = makeClient(() => ({ status: 200, body: '{}' }));
    const app = Fastify();
    await registerBotsRoutes(app, { bot: client });

    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: { strategy: { strategy_type: 'dca' } },
    });
    expect(res.statusCode).toBe(400);
    await app.close();
  });

  it('forwards a kill request and returns the killed state', async () => {
    const seen: { path: string }[] = [];
    const client = makeClient((path) => {
      seen.push({ path });
      return { status: 200, body: '{"bot_id":"b1","state":"killed"}' };
    });
    const app = Fastify();
    await registerBotsRoutes(app, { bot: client });

    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/kill',
      headers: { 'x-dev-user': 'u1' },
      payload: '',
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body).state).toBe('killed');
    expect(seen[0]!.path).toBe('/bots/b1/kill');
    await app.close();
  });

  it('forwards a global kill-all and returns the killed ids', async () => {
    const seen: { path: string }[] = [];
    const client = makeClient((path) => {
      seen.push({ path });
      return { status: 200, body: '{"killed":["b1","b2"]}' };
    });
    const app = Fastify();
    await registerBotsRoutes(app, { bot: client });

    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/kill-all',
      headers: { 'x-dev-user': 'u1' },
      payload: '',
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body).killed).toEqual(['b1', 'b2']);
    // "kill-all" must hit the static route, not be parsed as a bot id.
    expect(seen[0]!.path).toBe('/bots/kill-all');
    await app.close();
  });

  it('rejects kill-all without an authenticated user', async () => {
    const client = makeClient(() => ({ status: 200, body: '{"killed":[]}' }));
    const app = Fastify();
    await registerBotsRoutes(app, { bot: client });
    const res = await app.inject({ method: 'POST', url: '/v1/bots/kill-all', payload: '' });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('propagates upstream 4xx responses to the client', async () => {
    const client = makeClient(() => ({ status: 409, body: 'already running' }));
    const app = Fastify();
    await registerBotsRoutes(app, { bot: client });

    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/stop',
      headers: { 'x-dev-user': 'u1' },
      payload: '',
    });
    expect(res.statusCode).toBe(409);
    expect(JSON.parse(res.body).upstream).toBe('already running');
    await app.close();
  });
});
