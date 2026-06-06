import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import type { AuditLog, UserStore } from '../db/repos.js';
import { makeRequireUser, SESSION_COOKIE, sessionCookieOpts } from './middleware.js';
import { hashPassword, verifyPassword } from './password.js';
import type { SessionStore } from './sessions.js';
import { generateTotp, verifyTotp } from './totp.js';

interface Deps {
  users: UserStore;
  sessions: SessionStore;
  audit: AuditLog;
}

const SignupBody = z.object({ email: z.string().email(), password: z.string().min(8) });
const LoginBody = z.object({
  email: z.string().email(),
  password: z.string().min(1),
  code: z.string().optional(),
});
const ActivateBody = z.object({ code: z.string().min(6) });

export async function registerAuthRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  const requireUser = makeRequireUser(deps.sessions);

  app.post('/v1/auth/signup', async (req, reply) => {
    const parsed = SignupBody.safeParse(req.body);
    if (!parsed.success) return reply.status(400).send({ error: 'invalid_request' });
    const { email, password } = parsed.data;

    if (await deps.users.findByEmail(email)) {
      return reply.status(409).send({ error: 'email_taken' });
    }
    const user = await deps.users.create(email, await hashPassword(password));
    const token = await deps.sessions.create({ userId: user.id, totpVerified: true });
    await deps.audit.record('signup', user.id, req.ip);
    reply.setCookie(SESSION_COOKIE, token, sessionCookieOpts());
    return reply.send({ id: user.id, email: user.email, totpEnabled: false });
  });

  app.post('/v1/auth/login', async (req, reply) => {
    const parsed = LoginBody.safeParse(req.body);
    if (!parsed.success) return reply.status(400).send({ error: 'invalid_request' });
    const { email, password, code } = parsed.data;

    const user = await deps.users.findByEmail(email);
    if (!user || !(await verifyPassword(user.passwordHash, password))) {
      return reply.status(401).send({ error: 'invalid_credentials' });
    }

    if (user.totpEnabled) {
      if (!code) return reply.send({ twoFactorRequired: true });
      if (!user.totpSecret || !verifyTotp(user.totpSecret, code)) {
        return reply.status(401).send({ error: 'invalid_totp' });
      }
    }

    const token = await deps.sessions.create({ userId: user.id, totpVerified: true });
    await deps.audit.record(user.totpEnabled ? 'login_2fa' : 'login', user.id, req.ip);
    reply.setCookie(SESSION_COOKIE, token, sessionCookieOpts());
    return reply.send({ id: user.id, email: user.email, totpEnabled: user.totpEnabled });
  });

  app.post('/v1/auth/logout', { preHandler: requireUser }, async (req, reply) => {
    const token = req.cookies?.[SESSION_COOKIE];
    if (token) await deps.sessions.revoke(token);
    await deps.audit.record('logout', req.userId ?? null, req.ip);
    reply.clearCookie(SESSION_COOKIE, { path: '/' });
    return reply.send({ ok: true });
  });

  app.get('/v1/auth/me', { preHandler: requireUser }, async (req, reply) => {
    const user = await deps.users.findById(req.userId!);
    if (!user) return reply.status(401).send({ error: 'unauthenticated' });
    return reply.send({ id: user.id, email: user.email, totpEnabled: user.totpEnabled });
  });

  app.post('/v1/auth/2fa/enroll', { preHandler: requireUser }, async (req, reply) => {
    const user = await deps.users.findById(req.userId!);
    if (!user) return reply.status(401).send({ error: 'unauthenticated' });
    const { secret, otpauthUrl } = generateTotp(user.email);
    // Store the secret but keep 2FA disabled until activate() confirms a code.
    await deps.users.setTotp(user.id, secret, false);
    return reply.send({ secret, otpauthUrl });
  });

  app.post('/v1/auth/2fa/activate', { preHandler: requireUser }, async (req, reply) => {
    const parsed = ActivateBody.safeParse(req.body);
    if (!parsed.success) return reply.status(400).send({ error: 'invalid_request' });
    const user = await deps.users.findById(req.userId!);
    if (!user?.totpSecret) return reply.status(400).send({ error: 'not_enrolled' });
    if (!verifyTotp(user.totpSecret, parsed.data.code)) {
      return reply.status(401).send({ error: 'invalid_totp' });
    }
    await deps.users.setTotp(user.id, user.totpSecret, true);
    await deps.audit.record('2fa_enable', user.id, req.ip);
    return reply.send({ ok: true });
  });
}
