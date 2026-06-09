#!/usr/bin/env bash
# cloud-init user-data script for the Bot Engine Droplet.
#
# Bootstraps Docker, clones the repo, and brings the stack up. The env file
# /etc/xbt/bot-engine.env is provisioned out-of-band after first boot
# (see infra/scripts/deploy-bot-engine.sh).
#
# Pass the repo URL and branch via env interpolation when invoking, OR edit
# the two variables below before pasting into Droplet user-data.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/REPLACE_ME/xbottrader.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

# ---- Docker + git ----
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg git

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# ---- Repo ----
mkdir -p /opt/xbt
cd /opt/xbt
if [ ! -d xbottrader ]; then
    git clone --depth=1 --branch "$REPO_BRANCH" "$REPO_URL" xbottrader
fi

# ---- Placeholder env file (operator overwrites with real secrets) ----
mkdir -p /etc/xbt
if [ ! -f /etc/xbt/bot-engine.env ]; then
    cat > /etc/xbt/bot-engine.env <<'EOF'
# Filled in by the operator on first deploy. See infra/README.md.
GATEWAY_INTERNAL_HMAC_SECRET=CHANGE_ME
XBT_KEK=CHANGE_ME

# --- runtime mode (compose reads these via --env-file) ---
# Defaults (when unset) are demo / paper / sqlite. To go to real venues:
#   XBT_LAUNCHER=prod
#   XBT_ALLOW_LIVE=1                                          # the real-money switch
#   DATABASE_URL=postgresql+asyncpg://USER:PW@HOST:25060/db
# Then: systemctl restart xbt-bot-engine
EOF
    chmod 600 /etc/xbt/bot-engine.env
fi

# ---- Stack ----
# --env-file feeds XBT_LAUNCHER/XBT_ALLOW_LIVE/DATABASE_URL into compose interpolation.
cd /opt/xbt/xbottrader
docker compose --env-file /etc/xbt/bot-engine.env -f infra/do/bot-engine-compose.yml pull || true
docker compose --env-file /etc/xbt/bot-engine.env -f infra/do/bot-engine-compose.yml up -d --build

# ---- systemd unit so the stack survives reboots ----
cat > /etc/systemd/system/xbt-bot-engine.service <<'EOF'
[Unit]
Description=xbottrader Bot Engine compose stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/xbt/xbottrader
ExecStart=/usr/bin/docker compose --env-file /etc/xbt/bot-engine.env -f infra/do/bot-engine-compose.yml up -d
ExecStop=/usr/bin/docker compose --env-file /etc/xbt/bot-engine.env -f infra/do/bot-engine-compose.yml down

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable xbt-bot-engine.service
