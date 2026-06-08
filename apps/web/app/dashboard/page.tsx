'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from 'lightweight-charts';

interface IncomingEvent {
  event_type: string;
  bot_id: string;
  ts: string;
  payload: Record<string, unknown>;
}
interface Me {
  id: string;
  email: string;
  totpEnabled: boolean;
}

type WsStatus = 'connecting' | 'open' | 'closed' | 'error';
type StrategyType = 'dca' | 'grid' | 'ma_crossover' | 'custom_rules' | 'ai_signal';
type ExchangeId = 'binance' | 'coinbase' | 'alpaca';

const EXCHANGES: { id: ExchangeId; label: string }[] = [
  { id: 'binance', label: 'Binance · crypto' },
  { id: 'coinbase', label: 'Coinbase · crypto' },
  { id: 'alpaca', label: 'Alpaca · US stocks' },
];

// Mirrors Symbol_ / assetClassFor in packages/shared/src/primitives.ts — the
// gateway rejects a mismatched symbol with a 400. Crypto venues use BASE/QUOTE
// (e.g. BTC/USDT); equity venues use a bare ticker (e.g. AAPL).
const CRYPTO_SYMBOL_RE = /^[A-Z0-9]+\/[A-Z0-9]+$/;
const EQUITY_SYMBOL_RE = /^[A-Z]{1,5}(\.[A-Z])?$/;
const isEquityExchange = (ex: string): boolean => ex === 'alpaca';
const symbolValid = (ex: string, sym: string): boolean =>
  (isEquityExchange(ex) ? EQUITY_SYMBOL_RE : CRYPTO_SYMBOL_RE).test(sym);
const symbolHint = (ex: string): string =>
  isEquityExchange(ex) ? 'a stock ticker, e.g. AAPL' : 'BASE/QUOTE, e.g. BTC/USDT';

// --- Custom rule-engine builder shapes (UI-side; flattened to the DSL on send) -
type IndFn = 'price' | 'value' | 'sma' | 'rsi';
type CompOp = '<' | '<=' | '>' | '>=' | '==' | 'crossover' | 'crossunder';
const COMP_OPS: CompOp[] = ['<', '<=', '>', '>=', '==', 'crossover', 'crossunder'];

interface IndicatorRow { name: string; fn: IndFn; period: string; value: string }
interface TermRow { left: string; op: CompOp; right: string }
interface RuleRow {
  combinator: 'and' | 'or';
  terms: TermRow[];
  side: 'buy' | 'sell';
  type: 'market' | 'limit';
  quote: string;
  limitOffsetPct: string;
  cooldownMinutes: string;
}
interface CustomCfg { indicators: IndicatorRow[]; rules: RuleRow[] }

// A ready-to-run example: SMA(10) crossing up through SMA(30) buys $100.
const DEFAULT_CUSTOM: CustomCfg = {
  indicators: [
    { name: 'fast', fn: 'sma', period: '10', value: '0' },
    { name: 'slow', fn: 'sma', period: '30', value: '0' },
  ],
  rules: [
    {
      combinator: 'and',
      terms: [{ left: 'fast', op: 'crossover', right: 'slow' }],
      side: 'buy', type: 'market', quote: '100', limitOffsetPct: '', cooldownMinutes: '0',
    },
  ],
};

