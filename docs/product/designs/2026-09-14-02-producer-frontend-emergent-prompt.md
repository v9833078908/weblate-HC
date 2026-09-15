# Producer-фронт HCGameLoc: промпт для Emergent

Дата: 2026-09-14. Статус: черновик промпта; основан на
`docs/product/designs/2026-09-14-01-producer-frontend-brief.md` (бриф на
согласовании). Правки владельца от 2026-09-14 учтены: экран входа не
строится (родной вход Weblate), wizard называется «Сделать локализацию» и
открывается из пустого проекта с приглашением «Это автоматический wizard.
Загрузите лок-кит», в UI используются термины «проект» и «лок-кит», не
«игра». Отправляется в Emergent первым сообщением целиком; дальнейшие
итерации — по одному экрану за сообщение. Текст ниже — сам промпт, на
английском, без изменений.

---

# The UI/UX Pattern Master — HCGameLoc Producer Console

You are a Senior Product Designer and front-end engineer, the UI/UX Pattern Master, specializing in dense, keyboard-first **web** applications for professional operators. You design *and build* a working click-through prototype: React + Vite + TypeScript + Tailwind + shadcn/ui, running on mock data behind a single typed API client. The prototype will later be attached to a real backend without redesigning screens, so the API boundary below is a contract, not a suggestion.

Design and build the complete UI for a **producer console of an LLM-first game localization platform** ("HCGameLoc", a fork of Weblate). The console is a *new front door* over an existing translation backend: a producer uploads a project's text table (a "loc-kit"), the backend translates it into every project language with LLMs, runs deterministic quality checks and an optional LLM judge, and the producer downloads the finished files. The old Weblate UI stays available as an "Advanced" escape hatch; it is not redesigned here.

**Authentication is not part of this prototype.** The console reuses Weblate's native login and is served on the same origin; the user arrives already signed in. Build no sign-in, sign-up or password screens. In mock mode the session is a fixture; in live mode a `401` redirects to `/accounts/login/?next=<current url>`.

**The whole UI is in Russian.** Every label, empty state, error, tooltip, email preview and fixture string must be Russian. Code, identifiers, comments and this prompt are English. Do not add an English toggle.

**Terminology is fixed.** The Weblate `Project` is «проект» in the UI; the uploaded table is «лок-кит»; the one-time setup wizard is «Сделать локализацию». The word «игра» appears only inside the studio knowledge-base entity name «карточка игры в БДХК». Never «создать игру», never «игра» as a screen, menu or card name.

## User research insights

- **Primary user:** a Russian-speaking game producer at a mobile/PC game studio. Reads English, does *not* read the target languages (French, Japanese, Chinese, Turkish, Brazilian Portuguese…). Not a translator, not a Weblate expert. The studio has no translators: the LLM pipeline *is* the localization team. Today every producer is given the Weblate admin role and a six-step wiki instruction.
- **Secondary user:** an "AI Tools administrator" who owns API keys, model routing, the language preset and budgets. Appears only in settings/admin surfaces; not the focus of this prototype.
- **Top 3 user goals:**
  1. Upload a loc-kit (a table with `key / ru / en / … / explanation` columns) or store-page texts for Steam / Google Play / App Store, and get all languages back translated and checked without touching any translation-tool concepts.
  2. See at a glance which languages are ready to ship and which strings need *their* decision, with a reason they can understand without reading the target language.
  3. Download the result in the format they want (XLSX / CSV / JSON / the same format they uploaded; for store texts a ZIP or per-language copyable fields) and know exactly what was left out and why.
- **Pain points in current solutions:**
  - Weblate's UI is old-fashioned and overloaded: dozens of navigation items, a check catalogue with technical names (`game-markup`, `end_stop`), three parallel string states, add-ons, VCS panels.
  - The critical path is six manual steps across different pages: create project → import kit → add each language by hand → paste three prompt texts → open "Automatic translation", choose the engine and *type the search filter `state:empty`* so already-translated strings are not overwritten → run judge → find the right download among 60 formats.
  - Failing checks are shown as a scary red list of check codes; the producer cannot tell what is blocking release from what is a suggestion.
  - No single place shows "what did this cost", "is the run still alive", "is French ready".
  - Store texts require knowing the engine's file-naming scheme (`metadata/ru/title.txt`) and per-store character limits from memory.

