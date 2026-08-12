# Glossary terminology flag Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make glossaries imported through the loc-kit wizard usable in every language: mark their
source strings as terminology so Weblate propagates the terms into each glossary language, and repair
the already-imported `heart-abyss/glossary` on production.

**Architecture:** A glossary language in Weblate is a separate bilingual TBX file with its own copy of
every term. Weblate fills a newly added glossary language in `Translation.sync_terminology`
(`weblate/trans/models/translation.py:2759`), which copies a source string only when its flags contain
`terminology`. That flag lives in `Unit.extra_flags` in the database, never in the TBX file - the TBX
reader maps only `forbidden` (`weblate/formats/ttkit.py:3404-3423`). The UI sets it for hand-added
terms (`weblate/trans/forms.py:4214`); the loc-kit wizard writes TBX directly and therefore never sets
it. Part A repairs the existing production component with a one-off script. Part B closes the import
path with a small Celery task scheduled by the wizard's confirm view, which sets the flag once the
component's source units exist and then hands over to the ordinary `sync_terminology`.

**Tech Stack:** Django, Celery, pytest/Django test client, `weblate shell` on production over
`deploy/vps.sh ssh`.

---

## Evidence this is the defect

Production logs, 2026-08-12:

```
11:13:12 heart-abyss/glossary/fr: processing tbx/fr.tbx, new file, 0 strings
11:13:24 heart-abyss/glossary/fr: fetching translations for 0 units from OpenRouter, 10 per request
11:13:24 heart-abyss/glossary/fr: completed automatic translation
```

Production database:

```
 code | filename   | units        flags | count
 en   | tbx/en.tbx |    57              |   114
 fr   | tbx/fr.tbx |     0
 ru   | (source)   |    57
```

All 114 units carry empty flags, so `sync_terminology` skipped all 57 terms and `fr` stayed empty.
Automatic translation then had zero units to translate and succeeded with zero changes.

---

## Part A: repair `heart-abyss/glossary` on production

This part writes to production. Do not run any step of it without the user saying so at the time.
Every command below is issued from the repository root on the host.

### Task A1: Capture the before state

**Files:**
- Create: `docs/misc/glossary-terminology-fix.py`

**Step 1: Record the current numbers**

Run:

```bash
./deploy/vps.sh ssh 'docker exec hcgameloc-database-1 psql -U weblate -d weblate -t -c "
SELECT l.code, count(u.id)
FROM trans_translation t
JOIN trans_component c ON c.id = t.component_id
JOIN trans_project p ON p.id = c.project_id
JOIN lang_language l ON l.id = t.language_id
LEFT JOIN trans_unit u ON u.translation_id = t.id
WHERE p.slug = '"'"'heart-abyss'"'"' AND c.is_glossary
GROUP BY 1 ORDER BY 1;"'
```

Expected, unchanged from the investigation:

```
 en | 57
 fr |  0
 ru | 57
```

If `fr` is no longer 0, stop: somebody has already changed the component and this plan needs rechecking.

**Step 2: Write the fix script**

Create `docs/misc/glossary-terminology-fix.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""One-off: flag loc-kit glossary source strings as terminology.

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
    for unit in component.source_translation.unit_set.select_for_update().order_by("id"):
        flags = Flags(unit.extra_flags)
        if "terminology" in flags:
            continue
        flags.merge("terminology")
        unit.update_extra_flags(flags.format(), author)
        updated += 1

print(f"TERMINOLOGY_FLAGGED {updated}")

component.schedule_sync_terminology()
print("SYNC_SCHEDULED")
```

**Step 3: Check the licence header passes**

Run: `uv run prek run --all-files --files docs/misc/glossary-terminology-fix.py`
Expected: PASS. `docs/misc/**.py` is not in `REUSE.toml`, so if `reuse` fails, add
`"docs/misc/**.py"` to the HCGameLoc annotation block in `REUSE.toml` (next to
`docs/misc/**.csv`, `REUSE.toml:33`) and rerun.

**Step 4: Commit**

```bash
git add docs/misc/glossary-terminology-fix.py REUSE.toml
git commit -m "chore(glossary): add one-off terminology flag script"
```

### Task A2: Run the fix on production

**Step 1: Ask for approval**

State to the user: this writes to `heart-abyss/glossary` on `l10n.herocraft.com`, sets the terminology
flag on 57 source strings, and creates 57 empty strings in every glossary language. Wait for a yes.

