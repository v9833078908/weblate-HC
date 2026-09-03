# Candidate readiness and repair visibility implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Make the judge's repair candidate reliably present before the
producer opens the string, and make its absence explain itself instead of
looking like a page that does nothing.

**Status:** completed on 2026-09-03. All four tasks were implemented,
verified, merged to `main` (`81b5f3a`) and deployed to the dev instance
(migrations `0119`/`0120` applied, `compilemessages`, `collectstatic`, and
the `weblate_customization` copy). The acceptance semantics of Task 4 were
decided on 2026-09-03 (decision 1); its label, its gating and its third JS
collision come from that review (decisions 6-7). Two later changes were made
on top of the plan at the producer's request: the on-demand paid
:guilabel:`Сгенерировать исправление` button and every "Paid model request"
hint were removed from the verdict card (superseding decision 6, which had
kept the button as the only way to ask for a fix on a `no-candidate`
string), and the run report's row action now says :guilabel:`Fix by hand`
for a `no-engine-for-language` row instead of relying on the per-row status
column that the report's Pareto redesign had removed. Production has not
been touched and needs its own approval.

**Origin:** a producer pressed :guilabel:`Сгенерировать исправление` on
`need-for-greed/orders/ru`, navigated away, came back, and found the card
unchanged. Investigation on the dev instance found three separate causes,
one each for Tasks 1-3. Nothing about the observed symptom was a queue or a
timing problem: generation is already asynchronous
(`weblate/trans/views/edit.py:2054-2070`), and the run-time candidate path
already exists (`weblate/trans/judge_loop.py:1425-1507`). Task 4 comes from
the follow-up question of where the producer expects to find the fix.

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
| 1 | The stored candidate is rendered in the :guilabel:`Автоматические предложения` tab, and accepting it there stores `STATE_TRANSLATED` (decided 2026-09-03) | Task 4 is in scope. The row is server-rendered, so it costs no call. Its action still posts to `accept_judge_candidate`, which re-verifies target and context hashes, refuses a forged run id, deletes the candidate and records the audited Change (`judge_loop.py:2114-2148`); only the written state changes from `STATE_FUZZY` to `STATE_TRANSLATED`. The diff, the round and the judge's repair instruction stay in the card, which the row cannot carry |
| 2 | Generation outcome becomes durable, not a message | A redirect message cannot survive navigating away, which is exactly what the producer did; the outcome is stored on the verdict and rendered by the card |
| 3 | The reply schema must be provider-portable | An OpenAI-family model in any routing entry currently breaks all machine translation and all repair for that language, silently |
| 4 | A language with no usable routing entry is a configuration error, reported once | Not a per-string failure the producer has to interpret |
| 5 | Backfill stays the only way to fill a historical gap | `judge_backfill_candidates` already exists; this plan does not re-judge anything |
| 6 | The card keeps :guilabel:`Сгенерировать исправление`; the tab row is a second place to see the one candidate, not a second way to create one | `snippets/judge-verdict.html` renders generate and accept as mutually exclusive branches (`{% elif judge_candidate %}` at line 130, `{% elif judge_active_verdict %}` at line 153), so the generate button is visible exactly when no candidate exists - the state this plan exists to repair. Removing it would strand every `no-candidate` string with no way to ask for a fix. :guilabel:`Сгенерировать другой вариант` (line 206) stays the paired action for when a candidate is already there |
| 7 | The row's button is labelled :guilabel:`Принять исправление`, never a bare :guilabel:`Принять` | The engine rows in the same table already carry :guilabel:`Принять` (`js-copy-save-machinery`, `full.js:815-817`), which only copies, saves and skips every guard. Two identically labelled buttons with different semantics in one table is the worst outcome available. The existing `Use suggested fix` -> `Принять исправление` msgid is reused, and the origin column says `AI judge` -> `ИИ-судья` |

## Invariants that stay

1. Generation never mutates the target. Acceptance is audited and hash-checked,
   and it queues the fresh re-check.
2. Generate, accept and re-check keep requiring `unit.review` and
   `translation.auto`.
3. No secret, prompt or response body reaches a template or a log.
4. A paid output that no longer matches the judged snapshot is discarded.
5. No new paid call is introduced by any task here: Task 1 reports what already
   happened, Task 2 makes an already-paid call succeed instead of fail, Task 3
   refuses before spending.

## The one invariant this plan changes