// Same-origin: the gateway owns /ws + /v1 behind the app's ingress, and the
// browser sends the session cookie on the WS upgrade — no token needed.
function wsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws`;
}

// Events carry the internal per-user id "<userId>:<botId>"; show the plain part.
function displayBot(id: string): string {
  const i = id.indexOf(':');
  return i === -1 ? id : id.slice(i + 1);
}

const card: React.CSSProperties = { border: '1px solid #e5e5e5', borderRadius: 8, padding: 16, marginBottom: 16 };
const input: React.CSSProperties = { padding: '6px 8px', border: '1px solid #ccc', borderRadius: 6, marginRight: 8, fontSize: 14 };
const btn: React.CSSProperties = { padding: '6px 12px', border: '1px solid #2563eb', background: '#2563eb', color: '#fff', borderRadius: 6, cursor: 'pointer', fontSize: 14 };
const btnDanger: React.CSSProperties = { ...btn, border: '1px solid #dc2626', background: '#dc2626' };

export default function DashboardPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<WsStatus>('connecting');
  const [events, setEvents] = useState<IncomingEvent[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const [botId, setBotId] = useState('bot-1');
  const [exchange, setExchange] = useState<ExchangeId>('binance');
  const [symbol, setSymbol] = useState('BTC/USDT');
  const [strategyType, setStrategyType] = useState<StrategyType>('dca');
  // DCA
  const [quote, setQuote] = useState('50');
  const [interval, setIntervalMin] = useState('1');
  // Grid
  const [lowerPrice, setLowerPrice] = useState('20000');
  const [upperPrice, setUpperPrice] = useState('40000');
  const [gridLevels, setGridLevels] = useState('10');
  const [totalQuote, setTotalQuote] = useState('500');
  // MA crossover
  const [fastPeriod, setFastPeriod] = useState('10');
  const [slowPeriod, setSlowPeriod] = useState('30');
  const [positionQuote, setPositionQuote] = useState('100');
  // Custom rule engine
  const [customCfg, setCustomCfg] = useState<CustomCfg>(DEFAULT_CUSTOM);
  // AI signal (LLM in the loop)
  const [aiQuote, setAiQuote] = useState('50');
  const [aiIntervalMin, setAiIntervalMin] = useState('15');
  const [aiLookback, setAiLookback] = useState('50');
  const [aiGuidance, setAiGuidance] = useState('');
  const [aiModel, setAiModel] = useState('');
  // AI strategy author (NL -> custom_rules): describe it, AI fills the RuleBuilder.
  const [authorDesc, setAuthorDesc] = useState('');
  const [authoring, setAuthoring] = useState(false);
  const [authorNote, setAuthorNote] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Auth gate.
  useEffect(() => {
    fetch('/v1/auth/me').then(async (r) => {
      if (!r.ok) window.location.assign('/login');
      else setMe(await r.json());
    });
  }, []);

  // Connect the WS once authenticated.
  useEffect(() => {
    if (!me) return;
    setStatus('connecting');
    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;
    ws.onopen = () => setStatus('open');
    ws.onerror = () => setStatus('error');
    ws.onclose = () => setStatus('closed');
    ws.onmessage = (msg) => {
      try {
        setEvents((prev) => [JSON.parse(msg.data as string) as IncomingEvent, ...prev].slice(0, 300));
      } catch {
        /* ignore */
      }
    };
    return () => ws.close();
  }, [me]);

  const api = useCallback(async (path: string, body?: unknown): Promise<{ ok: boolean; text: string }> => {
    const headers: Record<string, string> = {};
    if (body) headers['content-type'] = 'application/json';
    const res = await fetch(path, { method: 'POST', headers, ...(body ? { body: JSON.stringify(body) } : {}) });
    return { ok: res.ok, text: await res.text() };
  }, []);

  // Build the discriminated strategy payload the Bot Engine expects. Decimal
  // fields go as strings (pydantic Decimal accepts them); counts as numbers.
  const buildStrategy = useCallback((): Record<string, unknown> => {
    switch (strategyType) {
      case 'grid':
        return {
          strategy_type: 'grid', symbol,
          lower_price: lowerPrice, upper_price: upperPrice,
          grid_levels: Number(gridLevels), total_quote: totalQuote,
        };
      case 'ma_crossover':
        return {
          strategy_type: 'ma_crossover', symbol,
          fast_period: Number(fastPeriod), slow_period: Number(slowPeriod),
          position_quote: positionQuote,
        };
      case 'custom_rules':
        return buildCustomRules(symbol, customCfg);
      case 'ai_signal': {
        const cfg: Record<string, unknown> = {
          strategy_type: 'ai_signal', symbol,
          quote_amount: aiQuote,
          decision_interval_minutes: Number(aiIntervalMin),
          lookback_bars: Number(aiLookback),
        };
        // guidance/model are optional — omit when blank so server defaults apply.
        if (aiGuidance.trim()) cfg.guidance = aiGuidance.trim();
        if (aiModel.trim()) cfg.model = aiModel.trim();
        return cfg;
      }
      case 'dca':
      default:
        return { strategy_type: 'dca', symbol, quote_amount: quote, interval_minutes: Number(interval) };
    }
  }, [strategyType, symbol, quote, interval, lowerPrice, upperPrice, gridLevels, totalQuote, fastPeriod, slowPeriod, positionQuote, customCfg, aiQuote, aiIntervalMin, aiLookback, aiGuidance, aiModel]);

  const startBot = useCallback(async () => {
    setNotice(null);
    if (!symbolValid(exchange, symbol)) {
      setNotice(`Symbol must be ${symbolHint(exchange)}.`);
      return;
    }
    const r = await api(`/v1/bots/${encodeURIComponent(botId)}/start`, {
      strategy: buildStrategy(),
      mode: 'paper',
      exchange,
    });
    setNotice(r.ok ? `started ${botId} (${strategyType} on ${exchange})` : `start failed: ${r.text}`);
  }, [api, botId, exchange, symbol, strategyType, buildStrategy]);

  // Ask the AI Engine to author a custom_rules strategy from a plain-English
  // description, then load it into the RuleBuilder so the user can review/edit
  // before starting — nothing trades until they hit Start.
  const generateRules = useCallback(async () => {
    setAuthorNote(null);
    if (!authorDesc.trim()) {
      setAuthorNote('Describe the strategy first.');
      return;
    }
    if (!symbolValid(exchange, symbol)) {
      setAuthorNote(`Symbol must be ${symbolHint(exchange)}.`);
      return;
    }
    setAuthoring(true);
    const r = await api('/v1/ai/strategy/author', {
      description: authorDesc.trim(),
      symbol,
      exchange,
    });
    setAuthoring(false);
    if (!r.ok) {
      setAuthorNote(`Generate failed: ${r.text}`);
      return;
    }
    try {
      const data = JSON.parse(r.text) as { strategy: AuthoredStrategy; explanation: string };
      const { cfg, warnings } = authoredToCustomCfg(data.strategy);
      setCustomCfg(cfg);
      setAuthorNote([data.explanation, ...warnings].filter(Boolean).join(' '));
    } catch {
      setAuthorNote('Could not parse the generated strategy.');
    }
  }, [api, authorDesc, exchange, symbol]);

  const killBot = useCallback(async (id: string) => {
    const r = await api(`/v1/bots/${encodeURIComponent(id)}/kill`);
    setNotice(r.ok ? `killed ${id}` : `kill failed: ${r.text}`);
  }, [api]);

  const killAll = useCallback(async () => {
    const r = await api('/v1/bots/kill-all');
    setNotice(r.ok ? `kill-all: ${r.text}` : `kill-all failed: ${r.text}`);
  }, [api]);

  async function logout() {
    await api('/v1/auth/logout');
    window.location.assign('/login');
  }

  const { fills, summary } = useMemo(() => {
    const fills = events.filter((e) => e.event_type === 'fill');
    let position = 0, cashOut = 0, fees = 0;
    for (const f of [...fills].reverse()) {
      const qty = Number(f.payload.quantity), price = Number(f.payload.price), fee = Number(f.payload.fee ?? 0);
      fees += fee;
      if (String(f.payload.side) === 'buy') { position += qty; cashOut += qty * price + fee; }
      else { position -= qty; cashOut -= qty * price - fee; }
    }
    const lastPrice = fills.length ? Number(fills[0]!.payload.price) : 0;
    return { fills, summary: { count: fills.length, position, fees, lastPrice, equity: position * lastPrice - cashOut } };
  }, [events]);

  if (!me) return <main style={{ padding: 32 }}>Loading…</main>;

  const statusColor = { open: '#16a34a', connecting: '#ca8a04', closed: '#6b7280', error: '#dc2626' }[status];

  return (
    <main style={{ padding: 32, maxWidth: 1000, fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ marginBottom: 0 }}>Dashboard</h1>
        <div style={{ fontSize: 14, color: '#666' }}>
          {me.email} · <a href="/settings">Settings</a> ·{' '}
          <button onClick={logout} style={{ ...btn, background: '#6b7280', border: 0 }}>Log out</button>
        </div>
      </div>
      <p style={{ marginTop: 4, color: '#666' }}>
        WebSocket: <strong style={{ color: statusColor }}>{status}</strong>
        {!me.totpEnabled && (
          <span style={{ color: '#ca8a04' }}> · enable 2FA in <a href="/settings">Settings</a> for live trading</span>
        )}
      </p>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Start a paper bot</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 0 }}>
          <Field label="bot id"><input style={{ ...input, width: 90 }} value={botId} onChange={(e) => setBotId(e.target.value)} /></Field>
          <Field label="exchange">
            <select
              style={{ ...input, width: 150 }}
              value={exchange}
              onChange={(e) => {
                const ex = e.target.value as ExchangeId;
                setExchange(ex);
                // Swap to a sensible default symbol when crossing asset classes.
                setSymbol((s) => (symbolValid(ex, s) ? s : isEquityExchange(ex) ? 'AAPL' : 'BTC/USDT'));
              }}
            >
              {EXCHANGES.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
            </select>
          </Field>
          <Field label="strategy">
            <select style={{ ...input, width: 140 }} value={strategyType} onChange={(e) => setStrategyType(e.target.value as StrategyType)}>
              <option value="dca">DCA</option>
              <option value="grid">Grid</option>
              <option value="ma_crossover">MA crossover</option>
              <option value="custom_rules">Custom rules</option>
              <option value="ai_signal">AI signal</option>
            </select>
          </Field>
          <Field label="symbol">
            <input
              style={{ ...input, width: 110, borderColor: symbol && !symbolValid(exchange, symbol) ? '#dc2626' : '#ccc' }}
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder={isEquityExchange(exchange) ? 'AAPL' : 'BTC/USDT'}
            />
          </Field>

          {strategyType === 'dca' && (
            <>
              <Field label="quote/buy"><input style={{ ...input, width: 70 }} value={quote} onChange={(e) => setQuote(e.target.value)} /></Field>
              <Field label="interval (min)"><input style={{ ...input, width: 70 }} value={interval} onChange={(e) => setIntervalMin(e.target.value)} /></Field>
            </>
          )}
          {strategyType === 'grid' && (
            <>
              <Field label="lower price"><input style={{ ...input, width: 80 }} value={lowerPrice} onChange={(e) => setLowerPrice(e.target.value)} /></Field>
              <Field label="upper price"><input style={{ ...input, width: 80 }} value={upperPrice} onChange={(e) => setUpperPrice(e.target.value)} /></Field>
              <Field label="levels"><input style={{ ...input, width: 60 }} value={gridLevels} onChange={(e) => setGridLevels(e.target.value)} /></Field>
              <Field label="total quote"><input style={{ ...input, width: 80 }} value={totalQuote} onChange={(e) => setTotalQuote(e.target.value)} /></Field>
            </>
          )}
          {strategyType === 'ma_crossover' && (
            <>
              <Field label="fast period"><input style={{ ...input, width: 70 }} value={fastPeriod} onChange={(e) => setFastPeriod(e.target.value)} /></Field>
              <Field label="slow period"><input style={{ ...input, width: 70 }} value={slowPeriod} onChange={(e) => setSlowPeriod(e.target.value)} /></Field>
              <Field label="position quote"><input style={{ ...input, width: 80 }} value={positionQuote} onChange={(e) => setPositionQuote(e.target.value)} /></Field>
            </>
          )}
          {strategyType === 'ai_signal' && (
            <>
              <Field label="quote/buy"><input style={{ ...input, width: 70 }} value={aiQuote} onChange={(e) => setAiQuote(e.target.value)} /></Field>
              <Field label="decision (min)"><input style={{ ...input, width: 80 }} value={aiIntervalMin} onChange={(e) => setAiIntervalMin(e.target.value)} /></Field>
              <Field label="lookback bars"><input style={{ ...input, width: 80 }} value={aiLookback} onChange={(e) => setAiLookback(e.target.value)} /></Field>
              <Field label="model (optional)"><input style={{ ...input, width: 150 }} value={aiModel} onChange={(e) => setAiModel(e.target.value)} placeholder="server default" /></Field>
            </>
          )}
        </div>
        {strategyType === 'ai_signal' && (
          <Field label="guidance (optional)">
            <textarea
              style={{ ...input, width: '100%', maxWidth: 640, minHeight: 60, resize: 'vertical', marginRight: 0 }}
              value={aiGuidance}
              onChange={(e) => setAiGuidance(e.target.value)}
              maxLength={2000}
              placeholder="Plain-English directive for the model, e.g. &quot;Only buy on strong upward momentum; stay in cash otherwise.&quot;"
            />
          </Field>
        )}
        {strategyType === 'custom_rules' && (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 8 }}>
            <Field label="describe a strategy — AI writes the rules (optional)">
              <textarea
                style={{ ...input, width: 'min(100%, 640px)', boxSizing: 'border-box', minHeight: 56, resize: 'vertical', marginRight: 0 }}
                value={authorDesc}
                onChange={(e) => setAuthorDesc(e.target.value)}
                maxLength={4000}
                placeholder='e.g. "Buy $100 when RSI(14) drops below 30; sell when it crosses above 70."'
              />
            </Field>
            <button
              style={{ ...btn, ...(authoring ? { opacity: 0.6, cursor: 'wait' } : {}) }}
              onClick={generateRules}
              disabled={authoring}
            >
              {authoring ? 'Generating…' : 'Generate rules'}
            </button>
            {authorNote && (
              <p style={{ margin: 0, color: '#374151', fontSize: 13 }}>{authorNote}</p>
            )}
            <p style={{ margin: 0, color: '#6b7280', fontSize: 12 }}>
              Generated rules load into the editor below — review and edit them before starting.
            </p>
          </div>
        )}
        {strategyType === 'custom_rules' && <RuleBuilder cfg={customCfg} setCfg={setCustomCfg} />}
        {symbol && !symbolValid(exchange, symbol) && (
          <p style={{ margin: '8px 0 0', color: '#dc2626', fontSize: 13 }}>
            Symbol must be {symbolHint(exchange)}.{' '}
            {isEquityExchange(exchange)
              ? 'US stock tickers only on Alpaca.'
              : 'Crypto pairs only on this exchange.'}
          </p>
        )}
        <div style={{ marginTop: 12 }}>
          <button style={{ ...btn, ...(symbolValid(exchange, symbol) ? {} : { opacity: 0.5, cursor: 'not-allowed' }) }} onClick={startBot} disabled={!symbolValid(exchange, symbol)}>Start</button>
          <button style={{ ...btnDanger, marginLeft: 8 }} onClick={() => killBot(botId)}>Kill this</button>
          <button style={{ ...btnDanger, marginLeft: 8 }} onClick={killAll}>Kill all</button>
        </div>
        {notice && <p style={{ marginBottom: 0, color: '#374151', fontSize: 13 }}>{notice}</p>}
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <Stat label="Fills" value={String(summary.count)} />
        <Stat label="Position" value={summary.position.toFixed(6)} />
        <Stat label="Last price" value={summary.lastPrice ? summary.lastPrice.toFixed(2) : '—'} />
        <Stat label="Fees" value={summary.fees.toFixed(4)} />
        <Stat label="PnL (mark-to-last)" value={summary.equity.toFixed(2)} accent={summary.equity >= 0 ? '#16a34a' : '#dc2626'} />
      </div>

      <div style={card}>
        <MarketChart exchange={exchange} symbol={symbol} />
      </div>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>
          {symbol} — fill price{' '}
          <span style={{ color: '#999', fontWeight: 400, fontSize: 13 }}>(live)</span>
        </h3>
        <PriceChart values={fills.map((f) => Number(f.payload.price)).reverse()} />
      </div>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Fills</h3>
        {fills.length === 0 ? (
          <p style={{ color: '#666' }}>No fills yet — start a bot above.</p>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ textAlign: 'left', color: '#666' }}><th>time</th><th>bot</th><th>side</th><th>qty</th><th>price</th><th>fee</th></tr></thead>
            <tbody>
              {fills.slice(0, 50).map((f, i) => (
                <tr key={`${f.ts}-${i}`} style={{ borderTop: '1px solid #eee' }}>
                  <td style={{ color: '#888' }}>{f.ts.slice(11, 19)}</td>
                  <td>{displayBot(f.bot_id)}</td>
                  <td style={{ color: f.payload.side === 'buy' ? '#16a34a' : '#dc2626' }}>{String(f.payload.side)}</td>
                  <td>{String(f.payload.quantity)}</td>
                  <td>{String(f.payload.price)}</td>
                  <td>{String(f.payload.fee)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Live events</h3>
        {events.length === 0 ? (
          <p style={{ color: '#666' }}>Waiting for events…</p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, fontFamily: 'monospace', fontSize: 12, margin: 0 }}>
            {events.slice(0, 60).map((e, i) => (
              <li key={`${e.ts}-${i}`} style={{ padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
                <span style={{ color: '#999' }}>{e.ts.slice(11, 19)}</span> <strong>{e.event_type}</strong> <span style={{ color: '#777' }}>{displayBot(e.bot_id)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}

// Dependency-free SVG line chart. x = sample index, y = value; auto-scaled.
function PriceChart({ values }: { values: number[] }) {
  if (values.length < 2) {
    return <p style={{ color: '#666', margin: 0 }}>Waiting for fills… (need at least 2)</p>;
  }
  const W = 640;
  const H = 200;
  const pad = 24;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const n = values.length;
  const x = (i: number) => pad + (n === 1 ? 0 : (i / (n - 1)) * (W - 2 * pad));
  const y = (v: number) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const d = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const last = values[n - 1]!;
  const first = values[0]!;
  const up = last >= first;
  const stroke = up ? '#16a34a' : '#dc2626';

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }} preserveAspectRatio="none">
        {/* baseline + area */}
        <path d={`${d} L${x(n - 1).toFixed(1)} ${H - pad} L${x(0).toFixed(1)} ${H - pad} Z`} fill={stroke} opacity={0.08} />
        <path d={d} fill="none" stroke={stroke} strokeWidth={2} vectorEffect="non-scaling-stroke" />
        <circle cx={x(n - 1)} cy={y(last)} r={3} fill={stroke} />
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#6b7280' }}>
        <span>low {min.toFixed(2)}</span>
        <span style={{ color: stroke, fontWeight: 600 }}>last {last.toFixed(2)}</span>
        <span>high {max.toFixed(2)}</span>
      </div>
    </div>
  );
}

// Live OHLCV candlestick chart for the selected symbol/exchange, rendered with
// lightweight-charts. Fetches on symbol/exchange/timeframe change and polls
// every 15s so the latest candle stays fresh. Market data is public, served by
// the Bot Engine via the gateway's /v1/market/ohlcv proxy.
type Timeframe = '1m' | '5m' | '1h' | '1d';
const TIMEFRAMES: Timeframe[] = ['1m', '5m', '1h', '1d'];
const POLL_MS = 15_000;

// Dig the Bot Engine's actionable detail out of the gateway's error envelope
// ({ error, upstream: '<json>' }) so the user sees e.g. "Alpaca market-data keys
// not configured" rather than a generic message. Falls back gracefully.
async function ohlcvErrorMessage(res: Response): Promise<string> {
  const fallback = 'Market data unavailable for this symbol.';
  try {
    const body = (await res.json()) as { upstream?: string; message?: string };
    const upstream = body.upstream ? (JSON.parse(body.upstream) as { detail?: string }) : null;
    return upstream?.detail ?? body.message ?? fallback;
  } catch {
    return fallback;
  }
}

function MarketChart({ exchange, symbol }: { exchange: string; symbol: string }) {
  const [timeframe, setTimeframe] = useState<Timeframe>('1m');
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);

  const valid = symbolValid(exchange, symbol);

  // Create the chart + candlestick series once, and keep its width in sync.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      height: 260,
      layout: { background: { color: '#fff' }, textColor: '#374151' },
      grid: { vertLines: { color: '#f3f4f6' }, horzLines: { color: '#f3f4f6' } },
      rightPriceScale: { borderColor: '#e5e5e5' },
      timeScale: { borderColor: '#e5e5e5', timeVisible: true, secondsVisible: false },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#16a34a',
      downColor: '#dc2626',
      wickUpColor: '#16a34a',
      wickDownColor: '#dc2626',
      borderVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const onResize = () => chart.applyOptions({ width: el.clientWidth });
    onResize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Load + poll candles. Re-runs whenever the symbol/exchange/timeframe changes;
  // fitContent only on the first load of each run so polling doesn't fight a
  // user's zoom/pan.
  useEffect(() => {
    if (!valid) {
      setError(null);
      return;
    }
    let cancelled = false;
    let first = true;
    const load = async () => {
      try {
        const qs = new URLSearchParams({ exchange, symbol, timeframe, limit: '200' });
        const res = await fetch(`/v1/market/ohlcv?${qs.toString()}`);
        if (!res.ok) {
          if (!cancelled) setError(await ohlcvErrorMessage(res));
          return;
        }
        const data = (await res.json()) as { candles: number[][] };
        if (cancelled || !seriesRef.current) return;
        const bars: CandlestickData<Time>[] = data.candles.map((c) => ({
          time: (c[0]! / 1000) as Time,
          open: c[1]!,
          high: c[2]!,
          low: c[3]!,
          close: c[4]!,
        }));
        seriesRef.current.setData(bars);
        if (first) {
          chartRef.current?.timeScale().fitContent();
          first = false;
        }
        setError(null);
      } catch {
        if (!cancelled) setError('Failed to load market data.');
      }
    };
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [exchange, symbol, timeframe, valid]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h3 style={{ marginTop: 0, marginBottom: 8 }}>
          {symbol} — market (OHLCV){' '}
          <span style={{ color: '#999', fontWeight: 400, fontSize: 13 }}>· {exchange}</span>
        </h3>
        <select
          style={{ ...input, width: 70, marginRight: 0 }}
          value={timeframe}
          onChange={(e) => setTimeframe(e.target.value as Timeframe)}
        >
          {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
        </select>
      </div>
      {!valid ? (
        <p style={{ color: '#666', margin: 0 }}>Enter a valid {symbolHint(exchange)} to see the chart.</p>
      ) : error ? (
        <p style={{ color: '#dc2626', margin: 0, fontSize: 13 }}>{error}</p>
      ) : null}
      <div ref={containerRef} style={{ width: '100%', ...(valid ? {} : { display: 'none' }) }} />
    </div>
  );
}

// Flatten the builder rows into the rule-engine DSL the Bot Engine validates.
// A single term emits a bare comparison; multiple terms wrap in and/or.
function buildCustomRules(symbol: string, cfg: CustomCfg): Record<string, unknown> {
  const indicators = cfg.indicators.map((i) => {
    const base: Record<string, unknown> = { name: i.name, fn: i.fn };
    if (i.fn === 'sma' || i.fn === 'rsi') base.period = Number(i.period);
    if (i.fn === 'value') base.value = i.value;
    return base;
  });
  const rules = cfg.rules.map((r) => {
    const terms = r.terms.map((t) => ({ op: t.op, left: t.left, right: t.right }));
    const when = terms.length === 1 ? terms[0] : { op: r.combinator, terms };
    const action: Record<string, unknown> = { side: r.side, type: r.type, quote: r.quote };
    if (r.type === 'limit') action.limit_offset_pct = r.limitOffsetPct;
    return { when, do: action, cooldown_minutes: Number(r.cooldownMinutes) };
  });
  return { strategy_type: 'custom_rules', symbol, indicators, rules };
}

// The AI-authored custom_rules config the AI Engine returns (a subset of the
// rule-engine DSL; see packages/shared CustomRulesStrategy).
interface AuthoredCond { op: string; left?: string; right?: string; terms?: AuthoredCond[] }
interface AuthoredStrategy {
  strategy_type: 'custom_rules';
  symbol: string;
  indicators?: { name: string; fn: string; period?: number; value?: string }[];
  rules: {
    when: AuthoredCond;
    do: { side?: string; type?: string; quote?: string; limit_offset_pct?: string };
    cooldown_minutes?: number;
  }[];
}

// Reverse of buildCustomRules: turn an authored config into RuleBuilder state so
// the user can review/edit it. The visual builder only models a flat AND/OR of
// comparisons, so nested boolean logic / NOT is flattened to its leaf
// comparisons and flagged in `warnings` — the user should review those rules.
function authoredToCustomCfg(s: AuthoredStrategy): { cfg: CustomCfg; warnings: string[] } {
  const warnings: string[] = [];
  const indicators: IndicatorRow[] = (s.indicators ?? []).map((i) => ({
    name: i.name,
    fn: (['price', 'value', 'sma', 'rsi'].includes(i.fn) ? i.fn : 'value') as IndFn,
    period: String(i.period ?? 0),
    value: i.value != null ? String(i.value) : '0',
  }));

  const isCmp = (c: AuthoredCond): boolean =>
    COMP_OPS.includes(c.op as CompOp) && c.left != null && c.right != null;
  const leaves = (c: AuthoredCond): TermRow[] => {
    if (isCmp(c)) return [{ left: c.left!, op: c.op as CompOp, right: c.right! }];
    if (Array.isArray(c.terms)) return c.terms.flatMap(leaves);
    return [];
  };

  const rules: RuleRow[] = s.rules.map((r, idx) => {
    const w = r.when;
    let combinator: 'and' | 'or' = 'and';
    let terms: TermRow[];
    if (isCmp(w)) {
      terms = [{ left: w.left!, op: w.op as CompOp, right: w.right! }];
    } else if ((w.op === 'and' || w.op === 'or') && Array.isArray(w.terms)) {
      combinator = w.op;
      if (w.terms.every(isCmp)) {
        terms = w.terms.map((t) => ({ left: t.left!, op: t.op as CompOp, right: t.right! }));
      } else {
        terms = leaves(w);
        warnings.push(`Rule ${idx + 1} used nested logic the editor can't fully show — review it.`);
      }
    } else {
      terms = leaves(w);
      warnings.push(`Rule ${idx + 1} used "${w.op ?? '?'}" logic the editor can't represent — review it.`);
    }
    if (terms.length === 0) {
      terms = [{ left: 'price', op: '>', right: '0' }];
      warnings.push(`Rule ${idx + 1} had no readable condition — added a placeholder.`);
    }
    const d = r.do;
    return {
      combinator,
      terms,
      side: (d.side === 'sell' ? 'sell' : 'buy') as 'buy' | 'sell',
      type: (d.type === 'limit' ? 'limit' : 'market') as 'market' | 'limit',
      quote: String(d.quote ?? '0'),
      limitOffsetPct: d.limit_offset_pct != null ? String(d.limit_offset_pct) : '',
      cooldownMinutes: String(r.cooldown_minutes ?? 0),
    };
  });

  return { cfg: { indicators, rules }, warnings };
}

