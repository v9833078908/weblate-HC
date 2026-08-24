# Plan 03: Judge Decisions and Whitebox

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this
> plan task-by-task. Use `test-driven-development`,
> `browser:control-in-app-browser`, `requesting-code-review`, and
> `verification-before-completion` where specified below.

**Date:** 2026-08-24. **Revised:** 2026-08-24 after architecture, product,
and code-feasibility review. **Status:** proposed, awaiting implementation
approval.

**Series:** third slice of the judge UI series defined in
`docs/llm-first/designs/2026-08-13-judge-native-ui-design.md` (row
«3. Решения и белый ящик»), after:

- `docs/llm-first/plans/2026-08-13-01-judge-verdict-core.md` (implemented);
- `docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md`
  (approved, implementation pending).

**Dependency:** Plan 02 must land in full before this plan starts. Plan 03
extends, rather than parallels:

- migration `0103_judge_target_storage_hash` and its exact raw-target
  fingerprint;
- `judge_status_annotations()`, the one current-round query contract shared
  by search and stats;
- migration `0104_llm_usage_operation`;
- the component readiness rows and template from Plan 02 Task 8.

The next translation-app migration is therefore expected to be
`0105_judge_decisions_whitebox.py` with dependency
`0104_llm_usage_operation`. If the migration graph differs when execution
starts, use the actual next migration and update this plan's references in
the same documentation commit; never create a parallel migration leaf.

**Goal:** Let a producer close every current-target critical judge hold by
an auditable decision—accept the exception, send the string back to work, or
escalate it—and inspect the exact persisted evidence for every judged
attempt.

**Architecture:** A resolution is a terminal property of one exact
current-target critical round. `record_judge_resolution()` locks the unit
and every row of that round, writes one immutable decision to every seat,
and changes state through a new state-only Unit seam. That seam preserves
the raw target, appends the required pending state transition, and retains
deterministic enforcement. A same-target resolved round remains cached
evidence but can neither trigger repair nor reapply a negative state;
editing the exact raw target is the only automatic way back into the judge
flow.

Whitebox evidence is persisted when the verdict is written, never
reconstructed from mutable current data. `JudgeVerdict` gains a nullable,
versioned `input_snapshot` containing the exact segment payload sent to the
judge and a `prompt_fingerprint` of the rendered system prompt. Historical
rows without a snapshot are labelled honestly. No new round model, raw
prompt, project-context text, credential, endpoint, or provider response is
stored.

Search, stats, readiness, checks, and the unit page all read the same
current-round-plus-resolution semantics. Delivery remains a separate axis
calculated from `PendingUnitChange` and the project commit policy; neither a
judge verdict nor a human resolution is a release guarantee.

**Tech stack:** Django 6, PostgreSQL, Bootstrap 5, crispy-bootstrap5,
jQuery/vanilla JavaScript patterns already used by the editor, and pytest
via `./rundev.sh test`.

---

## Review decisions incorporated

| Finding | Decision in this revision |
| --- | --- |
| The cited “direct state mechanism” called `Unit.translate()` and reran autofixes | Add an explicit state-only Unit seam; never call `translate()` for a resolution |
| A direct `save(state=...)` would miss `PendingUnitChange`, `original_state`, enforcement, and stats | The state-only seam owns all of those effects and emits one normal `Change` |
| A repeat judge run could reuse a resolved reject and demote the unit | Resolved same-target rounds are terminal: no provider call, repair, or state application |
| Whitebox promised inputs and repair history that were not stored | Persist a versioned input snapshot and prompt fingerprint at verdict-write time |
| Runtime schema stores `error["span"]`, not offsets | Derive a highlight only from one exact, unambiguous string match |
| Blocking one Dismiss endpoint left source-wide and flag-based bypasses | Refuse both endpoints and make judge checks ignore-proof |
| Plan 02 already owns exact-target annotations | Extend `judge_status_annotations()`; never add parallel raw `Exists` predicates |
| Producer decisions disappeared into raw search syntax | Surface unresolved, override, and escalated counts as exact readiness links |
| UI assumptions referenced Bootstrap 3 and stale paths | Use current Bootstrap 5 markup, existing tests, and actual stylesheet paths |
| The design left `unit.review` as an open hypothesis | Treat approved Plan 02 as the later decision: review-enabled `unit.review` is the access gate; Task 9 reconciles the design text |

---

## Contract

### Current round and exact-target identity

A decision may be recorded only when all of the following hold under one
database transaction:

1. the unit row is locked with `select_for_update()`;
2. the deciding round is the newest parsed collegium round selected by the
   shared Plan 02 semantics for the exact stored `Unit.target`;
3. every deciding row has a non-null `target_storage_hash` equal to
   `compute_target_storage_hash(locked.target)`;
4. the strictest parsed seat maps to `reject` / `critical`;
5. every row in that `(unit, run_id, attempt)` round is unresolved.

`flag` / `major` remains Plan 02 advisory evidence and has no decision
buttons or resolution queue. The separate Delivery column remains
authoritative if project policy happens to hold an advisory target.

