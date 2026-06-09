# Staging deploy — pre-flight checklist

Goal of a **staging** deploy: validate the live topology (App Platform Gateway +
Web + AI Engine, Droplet Bot Engine + Reserved IP + Redis + HMAC) and obtain the
**Reserved IP** to publish for exchange API-key allowlisting. **Paper-only. Not
for real users.** Real auth (2FA) and Stripe billing now exist; staging still runs
SQLite (no Postgres) and keeps live trading gated (`XBT_ALLOW_LIVE` unset →
demo/paper). Billing degrades gracefully when Stripe is unconfigured.

Full step-by-step commands live in [README.md](README.md); this is the gate list.

## Verified locally (this session)
- [x] All suites green — bot-engine 103, ai-engine 8, gateway 41, typecheck clean.
- [x] All three Docker images build from the monorepo root.
- [x] bot-engine (demo) and ai-engine containers boot and serve `/healthz`.
- [x] Backtest runs end-to-end over real kraken data.

## Before `doctl apps create`
- [ ] Push the latest `main` to GitHub (App Platform deploys from git).
- [ ] In [.do/app.yaml](../.do/app.yaml) replace `REPLACE_ME/xbottrader` with your
      `owner/repo` (3 occurrences: gateway, ai-engine, web) and set `region`.
- [ ] Generate + store the two secrets (keep them safe — not regenerated):
  - `XBT_KEK` — 32-byte base64 master key (envelope encryption).
  - `GATEWAY_INTERNAL_HMAC_SECRET` — shared Gateway↔services HMAC secret.
- [ ] Have an `ANTHROPIC_API_KEY` for the AI Engine copilot.
- [ ] Provide a `REDIS_URL` (temporary Upstash free tier is fine for first boot;
      move to DO Managed Caching once Bot Engine shares it).

## App Platform (Gateway + Web + AI Engine)
- [ ] `doctl apps create --spec .do/app.yaml` → save `APP_ID`.
- [ ] Set secrets: `XBT_KEK`, `GATEWAY_INTERNAL_HMAC_SECRET`, `ANTHROPIC_API_KEY`,
      `REDIS_URL`, `XBT_ALPACA_DATA_KEY`/`XBT_ALPACA_DATA_SECRET` (in the App
      Platform UI as encrypted env vars, or by filling the SECRET `value:` fields
      in an untracked copy of the spec and `doctl apps update --spec <copy>` —
      there is no `-e` flag). Note: here the Alpaca keys reach only `ai-engine`
      (backtests). The dashboard chart + equity bar feed run in the **Bot
      Engine** — set those keys on the Droplet too (next section).
- [ ] `AI_ENGINE_URL` resolves automatically via `${ai-engine.PRIVATE_URL}` — no
      manual value needed. `BOT_ENGINE_URL` is filled after the Droplet step.
- [ ] Verify `GET https://gateway-xxxx.ondigitalocean.app/healthz` → `{"ok":true}`.

## Droplet (Bot Engine)
- [ ] Run `infra/scripts/provision-bot-engine.sh` (needs `doctl` auth + an SSH key).
- [ ] Seed `/etc/xbt/bot-engine.env` with `GATEWAY_INTERNAL_HMAC_SECRET` + `XBT_KEK`;
      restart the service. Staging keeps `XBT_LAUNCHER=demo` + SQLite (default).
- [ ] For equity (Alpaca) charts/bars: add `XBT_ALPACA_DATA_KEY` +
      `XBT_ALPACA_DATA_SECRET` to `/etc/xbt/bot-engine.env` and restart. App
      Platform vars don't reach the Droplet; crypto needs no keys.
- [ ] Record the **Reserved IP**; set `BOT_ENGINE_URL=http://<ip>:5001` on the app.
- [ ] Verify `curl http://<ip>:5001/healthz` → `{"ok":true}`.

## Smoke the loop
- [ ] HMAC-signed paper-DCA start (README §3) → fills appear in `redis-cli XLEN xbt.events`.
- [ ] Gateway `POST /v1/ai/backtest` (signed via `x-dev-user`) returns stats.

## Smoke billing (`/v1/billing/*`)
- [ ] **Stripe unset:** gateway still boots; `GET /v1/billing/status` →
      `{enabled:false, active:false}`; `POST /v1/billing/checkout` → `503
      billing_unavailable`. App runs fully free (live + AI stay locked).
- [ ] **Stripe configured** (`STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET`/`STRIPE_PRICE_ID`
      set on the gateway; webhook → `https://<domain>/v1/webhooks/stripe`; Customer
      Portal activated):
  - [ ] `stripe listen --forward-to <gateway>/v1/webhooks/stripe`; subscribe with test
        card `4242 4242 4242 4242` → `subscriptions.status` becomes `active`.
  - [ ] `GET /v1/billing/status` → `{active:true}`; a free user gets `403
        subscription_required` on `/v1/ai/copilot/chat`, `/v1/ai/strategy/author`,
        and on `mode:'live'` / `ai_signal` bot starts; `/v1/ai/backtest` stays free.
  - [ ] Customer Portal **cancel** → webhook flips status → routes lock again.
  - [ ] Webhook rejects an unsigned/garbage body with `400`.

## Known gaps (do NOT skip before *beta* / *real money*)
- **Beta (real users, paper):** real auth + 2FA, Postgres + `alembic upgrade head`
  (replace SQLite), shared managed Redis, Sentry, secrets in Doppler/Infisical.
- **Real money:** the plan's §Verification gates (backtest↔live parity, key-handling
  audit, kill-switch <5s, circuit-breaker <1s, load test, Stripe idempotency) **and
  legal counsel**, then set `XBT_ALLOW_LIVE=1`.
