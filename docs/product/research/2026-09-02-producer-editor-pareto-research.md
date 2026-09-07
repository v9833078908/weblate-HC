# Producer editor Pareto reduction: research and recommendation

Date: 2026-09-02. Status: research complete; the approved subset became
`docs/llm-first/plans/2026-09-02-producer-editor-pareto.md`.

Trigger: the product owner opened
`http://localhost:3001/translate/col4/data/fr/?q=check:judge-reject` and
reported that the translate page carries far more actions than a producer
needs in an LLM-first TMS without translators. The requested outcome: a
minimal, modern, understandable page where a producer opens the critical
judge findings, reads the judges' verdict, and either edits the text by hand
or accepts the judges' variant in one click; a Pareto proposal of the form
"hide this because X, collapse that, change this here".

Method: one live inventory of the running dev instance (headless browser,
read-only), three read-only scouts (fork documentation, template map, judge
verdict UI), and a manual verification of every fact the recommendation
rests on. Source marks follow
`docs/llm-first/designs/2026-09-01-02-producer-ui-reduction.md`: `[live]` is
a measurement on `localhost:3001`, `[code]` a static read with file:line,
`[doc]` a statement from a fork document.

## 1. Live inventory of the translate page

`[live]` DOM of `/translate/col4/data/fr/?q=check:judge-reject` as `admin`,
selector `button, a.btn, input[type=submit]`: **215** elements. Of them 62 are
the per-row "Скопировать в буфер обмена" / "Дублировать в перевод" pairs of the
31 rows in "Соседние строки" (the default active bottom tab), about 40 sit in
hidden modals (context edit, add unit, screenshots, glossary term). Distinct
controls for a full-permission user: **87** (TemplateMapScout count, section 3).

Regions as rendered (Russian UI, English judge card):

| Region | Controls |
| --- | --- |
| Top navbar | Поиск, Панель управления, Проекты, Языки, Проверки, wrench, alert, "+", language, "..." |
| Breadcrumb | CoL4 / data / Французский / Перевести; "переведено 99%" |
| Filter bar | pager with 6 buttons and "1 / 2", Фильтры, query `check:judge-reject`, "Позиция и приоритет", sort direction, Дзен, editor settings |
| Card "Перевод" | permalink; "Approximate reconstruction" (judge back-translation, `translate.html:111-115`); Русский source + Контекст + 2 copy icons; Французский editor with a 12-button special-characters toolbar; counter "33/260 · 26"; "Статус рецензирования" radios На правку / Ожидает рецензии / Одобрено + footnote about "На правку" not being written to the file; buttons Сохранить и продолжить, Сохранить и остаться, Предложить, Пропустить |
| Bottom tabs | Соседние строки 31, Похожие ключи 30, Комментарии, Автоматические предложения, Другие языки 3, История |
| "Автоматические предложения" panel | Память переводов search + Поиск; table Перевод / Предлагаемая правка / Источник / Происхождение (LiteLLM) / Сходство; per row: Дублировать в перевод, Принять, Принять и одобрить |
| Card "AI judge verdict" | "Rejected" + badge "Не публикуется"; "Both judges reviewed this string independently."; collapsed "Judge details"; button "Generate suggested fix"; link "Автоматические предложения"; "Решение:" select (Эскалировать на проверку / Принять как есть); "Причина:" textarea; button "Зафиксировать решение"; link "Re-check this string" |
| Card "Объекты для проверки" | "Уже переведено 1" + Скрыть + "Для всех языков"; "Переведено автоматически ... Hero Craft Admin" + Скрыть; "Фильтр качества перевода" note |
| Card "Словарь" | empty state + Добавить понятие в словарь + Обзор словаря CoL4 |
| Card "Снимки экрана" | empty state + Добавление снимков экрана + Управлять снимками экрана |
| Card "Сведения о строке" | Пояснение, Контекст, Метки, Флаги with pencil icons; Подробности |

