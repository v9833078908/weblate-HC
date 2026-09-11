#!/usr/bin/env bash
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Fixture test for deploy/vps.sh's deploy preflight (Task 3 of
# docs/product/plans/2026-09-11-producer-tasks-survive-deploy.md).
#
# Sources the real script as a function library (guarded `main` never runs),
# stubs everything that would touch the network or a real VPS, and drives
# `deploy_stack` through its argument parser and preflight decision:
#
#   - an unknown flag exits 2 with usage and never reaches `git push`;
#   - an active auto_translate* task blocks without --force;
#   - a queue-only report (no active task) never blocks;
#   - --force proceeds over an active task, recording it instead.
#
# Run: ./deploy/vps_test.sh

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

WORKDIR=$(mktemp -d)
LOGDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR" "$LOGDIR"' EXIT

# `root=$(cd .. && pwd)` inside deploy_stack needs a real git repo one level
# above deploy/, and `git status --porcelain` must read clean: every fixture
# file lives inside the commit, never loose in the working tree. Logs live
# outside $WORKDIR entirely so a scenario run can never dirty the checkout.
mkdir -p "$WORKDIR/deploy"
git -C "$WORKDIR" init -q -b main
git -C "$WORKDIR" config user.email test@example.com
git -C "$WORKDIR" config user.name Test
echo x > "$WORKDIR/file"
cat > "$WORKDIR/deploy/.env.local" << 'EOF'
VPS_HOST=vps.invalid
VPS_USER=deploy
VPS_PASSWORD=unused
VPS_SSH_PORT=22
VPN_PROFILE=/dev/null
VPN_USER=unused
VPN_PASSWORD=unused
VPN_KEY_PASSPHRASE=unused
EOF
cp vps.sh "$WORKDIR/deploy/vps.sh"
git -C "$WORKDIR" add -A
git -C "$WORKDIR" commit -q -m initial

GIT_LOG="$LOGDIR/git.log"
OUT_LOG="$LOGDIR/out.log"

pass=0
fail=0

# Runs $1 (a shell snippet) in its own subshell, after sourcing the real
# script as a function library and stubbing everything that would touch the
# network or a real VPS. Each scenario is fully isolated: stubs and the git
# log never leak between them.
run_scenario() {
    local snippet=$1
    : > "$GIT_LOG"
    (
        # shellcheck disable=SC1091
        source "$WORKDIR/deploy/vps.sh"

        # Record every `git` invocation without touching the network; real
        # git still runs so the deploy-decision logic sees genuine state.
        # shellcheck disable=SC2329 # invoked indirectly by `eval "$snippet"` below
        git() {
            printf '%s\n' "$*" >> "$GIT_LOG"
            command git "$@"
        }

        # Never dial the real VPN gateway or SSH anywhere; all invoked
        # indirectly by `eval "$snippet"` below.
        # shellcheck disable=SC2329
        gateway_running() { return 0; }
        # shellcheck disable=SC2329
        require_gateway() { return 0; }
        # shellcheck disable=SC2329
        run_root_script() { :; }
        # shellcheck disable=SC2329
        ssh_retry() { git -C "$WORKDIR" rev-parse HEAD; }

        eval "$snippet"
    ) > "$OUT_LOG" 2>&1
}

assert_eq() {
    local label=$1 expected=$2 actual=$3
    if [ "$expected" = "$actual" ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL $label: expected [$expected], got [$actual]"
        cat "$OUT_LOG"
    fi
}

assert_pushed() {
    local label=$1 want_pushed=$2 pushed=0
    grep -q 'push' "$GIT_LOG" && pushed=1
    if [ "$pushed" = "$want_pushed" ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL $label: expected git push invoked=$want_pushed, got $pushed"
    fi
}

# --- Scenario 1: unknown flag never reaches git push -----------------------
status=0
run_scenario 'deploy_stack --bogus' || status=$?
assert_eq "unknown flag exit code" 2 "$status"
assert_pushed "unknown flag" 0

# --- Scenario 2: active task blocks without --force -------------------------
status=0
run_scenario '
    auto_translate_preflight_report() {
        printf "TASK abc123 weblate.trans.tasks.auto_translate since 42s\nQUEUED 0\n"
    }
    deploy_stack
' || status=$?
assert_eq "active task exit code" 1 "$status"
assert_pushed "active task" 0

# --- Scenario 3: queue-only report never blocks -----------------------------
status=0
run_scenario '
    auto_translate_preflight_report() {
        printf "QUEUED 3\n"
    }
    deploy_stack
' || status=$?
assert_eq "queue-only exit code" 0 "$status"
assert_pushed "queue-only" 1

# --- Scenario 4: --force proceeds over an active task -----------------------
status=0
run_scenario '
    auto_translate_preflight_report() {
        printf "TASK abc123 weblate.trans.tasks.auto_translate since 42s\nQUEUED 0\n"
    }
    deploy_stack --force
' || status=$?
assert_eq "--force exit code" 0 "$status"
assert_pushed "--force" 1

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
