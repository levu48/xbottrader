import Fastify, { type FastifyInstance } from 'fastify';
import fastifyRawBody from 'fastify-raw-body';
import type Stripe from 'stripe';
import { describe, expect, it, vi } from 'vitest';
import type { RequireUser } from '../auth/middleware.js';
import type { SubscriptionRecord, SubscriptionStore, UserStore } from '../db/repos.js';
import { registerBillingRoutes } from './routes.js';

const devRequireUser: RequireUser = async (req, reply) => {
  const u = req.headers['x-dev-user'];
  if (typeof u === 'string' && u) req.userId = u;
  else await reply.status(401).send({ error: 'unauthenticated' });
};

const users: UserStore = {
  findByEmail: async () => null,
  findById: async (id) => ({ id, email: 'a@b.c', passwordHash: '', totpSecret: null, totpEnabled: false }),
  create: async () => ({ id: 'u1', email: 'a@b.c', passwordHash: '', totpSecret: null, totpEnabled: false }),
  setTotp: async () => {},
};

// In-memory subscription store keyed by user / customer.
function makeSubs(initial?: SubscriptionRecord): SubscriptionStore & { rows: Map<string, SubscriptionRecord> } {
  const rows = new Map<string, SubscriptionRecord>();
  if (initial) rows.set(initial.userId, initial);
  const byCustomer = (cid: string) => [...rows.values()].find((r) => r.stripeCustomerId === cid) ?? null;
  return {
    rows,
    getByUser: async (userId) => rows.get(userId) ?? null,
    getByCustomerId: async (cid) => byCustomer(cid),
    ensureCustomer: async (userId, stripeCustomerId) => {
      if (!rows.has(userId)) {
        rows.set(userId, {
          userId,
          stripeCustomerId,
          stripeSubscriptionId: null,
          status: null,
          priceId: null,
          currentPeriodEnd: null,
          cancelAtPeriodEnd: false,
        });
      }
    },
    upsertFromStripe: async (cid, update) => {
      const row = byCustomer(cid);
      if (!row) return;
      rows.set(row.userId, {
        ...row,
        stripeSubscriptionId: update.stripeSubscriptionId ?? row.stripeSubscriptionId,
        status: update.status ?? row.status,
        priceId: update.priceId ?? row.priceId,
        currentPeriodEnd: update.currentPeriodEnd
          ? update.currentPeriodEnd.toISOString()
          : row.currentPeriodEnd,
        cancelAtPeriodEnd: update.cancelAtPeriodEnd ?? row.cancelAtPeriodEnd,
      });
    },
    isActive: async (userId) => {
      const s = rows.get(userId)?.status;
      return s === 'active' || s === 'trialing';
    },
  };
}

// Minimal Stripe stub covering only the methods the routes call.
function makeStripe(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    customers: { create: vi.fn(async () => ({ id: 'cus_123' })) },
    checkout: { sessions: { create: vi.fn(async () => ({ url: 'https://checkout.stripe.com/s/1' })) } },
    billingPortal: { sessions: { create: vi.fn(async () => ({ url: 'https://billing.stripe.com/p/1' })) } },
    subscriptions: { retrieve: vi.fn() },
    webhooks: {
      constructEvent: vi.fn(() => {
        throw new Error('bad signature');
      }),
    },
    ...overrides,
  } as unknown as Stripe;
}

async function reg(app: FastifyInstance, stripe: Stripe, subs: SubscriptionStore): Promise<void> {
  await app.register(fastifyRawBody, { global: false, runFirst: true });
  await registerBillingRoutes(app, {
    stripe,
    subs,
    users,
    requireUser: devRequireUser,
    priceId: 'price_123',
    webhookSecret: 'whsec_test',
    publicBaseUrl: 'https://app.test',
  });
}

describe('billing routes', () => {
  it('checkout creates a Stripe customer on first use and returns the session url', async () => {
    const subs = makeSubs();
    const stripe = makeStripe();
    const app = Fastify();
    await reg(app, stripe, subs);

    const res = await app.inject({
      method: 'POST',
      url: '/v1/billing/checkout',
      headers: { 'x-dev-user': 'u1' },
    });
    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body).url).toMatch(/checkout\.stripe\.com/);
    // Customer was created and linked.
    expect((stripe.customers.create as ReturnType<typeof vi.fn>)).toHaveBeenCalledOnce();
    expect(subs.rows.get('u1')?.stripeCustomerId).toBe('cus_123');
    await app.close();
  });

  it('checkout reuses an existing customer (no second create)', async () => {
    const subs = makeSubs({
      userId: 'u1',
      stripeCustomerId: 'cus_existing',
      stripeSubscriptionId: null,
      status: null,
      priceId: null,
      currentPeriodEnd: null,
      cancelAtPeriodEnd: false,
    });
    const stripe = makeStripe();
    const app = Fastify();
    await reg(app, stripe, subs);

    await app.inject({ method: 'POST', url: '/v1/billing/checkout', headers: { 'x-dev-user': 'u1' } });
    expect((stripe.customers.create as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
    await app.close();
  });

  it('status reflects the stored subscription', async () => {
    const subs = makeSubs({
      userId: 'u1',
      stripeCustomerId: 'cus_1',
      stripeSubscriptionId: 'sub_1',
      status: 'active',
      priceId: 'price_123',
      currentPeriodEnd: '2026-07-01T00:00:00.000Z',
      cancelAtPeriodEnd: false,
    });
    const app = Fastify();
    await reg(app, makeStripe(), subs);
    const res = await app.inject({ method: 'GET', url: '/v1/billing/status', headers: { 'x-dev-user': 'u1' } });
    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body)).toMatchObject({ active: true, status: 'active' });
    await app.close();
  });

  it('webhook rejects a bad signature with 400', async () => {
    const app = Fastify();
    await reg(app, makeStripe(), makeSubs());
    const res = await app.inject({
      method: 'POST',
      url: '/v1/webhooks/stripe',
      headers: { 'stripe-signature': 'bad', 'content-type': 'application/json' },
      payload: '{}',
    });
    expect(res.statusCode).toBe(400);
    await app.close();
  });

  it('webhook applies a subscription update on a valid event', async () => {
    const subs = makeSubs({
      userId: 'u1',
      stripeCustomerId: 'cus_1',
      stripeSubscriptionId: null,
      status: null,
      priceId: null,
      currentPeriodEnd: null,
      cancelAtPeriodEnd: false,
    });
    const sub = {
      id: 'sub_1',
      customer: 'cus_1',
      status: 'active',
      cancel_at_period_end: false,
      current_period_end: 1893456000,
      items: { data: [{ price: { id: 'price_123' } }] },
    };
    const stripe = makeStripe({
      webhooks: {
        constructEvent: vi.fn(() => ({ type: 'customer.subscription.updated', data: { object: sub } })),
      },
    });
    const app = Fastify();
    await reg(app, stripe, subs);
    const res = await app.inject({
      method: 'POST',
      url: '/v1/webhooks/stripe',
      headers: { 'stripe-signature': 'good', 'content-type': 'application/json' },
      payload: JSON.stringify({ any: 'thing' }),
    });
    expect(res.statusCode).toBe(200);
    expect(subs.rows.get('u1')?.status).toBe('active');
    expect(await subs.isActive('u1')).toBe(true);
    await app.close();
  });
});