`[live]` Expanded "Judge details" for `EVENT_113_ANSWER_201`:
`deepseek-v4-pro` "mistranslation/major: ... The target omits the reason for
the happiness, 'the food' ..." with back-translation "Я был просто доволен,
вот и всё"; `atlas/qwen3.8-max` "mistranslation/critical: ... The object
'еде' (food) is omitted ..." with back-translation "Я был просто доволен, и
всё". The second unit in the filter (`EVENT_115`, "L'amie de Julia est enfin
partie") shows the same card shape. Both cards offer "Generate suggested fix",
not "Use suggested fix".

`[live]` `Suggestion.objects.filter(userdetails__kind="judge-repair").count()`
in the dev container: **0**. Every current verdict predates the stored
candidate feature, so the one-click path is present in code but never seen on
this instance.

`[code]` `git merge-base --is-ancestor e499e7e HEAD` is true on `main`: the
stored-candidate triage plan is merged and live on the bind-mounted dev
instance.

## 2. Scout report: fork documentation (DocsTriageScout)

Read: `designs/2026-09-01-02-producer-ui-reduction.md`,
`plans/2026-09-01-judge-producer-triage-embed.md`,
`research/2026-09-01-judge-producer-triage-embed-research.md`,
`plans/2026-08-25-01-judge-producer-ux-and-delivery.md`,
`research/2026-08-11-judge-ux-competitor-research.md`,
`research/2026-08-11-judge-weblate-ui-integration.md`,
`designs/2026-08-13-judge-native-ui-design.md`,
`vision/2026-08-15-producer-first-product-research.md`,
`vision/llm-first-product-architecture.md`,
`docs/guides/producer-guide-weblate.md` (all under `docs/llm-first/` unless
stated).

### 2.1 Decided principles

- Persona: "Russian-speaking game producer who knows English but does not
  read French, Chinese, Japanese and other target languages. Their task is
  not to translate strings. Their task is to make a trust decision about
  localization and ship it without hiring translators."
  (`vision/2026-08-15-producer-first-product-research.md`, section 1)
- "They cannot fix a French string, so their action is not editing but a
  trust decision." (`designs/2026-08-13-judge-native-ui-design.md`)
- Hard constraints of the triage embed research: no new pages; a stale or
  context-changed verdict offers only Re-check; clearing `check:judge-reject`
  after a manual save is not proof of quality because nobody judged the new
  text. (`research/2026-09-01-judge-producer-triage-embed-research.md`, section 1)
- "Do not build another Weblate module and do not physically remove domain
  capabilities. Build one producer-oriented pipeline on top of existing
  mechanisms, and hide translator functionality in Linguist/Advanced mode."
  (`vision/2026-08-15-producer-first-product-research.md`, conclusion)
- Severity gating: critical holds; unresolved major ships with `judge-flag`
  evidence; minor is visible as `judge-note`.
  (`plans/2026-08-25-01-judge-producer-ux-and-delivery.md`)
- Candidate as preview, not mutation; acceptance writes `STATE_FUZZY`; only a
  fresh re-check makes it shippable; freshness is a hard state machine.
  (`plans/2026-09-01-judge-producer-triage-embed.md`, invariants 1 and 4;
  research section 4.3)

### 2.2 Plan status

| Plan | Status line | Tasks |
| --- | --- | --- |
| `2026-08-25-01-judge-producer-ux-and-delivery.md` | Tasks 1-7 implemented, merged `f91935b` | advisory shipping semantics; severity vocabulary; atomic `resolve_verdict`; queue strip and launchers; repair evidence; one summary with live phases; docs/i18n/security. Pending: Selenium coverage and a live run |
| `2026-09-01-judge-producer-triage-embed.md` | Tasks 1-11 implemented, merged `e499e7e`, 486 passed / 47 subtests, not deployed | candidate persistence; persist instead of mutate; generation retry and one-unit re-check; acceptance guard; embedded flow render; compressed two-seat evidence; named outcomes; auto-advance; conservative readiness; docs and security; verification. Migration `0118_judge_run_unit_candidate_stored`, choices-only |
| `designs/2026-09-01-02-producer-ui-reduction.md` | P0 applied on dev and prod (registration, sharing, formats 69 -> 14, machinery -> 4 services); P1/P2 not implemented | P1: hide Automatic suggestions tab, Translation memory, MT providers, Search and replace, Bulk edit, Translator reports, Similar keys / Variants / Other occurrences / Other languages, Screenshots, Announcements / widgets / exports / RSS, Comments as a working channel. P2: ZIP download -> release package |
| `designs/2026-08-13-judge-native-ui-design.md` | predates implementation; its card, collegium and gating landed through the two plans above | - |

