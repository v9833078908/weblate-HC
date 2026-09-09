# Mass fix for failing checks

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Status:** implemented (2026-09-08), code-reviewed and corrected
(2026-09-09). Tasks 1-6 complete on branch `feat/mass-fix-failing-checks`;
not yet deployed to production.

**Code review (2026-09-09)** found and fixed, before merge: the generic task
poller mapped every finished task to success, so a failed run rendered as an
empty success (Task 4 step 2) - the flash and the persisted user-task entry
now carry an explicit status contract; a lost lock lease on the retry path
was ignored and retried (Task 4 step 5); actor/scope resolution ran outside
the task's own error handling, so it raised instead of returning a failed
payload; failed payloads omitted the five run counters; the Redis Lua
scripts were registered on every tick; a cosmetic cascade with no author
duplicated an unflushed `PendingUnitChange` (Task 3 step 3); the review-tier
POST accepted unpreviewed ids, which is now refused through a signed,
actor- and scope-bound cohort; the "select all applicable" control was
unwired; the review table's first column was not sticky and the screen had
no heading; the inline Fix action could render on aggregate language pages;
the judge-verdict lookup was a per-unit query; and the `repeat-drift`
linearity test never built a repeat group. Remaining known gap: the
repository has no JavaScript test harness, so the poller mapping and the
select-all binding are covered by rendered-markup assertions and lint only.

**Revised 2026-09-08** against
`docs/product/reviews/2026-09-08-mass-fix-failing-checks-plan-review.md`:
every finding of that review is folded into the tasks below, every code
reference was re-resolved against the current tree, and the non-blocking
follow-ups are listed in the last section instead of being smuggled into the
task list.

**Goal:** a producer looking at "Failing check: X" repairs the whole current
scope from the UI instead of opening each unit by hand. Two honest shapes,
not one:

- **safe tier** - one count confirmation, then the whole scope is repaired in
  the background. Two clicks;
- **review tier** - a per-string preview with diffs and checkboxes, served in
  batches of at most 250 eligible rows **per `check_id`**. A component with
  470 `end_stop` and 342 `end_exclamation` rows therefore needs several
  passes, and the plan does not pretend otherwise.

The mechanism is generic: a check participates only when a maintainer assigns
it a tier and its fixup is proven to clear every check it touches.

## Decisions (agreed with the user on 2026-08-25)

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

## Decisions added on revision (2026-09-08)

6. **Revised coverage decision - this changes user decision 1 and needs the
   owner's confirmation.** Decision 1 fixed coverage as the A/B sets, so
   moving checks out of the safe tier is a coverage change, not merely an
   implementation rule. Five checks (`end_ellipsis`, `begin_space`,
   `end_space`,
   `zero-width-space`, `punctuation_spacing`) leave the safe tier because the
   autofix layer already repairs those defects both on write and, over the
   historical corpus, through `reapply_autofixes`. The justification is not
   redundancy alone - it is that the autofix path carries the stricter
   protocol (bot actor, own scoped commit, foreign pending changes refused),
   and duplicating those defects here would give one string two repair
   policies. The cost is explicit and belongs to the owner, not to the
   implementer: a producer with `unit.bulk_edit` but no container access
   cannot clean those five from the UI, and the backfill stays an operator
   task. If the owner rejects that tradeoff, the five return to the safe tier
   unchanged under the decision-7 contract, and the ownership tests in Task 1
   become mandatory rather than confirmatory. Nothing else in the plan
   depends on which way this goes.
7. **Terminal fixup contract.** A computed target is accepted only when
   **both** hold: the selected `check_id` no longer fails, **and** the set of
   failing terminal checks (`end_stop`, `end_colon`, `end_question`,
   `end_exclamation`, `end_interrobang`) strictly shrinks with no member newly
   appearing, and `punctuation_spacing` does not start failing. The
   before/after comparison is over sets, so a unit that already failed an
   unrelated terminal check is not rejected for that alone. This mirrors the
   rule the fork's own terminal autofix applies in the removal direction
   (`weblate_customization/src/weblate_customization/autofixes.py:112-141`),
   extended to `end_interrobang`, which that autofix does not cover. The
   per-language mark table is an optimization, never the guarantee.
8. `ActionEvents.FIX_FAILING_CHECK = 106`. `105` is already
   `JUDGE_RESOLUTION` (`weblate/trans/actions.py:646-656`) and persisted
   action values are never renumbered.
9. **Judge policy: report, never re-judge.** A judge verdict describing older
   text becoming non-current is the existing intended invariant, and
   `Unit.translate()` reprojects the `judge-*` rows by itself
   (`weblate/trans/models/unit.py:1758,1823-1828`). The mass fix therefore
   queues no paid re-check; it only counts and displays how many verdicts stop
   being current. Precedent for surfacing that number in a bulk preview:
   `docs/product/plans/2026-08-18-loc-kit-table-add-strings.md:77`.
10. **Task metadata.** `Translation`/`Component` scope stores
    `translation_id`/`component_id`; `Project` scope stores only `user_id`.
    Otherwise the flash cannot be polled (`weblate/api/views.py:4883-4919`).
11. **Never eligible:** glossary components, advisory checks, custom
    (`weblate_customization`) checks, and any check without an explicit tier.
12. **Task 3 fallback.** If the source-cascade pending branch cannot be made
    safe, source-side checks leave this feature entirely. Decision 2 stands:
    shipping a mass fix that fuzzies translations is not an option.

## Architecture

Reuse the existing `Check.get_fixup()` contract. `FixupType`
(`weblate/checks/base.py:36-38`) is a union: regex tuples
`("regex", pattern, replacement, flags)` **and** `("plurals", list[str])`,
which only `ConsistencyCheck` returns
(`weblate/checks/consistency.py:493-499`). `BaseCheck.get_fixup` is
`weblate/checks/base.py:261-262`; the value is JSON-serialized in
`weblate/checks/models.py:130-140`, emitted as `data-check-fixup`
(`weblate/templates/translate.html:684-689`) and consumed by
`weblate/static/editor/full.js:577-588`, which builds
`new RegExp(value[1], value[3])` with **no** flag whitelist.

On top of that: add the missing fixups, classify participating checks as
`safe` or `review`, and build a server-side engine that selects units failing
one check (`check:=<id>` - exact id, `dismissed=False`, correlated `Exists`,
`weblate/utils/search.py:909-926`), computes the final stored target in
Python, and writes through `Unit.translate()` with component-level check
batching. Apply runs as a Celery task with the generic progress flash.

