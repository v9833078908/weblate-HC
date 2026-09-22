# Идентичность строк, порядок и authoritative sync в TMS

**Дата:** 2026-09-21
**Статус:** исследование; не план реализации

## Вопрос

Где реализовать три связанные возможности HCGameLoc:

1. переименование существующего ключа без потери переводов и метаданных;
2. перемещение существующего ключа в порядке строк;
3. синхронизацию компонента с полной мастер-таблицей: добавить новые ключи,
   обновить переводы, привести порядок к таблице и удалить отсутствующие ключи,
   сохранив метаданные совпавших ключей.

## Краткий вывод

Рыночный паттерн разделяет **операцию над одной строкой** и **authoritative
source sync**:

- переименование выполняется по стабильному внутреннему ID строки, а внешний
  key остаётся редактируемым атрибутом;
- порядок обычно приходит из source-файла либо является режимом отображения,
  а не переводческим содержимым;
- удаление отсутствующих строк никогда не должно быть неявным побочным
  эффектом обычной загрузки: это отдельная опция/mode с явным подтверждением;
- массовый source update показывает diff и отдельно решает, сохранять ли
  переводы и approvals для изменённых строк;
- таблицы с нестандартными колонками проходят mapping, а не требуют одного
  жёсткого порядка заголовков.

Рекомендация для HCGameLoc:

1. **Rename key** и **Move before/after key** — прямые UI/API-команды над
   существующей строкой.
2. **Synchronize with loc-kit table** — destructive mode существующего
   loc-kit intake, но отдельный preview/confirm workflow. Не расширять
   безопасный append-only confirm скрытыми удалениями.
3. Multilingual spreadsheet оставить переводческим round-trip: он не должен
   управлять составом или идентичностью строк.

## Что делают зрелые продукты

### Стабильный внутренний ID, изменяемый внешний key

