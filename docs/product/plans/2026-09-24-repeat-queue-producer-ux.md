# Repeat queue: producer UX after the bulk-recommendation branch

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

Date: 2026-09-24.
Status: **decisions agreed with the repository owner on 2026-09-24;
implementation not started.** Work starts after
`docs/product/plans/2026-09-23-repeat-queue-speed-and-bulk-recommendations.md`
is finished and merged (branch `codex/repeat-queue-speed-and-bulk`). Editing
this plan does not authorize deploying it.

**Goal:** A producer who cannot read the target language opens a repeat group
and immediately understands what the variants are, which one is known to be
good, and what happens next, without a paid command they do not understand.

**Architecture:** UI changes on top of the finished branch; nothing is deleted.
The queue no longer links to the paid "Prepare recommendations" page. A group's
recommended variant comes from verdicts the LLM judge has already stored for
the exact current text of its places (`JudgeVerdict`, read through the existing
collegium helpers). The queue never starts the judge or any other paid request.
The recommended variant is preselected; the preview and confirmation screen
stays mandatory.

**Tech Stack:** Django templates with `{% translate %}`,
`weblate/trans/views/repeats.py`, `weblate/trans/models/judge.py`,
`weblate/static/js/repeat-queue.js`, Russian `.po`, pytest.

---

## Why this plan exists

Producers asked why the queue has a "Prepare recommendations" button when every
group already lists translations. The variants on a card are the translations
already in the game, almost all produced by machine translation of each place
separately. The button started a separate paid LLM run that chose between them.
From the producer's side this reads as "ask the machine again", its launch page
speaks in requests, reservations and overdue sends, and it is a second quality
judgement next to the LLM judge, which already evaluates translations and runs
separately and optionally.

## Decisions (2026-09-24)

| # | Decision | Consequence |
| --- | --- | --- |
| D1 | Hide the "Prepare recommendations" button and its launch page from the UI. | The link leaves `repeat_queue.html`. The `repeat-recommend` route, view, models, prompt and tests stay; nothing is deleted. |
| D2 | No background (automatic) recommendation run in this plan. | Discussed and deferred; see "Deferred". |
| D3 | A card's recommended variant comes from the judge, only when the judge has already checked it. | The queue reads stored verdicts; it never triggers the judge. Groups without verdicts look as they do today. |
| D4 | The recommended variant is preselected. | Changes the `PRODUCT.md` truth "the UI preselects nothing" for this one case. The preview/confirmation step is unchanged. |
| D5 | Show judge evidence under a variant when it exists: verdict and back-translation. | Read-only; lets a producer understand the meaning of a French variant. |

Unchanged: existing model recommendations (`RepeatRecommendationResult`) still
render on the card, and the "Review and apply" banner still appears when current
model recommendations exist. Bulk application of judge recommendations is out of
scope.

## Prerequisites in the current branch

Both defects are in `codex/repeat-queue-speed-and-bulk` (pushed and working
copy, checked 2026-09-24) and were handed to that branch on 2026-09-24. They
are fixed there before merge, not in this plan.

1. **Double reservation on launch.** `weblate/trans/views/repeats.py`,
   `repeat_recommend` POST: `prepare_run` already reserves and queues every
   attempt, then the view calls `reserve_attempt` + `queue_attempt` again with
   all groups of the snapshot. With the cap used up (default cap 1) this raises
   `ValidationError` (HTTP 500) after the paid run was queued; otherwise it pays
   for the same groups a second time. Fix: delete the second reservation; decide
   "nothing to send" from `run.requests_reserved`. Hiding the page (D1) does not
   remove the route, so this still matters.
2. **Card and banner disagree on freshness.** `_add_recommendations` still
   filters `group_revision=group.revision, run__status="completed"`, while the
   banner and review use `current_recommendations`. Fix: call
   `current_recommendations(policy, actor=request.user)` once per page and look
   groups up in the result.

## Recommendation rule

Evaluated only for groups with status `open` on the current page.

