# Judge consensus REJECT implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** proposed, awaiting approval. Nothing below is deployed.

**Goal:** A string is held (`REJECT` -> `STATE_FUZZY`, excluded from the
release export) only when every parsed seat of the collegium grades it
`critical`; a `critical` from one seat that the other parsed seat grades lower
reads as `FLAG` (major): the string ships with both opinions attached and
appears under "Major not fixed", never under "Critical held".

**Architecture:** The rule lives in one pure function,
`collegium_severity(rows)` in `weblate/trans/models/judge.py`, next to the
existing `collegium_verdict(rows)`. `collegium_verdict` keeps returning the
strictest seat's row (its errors are the evidence the card, the repair prompt
and the candidate generator already use) and stamps the round severity on it as
a transient attribute; `JudgeVerdict.verdict` and a new
`JudgeVerdict.effective_severity` read that attribute, falling back to the
row's own `max_severity`. The SQL twin, `judge_status_annotations()`, gets the
same rule so the `judge:reject` / `judge:flag` search filters and the
`check:judge-*` projections agree with the Python read. No migration: the
`verdict` property was written to be reopened by measurement R3 without one
(`JudgeVerdict` docstring, models/judge.py:649-654).

**Tech stack:** Python 3.14, Django ORM (`Case`/`When`/`Exists` subqueries),
pytest via `./rundev.sh test`, prek for lint.

**Evidence and rationale:**
`docs/operations/audits/2026-09-04-need-for-greed-ui-es-judge-calibration.md`
(43 critical holds, 3 confirmed) and the investigation of 2026-09-04 in the
same session: on production run `36a5be4a`, 20 of the 36 critical holds with
intact per-seat evidence came from seat 2 alone while seat 1 recorded zero
errors on 11 of the 13 analyst-clean holds. Prompt work was measured in
`docs/llm-first/measurements/2026-08-19-severity-recalibration-final.md` and
cannot reach the R2 gate; its R3 conclusion ("the gate leaves severity-only")
was never implemented. Recomputed offline on the sealed zh corpus
(`analysis/data/st2-zh-recal`, arms C and D, 5 paired runs each), the rule
below cuts false criticals from a median 14/122 to 0-2 and demotes the one
revised true critical to `FLAG` in 1-2 of 5 runs.

**Out of scope:** any prompt edit (R3: it would invalidate every measurement),
seat model or reasoning changes, a per-project severity mapping, a span or
glossary validator in the reply parser, the run-report layout.

---

## The rule, stated once

For the parsed rows of one round:

| parsed seats' severities | round severity | verdict |
|---|---|---|
| all `critical` (one lone parsed seat included) | `critical` | `reject` |
| strictest is `critical`, another parsed seat is lower | `major` | `flag` |
| strictest is `major` / `minor` / `none` | unchanged (strictest) | as today |
| no parsed row | none | `unparsed` |

An unparsed row is still not an opinion: it neither raises nor lowers the round
(`test_a_parsed_seat_outvotes_an_unparsed_one` keeps passing). Below
`critical`, "no seat may lower another" is unchanged.

## Environment

Run tests inside the dev container so no host database setup is needed:

```sh
./rundev.sh test weblate/trans/tests/test_judge_round.py
```

Run lint on the host:

```sh
uv run prek run --files weblate/trans/models/judge.py weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py
```

Commit after every task. Commit messages use Conventional Commits and end
with the session attribution footer configured for this repository.

---

### Task 1: `collegium_severity` and `effective_severity` (Python read)

**Files:**
- Modify: `weblate/trans/models/judge.py:805-813` (the `verdict` property)
- Modify: `weblate/trans/models/judge.py:985-997` (`collegium_verdict`)
- Modify: `weblate/trans/models/judge.py:714` (comment on `seat`)
- Test: `weblate/trans/tests/test_judge_round.py`

**Step 1: Write the failing tests**

Add to `weblate/trans/tests/test_judge_round.py`, right after
`test_no_seat_may_lower_the_other` (line 213-224), and add
`collegium_severity` to the `weblate.trans.models.judge` import block at the top
of the file:

```python
    def test_a_lone_critical_seat_is_only_a_flag(self) -> None:
        # Consensus REJECT: one seat's critical against the other seat's
        # lower grade holds nothing. The row is still the critical seat's
        # (its errors are the evidence), but the round reads as major.
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=run)
        strict = self.make(unit, "critical", seat=2, run_id=run)
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.pk, strict.pk)
        self.assertEqual(verdict.max_severity, "critical")
        self.assertEqual(verdict.effective_severity, "major")
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_two_critical_seats_still_reject(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "critical", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.effective_severity, "critical")
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_lone_parsed_critical_seat_rejects(self) -> None:
        # One voice and nobody disagreeing: not a disputed critical.
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "critical", seat=1, run_id=run)
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_collegium_severity_is_pure(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        rows = [
            self.make(unit, "major", seat=1, run_id=run),
            self.make(unit, "critical", seat=2, run_id=run),
        ]
        self.assertEqual(collegium_severity(rows), "major")
        self.assertEqual(collegium_severity([rows[1]]), "critical")
        self.assertEqual(collegium_severity([rows[0]]), "major")
        self.assertIsNone(collegium_severity([]))

    def test_a_row_read_outside_the_collegium_keeps_its_own_severity(self) -> None:
        unit = self.get_unit()
        row = self.make(unit, "critical", seat=2, run_id=uuid.uuid4())
        row = JudgeVerdict.objects.get(pk=row.pk)
        self.assertEqual(row.effective_severity, "critical")
        self.assertEqual(row.verdict, JudgeVerdict.Verdict.REJECT)
```

Then change the existing `test_collegium_takes_the_strictest_seat` (line 204)
so it no longer asserts the old rule:

```python
    def test_collegium_takes_the_strictest_seat(self) -> None:
        # Below critical the strictest seat is the round: major beats minor.
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "minor", seat=1, run_id=run)
        self.make(unit, "major", seat=2, run_id=run)
        verdict = active_verdict(unit)
        assert verdict is not None
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict.seat, 2)
```

**Step 2: Run the tests to verify they fail**

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py -k "lone_critical or two_critical or lone_parsed or collegium_severity or outside_the_collegium"`
Expected: FAIL - `ImportError: cannot import name 'collegium_severity'`.

**Step 3: Implement**

In `weblate/trans/models/judge.py`, replace the `verdict` property
(lines 805-813) with:

```python
    @property
    def effective_severity(self) -> str:
        """
        The severity this row stands for where it was read.

        A row returned by ``collegium_verdict`` carries the round's
        severity (consensus rule, ``collegium_severity``), which may be
        lower than the row's own ``max_severity`` when this seat's
        ``critical`` is disputed by the other parsed seat. A row read
        directly from the database stands only for itself.
        """
        return getattr(self, "_round_severity", None) or self.max_severity

    @property
    def verdict(self) -> str:
        """
        Derive the verdict, never stored.

        The severity->verdict mapping is reopened by R3 and must change
        without a data migration (D4).
        """
        if self.unparsed:
            return self.Verdict.UNPARSED
        return verdict_for_severity(self.effective_severity)
```

Replace `collegium_verdict` (lines 985-997) with:

```python
def collegium_severity(rows: Sequence[JudgeVerdict]) -> str | None:
    """
    Reduce a round's parsed seats to one severity.

    Below ``critical`` the strictest seat is the round: no seat may
    lower another's finding. ``critical`` alone is different: it holds
    the string out of the release, and one seat's word is not enough for
    that. A ``critical`` that the other parsed seat grades lower reads
    as ``major`` (a flag, shipped with both opinions attached); only a
    round whose every parsed seat says ``critical`` rejects. A lone
    parsed seat is unanimous by itself. An unparsed row is not an
    opinion and takes no part. ``None`` when nothing parsed.

    Measured: docs/llm-first/plans/2026-09-04-judge-consensus-reject.md.
    """
    parsed = [row for row in rows if not row.unparsed]
    if not parsed:
        return None
    strictest = max(SEVERITY_RANK[row.max_severity] for row in parsed)
    severity = JudgeVerdict.Severity.values[strictest]
    if severity == JudgeVerdict.Severity.CRITICAL and any(
        row.max_severity != JudgeVerdict.Severity.CRITICAL for row in parsed
    ):
        return JudgeVerdict.Severity.MAJOR
    return severity


