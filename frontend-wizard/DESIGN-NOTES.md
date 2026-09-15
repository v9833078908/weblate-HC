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

### 4. «Сделать локализацию» wizard — `screens/Wizard.jsx`
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
