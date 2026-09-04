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
row's own `max_severity`. Consequence of the transient: any consumer that
**re-fetches the row from the database** loses the round severity and silently
falls back to the seat's own. Exactly one such consumer exists -
`resolve_verdict`, which re-reads the representative under
`select_for_update()` - and Task 3 carries the stamp across that re-read. The
SQL twin, `judge_status_annotations()`, gets the same rule so the
`judge:reject` / `judge:flag` search filters, the `check:judge-*` projections
and the judge statistics agree with the Python read. No migration: the
`verdict` property was written to be reopened by measurement R3 without one
(`JudgeVerdict` docstring, models/judge.py:649-656).

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

A "round" is the per-seat assembly the readers already use
(`_seat_round_rows`: each seat's freshest matching row), not one
`(run_id, attempt)` pair. So a seat's later recovered pass - a deferral retry
landing in its own run - does dispute an earlier critical from the other seat.
That is deliberate: it is a real opinion about the current text, and two
existing fixtures encode exactly this shape (Task 2 and Task 3 update them).

## What follows from the rule, and where it is pinned

These are consequences, not extra scope; each has an owning task and a test:

1. **The state gate** - `state_for_verdict()` reads the derived verdict, so a
   disputed critical projects `STATE_TRANSLATED`. It is written in
   `AutoTranslate.process_judge` (autotranslate.py:858-873) and
   `_finalize_drain_run` (judge_loop.py:1695-1710) - **never** in
   `run_judge_batch`, so no loop-level test can assert it (Task 3).
2. **Resolution** - a disputed critical offers and applies the *major*
   transitions: escalation sends it to the needs-checking queue instead of
   forcing a fuzzy hold (Task 3's `resolve_verdict` fix, Task 5's doc line).
3. **Candidates** - `FLAG` is in the default candidate severities, so a
   disputed critical still gets a repair candidate in a normal run. A producer
   one-unit re-check narrows candidates to critical only and therefore stops
   auto-storing one; see "What this plan does not settle".

## Environment

Run tests inside the dev container so no host database setup is needed:

```sh
./rundev.sh test weblate/trans/tests/test_judge_round.py
```

Run lint on the host:

```sh
uv run prek run --files weblate/trans/models/judge.py weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py
```

`./rundev.sh` with no arguments rebuilds and recreates the shared `dev-docker`
stack. Per `AGENTS.md` that is a deployment-class action: never run it as part
of these tasks (Task 6 Step 1).

Commit after every task. Commit messages use Conventional Commits and end
with the session attribution footer configured for this repository.

---

### Task 1: `collegium_severity` and `effective_severity` (Python read)

**Files:**

- Modify: `weblate/trans/models/judge.py:803-813` (the `verdict` property)
- Modify: `weblate/trans/models/judge.py:984-997` (`collegium_verdict`)
- Modify: `weblate/trans/models/judge.py:714` (comment on `seat`)
- Test: `weblate/trans/tests/test_judge_round.py`

**Step 1: Write the failing tests**

Add to `weblate/trans/tests/test_judge_round.py`, right after
`test_no_seat_may_lower_the_other` (line 213-223), and add
`collegium_severity` to the `weblate.trans.models.judge` import block at the top
of the file (after `active_verdict`):

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
        # The transient stamp is the whole footgun of this design: a row
        # re-fetched from the database stands only for itself. Pin both
        # halves on the same disputed round, so a regression in either
        # direction fails here (resolve_verdict depends on this, Task 3).
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "minor", seat=1, run_id=run)
        strict = self.make(unit, "critical", seat=2, run_id=run)
        round_read = active_verdict(unit)
        assert round_read is not None
        self.assertEqual(round_read.verdict, JudgeVerdict.Verdict.FLAG)
        row = JudgeVerdict.objects.get(pk=strict.pk)
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
(lines 803-813) with:

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

Replace `collegium_verdict` (lines 984-997) with:

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

    Measured: docs/operations/audits/2026-09-04-need-for-greed-ui-es-judge-calibration.md.
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
Expected: **all PASS.** Two tests still assert the old rule at this point and
still pass, because both read values Task 1 does not touch:
`test_status_annotations_reduce_the_fresh_round` (line 75) reads the SQL
annotation, and
`test_one_seat_retry_in_a_new_run_cannot_hide_the_other_seats_critical`
(line 375) asserts the representative row's own `max_severity` plus that same
annotation. Task 2 owns both. If anything else fails, stop and read it: it is a
consumer that reads `max_severity` where it should read `effective_severity`
(Task 3 list).

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

