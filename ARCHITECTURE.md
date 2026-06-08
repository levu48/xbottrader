# xbottrader — Architecture Reference

A complete, study-oriented tour of the xbottrader system: what each piece does, how
the pieces talk, and *why* the boundaries fall where they do. Written against the
code as it stands (commit on `main`). File references are clickable.

> **One-sentence summary:** A TypeScript edge owns identity, key custody, and
> real-time fan-out; a Python core owns trading and AI; they're bound by HMAC-signed
> internal calls, a shared strategy library that guarantees backtest/live parity, and
> a Redis event stream — with the trading engine physically isolated onto a static IP
> because exchange API keys are IP-allowlisted.

---

## Table of contents

1. [What the product is](#1-what-the-product-is)
2. [The monorepo layout](#2-the-monorepo-layout)
3. [The four services + two shared packages](#3-the-four-services--two-shared-packages)
4. [End-to-end request & data flows](#4-end-to-end-request--data-flows)
5. [Security model (the keystone)](#5-security-model-the-keystone)
6. [The Gateway in depth](#6-the-gateway-in-depth)
7. [The Bot Engine in depth](#7-the-bot-engine-in-depth)
8. [The AI Engine in depth](#8-the-ai-engine-in-depth)
9. [The strategy domain & backtest/live parity](#9-the-strategy-domain--backtestlive-parity)
10. [Exchange adapters](#10-exchange-adapters)
11. [Shared contracts (TS ⇄ Python)](#11-shared-contracts-ts--python)
12. [Persistence & data ownership](#12-persistence--data-ownership)
13. [The real-time event pipeline](#13-the-real-time-event-pipeline)
14. [The web frontend](#14-the-web-frontend)
15. [Deployment topology & infra](#15-deployment-topology--infra)
16. [CI](#16-ci)
17. [Configuration / environment variables](#17-configuration--environment-variables)
18. [Glossary of invariants & design rules](#18-glossary-of-invariants--design-rules)
19. [Known gaps / deferred work](#19-known-gaps--deferred-work)

---

## 1. What the product is

A **multi-tenant SaaS for AI-assisted automated crypto trading**. The user journey:

1. Sign up / log in (with optional TOTP 2FA).
2. Store exchange API keys (encrypted; required for live trading).
3. Build a trading bot — pick a strategy (DCA, grid, MA-crossover) or compose custom
   rules in a visual builder; optionally ask the AI copilot for help.
4. Backtest the strategy over historical candles.
5. Run it — in **paper** mode (simulated fills) or **live** mode (real orders).
6. Watch fills, PnL, and lifecycle events stream into a dashboard in real time.
7. Hit a kill switch any time.

It's a **polyglot monorepo**: TypeScript handles the user-facing edge (web + auth +
API gateway), Python handles the trading domain (bot execution + AI/backtesting).

---

## 2. The monorepo layout

```
xbottrader/
├── apps/
│   ├── web/            Next.js (React) frontend — dashboard, login, settings
│   ├── gateway/        Node/Fastify — auth, key custody, BFF, WS fan-out
│   ├── bot-engine/     Python/FastAPI — runs bots, places orders, emits events
│   └── ai-engine/      Python/FastAPI — copilot (Anthropic) + backtester
├── packages/
│   ├── shared/         TS/Zod — event, order, strategy contracts (TS side)
│   └── xbt-core/       Python — strategy domain + paper fills + internal auth
├── infra/
│   ├── do/             Droplet cloud-init + docker-compose for Bot Engine
│   ├── scripts/        provision / deploy scripts (doctl)
│   ├── README.md       deploy runbook
│   └── STAGING-CHECKLIST.md
├── .do/app.yaml        DigitalOcean App Platform spec (web + gateway + ai-engine)
├── .github/workflows/ci.yml
├── pnpm-workspace.yaml  (apps/web, apps/gateway, packages/*)
└── package.json         pnpm@9, Node >= 22
```

**Workspace tooling.**
- JS/TS: pnpm workspaces ([pnpm-workspace.yaml](pnpm-workspace.yaml)); root scripts
  `build`/`test`/`lint`/`typecheck` run recursively ([package.json](package.json)).
- Python: each app has its own venv; both depend on the local editable
  `packages/xbt-core` (installed via a `.pth` editable shim).
- Note: the pnpm workspace lists `apps/web` and `apps/gateway` but **not** the Python
  apps — those are managed by Python tooling (uv/pip + pyproject).

---

## 3. The four services + two shared packages

```
                          ┌──────────────────────────────────────────────┐
   Browser  ──────────────┤  Web (Next.js)  apps/web                      │
   (xbottrader.ai)        │  dashboard / login / signup / settings        │
                          └───────────────┬──────────────────────────────┘
                                          │ same-origin: /v1 (REST), /ws (WebSocket)
                          ┌───────────────▼──────────────────────────────┐
                          │  Gateway (Node/Fastify)  apps/gateway         │
                          │  • sessions + 2FA (the ONLY user-auth surface)│
                          │  • envelope-encrypts API keys                 │
                          │  • BFF: signs internal calls with HMAC        │
                          │  • consumes Redis Stream → fans out to WS     │
                          └───┬───────────────────────────┬──────────────┘
                  HMAC-signed │                           │ HMAC-signed
                              ▼                           ▼
        ┌─────────────────────────────┐    ┌──────────────────────────────┐
        │ Bot Engine (Py/FastAPI)     │    │ AI Engine (Py/FastAPI)        │
        │ apps/bot-engine             │    │ apps/ai-engine                │
        │ • Supervisor runs N bots    │    │ • Copilot (Anthropic)         │
        │   as asyncio tasks          │    │ • Backtester                  │
        │ • decrypts keys, trades     │    │ • NEVER sees keys / trades    │
        │ • emits events → Redis      │    └──────────────────────────────┘
        └──────┬───────────────┬──────┘
               │               │ xadd "xbt.events"
               ▼               ▼
          Exchanges        Redis Streams ───────► (consumed by Gateway)
          (ccxt)
               
   Shared contracts: packages/shared (TS/Zod)  ·  packages/xbt-core (Python)
   Persistence: Postgres (per-service-owned tables)  ·  Redis (sessions + event bus)
```

| Service | Language / framework | Responsibility | Sees plaintext keys? | Public? |
|---|---|---|---|---|
| **web** | Next.js / React | Presentation only | No | Yes (`/`) |
| **gateway** | Node 22 / Fastify | Auth, key encryption, reverse-proxy/BFF, WS fan-out | Only at *encrypt* time (input from user) | Yes (`/v1`, `/ws`, `/healthz`) |
| **bot-engine** | Python 3.12 / FastAPI | Bot lifecycle, order routing, risk, persistence, event emit | **Yes** (decrypts to trade) | No (private, Droplet:5001) |
| **ai-engine** | Python 3.12 / FastAPI | Copilot chat + backtests | No | No (internal VPC only) |

Two shared packages keep the polyglot services in lockstep:
- **[packages/shared](packages/shared/)** (TS/Zod) — the event/order/strategy schemas
  for the Node + browser side.
- **[packages/xbt-core](packages/xbt-core/)** (Python) — the *strategy domain*, the
  paper fill model, and the HMAC internal-auth verifier; imported by both Python apps.

---

## 4. End-to-end request & data flows

### 4a. Auth (signup / login / 2FA)
1. Browser POSTs `/v1/auth/signup` or `/v1/auth/login` to the Gateway.
2. Gateway validates with Zod, hashes/verifies the password (argon2id), and on success
   creates a **Redis session**, returns it as the `xbt_session` httpOnly cookie.
3. If the user has TOTP enabled, login returns `{ twoFactorRequired: true }` until a
   valid `code` is supplied.
4. Every protected route runs the `requireUser` preHandler, which resolves the cookie
   → Redis session → `req.userId`.

See [§6](#6-the-gateway-in-depth).

### 4b. Storing an exchange API key
1. Browser POSTs `/v1/keys` `{ exchange, apiKey, secret, passphrase?, label? }`.
2. Gateway packs `{apiKey, secret, password?}` into JSON, **envelope-encrypts** it
   ([security/keys.ts](apps/gateway/src/security/keys.ts)), and stores only the
   ciphertext envelope in Postgres ([keys/routes.ts](apps/gateway/src/keys/routes.ts)).
3. The response contains **metadata only** — never the secret.

### 4c. Starting a bot (the central flow)
```
Browser ──POST /v1/bots/:id/start (cookie)──► Gateway
  Gateway:
    • requireUser → userId
    • Zod-validate StartBotRequest
    • if mode == 'live':  require totpEnabled  AND  fetch the encrypted envelope
      for the chosen exchange, inject it as `credentials`
    • sign HMAC headers (X-XBT-User/Ts/Sig) over METHOD\nPATH\nTS\nUSER\nsha256(body)
    └──POST http://bot-engine:5001/bots/:id/start──► Bot Engine
         • verify HMAC → InternalIdentity(user_id)
         • scope bot id to "<user_id>:<bot_id>"
         • launcher.launch(): ensure DB row, build strategy + bars + router
           (live → decrypt key in-process, build ccxt client)
         • supervisor.start(): spawn an asyncio task running the bar loop
         • emit bot_started → Redis Stream
```
Routing: [routes/bots.ts](apps/gateway/src/routes/bots.ts) → [clients/bot.ts](apps/gateway/src/clients/bot.ts) → [api/bots.py](apps/bot-engine/app/api/bots.py) → [launcher](apps/bot-engine/app/launchers/production.py) → [supervisor](apps/bot-engine/app/runtime/supervisor.py).

### 4d. A bar → order → fill → UI
```
BarSource yields Bar
  → Supervisor._run: strategy.on_bar(bar, state) → [OrderIntent, …]
    → ExchangeOrderRouter.submit(intent)
       → ExchangeAdapter.place_order(intent)  (paper sim OR ccxt live)
       → PERSIST FIRST: orders row + fills rows + audit_log_trade row  (one txn)
       → publish order_submitted, then fill events → Redis Stream "xbt.events"
    → PnLLedger.apply(fill); circuit-breaker check vs max_loss_quote
Gateway EventStreamConsumer (consumer group "gateway")
  → reads xbt.events, routes each event to that user_id's WebSockets, ACKs
Browser dashboard
  → ws.onmessage → prepend to event list, recompute fills/PnL, redraw chart
```
See [§7](#7-the-bot-engine-in-depth) and [§13](#13-the-real-time-event-pipeline).

### 4e. Backtest
```
Browser ──POST /v1/ai/backtest──► Gateway ──HMAC──► AI Engine /backtest/run
  Backtester.run: fetch public OHLCV (ccxt, no keys) → [Bar]
    → run_backtest: build_strategy(config) + PaperExchangeAdapter, replay bars
    → equity curve + stats (return %, max drawdown, num trades, win rate)
```
The backtester runs **the exact same strategy classes and paper adapter** the live
engine uses — see [§9](#9-the-strategy-domain--backtestlive-parity).

### 4f. Copilot chat
```
Browser ──POST /v1/ai/copilot/chat──► Gateway ──HMAC──► AI Engine /copilot/chat
  Copilot.chat: cached system prompt + (optional context) + messages
    → Anthropic Messages API (claude-sonnet-4-6) → reply + token usage
```

---

## 5. Security model (the keystone)

This is the single most important part of the design and the reason the topology is
shaped the way it is.

### 5a. Envelope encryption of API keys
Implemented twice with byte-for-byte parity:
- **Encrypt (Node):** [apps/gateway/src/security/keys.ts](apps/gateway/src/security/keys.ts)
- **Decrypt (Python):** [apps/bot-engine/app/security/keys.py](apps/bot-engine/app/security/keys.py)

Scheme (AES-256-GCM, version-tagged):
1. Generate a random 32-byte **DEK** (data-encryption key) per record.
2. Encrypt the plaintext (`{"apiKey","secret","password"?}`) with the DEK.
3. Encrypt the DEK with the master **KEK** (`XBT_KEK`, 32 bytes, base64, from the
   secrets manager).
4. Store `{ v, dek_iv, dek_ct, data_iv, data_ct }` as JSONB. **Plaintext never hits
   the DB.**

The envelope shape is also a Zod schema in [clients/bot.ts](apps/gateway/src/clients/bot.ts)
(`EncryptedKeyEnvelope`) and a pydantic model in [api/schemas.py](apps/bot-engine/app/api/schemas.py).
Why envelope (not direct KEK encryption)? Per-record DEKs limit blast radius and make
future KEK rotation possible without re-encrypting every secret's payload. DO has no
KMS, hence application-level crypto with the KEK sourced from Doppler/Infisical.

### 5b. Only the Bot Engine decrypts
- The AI Engine never receives keys (no decrypt path, no `XBT_KEK` needed for its
  function).
- The Gateway holds the KEK (to *encrypt* on input) but the live-bot decrypt happens
  in the Bot Engine, in-process, and the plaintext lives only as long as the ccxt
  client that holds it ([production.py](apps/bot-engine/app/launchers/production.py),
  [ccxt_live.py](apps/bot-engine/app/exchanges/ccxt_live.py) — "never log, never
  persist").

### 5c. Internal HMAC auth (Gateway → Python services)
The Gateway authenticates the human (session/2FA), then becomes a trusted caller to
the Python services. Each internal request is signed:
```
msg = METHOD "\n" PATH "\n" TS "\n" USER_ID "\n" hex(sha256(body))
sig = hex(hmac_sha256(secret, msg))
headers: X-XBT-User, X-XBT-Ts, X-XBT-Sig
```
- **Signer (Node):** [clients/internal-auth.ts](apps/gateway/src/clients/internal-auth.ts)
- **Verifier (Python):** [packages/xbt-core/xbt_core/internal_auth.py](packages/xbt-core/xbt_core/internal_auth.py)
  (shared by both Python apps; re-exported as `app/api/auth.py` in the Bot Engine).

Properties: `hmac.compare_digest` (constant-time), a **±5-minute skew window** to bound
replay, and the body hash binds the signature to the payload. The Python services
**trust the signature instead of re-authenticating** — keeping the entire user-auth
surface in Node. There's a cross-language interop test that signs in Node and verifies
in Python (`test_internal_auth_interop.py`).

### 5d. Tenant isolation, end-to-end
- Bots are scoped server-side to `"<user_id>:<bot_id>"`
  ([api/bots.py `_scoped`](apps/bot-engine/app/api/bots.py)) so two users' `bot-1`
  never collide on the global supervisor map or DB PK.
- Foreign/unknown bot access returns **404** (not 403) to avoid leaking id existence.
- Events fan out only to the owning `user_id`'s sockets
  ([ws/consumer.ts](apps/gateway/src/ws/consumer.ts) +
  [ws/registry.ts](apps/gateway/src/ws/registry.ts)).
- The browser only ever sees the plain `bot_id`; the dashboard strips the
  `userId:` prefix for display.

### 5e. Live-trading gates (defense in depth)
Two independent gates must both pass for real-money orders:
1. **Gateway:** `mode === 'live'` requires `totpEnabled` *and* a stored key for the
   exchange, else 403/400 ([routes/bots.ts](apps/gateway/src/routes/bots.ts)).
2. **Bot Engine:** `XBT_ALLOW_LIVE=1` must be set, else the production launcher raises
   on building a live adapter ([production.py `_build_live_adapter`](apps/bot-engine/app/launchers/production.py)).

### 5f. Password & session hygiene
- Passwords: argon2id via `@node-rs/argon2` (prebuilt, no node-gyp)
  ([auth/password.ts](apps/gateway/src/auth/password.ts)); `verify` swallows malformed
  hashes → returns false, never throws.
- Sessions: opaque 32-byte base64url tokens in Redis with a 7-day TTL, **revocable**
  on logout ([auth/sessions.ts](apps/gateway/src/auth/sessions.ts)).
- Cookie: httpOnly, `sameSite=lax`, `secure` in production
  ([auth/middleware.ts](apps/gateway/src/auth/middleware.ts)).
- TOTP: `otplib`, issuer `xbottrader`; secret stored on enroll but 2FA stays disabled
  until an `activate` call confirms a valid code ([auth/totp.ts](apps/gateway/src/auth/totp.ts),
  [auth/routes.ts](apps/gateway/src/auth/routes.ts)).

---

## 6. The Gateway in depth

Entrypoint: [apps/gateway/src/server.ts](apps/gateway/src/server.ts). On boot it:
1. Optionally runs DB migrations (`XBT_DB_MIGRATE_ON_BOOT != '0'`) via
   [db/migrate.ts](apps/gateway/src/db/migrate.ts) (drizzle migrator, single
   connection, idempotent).
2. Builds Fastify with `@fastify/cookie` + `@fastify/websocket`.
3. Wires persistence (Drizzle over postgres.js), the Redis session store, the
   `EnvelopeCipher`, and the two service clients (`BotEngineClient`, `AiEngineClient`)
   with an `InternalAuthSigner`.
4. Registers route groups: auth, keys, bots, ai. Adds `/healthz`.
5. Mounts `/ws` (cookie-authenticated WebSocket upgrade) and starts the
   `EventStreamConsumer`.
6. Installs SIGINT/SIGTERM graceful shutdown (stop consumer, close server, disconnect
   Redis).

### Module map
| Area | Files |
|---|---|
| HTTP entry | [server.ts](apps/gateway/src/server.ts) |
| Auth | [auth/routes.ts](apps/gateway/src/auth/routes.ts), [auth/middleware.ts](apps/gateway/src/auth/middleware.ts), [auth/sessions.ts](apps/gateway/src/auth/sessions.ts), [auth/password.ts](apps/gateway/src/auth/password.ts), [auth/totp.ts](apps/gateway/src/auth/totp.ts) |
| Keys | [keys/routes.ts](apps/gateway/src/keys/routes.ts), [security/keys.ts](apps/gateway/src/security/keys.ts) |
| Proxy routes | [routes/bots.ts](apps/gateway/src/routes/bots.ts), [routes/ai.ts](apps/gateway/src/routes/ai.ts) |
| Service clients | [clients/bot.ts](apps/gateway/src/clients/bot.ts), [clients/ai.ts](apps/gateway/src/clients/ai.ts), [clients/internal-auth.ts](apps/gateway/src/clients/internal-auth.ts) |
| DB | [db/schema.ts](apps/gateway/src/db/schema.ts), [db/repos.ts](apps/gateway/src/db/repos.ts), [db/client.ts](apps/gateway/src/db/client.ts), [db/migrate.ts](apps/gateway/src/db/migrate.ts), [drizzle/](apps/gateway/drizzle/) |
| WebSocket fan-out | [ws/consumer.ts](apps/gateway/src/ws/consumer.ts), [ws/redis-reader.ts](apps/gateway/src/ws/redis-reader.ts), [ws/registry.ts](apps/gateway/src/ws/registry.ts) |

### HTTP API surface (Gateway, all under `/v1`)
| Method & path | Auth | Purpose |
|---|---|---|
| `POST /v1/auth/signup` | – | Create user, start session |
| `POST /v1/auth/login` | – | Password (+TOTP) login |
| `POST /v1/auth/logout` | session | Revoke session |
| `GET  /v1/auth/me` | session | Current user |
| `POST /v1/auth/2fa/enroll` | session | Begin TOTP enrollment (returns secret + otpauth URL) |
| `POST /v1/auth/2fa/activate` | session | Confirm a code, enable 2FA |
| `POST /v1/keys` | session | Store an encrypted exchange key |
| `GET  /v1/keys` | session | List key metadata |
| `DELETE /v1/keys/:id` | session | Delete a key |
| `POST /v1/bots/:id/start` | session | Start a bot (paper/live) |
| `POST /v1/bots/:id/stop` | session | Graceful stop |
| `POST /v1/bots/:id/kill` | session | Force-kill one bot |
| `POST /v1/bots/kill-all` | session | Force-kill all the user's bots |
| `GET  /v1/bots/:id` | session | Bot state |
| `POST /v1/ai/copilot/chat` | session | Copilot chat |
| `POST /v1/ai/backtest` | session | Run a backtest |
| `GET  /healthz` | – | Liveness |
| `GET  /ws` | session cookie | WebSocket: live event stream |

**Design notes.**
- The BFF clients re-validate upstream responses with Zod (`BotStateResponse.parse`) —
  the Gateway never trusts shapes blindly, in either direction.
- `kill-all` is registered **before** the `:id` routes so Fastify's radix router never
  treats `"kill-all"` as a bot id.
- GET requests are signed but carry **no body** (undici rejects GET-with-body), so the
  signature is over an empty string ([clients/bot.ts `#send`](apps/gateway/src/clients/bot.ts)).
- Upstream errors are surfaced as `{ error: 'bot_engine_error', upstream }` with the
  upstream status preserved.
- Postgres uses TLS for any non-local host (`ssl: 'require'`)
  ([db/client.ts](apps/gateway/src/db/client.ts)).

---

## 7. The Bot Engine in depth

The trading workhorse. A single process hosts **many bots as asyncio tasks**.

App factory: [apps/bot-engine/app/main.py](apps/bot-engine/app/main.py) builds a
FastAPI app from injected singletons (`Supervisor`, `EventPublisher`,
`InternalAuthenticator`, `BotLauncher`). uvicorn entrypoint:
[entrypoint.py](apps/bot-engine/app/entrypoint.py) (`app = build_app()`).

### 7a. Launchers (`XBT_LAUNCHER`)
The `BotLauncher` Protocol ([api/launcher.py](apps/bot-engine/app/api/launcher.py)) is
the only thing the API depends on; `launch()` returns a `LaunchPlan(strategy, bars,
router)`. Two implementations:

- **`demo`** → [DemoPaperLauncher](apps/bot-engine/app/launchers/demo_paper.py): a
  **synthetic random-walk bar source** + paper adapter. Validates the infra topology
  (Droplet + Reserved IP + Redis + HMAC) without real exchanges or keys. Still writes
  real DB rows so the audit trail behaves like prod.
- **`prod`** → [ProductionLauncher](apps/bot-engine/app/launchers/production.py):
  ensures the bot's Postgres row, builds the strategy, and wires:
  - **paper mode** → `PaperExchangeAdapter` (real prices via public ccxt, simulated
    fills),
  - **live mode** → `CcxtExchangeAdapter` (real orders) after decrypting the key
    in-process — *gated behind `XBT_ALLOW_LIVE=1`*.
  - Market data for both modes comes from a polling `CcxtBarSource` over **public**
    OHLCV (no keys needed for data).

`entrypoint.py` also validates `REDIS_URL` early (clear error on a blank/garbage value)
and bootstraps a SQLite schema for the staging DB (no-op for Postgres, which Alembic
owns).

> Note: `main.py`'s standalone `main()` still raises `NotImplementedError`; the real
> production wiring lives in `entrypoint.py:build_app()`, which is what uvicorn loads.

### 7b. Supervisor — lifecycle, the bar loop, risk
[runtime/supervisor.py](apps/bot-engine/app/runtime/supervisor.py).

**State machine** (`BotState`): `STARTING → RUNNING → {STOPPING → STOPPED | ERRORED |
PAUSED | KILLED}`.
- `PAUSED` = a risk limit tripped; trading stopped, no clean exit, won't resume without
  restart.
- `KILLED` = force-stopped via kill switch.

**The loop** (`_run`): for each `bar` from the `BarSource`:
1. break if `STOPPING`,
2. record `last_mark = bar.close`,
3. `intents = strategy.on_bar(bar, state)`,
4. for each intent, `fills = await router.submit(...)`; feed fills into the
   `PnLLedger`,
5. evaluate the **circuit breaker** (`_tripped`); if tripped, cancel open orders and
   return.
Exceptions (other than cancellation) → `ERRORED` + a `bot_error` event. Clean
exhaustion → `STOPPED` + `bot_stopped`.

**PnLLedger** — mark-to-market, single-quote-currency, average-cost accounting:
`pnl(mark) = position * mark - cash_out`. Good enough to enforce a loss cap on
single-symbol strategies; full per-symbol position accounting is deferred.

**Circuit breaker** — if `max_loss_quote` is set and `pnl(last_mark) <=
-max_loss_quote`, transition to `PAUSED`, emit `bot_circuit_tripped` (with pnl, cap,
mark, position), and unwind.

**Kill switch** — `kill(bot_id)` cancels the task immediately, best-effort cancels
resting orders via the router's optional `cancel_open`, emits `bot_killed`. Idempotent.
`kill_all(user_id=…)` scopes to one user.

**Concurrency guarantees** — an `asyncio.Lock` guards start; a bot already
STARTING/RUNNING can't be double-started (the caller owns the *distributed* lock for
multi-worker safety, which is a deferred concern).

### 7c. Order router — persist-before-publish
[runtime/router.py](apps/bot-engine/app/runtime/router.py) implements the supervisor's
`OrderRouter` Protocol, one per bot (so a buggy adapter for one user can't affect
another).

Every order, in **one DB transaction, before any event is published**:
1. an `orders` row,
2. `fills` rows for immediate fills,
3. an `audit_log_trade` row.

*Then* it publishes `order_submitted` and `fill` events. The ordering invariant: a
downstream consumer can **never** see an event without a durable paper trail (your
defense in a trade dispute).

Other responsibilities:
- Tracks resting (acknowledged-but-unfilled) orders in `_open_orders` keyed by
  exchange order id; `cancel_open` cancels them at the venue, marks them `cancelled`
  with an audit row, and emits `order_cancelled` per order (best-effort per order).
- `deliver_fills` handles out-of-band fills (limit fills, ccxt WS stream) that don't
  go through `place_order`.

### 7d. Bot Engine API
[api/bots.py](apps/bot-engine/app/api/bots.py) — all routes HMAC-authenticated via the
`internal_identity` dependency; all bot ids scoped to `"<user_id>:<bot_id>"`.

| Method & path | Purpose |
|---|---|
| `POST /bots/:id/start` | Launch + supervise (400 on launcher reject, 409 if already running) |
| `POST /bots/:id/stop` | Graceful stop |
| `POST /bots/:id/kill` | Force kill (404 if unknown/foreign) |
| `POST /bots/kill-all` | Kill all caller's bots |
| `GET  /bots/:id` | State + last_error |
| `GET  /healthz` | Liveness |

Request/response models: [api/schemas.py](apps/bot-engine/app/api/schemas.py) —
`StartBotRequest` (strategy + mode + exchange + risk + optional credentials),
`BotStateResponse`, `KillAllResponse`. Strategy models are **re-exported from
`xbt_core.strategy_config`** so live and backtest validate identical strategies.

### 7e. Bot Engine persistence
SQLAlchemy 2.0 models ([db/models.py](apps/bot-engine/app/db/models.py)): `bot_configs`,
`orders`, `fills`, `audit_log_trade`. Async repositories
([db/repositories.py](apps/bot-engine/app/db/repositories.py)) are stateless; sessions
are injected. Numeric precision uses `Numeric(36, 18)`. `audit_log_trade` is
append-only ("never delete rows"). Schema is owned by Alembic
([alembic/versions/](apps/bot-engine/alembic/)).

---

## 8. The AI Engine in depth

App factory: [apps/ai-engine/app/main.py](apps/ai-engine/app/main.py) (injects
`Copilot`, `Backtester`, `InternalAuthenticator`). Entrypoint:
[entrypoint.py](apps/ai-engine/app/entrypoint.py). Routes:
[api/routes.py](apps/ai-engine/app/api/routes.py) — both HMAC-authenticated.

### 8a. Copilot
[llm/copilot.py](apps/ai-engine/app/llm/copilot.py). A thin wrapper around a
`CompleteFn` (the real one wraps the Anthropic SDK; tests inject a fake).
- Model: `claude-sonnet-4-6` (override via `XBT_COPILOT_MODEL`), `max_tokens=1024`.
- The large, stable system prompt is marked `cache_control: ephemeral` so repeated
  calls hit the **prompt cache**.
- Optional `context` (e.g. a bot/PnL summary) is prepended as a user turn to ground the
  answer.
- Guardrails are *in the system prompt*: it explains strategies, summarizes PnL, and
  answers "why did my bot do X" — but **never places/modifies/cancels trades** and
  **never gives individualized financial advice or guarantees**.
- Returns `reply` + `usage` (incl. `cache_read_input_tokens`).

### 8b. Backtester
[backtest/engine.py](apps/ai-engine/app/backtest/engine.py) +
[backtest/data.py](apps/ai-engine/app/backtest/data.py).
- `Backtester.run` fetches historical OHLCV from a **public** ccxt client (default
  exchange `kraken`, override `XBT_BACKTEST_EXCHANGE`), converts candles → `Bar`s, then
  calls `run_backtest`.
- `run_backtest` builds the strategy via the shared `build_strategy` and replays bars
  through a `PaperExchangeAdapter`, computing an equity curve and stats: total return
  %, **max drawdown %**, num trades, win rate (average-cost realized PnL on sells).
- The data client is injected (tests use a scripted fake, no network).

### 8c. AI Engine API
| Method & path | Purpose |
|---|---|
| `POST /copilot/chat` | `{messages[], context?}` → `{reply, usage}` |
| `POST /backtest/run` | `{strategy, timeframe?, limit?, since_ms?, starting_cash?, exchange?}` → stats + equity curve (400 on bad config) |
| `GET  /healthz` | Liveness |

Schemas: [api/schemas.py](apps/ai-engine/app/api/schemas.py) — `BacktestRequest`
defaults: `timeframe=1h`, `limit=500` (2–1000), `starting_cash=10000`,
`exchange=kraken`.

---

## 9. The strategy domain & backtest/live parity

This is the conceptual heart of the system, all in
[packages/xbt-core/xbt_core](packages/xbt-core/xbt_core/).

### 9a. The parity contract
A `Strategy` ([strategies/base.py](packages/xbt-core/xbt_core/strategies/base.py)) is
**pure** with respect to its inputs: `on_bar(bar, state) -> list[OrderIntent]`. It
never touches an exchange — the *runner* (live supervisor or backtest harness)
translates intents into real orders. Because:
- the **same `Strategy` subclasses**, and
- the **same `PaperExchangeAdapter`**, and
- the **same cash/position accounting** (`position*mark - cash_out`)

drive both paths, an equity curve from a backtest equals what a live paper bot would
produce on the same bars — **parity by construction**, not by re-implementation.
State that must survive across bars lives in the mutable `StrategyState` (a
`last_action_ts_ms` + a `custom` dict); the strategy object itself is stateless.

Core domain types:
- `Bar(ts_ms, symbol, open, high, low, close, volume)` — all `Decimal`.
- `OrderIntent(symbol, side, type, quantity, limit_price?, client_tag)` — validates
  quantity > 0 and the market/limit price invariants in `__post_init__`.

### 9b. Built-in strategies
| Strategy | File | Logic |
|---|---|---|
| **DCA** | [dca.py](packages/xbt-core/xbt_core/strategies/dca.py) | Every `interval_minutes`, market-buy `quote_amount` worth. Simplest possible; exercises the whole pipeline. |
| **Grid** | [grid.py](packages/xbt-core/xbt_core/strategies/grid.py) | Even ladder between `lower/upper_price`. Falling close crosses a line → buy a lot; rising → sell a lot (LIFO lot stack, no shorting). MVP: fills modeled at the bar close, not as resting limits. |
| **MA crossover** | [ma_crossover.py](packages/xbt-core/xbt_core/strategies/ma_crossover.py) | Two SMAs of the close. Golden cross opens a long of `position_quote`; death cross closes it. One position at a time. Rolling window of `slow_period` closes. |
| **Custom rules** | [rule_engine.py](packages/xbt-core/xbt_core/strategies/rule_engine.py) | User-authored logic *as data* — see below. |

### 9c. The custom-rules DSL (logic as data)
A user composes a strategy from a fixed vocabulary, serialized as JSON. The engine
**interprets** it — there is no `eval`, no user code — so the same JSON runs identically
in backtest and live with **zero sandboxing**.

Shape:
```json
{
  "symbol": "BTC/USDT",
  "indicators": [
    {"name": "fast", "fn": "sma", "period": 10},
    {"name": "slow", "fn": "sma", "period": 30},
    {"name": "rsi14", "fn": "rsi", "period": 14}
  ],
  "rules": [
    {"when": {"op": "and", "terms": [
        {"op": "crossover", "left": "fast", "right": "slow"},
        {"op": "<", "left": "rsi14", "right": "30"}]},
     "do": {"side": "buy", "type": "market", "quote": "100"},
     "cooldown_minutes": 60}
  ]
}
```
- **Indicators:** `price` (reserved built-in = current close), `value` (constant),
  `sma`, `rsi` (simple-average form). All **window-bounded** — they depend only on the
  last N closes, so a live bot starting mid-stream converges to the exact values a
  full-history backtest produces. Path-dependent indicators (EMA, Wilder-RSI) are
  deliberately deferred to preserve parity.
- **Conditions:** comparisons (`< <= > >= ==`) and edge-triggers (`crossover`,
  `crossunder`), composable with boolean `and`/`or`/`not` (recursive).
- **Edge-triggered firing:** a rule fires on the bar its condition flips false→true,
  not every bar it stays true. `cooldown_minutes` rate-limits further.
- **No inventory guard:** a `sell` rule doesn't verify there's anything to sell —
  overselling is caught downstream by the adapter and the supervisor's risk limits.

The engine maintains a trailing window sized to `max_window`, computes indicator values
each bar, evaluates rules against current + previous values (for crossovers), and emits
market or limit intents.

### 9d. The factory
[strategies/factory.py](packages/xbt-core/xbt_core/strategies/factory.py) maps a
**duck-typed** config (anything exposing `strategy_type` + the right fields) to a
runtime `Strategy`. Both the live launcher and the backtester call it. New strategies
plug in here.

### 9e. Validation schema
[strategy_config.py](packages/xbt-core/xbt_core/strategy_config.py) holds the shared
**pydantic** models (`DcaParams`, `GridParams`, `MaCrossoverParams`,
`CustomRulesParams`), a discriminated union on `strategy_type`. Both Python apps
validate against this *same* schema before calling `build_strategy` — that's what
guarantees the two services accept exactly the same strategies. It deliberately lives
apart from the pure strategy domain (which must stay dependency-free); `pydantic` is an
optional `schemas` extra of `xbt-core` ([pyproject.toml](packages/xbt-core/pyproject.toml)).
`CustomRulesParams` does deeper structural checks: unique indicator names, reserved-name
collisions, and that every referenced operand resolves to a known indicator or numeric
literal.

---

## 10. Exchange adapters

### 10a. The interface
[xbt_core/exchanges/base.py](packages/xbt-core/xbt_core/exchanges/base.py) defines the
`ExchangeAdapter` Protocol (`venue`, `place_order`, `cancel_order`, `close`) and the
DTOs `SubmittedOrder`, `FillEvent`, `PlacementResult`. New venues plug in as adapters;
strategies and the supervisor never change.

There are **two copies of the exchange layer** by design:
- [packages/xbt-core/xbt_core/exchanges/](packages/xbt-core/xbt_core/exchanges/) — the
  `paper` adapter + base, shared so the backtester (in the AI Engine) can simulate
  fills without importing Bot-Engine code.
- [apps/bot-engine/app/exchanges/](apps/bot-engine/app/exchanges/) — the Bot-Engine
  copy (`base`, `paper`, plus the live `ccxt_adapter` and `ccxt_live`).

### 10b. Paper adapter
[xbt_core paper.py](packages/xbt-core/xbt_core/exchanges/paper.py) (and the Bot-Engine
twin). In-memory, no network:
- Market orders fill instantly at the current mark, adjusted by **slippage** (default 5
  bps) and **fee** (default 10 bps, `USDT`).
- Limit orders rest in memory and fill when `update_mark` shows the mark crossing the
  limit.
- `update_mark(symbol, price)` both updates the mark and returns any limit fills that
  triggered. Paper mode wires `update_mark` as the bar source's `mark_sink` so
  simulated fills price off real (or synthetic) data.

### 10c. ccxt live adapter
[ccxt_adapter.py](apps/bot-engine/app/exchanges/ccxt_adapter.py) wraps any
`ccxt.async_support` exchange behind `ExchangeAdapter`. Maps ccxt order statuses to our
normalized set, extracts fills from `trades[]` or the order's `filled`/`average`.
[ccxt_live.py](apps/bot-engine/app/exchanges/ccxt_live.py) provides:
- `CcxtBarSource` — polls `fetch_ohlcv` (MVP: polling, not ccxt.pro WS), yields each
  newly-*closed* candle (dedup by timestamp), optionally feeding a `mark_sink`.
- `ExchangeCredentials.from_plaintext` — parses the decrypted `{apiKey, secret,
  password?}` JSON.
- `build_ccxt_client` / `build_public_ccxt_client` — authed vs keyless clients (ccxt
  imported lazily so the app boots without it and tests can inject fakes).

Caveat in code: ccxt uses Python `float`; per-venue precision normalization
(`amount_to_precision`/`price_to_precision`) is a TODO before the first real-money
deploy.

---

## 11. Shared contracts (TS ⇄ Python)

The system maintains **parallel schemas** in two languages, kept in sync by convention
(and an intended JSON-Schema diff in CI).

### TS side — [packages/shared](packages/shared/)
- [primitives.ts](packages/shared/src/primitives.ts): `Uuid`, `Decimal` (string-encoded
  to preserve precision over JSON), `IsoTimestamp`, `Exchange` (`binance`|`coinbase`),
  `Symbol_` (`BASE/QUOTE` regex), `Side`, `Mode`.
- [order.ts](packages/shared/src/order.ts): `OrderType`, `OrderStatus`, `Order`, `Fill`.
- [bot.ts](packages/shared/src/bot.ts): the Zod `StrategyConfig` discriminated union
  (mirrors the pydantic one), `RiskLimits`, `BotConfig`. The recursive `Condition` uses
  `z.lazy`.
- [events.ts](packages/shared/src/events.ts): the `EventEnvelope` discriminated union —
  `signal`, `order_submitted`, `fill`, `bot_*` lifecycle, `bot_error`, `log_line`,
  `equity_point`.

### Python side
- [xbt_core/strategy_config.py](packages/xbt-core/xbt_core/strategy_config.py): the
  pydantic `StrategyConfig` (source of truth for deeper validation).
- Bot Engine & AI Engine re-export it from their `api/schemas.py`.

**Why strings for decimals?** JSON numbers are IEEE-754 floats; money isn't. Every
monetary/price field crosses the wire as a decimal string and is parsed to Python
`Decimal` / validated by the `Decimal` Zod regex.

**Sync discipline.** The Zod `StrategyConfig` in `bot.ts` and the pydantic one are
mirror images; the dashboard builds the discriminated payload by hand and the Bot
Engine is the source of truth for the deeper checks (and returns 400 on violation).

---

## 12. Persistence & data ownership

**Database-per-service discipline. No cross-service foreign keys** — joins happen in the
Gateway BFF layer. `user_id` is a plain string across services, intentionally not
DB-enforced.

| Owner | Tables / store | Schema tooling |
|---|---|---|
| **Gateway** | Postgres: `users`, `api_keys_ciphertext`, `audit_log_auth` | Drizzle ([schema.ts](apps/gateway/src/db/schema.ts)) + [drizzle/](apps/gateway/drizzle/) migrations |
| **Gateway** | Redis: sessions (`sess:` prefix, 7-day TTL) | n/a |
| **Bot Engine** | Postgres: `bot_configs`, `orders`, `fills`, `audit_log_trade` | SQLAlchemy + Alembic ([models.py](apps/bot-engine/app/db/models.py)) |
| **Bot Engine** | Redis Streams: `xbt.events` (producer) | n/a |

Key columns:
- `users`: email (unique), `password_hash`, `totp_secret` (nullable), `totp_enabled`.
- `api_keys_ciphertext`: `(user_id, exchange, label, envelope jsonb)` — ciphertext only.
- `audit_log_auth`: `(user_id?, action, ip, ts)` — signup/login/login_2fa/logout/2fa_enable.
- `bot_configs`: `(user_id, name, exchange, mode, strategy jsonb, status)`.
- `orders` / `fills`: full order + execution detail, `Numeric(36,18)`.
- `audit_log_trade`: append-only `(user_id, bot_id?, action, detail jsonb, ts)`.

Migration strategy:
- Gateway migrates **on boot** (`runMigrations`) — fits single-instance staging;
  disable with `XBT_DB_MIGRATE_ON_BOOT=0` and run out-of-band for multi-instance prod.
- Bot Engine: Alembic owns the Postgres schema; a SQLite bootstrap path exists for
  staging only.

---

## 13. The real-time event pipeline

```
Bot Engine: EventPublisher.publish(Event)
  → redis.xadd("xbt.events", fields, maxlen≈1,000,000)
       fields = {event_type, user_id, bot_id, ts, payload(JSON)}
Gateway: EventStreamConsumer (consumer group "gateway", consumer "gateway-<host>-<pid>")
  → XREADGROUP BLOCK 5000 COUNT 100  ">"
  → for each entry: registry.sendTo(user_id, serializeForClient(fields)); XACK
Browser: ws.onmessage → {event_type, bot_id, ts, payload}
```

- **Producer:** [events/publisher.py](apps/bot-engine/app/events/publisher.py) — stream
  key `xbt.events`, capped at ~1M entries (older dropped), `payload` JSON-serialized
  with `default=str` (Decimals → strings).
- **Consumer:** [ws/consumer.ts](apps/gateway/src/ws/consumer.ts) — uses a Redis
  **consumer group** for durable, at-least-once delivery; ensures the group exists
  (`MKSTREAM`, ignores `BUSYGROUP`). Reader: [ws/redis-reader.ts](apps/gateway/src/ws/redis-reader.ts).
- **Registry:** [ws/registry.ts](apps/gateway/src/ws/registry.ts) — `Map<userId,
  Set<Sender>>`; one user can hold many sockets (multi-tab/device). `sendTo` is
  best-effort and returns the recipient count.
- **Delivery semantics:** live updates are **best-effort** — events for a user with no
  open socket are simply ACKed and dropped. **Durable history lives in Postgres.** The
  Redis stream decouples bot execution from slow browsers (a bot never blocks on the
  UI).

**Event catalogue** (from [events.ts](packages/shared/src/events.ts) + emitters):
`signal`, `order_submitted`, `fill`, `order_cancelled`, `bot_started`, `bot_stopped`,
`bot_paused`, `bot_killed`, `bot_circuit_tripped`, `bot_error`, `log_line`,
`equity_point`. (The Zod union covers the documented set; the supervisor/router also
emit `order_cancelled` and `bot_circuit_tripped`.)

---

## 14. The web frontend

Next.js App Router ([apps/web/app/](apps/web/app/)): `page.tsx` (landing),
`login`, `signup`, `dashboard`, `settings`. Talks **only** to the Gateway,
same-origin — REST under `/v1`, WebSocket at `/ws`. No secrets, no direct contact with
the Python services.

### Dashboard — [apps/web/app/dashboard/page.tsx](apps/web/app/dashboard/page.tsx)
- **Auth gate:** `GET /v1/auth/me`; redirect to `/login` if unauthenticated.
- **Live WS:** connects to `wss?://<host>/ws` (cookie sent on the same-origin upgrade —
  no token needed), keeps the last 300 events, shows connection status.
- **Strategy builder:** form for DCA / grid / MA-crossover, plus a full **visual
  rule-builder** (`RuleBuilder`) for `custom_rules` — add indicators and AND/OR
  comparison terms, flattened into the DSL by `buildCustomRules` before POSTing.
- **Symbol validation:** inline `BASE/QUOTE` regex check (crypto only) mirroring the
  shared `Symbol_`.
- **Controls:** Start (paper), Kill this, Kill all.
- **Live analytics, all client-side from the fill stream:** fills count, position,
  last price, fees, mark-to-last PnL, and a dependency-free SVG price chart. Bot ids
  are displayed with the `userId:` prefix stripped.

### Settings — [apps/web/app/settings/page.tsx](apps/web/app/settings/page.tsx)
- **2FA enrollment:** `enroll` shows the TOTP secret; `activate` confirms a code.
- **API keys:** add (`POST /v1/keys`), list metadata, delete. Copy reminds the user the
  key is encrypted and only the Bot Engine decrypts.

Frontend↔backend env: `NEXT_PUBLIC_GATEWAY_WS` / `NEXT_PUBLIC_GATEWAY_HTTP` are
build-time, but in the single-origin prod setup the dashboard just uses relative paths.

---

## 15. Deployment topology & infra

**The defining infra decision:** the Bot Engine needs a **stable outbound IP** because
exchange API keys are IP-allowlisted; DO App Platform's egress IPs rotate. So the trading
engine is physically separated onto a fixed Droplet.

```
                     ┌───────────────── DO App Platform (single origin) ─────────────────┐
                     │  domain: xbottrader.ai  (Let's Encrypt, DO-managed DNS zone)       │
   Internet ─────────┤  ingress path-routing:                                            │
                     │    /v1, /ws, /healthz → gateway  (preserve_path_prefix)            │
                     │    /                   → web                                       │
                     │    (ai-engine: NO public route — internal VPC only)               │
                     │                                                                    │
                     │  ┌─ gateway (basic-xs) ─┐  ┌─ ai-engine (internal:5002) ─┐  ┌─ web ┐ │
                     │  └──────────┬───────────┘  └────────────▲────────────────┘  └──────┘ │
                     └────────────┼─────────────────────────── │ ${ai-engine.PRIVATE_URL} ─┘
                                  │ BOT_ENGINE_URL = http://<reserved-ip>:5001
                                  ▼
                     ┌──────────── DO Droplet (Reserved/static IP) ────────────┐
                     │  docker-compose: bot-engine (FastAPI :5001)             │
                     │  systemd unit survives reboots; firewall opens 22, 5001 │
                     └────────────────────────────────────────────────────────┘

   Managed services: DO Managed Postgres (TLS) · DO Managed Caching/Valkey (shared Redis)
   Secrets: App Platform encrypted env + /etc/xbt/bot-engine.env on the Droplet
            (→ migrate to Doppler/Infisical for rotation)
```

### App Platform — [.do/app.yaml](.do/app.yaml)
- **gateway** (`basic-xs`, port 4000): Docker build, `deploy_on_push`, `/healthz`
  health check, migrates Postgres on boot. Env: `REDIS_URL`,
  `GATEWAY_INTERNAL_HMAC_SECRET`, `BOT_ENGINE_URL` (the Droplet Reserved IP),
  `AI_ENGINE_URL` (`${ai-engine.PRIVATE_URL}`), `XBT_KEK`, `DATABASE_URL`.
- **ai-engine** (`basic-xs`, `internal_ports: [5002]`): **no `http_port` → no public
  ingress route**; reachable only over the VPC. Env: HMAC secret, `ANTHROPIC_API_KEY`,
  `XBT_COPILOT_MODEL`, `XBT_BACKTEST_EXCHANGE`. (It has no exchange egress, so it
  doesn't need the Reserved-IP constraint.)
- **web** (`basic-xxs`, port 3000): Docker build (the npm buildpack can't resolve
  pnpm's `workspace:*` dep on `@xbt/shared`). Build-time `NEXT_PUBLIC_*` gateway URLs.
- **ingress:** more-specific prefixes (`/v1`, `/ws`, `/healthz` → gateway) precede the
  `/` catch-all → web. `preserve_path_prefix: true` because App Platform otherwise
  strips the matched prefix.

### Droplet — [infra/do/](infra/do/)
- [bot-engine-cloud-init.sh](infra/do/bot-engine-cloud-init.sh): installs Docker, clones
  the repo, brings up the compose stack, installs a systemd unit (`xbt-bot-engine`) so
  it survives reboots.
- [bot-engine-compose.yml](infra/do/bot-engine-compose.yml): the `bot-engine` service on
  :5001, `restart: unless-stopped`, secrets from `/etc/xbt/bot-engine.env`. Redis points
  at the **shared** DO Managed Caching (Valkey) so fills reach the Gateway's fan-out.
  Staging defaults: `XBT_LAUNCHER=demo`, SQLite on a named volume.
- [scripts/provision-bot-engine.sh](infra/scripts/provision-bot-engine.sh): `doctl`
  creates the Droplet (cloud-init), allocates + attaches a **Reserved IP** (polls for
  assignment), and opens a firewall (22, 5001 inbound). Prints the Reserved IP — *that's
  what users allowlist*.
- `scripts/deploy-bot-engine.sh`: `git pull` + rebuild the compose stack on update.

### Runbook & smoke test
[infra/README.md](infra/README.md) walks the full deploy and includes an HMAC-signed
`curl` that starts a demo DCA bot and watches `XLEN xbt.events` climb. (Heads-up: parts
of that README's "intentionally deferred" section are stale — real auth and the
production launcher now exist; see [§19](#19-known-gaps--deferred-work).)

---

## 16. CI

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on PRs and pushes to `main`,
with cancel-in-progress concurrency. Three jobs:
1. **node** — pnpm install, `pnpm -r typecheck`, `pnpm -r test` (Node 22).
2. **python** — uv venv in `apps/bot-engine`, install deps + pytest/httpx, also install
   gateway deps for the **cross-language HMAC interop test**, then `pytest tests/`.
3. **docker-build** — builds the gateway and bot-engine images (no push) to catch
   Dockerfile breakage.

Test coverage is substantial on the Python side (supervisor, router, kill-switch,
strategies, rule engine, ccxt adapter, internal-auth interop, integration DCA paper)
and the TS side (auth routes, sessions, totp, keys, internal-auth, bot client, ws
consumer/registry, security keys).

---

## 17. Configuration / environment variables

From [.env.example](.env.example) and the entrypoints:

| Var | Used by | Purpose |
|---|---|---|
| `NODE_ENV` | gateway, web | `production` toggles secure cookies |
| `XBT_KEK` | gateway (encrypt), bot-engine (decrypt) | base64 32-byte master key for envelope encryption |
| `DATABASE_URL` | gateway, bot-engine | Postgres (gateway: postgres.js; bot-engine: `postgresql+asyncpg://…`) |
| `REDIS_URL` | gateway, bot-engine | sessions (gateway) + event stream (both) |
| `GATEWAY_PORT` | gateway | default 4000 |
| `GATEWAY_SESSION_SECRET` | gateway | (reserved; sessions are random tokens today) |
| `GATEWAY_INTERNAL_HMAC_SECRET` | all three services | shared HMAC secret |
| `BOT_ENGINE_URL` / `AI_ENGINE_URL` | gateway | upstream service URLs |
| `BOT_ENGINE_PORT` / `AI_ENGINE_PORT` | bot/ai engines | 5001 / 5002 |
| `ANTHROPIC_API_KEY` | ai-engine | copilot |
| `XBT_COPILOT_MODEL` | ai-engine | default `claude-sonnet-4-6` |
| `XBT_BACKTEST_EXCHANGE` | ai-engine | default `kraken` |
| `XBT_LAUNCHER` | bot-engine | `demo` or `prod` |
| `XBT_ALLOW_LIVE` | bot-engine | `1` to permit real-money orders |
| `XBT_DEMO_BAR_INTERVAL_S` | bot-engine | synthetic bar cadence (demo) |
| `XBT_DB_MIGRATE_ON_BOOT` | gateway | `0` disables boot migrations |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | (future) | billing (not yet wired) |
| `SPACES_*` | (future) | DO Spaces (S3-compatible) |

---

## 18. Glossary of invariants & design rules

- **Strategies are pure** — same bars in, same intents out. The runner owns side
  effects. This is what makes parity testable.
- **Backtest == live (paper)** — same strategy classes + same paper adapter + same
  accounting.
- **Persist before publish** — DB rows (order, fills, audit) commit before any event is
  emitted. No event without a paper trail.
- **Only the Bot Engine decrypts keys** — and only in-process, only as long as the ccxt
  client lives; never logged, never persisted, never returned.
- **All user auth lives in Node** — Python services trust HMAC signatures, never
  re-authenticate users.
- **Decimals cross the wire as strings** — never floats, to preserve precision.
- **Per-user scoping everywhere** — `"<user_id>:<bot_id>"`, 404 (not 403) on
  foreign/unknown ids, events fan out only to the owning user.
- **Two live-trading gates** — Gateway (2FA + stored key) *and* Bot Engine
  (`XBT_ALLOW_LIVE`).
- **Best-effort live UI, durable history in Postgres** — the Redis stream decouples
  execution from the browser.
- **No cross-service FKs** — database-per-service; the BFF joins.
- **Window-bounded indicators only (rule engine)** — guarantees live convergence to
  backtest values.

---

## 19. Known gaps / deferred work

Drawn from in-code TODOs and the infra README (some of which is now stale):

- **Production live trading is gated off** by default (`XBT_ALLOW_LIVE=0`). The
  pre-real-money checklist: kill-switch < 5s, key-handling audit, backtest↔live parity
  verification.
- **`main.py:main()`** still raises `NotImplementedError`; uvicorn uses
  `entrypoint.py:build_app()` instead — harmless, but a loose end.
- **ccxt precision normalization** (`amount_to_precision`/`price_to_precision`) not yet
  applied — needed before the first real-money deploy.
- **PnL accounting is single-symbol, average-cost** — enough for the loss-cap circuit
  breaker; full per-symbol positions are deferred.
- **Grid fills modeled at bar close**, not as true resting limits; partial fills
  deferred.
- **Market data is polled** (`fetch_ohlcv`), not streamed via ccxt.pro WS.
- **Out-of-band fill linkage** (`deliver_fills`) currently joins by `exchange_order_id`
  best-effort; precise parent-order resolution is a follow-up.
- **Distributed lock** for multi-worker bot ownership is the caller's responsibility and
  not yet implemented (single-process today).
- **Stripe billing and DO Spaces** are scaffolded in env only, not wired.
- **Secrets** live in App Platform env + a Droplet env file; migrate to
  Doppler/Infisical when rotation becomes a workflow.
- **Schema sync** between Zod and pydantic is by convention; the intended JSON-Schema
  diff in CI isn't in place yet.
- The infra README's "no Postgres / no auth / placeholder header" notes are **out of
  date** — Drizzle/Postgres, argon2 + sessions + TOTP, and the production launcher all
  exist now.

---

*Generated by reading the codebase end-to-end. When in doubt, the code wins — every
claim above links to its source.*
