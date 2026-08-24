# Judge run progress reporting (superseded)

**Date:** 2026-08-24. **Status:** superseded and absorbed into
`docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md`, Task 6.
Do not implement this document independently.

## Reason for consolidation

The original proposal correctly identified that `process_mt()` consumes an
entire automatic-translation progress slice while the subsequent LLM judge
does not report progress. Its callback and range split, however, touch the
same execution seam as Plan 02's capped judge scope:

```text
request_verdicts -> run_judge_batch -> AutoTranslate.process_judge
```

Plan 02 selects a stable, permission-filtered and cap-limited execution scope
`K`. A separate progress implementation based on the uncapped query result
`N` could show a denominator that does not describe the units actually
processed. One task must own both concerns.

## Adopted contract

Plan 02 Task 6 now owns the complete behavior:

- an optional callback fires once per completed judge **seat batch**, including
  an unparsed batch, but not once per retry HTTP attempt;
- judge configuration rejects non-positive batch sizes and negative repair or
  run-limit values before preview or execution calculates progress;
- the denominator is
  `ceil(K / JUDGE_BATCH_SIZE) * 2 * (JUDGE_MAX_REPAIR_ATTEMPTS + 1)`;
- MT uses the first tenth of the existing instance progress range and judging
  uses the remaining nine tenths;
- a cached verdict or no-repair run may finish with a final jump because the
  denominator is deliberately a fixed worst-case repair budget;
- the completion report and return link remain Plan 02's only
  producer-facing status contract.

The optional live-detail task is rejected. It would overwrite the shared
`task-log-<id>` component log, add an untranslated UI string, and duplicate
Plan 02's completion reporting. This consolidation does not add live
readiness polling.

## Required verification

Implementers follow Task 6's combined tests for callback propagation, parsed
and unparsed batches, capped-scope denominator math, nested progress ranges,
empty scopes, preview/execution parity, and completion reporting. Plan 02
also owns the single user-facing documentation and changelog update.
