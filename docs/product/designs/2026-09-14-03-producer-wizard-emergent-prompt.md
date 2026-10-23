# Producer-фронт HCGameLoc: промпт-2 для Emergent — wizard «Сделать локализацию»

Дата: 2026-09-14. Статус: черновик; продолжение
`docs/product/designs/2026-09-14-02-producer-frontend-emergent-prompt.md`
(промпт-1). Отправляется в Emergent вторым сообщением после того, как
промпт-1 построен (на 2026-09-14 Emergent отдал оболочку, пустой проект и
заглушку «Мастер в разработке» на месте wizard-а).

Источник требований: скиллы `v9833078908/Herocraft-Localization-Skills`
(main @ `455ea10`): `preparing-weblate-loc-kits`,
`splitting-loc-kits-into-components`, `game-glossary-builder`,
`weblate-machinery-prompts`. Wizard — это их интервью, превращённые в
экраны; всё, что скиллы делали руками (разбор формата, проверка схемы,
карантин, гейт `loc_kit_ingest`, извлечение терминов, генерация промптов),
делает бэкенд. Формулировки вопросов и таблиц последствий взяты из скиллов
дословно, чтобы UI и скиллы не разошлись.

Изменения относительно промпта-1 (для бэкенд-плана): правило 8 «исходный
язык = первая заполненная колонка» заменено на явный выбор продюсера
`ru`/`en` с последствиями (бэкенд сам ставит выбранный язык первой колонкой
при записи импортного файла); wizard вырос с 5 до 8 шагов; добавлен
опциональный шаг разбиения кита на компоненты; список эндпоинтов расширен
(§5). Всё остальное из промпта-1 действует без изменений.

---

## Follow-up: build the «Сделать локализацию» wizard (replaces the «Мастер в разработке» stub)

Everything in the first prompt stays binding: tokens, typography, Russian-only UI, «проект» / «лок-кит» terminology, no sign-in screen, the closed API list (extended below, §5), no numeric quality score, no check codes. The sidebar behaviour you built for an empty project (items disabled with the tooltip «Появится после первой локализации») is correct — keep it. This message replaces **screen 4** of the first prompt with a complete specification and asks you to **draw every screen and every state** of the wizard.

### 0. Why the wizard looks like this

Until now the studio ran the same setup through four expert checklists (agent "skills") operated by a specialist: prepare the kit for import, optionally split it into several components, build a glossary, write the LLM prompt fields. Each checklist is an *interview* — a short list of decisions only the producer can make — followed by deterministic work and a verification gate. The wizard is those interviews turned into screens. The producer answers; the backend does the work and shows its evidence. Three principles from the checklists are binding for the UI:

1. **Never guess a product fact.** Source language, regional variant, what a column means, whether a rare language column is real, where a split boundary lies, whether a term may be translated — these are asked, with options and consequences, never inferred and never defaulted silently. When the file contradicts the producer's answer, the wizard shows the evidence and asks again; it does not override the answer.
2. **Bring measurements to every question.** Each question is accompanied by what the backend found in the file: counts, samples, the exact rows concerned. A question without evidence is a bad screen.
3. **Readiness is proven by the gate, not by "the file opened".** The backend runs the real import gate after every answer that changes the file; the wizard shows the gate's own verdict lines and counts (`imported`, `quarantined`, `0 skipped`, source language, resolved languages). Any error = «не готово», with the row.

### 1. Amendments to the binding product rules of the first prompt

- **Rule 7 (languages)** becomes: languages are configured once, in the wizard, from *(a)* the language columns found in the kit, pre-selected, plus *(b)* the studio preset chips. Every later upload is translated into all project languages.
- **Rule 8 (source language)** becomes: the source language is the **producer's explicit choice between `ru` and `en`**, asked once with both options' consequences; it is never derived from column order, fill, file name or text quality. The backend places the chosen language as the first language column when it writes the import file. If the kit contradicts the choice (the chosen column is absent or sparse), the wizard stops with the evidence and asks again. After «Сделать локализацию» the source language is immutable — the wizard says so on the question and on the summary.
- **New rule 13 (quarantine, not repair).** A row the backend cannot import honestly — key empty, duplicate key with different texts, a cross-language hybrid, a row empty in every language — is quarantined with its source row number and reason, listed in the wizard, and never silently dropped, merged, renamed or "fixed". An empty *target* beside a real source is normal and stays.
- **New rule 14 (Character vs Explanation).** A kit may carry two kinds of context: `Character` — the speaker's name, one word — and `Explanation` — usage context a translator cannot see in the string. They reach different destinations in the backend; the wizard shows them as two distinct columns and never merges them. Both columns exist in the import file even when empty.
- **New rule 15 (profile answers are judge ground truth).** Register, profanity policy and CJK politeness answers feed the prompts read by both the translator model and the LLM judge. Changing them later invalidates the judge's cached verdicts (a re-check costs money). The settings screen says so before the producer edits them.

