#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]]
root=/opt/bdhc/weblate-wizard
destination=$(realpath -m "${1:?Supply a new absolute backup directory}")
release=$(readlink -f "$root/current")
[[ "$destination" == "$root/shared/backups/"* && ! -e "$destination" ]]
script=$(dirname "$(realpath "$0")")
python3 "$script/preflight.py" "$root/shared/data/wizard.sqlite"
worker_active=0
profile_active=0
systemctl is-active --quiet wizard-worker.service && worker_active=1
systemctl is-active --quiet wizard-profile-worker.service && profile_active=1
systemctl stop wizard-api.service
resume_services() {
  systemctl start wizard-api.service
  [[ $worker_active -eq 0 ]] || systemctl start wizard-worker.service
  [[ $profile_active -eq 0 ]] || systemctl start wizard-profile-worker.service
}
trap resume_services EXIT
if [[ $profile_active -eq 1 ]]; then systemctl stop wizard-profile-worker.service; fi
python3 "$script/preflight.py" "$root/shared/data/wizard.sqlite"
systemctl stop wizard-worker.service
PYTHONPATH="$script/../backend" "$release/.venv/bin/python" - "$root" "$release" "$destination" <<'PY'
import sys
from pathlib import Path
from app.backup import create_full_backup
create_full_backup(*(Path(arg) for arg in sys.argv[1:]))
PY
python3 "$script/verify_backup.py" "$destination"
echo 'Full backup verified; keep a second private off-host copy.'