`judge:reject`, `judge:flag`, `judge:pass` search filters, the `has:judge`
annotation and the judge statistics read severity in SQL
(`weblate/utils/search.py:816-828` and `weblate/utils/stats.py:929-977`, both
over `judge_active_severity`). They must agree with Task 1.

**Files:**

- Modify: `weblate/trans/models/judge.py:1086-1145` (`judge_status_annotations`)
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

Then fix the fixture that encodes the old rule for the annotation. In
`test_one_seat_retry_in_a_new_run_cannot_hide_the_other_seats_critical`
(line 375-405) the round is seat 1 `critical` plus seat 2's later recovered
`none` - a disputed critical. The row-level assertions stay (the critical row
is still the representative, which is what that test is about); only the
annotation changes. Replace its last four lines (402-405) with:

```python
        active = active_verdict(unit)
        assert active is not None
        # The retry cannot hide seat 1's critical: it is still the
        # representative row and its evidence is intact. It can dispute
        # it, though - a fresh parsed pass from the other seat makes the
        # round a major (consensus REJECT), in Python and in SQL alike.
        self.assertEqual(active.max_severity, "critical")
        self.assertEqual(active.effective_severity, "major")
        self.assertEqual(self.judge_status(unit)["judge_active_severity"], "major")
```

**Step 2: Run to verify they fail**

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py -k "status_annotations or search_filters or one_seat_retry"`
Expected: three FAIL - `demote_a_disputed_critical` and
`one_seat_retry_in_a_new_run_cannot_hide_the_other_seats_critical` with
`'critical' != 'major'`, `search_filters_follow` with `[] != [<Unit ...>]`.
`keep_a_unanimous_critical`, `keep_a_lone_parsed_critical` and the rewritten
`reduce_the_fresh_round` PASS already (the old SQL agrees on those).

**Step 3: Implement**

In `judge_status_annotations()` (`weblate/trans/models/judge.py`), after
`severity_rank = Case(...)` (lines 1112-1118) and before
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

`_has_newer_sibling` is the existing helper defined above in the same function.
Inside `disputed_critical` its single-level `OuterRef("unit_id")` /
`OuterRef("seat")` / `OuterRef("timestamp")` / `OuterRef("pk")` resolve against
the disputing row, exactly as they do inside `current_parsed_round`, so the
Exists sees each seat's freshest parsed row and nothing else. The double
`OuterRef(OuterRef("pk"))` is required because `disputed_critical` is evaluated
one subquery level below `current_parsed_round`, whose own `OuterRef("pk")`
already points at `Unit`. `judge_active_resolution` keeps its existing
annotation: it orders by the row's own `severity_rank`, so it still returns the
representative's resolution. Add `F` to the `django.db.models` import at the
top of the module if it is not already there.

**Step 4: Run the tests**

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py weblate/utils/tests/test_search.py -k "judge or preset"`
Expected: all PASS. The single-seat search fixtures are unaffected (a lone
parsed critical still rejects).

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

### Task 3: gate consumers read the round severity

Every place that turns a collegium read into a state, a check row, a run-report
outcome, a producer summary or a resolution must use the round severity. Rows
written by `_write_verdict` (`judge_loop.py:332`) keep the seat's own
`max_severity`; that line does not change.

Note which layer does what, because it decides where each test can live:
`run_judge_batch` projects **check rows** (`_prepare_round_unit` calls
`run_checks`) but never unit **state**; state is written only by
`AutoTranslate.process_judge` (autotranslate.py:858-873), `_finalize_drain_run`
(judge_loop.py:1695-1710), `_apply_repair` and candidate acceptance.

**Files:**

