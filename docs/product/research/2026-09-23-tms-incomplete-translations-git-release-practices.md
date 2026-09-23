<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Неполные переводы при Git-синхронизации и выпуске: практики TMS

**Дата:** 2026-09-23
**Статус:** исследование официальной документации; не утверждённая политика выпуска
**Область:** Weblate, Crowdin, Phrase Strings, Lokalise; отдельно — механизм
GitHub для обязательных проверок. Это не эксперимент с экспортом других TMS
на данных HCGameLoc.

## Вопрос и границы

Read-only проверка production 2026-09-23 через
`GET /api/components/pirate-ships/localization-json/file/` и
`GET /api/translations/pirate-ships/localization-json/<lang>/units/?q=state:empty`
подтвердила 3 914 русских ключей, 16 языков и 23 отсутствующих в целевых
JSON значения для 12 ключей с непустым русским исходником. Все 23 есть в
Weblate как пустые единицы (`state=0`); отдельно 17 ключей имеют пустой
русский исходник. При отсутствии перевода игра, по сообщению разработчика,
показывает сырой ключ. Game Director **пока не считает 23 пропуска блокером
релиза**. Требуется не держать локализационную ветку закрытой до 100%
готовности, а начать регулярную синхронизацию с Git. Git-ветка игры и её
сборка в ходе этой проверки не читались.

Различаем четыре самостоятельных этапа: **проверка при переводе**,
**сборка/фильтрация экспортируемого файла**, **доставка файла в Git**
и **допуск PR либо сборки/релиза**. «Экспорт поддерживает неполные
строки» не означает «каждый выпущенный клиент корректно обработает
отсутствующий ключ»; способ показать сырой ключ определяется игрой.
Ни один из рассмотренных документов не устанавливает общего для всех
проектов правила «все языки на 100% до Git push» или «100% до релиза».
Это ограниченный вывод о **документированных механизмах**, не
доказательство отсутствия пользовательской настройки или CI-политики.

## Сравнение: что происходит и на каком рубеже

