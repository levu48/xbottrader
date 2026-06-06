import Fastify, { type FastifyInstance } from 'fastify';
import { describe, expect, it } from 'vitest';
import type { RequireUser } from '../auth/middleware.js';
import type { KeyMeta, KeyStore } from '../db/repos.js';
import { EnvelopeCipher, type EncryptedEnvelope } from '../security/keys.js';
import { registerKeysRoutes } from './routes.js';

const TEST_KEK = Buffer.alloc(32, 7).toString('base64');

const devRequireUser: RequireUser = async (req, reply) => {
  const u = req.headers['x-dev-user'];
  if (typeof u === 'string' && u) req.userId = u;
  else await reply.status(401).send({ error: 'unauthenticated' });
};

class MemKeys implements KeyStore {
  rows: { id: string; userId: string; exchange: string; label: string | null; envelope: EncryptedEnvelope; createdAt: string }[] = [];
  n = 0;
  async add(userId: string, exchange: string, label: string | null, envelope: EncryptedEnvelope): Promise<KeyMeta> {
    const row = { id: 'k' + ++this.n, userId, exchange, label, envelope, createdAt: '2026-01-01T00:00:00Z' };
    this.rows.push(row);
    return { id: row.id, exchange, label, createdAt: row.createdAt };
  }
  async listMeta(userId: string): Promise<KeyMeta[]> {
    return this.rows
      .filter((r) => r.userId === userId)
      .map((r) => ({ id: r.id, exchange: r.exchange, label: r.label, createdAt: r.createdAt }));
  }
  async getEnvelope(userId: string, exchange: string) {
    return this.rows.find((r) => r.userId === userId && r.exchange === exchange)?.envelope ?? null;
  }
  async remove(userId: string, id: string) {
    this.rows = this.rows.filter((r) => !(r.userId === userId && r.id === id));
  }
}

async function makeApp(): Promise<{ app: FastifyInstance; keys: MemKeys; cipher: EnvelopeCipher }> {
  const app = Fastify();
  const keys = new MemKeys();
  const cipher = new EnvelopeCipher(TEST_KEK);
  await registerKeysRoutes(app, { keys, cipher, requireUser: devRequireUser });
  return { app, keys, cipher };
}

describe('keys routes', () => {
  it('stores an encrypted key and never returns the secret', async () => {
    const { app, keys, cipher } = await makeApp();
    const res = await app.inject({
      method: 'POST',
      url: '/v1/keys',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: { exchange: 'binance', apiKey: 'AK123', secret: 'SECRET456', label: 'main' },
    });
    expect(res.statusCode).toBe(200);
    const meta = JSON.parse(res.body);
    expect(meta.exchange).toBe('binance');
    // response carries no secret material
    expect(res.body).not.toContain('SECRET456');
    expect(res.body).not.toContain('AK123');

    // stored envelope round-trips to the original creds (Bot Engine will decrypt)
    const envelope = await keys.getEnvelope('u1', 'binance');
    expect(envelope).not.toBeNull();
    expect(JSON.parse(cipher.decrypt(envelope!))).toEqual({ apiKey: 'AK123', secret: 'SECRET456' });

    const list = await app.inject({ method: 'GET', url: '/v1/keys', headers: { 'x-dev-user': 'u1' } });
    expect(list.body).not.toContain('SECRET456');
    expect(JSON.parse(list.body).keys[0].exchange).toBe('binance');
    await app.close();
  });

  it('requires auth', async () => {
    const { app } = await makeApp();
    const res = await app.inject({ method: 'GET', url: '/v1/keys' });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('isolates keys per user', async () => {
    const { app } = await makeApp();
    await app.inject({
      method: 'POST',
      url: '/v1/keys',
      headers: { 'x-dev-user': 'u1', 'content-type': 'application/json' },
      payload: { exchange: 'binance', apiKey: 'a', secret: 'b' },
    });
    const u2 = await app.inject({ method: 'GET', url: '/v1/keys', headers: { 'x-dev-user': 'u2' } });
    expect(JSON.parse(u2.body).keys).toEqual([]);
    await app.close();
  });
});
