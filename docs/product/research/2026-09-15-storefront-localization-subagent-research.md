# Локализация текстов магазинов: полные отчёты субагентов

Дата исследования: 2026-09-15.
Статус: исследование, не утверждённая спецификация и не план реализации.

## Область и происхождение

Запрос: расширить текущий HCGameLoc Weblate на Steam, Epic Games Store, GOG,
Xbox, PlayStation и Nintendo Switch / Switch 2, сохраняя форматирование и учитывая
правила перевода каждой площадки; существующие Google Play / App Store не исключаются.
Исходный пользовательский материал: файл «lokalizaciya-tekstov-dlya-storovstim.md».

Ниже сохранены полные итоговые тексты семи успешных исследователей:
шесть внешних исследований через Exa и официальные источники на
`openai-codex/gpt-5.6-luna` и отчёт `CurrentStoreSupport` типа scout о текущем коде.
Точная модель scout в результате инструмента не указана.
Последующие дополнения, исправления и заменяющие отчёты сохранены отдельно,
в порядке «первоначальный отчёт → уточнение». Это не сокращённая сводка.
Внутренние рассуждения, служебные JSON-события, зашифрованные поля reasoning,
учётные данные и полные HTML-ответы инструментов не включены.

Предшествующие запуски не дали исследовательских результатов:
шесть researcher на `anthropic/claude-sonnet-5` завершились с 429 rate limit;
три резервных task на `bifrost-litellm/aliyun/qwen3.8-flash` — с 429
No deployments available. После явного выбора Luna все шесть процессов завершились
с кодом 0; модель подтверждена в метаданных ответов.

## Как читать архив

Полные сообщения приведены в блоках Markdown без сокращения содержания.
Единственная нормализация цитат — удаление пробелов в концах строк стандартным
репозиторным formatter. Это цитаты исследователей, а не автоматически принятые
требования продукта. При противоречии приоритет имеет
последующее исправление. Ссылки и формулировки внутри цитат сохранены для аудита;
это не означает, что каждая первоначальная ссылка подтверждает приписанное ей утверждение.
Номера строк репозитория — снимок на дату исследования и могут сместиться.

### Исправления и ограничения, обязательные для дальнейшей работы

- **Steam:** публичная документация не подтверждает точное число 300 для Short
  Description, полный синтаксис разметки или схему экспорта. Сторонний процессор
  Steam JSON — гипотеза, не контракт Valve.
- **Epic:** `Localization Field` и `Field Description` помогают найти строку,
  но не объявлены стабильными внешними идентификаторами. UTF-8 CSV не означает
  байтовый подсчёт длины текста. Точные counting semantics не раскрыты.
- **Xbox:** официальные страницы расходятся по CSV/XLSX. Нельзя считать один из
  форматов единственным или переносить схему MSIX на Xbox без реального экспорта.
  Утверждение первого отчёта о приоритете «более нового» документа не заменяет
  проверки актуального Partner Center. Использовать заменяющий отчёт ниже.
- **PlayStation:** абзац первого отчёта про SEC-договор **отозван**: полученный
  материал не подтверждал утверждение. Сохранён только как история ошибки;
  не использовать SEC-ссылку как доказательство. Статья Sony подтверждает доступ
  к документации после GDPA, но не спецификацию Store metadata. Публичный HTML и
  старые PS3 browser-документы не задают контракт PS5.
- **Nintendo:** TTS/GameChat не доказывают локали игры или eShop. Наблюдаемое
  отображение карточки не доказывает обязательность полей или формат ввода.
  Доступность регистрации Switch 2 — датированное наблюдение, не постоянное правило.
- **GOG:** языки игры/build/GALAXY нельзя принимать за языки описания Store.
  Checklist использует EN/DE/FR/CN/PL/RU; точные locale mappings неизвестны.
  QA-правила для build/материалов не следует без проверки превращать в валидатор
  конкретного поля Store description.
- **Все площадки:** рекомендации архитектуры внутри отчётов не утверждены.
  Нет разрешения автоматически добавлять claims, compatibility warnings или
  функции, которых не было в исходных данных. Непубличные требования остаются
  неизвестными до получения разрешённых партнёрских материалов.

## Сопоставление с текущим кодом

Основной агент непосредственно прочитал следующие участки:

- `weblate/formats/txt.py:65–84`: импорт использует `content.strip()`,
  сериализация добавляет LF. Точное сохранение крайних пробелов, пустых строк,
  исходного финального перевода строки и исходных line endings не гарантируется.
- `weblate/formats/txt.py:128–145`: ограничения выбираются по имени файла,
  а catch-all `*.txt` не получает флаги длины.
- `weblate/formats/txt.py:202–213,250–265`: каталоговый monolingual-формат,
  `language_format = "googleplay"`; пустой изменённый файл удаляется.
- `weblate/checks/markup.py:275–305`: BBCodeCheck сравнивает число найденных
  тегов и множество их имён; это не полная проверка платформенной грамматики.
- `weblate_customization/src/weblate_customization/checks.py:920–940`:
  GameMaxLengthCheck предварительно обрабатывает текст через
  `_length_budget_text`. Это не доказательство корректного метра каждого стора.
- `weblate/machinery/llm.py:220–249,1025–1076`: общий prompt требует
  сохранения formatting/whitespace/line breaks; специального storefront profile
  в контексте строки нет.
- `weblate/trans/judge_loop.py:111–137,274–292`: передаются ключ, языки,
  пояснения, глоссарий и checks; проектный контекст — persona/style.

Это чтение кода, не проверка работающей инстанции. Тесты, изменения кода,
публикации в магазины и обращения к production не выполнялись.

