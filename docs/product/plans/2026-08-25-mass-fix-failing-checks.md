# Mass fix for failing checks

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Status:** awaiting approval. No task has been started.

**Goal:** A producer viewing "Failing check: X" can press one button and fix
every affected string in the current scope, instead of opening each unit by
hand. The mechanism is generic: any check that can produce a deterministic
fixup participates; checks whose fix is semantically risky always go through a
per-string preview.

**Decisions (agreed with the user on 2026-08-25):**

1. Coverage: group A (safe) and group B (review-required) below.
2. Fixing source strings must **not** mark existing translations "Needs
   editing" - the edit is cosmetic and meaning-preserving. Translations that
   themselves contain the defect are fixed by running the same button per
   language.
3. UX: group A applies after a count confirmation; group B always renders a
   preview with per-string diff and checkboxes (search-and-replace pattern).
4. Scope is contextual like bulk edit: the button on a translation page fixes
   that language, on a component page the whole component, on a project page
   the whole project.
5. Permission gate: `unit.bulk_edit` on the scope object, plus per-unit
   `unit.edit` at apply time.

**Architecture:** Reuse the existing `Check.get_fixup()` contract
(`weblate/checks/base.py:35-38,257` - `("regex", pattern, replacement, flags)`
tuples already consumed client-side by the "Fix string" button). Add the
missing fixups (`ellipsis`, `end_ellipsis`, and the five terminal-punctuation
checks), classify every fixup-capable check as `safe` or `review`, and build a
server-side engine that selects units failing a given check
(`check:=<id>` semantics: `dismissed=False`, `weblate/utils/search.py:884-900`),
applies the fixup in Python, and saves through `Unit.translate()` with
component-level check batching. Application runs as a Celery task with the
same progress flash pattern as automatic translation.

**Production census (2026-08-25, read-only API, active checks only):**

| check | count | fix | tier |
|---|---|---|---|
| `ellipsis` | 316 (298 on ru source, 18 on en source) | `...` → `…` (new fixup) | A |
| `end_stop` | 238 | mirror source terminal `.` (new) | B |
| `end_exclamation` | 112 | mirror source `!` (new) | B |
| `end_question` | 67 | mirror source `?` (new) | B |
| `end_ellipsis` | 17 | trailing dots → `…` (new) | A |
| `end_interrobang` | 16 | mirror source `?!`/`!?` (new) | B |
| `end_colon` | 12 | mirror source `:` (new) | B |
| `double_space` | 10 | existing fixup | A |
| `punctuation_spacing` | 5 | existing fixup | A |
| `end_space` | 1 | existing fixup | A |

Not mass-fixable (no deterministic fix; listed for completeness): `same` 588,
`multiple_failures` 196 (meta), `reused` 163, `multiple_capital` 143,
`inconsistent` 38 (its plurals fixup copies another unit's translation - too
risky for bulk), `duplicate` 37, `newline-count` 30, `game-number` 29,
`cyrillic-leak` 20, judge checks 20, `game-markup` 2, `game-line-break` 3 (the
`$` separator is missing entirely, not mis-spaced).

---

## Read before starting

- `weblate/checks/base.py:35-38,256-258` - `FixupType` and `get_fixup`.
- `weblate/checks/chars.py` - the nine existing `get_fixup` implementations
  and the five terminal-punctuation checks that get new ones.
- `weblate/checks/source.py:45-56` - `EllipsisCheck` (a `SourceCheck`; its
  failures attach to source-translation units).
- `weblate/trans/bulk.py:39-186` - `bulk_perform`: per-component
  `start_batched_checks` + `transaction.atomic`, per-unit permission checks,
  batched `Change`/`PendingUnitChange` creation. The engine copies this shape.
- `weblate/trans/views/search.py:50-137` (`search_replace`) - the two-phase
  confirm flow (`ReplaceConfirmForm`, `replace.html`) that group B reuses, and
  `:239-286` (`bulk_edit`) - path parsing and the `unit.bulk_edit` gate.
- `weblate/trans/models/unit.py:2373-2483` (`translate`), `:1750-1845`
  (`save_backend`), `:1889-2017` (`update_source_units` - the state flip that
  decision 2 suppresses).
- `weblate/trans/views/edit.py:1482-1598` and `weblate/trans/tasks.py:973-1074`
  - the auto-translate Celery + progress-flash pattern
  (`weblate/templates/message.html`, polling in
  `weblate/static/loader-bootstrap.js:1607-1668`).
