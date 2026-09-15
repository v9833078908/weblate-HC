# Producer-фронт HCGameLoc: промпт-3 для Emergent — универсальный лёгкий wizard

Дата: 2026-09-15. Статус: follow-up prompt; продолжение
`docs/product/designs/2026-09-14-02-producer-frontend-emergent-prompt.md` и
`docs/product/designs/2026-09-14-03-producer-wizard-emergent-prompt.md`.

Источник правил для стор-текстов:
`/Users/eli/Downloads/lokalizaciya-tekstov-dlya-storovstim.md`. Этот промпт
заменяет восьмишаговую визуальную структуру из промпта-2 на адаптивный
четырёхэтапный wizard. Бэкенд-инварианты подготовки кита, карантина,
глоссария и профиля сохраняются, но технические подробности больше не
являются обязательными экранами счастливого пути.

---

# Follow-up: simplify «Сделать локализацию» and support store `.txt` files

Keep the visual system, Russian-only UI, project shell, native Weblate authentication, quality rules, glossary extraction flow and all backend safety invariants from the previous prompts. This prompt **supersedes the visible 8-step wizard structure** where it conflicts with prompt 2.

The current wizard is too heavy: it exposes the sequence of the expert skills instead of the producer's task. Redesign it as one **adaptive universal wizard** that accepts either:

1. a table-like loc-kit (`.xlsx`, `.csv`, `.tsv`, table-like `.txt`); or
2. store texts as individual `.txt` files, a folder, or a ZIP (`metadata.zip`).

The producer should not have to know what a Weblate component, base-language directory, file mask, translation flag, language alias or `metadata/<locale>/<field>.txt` path is. The backend creates all of those.

## 1. Interaction thesis: one happy path, conditional questions

Replace the 8 visible steps with **4 stages**:

1. **Файлы** — upload anything; detect what it is.
2. **Языки** — choose source and target languages.
3. **Контекст и термины** — project profile plus optional glossary suggestions.
4. **Проверка и запуск** — plain-language summary, estimate, start.

Do not show «Структура кита» and «Компоненты» as permanent steps. Their safety rules still apply on the backend, but the UI surfaces them only as an inline card **«Нужно уточнить»** when the file is ambiguous or invalid. Put full diagnostics under an expandable **«Технические детали»** section, closed by default.

Happy-path target:

- Known loc-kit: upload → source/targets → context/glossary → run.
- Known store package: upload → confirm detected store and fields → source/targets → context/glossary → run.
- No screen may ask the producer to configure metadata paths, file masks, source base directories, Weblate flags or component formats.

The progress rail shows only the four stages. Conditional questions appear inside the current stage and disappear once resolved. Preserve answers and upload analysis across reloads and Back/Next navigation.

## 2. Stage 1 «Файлы» — one universal upload surface

Replace the loc-kit-only dropzone with:

**Heading:** «Загрузите файлы для локализации»

**Supporting copy:** «Лок-кит XLSX/CSV/TSV или тексты для Steam, Google Play, App Store и других площадок. Можно загрузить TXT-файлы, папку или ZIP — тип определим автоматически.»

One large dropzone accepts:

- one table file;
- one or several `.txt` files;
- a directory through the browser folder picker;
- a ZIP archive.

Secondary actions below it:

- «Выбрать файлы»;
- «Выбрать папку»;
- «Скачать шаблон лок-кита».

Do not force the user to choose «лок-кит» or «стор» before upload. Detect first. Ask only if content is genuinely ambiguous.

### Detection rules and resulting cards

#### A. Table-like loc-kit detected

Show a compact success card:

> **Лок-кит**  
> `pirate-ships.xlsx` · 3 864 строки · 11 языковых колонок  
> 3 830 строк готовы · 34 будут вынесены отдельно

Primary «Продолжить». Secondary «Посмотреть анализ» opens the existing detailed analysis from prompt 2 in a side sheet: columns, header mapping, duplicates, quarantine, import-gate result. Do not force the producer through those details when the backend has resolved everything safely.

#### B. Store-text bundle detected

Show one card per detected store package:

> **Steam**  
> 3 текстовых поля · исходные файлы на русском  
> `steam_short.txt` · `steam_about.txt` · `steam_legal.txt`  
> Разметка: BBCode · лимиты: из справочника Steamworks

or:

> **Google Play**  
> 3 поля · `title.txt` · `short_description.txt` · `full_description.txt`  
> Лимиты применятся автоматически

or:

