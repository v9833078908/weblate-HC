# HCGameLoc Console — DESIGN-NOTES

Producer console over a Weblate-based LLM localization backend. React (Next.js host) + Tailwind + shadcn/ui, Russian UI, **mock mode by default**, no sign-in. The API boundary in `src/api/client.js` + `src/api/types.js` + `src/api/mock/` is a contract: every screen consumes typed functions; only the client knows URLs. `NEXT_PUBLIC_API_MODE=live` compiles against the same types (a 401 → `/accounts/login/?next=<url>`), with no extra routes.

> Framework note: the target stack names Vite; this host serves a Next.js app via supervisor, so the prototype is a client-side SPA (`app/page.js` router + `next.config.js` rewrites for deep links). The API contract, tokens, screens and states are implemented exactly as specified.

## Design token contract

Implemented as shadcn CSS variables in `app/globals.css` and Tailwind theme tokens in `tailwind.config.js` (light only; dark-* defined, unused). Fonts: Source Sans 3 (UI), Source Code Pro (`code-md` string cells). Type scale, radii (sm4 / DEFAULT10 / lg14 / xl20), 36px controls, 48px ink top bar, hairline borders, no shadows, single accent `#107a62`, focus ring 2px `#107a62` + 2px offset on every interactive element incl. table rows.

---

## Screens

### 1. Empty project (first run) — `screens/EmptyProject.jsx`

- **Wireframe:** project shell; `display-lg` hero «Это автоматический wizard. Загрузите лок-кит», three-line loop explanation (загрузите → перевод и проверки → скачайте), one primary «Загрузить лок-кит» → wizard step 1, quiet tertiary «Открыть в Weblate».
- **Components:** AppShell (sidebar items disabled), hero, three icon rows, primary Button, WeblateLink.
- **Interaction:** primary opens the wizard; no create/sign-in precedes it.
- **States:** loading = project-shell skeleton (`page.js` ProjectShellSkeleton).

### 2. Projects list (Home) — `screens/ProjectsList.jsx`

- **Wireframe:** F-pattern; title + search + primary «Создать проект»; card grid.
- **Card:** name, ready `7/9`, decisions badge, last-run status + time, month spend; not-localized card shows «Локализация не настроена» and opens screen 1.
- **Interaction:** sort by needs-attention (blocking desc, then decisions desc); one-field create dialog (slug derived, mirrors Weblate native create) → lands on empty project.
- **States:** loading skeletons; empty-search EmptyState; card hover accent.

### 3. Project overview — `screens/ProjectOverview.jsx`

- **Wireframe:** status band (3 `metric-lg` tiles: «Готово к выгрузке 7/9», «Требуют решения 14 · 3 блокируют» primary link, «Потрачено в сентябре $41.20») → live run card → language table → runs card → content card.
- **Interaction:** language row → decisions filtered by language; content rows have per-item «Скачать»/«Загрузить»; «Проверить качество» opens the judge sheet (estimate → paid confirm modal).
- **States:** run-failed banner with «Подробнее» + «Перезапустить незавершённое»; live-run card; runs skeleton.

### 4. «Сделать локализацию» — универсальный мастер (4 этапа) — `screens/UniversalWizard.jsx` (старый 8-шаговый мастер `screens/Wizard.jsx` сохранён на `/projects/{slug}/localize-legacy`)

- **Wireframe:** left progress rail + one decision per step. Step 1 Лок-кит (drop zone → preview: detected source lang, found languages w/ pre-fill counts, row count, quarantined rows collapsible, read-only column mapping). Step 2 Языки (preset chips, in-kit pre-selected «в ките есть переводы», one-time note). Step 3 Профиль (БДХК search → alert-info import, else 7-field questionnaire; «Пропустить» = default). Step 4 Глоссарий (recommend alert-info, drop zone, found-terms candidates with «Добавить»/«Добавить все», «Пропустить» secondary). Step 5 Проверка → primary «Сделать локализацию» → run #1.
- **States:** parse spinner morphs drop zone → preview; quarantine list; error states catalogued on `/dev/states`.

### 5. Upload (repeat) — `screens/UploadScreen.jsx`