The older SHA-256 `target_hash` remains audit evidence and is written to
`Change.details`, but it is not a sufficient write guard. Plan 02 introduced
`target_storage_hash` because `get_target_plurals()` can normalize the
stored plural representation.

Resolution identity is target-only: `target_storage_hash` participates in
the binding, while `context_hash` does not. A context-only
source/note/glossary change therefore preserves unresolved/resolved queue
membership and shows the existing context-change warning. Resolution
writers, annotations, search, stats, and readiness must all use Plan 02's
target-only active-round semantics, not context-sensitive
`current_round()`/`current_verdict()`. An exact target edit makes the round
stale, removes it from decision filters, and permits a fresh judge round.

### Decision semantics

Exactly three decisions exist, from `JudgeVerdict.Resolution`:

1. **Accept as is** (`accepted_as_is`)
   - A trimmed, non-empty reason is mandatory.
   - The unit requests `STATE_APPROVED` (30). The `unit.review` permission
     gate already refuses decisions when translation review is disabled.
   - This overrides only the judge opinion. If a deterministic enforced
     check fails, standard enforcement leaves the unit at
     `STATE_NEEDS_REWRITING` (11), and Delivery remains blocked.
   - The `judge-reject` projection clears, while the negative verdict and
     human resolution remain immutable evidence.
   - Copy reads **Accepted by <user> against the judge verdict**, never
     “approved by AI”.

2. **Send back** (`sent_back`)
   - Reason is optional, but the UI always offers the field.
   - State becomes `STATE_FUZZY` (10).
   - The `judge-reject` projection remains visible as repair evidence.
   - The same target is not judged again: a translator or MT must store a
     different target, producing a new exact-target identity and a fresh
     round. There is no mutable repair-attempt counter to reset.
   - This explicitly supersedes the design shorthand “reset attempts and
     start a new judge cycle”: the implemented data model has no resettable
     counter, and reusing unchanged negative evidence would immediately
     reproduce the same hold.

3. **Escalate** (`escalated`)
   - Reason is optional, but the UI always offers the field.
   - State and Delivery are unchanged. The decision itself is neither a
     release blocker nor a release approval.
   - The `judge-reject` projection remains visible.
   - The unit leaves `judge:exhausted` and becomes discoverable through
     `judge:escalated` and an exact readiness link.

### Atomic state-only write

One call to:

```python
def record_judge_resolution(
    unit: Unit,
    user: User,
    resolution: str,
    reason: str = "",
) -> JudgeVerdict: ...
```

must atomically:

1. lock and re-read the unit;
2. enforce `unit.review` on that locked target translation;
3. locate and lock every row of the deciding round;
4. validate exact-target, critical, unresolved, and reason invariants;
5. write the same `resolution`, trimmed `resolution_reason`,
   `resolved_by`, and one shared `resolved_at` to every seat row, including
   an unparsed seat in an otherwise parsed round;
6. apply the requested state without calling `Unit.translate()`,
   `adjust_plurals()`, `fix_target()`, propagation, or translation-memory
   update;
7. preserve `Unit.target` byte-for-byte and preserve
   `automatically_translated`;
8. when the requested state differs, update both `state` and
   `original_state`;
9. when the requested state differs, append the `PendingUnitChange` needed
   to make the new state visible to Delivery, then apply normal
   deterministic enforced-check demotion to the Unit and that new pending
   row;
10. create exactly one dedicated resolution `Change` through the normal
    Unit history seam, with the standard state/source/context details plus:

    ```json
    {
      "resolution": "accepted_as_is|sent_back|escalated",
      "reason": "...",
      "run_id": "...",
      "attempt": 1,
      "target_hash": "...",
      "target_storage_hash": "..."
    }
    ```

11. refresh checks when projection can change;
12. schedule translation-stat invalidation with
    `transaction.on_commit(..., robust=True)` so an invalidation failure
    cannot roll back recorded evidence.

When escalation requests no state or the requested state already equals the
stored state, the writer does not save Unit, touch `original_state` or
`last_updated`, or create `PendingUnitChange`. It writes only the locked
verdict rows, one required resolution `Change`, and cache invalidation. No
implicit translation/edit/approve `Change` is allowed.
If deterministic enforcement demotes an accepted exception, its existing
`ENFORCED_CHECK` audit event remains allowed and expected; it is not a
second producer-decision event.

Invalid input raises `JudgeResolutionError` with a translated,
user-actionable message. The function never partially resolves a round and
never overwrites or deletes a resolution.

### Terminal same-target semantics

A resolved active round remains reusable evidence but is terminal:

- `_process_round_unit()` must not repair it;
- `AutoTranslate.process_judge()` must not derive or apply state from it;
- a custom judge query that includes it makes no provider call and no write;
- check/search/stats/readiness read its resolution;
- an exact target edit is the only automatic reopening mechanism.

An explicit “reopen resolution” action is not part of this plan.

### Persisted whitebox snapshot

`JudgeVerdict` gains:

