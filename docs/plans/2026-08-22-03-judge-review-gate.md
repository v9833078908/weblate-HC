# LLM Judge Review Gate Rollout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the gap where a judge `FLAG` (major-severity) verdict ships unblocked despite the review gate, then enable the reviewer-approval workflow and the `WITHOUT_NEEDS_EDITING` commit gate on all 8 HCGameLoc projects, with the LLM judge itself configured (but manually triggered, per prior decision) in the dev container.

**Architecture:** A one-branch fix to `state_for_verdict()` routes `FLAG` to `STATE_NEEDS_CHECKING` (12, already in `FUZZY_STATES`) instead of falling through to `STATE_TRANSLATED` (20), so both `REJECT` and `FLAG` are held back by the existing `WITHOUT_NEEDS_EDITING` commit policy without requiring the stricter `APPROVED_ONLY`, which would force a human click on every translated string project-wide, not just judge findings. A new project-scoped management command, `enable_review_workflow`, bulk-toggles `translation_review`/`commit_policy` on the 8 named projects, idempotently and with a `--dry-run` preview. Judge itself (OpenRouter key + the already-calibrated model pair) is wired into the **dev** container only in this plan; enabling and running judge against **prod** content is a separate, later decision (per the prior conversation).

**Tech Stack:** Django (management commands, model fields, migrations-free settings toggle), pytest via `./rundev.sh test` (dev container, `DJANGO_SETTINGS_MODULE=weblate.settings_test`), Docker Compose (`dev-docker/`), the fork's existing LLM judge (`weblate/trans/judge_loop.py`, `weblate/trans/models/judge.py`, `weblate/trans/autotranslate.py`).

---

## Deviations from the generic writing-plans/executing-plans defaults (read first)

- **No dedicated worktree.** The generic skill default is a fresh worktree. This repo's own recorded convention is the opposite for anything touching `dev-docker/`: the dev stack is a single shared resource (fixed host ports, one compose project name), so a worktree copy would collide with the main checkout's containers instead of isolating from them. Phase 4 of this plan edits `dev-docker/docker-compose.yml` and runs `./rundev.sh`. **Execute this plan in the main checkout**, on a normal feature branch, not a worktree.
- **Deployment gates are explicit stop points, not just plan-approval.** Per `AGENTS.md`'s working agreement, approving this plan authorizes writing and committing code. It does **not** authorize: starting or rebuilding `dev-docker` via `./rundev.sh`, running a management command against prod, or any `deploy/vps.sh` command. Every task below that is one of those is marked **[DEPLOY-GATE]** and must get its own explicit go-ahead at execution time, even though the plan itself is approved.
- **Commit messages** use Conventional Commits (`<type>(<scope>): <description>`) per `AGENTS.md`.

---

## Phase 1: Close the judge `FLAG`-ships-unblocked gap

### Task 1: Update the failing unit test for `state_for_verdict`

**Files:**
- Modify: `weblate/trans/tests/test_judge.py:17-22` (imports), `weblate/trans/tests/test_judge.py:64-70` (test body)

**Step 1: Write the failing test**

Change the import block (add `STATE_NEEDS_CHECKING`):

```python
from weblate.utils.state import (
    FUZZY_STATES,
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_NEEDS_CHECKING,
    STATE_TRANSLATED,
)
```

Replace the `test_flag_ships_but_is_not_approved` test (lines 64-70) with:

```python
    def test_flag_lands_on_a_state_that_does_not_ship(self) -> None:
        # A major-severity finding must not slip through WITHOUT_NEEDS_EDITING
        # the way a clean pass does - it needs a human look, same as a
        # critical REJECT, just without the automatic repair attempt (that
        # stays REJECT-only, see judge_loop._NON_REPAIRABLE_VERDICTS).
        state = state_for_verdict(
            JudgeVerdict.Verdict.FLAG, enable_review=True, may_approve=True
        )
        self.assertEqual(state, STATE_NEEDS_CHECKING)
        self.assertIn(state, FUZZY_STATES)
```