- **Tab Лок-кит:** step-1 UI + diff card «Новых ключей 12 · Изменённый источник 3 · Без изменений 1 240 · Удалённых 0»; primary «Загрузить и перевести». Copy states that existing translations are never overwritten.
- **Tab Тексты для стора:** store selector (Steam / Google Play / App Store / Произвольный); per-store fields with live character counters (warning ≥90%, destructive over limit) and limits source («из справочника…»); Steam allows BBCode; source language fixed; over-limit blocks submit; primary «Перевести».

### 6. Run card — `screens/RunCard.jsx`

- **Wireframe:** header title + status badge + elapsed + «Отправим письмо…»; vertical stage rail with per-stage badge and per-language sub-rows on the translate stage; cost table (язык · модель · $) with running total; judge outcome tiles (прошло / замечания / требуют решения).
- **States:** completed, running (polls every 5 s), queued, **stalled** («Обработчик не отвечает 4 мин…»), **error** (summary + «Перезапустить незавершённое»); load skeleton. A run is never shown finished without a result.

### 7. Decisions queue + string sheet — `screens/DecisionsQueue.jsx`

- **Wireframe:** filters (language chips w/ counts, type Блокируют/Судья, content kit/stores, key search) → 40px table (key `code-md`, language, source truncated, one-line reason, kind badge). Row → right sheet 560px (Z-pattern): key+lang top-left, kind badge + ⋯ top-right, source → target → back-translation stacked, full reason, glossary highlight in source, history, action bar.
- **Actions by finding type:** blocking → «Исправить с помощью AI» + «Открыть в Weblate» (no accept); judge → same two + «Принять как есть» (required reason, recorded).
- **Keyboard:** ↑/↓ move, Enter open, Esc close, 1/2/3 actions, `/` focus filter, `?` cheat-sheet; roving tabindex; focus returns to row on close; rows announce «строка N из M, язык, тип».
- **Micro-interactions:** after repair → inline «В очереди на исправление», sheet stays open; after accept → row leaves + toast with 6 s undo; bulk «Исправить с помощью AI (n)».
- **States:** empty «Всё решено — N языков готовы к выгрузке»; loading skeleton rows.

### 8. Download — `screens/DownloadScreen.jsx`

- Scope (whole / one content) + format (XLSX · CSV · JSON · «Как загружено (.xlsx)»); for stores: ZIP or «Поля по языкам» accordion with per-field «Скопировать». Summary line «1 255 ключей · 3 выгружены пустыми → Требуют решения» (links to blocking queue). Primary «Скачать результат» → success toast with file name.

### 9. Glossary — `screens/GlossaryScreen.jsx`

- Terms table (source, per-language targets, note, rule as plain words: обязательный / не переводить / запрещён / обычный); drop zone «только новые термины добавляются…»; found-terms candidates «Добавить»/«Добавить все»; «Добавить термин» inline row; empty state explains the quality benefit.

### 10. Judge launch — sheet in `ProjectOverview.jsx`