`docs/llm-first/plans/2026-09-01-judge-producer-triage-embed.md` invariant 1
said that accepting a candidate writes `STATE_FUZZY`, so that only a fresh
re-check can make the repaired text shippable. Decision 1 replaces that
clause: acceptance writes `STATE_TRANSLATED`, and the queued re-check
decides afterwards.

What the producer gains: a string they accepted looks finished immediately,
in the editor and in every count, instead of sitting in
:guilabel:`Требует правки` until a background call returns.

What the studio takes on: between acceptance and the re-check's verdict the
string is shippable. If a commit or a hand-off downloads the file in that
window, an unverified LLM repair of a string the judge had rejected can
reach the game. A failing re-check then holds the string again, but it holds
text that already left.

The window is the re-check's own latency (two judge calls). No task here
widens it, and the "Download for hand-off" indicator still refuses while any
judged string is not current and clean, so the documented hand-off path is
not affected - only an ad-hoc commit or export in that window is.

Recorded as accepted on 2026-09-03. Reverting it is a one-line change in
`accept_judge_candidate` plus its tests.

## Cost contract

| Path | Paid calls | Change |
| --- | ---: | --- |
| Judge run over a reject/flag string | 1 repair MT | unchanged (already spent today, currently wasted when the model rejects the schema) |
| Producer presses Generate | 1 repair MT | unchanged |
| Producer opens a string with a stored candidate | 0 | unchanged |
| Producer accepts the candidate, in the card or in the tab | 2 judge (re-check) | unchanged; only the written state changes |
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

## Task 4: Show the candidate in the automatic suggestions tab and accept it as translated

Producers look for a suggestion in :guilabel:`Автоматические предложения`.
Put the stored candidate there as the first row, and make accepting it leave
the string translated rather than held.

**Files:**

- Modify: `weblate/templates/translate.html` (the `#machinery` pane,
  `tbody#machinery-translations` at line 543)
- Modify: `weblate/trans/judge_loop.py` (`accept_judge_candidate`:
  `STATE_FUZZY` at line 2138 becomes `STATE_TRANSLATED`, and the docstring
  at line 2060, which states the old value)
- Modify: `weblate/static/editor/full.js` (`Machinery.render` merge loop,
  the memory-search handler, the hotkey numbering pass)
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po` only if a new msgid is
  unavoidable; `Use suggested fix`, `Suggested fix` and `AI judge` are
  already translated, and a needed entry is added by a targeted edit plus
  `compilemessages`, never by a repository-wide `makemessages` run
- Modify: `docs/guides/producer-guide-weblate.md`, `docs/changes.rst`
- Tests: `weblate/trans/tests/test_judge_views.py`,
  `weblate/trans/tests/test_judge.py`, `weblate/trans/tests/test_edit.py`
- No view change: `_judge_view_context` already resolves `judge_candidate`
  (`views/edit.py:1442`) and the whole dict is merged into the
  `translate.html` context by `**judge_context` (`views/edit.py:1573`), so
  the pane reads `judge_candidate`, `judge_can_triage`,
  `judge_recheck_pending` and `judge_active_max_length` as they are

**The row.** Server-rendered inside `tbody#machinery-translations`, so it is
present the moment the tab opens and costs no call - the text is already in
the database. Columns reuse the existing table: target text, the diff against
the current text (the card's own rendering, which the row can carry after all
because it is server-side), an empty source column, the judge as origin, and
no similarity. It must carry the same cell count as a JS-rendered row - nine
`<td>` (`full.js:781-840`: target, diff, source diff, origin, similarity,
clone, accept, approve-or-empty, delete-or-empty) - or the table skews the
moment engine rows arrive. Marked `data-judge-candidate="1"`.

**When the row renders.** Under `user_can_use_machinery` and
`judge_can_triage`, and only while the card itself would show the candidate:
`judge_candidate and not judge_recheck_pending and not
judge_active_max_length`, mirroring the branch order in
`snippets/judge-verdict.html:121-130`. Two consequences the acceptance
criteria have to carry:

- `machinery.view` gates the tab and the pane alike
  (`translate.html:42,325,518`). A producer holding `unit.review` and
  `translation.auto` but not `machinery.view` sees no tab at all, so the card
  stays the only guaranteed acceptance path: the row is an addition, never a
  replacement.
- While a re-check is in flight the verdict behind the candidate is about to
  change, and the card deliberately shows `Re-checking this string…` instead
  of the candidate. `active_judge_candidate` (`judge_loop.py:1944-1979`) does
  not cover this on its own: it hash-matches the candidate against the unit
  and the current verdict, and a queued re-check changes neither, so the
  template condition has to say so.

