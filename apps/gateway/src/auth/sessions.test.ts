import { describe, expect, it } from 'vitest';
import { RedisSessionStore, type RedisLike } from './sessions.js';

class MemRedis implements RedisLike {
  store = new Map<string, string>();
  async get(k: string) {
    return this.store.get(k) ?? null;
  }
  async set(k: string, v: string) {
    this.store.set(k, v);
    return 'OK';
  }
  async del(k: string) {
    return this.store.delete(k) ? 1 : 0;
  }
}

describe('RedisSessionStore', () => {
  it('creates, reads, flips 2fa, and revokes', async () => {
    const sessions = new RedisSessionStore(new MemRedis());
    const token = await sessions.create({ userId: 'u1', totpVerified: false });
    expect(token).toMatch(/^[A-Za-z0-9_-]+$/); // base64url, no padding

    expect(await sessions.get(token)).toEqual({ userId: 'u1', totpVerified: false });

    await sessions.setVerified(token, true);
    expect((await sessions.get(token))?.totpVerified).toBe(true);

    await sessions.revoke(token);
    expect(await sessions.get(token)).toBeNull();
  });

  it('returns null for an empty/unknown token', async () => {
    const sessions = new RedisSessionStore(new MemRedis());
    expect(await sessions.get('')).toBeNull();
    expect(await sessions.get('nope')).toBeNull();
  });
});
