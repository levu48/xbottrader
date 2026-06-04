import { describe, expect, it } from 'vitest';
import { EventStreamConsumer, type StreamReader } from './consumer.js';
import { ConnectionRegistry, type Sender } from './registry.js';

const stubReader = (): StreamReader & { acked: string[]; groupEnsured: boolean } => {
  const r = {
    acked: [] as string[],
    groupEnsured: false,
  } as StreamReader & { acked: string[]; groupEnsured: boolean };
  r.ensureGroup = async () => {
    r.groupEnsured = true;
  };
  r.readGroup = async () => [];
  r.ack = async (_s: string, _g: string, id: string) => {
    r.acked.push(id);
  };
  return r;
};

const conn = (): Sender & { sent: string[] } => {
  const o = { sent: [] as string[] } as Sender & { sent: string[] };
  o.send = (d: string) => o.sent.push(d);
  o.isOpen = () => true;
  return o;
};

describe('EventStreamConsumer', () => {
  it('routes an event to the user\'s WS and ACKs the entry', async () => {
    const reader = stubReader();
    const registry = new ConnectionRegistry();
    const ws = conn();
    registry.add('u1', ws);

    const consumer = new EventStreamConsumer(reader, registry, 'test');
    await consumer.processOnce({
      id: '1-0',
      fields: {
        event_type: 'fill',
        user_id: 'u1',
        bot_id: 'b1',
        ts: '2026-06-03T00:00:00Z',
        payload: '{"price":"50000"}',
      },
    });

    expect(ws.sent).toHaveLength(1);
    const msg = JSON.parse(ws.sent[0]!);
    expect(msg.event_type).toBe('fill');
    expect(msg.bot_id).toBe('b1');
    expect(msg.payload).toEqual({ price: '50000' });
    expect(reader.acked).toEqual(['1-0']);
  });

  it('ACKs entries for offline users (no live connection)', async () => {
    const reader = stubReader();
    const registry = new ConnectionRegistry();

    const consumer = new EventStreamConsumer(reader, registry, 'test');
    await consumer.processOnce({
      id: '2-0',
      fields: {
        event_type: 'fill',
        user_id: 'offline_user',
        bot_id: 'b1',
        ts: '2026-06-03T00:00:00Z',
        payload: '{}',
      },
    });

    expect(reader.acked).toEqual(['2-0']);
  });

  it('handles malformed payload JSON without throwing', async () => {
    const reader = stubReader();
    const registry = new ConnectionRegistry();
    const ws = conn();
    registry.add('u1', ws);

    const consumer = new EventStreamConsumer(reader, registry, 'test');
    await consumer.processOnce({
      id: '3-0',
      fields: {
        event_type: 'log_line',
        user_id: 'u1',
        bot_id: 'b1',
        ts: '2026-06-03T00:00:00Z',
        payload: 'not json',
      },
    });

    expect(ws.sent).toHaveLength(1);
    expect(JSON.parse(ws.sent[0]!).payload).toBe('not json');
    expect(reader.acked).toEqual(['3-0']);
  });
});
