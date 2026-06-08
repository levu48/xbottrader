import { describe, expect, it } from 'vitest';
import { BotEngineClient, BotEngineError, StartBotRequest, type FetchLike } from './bot.js';
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

  it('signs the kill request and targets the kill path', async () => {
    const captured = { calls: [] as { url: string; init: Parameters<FetchLike>[1] }[] };
    const client = new BotEngineClient(
      'http://upstream:5001',
      signer,
      fakeFetch(200, '{"bot_id":"b1","state":"killed"}', captured),
    );
    const res = await client.killBot({ userId: 'u1', botId: 'b1' });
    expect(res.state).toBe('killed');
    expect(captured.calls[0]!.url).toBe('http://upstream:5001/bots/b1/kill');
  });

  it('does not attach a body to the GET state request', async () => {
    // undici rejects GET/HEAD with a body (even ''); regression for a live 500.
    const captured = { calls: [] as { url: string; init: Parameters<FetchLike>[1] }[] };
    const client = new BotEngineClient(
      'http://upstream:5001',
      signer,
      fakeFetch(200, '{"bot_id":"b1","state":"running"}', captured),
    );
    const res = await client.getBot({ userId: 'u1', botId: 'b1' });
    expect(res.state).toBe('running');
    const call = captured.calls[0]!;
    expect(call.init.method).toBe('GET');
    expect(call.init.body).toBeUndefined();
    // still signed (over the empty body)
    expect(call.init.headers[HEADER_SIG]).toMatch(/^[a-f0-9]{64}$/);
  });

  it('sends OHLCV query params on the URL but signs only the bare path', async () => {
    const captured = { calls: [] as { url: string; init: Parameters<FetchLike>[1] }[] };
    const client = new BotEngineClient(
      'http://upstream:5001',
      signer,
      fakeFetch(
        200,
        '{"exchange":"binance","symbol":"BTC/USDT","timeframe":"1m","candles":[[1,2,3,0.5,2.5,9]]}',
        captured,
      ),
    );
    const res = await client.getOhlcv({
      userId: 'u1',
      exchange: 'binance',
      symbol: 'BTC/USDT',
      timeframe: '1m',
      limit: 200,
    });
    expect(res.candles).toHaveLength(1);

    const call = captured.calls[0]!;
    const url = new URL(call.url);
    expect(url.pathname).toBe('/market/ohlcv');
    expect(url.searchParams.get('symbol')).toBe('BTC/USDT');
    expect(url.searchParams.get('limit')).toBe('200');
    expect(call.init.method).toBe('GET');
    expect(call.init.body).toBeUndefined();

    // The signature must cover the bare path (the Bot Engine verifies against
    // request.url.path, which excludes the query) — recompute at the same ts.
    const ts = Number(call.init.headers[HEADER_TS]);
    const expected = signer.sign({ method: 'GET', path: '/market/ohlcv', userId: 'u1', body: '', ts });
    expect(call.init.headers[HEADER_SIG]).toBe(expected[HEADER_SIG]);
    // ...and NOT the path-with-query, proving the query is excluded.
    const withQuery = signer.sign({
      method: 'GET',
      path: '/market/ohlcv?exchange=binance&symbol=BTC/USDT&timeframe=1m&limit=200',
      userId: 'u1',
      body: '',
      ts,
    });
    expect(call.init.headers[HEADER_SIG]).not.toBe(withQuery[HEADER_SIG]);
  });

  it('parses the kill-all response shape', async () => {
    const captured = { calls: [] as { url: string; init: Parameters<FetchLike>[1] }[] };
    const client = new BotEngineClient(
      'http://upstream:5001',
      signer,
      fakeFetch(200, '{"killed":["b1","b2"]}', captured),
    );
    const res = await client.killAll({ userId: 'u1' });
    expect(res.killed).toEqual(['b1', 'b2']);
    expect(captured.calls[0]!.url).toBe('http://upstream:5001/bots/kill-all');
  });
});

describe('StartBotRequest venue/symbol validation', () => {
  const dca = (symbol: string) => ({
    strategy_type: 'dca' as const,
    symbol,
    quote_amount: '100',
    interval_minutes: 60,
  });

  it('accepts a crypto pair on a crypto venue', () => {
    expect(StartBotRequest.safeParse({ strategy: dca('BTC/USDT'), exchange: 'binance' }).success).toBe(true);
  });

  it('accepts a bare ticker on alpaca', () => {
    expect(StartBotRequest.safeParse({ strategy: dca('AAPL'), exchange: 'alpaca' }).success).toBe(true);
    expect(StartBotRequest.safeParse({ strategy: dca('BRK.B'), exchange: 'alpaca' }).success).toBe(true);
  });

  it('rejects a stock ticker on a crypto venue', () => {
    expect(StartBotRequest.safeParse({ strategy: dca('AAPL'), exchange: 'binance' }).success).toBe(false);
  });

  it('rejects a crypto pair on alpaca', () => {
    expect(StartBotRequest.safeParse({ strategy: dca('BTC/USDT'), exchange: 'alpaca' }).success).toBe(false);
  });

  it('defaults to crypto validation when exchange is omitted', () => {
    expect(StartBotRequest.safeParse({ strategy: dca('BTC/USDT') }).success).toBe(true);
    expect(StartBotRequest.safeParse({ strategy: dca('AAPL') }).success).toBe(false);
  });
});