> **App Store**  
> 5 полей · `name.txt` · `subtitle.txt` · `description.txt` · `keywords.txt` · `promotional_text.txt`  
> Лимиты применятся автоматически

Actions: «Продолжить» and «Проверить поля».

«Проверить поля» expands a simple field list — not a file-system tree:

| Поле | Файл | Символов | Лимит | Разметка | Состояние |
|---|---|---:|---:|---|---|
| Короткое описание | `steam_short.txt` | 214 | 300 | Нет | Готово |
| Об игре | `steam_about.txt` | 2 451 | 8 000 | BBCode | Готово |
| Юридическая информация | `steam_legal.txt` | 612 | — | Нет | Готово |

The field label is primary; filename is muted monospace evidence. Never lead with a filename or path.

#### C. One ambiguous `.txt` file

Do not guess. Show one inline question:

> **Что находится в файле `description.txt`?**

Options:

- «Таблица строк лок-кита» — several records separated by tabs/semicolons;
- «Одно поле страницы стора» — one continuous text;
- «Другое».

If «Одно поле страницы стора» is chosen, ask in the same card:

1. «Для какой площадки?» — Steam / Google Play / App Store / Другая;
2. «Какое это поле?» — picker populated from that platform's known field schema.

No new wizard stage.

#### D. Unknown or custom store files

Show a lightweight mapping card only for unrecognized names:

> **Не узнали 2 имени файла. Укажите, какие это поля.**

Rows: filename → editable field label → optional limit → markup picker (Нет / BBCode / HTML).

For a limit, show:

- a value from the backend's versioned store registry with a link/label to its source; or
- for a custom field, «Лимита нет» / «Указать лимит» plus required «Источник лимита» URL/text.

Never invent a limit from memory.

### Store-specific backend work that must stay invisible

The prototype should communicate the result, not ask the producer to perform these operations:

- Build `metadata/<locale>/<field>.txt` with text-only contents.
- Create one Weblate content component per store package.
- Configure the monolingual base directory from the selected source locale.
- For Google Play, use canonical filenames and built-in field limits:
  `title.txt`, `short_description.txt`, `full_description.txt`.
- For App Store, use canonical filenames and built-in limits:
  `name.txt`, `subtitle.txt`, `description.txt`, `keywords.txt`, `promotional_text.txt`.
- For Steam, generate safe prefixed filenames such as `steam_short.txt`, `steam_about.txt`, `steam_legal.txt`. Never call Steam files `description.txt`, `short_description.txt`, `full_description.txt`, `title.txt`, `name.txt` or `keywords.txt`, because Weblate would silently apply Google Play/App Store limits.
- Detect Steam BBCode (`[b]`, `[list]`, `[*]`, `[url=]`, `[img]`) and apply the BBCode validation policy. HTML/Unity tags use the global markup policy. Apply field-specific `max-length` only from the versioned registry/source.
- Preserve filled target-language files and translate only missing or source-changed content.

The UI may show «Настроено автоматически» with a details popover; it must never expose raw Weblate flag names on the happy path.

### Stage-1 loading state

Use one analysis card with content-specific stages.

For a loc-kit:

«Читаем файл» → «Находим колонки и языки» → «Проверяем строки» → «Проверяем импорт».

For store text files:

«Читаем файлы» → «Определяем площадку и поля» → «Проверяем лимиты и разметку» → «Готовим набор».

Never fabricate a percentage. Show the current completed stages and file count.

### Stage-1 exception states to draw

Build all of these as clickable fixtures and add them to `/dev/states`:

1. table-like TXT detected as a loc-kit;
2. Steam TXT bundle detected;
3. Google Play folder detected;
4. App Store ZIP detected;
5. single ambiguous `description.txt`;
6. mixed files from two stores — group them into two proposed packages and ask «Создать два набора: Steam и Google Play?»;
7. unknown filenames requiring field mapping;
8. duplicate files mapped to the same field — require choosing one;
9. empty source TXT — «В файле нет текста» with «Удалить файл» / «Заменить»;
10. unreadable encoding/binary/encrypted ZIP — stop, never partially parse;
11. broken ZIP/path traversal entry — reject the archive with a plain-language error;
12. source markup is unbalanced — show the field and offending tag; allow replacing the file, not silently repairing it;
13. field over its source-store limit — warning with count, source of the limit and «Заменить файл»; do not silently truncate;
14. no store registry limit exists — ask only for that missing fact, not every field.