- `weblate/templates/snippets/translation.html:53-123` - the check-stats rows
  that get the entry link; `weblate/templates/translate.html:602-672` - the
  editor check panel.
- `AGENTS.md` - crispy `FormHelper`/`form_tag = False` rule for any form
  rendered inside a template-owned `<form>`; changelog rules.

## Ground rules

1. Work in the main checkout on a branch; no git worktree (shared dev-docker
   ports).
2. Do not restart the dev stack; Python under `weblate/` hot-reloads. No
   deployment to production - the census was read-only and the fix run on prod
   is a separate, explicitly approved operation.
3. Every user-facing string is translatable (`gettext`/`{% translate %}`);
   Russian translations added to `weblate/locale/ru/LC_MESSAGES/django.po`.
4. New code keeps GPL-3.0-or-later headers, `from __future__ import
   annotations`, type hints.

## Test commands

```sh
# checks-level tests, no DB
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/checks/tests/test_chars_checks.py weblate/checks/tests/test_source_checks.py -q
# engine + view tests inside the dev container
./rundev.sh test weblate/trans/tests/test_fix_check.py
```

---

## Task 1 - fixups and safety tiers on the checks

**Files:** `weblate/checks/base.py`, `weblate/checks/chars.py`,
`weblate/checks/source.py`, `weblate/checks/consistency.py`, tests under
`weblate/checks/tests/`.

1. Add `mass_fixup: Literal["safe", "review"] | None` to `BaseCheck`, default
   `None`. Set a tier explicitly on the concrete check classes in this plan;
   do not infer it merely because a class overrides `get_fixup`. Future,
   third-party, and custom checks remain excluded until a maintainer assigns a
   tier and tests that contract.
   - `safe`: `begin_space`, `end_space`, `double_space`, `zero-width-space`,
     `kashida`, `kabyle-characters`, `punctuation_spacing`, `md-link`,
     `ellipsis`, `end_ellipsis`.
   - `review`: `end_stop`, `end_colon`, `end_question`, `end_exclamation`,
     `end_interrobang`.
   - `None` (excluded from mass fix, single-unit button untouched):
     `inconsistent` and every other current check.
2. `EllipsisCheck.get_fixup` → `[("regex", r"\.{3,}", "…", "gu")]`.
   `\.{3,}` so that `....` becomes `…`, not `….`.
3. `EndEllipsisCheck.get_fixup`: when the source ends with `…` and the target
   ends with 2+ dots (or `….` variants), replace the trailing run with `…`;
   return `None` for the non-deterministic direction (target must gain an
   ellipsis the translator never wrote → manual).
4. Terminal-punctuation fixups (`end_stop`, `end_colon`, `end_question`,
   `end_exclamation`, `end_interrobang`): a shared helper that, given the
   source's terminal mark and the target language, appends or strips the
   language-appropriate mark (CJK fullwidth `。！？：`, Greek `;`, Arabic `؟`,
   French preceding NNBSP so `punctuation_spacing` does not immediately fire).
   Contract: the fixup must make the check pass; when the language pair is not
   covered by the table, return `None` and the unit is reported as manual.
5. Tests: per-check fixup tests (including `....`, `1...5`, CJK/fr/el/ar
   variants, plurals) plus a parity test asserting every emitted fixup applies
   identically via Python `re` and is JS-compatible (flags limited to `gui`).

**Verify:** checks test files pass on the host without a database.

## Task 2 - server-side fixup engine

**Files:** new `weblate/trans/fix_check.py`,
`weblate/trans/actions.py`, tests in
`weblate/trans/tests/test_fix_check.py`.

1. `apply_fixup_python(fixups, texts) -> list[str]` translates JS flags
   (`g` → `count=0` else `count=1`, `i` → `re.IGNORECASE`, `u` → default)
   and applies the result to every plural form. For a non-template
   translation, then run the same `fix_target()` normalization that
   `Unit.translate()` will run. Preview and apply must use this final stored
   value, not the intermediate check-fixup value.
