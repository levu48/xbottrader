import { boolean, jsonb, pgTable, text, timestamp, uuid } from 'drizzle-orm/pg-core';

// Gateway-owned (Node) schema, per the architecture plan §5. Sessions live in
// Redis (revocable, fast), not here.

export const users = pgTable('users', {
  id: uuid('id').defaultRandom().primaryKey(),
  email: text('email').notNull().unique(),
  passwordHash: text('password_hash').notNull(),
  // TOTP secret is stored once enrolled; null until the user sets up 2FA.
  totpSecret: text('totp_secret'),
  totpEnabled: boolean('totp_enabled').notNull().default(false),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});

export const apiKeysCiphertext = pgTable('api_keys_ciphertext', {
  id: uuid('id').defaultRandom().primaryKey(),
  userId: uuid('user_id')
    .notNull()
    .references(() => users.id, { onDelete: 'cascade' }),
  exchange: text('exchange').notNull(),
  label: text('label'),
  // EnvelopeCipher output (v, dek_iv, dek_ct, data_iv, data_ct). Plaintext keys
  // never touch this table — only the Bot Engine ever decrypts.
  envelope: jsonb('envelope').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});

export const auditLogAuth = pgTable('audit_log_auth', {
  id: uuid('id').defaultRandom().primaryKey(),
  userId: uuid('user_id'),
  action: text('action').notNull(), // signup | login | login_2fa | logout | 2fa_enroll | ...
  ip: text('ip'),
  ts: timestamp('ts', { withTimezone: true }).notNull().defaultNow(),
});

export type User = typeof users.$inferSelect;
export type ApiKeyRow = typeof apiKeysCiphertext.$inferSelect;