def collegium_verdict(rows: Sequence[JudgeVerdict]) -> JudgeVerdict | None:
    """
    Return the round's representative row, carrying the round severity.

    The representative is the strictest parsed seat (lowest seat number
    on a tie): its errors are the evidence the card, the repair prompt
    and the candidate generator read. Its ``effective_severity`` and
    ``verdict`` are the round's, from ``collegium_severity``; its own
    ``max_severity`` is untouched. A transport failure is not an
    opinion, so an unparsed row neither raises nor lowers the round;
    only when every seat failed does the round read as unparsed.
    """
    if not rows:
        return None
    parsed = [row for row in rows if not row.unparsed]
    if not parsed:
        return rows[0]
    representative = max(
        parsed, key=lambda row: (SEVERITY_RANK[row.max_severity], -row.seat)
    )
    representative._round_severity = collegium_severity(parsed)  # ruff: ignore[private-member-access]
    return representative
```

Change the comment on the `seat` field (line 714) to:

```python
    # Place in the collegium, not seniority: below critical, seat 2 may not
    # lower seat 1; a critical needs both seats (collegium_severity).
```

**Step 4: Run the round tests**

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py`
Expected: PASS, except `test_status_annotations_reduce_the_fresh_round`
(minor + critical -> asserts `"critical"`), which is the SQL twin and is fixed in
Task 2. If anything else fails, stop and read it: it is a consumer that reads
`max_severity` where it should read `effective_severity` (Task 3 list).

**Step 5: Lint and commit**

Run: `uv run prek run --files weblate/trans/models/judge.py weblate/trans/tests/test_judge_round.py`
If ruff rejects the `# ruff: ignore[...]` pragma spelling, use the rule name it
prints for the private-attribute access on `_round_severity`; do not switch to
a bare `# noqa` code.

```sh
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge_round.py
git commit -m "feat(judge): reject only on a unanimous critical"
```

---

### Task 2: the SQL twin (`judge_status_annotations`)

`judge:reject`, `judge:flag`, `judge:pass` search filters and the
`has:judge` annotation read severity in SQL (`weblate/utils/search.py:816-827`
over `judge_active_severity`). They must agree with Task 1.

**Files:**
- Modify: `weblate/trans/models/judge.py:1110-1140` (`judge_status_annotations`)
- Test: `weblate/trans/tests/test_judge_round.py`

**Step 1: Write the failing tests**

Replace `test_status_annotations_reduce_the_fresh_round` (line 75-80) with:

```python
    def test_status_annotations_reduce_the_fresh_round(self) -> None:
        # Below critical: strictest seat, as in Python.
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "minor", seat=1, run_id=run)
        self.make(unit, "major", seat=2, run_id=run)
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "major")

    def test_status_annotations_demote_a_disputed_critical(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "minor", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "major")
        self.assertEqual(active_verdict(unit).effective_severity, "major")

    def test_status_annotations_keep_a_unanimous_critical(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "critical", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "critical")

    def test_status_annotations_keep_a_lone_parsed_critical(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "critical", seat=1, run_id=run)
        self.make(unit, "none", seat=2, run_id=run, unparsed=True)
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "critical")

    def test_search_filters_follow_the_consensus_rule(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        translation = unit.translation
        self.assertEqual(list(translation.unit_set.search("judge:reject")), [])
        self.assertEqual(list(translation.unit_set.search("judge:flag")), [unit])
```

**Step 2: Run to verify they fail**

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py -k "status_annotations or search_filters"`
Expected: `demote_a_disputed_critical` and `search_filters_follow` FAIL with
`'critical' != 'major'` / `[] != [<Unit ...>]`; the rest PASS.

**Step 3: Implement**

In `judge_status_annotations()` (`weblate/trans/models/judge.py`), after
`severity_rank = Case(...)` (around line 1115-1121) and before
`seat_fresh_unparsed`, add:

```python
    # The SQL twin of collegium_severity: the strictest parsed seat, except
    # that a critical some other parsed seat of the same text grades lower
    # reads as major. Same rows as current_parsed_round, minus the critical
    # ones, so Exists() answers "is the critical disputed".
    disputed_critical = Exists(
        JudgeVerdict.objects.filter(
            unit_id=OuterRef(OuterRef("pk")),
            target_storage_hash=MD5(OuterRef(OuterRef("target"))),
            unparsed=False,
        )
        .exclude(_has_newer_sibling(newer_parsed=True))
        .exclude(max_severity=JudgeVerdict.Severity.CRITICAL)
    )
    round_severity = Case(
        When(
            Q(max_severity=JudgeVerdict.Severity.CRITICAL) & disputed_critical,
            then=Value(JudgeVerdict.Severity.MAJOR.value),
        ),
        default=F("max_severity"),
        output_field=CharField(),
    )
