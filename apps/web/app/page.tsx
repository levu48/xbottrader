import Link from 'next/link';

export default function Home() {
  return (
    <main style={{ padding: 32 }}>
      <h1>xbottrader</h1>
      <p>AI bot trading platform.</p>
      <Link href="/dashboard">Open dashboard →</Link>
    </main>
  );
}