2. `collect_fix_candidates(user, unit_set, project, check_obj)` queries the
   *resolved contextual* `unit_set` with
   `unit_set.search(f'check:={check_obj.check_id}', project=project)`. That
   exact query includes active (`dismissed=False`) rows only. Sort candidates
   by `translation__component_id`, `translation_id`, `position`, `id` before
   calculating final targets and re-running this check against them.
   - `eligible`: a permitted final target that clears this check;
   - `manual`: no fixup, no final target change, an uncleared check, or a
     review-tier target already ending in a *different* terminal mark;
   - `denied`: no ordinary unit-edit permission.
   The ordinary permission is always `user.has_perm("unit.edit", unit)`.
   For a source template, that existing callback additionally enforces
   `unit.template`; do **not** substitute `source.edit`, which protects the
   flags/labels `update_source` branch in `bulk_perform`, not a
   `Unit.translate()` target edit.
3. Tier `review` previews the first 250 **eligible** rows in that stable
   order and returns aggregate `manual`/`denied` counts plus
   `total_eligible`, `shown`, and `remaining`. After selected rows are fixed
   they no longer match `check:=<id>`, so reloading the same review
   deterministically advances to the next eligible cohort; intentionally
   unselected rows remain visible. This is the explicit continuation contract,
   not offset pagination. Tier `safe` computes only aggregate bucket counts
   and never renders candidate text.
4. `perform_fix(user, check_obj, unit_ids=None)` recomputes all candidates at
   apply time. `unit_ids is None` means the full current scope (tier `safe`);
   an explicit id list means only checked review rows. Intersect explicit ids
   with the live active-check query and apply only fresh `eligible` rows. Per
   component: `start_batched_checks()` + `transaction.atomic()` +
   `select_for_update()`, then
   `unit.translate(user, new_target, unit.state,
   change_action=ActionEvents.FIX_FAILING_CHECK, propagate=False)`. This
   preserves the requested state but deliberately retains normal `translate()`
   semantics: save-time autofixes, empty-target state, and enforced-check
   downgrade still apply. Finish with `run_batched_checks()`. Return fixed /
   denied / manual / stale-or-no-change counts. A manual or unresolved row is
   never submitted.
5. Add `ActionEvents.FIX_FAILING_CHECK = 105` (after the current maximum 104)
   with a history description. Add it to `ACTIONS_CONTENT` and
   `ACTIONS_SHOW_CONTENT` so history renders the fixed target and reports it
   as a content edit; deliberately leave it out of `ACTIONS_REVERTABLE`
   because the UI does not offer bulk undo.

**Verify:** engine tests cover dismissed exclusion, source-template
`unit.edit`/`unit.template` permission, plural targets, storage-autofix
parity, an unresolved fix, a conflicting terminal mark, stable continuation
after a partial first-250 apply, the idempotent re-apply, action-set
membership/history content, and batching (single `run_batched_checks` per
component).
## Task 3 - cosmetic source-change cascade

**Files:** `weblate/trans/models/unit.py`, tests in
`weblate/trans/tests/test_fix_check.py`.

1. Thread `mark_source_change_fuzzy=True` from `Unit.translate` through
   `save_backend`, `update_source_units`, `update_unit_from_source_change`,
   and `update_source_unit_state`. Consume it only inside the **existing**
   cascade condition: `self.translation.is_template and old_target != target`.
   It must never use `Unit.is_source` or `Translation.is_source` to create a
   new propagation path; source language and template translation are distinct
   concepts in the current model.
2. With `mark_source_change_fuzzy=False`, `update_source_unit_state` still
   sets each sibling's `source` and `num_words`, then returns before *every*
   revert/fuzzy branch. It preserves the sibling's `state`, `original_state`,
   and `previous_source`. It must nevertheless create a
   `PendingUnitChange` for every changed sibling whose translation has a
   writable filename, preserving that unit's unchanged target/state and using
   the mass-fix actor as author. This schedules the new source text into the
   target translation file without falsely marking it for review. Existing
   `unit.save()` calls still recalculate checks/stats and
   `update_source_units()` still writes `SOURCE_CHANGE` history.
3. The mass-fix engine supplies `False`; every other call keeps the `True`
   default. The fixed unit itself still follows all normal `translate()`
   state rules from Task 2, including an enforced-check downgrade. A
   non-template source-language edit does not gain any cascade from this
   feature.
4. Tests: a cosmetic fix in a po-mono template changes sibling sources and
   produces `SOURCE_CHANGE` while preserving sibling state/original state/
   previous source; it creates one pending change per writable sibling
   translation, and `component.commit_pending()` writes the new source to
   those files. An ordinary source edit still fuzzies siblings; a
   non-template source-language unit does not begin cascading.