## Binding product rules (do not "improve" these)

These come from the platform's quality architecture and the backend enforces them. The UI must reflect them exactly.

1. **Only deterministic checks block export** (placeholders/markup lost, engine line separator `$` broken, numbers changed, Steam BBCode broken, store field over its character limit). A string with a blocking check is exported as an **empty value**; the key stays in the file. The download screen says how many keys were exported empty.
2. **The LLM judge is advisory.** Its findings have severities *critical / major / minor*. Critical stays in the decisions queue until the producer decides; major and minor are shown as notes and do not block export. Judge runs are manual, paid and take hours: the UI shows an estimate (strings, cost in $, expected duration) *before* the producer confirms.
3. **No numeric quality score anywhere.** No "87/100", no progress-style quality bars. Quality is shown as: ready languages, count of strings needing a decision, and per-string *one* reason in plain Russian plus a back-translation when available.
4. **Actions on a string depend on the finding type.**
   - Blocking check → «Исправить с помощью AI» (re-run translation with the check fed back; for auto-fixable checks the backend fixes without an LLM) and «Открыть в Weblate». **No "accept as is".** A false positive is dismissed only in Advanced (Weblate), never from this queue.
   - Judge finding → the same two actions **plus** «Принять как есть», which requires a short reason and is recorded in the string's history.
5. **Check codes and the word "check" are never shown to the producer.** The queue shows reasons like «Потерян тег `<color>`», «Число изменилось: 24 → 42», «Судья: искажён смысл — герой *отдаёт* меч, в переводе *получает*». Codes exist only in the Advanced link's URL.
6. **Translation runs never overwrite existing translations.** A re-uploaded kit translates new keys and keys whose *source text changed*; everything else is untouched. The producer never sees or edits a filter to achieve this. Translations already present in the uploaded kit's language columns are imported as-is.
7. **Languages are configured once per project** in the «Сделать локализацию» wizard from a studio preset (`en, de, fr, es, pt_BR, tr, ja, ko, zh_Hans` — editable). Every upload is translated into all project languages; there is no per-upload language picker.
8. **The source language of a kit is the leftmost populated language column**, and it cannot be changed after localization is set up. The upload screen states the detected source language and stops with an explanation if it is not the expected one; it does not offer a dropdown.
9. **Rows that have a key but no text in any language** are quarantined into a separate list on the upload screen (the file still imports). Do not silently drop them.
10. **Run status is truthful.** States: «В очереди» / «Выполняется» / «Нет обновлений» (the worker stopped reporting — offer «Перезапустить незавершённое») / «Завершён» / «Ошибка». A run is never shown as finished without an explicit result. Producer gets an email on completion and on failure (render the email as a preview screen).
11. **Money is visible.** Every translate and judge run shows cost by language and model; the project overview shows spend for the month and per run.
12. **Advanced is one link away, never a mode switch.** Every entity (project, content, string, run) has «Открыть в Weblate»; the prototype opens a placeholder page with the would-be Weblate URL.

## Design foundation (binding token contract)

The platform already has a design contract, "HCGameLoc Console — тихий продуктовый SaaS": flat surfaces, hairline borders instead of shadows, one accent colour, a dense vertical grid. This is a **refinement of the existing product, not a redesign**: palette, fonts and radii are fixed. Implement them as Tailwind theme tokens and shadcn CSS variables. Light theme only for this prototype (keep the `dark-*` values in the theme file, unused).

Colours (light):

```
background #ffffff   foreground #2a3744
card #ffffff         card-foreground #2a3744
muted #f5f5f5        muted-foreground #6b7280
accent #e9eaec       accent-foreground #2a3744
border #e9eaec       input #cccccc        ring #107a62
primary #107a62      primary-foreground #ffffff   primary-strong (hover) #144d3f
secondary #f5f5f5    secondary-foreground #2a3744
destructive #cc3d20  destructive-foreground #ffffff
success #158068      success-foreground #ffffff
warning #8a6d3b  ONLY on warning-surface #fcf8e3 (4.54:1; never on white)
info #1378d0     info-strong #0f5fa6 (text)   info-surface #e0eaf1
topbar #2a3744   topbar-foreground #bfc3c7   topbar-foreground-hover #2eccaa
highlight-glossary #ffffcc  (glossary term highlight inside string text — product semantics, not decoration)
```

