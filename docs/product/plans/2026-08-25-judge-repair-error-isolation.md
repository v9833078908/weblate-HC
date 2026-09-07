# Plan: fix the run-abort on a single failed repair

Date: 2026-08-25. Status: **done.**

Genre: implementation. Output: one exception catch and two tests.

Basis:

- `docs/llm-first/measurements/2026-08-25-judge-repair-loop.md`, section 4.2
- `weblate/trans/judge_loop.py:305`: `repair_target` calls
  `MACHINERY[engine_id](setting).translate(unit, user)`, which can raise
  `MachineTranslationError`
- `weblate/trans/judge_loop.py:460`: `_process_round_unit` is called
  inside `for unit in pending`, and any unhandled exception aborts the
  whole loop

## Defect

`MachineTranslationError` from `repair_target` propagates straight
through `_process_round_unit`, `run_judge_batch`, and `process_judge`.
Judge verdicts are paid for, no final states are assigned, no text is
repaired. One bad unit aborts the run for the whole component.

Reproduced twice on 2026-08-25 against `zh_Hans`: `deepseek-chat-v3.1`
returned structured JSON to a repair request, the
`machinery/openai.py` validator raised `MachineTranslationError`, and
the run crashed.

## Change

One place: `weblate/trans/judge_loop.py`.

```python
# Before (line 305):
new_target = repair_target(current, user)

# After:
try:
    new_target = repair_target(current, user)
except MachineTranslationError:
    new_target = None
```

Behavior: `new_target is None` is handled by the existing branch
`if new_target is None: return verdict, _RepairOutcome(current)`. The
unit stays in its current state, the verdict is recorded, and the loop
moves to the next candidate. Logging is `repair_target`'s own concern;
it already reports a warning through `machinery`.

`MachineTranslationError` is NOT reachable through an existing import:
`judge_loop.py` imports `MACHINERY` from `weblate.machinery.models`,
while `MachineTranslationError` lives in `weblate.machinery.base` and
needs its own import line, matching
`weblate/trans/autotranslate.py:21` (`from weblate.machinery.base
import MachineTranslationError`).

## What does not change

- `repair_target` is unchanged: it already returns `None` on an empty
  result, and that is the right contract
- `MachineTranslationError` stays an exceptional condition in
  `machinery`, caught only at the call site where it matters
- `run_judge_batch` is unchanged: it should not know repair internals
- `process_judge` is unchanged: it already handles
  `failed`/`failure_message` via `auto.failure_message`

## Test

Two tests in `weblate/trans/tests/test_judge_loop.py`:

`test_repair_target_machinery_error_does_not_crash_the_batch`: minimal
reproduction. One unit, `repair_target` raises
`MachineTranslationError`, `run_judge_batch` does not crash, a FLAG
verdict is recorded, the unit stays unrepaired.

`test_repair_target_error_on_one_unit_lets_others_continue`:
acceptance scenario. Two units in one run, the first unit's
`repair_target` raises, the second repairs successfully. Verifies the
first unit gets FLAG and stays unrepaired, while the second reaches a
second round, is repaired, and gets PASS. A sibling in the same batch
is not blocked by another candidate's failure.

## Acceptance

- [x] `MachineTranslationError` is caught in `_process_round_unit`
- [x] Tests reproduce the 2026-08-25 measurement scenario (both fail
      against the unfixed code, confirmed by reverting the fix)
- [x] `uv run pytest weblate/trans/tests/test_judge_loop.py`: 30 passed
- [x] Full suite `uv run pytest weblate/trans/tests/test_judge.py
      weblate/trans/tests/test_judge_round.py
      weblate/trans/tests/test_judge_loop.py
      weblate/trans/tests/test_judge_autotranslate.py`: 69 passed
- [x] Commit and push