### 2.3 Proposed UI changes versus the live page

| Proposal | Source | On the live page | Status |
| --- | --- | --- | --- |
| Hide Automatic suggestions tab in Producer mode | ui-reduction P1 | visible, active in the screenshot | pending |
| Hide Similar keys, Variants, Other occurrences, Other languages | ui-reduction P1 | Похожие ключи 30, Другие языки 3 visible | pending |
| Hide Comments as a working channel | ui-reduction P1 | Комментарии visible | pending |
| Hide Screenshots card | ui-reduction P1 | visible (empty) | pending |
| Hide Translation memory, Search and replace, Bulk edit, Translator reports, Announcements, exports | ui-reduction P1 | not on this page | pending |
| One-line summary + details for two seats | triage research Solution 1, embed plan Task 6 | collapsed "Judge details" | implemented |
| Producer-friendly outcome names | embed plan Task 7 | resolution select | implemented |
| Auto-advance after a decision | embed plan Task 8 | behavioural | implemented |
| "Use suggested fix" for a stored candidate | embed plan Tasks 1-5 | "Generate suggested fix" shown (no candidate stored yet) | implemented, not exercised on dev |
| "Re-check this string" | embed plan Task 3 | visible | implemented |
| Conservative readiness CTA | embed plan Task 9 | component level | implemented |
| De-emphasize state radios | triage research section 2.4 | prominent | partial: "cannot remove, only de-emphasize" |
| Stale card shows only Re-check | triage research Solution 1 | fresh card shown | implemented |

### 2.4 Competitor patterns already adopted

From `research/2026-08-11-judge-ux-competitor-research.md` and the triage
research section 4: Crowdin inline QA banner with an executable fix; Transifex
"Use this" replacing the target; Transifex/Lokalise staleness invalidating
scores; Crowdin/memoQ/Unbabel filter plus auto-advance; severity-first with
compact evidence (all seven products); release gate on critical with a visible
reason (Crowdin, Phrase, Smartling, memoQ); every non-fix disposition records
a reason; re-check as result replacement (Transifex, Lokalise, memoQ);
back-translation for a non-reading reviewer (Crowdin, SPF.io); multi-LLM
consensus as QE (XTM, Transifex). Partially adopted: Smartling three-tier
routing (no auto-approve threshold); Lokalise MQM penalty score (severity
shown, no number). Researcher recommendation: Option B (side QA pane with
explicit disposition) as base, plus Option A (inline executable fix) and
Option D (correction/dispute panel).

### 2.5 Explicit rejections

| Rejected | Source | Reason |
| --- | --- | --- |
| Consensus-QE (2-3x tokens) | producer-first research 4.0 | measured defects are deterministic; a score tunes a human threshold that does not exist |
| 3x same-model aggregation | judge-native design | reduces variance, not bias; second family reduces both |
| Two-step ladder (second judge only on flag/reject) | judge-native design | recall cannot exceed the first step; specificity already 97.6-99.2% |
| Second judge only as escalation regulator | judge-native design, 2026-08-19 addendum | escalation to critical is the noisiest signal |
| Severity gate as automatic guarantee | judge-native design | no run caught all 7 true critical defects; the gate means "queue for a human", not "cannot ship" |
| Labels or comments as verdict storage | weblate-ui-integration, judge-native design | wrong shape; comments stop meaning "a human said something" |
| Back-translation as a scoring signal | producer-first research, invariant 5 | RTT as evaluator is unreliable; BT stays as evidence |
| Dedicated "Edit manually" button | triage embed plan | the normal editor already covers it |
| Automatic acceptance of candidates, auto-approve by QE threshold | triage embed plan, producer-first research 4.0 | out of scope; release criterion, not labor saving |
| Phrase stale-result behaviour, Unbabel non-blocking suggestions, Lokalise/Transifex score-first UX | triage research 4.2 | unsafe, too weak, or hides why a critical was rejected |

