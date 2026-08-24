# Plan 02: LLM Judge Navigation and Release Readiness

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Use `test-driven-development`, `frontend-design`, `lightpanda-browser`, `requesting-code-review`, and `verification-before-completion` where specified below.

**Date:** 2026-08-22, updated 2026-08-24. **Status:** approved, implementation
pending. Absorbs the former judge progress reporting plan, now archived as
`docs/llm-first/archive/2026-08-24-judge-progress-reporting.md`; Task 6 owns
judge progress reporting.

**Goal:** Give a producer a component-first Weblate path from per-language release readiness to exact LLM-judge queues and a bounded, cost-aware judge run, without implying that a probabilistic verdict approves a release.

**Architecture:** Keep `JudgeVerdict` as immutable evidence and add one database-queryable fingerprint of the exact stored target. One shared annotation contract drives search and lazy cached translation statistics. Render a separate readiness table above the existing component language table. Its primary action deep-links to the existing language-scoped automatic-translation form. Preview and execution share scope, ordering, permissions, cap, and numeric in-flight task progress. Delivery remains a separate axis based only on `PendingUnitChange` commit-policy accounting.

**Tech Stack:** Django, PostgreSQL, Celery, existing Weblate stats cache, Django templates, Bootstrap, vanilla JavaScript, pytest.

---

## Approved contract

### Producer flow

1. Entry: existing component page, for example `/projects/need-for-greed/buyers/`, **Languages** tab.
2. Add a separate **Release readiness** table above the existing language table. Do not extend generic `snippets/list-objects.html`.
3. Show it only when `JUDGE_ENABLED=True` and the component is not a glossary. Exclude source/ghost translations and read-only units from AI coverage.
4. Columns: **Language**, **Delivery**, **AI coverage**, **Advisory**, **Held for decision**, **Primary action**.
5. Delivery and AI are independent:
   - Delivery: exact `PendingUnitChange` `eligible_for_commit` and `commit_policy_skipped` counts.
   - AI: parsed evidence for the exact current target.
6. Copy: **Evaluated - no blocking concern**, **Advisory - ships**, **Held for decision**, **Not evaluated**, **Stale**, **Latest attempt incomplete**, **No blocking action**. Every state the table can render also appears in a compact legend directly below the table: the exact copy above plus one plain-language sentence per state, rendered as visible text, never hover-only tooltips.
7. Never render **Approved by AI**, **AI approved**, or **Ready for release**. A pass remains state 20 by default.
8. Primary action priority:
   1. uncovered/stale -> **Evaluate N**;
   2. critical -> **Review N**;
   3. commit-policy-held updates -> policy-aware native review/fix action;
   4. advisory -> **Inspect N**;
   5. otherwise -> **No blocking action**.
   Until Plan 03 lands, the held **Review N** action carries a short secondary
   line stating that a decision is recorded only by editing the translation or
   re-evaluating; Plan 02 offers no resolution control.
9. AI counts open exact language-scoped Zen queues. Delivery counts are informational, not links: `detailed_count()` includes pending history/retry semantics that a current-state q cannot represent exactly. Its action opens a broader native pending queue and is labelled exactly **Open pending strings - broader than this count**, so the count/queue mismatch is stated, not implied.
10. **Evaluate N** opens that language's existing Automatic translation tab with `mode=judge`, `q=NOT has:judge`, and a safe return URL. It never starts a paid call automatically.
11. Reuse persistent task progress. It advances once for each completed judge
    seat batch, against the fixed worst-case repair budget for the capped
    execution scope. Completion reports evaluated/pass/advisory/held/incomplete/cap
    remainder and links back to readiness. Do not add a second live status channel
    or readiness polling.

### Verdict semantics

