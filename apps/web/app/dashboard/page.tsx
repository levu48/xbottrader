'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

interface IncomingEvent {
  event_type: string;
  bot_id: string;
  user_id?: string;
  ts: string;
  payload: Record<string, unknown>;
}

type WsStatus = 'connecting' | 'open' | 'closed' | 'error';

// Same-origin: the gateway owns /ws and /v1 behind the app's ingress, so we
// derive the URLs from the browser location rather than build-time env.
function wsUrl(userId: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws?token=${encodeURIComponent(`user:${userId}`)}`;
}

const card: React.CSSProperties = {
  border: '1px solid #e5e5e5',
  borderRadius: 8,
  padding: 16,
  marginBottom: 16,
};
const input: React.CSSProperties = {
  padding: '6px 8px',
  border: '1px solid #ccc',
  borderRadius: 6,
  marginRight: 8,
  fontSize: 14,
};
const btn: React.CSSProperties = {
  padding: '6px 12px',
  border: '1px solid #2563eb',
  background: '#2563eb',
  color: '#fff',
  borderRadius: 6,
  cursor: 'pointer',
  fontSize: 14,
};
const btnDanger: React.CSSProperties = { ...btn, border: '1px solid #dc2626', background: '#dc2626' };

export default function DashboardPage() {
  const [userId, setUserId] = useState('u-staging');
  const [connectedUser, setConnectedUser] = useState('u-staging');
  const [status, setStatus] = useState<WsStatus>('connecting');
  const [events, setEvents] = useState<IncomingEvent[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  // start form
  const [botId, setBotId] = useState('bot-1');
  const [symbol, setSymbol] = useState('BTC/USDT');
  const [quote, setQuote] = useState('50');
  const [interval, setIntervalMin] = useState('1');

  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setStatus('connecting');
    const ws = new WebSocket(wsUrl(connectedUser));
    wsRef.current = ws;
    ws.onopen = () => setStatus('open');
    ws.onerror = () => setStatus('error');
    ws.onclose = () => setStatus('closed');
    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data as string) as IncomingEvent;
        setEvents((prev) => [parsed, ...prev].slice(0, 300));
      } catch {
        /* ignore non-JSON frames */
      }
    };
    return () => ws.close();
  }, [connectedUser]);

  const api = useCallback(
    async (path: string, body?: unknown): Promise<{ ok: boolean; text: string }> => {
      // Only set a JSON content-type when there IS a body — Fastify 400s on an
      // empty body with content-type: application/json (the kill / kill-all calls).
      const headers: Record<string, string> = { 'x-dev-user': userId };
      if (body) headers['content-type'] = 'application/json';
      const res = await fetch(path, {
        method: 'POST',
        headers,
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      return { ok: res.ok, text: await res.text() };
    },
    [userId],
  );

  const startBot = useCallback(async () => {
    setNotice(null);
    const r = await api(`/v1/bots/${encodeURIComponent(botId)}/start`, {
      strategy: {
        strategy_type: 'dca',
        symbol,
        quote_amount: quote,
        interval_minutes: Number(interval),
      },
      mode: 'paper',
    });
    setNotice(r.ok ? `started ${botId}` : `start failed: ${r.text}`);
  }, [api, botId, symbol, quote, interval]);

  const killBot = useCallback(
    async (id: string) => {
      const r = await api(`/v1/bots/${encodeURIComponent(id)}/kill`);
      setNotice(r.ok ? `killed ${id}` : `kill failed: ${r.text}`);
    },
    [api],
  );

  const killAll = useCallback(async () => {
    const r = await api('/v1/bots/kill-all');
    setNotice(r.ok ? `kill-all: ${r.text}` : `kill-all failed: ${r.text}`);
  }, [api]);

  // Derive fills + a simple position/PnL summary from the event stream.
  const { fills, summary } = useMemo(() => {
    const fills = events.filter((e) => e.event_type === 'fill');
    let position = 0;
    let cashOut = 0; // quote spent net of sells
    let fees = 0;
    // oldest → newest for correct running math
    for (const f of [...fills].reverse()) {
      const qty = Number(f.payload.quantity);
      const price = Number(f.payload.price);
      const fee = Number(f.payload.fee ?? 0);
      const side = String(f.payload.side);
      fees += fee;
      if (side === 'buy') {
        position += qty;
        cashOut += qty * price + fee;
      } else {
        position -= qty;
        cashOut -= qty * price - fee;
      }
    }
    const lastPrice = fills.length ? Number(fills[0]!.payload.price) : 0;
    const equity = position * lastPrice - cashOut; // mark-to-last-fill PnL
    return {
      fills,
      summary: { count: fills.length, position, cashOut, fees, lastPrice, equity },
    };
  }, [events]);

  const statusColor = {
    open: '#16a34a',
    connecting: '#ca8a04',
    closed: '#6b7280',
    error: '#dc2626',
  }[status];

  return (
    <main style={{ padding: 32, maxWidth: 1000, fontFamily: 'system-ui, sans-serif' }}>
      <h1 style={{ marginBottom: 4 }}>Dashboard</h1>
      <p style={{ marginTop: 0, color: '#666' }}>
        WebSocket: <strong style={{ color: statusColor }}>{status}</strong> · user{' '}
        <code>{connectedUser}</code>
      </p>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Session</h3>
        <input
          style={input}
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder="user id"
        />
        <button style={btn} onClick={() => setConnectedUser(userId)}>
          Connect as this user
        </button>
        <span style={{ marginLeft: 8, color: '#999', fontSize: 13 }}>
          (staging auth stub — sets the WS token and x-dev-user header)
        </span>
      </div>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Start a paper DCA bot</h3>
        <input style={{ ...input, width: 90 }} value={botId} onChange={(e) => setBotId(e.target.value)} placeholder="bot id" />
        <input style={{ ...input, width: 110 }} value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="symbol" />
        <input style={{ ...input, width: 70 }} value={quote} onChange={(e) => setQuote(e.target.value)} placeholder="quote" />
        <input style={{ ...input, width: 60 }} value={interval} onChange={(e) => setIntervalMin(e.target.value)} placeholder="min" />
        <button style={btn} onClick={startBot}>Start</button>
        <button style={{ ...btnDanger, marginLeft: 8 }} onClick={() => killBot(botId)}>Kill this</button>
        <button style={{ ...btnDanger, marginLeft: 8 }} onClick={killAll}>Kill all</button>
        {notice && <p style={{ marginBottom: 0, color: '#374151', fontSize: 13 }}>{notice}</p>}
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <Stat label="Fills" value={String(summary.count)} />
        <Stat label="Position" value={summary.position.toFixed(6)} />
        <Stat label="Last price" value={summary.lastPrice ? summary.lastPrice.toFixed(2) : '—'} />
        <Stat label="Fees" value={summary.fees.toFixed(4)} />
        <Stat
          label="PnL (mark-to-last)"
          value={summary.equity.toFixed(2)}
          accent={summary.equity >= 0 ? '#16a34a' : '#dc2626'}
        />
      </div>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Fills</h3>
        {fills.length === 0 ? (
          <p style={{ color: '#666' }}>No fills yet — start a bot above.</p>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: '#666' }}>
                <th>time</th>
                <th>bot</th>
                <th>side</th>
                <th>qty</th>
                <th>price</th>
                <th>fee</th>
              </tr>
            </thead>
            <tbody>
              {fills.slice(0, 50).map((f, i) => (
                <tr key={`${f.ts}-${i}`} style={{ borderTop: '1px solid #eee' }}>
                  <td style={{ color: '#888' }}>{f.ts.slice(11, 19)}</td>
                  <td>{f.bot_id}</td>
                  <td style={{ color: f.payload.side === 'buy' ? '#16a34a' : '#dc2626' }}>
                    {String(f.payload.side)}
                  </td>
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
                <span style={{ color: '#999' }}>{e.ts.slice(11, 19)}</span>{' '}
                <strong>{e.event_type}</strong> <span style={{ color: '#777' }}>{e.bot_id}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
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
