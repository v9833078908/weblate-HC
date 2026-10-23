#!/usr/bin/env bash
set -euo pipefail
# Run only after explicit approval of production deployment.
root=/opt/bdhc/weblate-wizard
bundle=$(realpath "${1:?Supply an extracted release directory}")
release=${2:?Supply a unique release id}
backup=$(realpath "${3:?Supply a verified full backup directory}")
[[ $EUID -eq 0 && $release =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,60}$ ]]
[[ -f "$bundle/requirements.txt" && -f "$bundle/console/dist/index.html" ]]
[[ -f "$root/shared/wizard.env" && -d "$root/shared/secrets" ]] || {
  echo 'Prepare shared/wizard.env and protected secret files first.' >&2; exit 1;
}
[[ ! -e "$root/releases/$release" ]]
baseline=$(python3 "$bundle/deploy/verify_backup.py" "$backup" --restored)
[[ "$backup" == "$root/shared/backups/"* && -f "$backup/restore-result.json" ]]
[[ "$(readlink -f "$root/current")" == "$root/releases/$baseline" ]]
if grep -Eq '^WIZARD_EXTERNAL_WRITES=1$' "$root/shared/wizard.env"; then
  echo 'Disable external writes before this installation.' >&2; exit 1
fi
python3 "$bundle/deploy/preflight.py" "$root/shared/data/wizard.sqlite"
command -v python3 >/dev/null
getent passwd weblate-wizard >/dev/null || useradd --system --home-dir "$root" --shell /usr/sbin/nologin weblate-wizard
install -d -m 0750 -o weblate-wizard -g weblate-wizard "$root/shared/data" "$root/shared/backups"
install -d -m 0755 "$root/releases/$release"
cp -a "$bundle/." "$root/releases/$release/"
chown -R root:root "$root/releases/$release"
chmod -R a+rX,go-w "$root/releases/$release"
chown -R weblate-wizard:weblate-wizard "$root/shared/secrets"
chmod 0700 "$root/shared/secrets"
find "$root/shared/secrets" -type f -exec chmod 0600 {} +
chown root:weblate-wizard "$root/shared/wizard.env"
chmod 0640 "$root/shared/wizard.env"
python3 -m venv "$root/releases/$release/.venv"
"$root/releases/$release/.venv/bin/pip" install --require-hashes -r "$root/releases/$release/requirements.txt"
# A caller can use umask 077. The generated runtime contains no secrets and
# must be traversable by the unprivileged service account before switching.
chmod -R a+rX,go-w "$root/releases/$release/.venv"
runuser -u weblate-wizard -- "$root/releases/$release/.venv/bin/python" -c 'import fastapi'
previous=$(readlink -f "$root/current")
systemctl stop wizard-api.service
recover_install() {
  systemctl stop wizard-worker.service wizard-profile-worker.service || true
  systemctl disable wizard-worker.service wizard-profile-worker.service || true
  if [[ -n "${previous:-}" ]]; then
    ln -s "$previous" "$root/current.failed-install"
    mv -Tf "$root/current.failed-install" "$root/current"
  fi
  recovery_config=$(mktemp -d "$root/shared/backups/failed-install-config-XXXXXXXX")
  if ! python3 "$bundle/deploy/disable_profile_agent.py" "$root/shared/wizard.env" "$recovery_config/wizard.env"; then
    echo 'Could not disable profile credentials; API remains stopped for manual recovery.' >&2
    return
  fi
  systemctl start wizard-api.service || true
  echo 'Installation failed; worker disabled. Inspect journal and run explicit rollback before re-enabling.' >&2
}
trap recover_install EXIT
python3 "$bundle/deploy/preflight.py" "$root/shared/data/wizard.sqlite" --unchanged "$backup/data/wizard.sqlite" --compatible "$backup/data/wizard.sqlite"
systemctl stop wizard-worker.service
if systemctl cat wizard-profile-worker.service >/dev/null 2>&1; then
  systemctl stop wizard-profile-worker.service
fi
# No API can admit work while the final quiet-state check and switch occur.
python3 "$bundle/deploy/preflight.py" "$root/shared/data/wizard.sqlite" --unchanged "$backup/data/wizard.sqlite"
previous=$(readlink -f "$root/current" || true)
printf '%s\n' "$previous" > "$root/shared/previous-release"
ln -s "$root/releases/$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
install -m 0644 "$root/current/deploy/wizard-api.service" /etc/systemd/system/wizard-api.service
systemctl unmask wizard-worker.service wizard-profile-worker.service
install -m 0644 "$root/current/deploy/wizard-profile-worker.service" /etc/systemd/system/wizard-profile-worker.service
install -m 0644 "$root/current/deploy/wizard-worker.service" /etc/systemd/system/wizard-worker.service
install -m 0644 "$root/current/deploy/wizard-backup.service" /etc/systemd/system/wizard-backup.service
install -m 0644 "$root/current/deploy/wizard-backup.timer" /etc/systemd/system/wizard-backup.timer
systemctl daemon-reload
# These are the only services this installer changes.
systemctl enable --now wizard-api.service wizard-worker.service wizard-backup.timer
systemctl restart wizard-api.service wizard-worker.service
if grep -Eq '^WIZARD_PROFILE_AGENT_ENABLED=1$' "$root/shared/wizard.env"; then
  systemctl enable --now wizard-profile-worker.service
  systemctl restart wizard-profile-worker.service
else
  systemctl disable --now wizard-profile-worker.service
fi
trap - EXIT
echo 'Wizard installed. Verify health/ready, external access and existing BDHC services.'
