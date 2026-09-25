# Repeat queue: judge-checked variants and one-table bulk apply

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

Date: 2026-09-24, rewritten in full on 2026-09-25, engineering review folded
in on 2026-09-25.
Status: **phase 1 (Tasks 1-6) implemented at `ee3e4012` and Task 7 local
verification recorded on 2026-09-25. Deployment, the paid judge run and the
25-ready-group precision gate await explicit approval. Phase 2 has not started.**
This version replaces the 2026-09-24 text (judge preselection only, bulk apply
out of scope). Editing this plan does not authorize deploying it or starting a
paid judge run. Phase 2 (Tasks 8-10) starts only after the phase 1 gate in
Task 7.

**Goal:** A producer who cannot read the target language clears most of a
repeat queue (anvil-saga / fr: 383 diverging groups, 1022 places) by letting
the existing LLM judge check every variant, then reviewing one table of groups
where exactly one variant has no errors and applying it with one click and one
undo.

**Architecture:** The queue gains a state-driven panel that links to the
existing judge launch, prefilled for the queue's places and in a mode that
records verdicts only: string states do not change, no repair candidates are
generated and no missing string is pre-translated. A batched reader turns
stored verdicts into one judgement per group (ready / choose / rewrite /
unchecked). Cards show the evidence and preselect the ready variant. After the
gate, a second review page freezes the ready groups in a signed manifest and
feeds them into the existing `RepeatBulkRun` machinery (apply, resume, status,
undo) with `result=None` items, so no migration is needed.

**Tech Stack:** Django 6 views and templates with `{% translate %}`,
`weblate/trans/models/judge.py`, new `weblate/trans/repeat_judge.py`,
`weblate/trans/repeats.py`, `weblate/trans/repeat_bulk.py`,
`weblate/trans/repeat_recommendations.py`, `weblate/trans/views/repeats.py`,
`weblate/trans/views/basic.py`, `weblate/trans/views/edit.py`,
`weblate/trans/forms.py`, `weblate/static/js/repeat-queue.js`, Russian `.po`,
pytest.

UI contract: `analysis/prototypes/repeat-queue/judge-bulk.html`
(`?state=start|running|ready|review|done`). Templates are written from this
plan and the prototype's structure, not copied from it. The prototype's
numbers (1052 places, 214 ready groups) are illustrative; this plan's figures
come from the local copy.

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
| D2 | Nothing paid starts without a click on the existing judge launch form. | The queue only links; the producer launches from the standard form, which shows a cost estimate when enough priced history exists. No background runs. |
| D3 | The queue's judge link is prefilled: mode `judge`, query `check:repeat-drift`, this language, proposal-only, return to the queue. | Proposal-only records verdicts and never changes string states (`weblate/trans/autotranslate.py:1396`), so a critical verdict does not drop a place out of its repeat group. |
| D4 | A group is **ready** only when exactly one variant passed, every other variant was checked and flagged, and no place is approved. | Two passed variants, or an approved place, mean "choose yourself". An unchecked variant means "unchecked" (D9). Majority is never evidence. |
| D5 | The ready variant is preselected on the card; preview and confirmation stay mandatory for single-group decisions. | Changes the `PRODUCT.md` truth "the UI preselects nothing" for this case only. Plural groups are never preselected (the card posts one form). |
| D6 | Evidence is shown under each variant: judge badge, back-translation, and the reason of a flagged variant. | Read-only; lets a producer understand a French variant without reading French. |
| D7 | Ready groups are applied in bulk from one review table through the existing `RepeatBulkRun`. | Items carry `result=None` and a copied decision with `"source": "judge"`; resume, status and undo are reused unchanged. |
| D8 | The queue gets a panel with three states (start / running / ready) and four bucket counters that filter the queue. | Replaces the toolbar button; buckets are `ready`, `choose`, `rewrite`, `unchecked`. |
| D9 | Every variant of a group must be checked before the group can be `ready` (review Q1, answer A). | One passed variant next to an unchecked one is `unchecked`, not `ready`: an unchecked variant could also pass, which would make the group "choose yourself". |
| D10 | The queue's judge run records verdicts only (review Q2, answer A). | A proposal-only launch stores `judge_candidate_severities=[]` and skips the machine-translation preparation: no repair candidate is paid for, since this flow never shows one and bulk apply overwrites flagged places. |
| D11 | A place's verdict is the existing collegium rule, unchanged (review Q3, answer B; the owner revised an earlier answer A on 2026-09-25). | `active_verdict` / `collegium_verdict` (`weblate/trans/models/judge.py:1336-1364`): when both seats answered, the strictest wins; when one seat failed in transport, the other seat's parsed verdict stands. No seat-count requirement. The queue must show the same verdict as the editor card; a disagreement between seats is already safe because the strictest wins; the only difference from requiring both seats is a one-seat transport failure, and the phase 1 paid run with its 25-group check shows whether that matters. Tighten later if it does. |
| D12 | Two phases (review Q4, answer A). | Phase 1 = Tasks 1-7: judgements, panel, card, and a gated paid run with a precision check of at least 25 ready groups. Phase 2 = Tasks 8-10 (bulk apply) starts only if at most one in five is wrong and the owner approves phase 2. |

## Evidence the design rests on

Verified in the code and on the local copy of anvil-saga / fr on 2026-09-24/25,
and re-checked by the engineering review on 2026-09-25.

- **Scope query.** `check:repeat-drift` selects failing places
  (`weblate/utils/search.py:909-926`). Until commit `90335925` the check capped
  itself at 200 groups per plural form and matched only 553 of 1022 places; it
  now matches all 1022 places of the 383 groups. The check is
  `default_disabled`: a component must carry the `repeat-drift` flag, and
  members marked independent or `ignore-repeat-drift` are skipped
  (`weblate/checks/consistency.py:324-331`). The queue's groups
  (`weblate/trans/repeats.py:816-836`) do not skip them, so such places are
  never judged through this query.
