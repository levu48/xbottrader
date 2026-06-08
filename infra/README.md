# Deployment — DigitalOcean

What this gets you: a real DO deploy of the Gateway (App Platform), the Web
frontend (App Platform), and a Bot Engine Droplet with a Reserved IP. End
state — an HMAC-signed curl against the Bot Engine starts a paper-traded
DCA bot and you see fills emitted over Redis Streams.

What it does NOT get you: real users, real exchanges, real money. There is
no Postgres, no Stripe, no Lucia/Clerk auth, no production `BotLauncher`.
Auth on the Bot Engine is HMAC only; the Gateway's user-facing auth is a
placeholder header. **Do not expose this to actual users.**

---

## Prerequisites

- [`doctl`](https://docs.digitalocean.com/reference/doctl/how-to/install/) installed and authenticated (`doctl auth init`).
- An SSH key registered with DigitalOcean (`doctl compute ssh-key list`).
- This repo pushed to GitHub (App Platform needs a git source).
- Two strong random secrets generated:
  ```bash
  # 32-byte master key for envelope encryption
  XBT_KEK=$(node -e "console.log(require('crypto').randomBytes(32).toString('base64'))")
  # HMAC secret shared by Gateway and Bot Engine
  GATEWAY_INTERNAL_HMAC_SECRET=$(openssl rand -hex 32)
  ```
  Keep both somewhere safe — they won't be regenerated.

---

## 1. App Platform: Gateway + Web

Edit `.do/app.yaml` and replace `REPLACE_ME/xbottrader` with your GitHub
`<owner>/<repo>`. Then:

```bash
doctl apps create --spec .do/app.yaml
# → returns an APP_ID; save it.
```

Set the secrets and resolved URLs. `doctl apps update` has **no `-e` flag** —
App Platform env vars/secrets are applied through the spec or the UI. Two ways:

**UI (simplest for secrets):** in the App Platform console, open each component
(gateway / ai-engine) → Settings → Environment Variables → add the values and
check **Encrypt**. Save to redeploy.

**CLI (no plaintext in git):** apply from an *untracked* copy of the spec so the
real values never get committed:

```bash
APP_ID=<from above>
cp .do/app.yaml /tmp/app.run.yaml
# Edit /tmp/app.run.yaml and fill in the SECRET `value:` fields (XBT_KEK,
# GATEWAY_INTERNAL_HMAC_SECRET, DATABASE_URL, REDIS_URL, ANTHROPIC_API_KEY,
# XBT_ALPACA_DATA_KEY/SECRET) and the URL placeholders (BOT_ENGINE_URL →
# http://<RESERVED_IP>:5001, filled in after step 2). DO encrypts SECRET values
# server-side on apply.
doctl apps update "$APP_ID" --spec /tmp/app.run.yaml
rm /tmp/app.run.yaml
```

`REDIS_URL` is required by the Gateway at boot. For first deploy you can
point it at a temporary Upstash free tier; eventually move it to DO
Managed Caching once Bot Engine connects to the same Redis.

When the deploy finishes, App Platform prints two URLs:

- Gateway: `https://gateway-xxxx.ondigitalocean.app`
- Web:     `https://web-xxxx.ondigitalocean.app`

Verify:

```bash
curl https://gateway-xxxx.ondigitalocean.app/healthz
# → {"ok":true}
```

---

## 2. Droplet: Bot Engine

```bash
SSH_KEY_FINGERPRINT=$(doctl compute ssh-key list --format FingerPrint --no-header | head -1)

REPO_URL=https://github.com/<owner>/xbottrader.git \
REPO_BRANCH=main \
SSH_KEY_FINGERPRINT="$SSH_KEY_FINGERPRINT" \
REGION=nyc3 \
DROPLET_SIZE=s-2vcpu-2gb \
./infra/scripts/provision-bot-engine.sh
```

The script:
1. Creates the Droplet with cloud-init that installs Docker, clones the
   repo, builds the Bot Engine image, and starts the compose stack
   (`bot-engine` + local `redis`).
2. Allocates a Reserved IP and attaches it.
3. Opens a firewall: SSH (22) and Bot Engine API (5001) from anywhere.

When it finishes, it prints the Reserved IP. **That's the IP you publish
to users for exchange API key allowlisting** (per the plan).

Seed the env file:

```bash
RESERVED_IP=<from previous output>
ssh root@"$RESERVED_IP" tee /etc/xbt/bot-engine.env >/dev/null <<EOF
GATEWAY_INTERNAL_HMAC_SECRET=$GATEWAY_INTERNAL_HMAC_SECRET
XBT_KEK=$XBT_KEK
# Equities only: needed for the dashboard OHLCV chart and the live equity bar
# feed (the Bot Engine fetches AAPL bars from Alpaca's data API). Crypto needs
# no keys. NOTE: setting these on App Platform does NOT reach the Bot Engine —
# in .do/app.yaml they're only on the ai-engine service (backtests). The Bot
# Engine reads them only from this file.
XBT_ALPACA_DATA_KEY=$XBT_ALPACA_DATA_KEY
XBT_ALPACA_DATA_SECRET=$XBT_ALPACA_DATA_SECRET
EOF
ssh root@"$RESERVED_IP" systemctl restart xbt-bot-engine
```

Verify:

```bash
curl http://"$RESERVED_IP":5001/healthz
# → {"ok":true}
```

Then set the Gateway's `BOT_ENGINE_URL` to `http://$RESERVED_IP:5001` — either in
the App Platform UI (gateway component → Environment Variables) or by editing the
untracked spec copy and re-applying:

```bash
cp .do/app.yaml /tmp/app.run.yaml
# set BOT_ENGINE_URL's value to http://<RESERVED_IP>:5001 in /tmp/app.run.yaml
doctl apps update "$APP_ID" --spec /tmp/app.run.yaml
rm /tmp/app.run.yaml
```

---

## 3. Smoke-test the loop

Sign a request from your laptop and start a demo paper bot:

```bash
RESERVED_IP=<your reserved ip>
SECRET="$GATEWAY_INTERNAL_HMAC_SECRET"
USER=u-demo
BODY='{"strategy":{"strategy_type":"dca","symbol":"BTC/USDT","quote_amount":"50","interval_minutes":1}}'
PATH=/bots/demo-1/start
TS=$(date +%s)
BODY_HASH=$(printf '%s' "$BODY" | shasum -a 256 | cut -d' ' -f1)
SIG=$(printf 'POST\n%s\n%s\n%s\n%s' "$PATH" "$TS" "$USER" "$BODY_HASH" \
      | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $2}')

curl -s -X POST "http://$RESERVED_IP:5001$PATH" \
     -H "x-xbt-user: $USER" \
     -H "x-xbt-ts: $TS" \
     -H "x-xbt-sig: $SIG" \
     -H "content-type: application/json" \
     -d "$BODY"
# → {"bot_id":"demo-1","state":"starting","last_error":null}
```

Watch fills land in Redis on the Droplet:

```bash
ssh root@"$RESERVED_IP" docker exec -i \
    "$(ssh root@$RESERVED_IP docker ps --filter name=redis --format '{{.ID}}')" \
    redis-cli XLEN xbt.events
# the count should rise every ~5 seconds
```

Tail the Bot Engine logs:

```bash
ssh root@"$RESERVED_IP" docker logs -f xbottrader-bot-engine-1
```

---

## 4. Updates

Push to `main`. App Platform autodeploys Gateway + Web. For the Droplet:

```bash
DROPLET_IP=<reserved ip> ./infra/scripts/deploy-bot-engine.sh
```

That `git pull`s and rebuilds the compose stack.

---

## Gotchas

- **Binance geo-blocks datacenter / US IPs.** `api.binance.com` returns HTTP
  451 (or just times out) from many cloud hosts, including US-region DO
  Droplets. ccxt's `fetch_ohlcv` then throws, so the dashboard OHLCV chart shows
  "Market data unavailable…" for `binance` symbols — **and Binance paper-marks /
  live orders fail the same way** (the chart is just the first place it surfaces;
  the Reserved IP stabilizes key *allowlisting* but does nothing about Binance's
  geo-restrictions). Confirm from the Droplet:
  ```bash
  curl -s -o /dev/null -w "binance: %{http_code}\n" \
    "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2"
  # 451 / hang → blocked. Compare with coinbase/kraken (usually 200 from US).
  ```
  Mitigations: use a US-reachable venue (Coinbase, Kraken, or `binanceus` — note
  `BTC/USD` vs `BTC/USDT` and separate keys), source crypto *market data* from a
  reachable venue independent of the trade venue, or host the Droplet in a
  Binance-friendly region (Amsterdam/Frankfurt/Singapore — but that changes the
  Reserved IP). Equities (Alpaca) are unaffected.

---

## What's intentionally deferred

- **Managed Redis** (DO Caching) → currently runs on the Droplet. Both
  services need to share Redis once the Gateway consumes `xbt.events` for
  real WS fanout. Add a `redis` cluster in the App Platform UI or via
  `doctl databases create`, then set `REDIS_URL` on both sides.
- **Postgres** → schema and Alembic migration exist; staging still runs
  SQLite on the Droplet volume. For prod set
  `DATABASE_URL=postgresql+asyncpg://…` and apply the schema with
  `alembic upgrade head` (the boot-time SQLite bootstrap is a no-op for PG).
- **Real auth** → Gateway accepts `x-dev-user`. Lucia/Clerk goes in
  `apps/gateway/src/auth/`.
- **Production `BotLauncher`** → built (`XBT_LAUNCHER=prod`): ensures the bot
  row in Postgres, builds a paper adapter (real prices via public ccxt) or a
  live ccxt adapter, with market data from a polling `CcxtBarSource`. The live
  client decrypts the user's key (envelope passed in the signed start request,
  `XBT_KEK` required). **Real-money orders are gated behind `XBT_ALLOW_LIVE=1`**
  — keep it off until the plan's pre-real-money verification passes (kill-switch
  <5s, key-handling audit, backtest↔live parity). Still deferred: the Gateway
  *storing/fetching* user keys (ships with auth) and per-venue ccxt precision
  normalization.
- **Doppler / Infisical for secrets** → currently App Platform env vars +
  `/etc/xbt/bot-engine.env` on the Droplet. Migrate when secret rotation
  becomes a real workflow.
- **CD for the Droplet** → manual via `deploy-bot-engine.sh`. Wire into
  GHA later.