1. Fresh coverage: newest parsed collegium round for the exact current target.
2. Strictest parsed seat maps `none|minor -> pass`, `major -> flag`, `critical -> reject`.
3. A latest all-unparsed attempt does not erase an older parsed verdict for the same target; it adds an independent warning. Without older parsed evidence, the unit is not evaluated.
4. `has:judge`: fresh parsed evidence for current target.
5. `NOT has:judge`: never parsed, target-stale, or current-target-only-unparsed.
6. `judge:stale`: parsed history exists but none matches current target.
7. Context-only source/note/glossary changes keep aggregate target coverage; the unit card retains its existing `context changed` warning.
8. Plan 02 implements only `has:judge`, `judge:pass`, `judge:flag`, `judge:reject`, `judge:stale`, `judge:unparsed`. Exhausted/override/resolution belong to Plan 03.

### Preview and cost

1. Judge mode inserts `NOT has:judge` only while q is untouched; custom q is never overwritten.
2. Preview shows matched `N`, this-run `K=min(N, cap)`, remainder, empty/writable `W`, exact initial/worst-case judge batch calls, observed judge USD range, and separate observed OpenRouter pre-translation range.
3. Initial calls: `2 * ceil(K / JUDGE_BATCH_SIZE)`. Worst case: initial calls times `1 + JUDGE_MAX_REPAIR_ATTEMPTS`.
4. Cost is a recent observed range, not a quote: newest 20 priced requests for the same project/model/operation, minimum 5 samples, min-max billed cost per unit. Missing evidence makes that leg unavailable; never invent a total.
5. Invalid q or zero matches disables **Apply**. Preview transport failure warns but does not block because the backend validates and caps again.
6. Execution processes stable first `K`; it must not estimate N and then refuse because N exceeds cap.

### Permissions and unavailable states

1. Component viewers see aggregates allowed by normal object access.
2. Previewing financial details and running judge require existing automatic-translation access plus `unit.review` on the target translation.
3. With judge enabled but incomplete seat configuration, keep Delivery visible; AI reads **Unavailable**, Evaluate is disabled, and ordinary viewers receive no model/key details.
4. Only an existing settings-capable user sees a configuration link.

### Out of scope

- Plan 03 producer decisions, resolution/reasons/history.
- Background context-only re-judging.
- Project/category/workspace readiness dashboards.
- Glossary readiness.
- Custom review queue or replacement editor.
- Live readiness polling.
- Auto-approval or changes to `JUDGE_MAY_APPROVE`.
- Production enablement/deploy, shared-stack rebuild, or paid live model run.

---

## Task 1: Persist the queryable target fingerprint

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0103_judge_target_storage_hash.py`
- Create: `weblate/trans/tests/test_judge_migration.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Write failing fingerprint tests

Add tests for `compute_target_storage_hash(target: str)`:

- takes the raw stored `Unit.target` string exactly as the column stores it,
  including the `\x1e\x1e` plural separator;
- is never fed a join of `get_target_plurals()`: that helper pads or
  truncates forms to the language plural count, so its join is not
  guaranteed to equal the stored column and would silently break the
  PostgreSQL `MD5(Unit.target)` comparison in Task 2;
- stable for Unicode (UTF-8 on both the Python and PostgreSQL side);
- changes when one plural form changes;
- leaves existing SHA-256 `compute_target_hash()` unchanged.

Expected implementation contract:

```python
def compute_target_storage_hash(target: str) -> str:
    return hashlib.md5(target.encode(), usedforsecurity=False).hexdigest()
```

### Step 2: Prove RED, implement the helper, and prove GREEN

```bash
./rundev.sh test weblate/trans/tests/test_judge.py
```

Expected first run: missing helper. Add the minimal helper to
`weblate/trans/models/judge.py`, rerun, and expect the focused tests to pass.

### Step 3: Write a failing migration test

Use `MigrationExecutor`. Before migration create:

- Unicode singular and plural current targets;
- a matching parsed and unparsed round;
- an old-target round.

After migration assert only provably current rows receive MD5 of exact
`Unit.target`; stale/unprovable rows remain null; all evidence fields remain
unchanged.