- **Launch.** `auto_translation` (`weblate/trans/views/edit.py:2012-2044`)
  accepts a project-language object and, in mode `judge`, calls
  `_start_judge_producer_run` (`weblate/trans/views/edit.py:1826-1961`). It
  stores the run as `ScopeType.PROJECT` with `scope_path` = the
  project-language URL and `requested_query` = the form's `q`
  (`weblate/trans/views/edit.py:1876-1908`), and judges only `scope_snapshot`
  unit ids. UI launches are uncapped (`execution_version=1`). The launch
  hardcodes `"judge_proposal_only": False` and
  `"judge_candidate_severities": list(DEFAULT_CANDIDATE_SEVERITIES)`
  (`weblate/trans/views/edit.py:1892-1895`), and requests the preparation
  whenever `auto_source == "mt"` (`weblate/trans/views/edit.py:1848-1850`);
  `AutoForm` defaults to `mt` when a routed engine exists. After the launch it
  redirects to the posted `next` (`weblate/trans/views/edit.py:1918`).
- **Repair candidates.** `needs_candidate` does not depend on proposal-only
  (`weblate/trans/judge_loop.py:735-739`): every flagged place costs one repair
  machine translation (`weblate/trans/judge_loop.py:1576-1580`) and two more
  judge calls on the candidate (`weblate/trans/judge_loop.py:1672-1684`). An
  empty `judge_candidate_severities` turns this off
  (`weblate/trans/judge_loop.py:1356-1360`, dispatched through
  `_producer_run_dispatch_kwargs` in `weblate/trans/tasks.py:1870`).
- **Prefill.** The project, component and translation pages copy `mode` and
  `q` from `request.GET` into `AutoForm.initial`
  (`weblate/trans/views/basic.py:553-567`); the project-language page does not
  (`weblate/trans/views/basic.py:385-392`). `#auto` opens the tab
  (`weblate/static/loader-bootstrap.js:1012-1031`). Browser storage restores
  persisted checkboxes and selects unless the key is present in the URL
  (`weblate/static/loader-bootstrap.js:1315-1370`); hidden inputs are not
  persisted.
- **Cost.** The launch form's AJAX preview shows an upper-bound judge cost
  only when every seat has enough priced history
  (`weblate/trans/views/edit.py:1700-1729`); the upper bound multiplies by
  `JUDGE_MAX_REPAIR_ATTEMPTS + 1`. The run report shows actual spend
  (`run_spend`, `weblate/trans/models/llm_usage.py:172-199`). Re-judging an
  unchanged unit reuses its cached verdict at no cost
  (`weblate/trans/judge_loop.py:447-474`).
- **Verdicts.** `JudgeVerdict` (`weblate/trans/models/judge.py:920-1047`)
  carries `seat`, `subject`, `unparsed`, `max_severity`, `errors`,
  `back_translation`, `target_hash`. `active_verdict(unit)`
  (`weblate/trans/models/judge.py:1362`) =
  `collegium_verdict(active_round(unit))`: per seat the newest parsed live row
  whose `target_hash` equals the current target, reduced with
  `JUDGE_CONSENSUS_REJECT`. It costs about three queries per unit. Its
  `verdict` is `pass` (none/minor), `flag` (major) or `reject` (critical). The
  same rule exists in SQL as `judge_status_annotations()`
  (`weblate/trans/models/judge.py:1456-1542`, matching on
  `target_storage_hash`). A reason text is built as
  `f"{label}: {description}"` from `primary_error` and `_CATEGORY_LABELS`
  (`weblate/trans/views/judge.py:120-131`, `298-309`).
- **Progress.** `ProducerRun.get_coverage()`
  (`weblate/trans/models/judge.py:460-551`) returns `total`, `recorded`,
  `pending` and more. `recorded` includes the `PENDING` rows a run reserves up
  front (`weblate/trans/autotranslate.py:1168-1180`), so "checked" is
  `total - pending`. `recent_producer_runs` for a project-language does not
  find UI judge runs, because they are stored as `PROJECT` scope.
- **Bulk engine.** Only `plan_bulk`, `_review_row` and `start_bulk`
  (`weblate/trans/repeat_bulk.py:89-297`) know about
  `RepeatRecommendationResult`. Item processing, resume, undo and the status
  page never read `item.result` (`weblate/trans/repeat_bulk.py:353-660`), and
  `RepeatBulkItem.result` is nullable. Item-time freshness compares
  `context_fingerprint` and member fingerprints
  (`weblate/trans/repeat_bulk.py:596-632`).
- **Two unit sets.** `detect_policy_groups` keeps only translated and approved
  places (`state__gte=STATE_TRANSLATED`, `weblate/trans/repeats.py:826`), and
  `live_group_contexts` builds contexts from them
  (`weblate/trans/repeat_recommendations.py:167-194`). `_item_context`
  (`weblate/trans/repeat_bulk.py:394-408`) and `preview_group`
  (`weblate/trans/repeats.py:444-495`) take every place in the rule with the
  same source, of any state below read-only. A context built with
  `live_group_contexts` therefore never matches at item time when a group's
  source also has a needs-editing or empty place, and aligning `_item_context`
  to the translated-only set would fail the next guard instead
  (`preview_members != frozen_members`, `weblate/trans/repeat_bulk.py:618`).
  The judge path builds its context with `_item_context`.
- **Query costs.** `live_group_contexts` runs a unit query per detected group,
  and `build_group_context` reads labels with `.values_list`
  (`weblate/trans/repeat_recommendations.py:112-114`), which bypasses the
  prefetch and costs one query per place. `preview_group` runs per review row;
  the existing review test allows about 30 queries per extra row
  (`weblate/trans/tests/test_repeat_views.py:1436-1470`).
- **Card.** `weblate/templates/snippets/repeat_group.html` never renders
  `checked`; variant radios post only the first plural form
  (`value="{{ variant.target.0 }}"`, lines 59-62). `repeat-queue.js` runs
  `updateDecision` only on `change`, so a server-side preselection needs one
  call on load. Custom and keep post `choice`.

## Judgement rule

Evaluated per group whose queue status is `open`.

1. For each place, take its active verdict (`active_verdict` semantics, D11):
   per seat the newest parsed live row for its current text, reduced by the
   collegium rule; a seat without a parsed row does not block the other.
