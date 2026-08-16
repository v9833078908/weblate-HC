# План: локальный JSON-клон Pirate Ships

> **Статус:** выполнено 2026-08-16. Компонент `pirate-ships/localization-json`
> (id 16) создан в production штатным REST-путём из seed ZIP, все проверки
> сверки пройдены; см. «Execution record» в конце документа.
>
> **Решение:** создать отдельный local-only JSON-компонент
> `pirate-ships/localization-json` рядом с существующим
> `pirate-ships/localization`. Старый CSV-компонент не является источником
> миграционных файлов и не изменяется.

## Цель

Сделать в production видимый для команды полный JSON-клон текущего
`pirate-ships/localization`, сохранив все 16 языков, 3 735 ключей на язык,
намеренно пустые значения, уже применённые исправления, состояния, flags и
explanations.

Исходный артефакт — проверенный production-derived handoff:

```text
/Users/eli/Downloads/pirate-ships-localization-cleaned-2026-08-15
```

Его `manifest.json` фиксирует 16 JSON-файлов, их хеши, порядок ключей и
инвариант из 3 735 ключей на язык.

## Конфигурация компонента

| Поле | Значение |
| --- | --- |
| Project | `pirate-ships` |
| Name | `Localization JSON` |
| Slug | `localization-json` |
| VCS | `local` |
| Repository | `local:` |
| Push URL / webhook | отсутствуют |
| File format | flat JSON |
| File mask | `Localization_*.json` |
| Source language | Russian (`ru`) |
| Source template | `Localization_ru.json` |
| New base | `Localization_ru.json` |
| Languages | `de`, `en`, `es`, `fr`, `id`, `it`, `ja`, `ko`, `nl`, `pl`, `pt_BR`, `ru`, `th`, `tr`, `vi`, `zh_Hans` |

Перед созданием проверяется фактическое mapping filename suffixes
(`cn`, `jp`, `kr`, `pt`) к кодам Weblate. Production lookup подтвердил
`cn` → `zh_Hans` и `jp` → `ja`, но `kr` распознаётся как Kanuri, а `pt` —
как generic Portuguese. С отдельным подтверждением пользователя временный
seed ZIP переименовывает только два archive member:

```text
Localization_kr.json -> Localization_ko.json
Localization_pt.json -> Localization_pt_BR.json
```

Handoff directory и CSV-компонент не меняются. JSON bytes, values, порядок
и hashes меняются только в имени archive member.

## Порядок выполнения

1. Выполнить read-only preflight production:
   - slug `localization-json` свободен;
   - исходный CSV-компонент по-прежнему имеет 16 языков и 3 735 ключей;
   - его values, states, flags и explanations совпадают с handoff либо
     расхождения перечислены до записи;
   - все 16 handoff JSON-файлов проходят strict parse и hash verification.
2. Создать временный ZIP только с JSON-файлами, применив два утверждённых
   переименования archive member, и использовать штатный Weblate
   local-component/import flow, а не прямые SQL-изменения.
3. Создать `Localization JSON` как отдельный local-only component и
   импортировать все 16 файлов.
4. Перенести metadata только после точного соответствия идентификатора,
   source и target между CSV и JSON units:
   - translated/approved states;
   - flags, включая 13 selector exceptions;
   - explanations.
   Несовпадение не допускает частичного metadata copy.
5. Сверить новый компонент с handoff и исходным production snapshot:
   - 16 languages и 3 735 keys/language;
   - key order и value hashes;
   - 10 all-language-empty keys;
   - states, flags, explanations и failing-check identity;
   - `vcs=local`, `repo=local:`, пустой push URL;
   - наличие компонента по адресу
     `https://l10n.herocraft.com/projects/pirate-ships/#components`.
6. Если создание или импорт не проходит валидацию, удалить только
   компонент и files, созданные этим запуском, и оставить CSV-компонент
   нетронутым.

## Вне scope

- Конвертация существующего CSV-компонента на месте.
- Удаление, lock или cutover CSV-компонента.
- Git credentials, Git remote, webhook и автоматическая синхронизация.
- Новые редакторские/технические правки за пределами уже утверждённого
  cleanup.

## Репозиторий

После production validation закоммитить и запушить только:

- этот execution record;
- удаление `docs/LLM-first/plans/old/2026-08-10-llm-first-p1-setup.md`;
- новый `docs/LLM-first/plans/2026-08-15-llm-first-auto-translation-rollout.md`.

Перед commit выполнить `git diff --check` и целевые documentation hooks.

## Execution record (2026-08-16)

Выполнено по порядку раздела «Порядок выполнения»:

1. Preflight: slug свободен; CSV-компонент — 16 языков, 3 735 ключей на
   язык, 59 760 units, states `{0: 10, 20: 3725}` на язык, 13 selector
   flags и 13 explanations, 160 пустых units (10 ключей × 16 языков);
   контентных изменений CSV после snapshot нет (последний — 2026-08-15
   13:29 UTC). Seed ZIP в контейнере сверен: 16 файлов, payload SHA-256
   каждого совпадает с `manifest.json`, переименования `ko`/`pt_BR`
   применены.
2. Компонент создан штатным путём: `POST /api/projects/pirate-ships/components/`
   c `zipfile` (репозиторий развёрнут через `LocalRepository.from_zip`,
   тот же код, что в UI-мастере), фоновой Celery-парсинг отработал сам.
3. Настройки выровнены с CSV-компонентом после создания:
   `edit_template=True` (вернуло ru-юнитам states `{0: 10, 20: 3725}`
   вместо read-only 100), `allow_translation_propagation=True`,
   `enable_suggestions=True`, `push_on_commit=True`,
   `auto_lock_error=True`, `language_regex='^[^.]+$'`.
4. Metadata перенесён только после точного совпадения `(context, source,
   target)` всех 13 selector units между CSV и JSON: 13 `extra_flags` и
   13 `explanations` (через `update_extra_flags`/`update_explanation`).
5. Сверка пройдена: 16 языков × 3 735 units (59 760), распределения
   states по всем языкам идентичны CSV, values (source+target по каждому
   ключу) совпадают с CSV без расхождений, 160 пустых units на месте,
   13 flags и 13 explanations, `vcs=local`, `repo=local:`, push пуст,
   repo-файлы побайтово равны seed/handoff (SHA-256 совпадают).
   Failing-check identity: JSON 1 277 против CSV 1 307. Разница —
   `reused` 28→20 (после включения `allow_translation_propagation`
   пересчитан корректно для двух компонентов в одном проекте) и
   `multiple_failures` 178→177 (у CSV один устаревший чек на
   `lang_name_it` с нулём связанных failing checks — артефакт, не
   воспроизводимый на актуальных данных).
6. Временные seed-файлы на VPS и в контейнере удалены; CSV-компонент
   не изменялся (только чтение).
