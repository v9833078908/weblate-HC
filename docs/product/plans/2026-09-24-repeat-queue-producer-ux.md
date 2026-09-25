# Repeat queue: judge-checked variants and one-table bulk apply

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

Date: 2026-09-24, rewritten in full on 2026-09-25.
Status: **rewritten after the owner chose "option 3" on 2026-09-25; awaiting
the owner's approval before implementation.** This version replaces the
2026-09-24 text (judge preselection only, bulk apply out of scope). Editing
this plan does not authorize deploying it or starting a paid judge run.

**Goal:** A producer who cannot read the target language clears most of a
repeat queue (anvil-saga / fr: 383 diverging groups, 1022 places) by letting
the existing LLM judge check every variant, then reviewing one table of groups
where exactly one variant has no errors and applying it with one click and one
undo.

**Architecture:** The queue gains a state-driven panel that links to the
existing judge launch, prefilled for the queue's places and in a mode that
records verdicts without changing string states. A batched reader turns stored
verdicts into one judgement per group (ready / choose / rewrite / unchecked).
Cards show the evidence and preselect the ready variant. A second review page
freezes the ready groups in a signed manifest and feeds them into the existing
`RepeatBulkRun` machinery (apply, resume, status, undo) with `result=None`
items, so no migration is needed.

**Tech Stack:** Django 6 views and templates with `{% translate %}`,
`weblate/trans/models/judge.py`, new `weblate/trans/repeat_judge.py`,
`weblate/trans/repeat_bulk.py`, `weblate/trans/views/repeats.py`,
`weblate/trans/views/basic.py`, `weblate/trans/forms.py`,
`weblate/static/js/repeat-queue.js`, Russian `.po`, pytest.

UI contract: `analysis/prototypes/repeat-queue/judge-bulk.html`
(`?state=start|running|ready|review|done`). Templates are written from this
plan and the prototype's structure, not copied from it.

---

## Why this plan exists

The 2026-09-23 plan shipped a paid "Prepare recommendations" page. Producers
found it unusable: it speaks in requests, caps, reservations and unknown
deliveries, and it asks the model a second quality question next to the LLM
judge that already evaluates translations. The owner reviewed three options on
2026-09-25 and chose to rely on the judge: the judge checks every variant of a
diverging group, the queue recommends the only variant without errors, and
such groups are applied in bulk after one review table.

## Decisions (2026-09-25)

| # | Decision | Consequence |
| --- | --- | --- |
| D1 | The "Prepare recommendations" button leaves the queue. | `repeat-recommend` route, models, prompt and tests stay; nothing is deleted. The existing "Review and apply" banner for current model results stays. |
| D2 | Nothing paid starts without a click on the existing judge launch form. | The queue only links; the producer launches from the standard form, which shows its own cost estimate. No background runs. |
| D3 | The queue's judge link is prefilled: mode `judge`, query `check:repeat-drift`, this language, proposal-only. | Proposal-only records verdicts and never changes string states (`weblate/trans/autotranslate.py:1396`), so a critical verdict does not drop a place out of its repeat group. |
| D4 | A group is **ready** only when exactly one variant passed and no place is approved. | Two passed variants, or an approved place, mean "choose yourself". Majority is never evidence. |
| D5 | The ready variant is preselected on the card; preview and confirmation stay mandatory for single-group decisions. | Changes the `PRODUCT.md` truth "the UI preselects nothing" for this case only. |
| D6 | Evidence is shown under each variant: judge badge, back-translation, and the reason of a flagged variant. | Read-only; lets a producer understand a French variant without reading French. |
| D7 | Ready groups are applied in bulk from one review table through the existing `RepeatBulkRun`. | Items carry `result=None` and a copied decision with `"source": "judge"`; resume, status and undo are reused unchanged. |
| D8 | The queue gets a panel with three states (start / running / ready) and four bucket counters that filter the queue. | Replaces the toolbar button; buckets are `ready`, `choose`, `rewrite`, `unchecked`. |

## Evidence the design rests on

Verified in the code and on the local copy of anvil-saga / fr on 2026-09-24/25.