2. A **place** is
   - `flagged` when that verdict is `flag` or `reject`;
   - `passed` when that verdict is `pass`;
   - otherwise (no parsed verdict for the current text) `unchecked`.
3. A **variant** is `flagged` when any of its places is flagged; `passed` when
   at least one place passed and none is flagged; otherwise `unchecked`.
4. The **group bucket**, first match wins:
   - `choose`: two or more variants `passed`;
   - `unchecked`: any variant `unchecked` (D9);
   - `choose`: exactly one variant `passed` while a place is approved;
   - `ready`: exactly one variant `passed` (every other variant is flagged);
   - `rewrite`: every variant `flagged`.
5. Only `ready` preselects on the card (never for a plural group) and enters
   the bulk table.
6. Evidence shown for a variant: the back-translation of the representative
   row of its passed place with the lowest unit id; for a flagged variant, the
   reason of the strictest flagged place (highest severity, then lowest unit
   id).

Known limit, stated in the UI copy: the judge checked a variant in the context
of its own places, not in every place of the group. The repeat rule's scope and
the manual "Different meanings" choice cover the rest. A place outside
`check:repeat-drift` (independent member, `ignore-repeat-drift`) is never
judged by the queue's link, so its group stays `unchecked` and is decided by
hand.

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
  Tests that compare with `judge_status_annotations()` also set
  `target_storage_hash=compute_target_storage_hash(unit.target)`.
- Deploying to the shared dev stack, restarting workers and launching a real
  judge run are outside this plan's approval (Tasks 7 and 10).

---

## Phase 1: judgements, panel and card

### Task 1: batched active verdicts

**Files:**

- Modify: `weblate/trans/models/judge.py` (next to `active_verdict`, line 1362)
- Test: `weblate/trans/tests/test_judge.py`

**Step 1: Write the failing test**

Add a test class that reuses the verdict factory shape from
`weblate/trans/tests/test_commands.py:1951` (`target_hash`, `context_hash`,
`judge_model`, `seat`, `unparsed`). Load the units with
`select_related("translation__component", "translation__plural")` so that
`get_target_plurals()` does not query inside the assertion:

```python
class ActiveVerdictsTest(ViewTestCase):
    def make_verdict(self, unit, **kwargs) -> JudgeVerdict:
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault(
            "target_storage_hash", compute_target_storage_hash(unit.target)
        )
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        kwargs.setdefault("unparsed", False)
        kwargs.setdefault("max_severity", JudgeVerdict.Severity.NONE)
        return JudgeVerdict.objects.create(unit=unit, **kwargs)

    def test_matches_active_verdict_per_unit_in_one_query(self) -> None:
        units = list(
            self.get_translation()
            .unit_set.select_related("translation__component", "translation__plural")
            .order_by("pk")[:5]
        )
        passed, flagged, one_seat, stale, empty = units
        self.make_verdict(passed, seat=1)
        self.make_verdict(passed, seat=2, max_severity="minor")
        self.make_verdict(flagged, seat=1, max_severity="major")
        # A newer unparsed row never hides an older parsed opinion.
        self.make_verdict(flagged, seat=1, unparsed=True)
        # Seat 2 failed in transport: seat 1's parsed pass stands.
        self.make_verdict(one_seat, seat=1)
        self.make_verdict(one_seat, seat=2, unparsed=True)
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
                annotated = {
                    unit.pk: unit.judge_active_severity
                    for unit in Unit.objects.filter(
                        pk__in=[unit.pk for unit in units]
                    ).annotate(**judge_status_annotations())
                }
                for unit in units:
                    single = active_verdict(unit)
                    self.assertEqual(
                        getattr(batched[unit.pk], "pk", None),
                        getattr(single, "pk", None),
                    )
                    if single is not None:
                        self.assertEqual(batched[unit.pk].verdict, single.verdict)
                    self.assertEqual(
                        getattr(batched[unit.pk], "effective_severity", None),
                        annotated[unit.pk],
                    )
```

Add a second case with a `critical` seat 1 and a `minor` seat 2 so both
`JUDGE_CONSENSUS_REJECT` values yield different `verdict`s, asserting equality
with `active_verdict` and with `judge_status_annotations()` under each
setting.

**Step 2: Run it and see it fail**

`uv run pytest weblate/trans/tests/test_judge.py -k ActiveVerdictsTest -n 0`
Expected: `ImportError`/`NameError` for `active_verdicts`.

**Step 3: Implement**