### 2.6 Gaps the documents do not cover

Role-based mode rendering mechanism; telemetry audit before hiding P1 items;
a Producer/Advanced toggle UX; the judge card in Zen (decision: leave Zen
untouched, card does not render there); keyboard shortcuts for triage; batch
triage; producer onboarding (which permissions); narrow viewport; cost
visibility in the card; producer-facing error handling for failed generation;
release pipeline integration; producer metrics; glossary conflict resolution
in the card; undo for a decision (resolutions are immutable); cross-language
triage.

## 3. Scout report: template map (TemplateMapScout)

`[code]` `weblate/templates/translate.html` (1183 lines): breadcrumbs
`13-20`; Zen and settings `49-56`; search form `60-65`; main editor card
`67-283` (form tag `71`, `{% crispy form %}` `195`, save buttons `252-264`,
Suggest `266-270`, Skip `271-279`); bottom tabs `285-371`; tab panes
`373-600`; right sidebar `604-1100` (checks card `604-765`, Screenshots
`820-847`); modals `1100-1183`. Permission flags computed at `36-47`
(`unit.review`, `suggestion.add`, `suggestion.accept`, `suggestion.vote`,
`machinery.view`, `meta:unit.flag`, `screenshot.add`, `comment.add`,
`unit.add`).

Snippets: `snippets/judge-verdict.html` (card, 252 lines);
`snippets/judge-verdict-evidence.html` (seat details); `snippets/editor.html`
(textarea, toolbar, counter); `snippets/position-field.html` (pager, hidden in
Zen); `snippets/search-form.html`; `snippets/query-builder.html`;
`snippets/glossary.html`; `snippets/embed-units.html` (nearby / similar keys /
variants tables with per-row clone buttons); `snippets/last-changes-content.html`
(history with revert / block user / details); `list-comments.html`;
`snippets/suggestions.html` (vote, accept, accept_edit, accept_approve, delete,
spam; permission gated).

Forms: `weblate/trans/forms.py` `TranslationForm` `675-855`; review radios
`InlineRadios("review")` at `770`; choices filtered by state `732-744`, by
`unit.edit` `745-753`, radios versus fuzzy checkbox by `unit.review` `773-776`;
commit-policy footnote appended to help text `785-788`; special characters
toolbar `474-512` with `weblate/trans/specialchars.py:275` (language +
`profile.special_chars` + source).

Existing levers that already hide parts of the page: `Profile` preferences
(`weblate/accounts/models.py`) `secondary_languages` `767`,
`hide_source_secondary` `795`, `editor_link` `809`, `translate_mode` `823`
(full/zen), `zen_mode` `833`, `special_chars` `841`, `nearby_strings` `852`
(`MinValueValidator(1)`, so never zero), `auto_watch` `860`;
`translation.enable_review` (`weblate/trans/models/translation.py:1596`,
`project.py:327,332,1354`); `component.enable_suggestions`,
`component.hide_glossary_matches`, `unit.readonly`, `unit.is_source`,
`has_plural`, `locked`.

Lazy panels: machinery (`data-load="machinery"`, `full.js:275-311`), other
languages (`data-href`), history rendered server-side. Keyboard
(`full.js:131-198, 333-350, 595-640`): Alt+Home/End, Alt+PgUp/PgDn, Ctrl+O,
Ctrl+Y, Ctrl+Shift+Enter, Alt+Enter, Ctrl+E, Ctrl+S, Ctrl+U, Ctrl+J, Ctrl+M
then 1-9-0 (copy machinery result), Ctrl+I then 1-9-0 (dismiss check).

Checks card: name `622`, `{{ check.get_description }}` `629`, Fix string when
`get_fixup_json()` `631-637`, Dismiss / Reset `639-656`; "Automatically
translated" block `725-749` (gated by `unit.automatically_translated`, dismiss
via `js-dismiss-automatically-translated`); "Translation quality filter"
`752-761` (gated by `unit.is_blocked_by_commit_policy`).