- Modify: `weblate/checks/judge.py:64`
- Modify: `weblate/trans/judge_loop.py:1493,1694,1734,1735`
- Modify: `weblate/trans/autotranslate.py:158,160,162,930,952,954`
- Modify: `weblate/trans/models/judge.py:1249-1256` (`resolve_verdict`)
- Test: `weblate/checks/tests/test_judge.py`,
  `weblate/trans/tests/test_judge_loop.py`,
  `weblate/trans/tests/test_judge_autotranslate.py`,
  `weblate/trans/tests/test_judge_deferrals.py`,
  `weblate/trans/tests/test_judge.py`

**Step 1: Write the failing tests**

*(a) The round's own verdict, in the loop.* In
`weblate/trans/tests/test_judge_loop.py`, replace
`test_verdict_takes_the_higher_severity` (line 973-975) with:

```python
    def test_a_disputed_critical_is_a_flag(self) -> None:
        # Consensus REJECT: seat 1 major, seat 2 critical -> the round is a
        # flag and projects judge-flag. State is not asserted here:
        # run_judge_batch never writes it (see the note above); the
        # ship-or-hold assertions live in test_judge_autotranslate.py and
        # test_judge_deferrals.py.
        _unit, verdict, _client = self.run_batch([MAJOR, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict.effective_severity, "major")
        self.assertEqual(verdict.max_severity, "critical")
        self.assertEqual(verdict.seat, 2)
        fresh = self.get_unit()
        self.assertIn("judge-flag", fresh.all_checks_names)
        self.assertNotIn("judge-reject", fresh.all_checks_names)

    def test_a_unanimous_critical_still_rejects(self) -> None:
        _unit, verdict, _client = self.run_batch([CRITICAL, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)
        self.assertEqual(verdict.effective_severity, "critical")
        self.assertIn("judge-reject", self.get_unit().all_checks_names)
```

*(b) The check projection.* In `weblate/checks/tests/test_judge.py`, add to
`JudgeCheckTest` next to `test_reject_verdict_makes_run_checks_create_the_row`
(line 52), reusing that class's own `make` helper (line 33, defaults
`seat=1`):

```python
    def test_a_disputed_critical_projects_judge_flag(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-flag", unit.all_checks_names)
        self.assertNotIn("judge-reject", unit.all_checks_names)
```

Add `import uuid` to that file if it is not already imported.

*(c) Ship or hold, through the real producer path.* In
`weblate/trans/tests/test_judge_autotranslate.py`, add next to
`test_major_candidate_passes_through_the_operator_path` (line 320) - that test
is the pattern to copy, because the `perform` helper (line 60) writes
single-seat rounds and cannot express a disputed one. Add `current_verdict` to
the `weblate.trans.models.judge` imports:

```python
    def test_a_disputed_critical_ships_flagged(self) -> None:
        # The state gate runs in process_judge: one seat's critical against
        # the other seat's lower grade must leave the string shipping.
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        unit = self.get_unit()
        unit.translate(self.user, ["existing translation"], STATE_TRANSLATED)
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="",
            mode="judge",
            overwrite_existing=True,
            unit_ids=[unit.id],
        )
        # Which seat returns which grade does not matter to the rule.
        results = iter(
            [[JudgeResult("major", "flag", [], "")], [JudgeResult("critical", "reject", [], "")]]
        )

        def request(requests, *, on_batch, **kwargs):
            batch_results = next(results)
            on_batch(requests, batch_results)
            return batch_results

        with (
            mock.patch.object(auto, "process_mt"),
            mock.patch(
                "weblate.trans.judge_loop.request_verdicts",
                mock.Mock(side_effect=request),
            ),
            mock.patch(
                "weblate.trans.judge_loop.repair_targets",
                return_value={unit.id: ["repaired translation"]},
            ),
        ):
            auto.process_judge(engines=[], threshold=80)
        stored = self.get_unit()
        self.assertEqual(stored.state, STATE_TRANSLATED)
        self.assertNotIn(stored.state, FUZZY_STATES)
        self.assertEqual(auto.judge_summary.major_not_fixed, 1)
        self.assertEqual(auto.judge_summary.critical_held, 0)
        round_read = current_verdict(stored)
        assert round_read is not None
        self.assertEqual(round_read.max_severity, "critical")
        self.assertEqual(round_read.verdict, JudgeVerdict.Verdict.FLAG)
```

