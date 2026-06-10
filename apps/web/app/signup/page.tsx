'use client';

import { useState } from 'react';

const wrap = { maxWidth: 360, margin: '80px auto', fontFamily: 'system-ui, sans-serif' } as const;
const input = { display: 'block', width: '100%', padding: '8px 10px', margin: '8px 0', border: '1px solid #ccc', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' } as const;
const btn = { width: '100%', padding: '9px 12px', background: '#2563eb', color: '#fff', border: 0, borderRadius: 6, fontSize: 15, cursor: 'pointer' } as const;

export default function Signup() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    const res = await fetch('/v1/auth/signup', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (res.ok) window.location.assign('/dashboard');
    else setErr((await res.json().catch(() => ({}))).error ?? 'signup failed');
  }

  return (
    <main style={wrap}>
      <img src="/images/xbottrader_logo.png" alt="xbottrader" style={{ display: 'block', width: 240, height: 'auto', margin: '0 auto 16px' }} />
      <h1>Sign up</h1>
      <form onSubmit={submit}>
        <input style={input} type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input style={input} type="password" placeholder="password (min 8 chars)" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required />
        <button style={btn} type="submit">Create account</button>
      </form>
      {err && <p style={{ color: '#dc2626' }}>{err === 'email_taken' ? 'That email is already registered.' : err}</p>}
      <p style={{ fontSize: 14 }}>Have an account? <a href="/login">Log in</a></p>
    </main>
  );
}
