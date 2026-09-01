# Refused judge requests fail fast: implementation proof

**Date:** 2026-09-01. **Status:** implementation proven by test suite; dev-container arms pending deployment approval.

## Purpose

Plan `docs/llm-first/plans/2026-09-01-03-judge-zero-unparsed.md` (Tasks 1-5)
removes the mechanism by which a refused judge request (HTTP 400/401) became a
fake `unparsed` verdict, a paid retry, or a deferral. This document records
what was proven, at which commit, and what remains unproven.

No running instance was touched: the proof below is the host-side test suite
against the worktree branch `feat/judge-zero-unparsed`. The plan's three-arm
dev-container proof (Task 5) recreates the shared `dev-docker` stack and needs
separate explicit approval (AGENTS.md deployment rule); it is **pending**, not
done.

## Change under test

| Piece | Location |
|---|---|
| New failure kind `http-request-invalid` in the closed taxonomy | `weblate/trans/judge.py:58` |
| Refused statuses map to it; 413/unclassified 4xx stay `http-other` | `_failure_for_http` (`weblate/trans/judge.py:1106-1120`) |
| Fail-fast raise after the attempt row persists, before any retry/verdict/deferral | `weblate/trans/judge.py:1523-1530` |
| Stored on the attempt ledger | `JudgeRequestAttempt.FailureKind.HTTP_REQUEST_INVALID` (`weblate/trans/models/judge.py:246`), migration `0114_judge_request_invalid_kind` |
| Not an availability failure: the endpoint circuit stays closed | absent from `_AVAILABILITY_FAILURE_KINDS` (`weblate/trans/judge_loop.py:632-637`) |
| Explicit `refused` producer-report outcome | `JudgeRunUnit.Outcome.REFUSED` (`weblate/trans/models/judge.py:438`), migration `0115_judge_run_unit_refused_outcome` |
| Guarded historical cleanup (dry-run default, `--expected-count` + `--confirm`) | `weblate/trans/management/commands/judge_close_refused_verdicts.py` |

## Result 1: a refusal aborts with one attempt and no verdict

`weblate/trans/tests/test_judge_client.py::JudgeRefusedRequestTest` (4 tests):

- `test_refused_request_raises_before_any_retry` - a 400 raises `JudgeError`
  after exactly one HTTP call; the retry loop never re-sends it.
- `test_refused_attempt_row_survives_the_abort` - the ledger row keeps
  `failure_kind="http-request-invalid"`, `parsed=False`,
  `transport_succeeded=False`, `http_status=400`; the error message names the
  status and no key, base URL, or model.
- `test_size_dependent_413_is_not_a_refusal` - 413 keeps the old
  retry/adaptive-halving path (control against over-classification).
- `test_refusal_without_persistence_still_aborts` - the abort does not depend
  on the ledger write succeeding.

## Result 2: the seat loop writes nothing on a refusal

`weblate/trans/tests/test_judge_loop.py::JudgeRefusedSeatTest` (2 tests, real
two-seat Celery-path loop, per-model HTTP doubles):

- refusal on seat 1 raises `JudgeError`; seat 1 writes zero verdicts, zero
  deferrals, and the repair engine is never invoked;
- the refused seat made exactly one HTTP call (no paid retry, no fallback resend);
- a valid peer-seat verdict written before the abort does not become complete
  evidence: `has_complete_current_evidence` stays false for that unit.

## Result 3: the producer report shows a failure, not a fake verdict

- `test_judge_autotranslate.py::JudgeAutoTranslateTest::test_refused_request_fails_the_run_without_a_fake_verdict`
  - the batch run ends `failed` with the refusal in `run.failure`, zero
    unparsed verdicts exist, unit state is unchanged.
- `test_judge_views.py::JudgeRunReportViewTest::test_refused_run_shows_the_refusal_and_no_unparsed_row`
  - the run report renders the refusal message and `stats["unparsed"] == 0`.

## Result 4: the cleanup command deletes exactly the false verdicts

`weblate/trans/tests/test_commands.py::JudgeCloseRefusedVerdictsCommandTest`
(4 tests):

- dry-run prints `total: N` grouped by HTTP status, model, profile fingerprint
  and changes nothing;
- `--confirm` deletes the candidate verdicts, reclassifies each linked
  `JudgeRunUnit(outcome="unparsed")` to `refused` before the `SET_NULL` FK can
  lose the link, and keeps the attempt ledger;
- `--confirm` aborts (`CommandError`) when the live count no longer matches
  `--expected-count`;
- exclusions survive: 413, 500, deadline (NULL status), transport (NULL
  status), no-attempt, and `unparsed=False` rows are never touched. Selection
  is `unparsed=True AND request_attempt.http_status IN (400, 401)` only - the
  new kind is not retroactive and is never a selector.

## Verification

Host-side suite (branch `feat/judge-zero-unparsed`, commits `0f83dc5`,
`a867db8`, `6216533`, `0f488ad`):

```text
pytest weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py \
       weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_views.py \
       -q -k "refused or failure_for_http or declared_kind or availability"  -> green
pytest weblate/trans/tests/test_commands.py -q -k refused  -> 4 passed
```

## Pending (needs deployment approval)

Task 5's three dev-container arms on `dev-docker` (read-only,
`writable_ids=set()`): refused arm (`JUDGE_MODEL_SEAT_1` pointed at an
unserved model, expected: one `http-request-invalid` attempt, failed run, no
verdict, no deferral), size arm (413 unchanged or test-double-asserted),
healthy control (zero refusals, previously measured call count for the fixed
scope). Task 4's production dry-run count is likewise pending; it, not the
stale "101" figure, is the cleanup authority.