### Step 4: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_migration.py
```

Expected: migration `0103_judge_target_storage_hash` is missing.

### Step 5: Add the runtime field and migration 0103

Declare nullable indexed
`JudgeVerdict.target_storage_hash(max_length=32)`. The data migration must:

- iterate only units with verdicts in bounded chunks;
- locally reproduce SHA-256 plural hashing: split the stored target with
  `target.split("\x1e\x1e")` and digest exactly
  `json.dumps(list(parts), ensure_ascii=False, sort_keys=False)` - the
  frozen equivalent of today's `_digest()`;
- update only rows whose old `target_hash` matches the current target;
- leave all other rows null;
- not import current runtime model helpers.

Do not add the `LLMUsageLog` fields here; Task 5 owns a separate migration so
the runtime model and migration state remain aligned after every task.

### Step 6: Test and update the verdict-write seam

Add a failing assertion that `_write_verdict` stores both the existing audit
SHA-256 and the new storage MD5 of `JudgeRequest.target` - the raw stored
`unit.target` string, never a join of `request.target_plurals`. Populate the
new field in `judge_loop.py`.
After evidence is persisted, invalidate affected translation stats through
existing `Translation.invalidate_cache()` without making invalidation failure
able to erase evidence.

### Step 7: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_migration.py weblate/trans/tests/test_judge_loop.py
git add weblate/trans/models/judge.py weblate/trans/migrations/0103_judge_target_storage_hash.py weblate/trans/tests/test_judge_migration.py weblate/trans/judge_loop.py weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): persist queryable target fingerprints"
```

---

## Task 2: Add target-fresh verdict annotations

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/tests/test_judge_round.py`

### Step 1: Write failing annotation tests

Add database cases for a shared `judge_status_annotations()` helper:

- no evidence;
- strictest of two parsed seats;
- latest all-unparsed plus older parsed same-target fallback;
- only all-unparsed current-target evidence;
- old-target parsed history;
- re-judge after target edit;
- context hash changes with target unchanged.

Required annotations:

```text
judge_active_severity: none|minor|major|critical|None
judge_has_parsed_history: bool
judge_latest_incomplete: bool
```

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_round.py
```

Expected: the annotation helper is missing. The schema and backfill from Task
1 are already present, so these tests can execute against the real persisted
fingerprint.

### Step 3: Implement one annotation contract

In `models/judge.py`:

- compare `JudgeVerdict.target_storage_hash` with PostgreSQL
  `MD5(Unit.target)`;
- locate the newest parsed row for the current target, use its
  `(run_id, attempt)`, then reduce that round by explicit severity rank;
- locate the newest current-target round independently for incomplete status;
- use `-timestamp, -pk` tie-breaking;
- return one annotation dict reusable by search and stats.

Do not add a mutable `JudgeStatus` projection or mutate history.

### Step 4: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_loop.py
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge_round.py
git commit -m "feat(judge): add target-fresh verdict queries"
```

---

## Task 3: Add exact judge search filters

**Files:**

- Modify: `weblate/utils/search.py`
- Modify: `weblate/trans/filter.py`
- Modify: `weblate/utils/tests/test_search.py` (parser fixture suite around `parse_query`)
- Modify: `weblate/trans/tests/test_widgets.py` (existing `FILTERS` choice tests)

### Step 1: Write failing fixture-backed tests

Pin result sets for:

- `has:judge` and `NOT has:judge`;
- pass (`none|minor`), flag (`major`), reject (`critical`);
- stale;
- newest current-target all-unparsed, including older parsed fallback;
- composition with `state:>=translated` and negation;
- unsupported `judge:override` raising normal search error.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/utils/tests/test_search.py -k judge
```

### Step 3: Implement through parser annotations

`UnitTermExpr.get_annotations()` returns `judge_status_annotations()` only for judge terms. Predicates consume annotations, never Python `JudgeVerdict.verdict` and never bare `Exists(JudgeVerdict)`.

