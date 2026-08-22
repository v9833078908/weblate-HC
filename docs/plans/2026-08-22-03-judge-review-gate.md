# LLM judge repair and review-gate rollout plan

## Goal

Make a manually started **Add as translation with an LLM judge** run safe and
useful for the next localization job:

1. Every parsed `major` or `critical` verdict on a writable string is sent to
   the configured `openrouter` repair engine.
2. Both judge seats evaluate the repaired text again.
3. A repaired `pass` becomes `Translated` and is eligible for export.
4. A remaining `major` becomes `Needs checking` with `judge-flag`; a remaining
   `critical` becomes `Needs editing` with `judge-reject`. Both are excluded by
   `WITHOUT_NEEDS_EDITING` and remain visible in the judge verdict card and
   search.
5. This workflow is available on the eight HCGameLoc projects after an
   explicitly approved production activation, but it is never scheduled
   automatically and does not touch already translated production strings.

The repair is performed by the project's configured
`RoutedLLMTranslation` (`openrouter`) engine, not by the two judge seats
themselves. The judge supplies its structured error descriptions as repair
context, then re-evaluates the candidate.

## Scope and invariants

- An operator must intentionally select **Add as translation with an LLM
  judge**. The ordinary automatic-translation mode stays unchanged.
- A fresh/needs-editing string is writable. An existing translation is
  rewritten only when the operator selects **Overwrite the existing
  translation**.
- `JUDGE_MAY_APPROVE` remains off. A judge `pass` means `Translated`, not
  human-review-bypassing `Approved`.
- `JUDGE_MAX_REPAIR_ATTEMPTS=1`: each selected string receives at most one
  repair candidate per judge run, followed by one two-seat re-judgment. A
  remaining negative verdict is held for a human.
- The production judge key is a dedicated
  `WEBLATE_JUDGE_OPENROUTER_KEY`, separate from the machine-translation and
  loc-kit keys, as documented by `JUDGE_OPENROUTER_KEY`.
- This plan does not repair the existing German loc-kit or run a bulk judge
  pass against production content.

## Phase 1: Repair `major` and `critical` verdicts

### Task 1: Specify the missing `FLAG` repair behavior

**Files:**

- Modify: `weblate/trans/tests/test_judge_loop.py`

Add failing tests alongside the existing repair-loop tests:

```python
def test_flag_triggers_one_repair_judged_by_both_seats(self) -> None:
    unit, verdict, client = self.run_batch(
        [MAJOR, MAJOR, PASS, PASS], repair=["fixed text"]
    )
    self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
    self.assertEqual(verdict.attempt, 1)
    self.assertEqual(self.get_unit().target, "fixed text")
    self.assertEqual(client.call_count, 4)

def test_exhausted_flag_repair_keeps_the_last_flag(self) -> None:
    _unit, verdict, client = self.run_batch(
        [MAJOR, MAJOR, MAJOR, MAJOR], repair=["still wrong"]
    )
    self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
    self.assertEqual(verdict.attempt, 1)
    self.assertEqual(client.call_count, 4)
```

Replace the current `test_flag_does_not_trigger_a_repair`; its asserted
behavior is the exact gap this plan closes.

Run:

```sh
./rundev.sh test weblate/trans/tests/test_judge_loop.py -k flag -v
```

Expected before implementation: the pass-after-repair test fails because a
`FLAG` is currently non-repairable.

### Task 2: Make `FLAG` repairable

**Files:**

- Modify: `weblate/trans/judge_loop.py`

Remove `JudgeVerdict.Verdict.FLAG` from `_NON_REPAIRABLE_VERDICTS`; only
`PASS` and `UNPARSED` are non-repairable. Keep the existing safety guards:

- only units in `writable_ids` can be changed;
- repair begins in `STATE_FUZZY`;
- a candidate that adds a deterministic check is rolled back;
- the repaired candidate is judged by both seats;
- repair stops at `JUDGE_MAX_REPAIR_ATTEMPTS`.

Do not add a parallel repair implementation or bypass `repair_target()`.
That function already uses the effective project `openrouter` configuration
and carries the judge descriptions through `failing_checks`.

Update the stale `run_judge_batch()` docstring that currently promises every
unresolved negative a “state-10 hold”; the final state is projected later, so
an unresolved major will instead have the state-12 hold described below.

