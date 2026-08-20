# First judge run on dev — 2026-08-20

Acceptance run for `docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md`
(task 13, step 7). Dev instance only (`localhost:3001`); nothing on production
was touched.

## Setup

| | |
|---|---|
| Target | `need-for-greed/buyers`, `ru` (source `en`, xlsx) |
| Units in scope | 10, all `state=20` — existing human translations |
| Mode | `judge`, `q = state:>=translated`, overwrite **off** |
| Pre-translation engine | `openrouter`, threshold 80 (nothing to do: no empty strings) |
| Seat 1 | `deepseek/deepseek-v4-pro` |
| Seat 2 | `qwen/qwen3-235b-a22b-2507` |
| Batch size / repair attempts / may-approve | 5 / 1 / off (all defaults) |

`translation_review` had to be turned on for the project: `unit.review` is gated
by the project's review workflow, so without it the mode is not offered at all —
not even to a superuser. It was turned back off after the run.

## Result

| Metric | Value |
|---|---|
| Verdicts written | 20 = 10 units × 2 seats, `attempt=0` only |
| `unparsed` | **0** |
| Seats disagreed | **2 of 10** |
| Repairs run | **0** (no `reject`, so the loop never entered) |
| Collegium outcome | 8 `pass`, 2 `flag`, 0 `reject` |
| States changed | none — every unit stayed at 20 |
| Human translations rewritten | **0** (overwrite off) |
| Wall time | first verdicts ~65 s after submit, complete by ~85 s |

Both disagreements resolved to `flag`, in both directions — seat 2 stricter on
one unit, seat 1 stricter on the other — so "no seat may lower the other" held
symmetrically on live data:

- `Eliza Glitterford` → `Элиза Блескфорд`: seat 1 `minor`, seat 2 `major`.
- `Sir Maxillion` → `Сэр Максиллион`: seat 1 `major`, seat 2 `none`.

Navigation and projection:

- `check:judge-flag` matches exactly the 2 flagged units; `check:judge-reject`
  matches none.
- The projected rows survived a full `run_checks()` over all 10 units
  (regression D1).
- The verdict card renders both seats with the model and no score, and the
  judge check does not appear in "Things to check".

## Cost

Four requests (2 batches × 2 seats) for 10 strings:

| Model | prompt | completion | reasoning | cost, $ |
|---|---|---|---|---|
| `deepseek/deepseek-v4-pro` | 1168 | 3512 | 3248 | 0.01425 |
| `deepseek/deepseek-v4-pro` | 1156 | 306 | 0 | 0.00200 |
| `qwen/qwen3-235b-a22b-2507` | 1175 | 304 | 0 | 0.00042 |
| `qwen/qwen3-235b-a22b-2507` | 1159 | 190 | 0 | 0.00021 |
| **total** | 4658 | 4312 | 3248 | **0.01688** |

One deepseek batch spent 3248 reasoning tokens and 84% of the run's entire cost;
its sibling batch spent none. Reasoning bursts, not string count, dominate the
bill, and they are not predictable per batch — the naive extrapolation of this
run ($1.69/1000 strings) is roughly five times the phase-0 rate
($0.33/1000 for the same pair) purely because of that one batch.

## Findings

1. **The row counter does not count the filter it names.** The label promises
   "The current filter matches N strings", but `judge_row_count`
   (`weblate/trans/views/basic.py:805-812`) counts a hardcoded
   `state:<translated` — neither the `q` the operator typed nor the form's own
   default, `state:empty` (`weblate/trans/forms.py:1201`). On a fully
   translated component — the audit case, and the expected primary use — the
   form promised 0 strings and then judged 10, and `judge_request_estimate`
   under-priced the run by the same factor. The counter exists to show the
   price before the money is spent, so it misleads exactly where it is needed.
2. **The default `q` does not follow the mode.** It stays `state:empty`, so the
   audit case requires the operator to type a filter by hand; leaving the
   default would judge nothing on a translated component. The design's intended
   default is `NOT has:judge`.

   Findings 1 and 2 are one piece of work and both are now recorded in plan 2's
   scope (`docs/LLM-first/2026-08-13-judge-native-ui-design.md`, "Планы первого
   тира" and "Известные временные разрывы"; deferral table in
   `docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md`). They share one
   dependency — the `has:judge` filter that plan 2 introduces — so fixing the
   counter before that filter exists would be work thrown away.
3. **Judge spend was unattributed.** All four usage rows carried a blank
   `project_slug`, so `llm_usage_report --project` could not see them. Fixed in
   this branch (`fix(judge): bill judge requests to the project that ran them`).
4. **The mode label is untranslated** in the ru UI ("Add as translation with an
   LLM judge") — a new source string awaiting the usual locale update.
5. **A live false `major`.** Seat 1 rejected `Сэр` in `Сэр Максиллион`, arguing
   that "in a formal military/political WWII setting" the correct form is `Сир`.
   The component is a roster of fantasy NPC buyers; the setting is invented. The
   verdict is a hallucination of context, and because the collegium takes the
   strict maximum, one seat's hallucination becomes a flag a human must clear.
   This is the measured false-major rate showing up on the first live run, and
   it is the reason `pass` does not auto-approve without `JUDGE_MAY_APPROVE`.
   The other disagreement was the opposite: both seats found a real defect —
   `Glitterford` rendered as `Блескфорд`, half-translated where the rest of the
   roster is transliterated.

## Not exercised by this run

No string drew a `critical`, so the `reject` → `STATE_FUZZY` hold, the
`WITHOUT_NEEDS_EDITING` export gate, and the repair loop were not observed
live. They are covered by tests only.