### 2. Wizard shell

Route `/projects/{slug}/localize`, opened only from the empty-project screen («Загрузить лок-кит»). Full page inside the project shell; breadcrumb `Проекты / {проект} / Сделать локализацию`. Left progress rail with 8 steps; a step becomes clickable once reached; steps 5 and 7 show «пропущено» when skipped. «Назад» / «Далее» at the bottom of every step; «Отмена» (tertiary) returns to the empty-project screen and deletes the draft upload. Draft state persists server-side (`uploads/{id}`), so a reload returns to the current step. Every step has: default, loading («Анализируем…» with the sub-steps listed), a *stop-and-ask* state where applicable, an error state, and an «изменение позже» note when the decision is irreversible.

Steps:

1. Лок-кит
2. Исходный язык
3. Языки
4. Структура кита
5. Компоненты *(optional)*
6. Профиль проекта
7. Глоссарий *(recommended, skippable)*
8. Проверка и запуск

### 3. Steps — questions, evidence, states

#### Step 1 «Лок-кит»

Drop zone (XLSX / CSV / TSV / TXT — «расширение не важно, формат определим по содержимому»). Link «Скачать шаблон лок-кита» (downloads a 13-row semicolon CSV with header `key;Character;ru;en;Explanation`; the two-sentence rule under the link: «`Character` — имя говорящего и ничего больше. `Explanation` — всё, чего переводчик не увидит в самой строке.»).

**Loading:** the drop zone morphs into an analysis card listing sub-steps as they complete: «Кодировка и формат» → «Колонки» → «Языки» → «Ключи и дубликаты» → «Проверка импортом». 2–6 s in mock.

**Result card «Что мы нашли»** (read-only summary; decisions come on the next steps):

