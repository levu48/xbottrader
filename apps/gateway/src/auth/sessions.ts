import { randomBytes } from 'node:crypto';

export interface SessionData {
  userId: string;
  // false between password step and TOTP step when 2FA is enabled.
  totpVerified: boolean;
}

export interface SessionStore {
  create(data: SessionData): Promise<string>;
  get(token: string): Promise<SessionData | null>;
  setVerified(token: string, verified: boolean): Promise<void>;
  revoke(token: string): Promise<void>;
}

// Minimal slice of ioredis we need — keeps this testable with an in-memory fake.
export interface RedisLike {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, mode: 'EX', ttlSeconds: number): Promise<unknown>;
  del(key: string): Promise<unknown>;
}

const TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days

export class RedisSessionStore implements SessionStore {
  constructor(
    private readonly redis: RedisLike,
    private readonly prefix = 'sess:',
  ) {}

  async create(data: SessionData): Promise<string> {
    const token = randomBytes(32).toString('base64url');
    await this.redis.set(this.prefix + token, JSON.stringify(data), 'EX', TTL_SECONDS);
    return token;
  }

  async get(token: string): Promise<SessionData | null> {
    if (!token) return null;
    const raw = await this.redis.get(this.prefix + token);
    return raw ? (JSON.parse(raw) as SessionData) : null;
  }

  async setVerified(token: string, verified: boolean): Promise<void> {
    const data = await this.get(token);
    if (!data) return;
    data.totpVerified = verified;
    await this.redis.set(this.prefix + token, JSON.stringify(data), 'EX', TTL_SECONDS);
  }

  async revoke(token: string): Promise<void> {
    await this.redis.del(this.prefix + token);
  }
}
