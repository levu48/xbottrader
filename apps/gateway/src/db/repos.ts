import { and, eq } from 'drizzle-orm';
import type { EncryptedEnvelope } from '../security/keys.js';
import type { Db } from './client.js';
import { apiKeysCiphertext, auditLogAuth, subscriptions, users } from './schema.js';

export interface UserRecord {
  id: string;
  email: string;
  passwordHash: string;
  totpSecret: string | null;
  totpEnabled: boolean;
}

export interface UserStore {
  findByEmail(email: string): Promise<UserRecord | null>;
  findById(id: string): Promise<UserRecord | null>;
  create(email: string, passwordHash: string): Promise<UserRecord>;
  setTotp(userId: string, secret: string, enabled: boolean): Promise<void>;
}

export interface KeyMeta {
  id: string;
  exchange: string;
  label: string | null;
  createdAt: string;
}

export interface KeyStore {
  add(userId: string, exchange: string, label: string | null, envelope: EncryptedEnvelope): Promise<KeyMeta>;
  listMeta(userId: string): Promise<KeyMeta[]>;
  getEnvelope(userId: string, exchange: string): Promise<EncryptedEnvelope | null>;
  remove(userId: string, id: string): Promise<void>;
}

export interface AuditLog {
  record(action: string, userId: string | null, ip: string | null): Promise<void>;
}

function toUser(r: typeof users.$inferSelect): UserRecord {
  return {
    id: r.id,
    email: r.email,
    passwordHash: r.passwordHash,
    totpSecret: r.totpSecret,
    totpEnabled: r.totpEnabled,
  };
}

export class DrizzleUserStore implements UserStore {
  constructor(private readonly db: Db) {}

  async findByEmail(email: string): Promise<UserRecord | null> {
    const r = await this.db.select().from(users).where(eq(users.email, email)).limit(1);
    return r[0] ? toUser(r[0]) : null;
  }

  async findById(id: string): Promise<UserRecord | null> {
    const r = await this.db.select().from(users).where(eq(users.id, id)).limit(1);
    return r[0] ? toUser(r[0]) : null;
  }

  async create(email: string, passwordHash: string): Promise<UserRecord> {
    const r = await this.db.insert(users).values({ email, passwordHash }).returning();
    return toUser(r[0]!);
  }

  async setTotp(userId: string, secret: string, enabled: boolean): Promise<void> {
    await this.db
      .update(users)
      .set({ totpSecret: secret, totpEnabled: enabled })
      .where(eq(users.id, userId));
  }
}

export class DrizzleKeyStore implements KeyStore {
  constructor(private readonly db: Db) {}

  async add(
    userId: string,
    exchange: string,
    label: string | null,
    envelope: EncryptedEnvelope,
  ): Promise<KeyMeta> {
    const r = await this.db
      .insert(apiKeysCiphertext)
      .values({ userId, exchange, label, envelope })
      .returning();
    const row = r[0]!;
    return { id: row.id, exchange: row.exchange, label: row.label, createdAt: row.createdAt.toISOString() };
  }

  async listMeta(userId: string): Promise<KeyMeta[]> {
    const rows = await this.db
      .select()
      .from(apiKeysCiphertext)
      .where(eq(apiKeysCiphertext.userId, userId));
    return rows.map((row) => ({
      id: row.id,
      exchange: row.exchange,
      label: row.label,
      createdAt: row.createdAt.toISOString(),
    }));
  }

  async getEnvelope(userId: string, exchange: string): Promise<EncryptedEnvelope | null> {
    const r = await this.db
      .select()
      .from(apiKeysCiphertext)
      .where(and(eq(apiKeysCiphertext.userId, userId), eq(apiKeysCiphertext.exchange, exchange)))
      .limit(1);
    return r[0] ? (r[0].envelope as EncryptedEnvelope) : null;
  }

  async remove(userId: string, id: string): Promise<void> {
    await this.db
      .delete(apiKeysCiphertext)
      .where(and(eq(apiKeysCiphertext.userId, userId), eq(apiKeysCiphertext.id, id)));
  }
}

export class DrizzleAuditLog implements AuditLog {
  constructor(private readonly db: Db) {}

  async record(action: string, userId: string | null, ip: string | null): Promise<void> {
    await this.db.insert(auditLogAuth).values({ action, userId, ip });
  }
}

// Stripe statuses that grant entitlement. trialing included so a trial unlocks
// features; past_due/canceled/etc. do not.
const ACTIVE_STATUSES = new Set(['active', 'trialing']);

export interface SubscriptionRecord {
  userId: string;
  stripeCustomerId: string;
  stripeSubscriptionId: string | null;
  status: string | null;
  priceId: string | null;
  currentPeriodEnd: string | null;
  cancelAtPeriodEnd: boolean;
}

// Fields synced from a Stripe subscription/checkout event.
export interface SubscriptionUpdate {
  stripeSubscriptionId?: string | null;
  status?: string | null;
  priceId?: string | null;
  currentPeriodEnd?: Date | null;
  cancelAtPeriodEnd?: boolean;
}

export interface SubscriptionStore {
  getByUser(userId: string): Promise<SubscriptionRecord | null>;
  getByCustomerId(customerId: string): Promise<SubscriptionRecord | null>;
  // Create the row on first checkout (links user ↔ Stripe customer).
  ensureCustomer(userId: string, stripeCustomerId: string): Promise<void>;
  // Apply a Stripe-sourced update, keyed by the Stripe customer id (the only id
  // present on every webhook event).
  upsertFromStripe(stripeCustomerId: string, update: SubscriptionUpdate): Promise<void>;
  isActive(userId: string): Promise<boolean>;
}

function toSub(r: typeof subscriptions.$inferSelect): SubscriptionRecord {
  return {
    userId: r.userId,
    stripeCustomerId: r.stripeCustomerId,
    stripeSubscriptionId: r.stripeSubscriptionId,
    status: r.status,
    priceId: r.priceId,
    currentPeriodEnd: r.currentPeriodEnd ? r.currentPeriodEnd.toISOString() : null,
    cancelAtPeriodEnd: r.cancelAtPeriodEnd,
  };
}

export class DrizzleSubscriptionStore implements SubscriptionStore {
  constructor(private readonly db: Db) {}

  async getByUser(userId: string): Promise<SubscriptionRecord | null> {
    const r = await this.db
      .select()
      .from(subscriptions)
      .where(eq(subscriptions.userId, userId))
      .limit(1);
    return r[0] ? toSub(r[0]) : null;
  }

  async getByCustomerId(customerId: string): Promise<SubscriptionRecord | null> {
    const r = await this.db
      .select()
      .from(subscriptions)
      .where(eq(subscriptions.stripeCustomerId, customerId))
      .limit(1);
    return r[0] ? toSub(r[0]) : null;
  }

  async ensureCustomer(userId: string, stripeCustomerId: string): Promise<void> {
    await this.db
      .insert(subscriptions)
      .values({ userId, stripeCustomerId })
      .onConflictDoNothing({ target: subscriptions.userId });
  }

  async upsertFromStripe(stripeCustomerId: string, update: SubscriptionUpdate): Promise<void> {
    await this.db
      .update(subscriptions)
      .set({ ...update, updatedAt: new Date() })
      .where(eq(subscriptions.stripeCustomerId, stripeCustomerId));
  }

  async isActive(userId: string): Promise<boolean> {
    const r = await this.db
      .select({ status: subscriptions.status })
      .from(subscriptions)
      .where(eq(subscriptions.userId, userId))
      .limit(1);
    return r[0]?.status != null && ACTIVE_STATUSES.has(r[0].status);
  }
}
