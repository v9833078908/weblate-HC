<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Production deployment (VPS)

Deploys this fork (HCGameLoc) as a Docker Compose stack: Weblate (web + Celery),
PostgreSQL and Redis. A reverse proxy in front of it is either the host nginx
(the target server already runs one) or the optional bundled Caddy.

The image is built from this repository, not pulled from Docker Hub. It reuses
`weblate/weblate:2026.8.0.0` for the system dependencies, virtualenv and
`/app/bin/start` entrypoint, then replaces the Python code with:

- `hcgameloc` (the `weblate` package from this repo, with compiled locales),
- `weblate_customization` and `loc_kit_ingest`, installed to `/app/pylib` and
  added to `sys.path` (they are **not** placed in `/app/data`, so the data
  volume never shadows them).

Migrations and `collectstatic` run automatically on every container start.

## Current state

Deployed on 2026-08-10 to `hc-srv15-localizer`, `/srv/hcgameloc` (owner
`dev02`). Live on `http://192.168.0.233/` and, once DNS exists, on
`http://l10n.herocraft.com/` through the host nginx. Weblate itself listens on
`127.0.0.1:8081` only.

Open item: `WEBLATE_EMAIL_HOST` is set to `localhost`, and nothing listens
there, so Weblate cannot send mail. Registration and password reset are
therefore unusable; accounts have to be created by the admin. Point it at a
real SMTP relay and restart the `weblate` service to fix that.

## CPU baseline constraint

The VM exposes a `Common KVM processor` (qemu64, x86-64-v1: no SSE4.2, POPCNT
or SSSE3). numpy 2.4 raised its wheel baseline to x86-64-v2, and
`weblate.fonts.render` imports matplotlib - and therefore numpy - while Django
loads apps. As a result **the stock `weblate/weblate` image cannot start on
this host at all**; `deploy/Dockerfile` pins `numpy==2.3.5`, the last release
with x86-64-v1 wheels for CPython 3.14.

The pin is a workaround, not a fix: every future wheel that adopts the same
baseline will break again. The real fix is one hypervisor setting - give the
VM `cpu mode=host-passthrough` (libvirt) or CPU type `host` (Proxmox) and
reboot it. After that, drop the `NUMPY_VERSION` pin from `deploy/Dockerfile`.

## Target server

`hc-srv15-localizer`, reachable at `192.168.0.233` / `10.39.40.233` over the
office VPN (surveyed 2026-08-10):

| Resource | State |
| --- | --- |
| OS | Debian 12, kernel 6.1, x86_64 |
| CPU / RAM | 4 vCPU, 7.7 GB RAM - 6.9 GB available, 976 MB swap |
| Disk | 117 GB root, 71 GB free (plus ~33 GB reclaimable Docker images) |
| Docker | 28.2.2 with Compose v2.36.2, `docker` group holds `dev01` only |
| In use | compose project `localization` in `/home/dev01/localization`: `cathedral_server` (:8000), `cathedral_pgadmin` (:8080), `cathedral_postgres` (:5432, published on 0.0.0.0) |
| Host nginx | 1.22.1, single vhost `fastapi` as `default_server` on :80 proxying to :8000 |
| Network | outbound HTTPS works (openrouter.ai reachable), public egress 8.228.108.213, no ufw |

Consequences for this stack:

- Ports 80, 8000, 8080 and 5432 are taken. Weblate publishes only
  `127.0.0.1:8081`; PostgreSQL and Redis stay inside the compose network.
- Do not start the `caddy` profile here - the host nginx owns port 80. Use
  `deploy/nginx-l10n.conf` instead.
- The cathedral stack uses ~0.9 GB, so about 5.7 GB RAM is free. Keep
  `WEBLATE_WORKERS=2` / `WEB_WORKERS=2` (already set in
  `environment.example`); that lands around 1.5-2.5 GB for the whole stack.
- Deploy into `/srv/hcgameloc` owned by `dev02`, keeping this service out of
  `dev01`'s home directory. `dev02` needs to be added to the `docker` group
  (`sudo usermod -aG docker dev02`), otherwise every compose command needs
  `sudo`.

## Requirements

- Linux with Docker Engine 24+ and the Compose plugin.
- 4 GB RAM minimum (8 GB recommended: Weblate runs several Celery workers),
  2+ vCPU, 40 GB disk. Repositories, uploaded files and the database all live
  in Docker volumes.
- Outbound HTTPS to `openrouter.ai` (machine translation and loc-kit analysis)
  and to the game repositories.

## First deployment (runbook, already executed on hc-srv15-localizer)

Every step below was run on 2026-08-10; it is kept as the reproducible
procedure for a rebuild or a second server.

