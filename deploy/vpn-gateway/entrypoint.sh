#!/bin/bash
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Expects /vpn/profile.ovpn, /vpn/auth and /vpn/askpass to be mounted.
# Brings up the tunnel, then serves the office network over SOCKS5:1080.

set -euo pipefail

# --mssfix is required: without it a full-size TCP segment inside the tunnel
# (any HTTP POST, git push or file upload) is silently dropped and the request
# hangs, while small GET requests keep working.
openvpn \
    --config /vpn/profile.ovpn \
    --auth-user-pass /vpn/auth \
    --askpass /vpn/askpass \
    --auth-nocache \
    --data-ciphers AES-256-GCM:AES-128-GCM:AES-256-CBC \
    --mssfix "${OPENVPN_MSSFIX:-1300}" \
    --verb "${OPENVPN_VERB:-3}" &
openvpn_pid=$!

for _ in $(seq 1 60); do
    if ip -4 addr show tun0 2> /dev/null | grep -q "inet "; then
        break
    fi
    if ! kill -0 "$openvpn_pid" 2> /dev/null; then
        echo "openvpn exited before the tunnel came up" >&2
        exit 1
    fi
    sleep 1
done

if ! ip -4 addr show tun0 2> /dev/null | grep -q "inet "; then
    echo "tun0 has no address after 60s" >&2
    exit 1
fi

echo "tunnel up:"
ip -4 addr show tun0 | sed -n 's/^ *inet /  /p'
ip -4 route | sed 's/^/  /'

cat > /etc/sockd.conf << 'EOF'
logoutput: stderr
internal: 0.0.0.0 port = 1080
external: tun0
socksmethod: none
clientmethod: none
user.notprivileged: nobody
client pass {
    from: 0.0.0.0/0 to: 0.0.0.0/0
}
socks pass {
    from: 0.0.0.0/0 to: 0.0.0.0/0
}
EOF

echo "SOCKS5 listening on 1080"
sockd -f /etc/sockd.conf &
sockd_pid=$!

trap 'kill "$openvpn_pid" "$sockd_pid" 2>/dev/null' TERM INT
wait -n "$openvpn_pid" "$sockd_pid"
