import { describe, expect, it } from 'vitest';
import { HEADER_SIG, HEADER_TS, HEADER_USER, InternalAuthSigner } from './internal-auth.js';

describe('InternalAuthSigner', () => {
  it('signs deterministically for fixed inputs', () => {
    const s = new InternalAuthSigner('test-secret');
    const a = s.sign({ method: 'POST', path: '/bots/b1/start', userId: 'u1', body: '{}', ts: 1_700_000_000 });
    const b = s.sign({ method: 'POST', path: '/bots/b1/start', userId: 'u1', body: '{}', ts: 1_700_000_000 });
    expect(a[HEADER_SIG]).toBe(b[HEADER_SIG]);
    expect(a[HEADER_USER]).toBe('u1');
    expect(a[HEADER_TS]).toBe('1700000000');
  });

  it('produces a different sig for a different path', () => {
    const s = new InternalAuthSigner('test-secret');
    const a = s.sign({ method: 'POST', path: '/bots/b1/start', userId: 'u1', body: '{}', ts: 1 });
    const b = s.sign({ method: 'POST', path: '/bots/b2/start', userId: 'u1', body: '{}', ts: 1 });
    expect(a[HEADER_SIG]).not.toBe(b[HEADER_SIG]);
  });

  it('produces a different sig for a different body', () => {
    const s = new InternalAuthSigner('test-secret');
    const a = s.sign({ method: 'POST', path: '/x', userId: 'u1', body: 'a', ts: 1 });
    const b = s.sign({ method: 'POST', path: '/x', userId: 'u1', body: 'b', ts: 1 });
    expect(a[HEADER_SIG]).not.toBe(b[HEADER_SIG]);
  });

  it('rejects an empty secret', () => {
    expect(() => new InternalAuthSigner('')).toThrow(/non-empty/);
  });

  it('defaults the timestamp to the current time', () => {
    const s = new InternalAuthSigner('k');
    const before = Math.floor(Date.now() / 1000);
    const out = s.sign({ method: 'GET', path: '/x', userId: 'u1', body: '' });
    const after = Math.floor(Date.now() / 1000);
    const ts = Number(out[HEADER_TS]);
    expect(ts).toBeGreaterThanOrEqual(before);
    expect(ts).toBeLessThanOrEqual(after);
  });
});
