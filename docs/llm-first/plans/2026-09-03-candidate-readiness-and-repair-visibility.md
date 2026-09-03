# Candidate readiness and repair visibility implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Make the judge's repair candidate reliably present before the
producer opens the string, and make its absence explain itself instead of
looking like a page that does nothing.

**Status:** proposed on 2026-09-03, not approved, not started.

**Origin:** a producer pressed :guilabel:`Сгенерировать исправление` on
`need-for-greed/orders/ru`, navigated away, came back, and found the card
unchanged. Investigation on the dev instance found three separate causes,
one per task below. Nothing about the observed symptom was a queue or a
timing problem: generation is already asynchronous
(`weblate/trans/views/edit.py:2054-2070`), and the run-time candidate path
already exists (`weblate/trans/judge_loop.py:1425-1507`).

## What the investigation established

| # | Fact | Evidence |
| --- | --- | --- |
| 1 | The judge run already stores a candidate for every reject/flag it produces | `_prepare_round_unit` sets `needs_candidate` for `_CANDIDATE_VERDICTS` (`judge_loop.py:638-642`); the run calls `repair_targets` and `_store_candidate` (`judge_loop.py:1425-1507`) |
| 2 | Generation from the card is already a Celery task, not a request-time call | `judge_generate_candidate` calls `generate_judge_candidate.delay(...)` unless `CELERY_TASK_ALWAYS_EAGER` (`views/edit.py:2054-2070`) |
| 3 | The task's outcome is discarded on the asynchronous path | the `report()` closure runs only in the eager branch (`views/edit.py:2032-2062`); the producer is told "queued" and never learns the result |
| 4 | The dev routing map had no `ru` entry, so the repair engine refused before spending anything | `machinery RoutedLLMTranslation failed: not supported language pair: en - ru`; site-wide `openrouter.routing` was `{'fr': 'openai/gpt-4o-mini'}` |
| 5 | `openai/gpt-4o-mini` rejects our reply schema outright | OpenRouter returned `Invalid schema for response_format 'translations': schema must be a JSON Schema of 'type: "object"', got 'type: "array"'` for the payload `_reply_format` builds (`weblate_customization/.../machinery.py:298-353`) |
| 6 | Google, DeepSeek and Qwen models accept the same schema | probed 2026-09-03: `google/gemini-2.5-flash` 200, `google/gemini-3.7-flash` 200, `deepseek/deepseek-chat` 200, `qwen/qwen-2.5-72b-instruct` 200, `openai/gpt-4o-mini` 400 |
| 7 | With routing fixed the whole path works unchanged | `generate_candidate_for_verdict(unit_id=141933, verdict_id=1099, replace=True)` returned `generated` and stored suggestion 11 |

Consequence of facts 4 and 5 together: on this instance every string judged
while the misconfiguration was live got `repair_status="no-candidate"`, and
nothing on the page or in the run report said why. The candidate pipeline was
never broken; it was starved by configuration and silent about it.

## Decisions

| # | Decision | Consequence |
| --- | --- | --- |
| 1 | The stored candidate stays out of the :guilabel:`Автоматические предложения` tab | That tab is the live-engine surface; a stored candidate keeps its diff, round, provenance and instruction only in the verdict card and the :guilabel:`Предложения` tab (`snippets/suggestions.html:7,21`) |
| 2 | Generation outcome becomes durable, not a message | A redirect message cannot survive navigating away, which is exactly what the producer did; the outcome is stored on the verdict and rendered by the card |
| 3 | The reply schema must be provider-portable | An OpenAI-family model in any routing entry currently breaks all machine translation and all repair for that language, silently |
| 4 | A language with no usable routing entry is a configuration error, reported once | Not a per-string failure the producer has to interpret |
| 5 | Backfill stays the only way to fill a historical gap | `judge_backfill_candidates` already exists; this plan does not re-judge anything |

## Invariants that stay

