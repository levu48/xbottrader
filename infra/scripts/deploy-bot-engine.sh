#!/usr/bin/env bash
# Pull latest code on the Bot Engine Droplet and bounce the stack.
#
# Usage:
#     DROPLET_IP=1.2.3.4 ./infra/scripts/deploy-bot-engine.sh
#
# Assumes you have SSH access as root (DO default). Use a non-root user with
# sudoers once you're past the staging phase.

set -euo pipefail

: "${DROPLET_IP:?must export DROPLET_IP}"
BRANCH="${BRANCH:-main}"

ssh -o StrictHostKeyChecking=accept-new "root@${DROPLET_IP}" bash -s <<EOF
set -euo pipefail
cd /opt/xbt/xbottrader
git fetch origin "${BRANCH}"
git reset --hard "origin/${BRANCH}"
# --env-file lets the compose file read XBT_LAUNCHER/XBT_ALLOW_LIVE/DATABASE_URL
# from the operator's env file (with safe demo/0/sqlite defaults when unset).
docker compose --env-file /etc/xbt/bot-engine.env -f infra/do/bot-engine-compose.yml up -d --build
docker compose --env-file /etc/xbt/bot-engine.env -f infra/do/bot-engine-compose.yml ps
EOF
