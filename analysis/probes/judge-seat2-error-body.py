#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# ruff: file-ignore[private-member-access]

"""
Print the LiteLLM proxy's raw error body for the configured seat-2 model.

Run inside the dev container:

    docker compose exec -T weblate weblate shell -c \
        "exec(open('/app/src/analysis/probes/judge-seat2-error-body.py').read())"

It writes nothing.
"""

from __future__ import annotations

import httpx2
from django.conf import settings

from weblate.trans import judge
from weblate.trans.judge_loop import build_request, judge_project_context
from weblate.trans.models import Translation
from weblate.utils.state import STATE_TRANSLATED

translation = Translation.objects.get(pk=182)
units = list(
    translation.unit_set.filter(state__gte=STATE_TRANSLATED).order_by("pk")[:2]
)
batch = [build_request(unit) for unit in units]
project_context = judge_project_context(translation.component.project)

for seat in (2, 1):
    profile = judge.resolve_judge_seat_profile(seat)
    payload = judge._payload(batch, profile, project_context)
    url = f"{judge.get_judge_base_url().rstrip('/')}/chat/completions"
    with httpx2.Client(timeout=120) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {settings.JUDGE_API_KEY}"},
            json=payload,
        )
    print("=" * 72)
    print(f"seat={seat} model={profile.model} status={response.status_code}")
    print("body:", response.text[:2000])
    print("requested keys:", sorted(payload))
    print("response_format:", payload.get("response_format"))
    print("extra:", {k: v for k, v in payload.items() if k != "messages"})
