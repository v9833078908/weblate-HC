#!/usr/bin/env bash
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Samples the state of a production LLM-judge run every $INTERVAL seconds and
# appends one record per sample to $OUT. Read-only: the REST API for unit
# counts, the authenticated judge-run page for run status. No SSH, no
# management command, no `weblate shell` against production.

set -uo pipefail

cd "$(dirname "$0")/../.." || exit

# shellcheck disable=SC1091
. deploy/.env.local

BASE=${BASE:-https://l10n.herocraft.com}
SCOPE=${SCOPE:-heart-abyss/temple/en}
RUN_ID=${RUN_ID:-60cf5abc-a6d0-42ee-a264-89a4946744b1}
INTERVAL=${INTERVAL:-300}
OUT=${OUT:-analysis/data/heart-abyss-judge-monitor.log}
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

api() {
    curl -s -H "Authorization: Token $PROD_WEBLATE_API_TOKEN" \
        -G --data-urlencode "q=$1" --data "page_size=1" \
        "$BASE/api/translations/$SCOPE/units/" | jq -r '.count // "ERR"'
}

login() {
    local page csrf
    page=$(curl -s -c "$JAR" "$BASE/accounts/login/")
    csrf=$(grep -o 'csrfmiddlewaretoken" value="[^"]*' <<< "$page" | head -1 | cut -d'"' -f3)
    curl -s -b "$JAR" -c "$JAR" -o /dev/null \
        -e "$BASE/accounts/login/" \
        -d "csrfmiddlewaretoken=$csrf&username=$PROD_ADMIN_USER&password=$PROD_ADMIN_PASSWORD" \
        "$BASE/accounts/login/"
}

run_page() {
    curl -s -L -b "$JAR" "$BASE/judge-runs/$RUN_ID/" |
        python3 analysis/probes/judge_run_parse.py ||
        echo "RUN_FETCH_ERR parse-failed"
}

login

while true; do
    {
        echo "SAMPLE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "UNITS_TOTAL $(api '')"
        echo "JUDGED $(api 'has:judge')"
        echo "PENDING $(api 'NOT has:judge')"
        echo "PASS $(api 'judge:pass')"
        echo "MINOR $(api 'judge:minor')"
        echo "FLAG $(api 'judge:flag')"
        echo "REJECT $(api 'judge:reject')"
        echo "UNPARSED $(api 'judge:unparsed')"
        echo "STALE $(api 'judge:stale')"
        echo "ESCALATED $(api 'judge:escalated')"
        run_page
        echo "---"
    } >> "$OUT"

    if grep -qE '^RUN_STATUS (Completed|Failed)' <<< "$(tail -30 "$OUT")"; then
        echo "RUN SETTLED" >> "$OUT"
        break
    fi
    sleep "$INTERVAL"
done