```python
def active_verdicts(units: Sequence[Unit]) -> dict[int, JudgeVerdict | None]:
    """Return ``active_verdict`` for many units with one query."""
    # The same rule lives in active_verdict() and, as SQL, in
    # judge_status_annotations(); ActiveVerdictsTest pins all three together.
    target_hashes = {
        unit.pk: compute_target_hash(unit.get_target_plurals()) for unit in units
    }
    newest: dict[tuple[int, int], JudgeVerdict] = {}
    rows = (
        JudgeVerdict.objects.filter(
            unit_id__in=target_hashes,
            target_hash__in=set(target_hashes.values()),
            subject=JudgeVerdict.Subject.LIVE,
            unparsed=False,
        )
        .only(
            "id",
            "unit",
            "seat",
            "max_severity",
            "unparsed",
            "errors",
            "back_translation",
            "target_hash",
            "timestamp",
        )
        .order_by("unit_id", "seat", "-timestamp", "-pk")
    )
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

This mirrors `_seat_round_rows(..., context_hash=None, prefer_parsed=True)`
reduced by `collegium_verdict`: per seat the newest parsed live row for the
current text, strictest seat wins, a seat without a parsed row does not block
the other (D11).

**Step 4: Run it and see it pass**, then run the whole
`weblate/trans/tests/test_judge.py`.

**Step 5: Commit** `feat(judge): read active verdicts for many units at once`.

### Task 2: group judgements

**Files:**

- Create: `weblate/trans/repeat_judge.py` (GPL header as in other new modules)
- Modify: `weblate/trans/models/judge.py` and `weblate/trans/views/judge.py`
  (only if `_CATEGORY_LABELS` has to move, see Step 3)
- Test: `weblate/trans/tests/test_repeat_judge.py`

**Step 1: Write the failing tests**

Build groups with the `add_group` helper of
`weblate/trans/tests/test_repeat_views.py:471` and the verdict factory from
Task 1. "Passed" below means a parsed `none`/`minor` row from both seats unless
stated. One test per rule row:

- one passed + one flagged variant: bucket `ready`, `recommended` is the
  passed variant's full target tuple; the flagged variant's `reason` is
  `"<category label>: <description>"`;
- one passed + one unchecked variant: `unchecked`, `recommended` is `None`
  (D9);
- one passed + one flagged + one unchecked: `unchecked`;
- a place whose seat 1 passed while seat 2 has only an unparsed row: the place
  and its variant are `passed`, so one such variant next to a flagged one is
  `ready` (D11);
- a place with a `major` from seat 1 only: its variant is `flagged`;
- a place with seat 1 `none` and seat 2 `major`: `flagged` (the strictest seat
  wins);
- two passed: `choose`, `recommended` is `None`; two passed + one unchecked:
  still `choose`;
- one passed + one flagged while a place is `STATE_APPROVED`: `choose`;
- all flagged: `rewrite`;
- nothing judged: `unchecked`;
- a verdict for an older target of a place is ignored;
- a variant with two passed places shows the back-translation of the place
  with the lower unit id; a variant with a `major` and a `critical` place shows
  the `critical` reason;
- a plural group (two forms) keeps full target tuples as variant keys;
- `judge_groups` for 20 groups runs one verdict query (`assertNumQueries`
  around the call with units loaded as in Task 1).

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
(`weblate/trans/views/repeats.py:149-165`). The place, variant and bucket rules
are the "Judgement rule" section, in that order; `judge_groups` reads all
places through one `active_verdicts` call. The reason text reuses `_CATEGORY_LABELS` from
`weblate/trans/views/judge.py:120`; move that mapping to
`weblate/trans/models/judge.py` if importing a view module from a service
module would create a cycle, and import it back in the view.

**Step 4: Run and see them pass.**

**Step 5: Commit** `feat(repeats): judge each repeat group from stored verdicts`.

### Task 3: verdict-only judge launch for the queue's places

**Files:**

- Modify: `weblate/trans/forms.py` (`AutoForm`, line 1318; layout)
- Modify: `weblate/trans/views/edit.py` (`_start_judge_producer_run`,
  lines 1826-1961; `auto_translation_preview` cost bound, lines 1700-1729)
- Modify: `weblate/trans/views/basic.py:385-392` (project-language `autoform`)
- Modify: `weblate/templates/snippets/autoform.html` (one explanatory line)
- Test: `weblate/trans/tests/test_judge_form.py`

**Step 1: Write the failing tests**

- GET
  `/projects/<project>/-/<lang>/?mode=judge&q=check:repeat-drift&judge_proposal_only=1&next=<queue URL>`
  as a reviewer with the judge configured: the rendered form has `mode`
  `judge`, `q` `check:repeat-drift`, a hidden `judge_proposal_only` with value
  `1` and a hidden `next` with the queue URL; the page shows "Verdicts are
  recorded; string states are not changed and no replacement translations are
  generated."
- POST the launch with `judge_proposal_only=1`, `auto_source=mt` and the queue
  URL as `next` (dispatch patched as in the existing launch tests of this
  module): the created `ProducerRun` has
  `execution_options["judge_proposal_only"] is True`,
  `execution_options["judge_candidate_severities"] == []`,
  `preparation_snapshot == {}` and `preparation_phase == ""`, `scope_path`
  equal to the project-language URL and
  `requested_query == "check:repeat-drift"`; the response redirects to the
  queue URL.
- `_producer_run_dispatch_kwargs` (`weblate/trans/tasks.py:1870`) for that run
  returns `judge_proposal_only=True` and `judge_candidate_severities=()`.
- POST without the field keeps today's behaviour: `False`, the default
  candidate severities, and the preparation when `auto_source=mt`.
- The preview (`auto_translation_preview`) with `judge_proposal_only=1` and
  priced history for both seats: the upper bound equals `high * processed`,
  without the `JUDGE_MAX_REPAIR_ATTEMPTS + 1` factor; without the field the
  factor stays.

The state behaviour D3 relies on is already pinned by
`test_proposal_only_reject_preserves_live_target_and_state`
(`weblate/trans/tests/test_judge_autotranslate.py:228`); do not duplicate it.

**Step 2: Run and see them fail.**

**Step 3: Implement**

- `AutoForm`: `judge_proposal_only = forms.BooleanField(required=False, widget=forms.HiddenInput)`,
  added to the crispy layout so it renders.
- `_start_judge_producer_run`: read
  `proposal_only = autoform.cleaned_data.get("judge_proposal_only", False)`;
  store `"judge_proposal_only": proposal_only` and
  `"judge_candidate_severities": [] if proposal_only else list(DEFAULT_CANDIDATE_SEVERITIES)`;
  `preparation_requested` is `False` when `proposal_only` (D10).
- `auto_translation_preview`: when `judge_proposal_only`, the upper bound
  omits the repair factor.
- Project-language view: pass
  `initial={**{key: request.GET[key] for key in ("mode", "q", "judge_proposal_only") if request.GET.get(key)}, "next": request.GET.get("next", "")}`,
  matching `weblate/trans/views/basic.py:560-567`.
- `autoform.html`: when `form.initial.judge_proposal_only`, show the
  translated line above.

**Step 4: Run and see them pass.** Run the whole `test_judge_form.py` and
`test_judge_autotranslate.py`.

**Step 5: Commit** `feat(judge): launch a verdict-only judge run from a prefilled link`.

### Task 4: queue panel, buckets and filter

**Files:**

- Modify: `weblate/trans/repeat_judge.py` (`latest_repeat_judge_run`, `judge_launch_url`)
- Modify: `weblate/trans/views/repeats.py` (`repeat_queue`, `_queue_groups`)
- Modify: `weblate/templates/repeat_queue.html` (panel, remove the paid link)
- Test: `weblate/trans/tests/test_repeat_views.py`

**Step 1: Write the failing tests**

- No verdicts, user may launch the judge: panel state `start`; the response
  contains the launch URL
  `<project-language URL>?mode=judge&q=check%3Arepeat-drift&judge_proposal_only=1&overwrite_existing=&next=<queue URL>#auto`
  and "Check variants with the judge"; it does **not** contain the
  `repeat-recommend` URL.