The unanimous counterpart is already covered by
`test_reject_lands_on_a_state_that_does_not_ship` (line 106): a lone parsed
critical still rejects, so it must stay green.

*(d) Resolution.* In `weblate/trans/tests/test_judge.py`
(`JudgeResolutionTest`), add next to
`test_resolution_applies_to_the_collegium_representative` (line 674):

```python
    def test_escalating_a_disputed_critical_queues_it_as_a_major(self) -> None:
        # The card offers the major transitions for a disputed critical
        # (views/edit.py reads the collegium instance); the resolver must
        # agree, so escalation sends the string to the needs-checking
        # queue instead of forcing a fuzzy hold.
        self.enable_review()
        unit = self.get_unit()
        run = self.make_verdict(unit, "minor", seat=1).run_id
        representative = self.make_verdict(unit, "critical", seat=2, run_id=run)
        resolve_verdict(
            unit=unit,
            expected_verdict_id=representative.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="one seat only",
        )
        representative.refresh_from_db()
        self.assertEqual(representative.resolution, JudgeVerdict.Resolution.ESCALATED)
        self.assertEqual(self.get_unit().state, STATE_NEEDS_CHECKING)
```

Import `STATE_NEEDS_CHECKING` from `weblate.utils.state` in that file if it is
not already imported.

*(e) The drain finalizer.* In
`weblate/trans/tests/test_judge_deferrals.py`, first repair the existing
fixture: `test_drain_run_projects_a_recovered_critical_hold_without_mutating_target`
(line 451) pre-seeds seat 2 with `JudgeResult("none", "pass", [], "")`
(line 462) and then recovers a seat 1 `critical` - a disputed critical under
this rule, which is not what that test is about. Change the pre-seed to a
critical so the recovered round is unanimous and the hold it asserts is real:

```python
        result=JudgeResult(
            "critical",
            "reject",
            [{"span": "x", "category": "terminology", "severity": "critical"}],
            "",
        ),
```

Then add the disputed sibling right after it (add `STATE_TRANSLATED` to the
`weblate.utils.state` import of that file):

```python
    def test_drain_run_projects_a_disputed_critical_as_a_major(self) -> None:
        # The other seat already parsed a pass for this text, so the
        # recovered critical is disputed: the run reports major and the
        # string is not held.
        unit = self.change_unit("Ahoj svete!")
        before_target = unit.get_target_plurals()
        _write_verdict(
            unit,
            build_request(unit),
            seat=2,
            attempt=0,
            run_id=uuid.uuid4(),
            result=JudgeResult("none", "pass", [], ""),
            profile=resolve_judge_seat_profile(2),
            project_context="",
        )
        self.defer(unit)
        JudgeDeferral.objects.filter(unit=unit).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        critical = JudgeResult(
            "critical",
            "reject",
            [{"span": "x", "category": "terminology", "severity": "critical"}],
            "",
        )
        with mock.patch(
            "weblate.trans.judge_loop.request_verdicts",
            mock.Mock(side_effect=mock_request_verdicts([critical])),
        ):
            processed = drain_judge_deferrals()

        self.assertEqual(processed, 1)
        run_unit = JudgeRunUnit.objects.get(
            run=JudgeRun.objects.get(), unit_id_snapshot=unit.pk
        )
        self.assertEqual(run_unit.outcome, JudgeRunUnit.Outcome.MAJOR)
        self.assertEqual(run_unit.final_severity, "major")
        unit.refresh_from_db()
        self.assertEqual(unit.state, STATE_TRANSLATED)
        self.assertEqual(unit.get_target_plurals(), before_target)
```

