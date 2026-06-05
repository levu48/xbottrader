import { StrategyConfig } from '@xbt/shared';
import { z } from 'zod';
import { FetchLike } from './bot.js';
import { InternalAuthSigner } from './internal-auth.js';

export const ChatMessage = z.object({
  role: z.enum(['user', 'assistant']),
  content: z.string().min(1),
});

export const CopilotChatRequest = z.object({
  messages: z.array(ChatMessage).min(1),
  context: z.string().optional(),
});
export type CopilotChatRequest = z.infer<typeof CopilotChatRequest>;

export const BacktestRequest = z.object({
  strategy: StrategyConfig,
  timeframe: z.string().optional(),
  limit: z.number().int().optional(),
  since_ms: z.number().int().optional(),
  starting_cash: z.string().optional(),
  exchange: z.string().optional(),
});
export type BacktestRequest = z.infer<typeof BacktestRequest>;

export class AiEngineError extends Error {
  constructor(
    public readonly status: number,
    public readonly upstream: string,
  ) {
    super(`ai engine ${status}: ${upstream}`);
  }
}

export class AiEngineClient {
  constructor(
    private readonly baseUrl: string,
    private readonly signer: InternalAuthSigner,
    private readonly fetchImpl: FetchLike = globalFetch,
  ) {}

  async chat(args: { userId: string; body: CopilotChatRequest }): Promise<unknown> {
    return this.#post('/copilot/chat', args.userId, args.body);
  }

  async backtest(args: { userId: string; body: BacktestRequest }): Promise<unknown> {
    return this.#post('/backtest/run', args.userId, args.body);
  }

  async #post(path: string, userId: string, body: unknown): Promise<unknown> {
    const bodyStr = JSON.stringify(body);
    const headers = this.signer.sign({ method: 'POST', path, userId, body: bodyStr });
    const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: { ...headers, 'content-type': 'application/json' },
      body: bodyStr,
    });
    const text = await res.text();
    if (res.status >= 400) {
      throw new AiEngineError(res.status, text);
    }
    return JSON.parse(text);
  }
}

const globalFetch: FetchLike = async (input, init) => {
  const res = await fetch(input, init);
  return { status: res.status, text: () => res.text() };
};
