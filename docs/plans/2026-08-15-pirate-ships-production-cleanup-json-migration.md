# Очистка production-локализации Pirate Ships и миграция на Git JSON

**Статус (2026-08-15): cleanup выполнен; Git migration отложена.**

**Цель:** подготовить актуальную локализацию Pirate Ships к передаче команде
игры, не опираясь на устаревший локальный JSON-снимок, и перевести дальнейшую
работу на параллельный Git-backed JSON-компонент. Старый local CSV-компонент
остаётся неизменённым fallback до успешной сверки и отдельного cutover.

**Контекст:** текущий production-компонент `pirate-ships/localization` работает
как local CSV seed (`vcs=local`, push URL отсутствует, hooks проекта
отключены). Его `push_on_commit` включён, но без push URL это не создаёт
репозиторный Git-flow. Он не может автоматически получать новые ключи из
игрового репозитория. Рабочий
контракт для нового компонента находится в
`docs/specs/game-repo-integration-contract.md`.

## Результат выполнения

| Проверка | Результат |
| --- | --- |
| Production baseline | 16 языков × 3 735 ключей, все key sets совпали |
| Детерминированные repairs | 2 273 исходных candidates, один каскадный repair и 173 явно одобренных производных target ellipsis |
| Source ellipsis | 29 `...` → `…`; 435 связанных target сохранили побайтные значения и прежний `translated` state |
| Исключения endonym | 13 узких source flags; selector findings стали нулевыми |
| Autofix closure | финальный dry-run: 0 candidates |
| Оставшиеся findings | 1 307 rows на 1 170 units; выгружены в technical/editorial/meta review queues |
| Пустые ключи | 10 сохранены непереведёнными |
| JSON handoff | 16 strict JSON-файлов прошли parse-back, no-BOM/LF/tabs/key-order/hash проверки |
| CSV component | сохранён, не удалён и не перенастроен на Git |
| Git migration | отложена: credential store для `Tempest_f2p` отсутствует, remote push не выполнялся |

## Зафиксированные решения

- Создать новый Git-backed JSON-компонент рядом с текущим CSV-компонентом и
  переключать пользователей только после проверки эквивалентности.
- Использовать ветку `localization` репозитория `Tempest_f2p` и контрактный
  путь `Assets/Resources/Data/Localization_<code>.json`.
- Нормализовать ровно 29 подтверждённых русских source-строк с `...` в `…`.
- Оставить десять ключей, которые пусты во всех 16 языках, непереведёнными до
  решения команды игры.
- Первоначально оставить `push` и `push_on_commit` нового компонента
  выключенными. Любой реальный push в игровой репозиторий требует отдельного
  явного подтверждения непосредственно перед ним.
- После проверки production (нет credential store и нет Git-компонента)
  пользователь решил завершить работу без Git migration. Создание Git
  компонента, загрузка ветки и cutover не выполняются до восстановления
  безопасного доступа к `Tempest_f2p`.

## Исходное состояние и инварианты

На момент аудита production содержит 16 языков (`ru`, `en`, `es`, `fr`,
`pt_BR`, `pl`, `de`, `it`, `th`, `id`, `zh_Hans`, `ko`, `ja`, `tr`, `vi`,
`nl`) и 3 735 согласованных ключей в каждом.

Снимок из `~/Downloads/pirate-ships-localization (1)` не используется как
источник: в нём 3 737 ключей, включая два устаревших тестовых ключа, и
от 11 до 25 отличающихся target-значений на язык.

Во всех этапах соблюдаются следующие правила:

1. Перед каждой записью повторно снять production snapshot. Если ключи,
   значения, flags или набор кандидатов изменились с аудита, остановиться и
   пересчитать планируемое изменение.
2. Не писать напрямую в PostgreSQL и не обходить Weblate. Использовать
   штатные management commands, API или интерфейс с audit trail.
3. Не переводить и не менять семантически спорные строки без отдельного
   решения. Автоматизировать только механически доказуемые правки.
