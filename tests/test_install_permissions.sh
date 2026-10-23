#!/usr/bin/env bash
# Copyright HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
source_dir=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d /tmp/wizard-install-permissions.XXXXXX)
trap 'rm -rf -- "$fixture"' EXIT
root="$fixture/root"
mkdir -p "$root/shared/secrets" "$root/shared/data" "$root/shared/backups/test" "$root/releases/baseline" "$fixture/bundle/console/dist" "$fixture/bundle/deploy" "$fixture/bin" "$fixture/units"
touch "$root/shared/wizard.env" "$root/shared/backups/test/restore-result.json" "$fixture/bundle/requirements.txt" "$fixture/bundle/console/dist/index.html"
for name in wizard-api.service wizard-worker.service wizard-backup.service wizard-backup.timer; do touch "$fixture/bundle/deploy/$name"; done
ln -s "$root/releases/baseline" "$root/current"
sed -e "s@root=/opt/bdhc/weblate-wizard@root=$root@" -e "s@\$EUID -eq 0@1 -eq 1@" -e "s@/etc/systemd/system/@$fixture/units/@g" "$source_dir/deploy/install.sh" > "$fixture/installer.sh"
# Stub only host identities, package/network work and service control. Real file
# creation, umask, symlinks and chmod are exercised by the complete installer.
cat > "$fixture/bin/python3" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == *verify_backup.py ]]; then echo baseline; exit 0; fi
if [[ "$1" == -m && "$2" == venv ]]; then
  mkdir -p "$3/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$3/bin/pip"
  cp "$3/bin/pip" "$3/bin/python"
  chmod u+x "$3/bin/pip" "$3/bin/python"
fi
SH
printf '#!/usr/bin/env bash\nexit 0\n' > "$fixture/bin/chown"
cp "$fixture/bin/chown" "$fixture/bin/getent"
cat > "$fixture/bin/runuser" <<'SH'
#!/usr/bin/env bash
shift 3
exec "$@"
SH
cat > "$fixture/bin/install" <<'SH'
#!/usr/bin/env bash
args=()
while (($#)); do
  case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac
done
/usr/bin/install "${args[@]}"
SH
cat > "$fixture/bin/systemctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *'--now wizard-api.service'* ]]; then
  for path in "$TASK_ROOT/current/.venv" "$TASK_ROOT/current/.venv/bin" "$TASK_ROOT/current/.venv/bin/python"; do
    mode=$(stat -c %a "$path")
    (( (8#$mode & 5) == 5 )) || { echo "Runtime inaccessible to service user: $path" >&2; exit 1; }
  done
fi
SH
chmod +x "$fixture/bin/"*
export PATH="$fixture/bin:$PATH" TASK_ROOT="$root"
umask 077
bash "$fixture/installer.sh" "$fixture/bundle" new-release "$root/shared/backups/test"
[[ "$(stat -c %a "$root/shared/wizard.env")" == 640 ]]
[[ "$(stat -c %a "$root/shared/secrets")" == 700 ]]
echo 'Restrictive umask installation: runtime accessible, secrets private'