```python
input_snapshot = models.JSONField(null=True, blank=True)
prompt_fingerprint = models.CharField(max_length=64, blank=True)
```

The migration leaves both fields empty on historical rows. Runtime writes
one snapshot per round/attempt on seat 1; seat 2 retains its output evidence
but does not duplicate the input JSON. The snapshot has a closed, versioned
shape:

```json
{
  "version": 1,
  "segment": {
    "id": 0,
    "key": "...",
    "source": "...",
    "target": "...",
    "rendered_source": "...",
    "rendered_target": "...",
    "note": "...",
    "glossary": [{"source": "...", "target": "..."}],
    "checks": ["..."]
  },
  "source_language": "...",
  "target_language": "..."
}
```

Optional segment keys are omitted exactly as in the actual request. The
snapshot builder is shared with `request_verdicts()` so tests can prove that
persisted input and sent segment do not drift. The exact snapshot and
fingerprint are attached to each typed result/envelope inside
`request_verdicts()`, where the batch-local `segment.id` is known.
`_write_verdict()` persists that attached evidence; it never rebuilds a
snapshot from `JudgeRequest` after the call.

`prompt_fingerprint` is SHA-256 of the exact rendered system prompt,
including language and project context. The raw prompt and project-context
text are not persisted or displayed. The snapshot contains only Unit data
already visible to an authorized viewer; it must never contain a key, base
URL, provider headers, response payload, or configuration secret.

History groups rows by `(run_id, attempt)`. When consecutive attempts of one
run have snapshots with different exact targets, the UI renders an
**applied repair between judged attempts**. A rejected, rolled-back, or
no-regress candidate which never became a judged target is not invented and
is outside this plan's forensic history.

Legacy rows render **Input snapshot was not recorded for this historical
round**. Current mutable Unit/glossary/note data is never substituted for
missing historical evidence.

### String-span highlighting

The measured strict reply schema remains unchanged:

```json
{"span": "...", "category": "...", "severity": "...", "description": "..."}
```

There are no `span_start`/`span_end` fields. For each error, highlighting is
attempted against both raw and rendered target snapshots:

- a non-empty `span` must occur exactly once across every plural form and
  exactly one representation;
- that unique match becomes one
  `(representation, plural_index, start, end)` range;
- zero matches, duplicate matches, a match in both representations, invalid
  data, missing snapshot, or overlaps that cannot be merged safely produce
  no highlight and no exception;
- `description`, category, and severity always render.

This is a display-only degradable function. Changing the reply schema or
prompt to produce offsets requires a separately measured R3 slice.

### Dismiss is impossible for judge checks

The mandatory-reason decision path is the only judge override:

- `ignore_check` refuses `judge-flag` and `judge-reject`;
- `ignore_check_source` refuses them before writing `ignore-judge-*`;
- `BaseJudgeCheck.is_ignored()` returns false, making existing
  `ignore-judge-*` and `ignore-all-checks` flags inert for judge
  projections;
- the migration resets historically dismissed judge `Check` rows to
  `dismissed=False` without touching unrelated checks;
- no template renders Dismiss/Reset controls for judge checks;
- judge checks remain absent from **Things to check** and live in their
  separate AI evaluation surface.

Deterministic checks retain their existing dismiss behavior.

### Permissions and POST integration

All decisions require `unit.review` on the target translation, matching the
approved Plan 02 access gate. Because that permission is disabled when
translation review is off, the decision workflow is unavailable there.
Permission is authorization only and never quality or release evidence.

The decision form posts to the existing translate URL. The translate view
detects a distinct `judge_resolution` marker and delegates to
`handle_judge_resolution()` before the ordinary translation handler. No new
public route or REST API is added. The form carries the current Unit
`checksum` so normal translate navigation cannot retarget a decision when
search results or offsets change between render and POST.

Anonymous and authenticated unauthorized POSTs return 403 and write nothing.
An invalid form or `JudgeResolutionError` re-renders the same unit with
field/non-field errors and the Bootstrap 5 modal reopened. A successful POST
uses POST/Redirect/GET to the exact same unit URL.

### Decision UI

The verdict card renders three real buttons only for a current-target,
unresolved reject and a user with `unit.review`. All three open one
accessible Bootstrap 5 confirmation modal:

- JavaScript copies the button's resolution into a hidden ChoiceField;
- the reason textarea is always visible;
- accept marks the reason as required in explanatory copy, while server-side
  validation remains authoritative;
- send back and escalate explain their state and Delivery effects;
- focus moves to the modal heading or first invalid field on open and
  returns to the trigger on close;
- validation failure automatically reopens the modal.

`JudgeResolutionForm` sets `FormHelper(self)` and
`helper.form_tag = False` because the template owns the one explicit form.
The whole rendered page must never contain nested forms.

Recorded resolution renders once per round with decision, user, absolute
timestamp, reason, and exact-target status.

### Shared search, stats, and readiness semantics

Plan 03 extends `judge_status_annotations()` with the active resolution of
the exact current-target parsed round. Search and stats consume that
annotation; they do not add independent `Exists(JudgeVerdict)` logic.