Machinery buttons are rendered in JS (`full.js:816-829`): `.js-copy-machinery`
(clone, marks fuzzy), `.js-copy-save-machinery` (accept, marks translated and
submits), `.js-copy-approve-save-machinery` (accept and approve, marks approved
and submits; gated by `WLT.Config.HAS_REVIEW_WORKFLOW`), `.js-delete-machinery`
for own TM entries.

Zen (`zen.html`, `zen-units.html`) strips the sidebar, the tabs and the pager
buttons; the judge card does not render there.

Control count for a full-permission user, default page, excluding per-row
buttons: about 87 (filter bar 4, pager 7, source 2, toolbar 12-16, actions
5-9, tabs 9, judge card 3-7, checks card 10-20, glossary 3-10, screenshots
2-5, string info 4, modals 4).

## 4. Scout report: judge verdict in the editor (JudgeVerdictUiScout)

`[code]` Flow: `JudgeVerdict` (`weblate/trans/models/judge.py:642-800`;
fields `errors` JSON, `back_translation`, `instruction`, `judge_model`,
`target_hash`, `context_hash`, `resolution`, `resolution_reason`, ...) ->
check projection `BaseJudgeCheck.check_target_unit`
(`weblate/checks/judge.py:40-50`; `judge-reject` critical, `judge-flag` major,
`judge-note` minor, `JUDGE_CHECKS` at `134-136`) -> `_judge_view_context`
(`weblate/trans/views/edit.py:1313-1406`) -> `snippets/judge-verdict.html`
included from `translate.html:601`.

Card states and gates: rejected / questionable / accepted headings with
"Will not ship" / "Ships with override" / "Ships with evidence" badges;
`Both judges reviewed this string independently.` at `:50`; evidence
`<details>`; candidate block with diff and `Use suggested fix`
(`:130-162`, POST `judge-accept-candidate`, `edit.py:2033-2066`) and
`Generate another`; no-candidate block with `Generate suggested fix`
(`:163-183`, POST `judge-generate-candidate`, `edit.py:1957-2031`, Celery
`generate_judge_candidate` `tasks.py:1445-1460` ->
`generate_candidate_for_verdict` `judge_loop.py:2162-2242` -> `repair_targets`
`judge_loop.py:170-213` -> `_store_candidate` `judge_loop.py:504-598`);
resolution form (`:185-206`, `JudgeResolutionForm` `forms.py:1438-1457`,
POST `resolve-judge-verdict` `edit.py:1885-1917`); `Re-check this string`
(`:207-219`, POST `judge-recheck` `edit.py:1922-1953` -> `queue_judge_recheck`
`judge_loop.py:1982-2047`). `judge_can_triage` = `unit.review` +
`translation.auto`; `judge_can_resolve` = `unit.review` and an allowed
transition.

Acceptance: `Suggestion.accept` detects `kind == "judge-repair"` and delegates
to `accept_judge_candidate` (`judge_loop.py:2049-2154`): locks unit,
suggestion, verdict; validates metadata and hashes; `unit.translate(...,
STATE_FUZZY)` with `ActionEvents.ACCEPT`; deletes the candidate; queues the
re-check. The unit lands on `STATE_FUZZY`; the re-check decides the release
state. User steps today for a string without a stored candidate: Generate ->
wait for Celery -> reload -> Use suggested fix.

Resolution: `ALLOWED_RESOLUTION_TRANSITIONS` (`models/judge.py:1133`), six
transitions among `REJECT`/`FLAG` x `""`/`escalated` -> `escalated` /
`accepted_as_is`; `resolve_verdict` (`:1167-1260`) requires a non-blank reason
(`:1188-1190`), sets `STATE_FUZZY` (critical) or `STATE_NEEDS_CHECKING`
(major) on escalate and `STATE_TRANSLATED` on accept-as-is; it never saves a
target.

Queries: `check:judge-reject`, `check:judge-flag`, `check:judge-note`;
`judge:pass|minor|flag|reject|stale|unparsed|resolved|escalated`
(`weblate/utils/search.py:816-836`). The run report (`judge-run.html`) links
each row to its unit but does not prefill a filter.