Dropdown choices now: Not evaluated, Advisory - ships, Held for decision, Stale, Latest attempt incomplete. Keep pass queryable but not prominent. No exhausted/override.

### Step 4: Verify and commit

```bash
./rundev.sh test weblate/utils/tests/test_search.py weblate/trans/tests/test_widgets.py
git add weblate/utils/search.py weblate/trans/filter.py weblate/utils/tests/test_search.py weblate/trans/tests/test_widgets.py
git commit -m "feat(judge): add current-verdict search filters"
```

---

## Task 4: Add cached AI stats and batched Delivery counts

**Files:**

- Modify: `weblate/utils/stats.py`
- Modify: `weblate/utils/tests/test_stats.py`
- Modify: `weblate/trans/models/pending.py`
- Modify: `weblate/trans/tests/test_remote.py` (existing `detailed_count()` coverage lives here)

### Step 1: Write failing stats tests

Add lazy translation keys:

```text
judge_total, judge_evaluated, judge_pass, judge_flag,
judge_reject, judge_stale, judge_unparsed
```

Pin invariants:

- total excludes read-only;
- pass+flag+reject=evaluated;
- unparsed may overlap evaluated due historical fallback;
- each bucket equals its search filter;
- target edit invalidates cache and moves evaluated -> stale/uncovered;
- new current-target round restores coverage;
- context-only change preserves coverage.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/utils/tests/test_stats.py -k judge
```

### Step 3: Implement one-pass lazy judge stats

Define separate `JUDGE_KEYS`; do not add them to ordinary unit delta buckets. `TranslationStats.calculate_by_name()` calculates every judge key from one queryset using shared annotations and stores them together. Existing target-edit invalidation and Task 1 verdict-write invalidation must clear them.

Do not add project/global judge stats.

### Step 4: Write failing pending aggregation tests

For multiple target translations and both commit policies, assert new `detailed_count_by_translation(component)` equals existing `detailed_count(translation)` for:

- no pending;
- eligible;
- policy skipped;
- older eligible plus newer held;
- retry-ineligible blocking history.

Extend `weblate/trans/tests/test_remote.py`, next to the existing
`detailed_count()` cases; do not introduce a new `test_pending.py`
convention.

### Step 5: Implement batched aggregation

Run component-level retry/blocking logic once, then group distinct units per translation before retry, after retry, and after policy. Zero-fill translations without pending rows. Do not change `detailed_count()` semantics.

### Step 6: Verify and commit

```bash
./rundev.sh test weblate/utils/tests/test_stats.py weblate/trans/tests/test_remote.py
git add weblate/utils/stats.py weblate/utils/tests/test_stats.py weblate/trans/models/pending.py weblate/trans/tests/test_remote.py
git commit -m "feat(judge): cache readiness statistics"
```

---

## Task 5: Record operation-aware usage and observed cost ranges

**Files:**

- Create: `weblate/trans/migrations/0104_llm_usage_operation.py`
- Modify: `weblate/trans/models/llm_usage.py`
- Modify: `weblate/trans/judge.py`
- Modify: `weblate/machinery/llm.py`
- Modify: `weblate/machinery/openai.py`
- Modify: `weblate/trans/tests/test_llm_usage.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/machinery/tests.py`
- Modify: `weblate_customization/tests/test_machinery.py`

### Step 1: Write failing attribution tests

Assert:

- judge responses log `operation="judge"`, `unit_count=len(batch)`;
- billed retry responses each log actual batch size;
- OpenRouter MT logs `operation="translation"` and actual source batch size, including split recovery;
- accounting failure remains non-fatal;
- legacy/default fields may be blank/null.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_judge_client.py weblate/machinery/tests.py -k usage
```

### Step 3: Add model fields and migration 0104

Create `0104_llm_usage_operation.py` in the same step as the runtime model declaration:

```python
class Operation(models.TextChoices):
    TRANSLATION = "translation"
    JUDGE = "judge"

operation = models.CharField(max_length=20, choices=Operation, blank=True, db_index=True)
unit_count = models.PositiveIntegerField(null=True, blank=True)
```

Pass `len(batch)` through judge usage. Add a `ContextVar` beside `llm_batch_project`, set/reset it around sync/async LLM batch fetches, and record it in OpenAI usage.

### Step 4: Write observed-range tests

A data-only helper must:

- filter exact project/model/operation;
- ignore null cost and null/zero count;
- use newest 20 rows;
- return `None` below 5 samples;
- return Decimal min/max `cost_usd / unit_count` at 5+;
- never mix translation/judge or projects.

### Step 5: Implement, verify, commit

```bash
./rundev.sh test weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_judge_client.py weblate/machinery/tests.py -k "usage or batch"
./rundev.sh test weblate_customization/tests/test_machinery.py
git add weblate/trans/migrations/0104_llm_usage_operation.py weblate/trans/models/llm_usage.py weblate/trans/judge.py weblate/machinery/llm.py weblate/machinery/openai.py weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_judge_client.py weblate/machinery/tests.py weblate_customization/tests/test_machinery.py
git commit -m "feat(judge): track observed per-unit costs"
```

---

## Task 6: Share preview scope, cap, and judge progress reporting

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/autotranslate.py`
- Modify: `weblate/trans/tasks.py`
- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/tests/test_judge_autotranslate.py`
- Modify: `weblate/trans/tests/test_autotranslate.py`

### Step 1: Write failing scope, cap, and progress tests

For translation and component scopes assert:

- K matches -> K processed;
- K+1 -> stable first K by translation order then unit position/PK, one remains;
- multiple languages share one global cap;
- read-only and denied translations do not consume it;
- preview/execution matched/processed/remaining/writable counts agree;
- the old above-cap refusal disappears.

Add callback tests at the two judge seams:

- `request_verdicts()` calls an optional `on_batch` exactly once after each
  completed batch, whether that batch parses or becomes unparsed. A 403/429
  retry remains one completed batch, not two ticks. For retry coverage,
  register a 429 response followed by a valid 200 response, mock its sleep,
  and assert two HTTP calls but one callback tick. Separately register
  `status_code=500` for unparsed-batch coverage. Use the local
  `http_mock.register("POST", CHAT_URL, ...)` API; do not use a hypothetical
  `http_mock.add()` API.
- `run_judge_batch()` forwards the optional callback to both seat calls.
  Cached units make no judge client call and therefore no tick.
- Update every patched `run_judge_batch` fake in
  `test_judge_autotranslate.py` to accept `on_batch=None`, then retain the
  existing behavior assertions. This change must not break unrelated judge
  autotranslate tests.

Add exact progress assertions for a known capped scope. For example, six
processed units with `JUDGE_BATCH_SIZE=5` and one permitted repair attempt
have eight worst-case steps:

```text
ceil(6 / 5) * 2 * (1 + 1) == 8
```

Assert the four no-repair seat-batch ticks land inside the judge range and
the final completion reaches its high endpoint. Also set a child instance's
range to `(20, 40)` and assert that MT receives `(20, 22)` while judge
progress stays in `(22, 40)`. Test an empty capped scope through
`BatchAutoTranslate` and assert outer task completion without MT or judge
callbacks.

Add settings-validation cases before relying on the formulas: reject
`JUDGE_BATCH_SIZE <= 0`, `JUDGE_MAX_REPAIR_ATTEMPTS < 0`, and
`JUDGE_MAX_UNITS_PER_RUN < 0` through `validate_judge_configuration()`. Define
zero `JUDGE_MAX_UNITS_PER_RUN` as a zero cap, producing an empty `K`, rather
than as unlimited execution.

### Step 2: Prove RED

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_autotranslate.py \
  -p no:randomly --no-cov -q
```

### Step 3: Add a small immutable preview result

For example:

```python
@dataclass(frozen=True, slots=True)
class JudgeScopePreview:
    matched: int
    processed: int
    remaining: int
    writable: int
    initial_calls: int
    worst_case_calls: int