**Step 2: Run test to verify it fails**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py -k test_flag_lands_on_a_state_that_does_not_ship -v`
Expected: FAIL - `AssertionError: 20 != 12` (current code still returns `STATE_TRANSLATED`).

### Task 2: Update the failing integration test through `AutoTranslate.process_judge`

**Files:**
- Modify: `weblate/trans/tests/test_judge_autotranslate.py:71-75`

**Step 1: Write the failing test**

Replace `test_flag_ships_but_is_not_approved` (lines 71-75) with:

```python
    def test_flag_lands_on_a_state_that_does_not_ship(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.FLAG, severity="major")
        self.assertIn(self.get_unit().state, FUZZY_STATES)
```

No import changes needed - `FUZZY_STATES` is already imported at line 19.

**Step 2: Run test to verify it fails**

Run: `./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py -k test_flag_lands_on_a_state_that_does_not_ship -v`
Expected: FAIL - the unit's state is `STATE_TRANSLATED` (20), which is not in `FUZZY_STATES`.

### Task 3: Implement the `state_for_verdict` fix

**Files:**
- Modify: `weblate/trans/models/judge.py:17-22` (imports), `weblate/trans/models/judge.py:191-210` (function body)

**Step 1: Write the minimal implementation**

Add `STATE_NEEDS_CHECKING` to the existing import:

```python
from weblate.utils.state import (
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_NEEDS_CHECKING,
    STATE_TRANSLATED,
    StringState,
)
```

Replace the function body:

```python
def state_for_verdict(
    verdict: str, *, enable_review: bool, may_approve: bool
) -> StringState | None:
    """
    Target state for a verdict, or None when the state must not move.

    ``critical`` lands on STATE_FUZZY and ``major`` lands on
    STATE_NEEDS_CHECKING - both are in FUZZY_STATES, so either the
    project-level ``WITHOUT_NEEDS_EDITING`` or ``APPROVED_ONLY`` commit
    policy already excludes them from export. ``pass`` stops at
    STATE_TRANSLATED unless the site opts into judge approval
    (JUDGE_MAY_APPROVE) AND the project has review: measurement shows pass
    misses real critical defects, so the judge does not hand out the top
    trust state by default (review D2).
    """
    if verdict == JudgeVerdict.Verdict.UNPARSED:
        return None
    if verdict == JudgeVerdict.Verdict.REJECT:
        return STATE_FUZZY
    if verdict == JudgeVerdict.Verdict.FLAG:
        return STATE_NEEDS_CHECKING
    if verdict == JudgeVerdict.Verdict.PASS and enable_review and may_approve:
        return STATE_APPROVED
    return STATE_TRANSLATED
```

**Step 2: Run tests to verify they pass**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py -v`
Expected: PASS, all tests in both files green.

### Task 4: Run the full judge test suite as a regression net

**Step 1: Run every judge-related test file**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_views.py -v`
Expected: PASS, no other test's assertions reference `STATE_TRANSLATED` for a `FLAG` verdict (confirmed absent by this plan's own investigation - `test_judge_loop.py::test_flag_does_not_trigger_a_repair` only asserts the verdict and that `client.call_count == 2` / target unchanged, not the final state, so it is unaffected).

**Step 2: Commit**

```bash
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py
git commit -m "fix(judge): hold major-severity FLAG verdicts back from shipping

