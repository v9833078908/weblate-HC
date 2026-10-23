"""
Time one repeat apply and its undo on a local copy of anvil-saga fr.

Run inside the dev container:
    docker exec -i dev-docker-weblate-1 weblate shell < analysis/probes/repeat_apply_timing.py
"""

import time

from django.conf import settings
from django.db import connection, reset_queries

from weblate.auth.models import User
from weblate.trans.models import RepeatPolicy
from weblate.trans.repeats import (
    apply_preview,
    get_or_create_group,
    policy_units,
    preview_group,
    undo_event,
)

settings.DEBUG = True
SOURCE = "Добивая выживших"
TARGET = "Achever les survivants"

user = User.objects.get(username="admin")
policy = RepeatPolicy.objects.get(
    project__slug="anvil-saga", target_language__code="fr"
)
unit = policy_units(policy).filter(source=SOURCE).order_by("pk").first()
group = get_or_create_group(policy, unit)
preview = preview_group(group=group, target=[TARGET], actor=user)
selected = [member.unit_id for member in preview.changing]

reset_queries()
started = time.time()
event = apply_preview(token=preview.token, actor=user, unit_ids=selected)
print(
    f"apply: {len(selected)} places, {time.time() - started:.2f}s, {len(connection.queries)} queries"
)

reset_queries()
started = time.time()
undo_event(token=str(event.token), actor=user)
print(f"undo: {time.time() - started:.2f}s, {len(connection.queries)} queries")