| Продукт | Авторский workflow | Экспорт неполного языка | Git и PR | Что именно блокируется |
| --- | --- | --- | --- | --- |
| **Weblate** | Показывает непереведённые строки и статистику по языкам; project-level **Translation quality filter** выбирает, коммитить все переводы, пропускать «needs editing» или брать только утверждённые. [Translating using Weblate → «Untranslated strings»](https://docs.weblate.org/en/latest/user/translating.html); [Translation projects → «Translation quality filter»](https://docs.weblate.org/en/latest/admin/projects.html#project-commit-policy) | Файлы скачиваются в исходном виде из репозитория; настройка качества определяет, какие изменения переводов коммитятся, а не требует полного покрытия. В Pirate Ships 23 непереведённые единицы отсутствуют в соответствующих JSON выгрузки. [Downloading and uploading translations → «Downloading translations»](https://docs.weblate.org/en/latest/user/files.html#downloading-translations); production GET выше | С настроенным push URL Weblate отправляет коммиты в remote; **Push on commit** и «ленивый коммит» управляют моментом отправки. [Continuous localization → «Pushing changes from Weblate», «Lazy commits»](https://docs.weblate.org/en/latest/admin/continuous.html#push-changes) | Неизвестен штатный стопор Git push по условию «каждый язык переведён»; это не обещание отсутствия иных пользовательских ограничений. В нынешнем production `vcs=local`, push URL пустой, `push_on_commit=False`, `enforced_checks=[]`, project `commit_policy=20` («Skip translations marked as needing editing»). [Translation projects → «Translation quality filter»](https://docs.weblate.org/en/latest/admin/projects.html#project-commit-policy); production `GET /api/projects/pirate-ships/` и `GET /api/components/pirate-ships/localization-json/` 2026-09-23; код форка `weblate/trans/models/component.py:2956-2987` |
| **Crowdin** | QA выявляет пустой перевод; это отдельная проверка качества. [QA Check Settings → «QA Check Parameters»](https://support.crowdin.com/project-settings/qa-checks/) | По умолчанию подставляет текст исходника вместо непереведённого; можно выбрать **Skip Untranslated Strings** и **Export Only Approved Translations**. [Export Settings → «Translations Export Settings»](https://support.crowdin.com/project-settings/export/) | GitHub integration синхронизирует файлы; официальный Action отдельно включает `download_translations` и `create_pull_request`. [GitHub Integration → «GitHub Integration»](https://support.crowdin.com/github-integration/); [crowdin/github-action → README, пример workflow](https://github.com/crowdin/github-action) | Документы описывают **состав экспорта и создание PR**, а не обязательную 100%-ную готовность перед синхронизацией или merge. Пропуск строки в JSON небезопасен для игры, которая показывает ключ: это следствие условий Pirate Ships, не заявленная гарантия Crowdin. [Export Settings → «Translations Export Settings»](https://support.crowdin.com/project-settings/export/); [crowdin/github-action → README, пример workflow](https://github.com/crowdin/github-action) |
| **Phrase Strings** | Ключ может быть unverified и всё же попасть в файл: по умолчанию такие переводы **не исключаются**, поскольку выпуск имеющегося варианта предпочтительнее задержки выпуска ради идеального. Рекомендуется непрерывно улучшать переводы. [Review Workflow (Strings) → «Review Workflow (Strings)»](https://support.phrase.com/hc/en-us/articles/5784094755484) | Download/API различают `include_empty_translations` (включать ключи без перевода) и `fallback_locale_id` (подставлять перевод другого locale); для ручной загрузки с fallback нужно включить **Include empty translations** и выбрать fallback locale. [Download a locale → «OpenAPI», параметры запроса](https://developers.phrase.com/en/api/strings/locales/download-a-locale); [Languages and Locales (Strings) → «Fallback Language»](https://support.phrase.com/hc/en-us/articles/5818281650204) | GitHub integration и `.phrase.yml` задают импорт/экспорт; CLI можно запускать в CI. Отдельный *Zero-Touch Localization* workflow экспортирует строки обратно в тот же PR **после завершения созданного job**: это gate конкретной автоматизации, а не универсальная обязанность закончить все языки проекта. [GitHub (Strings) → «Prerequisites»](https://support.phrase.com/hc/en-us/articles/5784125562012); [Continuous Integration (Strings) → «Continuous Integration (Strings)»](https://support.phrase.com/hc/en-us/articles/5822060164508); [Zero-Touch Localization (Strings) → «Zero-Touch Localization (Strings)»](https://support.phrase.com/hc/en-us/articles/29265012652956) | Обычная загрузка допускает unverified по умолчанию; ожидание завершения job относится **только** к описанному zero-touch сценарию. Ни один из этих разделов не задаёт обязательный для каждого PR порог полноты по всем языкам. [Review Workflow (Strings) → «Review Workflow (Strings)»](https://support.phrase.com/hc/en-us/articles/5784094755484); [Zero-Touch Localization (Strings) → «Zero-Touch Localization (Strings)»](https://support.phrase.com/hc/en-us/articles/29265012652956) |
| **Lokalise** | Различает untranslated (пустое значение в конкретном языке) и другие статусы; страница скачивания показывает предупреждение о QA-проблемах и ссылку на них. [Translation Statuses → «Translated and Untranslated»](https://docs.lokalise.com/en/articles/3684557-translation-statuses-translated-verified-reviewed-and-completed); [Downloading translation files → «Getting started»](https://docs.lokalise.com/en/articles/3150682-downloading-translation-files) | **Empty translations** по умолчанию экспортирует пустые строки; альтернативы — не экспортировать соответствующие ключи либо взять значение базового языка. Выбор языков, платформ и набора ключей — отдельные параметры. [Downloading translation files → «Advanced settings», «Selecting keys/translations to download», «Selecting languages and file structure»](https://docs.lokalise.com/en/articles/3150682-downloading-translation-files) | GitHub app импортирует файлы из GitHub (в том числе при автоматическом pull) и создаёт PR при экспорте. Официальный pull Action скачивает переводы и создаёт PR при обнаружении изменений; в его примере явно указан `export_empty_as: "skip"`. [GitHub → «GitHub»](https://docs.lokalise.com/en/articles/1684090-github); [lokalise/lokalise-pull-action → README, «Usage»](https://github.com/lokalise/lokalise-pull-action) | Предупреждение QA **не описано** как блокирующее экспорт; PR Action создаёт предложение изменения Git, но не решает за проект, можно ли его merge/deploy. Это разделение подтверждают описания предупреждения и Action; утверждение о политике конкретной команды потребовало бы её CI-конфигурации. [Downloading translation files → «Getting started»](https://docs.lokalise.com/en/articles/3150682-downloading-translation-files); [lokalise/lokalise-pull-action → README, «Usage»](https://github.com/lokalise/lokalise-pull-action) |

### Исключения, пустые источники и важные различия

- **Crowdin:** default *source-text fallback* сохраняет читаемое значение
  там, где целевой перевод отсутствует; переключение на
  **Skip Untranslated Strings** меняет эту семантику на пропуск.
  Ограничения зависят также от формата файла: Crowdin CLI прямо указывает,
  что флаг пропуска не работает для некоторых документных форматов
  (`.docx`, `.html`, `.md` и др.). Выбранные JSON-файлы нужно проверять
  на выходе, не по одному названию переключателя.
  [Export Settings → «Translations Export Settings»](https://support.crowdin.com/project-settings/export/);
  [Crowdin CLI → «Options», `--skip-untranslated-strings`](https://crowdin.github.io/crowdin-cli/commands/crowdin-download-translations).
- **Phrase:** у ключа есть исключения **по locale**: он не экспортируется
  для соответствующего языка и не учитывается в его отчёте; пустое
  исключённое содержимое не считается untranslated. Это способ отметить
  «для этого языка не требуется», а не автоматически восполнить
  отсутствующий перевод. **Blocked key** — другая функция: запрещает
  создание/загрузку ключа и редактирование существующего, не должна
  использоваться как «пропустить одну пустую локаль».
  [Keys (Strings) → «Exclusions» и описание blocking key](https://support.phrase.com/hc/en-us/articles/5784119185436);
  [Exclude a locale on a collection of keys → «OpenAPI»](https://developers.phrase.com/en/api/strings/keys/exclude-a-locale-on-a-collection-of-keys).
- **Lokalise:** фильтр **Untranslated strings** выбирает только
  непереведённые записи *для скачивания*; это не запрет выгрузки
  остальных языков. Отдельная настройка импорта **Fill empty keys with
  key names** превращает пустое значение ключа в его имя; для игры,
  где сырой key недопустим в UI, включать её без проверки источника
  особенно рискованно. Это настройка **импорта**, не fallback
  экспортируемых переводов.
  [Downloading translation files → «Selecting keys/translations to download»](https://docs.lokalise.com/en/articles/3150682-downloading-translation-files);
  [Uploading translation files → «Upload options»](https://docs.lokalise.com/en/articles/1400492-uploading-translation-files).
- **Пустой русский исходник ≠ потерянный целевой перевод.** Перечисленные
  fallback-механизмы не создадут осмысленного видимого текста из пустого
  источника. Достоверная метрика для данного кейса — пара
  `(ключ, язык)` с **непустым RU** и отсутствующим target, плюс отдельный
  учёт намеренно пустых RU и исключённых для языка ключей. Это
  **рекомендуемое правило HCGameLoc**, а не универсальное правило
  перечисленных TMS. Различие особенно важно там, где в Lokalise
  предлагается заполнять пустой source именем ключа, а в Phrase
  locale-исключение удаляет строку из отчёта о непереведённых.
  [Lokalise Uploading translation files → «Upload options»](https://docs.lokalise.com/en/articles/1400492-uploading-translation-files);
  [Phrase Keys (Strings) → «Exclusions»](https://support.phrase.com/hc/en-us/articles/5784119185436).

### Где может стоять настоящий release gate

Генерация локализационного PR не тождественна его слиянию и не тождественна
сборке игры. GitHub разрешает сделать **status checks обязательными для
protected branch**: тогда PR нельзя слить, пока они не пройдут. Наличие
такой проверки и её критерий устанавливает **репозиторий**, а не
автоматически Crowdin/Phrase/Lokalise.
[GitHub Docs → «Status checks»](https://docs.github.com/en/pull-requests/reference/status-checks);
[Crowdin GitHub Action → README, пример workflow](https://github.com/crowdin/github-action);
[Lokalise pull Action → README, «Usage»](https://github.com/lokalise/lokalise-pull-action);
[Phrase Continuous Integration (Strings) → «Continuous Integration (Strings)»](https://support.phrase.com/hc/en-us/articles/5822060164508).

Разные продуктовые решения возможны и не противоречат документации:
TMS хранит незавершённую локаль; экспорт либо подставляет source, либо
оставляет пустое, либо исключает key; Git получает обновление; CI проверяет
контракт исполняемых ресурсов; release pipeline определяет, можно ли
поставить конкретный артефакт. Исключение ключа из файла **само по себе**
не решает проблему Pirate Ships, поскольку текущая игра отображает
отсутствующий key буквально. Последнее — вывод из входного контекста,
не утверждение о поведении любого другого игрового движка.

## Рекомендация для HCGameLoc / Pirate Ships (не факт о TMS)

1. **Развязать частоту синхронизации и готовность перевода.** Начать
   Weblate и долгоживущей узкой ветки `localization` без обязательного
   покрытия 100%. **Не блокировать Weblate→Git push или перенос в ветку
   сборки из-за текущих 23 пропусков**, раз Game Director разрешил их
   сейчас. Зафиксировать 23 пары / 12 исходных ключей как долг с
   ответственным и числом в каждом следующем отчёте, а не считать их
   переведёнными. У игрового SCM-Manager не подтверждён GitHub PR и CI,
   поэтому **не** переносить GitHub-специфичный workflow без проверки:
   ответственность за перенос файлов `localization` в beta/master
   определить вместе с разработчиком.
2. **Отдельно решить вопрос отображения.** При текущем поведении игры
   пропуск JSON-ключа может вывести сырой key игроку. Это известный
   принятый риск, а не свойство, которое Weblate «починит» при Git push.
   Если позже будет нужна гарантия отсутствия сырых ключей, отдельно
   согласовать и проверить в игровом клиенте fallback на RU либо
   подстановку непустого RU **только в выпускаемый артефакт**. Не
   записывать fallback обратно в Weblate как «готовый перевод».
   Аналогичные режимы экспорта документированы у Crowdin и Lokalise,
   но это не доказательство их наличия в нынешнем экспорте HCGameLoc.
   [Crowdin Export Settings → «Translations Export Settings»](https://support.crowdin.com/project-settings/export/);
   [Lokalise Downloading translation files → «Advanced settings»](https://docs.lokalise.com/en/articles/3150682-downloading-translation-files).
3. **Раздельные сигналы в CI.** Публиковать по каждому языку число
   `(непустой RU, пустой/отсутствующий target)` **до fallback**,
   динамику относительно зафиксированного baseline и, при появлении
   fallback, число реально подставленных значений. Пока полноту сделать
   предупреждением, а не обязательным status check. Структурные ошибки
   JSON и потерю плейсхолдеров оценивать отдельно: разрешение ГД на
   отсутствующие переводы не означает разрешения сломанного формата или
   движковых токенов. Если будет утверждён fallback, отдельным решением
   включить runtime-тест «нет сырого ключа у используемых строк»; он
   требует работающего fallback, но не требует 100% перевода. Без
   fallback такой тест противоречил бы принятому решению ГД.
   Динамически вычисляемые в игре ключи требуют runtime-проверки —
   статическая проверка JSON их не доказывает. GitHub status check
   становится merge-gate лишь при явном требовании в правилах ветки.
   [GitHub Docs → «Status checks»](https://docs.github.com/en/pull-requests/reference/status-checks).
4. **Явные исключения.** Хранить разрешённые случаи «RU пуст намеренно» и
   «ключ не применяется к языку» с владельцем/причиной отдельно от
   23 реальных пробелов; не превращать весь пустой RU в имя key.
   Сначала тестом подтвердить семантику нынешнего JSON-формата и
   импорт/экспорт Weblate. Не отключать язык целиком только из-за
   малого числа пропусков; решение о списке выпускаемых языков — отдельная
   продуктовая политика, не следствие настроек TMS.

**Проверялось:** read-only настройки и ZIP-выгрузка production Weblate,
состояния 23 непереведённых единиц через API. **Не проверялось:** Git-ветка
игры, её CI/PR-процесс, способ переноса в beta/master и логика отображения
в билде помимо сообщения разработчика. Поэтому перечисленные шаги —
предлагаемый контракт для обсуждения, а не объявление о внедрённом gate
или об отсутствии сырых ключей в релизе.