Filters:

- `judge:exhausted` — current-target `reject` / `critical` with no
  resolution;
- `judge:override` — current-target `reject` resolved
  `accepted_as_is`;
- `judge:escalated` — current-target `reject` resolved `escalated`.

`sent_back` needs no filter: it returns to the ordinary
`state:<translated` work queue. All decision filters compose with language,
translation, negation, and Zen scoping.

New lazy translation stats:

- `judge_exhausted`;
- `judge_override`;
- `judge_escalated`.

Every key equals its exact search-filter count. Recording any resolution
invalidates them.

Readiness preserves separate Delivery and AI axes:

- **Advisory - ships** remains Plan 02's `judge_flag` evidence row;
- **Held for decision** uses `judge_exhausted` rather than raw
  `judge_reject`;
- **Accepted against judge** links to `judge:override`;
- **Escalated** links to `judge:escalated`;
- raw reject evidence remains queryable through `judge:reject` and visible
  on the unit card/history; it is not a second readiness count and may
  overlap a resolution filter;
- primary action priority is uncovered/stale, exhausted, escalated,
  commit-policy-held, advisory, then no action.

An accepted, sent-back, or escalated unit leaves **Held for decision**
without another judge call. Accepted exceptions and escalations never
disappear into undiscoverable raw query syntax.

Operationally, `judge_exhausted == 0` is necessary but not sufficient for a
release decision. Accepted exceptions still depend on deterministic checks
and Delivery; sent-back strings require a changed target and a fresh judge
round; escalations remain an explicit stop for external disposition and are
prioritized in readiness. The UI never collapses those independent facts
into a “ready” boolean.

### Copy constraints

Never render:

- **Approved by AI**;
- **AI approved**;
- **Ready for release**;
- a numerical AI confidence score;
- raw internal state numbers;
- raw prompts, JSON schema, credentials, endpoints, or project-context text.

Use visible text in addition to color. Judge opinion uses an outline;
deterministic fact uses a filled badge. If a unit has both, deterministic
fact wins the unit-state cell styling.

### Out of scope

- Any change to `JUDGE_MAY_APPROVE` or automatic approval.
- Judge reply schema or prompt changes.
- A new JudgeRound/UnitEvaluation model.
- Backfilling historical input snapshots from mutable data.
- Logging rejected or rolled-back repair candidates which were never judged.
- Resolution REST API or a new POST route.
- Zen editor decision buttons.
- Explicit reopen/delete/overwrite of a resolution.
- External-linguist workflow beyond filter/readiness discovery.
- Project/category/workspace dashboards or live readiness polling.
- Production enablement, deploy, shared dev-stack rebuild, or paid calls.

---

## Task 1: Persist whitebox input and resolution audit actions

**Files:**

- Modify: `weblate/trans/actions.py`
- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Create: `weblate/trans/migrations/0105_judge_decisions_whitebox.py`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/tests/test_judge_migration.py`

### Step 1: Write failing action and migration tests

Add three `ActionEvents` with the next free values at execution time
(expected 105–107): `JUDGE_ACCEPTED`, `JUDGE_SENT_BACK`, and
`JUDGE_ESCALATED`. Use the existing three-item IntegerChoices tuple shape.
Persisted audit descriptions are English and not localized.

Migration tests start from Plan 02 migration 0104 and prove:

- snapshot fields are empty for historical verdicts;
- no existing verdict, hash, resolution, or user value changes;
- dismissed `judge-flag`/`judge-reject` rows become non-dismissed;
- unrelated dismissed checks remain unchanged;
- `Change.action` exposes all three new choices.

Dependencies:

```python
dependencies = [
    ("checks", "0002_check_active_unit_index"),
    ("trans", "0104_llm_usage_operation"),
]
```

The migration performs no snapshot backfill.

### Step 2: Prove RED, add fields/migration, prove GREEN

```bash
./rundev.sh test weblate/trans/tests/test_judge_migration.py
```

Add `input_snapshot` and `prompt_fingerprint` exactly as specified, include
`AlterField` for `Change.action` choices, and add the scoped dismissed-check
cleanup. Rerun and expect PASS.

### Step 3: Write failing snapshot/fingerprint tests

Pin:

- snapshot version 1 and `segment` exactly equal the outbound dictionary;
- raw/rendered target, note, glossary, checks, key, languages, Unicode, and
  plural separators survive unchanged;
- absent optional segment keys remain absent;
- no key, endpoint, header, raw prompt, provider response, or
  project-context text is present;
- fingerprint is 64 lowercase hex characters;
- it changes with rendered prompt/language/project context but not
  model/seat;
- seat 1 stores snapshot/fingerprint; seat 2 stores neither;
- unparsed seat 1 still stores input;
- Plan 01/02 hash writes remain unchanged.

### Step 4: Implement one shared snapshot builder

Expose one helper in `weblate/trans/judge.py` which builds the outbound
segment. Add a fingerprint helper over the exact result of `_load_prompt()`.
Inside `request_verdicts()`, attach the exact batch-local segment snapshot
and fingerprint to each parsed or unparsed typed result/envelope. Pass that
evidence into `_write_verdict()` and persist it only for seat 1. Do not
rebuild the snapshot in `judge_loop.py`.

### Step 5: Verify and commit

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_migration.py
uv run ./manage.py makemigrations --check --dry-run
git add \
  weblate/trans/actions.py \
  weblate/trans/models/judge.py \
  weblate/trans/judge.py \
  weblate/trans/judge_loop.py \
  weblate/trans/migrations/0105_judge_decisions_whitebox.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_migration.py
git commit -m "feat(judge): persist whitebox input evidence"
```

