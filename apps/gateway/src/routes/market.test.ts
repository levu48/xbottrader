import Fastify, { type FastifyInstance } from 'fastify';
import { describe, expect, it } from 'vitest';
import type { RequireUser } from '../auth/middleware.js';
import { BotEngineClient } from '../clients/bot.js';
import { InternalAuthSigner } from '../clients/internal-auth.js';
import { registerMarketRoutes } from './market.js';

// Dev auth stand-in: maps x-dev-user → req.userId (real auth is covered elsewhere).
const devRequireUser: RequireUser = async (req, reply) => {
  const u = req.headers['x-dev-user'];
  if (typeof u === 'string' && u) req.userId = u;
  else await reply.status(401).send({ error: 'unauthenticated' });
};

// Captures the full upstream URL (incl. query) so we can assert forwarding.
const makeClient = (
  responder: (url: string) => { status: number; body: string },
): { client: BotEngineClient; urls: string[] } => {
  const urls: string[] = [];
  const client = new BotEngineClient('http://stub', new InternalAuthSigner('s'), async (url) => {
    urls.push(url);
    const r = responder(url);
    return { status: r.status, text: async () => r.body };
  });
  return { client, urls };
};

const OK = '{"exchange":"binance","symbol":"BTC/USDT","timeframe":"1m","candles":[[1700000000000,1,2,0.5,1.5,9]]}';

function reg(app: FastifyInstance, client: BotEngineClient): Promise<void> {
  return registerMarketRoutes(app, { bot: client, requireUser: devRequireUser });
}

describe('market routes', () => {
  it('forwards symbol/exchange/timeframe/limit to the bot engine and returns candles', async () => {
    const { client, urls } = makeClient(() => ({ status: 200, body: OK }));
    const app = Fastify();
    await reg(app, client);

    const res = await app.inject({
      method: 'GET',
      url: '/v1/market/ohlcv?exchange=binance&symbol=BTC/USDT&timeframe=5m&limit=50',
      headers: { 'x-dev-user': 'u1' },
    });

    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body).candles).toHaveLength(1);
    const url = new URL(urls[0]!);
    expect(url.pathname).toBe('/market/ohlcv');
    expect(url.searchParams.get('symbol')).toBe('BTC/USDT');
    expect(url.searchParams.get('timeframe')).toBe('5m');
    expect(url.searchParams.get('limit')).toBe('50');
    await app.close();
  });

  it('rejects requests without an authenticated user', async () => {
    const { client } = makeClient(() => ({ status: 200, body: OK }));
    const app = Fastify();
    await reg(app, client);
    const res = await app.inject({ method: 'GET', url: '/v1/market/ohlcv?symbol=BTC/USDT' });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('returns 400 when symbol is missing', async () => {
    const { client } = makeClient(() => ({ status: 200, body: OK }));
    const app = Fastify();
    await reg(app, client);
    const res = await app.inject({
      method: 'GET',
      url: '/v1/market/ohlcv?exchange=binance',
      headers: { 'x-dev-user': 'u1' },
    });
    expect(res.statusCode).toBe(400);
    expect(JSON.parse(res.body).error).toBe('symbol_required');
    await app.close();
  });

  it('clamps limit to the max', async () => {
    const { client, urls } = makeClient(() => ({ status: 200, body: OK }));
    const app = Fastify();
    await reg(app, client);
    await app.inject({
      method: 'GET',
      url: '/v1/market/ohlcv?symbol=BTC/USDT&limit=999999',
      headers: { 'x-dev-user': 'u1' },
    });
    expect(new URL(urls[0]!).searchParams.get('limit')).toBe('1000');
    await app.close();
  });

  it('propagates upstream errors', async () => {
    const { client } = makeClient(() => ({ status: 502, body: 'market data unavailable' }));
    const app = Fastify();
    await reg(app, client);
    const res = await app.inject({
      method: 'GET',
      url: '/v1/market/ohlcv?symbol=BTC/USDT',
      headers: { 'x-dev-user': 'u1' },
    });
    expect(res.statusCode).toBe(502);
    expect(JSON.parse(res.body).upstream).toBe('market data unavailable');
    await app.close();
  });
});
