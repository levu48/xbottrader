export interface Sender {
  send(data: string): void;
  isOpen(): boolean;
}

/**
 * Tracks which WebSocket connections belong to which user.
 * One user can hold multiple connections (multi-device, multiple tabs).
 */
export class ConnectionRegistry {
  readonly #byUser = new Map<string, Set<Sender>>();

  add(userId: string, conn: Sender): void {
    let set = this.#byUser.get(userId);
    if (!set) {
      set = new Set();
      this.#byUser.set(userId, set);
    }
    set.add(conn);
  }

  remove(userId: string, conn: Sender): void {
    const set = this.#byUser.get(userId);
    if (!set) return;
    set.delete(conn);
    if (set.size === 0) this.#byUser.delete(userId);
  }

  /** Returns the number of recipients that were sent the message. */
  sendTo(userId: string, data: string): number {
    const set = this.#byUser.get(userId);
    if (!set) return 0;
    let sent = 0;
    for (const conn of set) {
      if (!conn.isOpen()) continue;
      try {
        conn.send(data);
        sent++;
      } catch {
        // best-effort; the consumer/server is responsible for tracking dropped conns
      }
    }
    return sent;
  }

  connectionCount(userId: string): number {
    return this.#byUser.get(userId)?.size ?? 0;
  }

  userCount(): number {
    return this.#byUser.size;
  }
}
