# План: безопасный rollout автоматического LLM-перевода

> **Статус:** план реализации. Текущий rollout не включает judge pipeline.
> Judge-ready этап ниже является продолжением этого же контракта, но
> выполняется только после реализации
> `docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md`.

## Цель

Добавить одну идемпотентную management-команду
`llm_first_setup <project>`, которая переводит существующий проект в режим
автоматического LLM-черновика через уже зарегистрированный OpenRouter
machinery и `AutoTranslateAddon`.

В текущем rollout addon должен записывать ответ модели как обычный
перевод:

- `mode=translate`;
- `state=20` (`translated`), если перевод прошёл без enforced check;
- `state=11` (`needs rewriting`), если после записи нарушен enforced check;
- suggestion без изменения target, если результат превышает допустимую длину
  unit; это существующее поведение `AutoTranslate.update()`, а не успешный
  state-20 результат;
- `Project.translation_review` не включается;
- `Project.commit_policy` не изменяется автоматически.

Это означает «строка переведена», но не означает, что она одобрена судьёй или
готова к релизу. Судейская проверка и release gate появляются на следующем
этапе и используют тот же переводческий слой, а не отдельный второй addon.

## Архитектурный контракт

### Текущий этап: `draft`

Команда по умолчанию работает только в read-only режиме. Запись требует
явного `--apply`.

После `--apply` для каждого non-glossary компонента проекта:

1. проверяется effective OpenRouter configuration проекта;
2. проверяется routing всех существующих target translations;
3. проверяется наличие игровых checks;
4. устанавливается или приводится к канонической конфигурации ровно один
   direct `AutoTranslateAddon`;
5. к `Component.enforced_checks` добавляются
   `game-markup` и `game-line-break` без удаления уже существующих checks;
6. начальный sweep ставится в Celery только после успешного commit;
7. успешный eligible automatic-translation result сохраняется как `state=20`,
   потому что канонический режим — `translate`, а не `suggest`, `fuzzy` или
   `approved`; provider failures, threshold misses, overlong results и
   enforced-check failures имеют отдельную семантику ниже.

Текущий профиль не перезаписывает существующий target. Его фильтр:

```python
{
    "auto_source": "mt",
    "mode": "translate",
    "engines": ["openrouter"],
    "q": "state:empty",
    "threshold": 80,
    "component": None,
}
```

`state:empty` выбран намеренно. Старый `state:<translated` включал уже
существующие state 10 и 11 и мог перезаписывать работу человека или
предыдущий LLM-черновик. Backfill существующих targets не входит в этот
rollout и требует отдельного плана с собственной оценкой стоимости и
подтверждением перезаписи.

### Семантика текущего профиля

| Сценарий | Результат |
| --- | --- |
| Новый source unit и пустые target translations | ставится LLM-перевод |
| Existing target с state 10, 11, 20 или 30 | не перезаписывается |
| Успешный ответ модели | `state=20` |
| Ответ с failing enforced check | target сохраняется, state понижается до 11 |
| Ответ длиннее `Unit.get_max_length()` | создаётся suggestion, target/state не меняются |
| Provider failure или результат ниже threshold | target/state не меняются, ошибка видна в activity |
| `translation_review=True` уже был включён ранее | команда его не меняет; state 20 может отображаться как waiting for review |
| `commit_policy=ALL` | команда показывает предупреждение и требует явного `--allow-unsafe-export` |
| Glossary component | пропускается |
| Нет target translations | addon устанавливается, но запросить перевод некуда |
| Добавлен новый component | addon не наследуется автоматически; setup запускается повторно |
| Добавлен новый target language | существующий addon увидит его через component update, но route должен быть настроен заранее |

Фильтр `state:empty` не отменяет reclassification уже существующих
translated/approved units при добавлении enforced checks. Это отдельный
background task `update_enforced_checks`; команда сообщает, что он поставлен,
а не утверждает, что все units уже обработаны.

### Будущий этап: `judge-ready`

После реализации judge pipeline тот же addon остаётся producer-слоем:

```text
source update
    -> AutoTranslateAddon / mode=translate
    -> target state 20
    -> JudgeVerdict pipeline
    -> pass: state 30
       flag: state 20
       reject/critical: state 10
```

Judge-ready activation не должна:

- создавать второй переводческий addon;
- переводить через `mode=approved` напрямую;
- считать state 20 доказательством качества;
- автоматически пересоздавать полный draft sweep без cost guard;
- дублировать модель `JudgeVerdict`, judge checks или judge loop.

