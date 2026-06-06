import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import * as schema from './schema.js';

export type Db = ReturnType<typeof createDb>;

/**
 * Drizzle client over postgres.js. DO Managed Postgres requires TLS; we enable
 * it for any non-local host (`ssl: 'require'` — encrypted, no strict CA check,
 * which is what the managed connection string expects).
 */
export function createDb(databaseUrl: string): ReturnType<typeof drizzle<typeof schema>> {
  const isLocal = /@(localhost|127\.0\.0\.1)[:/]/.test(databaseUrl);
  const sql = postgres(databaseUrl, { max: 5, ssl: isLocal ? false : 'require' });
  return drizzle(sql, { schema });
}
