import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';

/**
 * Apply pending migrations on boot using drizzle-orm's migrator (no drizzle-kit
 * needed at runtime). Idempotent — drizzle records applied migrations in its own
 * table. Uses a dedicated single connection that's closed afterwards.
 *
 * Migrate-on-boot fits the single-instance staging gateway and sidesteps needing
 * a trusted external host to run migrations. For multi-instance prod, move this
 * to a release/predeploy job.
 */
export async function runMigrations(databaseUrl: string): Promise<void> {
  const isLocal = /@(localhost|127\.0\.0\.1)[:/]/.test(databaseUrl);
  const sql = postgres(databaseUrl, { max: 1, ssl: isLocal ? false : 'require' });
  try {
    await migrate(drizzle(sql), { migrationsFolder: 'drizzle' });
  } finally {
    await sql.end();
  }
}