```

and change the `judge_active_severity` subquery from
`.values("max_severity")[:1]` to:

```python
        "judge_active_severity": Subquery(
            current_parsed_round.annotate(
                severity_rank=severity_rank, round_severity=round_severity
            )
            .order_by("-severity_rank", "seat")
            .values("round_severity")[:1],
            output_field=CharField(),
        ),
```

`_has_newer_sibling` is the existing helper defined above in the same function
(it uses `OuterRef("timestamp")` / `OuterRef("pk")` relative to the row it is
called on, so it composes here exactly as it does in `current_parsed_round`).
The double `OuterRef(OuterRef("pk"))` is required: `disputed_critical` is
evaluated two subquery levels below the `Unit` queryset. Add `F` to the
`django.db.models` import at the top of the module if it is not already there.

**Step 4: Run the tests**

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py`
Expected: all PASS.

If `OuterRef(OuterRef(...))` raises `ValueError: This queryset contains a
reference to an outer query and may only be used in a subquery`, the
annotation is being evaluated at the wrong level: move `round_severity` into
the `current_parsed_round.annotate(...)` call literally as written above and
do not assign `disputed_critical` to a variable used elsewhere.

**Step 5: Lint and commit**

Run: `uv run prek run --files weblate/trans/models/judge.py weblate/trans/tests/test_judge_round.py`

```sh
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge_round.py
git commit -m "feat(judge): apply the consensus rule in the search annotations"
```

---

### Task 3: gate consumers read `effective_severity`

Every place that turns a collegium read into a state, a check row, a run-report
outcome or a producer summary must use the round severity. Rows written by
`_write_verdict` (`judge_loop.py:332`) keep the seat's own `max_severity`; that
line does not change.

**Files:**
- Modify: `weblate/checks/judge.py:64`
- Modify: `weblate/trans/judge_loop.py:1493,1694,1734,1735`
- Modify: `weblate/trans/autotranslate.py:158,160,162,930,952,954`
- Test: `weblate/checks/tests/test_judge.py`, `weblate/trans/tests/test_judge_loop.py`

**Step 1: Write the failing tests**

In `weblate/trans/tests/test_judge_loop.py`, replace
`test_verdict_takes_the_higher_severity` (line 973) with:

```python
    def test_a_disputed_critical_is_a_flag_not_a_hold(self) -> None:
        # Consensus REJECT: seat 1 major, seat 2 critical -> the string ships
        # flagged; a hold needs both seats.
        unit, verdict, _ = self.run_batch([MAJOR, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict.effective_severity, "major")
        self.assertEqual(verdict.max_severity, "critical")
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_TRANSLATED)
        self.assertIn("judge-flag", unit.all_checks_names)
        self.assertNotIn("judge-reject", unit.all_checks_names)

    def test_a_unanimous_critical_still_holds(self) -> None:
        unit, verdict, _ = self.run_batch([CRITICAL, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_FUZZY)
        self.assertIn("judge-reject", unit.all_checks_names)
```

Import `STATE_FUZZY` and `STATE_TRANSLATED` from `weblate.utils.state` in that
file if they are not already imported (check the top of the file first).

In `weblate/checks/tests/test_judge.py`, add next to the existing
`judge-reject` projection tests (around line 57-86), using that file's own
verdict factory (read the file: it has a helper that writes a `JudgeVerdict`
with `seat=`; reuse it, do not write a second one):

```python
    def test_a_disputed_critical_projects_judge_flag(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make_verdict(unit, "none", seat=1, run_id=run)
        self.make_verdict(unit, "critical", seat=2, run_id=run)
        unit.run_checks()
        self.assertIn("judge-flag", unit.all_checks_names)
        self.assertNotIn("judge-reject", unit.all_checks_names)
```