---

## Task 2: Atomic resolution writer and terminal rerun semantics

**Files:**

- Modify: `weblate/trans/models/unit.py`
- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/autotranslate.py`
- Create: `weblate/trans/tests/test_judge_resolution.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/tests/test_judge_autotranslate.py`
- Modify: `weblate/trans/tests/test_models.py`

### Step 1: Pin the state-only Unit seam

Write failing tests for a transaction-only
`store_state_without_target_change()` helper:

- `fix_target()` is patched to raise and is never called;
- raw singular/plural target is byte-identical;
- `automatically_translated` is unchanged;
- `state` and `original_state` change together;
- one append-only `PendingUnitChange` carries exact target/final state;
- exactly one dedicated resolution `Change` uses the supplied
  action/details; deterministic enforcement may additionally retain its
  existing `ENFORCED_CHECK` event;
- no propagation or translation-memory update occurs;
- deterministic enforced failure demotes Unit and new pending row to state
  11;
- existing `Unit.translate()` enforcement tests remain unchanged after
  extracting shared enforcement bookkeeping.

The helper assumes the caller owns a row lock inside
`transaction.atomic()`. It reuses `save_backend()` and existing enforcement;
it does not copy that logic into `models/judge.py`.

### Step 2: Prove RED, implement the seam, prove GREEN

```bash
./rundev.sh test weblate/trans/tests/test_models.py -k "state_without_target or enforced"
```

### Step 3: Write failing resolution tests

Cover:

- accept empty/whitespace reason raises; stored reason is trimmed;
- send back/escalate allow optional trimmed reason;
- unknown resolution raises;
- no `unit.review`, review disabled, read-only, flag/pass, absent, stale,
  raw-target-mismatched, all-unparsed-only, null-storage-hash, or
  already-resolved evidence raises;
- every row of the exact round gets identical resolution fields/timestamp;
- accept requests state 30;
- accept plus deterministic enforced failure ends in Unit/pending state 11;
- send back ends state 10; escalation or an already-state-10 send back does
  not save Unit, normalize `original_state`, update `last_updated`, or append
  pending rows;
- all decisions create one dedicated resolution `Change` with standard
  details and the resolution audit subset; an enforced accept additionally
  retains the existing `ENFORCED_CHECK` event;
- target bytes, hashes, and `automatically_translated` never change;
- generic and judge stats invalidate after commit;
- a failing robust invalidation callback cannot roll back evidence;
- stale-unit and duplicate-writer paths leave all rows untouched.

Add a context-only drift case: it retains the same target-bound decision
eligibility and resolution bucket while rendering the independent warning.

Use `TransactionTestCase` with two connections and a barrier for one true
duplicate-decision serialization case.

### Step 4: Implement `record_judge_resolution()`

Follow the contract order. Use locked ORM instances, not the passed Unit.
Bulk-update only locked deciding-row primary keys. If state is already
unchanged, use the normal Unit change generator with `check_new=False` so
there is one decision history row and no fake content edit.

### Step 5: Pin and implement terminal reruns

For each resolution, rerun judge mode with a custom query selecting the same
unit and assert zero provider/repair calls and zero state/target/pending/
history changes. Then edit target normally and assert a new unresolved round
can be judged.

Implement both guards:

- `_process_round_unit()` returns resolved evidence without repair;
- final state application skips a resolved current verdict.

Do not make `_cached_verdict()` return `None` for resolved evidence; that
would convert a terminal human decision into a paid call.

### Step 6: Verify and commit

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge_resolution.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_models.py -k "judge or state_without_target or enforced"
git add \
  weblate/trans/models/unit.py \
  weblate/trans/models/judge.py \
  weblate/trans/judge_loop.py \
  weblate/trans/autotranslate.py \
  weblate/trans/tests/test_judge_resolution.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_models.py
git commit -m "feat(judge): record terminal producer resolutions"
```

---

## Task 3: Resolution-aware projection and complete Dismiss closure

**Files:**

