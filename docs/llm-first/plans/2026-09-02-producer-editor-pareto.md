# Producer editor Pareto reduction implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** Turn the translate page into a three-action producer triage surface
for a judged string - read why the judge rejected it, accept the judge-guided
fix in one click, or fix the text by hand and let the judge re-check it
automatically - by removing or collapsing every control that serves a
translator the studio does not have.

**Architecture:** Keep the existing translate page, the embedded verdict card
(`weblate/templates/snippets/judge-verdict.html`), the stored repair candidate
(`Suggestion` with `userdetails.kind == "judge-repair"`), `resolve_verdict`,
and `queue_judge_recheck`. No new pages, no new models, no migrations. The
reduction is template- and form-level and unconditional in this fork: the
producers are project administrators, and the `Administration` role carries
every permission (`weblate/auth/data.py:199-200`), so permission-based hiding
cannot reach them. Every hidden control keeps a reachable home (a "More"
dropdown tab, the component screenshots page, the string history).

**Tech stack:** Python 3.14, Django templates (Bootstrap 5, jQuery-free
`weblate/static/editor/full.js`), gettext (`weblate/locale/ru`), pytest,
Docker Compose dev instance on port 3001.

**Design origin:** `docs/llm-first/designs/2026-09-01-02-producer-ui-reduction.md`
(P1 items), the review of the live page on 2026-09-02 (215 button/link
actions in the DOM, 87 distinct controls; 62 of them are per-row copy buttons
of the default "Nearby strings" tab), and the product owner's decisions in
chat on 2026-09-02 recorded below.

**Research:** `docs/llm-first/research/2026-09-02-producer-editor-pareto-research.md`
(live inventory, the three scout reports, verified facts, and the full
recommendation including the deferred waves).

**Status:** waves 0 and 1 approved on 2026-09-02 (second round: every wave 0
and wave 1 proposal, including the ones first parked as deferred); wave 2
items approved individually (see "Waves"). Not started. Items outside the
approved list are kept in "Deferred" at the end and need their own approval.