**Step 2: Copy the script to the VPS and run it**

```bash
payload=$(base64 < docs/misc/glossary-terminology-fix.py | tr -d '\n')
./deploy/vps.sh ssh "echo $payload | base64 -d > /tmp/glossary-terminology-fix.py \
    && docker exec -i hcgameloc-weblate-1 weblate shell < /tmp/glossary-terminology-fix.py \
    && rm /tmp/glossary-terminology-fix.py"
```

Expected output:

```
TERMINOLOGY_FLAGGED 57
SYNC_SCHEDULED
```

`TERMINOLOGY_FLAGGED 0` means the flags were already set; carry on to verification either way.

**Step 3: Wait for the sync task and watch the log**

```bash
./deploy/vps.sh ssh 'docker logs --since 5m hcgameloc-weblate-1 2>&1 \
    | grep -E "sync_terminology|glossary/fr" | tail -20'
```

Expected: a `sync_terminology` task received, then `heart-abyss/glossary/fr` committing `tbx/fr.tbx`.

**Step 4: Verify the after state**

Rerun the SQL from Task A1 Step 1.
Expected:

```
 en | 57
 fr | 57
 ru | 57
```

If `fr` is still 0, do not retry blindly: read `docker logs` for the `sync_terminology` task and report
what it says. The usual cause would be `manage_units` off, which the investigation already confirmed is
`t`.

**Step 5: Hand back to the user**

Tell the user to rerun automatic translation on `heart-abyss/glossary/fr` in the UI, with the same
settings as before. Do not start it: it spends OpenRouter budget, and the run is the user's call. What
should now appear in the log instead of `0 units`:

```
heart-abyss/glossary/fr: fetching translations for 57 units from OpenRouter
```

---

## Part B: make the loc-kit wizard flag terminology at import

Runs on the host, ordinary development, no production access. TDD.

### Task B1: Failing test for the imported glossary

**Files:**
- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: Write the failing test**

Add to the glossary wizard test class that already has `_confirm()` (near
`weblate/trans/tests/test_loc_kit_ingest_contract.py:680`):

```python
    def test_confirmed_glossary_flags_terminology(self) -> None:
        """Imported terms must reach every glossary language, not just the source pair."""
        self._start()
        self._select_sheet(self._draft())
        self._upload_profile(self._draft(), self.PROFILE)
        self._confirm()

        component = Component.objects.get(
            project=self.project, slug=self._draft_slug(), is_glossary=True
        )
        sources = component.source_translation.unit_set.all()
        self.assertTrue(sources)
        for unit in sources:
            self.assertIn("terminology", unit.all_flags)
```

Read the surrounding tests first: reuse their exact helper names and their way of naming the created
component and profile document (`self.PROFILE` and `self._draft_slug()` above are placeholders for
whatever the existing tests use). Do not invent a second fixture.

**Step 2: Run it and watch it fail**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k terminology`
Expected: FAIL, `'terminology' not found in Flags()`.

**Step 3: Commit the failing test**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "test(loc-kit): require terminology flags on imported glossaries"
```

### Task B2: The task that sets the flag

**Files:**
- Modify: `weblate/glossary/tasks.py`

**Step 1: Add the task**

Append to `weblate/glossary/tasks.py`:

```python
@app.task(
    trail=False,
    autoretry_for=(Component.DoesNotExist, WeblateLockTimeoutError),
    retry_backoff=60,
    bind=True,
)
def flag_glossary_terminology(self, pk: int) -> None:
    """
    Mark every source string of an imported glossary as terminology.

    Terms written straight into TBX carry no flags, and `sync_terminology`
    copies only flagged strings into the other languages, so without this the
    glossary exists in the source pair alone. The component is created by a
    background task, so the source strings may not exist yet: retry rather
    than flag nothing.
    """
    component = Component.objects.get(pk=pk)
    source = component.source_translation
    if not source.unit_set.exists():
        raise self.retry(countdown=10, max_retries=30)

    author = User.objects.get_or_create_bot(
        scope="glossary", name="sync", verbose="Glossary sync"
    )
    with transaction.atomic():
        for unit in source.unit_set.select_for_update().order_by("id"):
            flags = Flags(unit.extra_flags)
            if "terminology" in flags:
                continue
            flags.merge("terminology")
            unit.update_extra_flags(flags.format(), author)

    component.schedule_sync_terminology()
```