4. Не удалять, не переименовывать и не превращать существующий CSV-компонент.
   В период миграции он остаётся fallback и может быть только временно
   заблокирован от параллельного редактирования.
5. Не включать webhook, постоянный push или не выполнять Git push в игровой
   репозиторий в рамках этой реализации без новой явной команды пользователя.

## Область работ

### Изменяемое состояние

- Этот план: `docs/plans/2026-08-15-pirate-ships-production-cleanup-json-migration.md`.
- Записи и flags существующего production-компонента
  `pirate-ships/localization`, только в границах подтверждённых механических
  исправлений и 29 source-ellipsis.
- Новый production Git JSON-компонент рядом со старым CSV-компонентом.
- Временный, production-derived JSON workspace для parse-back, manifest и
  ключевой/значенческой сверки. Пользовательский каталог `Downloads` не
  перезаписывается.

### Не изменяемые файлы и системы

- Код Weblate, пользовательские checks и autofixes.
- `master` игрового репозитория, SCM-Manager webhooks и история старого
  компонента.
- Десять универсально пустых ключей.
- Ручные редакторские, стилистические и смысловые проблемы, для которых
  source/target-пара не даёт единственного безопасного исправления.

## Этап 1. Зафиксировать актуальный baseline

1. Через production Weblate получить полный snapshot всех 16 языков: ключ,
   source, target, state, flags, explanation и failing checks.
2. Сверить, что каждая language translation содержит те же 3 735 ключей и что
   source links не повреждены.
3. Составить машиночитаемый manifest с порядком ключей, количеством ключей,
   hash каждого языка и списком active check rows.
4. Сравнить текущий набор с audit baseline:
   - 3 738 active findings на 3 282 unit-language rows;
   - 10 общих пустых ключей;
   - 29 известных source-ellipsis;
   - текущие flags на `lang_selector_*`.
5. Если параллельные правки обнаружены, не продолжать с прежними counts и
   повторно классифицировать отличия.

**Проверка:** snapshot воспроизводим, все 16 наборов ключей идентичны, и
manifest не содержит тестовых ключей из устаревшего Downloads-снимка.

## Этап 2. Очистить только подтверждённые defects

### 2.1 Детерминированные autofixes

1. Повторить dry-run штатной команды `reapply_autofixes` для
   `pirate-ships/localization`.
2. Сверить набор кандидатов с предварительно измеренными 2 273 repairs:
   `removed-final-stop`, `punctuation-spacing`, `end-ellipsis`,
   `french-punctuation-spacing`, `end-whitespace`, `zero-width-space` и
   spacing около engine separator `$`.
3. Если набор изменился, сохранить дифферентный список для review и не
   применять его автоматически.
4. При совпадении выполнить `--apply` через штатный scoped path, который
   коммитит только собственные pending-изменения команды.
5. Повторно получить checks и доказать, что исчезли только finding rows,
   соответствующие исправленным unit IDs.

**Проверка:** target изменился только в dry-run candidate set; неизменяемая
нормализация, placeholders, Unity tags, `$`, key set и all-empty keys
сохранились.

### 2.2 Source ellipsis

1. Снова найти ровно 29 русских source-значений, ранее подтверждённых как
   `...`, и проверить, что их текущее содержимое не изменилось.
2. Заменить только эти последовательности на `…` штатным Weblate write path.
3. Штатное изменение source помечает связанные target-строки как
   `Needs rewriting`. До записи сохранить exact `(unit ID, target, state)` для
   всех 29 × 15 target-строк.
4. После source edit сравнить каждый target с сохранённым значением. Если и
   только если target остался побайтно тем же, вернуть ему прежнее состояние
   (в текущем наборе — `translated`), не меняя target и не трогая строки вне
   этого списка.
5. Проверить, что нет новых source-строк с подтверждённым ASCII ellipsis и что
   target-значения не были перезаписаны.