Она должна использовать реализации из
`2026-08-13-01-judge-verdict-core.md` и проверять:

- применены миграции judge;
- зарегистрированы `judge-flag` и `judge-reject`;
- включена и валидна judge configuration;
- прошли калибровочные и production-gate предусловия;
- для target languages effective workflow разрешает нужный review flow;
- release policy не пропускает critical/rejected units.

`translation_review=True` включается только в judge-ready режиме. Если
`WorkflowSetting.translation_review=False` переопределяет project setting для
target language, activation завершается ошибкой с перечислением языков.

## Командный интерфейс

### Текущий этап

```text
weblate llm_first_setup <project>
weblate llm_first_setup <project> --apply --max-units 500
weblate llm_first_setup <project> --apply --max-units 500 --allow-unsafe-export
weblate llm_first_setup <project> --apply --max-units 500 --reschedule-initial-sweep
```

Правила:

- без `--apply` команда только показывает план действий;
- `--apply` меняет БД и может поставить платные Celery jobs;
- `--max-units` обязателен вместе с `--apply`; это жёсткий лимит eligible
  `state:empty` units во всём проекте;
- если preflight считает больше units, чем разрешает `--max-units`, команда
  завершается до любых записей;
- точная стоимость заранее не вычисляется: machinery не предоставляет
  надёжную per-model pricing contract; количество units выводится как
  cost proxy, а фактическая стоимость измеряется по bounded run;
- `--allow-unsafe-export` обязателен, если `commit_policy=ALL`;
- `--reschedule-initial-sweep` разрешает повторно поставить sweep для уже
  канонически настроенных компонентов и требует `--apply` и `--max-units`;
- positional project slug остаётся единственным обязательным аргументом;
- команда не принимает engine, language или model: они берутся из effective
  project settings;
- команда не создаёт target languages;
- команда не удаляет и не объединяет конфликтующие addons.

Будущий judge-ready режим добавляется отдельным изменением интерфейса. До
реализации judge core попытка включить его должна завершаться понятной
ошибкой, а не частичным применением draft setup.

## Preflight без записей

Preflight должен собрать все ошибки проекта за один запуск. В нём запрещены:

- `Project.save`;
- `Component.save`;
- `Addon.create` и `Addon.configure`;
- Celery task dispatch;
- HTTP-запросы к OpenRouter;
- вывод API keys и полных machinery settings.

### Проверки

1. Найти проект по slug. Неизвестный slug — `CommandError`.
2. Проверить регистрацию:
   - `MACHINERY["openrouter"]`;
   - `CHECKS["game-markup"]`;
   - `CHECKS["game-line-break"]`.
3. Получить `project.get_machinery_settings()["openrouter"]`.
   Учитываются:
   - site-wide setting;
   - field-by-field project override;
   - `{"openrouter": None}`, отключающий inherited service.
4. Локально проверить effective mapping:
   - settings — object;
   - `key` — непустая строка;
   - `routing` — непустой object;
   - каждый model ID — непустая строка.
   Secret не должен попадать ни в `CommandError`, ни в stdout.
5. Создать зарегистрированный machinery class с effective settings и вызвать
   `is_supported(source_code, target_code)` для каждого target language.
   `validate_settings()` не вызывается: формы machinery выполняют live
   validation и могут отправить пробный перевод.
6. Выбрать non-glossary components проекта в стабильном порядке по `pk`.
7. Для каждого компонента получить target translations через
   `translation_set.exclude_source()`, а не через ручное сравнение language
   codes.
8. Собрать effective addons через
   `Addon.objects.prefetch_for_components(components)`.
9. Для каждого компонента:
   - inherited/category/project/site-wide autotranslate addon — ошибка;
   - более одного direct autotranslate addon — ошибка;
   - ноль direct addons — действие `install`;
   - один direct addon — сравнить **normalized effective configuration**, а
     не raw JSON;
   - semantically equal configuration — `noop`;
   - отличающаяся direct configuration — `reconfigure`, без удаления addon.
10. Построить `AutoTranslateAddon.get_add_form(..., data=configuration)` и
    проверить canonical configuration так же, как штатный addon flow.
11. Посчитать:
    - количество target translations;
    - количество eligible `state:empty` units;
    - количество компонентов с install/reconfigure/noop;
    - количество callback'ов, которые будут зарегистрированы.
12. Сравнить eligible-unit count с обязательным `--max-units` для apply.
    Превышение лимита — blocking error без writes и callbacks.
