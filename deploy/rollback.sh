#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]]
root=/opt/bdhc/weblate-wizard
backup=$(realpath "${1:?Supply a verified full backup directory}")
mode=${2:-code}
[[ "$mode" == code || "$mode" == full ]]
[[ "$backup" == "$root/shared/backups/"* ]]
baseline=$(python3 "$(dirname "$0")/verify_backup.py" "$backup")
[[ "$baseline" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,60}$ && -d "$root/releases/$baseline" ]]
if [[ "$mode" == code ]]; then
  python3 "$(dirname "$0")/preflight.py" "$root/shared/data/wizard.sqlite" --compatible "$backup/data/wizard.sqlite" --allow-recovery-addition --read-only-rollback
else
  # Restoring old data must never erase paid receipts or ready translations.
  python3 "$(dirname "$0")/preflight.py" "$root/shared/data/wizard.sqlite" --unchanged "$backup/data/wizard.sqlite"
fi
if [[ "${3:-}" == --dry-run ]]; then
  echo "Verified baseline $baseline; mode $mode. No running service changed."
  exit 0
fi
systemctl stop wizard-api.service wizard-worker.service
if systemctl cat wizard-profile-worker.service >/dev/null 2>&1; then
  systemctl stop wizard-profile-worker.service
fi
umask 077
recovery="$root/shared/backups/pre-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$recovery"
cp -a "$root/shared/data" "$root/shared/wizard.env" "$recovery/"
python3 "$(dirname "$0")/reconcile_profile_jobs.py" "$root/shared/data/wizard.sqlite"
if [[ "$mode" == full ]]; then
  mv "$root/shared/data" "$recovery/data-before-restore"
  cp -a "$backup/data" "$root/shared/data"
  cp -a "$backup/private/wizard.env" "$root/shared/wizard.env"
  if [[ -d "$backup/private/secrets" ]]; then
    mv "$root/shared/secrets" "$recovery/secrets-before-restore"
    cp -a "$backup/private/secrets" "$root/shared/secrets"
  fi
fi
# Disable profile calls for both new and legacy code; recovery keeps old settings.
python3 "$(dirname "$0")/disable_profile_agent.py" "$root/shared/wizard.env"
target="$root/releases/$baseline"
if [[ "$mode" == code ]]; then
  bridge="$root/releases/rollback-bridge-$(date -u +%Y%m%d%H%M%S)"
  target=$(python3 "$(dirname "$0")/prepare_rollback_bridge.py" "$target" "$(readlink -f "$root/current")" "$bridge")
fi
ln -s "$target" "$root/current.rollback"
mv -Tf "$root/current.rollback" "$root/current"
for name in wizard-api.service wizard-worker.service wizard-profile-worker.service wizard-backup.service wizard-backup.timer; do
  [[ ! -f "$backup/systemd/$name" ]] || install -m 0644 "$backup/systemd/$name" "/etc/systemd/system/$name"
done
chown -R weblate-wizard:weblate-wizard "$root/shared/data" "$root/shared/secrets"
systemctl disable wizard-worker.service wizard-profile-worker.service
# Remove restored unit before masking; baseline copy remains in the verified backup.
rm -f /etc/systemd/system/wizard-worker.service /etc/systemd/system/wizard-profile-worker.service
systemctl mask wizard-worker.service wizard-profile-worker.service
systemctl daemon-reload
systemctl start wizard-api.service
echo "Baseline $baseline restored. Both workers stay disabled and masked across reboot: reconcile $recovery before restarting it."
echo 'External Weblate operations are not undone. No existing BDHC service was restarted.'