- **Scope query.** `check:repeat-drift` selects failing places
  (`weblate/utils/search.py:909-926`). Until commit `90335925` the check capped
  itself at 200 groups per plural form and matched only 553 of 1022 places; it
  now matches all 1022 places of the 383 groups. The check is
  `default_disabled`: a component must carry the `repeat-drift` flag, and
  members marked independent or `ignore-repeat-drift` are skipped
  (`weblate/checks/consistency.py:323-331`).
- **Launch.** `auto_translation` (`weblate/trans/views/edit.py:2012-2044`)
  accepts a project-language object and, in mode `judge`, calls
  `_start_judge_producer_run` (`edit.py:1824-1961`). It stores the run as
  `ScopeType.PROJECT` with `scope_path` = the project-language URL and
  `requested_query` = the form's `q` (`edit.py:1876-1908`), and judges only
  `scope_snapshot` unit ids. UI launches are uncapped (`execution_version=1`).
  The launch hardcodes `"judge_proposal_only": False` (`edit.py:1892`).
- **Prefill.** The project, component and translation pages copy `mode` and
  `q` from `request.GET` into `AutoForm.initial` (`weblate/trans/views/basic.py:553-567`);
  the project-language page does not (`basic.py:385-392`). `#auto` opens the
  tab (`loader-bootstrap.js:1012-1031`). Browser storage restores persisted
  form fields unless the key is present in the URL (`loader-bootstrap.js:1315-1351`).
- **Cost.** The launch form's AJAX preview shows an upper-bound judge cost
  when enough priced history exists (`edit.py:1700-1729`); the run report shows
  actual spend (`run_spend`, `weblate/trans/models/llm_usage.py:172-199`).
  Re-judging an unchanged unit reuses its cached verdict at no cost
  (`weblate/trans/judge_loop.py:447-474`).
- **Verdicts.** `JudgeVerdict` (`weblate/trans/models/judge.py:920-1047`)
  carries `seat`, `subject`, `unparsed`, `max_severity`, `errors`,
  `back_translation`, `target_hash`. `active_verdict(unit)`
  (`judge.py:1362`) = `collegium_verdict(active_round(unit))`: per seat the
  newest parsed live row whose `target_hash` equals the current target, reduced
  with `JUDGE_CONSENSUS_REJECT`. It costs about three queries per unit. Its
  `verdict` is `pass` (none/minor), `flag` (major) or `reject` (critical).
  A reason text is built as `f"{label}: {description}"` from `primary_error`
  and `_CATEGORY_LABELS` (`weblate/trans/views/judge.py:298-309`).
- **Progress.** `ProducerRun.get_coverage()` (`judge.py:460-551`) returns
  `total`, `recorded` and more. `recent_producer_runs` for a project-language
  does not find UI judge runs, because they are stored as `PROJECT` scope.
- **Bulk engine.** Only `plan_bulk`, `_review_row` and `start_bulk`
  (`weblate/trans/repeat_bulk.py:89-297`) know about
  `RepeatRecommendationResult`. Item processing, resume, undo and the status
  page never read `item.result` (`repeat_bulk.py:353-660`), and
  `RepeatBulkItem.result` is nullable. Item-time freshness compares
  `context_fingerprint` and member fingerprints (`repeat_bulk.py:596-632`).
- **Card.** `snippets/repeat_group.html` never renders `checked`;
  `repeat-queue.js` runs `updateDecision` only on `change`, so a server-side
  preselection needs one call on load. Variant radios post `target`; custom and
  keep post `choice`.

## Judgement rule

Evaluated per group whose queue status is `open`.

1. For each place, take `active_verdict` semantics: the collegium row for its
   current text, or none.
2. A **variant** is `passed` when at least one of its places has verdict
   `pass` and none has `flag` or `reject`; `flagged` when any place has `flag`
   or `reject`; otherwise `unchecked`.
3. The **group bucket**:
   - `ready`: exactly one variant `passed` and no place is approved;
   - `choose`: two or more variants `passed`, or exactly one `passed` while a
     place is approved;
   - `rewrite`: every variant `flagged`;
   - `unchecked`: anything else (no passed variant and at least one unchecked).
4. Only `ready` preselects on the card and enters the bulk table.

Known limit, stated in the UI copy: the judge checked a variant in the context
of its own places, not in every place of the group. The repeat rule's scope and
the manual "Different meanings" choice cover the rest.