**Проверка:** число source changes равно 29, каждое изменение — одна
типографическая замена. У 435 связанных target-строк после точной сверки
восстановлены исходные state; key, target и explanation не меняются.

### 2.3 Производные target ellipsis

1. После source-нормализации повторить dry-run autofixes и сохранить новый
   candidate set отдельно от первоначальных 2 273 repairs.
2. Применить только явно одобренные 173 final-замены `...` → `…`.
   Каждая из них должна быть `end-ellipsis`, затрагивать только один из
   нормализованных source keys и менять только terminal ellipsis.
3. Повторить dry-run до нулевого candidate set и проверить точное совпадение
   фактически изменённых target units с этими 173 candidates.

**Проверка:** не изменены слова, placeholders, markup, `$`, ключи и state;
у 173 target-значений изменён только последний ellipsis.

### 2.4 Нормальные неизменяемые значения

1. Составить exact list source keys, которым корректно совпадать с target:
   endonym-значения `lang_selector_*`, включая `Русский`, `English`, `Deutsch`,
   `日本語` и `中文`.
2. Для 13 source keys, реально имеющих `same` findings
   (`de`, `es`, `fr`, `it`, `jp`, `kr`, `nl`, `pl`, `pt`, `ru`, `th`, `tr`,
   `vi`), добавить `ignore-same`, сохранив существующий
   `ignore-multiple-failures`.
3. Только для `lang_selector_ru` добавить `ignore-cyrillic-leak`.
4. Только для восьми подтверждённых valid duplicate pairs добавить
   `ignore-reused`: `lang_selector_de`, `lang_selector_es`,
   `lang_selector_fr`, `lang_selector_it`, `lang_selector_nl`,
   `lang_selector_pl`, `lang_selector_pt`, `lang_selector_tr`.
   Пары связаны с соответствующими `lang_name_*` и несут одинаковый
   endonym-ярлык, а не ошибочно повторённый перевод.
5. Сохранить существующие `ignore-multiple-failures` flags, если они уже
   назначены, и не использовать их как замену узких исключений.
6. Не ставить `read-only` и не подавлять checks глобально.

**Проверка:** подавляются только заранее перечисленные valid findings; похожая
кириллица или identical target в любом другом ключе по-прежнему показывает
check.

### 2.5 Техническая и editorial очередь

1. Разобрать все оставшиеся rows в `game-markup`, `game-line-break`,
   `newline-count`, XML tags, escaped newline, unexpected Cyrillic и
   semantic/editorial очередь.
2. Механически исправлять только такие source/target пары, где ровно одно
   изменение восстанавливает идентичный набор engine tokens или separator
   count, не меняя человеческий текст.
3. Добавить все неоднозначные строки, включая `main_menu_button_vk`, в review
   queue с key, language, source, target, check ID и причиной остановки.

**Проверка:** `game-markup` сравнивает полные tag/placeholder multisets,
`game-line-break` сравнивает count и пробелы около `$`; ни один неразрешённый
semantic issue не маскируется flag-ом.

## Этап 3. Собрать JSON из production

1. После Этапа 2 экспортировать live значения в изолированный временный
   workspace, а не в `Downloads`.
2. Создать по одному strict flat JSON object для каждого из 16 языков:
   `Assets/Resources/Data/Localization_<game-code>.json`.
3. Использовать game filenames согласно aliases:
   `kr:ko`, `pt:pt_BR`; штатные `cn:zh_Hans` и `jp:ja` остаются в контрактном
   отображении.
4. Сериализовать UTF-8 без BOM, LF, tabs, без сортировки ключей и с сохранением
   baseline order.
5. Выполнить parse-back строгим JSON parser и сравнить каждые `(key, value)`
   с live production snapshot.
6. Создать hash manifest и сравнить новый набор со старым production snapshot:
   допустимы только verified autofix, 29 ellipsis и explicitly documented
   technical repairs.

**Проверка:** 16 файлов, каждый содержит ровно 3 735 ключей, отсутствуют
`title_new_key_from_developer` и `title_test_new_key_from_weblate`, нет BOM,
комментариев и форматного full-file diff.