Dark (define, do not use): background #1a1d1e, card #1e2122, muted #212324, foreground #d9d3cc, muted-foreground #d5d0c7, border #63696c, primary #29b396 with primary-foreground #0b1211, ring #4eeac9, topbar #25303b.

Typography — "Source Sans 3" for UI, "Source Code Pro" for any source/target string text (load from Google Fonts):

```
display-lg   56/64 400 -0.46px    (empty-state hero only)
metric-lg    40/50 600 -0.67px    (dashboard numbers)
heading-page 24/31 600 -0.4px
heading-card 18/24 600
body-md      16/24 400            (default)
body-sm      14/18 400
label-strong 14/18 600            (buttons, table headers)
label-caps   12/16 600 +0.08em    (badges)
code-md      14/22 400 Source Code Pro  (source/target cells — never proportional)
```

Radii: sm 4px (buttons, inputs, badges), DEFAULT 10px (cards, alerts), lg 14px, xl 20px, full. Spacing unit 4px; control padding 12px × 8px; card padding 16px; card gap 24px; section gap 40px. Top bar 48px, ink `#2a3744`, links `#bfc3c7` → hover `#2eccaa`. Controls (buttons, inputs, selects) are **36px** tall.

Rules: one `primary` button per visible area; secondary is `#f5f5f5` with dark text; destructive only for irreversible actions; **no card or table shadows**; no new greys, radii or font sizes; status is always icon + text badge, never colour alone; visible focus ring 2px `#107a62` with 2px offset on every interactive element including table rows (never `#2eccaa` as a ring on light backgrounds — 2.03:1); use CSS logical properties (`ms-`, `pe-`, `text-start`) throughout; no gradients, glass, illustrations or pill buttons on work screens.

## 1. HIERARCHY & LAYOUT

- **Visual hierarchy strategy.** On every project screen the eye lands, in order, on: (1) the *state* of the project — «Готово к выгрузке: 7 из 9 языков», (2) the *one thing that needs the producer* — «Требуют решения: 14 строк (3 блокируют выгрузку)» with a primary action, (3) the *live run* if any, (4) money, (5) everything else. Navigation, metadata and Advanced links are typographically quiet (`body-sm`, `muted-foreground`).
- **Reading patterns.** Dashboard and list screens follow an F-pattern: status band at the top, then left-aligned rows. The decisions queue string card follows a Z-pattern: key + language top-left, actions top-right, source → target → back-translation stacked, reason and history at the bottom.
- **Density.** Operate-mode density: tables at 40px rows, `body-sm`; cards 16px padding, 24px gaps; no hero whitespace inside the app. Breathing room is reserved for the wizard (one decision per step) and for empty states.
- **Layout shell.** 48px ink top bar (product name, project switcher, user menu with the signed-in name, «Открыть в Weblate»). Left sidebar 240px inside a project with 7 items in this exact order: **Обзор · Загрузить · Требуют решения (badge with count) · Глоссарий · Скачать · Настройки проекта · Advanced → Weblate**. In a project without localization only Обзор, Настройки проекта and Advanced are enabled; the rest are disabled with the tooltip «Появится после первой локализации». Content column max 1200px, `container-fluid` behaviour on wide screens. Breadcrumb `Проекты / Pirate Ships / Требуют решения`.

## 2. PLATFORM-SPECIFIC PATTERNS (web, desktop-first)

- **Navigation:** sidebar inside a project; the projects list is the top-level page. The wizard is a full-page stepper, not a modal. Deep links for every screen and filter state (`/projects/pirate-ships/decisions?lang=fr&kind=blocking`).
- **Modals:** only for confirmations that spend money or are irreversible (start judge run; accept as is with reason; re-run unfinished) and for the one-field «Создать проект» dialog. Everything else is inline or a side sheet (string card opens as a right-side sheet 560px, keeping the list scroll position).
- **Keyboard:** the decisions list is fully keyboard-driven: `↑/↓` move, `Enter` opens the sheet, `Esc` closes, `1/2/3` trigger the visible actions in order, `/` focuses the filter. Show a `?` shortcut cheat-sheet. No gestures required; pull-to-refresh is N/A on desktop.
- **Context menus:** a `⋯` menu on rows for secondary actions («Открыть в Weblate», «Скопировать ключ»), never for the primary action.
- **Uploads:** drag-and-drop zone plus file picker; the zone announces accepted formats; the same zone accepts kit and glossary tables.

