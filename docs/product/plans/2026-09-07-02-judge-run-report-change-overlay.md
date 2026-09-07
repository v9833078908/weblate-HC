# Judge Run Report Current-text Overlay Plan

**Status:** proposed, revised after source review; awaiting approval. Not started.

## Decision

Keep `JudgeRunUnit` as immutable evidence of what the run found. Add a separate,
overlapping `changed-since-run` view for strings whose stored target no longer
matches the target the report's verdict judged. A changed target is **not** a
fixed target: it can be an unrelated edit, a regression, a pending candidate,
or text already covered by a newer re-check. It must never erase a historical
critical/major/minor outcome or be presented as an automatically confirmed fix.

The first version of this plan did the opposite: it reclassified a changed
critical as `fixed-since-run`, removed it from historical severity counts,
blocking and Pareto, and said that it had been fixed. That is unsafe and is
replaced in full by this document.

## Goal

A producer reading `/judge-runs/<uuid>/` can distinguish three facts without
mistaking one for another:

1. **What this immutable run found** — its recorded `critical`, `major`,
   `minor`, `unparsed`, and `stale-conflict` outcomes.
2. **Which current targets no longer equal the target this run judged** — the
   separate `Changed since this run` overlay, across every outcome including a
   previously passed string.
3. **What the current editor says** — the existing scope-wide live links and
   verdict card; the report's row action opens that current surface instead of
   declaring a stale verdict fixed.

## Review findings

| Finding | Evidence | Required correction |
|---|---|---|
| A target change proves staleness, not a fix. | `active_verdict()` only describes the current target (`weblate/trans/models/judge.py:984-1055`). The administrator guide says one manual edit hides hand-off until that string is re-checked (`docs/admin/checks.rst:275-286`). | Never remove or downgrade the run's verdict from a target hash mismatch. Never call the bucket `fixed`. |
| The run row is an audit record, not mutable current state. | `JudgeRunUnit` is explicitly “The immutable participation record for one unit in one producer run” (`weblate/trans/models/judge.py:543-544`); it snapshots `input_target`, `before_target`, and `after_target` (`:584`, `:620-621`). | Keep every existing outcome filter and its count unchanged. The new view is deliberately overlapping. |
| `input_target` is the start of a run, not its final text. | `process_judge()` writes `input_target` before judging and `after_target` after post-processing (`weblate/trans/autotranslate.py:941-959`). An automated repair can therefore make the current text differ from `input_target` while no post-run edit occurred. | For a hashless verdict, the row marker falls back to `after_target`, never `input_target`. |
| The final report verdict has the exact raw target hash needed for a constant-query overlay. | `judge_loop.py` writes `target_storage_hash = md5(request.target)` (`weblate/trans/judge_loop.py:327-344`); `judge_status_annotations()` already uses `MD5(OuterRef("target"))` for the same raw-storage comparison (`weblate/trans/models/judge.py:1160-1164`). | Compare `JudgeVerdict.target_storage_hash` with `MD5(F("unit__target"))` in PostgreSQL. No migration or Python scan. |
| The production observation is correlation only. | Run `36a5be4a-b2d8-42a7-a934-23eb76800737` has 13 current targets different from its recorded verdicts; those edits correlate with accepted suggestions, but no re-check result proves every one correct. | Label the 13 as changed, not fixed; the post-deploy check must not claim that the 13 defects disappeared. |
| Existing hero-card numbers are historical while its editor links are live. | The report counts filter frozen `JudgeRunUnit.outcome` (`weblate/trans/views/judge.py:125-151`); `blocking_review_url` uses live `judge:reject` search (`:531-537`). | Make hero-card prose explicitly historical and name the link as current. Do not promise that an old count equals a live editor result. |

## Evidence

Observed read-only on 2026-09-07 for production run
`36a5be4a-b2d8-42a7-a934-23eb76800737`
(`need-for-greed/ui/es`, 462 rows, completed 2026-09-04):

| Historical outcome | Rows | Current target differs from recorded verdict target |
|---|---:|---:|
| Needs action | 108 | 13 |
| Critical held | 43 | 11 |
| Major not fixed | 49 | 2 |
| Minor noted | 16 | 0 |
| Unparsed / stale conflict | 0 | 0 |
| Accepted as is | 4 | — |