- Modify: `weblate/checks/judge.py`
- Modify: `weblate/trans/views/js.py`
- Modify: `weblate/checks/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_edit.py`
- Modify: `weblate/trans/tests/test_judge_resolution.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing projection tests

Pin:

- unresolved, sent-back, and escalated rejects project `judge-reject`;
- accepted reject projects neither judge check;
- advisory `judge-flag` behavior stays unchanged and unresolved;
- `run_checks()` removes reject projection after accept;
- `ignore-judge-*` and `ignore-all-checks` never suppress judge projection;
- deterministic check ignore behavior remains unchanged;
- no judge check renders in **Things to check**, including beside a
  deterministic failure.

### Step 2: Write failing endpoint tests

For both `js-ignore-check` and `js-ignore-check-source`:

- a fully privileged user receives translated JSON 400 for a judge check;
- dismissed/source flags remain unchanged;
- deterministic fixture still dismisses normally;
- the “revert” variant cannot reopen a judge-dismiss path.

Use one shared endpoint predicate and reject before `set_dismiss()` or flag
mutation.

### Step 3: Implement, prove GREEN, commit

Override judge-check ignore semantics in `BaseJudgeCheck` and guard both
endpoints.

```bash
./rundev.sh test \
  weblate/checks/tests/test_judge.py \
  weblate/trans/tests/test_edit.py \
  weblate/trans/tests/test_judge_resolution.py \
  weblate/trans/tests/test_judge_views.py -k "judge and (check or dismiss or things)"
git add \
  weblate/checks/judge.py \
  weblate/trans/views/js.py \
  weblate/checks/tests/test_judge.py \
  weblate/trans/tests/test_edit.py \
  weblate/trans/tests/test_judge_resolution.py \
  weblate/trans/tests/test_judge_views.py
git commit -m "fix(judge): make producer resolution the only override"
```

---

## Task 4: Accessible decision UI on the existing translate POST path

**Files:**

- Modify: `weblate/trans/forms.py`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/static/editor/full.js`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing form and permission tests

Add `JudgeResolutionForm` with hidden `resolution` ChoiceField and visible
`reason` Textarea. Pin:

- only three enum values validate;
- accept requires a trimmed reason;
- other decisions preserve an optional trimmed reason;
- `FormHelper(self)` and `helper.form_tag = False` are set.

Translate-view tests pin:

- GET marker never writes;
- anonymous/unauthorized POST receives 403;
- review-disabled or read-only translation refuses the workflow;
- successful decision redirects to the same exact unit and flashes one
  translated message;
- checksum mismatch or a shifted search offset cannot resolve a different
  Unit;
- invalid form/`JudgeResolutionError` re-renders status 200 with errors,
  writes nothing, and marks modal reopen;
- CSRF is enforced;
- no provider or repair call occurs.

### Step 2: Write nesting/card tests

Tokenize full-page form start/end tags; assert depth never exceeds one and
finishes at zero. Also assert exactly one decision form, its submit button
inside it, and no card buttons for flag/pass/stale/resolved/unauthorized
units.

### Step 3: Implement the existing-route handler

Add `handle_judge_resolution()` beside other per-unit handlers and call it
when `judge_resolution` is present, before ordinary translation handling.
Keep the bound form in context on error. Do not add a URL.

### Step 4: Implement one Bootstrap 5 modal

Use `data-bs-*` and current modal structure. Minimal editor JavaScript sets
the hidden resolution, swaps translated plain-text copy, reopens a bound
invalid modal, and manages focus. The textarea is visible for every action.

### Step 5: Verify, browser-test, revert-and-prove, commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py
```

On an already-running local stack, use
`browser:control-in-app-browser` and real clicks for validation failure,
accept, send back, escalate, and keyboard focus. Do not start/rebuild the
shared stack or call a provider. If no local fixture-held unit exists, record
that limitation and rely on the automated browser-facing regression.

Temporarily remove `helper.form_tag = False`, observe the nesting test fail,
restore, and rerun PASS.

```bash
git add \
  weblate/trans/forms.py \
  weblate/trans/views/edit.py \
  weblate/templates/snippets/judge-verdict.html \
  weblate/static/editor/full.js \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): add producer decision workflow"
```

---

## Task 5: Persisted AI evaluation history

**Files:**

- Modify: `weblate/templates/translate.html`
- Create: `weblate/templates/snippets/judge-history.html`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing history-context tests

Fetch all rows with one ordered `select_related("resolved_by")` queryset and
group in Python. Pin:

- no rows means no tab/pane and no separate `exists()` query;
- reverse-chronological `(run_id, attempt)` groups contain every seat;
- snapshot/fingerprint render once from seat 1;
- errors/back-translation remain per-seat;
- resolution renders once;
- exact target and context staleness labels are distinct;
- legacy null snapshot shows an unavailable message and never current data;
- consecutive snapshot targets in one run produce one applied-repair diff;
- no diff is invented across runs/missing/identical snapshots;
- history fetch stays one bounded query.

### Step 2: Write content/secrecy tests

Rendered history includes exact raw/rendered target, key, note, glossary,
deterministic checks, language pair, fingerprint, model, seat, timestamp,
parsed state, severity, descriptions, BT, and resolution.

Assert absence of configured key, base URL, raw prompt sentinel,
project-context text, provider response, and forbidden approval copy.

### Step 3: Implement, prove GREEN, commit

Use Bootstrap 5 tab semantics. The pane is read-only with no AJAX or
controls.

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py -k "history or whitebox or secret"
git add \
  weblate/templates/translate.html \
  weblate/templates/snippets/judge-history.html \
  weblate/trans/views/edit.py \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): show persisted AI evaluation history"
```

