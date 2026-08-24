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
#   ./deploy/vps.sh deploy           # push HEAD and roll it out (see below)
#   ./deploy/vps.sh deploy --build   # same, but always rebuild the image
#   ./deploy/vps.sh up               # build and start the gateway container
#   ./deploy/vps.sh status           # gateway state and SSH reachability
#   ./deploy/vps.sh survey           # OS/CPU/RAM/disk/docker state of the VPS
#   ./deploy/vps.sh ssh <command>    # run a command on the VPS
#   ./deploy/vps.sh shell            # interactive shell
#   ./deploy/vps.sh forward <port>   # publish a VPS port on 127.0.0.1:<port>
#   ./deploy/vps.sh down             # stop the gateway
#
# `deploy` picks the cheapest action that can carry the change: it compares the
# commit running on the server with the one being deployed and only rebuilds
# the image when a file that ends up inside it changed. Documentation, plans
# and tests never trigger a rebuild. The work runs detached on the VPS, so a
# dropped tunnel cannot leave a half-finished deploy behind.
#
# Credentials come from deploy/.env.local (gitignored).

set -euo pipefail

cd "$(dirname "$0")"
# shellcheck disable=SC1091
. ./.env.local

CONTAINER=hc-vpn-gw
IMAGE=hc-vpn-gw
SOCKS_PORT=${SOCKS_PORT:-11080}
TUN_MTU=${TUN_MTU:-1100}
SECRETS_DIR="${TMPDIR:-/tmp}/hc-vpn-gw-secrets"
REPO_DIR=/srv/hcgameloc
WEBLATE_CONTAINER=hcgameloc-weblate-1
REMOTE_LOG=/tmp/hc-deploy.log

# Files baked into the image by deploy/Dockerfile. A commit that touches none
# of them cannot change the image, so the deploy skips the build.
IMAGE_PATHS='^(weblate/|weblate_customization/|loc_kit_ingest/|client/|scripts/|pyproject\.toml|MANIFEST\.in|deploy/Dockerfile|\.dockerignore$)'

proxy_command="nc -X 5 -x 127.0.0.1:$SOCKS_PORT %h %p"
ssh_opts=(
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=15
    -o "ProxyCommand=$proxy_command"
    -p "$VPS_SSH_PORT"
)

# The SOCKS port answering is the property that matters, and probing it keeps
# the local Docker daemon off the hot path: `docker inspect` blocks for a
# minute or more whenever another project on this workstation is mid-build.
gateway_running() {
    nc -z 127.0.0.1 "$SOCKS_PORT" > /dev/null 2>&1
}