**Step 2: Run to verify they fail**

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py -k "disputed_critical or unanimous_critical" weblate/checks/tests/test_judge.py -k disputed`
Expected: FAIL. The loop test fails on `unit.state` (`10 != 20`) or on
`judge-reject` still projected; the check test fails on `judge-reject`
present.

**Step 3: Implement**

`weblate/checks/judge.py:64`:

```python
        return verdict is not None and verdict.effective_severity == self.judge_severity
```

`weblate/trans/judge_loop.py`:

- line 1493: `verdicts.initial_severity.setdefault(unit.id, item.verdict.effective_severity)`
- line 1694: `outcome = _DRAIN_SEVERITY_OUTCOMES[verdict.effective_severity]`
- lines 1734-1735:

```python
                    "initial_severity": verdict.effective_severity if verdict else "",
                    "final_severity": verdict.effective_severity if verdict else "",
```

`weblate/trans/autotranslate.py`:

- lines 158, 160, 162 in `_summarize_verdicts`: `verdict.max_severity` ->
  `verdict.effective_severity` (three occurrences).
- line 930: `else severity_outcomes[verdict.effective_severity]`
- lines 952 and 954:

```python
                        "initial_severity": initial_severity.get(
                            unit.id, verdict.effective_severity
                        ),
                        "final_severity": verdict.effective_severity,
```

Verify with grep that nothing else gates on the seat's own severity:

Run: `grep -n "\.max_severity" weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py`
Expected: exactly one line, `judge_loop.py:332:        max_severity=result.max_severity,`
(the write). `weblate/trans/views/judge.py:173-180` reads
`verdict__max_severity` only to pick the headline error text of a row whose
outcome is already stored on `JudgeRunUnit`; leave it.

**Step 4: Run the tests**

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py weblate/checks/tests/test_judge.py weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_views.py`
Expected: PASS. `test_resolution_applies_to_the_collegium_representative`
(`test_judge.py:675`) still passes: minor + critical keeps the critical row as
representative, only its verdict changed, and `resolve_verdict` matches on pk.
If a `test_judge_views.py` test asserts the "Critical held" count or the
`judge:reject` link for a minor/critical fixture, update that fixture to
critical/critical: the view is right, the fixture encoded the old rule.

**Step 5: Lint and commit**

Run: `uv run prek run --files weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py weblate/trans/tests/test_judge_loop.py weblate/checks/tests/test_judge.py`

```sh
git add weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py weblate/trans/tests/test_judge_loop.py weblate/checks/tests/test_judge.py
git commit -m "feat(judge): gate, project and report on the round severity"
```

---

### Task 4: full judge suite, mypy, pylint

**Step 1: Run every judge test module**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_views.py weblate/checks/tests/test_judge.py weblate/trans/tests/test_autotranslate.py`
Expected: PASS. A failure here is a consumer this plan missed; find it with
`grep -rn "max_severity" weblate --include='*.py' | grep -v tests`, decide
whether it is a gate read (switch to `effective_severity`) or a per-row read
(leave), and add a test for it before fixing.

**Step 2: Type check and lint**

Run: `uv run mypy --show-column-numbers weblate/trans/models/judge.py weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py | ./scripts/filter-mypy.sh`
Expected: no new findings. `_round_severity` is set on a model instance
outside `__init__`; if mypy reports it, declare it on the class:

```python
    # Set only by collegium_verdict; never stored (effective_severity).
    _round_severity: str | None
