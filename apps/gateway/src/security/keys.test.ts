import { randomBytes } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { EnvelopeCipher, type EncryptedEnvelope } from './keys.js';

const kek = (): string => randomBytes(32).toString('base64');

describe('EnvelopeCipher', () => {
  it('round-trips a plaintext', () => {
    const c = new EnvelopeCipher(kek());
    const pt = 'binance-api-key-7f2e9c1a4b8d';
    expect(c.decrypt(c.encrypt(pt))).toBe(pt);
  });

  it('produces different ciphertexts for the same plaintext (random DEK + IV)', () => {
    const c = new EnvelopeCipher(kek());
    const a = c.encrypt('same');
    const b = c.encrypt('same');
    expect(a.data_ct).not.toBe(b.data_ct);
    expect(a.dek_ct).not.toBe(b.dek_ct);
    expect(a.dek_iv).not.toBe(b.dek_iv);
    expect(a.data_iv).not.toBe(b.data_iv);
  });

  it('decrypt with wrong KEK throws', () => {
    const c1 = new EnvelopeCipher(kek());
    const c2 = new EnvelopeCipher(kek());
    const env = c1.encrypt('secret');
    expect(() => c2.decrypt(env)).toThrow();
  });

  const flipFirstByte = (b64: string): string => {
    const buf = Buffer.from(b64, 'base64');
    buf[0] = (buf[0] ?? 0) ^ 1;
    return buf.toString('base64');
  };

  it('rejects tampered data ciphertext (GCM auth)', () => {
    const c = new EnvelopeCipher(kek());
    const env = c.encrypt('secret');
    expect(() => c.decrypt({ ...env, data_ct: flipFirstByte(env.data_ct) })).toThrow();
  });

  it('rejects tampered DEK ciphertext (GCM auth)', () => {
    const c = new EnvelopeCipher(kek());
    const env = c.encrypt('secret');
    expect(() => c.decrypt({ ...env, dek_ct: flipFirstByte(env.dek_ct) })).toThrow();
  });

  it('rejects unknown envelope version', () => {
    const c = new EnvelopeCipher(kek());
    const env = c.encrypt('secret');
    expect(() => c.decrypt({ ...env, v: 999 })).toThrow(/version/i);
  });

  it('rejects KEK of wrong length', () => {
    expect(() => new EnvelopeCipher(Buffer.alloc(16).toString('base64'))).toThrow(/32 bytes/);
  });

  it('round-trips empty string', () => {
    const c = new EnvelopeCipher(kek());
    expect(c.decrypt(c.encrypt(''))).toBe('');
  });

  it('round-trips a 10KB string', () => {
    const c = new EnvelopeCipher(kek());
    const pt = randomBytes(5_000).toString('hex'); // 10_000 chars
    expect(c.decrypt(c.encrypt(pt))).toBe(pt);
  });

  it('serialize / parse round-trip preserves envelope', () => {
    const c = new EnvelopeCipher(kek());
    const env = c.encrypt('secret');
    const parsed = EnvelopeCipher.parse(EnvelopeCipher.serialize(env));
    expect(c.decrypt(parsed)).toBe('secret');
  });
});
