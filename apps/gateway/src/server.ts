import fastifyCookie from '@fastify/cookie';
import fastifyWebsocket from '@fastify/websocket';
import Fastify from 'fastify';
import fastifyRawBody from 'fastify-raw-body';
import { Redis } from 'ioredis';
import Stripe from 'stripe';
import { hostname } from 'node:os';

import { makeRequireSubscription, makeRequireUser, resolveUserId } from './auth/middleware.js';
import { registerAuthRoutes } from './auth/routes.js';
import { RedisSessionStore } from './auth/sessions.js';
import { registerBillingDisabledRoutes, registerBillingRoutes } from './billing/routes.js';
import { AiEngineClient } from './clients/ai.js';
import { BotEngineClient } from './clients/bot.js';
import { InternalAuthSigner } from './clients/internal-auth.js';
import { createDb } from './db/client.js';
import { runMigrations } from './db/migrate.js';
import {
  DrizzleAuditLog,
  DrizzleKeyStore,
  DrizzleSubscriptionStore,
  DrizzleUserStore,
} from './db/repos.js';
import { registerKeysRoutes } from './keys/routes.js';
import { registerAiRoutes } from './routes/ai.js';
import { registerBotsRoutes } from './routes/bots.js';
import { registerMarketRoutes } from './routes/market.js';
import { EnvelopeCipher } from './security/keys.js';
import { EventStreamConsumer } from './ws/consumer.js';
import { IoredisStreamReader } from './ws/redis-reader.js';
import { ConnectionRegistry } from './ws/registry.js';

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} env var must be set`);
  return v;
}

async function main(): Promise<void> {
  const port = Number(process.env.GATEWAY_PORT ?? 4000);
  const redisUrl = process.env.REDIS_URL ?? 'redis://localhost:6379';
  const databaseUrl = requireEnv('DATABASE_URL');

  // Apply migrations on boot (single-instance staging). Disable with
  // XBT_DB_MIGRATE_ON_BOOT=0 to run them out-of-band instead.
  if (process.env.XBT_DB_MIGRATE_ON_BOOT !== '0') {
    await runMigrations(databaseUrl);
  }

  const app = Fastify({ logger: { level: 'info' } });
  await app.register(fastifyCookie);
  await app.register(fastifyWebsocket);
  // Capture the raw body only where opted in (config.rawBody) — the Stripe
  // webhook needs the exact bytes to verify its signature. All other routes
  // keep Fastify's default JSON parser.
  await app.register(fastifyRawBody, { global: false, runFirst: true });

  // --- persistence + auth ---
  const db = createDb(databaseUrl);
  const users = new DrizzleUserStore(db);
  const keys = new DrizzleKeyStore(db);
  const audit = new DrizzleAuditLog(db);
  const subs = new DrizzleSubscriptionStore(db);
  const sessionRedis = new Redis(redisUrl);
  const sessions = new RedisSessionStore(sessionRedis);
  const cipher = EnvelopeCipher.fromEnv();
  const requireUser = makeRequireUser(sessions);
  const requireSubscription = makeRequireSubscription(subs);

  // --- service clients ---
  const signer = InternalAuthSigner.fromEnv();
  const botClient = new BotEngineClient(process.env.BOT_ENGINE_URL ?? 'http://localhost:5001', signer);
  const aiClient = new AiEngineClient(process.env.AI_ENGINE_URL ?? 'http://localhost:5002', signer);

  // --- routes ---
  await registerAuthRoutes(app, { users, sessions, audit });
  await registerKeysRoutes(app, { keys, cipher, requireUser });
  await registerBotsRoutes(app, { bot: botClient, requireUser, keys, users, subs });
  await registerMarketRoutes(app, { bot: botClient, requireUser });
  await registerAiRoutes(app, { ai: aiClient, requireUser, requireSubscription });

  // Billing degrades gracefully: with Stripe unconfigured the gateway still
  // boots and the app runs fully free (paid features stay locked because no one
  // has an active subscription).
  const stripeSecret = process.env.STRIPE_SECRET_KEY;
  const stripePriceId = process.env.STRIPE_PRICE_ID;
  const stripeWebhookSecret = process.env.STRIPE_WEBHOOK_SECRET;
  if (stripeSecret && stripePriceId && stripeWebhookSecret) {
    await registerBillingRoutes(app, {
      stripe: new Stripe(stripeSecret),
      subs,
      users,
      requireUser,
      priceId: stripePriceId,
      webhookSecret: stripeWebhookSecret,
      publicBaseUrl: process.env.PUBLIC_BASE_URL ?? 'https://xbottrader.ai',
    });
  } else {
    app.log.warn('Stripe not configured (STRIPE_SECRET_KEY/PRICE_ID/WEBHOOK_SECRET) — billing disabled');
    await registerBillingDisabledRoutes(app, { requireUser });
  }

  app.get('/healthz', async () => ({ ok: true }));

  const registry = new ConnectionRegistry();
  app.get('/ws', { websocket: true }, async (socket, req) => {
    // Cookie-based: the browser sends xbt_session on the same-origin WS upgrade.
    const userId = await resolveUserId(req, sessions);
    if (!userId) {
      socket.close(4401, 'unauthenticated');
      return;
    }
    const sender = {
      send: (d: string) => socket.send(d),
      isOpen: () => socket.readyState === socket.OPEN,
    };
    registry.add(userId, sender);
    socket.on('close', () => registry.remove(userId, sender));
  });

  const redisCmd = new Redis(redisUrl, { maxRetriesPerRequest: null });
  const reader = new IoredisStreamReader(redisCmd);
  const consumer = new EventStreamConsumer(reader, registry, `gateway-${hostname()}-${process.pid}`);

  consumer.start().catch((e) => app.log.error({ err: e }, 'stream consumer crashed'));

  const shutdown = async (): Promise<void> => {
    consumer.stop();
    await app.close();
    redisCmd.disconnect();
    sessionRedis.disconnect();
  };
  process.on('SIGINT', () => void shutdown().then(() => process.exit(0)));
  process.on('SIGTERM', () => void shutdown().then(() => process.exit(0)));

  await app.listen({ port, host: '0.0.0.0' });
  app.log.info({ port }, 'gateway listening');
}

void main();
