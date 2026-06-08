import Fastify, { type FastifyInstance } from 'fastify';
import { describe, expect, it } from 'vitest';
import type { RequireUser } from '../auth/middleware.js';
import { BotEngineClient } from '../clients/bot.js';
import { InternalAuthSigner } from '../clients/internal-auth.js';
import type { KeyStore, SubscriptionStore, UserStore } from '../db/repos.js';
import { registerBotsRoutes } from './bots.js';

// Dev auth stand-in: maps x-dev-user → req.userId so route tests stay focused on
// forwarding (real auth is covered in auth/*.test.ts).
const devRequireUser: RequireUser = async (req, reply) => {
  const u = req.headers['x-dev-user'];
  if (typeof u === 'string' && u) req.userId = u;
  else await reply.status(401).send({ error: 'unauthenticated' });
};

const makeUsers = (totpEnabled = false): UserStore => ({
  findByEmail: async () => null,
  findById: async (id) => ({ id, email: 'a@b.c', passwordHash: '', totpSecret: 's', totpEnabled }),
  create: async () => ({ id: 'u1', email: 'a@b.c', passwordHash: '', totpSecret: null, totpEnabled: false }),
  setTotp: async () => {},
});

const makeKeys = (envelope: unknown = null): KeyStore => ({
  add: async () => ({ id: 'k', exchange: 'binance', label: null, createdAt: '' }),
  listMeta: async () => [],
  getEnvelope: async () => envelope as Awaited<ReturnType<KeyStore['getEnvelope']>>,
  remove: async () => {},
});

const makeSubs = (active = true): SubscriptionStore => ({
  getByUser: async () => null,
  getByCustomerId: async () => null,
  ensureCustomer: async () => {},
  upsertFromStripe: async () => {},
  isActive: async () => active,
});

const makeClient = (
  responder: (path: string, init: { body?: string }) => { status: number; body: string },
): BotEngineClient =>
  new BotEngineClient('http://stub', new InternalAuthSigner('s'), async (url, init) => {
    const r = responder(new URL(url).pathname, init);
    return { status: r.status, text: async () => r.body };
  });

function reg(
  app: FastifyInstance,
  client: BotEngineClient,
  opts: { users?: UserStore; keys?: KeyStore; subs?: SubscriptionStore } = {},
): Promise<void> {
  return registerBotsRoutes(app, {
    bot: client,
    requireUser: devRequireUser,
    users: opts.users ?? makeUsers(),
    keys: opts.keys ?? makeKeys(),
    // Default to an active subscription so live/ai_signal tests exercise the
    // 2FA/key logic; the entitlement gate is tested explicitly below.
    subs: opts.subs ?? makeSubs(true),
  });
}