1. Generation never mutates the target; acceptance writes `STATE_FUZZY`; only a
   fresh re-check makes accepted text shippable.
2. Generate, accept and re-check keep requiring `unit.review` and
   `translation.auto`.
3. No secret, prompt or response body reaches a template or a log.
4. A paid output that no longer matches the judged snapshot is discarded.
5. No new paid call is introduced by any task here: Task 1 reports what already
   happened, Task 2 makes an already-paid call succeed instead of fail, Task 3
   refuses before spending.

## Cost contract

| Path | Paid calls | Change |
| --- | ---: | --- |
| Judge run over a reject/flag string | 1 repair MT | unchanged (already spent today, currently wasted when the model rejects the schema) |
| Producer presses Generate | 1 repair MT | unchanged |
| Producer opens a string with a stored candidate | 0 | unchanged |
| Language with no routing entry | 0 | Task 3 refuses at run start instead of failing per string |

---

## Task 1: Make the asynchronous generation outcome visible

**Files:**

- Modify: `weblate/trans/models/judge.py` (`JudgeVerdict`)
- Add: `weblate/trans/migrations/0119_judge_verdict_generation_outcome.py`
- Modify: `weblate/trans/judge_loop.py` (`generate_candidate_for_verdict`)
- Modify: `weblate/trans/views/edit.py` (`_judge_view_context`, `judge_generate_candidate`)
- Modify: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Tests: `weblate/trans/tests/test_judge.py`, `weblate/trans/tests/test_judge_views.py`

Add two fields to `JudgeVerdict`: `generation_outcome`
(`CharField(max_length=20, blank=True)`, choices from the existing stable
outcome codes in the `generate_candidate_for_verdict` docstring) and
`generation_outcome_at` (`DateTimeField(null=True, blank=True)`).

`generate_candidate_for_verdict` writes both on every terminal return,
including `busy`, using a targeted `update()` on the verdict row so it never
touches the judged snapshot or the unit. `generated` clears the pair, because
the stored candidate is then the state worth rendering.

`_judge_view_context` exposes `judge_generation_outcome` for the current
verdict only. The card renders a passive line under the card when the last
generation neither produced a candidate nor is in flight - never a modal,
never a form error:

| Outcome | Producer-facing line |
| --- | --- |
| `failed` | "Модель не вернула пригодного исправления. Попробуйте ещё раз." |
| `no-engine` | "Движок исправлений не настроен для этого языка." |
| `max-length` | "Строка не проходит по длине: исправление подбирается автоматически." |
| `denied` | "Нет прав на генерацию исправления." |
| `stale`, `resolved`, `invalid-verdict` | "Вердикт больше не актуален для этой строки." |
| `drift` | "Строка изменилась во время генерации; исправление отброшено." |

`judge_generate_candidate` keeps its "queued" message but stops being the only
signal: the durable outcome is what the producer sees after navigating away
and back.

**Acceptance:**

- A test queues generation with a failing engine, then GETs the string page and
  finds the failure line, with no candidate and no pending marker.
- A test asserts a successful generation leaves no outcome line.
- A test asserts `busy` is recorded and rendered as pending, not as a failure.
- Existing eager-path message tests keep passing unchanged.

## Task 2: Make the reply schema provider-portable

**Files:**

- Modify: `weblate_customization/src/weblate_customization/machinery.py`
  (`_reply_format`, reply parsing)
- Copy: `cp -r weblate_customization/src/weblate_customization dev-docker/data/python/`
- Tests: `weblate_customization/tests/test_machinery.py`

`_reply_format` currently declares a top-level `type: array`. OpenAI-family
providers reject that outright (fact 5), so any routing entry pointing at such
a model fails every batch with `Provider returned error` - both machine
translation and judge repair, for that language, permanently and silently.