## Ownership boundary against the autofix layer

`DEFAULT_AUTOFIX_LIST` (`weblate/trans/defaults.py:20-27`) plus the fork's own
autofixes (`weblate_customization/src/weblate_customization/autofixes.py`)
already repair several of the defects the first revision put in the safe tier.
`fix_target()` runs on every non-template `Unit.translate()`
(`weblate/trans/models/unit.py:2419-2420`) and on suggestion creation, and
`reapply_autofixes` replays the same list over the historical corpus. Claiming
those defects again here would create two mass-mutation paths with different
actors, permissions, state handling and commit strategy.

| Defect | Owner | Why |
|---|---|---|
| trailing dots where the source ends with `…` (`end_ellipsis`) | autofix `ReplaceTrailingDotsWithEllipsis` (`weblate/trans/autofixes/chars.py:27-42`) | already repaired on write and by backfill |
| leading/trailing whitespace (`begin_space`, `end_space`) | autofix `SameBookendingWhitespace` | same |
| zero-width space (`zero-width-space`) | autofix `RemoveZeroSpace` | same |
| French/Breton spacing (`punctuation_spacing`) | autofixes `PunctuationSpacing`, `AddFrenchPunctuationSpacing` | same |
| an **added** terminal mark the source does not have | autofix `RemoveAddedFinalStop` (`weblate_customization/src/weblate_customization/autofixes.py:83-141`) | already repaired on write and by backfill; it removes `.`/`!` only, and refuses any removal that merely trades one terminal failure for another |
| a **missing** terminal mark the source does have (`end_stop`, `end_colon`, `end_question`, `end_exclamation`, `end_interrobang`) | **this feature, review tier** | the fork already decided the opposite direction is not machine-repairable ("a mark lost in translation is not repairable and stays a check", `autofixes.py:101-102`); adding a mark is a linguistic decision and gets a human |
| `double_space`, `kabyle-characters` | **this feature, safe tier** | deterministic, no autofix owner |
| `ellipsis` on a source string | **this feature, safe tier** (subject to Task 3) | autofixes never touch a template store path |

Ownership is therefore split **by direction**, not by check id. Four of the
five terminal checks - `end_stop`, `end_colon`, `end_question`,
`end_exclamation` - legitimately appear in
`RemoveAddedFinalStop.get_related_checks()` (`autofixes.py:52-57,108-110`),
because that autofix decides *through* them while repairing only the removal
direction. `end_interrobang` is **not** in that set, so it has no
direction-overlap precedent at all; the engine's own before/after terminal-set
contract (decision 7) is what covers it. A registry test asserting "no tiered
check is related to any active autofix" would be false by design for the four
and must not be written.

Consequences the implementer must honour:

- `reapply_autofixes` keeps its current contract and is **not** modified. The
  safe tier contains no defect an active autofix repairs, so the two paths do
  not compete there.
- Task 1 adds two ownership tests instead of a blanket registry assertion:
  1. for every `safe`-tier check, a defective sample survives a
     `fix_target()` pass unchanged - i.e. no active autofix already owns it;
  2. for the `review` tier, a target the engine has just repaired survives a
     `fix_target()` pass unchanged. This holds by construction -
     `RemoveAddedFinalStop` only acts when one of its four terminal checks is
     currently failing (`autofixes.py:134-135`) - and the test locks it in, so
     the two layers can never fight over the same string.

## Production census (2026-08-25, read-only API, active checks only)

Site-wide numbers, not per component. Per-component distribution differs a
lot: `anvil-saga/locale_v1-02-import-explained-3/en` alone carries 470
`end_stop`, 342 `end_exclamation`, 150 `end_question`, 24 `end_interrobang`,
1 `end_colon` and 289 `game-length`. Treat the table as an order of magnitude,
not as an acceptance number.

| check | count | fix | tier after decision 6 |
|---|---|---|---|
| `ellipsis` | 316 (298 on ru source, 18 on en source) | `...` → `…` (new fixup) | A |
| `end_stop` | 238 | mirror source terminal `.` (new) | B |
| `end_exclamation` | 112 | mirror source `!` (new) | B |
| `end_question` | 67 | mirror source `?` (new) | B |
| `end_interrobang` | 16 | mirror source `?!`/`!?` (new) | B |
| `end_colon` | 12 | mirror source `:` (new) | B |
| `double_space` | 10 | existing fixup | A |
| `end_ellipsis` | 17 | trailing dots → `…` | autofix layer, dropped here |
| `punctuation_spacing` | 5 | existing fixup | autofix layer, dropped here |
| `end_space` | 1 | existing fixup | autofix layer, dropped here |

