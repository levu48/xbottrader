import { StrategyConfig } from '@xbt/shared';
import { z } from 'zod';
import { InternalAuthSigner } from './internal-auth.js';

export const StartBotRequest = z.object({
  strategy: StrategyConfig,
  mode: z.enum(['paper', 'live']).default('paper'),
});
export type StartBotRequest = z.infer<typeof StartBotRequest>;

export const BotStateResponse = z.object({
  bot_id: z.string(),
  state: z.string(),
  last_error: z.string().nullable().optional(),
});
export type BotStateResponse = z.infer<typeof BotStateResponse>;

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
    return BotStateResponse.parse(JSON.parse(text));
  }
}

const globalFetch: FetchLike = async (input, init) => {
  const res = await fetch(input, init);
  return { status: res.status, text: () => res.text() };
};