Wrap the schema in an object with one required array property
(`{"type": "object", "properties": {"translations": {...}}, "required":
["translations"], "additionalProperties": false}`), keeping `minItems` and
`maxItems` on the inner array, and unwrap it before the base parser runs:
`RoutedLLMTranslation` (and the LiteLLM subclass, which shares the payload
builder) accepts both shapes on the way in, so a provider that answers with a
bare array - which is what today's models do - keeps working.

Do not change `provider.require_parameters`: routing only to providers that
honour the schema is still what keeps the parser's contract meaningful.

**Acceptance:**

- A unit test asserts the built payload's `response_format` root is
  `type: object` and that the inner array keeps the exact expected count.
- A unit test feeds both a wrapped and a bare reply through the parser and
  gets the same result.
- A manual live probe against `google/gemini-2.5-flash` and one
  OpenAI-family model both return 200 with the new payload, recorded in
  `docs/llm-first/measurements/`.

## Task 3: Refuse an unusable routing configuration before spending

**Files:**

- Modify: `weblate/trans/judge_loop.py` (`repair_targets`)
- Modify: `weblate/trans/models/judge.py` (`JudgeRunUnit.RepairStatus`)
- Add: `weblate/trans/migrations/0120_judge_run_unit_repair_status.py`
- Modify: `weblate/templates/judge-run.html`
- Modify: `docs/admin/machine.rst` or `docs/admin/checks.rst` as the routing
  documentation dictates at implementation time
- Tests: `weblate/trans/tests/test_judge.py`, `weblate/trans/tests/test_judge_views.py`

`repair_targets` returns `{}` for three unrelated reasons today: no engine
configured, the engine refusing the language pair, and the engine answering
with nothing usable. All three collapse into `repair_status="no-candidate"`,
which is why a whole component can come out candidate-less with no trace of a
misconfiguration.

Split the reason at the source. `repair_targets` gains a companion that
reports why it produced nothing, and `RepairStatus` gains
`NO_ENGINE_FOR_LANGUAGE`. The run report shows the new status as a
configuration problem with the language named, once per run, not once per
string. Existing rows keep `no-candidate`; the new value is additive.

`RoutedLLMTranslation` already refuses an unrouted language pair before the
HTTP call, so this task adds no paid call and removes none: it only stops the
result being indistinguishable from a model that answered badly.

**Acceptance:**

- A test runs the judge over a translation whose language is absent from the
  routing map and asserts `no-engine-for-language` on the run unit, no paid
  call attempted, and the verdict still holding the string.
- A test asserts a configured language whose engine returns nothing usable
  still records `no-candidate`.
- The run report renders the configuration line once for a multi-string run.

---

## Verification

```bash
CI_DB_NAME=weblate_candidate_readiness \
  DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_tasks.py -q -p no:randomly
cd weblate_customization && uv run pytest
uv run prek run --files <touched files>
```

## Out of scope

- Rendering the stored candidate in the :guilabel:`Автоматические предложения`
  tab (decision 1).
- Any change to the judge prompt, the seat configuration or the verdict
  schema.
- Re-judging anything. Filling a historical candidate gap is
  `judge_backfill_candidates`, already documented in
  `docs/admin/management.rst`.
- Changing the production routing map. That is a configuration change on a
  live instance and needs its own approval.

## Deployment notes (each needs explicit approval)

- Task 1 and Task 3 add migrations (`0119`, `0120`). Dev needs
  `./rundev.sh` migrate; production needs the usual approval.
- Task 2 reaches the dev container only through
  `cp -r weblate_customization/src/weblate_customization dev-docker/data/python/`.
- Russian strings from Task 1 need `./rundev.sh compilemessages`.
- Production routing currently must be verified: this plan assumes
  `google/gemini-3.7-flash` there and `google/gemini-2.5-flash` on dev. Dev
  was set to `{'fr': 'google/gemini-2.5-flash', 'ru':
  'google/gemini-2.5-flash'}` on 2026-09-03; production has not been read or
  changed and needs a separate approved check.
