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
`dev02`). Two entry points, both verified with a real login POST:

| Audience | URL | Path |
| --- | --- | --- |
| Public internet | `https://l10n.herocraft.com/` | edge `195.135.212.209` (TLS, Let's Encrypt) -> office proxy `192.168.0.210` -> host nginx `192.168.0.233:80` -> `127.0.0.1:8081` |
| Office network | `http://192.168.0.233/` | host nginx -> `127.0.0.1:8081` |

Inside the office the public name is useless: office DNS answers with the
public address `195.135.212.209` and the perimeter has no hairpin NAT, so the
connection never comes back. Verified from the server itself and through the
VPN gateway - both time out. Office users therefore need the IP.

Open items:

- `WEBLATE_EMAIL_HOST` is set to `localhost`, and nothing listens there, so
  Weblate cannot send mail. Registration and password reset are unusable;
  accounts have to be created by the admin. Point it at a real SMTP relay and
  restart the `weblate` service to fix that.
- `WEBLATE_ENABLE_HTTPS` is still `0`, so session and CSRF cookies are set
  without the `Secure` flag and HSTS is off. Turning it on also turns on
  `SECURE_SSL_REDIRECT` and secure cookies, which would cut off the plain-HTTP
  office entry point entirely. Flip it only together with one of: hairpin NAT
  for `l10n.herocraft.com`, or split-horizon DNS plus TLS on the host nginx.
- Every public visitor reaches Weblate as `192.168.0.1`: the edge sends
  `X-Forwarded-For: 192.168.0.1` instead of the real client address, and
  `IP_PROXY_OFFSET` is `0`, so that is the address used for rate limiting and
  the audit log. Ask the edge and `192.168.0.210` to append the real client IP.

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
only for `l10n.herocraft.com`, `192.168.0.233` and `10.39.40.233`.

TLS is terminated by the edge, which reaches this server over plain HTTP. The
vhost therefore passes the incoming `X-Forwarded-Proto` through instead of
overwriting it with `$scheme`; with `$scheme` Django sees `http` for a request
the browser made over `https`, and every POST fails CSRF with "Origin checking
failed". Requests are logged to `/var/log/nginx/l10n.log` with the forwarded
scheme and address, which is the quickest way to see what the edge sends.

`WEBLATE_SITE_DOMAIN` is `l10n.herocraft.com`: it is used for absolute URLs in
e-mails and notifications, not for routing, so the office entry point keeps
working over the IP. `WEBLATE_ENABLE_HTTPS` stays `0` while that plain-HTTP
entry point exists - see "Current state".

On a server without a reverse proxy, start the bundled Caddy instead:
`docker compose --profile caddy up -d`. `CADDY_SITE_ADDRESS` is then the only
TLS switch - `:80` for plain HTTP, or the public hostname for automatic Let's
Encrypt certificates.

## Privacy

The site is published on the internet, so nothing may be readable without an
account:

- `WEBLATE_REQUIRE_LOGIN=1` - every page and every API call needs a session or
  a token. An anonymous visitor only ever sees the sign-in page.
- `WEBLATE_REGISTRATION_OPEN=0` - accounts are created by an administrator in
  `/manage/users/`; nobody can sign themselves up.
- `WEBLATE_PRIVATE_COMMIT_EMAIL_OPT_IN=0` and
  `WEBLATE_PRIVATE_COMMIT_NAME_OPT_IN=0` - translations are committed to the
  game repositories as `Hero Craft Localization user <id>
  <user-<id>@users.noreply.l10n.herocraft.com>`. Upstream makes this a per-user
  opt-in, which means the real name and e-mail end up in the repository history
  by default; here it is the other way round, and a user who wants their own
  name on their commits selects it in :guilabel:`Profile`.
- `WEBLATE_ENABLE_AVATARS=0` - upstream renders avatars by sending an MD5 of
  the user's e-mail to `gravatar.com`. Off, the local fallback images are used
  and no address ever leaves the server.

`weblate/templates/accounts/snippets/login-info.html` no longer carries the
upstream notice about names being "visible publicly" and about contributing
under each project's license. Both describe a public hosted service and are
wrong for this deployment; the template keeps a comment explaining why the
block is gone.

Outbound calls to `weblate.org`, checked on the live instance: the daily
`support-status-update` task walks `SupportStatus` rows, and there are none
(`SupportStatus.objects.count() == 0`), so nothing is reported. Registering a
support subscription, or ticking :guilabel:`discoverable` in
:guilabel:`Manage`, starts sending the site URL, the instance counters and -
when discoverable - the list of public projects. `weblate check --deploy`
separately fetches the latest release number from `pypi.org`; that request
carries no instance data.

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

Already done: `l10n.herocraft.com` resolves to `195.135.212.209`, which
terminates TLS with a Let's Encrypt certificate (`notAfter` 2026-11-08) and
forwards into the office. Plain HTTP on that name is redirected to HTTPS by
the edge.

What is still missing is the reverse direction for office clients (no hairpin
NAT, see "Current state") and the real client address in `X-Forwarded-For`.

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
