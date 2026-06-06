import { authenticator } from 'otplib';
import { describe, expect, it } from 'vitest';
import { generateTotp, verifyTotp } from './totp.js';

describe('totp', () => {
  it('enrolls and verifies a current code', () => {
    const { secret, otpauthUrl } = generateTotp('user@example.com');
    expect(otpauthUrl).toContain('otpauth://totp/');
    expect(otpauthUrl).toContain('xbottrader');
    const code = authenticator.generate(secret);
    expect(verifyTotp(secret, code)).toBe(true);
  });

  it('rejects a wrong code', () => {
    const { secret } = generateTotp('user@example.com');
    expect(verifyTotp(secret, '000000')).toBe(false);
  });
});