## 3. SCREEN DESIGNS

For each screen provide, in a `DESIGN-NOTES.md` you generate alongside the code: wireframe description, component inventory, interaction specifications, empty/error/loading states. Build every state listed, reachable from the mock fixtures.

**1. Empty project (first run).** Reached by opening a project that has no localization yet. Full-width empty state inside the project shell: `display-lg` headline «Это автоматический wizard. Загрузите лок-кит», a three-line explanation of the loop (загрузите лок-кит → перевод и проверки → скачайте результат), one primary «Загрузить лок-кит» that opens the wizard at step 1, and a quiet tertiary link «Открыть в Weblate». No sign-in precedes this screen. Loading: skeleton of the project shell.

**2. Projects list (Home).** The console's landing page after native login. Cards per project: name, ready languages `7/9`, decisions badge, last run status and time, month spend; a project without localization shows «Локализация не настроена» and its card opens screen 1. Sort by "needs attention". Search. Primary «Создать проект» opens a one-field dialog (name; slug derived) that creates an empty project and lands on screen 1 — this mirrors Weblate's native project creation and is deliberately *not* the wizard. Empty search result state. Card hover `accent`.

**3. Project overview (Dashboard).** Status band: three `metric-lg` tiles — «Готово к выгрузке 7 / 9», «Требуют решения 14» (with «3 блокируют выгрузку», primary link), «Потрачено в сентябре $41.20». Language table: language, strings, ready ✓ / needs decisions n / translating…; row click → decisions filtered by language. Runs card: last 5 runs with status badges and a live run card if running. Content card: loc-kit, glossary, stores with per-item «Скачать» and «Загрузить». Right after «Сделать локализацию» the overview shows run #1 queued. Error: run failed banner with «Подробнее» and «Перезапустить незавершённое».

**4. «Сделать локализацию» (Primary task — wizard, 5 steps, one screen each, progress rail on the left).** Opened only from screen 1; full page, not a modal; «Отмена» returns to screen 1 and discards nothing on the server.
   - Step 1 «Лок-кит»: drop zone (XLSX / CSV / TSV); after parse, a preview card: detected source language «ru — первая заполненная колонка», found languages with counts of pre-filled translations, row count, quarantined rows list (expandable), read-only column mapping. Error states: source language is not the expected one (stop with explanation, no dropdown), unreadable file, empty file.
   - Step 2 «Языки»: preset chips (`en, de, fr, es, pt_BR, tr, ja, ko, zh_Hans`) all on; languages present as columns in the kit are pre-selected and labelled «в ките есть переводы»; can remove/add; note that these apply to every future upload in this project.
   - Step 3 «Профиль проекта»: search field «Карточка игры в БДХК» (studio knowledge base) with results list; when a card with brief and voice/style blocks is selected show alert-info «Профиль проекта импортирован из карточки игры в БДХК: жанр, сеттинг, тон, обращение к игроку». If no card or blocks are missing: a short questionnaire (5–8 fields: жанр, сеттинг, тон, аудитория/возраст, обращение «ты/вы», особенности по языкам, запрещённые слова). The questionnaire is *turned into prompts by the backend*; the producer never sees prompts. «Пропустить» (secondary) applies the studio default profile.
   - Step 4 «Глоссарий»: alert-info «Глоссарий заметно повышает качество перевода — рекомендуем загрузить»; drop zone for a term table; **plus** a card «Термины, найденные в лок-ките» — extracted candidate terms (term, optional suggested translation per language, context snippet, «почему»: имя, название, валюта) with per-row «Добавить» and a header «Добавить все»; «Пропустить» is a secondary button, never hidden.
   - Step 5 «Проверка»: summary of all steps; primary «Сделать локализацию»; result: languages configured, loc-kit and glossary created, run #1 started; land on the run card.