- A `ProducerRun` (mode `judge`, `scope_path` = project-language URL,
  `requested_query` = `check:repeat-drift`,
  `execution_options={"judge_proposal_only": True}`, status `running`) with a
  patched `get_coverage()` returning `{"total": 1022, "pending": 602}`: state
  `running`, text "Checked 420 of 1022 places", link to `judge-run`.
- A running run of three places with three real `PENDING` `JudgeRunUnit` rows
  and no verdicts shows "Checked 0 of 3 places" (reserved rows are not
  checked).
- A running judge run with the same query but
  `judge_proposal_only` false is ignored by the panel.
- The latest run `failed` (or `cancelled`/`partial`) with no verdicts: state
  `start` plus "The last check stopped before it finished."; with verdicts:
  state `ready` plus the same line.
- Verdicts making one group `ready`, one `choose`, one `rewrite`, one
  `unchecked`: state `ready`, counters 1/1/1/1. Phase 1 renders no review
  link (Task 9 adds it).
- `?judge=ready` shows only ready groups; an unknown value is ignored.
- An `unchecked` group whose unchecked place matches `check:repeat-drift`
  shows "Check the remaining places"; when every unchecked place is outside
  the check (member flagged `ignore-repeat-drift`), the link is absent and
  the panel says "1 place is outside the repeat check; the judge does not
  check it."
- A user without `unit.review`, without `translation.auto`, or with the judge
  disabled sees no launch button; bucket counters still show if verdicts
  exist.
- The panel's place count equals the number of units matching
  `check:repeat-drift` in this project-language; when it differs from the
  queue's open places, both numbers are shown.
- Query count for the queue page grows by a constant with the number of
  groups (extend `test_queue_banner_query_cost_is_bounded`,
  `weblate/trans/tests/test_repeat_views.py:1482`).

**Step 2: Run and see them fail.**

**Step 3: Implement**

- `latest_repeat_judge_run(project_language)`: `ProducerRun.objects.filter(
  scope_type=ProducerRun.ScopeType.PROJECT, scope_id=str(project.pk),
  requested_mode="judge", scope_path=project_language.get_absolute_url(),
  requested_query=REPEAT_JUDGE_QUERY,
  execution_options__judge_proposal_only=True).order_by("-created").first()`.
- `judge_launch_url(project_language, queue_url)`: the URL above, built with
  `urlencode`.
- In `repeat_queue`: judge every `open` group once through `judge_groups` (one
  verdict query), count buckets, apply the `judge` filter to the open groups,
  and read the ids of units matching `check:repeat-drift` in this
  project-language with one `filter_access` query. Build
  `judge_panel = {"state", "places", "queue_places", "buckets", "run",
  "checked", "total", "stopped", "relaunch_places", "outside_places",
  "launch_url", "can_launch"}`:
  - `running` wins while the latest run is `queued`/`running`/`cancel_requested`;
    otherwise `ready` if any bucket but `unchecked` is non-empty; otherwise
    `start`;
  - `checked = coverage["total"] - coverage["pending"]`;
  - `stopped` when the latest run is `failed`, `cancelled` or `partial`;
  - `relaunch_places` / `outside_places`: unchecked places of `unchecked`
    groups inside / outside the drift ids;
  - `can_launch` = `translation.auto` on the project-language object (the
    permission `auto_translation` checks), `unit.review` on the project, and
    `judge_configuration_ready()`.
- Attach each page item's `GroupJudgement` as `item["judge"]` for Task 5.
- Template: replace the "Prepare recommendations" link with the panel from
  the prototype (states 1-3, without the review link). Counters are links to
  `?status=open&judge=<bucket>`. In `ready`, `relaunch_places > 0` repeats the
  launch link as "Check the remaining places" (cached verdicts are not paid
  again); `outside_places > 0` shows the outside-the-check line. The launch
  text says the cost estimate appears before launch when enough history
  exists. Keep the model banner.

**Step 4: Run and see them pass**; run the whole `test_repeat_views.py`.

**Step 5: Commit** `feat(repeats): guide the queue through a judge check`.

### Task 5: evidence and preselection on the card

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
- `ready` plural group (two forms): nothing is checked, the variant shows
  "Checked by the judge" and its back-translation, and the group header shows
  no "Recommended" badge.
- `choose` group: nothing checked; each passed variant shows "Checked by the
  judge".
- flagged variant: "The judge found an error" plus its reason; never checked.
- a back-translation `<b>x</b><script>y</script>` and a reason containing
  `<color=#FF0000>` render escaped.
- a group with a current model recommendation and a `ready` judgement: the
  judge's variant radio is checked, the model recommendation radio is not.
- group without verdicts renders as before: update
  `test_queue_renders_unselected_decision_and_preview_selection`
  (`weblate/trans/tests/test_repeat_views.py:224`) to assert no `checked` only
  for that group.

**Step 2: Run and see them fail.**

**Step 3: Implement**

- View: for a `ready` item, move the recommended variant to the top of
  `item["variants"]` and set `variant["judge"]` from
  `item["judge"].variants[target]` on every variant; set
  `item["preselect"]` only when the group has one plural form.
- Template: badges carry text (never colour alone); back-translation and
  reason as muted lines under the label, rendered with autoescape (no
  `|safe`); `checked` on the recommended radio when `item.preselect`; the
  preview button omits `aria-disabled="true"` when something is checked. The
  summary badge in the group header reads "Recommended: <target>" for a
  preselected group.
- JS: call `updateDecision()` once per form after wiring the listeners, so a
  preselected radio sets the hint and custom field correctly.

**Step 4: Run and see them pass.**

**Step 5: Commit** `feat(repeats): show judge evidence and preselect the checked variant`.

### Task 6: phase 1 Russian strings, product truths and contract

**Files:**

- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Modify: `PRODUCT.md` ("Product truths that designs must respect")
- Modify: `docs/product/plans/2026-09-21-repeat-queue-ui-variant.md` (§2.2 and variant rows)
- Modify: `analysis/prototypes/repeat-queue/README.md` (status of `judge-bulk.html`)

Extract with `uv run ./manage.py makemessages -l ru -d django`, keep only this
change's entries, validate with
`msgfmt -c -o /dev/null weblate/locale/ru/LC_MESSAGES/django.po`.

| English msgid | Russian |
| --- | --- |
| How to sort out %(count)s groups quickly | Как быстро разобрать %(count)s групп (3 формы) |
| Check variants with the judge | Проверить варианты судьёй |
| The judge checks every translation variant and marks errors. Where exactly one variant has none, it is recommended in the group. | Судья проверит каждый вариант перевода и отметит ошибки. Если в группе ровно один вариант без ошибок, он будет рекомендован. |
| Opens the usual judge launch for these %(count)s places. The check is paid: when enough history exists, an estimate is shown before launch; the actual spend is in the run report. | Откроется обычный запуск проверки судьёй для этих %(count)s мест. Проверка платная: если хватает истории, оценка видна перед запуском; фактические расходы - в отчёте проверки. |
| Verdicts are recorded; string states are not changed and no replacement translations are generated. | Вердикты записываются; состояния строк не меняются, новые варианты перевода не создаются. |
| The judge is checking variants | Судья проверяет варианты |
| Checked %(checked)s of %(total)s places | Проверено %(checked)s из %(total)s мест |
| The last check stopped before it finished. | Последняя проверка остановилась, не дойдя до конца. |
| exactly one variant without errors | ровно один вариант без ошибок |
| several variants passed, the choice is yours | судья одобрил несколько вариантов, выбор за вами |
| errors in every variant, a new translation is needed | ошибки во всех вариантах, нужен новый перевод |
| not checked by the judge | не проверены судьёй |
| Check the remaining places | Проверить оставшиеся места |
| %(count)s place is outside the repeat check; the judge does not check it. | %(count)s место вне проверки повторов, судья его не проверяет. (3 формы) |
| Recommended: checked by the judge | Рекомендуем: проверен судьёй |
| Checked by the judge | Проверен судьёй |
| The judge found an error | Судья нашёл ошибку |
| Back-translation: %(text)s | Обратный перевод: %(text)s |
| The judge checked this translation in one of its places. Look through the preview before confirming. | Судья проверил этот перевод в одном из мест. Перед подтверждением просмотрите, что изменится. |

Plural entries use `{% blocktranslate count %}` / `ngettext` with all three
Russian forms.

`PRODUCT.md`: "the UI preselects nothing" becomes "the UI preselects nothing
except a single-form repeat variant the LLM judge passed as the only
variant without errors for its exact current text; a preselected
choice still goes through preview and confirmation". The paid-trigger truth
states that the repeat queue links to the standard judge launch in a
verdict-only mode (no repair candidates, no pre-translation) and starts
nothing itself.

UI contract: §2.2 renders the judge panel instead of "Update
recommendations"; variant rows gain badges, back-translation, reason and the
preselection rule, linking to this plan by its full path. README: mark
`judge-bulk.html` states 1-3 as the contract of phase 1 and states 4-5 as
phase 2, pending the gate.

Commit `docs(repeats): record the judge-checked repeat queue` (strings as
`fix(i18n): translate the judge-checked repeat queue` if committed
separately).

### Task 7: phase 1 verification and the gate

- `uv run pytest weblate/trans/tests/test_judge.py weblate/trans/tests/test_repeat_judge.py weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_repeats.py weblate/trans/tests/test_repeat_views.py -n 0`
- `uv run prek run --files <changed files>`; record the pre-existing
  `reuse lint` failure separately.
- `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check --dry-run`
  prints "No changes detected" (this plan adds no migration).
- Browser check in Russian on dev, only after explicit deployment approval:
  keyboard only through the panel and the card; the preselected radio is
  announced and its hint is set on load (the `updateDecision()` call has no
  automated test); badges are readable without colour. If not approved,
  record that it was not run.
- **Gated, paid:** a real verdict-only judge run over anvil-saga / fr
  `check:repeat-drift` (1022 places, two seats) needs the owner's explicit
  approval after the launch preview's cost line (or its absence) is shown to
  them. Afterwards record the bucket counts and the actual spend, read at
  least 25 `ready` recommendations with their back-translations, and record
  how many are wrong.
- **Gate to phase 2:** Tasks 8-10 start only if at most one in five of the
  read `ready` recommendations is wrong **and** the owner approves phase 2
  after reading the recorded numbers. Otherwise revise the judgement rule in
  a new plan increment and stop.
- Push the branch; the owner decides the merge.

---

## Phase 2: bulk apply (after the Task 7 gate)

### Task 8: bulk apply from judgements

**Files:**

- Modify: `weblate/trans/repeats.py` (queue grouping moved here)
- Modify: `weblate/trans/views/repeats.py` (`_queue_groups` delegates)
- Modify: `weblate/trans/repeat_recommendations.py:112-114` (labels read)
- Modify: `weblate/trans/repeat_bulk.py`
- Test: `weblate/trans/tests/test_repeat_bulk.py`

**Step 1: Write the failing tests** (new class `RepeatJudgeBulkTest`, reusing
`RepeatBulkServiceFixtures` at `weblate/trans/tests/test_repeat_bulk.py:285`
and the verdict factory):

- `plan_judge_bulk` returns one row per `ready` group, ordered by
  `queue_importance`, and never a `choose`/`rewrite`/`unchecked` group, nor a
  group whose queue status is not `open` (a `resolved` group with a drifted
  place and passing verdicts stays out; so does a `rule-conflict` group).
- `start_judge_bulk` with a subset of group ids creates one `RepeatBulkRun`
  and one item per group with `result is None`,
  `decision["source"] == "judge"`, `decision["target"]` equal to the full
  plural forms, and processing writes that target to the other places.
- A ready group whose source also has a `STATE_FUZZY` place in the rule's
  scope applies instead of being skipped as `stale`, and that place is
  written too.