The old card says 39 strings will not ship: 43 recorded critical outcomes less
4 accepted-as-is. It neither establishes that the remaining 39 are currently
critical nor that the 11 changed critical outcomes were fixed. The revised surface
preserves `43` as an audit fact, shows `13` as changed, and asks the producer
to inspect current verdicts.

## Architecture

### Immutable report buckets

`_filter_outcome()` remains the single source for the existing buckets. Its
`actionable`, `critical`, `major`, `minor`, `unparsed`, and `stale-conflict`
branches do not change. Thus the existing invariant remains true:

```text
critical + major + minor + unparsed + stale-conflict == actionable
```

Each header count still equals its own report-local drill-down list. `blocking`,
`total_actionable`, and the Pareto table keep consuming these immutable buckets.

### Derived overlay

Add one valid report filter, `?outcome=changed-since-run`. It is intentionally
**not** a new `JudgeRunUnit.Outcome`, and its count overlaps every static
outcome. It contains only rows satisfying all conditions:

```text
row.unit exists
row.verdict.target_storage_hash is present
row.verdict.target_storage_hash != md5(row.unit.target)
```

It includes a changed `passed` row: an earlier pass says nothing about text
edited later. It excludes hashless verdicts because the required comparison is
unknown; they retain their immutable outcome and use the final run snapshot for
the row marker.

The database predicate is:

```python
rows.filter(
    unit__isnull=False,
    verdict__target_storage_hash__isnull=False,
).exclude(verdict__target_storage_hash=MD5(F("unit__target")))
```

`MD5("unit__target")` is incorrect: Django `Func` wraps a plain string in
`Value`, hashing the literal field name. `MD5(F("unit__target"))` is required.

The predicate is a single SQL query over the `JudgeRunUnit → verdict/unit`
joins already used by the page. It adds no Python per-row pass and no new
migration. It must not attempt to compare the JSON plural `after_target` in SQL
to raw `Unit.target`; the existing verdict storage hash is the exact compatible
representation for rows that have a verdict.

### Per-row marker and action

`row.current_target_matches` acquires one precise meaning: whether the current
target equals the text this row's verdict judged. Use the verdict storage hash
when present. For a row without such a hash, compare the current plural target
to `row.after_target` — the state at the end of the run, not `input_target` at
its beginning.

A row with a mismatched current target links to the same editor but says
`Check the current verdict`. It must not offer `Fix and re-check`, `Review`, or
a stored repair candidate for evidence tied to older text. A producer can see
whether a later re-check already completed, or queue a re-check if it did not,
on the string's existing verdict card.

### Hero card wording

The card remains the producer's route into current work but must not conflate
historical outcomes, producer decisions recorded later, and live search. For
example:

```text
39 critical outcomes from this run still need a producer decision.
Open currently blocking strings
13 strings have changed since this run. Check their current verdicts.
```

The first sentence counts recorded critical outcomes without an
`accepted-as-is` decision; it does not assert the current release state of
their targets. The button is a live `judge:reject` query and deliberately
carries no stale count. The changed line links to this report's
`?outcome=changed-since-run` rows, each of which opens its current string.

## Constraints

- PostgreSQL is the supported Weblate database. `MD5(F("unit__target"))` is
  already an established production query pattern here.
- No model field, migration, backfill, background task, automatic re-check,
  state transition, or change to judge prompts/severity is in scope.
- This reporting change never writes or clears a `JudgeRunUnit.outcome`, a
  `JudgeVerdict`, a `JudgeVerdict.resolution`, a repair candidate, a unit
  state, or a release hold. It only adds the overlay and changes its row action.
- `target_storage_hash=None` means unknown, never changed and never fixed.
- `unparsed` and `stale-conflict` retain their existing re-check semantics. A
  hashless row cannot enter the overlay.
- The route name `judge-run`, report URL shape, and every existing `outcome`
  key stay unchanged.
- `weblate/locale/ru/LC_MESSAGES/django.mo` is ignored. Validate the PO with
  `msgfmt`; do not stage an `.mo` file. Running `./rundev.sh compilemessages`
  mutates the shared dev stack and is not needed for the English browser pass;
  request explicit approval before doing it.
