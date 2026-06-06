#!/usr/bin/env bash
# Provision the Bot Engine Droplet on DigitalOcean.
#
# Creates a Droplet running the cloud-init script, allocates a Reserved IP,
# attaches it, and prints the IP so you can publish it to users for exchange
# API key allowlisting.
#
# Requires: doctl authenticated (`doctl auth init`) and an SSH key registered
# with DigitalOcean.
#
# Usage:
#     REPO_URL=https://github.com/you/xbottrader.git \
#     REPO_BRANCH=main \
#     SSH_KEY_FINGERPRINT=<your-key-fp> \
#     REGION=nyc3 \
#     DROPLET_SIZE=s-2vcpu-2gb \
#     ./infra/scripts/provision-bot-engine.sh

set -euo pipefail

: "${SSH_KEY_FINGERPRINT:?must export SSH_KEY_FINGERPRINT (see: doctl compute ssh-key list)}"
REGION="${REGION:-nyc3}"
DROPLET_SIZE="${DROPLET_SIZE:-s-2vcpu-2gb}"
DROPLET_NAME="${DROPLET_NAME:-xbt-bot-engine}"
REPO_URL="${REPO_URL:?must export REPO_URL}"
REPO_BRANCH="${REPO_BRANCH:-main}"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_DATA="$(mktemp)"
trap 'rm -f "$USER_DATA"' EXIT

# Inline REPO_URL / REPO_BRANCH into the cloud-init script.
sed \
    -e "s#REPLACE_ME/xbottrader.git#${REPO_URL#https://github.com/}#" \
    -e "s#REPO_BRANCH=\"\${REPO_BRANCH:-main}\"#REPO_BRANCH=\"$REPO_BRANCH\"#" \
    "$SCRIPT_DIR/do/bot-engine-cloud-init.sh" > "$USER_DATA"

echo "==> creating Droplet $DROPLET_NAME ($DROPLET_SIZE) in $REGION"
DROPLET_ID="$(doctl compute droplet create "$DROPLET_NAME" \
    --region "$REGION" \
    --image ubuntu-24-04-x64 \
    --size "$DROPLET_SIZE" \
    --ssh-keys "$SSH_KEY_FINGERPRINT" \
    --user-data-file "$USER_DATA" \
    --tag-name xbt-bot-engine \
    --wait \
    --format ID --no-header)"
echo "    droplet id: $DROPLET_ID"

DROPLET_IP="$(doctl compute droplet get "$DROPLET_ID" --format PublicIPv4 --no-header)"
echo "    droplet ip: $DROPLET_IP"

echo "==> allocating Reserved IP"
RESERVED_IP="$(doctl compute reserved-ip create --region "$REGION" --format IP --no-header)"
echo "    reserved ip: $RESERVED_IP"

# NOTE: `reserved-ip-action assign` has no --wait in current doctl; poll instead.
doctl compute reserved-ip-action assign "$RESERVED_IP" "$DROPLET_ID"
for _ in $(seq 1 24); do
    [ "$(doctl compute reserved-ip get "$RESERVED_IP" --format DropletID --no-header)" = "$DROPLET_ID" ] && break
    sleep 5
done

echo "==> firewall"
FW_NAME="xbt-bot-engine-fw"
if ! doctl compute firewall list --format Name --no-header | grep -qx "$FW_NAME"; then
    doctl compute firewall create \
        --name "$FW_NAME" \
        --droplet-ids "$DROPLET_ID" \
        --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0 protocol:tcp,ports:5001,address:0.0.0.0/0" \
        --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0 protocol:udp,ports:all,address:0.0.0.0/0 protocol:icmp,address:0.0.0.0/0"
else
    doctl compute firewall add-droplets "$(doctl compute firewall list --format ID,Name --no-header | awk -v n="$FW_NAME" '$2==n {print $1}')" --droplet-ids "$DROPLET_ID"
fi

cat <<EOF

==> done

    Droplet:     $DROPLET_NAME ($DROPLET_ID)
    Public IP:   $DROPLET_IP
    Reserved IP: $RESERVED_IP   ← publish this to users for exchange API key allowlisting

Next:
  1. Wait ~2 minutes for cloud-init to finish provisioning.
  2. SSH in and seed the env file:
       ssh root@$RESERVED_IP
       \$EDITOR /etc/xbt/bot-engine.env   # set GATEWAY_INTERNAL_HMAC_SECRET, XBT_KEK
       systemctl restart xbt-bot-engine
  3. Verify:
       curl http://$RESERVED_IP:5001/healthz
EOF