## 3. Stage 2 «Языки» — shared screen, adaptive evidence

The screen always asks the same two producer questions, regardless of input type.

### Question 1: source

Use the two equal Russian/English cards from prompt 2:

> **Какой исходный язык?**  
> Русский — проще ежедневный workflow. Английский — потенциально чуть лучше качество локализации на части языков, только если английский написан и вычитан как настоящий оригинал.

Cards:

- **Русский (`ru`)** — «Проще для авторов, продюсера и QA; нет дополнительного английского пивота.»
- **Английский (`en`)** — «Может дать лучше результат на части языков; английский должен быть вычитан человеком, иначе все языки унаследуют ошибки пивота.»

Neither is pre-selected. Both show input-specific evidence:

- loc-kit: fill and row counts for the `ru`/`en` columns;
- store bundle: source-folder/file coverage — «Русский: 3 из 3 полей» / «Английский: 0 из 3 полей».

If a language does not have a complete source set, keep the option visible but disabled with the exact reason and actions «Добавить недостающие файлы» / «Выбрать другой язык». The source is immutable after launch.

### Question 2: targets

> **На какие языки делаем переводы?**

Reuse the searchable language picker, but adapt locale codes for the selected store:

- loc-kit: language columns from the kit pre-selected; studio preset available;
- store: studio preset pre-selected, displayed as Russian language name plus the platform locale, for example:
  - «Английский — `en-US`»;
  - «Португальский (Бразилия) — `pt-BR`»;
  - «Китайский, упрощённый — `zh-Hans`»;
  - «Испанский (Испания) — `es-ES`»;
  - «Испанский (Латинская Америка) — `es-419`».

The backend maps project language to the platform locale. Ask only where several store locales are materially different:

- `pt-BR` vs `pt-PT`;
- `es-ES` vs `es-419`;
- `zh-Hans` vs `zh-Hant`.

For Steam, English must resolve to the accepted regional locale (`en-US` in the current product contract); do not offer «просто English» when the format rejects it. Show automatic mappings in a compact confirmation list under the picker; hide the generated folder paths under «Технические детали».

If uploaded target-language store files already exist, label those languages «В файлах уже есть перевод» and preserve them. The run fills only missing or source-changed fields.

## 4. Stage 3 «Контекст и термины» — one calm page

Keep the project-profile questions and glossary extraction mechanism from prompt 2, but combine them into progressive cards instead of separate permanent wizard steps.

### Card A: project context

If a BDHC card supplies a complete brief/voice profile, show a compact imported summary and no questionnaire by default. «Изменить ответы» expands it.

If not, show only the unanswered questions: genre/setting/player role, address register, profanity policy, and Japanese/Korean politeness when those languages are selected. Use evidence from the uploaded content.

For store text input, evidence is field-aware:

- field label and store;
- character limit;
- markup type;
- sample source text;
- distinguish headline/short description/long description/keywords/legal text.

The backend-generated profile must tell the translator that a headline remains a headline, keywords remain keywords and legal text is not rewritten as marketing copy. Raw prompt fields remain Advanced-only.

### Card B: glossary

Keep the complete extraction → suggestions → selection → publication flow added in prompt 2. It must work for **both** input types:

- loc-kit: extract reusable terms from rows, keys, Character and Explanation;
- store texts: extract game names, character names, locations, factions, currencies, modes, named features and branded terminology from all uploaded fields.

For store text, generic marketing words, CTAs, SEO filler, whole sentences and platform boilerplate are not glossary terms by default. Every suggestion shows the field/file and source span that supports it.

Before extraction, show:

> **Предложить термины из загруженных файлов**  
> AI найдёт названия и повторяющиеся игровые понятия. Ничего не добавится без вашего решения.

Primary «Извлечь термины». Secondary «Пропустить».

The extraction uses the separate glossary-extraction model configured through LiteLLM/OpenRouter, not the translation model. Mock it; no real paid calls.

The suggestions workspace, evidence side sheet, editing, «Добавить», «Не добавлять», «Добавить выбранные (N)», «Добавить все (N)», conflicts, existing-term protection, partial success and all states from prompt 2 remain binding. During the wizard, selected terms say «Выбрано — добавится при запуске», never «Добавлено в глоссарий».

## 5. Conditional «Нужно уточнить» instead of permanent technical steps

All safety logic from prompt 2 remains, but it appears only when needed.

For loc-kits, a single attention card may ask about:

- ambiguous language headers;
- a column that could be Indonesian `id` or an identifier;
- a language column filled under 5%;
- duplicate/malformed row policy;
- optional component splitting.

Use safe defaults only where previously approved: quarantine exact duplicates/malformed identities; one component unless the producer requests splitting. Still require the producer's answer for source language, regional variants and unresolved conflicting rows.

For store texts, a single attention card may ask about:

- store/platform when filenames are ambiguous;
- mapping an unknown file to a store field;
- selecting one of duplicate files;
- missing source fields;
- unknown official limit or markup policy;
- regional locale ambiguity.

Resolved attention items collapse to one success line, for example:

> ✓ Уточнения завершены: App Store · 5 полей · португальский `pt-BR` · HTML не используется

No producer-facing screen should use the terms «монолингвальный базовый файл», «маска файла», `bbcode-text`, `max-length`, `metadata/*`, `state:empty` or «компонент» during the happy path. Those are backend/report details.

## 6. Stage 4 «Проверка и запуск»

Show a summary adapted to the detected content.

### Loc-kit summary

- «Лок-кит · 3 830 строк»;
- source and targets;
- «34 строки вынесем в отдельный файл»;
- selected profile;
- glossary term count or «пропущен»;
- optional split summary only if enabled.

### Store summary

- «Steam · 3 текстовых поля» or «Google Play · 3 поля»;
- source language and resolved target locales;
- field list with character counts, limits and markup in plain words;
- «Создадим набор файлов для каждой локали автоматически»;
- «Заполненные переводы сохраним; переведём только пустые поля»;
- selected profile;
- glossary term count or «пропущен».

Never show generated filesystem paths on the default summary. «Технические детали» may show the output contract for debugging.

Estimate card:

- loc-kit: «≈ 3 830 строк × 8 языков · ≈ $12 · ≈ 20 мин»;
- store: «3 поля × 8 языков · ≈ $0.80 · ≈ 3 мин».

Primary «Сделать локализацию».

Run stages adapt by content:

- loc-kit: «Подготовка кита → Языки → Глоссарий → Перевод → Проверки»;
- store: «Подготовка файлов → Языки → Глоссарий → Перевод → Проверка лимитов и разметки → Сборка ZIP».

After completion, store content opens a result screen with:

- «Скачать ZIP»;
- «Поля по языкам» with per-language accordions and one-click copy;
- a plain-language readiness summary;
- any blocked field shown in «Требуют решения» with source, target, store limit/markup reason and «Исправить с помощью AI» / «Открыть в Weblate».

A blocked target remains present as an empty `.txt` file/value; never remove the field from the package.

## 7. Store output contract represented in the prototype

Draw the producer-facing outcome, while the mock data models these backend rules:

- One package/component per store.
- Output ZIP contains `metadata/<platform-locale>/<field>.txt`.
- Each TXT contains only the field text, encoded as UTF-8.
- Google Play and App Store use their canonical filenames and built-in limits.
- Steam/custom use safe store-prefixed filenames and explicit versioned limits.
- Regional locales stay distinct: `pt-BR` ≠ `pt-PT`, `es-ES` ≠ `es-419`, `zh-Hans` ≠ `zh-Hant`.
- Source folder uses the locale expected by the platform; the backend chooses it from the explicit source-language answer and store mapping.
- Existing filled target files are preserved.
- Source and target files are never truncated to satisfy a limit.

## 8. Mock API additions

Extend the existing typed API client; do not invent routes outside this list.

```text
POST  projects/{slug}/uploads/
      multipart {files[], relative_paths[]?, archive?, declared_kind?}
      → UniversalUploadAnalysis

PATCH uploads/{id}/
      {content_kind?: loc_kit|store,
       store_packages?: [{id, platform: steam|google_play|app_store|custom,
                          files: [{file_id, field_id, field_label?, limit?, limit_source?, markup?}]}],
       source_language?, target_languages?: [{project_code, platform_locale}],
       resolutions?: {ambiguous_file_kind?, duplicate_file_choice?, regional_variants?}}
      → UniversalUploadAnalysis

GET   stores/registry/
      → [{platform, version, source_url, fields: [{id, label_ru, canonical_filename,
                                                   limit?, markup, forbidden_filename_for_other_stores?}],
           locales: [{project_code, platform_locale, ambiguous_with?: []}]}]

POST  projects/{slug}/localization/
      {upload_id,
       source_language,
       target_languages: [{project_code, platform_locale?}],
       content: {kind: loc_kit, ...existingKitAnswers}
              | {kind: store, packages: [{platform, fields[]}]},
       profile,
       glossary?}
      → Run
```

