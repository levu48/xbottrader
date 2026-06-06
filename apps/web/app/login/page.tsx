'use client';

import { useState } from 'react';

const wrap = { maxWidth: 360, margin: '80px auto', fontFamily: 'system-ui, sans-serif' } as const;
const input = { display: 'block', width: '100%', padding: '8px 10px', margin: '8px 0', border: '1px solid #ccc', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' } as const;
const btn = { width: '100%', padding: '9px 12px', background: '#2563eb', color: '#fff', border: 0, borderRadius: 6, fontSize: 15, cursor: 'pointer' } as const;

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [need2fa, setNeed2fa] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    const res = await fetch('/v1/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email, password, ...(code ? { code } : {}) }),
    });
    if (res.ok) {
      const d = await res.json();
      if (d.twoFactorRequired) {
        setNeed2fa(true);
        return;
      }
      window.location.assign('/dashboard');
    } else {
      setErr((await res.json().catch(() => ({}))).error ?? 'login failed');
    }
  }

  return (
    <main style={wrap}>
      <h1>Log in</h1>
      <form onSubmit={submit}>
        <input style={input} type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input style={input} type="password" placeholder="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {need2fa && (
          <input style={input} placeholder="6-digit 2FA code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
        )}
        <button style={btn} type="submit">{need2fa ? 'Verify code' : 'Log in'}</button>
      </form>
      {err && <p style={{ color: '#dc2626' }}>{err}</p>}
      <p style={{ fontSize: 14 }}>No account? <a href="/signup">Sign up</a></p>
    </main>
  );
}