describe('bots routes', () => {
  it('forwards a start request to the bot engine and returns the upstream response', async () => {
    const seen: { path: string; body: string }[] = [];
    const client = makeClient((path, init) => {
      seen.push({ path, body: init.body ?? '' });
      return { status: 200, body: '{"bot_id":"b1","state":"starting"}' };
    });
    const app = Fastify();
    await reg(app, client);

    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: { strategy_type: 'dca', symbol: 'BTC/USDT', quote_amount: '100', interval_minutes: 60 },
        mode: 'paper',
      },
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body)).toEqual({ bot_id: 'b1', state: 'starting' });
    expect(seen[0]!.path).toBe('/bots/b1/start');
    expect(JSON.parse(seen[0]!.body).mode).toBe('paper');
    await app.close();
  });

  it('rejects requests without an authenticated user', async () => {
    const app = Fastify();
    await reg(app, makeClient(() => ({ status: 200, body: '{}' })));
    const res = await app.inject({ method: 'POST', url: '/v1/bots/b1/stop', payload: '' });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('returns 400 when start body is missing required fields', async () => {
    const app = Fastify();
    await reg(app, makeClient(() => ({ status: 200, body: '{}' })));
    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: { strategy: { strategy_type: 'dca' } },
    });
    expect(res.statusCode).toBe(400);
    await app.close();
  });

  it('blocks live start without 2FA enabled', async () => {
    const app = Fastify();
    await reg(app, makeClient(() => ({ status: 200, body: '{}' })), { users: makeUsers(false) });
    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: { strategy_type: 'dca', symbol: 'BTC/USDT', quote_amount: '100', interval_minutes: 60 },
        mode: 'live',
      },
    });
    expect(res.statusCode).toBe(403);
    expect(JSON.parse(res.body).error).toBe('2fa_required_for_live');
    await app.close();
  });

  it('blocks live start when no key is stored, even with 2FA', async () => {
    const app = Fastify();
    await reg(app, makeClient(() => ({ status: 200, body: '{}' })), {
      users: makeUsers(true),
      keys: makeKeys(null),
    });
    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: { strategy_type: 'dca', symbol: 'BTC/USDT', quote_amount: '100', interval_minutes: 60 },
        mode: 'live',
        exchange: 'binance',
      },
    });
    expect(res.statusCode).toBe(400);
    expect(JSON.parse(res.body).error).toBe('no_api_key_for_exchange');
    await app.close();
  });

  it('injects the stored envelope as credentials on a live start', async () => {
    const envelope = { v: 1, dek_iv: 'a', dek_ct: 'b', data_iv: 'c', data_ct: 'd' };
    const seen: { body: string }[] = [];
    const client = makeClient((_p, init) => {
      seen.push({ body: init.body ?? '' });
      return { status: 200, body: '{"bot_id":"b1","state":"starting"}' };
    });
    const app = Fastify();
    await reg(app, client, { users: makeUsers(true), keys: makeKeys(envelope) });
    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: { strategy_type: 'dca', symbol: 'BTC/USDT', quote_amount: '100', interval_minutes: 60 },
        mode: 'live',
        exchange: 'binance',
      },
    });
    expect(res.statusCode).toBe(200);
    expect(JSON.parse(seen[0]!.body).credentials).toEqual(envelope);
    await app.close();
  });

  it('blocks a live start without an active subscription', async () => {
    const app = Fastify();
    await reg(app, makeClient(() => ({ status: 200, body: '{}' })), {
      users: makeUsers(true),
      keys: makeKeys({ v: 1 }),
      subs: makeSubs(false),
    });
    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: { strategy_type: 'dca', symbol: 'BTC/USDT', quote_amount: '100', interval_minutes: 60 },
        mode: 'live',
        exchange: 'binance',
      },
    });
    expect(res.statusCode).toBe(403);
    expect(JSON.parse(res.body).error).toBe('subscription_required');
    await app.close();
  });

  it('blocks a paper ai_signal start without an active subscription', async () => {
    const app = Fastify();
    await reg(app, makeClient(() => ({ status: 200, body: '{}' })), { subs: makeSubs(false) });
    const res = await app.inject({
      method: 'POST',
      url: '/v1/bots/b1/start',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: {
        strategy: {
          strategy_type: 'ai_signal',
          symbol: 'BTC/USDT',
          quote_amount: '50',
          decision_interval_minutes: 15,
        },
        mode: 'paper',
      },
    });
    expect(res.statusCode).toBe(403);
    expect(JSON.parse(res.body).error).toBe('subscription_required');
    await app.close();
  });

  it('forwards kill and kill-all to the right paths', async () => {
    const seen: { path: string }[] = [];
    const client = makeClient((path) => {
      seen.push({ path });
      return { status: 200, body: '{"bot_id":"b1","state":"killed","killed":["b1"]}' };
    });
    const app = Fastify();
    await reg(app, client);
    await app.inject({ method: 'POST', url: '/v1/bots/b1/kill', headers: { 'x-dev-user': 'u1' }, payload: '' });
    await app.inject({ method: 'POST', url: '/v1/bots/kill-all', headers: { 'x-dev-user': 'u1' }, payload: '' });
    expect(seen[0]!.path).toBe('/bots/b1/kill');
    expect(seen[1]!.path).toBe('/bots/kill-all');
    await app.close();
  });

  it('propagates upstream 4xx responses to the client', async () => {
    const app = Fastify();
    await reg(app, makeClient(() => ({ status: 409, body: 'already running' })));
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