state_for_verdict() let a FLAG verdict fall through to STATE_TRANSLATED,
so a major-severity finding shipped identically to a clean pass under the
WITHOUT_NEEDS_EDITING commit policy - only REJECT was ever held back.
FLAG now lands on STATE_NEEDS_CHECKING (12), already in FUZZY_STATES, so
both restrictive commit policies exclude it without requiring
APPROVED_ONLY project-wide."
```

---

## Phase 2: Bulk project-workflow management command

### Task 5: Write the failing test for `enable_review_workflow`

**Files:**
- Create: `weblate/trans/tests/test_commands.py` (append to existing file, near other simple command tests)
- Test target (not yet created): `weblate/trans/management/commands/enable_review_workflow.py`

**Step 1: Write the failing test**

Append to `weblate/trans/tests/test_commands.py` (add `CommitPolicyChoices` to the existing `weblate.trans.models` import on line 32, which currently reads `from weblate.trans.models import Change, Component, Translation, Unit` - change it to also import `CommitPolicyChoices`):

```python
from weblate.trans.models import Change, CommitPolicyChoices, Component, Translation, Unit
```

Then append this test class at the end of the file:

```python
class EnableReviewWorkflowCommandTest(RepoTestCase):
    def test_enables_review_and_gate_on_target_projects(self) -> None:
        component = self.create_component()
        project = component.project
        project.slug = "victory-banner"
        project.save(update_fields=["slug"])
        self.assertFalse(project.translation_review)
        self.assertEqual(project.commit_policy, CommitPolicyChoices.ALL)

        call_command("enable_review_workflow")

        project.refresh_from_db()
        self.assertTrue(project.translation_review)
        self.assertEqual(
            project.commit_policy, CommitPolicyChoices.WITHOUT_NEEDS_EDITING
        )

    def test_dry_run_changes_nothing(self) -> None:
        component = self.create_component()
        project = component.project
        project.slug = "space-arena"
        project.save(update_fields=["slug"])

        out = StringIO()
        call_command("enable_review_workflow", "--dry-run", stdout=out)

        project.refresh_from_db()
        self.assertFalse(project.translation_review)
        self.assertEqual(project.commit_policy, CommitPolicyChoices.ALL)
        self.assertIn("would change", out.getvalue())

    def test_ignores_projects_outside_the_target_list(self) -> None:
        component = self.create_component()
        project = component.project
        # create_component()'s default slug is not one of the 8 target slugs.
        call_command("enable_review_workflow")
        project.refresh_from_db()
        self.assertFalse(project.translation_review)
        self.assertEqual(project.commit_policy, CommitPolicyChoices.ALL)

    def test_is_idempotent_on_second_run(self) -> None:
        component = self.create_component()
        project = component.project
        project.slug = "col4"
        project.save(update_fields=["slug"])
        call_command("enable_review_workflow")

        out = StringIO()
        call_command("enable_review_workflow", stdout=out)
        self.assertIn("already up to date", out.getvalue())
```

`call_command`, `StringIO`, and `RepoTestCase` are already imported in this file (lines 17, 10, 34 respectively) - no new imports needed for those.

**Step 2: Run test to verify it fails**

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k EnableReviewWorkflowCommandTest -v`
Expected: FAIL - `django.core.management.base.CommandError: Unknown command: 'enable_review_workflow'`.

### Task 6: Implement the command

**Files:**
- Create: `weblate/trans/management/commands/enable_review_workflow.py`

**Step 1: Write the minimal implementation**

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import Any

from weblate.trans.models import CommitPolicyChoices, Project
from weblate.utils.management.base import BaseCommand

# The 8 HCGameLoc projects on this Weblate instance. Hardcoded and reviewed
# by hand instead of --all, so a newly created test/throwaway project never
# silently picks up the review gate.
TARGET_PROJECT_SLUGS = (
    "col4",
    "pirate-ships",
    "heart-abyss",
    "strategy-and-tactics-2",
    "korotkij-test",
    "need-for-greed",
    "space-arena",
    "victory-banner",
)