- Double submit with the same manifest returns the same run.
- Refusals without writes (`write_snapshot()` unchanged), each a
  `ValidationError`: a place edited after review; a new verdict turning the
  group into `choose` or `unchecked` between review and POST; a new verdict
  for the same text replacing the evidence; a group id not in the manifest;
  an expired manifest; a model-review manifest (other salt). Another actor
  gets `PermissionDenied`. A verdict for an older text of a place is ignored
  by the POST recheck (the group still applies).
- `start_undo` on a judge run restores the written places (reuse
  `RepeatBulkUndoSemanticsTest` assertions).
- The existing model-path tests in `test_repeat_bulk.py` and the queue tests
  in `test_repeat_views.py` stay green after the grouping move and the labels
  change (same values, same fingerprints).

**Step 2: Run and see them fail.**

**Step 3: Implement**

- Move the request-free core of `_queue_groups` and `_group_status`
  (`weblate/trans/views/repeats.py:87-185`) to
  `repeat_queue_groups(policy, *, user, component_ids=(), label_ids=())` in
  `weblate/trans/repeats.py`; `_queue_groups(request, policy)` parses the
  selectors and delegates. The panel (Task 4) and `plan_judge_bulk` both read
  groups from it, so the ready counter and the review table agree.
- `build_group_context`: read labels as
  `sorted(label.name for label in member.source_unit.labels.all())`, which
  uses the prefetch instead of one query per place and keeps the same values.
- `BulkReviewRow.result_id` becomes `int | None`; extract
  `_build_review_row(*, result_id, group, target, exclusions, action, rationale, actor)`
  from `_review_row`, which now delegates to it. Judge rows use
  `result_id=None` and are keyed by the existing `group_id`; the model review
  template and view stay as they are.
- Give `load_manifest` a `salt` keyword (default the current salt).
- Extract `_existing_run(*, nonce, policy, actor)` (the nonce lookup and actor
  check of `start_bulk`, `weblate/trans/repeat_bulk.py:219-225` and
  `290-295`) and `_create_apply_run(*, policy, actor, nonce, entries)` (the
  atomic run + items creation and `queue_bulk_run`); `start_bulk` uses both
  unchanged.
- `plan_judge_bulk(*, policy, actor)`: require `project.edit`; take the `open`
  groups of `repeat_queue_groups(policy, user=actor)`, call `judge_groups`,
  keep `ready`. For each ready group build its context with
  `_item_context(policy=policy, group=group, actor=actor)`, the same unit set
  as `preview_group` and the item-time check; never `live_group_contexts`.
  Rationale = the recommended variant's back-translation. Manifest salt
  `weblate.repeat-bulk-judge`, payload as in `plan_bulk` with
  `"groups": {str(group_id): {"target": [...], "evidence": fingerprint(evidence),
  "context_fingerprint": context_fingerprint(context)}}`.
- `start_judge_bulk(*, policy, actor, manifest, group_ids)`: `_existing_run`,
  non-empty and duplicate checks, subset validation, each group's policy is
  `policy`, then recompute the judgement of every selected group the same way
  and require bucket `ready`, the same target, the same evidence fingerprint
  and the same context fingerprint. Items: `result=None`,
  `decision={"action": "use_existing", "target": [...], "exclusions": [],
  "rationale": ..., "source": "judge", "evidence": ...}`,
  `context_fingerprint` from `_item_context`.

Item processing, resume, status and undo are not changed. Verdicts are not
re-checked item by item after the batch starts; only the context fingerprint
is (accepted limit).

**Step 4: Run and see them pass**; run all of `test_repeat_bulk.py`,
`test_repeats.py` and `test_repeat_views.py`.

**Step 5: Commit** `feat(repeats): apply judge-checked variants as one batch`.

### Task 9: judge review page

**Files:**

- Modify: `weblate/urls.py` (after `repeat-bulk-review`)
- Modify: `weblate/trans/views/repeats.py`
- Create: `weblate/templates/repeat_judge_review.html`
- Modify: `weblate/templates/repeat_queue.html` (review link in the `ready` panel)
- Modify: `weblate/static/js/repeat-queue.js` (selection counter)
- Test: `weblate/trans/tests/test_repeat_views.py`

**Step 1: Write the failing tests**

- `repeat-judge-review` at `repeats/<project>/<language>/judge/`: GET lists
  ready groups with source, "Will be everywhere" target plus back-translation,
  the other variants, and the number of places that change (including places
  of the same source that the queue does not list, such as needs-editing
  ones); every row has a checked `name="group"` checkbox; the button reads
  "Apply N groups". Back-translations render escaped.
- The queue panel in state `ready` links to the review for a user with
  `project.edit`; a user without it sees the counters and no link. The
  panel's ready counter equals the number of review rows.
- POST `action=apply` with two of three groups redirects to
  `repeat-bulk-status` and creates two items; unknown `action` is 400; a
  non-digit `group` value re-renders with the refresh message and writes
  nothing.
- A stale POST re-renders with the refresh message and writes nothing.
- Requires `project.edit` and project access (403 otherwise); a disabled
  policy shows no apply button.
- GET performs no durable write: with the groups created by the fixtures, the
  counts of `RepeatGroup`, `RepeatBulkRun`, `RepeatBulkItem` and
  `RepeatDecisionEvent` and every unit's target and state are unchanged.
- Query cost grows boundedly with the rows, on the pattern of
  `test_review_query_cost_is_bounded`
  (`weblate/trans/tests/test_repeat_views.py:1436-1470`): record the measured
  per-row growth in the assertion's comment.

**Step 2: Run and see them fail.**

**Step 3: Implement** the view on the pattern of `repeat_bulk_review`
(`weblate/trans/views/repeats.py:852-905`, including the non-digit guard at
lines 873-876) with `plan_judge_bulk` / `start_judge_bulk`. The template
follows prototype state 4: one table, per-row checkbox with an accessible
label naming the source, a `<details>` with the places (reuse the rows'
`members`), a sticky footer with the button and a polite live region "N places
will change." The JS updates the button text and the live region on checkbox
change and works without JS (static counts). The status page is reused as it
is.

**Step 4: Run and see them pass.**