`UniversalUploadAnalysis` is a discriminated union:

```text
{
  id,
  detected_kind: "loc_kit" | "store" | "ambiguous",
  confidence,
  requires_answer: bool,
  loc_kit?: UploadAnalysisFromPrompt2,
  store?: {
    packages: [{
      id, platform, detected_from,
      source_locale_candidates[],
      fields: [{file_id, filename, relative_path?, field_id?, label_ru,
                chars, limit?, limit_source?, markup,
                status: ready|empty|over_limit|broken_markup|unknown_field|duplicate}]
    }],
    existing_target_locales[],
    generated_output_summary
  },
  attention: [{id, kind, question_ru, options[], blocking}],
  errors: [{file_id?, message_ru}]
}
```

These are proposed prototype contracts, not claims that the current Weblate REST API already exposes them. Keep all calls behind `src/api/client.ts`; mock mode only.

## 9. Fixtures and screens to draw

Add clickable fixtures for:

1. loc-kit happy path with technical details collapsed;
2. Steam: three TXT files, BBCode in `steam_about.txt`, all ready;
3. Google Play: `metadata.zip` with `ru-RU` source folder and canonical fields;
4. App Store: folder upload with five canonical fields;
5. custom store: two unknown TXT files requiring labels and one documented limit;
6. single ambiguous `description.txt`;
7. one upload containing Steam and Google Play files, split into two store packages;
8. existing French and German store files preserved, only six target locales need translation;
9. English selected as source but only two of three English files exist;
10. Portuguese and Spanish regional-locale questions;
11. source text over limit;
12. broken Steam BBCode;
13. empty TXT;
14. unknown filename;
15. duplicate files for one field;
16. unreadable/encrypted ZIP;
17. completed store run and download/copy result;
18. target field blocked by length and exported empty;
19. glossary extraction from store texts with suggestions, conflict, existing term and partial publication.

Every fixture must be reachable through the UI without changing source code. Add all default/loading/success/attention/error states to `/dev/states`.

## 10. Deliverables for this iteration

1. Replace the current heavy wizard with the adaptive four-stage version.
2. Make the same wizard accept loc-kits and store TXT/ZIP/folder input from one upload surface.
3. Draw and implement every state in §2, §3, §4, §6 and §9, including the final store result screen.
4. Keep technical analysis accessible but collapsed; the happy path must not require opening it.
5. Update `src/api/types.ts`, `src/api/client.ts`, mock fixtures and `/dev/states`.
6. Update `DESIGN-NOTES.md` with a flow diagram for both branches and explain:
   - why detection happens after upload instead of asking content type first;
   - why the wizard has four permanent stages;
   - why technical questions are conditional;
   - why filenames are evidence but field labels lead the UI;
   - why platform paths, base files and flags are backend-only;
   - how existing target files are preserved;
   - how store locale ambiguity differs from a general language picker;
   - why glossary extraction remains optional but available for store texts.

## Acceptance scenarios

1. Producer drops `steam_short.txt`, `steam_about.txt`, `steam_legal.txt`; the wizard identifies Steam without questions, shows three Russian field labels, asks source/targets, optionally extracts terms, starts translation and returns a ZIP plus copyable fields.
2. Producer drops `description.txt`; the wizard asks one inline question to resolve whether it is a table or a store field, then continues without introducing another permanent step.
3. Producer drops a valid XLSX loc-kit; the happy path is no longer eight screens. Detailed quarantine and schema evidence remain available from «Посмотреть анализ».
4. Producer chooses Google Play targets; the picker uses regional platform locales and distinguishes `pt-BR`/`pt-PT`, `es-ES`/`es-419`, `zh-Hans`/`zh-Hant`.
5. Producer uploads Steam text with `[b]` unclosed; the wizard identifies the field and tag, does not repair or truncate the source silently, and preserves the rest of the draft.
6. Producer skips the glossary; localization continues, and extraction remains available later on the project Glossary page.
7. Producer returns after reload; upload analysis, resolved questions, glossary selections and current stage are preserved.

Final constraints: Russian UI; no auth screens; one universal upload surface; four permanent stages; no producer-facing Weblate plumbing; no invented limits; no silent filename-based store-limit mistakes; no truncation; no overwriting existing target texts; no paid model calls in the prototype; every conditional and failure state must be drawn and clickable.