- Формат: «XLSX, лист "Strings", 3 864 строки» / «TSV UTF-16LE с BOM» / «CSV, разделитель ";"».
- Колонки: chips in file order with the detected role under each: ключ · говорящий · служебная · язык `ru` · язык `en` · … · пояснение.
- Языки: found language columns with fill %: «ru 100 % · en 97 % · de 96 % · Portugal 96 % (уточним вариант) · tr 2 % (уточним, язык ли это)».
- Строки: «3 864 всего · 3 830 готовы к импорту · 34 в карантин (17 пустых во всех языках, 12 дубликатов ключа, 5 без ключа)» with «Показать» → side sheet listing quarantined rows (row №, reason, untouched cells).
- Заголовки, которые переписали: «Russian → ru, English → en» (old → new mapping; the producer's own vocabulary).

**Stop states (no «Далее» until resolved):**

- Файл не читается (unknown encoding, encrypted, binary): «Не удалось прочитать файл. Пришлите экспорт в CSV/TSV/XLSX или опишите формат.» Never a partial parse.
- Книга с несколькими листами строк: «В книге N листов. Одна загрузка создаёт один компонент — выберите лист» (sheet radio list with row counts; the other sheets can become components in step 5).
- Нет колонки ключа: «Не нашли колонку ключа. Укажите её» (column picker).
- Нет ни одной языковой колонки: stop with the header row shown and the hint that headers must be codes (`ru`, `zh-Hans`) or «Название(код)».

#### Step 2 «Исходный язык»

Verbatim question, then the two options as two equal cards (no pre-selection, no «recommended»):

> **Исходным языком делаем русский или английский?** Русский быстрее и удобнее в ежедневной работе; английский потенциально даёт выше качество на части языков. Выбор после создания компонента не меняется.

| Исходный | Что даёт | Чем платим |
|---|---|---|
| **Русский (`ru`)** | Быстрее и удобнее в ежедневной работе: авторы строк, продюсер и QA читают оригинал на своём языке; пояснения, глоссарий, сообщения проверок и промпты подсказок живут на нём же; между оригиналом и переводами нет лишнего звена, которое нужно поддерживать | На части целевых языков доступные подрядчики и модели могут работать с английского лучше, чем с русского, — качество на них может выйти ниже |
| **Английский (`en`)** | Потенциально выше качество на части языков, если доступные там подрядчики и модели работают с английского лучше, чем с русского | Английский придётся писать и вычитывать как настоящий оригинал. Если в ките он получен машинным переводом с русского, каждый перевод унаследует невычитанный пивот, а русскоязычная команда будет вычитывать язык, на котором не пишет |

Under each card, the evidence for *this* kit: «В ките: колонка `ru` заполнена на 100 %» / «колонка `en` заполнена на 97 %». An immutable marker: «После запуска не меняется» (lock icon, `body-sm`, `muted-foreground`).

Secondary question on the same screen, below a divider: «**Язык пояснений** (колонка `Explanation`)»: radio «Как исходный язык» (default) / «Другой: …». Shown only when the kit has an Explanation column or the producer may write explanations later.

**Stop state — kit contradicts the choice:** when the chosen language's column is absent or filled under the threshold: alert-warning «Вы выбрали английский как исходный, но колонка `en` заполнена на 41 % (1 584 из 3 864 строк) — как оригинал она не годится. Показать пустые строки». Buttons: «Выбрать русский» / «Загрузить другой кит». The wizard never proceeds with an inferred source.

#### Step 3 «Языки»

«**На какие языки делаем переводы?**» — a language picker:

- Section «Есть в ките» — chips for every language column found, pre-selected, each with fill %: «en 97 %», «de 96 %». Cannot remove the source.
- Section «Пресет студии» — the remaining preset languages (`en, de, fr, es, pt_BR, tr, ja, ko, zh_Hans`, minus those already present), unselected; «Добавить все».
- Search «Другой язык…» over the platform's language list (codes + Russian names).

Resolution sub-cards appear only when the file needs a decision (verbatim rules from the checklist):

- **Неоднозначный заголовок.** «Колонка `Portugal`: это португальский Португалии (`pt`) или Бразилии (`pt_BR`)?» / «Колонка `Chinese`: упрощённый (`zh_Hans`) или традиционный (`zh_Hant`)?» Radio with a note: «Смешанная лексика внутри колонки — признак неровного перевода, а не варианта; мы не угадываем по словарю.» No default is applied silently; «Далее» is disabled until answered.
- **Редкая колонка.** «Колонка `tr` заполнена в 2 % строк (77 из 3 864). Это язык или остатки вставки?» Options: «Язык — импортировать как есть» / «Не язык — не импортировать». Explain: «Колонки, заполненные меньше чем на 5 %, по умолчанию не импортируются.»
- **`Id` как язык.** «Колонка `Id`: это индонезийский язык или служебный идентификатор?» Options: «Индонезийский (`id`)» / «Служебная колонка» (then handled in step 4).

Footer line: «Каждая загрузка в этот проект будет переводиться на выбранные языки; добавить язык позже можно в настройках.»

#### Step 4 «Структура кита»

Table of the kit's non-language columns, one row each: header (as in file) → role select → what happens with it. Roles: «Ключ» (exactly one, required), «Говорящий (Character)», «Пояснение (Explanation)», «Комментарий для переводчика» (Comment/Context/Note → developer note), «Служебная колонка». For a service column, a second select: «Оставить значения» / «Оставить пустой» / «Не импортировать»; a language-shaped header (`Id`) is renamed automatically to a descriptive name («Unity legacy ID») and the rename is shown. A column literally named `flags` / `weblate-flags` / `флаги` shows alert-warning «Колонка с таким именем не импортируется как флаги — переименуйте её или оставьте служебной».

Below, the identity audit as a read-only card with counts per reason and «Показать» (side sheet of the rows):

- «Точные дубликаты: 9 — оставим первую копию, остальные в карантин»
- «Один ключ, разные тексты: 3 — в карантин до уточнения от разработчиков»
- «Пустой ключ: 5 — в карантин»
- «Пусто во всех языках: 17 — в карантин (такая строка иначе отклоняет весь файл)»
- «Пустой перевод при заполненном оригинале: 1 240 — это нормально, переведём»

One question, with the checklist's recommendation pre-selected: «**Как поступить с дубликатами и битыми строками?**» «В карантин, не угадывать (рекомендуем)» / «Остановиться — пришлю исправленный кит». Note: «Мы никогда не переименовываем ключи ради уникальности и не склеиваем строки по похожести.»

Gate result card at the bottom, re-run after every change on this step: «Проверка импортом: готово · 3 830 строк · 0 пропущено · исходный ru · языки en, de, fr, pt_BR, tr, ja, ko, zh_Hans · пояснения: 412 строк» or «не готово» with the offending row and reason. Warnings that are *content questions*, not defects, are listed as questions for step 7: «`Dead Shell` одинаков во всех языках — это название, которое не переводится?» (they become glossary exception proposals).

#### Step 5 «Компоненты» (optional)

Question: «**Один компонент или разделить лок-кит на несколько?**» Two cards: «Один компонент (по умолчанию)» / «Разделить — например UI, Диалоги, Обучение». Choosing «Разделить» opens the split editor:

- **Признак группировки** — detected signals in trust order, as radio with evidence: «Листы книги: Strings (3 120), Dialogues (744)» / «Колонка `screen` со значениями: shop (812), battle (1 044), …» / «Семейства ключей по началу: `dialog_text_` 388 · `dialog_character_` 96 · `tutorial_` 120 · `mission_` 1 045 · …» / «Внешний список от движка (загрузить)». If none detected — **stop state**: «В ките нет признака группировки: ключи не образуют семейств, листов и колонки группы нет. Загрузите список от движка или оставьте один компонент.» The backend never routes by the meaning of the text; say so in a muted note.
- **Правило** — a table: component name (editable, e.g. «Диалоги») ← anchors (chips: `dialog_text_`, `dialog_character_`); «Добавить компонент». Anchors match the **start** of the key only; the UI shows the count that a substring match would have wrongly caught: «`dial` внутри ключа поймал бы ещё 93 строки интерфейса (`label_continue_dialog`, …) — используем только начало ключа».
- **Остаток** — «В какой компонент попадают ключи, не подошедшие ни под одно правило?» select (default: the component named «UI» if present, else the first). Required; never silently dropped.
- **Спорные семейства** — the ambiguity set: families whose anchor and apparent meaning disagree, each with samples and a per-family choice of destination («`mission_descr_complete_tutorial` (14 строк): Обучение / Миссии»). «Далее» is disabled while any family is undecided.
- **Итог** — arithmetic identity `вход = компоненты + карантин`: «3 864 = 3 453 (UI) + 274 (Диалоги) + 120 (Обучение) + 17 (карантин)»; then per-component gate results (each file passes or fails on its own: «Диалоги: готово · 274 · 0 пропущено»; one failing component blocks «Далее»).

Note under the step: «Все компоненты получают одинаковые колонки и один исходный язык.»

#### Step 6 «Профиль проекта»

This step yields the three prompt fields (`persona`, `style`, `language_instructions`) on the backend. The producer never sees the prompts here; they see their answers and a human summary. Layout: left column — questions; right column — evidence card **«Что мы измерили в ките»** (from the backend's text analysis): «3 864 строки · 61 % короткие подписи (≤ 20 символов) · 744 реплики диалогов · разметка: `<color>` 212, `<b>` 40, `<sprite>` 8 · плейсхолдеры `{0}` 590, `%KEY%` 33 · разделитель `$` 1 102 · буквальный `\n` 0 · строк с `.`/`!`/`?` на конце 38 % · пояснения заполнены в 11 %» plus 3 sample rows each of UI label / tooltip with placeholder / dialogue line. A muted footer: «Профиль составляется только из этих измерений и ваших ответов — ничего не выдумываем.»

Questions (each one decision; options as radio cards):

1. **Игра.** Search «Карточка игры в БДХК» (as in prompt 1). When found with brief and voice/style blocks: alert-info «Профиль проекта импортирован из карточки игры в БДХК: жанр, сеттинг, тон, обращение к игроку». Otherwise the short questionnaire (жанр, сеттинг, роль игрока, аудитория/возраст) plus a field «**Ссылка на страницу игры в сторе** (Google Play, Steam или другой; если страницы ещё нет — название и краткое описание)».
2. **Обращение к игроку в интерфейсе.** «Регистр в интерфейсе (кнопки, подсказки, системные сообщения): на «ты» / на «вы» / формально» — radio. Evidence: 3 samples of shipped UI strings in the source.
3. **Обращение в диалогах.** «В репликах персонажей: как в оригинале (по говорящему) / всегда на «ты» / всегда на «вы»». Evidence: 3 dialogue samples with `Character`.
4. **Мат и грубость.** Verbatim: «Мат и грубость сохраняем в силе источника?» Options: «Сохранять в силе источника — не смягчать и не добавлять» / «Смягчать до …» (select: «лёгкой грубости» / «без мата»). Evidence: count of source rows with profanity markers and 2 samples; when the kit has shipped targets that softened profanity, show one pair as «в существующем переводе смягчено».
5. **Вежливость для японского и корейского.** Shown only when `ja` or `ko` is a project language. «Нужны ли ограничения по вежливости для ja/ko?» Options: «Как в существующих переводах (измерим)» / «Нейтрально-вежливая речь везде» / «Интерфейс нейтрально, диалоги по говорящему». Evidence: the register the shipped `ja`/`ko` columns already use, when present.
6. **Запрещённые слова и обязательные формы** — moved to step 7 (glossary), with a link «Задать в глоссарии →».

Answered → «Далее» shows the **summary card «Профиль проекта»** in plain Russian (genre/setting, what the text consists of, register decisions, profanity policy, CJK politeness) with «Изменить» on each line and a muted note: «Промпты для модели-переводчика и судьи формируются из этого автоматически; посмотреть и править их текст можно в Weblate (Advanced).»

**States:** БДХК search loading / no results / card without B or C blocks («В карточке нет блоков "бриф" и "голос и стиль" — ответьте на вопросы ниже»); evidence card loading skeleton; «Пропустить» on questions 2–5 is *not* offered — these are the judge's ground truth; the questionnaire in Q1 keeps «Пропустить» (studio default profile).

#### Step 7 «Глоссарий» (recommended, skippable)

Alert-info at top: «Глоссарий заметно повышает качество перевода — рекомендуем заполнить. Термины уезжают в модель вместе с каждым запросом.» «Пропустить» is a visible secondary button on every sub-screen; skipping keeps every candidate available later on the «Глоссарий» page.

7a. **Есть ли готовые материалы?** Verbatim: «Есть ли готовые правила или список терминов? Есть ли слова, которые нужно сохранять без перевода, писать строго в одной форме или не использовать?» Layout: drop zone «Таблица терминов (CSV/XLSX: исходный язык, переводы, пояснение)» — optional; three text areas, each optional: «Сохранять без перевода» (one term per line), «Писать строго в одной форме», «Не использовать (укажите замену через →)». Note: «Флаги из вашего файла мы покажем как предложения, а не применим автоматически.» Uploaded table: same analysis states as the kit (header mapping, source-language mismatch → **stop**: «В таблице терминов исходный язык `en`, а в проекте `ru` — термины не совпадут с текстом, который видит переводчик. Пришлите таблицу с колонкой `ru` первой или пропустите шаг.»).

7b. **Что считаем термином?** Verbatim: «Включаем названия предметов, персонажей и мест, а также повторяющиеся игровые понятия? Обычные кнопки и целые реплики не включаем.» Checkboxes, first four on: «Предметы и ресурсы» / «Персонажи» / «Места и локации» / «Повторяющиеся игровые понятия (валюты, режимы, фракции)» / off: «Названия кнопок и меню» / «Целые реплики». Note: «Количество терминов увидите после извлечения — заранее не обещаем.»

7c. **Кандидаты из лок-кита** (backend extraction; shows a loading state «Извлекаем термины…»). Table: term (source) · где встречается («в 42 строках», 3 sample keys on hover) · переводы из кита (per selected language, from existing columns; empty stays empty — «мы не переводим пустые ячейки») · пояснение (one Russian line proposed by the backend, editable) · «Добавить». Header: «Добавить все (N)», filter by category, search. Sub-sections:

- **Спорные** — one source term with divergent translations across the kit («`Крепость`: en `Keep` (38 строк) / `Fortress` (6 строк)») — radio to pick the one that goes into the flat glossary; cannot be added until decided.
- **Вопросы из проверки** — from the step-4 warnings: «`Dead Shell` одинаков во всех языках. Это название, которое не переводится?» → «Да, не переводить» (creates an exception proposal, 7d) / «Нет, обычный термин» / «Не термин».

**Required visual flow: extraction → glossary suggestions → producer selection → publication.** Do not implement 7c as a static pre-filled table or a single spinner. Draw and build the complete clickable mechanism, both inside the wizard and on the standalone «Глоссарий» page:

- **Before extraction:** a card «Предложить термины из лок-кита» with the selected file/version, source language, target languages and terminology categories from 7b. Primary «Извлечь термины». Explain: «AI найдёт повторяющиеся названия и понятия и предложит их для глоссария. Ничего не добавится без вашего решения.» Extraction is a separate backend LLM operation with its own prompt and capable model configured by the administrator through LiteLLM or OpenRouter, not the translation routing model. Never ask the producer for a key or raw prompt. Mock this operation; do not purchase real model calls.
- **During extraction:** show queued/running/no-update states and the current stage: «Читаем лок-кит» → «Ищем термины и контекст» → «Сравниваем с глоссарием» → «Готовим предложения». Display counts only when returned by the operation; never fabricate a completion percentage. Navigating back and returning must preserve the extraction and review state, not start another operation.
- **Suggestions workspace:** title «Предложения для глоссария», counts and filters «Новые», «Спорные», «Уже в глоссарии», «Отклонённые». Each suggestion shows the source term, category, why it qualifies, occurrences, proposed explanation, existing translations with their provenance, and missing translations explicitly marked «Нет перевода». A term suggestion is not an approved translation. Preserve supplied translations; do not silently fill missing targets. Use the selected explanation language, defaulting to the source language, for explanation values; keep interface labels and the rationale in Russian.
- **Evidence detail:** selecting «Посмотреть контекст» opens a keyboard-accessible side sheet with source rows, keys, file/sheet locations, Character and Explanation where available, and the term highlighted in each occurrence. Do not hide the evidence behind hover alone. Allow editing the suggested term and explanation before selection; keep the original evidence visible. Show conflicting meanings as well as competing translations; a conflict cannot be resolved by automatically choosing the most frequent form.
- **Individual and bulk actions:** «Добавить», «Изменить», «Не добавлять», checkboxes, «Добавить выбранные (N)» and «Добавить все (N)». State exactly which set “all” covers, including when filters are active. Bulk addition includes only eligible ordinary suggestions; unresolved conflicts, existing entries and unapproved special rules are excluded with visible counts and reasons. An ordinary term carries no special rule by default; 7d remains a separate approval flow.
- **Honest publication states:** inside the wizard, «Добавить» stages a suggestion in the draft and shows «Выбрано — добавится при запуске», not «В глоссарии». On the standalone glossary page, successful publication shows «Добавлено в глоссарий» only after the operation succeeds. Existing entries are never overwritten. For partial success show «Добавлено 10 · Уже есть 2 · Не добавлено 1» with the unresolved item still actionable; failed writes preserve the producer's selection and edits.
- **Empty and failure states:** draw “no terms found” with «Изменить категории», all suggestions already present, extraction failure with «Повторить», model not configured with an administrator-facing explanation, unresolved conflict, saving, save failure, partial success, and all eligible suggestions handled. «Пропустить» must still allow continuing localization without a glossary; candidates already obtained remain available later.

Prototype contract addition: extraction starts through `POST projects/{slug}/glossary/extractions/` with `{upload_id, scope, source_language, target_languages}` and returns an extraction ID; `GET projects/{slug}/glossary/extractions/{id}/` returns `{id, status, stage, processed_rows?, total_rows?, error?}`. These are proposed mock contracts, not claims about the existing backend. The existing candidates GET is read-only and must never initiate a paid extraction; add an `extraction_id` selector and stable candidate IDs, provenance, review status and eligibility reasons to its response. Wizard selections remain in its draft; standalone additions reuse the glossary terms endpoint from prompt 1. Keep these calls behind the typed API client.

7d. **Особые правила** — the exception review table; nothing is applied until the producer approves it row by row. Columns: термин · перевод (по языкам) · языки · правило простыми словами · зачем · что запретит · решение (Одобрить / Отклонить). Rules in plain words, mapped to backend flags: «Не переводить — оставить как в оригинале» (`read-only`), «Строго в этой форме, без склонения» (`exact`), «Не использовать этот вариант; замена: …» (`forbidden`, replacement required — a forbidden rule with no replacement cannot be approved). Rows come from: 7a lists, uploaded table's own flags (labelled «из вашего файла»), 7c «Да, не переводить», and the profile questionnaire's «запрещённые слова». Notes on the screen: «Правило "строго в этой форме" действует только на языки, для которых в таблице есть перевод»; «Правило для одного языка из нескольких мы применим уже в проекте после запуска» (rows flagged «применим в проекте» stay unflagged in the file). Combination of two rules on one row is not offered.

**Summary** of the step: «Глоссарий: 84 термина · 9 языков · особых правил: 3 одобрено, 1 отклонено · будет создан как компонент "Глоссарий"». Terms are added only; existing terms are never overwritten.

#### Step 8 «Проверка и запуск»

One summary card per step with «Изменить»: Лок-кит (file, rows imported/quarantined, gate verdict) · Исходный язык (with the lock icon and «не изменится после запуска») · Языки (chips) · Структура (roles, service columns, duplicates policy) · Компоненты (one, or the rule + counts identity) · Профиль (the human summary) · Глоссарий (counts, or «пропущен — заполните позже на странице Глоссарий»). Estimate card: «Перевод ≈ 3 830 строк × 8 языков · ≈ $12 · ≈ 20 мин» (translation estimate; the judge is not part of this run). Primary «Сделать локализацию». States: submitting (button spinner, whole page inert), server validation error per step (link to the step), success → run card of run #1 (stages: Импорт → Языки → Глоссарий → Перевод → Проверки).

### 4. After the wizard

- **Настройки проекта → Профиль проекта** shows the step-6 answers with «Изменить»; saving shows a confirmation dialog: «Изменение профиля обновит промпты переводчика и судьи и обнулит кэш вердиктов судьи — следующая проверка качества будет платной для всех строк. Продолжить?».
- **Глоссарий page** gains the same 7c/7d cards (candidates list and exception review), so a skipped step is completed later without the wizard.
- **Загрузить → Лок-кит** (repeat uploads) reuses step 1 + step 4's audit card and the diff card from prompt 1; source language and column roles are shown read-only («настроено при первой локализации»); a new language column in the kit triggers the step-3 resolution sub-cards inline.

### 5. API — additions to the closed list

Same rules as before: `src/api/client.ts` is the only module that knows URLs; mock fixtures; no other routes.

```text
POST   projects/{slug}/uploads/                 (as before) → UploadAnalysis
GET    uploads/{id}/                             → UploadAnalysis
PATCH  uploads/{id}/                             {source_language?, explanation_language?, languages?: [{code, include: bool, resolved_as?: code}],
                                                  columns?: [{header, role: key|character|explanation|note|service, service_policy?: keep|empty|drop, rename?}],
                                                  duplicates_policy?: quarantine|stop, split?: {rule: [{name, anchors[]}], residue, ambiguity: {family: component}}}
                                                 → UploadAnalysis   (re-runs analysis and the import gate with the answers)
GET    uploads/{id}/rows/?set=quarantine|duplicates|empty_all|sample|family&value=&page=  → Row[] {n, reason?, cells{}}
GET    uploads/{id}/split-signals/               → {sheets: [{name, rows}], group_columns: [{header, values: [{value, rows}]}],
                                                  key_families: [{anchor, rows, samples[]}], substring_trap?: {anchor, extra_rows, samples[]}}
GET    uploads/{id}/text-evidence/               → {rows, short_label_share, dialogue_rows, markup: {}, placeholders: {}, separators: {dollar, newline},
                                                  terminal_punct_share, explanation_coverage, profanity_rows, samples: {ui[], tooltip[], dialogue[], softened[]},
                                                  shipped_register: {ja?, ko?}}
GET    projects/{slug}/glossary/candidates/?upload=&scope=  → {terms: [{term, rows, sample_keys[], translations{}, explanation, category}],
                                                  disputed: [{term, options: [{translation, language, rows}]}],
                                                  questions: [{term, kind: same_in_all_languages|wrong_script, languages[]}]}
POST   projects/{slug}/glossary/exceptions/preview/  {lists: {read_only[], exact[], forbidden: [{term, replacement}]}, upload_flags?: bool, answers: {…}}
                                                 → Exception[] {term, translations{}, languages[], rule: read_only|exact|forbidden, replacement?, why, prohibits, exportable: bool}
POST   projects/{slug}/localization/             {upload_id, source_language, explanation_language, languages[], columns[], duplicates_policy, split?,
                                                  profile: {bdhc_title_id?, store_url?, questionnaire?, register_ui, register_dialogue, profanity, cjk_politeness?},
                                                  glossary?: {upload_id?, scope[], terms: Term[], exceptions: [{…, approved: bool}]}} → Run
GET    projects/{slug}/profile/                  → {answers, summary, evidence, judge_cache_invalidates_on_change: true}
PATCH  projects/{slug}/profile/                  {answers} → {answers, summary, judge_cache_invalidated: true}
```

`UploadAnalysis` = `{id, format: {kind, encoding, delimiter?, sheet?, sheets?}, columns: [{header, role, language?, fill, renamed_to?, ambiguous?: [codes], low_fill?: bool}], header_map: [{from, to}], rows: {total, importable, quarantined_by_reason: {}, empty_targets}, source_language?, source_conflict?: {chosen, fill, rows}, gate: {ready, imported, skipped, source, languages[], explanations, errors: [{row, reason}], questions: [{term, kind}]}, split?: {components: [{name, rows, gate}], quarantine, identity: "3864 = 3453 + 274 + 120 + 17"}}`.

### 6. Fixtures to add

- **«Новый проект» + `pirate-ships.xlsx`** (the happy path with every decision reachable): 3 864 rows; headers `key;Id;Character;Russian;English;de;Portugal;tr;ja;ko;Chinese;Explanation` → mapping `Russian→ru`, `English→en`; `Portugal` ambiguous, `Chinese` ambiguous, `Id` language-shaped service column, `tr` at 2 % fill; quarantine 17 empty-all + 12 duplicates (9 exact, 3 conflicting) + 5 empty key; key families `dialog_text_` 388, `dialog_character_` 96, `tutorial_` 120, `mission_` 1 045, residue UI; substring trap 93; ambiguity family `mission_descr_complete_tutorial` (14); text evidence as in step 6; glossary candidates 84 with 2 disputed (`Крепость`, `Осколок`), 3 questions (`Dead Shell`, `xray m2`, `+50% HP`); exception proposals 4.
- **`utf16-engine-export.txt`** — a UTF-16LE TSV with BOM whose extension lies; analysis succeeds and says so.
- **`workbook-3-sheets.xlsx`** — triggers the sheet-choice stop state in step 1 and sheet signals in step 5.
- **`positional-export.csv`** — keys are the English text; no grouping signal → step-5 stop state.
- **`en-sparse.xlsx`** — `en` filled 41 % → step-2 contradiction stop state when `en` is chosen.
- **`glossary-en-first.csv`** — glossary table with `en` first while the project source is `ru` → step-7a stop state.
- Existing project fixtures («Pirate Ships» localized) gain `profile` answers and a settings screen state with the judge-cache warning.

### 7. What to deliver in this iteration

1. Replace the «Мастер в разработке» stub with the full 8-step wizard, every step in every state listed above (default, loading, stop-and-ask, error, irreversible note), reachable from the fixtures in §6 without editing code.
2. Add every new state to `/dev/states`, grouped by step.
   Include a dedicated extraction-and-suggestions storyboard: before extraction → running → mixed suggestions → evidence sheet → edited suggestion → draft selection → published glossary. Provide clickable fixtures for no results, already-existing terms, conflicts, unconfigured model, extraction failure and partial publication. Demonstrate adding one term and multiple terms, and verify that rejected suggestions and existing entries remain unchanged. A table with hardcoded suggestions and nonfunctional buttons does not satisfy this deliverable.
3. Update `DESIGN-NOTES.md`: per step — wireframe, component inventory, interaction spec, states; and extend **Designer's Notes** with: why the source language is a two-card question with a cost table instead of a default; why questions carry measurements; why quarantine is shown and never hidden; why Character and Explanation are two columns; why the split rule anchors on the key start and shows the ambiguity set; why register/profanity questions cannot be skipped while the glossary can; why exceptions are approved row by row and never inherited from a file; why changing the profile warns about judge cost.
4. Update `src/api/types.ts`, `client.ts` and `mock/` for §5; no other routes.

Constraints recap (unchanged plus new): Russian UI; «проект» / «лок-кит»; tokens as given; no sign-in; no numeric quality score; never infer the source language; never apply a glossary rule without a per-row approval; never hide a quarantined row; never route a split by text meaning; «Далее» stays disabled while any stop-and-ask question is unanswered.
