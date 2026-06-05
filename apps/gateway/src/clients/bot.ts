import { StrategyConfig } from '@xbt/shared';
import { z } from 'zod';
import { InternalAuthSigner } from './internal-auth.js';

export const RiskLimits = z.object({
  // Absolute loss cap in the strategy's quote currency. String to preserve
  // decimal precision over JSON, mirroring quote_amount in StrategyConfig.
  max_loss_quote: z.string().nullable().optional(),
});
export type RiskLimits = z.infer<typeof RiskLimits>;

// Envelope-encrypted exchange credentials, produced by the Gateway's encrypt
// path (security/keys.ts). Forwarded verbatim to the Bot Engine, which is the
// only service that decrypts. Required by the Bot Engine for mode 'live'.
export const EncryptedKeyEnvelope = z.object({
  v: z.number().int(),
  dek_iv: z.string(),
  dek_ct: z.string(),
  data_iv: z.string(),
  data_ct: z.string(),
});
export type EncryptedKeyEnvelope = z.infer<typeof EncryptedKeyEnvelope>;

export const StartBotRequest = z.object({
  strategy: StrategyConfig,
  mode: z.enum(['paper', 'live']).default('paper'),
  // ccxt venue id (e.g. 'binance'); drives the live client + market-data feed.
  exchange: z.string().optional(),
  risk: RiskLimits.optional(),
  credentials: EncryptedKeyEnvelope.optional(),
});
export type StartBotRequest = z.infer<typeof StartBotRequest>;

export const BotStateResponse = z.object({
  bot_id: z.string(),
  state: z.string(),
  last_error: z.string().nullable().optional(),
});
export type BotStateResponse = z.infer<typeof BotStateResponse>;

export const KillAllResponse = z.object({
  killed: z.array(z.string()),
});
export type KillAllResponse = z.infer<typeof KillAllResponse>;

export class BotEngineError extends Error {
  constructor(
    public readonly status: number,
    public readonly upstream: string,
  ) {
    super(`bot engine ${status}: ${upstream}`);
  }
}

export type FetchLike = (
  input: string,
  init: { method: string; headers: Record<string, string>; body: string },
) => Promise<{ status: number; text(): Promise<string> }>;

export class BotEngineClient {
  constructor(
    private readonly baseUrl: string,
    private readonly signer: InternalAuthSigner,
    private readonly fetchImpl: FetchLike = globalFetch,
  ) {}

  async startBot(args: {
    userId: string;
    botId: string;
    body: StartBotRequest;
  }): Promise<BotStateResponse> {
    return this.#post(`/bots/${args.botId}/start`, args.userId, args.body);
  }

  async stopBot(args: { userId: string; botId: string }): Promise<BotStateResponse> {
    return this.#post(`/bots/${args.botId}/stop`, args.userId, undefined);
  }

  /** Force-stop a single bot (cancels its open orders). */
  async killBot(args: { userId: string; botId: string }): Promise<BotStateResponse> {
    return this.#post(`/bots/${args.botId}/kill`, args.userId, undefined);
  }

  /** Global kill switch — force-stop all of the caller's running bots. */
  async killAll(args: { userId: string }): Promise<KillAllResponse> {
    const text = await this.#send('POST', '/bots/kill-all', args.userId, '');
    return KillAllResponse.parse(JSON.parse(text));
  }

  async getBot(args: { userId: string; botId: string }): Promise<BotStateResponse> {
    return this.#request('GET', `/bots/${args.botId}`, args.userId, '');
  }

  async #post(path: string, userId: string, body: unknown): Promise<BotStateResponse> {
    const bodyStr = body === undefined ? '' : JSON.stringify(body);
    return this.#request('POST', path, userId, bodyStr);
  }

  async #request(
    method: string,
    path: string,
    userId: string,
    body: string,
  ): Promise<BotStateResponse> {
    const text = await this.#send(method, path, userId, body);
    return BotStateResponse.parse(JSON.parse(text));
  }

  async #send(method: string, path: string, userId: string, body: string): Promise<string> {
    const headers = this.signer.sign({ method, path, userId, body });
    const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers: { ...headers, 'content-type': 'application/json' },
      body,
    });
    const text = await res.text();
    if (res.status >= 400) {
      throw new BotEngineError(res.status, text);
    }
    return text;
  }
}

const globalFetch: FetchLike = async (input, init) => {
  const res = await fetch(input, init);
  return { status: res.status, text: () => res.text() };
};