- Hand-seed only the required Russian messages. Do not run broad
  `makemessages`: it can introduce unrelated fuzzy matches. Record
  `grep -c '^#, fuzzy'` before and after.
- Before each commit, inspect `git status --short` and stage explicit paths
  only. Do not use `git add <directory>`.
- Run tests through `./rundev.sh test`; it provides the required test database
  and Django settings. Deployment is outside this plan and separately approved.

## Sequencing

`docs/product/plans/2026-09-07-mt-run-cost-receipt.md` plans a later
`JudgeRun` → `ProducerRun` rename and touches the same view, template, and
tests. Implement this plan first: no migration and no public symbol rename.
Afterwards the other plan's LSP rename mechanically carries this overlay with
it. If that rename lands first, replace the renamed symbols consistently but
preserve every behavior and invariant below.

---

## Task 1: Add the immutable `changed-since-run` overlay

### Files

- Modify: `weblate/trans/views/judge.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

#### Contract

- New filter key: `changed-since-run`.
- New `counts["changed-since-run"]` and `triage["changed_since_run"]`.
- The new key overlaps static outcomes; it does not alter a static filter,
  `actionable`, `blocking`, `total_actionable`, or categories.
- [ ] **Step 1: Write failing behavioral tests**

In `JudgeRunReportViewTest`, make both fixture helpers represent production
verdicts: in the `JudgeVerdict.objects.create(...)` keyword list, add
`target_storage_hash=compute_target_storage_hash(unit.target)` immediately after
`target_hash=...`.

Add these tests. They defend observable contracts, not implementation shape.

```python
def test_changed_since_run_is_an_overlay_not_a_fixed_outcome(self) -> None:
    self.enable_review()
    run = self.create_run()
    critical_unit, passed_unit = list(self.translation.unit_set.all()[:2])

    critical_verdict = self.make_verdict_with_error(
        critical_unit, severity="critical", category="mistranslation"
    )
    passed_verdict = JudgeVerdict.objects.create(
        unit=passed_unit,
        max_severity=JudgeVerdict.Severity.NONE,
        model_verdict=JudgeVerdict.Verdict.PASS,
        judge_model="vendor/model-a",
        seat=1,
        target_hash=compute_target_hash(passed_unit.get_target_plurals()),
        target_storage_hash=compute_target_storage_hash(passed_unit.target),
        context_hash=judge_context_hash(passed_unit),
    )
    critical_row = self.add_row(
        run,
        critical_unit,
        outcome=JudgeRunUnit.Outcome.CRITICAL,
        verdict=critical_verdict,
    )
    passed_row = self.add_row(
        run,
        passed_unit,
        outcome=JudgeRunUnit.Outcome.PASSED,
        verdict=passed_verdict,
    )
    Unit.objects.filter(pk__in=[critical_unit.pk, passed_unit.pk]).update(
        target="text changed after this report"
    )

    default = self.client.get(self.report_url(run))
    self.assertEqual(default.context["counts"]["critical"], 1)
    self.assertEqual(default.context["counts"]["passed"], 1)
    self.assertEqual(default.context["counts"]["actionable"], 1)
    self.assertEqual(default.context["triage"]["blocking"], 1)
    self.assertEqual(default.context["counts"]["changed-since-run"], 2)
    self.assertEqual([row.pk for row in default.context["page_obj"]], [critical_row.pk])

    changed = self.client.get(self.report_url(run), {"outcome": "changed-since-run"})
    self.assertCountEqual(
        [row.pk for row in changed.context["page_obj"]],
        [critical_row.pk, passed_row.pk],
    )


def test_hashless_verdict_is_not_classified_as_changed(self) -> None:
    self.enable_review()
    run = self.create_run()
    unit = self.get_unit()
    verdict = self.make_verdict_with_error(
        unit, severity="critical", category="mistranslation"
    )
    row = self.add_row(
        run, unit, outcome=JudgeRunUnit.Outcome.CRITICAL, verdict=verdict
    )
    JudgeVerdict.objects.filter(pk=row.verdict_id).update(target_storage_hash=None)
    Unit.objects.filter(pk=unit.pk).update(target="different but unknown")

    response = self.client.get(self.report_url(run))
    self.assertEqual(response.context["counts"]["critical"], 1)
    self.assertEqual(response.context["counts"]["changed-since-run"], 0)