```

`BatchAutoTranslate.preview_judge_scope()` follows the exact ordered, permission-filtered execution scope. Cost lookup remains separate.

### Step 4: Slice instead of refuse, then report completed batches

First extend `validate_judge_configuration()` so preview and execution reject
`JUDGE_BATCH_SIZE <= 0`, `JUDGE_MAX_REPAIR_ATTEMPTS < 0`, and
`JUDGE_MAX_UNITS_PER_RUN < 0` before they calculate calls, costs, or progress.
Treat `JUDGE_MAX_UNITS_PER_RUN == 0` as an empty capped scope.

Thread an optional `Callable[[], None]` from `request_verdicts()` through
`run_judge_batch()` to `AutoTranslate.process_judge()`. Call it after a
batch's parsed or `UNPARSED` results have been appended, before an
inter-batch sleep. It is independent of, and must not be suppressed by,
non-fatal usage accounting. The callback measures completed **seat batches**,
not HTTP attempts or individual requests.

`AutoTranslate.process_judge()` obtains the ordered, permission-filtered,
cap-sliced execution scope once and processes only that stable `K`. It must
not independently re-evaluate the query or count the uncapped `N`.
Preserve snapshot/race checks and repair. Aggregate actual verdict buckets and
global remainder in `BatchAutoTranslate`.

Split the instance's existing progress range into the first tenth for
pre-translation and the remaining nine tenths for judging. Restore the judge
range in `finally` after MT. Before judging, set:

```text
progress_steps =
  ceil(K / JUDGE_BATCH_SIZE) * 2 * (JUDGE_MAX_REPAIR_ATTEMPTS + 1)
```

Increment the progress counter through the callback and finish the range when
`run_judge_batch()` returns. This is a fixed upper bound: both seats judge
every uncached selected string, and repaired strings are a subset of `K`.
Cached verdict reuse or a run without repairs may therefore finish with a
final jump. Do not count repair-MT calls. A direct empty scope has zero
steps and emits no child progress; the enclosing batch task still reports its
completion normally.

Do not write transient judge text into `task-log-<id>`, modify
`message.html`, or add task-detail rendering in `loader-bootstrap.js`.
`task-log-<id>` is the shared component log, and the task completion summary
below is the sole producer-facing status contract.

### Step 5: Add judge completion summary

Example contract:

```text
50 evaluated: 31 with no blocking concern, 14 advisory, 5 held for decision, 0 incomplete. 346 matching strings remain because of the per-run cap.
```

Generic modes retain generic messages.

### Step 6: Verify and commit

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_autotranslate.py \
  -p no:randomly --no-cov -q
git add weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/autotranslate.py weblate/trans/tasks.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_autotranslate.py
git commit -m "feat(judge): cap and report judge batch progress"
```

---

## Task 7: Add secured live preview and mode-aware form

**Files:**

- Modify: `weblate/trans/forms.py`
- Modify: `weblate/trans/views/edit.py`
- Modify: `weblate/urls.py`
- Modify: `weblate/templates/snippets/autoform.html`
- Modify: `weblate/static/loader-bootstrap.js`
- Modify: `weblate/trans/tests/test_judge_form.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing form/navigation tests

Pin:

- permitted deep-link initializes `mode=judge`, `q=NOT has:judge`, safe `next`;
- unauthorized/unsupported mode is not forced;
- external next is rejected through `redirect_next`;
- eager and async launch return/link to component readiness;
- warning copy says judge-evaluated, not judge-approved.

### Step 2: Write failing preview endpoint tests

Add `auto-translate-preview/<object_path:path>/` tests for authentication/object access, automatic-translation+review permissions, invalid q 400, zero/below/above cap, read-only exclusion, exact calls, separate cost legs, insufficient history, incomplete config without details, and zero writes/tasks/provider calls.

### Step 3: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_views.py
```

