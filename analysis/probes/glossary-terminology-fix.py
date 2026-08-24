# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One-off: flag loc-kit glossary source strings as terminology.

Run through `weblate shell` against the instance holding the component:

    docker exec -i hcgameloc-weblate-1 weblate shell < glossary-terminology-fix.py

Idempotent: strings that already carry the flag are left alone, and a rerun
after a successful run reports 0 updated.
"""

from django.db import transaction

from weblate.auth.models import User
from weblate.checks.flags import Flags
from weblate.trans.models import Component

PROJECT = "heart-abyss"
COMPONENT = "glossary"

component = Component.objects.get(project__slug=PROJECT, slug=COMPONENT)
if not component.is_glossary:
    msg = f"{PROJECT}/{COMPONENT} is not a glossary"
    raise SystemExit(msg)

author = User.objects.get_or_create_bot(
    scope="glossary", name="sync", verbose="Glossary sync"
)

updated = 0
with transaction.atomic():
    for unit in component.source_translation.unit_set.select_for_update().order_by(
        "id"
    ):
        flags = Flags(unit.extra_flags)
        if "terminology" in flags:
            continue
        flags.merge("terminology")
        unit.update_extra_flags(flags.format(), author)
        updated += 1

print(f"TERMINOLOGY_FLAGGED {updated}")

component.schedule_sync_terminology()
print("SYNC_SCHEDULED")