def test_changed_since_run_disappears_when_the_target_is_restored(self) -> None:
    self.enable_review()
    run = self.create_run()
    unit = self.get_unit()
    verdict = self.make_verdict_with_error(
        unit, severity="critical", category="mistranslation"
    )
    self.add_row(run, unit, outcome=JudgeRunUnit.Outcome.CRITICAL, verdict=verdict)
    original = unit.target
    Unit.objects.filter(pk=unit.pk).update(target="later text")
    self.assertEqual(
        self.client.get(self.report_url(run)).context["counts"]["changed-since-run"],
        1,
    )
    Unit.objects.filter(pk=unit.pk).update(target=original)
    self.assertEqual(
        self.client.get(self.report_url(run)).context["counts"]["changed-since-run"],
        0,
    )
```

Extend the established `test_every_outcome_count_equals_its_report_local_row_count`
loop implicitly through `_OUTCOME_LABELS`; add an explicit
`self.assertEqual(stats["changed-since-run"], 0)` for its unchanged fixture.
The existing `test_report_page_query_count_does_not_grow_with_row_count` is the
query-budget regression: it exercises every header count, including the new
one, and must remain unchanged rather than be duplicated with synthetic rows.

- [ ] **Step 2: Prove the tests fail before implementation**

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py \
  -k "changed_since_run or every_outcome_count_equals" -q
```

Expected: fail with `KeyError: 'changed-since-run'` from the report context.

- [ ] **Step 3: Add the derived query**

`weblate/trans/views/judge.py`:

1. Import `F` from `django.db.models` and `MD5` from
   `django.db.models.functions`.
2. Add `"changed-since-run": gettext_lazy("Changed since this run")` beside
   the actionable outcome labels.
3. Add the one helper before `_filter_outcome`:

```python
def _changed_since_run(rows: QuerySet) -> QuerySet:
    """Rows whose current target differs from this report's judged target."""
    return rows.filter(
        unit__isnull=False,
        verdict__target_storage_hash__isnull=False,
    ).exclude(verdict__target_storage_hash=MD5(F("unit__target")))
```

- Add the early `_filter_outcome` branch:

```python
if key == "changed-since-run":
    return _changed_since_run(rows)
```

- Do **not** change any existing branch. In particular, leave
  `if key == "actionable": return rows.filter(outcome__in=_ACTIONABLE_OUTCOMES)`
  and the literal severity return intact.
- Add `"changed_since_run": counts["changed-since-run"]` to `triage` for Task 3.
  Do not use it to derive `blocking`, `needs_recheck`, `total_actionable`, or
  `categories`.
- [ ] **Step 4: Run the focused and class regressions**

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py \
  -k "changed_since_run or every_outcome_count_equals or report_page_query_count" -q
./rundev.sh test weblate/trans/tests/test_judge_views.py -k JudgeRunReportViewTest -q
```

Expected: both pass. The changed overlay has 2 rows in the first test while
its report keeps 1 critical, 1 passed, 1 actionable, and 1 historical blocker.

- [ ] **Step 5: Commit**

```bash
git status --short
git add weblate/trans/views/judge.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): show targets changed since a run separately"
```

---

## Task 2: Make each report row describe the final judged target

### Files

- Modify: `weblate/trans/views/judge.py`
- Modify: `weblate/trans/tests/test_judge_views.py`

#### Contract

- The existing marker `current text changed since this run` means current text
  differs from this row's final verdict target; it no longer compares to
  `input_target` from before the run.
- A row with a stale target says `Check the current verdict` and opens the
  current string. It does not assert a fix or offer work for stale evidence.
- A row without a storage hash compares current plural forms to `after_target`.
- [ ] **Step 1: Write failing tests**

```python
def test_row_marker_compares_with_the_verdict_target(self) -> None:
    self.enable_review()
    run = self.create_run()
    unit = self.get_unit()
    verdict = self.make_verdict_with_error(
        unit, severity="critical", category="mistranslation"
    )
    self.add_row(run, unit, outcome=JudgeRunUnit.Outcome.CRITICAL, verdict=verdict)
    Unit.objects.filter(pk=unit.pk).update(target="changed after judgement")

    response = self.client.get(self.report_url(run), {"outcome": "changed-since-run"})
    [row] = response.context["page_obj"]
    self.assertFalse(row.current_target_matches)
    self.assertEqual(str(row.action), "Check the current verdict")
    self.assertContains(response, "current text changed since this run")
    self.assertNotContains(response, "Fix and re-check")