```

directly under the field declarations of `JudgeVerdict`, and keep the
`getattr` default in `effective_severity` for rows that never went through the
collegium.

Run: `uv run pylint weblate/trans/models/judge.py weblate/checks/judge.py`
Expected: no new messages.

**Step 3: Commit if anything changed**

```sh
git add -u
git commit -m "chore(judge): satisfy mypy on the round severity attribute"
```

---

### Task 5: documentation and changelog

**Files:**
- Modify: `docs/admin/checks.rst:165-169`
- Modify: `docs/admin/config.rst:1810-1812`
- Modify: `docs/changes.rst` (top unreleased section, "Improvements")
- Modify: `docs/llm-first/designs/2026-08-13-judge-native-ui-design.md:69`
- Modify: `docs/llm-first/plans/2026-09-04-judge-consensus-reject.md` (this file: status)

**Step 1: `docs/admin/checks.rst`**

Replace the sentence starting "Both configured models (called seats) judge
every selected string independently, and the string's verdict is the strictest
of the two" with:

```rst
Both configured models (called seats) judge every selected string
independently. Below critical the string's verdict is the strictest of the
two: a seat can never lower what the other seat found. A critical is held
only when both seats grade the string critical; a critical from one seat
that the other seat grades lower is shown as a major, so the string ships
with both opinions attached instead of being held on one seat's word. Each
seat's opinion, and any disagreement between them, is shown on the ``judge``
checks card of the string.
```

**Step 2: `docs/admin/config.rst`**

Replace "the string's verdict is the strictest of the two, so a seat can never
lower what the other seat found." with:

```rst
below critical the string's verdict is the strictest of the two, and a
critical hold requires both seats to agree (see :ref:`llm-judge`).
```

**Step 3: `docs/changes.rst`**

Add under `.. rubric:: Improvements` of the unreleased section:

```rst
* An LLM-judge critical now holds a string only when both seats grade it critical; a critical from one seat that the other grades lower ships as a major with both opinions attached, see :ref:`llm-judge`.
```

**Step 4: design diagram**

In `docs/llm-first/designs/2026-08-13-judge-native-ui-design.md:69` change
`M{{Вердикт = max severity}}` to
`M{{Вердикт = max severity; critical только при согласии обоих мест}}`.

**Step 5: this plan's status line**

Change `**Status:** proposed, awaiting approval.` to
`**Status:** implemented on <date>, commits <sha..sha>; not yet deployed.`

**Step 6: Commit**

```sh
git add docs/admin/checks.rst docs/admin/config.rst docs/changes.rst docs/llm-first/designs/2026-08-13-judge-native-ui-design.md docs/llm-first/plans/2026-09-04-judge-consensus-reject.md
git commit -m "docs(judge): document the consensus critical hold"
git push
```

---

### Task 6: verification on the dev instance (no production access)

**Step 1: restart the dev stack so Granian picks up the model change**

`weblate/` edits are live under Granian reload; Celery workers are not. Run
`./rundev.sh` only if a judge run is going to be launched from the UI (it
recreates containers). Reading the existing dev verdicts needs no restart.

**Step 2: check an existing dev disagreement**

In `./rundev.sh shell` (dev only, never production):

```python
from weblate.trans.models import Unit
from weblate.trans.models.judge import active_round, active_verdict
seen = 0
for unit in Unit.objects.filter(judge_verdicts__isnull=False).distinct()[:500]:
    rows = active_round(unit)
    sev = {row.seat: row.max_severity for row in rows}
    if "critical" in sev.values() and len(set(sev.values())) > 1:
        v = active_verdict(unit)
        print(unit.pk, sev, v.max_severity, v.effective_severity, v.verdict)
        seen += 1
    if seen >= 10:
        break
```

Expected: every printed line ends with `critical major flag`.

**Step 3: run `judge:reject` and `judge:flag` searches on that translation in the UI**

Open the translation, filter `judge:reject`: the units printed above must not
be listed. Filter `judge:flag`: they must be.

---

## What this plan does not settle

- **Production rollout.** Deploying changes which strings are held. It is an
  approval of its own (`deploy/vps.sh`). Existing production rows need no
  migration: the rule is applied at read time, so the 20 seat-2-only holds on
  `need-for-greed/ui/es` read as `flag` the moment the code is live; their
  `STATE_FUZZY` from the earlier projection stays until the next run projects
  them again or a producer resolves them.
- **Recall on a true critical one seat under-grades.** Measured on the sealed
  zh corpus: the one revised true critical lands on `flag` in 1-2 of 5 runs.
  It is still visible with evidence; it is not held. If the product wants that
  case held, the answer is a second-opinion re-check on disputed criticals,
  not a return to one seat's word.
- **Seat 2's own calibration.** `atlas/qwen3.8-max` without reasoning is the
  source of every register/punctuation/addition critical in run `36a5be4a`.
  Whether reasoning-off caused that is unmeasured on production; a read-only
  paired re-run on the 29 seat-2 criticals is the cheap test.