class Command(BaseCommand):
    help = (
        "Enables the reviewer-approval workflow (translation_review) and the "
        "WITHOUT_NEEDS_EDITING commit gate on the HCGameLoc projects."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without saving anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = options["dry_run"]
        projects = Project.objects.filter(slug__in=TARGET_PROJECT_SLUGS)
        found_slugs = set(projects.values_list("slug", flat=True))
        for missing_slug in sorted(set(TARGET_PROJECT_SLUGS) - found_slugs):
            self.stderr.write(f"Project slug not found, skipping: {missing_slug}")

        changed = 0
        for project in projects:
            if (
                project.translation_review
                and project.commit_policy
                == CommitPolicyChoices.WITHOUT_NEEDS_EDITING
            ):
                self.stdout.write(f"{project.slug}: already up to date, skipping")
                continue

            changed += 1
            self.stdout.write(
                f"{project.slug}: translation_review "
                f"{project.translation_review} -> True, commit_policy "
                f"{project.commit_policy} -> "
                f"{CommitPolicyChoices.WITHOUT_NEEDS_EDITING} "
                f"(WITHOUT_NEEDS_EDITING){' [dry run]' if dry_run else ''}"
            )
            if not dry_run:
                project.translation_review = True
                project.commit_policy = CommitPolicyChoices.WITHOUT_NEEDS_EDITING
                project.save(update_fields=["translation_review", "commit_policy"])

        if dry_run:
            self.stdout.write(f"Dry run: {changed} project(s) would change.")
        else:
            self.stdout.write(f"Updated {changed} project(s).")
```

**Step 2: Run tests to verify they pass**

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k EnableReviewWorkflowCommandTest -v`
Expected: PASS, all 4 tests green.

**Step 3: Commit**

```bash
git add weblate/trans/management/commands/enable_review_workflow.py weblate/trans/tests/test_commands.py
git commit -m "feat(trans): add enable_review_workflow management command

Bulk-toggles translation_review=True and commit_policy=WITHOUT_NEEDS_EDITING
on the 8 named HCGameLoc projects. Idempotent, supports --dry-run."
```

---

## Phase 3: Documentation

### Task 7: Add a changelog entry

**Files:**
- Modify: `docs/changes.rst` (top section, `Weblate 2026.8.1` / `Improvements` rubric, currently ending after the `game-length` check bullet at line 43)

**Step 1: Add two bullets**

Insert after the existing `game-length` bullet (line 43), still inside `.. rubric:: Improvements`:

```rst
* The :ref:`LLM judge <llm-judge>`'s major-severity ``flag`` verdict now lands on :guilabel:`Needs checking` instead of shipping identically to a clean pass, so a component under the ``WITHOUT_NEEDS_EDITING`` commit policy holds it back the same way it already held back a critical ``reject``.
* Added the :wladmin:`enable_review_workflow` management command, which enables the reviewer-approval workflow and the ``WITHOUT_NEEDS_EDITING`` commit policy on the HCGameLoc projects.
```

**Step 2: Commit**

```bash
git add docs/changes.rst
git commit -m "docs(changes): note the judge FLAG fix and enable_review_workflow"
```

### Task 8: Register `enable_review_workflow` for the `:wladmin:` cross-reference

**Files:**
- Modify: `docs/admin/management.rst` (near `reapply_autofixes` at line 1049-1052)

**Step 1: Add a matching entry**

Insert a new section right after the `reapply_autofixes` section ends (find its closing blank line following line 1052's directive and its prose - read the surrounding 15-20 lines first to place this after the full existing entry, not mid-paragraph):

```rst
enable_review_workflow
-----------------------

.. weblate-admin:: enable_review_workflow [--dry-run]

Enables the reviewer-approval workflow (:setting:`translation_review`) and
sets the commit policy to :guilabel:`Skip translations marked as needing
editing` on the HCGameLoc projects. Idempotent - a project already at the
target settings is reported and skipped. Pass ``--dry-run`` to preview the
changes without saving anything.
```

**Step 2: Commit**

```bash
git add docs/admin/management.rst
git commit -m "docs(admin): document enable_review_workflow"
```

---

## Phase 4: Dev judge wiring [DEPLOY-GATE]

### Task 9: Add judge environment variables to the dev container

**Files:**
- Modify: `dev-docker/docker-compose.yml` (the `weblate:` service's `environment:` list, alongside the existing `WEBLATE_ADD_CHECK`/`WEBLATE_ADD_MACHINERY` entries per `AGENTS.md`'s "Deploying custom checks and machinery" section)

**Step 1: Open the file and locate the `weblate:` service's `environment:` block**

Read the file first (`WEBLATE_ADD_MACHINERY: weblate_customization.machinery.RoutedLLMTranslation` is the anchor line per `AGENTS.md`) to get exact current line numbers before editing - do not guess them from this plan.

**Step 2: Add these lines to the `environment:` block**

```yaml
      WEBLATE_JUDGE_ENABLED: 1
      WEBLATE_JUDGE_OPENROUTER_KEY: "<dev OpenRouter key - obtain before running this task>"
      WEBLATE_JUDGE_MODEL_SEAT_1: deepseek/deepseek-v4-pro
      WEBLATE_JUDGE_MODEL_SEAT_2: qwen/qwen3-235b-a22b-2507
```

Leave `WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS`, `WEBLATE_JUDGE_BATCH_SIZE`, `WEBLATE_JUDGE_MAX_UNITS_PER_RUN`, `WEBLATE_JUDGE_REQUEST_SLEEP` unset (code defaults: 1, 5, 2000, 0.0). Leave `WEBLATE_JUDGE_MAY_APPROVE` unset (defaults to off, per the earlier decision).

**Blocking prerequisite:** an OpenRouter API key for the judge. Per `deploy/environment.example:106-108`, this is deliberately a separate credential from both the `RoutedLLMTranslation` machinery key and the loc-kit profile key - do not reuse either. Obtain or mint this key before running this task.

### Task 10: Update the environment template

**Files:**
- Modify: `deploy/environment.example:111-115`

**Step 1: Uncomment the existing judge block**

Change:
```
#WEBLATE_JUDGE_ENABLED=0
#WEBLATE_JUDGE_OPENROUTER_KEY=
#WEBLATE_JUDGE_MODEL_SEAT_1=
#WEBLATE_JUDGE_MODEL_SEAT_2=
#WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS=1
```
to:
```
WEBLATE_JUDGE_ENABLED=0
WEBLATE_JUDGE_OPENROUTER_KEY=
WEBLATE_JUDGE_MODEL_SEAT_1=deepseek/deepseek-v4-pro
WEBLATE_JUDGE_MODEL_SEAT_2=qwen/qwen3-235b-a22b-2507
WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS=1
```
Keep `WEBLATE_JUDGE_ENABLED=0` here (this is the prod template default, staying off until the later, separate decision to run judge on prod) - only the calibrated model names are filled in as documentation of the measured pair.

**Step 2: Commit**

```bash
git add deploy/environment.example
git commit -m "docs(deploy): document the calibrated judge model pair in the env template"
```

### Task 11: [DEPLOY-GATE] Rebuild the dev container

**This step changes a running instance - get explicit go-ahead immediately before running it, even though the plan is approved.**

**Step 1: Rebuild and restart**

Run: `./rundev.sh`
Expected: Container rebuilds and comes up healthy (`docker compose ps` eventually shows `weblate-dev:...healthy`, matching the `wait` subcommand's own check).

**Step 2: Confirm judge settings landed**

Run: `./rundev.sh exec -T weblate weblate shell -c "from django.conf import settings; print(settings.JUDGE_ENABLED, settings.JUDGE_MODEL_SEAT_1, settings.JUDGE_MODEL_SEAT_2)"`
Expected output: `True deepseek/deepseek-v4-pro qwen/qwen3-235b-a22b-2507`

### Task 12: Run the full test suite in the rebuilt container

**Step 1: Run it**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_commands.py -k "Judge or EnableReviewWorkflow" -v`
Expected: PASS. (These tests override judge settings per-test via `@override_settings`, so they do not depend on the real `WEBLATE_JUDGE_OPENROUTER_KEY` or make real network calls - this is a health check of the rebuilt container, not a live-judge test; that is Phase 5.)

---

## Phase 5: Dev functional verification (live judge calls - real, small OpenRouter cost) [DEPLOY-GATE]

**Every task in this phase makes real calls to OpenRouter using the dev key from Task 9. Confirm the key has a budget/limit you're comfortable with before running any of them.**

### Task 13: Enable the gate on a dev test project

**Step 1: Toggle settings on whatever demo project exists in the dev container**

`enable_review_workflow` only targets the 8 real prod slugs, which do not exist in the dev container's demo data - do not use it here. Instead:

Run: `./rundev.sh exec -T weblate weblate shell -c "
from weblate.trans.models import CommitPolicyChoices, Project
p = Project.objects.all().first()
p.translation_review = True
p.commit_policy = CommitPolicyChoices.WITHOUT_NEEDS_EDITING
p.save(update_fields=['translation_review', 'commit_policy'])
print(p.slug, p.translation_review, p.commit_policy)
"`
Expected: prints the demo project's slug, `True`, `20`.

### Task 14: Verify `REJECT` without overwrite holds the string back but does not rewrite it

**Step 1: Pick a translated unit, note its target, launch Judge mode without "overwrite existing" via the UI (Automatic translation on that translation, mode Judge, `unit.review` permission required), on a string you expect the judge to reject (or force one - see Task 16 for a scripted alternative if the UI path is inconvenient).**

**Step 2: Confirm**

- Unit state is one of `STATE_FUZZY`/`STATE_NEEDS_REWRITING`/`STATE_NEEDS_CHECKING` (10/11/12).
- Unit target text is unchanged from before the run.
- A `judge-reject` check is visible on the unit.

### Task 15: Verify `REJECT` with overwrite repairs and re-ships on success

**Step 1: Repeat Task 14's run on a different (or the same, reset) unit, this time checking "overwrite existing translations" in the Automatic translation form.**

**Step 2: Confirm**

- If the repaired candidate is re-judged as `PASS`: the unit ends at `STATE_TRANSLATED` (20) with the **new**, repaired target text - it shipped and self-healed.
- If the repair is still rejected: the unit ends at `STATE_FUZZY` (10) with the repair attempt's text (not necessarily the original), for a human to finish.

### Task 16: Verify `FLAG` lands on `STATE_NEEDS_CHECKING` end to end (not just in the unit test)

**Step 1: Find or engineer a unit the judge scores `major` severity (a plausible terminology/register issue works well for this - the model pair is not perfectly deterministic, so this may take more than one attempt).**

**Step 2: Confirm**

- Unit state is `STATE_NEEDS_CHECKING` (12).
- A `judge-flag` check is visible on the unit.

### Task 17: Verify the commit policy actually excludes the held-back units from the exported file

**Step 1: Trigger a commit on the translation from Task 14/16 (e.g. via the component's "Commit pending changes" action, or `./rundev.sh exec -T weblate weblate commit_pending <project>/<component>`).**

**Step 2: Confirm**

Read the exported translation file from the repository (e.g. `./rundev.sh exec -T weblate weblate ls_translations <project>/<component>` or inspect the file directly) and confirm the held-back units' current (possibly still-broken) text is **not** the value written to the file - the committed value should be whatever was there before the run, unchanged.

---

## Phase 6: Prod settings rollout [DEPLOY-GATE]

**Prod judge is explicitly out of scope for this plan - only `translation_review`/`commit_policy` are touched. Do not add `WEBLATE_JUDGE_*` to `deploy/.env.local` as part of this phase.**

### Task 18: [DEPLOY-GATE] Dry-run the command against prod

**Get explicit go-ahead immediately before this task, separate from the plan approval.**

**Step 1: Run**

Run: `./deploy/vps.sh ssh "docker exec hcgameloc-weblate-1 weblate enable_review_workflow --dry-run"`
Expected: 8 lines, one per project slug, each showing `translation_review False -> True, commit_policy 0 -> 20 (WITHOUT_NEEDS_EDITING) [dry run]`, plus a final `Dry run: 8 project(s) would change.` (matches the settings this plan's earlier investigation found live on prod for all 8 projects).

### Task 19: [DEPLOY-GATE] Run it for real

**Get explicit go-ahead immediately before this task.**

**Step 1: Run**

Run: `./deploy/vps.sh ssh "docker exec hcgameloc-weblate-1 weblate enable_review_workflow"`
Expected: Same 8 lines without `[dry run]`, final `Updated 8 project(s).`

### Task 20: Verify via the REST API

**Step 1: Confirm all 8 projects**

Run (reusing the already-known `PROD_WEBLATE_API_TOKEN` from `deploy/.env.local`, without printing its value):
```bash
for slug in col4 pirate-ships heart-abyss strategy-and-tactics-2 korotkij-test need-for-greed space-arena victory-banner; do
  curl -s -H "Authorization: Token $PROD_WEBLATE_API_TOKEN" \
    "https://l10n.herocraft.com/api/projects/$slug/" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('$slug', d['translation_review'], d['commit_policy'])"
done
```
Expected: `<slug> True 20` for all 8 lines.

### Task 21: Final push

**Step 1: Confirm everything from Phases 1-3 is committed, then push**

```bash
git status --short
git push origin main
```
Expected: clean tree, push succeeds. (Phases 4-6 are config/settings/deploy actions, not source changes beyond Tasks 9's docker-compose.yml and Task 10's already-committed environment.example - if Task 9 was applied, commit `dev-docker/docker-compose.yml` too, without the real OpenRouter key value in the commit message or anywhere logged.)

---

## Explicitly out of scope

- Running judge against real prod content (a future, separate decision - needs a cost/scope estimate first, per the earlier conversation).
- A Celery-beat scheduler for automatic judge triggering (manual trigger was the explicit decision).
- `EnglishAcronymLeakCheck` and enforcing `reused` via `Component.enforced_checks` (an earlier, separate recommendation in this conversation, not yet approved).
- Any change to `JUDGE_MAY_APPROVE` (stays off, per the earlier decision).