13. Если `project.commit_policy == ALL`, добавить blocking error для
    `--apply` без `--allow-unsafe-export`. В dry-run это warning с явным
    объяснением, что enforced checks сами по себе не являются VCS release gate.

Preflight возвращает immutable plan record, содержащий component IDs,
addon IDs, normalized configuration, settings fingerprint и counts. В
`apply` нельзя использовать устаревший plan без повторной проверки.

## Транзакционное применение

### Порядок блокировок

`handle()` открывает outer `transaction.atomic()` и блокирует:

1. project;
2. effective OpenRouter `Setting` rows;
3. components по возрастанию `pk`;
4. relevant `Addon` rows по возрастанию `pk`.

После блокировок preflight запускается повторно. Если изменились settings,
список components, addon rows или configuration fingerprint, команда
завершается ошибкой «project changed during preflight» без применения.

Это сериализует параллельные запуски самой команды. Команда не должна
утверждать абсолютную защиту от административного UI, который создаёт
addon, не используя тот же lock protocol. Такой случай обнаруживается
повторным preflight и требует повторного запуска.

### Изменения

Для каждого setup record:

1. Добавить только отсутствующие game checks, сохранив порядок и все
   пользовательские checks.
2. Если checks изменились, вызвать обычный `Component.save(update_fields=...)`,
   чтобы сохранились change/audit semantics и штатно поставилась
   `update_enforced_checks`.
3. Если addon отсутствует, создать его с canonical configuration.
4. Если addon существует и normalized configuration отличается, изменить
   только его configuration.
5. При истинном noop не вызывать `configure()`: штатный `configure()` вызывает
   `post_configure()` и может повторно поставить initial sweep.
6. `translation_review` не изменять.
7. `commit_policy` не изменять.

Создание/изменение addon должно использовать штатный `post_configure` и
`delay_on_commit`. Callback регистрируется только после всех DB writes
внутри текущей транзакции. В dry-run callback не существует.

Initial sweep ставится только для `install`, `reconfigure` или явно
запрошенного `--reschedule-initial-sweep`. Повторный noop не создаёт новую
задачу. Количество eligible units показывается до `--apply`; отсутствие
eligible units не должно приводить к OpenRouter HTTP-запросу.

### Граница atomicity

Транзакция гарантирует атомарность DB changes и не вызывает callbacks при
rollback. Она не гарантирует атомарность между PostgreSQL commit и
последующей публикацией Celery task. Поэтому:

- сообщение «applied» печатается только после выхода из atomic block;
- сообщение не утверждает, что LLM job завершён;
- ошибка публикации callback считается operational failure после commit;
- повторный запуск без `--reschedule-initial-sweep` не должен повторно
  тратить бюджет;
- для потерянного initial sweep оператор использует явный
  `--reschedule-initial-sweep`.

## Повторный запуск и идемпотентность

Повторный запуск с теми же данными должен:

- не менять `translation_review`;
- не менять `commit_policy`;
- не менять порядок `enforced_checks`;
- не создавать второй direct addon;
- не вызывать `configure()` для semantically equal configuration;
- не ставить initial sweep;
- не запускать network validation;
- сообщать `Already configured`.

Сравниваются normalized values, включая defaults:

```python
{
    "auto_source": "mt",
    "mode": "translate",
    "engines": ["openrouter"],
    "q": "state:empty",
    "threshold": 80,
    "component": None,
}
```

Legacy `filter_type`, пустой `component` и отсутствующие поля должны
сравниваться после `AutoTranslateAddon.normalize_configuration()`. Raw dict
comparison запрещён.

## Тестовый план

### Файлы

- Create:
  `weblate/trans/management/commands/llm_first_setup.py`
- Create:
  `weblate/trans/tests/test_llm_first_setup.py`
- Modify, только если потребуется общий lock/helper:
  `weblate/addons/base.py` или `weblate/addons/models.py`
- Modify:
  `docs/admin/management.rst`
- Modify:
  `docs/changes.rst` в unreleased section после реализации

Не добавлять детали новой feature в `AGENTS.md`: это repository guidance,
а не пользовательская документация.

### Test double

Тестовый OpenRouter double должен повторять контракт реального
`RoutedLLMTranslation`:

- exact normalized route;
- base-language route;
- `*` fallback;
- отсутствие HTTP;
- reject malformed routing values.

