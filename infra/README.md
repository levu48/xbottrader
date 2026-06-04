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

Set the secrets and resolved URLs (substitute the values you generated /
got from the previous step's output):

```bash
APP_ID=<from above>
doctl apps update "$APP_ID" --spec .do/app.yaml \
  -e XBT_KEK="$XBT_KEK" \
  -e GATEWAY_INTERNAL_HMAC_SECRET="$GATEWAY_INTERNAL_HMAC_SECRET" \
  -e REDIS_URL="<DO Managed Caching URL or leave for later>" \
  -e BOT_ENGINE_URL="http://<RESERVED_IP>:5001"  # filled in after step 2
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
EOF
ssh root@"$RESERVED_IP" systemctl restart xbt-bot-engine
```

Verify:

```bash
curl http://"$RESERVED_IP":5001/healthz
# → {"ok":true}
```

Then update the Gateway app with the Bot Engine URL:

```bash
doctl apps update "$APP_ID" --spec .do/app.yaml \
  -e BOT_ENGINE_URL="http://$RESERVED_IP:5001"
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

## What's intentionally deferred

- **Managed Redis** (DO Caching) → currently runs on the Droplet. Both
  services need to share Redis once the Gateway consumes `xbt.events` for
  real WS fanout. Add a `redis` cluster in the App Platform UI or via
  `doctl databases create`, then set `REDIS_URL` on both sides.
- **Postgres** → not wired. `BotConfig` / `Order` / `Fill` / audit log are
  in-memory until SQLAlchemy ships.
- **Real auth** → Gateway accepts `x-dev-user`. Lucia/Clerk goes in
  `apps/gateway/src/auth/`.
- **Production `BotLauncher`** → `XBT_LAUNCHER=demo` only. Replace with one
  that reads bot config from Postgres + decrypts the user's exchange API
  key + builds the right adapter (ccxt for live, paper for paper).
- **Doppler / Infisical for secrets** → currently App Platform env vars +
  `/etc/xbt/bot-engine.env` on the Droplet. Migrate when secret rotation
  becomes a real workflow.
- **CD for the Droplet** → manual via `deploy-bot-engine.sh`. Wire into
  GHA later.
