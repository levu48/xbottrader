import type { FastifyReply, FastifyRequest } from 'fastify';
import type { SubscriptionStore } from '../db/repos.js';
import type { SessionStore } from './sessions.js';

declare module 'fastify' {
  interface FastifyRequest {
    userId?: string;
  }
}

export const SESSION_COOKIE = 'xbt_session';

export function sessionCookieOpts(maxAgeSeconds = 60 * 60 * 24 * 7) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax' as const,
    path: '/',
    maxAge: maxAgeSeconds,
  };
}

/** Resolve a fully-authenticated user id from the request's session cookie. */
export async function resolveUserId(
  req: FastifyRequest,
  sessions: SessionStore,
): Promise<string | null> {
  const token = req.cookies?.[SESSION_COOKIE];
  if (!token) return null;
  const data = await sessions.get(token);
  if (!data || !data.totpVerified) return null;
  return data.userId;
}

/** preHandler that 401s unless a valid, 2FA-cleared session is present. */
export function makeRequireUser(sessions: SessionStore) {
  return async (req: FastifyRequest, reply: FastifyReply): Promise<void> => {
    const userId = await resolveUserId(req, sessions);
    if (!userId) {
      await reply.status(401).send({ error: 'unauthenticated' });
      return;
    }
    req.userId = userId;
  };
}

export type RequireUser = ReturnType<typeof makeRequireUser>;

/**
 * preHandler that 403s unless the user has an active subscription. Must run
 * after requireUser (it reads req.userId). Used to gate live trading + the LLM
 * AI features behind the paid plan.
 */
export function makeRequireSubscription(subs: SubscriptionStore) {
  return async (req: FastifyRequest, reply: FastifyReply): Promise<void> => {
    if (!req.userId || !(await subs.isActive(req.userId))) {
      await reply.status(403).send({ error: 'subscription_required' });
      return;
    }
  };
}

export type RequireSubscription = ReturnType<typeof makeRequireSubscription>;