**Phrase Strings** обновляет ключ по ресурсу `/keys/:id`; имя ключа (`name`)
является изменяемым полем. Следовательно, API-идентичность и экспортируемое имя
разделены. [Phrase API: Update a key](https://developers.phrase.com/en/api/strings/keys/update-a-key)

**Lokalise** использует `key_id` в URL и разрешает менять `key_name`,
description, platforms, filenames, tags и другие свойства. Есть также bulk
update по массиву `key_id`. Это прямой пример identity-preserving rename:
переводы принадлежат объекту key, а не создаются заново вместе с новым именем.
[Lokalise API: Update a key](https://developers.lokalise.com/reference/update-a-key),
[Lokalise API: Bulk update](https://developers.lokalise.com/reference/bulk-update)

**Crowdin** обновляет строку по числовому `stringId` и JSON Patch позволяет
заменить `/identifier`. Параметр `updateOption` управляет сохранением переводов
и approvals при изменении текста/identifier.
[Crowdin API: Edit String](https://support.crowdin.com/_llms-txt/api/crowdin/file-based/api.projects.strings.patch.txt)

**Unity Localization** хранит общий `Key Id` отдельно от имени ключа в Shared
Table Data. Google Sheets integration может включать ID рядом с key и имеет
отдельную destructive option `Remove Missing Pulled Keys`. Это особенно близко
к игровому сценарию: display/export key может меняться, тогда как references
безопаснее связывать со стабильным ID.
[Unity: String Tables](https://docs.unity3d.com/Packages/com.unity.localization@1.0/manual/StringTables.html),
[Unity: Google Sheets synchronization](https://docs.unity3d.com/Packages/com.unity.localization%401.5/manual/Google-Sheets-Syncing-StringTableCollections.html)

**Вывод:** HCGameLoc не должен моделировать rename как «удалить старую строку и
создать новую». Нужен неизменяемый внутренний Unit/semantic ID и транзакционная
смена внешнего key во всех языках/backing-файлах. Текущий `Unit.id_hash` — часть
импортной идентичности и unique constraint, поэтому простого изменения
`context` недостаточно: реализация обязана пересчитать и согласовать hashes и
связанные pending changes либо ввести отдельный стабильный string identity.

Rename также не может автоматически доказать, что обращения к старому key в
коде игры обновлены. Для repo-owned ключей UI должен предупреждать об этом;
надёжный вариант — rename через source repository/engine tool или машинно
проверяемая интеграция.

### Порядок — свойство source structure, не перевода

**Smartling** документирует, что Strings View по умолчанию следует порядку
source-файла, тогда как CAT Tool может использовать иной порядок показа.
[Smartling: String Ordering](https://help.smartling.com/hc/en-us/articles/4416183481627-String-Ordering)

**Weblate** связывает порядок и доступную метаинформацию с файловым форматом;
для JSON есть параметры сериализации вроде сортировки ключей. Обычный
upload/download прежде всего работает с файлами и способами merge/replace, а
не с независимым ручным sequence editor.
[Weblate: Localization file formats](https://docs.weblate.org/en/weblate-2026.9/formats.html),
[Weblate: Downloading and uploading translations](https://docs.weblate.org/en/weblate-5.12.2/user/files.html)

В Unity порядок таблицы естественно хранится в общей коллекции и может
синхронизироваться с Google Sheet; identity при этом остаётся Key ID, то есть
порядок и идентичность разведены.
[Unity: Google Sheets synchronization](https://docs.unity3d.com/Packages/com.unity.localization%401.5/manual/Google-Sheets-Syncing-StringTableCollections.html)

**Вывод:** `Move before/after key` — полезная UI-команда, но её authoritative
storage должен совпадать с тем, что реально сериализуется в source/template.
DB-only `position` будет потерян при следующем file sync. Для HCGameLoc команда
должна переставлять source/template и затем выравнивать target units по тому же
порядку. Drag-and-drop допустим как дополнение для коротких списков, но для
тысяч строк основной affordance — поиск anchor key и действия «перед/после».

### Destructive sync существует, но включается явно

**Lokalise** по умолчанию обновляет импортируемые значения. Cleanup mode
удаляет отсутствующие ключи как отдельную настройку, показывает подтверждение и
учитывает file/platform association.
[Lokalise: Uploading translation files](https://docs.lokalise.com/en/articles/1400492-uploading-translation-files)

**Phrase Strings** по умолчанию сохраняет неупомянутые ключи. Удаление
включается отдельно как `Delete unmentioned keys`; CLI требует явного
`delete_unmentioned_keys: true` и предупреждает применять это только когда
source-файл содержит полный набор ключей. UI позволяет отдельно удалить ключи,
созданные конкретным upload.
[Phrase: Uploading and downloading files](https://support.phrase.com/hc/en-us/articles/5822143502620-Uploading-and-Downloading-Localization-Files-Strings),
[Phrase: CLI](https://support.phrase.com/hc/en-us/articles/5808300599068-Using-the-CLI-Strings)

**Crowdin** при обновлении source-файла показывает изменённые строки и просит
решить, для каких сохранить существующие переводы и approvals. Configuration
API предоставляет явные `update_option` (`update_as_unapproved`,
`update_without_changes`). Version/branch workflow дополнительно позволяет
увидеть added/changed/deleted strings и conflicts до merge.
[Crowdin: File Management](https://support.crowdin.com/file-management/),
[Crowdin: Configuration File](https://support.crowdin.com/developer/configuration-file/),
[Crowdin: Version Management](https://support.crowdin.com/version-management/)

**Unity Google Sheets sync** называет destructive поведение прямо: `Remove
Missing Pulled Keys`; обычный Pull не обязан удалять отсутствующее.
[Unity: Google Sheets synchronization](https://docs.unity3d.com/Packages/com.unity.localization%401.5/manual/Google-Sheets-Syncing-StringTableCollections.html)

**Вывод:** удаление отсутствующих строк — признанный workflow, но не default и
не семантика обычного translation upload. Лучший контракт — named mode
`Synchronize with table`, где таблица явно объявлена полным authoritative
snapshot.

### Spreadsheet UX: mapping вместо жёсткой схемы

**Crowdin CSV/XLSX Configuration** открывает диалог сопоставления колонок:
key, source, translations, context, ignored columns; умеет detect и ручную
настройку, а также отдельно задаёт обработку существующих translations и
approvals.
[Crowdin: CSV/XLSX File Configuration](https://support.crowdin.com/csv-xlsx-configuration/)

**Phrase CSV Strings** использует `locale_mapping` и индексы колонок для
comments/tags/metadata. Это позволяет принимать внешний порядок колонок, не
смешивая display header с canonical locale identity.
[Phrase: CSV Strings](https://support.phrase.com/hc/en-us/articles/6111361680540--CSV-Strings)

**memoQ** также предоставляет preview и явное сопоставление CSV-колонок с
полями TM, включая файлы без пригодных заголовков.
[memoQ: Translation memory CSV import settings](https://docs.memoq.com/current/en/Workspace/translation-memory-csv-import.html)

**Вывод:** требование HCGameLoc строго совпасть с одним canonical header order
удобно для round-trip, но не для producer master table. Loc-kit profile/mapping
— правильный слой для aliases (`jp`, `kr`, `ch-s`) и произвольного порядка.
При этом apply должен связывать колонку с canonical `Language`, а не с её
позиционным индексом.

## Рекомендуемый продуктовый контракт

### 1. Rename key

Команда на source-unit page и API endpoint:

- вход: стабильная identity строки, `new_key`;
- preview: старый/new key, затронутые языки и файлы, наличие pending changes;
- guard: collision с существующим ключом/context — полный отказ;
- apply: одна транзакция + repository/component lock;
- сохраняются targets, states, labels, flags, explanations, screenshots,
  comments/history и position;
- перезаписываются source/template и все соответствующие target files;
- audit event хранит old/new key;
- предупреждение: ссылки в игровом коде Weblate не перепишет.

Это не bulk import и не delete+create.

### 2. Move before/after key

Команда на source-unit page и массовом списке:

- выбрать `before`/`after` и найти anchor key;
- обновить authoritative source/template order;
- нормализовать последовательность без gaps/duplicates;
- применить тот же порядок ко всем languages при записи файлов;
- сохранить content и metadata без изменений;
- показывать эффект только в source-order sorting; другие виды сортировки
  честно остаются другими.

### 3. Synchronize with loc-kit table

Отдельный mode внутри loc-kit intake, а не multilingual spreadsheet:

1. **Upload + mapping:** распознать key/source/languages/explanation/flags;
   aliases резолвятся в canonical language identities.
2. **Immutable preparation packet:** checksum и baseline revision компонента.
3. **Preview diff:** отдельные счётчики и таблицы Added, Translation changes,
   Reordered, Missing/deleted, Conflicts; никаких платных или mutating действий.
4. **Rules:**
   - совпавший key сохраняет внутреннюю identity и metadata;
   - непустая таблица обновляет target по явной политике;
   - blank имеет отдельную выбранную семантику (`keep` либо `clear`), никогда
     не угадывается;
   - отсутствующая language column означает `not supplied`, не очистку языка;
   - отсутствующий key удаляется только в destructive mode;
   - rename не выводится эвристически из пары delete+add.
5. **Confirmation:** typed acknowledgement с количеством удаляемых строк;
   для массового удаления — повышенное permission и лимит/second approval.
6. **Apply:** component/repository lock, повторная stale-check, атомарная
   порция с fencing token, durable progress; deletion желательно последней
   фазой после успешного add/update/reorder.
7. **Recovery:** до apply сохранить manifest/snapshot, достаточный для
   восстановления DB-only metadata, а не полагаться только на git revert.
8. **Audit:** upload checksum, actor, mode, counts, conflicts, created/deleted
   identities и результирующий commit.

Для примера результат допустим именно в этом mode:

1. `%KEY1%`: обновить Ru/Fr, сохранить label «На правку»;
2. `%KEY4%`: создать со всеми supplied translations;
3. `%KEY3%`: сохранить identity и `read-only`, добавить Ru по явно разрешённой
   системной политике;
4. `%KEY2%`: удалить после отдельного подтверждения списка deletions.

## Почему не multilingual spreadsheet

Текущий HCGameLoc round-trip требует точную language schema, полный набор
существующих identities и обновляет только targets. Эти ограничения полезны:
они делают документ ограниченной переводческой сессией и исключают случайную
смену inventory. Если добавить туда create/delete/reorder/rename, один и тот же
blank или отсутствующая строка получит несколько опасных трактовок.

Loc-kit intake уже владеет profile inference, aliases, durable draft,
background portions и DB-only Explanation. Поэтому authoritative sync должен
расширять этот bounded context, сохраняя append-only как default mode.

## Ограничение исследования

Была запущена параллельная группа из пяти специализированных research agents и
повторная группа из пяти general-purpose agents. Обе группы завершились до
начала работы с одинаковой ошибкой harness `No model selected`. Поэтому
поисковые потоки выполнены напрямую через Exa/web search, а материальные выводы
проверены по перечисленным первичным источникам. Это ограничение оркестрации,
не ограничение доступности источников.