**Verify:** targeted source-cascade tests plus the existing `test_unit.py`
suite stays green with the default path unchanged.
## Task 4 - Celery task, progress, and result states

**Files:** `weblate/trans/tasks.py`, `weblate/trans/views/search.py` (or a
sibling view module), `weblate/templates/message.html`,
`weblate/static/loader-bootstrap.js`.

1. `fix_failing_checks` mirrors `auto_translate`
   (`autoretry_for=WeblateLockTimeoutError`, backoff, progress via
   `current_task.update_state(PROGRESS)` every N units), with the eager branch
   for `CELERY_TASK_ALWAYS_EAGER`, `store_task_metadata`, and a `task:` flash.
   Its metadata and final result distinguish `queued`, `running`, `completed`
   and `failed`, and report fixed / denied / manual / stale-or-no-change
   counts. The progress text includes `X / Y`, not only a percentage.
2. Extend the existing generic task-progress markup rather than creating a
   second widget: polling updates both the bar width and `aria-valuenow`, and
   final status/result text sits in an `aria-live="polite"` region. Preserve
   the current behaviour for all other task flashes.
3. The only cancellation is the ordinary **Cancel** link before queueing.
   Do not promise undo, task cancellation, or rollback after the task starts:
   those capabilities do not exist in the current generic task flash.

**Verify:** view and JS tests assert task metadata and ARIA updates; manual
smoke in dev shows queued → running → result progress with keyboard and screen
reader-visible text.
## Task 5 - views, URLs, templates

**Files:** `weblate/trans/views/search.py`, `weblate/trans/forms.py`,
`weblate/urls.py`, `weblate/trans/checklists.py`,
`weblate/templates/snippets/translation.html`,
`weblate/templates/translate.html`, `weblate/templates/check_list.html`, new
`weblate/templates/fix_check.html`, `weblate/static/loader-bootstrap.js`.

1. Define an explicit `TranslationChecklistItem` data contract in
   `weblate/trans/checklists.py` rather than assuming a `check_obj` is already
   present. Today `TranslationChecklist.add()` emits a six-element tuple and
   its only consumer unpacks those six values. Replace that private tuple with
   a named item carrying the same display fields plus `check_id` and
   `mass_fixup`. Only the loop adding individual `CHECKS` entries sets those
   machine-readable fields; aggregate buckets (`all`, `translated`, labels)
   set them to `None`. Update its sole template consumer to use named fields.
   Route construction must use `check_id`, never the translated check name.
2. Register the exact URL
   `path("fix-check/<name:name>/<object_path:path>/", fix_check,
   name="fix-check")`, matching the existing trailing-slash and
   `object_path` conventions. It accepts exactly `Translation`, `Component`,
   or `Project` scopes, resolves through `parse_path_units`, and mirrors
   `bulk_edit` at the scope boundary: both `unit.bulk_edit` and `unit.edit`
   are required there, then Task 2 rechecks every unit. It 404s for
   unknown/non-mass-fixable checks. Do **not** render a Fix action on unscoped
   `/checks/`, `Workspace`, or any aggregate language/category/project-language
   page: they have no approved contextual blast radius.
3. `fix_check.html` uses two explicitly different screens:
   - tier `safe`: check name/description, unambiguous scope line, count
     (`Будет исправлено N строк` using `ngettext`), one CSRF-protected
     **Исправить N строк** button, and **Отмена**. It shows no per-unit
     preview, samples, or selection controls;
   - tier `review`: a `Показаны первые N применимых из M` notice whenever
     `remaining > 0`, aggregate manual/denied counts, and a `table-scroll`
     wrapper around the stable first-250 eligible rows. Each row has a labeled
     checkbox, translation/context, a pencil link to the unit editor, and the
     native Weblate diff from
     `format_unit_target unit value=fixed_target diff=unit.target` (the same
     `<ins>`/`<del>` renderer used for suggestions). `eligible` rows start
     unchecked. A deliberate **Выбрать все применимые на этой странице (N)**
     control selects only eligible rows; manual/denied strings are counts and
     a Browse link, not selectable rows. **Исправить выбранные (N)** submits
     only checked ids. Reload after a nonempty apply uses Task 2's continuation
     contract.
   POST re-validates selected ids against the live query and queues the task.
   The template owns each `<form>`, so `FixCheckConfirmForm` sets
   `self.helper = FormHelper(self); self.helper.form_tag = False`.
