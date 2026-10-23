#!/usr/bin/env bash
# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
# Defaults to dry run. --mode build is a separately authorized operation.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 "$script_dir/check-import-capacity.py" build "$@"