---

## Task 6: Degradable span highlighting, render preview, and opinion badge

**Files:**

- Modify: `weblate/trans/templatetags/translations.py`
- Modify: `weblate/templates/snippets/judge-history.html`
- Modify: `weblate/static/styles/main.css`
- Create: `weblate/trans/tests/test_judge_spans.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing string-span tests

Create a pure helper returning
`(representation, plural_index, start, end)`. Pin:

- unique exact raw or rendered occurrence highlights;
- empty/non-string/missing/zero-match span does not;
- duplicate within one form does not;
- match in both raw/rendered does not;
- unique one-plural match maps correctly;
- matches in two plurals are ambiguous;
- overlapping ranges merge safely or degrade;
- missing legacy snapshot does not highlight;
- descriptions always render;
- script-like span text cannot inject markup.

Do not add offset fixtures production cannot produce.

### Step 2: Integrate with Formatter and badge priority

Pass explicit ranges as a fourth Formatter parser input; search only
persisted snapshot data and emit only constant formatter markup.

Pin unit-state styling:

- judge-only failure gets outline opinion class;
- deterministic-only stays filled;
- both use filled deterministic priority;
- accept removes opinion styling after projection refresh.

### Step 3: Verify and commit

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge_spans.py \
  weblate/trans/tests/test_judge_views.py -k "span or badge or preview"
git add \
  weblate/trans/templatetags/translations.py \
  weblate/templates/snippets/judge-history.html \
  weblate/static/styles/main.css \
  weblate/trans/tests/test_judge_spans.py \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): add degradable evidence highlighting"
```

---

## Task 7: Extend shared annotations, filters, and decision stats

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/tests/test_judge_round.py`
- Modify: `weblate/utils/search.py`
- Modify: `weblate/utils/tests/test_search.py`
- Modify: `weblate/trans/filter.py`
- Modify: `weblate/trans/tests/test_widgets.py`
- Modify: `weblate/utils/forms.py`
- Modify: `weblate/trans/tests/test_judge_form.py`
- Modify: `weblate/utils/stats.py`
- Modify: `weblate/utils/tests/test_stats.py`

### Step 1: Extend shared annotation tests

Cover unresolved/accepted/escalated/sent-back critical rounds, resolved old
target, all-unparsed fallback, context-only drift, and inconsistent partial
resolution. Context-only drift must preserve the exhausted/override/
escalated bucket while exposing the independent warning. Add nullable
`judge_active_resolution` selected from the same target-only round as
`judge_active_severity`. Inconsistent seat resolutions are unavailable
rather than guessed.

```bash
./rundev.sh test weblate/trans/tests/test_judge_round.py
```

Implement through Plan 02 annotations; introduce no raw
`Exists(JudgeVerdict)` search/stats query.

### Step 2: Add filters and discoverable choices

Pin exact results, negation, language/translation composition, and target
edit for `judge:exhausted`, `judge:override`, and `judge:escalated`.

Register producer labels in `FILTERS` and the judge-mode hardcoded filter
choices. Replace Plan 02's prominent Held query from raw `judge:reject` to
`judge:exhausted`; retain `judge:reject` as evidence.

```bash
./rundev.sh test \
  weblate/utils/tests/test_search.py \
  weblate/trans/tests/test_widgets.py \
  weblate/trans/tests/test_judge_form.py -k judge
```

### Step 3: Add lazy stats

Add `judge_exhausted`, `judge_override`, and `judge_escalated` to Plan 02's
separate `JUDGE_KEYS` calculation. Pin equality with filters, one-pass
calculation, resolution invalidation, target-edit removal, and no
project/global stats.

```bash
./rundev.sh test weblate/utils/tests/test_stats.py -k judge
```

### Step 4: Commit

```bash
git add \
  weblate/trans/models/judge.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/utils/search.py \
  weblate/utils/tests/test_search.py \
  weblate/trans/filter.py \
  weblate/trans/tests/test_widgets.py \
  weblate/utils/forms.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/utils/stats.py \
  weblate/utils/tests/test_stats.py
