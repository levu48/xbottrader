import { describe, expect, it } from 'vitest';
import { hashPassword, verifyPassword } from './password.js';

describe('password', () => {
  it('hashes and verifies', async () => {
    const h = await hashPassword('correct horse battery staple');
    expect(h).not.toContain('correct horse'); // not plaintext
    expect(await verifyPassword(h, 'correct horse battery staple')).toBe(true);
    expect(await verifyPassword(h, 'wrong')).toBe(false);
  });

  it('returns false on a malformed hash instead of throwing', async () => {
    expect(await verifyPassword('not-a-hash', 'x')).toBe(false);
  });
});