**Out of scope:** Zen mode (the 2026-09-01 research decision "leave Zen
untouched" stands), a persistent Producer/Advanced mode toggle, judge prompt or
schema changes (the rationale stays in the judge's own language; the
back-translation already gives the producer Russian text), automatic
acceptance of candidates, batch triage, keyboard shortcuts for triage, and any
production mutation. Deployment to dev or production is listed separately and
requires explicit approval each time.

---

## Decisions recorded on 2026-09-02

| # | Decision | Consequence |
| --- | --- | --- |
| 1 | Producers are project administrators | No hiding through role permissions; template/form conditions only |
| 2 | Remove the "Has been translated" check | `WEBLATE_REMOVE_CHECK` gains `weblate.checks.consistency.TranslatedCheck` |
| 3 | Hide the Screenshots card | Rendered only when the string already has screenshots |
| 4 | Hide the Suggest button | Rendered only for a user without `unit.edit` (their only action) |
| 5 | Remove the "Automatically translated" item | Template block, JS handler, `js-dismiss-automatically-translated` view/URL and their tests go |
| 6 | Take the "Automatic suggestions" tab out of the main view | Moved into a "More" dropdown tab; the card no longer links to it; the judge-guided fix lives in the card |
| 7 | Hide the review-state radios for a judged string | A manual save of changed text stores `STATE_TRANSLATED`; the judge decides the release state |
| 8 | Re-check is invisible to the producer | Manual save of changed text on a judged string queues the one-unit re-check; the manual button moves under "More actions"; the pending status stays as passive text |
| 9 | "Keep as is" is one click; the reason is optional | `JudgeResolutionForm.reason` not required; `resolve_verdict` accepts a blank reason |
| 10 | "Escalate for review" becomes "Send back to queue" | Label and badge only; the `ESCALATED` value, transitions and `judge:escalated` filter are unchanged |
| 11 | Every remaining wave 0/1 proposal is in scope (second round) | Tabs folded into "More", collapsed toolbar, reduced pager, empty cards collapsed, demoted secondary buttons, triage shortcuts and paid-request hints become Tasks 10-15 |

## Waves

The work is ordered by effort, not by value: each wave is shippable on its
own and the next one never depends on a deferred item.

| Wave | What | Code touched | Tasks | Approval |
| --- | --- | --- | --- | --- |
| 0 | Zero-code: registry and environment configuration | `dev-docker/docker-compose.yml`, `deploy/environment.example` | 1 | approved |
| 1 | Templates, forms, static JS, Russian wording; no model or task changes | `translate.html`, `judge-verdict.html`, `judge-verdict-evidence.html`, `editor.html`, `position-field.html`, `keyboard-shortcuts.html`, `forms.py`, `full.js`, `ru/django.po`, one guard in `resolve_verdict` | 2, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15 | approved |
| 2 | Small backend: automatic re-check after a manual save, candidate backfill for the backlog | `views/edit.py`, `forms.py`, new management command | 3, 8 | 3 approved (questions 2 and 3 on 2026-09-02); 8 follows from decision 6 ("the fix lives in the card") and needs one confirmation before it is run anywhere |
| - | Documentation and verification | `docs/` | 9 | with wave 1 |

Wave 0 shrank on 2026-09-02: the original proposal hid the machinery tab and
the Screenshots card through role permissions, but producers hold the
`Administration` role, which carries every permission, so both moved into
wave 1 as template conditions.

## Invariants that stay

1. Generation never mutates the target; acceptance writes `STATE_FUZZY`; only a
   fresh re-check makes the accepted text shippable
   (`docs/llm-first/plans/2026-09-01-judge-producer-triage-embed.md`, invariants 1 and 4).
2. Stale or context-drifted cards offer only "Re-check this string".
3. Every producer decision still writes its immutable Change with actor and
   resolution; only the reason text may now be empty.
4. Generate, accept and re-check keep requiring `unit.review` and
   `translation.auto`; the resolution buttons keep requiring `unit.review`.
5. No secret, prompt or response body reaches a template or a log.

## Cost contract

| Path | Paid calls | Change against today |
| --- | ---: | --- |
| Producer accepts the stored candidate | 2 judge (re-check) | unchanged |
| Producer keeps the text as is | 0 | unchanged |
| Producer edits by hand and saves changed text on a judged string | 2 judge (automatic re-check) | new: today the string stays stale until someone presses Re-check |
| Producer saves a judged string without changing the text | 0 | no re-check is queued |
| Backfill of one current unresolved critical/major without a candidate | 1 repair MT | one-off, operator-run, capped by `--limit` |

## Target card

```text
Вердикт ИИ-судьи
  [x] Отклонено   [Не публикуется]
  mistranslation: The target omits "the food", which is the key information of the line.
  > Подробности судей  (both seats, models, timestamps, back-translations)

  Предложенное исправление · 2 минуты назад
  J'étais [-juste content, c'est tout-]{+tellement content de la nourriture+}
  [ Принять исправление ]          <- primary, one click
  [ Оставить как есть ]  > причина (необязательно)
  > Ещё: Сгенерировать другой вариант · Вернуть в очередь · Перепроверить строку

  (while pending) Перепроверяем строку…   <- passive, page refreshes itself
```

---

## Task 1 (wave 0): Drop the "Has been translated" check from the registries

**Files:**

- Modify: `dev-docker/docker-compose.yml:69`
- Modify: `deploy/environment.example` (the `WEBLATE_REMOVE_CHECK=` line)

Append `weblate.checks.consistency.TranslatedCheck` to both
`WEBLATE_REMOVE_CHECK` lists, comma-separated, same style as the existing
`weblate.checks.chars.MaxLengthCheck` entry. `settings_docker.py` folds the
list into `CHECK_LIST` through `modify_env_list`
(`weblate/utils/environment.py:182`); no code changes.

Verify locally without touching the running stack:

```bash
uv run python - <<'EOF'
from weblate.utils.environment import modify_env_list
import os
os.environ["WEBLATE_REMOVE_CHECK"] = "weblate.checks.chars.MaxLengthCheck,weblate.checks.source.SourceMaxLengthCheck,weblate.checks.consistency.TranslatedCheck"
from weblate.settings_docker import CHECK_LIST
assert "weblate.checks.consistency.TranslatedCheck" not in CHECK_LIST
print("ok")
EOF
```

Applying it to the dev container needs a full `./rundev.sh` recreate (the
environment block is baked at container creation) - request approval before
running it. Production `.env` is a separate approval.

Commit: `chore(checks): drop the has-been-translated check for producers`.

## Task 2 (wave 1): Trim translator-only controls from the translate page

**Files:**

- Modify: `weblate/templates/translate.html:266-270` (Suggest),
  `:343-353` (machinery tab), `:725-749` (Automatically translated),
  `:820` (Screenshots card)
- Modify: `weblate/templates/snippets/judge-verdict.html:81-88, 175-182`
  (machinery links)
- Modify: `weblate/static/editor/full.js:538-560` (dismiss handler)
- Modify: `weblate/urls.py:958-962`, `weblate/trans/views/js.py:138-160`
  (`dismiss_automatically_translated`)
- Test: `weblate/trans/tests/test_edit.py:426-470, 479-482`,
  `weblate/trans/tests/test_judge_views.py:2828-2844`

Tests first (in `test_edit.py`, `EditTest` or the class that owns the existing
`test_dismiss_automatically_translated`):

- `test_suggest_button_hidden_for_editor`: a user with `unit.edit` gets the
  translate page without `name="suggest"`.
- `test_suggest_button_shown_without_edit`: a user with `suggestion.add` but
  without `unit.edit` still gets the Suggest button (their only action).
- `test_screenshots_card_hidden_when_empty`: a full-permission user and a
  string with no screenshots: no `Screenshots` card title; attach a screenshot
  to the source unit and the card renders.
- `test_automatically_translated_block_absent`: set
  `unit.automatically_translated = True`; the page has no
  `check-automatically-translated` element and `reverse("js-dismiss-automatically-translated", ...)`
  raises `NoReverseMatch`.
- `test_machinery_tab_in_more_dropdown`: with `machinery.view` the page has
  the `More` dropdown containing `id="toggle-machinery"` with
  `data-load="machinery"`; without `machinery.view` neither the dropdown nor
  the `#machinery` pane renders.
- Update `test_judge_views.py:2828-2844`: the card no longer renders
  `Computer-aided translation suggestions` in either state; the assertions flip
  to `assertNotContains` and the "unrelated keyboard shortcuts help entry"
  comment goes with them.

Implementation:

- Suggest: change `{% if unit.translation.enable_suggestions %}` to
  `{% if unit.translation.enable_suggestions and not user_can_translate %}`,
  keeping the existing `disabled`/`title` logic for the remaining case.
- Screenshots: change `{% if screenshots or user_can_add_screenshot %}` to
  `{% if screenshots %}`; keep the footer links (Add / Manage) inside the card
  as they are, so a string that has screenshots keeps its management path.
  The component page keeps :guilabel:`Screenshots` for adding the first one.
- Automatically translated: delete the block `translate.html:725-749`, the
  `.dismiss-automatically-translated` handler in `full.js`, the URL, the view,
  and the three `test_dismiss_automatically_translated*` tests plus its entry
  in the URL tuple at `test_edit.py:479-482`. Leave the
  `Unit.automatically_translated` field alone: `autotranslate.py` still sets
  it and `Unit.translate` still clears it.
- Machinery tab: replace the `<li class="nav-item">` at `translate.html:344-352`
  with a Bootstrap 5 dropdown tab:

  ```django
  {% if user_can_use_machinery %}
    <li class="nav-item dropdown">
      <a class="nav-link dropdown-toggle" data-bs-toggle="dropdown" href="#" role="button" aria-expanded="false">{% translate "More" %}</a>
      <ul class="dropdown-menu">
        <li>
          <a class="dropdown-item"
             data-bs-target="#machinery"
             data-bs-toggle="tab"
             id="toggle-machinery"
             data-load="machinery"
             href="#"
             title="{% translate "Computer-aided translation suggestions" %}">{% translate "Automatic suggestions" %}</a>
        </li>
      </ul>
    </li>
  {% endif %}
  ```

  Bootstrap 5 activates a `.dropdown-item[data-bs-toggle="tab"]` like any tab
  trigger; `full.js:275-311` lazy-loads on `data-load="machinery"` and the
  Ctrl+M shortcut targets `#toggle-machinery`, both unchanged. Verify both in
  the browser on the dev instance (already running, no restart needed since
  templates are live-mounted).
- Card links: delete both `Automatic suggestions` `<a class="btn btn-link">`
  blocks in `judge-verdict.html` (`:81-88`, `:175-182`). The no-candidate state
  keeps `Generate suggested fix`; minor verdicts keep evidence only.

Verify:

```bash
uv run pytest weblate/trans/tests/test_edit.py weblate/trans/tests/test_judge_views.py -k "suggest or screenshot or automatically or machinery or CardRender" -q
uv run prek run --files weblate/templates/translate.html weblate/templates/snippets/judge-verdict.html weblate/static/editor/full.js weblate/urls.py weblate/trans/views/js.py
```

Commit: `feat(editor): trim translator-only controls from the translate page`.

## Task 3 (wave 2): Hide the review radios for a judged string and re-check a manual fix automatically

**Files:**

- Modify: `weblate/trans/forms.py:703-788` (`TranslationForm.__init__`)
- Modify: `weblate/trans/views/edit.py:881-963` (`perform_translation`),
  `:1467-1488` (form construction order in `translate`)
- Modify: `weblate/templates/translate.html:252-264` (save buttons area)
- Test: `weblate/trans/tests/test_judge_views.py` (new class
  `JudgeManualSaveTest(ViewTestCase)` next to `JudgeProducerTriageViewTest`)

Tests first:

- `test_review_radios_hidden_when_judged`: a reviewer opens a string with a
  current REJECT verdict; the response has no `review_radio` element and has a
  hidden `name="review"` input whose value is `STATE_TRANSLATED`; the commit
  policy footnote ("are not written to the translation file") is absent.
- `test_review_radios_shown_when_not_judged`: same user, a string without any
  judge round; the radios render as today.
- `test_review_hidden_keeps_approved_on_unchanged_save`: an approved (30)
  judged string saved without changing the text stays 30 and queues nothing
  (`JudgeRun.objects.filter(requested_mode="recheck").count() == 0`).
- `test_manual_save_of_changed_text_recheck`: reviewer with `translation.auto`
  posts a changed target on a string with a current REJECT; the unit is
  `STATE_TRANSLATED`, exactly one `JudgeRun(requested_mode="recheck",
  requested_query="id:<unit.pk>")` exists, `queue_judge_recheck` was called
  once (mock), the response redirects to `next_unit_url`.
- `test_manual_save_of_approved_changed_text_demotes_to_translated`: the same
  on a 30 string: state becomes 20, one re-check queued.
- `test_manual_save_unjudged_string_no_recheck`: no judge round, changed text:
  no `JudgeRun`, state as posted.
- `test_manual_save_without_translation_auto_no_recheck`: `unit.review`
  without `translation.auto`: text saved, no run, one `messages.info`
  explaining the string awaits a re-check by someone who may run the judge.
- `test_manual_save_recheck_configuration_error`: `validate_judge_configuration`
  raising `JudgeError` does not block the save; an error message is shown and
  no run exists.

Implementation:

- `TranslationForm.__init__(self, user, unit, *args, judged: bool = False, **kwargs)`.
  After the existing `if user_can_review or not user_can_edit:` branch, add:

  ```python
  if judged and user_can_edit and user_can_review and not unit.readonly:
      # A judged string takes its release state from the judge, not from
      # a human radio choice: the producer saves text, the re-check
      # decides. An untouched approved string keeps its state on a no-op
      # save; anything below translated is lifted to translated.
      self.fields["review"].widget = forms.HiddenInput()
      self.initial["review"] = max(unit.state, STATE_TRANSLATED)
      self.fields["review"].help_text = ""
  ```

  Import `STATE_TRANSLATED` if it is not already imported in `forms.py`.
- `translate` view: compute `judge_context = _judge_view_context(request, unit)`
  before the form and pass `judged=judge_context["judge_round"] is not None`
  (any judge round, including stale ones: an edited text of a once-judged
  string is unjudged text). `handle_translate` keeps constructing the POST form
  without the flag; validation does not depend on the widget.
- `perform_translation`: before `unit.translate(...)`:

  ```python
  judged = latest_round(unit) is not None
  target_changed = form.cleaned_data["target"] != unit.get_target_plurals()
  state = form.cleaned_data["state"]
  if judged and target_changed and state > STATE_TRANSLATED:
      state = STATE_TRANSLATED
  ```

  and pass `state` to `unit.translate`. After `saved` is confirmed and before
  the subscription messages:

  ```python
  if judged and target_changed:
      _queue_manual_save_recheck(request, unit)
  ```

  with one helper in `views/edit.py` that mirrors `judge_recheck`
  (`edit.py:1932-1953`) without raising: no `unit.review` +
  `translation.auto` -> `messages.info(...)` that the changed text awaits a
  judge re-check; `validate_judge_configuration()` failure ->
  `messages.error(str(error))`; otherwise `queue_judge_recheck(unit, user)`
  and `messages.info(_("The changed text has been queued for a judge re-check."))`.
  `queue_judge_recheck` dispatches on commit and dedupes an active run
  (`judge_loop.py:1982-2047`), so the view's `@transaction.atomic` is safe.
- Template: below the save buttons, when `judge_round` is set and the user can
  translate, one muted line:
  `{% translate "Changed text is re-checked by the judge automatically." %}`.
  Button labels stay `Save and continue` / `Save and stay`.

Verify:

```bash
uv run pytest weblate/trans/tests/test_judge_views.py -k "ManualSave" -q
uv run pytest weblate/trans/tests/test_edit.py -q
```

Commit: `feat(judge): re-check a manual fix automatically and hide review radios`.

## Task 4 (wave 1): One-click "Keep as is" with an optional reason, and rename escalation

**Files:**

- Modify: `weblate/trans/forms.py:1438-1457` (`JudgeResolutionForm`)
- Modify: `weblate/trans/models/judge.py:1188-1190` (`resolve_verdict` blank
  reason guard)
- Modify: `weblate/trans/views/edit.py:1339-1357, 1378-1406`
  (expose `judge_resolution_choices`)
- Modify: `weblate/templates/snippets/judge-verdict.html:106-119, 185-206`
- Test: `weblate/trans/tests/test_judge.py:280-292, 761-765`,
  `weblate/trans/tests/test_judge_views.py:973-1200` (`JudgeResolutionViewTest`)

Tests first:

- Replace `test_blank_reason_rejected` with `test_blank_reason_accepted`:
  `resolve_verdict(..., reason="")` succeeds, `resolution_reason == ""`, the
  Change details carry `"reason": ""`; remove the `blank_reason`
  localization row at `test_judge.py:761-765`.
- Replace `test_blank_reason_stays_on_the_current_unit`
  (`test_judge_views.py:1126`) with `test_keep_as_is_one_click`: POST
  `{"resolution": "accepted_as_is", "next": ..., "success_next": ...}` with no
  `reason` key resolves the verdict, sets `STATE_TRANSLATED`, redirects to
  `success_next`.
- `test_keep_as_is_with_reason` keeps the reason round trip.
- `test_card_renders_resolution_buttons_per_transition`: fresh REJECT renders
  both `Keep as is` and `Send back to queue`; an already `escalated` verdict
  renders `Keep as is` only; a resolved one renders neither; no `<select>` in
  the card.
- Existing tests asserting the strings `Escalate for review` /
  `Escalated for review` switch to `Send back to queue` / `Sent back to queue`.

Implementation:

- `JudgeResolutionForm`: `reason = forms.CharField(required=False, ...)`,
  label `gettext_lazy("Reason (optional)")`; the `ESCALATED` choice label
  becomes `gettext_lazy("Send back to queue")`. Keep the `ChoiceField`; the
  template now posts it as a hidden input per button.
- `resolve_verdict`: delete the `blank_reason` guard (`judge.py:1188-1190`);
  normalize `reason = reason.strip()`.
- `_judge_view_context`: add `"judge_resolution_choices": judge_resolution_choices`
  to the returned dict (the set already exists at `edit.py:1339-1346`).
- Card (`judge-verdict.html`): replace the single form at `:185-206` with

  ```django
  {% if judge_can_resolve %}
    {% if "accepted_as_is" in judge_resolution_choices %}
      <form method="post" action="{% url 'resolve-judge-verdict' pk=judge_current_verdict.pk %}" class="judge-keep-form">
        {% csrf_token %}
        <input type="hidden" name="next" value="{{ this_unit_url }}" />
        <input type="hidden" name="success_next" value="{{ next_unit_url }}" />
        <input type="hidden" name="resolution" value="accepted_as_is" />
        <button type="submit" class="btn btn-outline-secondary">{% translate "Keep as is" %}</button>
        <details class="mt-2">
          <summary class="text-muted">{% translate "Add a reason (optional)" %}</summary>
          <textarea class="form-control" name="reason" rows="2" aria-label="{% translate "Reason (optional)" %}"></textarea>
        </details>
      </form>
    {% endif %}
  {% endif %}
  ```

  and the `escalated` counterpart (`Send back to queue`, its own optional
  reason) rendered inside the "More actions" `<details>` introduced in Task 5.
  The badge at `:110` becomes `{% translate "Sent back to queue" %}`.

Verify:

```bash
uv run pytest weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_views.py -k "Resolution or reason or keep_as_is" -q
```

Commit: `feat(judge): make keep-as-is a one-click decision`.

## Task 5 (wave 1): Reorder the card around the reason and the fix

**Files:**

- Modify: `weblate/trans/models/judge.py` (new `JudgeVerdict.primary_error`
  property next to `verdict`, `:779-789`)
- Modify: `weblate/templates/snippets/judge-verdict.html` (whole card body),
  `weblate/templates/snippets/judge-verdict-evidence.html:4-6`
- Test: `weblate/trans/tests/test_judge.py`,
  `weblate/trans/tests/test_judge_views.py:2684-2900`
  (`JudgeVerdictCardRenderTest`)

Tests first:

- `test_primary_error_picks_max_severity`: errors
  `[{"category": "style", "severity": "minor", ...}, {"category": "mistranslation", "severity": "critical", ...}]`
  with `max_severity == "critical"` returns the critical one; an empty list
  returns `None`; ties return the first listed.
- Card render tests (extend `JudgeVerdictCardRenderTest`):
  - the rejected card shows the primary error description outside `<details>`
    and `Both judges reviewed this string independently.` only inside it;
  - the DOM order for a current candidate is: status heading, primary error,
    evidence `<details>`, candidate diff, `Use suggested fix`, `Keep as is`,
    then one `<details>` with summary `More actions` containing
    `Generate another`, `Send back to queue`, `Re-check this string`;
  - the no-candidate state renders `Generate suggested fix` as the only
    `btn-primary` and `Keep as is` outside "More actions";
  - `judge_recheck_pending` renders `Re-checking this string…` directly under
    the status heading and no candidate/generate/re-check forms
    (existing behaviour, now asserted by position);
  - stale/context-drift renders only `Re-check this string`, outside any
    `<details>` (unchanged behaviour, must stay).

Implementation:

- `JudgeVerdict.primary_error`:

  ```python
  @property
  def primary_error(self) -> dict | None:
      """The first recorded error at the verdict's own maximum severity."""
      for error in self.errors:
          if error.get("severity") == self.max_severity:
              return error
      return self.errors[0] if self.errors else None
  ```

- `judge-verdict.html`, in the `reject` and `flag` branches, after the heading
  and before the evidence include:

  ```django
  {% with error=judge_verdict.primary_error %}
    {% if error %}
      <p class="list-group-item-text"><strong>{{ error.category }}</strong>: {{ error.description }}</p>
    {% endif %}
  {% endwith %}
  ```

  Move the `Both judges reviewed this string independently.` paragraph into
  `judge-verdict-evidence.html` as the first line under `<summary>`, guarded
  by `{% if seats|length > 1 %}`.
- Restructure the action area of the card into exactly three visible slots
  and one `<details class="list-group-item-text"><summary>{% translate "More actions" %}</summary>…</details>`:
  `Use suggested fix` (candidate state) or `Generate suggested fix`
  (no-candidate state) first; `Keep as is` second (Task 4); `Generate another`,
  `Send back to queue`, `Re-check this string` inside "More actions". Keep the
  existing state guards (`judge_active_max_length`, `judge_recheck_pending`,
  `judge_generation_pending`, `judge_can_triage`, `judge_can_resolve`) exactly
  as they are; only the position of the markup changes. The unparsed-round
  card (`:222-252`) keeps `Re-check this string` visible: it is the only action
  there.
- Heading text stays `AI judge verdict`; Russian wording comes in Task 7.

Verify:

```bash
uv run pytest weblate/trans/tests/test_judge.py -k primary_error -q
uv run pytest weblate/trans/tests/test_judge_views.py -k "CardRender or Resolution" -q
uv run prek run --files weblate/templates/snippets/judge-verdict.html weblate/templates/snippets/judge-verdict-evidence.html
```

Commit: `feat(judge): lead the verdict card with the reason and the fix`.

## Task 6 (wave 1): Refresh the page while a paid action is pending

**Files:**

- Modify: `weblate/templates/snippets/judge-verdict.html:6` (card root)
- Modify: `weblate/static/editor/full.js` (new block near the machinery
  loader, `:275-311`)
- Test: `weblate/trans/tests/test_judge_views.py` (`JudgeVerdictCardRenderTest`)

Tests first:

- `test_card_marks_pending_generation`: `judge_generation_pending` renders
  `id="id_judge_card"` with `data-judge-pending="1"`.
- `test_card_marks_pending_recheck`: an active QUEUED re-check run renders the
  same attribute.
- `test_card_not_marked_when_idle`: neither pending -> attribute absent.

Implementation:

- Template: `<div class="card" id="id_judge_card" {% if judge_generation_pending or judge_recheck_pending %}data-judge-pending="1"{% endif %}>`.
- `full.js` (CSP is `script-src 'self'`, so no inline script):

  ```javascript
  const judgeCard = document.getElementById("id_judge_card");
  if (judgeCard && judgeCard.dataset.judgePending === "1") {
    window.setTimeout(() => {
      const editors = document.querySelectorAll("textarea.translation-editor");
      const untouched = Array.from(editors).every((el) => el.value === el.defaultValue);
      if (untouched) {
        window.location.reload();
      }
    }, 5000);
  }
  ```

  One reload per page load; the next load re-arms it while still pending. An
  editor with unsaved typing is never reloaded from under the producer.

Verify: view tests above; in the browser on the dev instance, press
`Generate suggested fix` on a judged string and observe the card switching to
the candidate without a manual refresh.

Commit: `feat(judge): refresh the string page while a judge action is pending`.

## Task 7 (wave 1): Russian wording for the card

**Files:**

- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Test: `weblate/trans/tests/test_judge_views.py` (new
  `JudgeCardLocalizationTest`, modeled on the existing
  `JudgeSeverityVocabularyLocalizationTest` in `weblate/trans/tests/test_judge.py`)

Tests first: with `translation.override("ru")` the rejected card with a
candidate contains `Вердикт ИИ-судьи`, `Отклонено`, `Не публикуется`,
`Предложенное исправление`, `Принять исправление`, `Оставить как есть`,
`Ещё`, `Перепроверить строку`; the pending state contains
`Перепроверяем строку…`; the escalation button contains `Вернуть в очередь`.

Implementation: add or update `msgstr` for every card string (existing
entries with empty `msgstr` and the new ones from Tasks 2-6):

| msgid | msgstr |
| --- | --- |
| AI judge verdict | Вердикт ИИ-судьи |
| Rejected | Отклонено |
| Questionable | Сомнительно |
| Accepted | Принято |
| Both judges reviewed this string independently. | Оба судьи проверили строку независимо. |
| Judge details | Подробности судей |
| Back-translation: | Обратный перевод: |
| Suggested fix | Предложенное исправление |
| Judge-guided repair · %(when)s | Исправление по вердикту судьи · %(when)s |
| Use suggested fix | Принять исправление |
| Generate suggested fix | Сгенерировать исправление |
| Generate another | Сгенерировать другой вариант |
| Generating a suggested fix… | Готовим исправление… |
| Keep as is | Оставить как есть |
| Add a reason (optional) | Указать причину (необязательно) |
| Reason (optional) | Причина (необязательно) |
| Send back to queue | Вернуть в очередь |
| Sent back to queue | Возвращено в очередь |
| Accepted as-is | Оставлено как есть |
| More actions | Ещё |
| More | Ещё |
| Re-check this string | Перепроверить строку |
| Re-checking this string… | Перепроверяем строку… |
| Changed text is re-checked by the judge automatically. | Изменённый текст судья перепроверит автоматически. |
| The changed text has been queued for a judge re-check. | Изменённый текст поставлен в очередь на перепроверку судьёй. |
| Relates to a previous version | Относится к предыдущей версии |
| The judge's opinion below was recorded for an earlier version of this translation; it may no longer apply. | Мнение судьи ниже записано для более ранней версии перевода и может быть неактуально. |
| Special characters | Специальные символы |
| Paid model request | Платный запрос к модели |
| Accepting queues one judge re-check. | После принятия строка уходит на одну перепроверку судьёй. |
| Apply the suggested judge fix. | Принять исправление, предложенное судьёй. |
| Keep the current text as is. | Оставить текущий текст как есть. |
| Re-check this string with the judge. | Перепроверить строку судьёй. |

Tasks 11 and 15 land after this task; their strings are listed here so the
`.po` file is touched once more at the end of wave 1, not on every commit.

Keep the existing `Will not ship` -> `Не публикуется` and `Ships with
evidence` -> current translation. Compile for the host tests with
`DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py compilemessages -l ru`
(`.mo` files are gitignored). The running dev container needs
`./rundev.sh compilemessages` - a mutation of the shared stack; request
approval before running it.

Verify:

```bash
uv run pytest weblate/trans/tests/test_judge_views.py -k Localization -q
uv run prek run --files weblate/locale/ru/LC_MESSAGES/django.po
```

Commit: `i18n(judge): translate the producer verdict card into Russian`.

## Task 8 (wave 2): Backfill stored candidates for the existing backlog

**Files:**

- Create: `weblate/trans/management/commands/judge_backfill_candidates.py`
- Test: `weblate/trans/tests/test_commands.py` (next to the existing
  `Judge*` command tests)
- Modify: `docs/admin/management.rst` (new `judge_backfill_candidates` entry
  next to `judge_release_advisory_holds`)

Tests first (mock `weblate.trans.judge_loop.repair_targets` so no provider is
called):

- dry run (default) lists each current unresolved REJECT/FLAG unit without a
  candidate and stores nothing; `--write` without `--user` is a `CommandError`.
- `--write --user admin` stores exactly one candidate per listed unit, calls
  the repair engine once per unit, reports the outcome code per unit and a
  final tally.
- a unit with an existing active candidate is reported as `existing` and
  costs no call; an active `max-length` unit is skipped as `max-length`; a
  resolved verdict is skipped; a `minor`/pass unit is not listed at all.
- `--limit 1` stops after one paid call.
- `--all` and `--file-format` are refused like in `judge_release_advisory_holds`.

Implementation: subclass `WeblateComponentCommand` following
`judge_release_advisory_holds.py` (dry-run by default, explicit scope). For
every unit in scope with `current_verdict(unit)` unresolved and in
`{REJECT, FLAG}`, call
`generate_candidate_for_verdict(unit_id=unit.pk, verdict_id=verdict.pk, user=actor, replace=False)`
synchronously and print `<unit id> <context> <outcome>`; stop at `--limit`
paid attempts (`generated`, `failed`, `drift` count as paid; `existing`,
`stale`, `resolved`, `max-length`, `busy`, `denied` do not). The actor must
hold `unit.review` and `translation.auto` (enforced inside `_generate_candidate`).

Running it against the dev instance (2 units in `col4/data/fr` today) or
production is a paid mutation and needs explicit approval per run.

Verify:

```bash
uv run pytest weblate/trans/tests/test_commands.py -k Judge -q
```

Commit: `feat(judge): add a candidate backfill command for the backlog`.

## Task 10 (wave 1): Fold the remaining translator tabs into "More" and open no tab by default

**Files:**

- Modify: `weblate/templates/translate.html:285-371` (tab headers),
  `:373-600` (tab panes: only the `active` class on `#nearby`)
- Test: `weblate/trans/tests/test_edit.py` (the class extended in Task 2)

Tests first:

- `test_bottom_tabs_reduced`: `Nearby strings` and `History` are direct
  `nav-link` items; `Suggestions` is a direct item only when
  `unit.suggestions` is non-empty; `Similar keys`, `Variants`,
  `Other occurrences`, `Comments`, `Other languages` and
  `Automatic suggestions` are `dropdown-item` entries of the single `More`
  dropdown, each keeping its `id="toggle-…"` and `data-bs-target`.
- `test_no_tab_open_on_load`: the response contains no `nav-link active` and
  no `tab-pane active` inside `.translation-tabs` / its `.tab-content`.
- `test_more_dropdown_absent_when_empty`: a unit with no similar keys, no
  variants, no other occurrences, a user without `comment.add` and
  `machinery.view`, and a single-language component renders no `More`
  dropdown at all (Other languages still counts: it is present whenever the
  component has other languages, so this case needs a one-language project).

Implementation:

- Extend the dropdown introduced in Task 2 so that every conditional tab
  header from `translate.html:294-353` and the `Other languages` header
  (`:354-362`) become `<li><a class="dropdown-item" …>` entries in the same
  `<ul class="dropdown-menu">`, preserving their existing conditions, ids,
  `data-bs-target`, `data-load`, `data-href`, titles and badges verbatim.
  Wrap the whole `<li class="nav-item dropdown">` in one condition that is
  true when at least one entry renders.
- Remove `active` from the `Nearby strings` link (`:287`) and from
  `<div class="tab-pane active" id="nearby">` (`:375`). Bootstrap 5 tabs
  work without an initially shown pane; the first click shows one.
- `full.js` needs no change: `Ctrl+U`, `Ctrl+J`, `Ctrl+M` select
  `.nav [data-bs-target="#…"]` (`full.js:186-198`) and a dropdown item is
  inside `.nav`; the machinery lazy loader keys on `data-load`, the other
  languages loader on `data-href`; the URL-hash tab opener in
  `loader-bootstrap.js` targets by `data-bs-target` too. Verify all four in
  the browser.

Verify:

```bash
uv run pytest weblate/trans/tests/test_edit.py -k "tabs or tab_open or more_dropdown" -q
uv run prek run --files weblate/templates/translate.html
```

Commit: `feat(editor): fold translator tabs into a More dropdown`.

## Task 11 (wave 1): Collapse the special-characters toolbar behind one button

**Files:**

- Modify: `weblate/trans/forms.py:276-278` (`TOOLBAR_TEMPLATE`),
  `:474-512` (`get_toolbar`)
- Modify: `weblate/static/styles/main.css:383-387` (`.editor-toolbar`) only if
  the collapsed wrapper needs a rule
- Test: `weblate/trans/tests/test_forms.py` (or wherever `get_toolbar` /
  `specialchar` is currently asserted: `grep -rn specialchar weblate/trans/tests`)

Tests first:

- `test_toolbar_collapsed_by_default`: the rendered editor contains one
  `button` with `data-bs-toggle="collapse"`, `aria-expanded="false"` and a
  `data-bs-target` pointing at a `div.collapse.btn-toolbar` that holds every
  `.specialchar` button; the toggle id embeds `unit.checksum` and the plural
  index so two units on one Zen page never share an id.
- `test_toolbar_rtl_toggle_outside_collapse`: for an RTL language the
  direction toggle from `get_rtl_toolbar` renders outside the collapsed
  group (it changes the editor, not the text).
- Existing tests asserting the `specialchar` buttons and their `data-value`
  keep passing unchanged.

Implementation: `TOOLBAR_TEMPLATE` becomes a wrapper with the toggle and the
collapsible group:

```python
TOOLBAR_TEMPLATE = """
<div class="float-end editor-toolbar">
<button type="button" class="btn btn-outline-primary btn-sm" data-bs-toggle="collapse" data-bs-target="#{0}" aria-expanded="false" aria-controls="{0}" title="{1}" tabindex="-1">…</button>
<div class="collapse btn-toolbar" id="{0}">{2}</div>
</div>
"""
```

`get_toolbar` passes `f"toolbar-{unit.checksum}-{idx}"`, the translated title
`gettext("Special characters")`, and `groups`. `base.js` inserts characters
through a delegated `.specialchar` click handler, so the buttons keep working
inside the collapse. Zen uses the same widget and inherits the change.

Verify:

```bash
uv run pytest weblate/trans/tests/test_forms.py -k toolbar -q
uv run prek run --files weblate/trans/forms.py
```

Commit: `feat(editor): collapse the special characters toolbar`.

## Task 12 (wave 1): Reduce the pager to previous, position, next

**Files:**

- Modify: `weblate/templates/snippets/position-field.html:5-58, 82-137`
- Modify: `weblate/static/editor/full.js:131-158` (`alt+home` / `alt+end`)
- Test: `weblate/trans/tests/test_edit.py`

Tests first:

- `test_pager_reduced`: the translate page renders `button-prev`,
  `position-input` and `button-next` and none of `button-first`,
  `button-end`, `prev-section`, `next-section`; the pager root carries
  `data-first-url` and `data-last-url` equal to `first_unit_url` /
  `last_unit_url`.
- `test_pager_first_and_last_disabled_states`: on the first unit `button-prev`
  has `disabled`; on the last unit `button-next` has `disabled` (unchanged
  behaviour, now the only boundary signal).

Implementation:

- Delete the `button-first`, `prev-section`, `next-section` and `button-end`
  anchors in both branches of the snippet; keep `button-prev`, the position
  widgets and `button-next` exactly as they are. Add
  `data-first-url="{{ first_unit_url }}" data-last-url="{{ last_unit_url }}"`
  to the root `div.unit-pagination` (empty when there is no previous/next
  unit, mirroring today's disabled buttons).
- `full.js:131-158`: `alt+home` / `alt+end` read
  `document.querySelector(".unit-pagination")?.dataset.firstUrl` /
  `.lastUrl` and navigate when non-empty. The shortcuts dialog entries
  (`keyboard-shortcuts.html:34-44`) stay valid.
- `browse.html` includes the same snippet with `is_in_browse`; it loses first
  and last too, which is acceptable: the position input jumps anywhere.

Verify:

```bash
uv run pytest weblate/trans/tests/test_edit.py -k pager -q
uv run prek run --files weblate/templates/snippets/position-field.html weblate/static/editor/full.js
```

Commit: `feat(editor): reduce the pager to previous, position and next`.

## Task 13 (wave 1): Collapse the Glossary and String information cards when empty

**Files:**

- Modify: `weblate/templates/translate.html:768-818` (Glossary card),
  `:851-960` (String information card)
- Modify: `weblate/static/editor/full.js:700-735` (glossary add success)
- Test: `weblate/trans/tests/test_edit.py`

Tests first:

- `test_glossary_card_collapsed_when_no_terms`: a string without glossary
  matches renders the Glossary header as a collapse toggle with
  `aria-expanded="false"` and its body inside `div.collapse` without `show`;
  with a matching term the body has `collapse show` and the toggle
  `aria-expanded="true"`.
- `test_string_info_card_collapsed_when_empty`: no explanation, no labels,
  no flags: body collapsed; any of the three present: body shown.
- The existing glossary-add view tests keep passing; the JSON contract of
  `js-add-glossary` is untouched.

Implementation: reuse the accordion markup already used for `Details`
(`translate.html:941-953`): the `h4.card-title` becomes a
`button.accordion-button` with `data-bs-toggle="collapse"` targeting the
card's `.list-group`, `collapsed` and `aria-expanded` set from
`{% if glossary %}` (Glossary) and
`{% if unit.source_unit.explanation or unit.all_labels or unit.all_flags %}`
(String information; use the exact context names the card already reads at
`:858-940`). Ids embed `unit.id` like the existing accordion. In `full.js`,
after a successful glossary add (`responseCode === 200`, `:716`), call
`bootstrap.Collapse.getOrCreateInstance(document.getElementById("glossary-card-body-<unit.id>")).show()`
so the new term is visible immediately; read the id from a `data-glossary-body`
attribute on the form rather than hard-coding it.

Verify:

```bash
uv run pytest weblate/trans/tests/test_edit.py -k "glossary_card or string_info" -q
uv run prek run --files weblate/templates/translate.html weblate/static/editor/full.js
```

Commit: `feat(editor): collapse empty glossary and string information cards`.

## Task 14 (wave 1): Demote "Save and stay"

**Files:**

- Modify: `weblate/templates/translate.html:259-264`
- Test: `weblate/trans/tests/test_edit.py`

Test first: `test_save_and_stay_is_secondary`: the `name="save-stay"` button
has class `btn-link` and not `btn-primary`; `name="save"` stays the only
`btn-primary` in the editor footer; `Skip` keeps `btn-link` (unchanged at
`:271`).

Implementation: change `class="btn btn-primary btn-spaced"` to
`class="btn btn-link btn-spaced"` on the `save-stay` button; keep the
`disabled` conditions. No behaviour change; `Ctrl+Enter` still submits
`save`.

Verify: `uv run pytest weblate/trans/tests/test_edit.py -k save_and_stay -q`.

Commit: `feat(editor): demote save and stay to a secondary action`.

## Task 15 (wave 1): Triage keyboard shortcuts and paid-request hints

**Files:**

- Modify: `weblate/static/editor/full.js:131-198` (hotkeys block)
- Modify: `weblate/templates/keyboard-shortcuts.html` (three new rows)
- Modify: `weblate/templates/snippets/judge-verdict.html` (hints)
- Test: `weblate/trans/tests/test_judge_views.py` (`JudgeVerdictCardRenderTest`)

Tests first:

- `test_paid_hint_on_paid_buttons`: `Generate suggested fix`,
  `Generate another` and `Re-check this string` each render with
  `title="Paid model request"` and a sibling
  `<small class="text-muted">Paid model request</small>`; `Use suggested fix`
  and `Keep as is` carry neither (accepting a stored candidate queues the
  re-check, which is already declared on the candidate block in one line:
  `Accepting queues one judge re-check.`).
- `test_shortcut_targets_present`: the three triage forms carry stable ids
  `id_judge_accept_form`, `id_judge_keep_form`, `id_judge_recheck_form` so
  the JS can find them.
- The shortcuts dialog test (if one exists; otherwise a render test of
  `keyboard-shortcuts.html`) lists the three new rows.

Implementation:

- `full.js`, next to the existing bindings (same `hotkeys("…", () => {…; return false;})` shape):
  `ctrl+alt+a,command+alt+a` submits `#id_judge_accept_form`;
  `ctrl+alt+k,command+alt+k` submits `#id_judge_keep_form`;
  `ctrl+alt+r,command+alt+r` submits `#id_judge_recheck_form`; each is a
  no-op when the form is absent. `Ctrl+Alt` avoids every browser-owned
  `Ctrl+Shift+letter` and the macOS `Option+letter` dead keys; the Russian
  layout has no `AltGr` characters. If the vendored `hotkeys-js` filter
  swallows the combination while focus is inside the textarea, the shortcut
  still works with focus anywhere else on the page, which is the producer's
  state after reading the card; note the observed behaviour in the commit.
- `keyboard-shortcuts.html`: three rows in the existing table format
  (`<kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>A</kbd>` … "Apply the suggested
  judge fix." / "Keep the current text as is." / "Re-check this string with
  the judge."), all `{% translate %}`d and added to `ru/django.po` with Task 7
  wording.
- `judge-verdict.html`: `title="{% translate "Paid model request" %}"` and
  the `<small>` hint on the three paid buttons; the one-line note under
  `Use suggested fix`.

Verify:

```bash
uv run pytest weblate/trans/tests/test_judge_views.py -k "paid_hint or shortcut" -q
uv run prek run --files weblate/static/editor/full.js weblate/templates/keyboard-shortcuts.html weblate/templates/snippets/judge-verdict.html
```

Commit: `feat(judge): add triage shortcuts and paid-request hints`.

## Task 9 (docs): Documentation, changelog, verification

**Files:**

- Modify: `docs/guides/producer-guide-weblate.md:495-535` (the AI judge step;
  edit only this guide)
- Modify: `docs/admin/checks.rst:190-220`
- Modify: `docs/admin/management.rst` (Task 8 entry, if not done there)
- Modify: `docs/changes.rst` (top unreleased section, "Improvements")
- Review: `docs/security/threat-model.rst` - no new endpoint, no permission
  change, no new AI write path; the only change is that a recorded decision
  may carry an empty reason. Record "reviewed, no change" in the commit body.

Producer guide: rename the buttons to their Russian labels, describe the
three-action flow (Принять исправление / Оставить как есть / правка руками +
Сохранить и продолжить с автоматической перепроверкой), state that the reason
is optional, that "Автоматические предложения" and the other context tabs now
sit under "Ещё" in the bottom tabs, that review radios are gone for judged
strings, that the special-characters toolbar opens from the "…" button, that
the pager keeps only previous / position / next, that the glossary and string
information cards open when they have content, list the three triage
shortcuts, and note that the page refreshes itself while a fix is being
generated or re-checked.

`checks.rst`: `Use suggested fix` / `Generate another` wording stays;
"always needs a written reason" (`:211`) becomes "may carry an optional
written reason"; `escalated` transitions are described as "sent back to
queue" while the stored value stays `escalated`.

Changelog entries (concise, one per user-visible change):

- The LLM-judge verdict card now leads with the judge's main finding and the
  stored fix; :guilabel:`Keep as is` is a one-click decision with an optional
  reason, and escalation is labelled :guilabel:`Send back to queue`.
- Saving changed text on a judged string now queues the one-string judge
  re-check automatically and stores the text as translated; the review-state
  radios are hidden for judged strings.
- The string page hides the Suggest button for users who can edit, the
  Screenshots card until a screenshot exists, and moves
  :guilabel:`Automatic suggestions`, :guilabel:`Similar keys`,
  :guilabel:`Comments`, :guilabel:`Other languages` and the other context tabs
  under a :guilabel:`More` tab; no tab is open until chosen; the
  "Automatically translated" notice is removed.
- The translation editor now keeps the special-characters toolbar behind one
  button, reduces the pager to previous / position / next, demotes
  :guilabel:`Save and stay` to a secondary action, and collapses the Glossary
  and String information cards while they have nothing to show.
- Added :kbd:`Ctrl+Alt+A`, :kbd:`Ctrl+Alt+K` and :kbd:`Ctrl+Alt+R` for the
  LLM-judge triage actions, and a paid-request hint on every button that
  spends a model call.
- Added the ``judge_backfill_candidates`` management command.

Verification (host, isolated test database per session):

```bash
CI_DB_NAME=weblate_producer_pareto uv run pytest \
  weblate/trans/tests/test_judge.py \
  weblate/trans/tests/test_judge_views.py \
  weblate/trans/tests/test_judge_form.py \
  weblate/trans/tests/test_edit.py \
  weblate/trans/tests/test_forms.py \
  weblate/trans/tests/test_commands.py -k "not Selenium" -q
uv run prek run --files <all touched files>
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check
```

Browser pass on the already running dev instance (templates and Python are
live-mounted; no restart): open
`http://localhost:3001/translate/col4/data/fr/?q=check:judge-reject` as
`admin` and confirm the card order, the absence of radios/Suggest/Screenshots/
"Automatically translated", the `More` dropdown holding
`Автоматические предложения`, `Generate suggested fix` followed by an
automatic refresh into `Принять исправление`, and one click landing on the
next string. Count the DOM actions again with the same selector used on
2026-09-02 (`button, a.btn, input[type=submit]`) and record the number in
this plan's status line.

Commit: `docs(judge): document the producer editor reduction`.

---

## Acceptance criteria

1. A judged string shows at most three visible actions in the card (accept
   fix or generate it, keep as is, more) and two under the editor (save and
   continue as the only primary button, save and stay demoted to a link); no
   review radios, no Suggest, no "Automatically translated", no Screenshots
   card without screenshots, no special-characters toolbar until it is opened,
   no first/last/section pager buttons.
2. `Keep as is` resolves a fresh critical or major in one POST without a
   reason and advances to the next string.
3. Saving changed text on a judged string stores `STATE_TRANSLATED`, queues
   exactly one re-check, and never re-checks an unchanged save or an unjudged
   string.
4. The card's main finding and the fix diff are visible without expanding
   anything; seat details stay one click away.
5. While generation or re-check is pending, the page refreshes itself without
   discarding unsaved editor text.
6. All card strings render in Russian under `ru`.
7. `judge_backfill_candidates` stores one candidate per current unresolved
   critical/major in a scope, dry-run by default, capped by `--limit`.
8. Every hidden control remains reachable: machinery under `More`, screenshots
   on the component page, escalation and manual re-check under `More actions`.
9. No migration, no new permission, no secret or prompt in templates or logs.
10. Only `Nearby strings`, `Suggestions` (when any exist) and `History` are
    direct tabs; every other bottom tab lives in `More`; no tab pane is open
    on page load.
11. The Glossary and String information cards are collapsed when they have
    nothing to show and open when they do; adding a glossary term opens the
    card.
12. Accept / keep / re-check are reachable from the keyboard and listed in the
    shortcuts dialog; every paid button carries the paid-request hint.

## Deferred (proposed on 2026-09-02, not approved)

- Link each judge-run report to the editor with `q=check:judge-reject`
  (wave 2 item).
- A persistent Producer/Advanced toggle (third `Profile.translate_mode`
  value) once the above has been used for a while (wave 3).
- Judge rationale in Russian (prompt change).

## Deployment notes (each needs explicit approval)

- Task 1 reaches the dev instance only through a full `./rundev.sh` recreate;
  production through `/srv/hcgameloc/deploy/.env` and a container recreate.
- Task 7 reaches the dev instance through `./rundev.sh compilemessages`.
- Task 8 spends one repair MT call per backlog verdict wherever it is run.
- Templates, Python and JS changes are live on dev without a restart.
