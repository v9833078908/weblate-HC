#!/usr/bin/env bash
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Samples the state of the COL4 French automatic translation on production
# every $INTERVAL seconds and appends one record per sample to $OUT.

set -uo pipefail

cd "$(dirname "$0")/../.."

TASK_ID=${TASK_ID:-ff7843b4-cf61-42a6-b9aa-39b58173eee8}
INTERVAL=${INTERVAL:-180}
OUT=${OUT:-docs/misc/col4-fr-monitor.log}
OR_KEY=${OR_KEY:-$(./deploy/vps.sh ssh "docker exec hcgameloc-weblate-1 weblate shell -c \"
from weblate.configuration.models import Setting
print(Setting.objects.get(category=2, name='openrouter').value['key'])
\"" 2> /dev/null | tail -1)}

remote_probe() {
    cat << 'PROBE'
echo "SAMPLE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "REQ_3M $(docker logs hcgameloc-weblate-1 --since 3m 2>&1 | grep -c 'chat/completions')"
echo "JSONERR_3M $(docker logs hcgameloc-weblate-1 --since 3m 2>&1 | grep -c 'WARNING/ForkPoolWorker.*Could not parse assistant reply as JSON')"
echo "MISMATCH_3M $(docker logs hcgameloc-weblate-1 --since 3m 2>&1 | grep -c 'WARNING/ForkPoolWorker.*Mismatching assistant reply')"
echo "HTTPERR_3M $(docker logs hcgameloc-weblate-1 --since 3m 2>&1 | grep -c 'chat/completions\" *HTTP/1.1 [45]')"
echo "FAILED_3M $(docker logs hcgameloc-weblate-1 --since 3m 2>&1 | grep -c 'failed automatic translation')"
docker exec hcgameloc-weblate-1 weblate shell -c "
from celery.result import AsyncResult
from weblate.trans.models import Translation, Suggestion
r = AsyncResult('__TASK_ID__')
print('STATE', r.state, r.info)
t = Translation.objects.get(component__project__slug='col4', component__slug='data', language__code='fr')
print('TRANSLATED', t.unit_set.filter(state__gte=20).count(), t.unit_set.count())
print('SUGGESTIONS', Suggestion.objects.filter(unit__translation=t).count())
" 2>/dev/null | grep -E '^(STATE|TRANSLATED|SUGGESTIONS)'
PROBE
}

while true; do
    probe=$(remote_probe | sed "s/__TASK_ID__/$TASK_ID/")
    {
        ./deploy/vps.sh ssh "$probe" 2> /dev/null | grep -E '^(SAMPLE|REQ_3M|JSONERR_3M|MISMATCH_3M|HTTPERR_3M|FAILED_3M|STATE|TRANSLATED|SUGGESTIONS)'
        echo "COST_USD_DAILY $(curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $OR_KEY" | jq -r .data.usage_daily)"
        echo "---"
    } >> "$OUT"

    if grep -qE '^STATE (SUCCESS|FAILURE|REVOKED)' <<< "$(tail -12 "$OUT")"; then
        echo "TASK SETTLED" >> "$OUT"
        break
    fi
    sleep "$INTERVAL"
done