## Environment notes for the implementer

- Work on a feature branch; the owner decides how it reaches `main`. Commit
  after each task with the message given there.
- Host tests: `source scripts/test-database.sh`,
  `export DJANGO_SETTINGS_MODULE=weblate.settings_test`, then
  `uv run pytest <files> -n 0`. See `docs/contributing/tests.rst` for a fresh
  checkout.
- Lint: `uv run prek run --files <changed files>`. `reuse lint` fails on
  about 135 pre-existing unrelated files; record that, do not "fix" it.
- No test contacts a provider. Verdicts are created directly in the database.
- Deploying to the shared dev stack, restarting workers and launching a real
  judge run are outside this plan's approval (Task 10).

---

## Task 1: batched active verdicts

**Files:**

- Modify: `weblate/trans/models/judge.py` (next to `active_verdict`, line 1362)
- Test: `weblate/trans/tests/test_judge.py`

**Step 1: Write the failing test**

Add a test class that reuses the verdict factory shape from
`weblate/trans/tests/test_commands.py:1951` (`target_hash`, `context_hash`,
`judge_model`, `seat`, `unparsed`):

```python
class ActiveVerdictsTest(ViewTestCase):
    def make_verdict(self, unit, **kwargs) -> JudgeVerdict:
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        kwargs.setdefault("unparsed", False)
        kwargs.setdefault("max_severity", JudgeVerdict.Severity.NONE)
        return JudgeVerdict.objects.create(unit=unit, **kwargs)

    def test_matches_active_verdict_per_unit_in_constant_queries(self) -> None:
        units = list(self.get_translation().unit_set.all()[:4])
        passed, flagged, stale, empty = units
        self.make_verdict(passed, seat=1)
        self.make_verdict(passed, seat=2, max_severity="minor")
        self.make_verdict(flagged, seat=1, max_severity="major")
        # A newer unparsed row never hides an older parsed opinion.
        self.make_verdict(flagged, seat=1, unparsed=True)
        self.make_verdict(stale, target_hash="not-the-current-text")
        # A candidate row is not an opinion about the live text.
        self.make_verdict(empty, subject=JudgeVerdict.Subject.CANDIDATE)

        for consensus in (True, False):
            with (
                self.subTest(consensus=consensus),
                override_settings(JUDGE_CONSENSUS_REJECT=consensus),
            ):
                with self.assertNumQueries(1):
                    batched = active_verdicts(units)
                for unit in units:
                    single = active_verdict(unit)
                    self.assertEqual(
                        getattr(batched[unit.pk], "pk", None),
                        getattr(single, "pk", None),
                    )
                    if single is not None:
                        self.assertEqual(batched[unit.pk].verdict, single.verdict)
```

Add a second case with a `critical` seat 1 and a `minor` seat 2 so both
`JUDGE_CONSENSUS_REJECT` values yield different `verdict`s, asserting equality
with `active_verdict` under each setting.

**Step 2: Run it and see it fail**

`uv run pytest weblate/trans/tests/test_judge.py -k ActiveVerdictsTest -n 0`
Expected: `ImportError`/`NameError` for `active_verdicts`.

**Step 3: Implement**

```python
def active_verdicts(units: Sequence[Unit]) -> dict[int, JudgeVerdict | None]:
    """Return ``active_verdict`` for many units with one query."""
    target_hashes = {
        unit.pk: compute_target_hash(unit.get_target_plurals()) for unit in units
    }
    newest: dict[tuple[int, int], JudgeVerdict] = {}
    rows = JudgeVerdict.objects.filter(
        unit_id__in=target_hashes,
        target_hash__in=set(target_hashes.values()),
        subject=JudgeVerdict.Subject.LIVE,
        unparsed=False,
    ).order_by("unit_id", "seat", "-timestamp", "-pk")
    for row in rows:
        # The same text in two units shares a hash; keep only the unit's own.
        if row.target_hash == target_hashes[row.unit_id]:
            newest.setdefault((row.unit_id, row.seat), row)
    rounds: dict[int, list[JudgeVerdict]] = defaultdict(list)
    for (unit_id, _seat), row in sorted(newest.items()):
        rounds[unit_id].append(row)
    return {
        unit_id: collegium_verdict(rounds.get(unit_id, [])) for unit_id in target_hashes
    }
```