### Task 3: Hold unresolved `FLAG` verdicts back from export

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_judge_autotranslate.py`

Map `JudgeVerdict.Verdict.FLAG` to `STATE_NEEDS_CHECKING`. Preserve the
existing mappings:

| Final verdict | Final state |
| --- | --- |
| `PASS` | `STATE_APPROVED` only when both project review and `JUDGE_MAY_APPROVE` are enabled; otherwise `STATE_TRANSLATED` |
| `FLAG` | `STATE_NEEDS_CHECKING` |
| `REJECT` | `STATE_FUZZY` |
| `UNPARSED` | no state transition |

Update the existing direct and `AutoTranslate.process_judge` tests to assert
that `FLAG` lands in `FUZZY_STATES`. Add the required
`STATE_NEEDS_CHECKING` import to `test_judge.py`.

Also add one real `process_judge()` integration test in
`test_judge_autotranslate.py`; do not mock `run_judge_batch()` in this test.
Start with an existing translated unit and `overwrite_existing=True`, mock
the initial `process_mt()` to avoid an unrelated translation request, and
mock only `request_verdicts()` plus `repair_target()` with
`MAJOR, MAJOR, PASS, PASS` and a replacement target. Assert that the stored
target is the repair, the final active verdict is `PASS`, and the final state
is `STATE_TRANSLATED`. This proves the operator-visible path, rather than
only the loop and final-state projections in isolation.

### Task 4: Regression-test the complete state machine

Run:

```sh
./rundev.sh test \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/checks/tests/test_judge.py -v
```

The suite must demonstrate all of these paths:

- `critical -> repair -> pass -> Translated`;
- `major -> repair -> pass -> Translated`;
- `critical -> unresolved -> Needs editing + judge-reject`;
- `major -> unresolved -> Needs checking + judge-flag`;
- existing text remains unchanged when overwrite is off;
- an all-unparsed round does not accidentally ship a new translation.

Commit:

```sh
git add \
  weblate/trans/judge_loop.py \
  weblate/trans/models/judge.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_loop.py
git commit -m "fix(judge): repair major verdicts before holding them back"
```

## Phase 2: Fail closed when the repair engine is unavailable

The judge can identify a defect without the repair engine being able to
translate it. That would still hold the string, but it would not provide the
automatic repair promised by this rollout. Check this before enabling the
workflow on a project.

### Task 5: Add a route-preflight command

**Files:**

- Create: `weblate/trans/judge_workflow.py`
- Create: `weblate/trans/management/commands/check_judge_repair_routes.py`
- Modify: `weblate/trans/tests/test_commands.py`

Give both new Python files the repository copyright and
`SPDX-License-Identifier: GPL-3.0-or-later` header, plus
`from __future__ import annotations`.

Define `TARGET_PROJECT_SLUGS` once in `weblate/trans/judge_workflow.py`; both
management commands import it rather than carrying diverging hard-coded
copies. By default, the read-only command checks that target set. Add
`--project <slug>` solely for a non-production dev preflight of one explicit
project, rather than scanning every project. For every non-source target
translation in the selected scope, it must instantiate the effective
`AutoForm.DEFAULT_ENGINE` configuration and validate:

1. the current default (`openrouter`) is registered in `MACHINERY`;
2. its effective configuration contains a non-empty key;
3. the instantiated machinery exposes `resolve_model()` and its routing
   resolves for that translation's `language.code`.

The check must use `project.get_machinery_settings()`, so field-by-field
project overrides are honored. It must not make a provider request or print a
key. Exit with `CommandError` if the engine is unavailable, has the wrong
implementation, lacks a usable key, or any target language lacks a route;
otherwise print only the project/language/model route that was validated.

Tests must cover a valid fallback route, a missing `openrouter` setting, a
target language with no route, and the explicit `--project` scope. Patch or
register the machinery explicitly in the test because `settings_test.py` does
not load the Docker-only custom machinery registration. The failing cases must
not modify project settings.

Run:

```sh
./rundev.sh test weblate/trans/tests/test_commands.py -k JudgeRepairRoute -v
```

Commit:

```sh
git add \
  weblate/trans/judge_workflow.py \
  weblate/trans/management/commands/check_judge_repair_routes.py \
  weblate/trans/tests/test_commands.py