```sh
# 1. Docker access for dev02 (currently only dev01 is in the group).
sudo usermod -aG docker dev02 && newgrp docker

# 2. Code.
git clone https://github.com/v9833078908/weblate-HC.git /srv/hcgameloc
cd /srv/hcgameloc/deploy

# 3. Configuration.
cp environment.example .env
$EDITOR .env
#   WEBLATE_SITE_DOMAIN=192.168.0.233   (later l10n.herocraft.com)
#   POSTGRES_PASSWORD / WEBLATE_ADMIN_PASSWORD - real secrets
#   WEBLATE_EMAIL_HOST - reachable SMTP relay
#   WEBLATE_LOC_KIT_PROFILE_OPENROUTER_KEY - only if glossary analysis is wanted
chmod 600 .env

# 4. Build and start (no caddy: host nginx owns port 80).
docker compose up -d --build
docker compose logs -f weblate      # wait for "Starting Weblate"
curl -sI http://127.0.0.1:8081/ | head -1

# 5. Publish through the host nginx.
sudo cp nginx-l10n.conf /etc/nginx/sites-available/l10n
sudo ln -s /etc/nginx/sites-available/l10n /etc/nginx/sites-enabled/l10n
sudo nginx -t && sudo systemctl reload nginx
curl -sI -H 'Host: l10n.herocraft.com' http://192.168.0.233/ | head -1
```

The build takes several minutes and about 3 GB of disk; the first start also
runs migrations and `collectstatic`. The admin account is created from
`WEBLATE_ADMIN_*` on the first start only - change the password after the first
login.

`WEBLATE_EMAIL_HOST` must be set to a reachable SMTP host - Weblate refuses to
start with an empty value.

Nothing else on the server is touched: the `localization` compose project of
`dev01` (cathedral) keeps its ports 8000, 8080 and 5432, and the `fastapi`
vhost stays the nginx `default_server`.

## Serving the site

On `hc-srv15-localizer` the host nginx is the front end:

```sh
sudo cp deploy/nginx-l10n.conf /etc/nginx/sites-available/l10n
sudo ln -s /etc/nginx/sites-available/l10n /etc/nginx/sites-enabled/l10n
sudo nginx -t && sudo systemctl reload nginx
```

The existing `fastapi` vhost stays `default_server`, so the new vhost answers
only for `l10n.herocraft.com`. Before DNS exists, test with an explicit host
header: `curl -H 'Host: l10n.herocraft.com' http://192.168.0.233/`.

`WEBLATE_SITE_DOMAIN` must match what users type in the browser; it is used for
absolute URLs in e-mails and for CSRF. The domain is not needed to bring the
service up: start with `WEBLATE_SITE_DOMAIN=192.168.0.233`, and when DNS for
`l10n.herocraft.com` exists change that value (plus `WEBLATE_ENABLE_HTTPS=1`
once nginx terminates TLS) and run `docker compose up -d`.

On a server without a reverse proxy, start the bundled Caddy instead:
`docker compose --profile caddy up -d`. `CADDY_SITE_ADDRESS` is then the only
TLS switch - `:80` for plain HTTP, or the public hostname for automatic Let's
Encrypt certificates.

## Upgrading

```sh
cd /srv/hcgameloc && git pull
cd deploy && docker compose up -d --build
```

The image build recompiles locales and reinstalls the package; the container
applies pending migrations on start.

## Backups

Two independent layers:

- Weblate's built-in Borg backup (Manage - Backups),
  which stores repositories and the database dump inside `/app/data`.
- Host-level dumps of the volumes:

```sh
docker compose exec -T database pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | zstd > weblate-$(date +%F).sql.zst
docker run --rm -v hcgameloc_weblate-data:/data -v "$PWD":/backup alpine \
    tar czf /backup/weblate-data-$(date +%F).tar.gz -C /data .
```

## Access from outside the office

The office VPS is only reachable over the office VPN. API clients outside that
network (for example `162.55.180.78`) need an explicit path in: either a NAT
rule and firewall allow-list for that address, or publication of
`l10n.herocraft.com` through the company's edge proxy. Weblate itself needs no
extra configuration beyond `WEBLATE_SITE_DOMAIN` matching the public hostname.

## Operator access (isolated VPN gateway)

`deploy/vps.sh` runs the office OpenVPN tunnel **inside a container**
(`deploy/vpn-gateway/`) and exposes the office network as a SOCKS5 proxy on
`127.0.0.1:11080`. The Mac routing table is untouched, so any VPN or proxy
running on the host keeps working, and the tunnel does not need host sudo.

Credentials come from `deploy/.env.local` (gitignored).

```sh
./deploy/vps.sh up            # build and start the gateway container
./deploy/vps.sh status        # gateway state plus an SSH round trip
./deploy/vps.sh survey        # OS, CPU, RAM, disk, docker, listening ports
./deploy/vps.sh ssh <cmd>     # run a command on the VPS
./deploy/vps.sh shell         # interactive shell
./deploy/vps.sh forward 8081  # publish the Weblate port on the Mac
./deploy/vps.sh down          # stop the gateway
```