This mirrors `_seat_round_rows(..., context_hash=None, prefer_parsed=True)`:
per seat the newest parsed live row for the current text.

**Step 4: Run it and see it pass**, then run the whole
`weblate/trans/tests/test_judge.py`.

**Step 5: Commit** `feat(judge): read active verdicts for many units at once`.

## Task 2: group judgements

**Files:**

- Create: `weblate/trans/repeat_judge.py` (GPL header as in other new modules)
- Test: `weblate/trans/tests/test_repeat_judge.py`

**Step 1: Write the failing tests**

Build groups with `RepeatBulkViewsTest`-style helpers (copy `add_group` from
`weblate/trans/tests/test_repeat_views.py:471`) and the verdict factory from
Task 1. One test per rule row:

- one passed + one unchecked variant: bucket `ready`, `recommended` is that
  variant's full target tuple;
- one passed + one flagged: `ready`; the flagged variant's `reason` is
  `"<category label>: <description>"`;
- two passed: `choose`, `recommended` is `None`;
- one passed while a place is `STATE_APPROVED`: `choose`;
- all flagged: `rewrite`;
- nothing judged: `unchecked`;
- a verdict for an older target of a place is ignored;
- `judge_groups` for 20 groups runs one verdict query (`assertNumQueries`
  around the call with prefetched units).

**Step 2: Run and see them fail** (`ModuleNotFoundError`).

**Step 3: Implement**

```python
READY, CHOOSE, REWRITE, UNCHECKED = "ready", "choose", "rewrite", "unchecked"
REPEAT_JUDGE_QUERY = "check:repeat-drift"


@dataclass(frozen=True)
class VariantJudgement:
    mark: str  # "passed" | "flagged" | "unchecked"
    back_translation: str
    reason: str


@dataclass(frozen=True)
class GroupJudgement:
    bucket: str
    recommended: tuple[str, ...] | None
    variants: dict[tuple[str, ...], VariantJudgement]
    # (unit id, verdict pk, target hash) for every judged place, sorted.
    evidence: tuple[tuple[int, int, str], ...]


def judge_group(
    variants: list[dict], verdicts: dict[int, JudgeVerdict | None]
) -> GroupJudgement: ...


def judge_groups(groups: Iterable[tuple[int, list[dict]]]) -> dict[int, GroupJudgement]:
    """Judge many groups with one verdict query; ``groups`` yields (group id, variants)."""
    ...
```

`variants` is the queue's shape: `[{"target": tuple, "units": [Unit, ...]}]`
(`weblate/trans/views/repeats.py:149-162`). The reason text reuses
`_CATEGORY_LABELS` from `weblate/trans/views/judge.py`; move that mapping to
`weblate/trans/models/judge.py` if importing a view module from a service
module would create a cycle, and import it back in the view.

**Step 4: Run and see them pass.**

**Step 5: Commit** `feat(repeats): judge each repeat group from stored verdicts`.

## Task 3: proposal-only judge launch for the queue's places

**Files:**

- Modify: `weblate/trans/forms.py` (`AutoForm`, line 1318; layout)
- Modify: `weblate/trans/views/edit.py:1892` (`_start_judge_producer_run`)
- Modify: `weblate/trans/views/basic.py:385-392` (project-language `autoform`)
- Modify: `weblate/templates/snippets/autoform.html` (one explanatory line)
- Test: `weblate/trans/tests/test_judge_form.py`

**Step 1: Write the failing tests**

- GET `/projects/<project>/-/<lang>/?mode=judge&q=check:repeat-drift&judge_proposal_only=1`
  as a reviewer with the judge configured: the rendered form has `mode`
  `judge`, `q` `check:repeat-drift`, and a hidden `judge_proposal_only` with
  value `1`; the page shows "Verdicts are recorded; string states are not
  changed."
- POST the launch with `judge_proposal_only=1` (dispatch patched as in the
  existing launch tests of this module): the created `ProducerRun` has
  `execution_options["judge_proposal_only"] is True`, `scope_path` equal to
  the project-language URL and `requested_query == "check:repeat-drift"`.
