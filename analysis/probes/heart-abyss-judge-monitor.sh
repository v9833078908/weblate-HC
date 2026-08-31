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
    local count
    count=$(
        curl -s -H "Authorization: Token $PROD_WEBLATE_API_TOKEN" \
            -G --data-urlencode "q=$1" --data "page_size=1" \
            "$BASE/api/translations/$SCOPE/units/" |
            jq -r '.count // "ERR"' 2> /dev/null
    )
    # A transport failure, an HTML error page or a rejected query must record
    # ERR rather than an empty field.
    [[ $count =~ ^[0-9]+$ ]] && echo "$count" || echo ERR
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
        pass=$(api 'judge:pass')
        minor=$(api 'judge:minor')
        # judge:pass is severity none OR minor, so the buckets below are made
        # disjoint: CLEAN + MINOR + FLAG + REJECT must equal JUDGED. A failed
        # api() call yields ERR, which must stay visible rather than becoming
        # an arithmetic 0.
        if [[ $pass =~ ^[0-9]+$ && $minor =~ ^[0-9]+$ ]]; then
            clean=$((pass - minor))
        else
            clean=ERR
        fi
        echo "SAMPLE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "UNITS_TOTAL $(api '')"
        # has:judge is true after the FIRST seat's verdict; seats run
        # sequentially (judge_loop.py judge_units), so JUDGED saturates when
        # seat 1 finishes while seat 2 is still judging. Severity counters
        # keep shifting until every seat's pass (and repairs) complete.
        echo "JUDGED $(api 'has:judge')"
        echo "PENDING $(api 'NOT has:judge')"
        echo "PASS_INCL_MINOR $pass"
        echo "CLEAN $clean"
        echo "MINOR $minor"
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