**Step 5: Commit** `feat(repeats): review judge-checked groups in one table`.

### Task 10: phase 2 strings, contract and verification

Strings, extracted and validated as in Task 6:

| English msgid | Russian |
| --- | --- |
| Review and apply at once | Проверить и применить разом |
| Apply variants checked by the judge | Применить варианты, одобренные судьёй |
| Will be everywhere | Станет везде |
| Also now | Сейчас ещё |
| Apply %(count)s groups | Применить %(count)s групп (3 формы) |
| %(count)s places will change. | Изменится %(count)s мест. (3 формы) |

Contract: README marks `judge-bulk.html` states 4-5 as the contract of phase 2;
§2.2 of `docs/product/plans/2026-09-21-repeat-queue-ui-variant.md` gains the
review link.

Verification:

- `uv run pytest weblate/trans/tests/test_judge.py weblate/trans/tests/test_repeat_judge.py weblate/trans/tests/test_judge_form.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_repeats.py weblate/trans/tests/test_repeat_views.py weblate/trans/tests/test_repeat_bulk.py -n 0`
- `uv run prek run --files <changed files>`; `makemigrations --check` as in
  Task 7.
- After explicit deployment approval, on the dev instance with the anvil-saga
  / fr copy: open the judge review page once and record its query count and
  time; keyboard-only pass through the review table and the status page in
  Russian. If not approved, record that it was not run.
- Bulk apply on real data needs the owner's explicit approval; record the
  status page's counts (changed, skipped as out of date) and check that undo
  restores them.
- Push the branch; the owner decides the merge.

Commit `docs(repeats): record judge-checked bulk apply` (strings as
`fix(i18n): translate judge-checked bulk apply` if committed separately).

### Out of scope

- Starting the judge from the queue without the launch form, and any
  background run.
- Deleting the model-recommendation code or its pages.
- Undo restoring the previous state (it restores text and sets
  `STATE_TRANSLATED`) and removing shared memberships
  (`weblate/trans/repeats.py:740-813`).
- `apply_preview` stamping `decision_origin="manual"` for bulk writes.
- Plural groups on the card: variant radios post only the first form
  (`weblate/templates/snippets/repeat_group.html:59-62`); this plan never
  preselects them, and bulk items already carry full forms.
- Follow-up, model path: `prepare_run` freezes contexts with
  `live_group_contexts` (translated places only) while item time uses
  `_item_context` (all places of the source), so a model recommendation for a
  group with a needs-editing or empty place is always skipped as stale. Fix it
  in its own change, with the same approach as Task 8.
- Follow-up, queue cost: the queue page calls `current_recommendations`
  (`weblate/trans/views/repeats.py:311`), which runs `live_group_contexts`
  over every detected group whenever any model result exists. Task 8's labels
  fix reduces it; a scoped read is its own change.

### Results

Local verification of `ee3e4012` on 2026-09-25: the Task 7 combined pytest
command collected 294 tests: **293 passed, 1 failed** in 456.90 seconds. The
failure is
`ProducerRunDispatchTest.test_publishes_and_adopts_a_project_scoped_judge_run_end_to_end`
in `test_judge.py:414`: the run status was `failed`, expected `completed`.
The same test failed before the phase 1 changes (93 passed, 1 failed in the
earlier `test_judge.py` baseline); the combined suite is not green. All other
tests in the six-file run passed.

`uv run prek run --files` over files changed from `6a971b44` to `ee3e4012`
passed every applicable hook except repository-wide `reuse lint`, which
reported pre-existing missing copyright or license information in unrelated
files; the hooks made no file changes. `makemigrations --check --dry-run`
exited 0 and printed `No changes detected`; it also warned that the default
PostgreSQL connection could not authenticate role `postgres` on the local
socket. `msgfmt --check` passed for both Russian catalogs. `node --check`
passed for `repeat-queue.js` and `loader-bootstrap.js`.

Phase 1:

| Check | Result |
| --- | --- |
| `active_verdicts` equals `active_verdict` and `judge_status_annotations`, one query | Covered by passing `test_judge.py` cases; one unrelated end-to-end dispatch case failed as recorded above. |
| Judgement rule cases (ready / choose / rewrite / unchecked, unchecked variant, one parsed seat, strictest seat, approved, stale) | `test_repeat_judge.py`: all 18 tests passed. |
| Verdict-only launch: no state change, no candidates, no preparation, returns to the queue | Corresponding `test_judge_form.py` and `test_judge_autotranslate.py` cases passed. |
| Queue panel states, progress without reserved rows, stopped run, relaunch scope, bucket filter; no paid recommend link | Corresponding `test_repeat_views.py` cases passed. |
| Card preselection (single-form only), evidence, escaping | Corresponding `test_repeat_views.py` cases passed. |
| Queue query count constant | `test_queue_judge_query_growth_is_bounded` passed; `test_judge_groups_reads_twenty_groups_with_one_verdict_query` passed. |
| Russian browser and keyboard check | Not run: requires explicit deployment approval. |
| Real judge run: buckets, spend, wrong-recommendation rate out of at least 25 | Not run: paid run requires explicit approval after cost preview. No metrics recorded. |
| Gate decision for phase 2 | Pending the paid run, 25-ready-group precision check and owner approval; phase 2 not started. |

Phase 2:

| Check | Result |
| --- | --- |
| Judge bulk: exact items, fuzzy sibling applies, only open groups, stale refusals, undo | Pending |
| Review page: counter equals rows, no write on GET, bounded query growth | Pending |
| Review page query count and time on the anvil-saga / fr copy | Pending (gated) |
| Real bulk apply and undo | Pending (gated) |

### GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | - | - |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | - | - |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 27 findings (2 P1, 12 P2, 13 P3): 4 owner decisions resolved on 2026-09-25 (Q1 A, Q2 A, Q3 B after revision, Q4 A; recorded as D9-D12); 23 fixes folded into Tasks 1-10; 18 test gaps added to the tasks; 0 open |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | - | - |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | - |

- **VERDICT:** ENG CLEARED - ready to implement phase 1 after the owner approves this plan; phase 2 waits for the Task 7 gate.

NO UNRESOLVED DECISIONS