git commit -m "feat(judge): verify repair routes before workflow rollout"
```

## Phase 3: Enable the review and commit gate atomically

### Task 6: Strengthen `enable_review_workflow`

**Files:**

- Modify: `weblate/trans/judge_workflow.py`
- Create: `weblate/trans/management/commands/enable_review_workflow.py`
- Modify: `weblate/trans/tests/test_commands.py`

Give the new command the same repository copyright and GPL SPDX header.

Keep the eight reviewed slugs in the shared `judge_workflow.py` module:

```python
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
```

The command must:

1. load the target set and fail with `CommandError` before any write when one
   of the eight is missing;
2. support `--dry-run`;
3. idempotently set `translation_review=True` and
   `commit_policy=CommitPolicyChoices.WITHOUT_NEEDS_EDITING`;
4. after the complete target set has been validated, wrap the entire
   non-dry-run mutation loop in `transaction.atomic()`;
5. write each changed project through
   `Project.save(update_fields={"translation_review", "commit_policy"})`, so
   the normal audit log remains intact and a later database failure rolls back
   every project setting and audit entry.

Tests must cover successful changes, dry run, idempotency, ignoring a
non-target project, and a missing target slug that leaves already-found
projects unchanged.

Run:

```sh
./rundev.sh test weblate/trans/tests/test_commands.py -k EnableReviewWorkflow -v
```

Commit:

```sh
git add \
  weblate/trans/judge_workflow.py \
  weblate/trans/management/commands/enable_review_workflow.py \
  weblate/trans/tests/test_commands.py
git commit -m "feat(trans): add guarded review workflow rollout command"
```

## Phase 4: Align documentation and check wording

### Task 7: Update the user-facing judge contract

**Files:**

- Modify: `weblate/checks/judge.py`
- Modify: `weblate/checks/tests/test_judge.py`
- Modify: `docs/admin/checks.rst`
- Modify: `docs/security/threat-model.rst`
- Modify: `docs/changes.rst`

Make the `JudgeFlagCheck` description say that a major problem is held back,
not that it still ships. Update `docs/admin/checks.rst` to document that both
parsed `flag` and `reject` verdicts are eligible for one repair on writable
strings; a re-judged pass ships, while an unresolved verdict stays in the
human queue.

Update the threat model to state that either negative verdict can trigger a
configured MT repair only for writable strings. The judge seats remain the
fixed-host judge data flow. A repair uses the already-modelled,
project-configured machine-translation data flow, and this change must not
give an end user control of its endpoint, key, or model.

Add concise changelog entries for:

- major verdicts no longer shipping and now being repairable;
- the guarded workflow and route-preflight commands.

### Task 8: Document the management commands

**Files:**

- Modify: `docs/admin/management.rst`

Add `:wladmin:` entries for `enable_review_workflow [--dry-run]` and
`check_judge_repair_routes [--project <slug>]`. Refer to the project setting using
the `:ref:` role with target `project-translation_review`, not `:setting:`.

Commit:

```sh
git add \
  weblate/checks/judge.py \
  weblate/checks/tests/test_judge.py \
  docs/admin/checks.rst \
  docs/admin/management.rst \
  docs/changes.rst \
  docs/security/threat-model.rst