def test_hashless_marker_compares_with_the_final_run_snapshot(self) -> None:
    self.enable_review()
    run = self.create_run()
    unit = self.get_unit()
    row = self.add_row(
        run,
        unit,
        outcome=JudgeRunUnit.Outcome.UNPARSED,
        input_target=["before automatic processing"],
    )
    # The run itself finished with the current text. A start-of-run comparison
    # would mark this clean row stale; the final snapshot must not.
    JudgeRunUnit.objects.filter(pk=row.pk).update(
        after_target=unit.get_target_plurals()
    )
    clean = self.client.get(self.report_url(run), {"outcome": "unparsed"})
    self.assertTrue(clean.context["page_obj"][0].current_target_matches)
    self.assertNotContains(clean, "current text changed since this run")

    Unit.objects.filter(pk=unit.pk).update(target="changed after completion")
    changed = self.client.get(self.report_url(run), {"outcome": "unparsed"})
    self.assertFalse(changed.context["page_obj"][0].current_target_matches)
    self.assertContains(changed, "current text changed since this run")
```

- [ ] **Step 2: Prove the tests fail**

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py \
  -k "marker_compares or hashless_marker" -q
```

Expected: the first test receives the old `Fix and re-check` action; the second
wrongly marks the unchanged final target as changed because it compares with
`input_target`.

- [ ] **Step 3: Align the annotation and action**

Import `compute_target_storage_hash` in `weblate/trans/views/judge.py`. Replace
the first assignment in `_annotate_row` with:

```python
verdict = row.verdict
if unit is None:
    row.current_target_matches = False  # type: ignore[attr-defined]
elif verdict is not None and verdict.target_storage_hash:
    row.current_target_matches = (  # type: ignore[attr-defined]
        verdict.target_storage_hash == compute_target_storage_hash(unit.target)
    )
else:
    row.current_target_matches = (  # type: ignore[attr-defined]
        unit.get_target_plurals() == row.after_target
    )
```

Reuse `verdict` for `primary_error` below. Replace the complete action-selection
chain with:

```python
if unit is None:
    row.action = ""  # type: ignore[attr-defined]
elif not row.current_target_matches:
    row.action = gettext_lazy("Check the current verdict")  # type: ignore[attr-defined]
elif row.repair_status == _REPAIR.CANDIDATE_STORED:
    row.action = gettext_lazy("Review the suggested fix")  # type: ignore[attr-defined]
elif row.repair_status == _REPAIR.APPLIED:
    row.action = gettext_lazy("See the applied fix")  # type: ignore[attr-defined]
elif row.repair_status == _REPAIR.NO_ENGINE_FOR_LANGUAGE:
    row.action = gettext_lazy("Fix by hand")  # type: ignore[attr-defined]
else:
    row.action = _ACTION_BY_OUTCOME.get(  # type: ignore[attr-defined]
        row.outcome, gettext_lazy("View")
    )
```

The template already gates the marker on `row.unit`, so deleted rows retain
`nothing to do`.

