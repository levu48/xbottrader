import type { Redis } from 'ioredis';
import type { StreamEntry, StreamReader } from './consumer.js';

type RawEntry = [string, string[]];
type RawStream = [string, RawEntry[]];

export class IoredisStreamReader implements StreamReader {
  constructor(private readonly client: Redis) {}

  async ensureGroup(stream: string, group: string): Promise<void> {
    try {
      await this.client.xgroup('CREATE', stream, group, '$', 'MKSTREAM');
    } catch (e: unknown) {
      if (e instanceof Error && e.message.includes('BUSYGROUP')) return;
      throw e;
    }
  }

  async readGroup(args: {
    group: string;
    consumer: string;
    stream: string;
    blockMs: number;
    count: number;
  }): Promise<StreamEntry[]> {
    const raw = (await this.client.xreadgroup(
      'GROUP',
      args.group,
      args.consumer,
      'COUNT',
      args.count,
      'BLOCK',
      args.blockMs,
      'STREAMS',
      args.stream,
      '>',
    )) as RawStream[] | null;

    if (!raw) return [];
    const out: StreamEntry[] = [];
    for (const [, entries] of raw) {
      for (const [id, kv] of entries) {
        const fields: Record<string, string> = {};
        for (let i = 0; i < kv.length; i += 2) {
          const k = kv[i];
          const v = kv[i + 1];
          if (k !== undefined && v !== undefined) fields[k] = v;
        }
        out.push({ id, fields });
      }
    }
    return out;
  }

  async ack(stream: string, group: string, id: string): Promise<void> {
    await this.client.xack(stream, group, id);
  }
}
