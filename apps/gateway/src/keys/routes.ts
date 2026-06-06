import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import type { RequireUser } from '../auth/middleware.js';
import type { KeyStore } from '../db/repos.js';
import type { EnvelopeCipher } from '../security/keys.js';

interface Deps {
  keys: KeyStore;
  cipher: EnvelopeCipher;
  requireUser: RequireUser;
}

const AddBody = z.object({
  exchange: z.string().min(1),
  apiKey: z.string().min(1),
  secret: z.string().min(1),
  passphrase: z.string().optional(),
  label: z.string().optional(),
});

export async function registerKeysRoutes(app: FastifyInstance, deps: Deps): Promise<void> {
  const { requireUser } = deps;

  // Store an exchange API key: encrypt to an envelope, persist ciphertext only.
  // Plaintext shape matches the Bot Engine's ExchangeCredentials decoder.
  app.post('/v1/keys', { preHandler: requireUser }, async (req, reply) => {
    const parsed = AddBody.safeParse(req.body);
    if (!parsed.success) return reply.status(400).send({ error: 'invalid_request' });
    const { exchange, apiKey, secret, passphrase, label } = parsed.data;
    const plaintext = JSON.stringify({
      apiKey,
      secret,
      ...(passphrase ? { password: passphrase } : {}),
    });
    const envelope = deps.cipher.encrypt(plaintext);
    const meta = await deps.keys.add(req.userId!, exchange, label ?? null, envelope);
    return reply.send(meta); // metadata only — never the secret
  });

  app.get('/v1/keys', { preHandler: requireUser }, async (req, reply) => {
    return reply.send({ keys: await deps.keys.listMeta(req.userId!) });
  });

  app.delete('/v1/keys/:id', { preHandler: requireUser }, async (req, reply) => {
    await deps.keys.remove(req.userId!, (req.params as { id: string }).id);
    return reply.send({ ok: true });
  });
}