## Этап 4. Создать параллельный Git JSON-компонент

> **Отложено 2026-08-15:** production не имеет credential store для
> `Tempest_f2p`; authenticated `git ls-remote` запросил логин. Не создавать
> component, не добавлять credentials и не выполнять Git push без нового
> утверждения.

1. Проверить, что orphan branch `localization` доступна с production и не
   содержит неучтённых переводческих правок.
2. До component creation подтвердить project-level language aliases
   `kr:ko,pt:pt_BR`.
3. Подготовить новый component с настройками:

   | Поле | Значение |
   | --- | --- |
   | VCS | Git |
   | Branch | `localization` |
   | File mask | `Assets/Resources/Data/Localization_*.json` |
   | Template/base | `Assets/Resources/Data/Localization_ru.json` |
   | Format | `json` |
   | Source language | `ru` |
   | Manage strings / Edit base | включены |
   | JSON output | indent `1`, tabs, sort keys off |
   | Push URL / Push on commit | пусто / выключено |

4. Наполнить ветку полным production-derived set только после отдельного
   подтверждения, разрешающего Git push в игровой репозиторий. До него
   допускаются только local clone, parse-back и diff checks.
5. Создать component после того, как branch содержит полный, проверенный
   baseline; импортировать source flags и supported Weblate metadata без
   подмены или удаления истории старого компонента.
6. Заблокировать старый CSV-component для редактирования только на короткое
   окно parity verification, затем либо снять lock при failure, либо оставить
   его archived fallback после успешного cutover.

**Проверка:** новый компонент показывает 16 ожидаемых языков, по 3 735 ключей,
aliases записывают нужные game filenames, а первый controlled file render даёт
однострочный diff, не переформатирование всего файла.

## Этап 5. Handoff-проверка и cutover

1. Сверить CSV baseline и новый JSON component по каждому `(language, key,
   value)`, source flag и state, с заранее объяснёнными различиями.
2. Проверить inbound flow: новый ключ должен появиться в
   `Localization_ru.json` ветки `localization`, затем после authorized pull
   стать непереведённым во всех 15 target-языках. Изменение только в `master`
   не считается inbound-проверкой.
3. Не включать webhook как часть этого плана. Если команда его настроит,
   проверить точное совпадение component repo URL отдельно.
4. Перед реальным outbound test, заполнением push URL или включением
   `push_on_commit` запросить отдельное подтверждение. После него проверить
   ровно один controlled translation change, авторство, target branch и
   однострочный Git diff.
5. Передать итоговый audit report: hashes, counts до/после, applied repairs,
   exception flags, open technical/editorial queue, 10 empty keys и результаты
   integration checks.
6. Получить отдельное cutover-подтверждение перед перенаправлением
   переводчиков на новый component. CSV-компонент не удалять.

## Условия остановки и откат

- Изменившийся production baseline, candidate set или flags: остановиться до
  записи и пересчитать.
- Любая parse-back/key/value mismatch: не создавать component и не готовить
  ветку к handoff.
- Full-file formatting diff: скорректировать JSON parameters, не принимать
  миграцию.
- Любой Git fetch/push/configuration error: оставить push выключенным и старый
  CSV component доступным.
- Ошибка после scoped autofix write: использовать Weblate audit trail и
  штатное точечное восстановление только затронутых units; не выполнять
  database rollback или broad reset.

## Верификация

Перед завершением должны быть предоставлены:

1. Baseline/export manifests и reproducible parse-back results.
2. Counts checks до/после с перечнем всех изменённых unit IDs и причин.
3. Проверка key/value parity между production baseline и JSON.
4. Проверка JSON encoding, no-BOM, key order, tabs и минимального diff.
5. Скрин или API-результат нового component с 16 языками и 3 735 keys.
6. Результат inbound new-key check.
7. Отдельный результат outbound push check только при новом явном разрешении.