Отдельный тест должен patch'ить actual registered machinery class или
проверять его `is_supported()` без импорта `weblate_customization` в core
suite, если customization package не является test dependency.

### Preflight и no-write

Проверить:

1. неизвестный project;
2. отсутствие `openrouter` в `MACHINERY`;
3. отсутствие effective setting;
4. project-level `openrouter=None`;
5. пустой key;
6. пустой или не-object routing;
7. пустой model ID;
8. missing exact/base/fallback route с перечислением component и language;
9. отсутствие одного из game checks;
10. inherited addon;
11. два direct addons;
12. невалидная canonical addon form;
13. `commit_policy=ALL` без `--allow-unsafe-export`;
14. ошибка во втором компоненте оставляет первый компонент полностью
    неизменённым.

Каждый no-write тест проверяет:

- `translation_review`;
- `commit_policy`;
- `enforced_checks`;
- количество и raw configuration addons;
- отсутствие `postconfigure_addon.delay_on_commit`;
- отсутствие `auto_translate_component.delay_on_commit`;
- отсутствие network calls.

### Positive cases

Проверить:

1. dry-run показывает install/reconfigure/counts и ничего не меняет;
2. `--apply` устанавливает canonical addon;
3. addon имеет `mode=translate`;
4. `AutoTranslate` с review disabled записывает `state=20`;
5. failing `game-markup`/`game-line-break` переводит результат в state 11;
6. существующие non-empty targets не выбираются профилем `state:empty`;
7. glossary component пропускается;
8. существующие enforced checks сохраняются;
9. existing translated/approved unit с failing check reclassifies через
   штатный background path;
10. initial sweep регистрируется после commit, а не внутри preflight;
11. patch покрывает и `postconfigure_addon`, и последующий
    `auto_translate_component` seam;
12. второй запуск не вызывает `configure()` и не ставит sweep;
13. новый component появляется только после повторного setup;
14. новый target language проходит exact/base/fallback route;
15. результат выше `Unit.get_max_length()` создаёт suggestion и не меняет
    target/state;
16. provider failure не меняет target/state и оставляет activity error;
17. `--apply` без `--max-units` отклоняется;
18. превышение `--max-units` отклоняется без writes и callbacks;
19. routing changes between preflight and apply abort the transaction;
20. addon changes between preflight and apply abort the transaction;
21. `--reschedule-initial-sweep` является единственным способом повторить
    sweep для unchanged component.

Для callback-тестов использовать `TransactionTestCase` там, где нужно
проверить commit boundary. Eager Celery не должен скрывать реальный порядок
`postconfigure_addon -> component event -> auto_translate_component`.

### Judge-ready contract tests

До реализации judge core эти тесты остаются contract tests с mocked judge
registry:

1. draft setup не создаёт `JudgeVerdict`;
2. draft result остаётся state 20;
3. judge-ready setup отклоняется без judge migrations/registrations;
4. judge-ready setup отклоняется при `WorkflowSetting.translation_review=False`;
5. judge-ready setup не меняет addon configuration, если draft addon уже
   canonical;
6. judge-ready setup не запускает новый full draft sweep;
7. после judge result:
   - pass может стать state 30 только при effective review;
   - flag остаётся state 20;
   - reject/critical становится state 10;
8. stale verdict не меняет новый target;
9. release policy не пропускает state 10/11/12.

Реализация этих тестов не должна копировать код из judge core plan.

## Реализационные задачи

### Задача 1. Зафиксировать контракт и командный интерфейс

Добавить падающие тесты для dry-run, `--apply`, `--max-units`,
`--allow-unsafe-export` и `--reschedule-initial-sweep`. Зафиксировать
`state:empty`, `mode=translate`, длинный результат как suggestion и
отсутствие изменений `translation_review` и `commit_policy`.

Критерий готовности:

- неизвестная команда падает до реализации;
- тесты не выполняют OpenRouter requests;
- пользовательские state semantics записаны в assertions.

### Задача 2. Реализовать read-only preflight

Создать `llm_first_setup.py` с:

- `Command.add_arguments`;
- immutable `SetupPlan`/`ComponentSetup`;
- effective settings validation;
- routing coverage;
- normalized addon comparison;
- conflict detection;
- eligible-unit counts;
- aggregated `CommandError`.

Preflight не должен вызывать ORM write methods или Celery.

### Задача 3. Реализовать locked apply

Добавить:

- project/settings/component/addon lock order;
- second preflight under locks;
- preservation of existing checks;
- component save and enforced-check scheduling;
- addon create/reconfigure/noop branches;
- post-commit output;
- explicit reschedule branch.