git commit -m "docs(judge): document major repair and review gating"
```

## Phase 5: Secure the development configuration and enable the dev judge

### Task 9: Stop tracking the existing secrets file

`dev-docker/environment` is currently tracked, so adding it to `.gitignore`
alone does not protect a judge key.

**Files:**

- Modify: `.gitignore`
- Remove from Git index while preserving locally: `dev-docker/environment`
- Create: `dev-docker/environment.example`
- Modify: `dev-docker/docker-compose.yml`

Steps:

1. Use `git rm --cached -- dev-docker/environment`; do not print its content.
2. Add `/dev-docker/environment` to `.gitignore`.
3. Add a sanitized `dev-docker/environment.example` containing only variable
   names and safe defaults, never copied credentials.
4. Add only these non-secret settings to `dev-docker/docker-compose.yml`:

   ```yaml
         WEBLATE_JUDGE_ENABLED: 1
         WEBLATE_JUDGE_MODEL_SEAT_1: deepseek/deepseek-v4-pro
         WEBLATE_JUDGE_MODEL_SEAT_2: qwen/qwen3-235b-a22b-2507
         WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS: 1
   ```

5. Obtain a dedicated development judge key. Append one
   `WEBLATE_JUDGE_OPENROUTER_KEY=...` line to the ignored
   `dev-docker/environment` file without displaying it.

Do not reuse the `RoutedLLMTranslation` key. The existing
`WEBLATE_JUDGE_OPENROUTER_KEY` contract deliberately separates spending,
rotation, and revocation from automatic translation.

### Task 10: Update the production environment template

**Files:**

- Modify: `deploy/environment.example`

Uncomment the judge variables with a disabled default and the calibrated model
pair:

```text
WEBLATE_JUDGE_ENABLED=0
WEBLATE_JUDGE_OPENROUTER_KEY=
WEBLATE_JUDGE_MODEL_SEAT_1=deepseek/deepseek-v4-pro
WEBLATE_JUDGE_MODEL_SEAT_2=qwen/qwen3-235b-a22b-2507
WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS=1
WEBLATE_JUDGE_BATCH_SIZE=5
WEBLATE_JUDGE_MAX_UNITS_PER_RUN=2000
WEBLATE_JUDGE_REQUEST_SLEEP=0.0
WEBLATE_JUDGE_MAY_APPROVE=0
```

Leave `JUDGE_MAY_APPROVE` disabled. No secret belongs in this tracked
template.

Commit:

```sh
git add \
  .gitignore \
  dev-docker/environment.example \
  dev-docker/docker-compose.yml \
  deploy/environment.example
git commit -m "chore(judge): configure isolated development credentials"
```

Before committing, verify that `git status --short` shows the removal of the
previously tracked environment file and does not show its ignored replacement.
The earlier `git rm --cached` has already staged that removal, so do not add
the ignored local replacement back to the index.
If this repository has been shared, rotate any credentials that were ever
committed in that file.

### Task 11: [DEPLOY-GATE] Recreate the development container

Obtain explicit approval immediately before this step.

Run:

```sh
./rundev.sh
./rundev.sh exec -T weblate weblate shell -c \
  "from django.conf import settings; print(
      settings.JUDGE_ENABLED,
      settings.JUDGE_MODEL_SEAT_1,
      settings.JUDGE_MODEL_SEAT_2,
      settings.JUDGE_MAX_REPAIR_ATTEMPTS,
      bool(settings.JUDGE_OPENROUTER_KEY),
  )"
```

Expected:

```text
True deepseek/deepseek-v4-pro qwen/qwen3-235b-a22b-2507 1 True
```

Then run:

```sh
./rundev.sh test \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_commands.py \
  weblate/checks/tests/test_judge.py -v
uv run prek run --files \
  weblate/trans/judge_loop.py \
  weblate/trans/models/judge.py \
  weblate/trans/judge_workflow.py \
  weblate/trans/management/commands/check_judge_repair_routes.py \
  weblate/trans/management/commands/enable_review_workflow.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_commands.py \
  weblate/checks/judge.py \
  weblate/checks/tests/test_judge.py \
  docs/admin/checks.rst \
  docs/admin/management.rst \
  docs/security/threat-model.rst \
  docs/changes.rst \
  .gitignore \
  dev-docker/docker-compose.yml \
  dev-docker/environment.example \
  deploy/environment.example