**Step 2: Run to verify they fail**

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py weblate/checks/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_deferrals.py weblate/trans/tests/test_judge.py -k "disputed"`
Expected: FAIL, each on the old rule -

- loop: `reject != flag` (and `judge-reject` still projected);
- check: `judge-reject` present;
- autotranslate: `state` is fuzzy and `critical_held` is 1;
- deferrals: `outcome` is `critical` and the unit is held;
- resolution: state is `STATE_FUZZY` (10), not needs-checking (12).

`test_a_unanimous_critical_still_rejects` and the repaired recovered-hold
fixture pass before and after: they are the guards that the rule did not
overreach.

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

`weblate/trans/models/judge.py`, `resolve_verdict` (lines 1249-1256): the
locked re-read replaces the collegium instance with a plain row, which drops
the transient round severity, so `verdict` would fall back to this seat's own
`critical` while the card offered the major transitions for the same row.
Carry the stamp across the re-read (the pk was verified on the line above, so
it is the same round):

```python
        if representative.pk != expected_verdict_id:
            msg = "stale"
            raise JudgeResolutionError(msg, stale_message)
        # The locked re-read refreshes this row's own fields for the race
        # window (a concurrent resolution); the round severity is not one
        # of them, so it travels from the collegium read above.
        round_severity = getattr(representative, "_round_severity", None)
        representative = JudgeVerdict.objects.select_for_update().get(
            pk=representative.pk
        )
        representative._round_severity = round_severity  # ruff: ignore[private-member-access]
        old_resolution = representative.resolution
        verdict = representative.verdict
```

Verify with grep that nothing else gates on the seat's own severity:

Run: `grep -n "\.max_severity" weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py`
Expected: exactly one line, `judge_loop.py:332:        max_severity=result.max_severity,`
(the write). `weblate/trans/views/judge.py:173-180` reads
`verdict__max_severity` only to pick the headline error text of a row whose
outcome is already stored on `JudgeRunUnit`; leave it.

That grep cannot see the second class of consumer - code that reads the
derived `.verdict`. Audit it once, by hand, and record the result here:
`judge_backfill_candidates.py:124` and
`judge_release_advisory_holds.py:150-153` read `.verdict` on a **collegium
instance** (`current_verdict` / `collegium_verdict`) and therefore follow the
round automatically; `views/edit.py:2021` reads it on a row fetched by pk, so a
disputed critical reads there as its own `reject` - harmless, because that gate
accepts `reject` and `flag` alike. `resolve_verdict` was the only one that both
re-fetches and branches on the value; it is fixed above.

**Step 4: Run the tests**

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py weblate/checks/tests/test_judge.py weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_deferrals.py`
Expected: PASS. `test_resolution_applies_to_the_collegium_representative`
(`test_judge.py:674`) still passes: minor + critical keeps the critical row as
representative, only its verdict changed, and `resolve_verdict` matches on pk.
If a `test_judge_views.py` test asserts the "Critical held" count or the
`judge:reject` link for a minor/critical fixture, update that fixture to
critical/critical: the view is right, the fixture encoded the old rule.
`test_card_shows_seat_disagreement` (line 441) is expected to keep passing on a
critical/none round - its card now renders the flag branch, which still shows
both seats' evidence.

**Step 5: Lint and commit**

Run: `uv run prek run --files weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py weblate/trans/models/judge.py weblate/trans/tests/test_judge_loop.py weblate/checks/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_deferrals.py weblate/trans/tests/test_judge.py`

```sh
git add weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py weblate/trans/models/judge.py weblate/trans/tests/test_judge_loop.py weblate/checks/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_deferrals.py weblate/trans/tests/test_judge.py
git commit -m "feat(judge): gate, project and resolve on the round severity"
```

---

### Task 4: full judge suite, mypy, pylint

**Step 1: Run every judge test module**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_deferrals.py weblate/trans/tests/test_judge_persistence.py weblate/trans/tests/test_judge_migration.py weblate/trans/tests/test_judge_form.py weblate/checks/tests/test_judge.py weblate/trans/tests/test_autotranslate.py weblate/utils/tests/test_search.py weblate/utils/tests/test_stats.py`
Expected: PASS. A failure here is a consumer this plan missed; find it with
`grep -rn "max_severity" weblate --include='*.py' | grep -v tests` **and** the
`.verdict` audit from Task 3 Step 3, decide whether it is a gate read (switch
to `effective_severity`, or carry the stamp if it re-fetches) or a per-row read
(leave), and add a test for it before fixing.

Run also: `./rundev.sh test weblate/trans/tests/test_commands.py weblate/trans/tests/test_tasks.py -k Judge`
Expected: PASS - `judge_backfill_candidates` and
`judge_release_advisory_holds` follow the round through `.verdict` with no
edit, and their fixtures are single-seat.

**Step 2: Type check and lint**

Run: `uv run mypy --show-column-numbers weblate/trans/models/judge.py weblate/checks/judge.py weblate/trans/judge_loop.py weblate/trans/autotranslate.py | ./scripts/filter-mypy.sh`
Expected: no new findings. `_round_severity` is set on a model instance
outside `__init__` (in `collegium_verdict` and in `resolve_verdict`); if mypy
reports it, declare it on the class:

```python
    # Set only by collegium_verdict, and carried across resolve_verdict's
    # locked re-read; never stored (effective_severity).
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
- Modify: `docs/admin/config.rst:1811-1813`
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
with both opinions attached instead of being held on one seat's word. Such
a disputed critical is also reviewed like a major: sending it back puts it
in the needs-checking queue rather than holding it. Each seat's opinion,
and any disagreement between them, is shown on the ``judge`` checks card of
the string.
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

