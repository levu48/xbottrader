import { describe, expect, it } from 'vitest';
import { ConnectionRegistry, type Sender } from './registry.js';

const conn = (): Sender & { sent: string[]; open: boolean } => {
  const o = { sent: [] as string[], open: true } as Sender & { sent: string[]; open: boolean };
  o.send = (d: string) => {
    o.sent.push(d);
  };
  o.isOpen = () => o.open;
  return o;
};

describe('ConnectionRegistry', () => {
  it('routes a message to all of one user\'s connections', () => {
    const r = new ConnectionRegistry();
    const a = conn();
    const b = conn();
    r.add('u1', a);
    r.add('u1', b);
    expect(r.sendTo('u1', 'hi')).toBe(2);
    expect(a.sent).toEqual(['hi']);
    expect(b.sent).toEqual(['hi']);
  });

  it('does not deliver to other users', () => {
    const r = new ConnectionRegistry();
    const a = conn();
    const b = conn();
    r.add('u1', a);
    r.add('u2', b);
    expect(r.sendTo('u1', 'hi')).toBe(1);
    expect(b.sent).toEqual([]);
  });

  it('skips closed connections', () => {
    const r = new ConnectionRegistry();
    const a = conn();
    a.open = false;
    r.add('u1', a);
    expect(r.sendTo('u1', 'hi')).toBe(0);
    expect(a.sent).toEqual([]);
  });

  it('cleans up empty user sets on remove', () => {
    const r = new ConnectionRegistry();
    const a = conn();
    r.add('u1', a);
    expect(r.userCount()).toBe(1);
    r.remove('u1', a);
    expect(r.userCount()).toBe(0);
    expect(r.connectionCount('u1')).toBe(0);
  });

  it('returns 0 when sending to an unknown user', () => {
    const r = new ConnectionRegistry();
    expect(r.sendTo('ghost', 'hi')).toBe(0);
  });

  it('tolerates a sender that throws', () => {
    const r = new ConnectionRegistry();
    const bad: Sender = { send: () => { throw new Error('boom'); }, isOpen: () => true };
    const ok = conn();
    r.add('u1', bad);
    r.add('u1', ok);
    expect(r.sendTo('u1', 'hi')).toBe(1);
    expect(ok.sent).toEqual(['hi']);
  });
});