- [ ] **Step 4: Run the class regression**

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py -k JudgeRunReportViewTest -q
```

Expected: pass, including existing current/stale/deleted-row coverage.

- [ ] **Step 5: Commit**

```bash
git status --short
git add weblate/trans/views/judge.py weblate/trans/tests/test_judge_views.py
git commit -m "fix(judge): compare report rows with their judged target"
```

---

## Task 3: Separate historical results from current navigation in the UI

### Files

- Modify: `weblate/trans/views/judge.py`
- Modify: `weblate/templates/judge-run.html`
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Modify: `weblate/trans/tests/test_judge_views.py`

#### Contract

- Static severity labels name the run context; no label says `not fixed`.
- Hero-card prose identifies unresolved producer decisions on critical outcomes
  from this run. Its editor link says current and has no promise that it
  contains the same number of rows.
- The overlay is visible without calling any changed string fixed.
- [ ] **Step 1: Write failing render tests**

```python
def test_hero_card_separates_historical_and_changed_counts(self) -> None:
    self.enable_review()
    run = self.create_run()
    unit = self.get_unit()
    verdict = self.make_verdict_with_error(
        unit, severity="critical", category="mistranslation"
    )
    self.add_row(run, unit, outcome=JudgeRunUnit.Outcome.CRITICAL, verdict=verdict)
    Unit.objects.filter(pk=unit.pk).update(target="later target")

    response = self.client.get(self.report_url(run))
    self.assertContains(
        response, "1 critical outcome from this run still needs a producer decision."
    )
    self.assertContains(response, "Open currently blocking strings")
    self.assertContains(response, "1 string has changed since this run.")
    self.assertContains(response, "?outcome=changed-since-run")
    self.assertNotContains(response, "Fixed since this run")
    self.assertNotContains(response, "needs a fix before release")


def test_static_outcome_labels_do_not_claim_current_state(self) -> None:
    self.enable_review()
    run = self.create_run()
    self.add_row(run, self.get_unit(), outcome=JudgeRunUnit.Outcome.MAJOR)
    response = self.client.get(self.report_url(run))
    self.assertContains(response, "Run outcomes needing attention: 1")
    self.assertContains(response, "Major in this run: 1")
    self.assertNotContains(response, "Major not fixed")
```

Delete `test_blocks_release_reflects_the_scope_project_commit_policy`: its only
observable contract was a template-context implementation detail. The revised
card makes no claim about the release state of a historical outcome, so
`_blocks_release`, `triage["blocks_release"]`, and the view's
`CommitPolicyChoices` import become dead code and must be removed in Step 3.

- [ ] **Step 2: Prove the tests fail**

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py \
  -k "hero_card_separates or static_outcome_labels" -q
```

Expected: current wording promises a fix before release; there is no changed
overlay sentence or truthful static label.

- [ ] **Step 3: Render truthful labels and the overlay**

In the existing `_OUTCOME_LABELS` dict, replace only these values:

```python
updated_labels = {
    "actionable": gettext_lazy("Run outcomes needing attention"),
    "critical": gettext_lazy("Critical in this run"),
    "major": gettext_lazy("Major in this run"),
    "minor": gettext_lazy("Minor in this run"),
}
```

In `judge-run.html`:

- critical branch: `{{ counter }} critical outcome from this run still needs a
  producer decision.` (pluralized);
- primary link: `Open currently blocking strings`;
- no critical but major/minor: `No critical outcome from this run awaits a
  decision.`;
- no static actionable rows: `This run needed no action. {{ counter }} string
  passed.` (pluralized);
- after the candidate link, when `triage.changed_since_run` is nonzero, add a
  muted link to `?outcome=changed-since-run`:

```html
{% blocktranslate count counter=triage.changed_since_run %}{{ counter }} string has changed since this run. Check its current verdict.{% plural %}{{ counter }} strings have changed since this run. Check their current verdicts.{% endblocktranslate %}
```

Delete the `triage.blocks_release` template branch. Delete `_blocks_release`,
its `CommitPolicyChoices` import, the local `blocks_release` variable and the
`"blocks_release"` triage entry from `weblate/trans/views/judge.py`.

Keep the visible card heading `What to do`: it routes to the live editor and the
changed report rows; only the outcome counts are explicitly historical.

- [ ] **Step 4: Add Russian messages by targeted PO edit**

Record the baseline first:

```bash
grep -c '^#, fuzzy' weblate/locale/ru/LC_MESSAGES/django.po
```

Hand-add the exact generated msgids for the five labels, five card messages,
and `Check the current verdict` to `django.po`, using the existing three-form
Russian plural style:

```po
#: weblate/templates/judge-run.html
#, python-format
msgid "%(counter)s string has changed since this run. Check its current verdict."
msgid_plural "%(counter)s strings have changed since this run. Check their current verdicts."
msgstr[0] "%(counter)s строка изменилась после этого прогона. Проверьте её текущий вердикт."
msgstr[1] "%(counter)s строки изменились после этого прогона. Проверьте их текущие вердикты."
msgstr[2] "%(counter)s строк изменилось после этого прогона. Проверьте их текущие вердикты."

#: weblate/templates/judge-run.html
#, python-format
msgid "%(counter)s critical outcome from this run still needs a producer decision."
msgid_plural "%(counter)s critical outcomes from this run still need a producer decision."
msgstr[0] "Для %(counter)s критического результата этого прогона всё ещё нужно решение продюсера."
msgstr[1] "Для %(counter)s критических результатов этого прогона всё ещё нужно решение продюсера."
msgstr[2] "Для %(counter)s критических результатов этого прогона всё ещё нужно решение продюсера."

#: weblate/templates/judge-run.html
msgid "No critical outcome from this run awaits a decision."
msgstr "В этом прогоне нет критических результатов, ожидающих решения."

#: weblate/templates/judge-run.html
#, python-format
msgid "This run needed no action. %(counter)s string passed."
msgid_plural "This run needed no action. %(counter)s strings passed."
msgstr[0] "В этом прогоне не потребовалось действий. %(counter)s строка прошла проверку."
msgstr[1] "В этом прогоне не потребовалось действий. %(counter)s строки прошли проверку."
msgstr[2] "В этом прогоне не потребовалось действий. %(counter)s строк прошли проверку."

#: weblate/templates/judge-run.html
msgid "Open currently blocking strings"
msgstr "Открыть текущие блокирующие строки"

#: weblate/trans/views/judge.py
msgid "Run outcomes needing attention"
msgstr "Результаты прогона, требующие внимания"

#: weblate/trans/views/judge.py
msgid "Critical in this run"
msgstr "Критично в этом прогоне"

#: weblate/trans/views/judge.py
msgid "Major in this run"
msgstr "Значимо в этом прогоне"

#: weblate/trans/views/judge.py
msgid "Minor in this run"
msgstr "Незначительно в этом прогоне"

#: weblate/trans/views/judge.py
msgid "Changed since this run"
msgstr "Изменено после этого прогона"

#: weblate/trans/views/judge.py
msgid "Check the current verdict"
msgstr "Проверить текущий вердикт"
```

Do not edit source-reference `#:` lines except for new entries, and do not run
`makemessages`.

Validate source translation syntax, without staging an ignored MO artifact:

```bash
msgfmt --check --statistics -o /dev/null weblate/locale/ru/LC_MESSAGES/django.po
grep -c '^#, fuzzy' weblate/locale/ru/LC_MESSAGES/django.po
```

Expected: `msgfmt` succeeds and the fuzzy count equals the recorded baseline.

- [ ] **Step 5: Run rendering tests and browser verification**

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py \
  -k "JudgeRunReportViewTest or JudgeCardLocalizationTest" -q
```

Then open a local report in the existing dev instance through Lightpanda. Use an
English UI unless explicit approval is given to compile Russian messages in the
shared dev stack. Verify visually:

1. historical severity button counts remain visible;
2. the changed overlay button opens only the target-mismatched rows;
3. a mismatched row says `Check the current verdict` and opens its string;
4. the hero card says the count belongs to this run and the editor link says
   current;
5. layout and keyboard-focus behavior remain ordinary Bootstrap links.

- [ ] **Step 6: Commit**

```bash
git status --short
git add weblate/trans/views/judge.py weblate/templates/judge-run.html \
  weblate/locale/ru/LC_MESSAGES/django.po \
  weblate/trans/tests/test_judge_views.py
