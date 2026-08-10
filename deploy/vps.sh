#!/usr/bin/env bash
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Access to the office VPS through an isolated VPN gateway container.
#
# The office OpenVPN tunnel runs inside a Docker container, so the Mac routing
# table is never touched and any VPN or proxy running on the host (Happ, etc.)
# keeps working. The office network is reachable through a SOCKS5 proxy
# published on 127.0.0.1:$SOCKS_PORT.
#
#   ./deploy/vps.sh up               # build and start the gateway container
#   ./deploy/vps.sh status           # gateway state and SSH reachability
#   ./deploy/vps.sh survey           # OS/CPU/RAM/disk/docker state of the VPS
#   ./deploy/vps.sh ssh <command>    # run a command on the VPS
#   ./deploy/vps.sh shell            # interactive shell
#   ./deploy/vps.sh forward <port>   # publish a VPS port on 127.0.0.1:<port>
#   ./deploy/vps.sh down             # stop the gateway
#
# Credentials come from deploy/.env.local (gitignored).

set -euo pipefail

cd "$(dirname "$0")"
# shellcheck disable=SC1091
. ./.env.local

CONTAINER=hc-vpn-gw
IMAGE=hc-vpn-gw
SOCKS_PORT=${SOCKS_PORT:-11080}
SECRETS_DIR="${TMPDIR:-/tmp}/hc-vpn-gw-secrets"

proxy_command="nc -X 5 -x 127.0.0.1:$SOCKS_PORT %h %p"
ssh_opts=(
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -o "ProxyCommand=$proxy_command"
    -p "$VPS_SSH_PORT"
)

gateway_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2> /dev/null)" = "true" ]
}

gateway_up() {
    if gateway_running; then
        echo "Gateway already running (SOCKS5 on 127.0.0.1:$SOCKS_PORT)."
        return 0
    fi

    docker build -q -t "$IMAGE" vpn-gateway > /dev/null

    rm -rf "$SECRETS_DIR"
    mkdir -p "$SECRETS_DIR"
    chmod 700 "$SECRETS_DIR"
    cp "$VPN_PROFILE" "$SECRETS_DIR/profile.ovpn"
    printf '%s\n%s\n' "$VPN_USER" "$VPN_PASSWORD" > "$SECRETS_DIR/auth"
    printf '%s\n' "$VPN_KEY_PASSPHRASE" > "$SECRETS_DIR/askpass"
    chmod 600 "$SECRETS_DIR"/*

    docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    docker run -d \
        --name "$CONTAINER" \
        --restart unless-stopped \
        --cap-add NET_ADMIN \
        --device /dev/net/tun \
        -p "127.0.0.1:$SOCKS_PORT:1080" \
        -v "$SECRETS_DIR":/vpn:ro \
        "$IMAGE" > /dev/null

    for _ in $(seq 1 45); do
        if docker logs "$CONTAINER" 2>&1 | grep -q "SOCKS5 listening"; then
            echo "Gateway up: SOCKS5 on 127.0.0.1:$SOCKS_PORT"
            return 0
        fi
        if ! gateway_running; then
            docker logs "$CONTAINER" 2>&1 | tail -20
            return 1
        fi
        sleep 2
    done
    docker logs "$CONTAINER" 2>&1 | tail -20
    return 1
}

require_gateway() {
    gateway_running || gateway_up
}

# The tunnel re-keys every so often and OpenVPN needs a few seconds to come
# back, so a dropped SSH connection is retried instead of failing the command.
ssh_retry() {
    local attempt
    for attempt in 1 2 3; do
        if sshpass -p "$VPS_PASSWORD" ssh -n "${ssh_opts[@]}" "$VPS_USER@$VPS_HOST" "$@"; then
            return 0
        fi
        [ "$attempt" = 3 ] && return 1
        sleep 10
    done
}

run_ssh() {
    require_gateway
    ssh_retry "$@"
}

# Runs a local script on the VPS as root; avoids quoting the payload twice.
run_root_script() {
    require_gateway
    local payload
    payload=$(base64 < "$1" | tr -d '\n')
    ssh_retry "echo '$VPS_PASSWORD' | sudo -S -v 2>/dev/null; echo $payload | base64 -d | sudo bash"
}

case ${1:-status} in
up)
    gateway_up
    ;;
down)
    docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    rm -rf "$SECRETS_DIR"
    echo "Gateway stopped."
    ;;
status)
    if gateway_running; then
        echo "Gateway: running (SOCKS5 127.0.0.1:$SOCKS_PORT)"
    else
        echo "Gateway: stopped"
    fi
    # shellcheck disable=SC2016 # expanded on the VPS, not locally
    run_ssh 'echo "SSH: $(whoami)@$(hostname) up $(uptime -p)"'
    ;;
survey)
    tmp=$(mktemp)
    cat > "$tmp" << 'EOS'
echo "== os"; . /etc/os-release; echo "$PRETTY_NAME"; uname -srm
echo "== cpu"; nproc
echo "== memory"; free -m
echo "== disk"; df -hT -x tmpfs -x devtmpfs
echo "== docker"; docker --version; docker compose version | head -1
echo "== containers"; docker ps -a --format "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"
echo "== container-memory"; docker stats --no-stream --format "{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}"
echo "== listeners"; ss -lntp | grep LISTEN
echo "== nginx"; ls -1 /etc/nginx/sites-enabled/ 2>/dev/null
EOS
    run_root_script "$tmp"
    rm -f "$tmp"
    ;;
ssh)
    shift
    run_ssh "$@"
    ;;
root)
    shift
    run_root_script "$1"
    ;;
shell)
    require_gateway
    exec sshpass -p "$VPS_PASSWORD" ssh "${ssh_opts[@]}" -t "$VPS_USER@$VPS_HOST"
    ;;
forward)
    require_gateway
    port=${2:?"usage: $0 forward <port>"}
    echo "Forwarding 127.0.0.1:$port -> $VPS_HOST:$port (Ctrl-C to stop)"
    exec sshpass -p "$VPS_PASSWORD" ssh "${ssh_opts[@]}" -N \
        -L "127.0.0.1:$port:127.0.0.1:$port" "$VPS_USER@$VPS_HOST"
    ;;
*)
    >&2 echo "usage: $0 {up|down|status|survey|ssh <cmd>|root <script>|shell|forward <port>}"
    exit 2
    ;;
esac
