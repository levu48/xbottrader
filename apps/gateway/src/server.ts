import fastifyWebsocket from '@fastify/websocket';
import Fastify from 'fastify';
import { Redis } from 'ioredis';
import { hostname } from 'node:os';

import { AiEngineClient } from './clients/ai.js';
import { BotEngineClient } from './clients/bot.js';
import { InternalAuthSigner } from './clients/internal-auth.js';
import { registerAiRoutes } from './routes/ai.js';
import { registerBotsRoutes } from './routes/bots.js';
import { EventStreamConsumer } from './ws/consumer.js';
import { IoredisStreamReader } from './ws/redis-reader.js';
import { ConnectionRegistry } from './ws/registry.js';

// Auth is stubbed for the MVP scaffold. Real implementation: validate a session
// token (cookie or query) against the sessions table before opening the socket.
// Tracked in apps/gateway/src/auth/.
function authStub(token: string | undefined): string | null {
  if (!token) return null;
  return token.startsWith('user:') ? token.slice('user:'.length) : null;
}

async function main(): Promise<void> {
  const port = Number(process.env.GATEWAY_PORT ?? 4000);
  const redisUrl = process.env.REDIS_URL ?? 'redis://localhost:6379';

  const app = Fastify({ logger: { level: 'info' } });
  await app.register(fastifyWebsocket);

  const registry = new ConnectionRegistry();

  const botEngineUrl = process.env.BOT_ENGINE_URL ?? 'http://localhost:5001';
  const aiEngineUrl = process.env.AI_ENGINE_URL ?? 'http://localhost:5002';
  const signer = InternalAuthSigner.fromEnv();
  const botClient = new BotEngineClient(botEngineUrl, signer);
  const aiClient = new AiEngineClient(aiEngineUrl, signer);
  await registerBotsRoutes(app, { bot: botClient });
  await registerAiRoutes(app, { ai: aiClient });

  app.get('/healthz', async () => ({ ok: true }));

  app.get('/ws', { websocket: true }, (socket, req) => {
    const token =
      typeof req.query === 'object' && req.query !== null && 'token' in req.query
        ? String((req.query as { token: unknown }).token)
        : undefined;
    const userId = authStub(token);
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
  };
  process.on('SIGINT', () => void shutdown().then(() => process.exit(0)));
  process.on('SIGTERM', () => void shutdown().then(() => process.exit(0)));

  await app.listen({ port, host: '0.0.0.0' });
  app.log.info({ port }, 'gateway listening');
}

void main();