git commit -m "fix(judge): distinguish report outcomes from current text"
```

---

## Task 4: Document the snapshot/overlay contract and run the judge regression

### Files

- Modify: `docs/admin/checks.rst`
- Modify: `docs/changes.rst`
- [ ] **Step 1: Update the administrator guide**

In the paragraph beginning `Every producer launch`, retain the list of run
outcomes and add one concise sentence immediately after it:

```rst
The outcome counts are immutable evidence from that run. When a current target
no longer matches the text its run verdict judged, a separate
:guilabel:`Changed since this run` count links to those strings; it records a
change, not a confirmed fix.
```

- [ ] **Step 2: Amend the existing unreleased report changelog bullet**

Append to the existing `Every LLM judge producer launch ...` bullet in the
unreleased section of `docs/changes.rst`:

```rst
The report keeps its recorded outcomes as audit evidence and separately marks
strings whose current target changed after the report's verdict, without
claiming that a change fixed the reported defect.
```

Do not add a new bullet: the report feature is already documented by that
unreleased bullet.

- [ ] **Step 3: Lint touched source and docs**

```bash
uv run prek run --files \
  weblate/trans/views/judge.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/templates/judge-run.html \
  docs/admin/checks.rst docs/changes.rst
```

Expected: all applicable hooks pass. If the repository-wide `typos` baseline
still reports findings in `analysis/data/**`, verify no touched file is named;
do not suppress unrelated findings.

- [ ] **Step 4: Run the full judge regression**

```bash
./rundev.sh test \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_round.py \
  weblate/trans/tests/test_judge_autotranslate.py -q
```

Expected: all pass. Do not retain a test that asserts a target change removed a
historical severity outcome; that would encode the rejected unsafe behavior.

- [ ] **Step 5: Commit and push**

```bash
git status --short
git add docs/admin/checks.rst docs/changes.rst
git commit -m "docs(judge): explain changed targets in run reports"
git push
```

## Acceptance criteria

- Every pre-existing report outcome keeps its count and report-local row set
  after a target edit. In particular, a changed critical remains both
  `critical` and `actionable`; it remains `blocking` until the producer records
  an accepted-as-is decision, and Pareto retains it as historical evidence.
- `?outcome=changed-since-run` is valid, includes changed `passed` rows as well
  as flagged rows, and its header count equals its own row list.
- The new bucket is an overlay, not a partition: it may overlap every static
  outcome and is never summed into `actionable`.
- A target hash mismatch is never displayed as fixed, repaired, accepted, or
  currently safe.
- A hashless verdict never enters the overlay. Its marker compares the current
  target with `after_target`, so an in-run automatic repair is not labelled a
  post-run edit.
- A mismatched row opens its string as `Check the current verdict`; it does not
  propose an action for the obsolete verdict.
- Hero-card count prose identifies an unresolved producer decision on an
  outcome from this run; its existing live editor link identifies itself as
  current.
- Every existing `?outcome=` remains valid; unauthorized reports still leak no
  count or scope label; no page query count grows with row count.
- PO syntax is valid, the fuzzy count is unchanged, and no `.mo` file is
  staged.
- Focused tests, `JudgeRunReportViewTest`, the full judge regression, targeted
  pre-commit hooks, and the local browser pass succeed.

## Post-deploy read-only verification (separate approval)

On production, re-read report `36a5be4a-b2d8-42a7-a934-23eb76800737` after a
separately approved deployment. Confirm:

| Measure | Before | Expected after |
|---|---:|---:|
| Needs action | 108 | 108 |
| Critical in this run | 43 | 43 |
| Major in this run | 49 | 49 |
| Minor in this run | 16 | 16 |
| Changed since this run | unavailable | 13, subject to later edits/rechecks |

Do **not** assert that the changed count means fixed. Manually open a sample
from the new bucket: a row that has a later current verdict should show it on
its string card; a row without current evidence should offer the existing
one-string re-check there. Verify the hero card names unresolved decisions on
critical outcomes from the run, never the current shipping state of a
historical count.

## Out of scope

- Automatic re-checking, batch re-checking, or generating candidates from the
  report.
- Reclassifying or deleting `JudgeRunUnit`/`JudgeVerdict` records.
- Inferring a manual fix from `Change` history or an accepted suggestion.
- Adding a stored `after_target_storage_hash` migration.
- Altering live judge search, release gates, judge models/prompts, costs,
  outcome storage, the `JudgeRun` → `ProducerRun` rename, or deployment.