git commit -m "feat(judge): query producer decision states"
```

---

## Task 8: Drain readiness while retaining decision visibility

**Files:**

- Modify: `weblate/trans/views/basic.py`
- Modify: `weblate/templates/snippets/judge-readiness.html`
- Modify: `weblate/trans/tests/test_views.py`
- Modify: `weblate/trans/tests/test_judge_views.py`
- Modify: `weblate/trans/tests/test_remote.py`
- Modify: `docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md`

### Step 1: Write failing readiness/Delivery tests

Pin:

- Held equals `judge_exhausted`, not raw reject/check count;
- accepted and escalated counts have exact scoped links;
- sent back appears in neither decision count;
- target edit removes resolved counts;
- Delivery stays equal to Plan 02 batched pending counts;
- accepted pending state 30 is eligible under
  `WITHOUT_NEEDS_EDITING` and `APPROVED_ONLY`;
- send-back/escalated state 10 remains held under both policies;
- accepted plus deterministic enforcement produces state/pending 11 and
  remains held under both policies;
- scalar/batched pending counts agree and translations never mix;
- escalation never claims to change Delivery;
- component query growth is bounded and templates execute no query.

### Step 2: Pin copy/action priority

Retain Plan 02 **Advisory - ships** for `judge_flag`. Add visible
**Accepted against judge** and **Escalated** exact links. Priority:
Evaluate, Review exhausted, Open escalations, native commit-policy action,
Inspect advisory, no action. Remove Plan 02's interim no-resolution caption
and preserve all forbidden-copy assertions.

### Step 3: Implement, prove GREEN, amend Plan 02, commit

Build links/rows in `show_component()`; template only renders prepared data.
Amend Plan 02 contract/readiness assertions with an explicit “superseded by
Plan 03 Task 8” note rather than silent divergence.

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_views.py \
  weblate/trans/tests/test_remote.py -k "judge or readiness or detailed_count"
git add \
  weblate/trans/views/basic.py \
  weblate/templates/snippets/judge-readiness.html \
  weblate/trans/tests/test_views.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_remote.py \
  docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md
git commit -m "feat(judge): drain held queue by producer decision"
```

---

## Task 9: Documentation, threat-model assessment, review, and push

**Files:**

- Modify: `docs/user/translating.rst`
- Modify: `docs/changes.rst`
- Modify: `docs/llm-first/designs/2026-08-13-judge-native-ui-design.md`
- Review and modify only if its stated conditions apply:
  `docs/security/threat-model.rst`
- Modify only implementation/test files required by in-scope review fixes

### Step 1: Document the workflow

Document decision meanings, mandatory product-risk reason, distinction among
AI verdict/human resolution/deterministic blocker/Delivery, terminal
same-target behavior, persisted versus unavailable history, raw/rendered
target, applied-repair diff, filters, and readiness links. Use
`:guilabel:` and add one top-unreleased changelog entry.

### Step 2: Reconcile design and threat model

Update the Plan 3 design row and snapshot/string-span contract. Mark the old
`unit.review` question resolved by approved Plan 02 while preserving that
permission is not quality evidence. Replace the “reset attempts” shorthand
for Send back with the exact terminal-same-target rule and explain that a
stored target edit starts the new round.

Read `docs/security/threat-model.rst`. Record in review notes that this plan
reuses an existing POST surface, adds no API/token/credential/provider/raw
prompt exposure, and stores only visible Unit inputs plus a fingerprint.
Edit the threat model only if its own change conditions apply.

### Step 3: Run complete focused verification

```bash
uv run ./manage.py makemigrations --check --dry-run
uv run prek run --all-files
./rundev.sh test \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_migration.py \
  weblate/trans/tests/test_judge_resolution.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_spans.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/checks/tests/test_judge.py \
  weblate/trans/tests/test_edit.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_widgets.py \
  weblate/utils/tests/test_search.py \
  weblate/utils/tests/test_stats.py \
  weblate/trans/tests/test_remote.py \
  weblate/trans/tests/test_views.py
```

If `prek --all-files` changes unrelated files, do not stage them. Preserve
all unrelated user work and rerun applicable hooks on this plan's files.

### Step 4: Request independent review

Use `requesting-code-review`. Gates:

- target bytes cannot change on decision;
- accepted exception cannot bypass deterministic enforcement;
- pending Delivery follows final Unit state;
- one resolution produces one dedicated resolution `Change` and no implicit
  content edit; deterministic enforcement may retain its normal audit event;
- resolved same-target round cannot call, repair, or demote;
- no overwrite/reopen path;
- snapshot equals sent input and contains no secret/raw prompt;
- missing history is never reconstructed;
- span matching cannot highlight ambiguity/stale text;
- no judge Dismiss/ignore path;
- search/stats/readiness share Plan 02 annotations;
- no nested forms, Bootstrap 3 markup, forbidden copy, paid call, Plan 02
  duplication, or production change.

Fix only in-scope findings and rerun their evidence.

### Step 5: Commit documentation/review fixes and push

Stage only files actually changed:

```bash
git add docs/user/translating.rst docs/changes.rst \
  docs/llm-first/designs/2026-08-13-judge-native-ui-design.md
git commit -m "docs(judge): document producer decisions and whitebox"
git status --short
git push
```

Add `docs/security/threat-model.rst` or explicit implementation/test paths
before committing only when they were actually modified. Never execute a
command containing placeholders.

Final evidence must show migration state aligned, focused tests and hooks
green, real-click paths observed or their local-fixture limitation recorded,
nested-form revert-and-prove executed, exact target unchanged, Delivery
pending state verified, secrecy tests green, branch pushed, and no production
change, stack rebuild, or paid provider call.