Не использовать `Component.objects.update()` вместо штатного save без
отдельного доказательства, что не теряются audit/change/background side
effects.

### Задача 4. Закрыть Celery и failure semantics

Проверить фактическую цепочку:

```text
Addon.create/configure
  -> BaseAddon.post_configure
  -> postconfigure_addon
  -> AutoTranslateAddon.post_configure_run
  -> component event
  -> auto_translate_component
```

Тесты должны отдельно покрывать:

- rollback до commit;
- callback после commit;
- callback enqueue failure;
- partial batch failure;
- повторный reschedule без повторной конфигурации.

Не объявлять эту цепочку end-to-end atomic.

### Задача 5. Документировать draft rollout

В `docs/admin/management.rst` описать:

- dry-run по умолчанию;
- `--apply`;
- обязательный `--max-units` и его cost-safety смысл;
- `state=20` как обычный translated result;
- отсутствие `translation_review` в текущем режиме;
- `state:empty` и запрет перезаписи existing targets;
- длинные результаты как suggestions, а не как state 20;
- warnings для `commit_policy=ALL`;
- exact/base/`*` routing;
- glossary skip;
- addon conflicts;
- Celery callback и границу atomicity;
- `--reschedule-initial-sweep`.

Changelog добавлять только вместе с фактической пользовательской
реализацией команды.

### Задача 6. Подготовить judge-ready extension

После завершения judge core plan:

1. обновить preflight judge registrations/settings;
2. добавить отдельный explicit activation mode;
3. включить review только в этой mode;
4. проверить effective workflow по всем target languages;
5. выставить согласованную commit policy;
6. связать translation result с judge trigger;
7. не создавать новый addon и не запускать необозначенный full sweep;
8. добавить contract tests из раздела выше;
9. обновить architecture roadmap, убрав неоднозначность вокруг «возврата
   addon в фазе 3».

Эта задача не реализует `JudgeVerdict`, judge client, collegium, repair loop,
judge checks или UI. Они принадлежат
`2026-08-13-01-judge-verdict-core.md` и связанным judge plans.

## Документация и smoke test

### До запуска

1. Запустить dry-run на disposable project.
2. Проверить effective `openrouter` settings без вывода key.
3. Убедиться, что routing покрывает все target languages.
4. Проверить число eligible `state:empty` units.
5. Если `commit_policy=ALL`, либо изменить policy отдельно, либо явно
   подтвердить `--allow-unsafe-export`.
6. Проверить, что нет inherited или duplicate autotranslate addon.

### Применение

`--apply` считается state-changing operational action. На dev-инстансе
запускать только после отдельного подтверждения. В production не запускать
без явного разрешения владельца проекта.

Проверить после commit:

- addon configuration;
- `mode=translate`;
- новый LLM result имеет state 20;
- failing game check даёт state 11;
- existing non-empty target не изменился;
- activity показывает background task;
- OpenRouter получает ожидаемый model route;
- повторный запуск не создаёт новый sweep.

Реальный платный smoke test не является частью обычного unit-test gate.
Он требует отдельного cost/safety approval и выполняется только после
успешного dry-run и targeted tests.

## Финальная проверка

```bash
./rundev.sh test weblate/trans/tests/test_llm_first_setup.py -- -n0

uv run prek run --files \
  weblate/trans/management/commands/llm_first_setup.py \
  weblate/trans/tests/test_llm_first_setup.py

uv run pylint \
  weblate/trans/management/commands/llm_first_setup.py \
  weblate/trans/tests/test_llm_first_setup.py

git diff --check
git status --short
```

Перед первым production запуском отдельно проверить:

- judge-ready зависимости не выдаются за реализованные;
- текущий draft rollout не включает auto-approve;
- текущая команда не меняет `translation_review` и `commit_policy`;
- production sweep имеет подтверждённый budget и rollback/reschedule
  procedure.

## Вне scope

- Реализация `JudgeVerdict`, judge client, collegium и repair loop.
- Judge UI, filters, decisions и white-box history.
- Автоматическое включение `translation_review` в текущем draft режиме.
- Автоматическая установка `state=30` текущим addon.
- Перезапись existing targets без отдельного backfill-плана.
- Создание target languages.
- Удаление или автоматическое объединение конфликтующих addons.
- Гарантия atomicity между PostgreSQL commit и Celery broker.
- Production deployment и платный smoke test без отдельного разрешения.