- POST without the field keeps `False` (today's behaviour).
- In `weblate/trans/tests/test_judge_autotranslate.py`, a proposal-only run
  over a unit with a `critical` verdict leaves its state `STATE_TRANSLATED`
  (reuse that module's run fixtures; this pins the behaviour at
  `autotranslate.py:1396` that D3 relies on).

**Step 2: Run and see them fail.**

**Step 3: Implement**

- `AutoForm`: `judge_proposal_only = forms.BooleanField(required=False, widget=forms.HiddenInput)`,
  added to the crispy layout so it renders.
- `_start_judge_producer_run`:
  `"judge_proposal_only": autoform.cleaned_data.get("judge_proposal_only", False),`.
- Project-language view: pass
  `initial={key: request.GET[key] for key in ("mode", "q", "judge_proposal_only") if request.GET.get(key)}`,
  matching `basic.py:560-567`.
- `autoform.html`: when `form.initial.judge_proposal_only`, show the
  translated line above.

**Step 4: Run and see them pass.** Run the whole `test_judge_form.py` and
`test_judge_autotranslate.py`.

**Step 5: Commit** `feat(judge): launch a proposal-only judge run from a prefilled link`.

## Task 4: queue panel, buckets and filter

**Files:**

- Modify: `weblate/trans/repeat_judge.py` (`latest_repeat_judge_run`, `judge_launch_url`)
- Modify: `weblate/trans/views/repeats.py` (`repeat_queue`, `_queue_groups`)
- Modify: `weblate/templates/repeat_queue.html` (panel, remove the paid link)
- Test: `weblate/trans/tests/test_repeat_views.py`

**Step 1: Write the failing tests**

- No verdicts, user may launch the judge: panel state `start`; the response
  contains the launch URL
  `<project-language URL>?mode=judge&q=check%3Arepeat-drift&judge_proposal_only=1&overwrite_existing=#auto`
  and "Check variants with the judge"; it does **not** contain the
  `repeat-recommend` URL.
- A `ProducerRun` (mode `judge`, `scope_path` = project-language URL,
  `requested_query` = `check:repeat-drift`, status `running`) with a patched
  `get_coverage()` returning `{"total": 1052, "recorded": 420}`: state
  `running`, text "Checked 420 of 1052 places", link to `judge-run`.
- Verdicts making one group `ready`, one `choose`, one `rewrite`, one
  `unchecked`: state `ready`, counters 1/1/1/1, and a link to the judge review
  (Task 7).
- `?judge=ready` shows only ready groups; an unknown value is ignored.
- A user without `unit.review` or with the judge disabled sees no launch
  button; bucket counters still show if verdicts exist.
- The panel's place count equals the number of units matching
  `check:repeat-drift` in this project-language; when it differs from the
  queue's open places, both numbers are shown.
- Query count for the queue page grows by a constant with the number of
  groups (extend the pattern at `test_repeat_views.py:1436`).

**Step 2: Run and see them fail.**

**Step 3: Implement**

- `latest_repeat_judge_run(project_language)`: `ProducerRun.objects.filter(
  scope_type=ProducerRun.ScopeType.PROJECT, scope_id=str(project.pk),
  requested_mode="judge", scope_path=project_language.get_absolute_url(),
  requested_query=REPEAT_JUDGE_QUERY).order_by("-created").first()`.
- `judge_launch_url(project_language)`: the URL above, built with
  `urlencode`.
- In `repeat_queue`: judge every `open` group once through `judge_groups`
  (one verdict query), count buckets, apply the `judge` filter to the open
  groups, and build `judge_panel = {"state", "places", "queue_places",
  "buckets", "run", "coverage", "launch_url", "can_launch"}`. `running` wins
  while the latest run is `queued`/`running`/`cancel_requested`; otherwise
  `ready` if any bucket but `unchecked` is non-empty; otherwise `start`.
  `can_launch` = `translation.auto` and `unit.review` on the project and
  `judge_configuration_ready()`.
- Attach each page item's `GroupJudgement` as `item["judge"]` for Task 5.
- Template: replace the "Prepare recommendations" link with the panel from
  the prototype (states 1-3). Counters are links to `?status=open&judge=<bucket>`.
  In `ready`, `unchecked > 0` repeats the launch link as "Check the remaining
  places" (cached verdicts are not paid again). Keep the model banner.

**Step 4: Run and see them pass**; run the whole `test_repeat_views.py`.

**Step 5: Commit** `feat(repeats): guide the queue through a judge check`.

## Task 5: evidence and preselection on the card

**Files:**

- Modify: `weblate/templates/snippets/repeat_group.html`
- Modify: `weblate/trans/views/repeats.py` (order variants)
- Modify: `weblate/static/js/repeat-queue.js`
- Test: `weblate/trans/tests/test_repeat_views.py`

**Step 1: Write the failing tests**

- `ready` group: the recommended variant is first, its radio has `checked`,
  the preview button renders without `aria-disabled="true"`, badge
  "Recommended: checked by the judge", line "Back-translation: ...", and the
  note "The judge checked this translation in one of its places. Look through
  the preview before confirming."
- `choose` group: nothing checked; each passed variant shows "Checked by the
  judge".
- flagged variant: "The judge found an error" plus its reason; never checked.
- group without verdicts renders as before: update
  `test_queue_renders_unselected_decision_and_preview_selection`
  (`test_repeat_views.py:224`) to assert no `checked` only for that group.

**Step 2: Run and see them fail.**

**Step 3: Implement**

- View: for a `ready` item, move the recommended variant to the top of
  `item["variants"]` and set `variant["judge"]` from
  `item["judge"].variants[target]` on every variant.
- Template: badges carry text (never colour alone); back-translation and
  reason as muted lines under the label; `checked` on the recommended radio;
  the preview button omits `aria-disabled="true"` when something is checked.
  The summary badge in the group header reads "Recommended: <target>" for
  `ready`.
- JS: call `updateDecision()` once per form after wiring the listeners, so a
  preselected radio sets the hint and custom field correctly.

**Step 4: Run and see them pass.**

**Step 5: Commit** `feat(repeats): show judge evidence and preselect the checked variant`.

## Task 6: bulk apply from judgements

**Files:**

- Modify: `weblate/trans/repeat_bulk.py`
- Test: `weblate/trans/tests/test_repeat_bulk.py`

**Step 1: Write the failing tests** (new class `RepeatJudgeBulkTest`, reusing
`RepeatBulkServiceFixtures` at `test_repeat_bulk.py:288` and the verdict
factory):

- `plan_judge_bulk` returns one row per `ready` group, ordered by
  `queue_importance`, and never a `choose`/`rewrite`/`unchecked` group.
- `start_judge_bulk` with a subset of group ids creates one `RepeatBulkRun`
  and one item per group with `result is None`,
  `decision["source"] == "judge"`, `decision["target"]` equal to the full
  plural forms, and processing writes that target to the other places.
- Double submit with the same manifest returns the same run.
- Refusals without writes (`write_snapshot()` unchanged), each a
  `ValidationError`: a place edited after review; a new verdict turning the
  group into `choose`; a new verdict for the same text replacing the evidence;
  a group id not in the manifest; an expired manifest; a model-review manifest
  (other salt). Another actor gets `PermissionDenied`.
- A group with a needs-editing member applies instead of being skipped as
  `stale`. If this fails, the defect is in `_item_context` using a different
  unit filter from `live_group_contexts`; align `_item_context` with it and
  keep the model-path tests green.
- `start_undo` on a judge run restores the written places (reuse
  `RepeatBulkUndoSemanticsTest` assertions).

**Step 2: Run and see them fail.**

**Step 3: Implement**

- Rename `BulkReviewRow.result_id` to `row_id`; extract
  `_build_review_row(*, row_id, group, target, exclusions, action, rationale, actor)`
  from `_review_row`, which now delegates to it. Update
  `weblate/templates/repeat_bulk_review.html` and views for the rename.
- Give `load_manifest` a `salt` keyword (default the current salt).
- Extract `_create_apply_run(*, policy, actor, nonce, entries)` from
  `start_bulk` (the atomic run + items creation and `queue_bulk_run`);
  `start_bulk` uses it unchanged.
- `plan_judge_bulk(*, policy, actor)`: require `project.edit`; build live
  groups with `live_group_contexts(policy, actor=actor)`, load their units,
  group variants by `tuple(unit.get_target_plurals())`, call `judge_groups`,
  keep `ready`. Rationale = the recommended variant's back-translation.
  Manifest salt `weblate.repeat-bulk-judge`, payload as in `plan_bulk` with
  `"groups": {str(group_id): {"target": [...], "evidence": fingerprint(evidence),
  "context_fingerprint": context_fingerprint(context)}}`.
- `start_judge_bulk(*, policy, actor, manifest, group_ids)`: nonce
  idempotency, subset validation, then recompute the judgement of every
  selected group and require bucket `ready`, the same target, the same
  evidence fingerprint and the same context fingerprint. Items:
  `result=None`, `decision={"action": "use_existing", "target": [...],
  "exclusions": [], "rationale": ..., "source": "judge", "evidence": ...}`.

Item processing, resume, status and undo are not changed.

**Step 4: Run and see them pass**; run all of `test_repeat_bulk.py`.

**Step 5: Commit** `feat(repeats): apply judge-checked variants as one batch`.

## Task 7: judge review page

**Files:**

- Modify: `weblate/urls.py` (after `repeat-bulk-review`)
- Modify: `weblate/trans/views/repeats.py`
- Create: `weblate/templates/repeat_judge_review.html`
- Modify: `weblate/static/js/repeat-queue.js` (selection counter)
- Test: `weblate/trans/tests/test_repeat_views.py`

**Step 1: Write the failing tests**

- `repeat-judge-review` at `repeats/<project>/<language>/judge/`: GET lists
  ready groups with source, "Will be everywhere" target plus back-translation,
  the other variants, and the number of places that change; every row has a
  checked `name="group"` checkbox; the button reads "Apply N groups".
- POST `action=apply` with two of three groups redirects to
  `repeat-bulk-status` and creates two items; unknown `action` is 400.
- A stale POST re-renders with the refresh message and writes nothing.
- Requires `project.edit` and project access (403 otherwise); a disabled
  policy shows no apply button.
- GET performs no durable write; query count constant in the number of rows.

**Step 2: Run and see them fail.**

**Step 3: Implement** the view on the pattern of `repeat_bulk_review`
(`weblate/trans/views/repeats.py:852-905`) with `plan_judge_bulk` /
`start_judge_bulk`. The template follows prototype state 4: one table, per-row
checkbox with an accessible label naming the source, a `<details>` with the
places (reuse the rows' `members`), a sticky footer with the button and a
polite live region "N places will change." The JS updates the button text and
the live region on checkbox change and works without JS (static counts). The
status page is reused as it is.

**Step 4: Run and see them pass.**

**Step 5: Commit** `feat(repeats): review judge-checked groups in one table`.

## Task 8: Russian strings

Extract with `uv run ./manage.py makemessages -l ru -d django`, keep only this
change's entries, validate with
`msgfmt -c -o /dev/null weblate/locale/ru/LC_MESSAGES/django.po`.

| English msgid | Russian |
| --- | --- |
| How to sort out %(count)s groups quickly | Как быстро разобрать %(count)s групп (3 формы) |
| Check variants with the judge | Проверить варианты судьёй |
| The judge checks every translation variant and marks errors. Where exactly one variant has none, you can apply it to many groups at once after one table. | Судья проверит каждый вариант перевода и отметит ошибки. Если в группе ровно один вариант без ошибок, его можно применить сразу ко многим группам после проверки одной таблицы. |
| Opens the usual judge launch for these %(count)s places. The check is paid; its cost is shown before launch and in the run report. | Откроется обычный запуск проверки судьёй для этих %(count)s мест. Проверка платная; её стоимость видна перед запуском и в отчёте. |
| Verdicts are recorded; string states are not changed. | Вердикты записываются, состояния строк не меняются. |
| The judge is checking variants | Судья проверяет варианты |
| Checked %(recorded)s of %(total)s places | Проверено %(recorded)s из %(total)s мест |
| exactly one variant without errors | ровно один вариант без ошибок |
| several variants passed, the choice is yours | судья одобрил несколько вариантов, выбор за вами |
| errors in every variant, a new translation is needed | ошибки во всех вариантах, нужен новый перевод |
| not checked by the judge | не проверены судьёй |
| Check the remaining places | Проверить оставшиеся места |
| Review and apply at once | Проверить и применить разом |
| Recommended: checked by the judge | Рекомендуем: проверен судьёй |
| Checked by the judge | Проверен судьёй |
| The judge found an error | Судья нашёл ошибку |
| Back-translation: %(text)s | Обратный перевод: %(text)s |
| The judge checked this translation in one of its places. Look through the preview before confirming. | Судья проверил этот перевод в одном из мест. Перед подтверждением просмотрите, что изменится. |
| Apply variants checked by the judge | Применить варианты, одобренные судьёй |
| Will be everywhere | Станет везде |
| Also now | Сейчас ещё |
| Apply %(count)s groups | Применить %(count)s групп (3 формы) |
| %(count)s places will change. | Изменится %(count)s мест. (3 формы) |

Plural entries use `{% blocktranslate count %}` / `ngettext` with all three
Russian forms. Commit `fix(i18n): translate the judge-checked repeat flow`.

## Task 9: product truths, contract and prototype status

**Files:**

- Modify: `PRODUCT.md` ("Product truths that designs must respect")
- Modify: `docs/product/plans/2026-09-21-repeat-queue-ui-variant.md` (§2.2 and variant rows)
- Modify: `analysis/prototypes/repeat-queue/README.md` (status of `judge-bulk.html`)

`PRODUCT.md`: "the UI preselects nothing" becomes "the UI preselects nothing
except a repeat variant the LLM judge passed as the only variant without
errors for its exact current text; a preselected choice still goes through
preview and confirmation". The paid-trigger truth states that the repeat
queue links to the standard judge launch and starts nothing itself.

UI contract: §2.2 renders the judge panel instead of "Update
recommendations"; variant rows gain badges, back-translation, reason and the
preselection rule, linking to this plan by its full path. README: mark
`judge-bulk.html` as the contract of this plan.

Commit `docs(repeats): record the judge-checked repeat flow`.

## Task 10: verification and delivery

- `uv run pytest weblate/trans/tests/test_judge.py weblate/trans/tests/test_repeat_judge.py weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_repeats.py weblate/trans/tests/test_repeat_views.py weblate/trans/tests/test_repeat_bulk.py -n 0`
- `uv run prek run --files <changed files>`; record the pre-existing
  `reuse lint` failure separately.
- `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check --dry-run`
  prints "No changes detected" (this plan adds no migration).
- Browser check in Russian on dev, only after explicit deployment approval:
  keyboard only through panel, card, review table and status; the
  preselected radio is announced; badges are readable without colour.
  If not approved, record that it was not run.
- **Gated, paid:** a real judge run over anvil-saga / fr `check:repeat-drift`
  (1022 places, two seats) needs the owner's explicit approval after the
  launch preview's cost line is shown to them. Afterwards record the bucket
  counts, read at least 25 `ready` recommendations with their
  back-translations, and record how many are wrong. If more than one in five
  is wrong, stop before any bulk apply and revise the judgement rule.
- Push the branch; the owner decides the merge.

## Out of scope

- Starting the judge from the queue without the launch form, and any
  background run.
- Deleting the model-recommendation code or its pages.
- Undo restoring the previous state (it restores text and sets
  `STATE_TRANSLATED`) and removing shared memberships
  (`weblate/trans/repeats.py:740-813`).
- `apply_preview` stamping `decision_origin="manual"` for bulk writes.
- Plural groups on the card: variant radios post only the first form
  (`snippets/repeat_group.html`); bulk items already carry full forms.

## Results

Pending. Fill from observed output, with the tested commit.

| Check | Result |
| --- | --- |
| `active_verdicts` equals `active_verdict`, one query | Pending |
| Judgement rule cases (ready / choose / rewrite / unchecked, approved, stale) | Pending |
| Proposal-only launch keeps string states | Pending |
| Queue panel states and bucket filter; no paid recommend link | Pending |
| Card preselection and evidence | Pending |
| Judge bulk: exact items, stale refusals, undo | Pending |
| Queue and review query counts constant | Pending |
| Russian browser and keyboard check | Pending |
| Real judge run: buckets and wrong-recommendation rate | Pending (paid, gated) |