**5. Upload (Primary task, repeat).** Tab «Лок-кит» = wizard step 1 UI with an extra diff card after parse: «Новых ключей 12 · Изменённый источник 3 · Без изменений 1 240 · Удалённых 0»; primary «Загрузить и перевести». Tab «Тексты для стора»: store selector (Steam / Google Play / App Store / Произвольный); a form of that store's fields with character counters and limits (Google Play: title 30, short 80, full 4000; App Store: name 30, subtitle 30, description 4000, keywords 100, promotional 170; Steam: short / about / legal with limits shown as «из справочника Steamworks v2026-09» and BBCode allowed); source language fixed to the project's; over-limit is a blocking validation *before* submit; primary «Перевести». Loading: parsing spinner in the drop zone with file name; error states as in wizard step 1.

**6. Run card (Detail view).** Header «Прогон #12 · Лок-кит v3 · Перевод», status badge, started/elapsed, «Отправим письмо, когда закончится». Stage rail: Импорт ✓ → Языки ✓ → Перевод (7/9, per-language rows with their own status) → Проверки. Cost so far by language and model. For judge runs: stage rail Оценка → Проверка (n/N строк) → Итог; outcome counts (прошло / замечания / требуют решения). Failure state with error summary and «Перезапустить незавершённое»; «Нет обновлений» state with explanation «Обработчик не отвечает 4 мин; незавершённые строки не потеряны». Skeleton on load. Polling every 5 s in mock.

**7. Decisions queue + string card (Search/Filter + Detail).** Filters: language (chips with counts), content (kit / stores), kind (Блокируют выгрузку / Судья), search by key. Table: key (`code-md`), language, source (truncated), reason (one line), kind badge («Блокирует» destructive-outline / «Судья: критично» warning / «Судья: замечание» info). Row → right side sheet: key, language, source cell (`editor-source-cell`), target cell, «Обратный перевод» cell when present (else «нет — появится после проверки судьёй»), reason in full, glossary terms highlighted `highlight-glossary` in source, history list (attempt 1: MT; attempt 2: repair …), action bar per rule 4. «Принять как есть» opens a small dialog with a required reason. Bulk: select rows → «Исправить с помощью AI (n)». Empty: «Всё решено — 9 языков готовы к выгрузке». Loading skeleton rows.

**8. Download (Action completion).** Choose scope (whole project / one content), format select (XLSX · CSV · JSON · «Как загружено (.xlsx)»); for stores: ZIP or a «Поля по языкам» view — per-language accordion with each field and a «Скопировать» button. Summary line before the button: «1 255 ключей · 3 выгружены пустыми → Требуют решения». Primary «Скачать результат». Success toast with file name. Error: export failed.

**9. Glossary.** Table of terms (source, per-language target columns, note, «правило» as plain words: обязательный / не переводить / запрещён); drop zone «Добавить таблицу терминов (только новые термины добавляются, существующие не меняются)»; the same candidates card as wizard step 4 with «Добавить» / «Добавить все»; «Добавить термин» inline row. Empty state explains the quality benefit with one sentence.

**10. Judge launch.** Entry from overview («Проверить качество») → sheet: scope (content, languages), estimate card «≈ 1 240 строк · ≈ $18 · ≈ 2–3 часа», note «Проверка идёт в фоне, письмо придёт по завершении»; primary «Запустить проверку». Disabled state when the administrator has not configured the judge, with a reason.

**11. Project settings.** Languages (preset chips), project profile (link to the карточка игры в БДХК or the questionnaire answers, editable; a muted note «Промпты формируются автоматически; редактируются в Weblate»), stores enabled, notifications email, and a card «Advanced» with links: Weblate project, add-ons, VCS, check dismissals, machine-translation settings. Administrator-only fields are shown read-only with a lock icon and tooltip.

**12. Error / empty states catalogue.** Build a `/dev/states` page that renders every empty, loading, error and «no updates» state from the screens above side by side, so stakeholders can review them without triggering them.

## 4. COMPONENT SPECIFICATIONS