Gap ranked by effort: (1) pre-generate or backfill candidates so
`Use suggested fix` is present at first sight; (2) a combined action that
generates and accepts; (3) auto re-check after a manual save.

## 5. Facts verified by hand before recommending

- `[code]` `weblate/locale/ru/LC_MESSAGES/django.po` translates only
  `Will not ship` and `Record decision` from the card; `AI judge verdict`,
  `Rejected`, `Judge details`, `Generate suggested fix`, `Use suggested fix`,
  `Re-check this string`, `Both judges reviewed ...` have no Russian entry.
- `[code]` `weblate/auth/data.py:199-200`: the `Administration` role is every
  permission except `workspace.*`. Producers are project administrators
  (product owner, 2026-09-02), so permission-based hiding cannot reach them.
- `[code]` `weblate/middleware.py:67`: `script-src 'self'`; any behaviour
  must live in static JS, not inline.
- `[code]` `weblate/checks/consistency.py:294-297`: `TranslatedCheck`,
  `check_id = "translated"`, is the "Уже переведено" item;
  `dev-docker/docker-compose.yml:69` already removes two checks through
  `WEBLATE_REMOVE_CHECK`.
- `[code]` The machinery tab's `Принять и одобрить` submits the normal
  translation form with state 30 (`full.js:816-829`); it is not a
  `Suggestion`, so the judge acceptance guard never sees it. [INFERENCE] After
  such a save the verdict is stale (target hash mismatch) and the string
  leaves `check:judge-reject` as approved without any re-check.
- `[code]` `handle_translate` / `perform_translation`
  (`views/edit.py:967-991, 881-963`) save through `unit.translate` with the
  form state; nothing judge-related happens on a manual save.
- `[code]` `nearby_strings` cannot be zero (`MinValueValidator(1)`), so the
  default 31-row nearby tab cannot be emptied by preference.
- `[code]` `./rundev.sh compilemessages` exists; `.mo` files are gitignored.

## 6. Recommendation as delivered on 2026-09-02

### 6.1 Diagnosis

The backend of the one-click flow exists and is merged; the page around it
is a translator's workbench. Seven distinct problems:

1. 215 actions in the DOM, 87 distinct, for a job that needs three.
2. Every current verdict predates the stored candidate, so the producer sees
   Generate -> wait without any indicator -> F5 -> Use, three steps instead of
   one.