Add the imports the file lacks, next to the existing ones at the top:

```python
from weblate.auth.models import User, get_anonymous
from weblate.checks.flags import Flags
```

(`get_anonymous` is already imported; extend that line rather than adding a second import.)

**Step 2: Run the test - still failing**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k terminology`
Expected: still FAIL. Nothing calls the task yet.

**Step 3: Commit**

```bash
git add weblate/glossary/tasks.py
git commit -m "feat(glossary): add task flagging imported terms as terminology"
```

### Task B3: Schedule it from the wizard

**Files:**
- Modify: `weblate/trans/views/create.py:1448-1492` (`LocKitGlossaryConfirmView.form_valid`)

**Step 1: Schedule the task after the component exists**

In `form_valid`, in the block that already guards on the component having been created - immediately
after `draft.delete()` and before `return response`:

```python
        flag_glossary_terminology.delay_on_commit(self.object.pk)
        return response
```

Import it at the top of the module with the other task imports:

```python
from weblate.glossary.tasks import flag_glossary_terminology
```

Place the call inside the existing `if getattr(self, "object", None) is None: return response` guard's
happy path, so a non-creating code path never schedules it.

**Step 2: Run the test - now passing**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k terminology`
Expected: PASS. Tests run Celery eagerly, so the task executes inline and finds the units already
created.

**Step 3: Run the whole contract suite for regressions**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py`
Expected: all pass.

**Step 4: Commit**

```bash
git add weblate/trans/views/create.py
git commit -m "fix(loc-kit): flag imported glossary terms as terminology"
```

### Task B4: Second test - the terms reach a new language

**Files:**
- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: Write the test**

This is the behaviour the user actually asked for; B1 only checks the flag.

```python
    def test_confirmed_glossary_populates_a_new_language(self) -> None:
        self._start()
        self._select_sheet(self._draft())
        self._upload_profile(self._draft(), self.PROFILE)
        self._confirm()

        component = Component.objects.get(
            project=self.project, slug=self._draft_slug(), is_glossary=True
        )
        expected = component.source_translation.unit_set.count()
        component.add_new_language(Language.objects.get(code="fr"), None)

        added = component.translation_set.get(language__code="fr")
        self.assertEqual(added.unit_set.count(), expected)
```

**Step 2: Run it**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k new_language`
Expected: PASS.

If it fails with 0 units, the cause is `sync_terminology` not having run for the new language: call
`component.schedule_sync_terminology()` after `add_new_language` in the test, mirroring what
`sync_glossary_languages` does in production, and note that in a comment.

**Step 3: Commit**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "test(loc-kit): glossary terms reach a newly added language"
```

### Task B5: Documentation and changelog

**Files:**
- Modify: `docs/changes.rst`
- Modify: `docs/specs/loc-kit-ingest.md`

**Step 1: Changelog entry**

Add to the top, unreleased section of `docs/changes.rst`, matching the surrounding style:

```rst
* Glossaries imported from a loc-kit table now mark their terms as terminology, so the terms appear in
  every glossary language.
```

**Step 2: Spec note**

In `docs/specs/loc-kit-ingest.md`, in the section describing what the glossary workflow creates, add one
sentence: imported terms are flagged as terminology, which is what makes them appear in glossary
languages added later. Read the surrounding section first and match its wording; do not restructure it.

**Step 3: Lint**

Run: `uv run prek run --all-files`
Expected: PASS.

**Step 4: Commit and push**

```bash
git add docs/changes.rst docs/specs/loc-kit-ingest.md
git commit -m "docs(loc-kit): note terminology flagging on glossary import"
git push
```

### Task B6: Type check

**Step 1: Run mypy on the touched modules**

Run:

```bash
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Expected: no new findings for `weblate/glossary/tasks.py` or `weblate/trans/views/create.py`. Pre-existing
findings elsewhere are not this plan's business.

---

## Out of scope

- Deploying Part B to production. Part A repairs the existing component; the import fix reaches
  production on the next ordinary deploy, which is a separate decision.
- Running automatic translation. It costs OpenRouter budget and stays the user's action.
- Backfilling any other already-imported glossary. `col4/glossariy` has the same defect and the same
  script fixes it with two constants changed, but that is a separate request.
- Changing how `sync_terminology` decides what to copy. The flag-based rule is upstream behaviour and
  correct; only our importer was failing to set the flag.