- **Buttons:** Primary (`#107a62`, hover `#144d3f`, white `label-strong`, 36px, radius 4); Secondary (`#f5f5f5`, dark text); Tertiary = text link in `#107a62` with underline on hover; Destructive (`#cc3d20`) only for irreversible actions such as «Удалить проект» — none are on the critical path. One primary per area. Loading buttons show spinner + text, keep width.
- **Forms:** labels above inputs (`label-strong`), 36px inputs with 1px `#cccccc` outline, focus ring per contract; inline validation on blur, error text in `#cc3d20` with icon, `aria-describedby`; success confirmation as a green check inside the field, not a toast. Character counters on store fields turn `warning` at 90% and `destructive` over limit.
- **Cards:** flat, 1px `#e9eaec` border, radius 10, header band `#e9eaec` with `heading-card` when the card has actions. Content priority: number → label → action.
- **Status badges:** `label-caps`, radius 4, icon + text: «Готово» success, «Переводится» info, «Требует решения» warning-on-surface, «Ошибка» destructive, «Нет обновлений» outline with pulse icon. Never colour-only.
- **Tables:** 40px rows, header `label-strong` on `#f5f5f5`, hover `#e9eaec`, focusable rows, sticky header, source/target cells in `code-md`.
- **Run stage rail:** vertical stepper with per-stage status badge, elapsed time, and per-language sub-rows for the translate stage.
- **Data visualization:** no charts. Spend is a table (run, language, model, $). Progress is `n / N` text plus a thin 4px bar in `primary` on `muted`.
- **Toasts:** bottom-start, 6 s, with an action link, `role="status"`.

## 5. DATA & API BOUNDARY (mock now, live later)

Implement `src/api/client.ts` as the **only** module that knows URLs. Every screen consumes typed functions from it. `VITE_API_MODE=mock|live`; in `mock` mode the client resolves from `src/api/mock/fixtures/*.json` with realistic latency (300–800 ms) and a deterministic run-progress simulator. If your platform generates a backend service, that service exists **only** to serve these same fixtures over these same routes; it must not add routes, storage, auth or business logic. **Do not invent endpoints.** If a screen needs data not covered below, add a `TODO(api)` comment and use a fixture field — never a new route.

Closed endpoint list (all under `/api/producer/`; auth = native Weblate session cookie with `X-CSRFToken` on mutations, Bearer token also accepted; JSON):

```
GET    me/                                          → {username, full_name}   (top bar; mock fixture)
GET    projects/                                    → ProjectSummary[]
POST   projects/                                    {name} → Project   (empty: no languages, no content)
GET    projects/{slug}/                             → Project {localized: bool, languages, contents, spend, decisions_count}
GET    bdhc/titles/?q=                              → {id, title, has_brief, has_voice}[]
GET    bdhc/titles/{id}/profile/                    → {brief?, voice?}
GET    stores/{store}/fields/                       → {fields:[{id,label,limit,markup}], limits_source}
POST   projects/{slug}/uploads/                     multipart {kind: loc-kit|glossary|store, file?, store?, fields?}
                                                    → UploadPreview {id, source_language, languages[], rows, diff?, quarantine[]} | 422 {reason}
POST   projects/{slug}/localization/                {languages[], profile: {bdhc_title_id} | {questionnaire} | {default: true},
                                                     kit_upload_id, glossary_upload_id?, glossary_terms?: Term[]} → Run
                                                    (the wizard's single commit; creates languages, loc-kit, glossary, run #1)
POST   projects/{slug}/uploads/{id}/confirm/        → Run   (repeat uploads once localization exists)
GET    projects/{slug}/runs/                        → Run[]
GET    runs/{id}/                                   → Run {kind, status, stages[], languages[], cost[], outcome?}
POST   projects/{slug}/runs/estimate/               {kind: judge, scope} → {strings, cost_usd, hours_min, hours_max}
POST   projects/{slug}/runs/                        {kind: judge, scope} → Run
POST   runs/{id}/resume/                            → Run
GET    projects/{slug}/decisions/?language=&kind=&content=&q=  → Decision[] {unit_id, key, language, source, target,
                                                    back_translation?, reason, kind: blocking|judge_critical|judge_note, history[]}
POST   decisions/{unit_id}/repair/                  → {run_id}
POST   decisions/{unit_id}/accept/                  {reason} → Decision
GET    projects/{slug}/glossary/                    → Term[]
GET    projects/{slug}/glossary/candidates/?upload= → Candidate[] {term, why, context, suggested: {lang: text}}
                                                    (`upload` set during the wizard; omitted on the glossary page)
POST   projects/{slug}/glossary/terms/              {terms: Term[]} → {added, skipped_existing}
GET    projects/{slug}/export/?content=&format=     → file  (mock: blob + summary {keys, empty})
GET    projects/{slug}/export/summary/?content=     → {keys, empty, by_language}
GET    projects/{slug}/costs/?period=               → {total_usd, by_run[], by_language[], by_model[]}
GET    projects/{slug}/advanced-links/              → {project, addons, vcs, checks, machinery}
```

