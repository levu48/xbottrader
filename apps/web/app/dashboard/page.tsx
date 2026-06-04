'use client';

import { useEffect, useRef, useState } from 'react';

interface IncomingEvent {
  event_type: string;
  bot_id: string;
  ts: string;
  payload: unknown;
}

export default function DashboardPage() {
  const [events, setEvents] = useState<IncomingEvent[]>([]);
  const [status, setStatus] = useState<'connecting' | 'open' | 'closed' | 'error'>('connecting');
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const gatewayUrl = process.env.NEXT_PUBLIC_GATEWAY_WS ?? 'ws://localhost:4000/ws';
    const token = process.env.NEXT_PUBLIC_DEV_TOKEN ?? 'user:dev';
    const ws = new WebSocket(`${gatewayUrl}?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;

    ws.onopen = () => setStatus('open');
    ws.onerror = () => setStatus('error');
    ws.onclose = () => setStatus('closed');
    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data as string) as IncomingEvent;
        setEvents((prev) => [parsed, ...prev].slice(0, 200));
      } catch {
        /* ignore */
      }
    };

    return () => ws.close();
  }, []);

  return (
    <main style={{ padding: 32, maxWidth: 900 }}>
      <h1>Dashboard</h1>
      <p>
        WebSocket: <strong>{status}</strong>
      </p>
      <h2>Live events</h2>
      {events.length === 0 ? (
        <p style={{ color: '#666' }}>Waiting for events…</p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0, fontFamily: 'monospace', fontSize: 13 }}>
          {events.map((e, i) => (
            <li
              key={`${e.ts}-${i}`}
              style={{ padding: '8px 12px', borderBottom: '1px solid #eee' }}
            >
              <span style={{ color: '#888' }}>{e.ts}</span>{' '}
              <strong>{e.event_type}</strong>{' '}
              <span style={{ color: '#555' }}>bot={e.bot_id}</span>
              <pre style={{ margin: '4px 0 0', color: '#444' }}>
                {JSON.stringify(e.payload, null, 2)}
              </pre>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