### Step 4: Implement safe initialization and return

- Add hidden `next` to `AutoForm` outside visible crispy layout.
- Accept GET initial values only when they are current valid choices.
- Use `redirect_next()` after launch.
- Give `add_user_task()` the safe component return URL/label for dashboard-started runs.

### Step 5: Implement bounded JSON preview

Response shape:

```json
{
  "matched": 396,
  "processed": 50,
  "remaining": 346,
  "writable": 8,
  "judge_calls_initial": 20,
  "judge_calls_worst_case": 40,
  "judge_cost": {"available": true, "min": "0.06", "max": "0.11"},
  "pretranslation_cost": {"available": false}
}
```

Use the same `AutoForm` validation and scope helper as POST/execution. For cross-project or unresolved model scope, mark the cost leg unavailable. Return no secrets/model IDs.

### Step 6: Replace misleading static counters

In `autoform.html`:

- remove current `state:<translated` and `strings x 2 judges x attempts` copy;
- add judge-only `aria-live="polite"` lines for scope/cap/calls/cost;
- give Apply a stable ID;
- preserve one outer form and `FormHelper` behavior;
- replace judge-approved copy with judge-evaluated.

### Step 7: Implement non-destructive JS

Per form instance:

- track whether q was edited;
- insert default only for untouched/default q;
- debounce and abort stale preview requests;
- disable Apply for invalid/zero;
- warn but keep Apply available on transport failure;
- hide preview outside judge mode;
- preserve existing auto-source behavior.

No inline script, framework, or modal.

### Step 8: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_autotranslate.py
git add weblate/trans/forms.py weblate/trans/views/edit.py weblate/urls.py weblate/templates/snippets/autoform.html weblate/static/loader-bootstrap.js weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): add bounded run preview"
```

---

## Task 8: Render component-first readiness

**Files:**

- Modify: `weblate/trans/views/basic.py`
- Modify: `weblate/templates/component.html`
- Create: `weblate/templates/snippets/judge-readiness.html`
- Modify: `weblate/trans/tests/test_views.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

### Step 1: Write failing component contracts

Assert:

- card precedes existing language table;
- regular enabled component only; disabled/glossary hidden;
- source/ghost absent, target language once, existing user sort retained;
- read-only excluded from denominator;
- Delivery matches batched pending counts;
- incomplete config shows Delivery plus AI unavailable;
- viewer sees counts but no Evaluate/configure;
- reviewer with auto permission sees Evaluate;
- settings-capable user alone sees Configure;
- AI URLs/q are exact;
- Delivery numbers are plain text;
- broader action uses `has:pending state:<translated` for `WITHOUT_NEEDS_EDITING`, `has:pending NOT state:approved` for `APPROVED_ONLY`;
- priority order and **No blocking action** copy;
- legend renders below the table with the exact state copy and one sentence
  per state, covering every state the rows can show;
- held **Review N** includes the interim no-resolution secondary line;
- Delivery broader action label is exactly **Open pending strings - broader
  than this count**.

### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_views.py -k component
```

### Step 3: Build rows in `show_component()`

Reuse existing sorted translations; select real non-source targets; prefetch stats; fetch all Delivery data once; create simple row dictionaries with counts, exact AI URLs, broader pending URL, Evaluate deep-link, permissions, and primary action. Do not query in the template.

### Step 4: Render native accessible table

Use existing `card`, table, scroll-wrapper, link/button classes; semantic heading and `<th scope>`; text in addition to color; secondary stale/incomplete details. No score, chart, card grid, inline style, or raw verdict terms.

Render the legend as visible help text under the table, for example a small
definition list, one entry per state; do not rely on hover-only `title`
tooltips, which fail keyboard and touch users.

Include it immediately before current language-list include in `component.html`.

### Step 5: Verify and commit

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_views.py -k "component or judge"
git add weblate/trans/views/basic.py weblate/templates/component.html weblate/templates/snippets/judge-readiness.html weblate/trans/tests/test_views.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): add component release readiness"
```