Fixtures to ship: a session user; **«Pirate Ships»** — localized: loc-kit 1 255 keys, glossary 84 terms, Steam and Google Play store texts, 9 languages, run #12 completed (translate, cost by language), run #13 judge running (stage 2/3), 14 decisions (3 blocking: lost `<color>` tag in `fr`, `$` separator in `ja`, number changed in `tr`; 11 judge: 4 critical, 7 notes; realistic Russian reasons and back-translations), 6 glossary candidates; **«Heart of the Abyss»** — profile from the questionnaire (no БДХК card), run #1 failed; **«Новый проект»** — `localized: false`, no content, opens screen 1. Plus email fixtures for run completion and failure.

## 6. ACCESSIBILITY COMPLIANCE (WCAG 2.2 AA)

- Contrast: text ≥ 4.5:1, UI boundaries ≥ 3:1. Use only the token pairs above; the two known traps are `warning` text (only on `#fcf8e3`) and `info` `#1378d0` as *text* (fails — use `info-strong`).
- Keyboard: everything reachable and operable; visible 2px ring with offset; roving tabindex in tables; focus returns to the row after the sheet closes; skip link to content.
- Screen readers: Russian `aria-label`s for icon buttons; live region for run progress and toasts; badges carry text, not only icons; the decisions table announces «строка 3 из 14, французский, блокирует выгрузку».
- Font scaling: layout survives 200% zoom and browser font-size 24px without horizontal scroll; no fixed heights on text containers except 36px controls.
- Reduce Motion: honour `prefers-reduced-motion` — replace slide/scale with opacity, stop the progress pulse.
- Colour-independent status everywhere (icon + text).

## 7. MICRO-INTERACTIONS

- Sheet open/close 200 ms `cubic-bezier(0.2, 0, 0, 1)`; hover/focus 120 ms; list row removal after a decision 160 ms opacity + height; run progress bar width transitions 300 ms linear.
- After «Исправить с помощью AI»: row shows «В очереди на исправление» inline, the sheet stays open with a muted note; no optimistic "fixed".
- After «Принять как есть»: row leaves the list, toast «Принято: <ключ> · fr — Отменить» (6 s undo).
- Upload parse: the drop zone morphs into the preview card in place.
- No haptics, no sound (web desktop) — N/A.

## 8. RESPONSIVE BEHAVIOR

- Desktop-first: design at 1440, verify at 1280 and 1920 (content max 1200 centred, sidebar fixed).
- Tablet 768–1279: sidebar collapses to icons with labels on hover/focus; decisions sheet becomes full-width overlay; wizard steps stack.
- Mobile < 768: read-only status experience only — projects list, project overview, run card, decisions list (no editing actions, show «Действия доступны на десктопе»). Do not build the wizard or upload for mobile.
- Orientation/foldables: N/A.

## Deliverables

1. Running prototype with all 12 screens and every listed state, navigable from the projects list, Russian UI, mock mode by default, no sign-in screen.
2. `src/api/client.ts` + `src/api/types.ts` + `src/api/mock/` exactly as specified; `live` mode compiles against the same types (no implementation needed).
3. `DESIGN-NOTES.md` with, per screen: wireframe description, component inventory, interaction spec, states; plus a **Designer's Notes** section explaining the rationale behind the key decisions: why the wizard lives inside an empty project and starts with the loc-kit instead of a "create" flow, why one decisions queue instead of a checks page, why no quality score, why actions differ by finding type, why languages are a one-time per-project setting, why the wizard recommends but does not require a glossary, why empty values on export, why Advanced is a link and not a mode.
4. `/dev/states` catalogue page.

Constraints recap: Russian UI; «проект» / «лок-кит», never «игра»; no sign-in screen; tokens exactly as given; no shadows, gradients or charts; no endpoints beyond the list; no numeric quality score; no "accept" on blocking checks; never show check codes; never claim a run finished without a result.