**Step 1: no restart, and none may be taken here**

Steps 2-4 need no restart: `./rundev.sh shell` starts a fresh process and the
web process reloads `/app/src/weblate` under Granian, so both read the new
code. Celery workers do **not** reload, so launching a judge run from the UI
would need a full `./rundev.sh`, which recreates the shared `dev-docker`
stack - a deployment-class action under `AGENTS.md`. Do not run it as part of
this task; if a live run is wanted, request that approval separately.

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

**Step 4: record the annotation's cost**

`disputed_critical` adds a correlated subquery level under every `judge:*`
filter and under `Stats.calculate_judge` (`weblate/utils/stats.py:929-977`,
which annotates a whole translation's unit set). In the same shell, on the
largest dev translation:

```python
import time
from weblate.trans.models import Translation
from weblate.trans.models.judge import judge_status_annotations
t = Translation.objects.order_by("-stats__all")[0]
start = time.monotonic()
list(t.unit_set.annotate(**judge_status_annotations()).values_list("judge_active_severity", flat=True))
print(t, t.unit_set.count(), round(time.monotonic() - start, 3))
```

Record the number in the commit message of Task 5 (or in a dated
`docs/llm-first/measurements/` note if it looks bad). A regression worth
acting on is a judge-filtered listing that stops answering in interactive
time; the fix would be an index on `(unit, target_storage_hash, seat)`, not a
stored severity column (see below).

---

## What this plan does not settle

- **Production rollout.** Deploying changes which strings are held. It is an
  approval of its own (`deploy/vps.sh`). Existing production rows need no
  migration: the rule is applied at read time, so the 20 seat-2-only holds on
  `need-for-greed/ui/es` read as `flag` the moment the code is live; their
  `STATE_FUZZY` from the earlier projection stays until the next run projects
  them again or a producer resolves them. The stored `judge-reject` check rows
  likewise survive until something re-runs checks for those units, so the
  filter and the check row disagree for exactly that window - worth naming in
  the rollout request.
- **Recall on a true critical one seat under-grades.** Measured on the sealed
  zh corpus: the one revised true critical lands on `flag` in 1-2 of 5 runs.
  It is still visible with evidence; it is not held. If the product wants that
  case held, the answer is a second-opinion re-check on disputed criticals,
  not a return to one seat's word.
- **Candidates on a producer re-check of a disputed critical.** A one-unit
  re-check dispatches `candidate_severities=(CRITICAL,)`
  (`queue_judge_recheck`, judge_loop.py:2076). A disputed critical now reads
  as `flag`, so that run projects the verdict and stores no candidate
  (`no-candidate`), exactly as it already does for an ordinary major
  (`test_recheck_major_projects_directly_without_a_candidate`). The card's
  manual generate button still accepts it. Whether the re-check should widen
  its candidate set is a paid-call policy decision, not a reader bug.
- **A stored round severity.** Everything here is read-time on purpose (no
  migration, R3 stays reopenable). If the SQL twin's cost ever forces
  materialization, that is a separate design with a backfill and a writer
  invariant, and it would have to answer what happens to a stored value when a
  seat's late retry changes the round.
- **Seat 2's own calibration.** `atlas/qwen3.8-max` without reasoning is the
  source of every register/punctuation/addition critical in run `36a5be4a`.
  Whether reasoning-off caused that is unmeasured on production; a read-only
  paired re-run on the 29 seat-2 criticals is the cheap test.