---

## Task 9: Document and verify Plan 02 end to end

**Files:**

- Modify: `docs/user/translating.rst`
- Modify: `docs/admin/checks.rst`
- Modify: `docs/changes.rst`
- Modify only implementation files changed by an in-scope review fix

### Step 1: Update existing English docs

Extend, do not duplicate:

- `docs/user/translating.rst`: readiness location, separate axes, stale/incomplete, exact queues, capped preview, observed-cost disclaimer, numeric judge-batch progress (including a possible final no-repair jump), and no approval implication.
- `docs/admin/checks.rst`: unavailable state, permissions, no configuration leakage, context-only warning semantics.
- top unreleased `docs/changes.rst`: concise linked entry.

Do not rewrite the approved Russian design document.

### Step 2: Run focused behavioral suite

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_migration.py \
  weblate/trans/tests/test_judge_client.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_llm_usage.py \
  weblate/utils/tests/test_search.py \
  weblate/utils/tests/test_stats.py \
  weblate/trans/tests/test_widgets.py \
  weblate/trans/tests/test_remote.py
```

Expected: all selected tests pass.

### Step 3: Run affected hooks once

```bash
uv run prek run --files \
  weblate/trans/models/judge.py \
  weblate/trans/models/llm_usage.py \
  weblate/trans/models/pending.py \
  weblate/trans/judge.py \
  weblate/trans/judge_loop.py \
  weblate/trans/autotranslate.py \
  weblate/trans/tasks.py \
  weblate/trans/forms.py \
  weblate/trans/views/basic.py \
  weblate/trans/views/edit.py \
  weblate/utils/search.py \
  weblate/utils/stats.py \
  weblate/machinery/llm.py \
  weblate/machinery/openai.py \
  weblate/urls.py \
  weblate/templates/component.html \
  weblate/templates/snippets/judge-readiness.html \
  weblate/templates/snippets/autoform.html \
  weblate/static/loader-bootstrap.js \
  docs/user/translating.rst docs/admin/checks.rst docs/changes.rst
```

Expected: all affected hooks pass. Do not use `--all-files` to absorb unrelated baseline failures.

### Step 4: Browser smoke-test real producer flow

Use the running dev instance and real controls. Do not rebuild/restart the shared stack and do not submit a paid run.

1. Reviewer opens regular component `/projects/need-for-greed/buyers/`.
2. Readiness appears above unchanged language table; source absent.
3. AI count click opens exact Zen q and matching count.
4. **Evaluate N** opens chosen language Auto tab with judge/default q/return link.
5. Custom q survives mode changes.
6. Invalid and zero q disable Apply.
7. Valid q shows cap/calls and separate cost legs in live region. On a dev
   instance without at least 5 priced samples per project/model/operation
   leg, a leg must read as unavailable instead of showing numbers - that is
   the correct rendering, not a failure.
8. Keyboard focus and labels work without color.
9. View-only account sees aggregates, not Evaluate/configure.
10. Glossary component has no card.
11. Browser console/network show no new errors.

Click real controls; never substitute `form.submit()` or direct POST.

### Step 5: Independent review

Use `requesting-code-review`. Check:

- no historical target counts as current;
- SQL and `active_round()` agree;
- preview/execution scope cannot drift;
- no secret/cost leakage;
- no component-page N+1;
- no nested forms/accessibility regression;
- no approval/release guarantee;
- no Plan 03 behavior.

Fix only in-scope findings and rerun affected evidence.

### Step 6: Commit docs/review fixes and push

```bash
git add docs/user/translating.rst docs/admin/checks.rst docs/changes.rst
git add <only source/tests changed by an in-scope review fix>
git commit -m "docs(judge): document Plan 02 readiness workflow"
git push
```

Final evidence must show: focused tests green, affected hooks green, browser flow observed, branch committed/pushed, no production change, no paid provider call.