```

## Phase 6: Dev end-to-end verification [DEPLOY-GATE]

Obtain explicit approval immediately before the paid OpenRouter calls.

1. On one non-production dev project, configure the existing `openrouter`
   machinery with a dedicated development MT credential and a target-language
   route. Keep that credential separate from the judge key and out of Git.
   Then enable `translation_review` and `WITHOUT_NEEDS_EDITING`.
2. Confirm `check_judge_repair_routes --project <dev-slug>` succeeds for its
   target language.
3. Run **Judge** mode with `auto_source=mt`, `engines=openrouter`, and
   `q=state:empty` on a deliberately small test selection.
4. Verify each deterministic test path with controlled mocks first, then
   smoke-test a live negative example:

   - a writable major/critical candidate is repaired and both seats judge the
     repair;
   - a repaired pass is `Translated` with the repaired text;
   - an unresolved major is `Needs checking` plus `judge-flag`;
   - an unresolved critical is `Needs editing` plus `judge-reject`;
   - a pre-existing string without overwrite is never changed;
   - a commit omits held-back unit changes from the exported file.

The live smoke test supplements the deterministic tests; it must not be the
only evidence for major-repair behavior.

## Phase 7: Production code and configuration rollout [DEPLOY-GATE]

Production activation has two separate approval points. Neither starts a
judge run or changes an existing translation.

### Task 12: Deploy the reviewed source changes

After the normal feature-branch review and merge have placed the verified
commits on `main`, use a clean checkout already on `main` whose `HEAD` matches
`origin/main`. Do not switch branches, reset, or stash user work in the
current workspace. Obtain explicit approval and run:

```sh
./deploy/vps.sh deploy
```

Verify the deployed container is healthy. Do not use a feature branch as an
implicit replacement for the reviewed `main` release.

### Task 13: Configure and restart the production judge

Obtain a second explicit approval immediately before modifying the production
environment.

On the server, update the ignored `deploy/.env` used by
`deploy/docker-compose.yml`, not the local VPN credential file
`deploy/.env.local`. Set a dedicated judge key and these non-secret values:

```text
WEBLATE_JUDGE_ENABLED=1
WEBLATE_JUDGE_MODEL_SEAT_1=deepseek/deepseek-v4-pro
WEBLATE_JUDGE_MODEL_SEAT_2=qwen/qwen3-235b-a22b-2507
WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS=1
WEBLATE_JUDGE_MAY_APPROVE=0
```

Do not print the key, put it in Git, or reuse an MT/loc-kit credential. Then
recreate only the Weblate service so Compose reads the changed environment:

```sh
./deploy/vps.sh ssh \
  "cd /srv/hcgameloc/deploy && docker compose up -d --force-recreate weblate"
```

Confirm only non-secret values:

```sh
./deploy/vps.sh ssh \
  "docker exec hcgameloc-weblate-1 weblate shell -c \
  \"from django.conf import settings; print(
      settings.JUDGE_ENABLED,
      settings.JUDGE_MODEL_SEAT_1,
      settings.JUDGE_MODEL_SEAT_2,
      settings.JUDGE_MAX_REPAIR_ATTEMPTS,
      bool(settings.JUDGE_OPENROUTER_KEY),
  )\""
```

### Task 14: Verify routes and enable the eight project gates

Obtain a third explicit approval immediately before changing project settings.

Run the read-only route preflight first:

```sh
./deploy/vps.sh ssh \
  "docker exec hcgameloc-weblate-1 weblate check_judge_repair_routes"
```

It must succeed for every configured target language before enabling a project
gate. Then preview and apply the workflow settings:

```sh
./deploy/vps.sh ssh \
  "docker exec hcgameloc-weblate-1 weblate enable_review_workflow --dry-run"
./deploy/vps.sh ssh \
  "docker exec hcgameloc-weblate-1 weblate enable_review_workflow"
```

The dry run and real run must each cover exactly the eight target slugs.
Verify the resulting `translation_review=True` and
`commit_policy=WITHOUT_NEEDS_EDITING` through the authenticated project API
without printing its token.

## Operator workflow after rollout

For a new localization:

1. Configure the project's `openrouter` routing, persona, style, glossary,
   and the target language route before starting work.
2. An operator with `unit.review` selects **Add as translation with an LLM
   judge**, **Machine translation**, `openrouter`, and normally
   `state:empty`.
3. New strings are translated, judged by both seats, repaired once for either
   a major or critical verdict, and judged again.
4. Repaired passes export normally. Remaining major/critical strings stay in
   the judge queue and are discoverable via `check:judge-flag` and
   `check:judge-reject`.
5. For existing human text, select **Overwrite the existing translation**
   only when automatic replacement is intentionally authorized.

## Explicitly out of scope

- A Celery-beat schedule or changing the normal automatic-translation mode to
  judge mode.
- Enabling `JUDGE_MAY_APPROVE`.
- Bulk judging, rewriting, or otherwise modifying the already translated
  German loc-kit.
- Treating a judge verdict as a deterministic enforced check.
- Silent deployment, production configuration changes, or paid live judge
  runs without the separate approvals listed above.