1. For every place, take the active collegium verdict for its current text:
   `active_verdict(unit)` semantics (`weblate/trans/models/judge.py`), i.e. the
   newest parsed row per seat matching the stored target hash, reduced by
   `collegium_verdict`. Unparsed rows are not opinions.
2. A variant is **passed** when at least one of its places has verdict `pass`
   and none has `flag` or `reject`. It is **flagged** when any place has `flag`
   or `reject`. Otherwise it is **unchecked**.
3. The group's recommendation is the passed variant **only when exactly one
   variant is passed**. Two or more passed variants produce no recommendation:
   the judge does not distinguish them, and majority is not evidence
   (base plan §3.4).
4. No recommendation in a group containing an approved place. This keeps the
   P0 finding of `docs/product/reviews/2026-09-22-repeat-queue-ui-variant-review.md`
   (a preselection must never compete with an approved translation).

Known limit, stated in the UI copy: the judge checked a variant in the context
of its own place, not in every place of the group. The repeat rule's scope
(only components where identical text means the same thing) and the manual
"Different meanings" choice cover the rest.

Measured on the local copy of `anvil-saga` / `fr` on 2026-09-24: 0 of 1052
places in the 385 diverging groups have a live judge verdict. Until the judge
is run on that project, cards there show no recommendation; this plan does not
change that.

## Task 1: batched active verdicts

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Test: `weblate/trans/tests/test_judge.py`

`active_verdict(unit)` issues several queries per unit (one per seat). A queue
page holds 20 groups and often more than 50 places.

**Step 1: failing test.** For a set of units covering two seats, a newer
unparsed row over an older parsed one, a stale target hash, a `candidate`
subject row and both `JUDGE_CONSENSUS_REJECT` settings, assert that
`active_verdicts(units)` returns, per unit, the same row, `verdict` and
`effective_severity` as `active_verdict(unit)`. Assert a constant number of
queries independent of the unit count.

**Step 2: implement** `active_verdicts(units) -> dict[int, JudgeVerdict | None]`:
one query for `subject=live` rows of the given units, filtered in Python by each
unit's `compute_target_hash(unit.get_target_plurals())`, then the same per-seat
selection as `_seat_round_rows(..., context_hash=None, prefer_parsed=True)` and
`collegium_verdict`. Share the per-seat selection with `_seat_round_rows`
instead of copying it.

**Step 3:** `uv run pytest weblate/trans/tests/test_judge.py -n 0`.

Commit: `feat(judge): read active verdicts for many units at once`.

## Task 2: judge recommendation on the card

**Files:**

- Modify: `weblate/trans/views/repeats.py` (page items, next to `_add_recommendations`)
- Modify: `weblate/templates/snippets/repeat_group.html`
- Modify: `weblate/static/js/repeat-queue.js`
- Test: `weblate/trans/tests/test_repeat_views.py`

**Step 1: failing view tests** (create `JudgeVerdict` rows with real target
hashes; no provider call):

- One passed variant, one unchecked: the passed variant is listed first, its
  radio is `checked`, the preview button is not `aria-disabled="true"`, and
  the badge "Recommended: checked by the judge" is shown.
- Two passed variants: nothing is checked; both show "Checked by the judge".
- A flagged variant shows "The judge found an error" and is never checked.
- A group with an approved place: nothing is checked.
- A variant with a back-translation shows it.
- A group without verdicts renders exactly as before (nothing checked,
  preview disabled).
- Query count for a page of groups grows by a constant, not per place.

**Step 2: view.** For page items with status `open`, call `active_verdicts`
once for all their places, classify each variant by the rule above, attach
`judge` (`passed`/`flagged`/`unchecked`, back-translation from the passed or
flagged row) to the variant dict and `judge_recommendation` to the item, and
move the recommended variant to the top of `item["variants"]`.

**Step 3: template.** On the variant row: the badge, and
"Back-translation: ..." as muted text when present. The recommended variant
carries `checked`; the preview button then renders enabled and the hint shows
the preview text. Under the recommended variant, one line of copy: "The judge
checked this translation in one of its places. Look through the preview before
confirming." Keep the model-recommendation row as it is. Status is never
conveyed by color alone; badges carry text.

