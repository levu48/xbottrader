import { authenticator } from 'otplib';

const ISSUER = 'xbottrader';

export interface TotpEnrollment {
  secret: string;
  otpauthUrl: string;
}

export function generateTotp(accountLabel: string): TotpEnrollment {
  const secret = authenticator.generateSecret();
  return { secret, otpauthUrl: authenticator.keyuri(accountLabel, ISSUER, secret) };
}

export function verifyTotp(secret: string, token: string): boolean {
  try {
    return authenticator.verify({ token, secret });
  } catch {
    return false;
  }
}