const subCard: React.CSSProperties = {
  border: '1px solid #eee', borderRadius: 6, padding: 12, marginBottom: 10, background: '#fafafa',
};
const mini: React.CSSProperties = { ...btn, padding: '2px 10px', fontSize: 12 };
const miniGray: React.CSSProperties = { ...mini, background: '#6b7280', border: 0 };
const miniDanger: React.CSSProperties = { ...btnDanger, padding: '2px 8px', fontSize: 12 };

// Visual editor for a custom_rules strategy. Supports a flat AND/OR of
// comparison terms per rule — nested boolean logic is valid in the API but not
// exposed here yet.
function RuleBuilder({
  cfg,
  setCfg,
}: {
  cfg: CustomCfg;
  setCfg: React.Dispatch<React.SetStateAction<CustomCfg>>;
}) {
  const operands = ['price', ...cfg.indicators.map((i) => i.name).filter(Boolean)];

  const setInd = (idx: number, patch: Partial<IndicatorRow>) =>
    setCfg((c) => ({ ...c, indicators: c.indicators.map((it, i) => (i === idx ? { ...it, ...patch } : it)) }));
  const addInd = () =>
    setCfg((c) => ({ ...c, indicators: [...c.indicators, { name: '', fn: 'sma', period: '14', value: '0' }] }));
  const delInd = (idx: number) =>
    setCfg((c) => ({ ...c, indicators: c.indicators.filter((_, i) => i !== idx) }));

  const setRule = (idx: number, patch: Partial<RuleRow>) =>
    setCfg((c) => ({ ...c, rules: c.rules.map((r, i) => (i === idx ? { ...r, ...patch } : r)) }));
  const addRule = () =>
    setCfg((c) => ({
      ...c,
      rules: [...c.rules, {
        combinator: 'and', terms: [{ left: 'price', op: '>', right: '0' }],
        side: 'buy', type: 'market', quote: '100', limitOffsetPct: '', cooldownMinutes: '0',
      }],
    }));
  const delRule = (idx: number) => setCfg((c) => ({ ...c, rules: c.rules.filter((_, i) => i !== idx) }));

  const setTerm = (ri: number, ti: number, patch: Partial<TermRow>) =>
    setRule(ri, { terms: cfg.rules[ri]!.terms.map((t, i) => (i === ti ? { ...t, ...patch } : t)) });
  const addTerm = (ri: number) =>
    setRule(ri, { terms: [...cfg.rules[ri]!.terms, { left: 'price', op: '>', right: '0' }] });
  const delTerm = (ri: number, ti: number) =>
    setRule(ri, { terms: cfg.rules[ri]!.terms.filter((_, i) => i !== ti) });

  return (
    <div style={{ marginTop: 12 }}>
      <datalist id="rb-operands">
        {operands.map((o) => <option key={o} value={o} />)}
      </datalist>

      {/* Indicators */}
      <h4 style={{ margin: '8px 0' }}>Indicators</h4>
      {cfg.indicators.map((ind, i) => (
        <div key={i} style={{ ...subCard, display: 'flex', flexWrap: 'wrap', alignItems: 'center' }}>
          <Field label="name"><input style={{ ...input, width: 90 }} value={ind.name} onChange={(e) => setInd(i, { name: e.target.value })} /></Field>
          <Field label="fn">
            <select style={{ ...input, width: 90 }} value={ind.fn} onChange={(e) => setInd(i, { fn: e.target.value as IndFn })}>
              <option value="sma">SMA</option>
              <option value="rsi">RSI</option>
              <option value="price">price</option>
              <option value="value">value</option>
            </select>
          </Field>
          {(ind.fn === 'sma' || ind.fn === 'rsi') && (
            <Field label="period"><input style={{ ...input, width: 70 }} value={ind.period} onChange={(e) => setInd(i, { period: e.target.value })} /></Field>
          )}
          {ind.fn === 'value' && (
            <Field label="value"><input style={{ ...input, width: 80 }} value={ind.value} onChange={(e) => setInd(i, { value: e.target.value })} /></Field>
          )}
          <button style={{ ...miniDanger, marginBottom: 8 }} onClick={() => delInd(i)}>remove</button>
        </div>
      ))}
      <button style={miniGray} onClick={addInd}>+ indicator</button>

      {/* Rules */}
      <h4 style={{ margin: '16px 0 8px' }}>Rules</h4>
      {cfg.rules.map((rule, ri) => (
        <div key={ri} style={subCard}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
            <strong style={{ fontSize: 13, marginRight: 12 }}>When</strong>
            {rule.terms.length > 1 && (
              <select style={{ ...input, width: 70 }} value={rule.combinator} onChange={(e) => setRule(ri, { combinator: e.target.value as 'and' | 'or' })}>
                <option value="and">all of</option>
                <option value="or">any of</option>
              </select>
            )}
            <span style={{ flex: 1 }} />
            <button style={miniDanger} onClick={() => delRule(ri)}>delete rule</button>
          </div>

          {rule.terms.map((t, ti) => (
            <div key={ti} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              <input list="rb-operands" style={{ ...input, width: 110, marginRight: 0 }} value={t.left} onChange={(e) => setTerm(ri, ti, { left: e.target.value })} placeholder="left" />
              <select style={{ ...input, width: 110, marginRight: 0 }} value={t.op} onChange={(e) => setTerm(ri, ti, { op: e.target.value as CompOp })}>
                {COMP_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
              </select>
              <input list="rb-operands" style={{ ...input, width: 110, marginRight: 0 }} value={t.right} onChange={(e) => setTerm(ri, ti, { right: e.target.value })} placeholder="right" />
              {rule.terms.length > 1 && (
                <button style={miniDanger} onClick={() => delTerm(ri, ti)}>×</button>
              )}
            </div>
          ))}
          <button style={{ ...miniGray, marginBottom: 10 }} onClick={() => addTerm(ri)}>+ condition</button>

          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', borderTop: '1px solid #eee', paddingTop: 8 }}>
            <strong style={{ fontSize: 13, marginRight: 12 }}>Then</strong>
            <Field label="side">
              <select style={{ ...input, width: 80 }} value={rule.side} onChange={(e) => setRule(ri, { side: e.target.value as 'buy' | 'sell' })}>
                <option value="buy">buy</option>
                <option value="sell">sell</option>
              </select>
            </Field>
            <Field label="type">
              <select style={{ ...input, width: 90 }} value={rule.type} onChange={(e) => setRule(ri, { type: e.target.value as 'market' | 'limit' })}>
                <option value="market">market</option>
                <option value="limit">limit</option>
              </select>
            </Field>
            <Field label="quote"><input style={{ ...input, width: 80 }} value={rule.quote} onChange={(e) => setRule(ri, { quote: e.target.value })} /></Field>
            {rule.type === 'limit' && (
              <Field label="limit offset %"><input style={{ ...input, width: 90 }} value={rule.limitOffsetPct} onChange={(e) => setRule(ri, { limitOffsetPct: e.target.value })} /></Field>
            )}
            <Field label="cooldown (min)"><input style={{ ...input, width: 90 }} value={rule.cooldownMinutes} onChange={(e) => setRule(ri, { cooldownMinutes: e.target.value })} /></Field>
          </div>
        </div>
      ))}
      <button style={miniGray} onClick={addRule}>+ rule</button>
    </div>
  );
}

// A labeled form control: tiny caption stacked above its input.
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'inline-flex', flexDirection: 'column', marginRight: 8, marginBottom: 8 }}>
      <span style={{ fontSize: 11, color: '#6b7280', marginBottom: 2 }}>{label}</span>
      {children}
    </label>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ ...card, flex: '1 1 140px', minWidth: 140 }}>
      <div style={{ color: '#6b7280', fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: accent ?? '#111' }}>{value}</div>
    </div>
  );
}
