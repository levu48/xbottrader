import type { ConnectionRegistry } from './registry.js';

export const STREAM_KEY = 'xbt.events';
export const CONSUMER_GROUP = 'gateway';

export interface StreamEntry {
  id: string;
  fields: Record<string, string>;
}

export interface StreamReader {
  /** Block up to `blockMs` for new entries since the last read by this consumer. */
  readGroup(args: {
    group: string;
    consumer: string;
    stream: string;
    blockMs: number;
    count: number;
  }): Promise<StreamEntry[]>;

  ack(stream: string, group: string, id: string): Promise<void>;

  ensureGroup(stream: string, group: string): Promise<void>;
}

/**
 * Consumes the `xbt.events` Redis Stream as a member of the `gateway` consumer
 * group, routes each event to the WebSocket connections of its `user_id`, and
 * ACKs. Events for users with no live connection are dropped (best-effort live
 * updates; durable history lives in Postgres).
 */
export class EventStreamConsumer {
  #stopped = false;

  constructor(
    private readonly reader: StreamReader,
    private readonly registry: ConnectionRegistry,
    private readonly consumerName: string,
  ) {}

  async start(): Promise<void> {
    await this.reader.ensureGroup(STREAM_KEY, CONSUMER_GROUP);
    while (!this.#stopped) {
      const entries = await this.reader.readGroup({
        group: CONSUMER_GROUP,
        consumer: this.consumerName,
        stream: STREAM_KEY,
        blockMs: 5_000,
        count: 100,
      });
      for (const entry of entries) {
        await this.#handle(entry);
      }
    }
  }

  stop(): void {
    this.#stopped = true;
  }

  /** Exposed for tests so the consumer can be driven without a loop. */
  async processOnce(entry: StreamEntry): Promise<void> {
    await this.#handle(entry);
  }

  async #handle(entry: StreamEntry): Promise<void> {
    const userId = entry.fields.user_id;
    if (userId) {
      this.registry.sendTo(userId, serializeForClient(entry.fields));
    }
    await this.reader.ack(STREAM_KEY, CONSUMER_GROUP, entry.id);
  }
}

function serializeForClient(fields: Record<string, string>): string {
  const payload = fields.payload ? safeParse(fields.payload) : null;
  return JSON.stringify({
    event_type: fields.event_type,
    bot_id: fields.bot_id,
    ts: fields.ts,
    payload,
  });
}

function safeParse(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}
