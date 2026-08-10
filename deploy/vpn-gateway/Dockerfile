# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Isolated office-VPN gateway: OpenVPN runs inside the container network
# namespace, so the host routing table and any VPN or proxy running on the Mac
# stay untouched. The office network is exposed as a SOCKS5 proxy on port 1080.

FROM alpine:3.22

RUN apk add --no-cache openvpn dante-server bash

COPY --chmod=0755 entrypoint.sh /entrypoint.sh

EXPOSE 1080

ENTRYPOINT ["/entrypoint.sh"]
