'use client';

import { useCallback, useEffect, useState } from 'react';

interface Me {
  id: string;
  email: string;
  totpEnabled: boolean;
}
interface KeyMeta {
  id: string;
  exchange: string;
  label: string | null;
  createdAt: string;
}

const card = { border: '1px solid #e5e5e5', borderRadius: 8, padding: 16, marginBottom: 16 } as const;
const input = { padding: '6px 8px', border: '1px solid #ccc', borderRadius: 6, marginRight: 8, marginBottom: 8, fontSize: 14 } as const;
const btn = { padding: '6px 12px', background: '#2563eb', color: '#fff', border: 0, borderRadius: 6, cursor: 'pointer', fontSize: 14 } as const;

export default function Settings() {
  const [me, setMe] = useState<Me | null>(null);
  const [keys, setKeys] = useState<KeyMeta[]>([]);
  const [enroll, setEnroll] = useState<{ secret: string; otpauthUrl: string } | null>(null);
  const [code, setCode] = useState('');
  const [form, setForm] = useState({ exchange: 'binance', apiKey: '', secret: '', passphrase: '', label: '' });
  const [notice, setNotice] = useState<string | null>(null);

  const loadKeys = useCallback(async () => {
    const r = await fetch('/v1/keys');
    if (r.ok) setKeys((await r.json()).keys ?? []);
  }, []);

  useEffect(() => {
    fetch('/v1/auth/me').then(async (r) => {
      if (!r.ok) {
        window.location.assign('/login');
        return;
      }
      setMe(await r.json());
      loadKeys();
    });
  }, [loadKeys]);

  async function startEnroll() {
    const r = await fetch('/v1/auth/2fa/enroll', { method: 'POST' });
    if (r.ok) setEnroll(await r.json());
  }
  async function activate() {
    const r = await fetch('/v1/auth/2fa/activate', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (r.ok) {
      setMe((m) => (m ? { ...m, totpEnabled: true } : m));
      setEnroll(null);
      setNotice('2FA enabled');
    } else setNotice('invalid code');
  }
  async function addKey(e: React.FormEvent) {
    e.preventDefault();
    const r = await fetch('/v1/keys', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(form),
    });
    setNotice(r.ok ? 'key stored' : 'failed to store key');
    if (r.ok) {
      setForm({ exchange: 'binance', apiKey: '', secret: '', passphrase: '', label: '' });
      loadKeys();
    }
  }
  async function delKey(id: string) {
    await fetch(`/v1/keys/${id}`, { method: 'DELETE' });
    loadKeys();
  }

  if (!me) return <main style={{ padding: 32 }}>Loading…</main>;

  return (
    <main style={{ padding: 32, maxWidth: 720, fontFamily: 'system-ui, sans-serif' }}>
      <p style={{ color: '#666' }}>
        <a href="/dashboard">← Dashboard</a> · {me.email}
      </p>
      <h1>Settings</h1>
      {notice && <p style={{ color: '#374151' }}>{notice}</p>}

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Two-factor auth {me.totpEnabled && <span style={{ color: '#16a34a' }}>· enabled</span>}</h3>
        {me.totpEnabled ? (
          <p style={{ color: '#666' }}>2FA is on. Required to start live bots.</p>
        ) : enroll ? (
          <div>
            <p style={{ fontSize: 13 }}>Add this secret to your authenticator app, then enter a code:</p>
            <code style={{ display: 'block', wordBreak: 'break-all', background: '#f6f6f6', padding: 8, borderRadius: 6 }}>{enroll.secret}</code>
            <input style={{ ...input, marginTop: 8 }} placeholder="6-digit code" value={code} onChange={(e) => setCode(e.target.value)} />
            <button style={btn} onClick={activate}>Activate</button>
          </div>
        ) : (
          <button style={btn} onClick={startEnroll}>Enroll 2FA</button>
        )}
      </div>

      <div style={card}>
        <h3 style={{ marginTop: 0 }}>Exchange API keys</h3>
        <p style={{ color: '#999', fontSize: 13, marginTop: 0 }}>
          Encrypted before storage; only the Bot Engine ever decrypts. Needed for live trading.
          {form.exchange === 'alpaca' && (
            <> Alpaca uses an <strong>API key ID + secret</strong> (no passphrase) and issues separate
            keys for paper vs live. For <strong>live</strong> bots, store your <strong>live</strong> keys
            (paper keys won&apos;t authenticate against the live endpoint).</>
          )}
        </p>
        <form onSubmit={addKey}>
          <select style={input} value={form.exchange} onChange={(e) => setForm({ ...form, exchange: e.target.value })}>
            <option value="binance">Binance (crypto)</option>
            <option value="coinbase">Coinbase (crypto)</option>
            <option value="alpaca">Alpaca (US stocks)</option>
          </select>
          <input style={input} placeholder="label (optional)" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
          <br />
          <input style={{ ...input, width: 220 }} placeholder={form.exchange === 'alpaca' ? 'API key ID' : 'API key'} value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} />
          <input style={{ ...input, width: 220 }} type="password" placeholder="API secret" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
          {form.exchange !== 'alpaca' && (
            <input style={input} placeholder="passphrase (optional)" value={form.passphrase} onChange={(e) => setForm({ ...form, passphrase: e.target.value })} />
          )}
          <br />
          <button style={btn} type="submit">Store key</button>
        </form>
        <ul style={{ listStyle: 'none', padding: 0, marginTop: 12 }}>
          {keys.map((k) => (
            <li key={k.id} style={{ padding: '6px 0', borderTop: '1px solid #eee', display: 'flex', justifyContent: 'space-between' }}>
              <span>{k.exchange}{k.label ? ` · ${k.label}` : ''}</span>
              <button style={{ ...btn, background: '#dc2626' }} onClick={() => delKey(k.id)}>Delete</button>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