- Scope + estimate card «≈ 1 240 строк · ≈ $18 · ≈ 2–3 часа», note «идёт в фоне, письмо придёт по завершении»; primary «Запустить проверку» → paid confirm modal. Disabled state (admin hasn't configured judge) shown in `/dev/states`.

### 11. Project settings — `screens/SettingsScreen.jsx`

- Languages preset chips; project profile (БДХК link, muted «Промпты формируются автоматически; редактируются в Weblate»); stores toggles; notifications email; admin-only service fields read-only with lock icon + tooltip; Advanced card with Weblate links (project, add-ons, VCS, snятые проверки, machine-translation).

### 12. Error / empty states catalogue — `screens/StatesCatalogue.jsx` (`/dev/states`)

- All status/kind badges, empty states, skeletons, run failed / stalled / judge-not-configured / export-failed banners, plus links to the two email previews.

**Email previews** — `screens/EmailPreview.jsx` (`/emails/completion`, `/emails/failure`): rendered as preview screens with subject/from/to/body/cost/CTA.
**Advanced placeholder** — `screens/AdvancedPlaceholder.jsx` (`/advanced?url=`): shows the would-be Weblate URL; every entity links here (never a mode switch).

---

## Designer's Notes — rationale

- **Wizard lives inside an empty project and starts with the loc-kit, not a "create" flow.** Weblate already owns project creation (mirrored by the one-field dialog). The producer's real first job is *content*, so the empty project *is* the wizard entry: upload → translate → download. No "создать игру"; the word «игра» appears only in «карточка игры в БДХК».
- **One decisions queue, not a checks page.** Producers don't read target languages or check codes. A single ranked queue with a plain-Russian reason (and back-translation when available) answers "what needs *my* decision" without exposing Weblate's three states, add-ons or a check catalogue.
- **No numeric quality score.** A score invents precision the pipeline can't honour and invites "ship at 85%". Quality is expressed as ready languages, a count of strings needing a decision, and per-string a human reason — actionable, not decorative.
- **Actions differ by finding type.** Deterministic checks are ground truth: they block export and can only be *fixed* (AI re-translate or auto-fix) or inspected in Weblate — never "accepted", because accepting a lost placeholder ships a broken string. The LLM judge is advisory and fallible, so a critical finding can be «Принято как есть» with a recorded reason.
- **Languages are a one-time per-project setting.** Every upload must land in the same set so files stay consistent and no per-upload picker can silently drop a language. Set once in the wizard from the studio preset.
- **The wizard recommends but does not require a glossary.** Glossary sharply improves names/terms/currency, but forcing it would block the producer's first run. So it's a strong recommendation with a never-hidden «Пропустить».
- **Empty values on export (key kept).** A string that fails a blocking check must not ship wrong. Exporting it empty (while keeping the key) preserves file structure for the engine and makes the gap explicit — the download screen states how many keys went out empty.
- **Advanced is a link, not a mode.** The old Weblate UI stays a one-click escape hatch on every entity; it is never a global toggle, so the producer never has to "switch worlds" to get their job done.

## Money & truthful runs

Every translate/judge run shows cost by language and model; the overview shows month + per-run spend. Run states are «В очереди / Выполняется / Нет обновлений / Завершён / Ошибка» — never "finished" without an explicit result; completion and failure both send an email (previewable).

---

## «Сделать локализацию» — универсальный мастер (4 этапа)

Route `/projects/{slug}/localize` (`components/console/wizard2/*`), opened from the empty-project screen. One full page inside the project shell with a left rail of **four adaptive stages** — **Файлы · Языки · Контекст и термины · Проверка и запуск** — and «Назад / Далее / Сделать локализацию / Отмена». A stage becomes clickable once reached; the draft is persisted (mock: `sessionStorage`, restored from `?u=`). Dev/states tiles deep-link a specific state via `?scenario=<key>&stage=<n>`.

**Core principle — one happy path, everything else is a conditional card.** The wizard never shows a permanent technical step. Detection happens *after* upload; whenever the backend is unsure it raises a **«Нужно уточнить»** card inline (duplicates, ambiguous text file, broken BBCode, over-limit source, unknown files, two stores, region locales, …). Once answered the card collapses to «Уточнения завершены: …» and the normal flow continues. Weblate plumbing (components, metadata paths, file masks, `bbcode-text` check names) is never surfaced.

### API (mock, behind the typed client — `src/api/client.js`)

`GET stores/registry/` → per-store fields, limits, source, markup, locales (`fixtures/stores_registry.json`); `POST projects/{slug}/uploads/` → **`UniversalUploadAnalysis`** (`{ kind: 'loc-kit' | 'store' | 'ambiguous', store, fields[], columns[], rows, languages_found[], source_options, clarifications[], stop, stage1_ready }`); `PATCH uploads/{id}/` (resolve a clarification, replace/remove a source file, set answers — re-runs the gate); `POST projects/{slug}/localization/` → a run (store runs add stages «Проверка лимитов и разметки» и «Сборка ZIP»). The scenario engine lives in `src/api/mock/universal.js`; no routes beyond this list.

### Stage 1 — Файлы

- **Universal dropzone** (XLSX/CSV/TSV/TXT/ZIP/папка, «расширение не важно») with example chips. On drop → parse spinner → **detection**:
  - **Лок-кит:** `имя · N строк · M языковых колонок`, `готовы / вынесены отдельно`; «Посмотреть анализ» opens a side sheet (format, columns+roles, languages+fill, quarantine by reason). Opening it is optional.
  - **Тексты для стора:** detected platform, source locale, per-field rows with live character counters (near ≥90% warning, over destructive) and status marks; markup + limits-source line; «Проверить поля» side sheet. No metadata paths / file masks shown.
- **«Нужно уточнить» cards (conditional):** unknown files → name/limit(or «нет»)/source/markup; single ambiguous file → table / store-field / other; two stores → create two sets; unknown file in a known set; two files → one field (compare cards); source over limit (overflow highlighted, «Заменить файл», never auto-trim); broken BBCode (highlighted tag, manual replace); empty file (remove if optional).
- **Hard stop:** unreadable/unsafe archive — nothing imported, no partial read.
- `Далее` enabled only when `stage1_ready` (no stop, every clarification resolved, no field over-limit / broken-markup / empty-required).

### Stage 2 — Языки

- One language set per project. **Adaptive:** loc-kit shows languages found in the file (fill %, «из файла»); store shows target locales from the registry. Studio preset chips + «Добавить все».
- **Conditional resolution:** ambiguous regional locales (pt → pt-BR/pt-PT, es → es-ES/es-419, zh → zh-Hans/zh-Hant) — treated as different languages, `Далее` blocked until chosen, then «Уточнения завершены: …»; existing translations in the upload → «в файлах уже есть перевод», never overwritten; incomplete source set (e.g. en 2/3 fields) → source card, `Далее` blocked, no silent fallback to Russian.
- Source language is locked and shown with a lock line. Summary tile: `N целевых языков · M перевода сохраним · K языков переведём`.

### Stage 3 — Контекст и термины

- **Profile questions** (adaptive, plain Russian, no invented facts): tone/register (ты/вы/формально), dialogue register (loc-kit only), profanity keep/soften(level), CJK politeness (only when ja/ko is a target). These are the judge's ground truth and can't be skipped.
- **Глоссарий (рекомендуем, «Пропустить» always visible):** the reused `GlossaryWorkspace` with store-aware labels («Предложить термины из загруженных файлов», stages «Читаем тексты площадки → …»). Before → running (queued/stages + processed/total, no invented %) → suggestions (categories, occurrences, evidence sheet, existing translations, disputed must be resolved, Добавить / Добавить все). In the wizard a picked term reads «Выбрано — добавится при запуске»; only after real save on the glossary page → «Добавлено в глоссарий».

### Stage 4 — Проверка и запуск

- One summary row per stage with «Изменить» (source language carries the lock). Translate-only estimate card (loc-kit: `≈ N строк × M языков`; store: `≈ K полей × M языков = S значений`) with `≈ $` and `≈ время`, note that the judge is a separate paid run and a completion email is sent. Primary «Сделать локализацию» → run.

### Store-run results (catalogued on `/dev/states`)

- **Завершён:** «Локализация Steam готова · 3 поля · 8 языков · 24 перевода · Проверки пройдены», stage rail incl. «Проверка лимитов и разметки» / «Сборка ZIP», primary «Скачать ZIP» + «Поля по языкам».
- **Target over limit:** `7 / 8 языков готовы · 1 поле требует решения`; per-field reason (`96 / 80`, «превышает лимит Google Play на 16 символов»), «Исправить с помощью AI» / «Открыть в Weblate» — **no «Принять как есть»**; the field ships empty (key kept) and the ZIP/summary say so.

### Designer's Notes — rationale (4-stage)

- **Detection after upload, not a format picker.** The producer drops whatever they have; the backend decides loc-kit vs store and asks only where it is genuinely unsure. A pre-upload «choose type» screen would push backend concepts onto the user.
- **Technical questions are conditional, never permanent steps.** Duplicates, split components, ambiguous columns and store field-mapping appear as «Нужно уточнить» cards only when the file needs them — the eight old visible steps collapse into four stages plus on-demand cards.
- **Store limits come from a registry, are shown, and never silently enforced.** Over-limit source stops with the overflow highlighted; over-limit target ships empty with the gap stated. Text is never auto-trimmed and markup is never auto-fixed.
- **Existing translations are sacred.** A language already present in the upload is marked and preserved; runs fill only empty or source-changed values.
- **Plumbing stays hidden.** «Компонент», file masks, metadata paths and Weblate check names never appear on the happy path; the split into per-store components happens in the backend, invisibly. The 19 catalogued states in `/dev/states` prove every branch.

---

## (Legacy) «Сделать локализацию» — the 8-step wizard

Kept at `/projects/{slug}/localize-legacy`, superseded by the 4-stage universal wizard above. Original notes follow for reference.

Route `/projects/{slug}/localize`, opened only from the empty-project screen. Full page inside the project shell; left progress rail (8 steps, a step becomes clickable once reached; steps 5 and 7 show «пропущено» when skipped). «Назад» / «Далее» / «Отмена» at the bottom. The draft upload is persisted server-side (mock: `sessionStorage` keyed by `upload_id`, restored from `?u=&step=`), so a reload returns to the current step. `Далее` stays disabled while any stop-and-ask question is unanswered.

Three checklist principles are load-bearing: (1) never guess a product fact — it is asked with options and consequences; (2) bring measurements to every question — counts, samples, exact rows; (3) readiness is proven by the import gate, not by "the file opened".

### Step 1 — Лок-кит

- **Wireframe.** Drop zone (XLSX/CSV/TSV/TXT, "расширение не важно"), template link + Character/Explanation rule, example-file chips (drive the §6 scenarios). On parse: analysis loading card listing sub-steps → result card «Что мы нашли»: format, columns-in-file-order with detected roles, languages with fill %, rows summary (total · importable · quarantine breakdown, «Показать» → rows side sheet), header rewrites (Russian→ru).
- **Components.** `DropZone`, analysis card, `RowsSheet`.
- **States.** default / parsing (sub-steps) / sheet-choice stop (multi-sheet workbook) / file-unreadable stop / no-key stop / no-language stop / utf16 "extension lies" info note.

### Step 2 — Исходный язык

- **Wireframe.** Verbatim question, two equal cards ru / en (no pre-selection, no "recommended"), each with «Что даёт» / «Чем платим» and this-kit evidence (fill %). Immutable lock marker. Secondary: explanation-language radio.
- **States.** default / source-conflict stop (`en-sparse`: en at 41% → warning with «Выбрать русский» / «Загрузить другой кит»; never proceeds on an inferred source).

### Step 3 — Языки

- **Wireframe.** «Есть в ките» chips (pre-selected, fill %, source locked) · «Пресет студии» chips + «Добавить все» · search. Resolution sub-cards only when the file needs a decision: ambiguous header (pt/pt_BR, zh_Hans/zh_Hant), rare column (tr 2% → язык / не язык), Id-as-language. `Далее` disabled until every resolution is answered.

### Step 4 — Структура кита

- **Wireframe.** Table of non-language columns → role select (Ключ / Говорящий / Пояснение / Комментарий / Служебная + service policy) with "what happens"; language-shaped `Id` auto-renamed to «Unity legacy ID»; a `flags`-named column warns. Identity audit card with per-reason counts and «Показать». One duplicates question (quarantine recommended / stop). Import-gate result card re-run after each change; content-question warnings surface as glossary questions for step 7.

### Step 5 — Компоненты (optional)

- **Wireframe.** Single vs split cards. Split editor: grouping signals in trust order (sheets / group column / key families / external list) with evidence; rule table (component name ← key-start anchors) + substring-trap note; residue select; ambiguity families with per-family destination; identity arithmetic `вход = компоненты + карантин`; per-component gate. No-signal stop (`positional`). Note: routing never uses text meaning, only the key start.

### Step 6 — Профиль проекта

- **Wireframe.** Left column questions, right column sticky evidence card «Что мы измерили в ките» (rows, short-label share, dialogue rows, markup/placeholders/separators, terminal punctuation, explanation coverage). Q1 БДХК search → alert-info import, else questionnaire + store link. Q2 register (UI), Q3 register (dialogue), Q4 profanity (keep / soften level), Q5 CJK politeness (only when ja/ko present). Register/profanity/CJK are the judge's ground truth and cannot be skipped; only Q1's questionnaire keeps «Пропустить».

### Step 7 — Глоссарий (recommended, skippable)

- **7a materials:** term-table drop zone + three optional textareas (без перевода / строго в форме / не использовать →). 7b categories checkboxes. **7c workspace** (`GlossaryWorkspace`, reused on the standalone page): before-extraction card (file, source, targets, categories, «Извлечь термины») → running (queued/stages + processed/total, back-nav preserves state) → suggestions (filters Новые / Спорные / Уже в глоссарии / Отклонённые; per-row category, occurrences, editable explanation, existing translations with «Нет перевода» marked, Добавить / Не добавлять, «Добавить выбранные (N)», «Добавить все (N)» eligible-only; disputed radio must be resolved before add; evidence side sheet with highlighted term). Honest publication: wizard stages «Выбрано — добавится при запуске»; page shows «Добавлено в глоссарий» after success, with partial-success line. **7d exceptions:** row-by-row Одобрить / Отклонить; rules in plain words; forbidden-without-replacement cannot be approved; single-language rules flagged «применим в проекте».
- **States.** before / running / ready(mixed) / empty(«Изменить категории») / failed(«Повторить») / model-not-configured / partial publication — all reachable in `/dev/states`.

### Step 8 — Проверка и запуск

- **Wireframe.** One summary row per step with «Изменить» (source language carries the lock + «не изменится»); translate-only estimate card («Перевод ≈ N строк × M языков · ≈ $ · ≈ время»; judge is a separate run); primary «Сделать локализацию» → run #1 (stages Импорт → Языки → Глоссарий → Перевод → Проверки).

### After the wizard

- Settings → Профиль shows the step-6 answers with «Изменить»; saving opens a confirmation dialog warning that changing the profile updates translator+judge prompts and invalidates the judge's cached verdicts (next quality check is paid for all strings).
- The standalone Глоссарий page mounts the same `GlossaryWorkspace` (7c/7d), so a skipped step is completed later without the wizard.

### Designer's Notes (additions)

- **Source language is a two-card cost question, not a default.** ru vs en changes who can read the original, which pivot the translations inherit, and downstream quality — a fact only the producer knows. Guessing it from column order/fill silently would poison every language; the wizard shows both consequences and this kit's fill, and stops (never overrides) when the chosen column is too sparse.
- **Every question carries measurements.** The producer isn't a translator; a decision is trustworthy only next to the backend's evidence (counts, samples, the exact rows). A question without evidence is a bad screen.
- **Quarantine is shown, never hidden.** A row that can't be imported honestly (empty key, conflicting duplicate, empty-in-all-languages) is listed with its source row number and reason. Silent drop/merge/rename would corrupt the file the engine round-trips; an empty target beside a real source is normal and stays.
- **Character and Explanation are two columns.** They reach different backend destinations (speaker field vs translator/judge context) and must never be merged; both exist in the import file even when empty.
- **Split anchors on the key start and shows the ambiguity set.** Substring matching would misroute UI strings (`dial` inside `label_continue_dialog`); routing by text meaning is never done. Families whose anchor and apparent meaning disagree are surfaced for an explicit per-family decision.
- **Register/profanity/CJK can't be skipped; the glossary can.** The first three are the judge's ground truth — skipping them would make the judge grade against nothing. The glossary improves quality but the pipeline runs without it, so it stays recommended-but-skippable.
- **Exceptions are approved row by row, never inherited from a file.** A "do not translate / forbidden word" rule changes output for every string; uploaded flags are shown as proposals with what-they-prohibit, and a forbidden rule without a replacement can't be approved.
- **Changing the profile warns about judge cost.** Because the profile feeds the judge's prompts, editing it invalidates cached verdicts; the settings dialog states the re-check will be paid before the producer commits.

### API additions (mock, behind the typed client)

`POST uploads/` → `UploadAnalysis`; `GET/PATCH uploads/{id}/` (PATCH re-runs analysis + gate); `GET uploads/{id}/rows/`, `/split-signals/`, `/text-evidence/`; `GET glossary/candidates/` (extended: extraction_id, stable ids, provenance, review_status, eligibility); `POST glossary/extractions/` + `GET glossary/extractions/{id}/` (queued/stage/processed — the read-only candidates GET never starts a paid extraction); `POST glossary/exceptions/preview/`; extended `POST localization/`; `GET/PATCH profile/`. No routes beyond this list.
