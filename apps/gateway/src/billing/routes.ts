import type { FastifyInstance } from 'fastify';
import type Stripe from 'stripe';
import type { RequireUser } from '../auth/middleware.js';
import type { SubscriptionStore, UserStore } from '../db/repos.js';

interface Deps {
  stripe: Stripe;
  subs: SubscriptionStore;
  users: UserStore;
  requireUser: RequireUser;
  priceId: string;
  webhookSecret: string;
  publicBaseUrl: string;
}

/**
 * Stripe billing: hosted Checkout to subscribe, hosted Customer Portal to
 * manage, and a signed webhook that syncs subscription state into our DB (the
 * source of truth for entitlement). All user-facing routes require an
 * authenticated session; the webhook is unauthenticated but signature-verified
 * and needs the raw request body (config.rawBody) for that verification.
 */
export async function registerBillingRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  const { stripe, subs, users, requireUser, priceId, webhookSecret, publicBaseUrl } = deps;

  // Find-or-create the Stripe customer for a user, persisting the link on first
  // use so subsequent checkouts/portal sessions reuse it.
  async function ensureCustomer(userId: string): Promise<string> {
    const existing = await subs.getByUser(userId);
    if (existing) return existing.stripeCustomerId;
    const user = await users.findById(userId);
    const customer = await stripe.customers.create({
      ...(user?.email ? { email: user.email } : {}),
      metadata: { userId },
    });
    await subs.ensureCustomer(userId, customer.id);
    return customer.id;
  }

  async function applySubscription(sub: Stripe.Subscription): Promise<void> {
    const customerId = typeof sub.customer === 'string' ? sub.customer : sub.customer.id;
    await subs.upsertFromStripe(customerId, {
      stripeSubscriptionId: sub.id,
      status: sub.status,
      priceId: sub.items.data[0]?.price?.id ?? null,
      currentPeriodEnd: sub.current_period_end ? new Date(sub.current_period_end * 1000) : null,
      cancelAtPeriodEnd: sub.cancel_at_period_end,
    });
  }

  app.post('/v1/billing/checkout', { preHandler: requireUser }, async (req, reply) => {
    const customerId = await ensureCustomer(req.userId!);
    const session = await stripe.checkout.sessions.create({
      mode: 'subscription',
      customer: customerId,
      line_items: [{ price: priceId, quantity: 1 }],
      success_url: `${publicBaseUrl}/dashboard?sub=success`,
      cancel_url: `${publicBaseUrl}/billing?sub=cancel`,
    });
    return reply.send({ url: session.url });
  });

  app.post('/v1/billing/portal', { preHandler: requireUser }, async (req, reply) => {
    const rec = await subs.getByUser(req.userId!);
    if (!rec) return reply.status(400).send({ error: 'no_customer' });
    const session = await stripe.billingPortal.sessions.create({
      customer: rec.stripeCustomerId,
      return_url: `${publicBaseUrl}/billing`,
    });
    return reply.send({ url: session.url });
  });

  app.get('/v1/billing/status', { preHandler: requireUser }, async (req, reply) => {
    const rec = await subs.getByUser(req.userId!);
    return reply.send({
      enabled: true,
      active: await subs.isActive(req.userId!),
      status: rec?.status ?? null,
      currentPeriodEnd: rec?.currentPeriodEnd ?? null,
      cancelAtPeriodEnd: rec?.cancelAtPeriodEnd ?? false,
    });
  });

  // Stripe → us. No session auth; we verify the signature over the raw body.
  app.post('/v1/webhooks/stripe', { config: { rawBody: true } }, async (req, reply) => {
    const sig = req.headers['stripe-signature'];
    if (!sig || req.rawBody == null) {
      return reply.status(400).send({ error: 'missing_signature' });
    }
    let event: Stripe.Event;
    try {
      event = stripe.webhooks.constructEvent(req.rawBody, sig as string, webhookSecret);
    } catch {
      return reply.status(400).send({ error: 'invalid_signature' });
    }

    switch (event.type) {
      case 'checkout.session.completed': {
        const s = event.data.object as Stripe.Checkout.Session;
        if (s.subscription) {
          const sub = await stripe.subscriptions.retrieve(s.subscription as string);
          await applySubscription(sub);
        }
        break;
      }
      case 'customer.subscription.created':
      case 'customer.subscription.updated':
      case 'customer.subscription.deleted':
        await applySubscription(event.data.object as Stripe.Subscription);
        break;
      default:
        break; // ignore other event types
    }
    return reply.send({ received: true });
  });
}

/**
 * Fallback when Stripe is not configured: the gateway still boots and the app
 * runs as fully free (no one has an active subscription, so paid features stay
 * locked). Billing actions report unavailable instead of crashing.
 */
export async function registerBillingDisabledRoutes(
  app: FastifyInstance,
  deps: { requireUser: RequireUser },
): Promise<void> {
  const { requireUser } = deps;
  app.get('/v1/billing/status', { preHandler: requireUser }, async (_req, reply) =>
    reply.send({
      enabled: false,
      active: false,
      status: null,
      currentPeriodEnd: null,
      cancelAtPeriodEnd: false,
    }),
  );
  const unavailable = (path: string) =>
    app.post(path, { preHandler: requireUser }, async (_req, reply) =>
      reply.status(503).send({ error: 'billing_unavailable' }),
    );
  unavailable('/v1/billing/checkout');
  unavailable('/v1/billing/portal');
  app.post('/v1/webhooks/stripe', async (_req, reply) =>
    reply.status(503).send({ error: 'billing_unavailable' }),
  );
}
