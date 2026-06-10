'use client';

import { useEffect, useState } from 'react';

const navBar: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: '10px 32px', borderBottom: '1px solid #e5e5e5', background: '#fff',
  position: 'sticky', top: 0, zIndex: 10,
};
const navBtn: React.CSSProperties = {
  padding: '6px 12px', background: '#6b7280', color: '#fff', border: 0,
  borderRadius: 6, cursor: 'pointer', fontSize: 14,
};

// Shared top navigation. Self-contained: fetches the current user + billing
// status so each page can drop it in with just `current`. `current` bolds the
// matching link to indicate the active page.
export default function NavBar({ current }: { current?: 'billing' | 'settings' }) {
  const [email, setEmail] = useState<string | null>(null);
  const [billingActive, setBillingActive] = useState(false);

  useEffect(() => {
    fetch('/v1/auth/me')
      .then((r) => (r.ok ? r.json() : null))
      .then((m: { email?: string } | null) => m?.email && setEmail(m.email))
      .catch(() => {});
    fetch('/v1/billing/status')
      .then((r) => (r.ok ? r.json() : { active: false }))
      .then((b: { active?: boolean }) => setBillingActive(Boolean(b.active)))
      .catch(() => {});
  }, []);

  async function logout() {
    await fetch('/v1/auth/logout', { method: 'POST' });
    window.location.assign('/login');
  }

  const linkStyle = (active: boolean): React.CSSProperties => ({
    fontWeight: active ? 700 : 400,
    color: active ? '#111' : undefined,
  });

  return (
    <nav style={navBar}>
      <a href="/dashboard" style={{ display: 'flex', alignItems: 'center', textDecoration: 'none' }}>
        <img src="/images/xbottrader_logo.png" alt="xbottrader" style={{ height: 32, width: 'auto', display: 'block' }} />
      </a>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 14, color: '#666' }}>
        {email && (
          <span>
            {email}
            {billingActive && <span style={{ color: '#16a34a' }}> · Pro</span>}
          </span>
        )}
        <a href="/billing" style={linkStyle(current === 'billing')}>Billing</a>
        <a href="/settings" style={linkStyle(current === 'settings')}>Settings</a>
        <button onClick={logout} style={navBtn}>Log out</button>
      </div>
    </nav>
  );
}