4. Three entry points:
   - translation status table: add a short **Исправить** inline action beside
     Browse/Translate/Zen only for a row with `mass_fixup` and only when
     `object` is exactly `Translation`;
   - full editor check panel: add **Исправить все такие строки** beside the
     existing per-unit **Исправить строку**, scoped to
     `unit.translation`; `ellipsis` gains the per-unit button after Task 1;
   - scoped check overview: add the same short inline action only when
     `path_object` is exactly `Component` or `Project`. It stays in the
     existing header cell, so `check_list.html` retains five columns and its
     progress-row `colspan="5"` remains correct.
   Zen stays untouched - it has no checks panel.
5. Change the global `tr[data-href]` handler to return when the event target
   is inside `a`, `button`, `input`, `label`, `select`, or `textarea`. This
   preserves row navigation on passive cells while allowing both pointer and
   keyboard activation of every nested action link (including existing
   Browse/Translate/Zen). Do not rely on a special `stopPropagation` handler
   for the new link alone.
6. Accessibility and responsive contract: use existing `.table-scroll` for
   the review table; keep the first actions/checkbox column sticky; retain
   visible focus at 200% zoom; bind every checkbox to a context/language label;
   convey eligibility and result by text as well as color. Dense action cells
   retain short labels; the confirmation heading, scope line, and button carry
   the full scope wording.

**Verify:** view tests cover checklist metadata, exact URL reverse and
trailing slash, all three allowed scopes and both scope permissions,
unscoped/Workspace/aggregate omission, safe confirmation, review selection,
manual counts/Browse link, the 250 continuation boundary, source-template
authorization, and form-nesting depth. JS/browser smoke tests click every new
and existing nested row link, tab through a review table at 200% zoom, and
assert the row destination is not opened instead.
## Task 6 - i18n, docs, changelog

1. Russian translations for the new UI strings in
   `weblate/locale/ru/LC_MESSAGES/django.po`: **Исправить**,
   **Исправить строку**, **Исправить все такие строки**, scope headings,
   `ngettext` forms for **Будет исправлена %(count)d строка**, **Будут
   исправлены %(count)d строки**, **Будут исправлены %(count)d строк**, the
   four bucket labels, the first-N-of-M notice, and all queued/result/error
   messages.
2. `docs/changes.rst` entry in the unreleased section linking to the user
   docs; a short section in the user documentation describing the two tiers,
   the 250-row review batch, source-cosmetic behaviour, permissions, and that
   no post-apply undo exists.
3. Update this plan's status; commit and push per repository convention.

**Verify:** run targeted gettext/catalog checks, then the explicitly named
pre-commit hooks for touched file types (never a bare `prek run --files`, which
can alter unrelated files); changelog builds.
## Out of scope

- Running the actual fix on production (separate, explicitly approved
  operation after the feature is deployed).
- Fixing `same`/`reused`/`multiple_capital`/`newline-count`/game-* checks -
  no deterministic fix exists.
- A Zen-mode panel, API endpoints, or exposing mass fix to the REST API.
- Reworking the autofix pipeline (`reapply_autofixes` stays as is).

## Risks

| Risk | Mitigation |
|---|---|
| Fixup regex behaves differently in Python vs the JS single-unit button | parity test in Task 1 and final-target parity after save-time autofix in Task 2 |
| Source cascade suppression leaks into normal editing | default `mark_source_change_fuzzy=True`, existing template-only branch, and default-path regression tests |
| A Fix anchor is captured by a clickable row | global interactive-descendant guard plus pointer/keyboard smoke tests |
| A review preview hides candidates or selects ambiguous changes | stable first-250 eligible contract, no default selections, explicit page-local select-all, manual bucket and continuation test |
| Preview goes stale before apply | apply recomputes from current DB state; stale units are skipped and reported |
| Mass fix misattributes history or appears revertible | dedicated action 105 in the content/show sets, excluded from revertable actions, history tests |
| fr punctuation fix creates new `punctuation_spacing` failures | terminal-punctuation helper appends NNBSP variants for fr; test asserts no new check fires |
| Large scopes block the request | apply always runs in Celery with progress; preview query is a single indexed `EXISTS` lookup |