3. The reason for the rejection and the back-translation are hidden under
   "Judge details" while a useless sentence ("Both judges reviewed this string
   independently.") is visible.
4. The card is English inside a Russian UI.
5. The machinery tab shows the same LiteLLM variant with three buttons that
   bypass the re-check contract; a second paid path and a quality hole.
6. The resolution form is a select with two options, a mandatory textarea and
   a button; its default "Эскалировать на проверку" escalates to nobody.
7. "Уже переведено" and "Переведено автоматически" fire on about 100% of
   strings in an LLM-first flow.

### 6.2 Target scenario

Open `check:judge-reject` -> see source, current text with its
back-translation, one line "what is wrong", the suggested fix as a diff ->
`Принять исправление` / `Оставить как есть` / edit by hand and
`Сохранить и продолжить` with an automatic re-check. Everything else under
"Ещё" or in an advanced view. Five seconds per string.

### 6.3 Waves by value / effort

Wave 0, zero code (about an hour): remove `TranslatedCheck` through
`WEBLATE_REMOVE_CHECK`; hide the machinery tab and the Screenshots card
through a producer role without `machinery.view` / `screenshot.add`. The role
part fell away once producers turned out to be administrators; those two items
became template conditions in wave 1.

Wave 1, templates and Russian wording (about a day, the main effect):

| Element | Action | Reason |
| --- | --- | --- |
| Judge card order | main finding -> fix diff -> Принять исправление (primary) -> Оставить как есть -> "Ещё" (Сгенерировать другой, Вернуть в очередь, Перепроверить) | the producer reads the reason and acts; seats and models are evidence, one click away |
| "Both judges reviewed ..." | into the details | no decision value |
| Resolution select + mandatory textarea + button | one button "Оставить как есть" with an optional reason; "Эскалировать" renamed "Вернуть в очередь" and moved under "Ещё" | same `STATE_FUZZY` semantics; nobody to escalate to |
| English card strings | Russian `msgstr` | one UI language |
| Manual F5 after Generate | automatic refresh while generation or re-check is pending, guarded against unsaved typing | remove the invisible wait |
| Предложить | render only for users without `unit.edit` | a producer decides, does not suggest |
| Статус рецензирования radios + footnote | hidden on a judged string; save stores `translated`; the judge sets the release state | three states only a translator understands (research 2.4 says de-emphasize, not remove: hidden field, not removed) |
| Переведено автоматически | remove the block | fires on 100% of strings |
| Снимки экрана | card only when screenshots exist | context comes from the loc-kit |
| Автоматические предложения tab | under a "More" dropdown; the card no longer links to it | the fix lives in the card; the tab's accept buttons bypass the re-check contract |

Wave 2, small backend (2-3 days): backfill stored candidates for the current
unresolved critical/major backlog (one repair MT call per verdict, dry-run by
default); automatic one-unit re-check after a manual save that changes the
text of a judged string (2 seat calls); judge-run report linking into the
editor with `q=check:judge-reject`.

Wave 3, best-in-class (about a week, only after waves 0-2 have been used): a
producer layout of the full page (third `Profile.translate_mode` value): one
row per string with source, back-translation, current text, fix diff and
Принять / Оставить / Править, 20-50 strings per screen, the "Commit
suggestion" pattern of a GitHub review. Not a Zen variant: the 2026-09-01
research decision keeps Zen untouched.

Do not: disable the `Suggestion` mechanism (it stores the candidate); touch
`translation_review`; add automatic acceptance or a dedicated "Edit manually"
button (both rejected); change the judge prompt for a Russian rationale in
this iteration (the back-translation already gives Russian text).

### 6.4 Second-round wave 1 items

Collapsing `Похожие ключи` / `Комментарии` / `Другие языки` into the same
"More" dropdown with no tab open by default, the special-characters toolbar
behind one button, the pager reduced to previous / position / next, empty
glossary and string-info cards collapsed, `Сохранить и остаться` demoted,
keyboard shortcuts for accept / keep / re-check and a paid-request hint were
first parked as deferred and then approved in a second round on 2026-09-02
(plan Tasks 10-15). The ui-reduction design asks for a telemetry audit before
removing a P1 item; every item here collapses or relocates, none removes a
capability, which is why no audit gates them. Still deferred: the
report-to-editor link (wave 2), the persistent Producer/Advanced toggle
(wave 3) and a Russian judge rationale (prompt change).

## 7. Product owner decisions (2026-09-02)

| Question | Answer | Effect |
| --- | --- | --- |
| Are producers superusers or a role? | Project administrators | all hiding through templates; wave 0 is configuration only |
| Remove `Уже переведено`? | yes | `WEBLATE_REMOVE_CHECK` += `TranslatedCheck` |
| Where does the suggested fix go if the machinery tab is hidden? | into the card (already implemented; backlog needs backfill) | tab moves under "More"; card links removed |
| Hide Screenshots, Предложить, Переведено автоматически? | yes, yes, yes | wave 1 |
| Why does the producer need "Re-check"? | they do not; it must be automatic | auto re-check on manual save; button under "Ещё"; passive status |
| Remove the three review radios and keep only "Зафиксировать решение"? | remove radios; one decision button is not enough because it does not save text | radios hidden on judged strings; three actions: accept fix, keep as is, save (with auto re-check) |
| Mandatory reason for "Оставить как есть"? | optional | `reason` not required; `resolve_verdict` accepts blank |
| Auto re-check after a manual edit? | yes | wave 2 task |
| Include the rest of waves 0 and 1 (tabs, toolbar, pager, empty cards, Save and stay, shortcuts, hints)? | yes, all of it | plan Tasks 10-15; only wave 2/3 items remain deferred |

The approved subset is the plan
`docs/llm-first/plans/2026-09-02-producer-editor-pareto.md`.
