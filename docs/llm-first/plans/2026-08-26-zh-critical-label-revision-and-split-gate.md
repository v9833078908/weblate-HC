# Revising the zh critical labels and splitting the judge recall gate

**Date:** 2026-08-26. **Status:** completed. All four tasks landed; the split
gate did not produce a Stage 4 candidate.
**Follows:** `docs/llm-first/plans/2026-08-26-judge-seat-pair-search.md`, which
stopped at Stage 3 with no surviving candidate.
**Rule:** R3 - changing the prompt or the model invalidates a measurement
(`docs/llm-first/vision/llm-first-product-architecture.md:674`). This plan
changes neither. It changes how a stored verdict is *scored*, so every affected
number is recomputed from the stored Stage 3 verdicts rather than re-requested.

## Why

Stage 3 disqualified `qwen3.8-max` on `missed_crit = 6/7` against the sealed
zh ground truth. Re-deriving those seven labels from the source/target pairs
shows the number does not mean what the gate assumes:

- `qwen3.8-max` produced a correct defect description for five of the seven,
  including the placeholder-role inversion in `24221` and the country-count
  binding in `24207`. It differed from the sealed rubric on *severity*, not on
  whether a defect exists.
- Three labels (`24180`, `24181`, `24182`) mark a connector-less noun pile whose
  meaning is still recoverable. That is a major fluency defect.
- `24208` marks a target that faithfully mirrors a defect present in the source.
- `24130` depends on whether `{[PARAM1]}` is a price or a turn count, which no
  artifact in the repository establishes; the sealed record already carries
  `disputed: true`.

A single `missed_crit` counter collapses two different failures - not finding a
defect, and finding it but grading it lower - into one disqualifying number.
Only the first is a recall failure.

## Scope

In scope:

1. A machine-readable revision overlay for the seven critical labels.
2. A written review recording the per-unit reasoning.
3. A split gate in the existing scorer.
4. Recomputing the Stage 3 outcome for every candidate under the split gate.

Out of scope, each needing separate approval:

- Editing `analysis/data/st2-zh-groundtruth.json`. It stays sealed.
- Any new paid judge request. This plan re-scores stored verdicts only.
- Changing the judge prompt, schema, batch size or seat settings.
- Reviewing the en->fr labels; they are construction-generated, not rubric-assigned.
- Any production write or deployment.

## Tasks

### Task 1: revision overlay and review

Add `analysis/data/st2-zh-critical-revision.json`: for each of the seven ids,
the sealed severity, the revised severity, an `in_gate` flag, and the reason.
`in_gate: false` marks a unit that cannot disqualify a candidate - either the
defect is unprovable from text (`24130`) or it lives in the source (`24208`).

Record the same reasoning in prose in
`docs/llm-first/reviews/2026-08-26-zh-critical-label-revision.md`.

Verification: the overlay parses, covers exactly the seven sealed criticals, and
every entry carries a reason.

### Task 2: split the gate in the scorer

Extend `analysis/probes/st2-zh-score.py` with an optional `--revision` argument.
When supplied, it reports two independent numbers over the in-gate units:

- `missed_defect` - the judge label ranks below `major` on a confirmed defect.
  This is a recall failure and disqualifies.
- `severity_miscal` - the judge label reaches `major` or higher but is below the
  revised severity. This is a calibration gap and does not disqualify.

`missed_crit` stays and keeps its current definition, so previously published
arm numbers remain reproducible. Without `--revision` the output is unchanged.

Verification: running the scorer with no `--revision` on an existing arm
reproduces its current line byte-for-byte; running it with the overlay adds the
two new numbers.

### Task 3: persist and re-score the Stage 3 verdicts

The Stage 3 verdicts currently exist only in the measurement session. Write them
to `analysis/data/st2-stage3-verdicts.json` so the re-score is reproducible
without paying for the calls again, then report every candidate under both the
old and the split gate.

Verification: the persisted file reproduces the published Stage 3 table, and the
split-gate numbers are derived from it by the scorer rather than by hand.

### Task 4: record the outcome

Amend `docs/llm-first/measurements/2026-08-26-judge-seat-pair-search-stage3.md`
with a section reporting the split-gate result. Do not rewrite the original
table: the transport numbers stand, only their interpretation changes. State
plainly whether any candidate becomes Stage 4 eligible.

If a candidate does become eligible, that does **not** authorise Stage 4. The
per-model repeat runs cost real requests and need their own approval.

## Stop conditions

1. The revision cannot be defended from text for some unit: leave it `in_gate:
   false` with the reason, do not guess a severity.
2. The split gate still leaves every candidate with `missed_defect > 0`: report
   that Stage 3 stands as stopped, and change nothing else.
3. Reproducing an existing arm line fails after the scorer change: revert the
   scorer, the regression is in the change and not in the data.