gateway_up() {
    if gateway_running; then
        echo "Gateway already running (SOCKS5 on 127.0.0.1:$SOCKS_PORT)."
        return 0
    fi

    docker build -q -t "$IMAGE" vpn-gateway > /dev/null

    # Secrets are copied INTO the container's own filesystem instead of being
    # bind-mounted from $TMPDIR: macOS cleans /var/folders/.../T out from
    # under a long-lived container, which used to leave `--restart
    # unless-stopped` crash-looping on an empty mount. Inside the container
    # they survive every restart, and the host copy lives only for seconds.
    rm -rf "$SECRETS_DIR"
    mkdir -p "$SECRETS_DIR"
    chmod 700 "$SECRETS_DIR"
    cp "$VPN_PROFILE" "$SECRETS_DIR/profile.ovpn"
    printf '%s\n%s\n' "$VPN_USER" "$VPN_PASSWORD" > "$SECRETS_DIR/auth"
    printf '%s\n' "$VPN_KEY_PASSPHRASE" > "$SECRETS_DIR/askpass"
    chmod 600 "$SECRETS_DIR"/*

    docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    docker create \
        --name "$CONTAINER" \
        --restart unless-stopped \
        --cap-add NET_ADMIN \
        --device /dev/net/tun \
        -p "127.0.0.1:$SOCKS_PORT:1080" \
        -e "TUN_MTU=$TUN_MTU" \
        "$IMAGE" > /dev/null
    docker cp -q "$SECRETS_DIR/." "$CONTAINER:/vpn"
    rm -rf "$SECRETS_DIR"
    docker start "$CONTAINER" > /dev/null

    for _ in $(seq 1 45); do
        if docker logs "$CONTAINER" 2>&1 | grep -q "SOCKS5 listening"; then
            echo "Gateway up: SOCKS5 on 127.0.0.1:$SOCKS_PORT (tun0 MTU $TUN_MTU)"
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

# Runs a local script on the VPS as $VPS_USER, detached, logging to $REMOTE_LOG.
# Detached because an image build outlives the tunnel's re-key interval.
start_remote_script() {
    local payload
    payload=$(base64 < "$1" | tr -d '\n')
    ssh_retry "echo $payload | base64 -d > /tmp/hc-deploy.sh \
        && setsid nohup bash /tmp/hc-deploy.sh > $REMOTE_LOG 2>&1 < /dev/null & sleep 2; echo started"
}

# Streams $REMOTE_LOG until the remote script reports a verdict.
follow_remote_script() {
    local seen=0 total waited=0 chunk
    while [ "$waited" -lt 1800 ]; do
        total=$(ssh_retry "stat -c %s $REMOTE_LOG 2>/dev/null || echo 0")
        if [ "$total" -gt "$seen" ]; then
            chunk=$(ssh_retry "tail -c +$((seen + 1)) $REMOTE_LOG")
            printf '%s\n' "$chunk"
            seen=$total
            case $chunk in
            *DEPLOY-OK*) return 0 ;;
            *DEPLOY-FAILED*) return 1 ;;
            esac
        fi
        sleep 5
        waited=$((waited + 5))
    done
    >&2 echo "Timed out waiting for the deploy; check $REMOTE_LOG on the VPS."
    return 1
}

reload_nginx_vhost() {
    local tmp
    tmp=$(mktemp)
    cat > "$tmp" << EOS
set -eu
cp $REPO_DIR/deploy/nginx-l10n.conf /etc/nginx/sites-available/l10n
nginx -t
systemctl reload nginx
echo "nginx: vhost reloaded"
EOS
    run_root_script "$tmp"
    rm -f "$tmp"
}

deploy_stack() {
    local force=0
    [ "${1:-}" = "--build" ] && force=1

    local root
    root=$(cd .. && pwd)

    if [ -n "$(git -C "$root" status --porcelain)" ]; then
        >&2 echo "Working tree is dirty; commit or stash first:"
        >&2 git -C "$root" status --short
        return 1
    fi

    local target
    target=$(git -C "$root" rev-parse HEAD)
    echo "Pushing $(git -C "$root" rev-parse --short HEAD) to origin/main..."
    git -C "$root" push -q origin "HEAD:main"

    require_gateway
    local deployed
    deployed=$(ssh_retry "git -C $REPO_DIR rev-parse HEAD")

    # The server can be ahead of what this checkout knows about when someone
    # else deployed in between; fetch before diffing against it.
    if ! git -C "$root" cat-file -e "${deployed}^{commit}" 2> /dev/null; then
        git -C "$root" fetch -q origin
    fi

    local changed action=none nginx=0
    if git -C "$root" cat-file -e "${deployed}^{commit}" 2> /dev/null; then
        changed=$(git -C "$root" diff --name-only "$deployed" "$target")
    else
        echo "Server commit $deployed is unknown here; rebuilding to be safe."
        changed=""
        force=1
    fi

    if [ "$force" = 1 ] || printf '%s\n' "$changed" | grep -qE "$IMAGE_PATHS"; then
        action=build
    elif printf '%s\n' "$changed" | grep -qx 'deploy/docker-compose.yml'; then
        action=compose
    fi
    printf '%s\n' "$changed" | grep -qx 'deploy/nginx-l10n.conf' && nginx=1

    if [ "$deployed" = "$target" ] && [ "$force" = 0 ]; then
        echo "Server is already on $(git -C "$root" rev-parse --short "$target"); nothing to deploy."
        return 0
    fi

    echo "Deploying $(git -C "$root" rev-parse --short "$target") over $(git -C "$root" rev-parse --short "$deployed" 2> /dev/null || echo "$deployed")"
    printf '%s\n' "$changed" | sed 's/^/  ~ /' | head -20
    echo "Action: $action$([ "$nginx" = 1 ] && echo " + nginx reload")"

    local tmp
    tmp=$(mktemp)
    cat > "$tmp" << EOS
set -euo pipefail
cd $REPO_DIR
git fetch -q origin
git reset -q --hard $target
export GIT_SHA=$target
cd deploy
started=\$(date +%s)
case $action in
build) docker compose up -d --build weblate ;;
compose) docker compose up -d ;;
none) docker compose up -d weblate ;;
esac
echo "compose: \$((\$(date +%s) - started))s"
for _ in \$(seq 1 120); do
    health=\$(docker inspect -f '{{.State.Health.Status}}' $WEBLATE_CONTAINER 2> /dev/null || echo missing)
    [ "\$health" = healthy ] && break
    sleep 5
done
echo "health: \$health after \$((\$(date +%s) - started))s"
revision=\$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' hcgameloc:latest 2> /dev/null || echo none)
echo "checkout: $target"
# Only a rebuild is expected to move the image; for the other actions the
# label legitimately lags behind the checkout.
if [ "$action" = build ] && [ "\$revision" != "$target" ]; then
    echo "image revision: \$revision (MISMATCH, expected $target)"
    stale=1
else
    echo "image revision: \$revision"
    stale=0
fi
# Docker reports the container healthy as soon as its own probe passes, which
# can happen while granian workers are still importing Django, so a single
# request here races the app and reports a false DEPLOY-FAILED. Retry until it
# serves, and keep the last code so a genuine failure still fails.
login=000
for _ in \$(seq 1 24); do
    login=\$(curl -s -o /dev/null -m 20 -w '%{http_code}' "http://127.0.0.1:\${WEBLATE_LOCAL_PORT:-8081}/accounts/login/" || echo 000)
    [ "\$login" = 200 ] && break
    sleep 5
done
echo "login page: \$login"
if [ "\$health" = healthy ] && [ "\$login" = 200 ] && [ "\$stale" = 0 ]; then
    echo DEPLOY-OK
else
    docker compose logs --tail 30 weblate
    echo DEPLOY-FAILED
fi
EOS
    start_remote_script "$tmp" > /dev/null
    rm -f "$tmp"

    local verdict=0
    follow_remote_script || verdict=1
    [ "$nginx" = 1 ] && reload_nginx_vhost
    return "$verdict"
}

case ${1:-status} in
deploy)
    shift
    deploy_stack "$@"
    ;;
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
        mtu=$(docker exec "$CONTAINER" cat /sys/class/net/tun0/mtu 2> /dev/null || echo "?")
        echo "Gateway: running (SOCKS5 127.0.0.1:$SOCKS_PORT, tun0 MTU $mtu)"
        if nc -X 5 -x "127.0.0.1:$SOCKS_PORT" -z -w 5 "$VPS_HOST" "$VPS_SSH_PORT" > /dev/null 2>&1; then
            echo "VPS: $VPS_HOST:$VPS_SSH_PORT reachable through the tunnel"
        else
            echo "VPS: $VPS_HOST:$VPS_SSH_PORT NOT reachable through the tunnel"
        fi
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
    >&2 echo "usage: $0 {deploy [--build]|up|down|status|survey|ssh <cmd>|root <script>|shell|forward <port>}"
    exit 2
    ;;
esac
