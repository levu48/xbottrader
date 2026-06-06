import fastifyCookie from '@fastify/cookie';
import Fastify, { type FastifyInstance } from 'fastify';
import { authenticator } from 'otplib';
import { describe, expect, it } from 'vitest';
import type { AuditLog, UserRecord, UserStore } from '../db/repos.js';
import { registerAuthRoutes } from './routes.js';
import type { SessionData, SessionStore } from './sessions.js';

class MemUsers implements UserStore {
  m = new Map<string, UserRecord>();
  n = 0;
  async findByEmail(email: string) {
    return [...this.m.values()].find((u) => u.email === email) ?? null;
  }
  async findById(id: string) {
    return this.m.get(id) ?? null;
  }
  async create(email: string, passwordHash: string) {
    const id = 'u' + ++this.n;
    const u: UserRecord = { id, email, passwordHash, totpSecret: null, totpEnabled: false };
    this.m.set(id, u);
    return u;
  }
  async setTotp(id: string, secret: string, enabled: boolean) {
    const u = this.m.get(id);
    if (u) {
      u.totpSecret = secret;
      u.totpEnabled = enabled;
    }
  }
}

class MemSessions implements SessionStore {
  m = new Map<string, SessionData>();
  n = 0;
  async create(d: SessionData) {
    const t = 'tok' + ++this.n;
    this.m.set(t, { ...d });
    return t;
  }
  async get(t: string) {
    return this.m.get(t) ?? null;
  }
  async setVerified(t: string, v: boolean) {
    const d = this.m.get(t);
    if (d) d.totpVerified = v;
  }
  async revoke(t: string) {
    this.m.delete(t);
  }
}

const noAudit: AuditLog = { record: async () => {} };

async function makeApp(): Promise<{ app: FastifyInstance; users: MemUsers }> {
  const app = Fastify();
  await app.register(fastifyCookie);
  const users = new MemUsers();
  await registerAuthRoutes(app, { users, sessions: new MemSessions(), audit: noAudit });
  return { app, users };
}

function sessionCookie(res: Awaited<ReturnType<FastifyInstance['inject']>>): string {
  const c = res.cookies.find((c) => c.name === 'xbt_session');
  if (!c) throw new Error('no session cookie set');
  return c.value;
}

describe('auth routes', () => {
  it('signup sets a session and /me returns the user', async () => {
    const { app } = await makeApp();
    const signup = await app.inject({
      method: 'POST',
      url: '/v1/auth/signup',
      payload: { email: 'a@b.co', password: 'longenough1' },
    });
    expect(signup.statusCode).toBe(200);
    const token = sessionCookie(signup);

    const me = await app.inject({ method: 'GET', url: '/v1/auth/me', cookies: { xbt_session: token } });
    expect(me.statusCode).toBe(200);
    expect(JSON.parse(me.body).email).toBe('a@b.co');
    await app.close();
  });

  it('/me is 401 without a session', async () => {
    const { app } = await makeApp();
    const res = await app.inject({ method: 'GET', url: '/v1/auth/me' });
    expect(res.statusCode).toBe(401);
    await app.close();
  });

  it('rejects duplicate signup and wrong-password login', async () => {
    const { app } = await makeApp();
    const body = { email: 'dup@b.co', password: 'longenough1' };
    await app.inject({ method: 'POST', url: '/v1/auth/signup', payload: body });
    expect((await app.inject({ method: 'POST', url: '/v1/auth/signup', payload: body })).statusCode).toBe(409);
    const bad = await app.inject({
      method: 'POST',
      url: '/v1/auth/login',
      payload: { email: 'dup@b.co', password: 'wrongpass' },
    });
    expect(bad.statusCode).toBe(401);
    await app.close();
  });

  it('enforces the 2FA two-step on login once enabled', async () => {
    const { app } = await makeApp();
    const signup = await app.inject({
      method: 'POST',
      url: '/v1/auth/signup',
      payload: { email: 'tf@b.co', password: 'longenough1' },
    });
    const token = sessionCookie(signup);

    const enroll = await app.inject({
      method: 'POST',
      url: '/v1/auth/2fa/enroll',
      cookies: { xbt_session: token },
    });
    const { secret } = JSON.parse(enroll.body);
    const activate = await app.inject({
      method: 'POST',
      url: '/v1/auth/2fa/activate',
      cookies: { xbt_session: token },
      payload: { code: authenticator.generate(secret) },
    });
    expect(activate.statusCode).toBe(200);

    // password alone now returns twoFactorRequired (no cookie)
    const step1 = await app.inject({
      method: 'POST',
      url: '/v1/auth/login',
      payload: { email: 'tf@b.co', password: 'longenough1' },
    });
    expect(JSON.parse(step1.body).twoFactorRequired).toBe(true);
    expect(step1.cookies.find((c) => c.name === 'xbt_session')).toBeUndefined();

    // password + code logs in
    const step2 = await app.inject({
      method: 'POST',
      url: '/v1/auth/login',
      payload: { email: 'tf@b.co', password: 'longenough1', code: authenticator.generate(secret) },
    });
    expect(step2.statusCode).toBe(200);
    expect(sessionCookie(step2)).toBeTruthy();
    await app.close();
  });
});