**The action.** A form posting to `judge-accept-candidate`, the same endpoint
the card uses - never the `js-copy-save-machinery` handler. `markTranslated`
plus a plain form submit would reach `STATE_TRANSLATED` too (`views/edit.py:914-926`
lifts changed text on a judged string and `_queue_manual_save_recheck`
queues the re-check), but it would skip the target/context hash
re-verification, the run-id check, the candidate deletion and the Change
carrying `judge_verdict_id`/`judge_run_id` (`judge_loop.py:2114-2148`). The
leftover candidate row is not hypothetical: it is deleted only when a newer
candidate replaces it (`models/suggestion.py:139`), so a plain-form
acceptance would leave a suggestion in the :guilabel:`Предложения` tab that
matches the current text.

**The state.** `accept_judge_candidate` writes `STATE_TRANSLATED` instead of
`STATE_FUZZY`; everything else in it stays. See "The one invariant this plan
changes". The card's :guilabel:`Принять исправление` gets the same behaviour -
two acceptance paths with different states would be worse than either
choice.

**What the card keeps.** Both of its buttons, exactly where they are.
Generate and accept are already mutually exclusive branches
(`snippets/judge-verdict.html:130,153`), so the new row never sits next to a
generate button for the same string; and on a `no-candidate` string - the
case this plan exists for - the card's
:guilabel:`Сгенерировать исправление` is the only way to ask for a fix at
all (decision 6). Task 4 adds a second place to see one candidate, not a
second candidate and not a second way to make one.

**Three JS collisions to handle, all in `full.js`:**

1. `Machinery.render` (line 904) walks every existing row through
   `getRawData(row)` and reads `base.quality`/`base.plural_forms` to merge
   and insert-sort. `getRawData` returns `undefined` for a row without
   `data-raw` (lines 38-40), so `base.text` at line 915 throws a TypeError.
   Skip rows carrying `data-judge-candidate` in the merge loop, and keep them
   first regardless of quality.
2. The memory-search handler calls
   `document.getElementById("machinery-translations").replaceChildren()`
   (line 344), which would wipe the candidate row. Preserve it.
3. `processMachineryResults` numbers every child row by index and writes
   `data-machinery-key` (lines 466-490). The candidate row would take key
   `1`, and the `Ctrl+M` then digit handler looks for `.js-copy-machinery`
   inside the matched row and `break`s when it is absent (lines 377-386), so
   the first hotkey slot would silently do nothing. Skip
   `data-judge-candidate` rows in the numbering pass so the engine rows keep
   `1`-`9`.

**Acceptance:**

- The pane contains the candidate row in the server-rendered HTML, before any
  machinery request is made (assert without JS).
- The row's only action posts to `judge-accept-candidate`; the accepted string
  ends in `STATE_TRANSLATED`, the candidate row is gone, one re-check is
  queued, and the Change carries the verdict and run ids.
- A string with no stored candidate, a string whose candidate no longer
  hash-matches, a string with a queued re-check and a string on the
  max-length path all render no such row.
- A producer without `machinery.view` gets neither the tab nor the row, and
  the card's own acceptance still works for them.
- The row's button reads :guilabel:`Принять исправление`, the engine rows
  keep :guilabel:`Принять`, and the row carries neither
  `js-copy-save-machinery` nor `js-copy-machinery`.
- A JS test or a browser pass shows machinery results loading around the
  candidate row without throwing and without displacing it, a memory search
  leaving it in place, and `Ctrl+M` then `1` still reaching the first engine
  row.
- `docs/changes.rst` records the state change as a behaviour change, not as a
  UI tweak.

---

## Verification

```bash
CI_DB_NAME=weblate_candidate_readiness \
  DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_edit.py \
  weblate/trans/tests/test_tasks.py -q -p no:randomly
uv run pytest weblate_customization/tests/test_machinery.py -q -p no:randomly
uv run prek run --files <touched files>
```

## Out of scope

- Rendering a live machine-translation result anywhere but that tab, and any
  change to how the tab's own engine rows are fetched or sorted.
- A second acceptance path with different semantics: the card and the row
  write the same state.
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
- Russian strings from Tasks 1 and 4 need `./rundev.sh compilemessages`.
- Production routing currently must be verified: this plan assumes
  `google/gemini-3.7-flash` there and `google/gemini-2.5-flash` on dev. Dev
  was set to `{'fr': 'google/gemini-2.5-flash', 'ru':
  'google/gemini-2.5-flash'}` on 2026-09-03; production has not been read or
  changed and needs a separate approved check.