Not mass-fixable (no deterministic fix; listed for completeness): `same` 588,
`multiple_failures` 196, `reused` 163, `multiple_capital` 143, `inconsistent`
38 (its plurals fixup copies another unit's translation - too risky for bulk),
`duplicate` 37, `newline-count` 30, `game-number` 29, `cyrillic-leak` 20,
judge checks 20, `game-markup` 2, `game-line-break` 3 (the `$` separator is
missing entirely, not mis-spaced).

`multiple_failures` deserves an explicit note because producers read it as a
defect: it is a source-side roll-up that fires when two or more translations of
one source string carry active checks (`weblate/checks/source.py:84-141`). It
names no text to change and must never receive a tier.

---

## Read before starting

- `weblate/checks/base.py:36-38` (`FixupType`), `:261-262` (`get_fixup`),
  `:65-87` (`check_id` vs `url_id`).
- The complete current `get_fixup` census: `weblate/checks/chars.py:176-181`,
  `:213-217`, `:248-253`, `:274-275`, `:535-536`, `:620-621`, `:719-745`;
  `weblate/checks/markup.py:571-575` (`md-link`, `default_disabled` through
  `:524-526`); `weblate/checks/consistency.py:493-499` (plurals variant).
  `KashidaCheck` uses `regex`-module syntax (`chars.py:608-617`) and is not
  stdlib-`re` compatible.
- Terminal checks and their per-language exception tables:
  `weblate/checks/chars.py:293-331` (`end_stop`), `:341-366` (`end_colon`),
  `:378-412` (`end_question`, Greek), `:427-446` (`end_exclamation`, `eu`),
  `:449-454` (`end_interrobang`), `:588-600` (`end_semicolon`, deliberately
  **not** in scope). French has spacing rules only (`:29-39`, `:650+`).
- `weblate/checks/source.py:45-56` - `EllipsisCheck` (a `SourceCheck`; its
  failures attach to source-translation units).
- `weblate/utils/search.py:909-926` and `:767-768` - `check:=<id>` versus
  `check:<id>`.
- `weblate/trans/bulk.py:39-204` - `bulk_perform`: per-component
  `start_batched_checks` (`:72`), `transaction.atomic` (`:73`),
  `select_for_update` (`:90-94`), per-unit `unit.edit` (`:100-105`) and the
  separate `source.edit` metadata branch (`:150-153`).
- `weblate/auth/permissions.py:634-692` and `:550-557` - `unit.edit` on a
  template unit additionally requires `unit.template`.
- `weblate/trans/models/unit.py:2381-2498` (`translate`), `:2419-2420`
  (`fix_target`), `:2431-2434` (empty target), `:2465-2495` (enforced-check
  downgrade), `:1752-1760` and `:1823-1828` (`save_backend`, `run_checks=True`
  by default), `:1842-1843` (template cascade condition), `:1891-1925`
  (`update_source_units`), `:1983-1997`
  (`update_unit_from_source_change`), `:1949-1981`
  (`update_source_unit_state` - the state flip decision 2 suppresses),
  `:1309-1325` (`previous_source` in serialization), `:2171-2246`
  (`run_checks`; glossary components run only `CHECKS.glossary` at
  `:2181-2183`).
- `weblate/trans/actions.py:646-656` (`105` is taken), `:692-708`
  (`ACTIONS_REVERTABLE`), `:709-737` (`ACTIONS_CONTENT`), `:757-770`
  (`ACTIONS_SHOW_CONTENT`).
- `weblate/trans/views/search.py:50-147` (`search_replace`, the two-phase
  confirm flow group B reuses) and `:239-286` (`bulk_edit`, path parsing and
  the `unit.bulk_edit` gate).
- `weblate/trans/tasks.py:991-1120` (`auto_translate` - note it does **not**
  publish progress), `:363-367` (the only progress helper),
  `weblate/trans/views/edit.py:1780-1889` (queue/eager branch and the `task:`
  flash), `weblate/utils/celery.py:51-70` (`store_task_metadata` stores
  `component_id`, `translation_id`, optional `user_id` - nothing else),
  `weblate/api/views.py:4883-4919` (task access) and `:4955-4967` (the
  retrieve payload: `completed/progress/result/log`, no lifecycle state).
- `weblate/static/loader-bootstrap.js:1725-1810` (generic polling; it never
  updates `aria-valuenow` after first render), `:2104-2108`
  (`tr[data-href]`), `:506-511` (the browser `gettext` wrapper);
  `weblate/templates/message.html:4-33` (flash markup, no `aria-live`).
- `weblate/trans/checklists.py:110-112` and `:133-153` - the six-element
  tuple; its sole consumer is
  `weblate/templates/snippets/translation.html:77-107`.
  `weblate/trans/filter.py:107-110` builds the translated filter names.
- `weblate/templates/check_list.html:34-40,44-47,88` - five columns and the
  progress `colspan`; `weblate/templates/translate.html:671-716` - the editor
  check panel with the existing per-unit **Fix string** button at `:686-693`.
- `weblate/checks/views.py:54-63,91-129` - which scopes `CheckList` accepts and
  how the unscoped `/checks/` page renders; `weblate/utils/views.py:581-637` -
  everything `parse_path_units` can return.
- `weblate/urls.py:149,159,164,174,184,288` (converter conventions), `:826-837`
  (checks routes), `:942` (`JavaScriptCatalog`).
- `weblate/trans/management/commands/reapply_autofixes.py` - the other
  mass-mutation path; read it before touching tiers.
- `docs/security/threat-model.rst:1041-1045` - the conditions that force a
  threat-model revision.
- `AGENTS.md` - crispy `FormHelper`/`form_tag = False` rule for any form
  rendered inside a template-owned `<form>`; changelog rules.

## Ground rules

1. Work in the main checkout on a branch; no git worktree (shared dev-docker
   ports).
2. Do not restart the dev stack; Python under `weblate/` hot-reloads. No
   deployment to production - the census was read-only and the fix run on prod
   is a separate, explicitly approved operation.
3. Every user-facing string is translatable. Server and template strings go to
   `weblate/locale/ru/LC_MESSAGES/django.po`; anything reaching the browser
   through `weblate/static/*.js` goes to `djangojs.po`, which is what
   `JavaScriptCatalog` serves (`weblate/urls.py:942`). A string in the wrong
   catalog compiles cleanly and never appears in the UI.
4. New code keeps GPL-3.0-or-later headers, `from __future__ import
   annotations`, type hints.
5. Merge-order note: the unmerged branch `feat/kit-explanation-string-import`
   also adds routes to `weblate/urls.py`, a form to `weblate/trans/forms.py`, a
   task to `weblate/trans/tasks.py` and a menu entry to
   `weblate/templates/component.html`. Rebase onto it (or after it lands)
   rather than resolving four conflicts twice.

## Test commands

```sh
# character-check fixups: DB-free (SimpleTestCase/CheckTestCase)
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/checks/tests/test_chars_checks.py -q
# source checks need a database: test_source_checks.py uses FixtureTestCase
./rundev.sh test weblate/checks/tests/test_source_checks.py -n0
# engine + view tests inside the dev container
./rundev.sh test weblate/trans/tests/test_fix_check.py -n0
```

Host-side pytest additionally needs `source scripts/test-database.sh` and a
prior `collectstatic`; `./rundev.sh test` avoids that setup.

---

## Task 1 - fixups and safety tiers on the checks

**Files:** `weblate/checks/base.py`, `weblate/checks/chars.py`,
`weblate/checks/source.py`, tests under `weblate/checks/tests/`.

1. Add `mass_fixup: Literal["safe", "review"] | None` to `BaseCheck`, default
   `None`. Set a tier explicitly on concrete classes; never infer it from the
   presence of `get_fixup`. Future, third-party and custom checks stay excluded
   until a maintainer assigns a tier and tests that contract.
   - `safe`: `double_space`, `kabyle-characters`, `ellipsis`.
   - `review`: `end_stop`, `end_colon`, `end_question`, `end_exclamation`,
     `end_interrobang`.
   - `None`: everything else, including every check listed under the autofix
     owner in "Ownership boundary", `md-link` (`default_disabled`,
     `weblate/checks/markup.py:524-526`), `end_semicolon`, `inconsistent`,
     `multiple_failures`, all judge checks, all `weblate_customization`
     checks, and any advisory check. Advisory checks are permanently excluded:
     an advisory finding does not assert that the text must change
     (`docs/product/plans/2026-08-11-glossary-morphological-enforcement.md`).
     `kashida` is excluded for a concrete technical reason: its fixup uses
     `regex`-module syntax (`\p{sc=Arab}`, `weblate/checks/chars.py:608-621`),
     which the `re`-based server engine of Task 2 cannot execute. Tiering it
     requires rewriting that fixup in stdlib-compatible syntax first, and it
     has no measured occurrences, so v1 leaves it alone.
2. `EllipsisCheck.get_fixup` → `[("regex", r"\.{3,}", "…", "gu")]`.
   `\.{3,}` so that `....` becomes `…`, not `….`; `1...5` becomes `1…5`.
3. Terminal-punctuation fixups (`end_stop`, `end_colon`, `end_question`,
   `end_exclamation`, `end_interrobang`): a shared helper that, given the
   source's terminal mark and the target language, appends or normalizes the
   language-appropriate mark (CJK fullwidth `。！？：`, Greek `;`, Arabic `؟`,
   French with the preceding NNBSP). The table mirrors the exception sets the
   checks themselves use (`chars.py:293-331,341-366,378-412,427-446,449-454`)
   and returns `None` for any pair it does not cover.
4. **Contract (decision 7), and it is the acceptance criterion, not the
   table:** compute the set of failing terminal checks before and after the
   fix. Accept only when the selected `check_id` is no longer in the "after"
   set, the set strictly shrank, no member appeared that was not there before,
   and `punctuation_spacing` did not start failing. A unit that already failed
   an unrelated terminal check is not rejected for that alone; a unit failing
   two terminal checks at once (source `.`, target `?`) is `manual` unless one
   fix clears both. The set always includes `end_interrobang`, which the
   fork's autofix does not cover. The implementation follows the shape of
   `_failing()` over `TERMINAL_CHECKS`
   (`weblate_customization/src/weblate_customization/autofixes.py:112-141`),
   extended by that fifth check. The engine, not the helper, enforces it
   (Task 2, step 3).
5. Tests: per-check fixup tests (`....`, `1...5`, CJK/fr/el/ar variants,
   plurals); an overlap test proving a `.`/`?` pair is rejected rather than
   turned into `?.`; a parity test asserting every regex fixup emitted by a
   tiered check behaves identically under Python `re` and is valid in
   JavaScript; the ownership tests from "Ownership boundary".

**Verify:** `test_chars_checks.py` on the host without a database;
`test_source_checks.py` in the container.

## Task 2 - server-side fixup engine

**Files:** new `weblate/trans/fix_check.py`, `weblate/trans/actions.py`,
tests in `weblate/trans/tests/test_fix_check.py`.

1. `apply_fixup_python(fixups, texts) -> list[str]` translates JS flags
   (`g` → `count=0` else `count=1`, `i` → `re.IGNORECASE`, `u` → default) and
   applies the result to every plural form. It accepts only the `regex`
   variant and raises on `("plurals", …)`, which no tiered check emits. For a
   non-template translation it then runs the same `fix_target()` normalization
   `Unit.translate()` will run (`weblate/trans/models/unit.py:2419-2420`), so
   preview and apply both use the **final stored value**.
2. `collect_fix_candidates(user, unit_set, project, check_obj)` queries the
   *resolved contextual* `unit_set` with
   `unit_set.search(f'check:={check_obj.check_id}', project=project)`, which
   includes active rows only. Glossary components are excluded from the query:
   `run_checks` runs only `CHECKS.glossary` there
   (`weblate/trans/models/unit.py:2181-2183`), so a projected row and a
   recomputed row would disagree. Sort candidates by
   `translation__component_id`, `translation_id`, `position`, `id` before
   computing final targets.
3. Bucket every candidate:
   - `eligible`: a permitted final target that satisfies the decision-7
     before/after set contract in full - the selected check cleared, the
     terminal set strictly smaller, no member newly appearing, no new
     `punctuation_spacing` failure;
   - `manual`: no fixup, no final target change, an uncleared check, a
     conflicting terminal mark, or any newly introduced terminal /
     `punctuation_spacing` failure;
   - `denied`: no ordinary unit-edit permission.
   The ordinary permission is always `user.has_perm("unit.edit", unit)`. For a
   source template that callback additionally enforces `unit.template`
   (`weblate/auth/permissions.py:550-557`); do **not** substitute
   `source.edit`, which protects the flags/labels `update_source` branch in
   `bulk_perform` (`weblate/trans/bulk.py:150-153`), not a `Unit.translate()`
   target edit.
4. Alongside the buckets, count `verdicts_no_longer_current`: candidates whose
   newest judge verdict is current for the present text and will stop being
   current after the fix (`JudgeVerdict.is_stale`,
   `weblate/trans/models/judge.py:869-871`). Per decision 9 this is a reported
   number only - no re-check is queued, and judge checks are never selectable.
5. Tier `review` previews the first 250 **eligible** rows in that stable order
   and returns aggregate `manual`/`denied` counts plus `total_eligible`,
   `shown`, `remaining` and `verdicts_no_longer_current`. After selected rows
   are fixed they no longer match `check:=<id>`, so reloading the same review
   deterministically advances to the next eligible cohort; intentionally
   unselected rows remain visible. This is the explicit continuation contract,
   not offset pagination. Tier `safe` computes only aggregate counts and never
   renders candidate text.
6. `perform_fix(user, check_obj, unit_ids=None)` recomputes all candidates at
   apply time. `unit_ids is None` means the full current scope (tier `safe`);
   an explicit id list means only checked review rows. Intersect explicit ids
   with the live active-check query and apply only fresh `eligible` rows. Per
   component: `start_batched_checks()` + `transaction.atomic()` +
   `select_for_update()`, then
   `unit.translate(user, new_target, unit.state,
   change_action=ActionEvents.FIX_FAILING_CHECK, propagate=False)` - the third
   argument is `new_state`, there is no keyword `state`. This passes the
   current state through but deliberately keeps normal `translate()`
   semantics: save-time autofixes, empty-target state and the enforced-check
   downgrade (`:2465-2495`) still apply, so the final stored state may differ.
   Finish with `run_batched_checks()`. Return fixed / denied / manual /
   stale-or-no-change counts plus `verdicts_no_longer_current`. A manual or
   unresolved row is never submitted.
7. Add `ActionEvents.FIX_FAILING_CHECK = 106` (decision 8) with a history
   description. Add it to `ACTIONS_CONTENT` and `ACTIONS_SHOW_CONTENT` so
   history renders the fixed target and counts it as a content edit;
   deliberately leave it out of `ACTIONS_REVERTABLE` because the UI offers no
   bulk undo.
8. `repeat-drift` interaction: the check propagates to group members
   (`propagates = "repeat"`), so a batch touching many members of one group
   recomputes those neighbours repeatedly. Measure it in the engine test with
   a small group and assert the number of `run_checks` passes stays linear in
   the number of edited units; if it does not, skip propagation inside the
   batch and rely on the closing `run_batched_checks()`.

**Verify:** engine tests cover dismissed exclusion, glossary-component
exclusion, source-template `unit.edit`/`unit.template` permission, plural
targets, storage-autofix parity, an unresolved fix, a conflicting terminal
mark, an introduced-failure rejection, stable continuation after a partial
first-250 apply, the idempotent re-apply, the judge counter, action-set
membership and history content, and batching (single `run_batched_checks` per
component).

## Task 3 - cosmetic source-change cascade

This task exists for exactly one member of the safe tier: source-side
`ellipsis`. It changes a shared write path, so it carries its own decision
gate.

**Files:** `weblate/trans/models/unit.py`, tests in
`weblate/trans/tests/test_fix_check.py`.

1. Thread a keyword-only `mark_source_change_fuzzy: bool = True` from
   `Unit.translate` through `save_backend`, `update_source_units`,
   `update_unit_from_source_change` and `update_source_unit_state`. Each of
   those has exactly one caller today (`weblate/trans/models/unit.py:1843`,
   `:1907`, `:1995`, `:2452`), plus a direct `save_backend` call in
   `weblate/trans/tests/test_edit.py:2612`, so a keyword-only default breaks
   nothing. Consume it only inside the **existing** cascade condition
   `self.translation.is_template and self.old_unit["target"] != self.target`
   (`:1842-1843`). It must never use `Unit.is_source` or
   `Translation.is_source` to create a new propagation path; source language
   and template translation are distinct concepts in the current model.
2. With `mark_source_change_fuzzy=False`, `update_source_unit_state` sets each
   sibling's `source` and `num_words` (`:1952-1954`) and then returns before
   *every* revert/fuzzy branch (`:1955-1981`). It preserves the sibling's
   `state`, `original_state` and `previous_source` - explicitly including
   **not** writing `previous_source`, so a later genuine source edit still has
   its comparison base (`previous_source` is serialized at `:1309-1325` and
   shown only for fuzzy units).
3. Because every existing `PendingUnitChange` creation lives inside the
   suppressed branches, the new branch must create one itself, and its
   construction is specified here rather than left to the implementer:
   - one pending change per changed sibling whose translation has a writable
     filename and is not read-only;
   - author is the mass-fix actor, the same user `Unit.translate()` received;
   - `target` and `state` are copied unchanged from the sibling, so the
     pending row carries the new source with the old translation;
   - an existing unflushed pending row for the same unit is updated rather
     than duplicated, matching how the suppressed branches behave today;
   - the row is created inside the same transaction as the sibling `save()`,
     so a rolled-back cascade leaves no orphan pending change.
   Existing `unit.save()` calls still recalculate checks and stats, and
   `update_source_units()` still writes `SOURCE_CHANGE` history
   (`:1916-1925`).
4. The mass-fix engine supplies `False`; every other caller keeps the `True`
   default. The fixed unit itself still follows all normal `translate()` state
   rules from Task 2. A non-template source-language edit gains no cascade
   from this feature.
5. **Decision gate.** If step 3 cannot be implemented without either
   duplicating pending rows or losing a file write, source checks leave the
   feature: `ellipsis` loses its tier, Task 3 is dropped, and source-side
   `...` is handled elsewhere (loc-kit/converter input, or a separate
   proposal). Shipping a mass fix that fuzzies healthy translations is not an
   option (decision 2), and neither is a DB-only source change that never
   reaches the file.
6. Tests: a cosmetic fix in a po-mono template changes sibling sources and
   produces `SOURCE_CHANGE` while preserving sibling state, original state and
   previous source; it creates exactly one pending change per writable sibling
   translation (and none for a read-only or fileless one); a second run
   creates no duplicate pending row; `component.commit_pending()` writes the
   new source to those files while the targets stay byte-identical; an
   ordinary source edit still fuzzies siblings; a non-template source-language
   unit does not begin cascading.

**Verify:** targeted source-cascade tests plus the existing `test_unit.py` and
`test_edit.py` suites stay green with the default path unchanged.

## Task 4 - Celery task, progress, and result states

The generic progress machinery is thinner than it looks: `auto_translate`
(`weblate/trans/tasks.py:991-1120`) publishes no progress at all, the only
helper (`:363-367`) stores `{progress}`, `store_task_metadata`
(`weblate/utils/celery.py:51-70`) stores three ids, the task API
(`weblate/api/views.py:4955-4967`) exposes `completed/progress/result/log` and
no lifecycle state, the poller
(`weblate/static/loader-bootstrap.js:1725-1810`) never updates
`aria-valuenow` after the first render, and `weblate/templates/message.html:4-33`
has no live region. This task therefore **builds** that contract; it does not
copy it.

**Files:** `weblate/trans/tasks.py`, `weblate/trans/views/search.py` (or a
sibling view module), `weblate/templates/message.html`,
`weblate/static/loader-bootstrap.js`.

1. `fix_failing_checks` follows the `auto_translate` shape
   (`autoretry_for=WeblateLockTimeoutError`, backoff, the eager branch for
   `CELERY_TASK_ALWAYS_EAGER`, `store_task_metadata`, a `task:` flash from
   `weblate/trans/views/edit.py:1780-1889`) and adds real progress:
   `current_task.update_state(PROGRESS)` every N units with an `X / Y` text,
   not only a percentage.
2. **Lifecycle without a new API surface.** The task API is not extended: it
   keeps returning `completed/progress/result/log`
   (`weblate/api/views.py:4955-4967`), and this feature adds no serializer
   field, because every other flash consumer would have to be revalidated for
   it. The lifecycle is expressed inside the two payloads the poller already
   reads:
   - `progress` carries the integer percentage, and
     `current_task.update_state(PROGRESS)` carries `{"progress": N,
     "done": X, "total": Y}` so the flash can render `X / Y`;
   - `result` is a dict with an explicit `"status"` of `"completed"` or
     `"failed"` plus the counters fixed / denied / manual /
     stale-or-no-change / `verdicts_no_longer_current`, and, when failed, a
     translated message.
   Mapping in the poller: not ready → running; ready with
   `result["status"] == "completed"` → success text; ready with `"failed"`,
   with a non-dict result, or with a stringified exception → failure text.
   **Terminal failure returns, it does not raise.** Celery stores the
   exception as the task result, which would replace the `result` dict and
   make `status` unobservable, so the body catches its own errors:
   `WeblateLockTimeoutError` is re-raised while retries remain, letting
   `autoretry_for` do its job; once retries are exhausted - detected with the
   same shape as the existing `commit_lock_retries_exhausted()`
   (`weblate/trans/tasks.py:69-76`) - and for any other unexpected exception,
   the task reports the error, **returns** `{"status": "failed", …}` and
   never re-raises. The non-dict/stringified-exception branch of the mapping
   above stays as the backstop for a worker that dies outright, so a failed
   run can never render as an empty success.
3. Metadata per decision 10: `translation_id` for a translation scope,
   `component_id` for a component scope, and `user_id` alone for a project
   scope. A project-scope task consequently has `component is None` in
   `get_task`, which also makes the API `DELETE` path refuse it
   (`weblate/api/views.py:4913-4915`) - that is intended, since this feature
   promises no cancellation.
4. Extend the existing generic markup rather than adding a second widget:
   polling updates the bar width **and** `aria-valuenow`, and the final
   status/result text sits in an `aria-live="polite"` region. Preserve current
   behaviour for every other task flash, and do not wire this task into the
   component-progress abort widget
   (`weblate/templates/component-progress.html:40-41`,
   `loader-bootstrap.js:1704-1718`).
5. **Concurrency guard as an explicit reservation.** The actor's task list
   cannot serve here: its entries carry no check or scope identity
   (`weblate/utils/celery.py:51-70` stores three ids and nothing else) and
   offer no atomic acquire, so two simultaneous submits would both pass a
   read-then-check. The reservation is keyed by
   `fix-check-lock-{check_id}-{scope_type}-{scope_pk}` - immutable scope
   identity plus the check, never the actor - and is taken in the request
   before `apply_async`, with the task id generated first, mirroring the
   reservation discipline already accepted in
   `docs/product/plans/2026-08-18-loc-kit-table-add-strings.md`.

   The Django cache API has **no compare-and-set**: `cache.get` followed by
   `cache.set`/`cache.delete` cannot be token-safe, and a holder whose lease
   already expired would happily delete a newer holder's key. The plan
   therefore does not claim token safety on top of it. Two implementations,
   chosen by `is_redis_cache()` (`weblate/utils/cache.py:13-14`):

   - **Redis (production, and the only configuration where the guard is
     strict).** Do not use `cache.lock(...)`/`redis.lock.Lock` for the
     handoff: its ownership token lives in `Lock.local.token`
     (thread-local, set by `acquire()`), and `reacquire()`/`extend()` take no
     token argument, so a lock acquired in the web request cannot be
     refreshed from the worker without poking at that private attribute. Only
     `Lock.do_release(expected_token)` accepts an explicit token. Use the raw
     client instead - the same one `WeblateLock` reaches through
     (`weblate/utils/lock.py:68-81`, `weblate/utils/cache.py:13-14`) - via
     `caches["default"].client.get_client(write=True)`, and three public
     operations, all verified against the installed redis-py:
     - acquire in the request: `client.set(key, token, nx=True, ex=ttl)`
       where `token` is the task id. `nx=True` makes it set-if-absent, so two
       simultaneous POSTs cannot both win;
     - refresh in the worker: a fixed application helper wrapping a script
       registered once with `client.register_script(...)` doing `GET` →
       compare token → `PEXPIRE`. This is byte-for-byte what redis-py's own
       `LUA_REACQUIRE_SCRIPT` does; registering it here keeps the plan off a
       private attribute rather than inventing a new mechanism;
     - release in the worker: the mirror helper, `GET` → compare token →
       `DEL`, identical to redis-py's `LUA_RELEASE_SCRIPT`; equivalently
       `Lock(client, key).do_release(token)`, which runs exactly that script
       with an explicit token.
     Both helpers return a boolean and **the caller must act on it** - a
     token mismatch is a lost lease, never a silent no-op:
     - refresh returned false: another run owns the reservation, and this is
       a **terminal** condition. The task finishes the unit already being
       written (it is inside a per-component transaction), applies **no
       further units**, does not start another component, returns
       `{"status": "failed", …}` with an "another run took over" message, and
       does **not** attempt a release, because the key is not its own. It
       does not retry either: retrying would re-enter a scope another run is
       already repairing;
     - release returned false: the lease had already lapsed, so the run was
       already terminal by then. The task records it and does not retry the
       release; the run still reports the outcome it actually produced.
     The worker refreshes on every progress tick of step 1 and releases on
     the two terminal paths of step 2 - never on the retry path, where it
     refreshes instead, because the backoff can exceed the lease. Because
     both scripts compare before acting, a lapsed holder can neither free nor
     extend a newer run. That is the compare-and-set the Django cache API
     does not offer.
   - **Non-Redis cache (locmem in tests, small single-process deployments).**
     No cross-process compare-and-set exists, so the guard degrades
     explicitly: `cache.add(key, task_id, timeout=FIX_CHECK_LOCK_TTL)` for the
     atomic enqueue refusal - `add` is set-if-absent on every backend, and a
     `get`-then-`set` pair is forbidden because two simultaneous POSTs would
     both pass it - and **no early release at all**; the reservation simply
     expires. A short `FIX_CHECK_LOCK_TTL` matters here, so the module
     constant is `FIX_CHECK_LOCK_TTL = 3600`, the same order as
     `TASK_METADATA_TTL` (`weblate/utils/celery.py:44`) and the same value
     `WeblateLock` uses for its Redis expiry (`weblate/utils/lock.py:48`).
     The TTL is a **lease**, not a run-duration estimate: nothing bounds a
     project-wide run, since this project configures no Celery time limit at
     all.
   - A failed publication releases the reservation in the same request on
     Redis, and on the degraded path leaves it to expire.
   - **The guarantee, stated honestly.** On Redis no second run can be queued
     for the same `(check_id, scope)` while a holder keeps refreshing its
     lease; a holder that stops making progress for longer than the lease -
     killed worker, stalled node - loses it, and a second run becomes
     possible. On a non-Redis cache the window is wider, because a finished
     run holds its reservation until expiry and a lapsed one cannot be
     distinguished. Either way the duplicate case is **bounded, not
     prevented**: `perform_fix` recomputes every candidate at apply time,
     skips rows that no longer fail the check, and is idempotent on re-apply
     (Task 2, step 6), so a duplicate run wastes work but cannot corrupt a
     target.
6. The only cancellation is the ordinary **Cancel** link before queueing. Do
   not promise undo, task cancellation or rollback after the task starts.

**Verify:** view and JS tests assert task metadata per scope, the completed
and failed result payloads and their poller mapping, that a terminal failure
is returned rather than raised, and a duplicate submit for the same
`(check_id, scope)` being refused while the first run holds the reservation.
The token-safe half is Redis-only, and `weblate/settings_test.py` does **not**
provide it: the default cache is LocMem (`:107`) and Redis is configured only
for the `avatar` cache (`:109-113`), which `is_redis_cache()` never consults.
That test therefore points the **default** cache at Redis itself with
`override_settings(CACHES={"default": {"BACKEND":
"django_redis.cache.RedisCache", "LOCATION": …}})` built from `CI_REDIS_HOST`
and skips when that variable is absent; Django resets `caches` on a `CACHES`
override, so `is_redis_cache()` sees the Redis backend. Under it: the lease is
re-armed by a progress tick so a run longer than `FIX_CHECK_LOCK_TTL` keeps
its reservation, and it survives a retry. The handoff-safety assertions are
the point of that test and must be explicit: with a newer token stored under
the key, both helpers return **false** for the stale token, the stored value
is still the newer token afterwards, and its TTL is unchanged; a false
refresh makes the run stop and report failure without releasing, and a false
release is recorded without retry. On the degraded non-Redis path the test
asserts the documented behaviour instead - atomic `add` refusal and
expiry-only release, with no early delete.
JS tests cover the ARIA updates; manual smoke in dev shows
queued → running → result progress with keyboard- and screen reader-visible
text.

## Task 5 - views, URLs, templates

**Files:** `weblate/trans/views/search.py`, `weblate/trans/forms.py`,
`weblate/urls.py`, `weblate/trans/checklists.py`,
`weblate/templates/snippets/translation.html`,
`weblate/templates/translate.html`, `weblate/templates/check_list.html`, new
`weblate/templates/fix_check.html`, `weblate/static/loader-bootstrap.js`.

1. Define an explicit `TranslationChecklistItem` data contract in
   `weblate/trans/checklists.py`. Today `add()` emits a six-element tuple
   (`:143-153`) and its only consumer unpacks those six values
   (`weblate/templates/snippets/translation.html:77-107`). Replace the tuple
   with a named item carrying the same display fields plus `check_id` and
   `mass_fixup`; only the `CHECKS` loop (`:110-112`) sets them, aggregate
   buckets (`all`, `translated`, labels) leave them `None`. Migrate the single
   unpacking loop in the same commit. Route construction uses `check_id`
   (`weblate/checks/base.py:65-87`), never the translated filter name
   (`weblate/trans/filter.py:107-110`).
2. Register the exact URL
   `path("fix-check/<name:name>/<object_path:path>/", fix_check,
   name="fix-check")`, matching existing converter and trailing-slash
   conventions (`weblate/urls.py:149,159,164,174,184,288`); the name is
   currently free. It passes only `(Translation, Component, Project)` to
   `parse_path_units`, so every other scope
   (`Workspace`, `Language`, `ProjectLanguage`, `Category`,
   `CategoryLanguage`, `weblate/utils/views.py:581-637`) is rejected before the
   view body. At the scope boundary it mirrors `bulk_edit`
   (`weblate/trans/views/search.py:239-286`): both `unit.bulk_edit` and
   `unit.edit` are required there, then Task 2 rechecks every unit. It 404s
   for an unknown check, a check without a tier, and a glossary component.
3. Render no Fix action on the unscoped `/checks/` page, `Workspace`, or any
   aggregate language/category/project-language page
   (`weblate/checks/views.py:54-63,91-129`): they have no approved contextual
   blast radius.
4. `fix_check.html` uses two explicitly different screens:
   - tier `safe`: check name/description, unambiguous scope line, count
     (`Будет исправлено N строк` using `ngettext`), the judge counter when it
     is nonzero, one CSRF-protected **Исправить N строк** button, and
     **Отмена**. No per-unit preview, samples or selection controls;
   - tier `review`: a `Показаны первые N применимых из M` notice whenever
     `remaining > 0`, aggregate manual/denied counts, the judge counter, and a
     `table-scroll` wrapper around the stable first-250 eligible rows. Each row
     has a labeled checkbox, translation/context, a pencil link to the unit
     editor, and the native Weblate diff from
     `format_unit_target unit value=fixed_target diff=unit.target` (the same
     `<ins>`/`<del>` renderer used for suggestions). `eligible` rows start
     unchecked. A deliberate **Выбрать все применимые на этой странице (N)**
     control selects only eligible rows; manual/denied strings are counts and
     a Browse link, not selectable rows. **Исправить выбранные (N)** submits
     only checked ids. Reload after a nonempty apply uses Task 2's
     continuation contract.
   Both screens state that the fix has no undo. POST re-validates selected ids
   against the live query and queues the task. The template owns each `<form>`,
   so `FixCheckConfirmForm` sets
   `self.helper = FormHelper(self); self.helper.form_tag = False`.
5. Three entry points:
   - translation status table: a short **Исправить** inline action beside
     Browse/Translate/Zen only for a row with `mass_fixup` and only when
     `object` is exactly `Translation`;
   - full editor check panel (`weblate/templates/translate.html:671-716`):
     **Исправить все такие строки** beside the existing per-unit **Исправить
     строку** (`:686-693`), scoped to `unit.translation`; `ellipsis` gains the
     per-unit button after Task 1;
   - scoped check overview: the same short inline action only when
     `path_object` is exactly `Component` or `Project`. It stays in the
     existing header cell (`weblate/templates/check_list.html:44-47`), so the
     table keeps five columns (`:34-40`) and the progress-row `colspan="5"`
     (`:88`) remains correct.
   Zen stays untouched - it has no checks panel.
6. Change the global `tr[data-href]` handler
   (`weblate/static/loader-bootstrap.js:2104-2108`) to return when the event
   target is inside `a`, `button`, `input`, `label`, `select` or `textarea`.
   This is a correctness fix for links that already exist
   (`check_list.html:46,50,59,68`, `snippets/translation.html:101,105-107`),
   not a workaround for the new one; do not add a `stopPropagation` handler
   for the new link alone.
7. Accessibility and responsive contract: use existing `.table-scroll` for the
   review table; keep the first actions/checkbox column sticky; retain visible
   focus at 200% zoom; bind every checkbox to a context/language label; convey
   eligibility and result by text as well as colour. Dense action cells retain
   short labels; the confirmation heading, scope line and button carry the full
   scope wording.

**Verify:** view tests cover checklist metadata, exact URL reverse and
trailing slash, all three allowed scopes and both scope permissions, rejection
of `Workspace`/`Language`/`ProjectLanguage`/`Category`/unscoped and of a
glossary component, safe confirmation, review selection, manual counts/Browse
link, the 250 continuation boundary, source-template authorization, the judge
counter rendering, and form-nesting depth. JS/browser smoke tests click every
new and existing nested row link, tab through a review table at 200% zoom, and
assert the row destination is not opened instead.

## Task 6 - i18n, docs, changelog, threat model

1. Split the new strings by catalog (ground rule 3): template and view strings
   into `weblate/locale/ru/LC_MESSAGES/django.po`, any progress/result string
   emitted from `weblate/static/*.js` into `djangojs.po`.
2. Avoid duplicate `msgid` entries - a duplicate with the same `msgctxt` is a
   fatal `compilemessages` error and an unbuildable production image. These
   already exist in `ru/django.po` and must be reused or disambiguated with
   `msgctxt`: `Fix string` (:17484), `Cancel` (:17893), `Scope` (:3375),
   `Language` (:3664 and contexted :11948), `Browse` (:11350), `Failed`
   (:13539), `Completed` (:13756), `Review` (contexted :4176, :25841), `Check`
   (:6369). `djangojs.po` already carries `Cancel` (:177). Run
   `env DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py compilemessages --locale ru`
   after the change and after every merge that touches a catalog.
3. Russian wording: **Исправить**, **Исправить строку**, **Исправить все
   такие строки**, scope headings, `ngettext` forms for **Будет исправлена
   %(count)d строка** / **Будут исправлены %(count)d строки** / **Будут
   исправлены %(count)d строк**, the four bucket labels, the judge counter,
   the first-N-of-M notice, and all queued/result/error messages.
4. Documentation: a section in `docs/admin/checks.rst` describing the two
   tiers, the per-`check_id` 250-row review batch, the source-cosmetic
   behaviour, both permissions, the judge counter, and that no post-apply undo
   exists. One `docs/changes.rst` entry in the unreleased section linking to
   that section.
5. Update `docs/security/threat-model.rst`: the feature adds an authenticated
   POST route that mass-mutates translation content and queues background
   work, which is exactly a condition the model names (`:1041-1045`). Record
   the blast radius per scope, both permissions, CSRF, the absence of undo,
   the concurrency guard, and that no API surface is exposed.
6. Update this plan's status; commit and push per repository convention.

**Verify:** `compilemessages --locale ru` succeeds; the explicitly named
pre-commit hooks for the touched file types run clean (never a bare
`prek run --files`, which can alter unrelated files); the docs build.

## Out of scope

- Running the actual fix on production (separate, explicitly approved
  operation after the feature is deployed).
- Every defect owned by the autofix layer (see "Ownership boundary").
  `reapply_autofixes` keeps its current behaviour and is not modified.
- `same`/`reused`/`multiple_capital`/`duplicate`/`newline-count`/`game-*`/
  `cyrillic-leak`/judge checks and `multiple_failures` - no deterministic fix,
  or nothing to point a fix at.
- `end_semicolon`: no measured occurrences, no fixup, no tier.
- Advisory checks and glossary components, permanently.
- A Zen-mode panel, API endpoints, or exposing mass fix to the REST API.
- Queueing judge re-checks (decision 9).

## Risks

| Risk | Mitigation |
|---|---|
| Fixup regex behaves differently in Python vs the JS single-unit button | parity test in Task 1, final-target parity after save-time autofix in Task 2, `KashidaCheck` left untiered while it uses `regex` syntax |
| A terminal fix trades one check for another (`?` → `?.`) | decision 7: before/after terminal-set comparison including `end_interrobang`, selected check must clear, no member may newly appear, overlap test in Task 1 |
| Two mass-mutation paths disagree | ownership boundary split by direction plus the two Task 1 idempotence tests |
| Source cascade suppression leaks into normal editing | keyword-only `mark_source_change_fuzzy=True` default, existing template-only branch, default-path regression tests |
| Source change lands in the database but not in the file | explicit pending-row construction in Task 3 step 3 and the `commit_pending()` test; failing that, the Task 3 decision gate removes source checks |
| A Fix anchor is captured by a clickable row | global interactive-descendant guard plus pointer/keyboard smoke tests |
| A review preview hides candidates or selects ambiguous changes | stable first-250 eligible contract, no default selections, explicit page-local select-all, manual bucket and continuation test |
| Preview goes stale before apply | apply recomputes from current DB state; stale units are skipped and reported |
| Mass fix misattributes history or appears revertible | dedicated action 106 in the content/show sets, excluded from revertable actions, history tests |
| Producers think judge state was lost | `verdicts_no_longer_current` shown in preview and result, documented in Task 6 |
| Large scopes block the request or pile up | apply always runs in Celery with progress; preview query is a single indexed `EXISTS` lookup; Task 4 concurrency guard |
| A failed task reads as success | explicit failure payload in Task 4 step 2 |

## Follow-ups (not conditions for approval)

1. **Yield measurement.** For one real component, count `eligible` versus
   `manual` per terminal check. Nothing in this plan or its review establishes
   that ratio, and the engine's own preview produces it on the first pass -
   which is why this is a follow-up rather than a gate. Result belongs in
   `docs/product/measurements/`. If the manual share turns out to dominate,
   revisit whether the review tier earns its UI.
2. **Preventing source-side `...` at the point of entry.** Normalizing
   ellipses where source text is produced (loc-kit/converter) would stop new
   occurrences. It is **preventative only and not a substitute for Task 3**:
   the loc-kit update flow deliberately never changes the source of an
   existing key
   (`docs/product/plans/2026-08-18-loc-kit-table-add-strings.md:29-36`), and
   nothing establishes that every affected source string came through that
   import path. Separate proposal, complementary, does not block this plan.
3. **`multiple_failures` as triage.** The roll-up would be more useful as a
   drill-down to the primary checks and affected languages it already knows
   (`weblate/checks/source.py:143-178`) than as a row in the failing-check
   list. Separate proposal; explicitly not a mass-fix action.
