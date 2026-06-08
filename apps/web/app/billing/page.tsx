'use client';

import { useCallback, useEffect, useState } from 'react';

interface BillingStatus {
  active: boolean;
  status: string | null;
  currentPeriodEnd: string | null;
  cancelAtPeriodEnd: boolean;
}

const card = { border: '1px solid #e5e5e5', borderRadius: 8, padding: 16, marginBottom: 16 } as const;
const btn = { padding: '8px 16px', background: '#2563eb', color: '#fff', border: 0, borderRadius: 6, cursor: 'pointer', fontSize: 14 } as const;

export default function Billing() {
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await fetch('/v1/billing/status');
    if (r.status === 401) {
      window.location.assign('/login');
      return;
    }
    if (r.ok) setStatus(await r.json());
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Surface the post-checkout redirect (?sub=success|cancel) once on load.
  useEffect(() => {
    const sub = new URLSearchParams(window.location.search).get('sub');
    if (sub === 'success') setNotice('Subscription active — thanks! It may take a moment to sync.');
    else if (sub === 'cancel') setNotice('Checkout canceled.');
  }, []);

  // POST to a billing endpoint that returns { url } and redirect to Stripe.
  async function go(path: string) {
    setBusy(true);
    try {
      const r = await fetch(path, { method: 'POST' });
      if (!r.ok) {
        setNotice('Could not start billing session. Try again.');
        return;
      }
      const { url } = (await r.json()) as { url: string };
      window.location.assign(url);
    } finally {
      setBusy(false);
    }
  }

  if (!status) return <main style={{ padding: 32 }}>Loading…</main>;

  return (
    <main style={{ padding: 32, maxWidth: 640, fontFamily: 'system-ui, sans-serif' }}>
      <p style={{ color: '#666' }}>
        <a href="/dashboard">← Dashboard</a> · <a href="/settings">Settings</a>
      </p>
      <h1>Billing</h1>
      {notice && <p style={{ color: '#374151' }}>{notice}</p>}

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>
          Plan{' '}
          {status.active ? (
            <span style={{ color: '#16a34a' }}>· active</span>
          ) : (
            <span style={{ color: '#999' }}>· free</span>
          )}
        </h3>
        {status.active ? (
          <>
            <p style={{ color: '#666' }}>
              Your subscription is {status.status}
              {status.cancelAtPeriodEnd && status.currentPeriodEnd
                ? ` and will end on ${new Date(status.currentPeriodEnd).toLocaleDateString()}`
                : status.currentPeriodEnd
                  ? ` and renews on ${new Date(status.currentPeriodEnd).toLocaleDateString()}`
                  : ''}
              .
            </p>
            <button style={btn} disabled={busy} onClick={() => go('/v1/billing/portal')}>
              Manage billing
            </button>
          </>
        ) : (
          <>
            <p style={{ color: '#666' }}>
              Paper trading and backtests are free. Subscribe to unlock <strong>live trading</strong>{' '}
              and the <strong>AI features</strong> (copilot, strategy author, AI-signal bots).
            </p>
            <button style={btn} disabled={busy} onClick={() => go('/v1/billing/checkout')}>
              {busy ? 'Redirecting…' : 'Subscribe'}
            </button>
          </>
        )}
      </div>
    </main>
  );
}
