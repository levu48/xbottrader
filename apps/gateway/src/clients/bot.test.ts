import { describe, expect, it } from 'vitest';
import { BotEngineClient, BotEngineError, type FetchLike } from './bot.js';
import { HEADER_SIG, HEADER_TS, HEADER_USER, InternalAuthSigner } from './internal-auth.js';

const fakeFetch = (
  status: number,
  body: string,
  capture: { calls: { url: string; init: Parameters<FetchLike>[1] }[] },
): FetchLike => {
  return async (url, init) => {
    capture.calls.push({ url, init });
    return { status, text: async () => body };
  };
};

const signer = new InternalAuthSigner('test-secret');

describe('BotEngineClient', () => {
  it('signs the start request and parses the response', async () => {
    const captured = { calls: [] as { url: string; init: Parameters<FetchLike>[1] }[] };
    const client = new BotEngineClient(
      'http://upstream:5001',
      signer,
      fakeFetch(200, '{"bot_id":"b1","state":"starting"}', captured),
    );

    const result = await client.startBot({
      userId: 'u1',
      botId: 'b1',
      body: {
        strategy: {
          strategy_type: 'dca',
          symbol: 'BTC/USDT',
          quote_amount: '100',
          interval_minutes: 60,
        },
        mode: 'paper',
      },
    });

    expect(result.bot_id).toBe('b1');
    expect(captured.calls).toHaveLength(1);
    const call = captured.calls[0]!;
    expect(call.url).toBe('http://upstream:5001/bots/b1/start');
    expect(call.init.method).toBe('POST');
    expect(call.init.headers[HEADER_USER]).toBe('u1');
    expect(call.init.headers[HEADER_SIG]).toMatch(/^[a-f0-9]{64}$/);
    expect(call.init.headers[HEADER_TS]).toMatch(/^\d+$/);
    expect(call.init.headers['content-type']).toBe('application/json');
  });

  it('throws BotEngineError on a 4xx', async () => {
    const captured = { calls: [] as { url: string; init: Parameters<FetchLike>[1] }[] };
    const client = new BotEngineClient(
      'http://upstream:5001',
      signer,
      fakeFetch(409, 'already running', captured),
    );
    await expect(
      client.startBot({
        userId: 'u1',
        botId: 'b1',
        body: {
          strategy: {
            strategy_type: 'dca',
            symbol: 'BTC/USDT',
            quote_amount: '100',
            interval_minutes: 60,
          },
          mode: 'paper',
        },
      }),
    ).rejects.toBeInstanceOf(BotEngineError);
  });

  it('signs the stop request with an empty body', async () => {
    const captured = { calls: [] as { url: string; init: Parameters<FetchLike>[1] }[] };
    const client = new BotEngineClient(
      'http://upstream:5001',
      signer,
      fakeFetch(200, '{"bot_id":"b1","state":"stopped"}', captured),
    );
    await client.stopBot({ userId: 'u1', botId: 'b1' });
    const call = captured.calls[0]!;
    expect(call.init.body).toBe('');
    expect(call.init.headers[HEADER_USER]).toBe('u1');
  });
});