**Step 4: JS.** Call `updateDecision()` once per form on load so a preselected
radio shows the custom field state and hint correctly.

**Step 5:** `uv run pytest weblate/trans/tests/test_repeat_views.py -n 0`.

Commit: `feat(repeats): recommend the variant the judge has passed`.

## Task 3: hide the paid launch from the queue

**Files:**

- Modify: `weblate/templates/repeat_queue.html`
- Test: `weblate/trans/tests/test_repeat_views.py`

Remove the "Prepare recommendations" link and its "Paid" badge from
`.rq-toolbar`. Keep the "Review and apply" banner. Test: the queue response
does not contain the `repeat-recommend` URL; existing `repeat-recommend` view
tests still pass unchanged.

Commit: `feat(repeats): hide the paid recommendation launch from the queue`.

## Task 4: Russian strings

Extract with `uv run ./manage.py makemessages -l ru -d django`, keep only this
change's entries, validate with `msgfmt -c -o /dev/null
weblate/locale/ru/LC_MESSAGES/django.po`.

| English msgid | Russian |
| --- | --- |
| Recommended: checked by the judge | Рекомендуем: проверен судьёй |
| Checked by the judge | Проверен судьёй |
| The judge found an error | Судья нашёл ошибку |
| Back-translation: %(text)s | Обратный перевод: %(text)s |
| The judge checked this translation in one of its places. Look through the preview before confirming. | Судья проверил этот перевод в одном из мест. Перед подтверждением просмотрите, что изменится. |

Commit: `fix(i18n): translate judge recommendations in the repeat queue`.

## Task 5: product truths and UI contract

**Files:**

- Modify: `PRODUCT.md` ("Product truths that designs must respect")
- Modify: `docs/product/plans/2026-09-21-repeat-queue-ui-variant.md` (§2.2 and the variant rows)

`PRODUCT.md`:

- "the UI preselects nothing" becomes: "the UI preselects nothing except a
  repeat variant the LLM judge has already passed for its exact current text;
  a preselected choice still goes through preview and confirmation".
- The paid-trigger truth notes that the repeat queue currently exposes no
  paid trigger: the "prepare recommendations" command exists but is not linked
  from the UI.

UI contract: §2.2 no longer renders the "Update recommendations" button;
variant rows gain the judge badge, back-translation and the preselection rule,
linking to this plan by its full path.

Commit: `docs(repeats): record judge-based preselection and hidden paid launch`.

## Task 6: verification and delivery

- `uv run pytest weblate/trans/tests/test_judge.py weblate/trans/tests/test_repeat_views.py weblate/trans/tests/test_repeats.py -n 0`
- `uv run prek run --files <changed files>`
- Browser check in Russian on a disposable dev fixture with stored verdicts:
  keyboard only, preselected radio announced, preview reachable, badges
  readable without color. Deploying to dev needs explicit approval; if not
  approved, record that the browser check was not run.
- Push the feature branch and open a PR against `main`.

## Deferred

Background recommendation runs without a button (discussed 2026-09-24,
postponed). If revived, it must resolve what the 2026-09-23 plan assumes about
an interactive run: runs and contexts are actor-bound
(`live_group_contexts` filters by `filter_access(actor)`), `needs_human`,
`keep_independent` and unknown deliveries are never retried automatically, the
limit cannot be in dollars because the price may be unknown (base plan
principle 8), and it must default to off because it spends money without a
click.

Out of scope: triggering the judge from the queue, bulk application of judge
recommendations, deleting any recommendation code, changes to `propose_new`
handling.

## Results

Pending. Fill from observed output, with the tested commit.

| Check | Result |
| --- | --- |
| `active_verdicts` equals `active_verdict` per unit | Pending |
| Queue queries constant in the number of places | Pending |
| Preselection rule cases (one/two passed, flagged, approved, none) | Pending |
| Queue has no link to the paid launch | Pending |
| Russian browser and keyboard check | Pending |
