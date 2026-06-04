import { createHash, createHmac } from 'node:crypto';

/**
 * HMAC scheme matching apps/bot-engine/app/api/auth.py.
 *
 *     msg = method + "\n" + path + "\n" + ts + "\n" + user_id + "\n" + sha256(body)
 *     sig = hex(hmac_sha256(secret, msg))
 *
 * Headers:
 *     X-XBT-User: <user_id>
 *     X-XBT-Ts:   <unix seconds>
 *     X-XBT-Sig:  <hex sha256 hmac>
 */
export const HEADER_USER = 'x-xbt-user';
export const HEADER_TS = 'x-xbt-ts';
export const HEADER_SIG = 'x-xbt-sig';

export interface SignedHeaders {
  [HEADER_USER]: string;
  [HEADER_TS]: string;
  [HEADER_SIG]: string;
}

export class InternalAuthSigner {
  readonly #secret: Buffer;

  constructor(secret: string | Buffer) {
    const buf = typeof secret === 'string' ? Buffer.from(secret, 'utf8') : secret;
    if (buf.length === 0) throw new Error('internal auth secret must be non-empty');
    this.#secret = buf;
  }

  static fromEnv(envVar = 'GATEWAY_INTERNAL_HMAC_SECRET'): InternalAuthSigner {
    const raw = process.env[envVar];
    if (!raw) throw new Error(`${envVar} env var not set`);
    return new InternalAuthSigner(raw);
  }

  sign(args: {
    method: string;
    path: string;
    userId: string;
    body: string | Buffer;
    ts?: number;
  }): SignedHeaders {
    const ts = args.ts ?? Math.floor(Date.now() / 1000);
    const bodyBuf = typeof args.body === 'string' ? Buffer.from(args.body, 'utf8') : args.body;
    const bodyHash = createHash('sha256').update(bodyBuf).digest('hex');
    const msg = `${args.method.toUpperCase()}\n${args.path}\n${ts}\n${args.userId}\n${bodyHash}`;
    const sig = createHmac('sha256', this.#secret).update(msg).digest('hex');
    return {
      [HEADER_USER]: args.userId,
      [HEADER_TS]: String(ts),
      [HEADER_SIG]: sig,
    };
  }
}