Дополнительно основной агент проверил мобильный baseline:
[Apple Platform version information](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information/)
документирует Promotional Text 170 characters, Description 4000 characters plain
text и Keywords 100 bytes. В текущих filename patterns нет отдельного promotional
text лимита, а keywords использует `max-length:100` без байтовой семантики.
[Google Play](https://support.google.com/googleplay/android-developer/answer/9859152)
указывает 30/80/4000 characters для name/short/full description.

## Открытое продуктовое решение

Задан, но ещё не отвечен вопрос: Weblate получает готовые отдельные тексты площадок,
адаптирует один общий текст в поля площадок или поддерживает оба режима?
Сохранение валидной структуры и адаптация под другой стор — разные операции.
Реализация и её объём не утверждены. Исследование не даёт разрешения на deployment.

## Реестр полных отчётов

| Исследователь | Модель | Полных сообщений |
|---|---|---:|
| CurrentStoreSupport | scout; точная модель не показана | 1 структурированный отчёт |
| Steam | openai-codex/gpt-5.6-luna | 2 |
| Epic Games Store | openai-codex/gpt-5.6-luna | 2 |
| GOG | openai-codex/gpt-5.6-luna | 2 |
| Xbox Series X\|S | openai-codex/gpt-5.6-luna | 2 |
| PlayStation Store / PS5 | openai-codex/gpt-5.6-luna | 3 |
| Nintendo eShop / Switch и Switch 2 | openai-codex/gpt-5.6-luna | 2 |

## Текущая реализация: полный отчёт CurrentStoreSupport

Структурированный результат сохранён целиком, без сокращения полей.

```json
{
  "summary": "Текущая поддержка storefront фактически ограничена AppStoreFormat с mobile-паттернами; Steam и другие платформы отдельными профилями не представлены. Импорт/экспорт plain-text не сохраняет края whitespace и всегда добавляет финальный LF. Платформенный контекст в MT/judge автоматически не передаётся.",
  "files": [
    {
      "path": "weblate/formats/txt.py:69-145",
      "description": "TextParser делает content.strip(); TextSerializer пишет bytes и всегда добавляет LF; MultiParser обходит glob; AppStoreParser содержит только hard-coded mobile filename patterns/limits, а catch-all *.txt без flags."
    },
    {
      "path": "weblate/formats/txt.py:202-268",
      "description": "AppStoreFormat: monolingual, directory, simple_filename=False, language_format='googleplay', add-unit запрещён; save удаляет пустые файлы или атомарно сериализует изменённые."
    },
    {
      "path": "weblate/formats/base.py:71-90,813-820",
      "description": "APPSTORE_CODES содержит только ar/de/fr/nl/pt aliases; get_language_appstore и get_language_googleplay используют разные таблицы, но AppStoreFormat выбирает googleplay."
    },
    {
      "path": "weblate/trans/models/component.py:5036-5055,5167-5169,5340-5358,6741-6748",
      "description": "Код языка извлекается из filemask; alias применяется через project.language_aliases_dict; template проверяется против component.source_language.base_code."
    },
    {
      "path": "weblate/trans/models/translation.py:507-523",
      "description": "load_store передаёт language_code целевого перевода; source_language берётся из template language либо component.source_language.code."
    },
    {
      "path": "weblate/checks/markup.py:275-306,650-673",
      "description": "BBCodeCheck включается только флагом bbcode-text и сравнивает найденные теги; SafeHTMLCheck отключён по умолчанию и санитизирует только при safe-html/auto-safe-html."
    },
    {
      "path": "weblate/checks/chars.py:1061-1073",
      "description": "Stock MaxLengthCheck сравнивает len(replacement(target)) с лимитом; max-length — typed validation flag."
    },
    {
      "path": "weblate_customization/src/weblate_customization/checks.py:381-428,920-940",
      "description": "GameMarkupCheck глобально активен и проверяет игровые markup tokens/placeholders/conditional DSL; GameMaxLengthCheck применяет видимую длину после удаления markup/условностей."
    },
    {
      "path": "weblate_customization/src/weblate_customization/machinery.py:35-207",
      "description": "Routed LLM поддерживает только language→model routing: нормализация casefold/hyphen, exact→base→* fallback, unknown/invalid language keys отвергаются."
    },
    {
      "path": "weblate/machinery/llm.py:139-249,585-680,1028-1072,1225-1230",
      "description": "MT prompt требует сохранить formatting/whitespace/line breaks; контекст содержит key/note/explanation/flags/glossary/placeholders, но не platform/store profile; persona/style/language_instructions — единственные проектные инструкции."
    },
    {
      "path": "weblate/trans/judge_loop.py:100-135,274-292",
      "description": "Judge получает key/source/target/languages/note/explanation/glossary/failing_checks/plurals; judge_project_context берёт только persona/style из routed engine."
    },
    {
      "path": "docs/formats/appstore.rst:1-42",
      "description": "Документация перечисляет Triple-T/Fastlane/F-Droid и fastlane Android configuration; Steam/Epic/GOG/Xbox/PlayStation/Nintendo или платформенные ограничения отсутствуют."
    },
    {
      "path": "docs/snippets/format-features/appstore-features.rst:5-26",
      "description": "Автогенерируемые возможности appstore: .txt, monolingual, no plural/description/explanation/context/location/flags."
    },
    {
      "path": "README.rst:248-306",
      "description": "Custom checks и routed OpenRouter/LiteLLM регистрируются через окружение; профилей storefront/settings нет."
    }
  ],
  "architecture": "Путь данных: Component.source_language и template/filemask определяют источник и языковой файл → AppStoreParser превращает каждый matched txt в TextItem с location filename:1 и hard-coded flags → TextUnit хранит source/target → check flags вычисляются из unit metadata → MT/judge строят общий контекст из key, note, explanation, checks и project persona/style → export AppStoreFormat сохраняет отдельные файлы с нормализацией краёв и LF. Роутер LLM разрешает модель по целевому language code, но не знает platform или field semantics.",
  "report": "Реальные gaps относительно brief: (1) filename collision — стандартные description/title/short_description и т.п. получают mobile max-length даже если это Steam; prefixed steam_*.txt попадает в catch-all без лимита. (2) Нет явного механизма preserve-vs-validate: parser strip и serializer LF изменяют whitespace, а limits/markup — только checks, не import/export validation. (3) BBCode проверяется лишь при bbcode-text; HTML/Unity покрывается custom GameMarkup, но SafeHTML требует отдельного флага и не является storefront-aware. (4) Для Steam/прочих платформ max-length остаётся ручным per-unit max-length; автоматических платформенных лимитов нет. (5) Alias restrictions общие для Weblate и filemask; AppStore aliases неполны и формат ошибочно использует Google Play mapping. (6) MT/judge получают языки, key/location и check diagnostics, но не платформу, поле (title/description), store terminology или официальный лимит; это можно лишь вручную выразить в persona/style/language_instructions либо flags. (7) Export перечисляет parsed units/files и не предоставляет отдельный platform-aware bundle contract; appstore docs описывают только mobile tooling. Высокоценный вопрос пользователю: нужен ли явный Component/Project storefront profile (platform + field limits + markup + terminology), который одновременно настраивает проверки, aliases/export и передаётся в MT/judge, или сохраняем filename/flags-only workflow?"
}
```

## Steam: полные сообщения

### Сообщение 1: первоначальный отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

````markdown
Модель: `openai-codex/gpt-5.6-luna`

## Краткий evidence report: Steamworks Store

### 1. Поля и ограничения

| Поле | Что подтверждено официально | Точный лимит и подсчёт |
|---|---|---|
| **Short Description** | Краткое описание для мест с ограниченным пространством. Только plain text; переносы строк, списки и форматирование корректно не отображаются. Не рекомендуется добавлять даты, «Now Available», «Prepurchase now» и другой быстро устаревающий текст. | Официальная формулировка: **“a few hundred characters”**. Числовой лимит не указан. Не уточнено: Unicode-кодовые точки, UTF-16 units, UTF-8 bytes или иной способ подсчёта. |
| **About This Game** | Длинное описание с форматированием, текстом, изображениями, GIF и короткими анимациями. | Числовой лимит текста в просмотренной документации не указан. |
| Изображения в описании | PNG, JPG, GIF, WEBP, MP4, WEBM. | Каждое изображение — менее **5 MB**; суммарно screenshots/GIF в разделе рекомендуется держать менее **15 MB**. Единица `MB` и метод подсчёта не уточнены; это ограничение размера файла, не текста. |
| Анимации | Отображаются в `About This Game` и `Special Announcement`; не во всех местах описания. | Максимум **12 секунд**. |
| Ширина media | Рекомендовано **1170 px**; большие assets поддерживаются до **4096 px** и могут открываться fullscreen. | Это рекомендация/техническая граница для ширины, не лимит описания. |
| Alt text изображения | Можно задать alt text для каждого изображения в письменном описании; он показывается screen readers. | Числовой лимит и семантика подсчёта не указаны. |

Источники: [Store Page Written Description](https://partner.steamgames.com/doc/store/page/description), [Store Page Extra Asset Management](https://partner.steamgames.com/doc/store/page/assets).

### 2. Rich text, абзацы, ссылки и media

**Подтверждено:**

- Short Description — только обычный текст.
- About This Game поддерживает «more formatting options», а также встроенные изображения и looping GIFs.
- Steamworks предоставляет визуальный редактор и preview.
- Для media поддерживаются PNG/JPG/GIF/WEBP/MP4/WEBM.
- Изображения с текстом нужно локализовать отдельно; Valve рекомендует по возможности использовать textless images.
- Alt text является отдельными accessibility-данными и входит в localization export/import.

**Не подтверждено официальной документацией:**

- полный список допустимых BBCode/HTML-подобных тегов;
- точная грамматика параграфов, заголовков, списков, жирного/курсива;
- допустимый синтаксис ссылок внутри rich text;
- стабильные идентификаторы media-вставок;
- допустимые fallback-правила для отсутствующего локализованного изображения;
- лимит alt text.

**Платформенное правило о ссылках:**

Steam запрещает ссылки на другие сайты в store-page descriptions и graphical assets, включая QR-коды, встроенные изображения и даже текстовые намёки на URL. Для социальных сетей и собственного сайта предназначены отдельные link fields в Store Page Editor. Также запрещены изображения, ссылки и widgets, рекламирующие другие игры Steam.

Это именно **политика платформы**, не рекомендация переводчика. Источник: [Store Page Written Description](https://partner.steamgames.com/doc/store/page/description).

### 3. Локализация и export/import

Официально подтверждено:

- Store-page localization и in-game localization — **два разных типа локализации**, их можно использовать независимо.
- Для store page локализуются:
  - written descriptions;
  - images;
  - trailers.
- В Steamworks можно переключать язык через language dropdown в редакторе.
- Для изображений с текстом существуют localized image groups:
  - одна группа может использоваться в каждой локали;
  - Steam показывает соответствующее изображение по языковым настройкам пользователя;
  - изображения можно добавлять, удалять и переназначать языкам.
- Alt text экспортируется и импортируется вместе с остальным store-page text через вкладку **Localization**.

Официальная документация **не раскрывает**:

- формат файла export/import;
- названия колонок или JSON-полей;
- stable IDs/key paths;
- правила удаления пустым значением;
- сохранение порядка элементов;
- поведение при повторном импорте;
- дифференциальный импорт;
- encoding;
- ограничения размера экспортируемого файла.

Источники: [Localization and Languages](https://partner.steamgames.com/doc/store/localization), [Store Page Extra Asset Management](https://partner.steamgames.com/doc/store/page/assets).

#### Внешнее свидетельство, не контракт Valve

Сторонний Crowdin-коннектор описывает Steam localization export как multilingual JSON с `itemid`, языковыми объектами и key paths вида:

```text
app[content][about]
app[content][short_description]
```

Это полезная рабочая гипотеза для будущего HCGameLoc-коннектора, но не подтверждённая публичной документацией Valve и не гарантия стабильного partner-only формата. Нужна проверка реальным export-файлом из Steamworks Partner Portal либо официальным ответом Valve.

Источник: [Crowdin Steam JSON processor](https://store.crowdin.com/steam-json) — сторонняя документация, не Steamworks.

### 4. Локали Steam и языки игры

Steam различает:

1. **Full Platform Support** — язык интерфейса Steam, Store и Community. Только эти языки поддерживаются Steamworks APIs.
2. **Game Support Only** — язык, который разработчик может указать как поддерживаемый игрой на store page; он не обязательно доступен для интерфейса Steam и не поддерживается Steamworks APIs.

Официальная таблица содержит **29 языков Full Platform Support** и отдельный расширенный список Game Support Only.

Важные следствия:

- Store-page locale не равна языку игрового runtime.
- In-game support отдельно указывается для **Subtitles**, **Interface**, **Full Audio**.
- Steam считает поддержку языка прежде всего по in-game language settings, а не по наличию переведённой store page.
- Если игра не поддерживает ни одного Full Platform языка, store descriptions и capsules всё равно нужно внести под **English**: английский — fallback locale, даже если содержимое фактически написано на другом языке.
- Для API используются отдельные коды: например, Russian — `russian` для client API и `ru` для Web API.

Источник: [Languages Supported on Steam](https://partner.steamgames.com/doc/store/localization/languages).

### 5. Терминология и style rules для переводов

#### Платформенные правила

Это требования или ограничения Steam:

- Short Description — plain text.
- Не использовать time-sensitive claims в Short Description.
- Не вставлять внешние ссылки, QR-коды и скрытые URL.
- Не рекламировать другие игры Steam через описание, изображения, ссылки или widgets.
- Не имитировать Steam UI, wishlist/buy buttons, capsules или pricing overlays.
- Текст на graphical assets должен быть локализован для соответствующих языков.
- Store page должна описывать именно текущий продукт и доступные функции; в review process запрещено создавать ложное ожидание незавершённых или недоступных возможностей.

Источники: [Description rules](https://partner.steamgames.com/doc/store/page/description), [Store Page, Building and Editing](https://partner.steamgames.com/doc/store/page), [Review Process](https://partner.steamgames.com/doc/store/review_process).

#### Рекомендации Valve, не жёсткие валидаторы

- Сначала передавать уникальное value proposition и жанр.
- Оставлять подробности для About This Game.
- Избегать длинных «стен текста».
- Делать описание сканируемым; выделять ключевые features/section headers.
- Не перегружать текст внутренним жаргоном и акронимами.
- Использовать визуальные aids.
- Приоритизировать языки на основе traffic, sales и community feedback.
- Не путать язык с географией: предпочтения языка не следуют напрямую региону проживания.

### 6. Вывод для будущего store-aware translation в HCGameLoc

Минимальная безопасная модель должна разделять:

- `field_type`: `short_description` / `about` / asset alt text / trailer metadata;
- plain-text и rich-text поля;
- текстовый контент и media references;
- локаль store page и in-game language support;
- source/target text;
- структуру rich text отдельно от переводимого текста;
- alt text отдельно от caption/description;
- platform policy violations отдельно от translation-quality findings;
- подтверждённые Steam ограничения отдельно от partner-only неизвестных лимитов.

Сохранение валидной структуры и маркетинговая адаптация могут конфликтовать: переводчику иногда нужно менять длину, порядок аргументов, абзацную композицию или CTA, но нельзя сломать разрешённую структуру, media reference, alt text-связь или платформенные запреты. Поэтому preservation должен быть жёстким для структуры и идентификаторов, а adaptation — разрешённой операцией внутри текстовых узлов с отдельной проверкой short-description budget и style policy.

### Неизвестно / требует partner-level проверки

- Числовой лимит Short Description: официально только «несколько сотен символов».
- Метод подсчёта: characters/bytes/UTF-16/Unicode — не опубликован.
- Числовые лимиты текста About This Game и alt text.
- Полный список rich-text тегов и их вложенность.
- Официальная схема localization export/import и стабильность identifiers.
- Правила импорта пустых значений и сохранения отсутствующих переводов.
- Формат и контракт localized image-group references.
- Есть ли отдельные лимиты для каждой локали или для всего мультиязычного export.

**Самый ценный уточняющий вопрос:**
можете ли вы предоставить один реальный Steamworks Localization export-файл (с замаскированными текстами, appid и URL), чтобы зафиксировать фактическую схему полей, identifiers, rich-text markup, alt text и правила round-trip вместо вывода по сторонним коннекторам?
````

### Сообщение 2: уточнение или заменяющий отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Модель: `openai-codex/gpt-5.6-luna`

Проверил полный текст официальных страниц Steamworks напрямую, без опоры на сокращённые excerpts.

## Уточнение по точным лимитам

Официальная страница [Store Page Written Description](https://partner.steamgames.com/doc/store/page/description) действительно содержит:

- Short Description: **“limited to a few hundred characters”**.
- Числового значения вроде `300` символов нет.
- Не указано, что именно считается:
  - Unicode code points;
  - UTF-16 units;
  - UTF-8 bytes;
  - grapheme clusters.
- Encoding и алгоритм подсчёта также не указаны.
- Для `About This Game` числовой лимит текста не указан.
- Для alt text числовой лимит не указан.

Следовательно, в HCGameLoc нельзя честно зафиксировать числовой validator для Short Description по публичной документации. Безопасный статус: **documented qualitative limit; exact numeric/counting semantics unknown**.

## Подтверждённые лимиты media

На той же странице:

- каждое изображение: **меньше 5 MB**;
- суммарный размер screenshots и GIFs: **меньше 15 MB**;
- animated assets: максимум **12 секунд**;
- рекомендованная ширина: **1170 px**;
- assets до **4096 px** по ширине поддерживаются с возможностью fullscreen.

Steam не уточняет в этих документах:

- decimal или binary interpretation `MB`;
- учитывается ли оригинальный файл либо transcoded result;
- применяется ли суммарный лимит ко всем media в секции или к более широкому набору store assets.

Это ограничения файлов/media, не текстовые лимиты.

## Formatting и links

Официально подтверждено:

- Short Description — plain text; переносы строк, bullets и стилистическое форматирование не работают корректно.
- About This Game имеет дополнительные formatting options и поддерживает встроенные screenshots/GIFs.
- Полный список rich-text тегов, грамматика вложенности и точный markup-синтаксис в публичной странице не приведены.
- Store descriptions и graphical assets не могут содержать внешние ссылки, QR-коды или даже текстовые намёки на URL.
- Нельзя рекламировать другие игры Steam через текст, изображения, links или widgets.
- Нельзя имитировать Steam UI, например fake wishlist/buy buttons или pricing overlays.

## Localization import/export

Полный текст [Localization and Languages](https://partner.steamgames.com/doc/store/localization) содержит:

- отдельное управление store-page и in-game localization;
- локализацию written descriptions, images и trailers;
- ввод переводов через language dropdown в Steamworks Partner Portal;
- отдельный список языков и языковых кодов.

В этой публичной странице **нет описания export/import-формата store page**.

Полный текст [Store Page Extra Asset Management](https://partner.steamgames.com/doc/store/page/assets) добавляет только один относящийся к этому пункт:

> Alt text также экспортируется и импортируется вместе с остальным store-page text на вкладке Localization.

Публичная документация Valve не раскрывает:

- JSON/CSV/XML-схему;
- stable identifiers;
- key paths;
- правила пустых значений;
- порядок элементов;
- encoding;
- round-trip semantics;
- импорт новых локалей;
- правила удаления или preservation существующих переводов.

Упоминание `itemid`, языковых объектов и ключей вроде `app[content][about]` найдено только в стороннем [Crowdin Steam JSON processor](https://store.crowdin.com/steam-json). Это полезная гипотеза для исследования, но **не официальный контракт Valve** и должно быть маркировано как partner-only/unverified до проверки реальным Steamworks export-файлом.

## Итог для будущего коннектора

Фиксировать как гарантированные можно только:

- `short_description` — plain text, качественный лимит «несколько сотен символов»;
- `about` — rich text/media-capable;
- images/GIF/media — перечисленные форматы и опубликованные media limits;
- alt text — локализуемый и включаемый в localization export/import;
- store locale и in-game language support — разные сущности;
- структура rich text и media references должна сохраняться, но конкретная схема ещё не подтверждена.

До получения реального export-файла из Partner Portal нельзя утверждать стабильные identifiers, точный формат обмена или способ подсчёта Short Description.
```

## Epic Games Store: полные сообщения

### Сообщение 1: первоначальный отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Модель: `openai-codex/gpt-5.6-luna`.

## Epic Games Store: факты для store-aware перевода

Область: **Epic Games Store**, не Fab. Источники — официальная документация Epic Games, проверенная 15 сентября 2026.

### 1. CSV roundtrip и идентификаторы

**Подтверждено:**

- Epic выдаёт `.CSV`-шаблон из Developer Portal для выбранных страниц, offers, web shop items и store settings.
- Каждая строка соответствует одной локализуемой строке.
- Документация явно называет колонки:
  - `Localization Field`
  - `Field Description`
  - `en`
  - locale-колонки, например `ja`, `ko`.
- Перевод добавляется в соответствующую колонку локали.
- Первый ряд — заголовок; его нельзя изменять или удалять.
- Нельзя менять структуру шаблона и формат CSV.
- Неиспользуемые locale-колонки нужно удалить; пустые колонки могут вызвать ошибку импорта.
- Максимальный размер файла — **5 MB**.
- Файл должен быть сохранён как **UTF-8 CSV**.
- Импорт допускается поэтапно: разные локали или группы сущностей можно загружать отдельными файлами.
- Portal сохраняет историю загрузок: имя файла, дату, добавленные локали, статус и ошибки.

**Стабильные идентификаторы:**

- `Localization Field` явно предназначен для идентификации строк внутри шаблона.
- Однако официальная документация **не обещает**, что `Localization Field` стабилен между изменениями продукта, повторными выгрузками или переносом сущности.
- Не подтверждено, можно ли безопасно использовать только `Localization Field` как постоянный внешний ключ.
- Практический безопасный контракт: сохранять исходную строку из Portal, не сортировать и не переименовывать строки/колонки, а roundtrip проверять по паре `Localization Field` + исходный `en` + контекст сущности.

Источник:
https://dev.epicgames.com/docs/epic-games-store/store-presence/localize/localize-text

### 2. Ограничение 280 символов

**Подтверждено:** поле **Short description** имеет максимум **280 characters across all languages**.

Epic рекомендует держать английский вариант примерно в **200 символов**, чтобы оставить запас для языков, где перевод длиннее.

**Семантика подсчёта:**

- Документация говорит именно `characters`, не bytes.
- Не указано, считаются ли:
  - Unicode code points;
  - UTF-16 code units;
  - grapheme clusters;
  - пробелы, переводы строк или markup-токены по особым правилам.
- UTF-8 требуется для CSV, но это **не доказывает**, что лимит измеряется UTF-8 bytes.
- Поэтому локальный валидатор должен помечать семантику как `Epic character limit — counting algorithm undocumented`, а не имитировать byte limit.

Также:

- Лимит offer name — **64 characters** для всех языков.
- Остальные поля имеют собственные лимиты; Portal показывает лимит при редактировании и отклоняет превышение при импорте.

Источник:
https://dev.epicgames.com/docs/epic-games-store/store-presence/store-config
https://dev.epicgames.com/docs/epic-games-store/store-presence/localize/localize-text

### 3. Offer names

Offer name:

- отображается пользователю;
- используется Epic для создания URL offer;
- имя можно изменить позже;
- URL после создания изменить нельзя через Developer Portal;
- для SEO Epic рекомендует включать название продукта в offer name, особенно для add-ons и season passes;
- примерная официальная рекомендация: `Product Name–Season 2 Starter Pack`.

**Важное следствие для перевода:** изменение локализованного отображаемого имени не следует автоматически трактовать как изменение идентичности offer или URL. URL создаётся из исходного offer name, а точный механизм локализации URL документация не описывает.

Источник:
https://dev.epicgames.com/docs/epic-games-store/store-presence/manage-offers

### 4. About blocks, paragraphs и media

About section:

- до **12 блоков** контента;
- каждый блок может быть текстом или изображением;
- требуется минимум один текстовый блок;
- официальные требования media guide дополнительно указывают минимум один asset;
- лимит: **10,000 characters или 30 images**;
- изображения считаются в лимит, несмотря на формулировку «characters or 30 images»;
- изображения About:
  - минимум 700 px шириной;
  - максимум 4 MB;
  - `.jpg`, `.jpeg`, `.png`, `.gif`.

Дополнительные About images:

- необязательны;
- до 20 изображений;
- рекомендуемый формат — `.jpg`;
- 1920×1080;
- максимум 1 MB.

**Paragraph semantics:**

- Epic подтверждает блочную структуру About, но не публикует в этой документации точный экспортный формат абзацев/блоков внутри CSV.
- Не подтверждено, кодируются ли блоки, переносы строк или изображения в отдельных `Localization Field`.
- Не подтверждено, сохраняет ли CSV все структурные границы About при полном roundtrip без Portal preview.

**Markup:**

- Для storefront About и short description официальная документация не описывает поддерживаемый HTML, Markdown, BBCode или иной rich-text синтаксис.
- Поэтому нельзя безопасно переносить игровые Unity-теги, Markdown или HTML из исходного текста без подтверждения конкретным Portal-полем и preview.
- В другой части документации Epic явно упоминает rich text для patch descriptions, но это не является доказательством поддержки того же markup в About или short description.

**Links:**

- Внешние ссылки разрешаются только при соблюдении правил Epic:
  - публичный прямой URL;
  - без обязательного входа;
  - без ошибок;
  - ссылка должна соответствовать содержанию store presence.
- Для сайта продукта допустим top-level domain продукта или компании.
- Ссылки на social media, support и privacy policy должны присутствовать в соответствующих dedicated fields; их можно дополнительно повторить в About или banner.
- Нельзя использовать ссылки на PDF или web-hosted PDF.
- Epic не документирует произвольный inline-link синтаксис для локализуемого текста.

Источники:
https://dev.epicgames.com/docs/epic-games-store/store-presence/manage-pages
https://dev.epicgames.com/docs/epic-games-store/sales-and-marketing/marketing/storefront-media-guide
https://dev.epicgames.com/docs/epic-games-store/requirements-guidelines/content-ratings/content-guidelines

### 5. Media и локализация assets

Epic локализует не только текст, но и media assets:

- localized offer images;
- localized page assets;
- localized images and videos.

Основные media requirements:

- product logo: 960×540 PNG, максимум 1 MB;
- offer landscape: 2560×1440;
- offer portrait: 1200×1600;
- carousel video: 1920×1080, MP4, максимум 300 MB;
- carousel image: 1920×1080, максимум 4 MB;
- About image: ширина от 700 px, максимум 4 MB.

Localized assets отображаются как `Localized`; скопированные из английской конфигурации — как `Default`. Это отдельный asset workflow, не CSV text roundtrip.

Источник:
https://dev.epicgames.com/docs/epic-games-store/store-presence/localize/localize-assets
https://dev.epicgames.com/docs/epic-games-store/sales-and-marketing/marketing/storefront-media-guide

### 6. Локали и языки

Официальный список локалей:

`ar`, `bg`, `cs`, `da`, `nl`, `en`, `fil`, `fi`, `fr`, `de`, `hi`, `hu`, `id`, `it`, `ja`, `ko`, `ms`, `no`, `pl`, `pt-BR`, `pt`, `ro`, `ru`, `es-ES`, `es`, `sv`, `th`, `tr`, `uk`, `vi`, `zh-CN`, `zh-Hant`.

Всего — **32 locale-кода**.

Важно различать:

- **локали storefront localization** — список выше;
- **in-game supported languages** — значения в Store Settings, где отдельно указываются Text и Audio languages.

Наличие языка в игре не означает автоматически наличие storefront-перевода, и наоборот.

Источники:
https://dev.epicgames.com/docs/epic-games-store/store-presence/localize/supported-languages
https://dev.epicgames.com/docs/epic-games-store/store-presence/store-config

### 7. Терминология и style/policy rules

Это **обязательные политики Epic**, а не рекомендации переводчика:

- copy text product home page должен быть на английском; полная английская локализация продукта не обязательна, но storefront page имеет английские требования;
- product logo должен быть на английском;
- видео должно иметь английскую речь, английские субтитры или не содержать диалоговой речи;
- About и short description должны описывать gameplay, features и experience продукта/offer;
- нельзя рекламировать сторонние storefronts;
- другие продукты можно упоминать только в ограниченных случаях и с соблюдением FAQ-правил;
- storefront text и binary должны быть точными и согласованными;
- контент должен соответствовать возрастному рейтингу;
- ссылки должны быть публичными, безопасными и согласованными со storefront;
- локализация не должна нарушать content requirements.

Это **рекомендации Epic**, а не жёсткие лимиты:

- английский текст использовать в 70–80% лимита;
- для short description держать английский около 200 символов;
- на старте приоритизировать English, French, German, Russian, Spanish;
- локализовать media assets для лучшего опыта глобальных пользователей;
- для offer name включать название продукта ради SEO.

Epic также предлагает бесплатный **Epic Translation Service**. Это не автоматический API-контракт: запрос делается через private support discussion с приложенным шаблоном; срок может составлять две недели и более.

### 8. Что нельзя утверждать по официальным источникам

Не найдено публичного подтверждения для следующих деталей:

- точный алгоритм подсчёта 280 characters;
- byte limit или Unicode normalization;
- поддерживаемый HTML/Markdown/BBCode в About и short description;
- точный CSV escape/quoting contract сверх UTF-8 CSV;
- гарантированная стабильность `Localization Field`;
- публичный API для импорта/экспорта storefront translations;
- гарантии сохранения переносов строк, параграфов или About block boundaries в CSV;
- правило, как Epic обрабатывает placeholders, tags или специальные символы в storefront text;
- partner-only или account-specific ограничения, видимые только внутри Developer Portal.

### 9. Практический контракт для store-aware pipeline

Рекомендуется сохранять одновременно:

- `entity_type`: page / offer / store settings / web shop item;
- entity identity из текущего шаблона;
- `Localization Field`;
- `Field Description`;
- исходный `en`;
- locale code;
- перевод;
- исходный CSV header и порядок колонок;
- structural markers отдельно от переводимого текста;
- длину по локальной диагностике с пометкой «Epic counting semantics unknown».

При адаптации нужно выбирать между двумя целями:

1. сохранить валидную структуру платформы: поля, блоки, links, media references, placeholders;
2. адаптировать маркетинговый текст под ограничения Epic: короткое описание, SEO offer name, возрастной рейтинг, английские policy requirements.

Эти цели могут конфликтовать. Сохранение исходной разметки не должно автоматически переносить markup, который Epic для конкретного поля не подтверждает. Conversely, platform adaptation не должна молча удалять placeholders, structural boundaries или уже утверждённые metadata.

**Наиболее ценный уточняющий вопрос:**
какие реальные CSV-шаблоны Epic нужно поддержать — только text localization для product home/offer/store settings или также FAQ, web shop и About/media localization; и можно ли получить один обезличенный выгруженный CSV из Developer Portal для проверки фактических `Localization Field`, строковых идентификаторов и структуры About-блоков?
```

### Сообщение 2: уточнение или заменяющий отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Уточнение к отчёту:

- `Localization Field` и `Field Description` — **колонки, которые Epic велит использовать для нахождения строк**, но не объявленные стабильные идентификаторы.
- Epic требует сохранять исходную структуру CSV, однако **не гарантирует**:
  - стабильность `Localization Field` между выгрузками;
  - уникальность или неизменность его значений;
  - возможность использовать его как внешний ключ;
  - семантику `Field Description` как идентификатора.
- Контракт идентичности следует считать **unknown** до проверки нескольких реальных выгрузок одного и того же продукта. Без такого CSV безопасная рекомендация: сохранять всю строку шаблона и контекст сущности, а не только `Localization Field`.

По лимитам:

- Официально подтверждено только то, что short description имеет максимум **280 characters across all languages**.
- Официально не подтверждено, что именно считается символом: Unicode code point, UTF-16 unit, grapheme cluster или иной внутренний формат.
- Официально подтверждено требование UTF-8 CSV и максимум **5 MB файла**, но это **не означает** byte-based подсчёт текстового лимита.
- Поэтому валидатор должен маркировать лимит как `280 characters; counting semantics unknown`, пока не найден отдельный field-specific источник или не проведён контрольный импорт в Portal.

Источник общего ограничения и CSV-контракта:
https://dev.epicgames.com/docs/epic-games-store/store-presence/localize/localize-text

Источник short description:
https://dev.epicgames.com/docs/epic-games-store/store-presence/store-config
```

## GOG: полные сообщения

### Сообщение 1: первоначальный отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
## GOG Store: подтверждённые ограничения и неизвестные

### Что требует GOG для Store Page

В публичном чек-листе GOG указаны следующие поля и материалы:

- store description;
- system requirements;
- supported languages;
- genres & tags;
- key art и logo;
- screenshots;
- trailer;
- legal lines — опционально;
- age ratings — опционально, кроме рейтингов через IARC;
- страны, в которых игру нужно заблокировать — опционально;
- запланированные DLC, bundles, season pass и editions с точными названиями;
- developer / publisher name;
- дата и время релиза.

Официальный источник: [Essentials Checklist](https://docs.gog.com/basic-game-assets/).

### Локализация Store description

Публичная документация прямо подтверждает поддержку описания на шести языках:

- EN;
- DE;
- FR;
- CN;
- PL;
- RU.

Важно: это список языков **Store description**, а не список языков игры и не полный список локалей GOG.

В документации не раскрыты:

- отдельные locale-коды для store description;
- различие `zh-Hans` / `zh-Hant` для поля `CN`;
- fallback-поведение;
- обязательность английского;
- возможность отправить только часть локализаций;
- порядок выбора языка на витрине;
- правила для региональных вариантов английского, испанского или португальского.

### Лимиты и подсчёт

Публичные GOG Developer Docs не указывают для Store description:

- максимальное число символов;
- максимальное число байт;
- лимит строк или абзацев;
- лимит заголовков;
- лимит ссылок;
- лимит URL;
- лимит изображений или видео;
- ограничения на длину названия, legal lines или system requirements.

Следовательно, точная семантика лимитов — **unknown**:

- characters vs bytes: неизвестно;
- Unicode code points vs UTF-16 units vs grapheme clusters: неизвестно;
- учитывается ли markup в лимите: неизвестно;
- учитываются ли HTML entities или URL после декодирования: неизвестно;
- encoding для store-description payload: публично не указан.

Упоминание UTF-8 найдено только в документации импорта локализованных achievements из Steam VDF и не может автоматически переноситься на Store description: [Steam VDF import](https://docs.gog.com/sdk-steam-import/).

### Markup, абзацы, ссылки и media

В публичных документах нет подтверждённого контракта для Store description по:

- HTML;
- Markdown;
- BBCode;
- собственным GOG-тегам;
- жирному/курсивному тексту;
- заголовкам;
- спискам;
- принудительным переносам строк;
- embedded images;
- video embeds;
- hyperlinks;
- разрешённым доменам;
- sanitization и escaping.

Поэтому любые предположения вроде «поддерживается `<br>`» или «описание принимает HTML» следует считать неподтверждёнными.

Подтверждены только требования к отдельным media assets:

- key art и logo: минимум `1920 × 1080`, layered `.psd`;
- screenshots;
- trailer;
- trailer должен содержать логотип GOG.com на финальной заставке;
- для подготовки graphics assets предлагаются официальные templates и Asset Rendering Tool.

Точные форматы, размеры файлов, число screenshots, aspect ratios и максимальные веса в открытой странице не указаны.

### Assets Manager и импорт/экспорт

Публичный чек-лист говорит, что материалы нужно отправлять через [Assets Manager](https://devportal.gog.com/panel/games). Это подтверждает workflow передачи assets, но не описывает публичный API или файловый обменный формат.

Не подтверждены:

- CSV/XLSX/JSON/XML/PO/TMX импорт для store-контента;
- пакетный импорт переводов;
- экспорт уже опубликованных описаний;
- API-доступ к Store Page fields;
- schema или stable key для каждого поля;
- revision ID / asset ID / locale ID;
- история изменений и diff;
- round-trip preservation markup;
- возможность заменить одну локаль без перезаписи остальных.

Доступ к самому DevPortal без партнёрской учётной записи перенаправляет на login: [DevPortal](https://devportal.gog.com/welcome). Поэтому детали интерфейса Assets Manager, включая фактические поля и идентификаторы, относятся к **partner-only unknowns**.

Из публичной документации подтверждается только Product ID как идентификатор продукта в Developer Portal: [Developer Portal Guide](https://docs.gog.com/developer-portal/), [GOG Store Widget](https://docs.gog.com/gog-widget/). Это не доказывает наличие публичного стабильного идентификатора конкретного текстового поля или локализованной версии.

### Store locales и языки игры — разные сущности

GOG отдельно описывает языки, доступные игре и её пакетам. Таблица содержит locale-коды, например:

- `en-US`;
- `de-DE`;
- `fr-FR`;
- `pl-PL`;
- `ru-RU`;
- `zh-Hans`;
- `zh-Hant`;
- `es-ES`;
- `es-MX`;
- `pt-BR`;
- `pt-PT`.

Источники:

- [Languages](https://docs.gog.com/bc-languages/);
- [Supported Languages](https://docs.gog.com/bc-languages-table/).

Эти языки используются для:

- language-specific depots;
- отдельных builds;
- offline installers;
- выбора языка игры через GOG GALAXY;
- определения языка через GOG GALAXY SDK.

Нельзя выводить из этого, что все эти локали доступны для Store description. Публичный checklist явно называет для описания только `EN, DE, FR, CN, PL & RU`.

### Платформенные правила и стиль

Подтверждённые правила GOG QA:

- игры должны быть DRM-free;
- игра должна работать offline и без GOG GALAXY;
- материалы и builds не должны содержать упоминаний или ссылок на другие магазины, включая Steam и Epic;
- ссылки на официальный сайт разработчика и официальные социальные сети допустимы и приветствуются;
- описания changelog должны соответствовать версии GOG и заявленным системам, режимам и языкам;
- changelog должен использовать последовательный стиль, даты, заголовки и форматирование списков;
- нельзя писать о функциях или платформах, которых нет в GOG-версии.

Источник: [Quality Assurance](https://docs.gog.com/quality-assurance/). Дополнительно: [Updates](https://docs.gog.com/updates/).

Это скорее platform policy и QA guidance, а не формальная спецификация Store description. Практическая рекомендация для перевода: исключать Steam/Epic и не переносить платформо-специфичные claims, если они не относятся к GOG-версии.

### Рекомендации для store-aware translation

**Рекомендация, не формальный GOG-контракт:**

1. Хранить отдельно:
   - исходный текст;
   - перевод;
   - locale GOG Store;
   - язык игры;
   - markup-структуру;
   - ссылки;
   - media references;
   - Product ID и локальный стабильный key поля.

2. До получения partner schema не считать HTML/Markdown/BBCode разрешёнными. Сохранять структуру консервативно, но валидировать её против фактического Assets Manager.

3. Не менять платформенные названия механически:
   - GOG Store;
   - GOG GALAXY;
   - offline installers;
   - Galaxy SDK;
   - depots;
   - Extras;
   - DLC.

4. Не путать локализацию витрины с локализацией игры: наличие `ru-RU` у игрового build не доказывает отдельную русскую Store description, и наоборот.

5. Сохранять валидную структуру полезно для качества и обратного импорта, но это может конфликтовать с адаптацией под витрину: длина, естественные переносы, маркетинговый тон и требования конкретного поля могут потребовать перестройки текста. Такая адаптация должна быть явной, а не маскироваться под структурно-нейтральный перевод.

## Самый ценный уточняющий вопрос

**Можете предоставить экспорт или скриншоты Assets Manager для одного продукта с заполненными Store description на всех доступных локалях, включая фактический формат поля, лимиты, разрешённые markup/link/media и стабильные идентификаторы?**

Без этого нельзя надёжно определить контракт импорта/экспорта, точные locale-коды, counting semantics и preservation rules.
```

### Сообщение 2: уточнение или заменяющий отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Принятое уточнение: `bc-languages*` описывает языки **игры, build’ов и GOG GALAXY**, а не контракт локалей Store Page.

Для Store description подтверждено только то, что указано в [Essentials Checklist](https://docs.gog.com/basic-game-assets/):

**EN, DE, FR, CN, PL, RU.**

Locale-коды (`ru-RU`, `zh-Hans` и т. п.), fallback, региональные варианты и соответствие `CN` конкретному варианту китайского остаются неизвестными до получения partner-only данных из Assets Manager.
```

## Xbox Series X|S: полные сообщения

### Сообщение 1: первоначальный отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Модель: `openai-codex/gpt-5.6-luna`

## Краткий вывод

Для **Xbox Series X|S Store listing** нельзя безопасно использовать общий профиль «Windows app localization». Публичная документация Microsoft подтверждает требования к игровому PDP, но оставляет часть языковых требований за **Xbox Games Store Policy, соглашением с Microsoft и Xbox-контактами**.

Главный практический риск: Microsoft публикует противоречивые инструкции по формату обмена listing-файлами — более новый readiness checklist говорит `.xlsx`, а документация по branches говорит `.csv`. Формат нужно определять по актуальному Partner Center для конкретного Xbox-продукта, а не предполагать CSV по аналогии с Windows apps.

## Поля и подтвержденные лимиты

| Поле | Ограничение | Семантика счёта |
|---|---:|---|
| Description | до 10 000 | Microsoft пишет «characters», не уточняет байты, Unicode code points, UTF-16 units или grapheme clusters |
| What’s new in this version | 1 500 | То же: метод подсчёта не раскрыт |
| Product features | до 20 элементов; максимум 200 на элемент | То же |
| Keywords | до 7 поисковых терминов; максимум 40 на термин; максимум 21 отдельное слово суммарно | «Characters» явно не уточнено для лимита 40 |
| Copyright and trademark info | 200 | Тип счёта не раскрыт |
| Developed by | 255 | Тип счёта не раскрыт |
| Published by | 255 | Тип счёта не раскрыт |
| Additional license terms | URL либо до 10 000 символов plain text | Тип счёта не раскрыт |
| Trailer title | 255 | Тип счёта не раскрыт |
| Screenshots | до 8 на device family | Это количество файлов, не текстовый лимит |
| Trailers | документация расходится: walkthrough упоминает до 30, раздел общих требований — до 15 | Требуется проверка в текущем Partner Center |
| Trailer duration | менее 30 минут | Не текстовый лимит |
| Screenshot alt text | 1–2 предложения, опционально | Это рекомендация по содержанию, не машинный лимит символов |

**Не найдено публичного точного определения**, считает ли Partner Center:

- Unicode code points;
- UTF-16 code units;
- grapheme clusters;
- байты UTF-8;
- пробелы, переводы строк и combining marks.

Для HCGameLoc безопасная модель: считать локально несколько метрик, показывать пользователю наиболее консервативную, но маркировать это как **приближение**, пока не будет проверено реальным Xbox Partner Center. Нельзя утверждать, что лимиты являются байтовыми или UTF-16-лимитами.

Источник: [Store listing for titles on XBOX devices](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/store-listing).

## Plain text, markup, абзацы, ссылки, media

### Подтверждено

- `Description` — **plain text**.
- `Additional license terms` — либо один URL, либо plain text до 10 000 символов.
- Product features отображаются как маркированный список.
- Store listing содержит отдельные media-поля: screenshots, logos, hero art, trailers.
- Для Xbox screenshots допускается изображение с текстом, объясняющим gameplay, состав издания/DLC или награды.
- Alt text для Xbox screenshots опционален; Microsoft рекомендует 1–2 предложения.
- Trailer captions — `.vtt`, audio description — `.mp3`.
- Для Xbox trailer нужны branded intro/end mnemonics; применяются отдельные правила для Series X|S end card.

### Не подтверждено

Публичная Xbox Store listing-документация не подтверждает поддержку:

- HTML;
- Markdown;
- XML/rich-text markup;
- ссылок внутри Description;
- форматирования bold/italic;
- структурированных абзацев как отдельного типа;
- inline media в тексте;
- placeholder-синтаксиса;
- специальных переносов строк с гарантированной визуальной семантикой.

Следствие для будущего store-aware слоя: **не переносить в Store listing игровые Unity-теги, PO-маркеры или Weblate markup как будто это разрешённый rich text**. Сохранять структуру можно только там, где она является частью экспортируемого поля и подтверждена Partner Center. В остальных случаях нужны два представления:

1. исходный структурированный игровой текст с сохранением токенов;
2. адаптированный plain-text store copy без неподтверждённой разметки.

Это реальный конфликт требований: сохранение валидной структуры локализации и адаптация под plain-text PDP не всегда совместимы. Их нельзя решать автоматическим «сохранить всё».

## XLSX, CSV, импорт/экспорт и идентификаторы

### Xbox-specific evidence

В [Xbox game publishing readiness checklist](https://learn.microsoft.com/en-us/gaming/game-publishing/publishing-processes/managed-creators/publishing-processes-game-publishing-readiness-checklist) указано:

1. Export из исходной ветки создаёт `.xlsx`;
2. затем `.xlsx` импортируется в другую ветку;
3. сертифицированный listing импортируется в `Main`.

В [Branches](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/branches), обновлённой ранее, указано другое:

- Store listings экспортируются в `.csv`;
- CSV можно импортировать в другую ветку;
- прямой импорт из ветки в ветку невозможен.

Это противоречие официальных страниц. Для Xbox Series X|S следует считать **актуальным workflow Partner Center источником истины**, экспортировать шаблон из конкретного продукта и не генерировать generic CSV/XLSX самостоятельно до проверки фактического формата.

### Generic Windows-app evidence — не переносить автоматически на Xbox

Документация по Windows app CSV описывает:

- `Field`;
- `ID`;
- `Type`;
- `default`;
- отдельные колонки с language-locale кодами;
- фиксированные имена полей;
- запрет изменять имена полей и языковые колонки;
- для asset-полей — relative path;
- UTF-8 для CSV.

Источник: [Import and export store listings for your MSIX app](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/import-and-export-store-listings).

Это полезная гипотеза для проектирования адаптера, но **не доказательство схемы Xbox `.xlsx`**.

### Стабильные идентификаторы

Публично подтверждены:

- `Store ID` идентифицирует сам Xbox-продукт;
- package/identity values идентифицируют title/build;
- generic CSV содержит числовой `ID`, связанный с полем;
- имена `Field` и language-locale columns должны сохраняться неизменными в generic CSV.

Не подтверждено для Xbox `.xlsx`:

- является ли `ID` стабильным между экспортами;
- можно ли идентифицировать строку только по `Field`;
- есть ли отдельный GUID/row key;
- можно ли безопасно сортировать строки;
- разрешено ли добавлять новые языковые колонки вручную;
- одинаковы ли поля для Xbox One и Series X|S.

Без актуального экспортированного Xbox-шаблона нельзя обещать стабильную схему импорта.

## Языки, локали и языки игры

Нужно разделять три разных понятия:

1. **Store listing language** — язык маркетингового PDP.
2. **Package/game language** — язык ресурсов, поддерживаемых игровым пакетом.
3. **UI / voice / subtitles support** — в Gaming Metadata эти категории задаются независимо.

Microsoft прямо пишет, что языки Store listing **не обязаны совпадать** с языками, поддерживаемыми игровым пакетом, хотя совпадение рекомендуется:

- [Store listing](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/store-listing)
- [Metadata overview](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/metadata-overview)

Для Xbox services локаль имеет форму `language-country`, например `en-US`; это не следует автоматически смешивать с перечнем Store listing languages:

- [Configuring localized strings in Partner Center](https://learn.microsoft.com/en-us/gaming/gdk/docs/services/community/localized-strings/live-localized-strings-config?view=gdk-2604)

### Что является требованием

- Для завершения Store listings нужен минимум один язык, Description и screenshot.
- Для публикации Xbox required languages должны быть `Complete`.
- Полный список обязательных языков определяется Xbox Games Store Policy и Microsoft agreement.
- Публичная readiness checklist не даёт универсального списка обязательных языков для всех продуктов.

### Что является рекомендацией

- Добавлять listing для языков рынков, где продаётся игра.
- Предлагать как минимум четыре screenshots на поддерживаемый device family.
- Синхронизировать названия listing и package, чтобы не путать пользователя.

### Что не следует выводить

Нельзя считать, что:

- каждый язык игры обязан иметь отдельный Store listing;
- Store listing автоматически покрывает UI, voice и subtitles;
- `ru`, `ru-RU`, язык консоли и язык рынка — одно и то же;
- наличие локали в manifest означает обязательность локализованного PDP.

Отдельная документация по supported/unsupported locales говорит, что ресурс можно включить в пакет даже для локали, которую текущий Xbox UI ещё не предлагает:

- [Game supported and unsupported locales](https://learn.microsoft.com/en-us/gaming/gdk/docs/gdk-dev/game-principles/localization/game_supported_and_unsupported_locales?view=gdk-2604)

## Терминология и стиль

### Обязательная Xbox-терминология

Microsoft поддерживает отдельный **Xbox Console Required Terminology List** с языковыми вариантами, включая русский:

- [Xbox Console Required Terminology](https://learn.microsoft.com/en-us/gaming/gdk/docs/store/policies/console/console-certification-terminology?view=gdk-2604)

Примеры терминологических требований:

- `Xbox network`, не устаревшее `Xbox Live` в соответствующем контексте;
- `Gamertag`, не `gamer tag`;
- `Xbox Controller`, не общий `Gamepad`/`Controller`, когда требуется официальное обозначение;
- `Home`, `Guide`, `Menu button`, `View button`;
- `Xbox Series X|S` — точное написание;
- `Xbox Game Pass Essential` и новые актуальные названия подписок.

Документ содержит утверждённые локализации, поэтому для русского store copy нужен терминологический слой, а не только машинная переводческая память.

### Сертификационные последствия

[XR022 Official Naming Standards](https://learn.microsoft.com/en-us/gaming/gdk/docs/store/policies/fma/xr022-official-naming-standards?view=gdk-2604) классифицирует неправильные термины как certification findings разной тяжести. В частности:

- неправильный регистр `XBOX NETWORK`;
- `XBox` вместо `Xbox`;
- `Xbox Live` вместо `Xbox network`;
- `gamerscore` как `Points`;
- ссылки на конкурирующие платформы в рекламном контексте;
- ошибочное обозначение кнопок и контроллера.

Это не просто рекомендация переводчика: часть правил участвует в Xbox quality/certification review.

### Общие стилистические правила

Публичные страницы Store listing не дают отдельного исчерпывающего руководства по стилю локализации описаний. Подтверждено только более общее требование:

- listing должен точно представлять продукт;
- нет универсального требования поддерживать определённый объём локализации;
- локализованный текст должен быть читаемым на целевой конфигурации;
- inaccurate/incomplete submission может быть отклонён.

Источники:

- [Xbox game publishing readiness checklist](https://learn.microsoft.com/en-us/gaming/game-publishing/publishing-processes/managed-creators/publishing-processes-game-publishing-readiness-checklist)
- [XR-003 Title quality for submission](https://learn.microsoft.com/en-us/gaming/gdk/docs/store/policies/xr/xr003?view=gdk-2604)

## Рекомендация для HCGameLoc

Store-aware pipeline должен иметь отдельный тип контента:

- `xbox_store_listing`, не общий `windows_store`;
- поле с исходным Partner Center export template;
- сохранение всех неизвестных колонок/строк без перестановки;
- отдельные language-locale значения;
- проверку plain-text ограничения;
- Xbox terminology checks;
- лимиты с пометкой `characters — counting semantics undocumented`;
- media paths/asset fields отдельно от переводимого текста;
- карантин для неподтверждённых markup, ссылок и placeholders;
- обязательную ручную проверку после реального export/import в Partner Center.

Не стоит строить импорт на generic CSV-схеме, пока не получен Xbox-specific экспорт из нужного продукта.

## Самый важный уточняющий вопрос

**Какой именно актуальный файл экспортирует Partner Center для конкретного Xbox Series X|S продукта и ветки: `.xlsx` или `.csv`, и можно ли предоставить обезличенный экспорт с заголовками/строками без маркетингового текста?**

Без этого невозможно надёжно зафиксировать схему полей, стабильные идентификаторы, порядок строк и правила сохранения структуры.
```

### Сообщение 2: уточнение или заменяющий отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Модель: `openai-codex/gpt-5.6-luna`

## Краткий вывод

Для **Xbox Series X|S Store listing** нельзя безопасно применять общий профиль «Windows app localization». Публичные документы подтверждают требования к игровому PDP, но часть языковых требований зависит от **Xbox Games Store Policy, соглашения с Microsoft и Xbox-контактов**.

Критический риск: официальные страницы Microsoft сейчас противоречат друг другу по формату обмена listing-файлами:

- [Xbox game publishing readiness checklist](https://learn.microsoft.com/en-us/gaming/game-publishing/publishing-processes/managed-creators/publishing-processes-game-publishing-readiness-checklist) говорит: `Export` создаёт `.xlsx`, затем используется `Import`.
- [Branches](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/branches) говорит: Store listings экспортируются в `.csv`, который импортируется в другую ветку.

Это нужно фиксировать как **документальный конфликт**. Нельзя называть XLSX единственным форматом, строить generic CSV-схему или выдумывать стабильные row ID без актуального экспортированного файла из Partner Center для конкретного продукта.

## Поля и подтвержденные лимиты

| Поле | Ограничение | Семантика счёта |
|---|---:|---|
| Description | до 10 000 | Microsoft пишет «characters», но не уточняет байты, Unicode code points, UTF-16 units или grapheme clusters |
| What’s new in this version | 1 500 | Метод подсчёта не раскрыт |
| Product features | до 20 элементов; максимум 200 на элемент | Метод подсчёта не раскрыт |
| Keywords | до 7 терминов; максимум 40 на термин; максимум 21 отдельное слово суммарно | Семантика лимита 40 не уточнена |
| Copyright and trademark info | 200 | Метод подсчёта не раскрыт |
| Developed by | 255 | Метод подсчёта не раскрыт |
| Published by | 255 | Метод подсчёта не раскрыт |
| Additional license terms | один URL либо до 10 000 символов plain text | Метод подсчёта не раскрыт |
| Trailer title | 255 | Метод подсчёта не раскрыт |
| Screenshots | до 8 на device family | Количество файлов |
| Trailers | документация расходится: walkthrough упоминает до 30, раздел требований — до 15 | Требует проверки в Partner Center |
| Trailer duration | менее 30 минут | Не текстовый лимит |
| Screenshot alt text | 1–2 предложения, опционально | Рекомендация, не подтверждённый символьный лимит |

Источник: [Store listing for titles on XBOX devices](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/store-listing).

**Не найдено публичного точного определения**, считает ли Partner Center:

- Unicode code points;
- UTF-16 code units;
- grapheme clusters;
- UTF-8 bytes;
- пробелы, переводы строк и combining marks.

Для HCGameLoc следует показывать лимиты как `characters; counting semantics undocumented`. Нельзя утверждать, что они байтовые или UTF-16.

## Plain text, markup, абзацы, ссылки, media

### Подтверждено

- `Description` — **plain text**.
- `Additional license terms` — один URL либо plain text до 10 000 символов.
- Product features отображаются как маркированный список.
- Store listing содержит отдельные media-поля: screenshots, logos, hero art, trailers.
- Xbox screenshots могут содержать текст, объясняющий gameplay, содержимое издания/DLC или награды.
- Alt text для Xbox screenshots опционален; Microsoft рекомендует 1–2 предложения.
- Captions для trailers — `.vtt`; audio descriptions — `.mp3`.
- Xbox trailers требуют branded intro/end mnemonics с отдельными правилами для Series X|S end card.

### Не подтверждено

Публичная Xbox Store listing-документация не подтверждает поддержку:

- HTML;
- Markdown;
- XML/rich-text markup;
- ссылок внутри Description;
- bold/italic;
- inline media в тексте;
- placeholder-синтаксиса;
- специальных переносов строк с гарантированной визуальной семантикой.

Следовательно, нельзя переносить Unity-теги, PO-маркеры или Weblate markup в store copy без отдельного подтверждения. Нужны два представления:

1. игровой текст со строгим сохранением токенов и структуры;
2. адаптированный plain-text store copy.

Сохранение валидной структуры и платформенная адаптация могут конфликтовать. Автоматическое «сохранить всё» небезопасно.

## Импорт/экспорт и идентификаторы

### Xbox-specific

Readiness checklist подтверждает workflow:

1. экспорт из исходной ветки;
2. импорт в `Main` или другую ветку;
3. сертификация listing;
4. перенос сертифицированной конфигурации в `Main`.

Но формат в двух официальных документах различается: `.xlsx` против `.csv`. Поэтому:

- брать шаблон только через текущий Partner Center;
- сохранять исходные строки, колонки и неизвестные поля;
- не конвертировать файл в другой формат без проверки;
- не считать generic CSV-правила применимыми к Xbox автоматически.

### Generic Windows-app CSV — только аналогия

[Import and export store listings for your MSIX app](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/import-and-export-store-listings) описывает CSV с колонками:

- `Field`;
- `ID`;
- `Type`;
- `default`;
- language-locale columns.

Документ также требует не изменять имена полей и языковых колонок, использовать UTF-8 и задавать asset-поля через relative paths.

Это полезная гипотеза для проектирования адаптера, но **не доказательство схемы Xbox `.xlsx` или Xbox `.csv`**.

### Стабильные идентификаторы

Подтверждено:

- `Store ID` идентифицирует Xbox-продукт;
- package/identity values идентифицируют title/build;
- generic CSV содержит числовой `ID`, связанный с полем.

Не подтверждено для Xbox listing export:

- стабильность `ID` между экспортами;
- наличие row GUID;
- достаточность `Field` как уникального ключа;
- безопасность сортировки строк;
- возможность добавлять language columns вручную;
- одинаковая схема для Xbox One и Series X|S.

Нельзя выдумывать схему или стабильные ID без экспортированного партнёрского файла.

## Языки и локали

Нужно разделять:

1. **Store listing language** — язык маркетингового PDP.
2. **Package/game language** — язык ресурсов игры.
3. **UI, voice, subtitles** — независимые категории в Gaming Metadata.

Microsoft прямо указывает, что Store listing languages не обязаны совпадать с языками игрового package, хотя совпадение рекомендуется:

- [Store listing](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/store-listing)
- [Metadata overview](https://learn.microsoft.com/en-us/gaming/game-publishing/concepts/metadata-overview)

Для Xbox services локали используют форму `language-country`, например `en-US`:

- [Configuring localized strings in Partner Center](https://learn.microsoft.com/en-us/gaming/gdk/docs/services/community/localized-strings/live-localized-strings-config?view=gdk-2604)

### Требования

- Для завершения Store listings нужен минимум один язык, Description и screenshot.
- Все требуемые языки должны иметь статус `Complete`.
- Обязательный набор языков определяется Xbox Games Store Policy и Microsoft agreement.
- Публичная checklist не задаёт универсальный список обязательных языков для всех продуктов.

### Рекомендации

- Добавлять listing для языков целевых рынков.
- Давать минимум четыре screenshots на каждый поддерживаемый device family.
- По возможности согласовывать названия package и listing.

Нельзя считать эквивалентными:

- язык игры;
- язык консоли;
- язык рынка;
- Store listing language;
- `ru` и `ru-RU`.

Документ [Game supported and unsupported locales](https://learn.microsoft.com/en-us/gaming/gdk/docs/gdk-dev/game-principles/localization/game_supported_and_unsupported_locales?view=gdk-2604) отдельно допускает включение ресурсов для локалей, которые текущий Xbox UI ещё не предлагает.

## Терминология и стиль

### Xbox terminology

[ Xbox Console Required Terminology List](https://learn.microsoft.com/en-us/gaming/gdk/docs/store/policies/console/console-certification-terminology?view=gdk-2604) содержит обязательную терминологию и локализации, включая русский.

Примеры:

- `Xbox network`, а не устаревшее `Xbox Live` в соответствующем контексте;
- `Gamertag`, а не `gamer tag`;
- `Xbox Controller`, когда требуется официальное обозначение;
- `Home`, `Guide`, `Menu button`, `View button`;
- точное написание `Xbox Series X|S`;
- актуальные названия подписок, включая `Xbox Game Pass Essential`.

### Сертификация

[XR022 Official Naming Standards](https://learn.microsoft.com/en-us/gaming/gdk/docs/store/policies/fma/xr022-official-naming-standards?view=gdk-2604) классифицирует нарушения терминологии по тяжести. Примеры:

- `XBox` вместо `Xbox`;
- `Xbox NETWORK` вместо корректного регистра;
- `Xbox Live` вместо `Xbox network`;
- `gamerscore` как `Points`;
- ссылки на конкурирующие платформы в рекламном контексте;
- неверные названия кнопок и контроллера.

Это не только редакторская рекомендация: часть нарушений участвует в certification findings.

### Общий стиль

Публичные документы не дают полного руководства по стилю локализации store descriptions. Подтверждено лишь, что:

- listing должен точно представлять продукт;
- нет универсального требования поддерживать определённый объём локализации;
- локализованный текст должен быть читаемым на целевой конфигурации;
- inaccurate/incomplete submission может быть отклонён.

Источники:

- [Xbox game publishing readiness checklist](https://learn.microsoft.com/en-us/gaming/game-publishing/publishing-processes/managed-creators/publishing-processes-game-publishing-readiness-checklist)
- [XR-003 Title quality for submission](https://learn.microsoft.com/en-us/gaming/gdk/docs/store/policies/xr/xr003?view=gdk-2604)

## Рекомендация для HCGameLoc

Ввести отдельный профиль `xbox_store_listing`, а не расширять generic `windows_store`:

- хранить исходный Partner Center export template;
- сохранять неизвестные колонки, строки и порядок;
- использовать language-locale как отдельные значения;
- проверять plain-text ограничения;
- подключить Xbox terminology checks;
- маркировать лимиты как `characters`, с неизвестной counting semantics;
- отделять media paths от переводимого текста;
- помещать неподтверждённые markup, links и placeholders в карантин;
- проверять импорт реальным Partner Center workflow.

## Высший приоритетный вопрос

**Какой файл фактически экспортирует текущий Partner Center для конкретного Xbox Series X|S продукта и ветки — `.xlsx` или `.csv`, и можно ли получить обезличенный экспорт с заголовками и строковыми идентификаторами?**

Только этот файл позволит достоверно определить схему, порядок строк, ключи, сохранение неизвестных полей и правила round-trip.
```

## PlayStation Store / PS5: полные сообщения

### Сообщение 1: первоначальный отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Модель: `openai-codex/gpt-5.6-luna`

## Evidence report: PlayStation Store PS5

### Что подтверждено публично

| Область | Подтверждённые поля / семантика | Точные лимиты |
|---|---|---|
| Основная карточка игры | Название, издатель, платформа, дата релиза, жанры, рейтинги/дескрипторы контента, функции PS5, издания, DLC/add-ons | **Не опубликованы публично** |
| Текст карточки | Описание игры, секции с заголовками, списки, сноски, ссылки на другие страницы PlayStation | **Не опубликованы публично** |
| Языки | Отдельные поля **Voice** и **Screen Languages**; они не равны языкам интерфейса консоли или языкам PSN-аккаунта | **Не применимо** |
| Медиа | Изображения галереи и видео YouTube отображаются на публичных страницах игры | Контракт загрузки, размеры, форматы, число файлов и лимиты — **partner-only / unknown** |
| Идентификаторы | Публично видны `conceptId`, `skuIds`, product/content URL с product ID; это наблюдаемые идентификаторы каталога, не подтверждённый импортный формат | Формат и правила стабильности — **unknown** |
| PS5 / PS4 различия | Для cross-gen страниц поля и языки могут быть разделены по платформе; публичные карточки показывают отдельные `PS5 Voice`, `PS5 Screen Languages` и соответствующие PS4-поля | **Не опубликованы публично** |

Пример официальной карточки: [God of War Ragnarök, PlayStation Store](https://store.playstation.com/en-nz/concept/10001850). В ней видны `PS4/PS5`, `Standard Edition`, `PS5 Pro Enhanced`, `Accessibility features`, `Voice`, `Screen Languages`, описания, изображения, издания, DLC и ссылки.

Публичная страница PlayStation с теми же семантическими полями: [Split Fiction](https://www.playstation.com/en-us/games/split-fiction/). В HTML-метаданных дополнительно видны `meta-conceptId`, `meta-skuIds`, `meta-platforms`, `meta-publisher`, `meta-releaseDate`, `meta-genres`, `meta-description`, `meta-keywords`. Это подтверждает существование таких каталоговых полей, но не раскрывает submission schema.

### Языки и локали

Официальный список PlayStation различает:

- **Console Languages** — язык домашнего экрана и меню консоли;
- **Network (PSN) Account Languages** — доступный язык PSN-аккаунта/Store в зависимости от страны или региона;
- в карточке игры — **Voice** и **Screen Languages**.

Источник: [Console and PSN Supported Languages](https://www.playstation.com/en-us/legal/language-support/).

Следствие для HCGameLoc: locale Store, язык текста игры и язык озвучки нельзя сводить к одному полю. Например, официальный список содержит `French (Canada)` и `French (France)`, `Portuguese (Brazil)` и `Portuguese (Portugal)`, `Spanish (LatAm)` и `Spanish (Spain)`. Конкретная игра может поддерживать только часть языков, причём Voice и Screen Languages могут различаться.

Дополнительный официальный пример от Santa Monica Studio явно разделяет:

- **Full Localization [Voice Over + Text]**
- **Text Only**

Источник: [Santa Monica Studio — Localization Support](https://support.sms.playstation.com/hc/en-us/articles/27018915210253-Localization-Support).

Это описание конкретного продукта, не универсальное требование Sony для всех игр.

### Лимиты и counting semantics

В доступных официальных публичных источниках **не найдено подтверждения** следующих часто цитируемых значений:

- лимит названия;
- лимит short description;
- лимит long description;
- лимиты trophy title/description;
- лимиты PS5 Activities;
- лимиты URL, ссылок, изображений или видео;
- character vs UTF-8 byte counting;
- нормализация Unicode, CRLF/LF, HTML entities или grapheme clusters.

Числа `30 / 100 / 4096`, а также byte-лимиты трофеев из сторонних публикаций не принимаются как доказанные: это не официальная публичная спецификация PlayStation.

**Encoding для Store-полей: unknown.** Нельзя безопасно реализовывать validator как `len(text)` или `len(text.encode("utf-8"))` без partner-документа, экспортного шаблона либо ошибки валидации из официального портала.

### Markup, абзацы, ссылки и медиа

Официальные публичные страницы показывают:

- структурированные секции с заголовками;
- маркированные списки;
- сноски;
- ссылки на страницы PlayStation;
- изображения;
- встроенные YouTube-видео.

Но это только **рендеринг опубликованного результата**. Публичная страница не доказывает, что авторский submission принимает произвольный HTML, Markdown, BBCode или те же структуры.

Статус:

- разрешённый markup: **unknown / partner-only**;
- допустимые paragraph breaks: визуально присутствуют, контракт импорта **unknown**;
- внешние ссылки: встречаются в опубликованных описаниях и юридических сносках, но политика разрешённых доменов и формат ввода **unknown**;
- изображения: публикуются, но upload format, dimensions, size и per-locale rules **unknown**;
- видео: официальная страница может содержать YouTube media, но submission requirements **unknown**.

Для будущего store-aware pipeline безопасный инвариант: сохранять структуру только если она валидна для конкретного экспортного формата; не превращать наблюдаемый Store HTML в разрешённый универсальный markup. Адаптация текста под Store может конфликтовать с сохранением внутриигровой разметки, placeholder’ов и абзацной структуры — это должно быть отдельным согласованным режимом, а не неявным переписыванием.

### Import/export и стабильные идентификаторы

Публично подтверждено лишь наличие каталоговых идентификаторов вроде:

- `conceptId`;
- `skuIds`;
- product/content IDs в URL.

Не подтверждено:

- CSV/XLSX/JSON/XML export/import;
- имена колонок;
- locale keys;
- связь `conceptId` с SKU/version/edition;
- стабильность ID при переиздании, смене региона или cross-gen bundle;
- механизм partial update;
- revision/version или optimistic locking;
- порядок полей;
- способ передачи пустого значения для очистки.

PlayStation Partners сообщает, что после регистрации партнёру становятся доступны документация и Help Center:

[PlayStation Partners](https://partners.playstation.net/)
[Официальное описание процесса Sony Interactive Entertainment](https://sonyinteractive.com/en/news/blog/showing-your-game-to-playstation/)

Sony прямо указывает, что документация открывается после регистрации партнёра и подписания GDPA. Поэтому field contracts, шаблоны и точные ограничения следует считать **partner-only**, пока они не получены из этого портала.

Публичный договор, доступный через SEC, также говорит, что Product Submission должен содержать точное описание и полные metadata согласно текущим Guidelines, причём процесс и требования могут изменяться:

[Electronic Arts filing with PlayStation Global Developer and Publisher Agreement](https://www.sec.gov/Archives/edgar/data/712515/000071251518000045/ex-101playstationglobaldev.htm)

Это подтверждает существование закрытых/изменяемых submission guidelines, но не раскрывает текущие Store limits.

### Терминология и стиль

**Официально подтверждённая терминология:**

- `Voice`
- `Screen Languages`
- `Console Languages`
- `Network (PSN) Account Languages`
- `Platform`
- `Release`
- `Publisher`
- `Genres`
- `Editions`
- `Add-Ons`
- `Accessibility Features`
- `PS5 Version`
- `PS5 Pro Enhanced`

**Публичного универсального PlayStation style guide для Store-перевода не найдено.** Поэтому нельзя утверждать обязательные правила для:

- tone of voice;
- capitalization;
- transliteration;
- punctuation;
- brand terms;
- длины предложений;
- машинного перевода;
- региональных вариантов русского;
- адаптации названий жанров и feature labels.

Региональные и редакционные требования могут быть доступны только партнёрам или зависеть от SIEA/SIEE/SIEJA и конкретного продукта.

## Практический вывод для HCGameLoc

До получения partner-export/template следует моделировать только доказанный слой:

1. Отдельные сущности `concept`, `product/SKU`, `edition`, `platform`.
2. Отдельные поля для Store locale, `Voice` и `Screen Languages`.
3. Сохранение структуры описания как opaque/typed content, без предположения о допустимом HTML.
4. Стабильные публичные IDs хранить отдельно от переводческих ключей.
5. Все limits, encoding и import/export semantics пометить `unknown`, а не реализовывать эвристически.
6. Не обещать, что внутриигровые placeholders/rich text допустимы в Store metadata.

### Самый ценный уточняющий вопрос

Есть ли у команды доступ к **PlayStation Partners → Help Center → Documentation** и актуальному export/submission template для нужного региона SIE (например, SIEE/RU или SIEA), включая Store descriptions, languages, media и edition/SKU fields?
```

### Сообщение 2: уточнение или заменяющий отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Дополнение: нормативный PS5 Store submission-schema не установлен по публичной карточке Store и не установлен по найденному PS3 Web Content PDF. Эти материалы подтверждают только отображаемые labels и публичный рендеринг.

Точные лимиты, encoding/counting semantics, допустимый markup, import/export, stable identifiers и региональные localization rules следует считать **PlayStation Partners / partner-only**, пока команда не получит актуальную документацию или шаблон из закрытого Help Center. Дальнейший поиск публичных browser/social-media материалов для этого контракта нецелесообразен.
```

### Сообщение 3: уточнение или заменяющий отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Исправление:

- Абзац про SEC-договор **отозван**. Полученный материал не содержал текста Exhibit 10.1, поэтому он не подтверждает Product Submission, metadata или свойства Guidelines. Ссылку на SEC следует исключить из отчёта.
- Утверждение о GDPA подтверждено официальной страницей Sony Interactive Entertainment: там прямо сказано, что после одобрения регистрации и подписания **Global Developer and Publisher Agreement (GDPA)** открывается доступ к инструментам, ресурсам, документации и Help Center PlayStation Partners:
  https://sonyinteractive.com/en/news/blog/showing-your-game-to-playstation/
- Формулировку следует ограничить этим фактом: официальная страница подтверждает закрытый доступ к документации через PlayStation Partners, но не раскрывает конкретные Store metadata limits или submission schema.

Итоговый статус остальных выводов не меняется: точные лимиты, encoding/counting semantics, markup, import/export и стабильность идентификаторов остаются **unknown / partner-only**.
```

## Nintendo eShop / Switch и Switch 2: полные сообщения

### Сообщение 1: первоначальный отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Модель: `openai-codex/gpt-5.6-luna`

## Краткий отчёт

### Доступность официальных требований

Nintendo прямо указывает, что:

- доступ к информации по Nintendo Switch требует отдельной заявки после регистрации;
- документация платформы и форумы доступны через Developer Portal после принятия NDA;
- перед публикацией продукт проходит review Nintendo;
- рекламные материалы для eShop и онлайн-каталогов передаются через закрытый процесс.

Источники:

- [The Process — Nintendo Developer Portal](https://developer.nintendo.com/the-process)
- [Register as a Nintendo Developer](https://developer.nintendo.com/register)
- [Developing for Nintendo Switch 2](https://developer.nintendo.com/home/developing-for-switch2)

Для Switch 2 публичная страница сейчас сообщает: Nintendo **не принимает заявки на доступ к среде разработки Switch 2**. Платформенные submission/localization-контракты остаются partner-only.

### Публично подтверждённые поля eShop-страницы

На официальной публичной странице игры Nintendo наблюдаются следующие поля или секции:

| Поле | Статус |
|---|---|
| Название игры | подтверждено |
| Платформа / версия: Nintendo Switch, Nintendo Switch 2 | подтверждено |
| Edition: Digital | подтверждено |
| Основное описание игры | подтверждено |
| Publisher notification(s) | подтверждено |
| Ссылка на официальный сайт игры | подтверждено |
| Скриншоты / изображения | подтверждено |
| Related tags / жанровые теги | подтверждено |
| Game file size отдельно для Switch и Switch 2 | подтверждено |
| Supported play modes: TV, Tabletop, Handheld | подтверждено |
| No. of players | подтверждено |
| Nintendo Switch Online | подтверждено как отдельная продуктовая возможность |
| Publisher | подтверждено |
| Developer | подтверждено |
| Supported languages | подтверждено |
| Release date | подтверждено |
| ESRB rating | подтверждено |
| Supported features and accessories | подтверждено |
| Compatibility information для Switch 2 | подтверждено |

Пример: [Decollate Decoration — Nintendo Official Site for Canada](https://www.nintendo.com/en-ca/store/products/decollate-decoration-switch/).

Это подтверждает публичную модель отображения данных, но **не подтверждает внутренний upload/import-контракт Nintendo**.

### Лимиты и counting semantics

Публично подтверждённых Nintendo-лимитов для следующих полей не найдено:

- максимальная длина title;
- максимальная длина short description;
- максимальная длина long description;
- максимальная длина publisher notification;
- лимит тегов;
- лимит ссылок;
- лимит скриншотов;
- допустимый размер изображений;
- лимиты по Unicode grapheme clusters, code points, UTF-16 units или байтам;
- лимиты по языку или региональной записи;
- правила подсчёта переносов строк, пробелов, emoji и combining marks.

Следовательно:

- **единица подсчёта неизвестна**: characters/bytes не подтверждены;
- **encoding неизвестен**;
- нельзя безопасно переносить лимиты из сторонних публикаций, Nintendo Partner Portal, Content Management Tools или NDA-документов;
- любые числовые ограничения должны поступить из закрытого partner contract или фактической схемы загрузки.

### Markup, абзацы, ссылки, media

Публичная страница показывает:

- обычный текст с абзацами;
- внешнюю ссылку на официальный сайт игры;
- изображения и галерею скриншотов;
- специальные publisher notifications;
- структурированные списки характеристик.

Не подтверждено:

- разрешён ли HTML;
- разрешён ли Markdown;
- разрешены ли inline tags;
- какие именно URL-схемы допустимы;
- есть ли автоматическое преобразование переносов строк;
- есть ли запрет на rich text;
- требуется ли plain text на входе;
- применяются ли разные правила к описанию, notification и promotional copy.

Практический безопасный вывод: до получения partner-схемы хранить plain text и структуру отдельно; не считать видимые на странице абзацы доказательством допустимого input markup.

### Locale и language — не одно и то же

Официальная публичная карточка использует поле **Supported languages**. В примере указано:

> Japanese, Simplified Chinese, American English

Это означает языки, поддерживаемые конкретным software title, а не полный список локалей storefront.

Отдельно публичные страницы Nintendo используют региональные storefronts, например `/en-ca/`, и регионально локализованные карточки. Поэтому следует различать:

- **storefront locale**: регион и язык страницы/магазина;
- **in-game supported language**: язык, который поддерживает сама игра;
- **platform/system language**: язык интерфейса консоли;
- **territory availability**: где продукт продаётся;
- **metadata localization**: локализация карточки eShop;
- **game-content localization**: локализация внутриигрового текста, аудио и интерфейса.

Публичный материал не даёт полного authoritative mapping между storefront locales и in-game language identifiers.

Для Switch 2 также нельзя автоматически считать, что поддержка Switch означает идентичную карточку или поведение: публичная карточка отдельно выводит compatibility information и может предупреждать о различиях между версиями.

### Stable identifiers и обмен данными

Публичная карточка показывает технический идентификатор title в URL/query, например:

- `70010000086741` в ссылках на purchase confirmation и asset paths.

Это подтверждает наличие публичного title identifier, но не доказывает, что:

- это единственный стабильный ID;
- он совпадает с ID в partner submission system;
- он используется для переводческих импортов/экспортов;
- он стабилен между регионами, версиями и Switch/Switch 2 editions;
- он является ключом строки или metadata field.

Публичный import/export contract не найден. Не подтверждены:

- CSV/TSV/XLSX/JSON/XML/PO/XLIFF;
- ключи строк;
- revision/version fields;
- locale codes;
- plural rules;
- round-trip requirements;
- protected placeholders;
- field-level identifiers;
- conflict resolution при повторной загрузке.

### Терминология и стиль

Публичные официальные страницы подтверждают терминологию, которую стоит использовать как отдельные store/platform concepts:

- `Nintendo Switch`
- `Nintendo Switch 2`
- `Nintendo Switch 2 Compatibility Information`
- `Supported languages`
- `Publisher notification(s)`
- `Supported play modes`
- `TV mode`
- `Tabletop mode`
- `Handheld mode`
- `No. of players`
- `Nintendo Switch Online`
- `Game file size`
- `Supported features and accessories`
- `Release date`
- `Publisher`
- `Developer`
- `Digital`

Это наблюдаемая публичная терминология, а не полный style guide. Nintendo-specific capitalization, trademark symbols, punctuation, tone, terminology databases и region-specific editorial rules partner-only, если они не опубликованы отдельно.

Публичная карточка также показывает, что publisher notifications могут содержать важные ограничения совместимости, например отсутствие отдельных функций на Switch 2 или несовместимость save data. Такие сведения нельзя смешивать с обычным маркетинговым описанием.

### Политики против рекомендаций

**Подтверждённая политика/процесс Nintendo:**

- регистрация разработчика;
- отдельный доступ к Switch;
- NDA для закрытой документации;
- review перед публикацией;
- передача promotional materials для eShop/catalog;
- отдельное отображение совместимости Switch 2;
- disclosure поддерживаемых языков и функций на публичной карточке.

**Рекомендации для будущего HCGameLoc store-aware pipeline, не утверждения Nintendo:**

- хранить каждое поле отдельно, а не как один текстовый blob;
- сохранять стабильный source key и platform/territory/locale dimensions;
- валидировать placeholders, markup и структуру до адаптации текста;
- считать длину несколькими способами: grapheme clusters, Unicode code points, UTF-16 units и UTF-8 bytes — пока partner contract не определит authoritative metric;
- экспортировать plain text отдельно от structural metadata;
- сохранять publisher notifications отдельно от description;
- не считать публичный title ID достаточным ключом импорта.

### Конфликт сохранения структуры и platform adaptation

Для HCGameLoc это принципиальный конфликт:

- сохранение валидной структуры требует не менять placeholders, markup, ссылки, поля и идентификаторы;
- platform adaptation может требовать перестроить фразы, сократить текст, изменить маркетинговый акцент, добавить compatibility warning или разделить информацию между description и notification;
- переводчик не должен незаметно превращать platform adaptation в изменение исходной структуры;
- лучше разделять операции:
  1. structural preservation;
  2. locale translation;
  3. platform/store adaptation;
  4. length validation;
  5. final partner-specific export.

Без закрытой схемы Nintendo нельзя определить, какие изменения допустимы автоматически, а какие требуют редакторского решения.

## Самый важный уточняющий вопрос

Нужен ли будущий контракт HCGameLoc только для **локализации публичных eShop metadata-полей**, или также для **внутриигровых строк Switch/Switch 2**?

Это определяет почти всё остальное: набор locale IDs, protected tokens, markup, лимиты, stable keys, import/export format и правила разделения между `description`, `publisher notification`, compatibility text и game-content translation.
```

### Сообщение 2: уточнение или заменяющий отчёт

Модель в метаданных: `openai-codex/gpt-5.6-luna`.

```markdown
Принято. TTS и GameChat исключаются из evidence по локалям игры/eShop.

Корректная классификация:

- **Supported languages** на публичной карточке конкретной игры — свидетельство языков software title.
- **TTS/GameChat language lists** — системные accessibility/communication features Switch 2; не свидетельствуют о:
  - языках игры;
  - языках eShop metadata;
  - доступных storefront locales;
  - partner submission contract.
- Их нельзя использовать для построения locale matrix или определения обязательных переводов.

Итоговый вывод по языкам остаётся ограниченным: публично подтверждены game-specific `Supported languages`, но полный mapping между storefront locales, metadata locales и in-game language identifiers неизвестен без partner access.
```
