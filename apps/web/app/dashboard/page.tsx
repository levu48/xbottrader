'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

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
type StrategyType = 'dca' | 'grid' | 'ma_crossover';

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
      case 'dca':
      default:
        return { strategy_type: 'dca', symbol, quote_amount: quote, interval_minutes: Number(interval) };
    }
  }, [strategyType, symbol, quote, interval, lowerPrice, upperPrice, gridLevels, totalQuote, fastPeriod, slowPeriod, positionQuote]);

  const startBot = useCallback(async () => {
    setNotice(null);
    const r = await api(`/v1/bots/${encodeURIComponent(botId)}/start`, {
      strategy: buildStrategy(),
      mode: 'paper',
    });
    setNotice(r.ok ? `started ${botId} (${strategyType})` : `start failed: ${r.text}`);
  }, [api, botId, strategyType, buildStrategy]);

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
          <Field label="strategy">
            <select style={{ ...input, width: 140 }} value={strategyType} onChange={(e) => setStrategyType(e.target.value as StrategyType)}>
              <option value="dca">DCA</option>
              <option value="grid">Grid</option>
              <option value="ma_crossover">MA crossover</option>
            </select>
          </Field>
          <Field label="symbol"><input style={{ ...input, width: 110 }} value={symbol} onChange={(e) => setSymbol(e.target.value)} /></Field>

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
        </div>
        <div style={{ marginTop: 12 }}>
          <button style={btn} onClick={startBot}>Start</button>
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
