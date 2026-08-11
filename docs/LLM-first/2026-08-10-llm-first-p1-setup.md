# P1 «Строка приходит переведённой» — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Одна идемпотентная management-команда `llm_first_setup <project>`, которая безопасно переводит существующий проект в LLM-first режим: включает review workflow, устанавливает ровно один OpenRouter autotranslate add-on на каждый не-глоссарный компонент и делает игровые проверки обязательными.

**Architecture:** Команда сначала выполняет read-only preflight всего проекта и только после его успешного завершения применяет изменения в одной транзакции. Preflight проверяет регистрацию игровых checks, effective OpenRouter configuration проекта, покрытие всех существующих target languages модельным routing и отсутствие конфликтующих autotranslate add-on'ов. Установка нового или изменение существующего add-on'а запускает initial sweep после commit; полностью повторный запуск ничего не перенастраивает и не создаёт повторных задач.

**Tech Stack:** Django management command, `Project`/`Component`/`Translation`/`Addon`, registries `CHECKS`/`MACHINERY`, `AutoTranslateAddon`, pytest (`ComponentTestCase`).

---

## Продуктовый контракт P1

### Что даёт включение

- **Бизнес:** сокращает time-to-first-draft после обновления исходников и переводит
  работу лингвистов с создания перевода с нуля на проверку готового черновика.
  P1 не обещает экономию без измерений: до и после включения фиксируются время от
  component update до первого state 20, доля автоматически заполненных eligible
  units, provider failures и объём последующих человеческих правок.
- **Пользователь:** переводчик открывает не пустую строку, а LLM-черновик в очереди
  `Waiting for review`; строка с нарушенными игровыми плейсхолдерами или разметкой
  попадает в `Needs rewriting`, а не выглядит готовой.
- **Продукт:** существующий событийный pipeline Weblate становится LLM-first без
  изменения поведения остальных проектов. Языки и модельный routing остаются
  явной административной конфигурацией, auto-approve не включается.
- **UX:** setup работает по принципу all-or-nothing, перечисляет все ошибки
  preflight за один запуск и не сообщает об успехе до commit. Сам LLM-перевод
  асинхронный; его завершение и ошибки видны в activity проекта, а не маскируются
  синхронным сообщением команды.

### Семантика включения

| Сценарий после setup | Будут ли LLM-запросы |
|---|---|
| Add-on впервые установлен или изменён | Да, initial sweep ставится в очередь после commit |
| В уже настроенный компонент загружен новый файл или пришёл git update | Да, `EVENT_COMPONENT_UPDATE` запускает фоновый sweep |
| В уже настроенном компоненте появилась новая source-строка | Да, `EVENT_CHANGE` направляет её существующим target translations |
| Создан новый компонент | Нет: component-scoped add-on на нём отсутствует до повторного setup |
| У компонента нет target translations | Нет: переводить некуда |
| Unit уже имеет state 20 или 30 | Нет: его исключает `q=state:<translated` |

«Сразу» здесь означает постановку фоновой работы после завершения DB-транзакции,
а не ожидание ответа OpenRouter внутри management-команды. Один Celery job может
отправить несколько batch-запросов; это не обязательно один HTTP-запрос на строку.

### Как выбираются языки и модели

Команда не принимает список языков и не просит LLM угадать его:

1. Source language берётся из `Component.source_language`.
2. Target languages — все существующие `Translation` компонента, кроме source
   translation (`exclude_source()`).
3. Для каждой target translation `RoutedLLMTranslation` получает её language code
   и выбирает модель из `routing`: точный нормализованный code (`pt_BR`), затем
   базовый code (`pt`), затем fallback `"*"`.
4. Если ни один route не подходит, первоначальный setup завершается до любых
   изменений и перечисляет component/language без модели.

Routing нельзя дублировать в management-команде: preflight обязан создать
зарегистрированный `openrouter` service с effective project settings и вызвать его
`is_supported()`. Тогда проверка setup и реальный запрос используют одну реализацию
`resolve_model()`.

### User flow

1. Администратор настраивает site-wide или project-level OpenRouter и routing моделей.
2. `llm_first_setup <project>` проверяет весь проект без изменений.
3. После успешного preflight команда включает `Project.translation_review`, добавляет `game-markup`/`game-line-break` в `Component.enforced_checks` и устанавливает или обновляет component-scoped add-on.
4. Установка или изменение add-on'а ставит initial sweep в Celery после commit транзакции.
5. Git pull, загрузка файла или добавление source-строки вызывает `EVENT_COMPONENT_UPDATE`/`EVENT_CHANGE`; add-on ставит LLM-перевод в фоновую очередь.
6. Target languages берутся из существующих `Translation` записей компонента. Команда не создаёт языки, а LLM не выбирает их самостоятельно.
7. Успешный перевод получает state 20 (`Waiting for review`); failing enforced check переводит его в state 11 (`Needs rewriting`). Auto-approve в P1 нет.

### Зафиксированные решения

- Поддерживается только machinery identifier `openrouter`. Параметр `--engine` удалён как противоречащий LLM-first контракту.
- Add-on устанавливается per-component; glossary components пропускаются.
- Если на компонент действует inherited/site-wide/category/project autotranslate add-on или найдено больше одного direct component add-on'а, preflight завершается ошибкой без изменений. Команда ничего автоматически не удаляет.
- Ровно один direct component add-on приводится к канонической P1-конфигурации.
- Новый компонент требует повторного запуска setup. Add-on уже настроенного
  компонента автоматически распространяется на добавленную target translation,
  но route для нового языка нужно настроить **до** её добавления, а setup затем
  повторить как операционную проверку. Unchanged components не получают новый
  initial sweep.
- `q=state:<translated` не затрагивает state 20/30, но включает пустые строки, `Needs editing` и `Needs rewriting`.

### Каноническая конфигурация

```python
{
    "auto_source": "mt",
    "mode": "translate",
    "engines": ["openrouter"],
    "q": "state:<translated",
    "threshold": 80,
    "component": None,
}
```

### Гарантия ошибки

При любой ошибке preflight не должны измениться:

- `Project.translation_review`;
- `Component.enforced_checks`;
- количество и конфигурация add-on'ов;
- Celery queue initial sweep.

---

## Контекст для исполнителя

- `docs/LLM-first/llm-first-product-research.md` — часть 4, P1.
- `weblate/addons/autotranslate.py:65-173` — configuration и постановка задач.
- `weblate/addons/base.py:165-185,268-304,379-386` — create/configure и initial sweep.
- `weblate/addons/management/commands/install_addon.py:33-72` — form validation.
- `weblate/trans/forms.py:1247-1324` — effective machinery choices проекта.
- `weblate/trans/models/project.py:1332-1351` — merge global/project settings.
- `weblate/trans/models/component.py:1295-1297,5691-5693,6651-6665` — enforced checks recheck.
- `weblate/addons/models.py:133-243,705-733` — effective add-on scopes и dispatch.
- `weblate/utils/management/base.py:23` — Weblate `BaseCommand`.

---

### Task 1: Зафиксировать контракт failing-тестами

**Files:**

- Modify: `weblate/trans/tests/test_commands.py`

#### Step 1: Добавить test double для OpenRouter

Добавить импорты `AutoTranslateAddon`, `Addon`, `CHECKS`, `Check`, `Setting`, `SettingCategory`, `MACHINERY`, `STATE_NEEDS_REWRITING`, `STATE_TRANSLATED` и `Unit` из соответствующих существующих модулей.

Test double должен поддерживать тот же routing contract, который нужен preflight, без сетевого вызова:

```python
class FakeOpenRouter:
    name = "OpenRouter"

    def __init__(self, settings) -> None:
        self.settings = settings

    @classmethod
    def get_identifier(cls) -> str:
        return "openrouter"

    def is_supported(self, _source_language: str, target_language: str) -> bool:
        routing = self.settings.get("routing", {})
        normalized = {
            str(key).casefold().replace("-", "_"): model
            for key, model in routing.items()
        }
        code = target_language.casefold().replace("-", "_")
        base = code.split("_", 1)[0].split("@", 1)[0]
        return code in normalized or base in normalized or "*" in routing
```

#### Step 2: Добавить общую test fixture

В `LLMFirstSetupTest(ComponentTestCase)`:

- создать site-wide `Setting(category=SettingCategory.MT, name="openrouter")` с
  `{"key": "test-key", "routing": {"*": "test/model"}}`;
- patch `MACHINERY.data` значением `{"openrouter": FakeOpenRouter}`;
- patch `CHECKS.data` фиктивными зарегистрированными `game-markup` и `game-line-break`;
- patch `weblate.addons.autotranslate.auto_translate_component.delay_on_commit` и возвращать его `call_args_list` из helper `run_setup`;
- helper `assert_unchanged` должен проверять `translation_review=False`, пустой `enforced_checks`, исходное количество и конфигурацию add-on'ов и отсутствие sweep calls.

Не импортировать `weblate_customization` в core test suite: package копируется в dev container и не является зависимостью основного Weblate test environment.

#### Step 3: Написать positive tests

Добавить отдельные тесты:

1. `test_enables_translation_review`.
2. `test_installs_canonical_addon_and_triggers_initial_sweep` — ровно один add-on и один component ID в `delay_on_commit`.
3. `test_skips_glossary` — создать glossary через `self.component.create_glossary()`, затем получить его из БД; не собирать `Component(...)` вручную.
4. `test_preserves_existing_enforced_checks` — `same` остаётся перед двумя game checks.
5. `test_enforced_check_reclassifies_existing_translation` — создать для translated unit запись `Check(name="game-markup")`, выполнить setup и проверить `STATE_NEEDS_REWRITING`.
6. `test_second_run_is_noop_and_does_not_schedule_sweep` — count остаётся 1, configuration неизменна, второй `delay_on_commit.call_args_list == []`.
7. `test_rerun_installs_only_new_component` — после первого setup создать второй non-glossary component, повторить команду и проверить sweep только для нового component ID.
8. `test_rerun_validates_new_target_without_reconfiguring_addon` — сначала
   настроить route, затем добавить target translation существующему компоненту;
   повторный setup проходит, add-on остаётся один, full sweep не создаётся.

#### Step 4: Написать no-write preflight tests

Каждый тест ниже должен ожидать `CommandError`, затем вызывать `assert_unchanged`:

1. неизвестный project slug;
2. `openrouter` отсутствует в `MACHINERY`;
3. effective OpenRouter setting отсутствует;
4. project-level `machinery_settings={"openrouter": None}` отключает site-wide service;
5. effective setting с пустым `key` или не-object/пустым `routing` отклоняется без
   вывода key;
6. routing без `*` и без текущего target language возвращает в ошибке component slug и language code;
7. `game-markup` или `game-line-break` отсутствует в `CHECKS`;
8. два direct `AutoTranslateAddon.create(..., run=False)` не удаляются и не перенастраиваются;
9. один project-scoped/inherited add-on не изменяется и блокирует component-scoped setup;
10. невалидная `AutoTranslateAddon.get_add_form(..., data=configuration)` configuration блокирует весь проект;
11. второй компонент с ошибкой preflight оставляет первый полностью неизменным —
    отдельная проверка project-wide all-or-nothing, а не только single-component
    failure.

Для duplicate/inherited cases сохранять исходные `configuration` и сравнивать их после ошибки. Это проверяет не только количество строк в БД, но и no-write guarantee.

#### Step 5: Подтвердить RED

```bash
./rundev.sh test weblate/trans/tests/test_commands.py -k LLMFirstSetup -- -n0
```

Expected: tests падают с `Unknown command: 'llm_first_setup'`; glossary helper не создаёт отдельной ошибки.

#### Step 6: Commit

```bash
git add weblate/trans/tests/test_commands.py
git commit -m "test(trans): specify safe LLM-first project setup"
```

---

### Task 2: Реализовать transactional preflight и setup

**Files:**

- Create: `weblate/trans/management/commands/llm_first_setup.py`
- Test: `weblate/trans/tests/test_commands.py`

#### Step 1: Добавить constants и immutable setup record

```python
OPENROUTER_ENGINE = "openrouter"
GAME_ENFORCED_CHECKS = ("game-markup", "game-line-break")
CANONICAL_CONFIGURATION: AutoTranslateAddonStoredConfiguration = {
    "auto_source": "mt",
    "mode": "translate",
    "engines": [OPENROUTER_ENGINE],
    "q": "state:<translated",
    "threshold": 80,
    "component": None,
}


@dataclass(frozen=True)
class ComponentSetup:
    component: Component
    addon: Addon | None
    configuration: AutoTranslateAddonStoredConfiguration
```

Новый Python file должен иметь `from __future__ import annotations`, GPL-3.0-or-later header и translatable operator-facing strings через `gettext`/`gettext_lazy`.

#### Step 2: Реализовать `preflight(project)` без записей

В таком порядке:

1. Проверить присутствие обоих `GAME_ENFORCED_CHECKS` в `CHECKS`.
2. Получить class через `MACHINERY.get("openrouter")`; отсутствие — `CommandError`.
3. Получить `project.get_machinery_settings()["openrouter"]`; отсутствие/`None` — `CommandError`.
   Проверить локально, без внешнего HTTP, что effective mapping содержит непустые
   `key` и `routing`. Не печатать secret в ошибке.
4. Создать `service = machinery_class(effective_settings)`. Не вызывать
   `validate_settings()`/`validate_service_configuration()`: machinery form делает
   пробный сетевой перевод, а setup preflight должен быть детерминированным и не
   расходовать OpenRouter до применения конфигурации.
5. Загрузить все non-glossary components с `select_for_update()`, `select_related("source_language")` и `prefetch_related("translation_set__language")`.
6. Вызвать `Addon.objects.prefetch_for_components(components)`, чтобы увидеть direct, category, project и site-wide scopes.
7. Для каждого компонента выбрать effective add-on'ы из
   `component.addons_cache.addons` с `name == AutoTranslateAddon.name`:
   - любой add-on с `component_id != component.pk` — inherited conflict;
   - больше одного direct add-on — duplicate conflict;
   - ноль или один direct add-on допустимы.
8. Получить target language objects тем же смыслом, что runtime
   `translation_set.exclude_source()`, не сравнивая только строки вручную.
9. Для каждого target language вызвать
   `service.is_supported(component.source_language.code, language.code)`. Собрать
   все uncovered languages, а не останавливаться на первом.
10. Создать `AutoTranslateAddon.get_add_form(None, component=component, data=CANONICAL_CONFIGURATION)` и проверить `form.is_valid()` так же, как `install_addon`.
11. Собрать `ComponentSetup` только для полностью валидного компонента.
12. Если накоплены errors, выбросить один `CommandError` с component slugs и всеми причинами; ничего не применять.

Preflight не должен вызывать `Addon.create`, `configure`, `Project.save`, `Component.save` или Celery tasks.

#### Step 3: Реализовать `apply_setup(project, setups)`

```python
if not project.translation_review:
    project.translation_review = True
    project.save(update_fields=["translation_review"])

for setup in setups:
    missing = [
        check_id
        for check_id in GAME_ENFORCED_CHECKS
        if check_id not in setup.component.enforced_checks
    ]
    if missing:
        setup.component.enforced_checks = [
            *setup.component.enforced_checks,
            *missing,
        ]
        setup.component.save(update_fields=["enforced_checks"])

    if setup.addon is None:
        AutoTranslateAddon.create(
            component=setup.component,
            configuration=setup.configuration,
        )
    elif setup.addon.configuration != setup.configuration:
        setup.addon.addon.configure(setup.configuration)
    else:
        # Истинный no-op: не вызывать configure, иначе повторный запуск
        # снова поставит component_update initial sweep.
        pass
```

Для каждой ветки собрать operator message: `Installed`, `Updated` или `Already configured`. Не печатать сообщения внутри транзакции: при неожиданной DB-ошибке они не должны утверждать, что rolled-back изменение применено.

#### Step 4: Реализовать atomic `handle`

```python
def handle(self, *args, **options) -> None:
    with transaction.atomic():
        try:
            project = Project.objects.select_for_update().get(slug=options["project"])
        except Project.DoesNotExist as error:
            raise CommandError(
                gettext("Project not found: {project}").format(
                    project=options["project"]
                )
            ) from error

        setups = self.preflight(project)
        messages = [
            gettext("Preflight passed for {count} component(s)").format(
                count=len(setups)
            ),
            *self.apply_setup(project, setups),
        ]

    for message in messages:
        self.stdout.write(message)
```

Параметр команды только один: positional project slug. Initial sweep от `create`/`configure` регистрируется через `delay_on_commit` и стартует после выхода из outer transaction.

#### Step 5: Запустить tests

```bash
./rundev.sh test weblate/trans/tests/test_commands.py -k LLMFirstSetup -- -n0
```

Expected: все positive/no-write/idempotency tests passed; HTTP к OpenRouter отсутствует.

#### Step 6: Targeted lint

```bash
uv run prek run --files \
  weblate/trans/management/commands/llm_first_setup.py \
  weblate/trans/tests/test_commands.py
```

Expected: passed; после autofix повторить tests и `prek`.

#### Step 7: Commit

```bash
git add \
  weblate/trans/management/commands/llm_first_setup.py \
  weblate/trans/tests/test_commands.py
git commit -m "feat(trans): add safe LLM-first project setup"
```

---

### Task 3: Документировать операторский flow

**Files:**

- Modify: `docs/admin/management.rst`
- Modify: `docs/changes.rst`
- Modify: `AGENTS.md`

#### Step 1: Добавить `llm_first_setup` в `docs/admin/management.rst`

Использовать `.. weblate-admin:: llm_first_setup <project>` и объяснить:

- что target languages берутся из существующих translations;
- что команда не создаёт языки;
- как routing выбирает exact code, base code и `*` fallback;
- effective OpenRouter configuration и route для каждого языка обязательны;
- glossary components пропускаются;
- inherited/multiple direct add-on'ы блокируют setup без изменений;
- новый component не получает add-on автоматически;
- для нового target language сначала настраивается route, затем язык добавляется и
  setup повторяется для проверки; уже установленный add-on действует на него без
  переустановки;
- unchanged components не получают повторный initial sweep;
- LLM jobs выполняются асинхронно после завершения команды.

#### Step 2: Добавить changelog в верхнюю unreleased section

```rst
* Added :wladmin:`llm_first_setup` to safely configure projects for the event-driven OpenRouter translation and review workflow.
```

#### Step 3: Обновить `AGENTS.md`

Добавить command в repository-specific parts с теми же границами: transactional no-write preflight, route coverage, conflict refusal, glossary skip и повторный запуск после новых компонентов/языков.

#### Step 4: Commit

```bash
git add docs/admin/management.rst docs/changes.rst AGENTS.md
git commit -m "docs: document LLM-first project setup"
```

---

### Task 4: Смоук на dev-инстансе

#### Step 1: Проверить effective configuration проекта

```bash
cd dev-docker && docker compose exec weblate weblate shell -c \
  "from weblate.trans.models import Project; p=Project.objects.get(slug='<project-slug>'); print(sorted(p.get_machinery_settings()))"
```

Expected: список содержит `openrouter`. Проверка только site-wide `Setting` недостаточна, потому что project-level configuration может дополнить или отключить сервис.

#### Step 2: Запустить setup

```bash
cd dev-docker && docker compose exec weblate \
  weblate llm_first_setup <project-slug>
```

Expected: сначала `Preflight passed`, затем сообщения по non-glossary components. При uncovered language или add-on conflict команда завершается non-zero до любых изменений.

#### Step 3: Проверить initial sweep

- Дождаться `Automatic translation` activity в истории проекта.
- Проверить ранее пустую строку: target заполнен, state — `Waiting for review`.
- Не требовать синхронного LLM-result от management-команды: она только ставит Celery jobs.
- В activity зафиксировать время от завершения setup до первого результата,
  количество eligible/заполненных units и provider errors как baseline P1.

#### Step 4: Проверить event flow

- Добавить новую source-строку через git pull, upload или UI.
- Проверить появление state 20 во всех уже существующих target translations.
- Проверить, что state 20/30 не перезаписаны.
- Сломать Unity tag/placeholder в тестовой target-строке и получить `Needs rewriting`.

#### Step 5: Проверить повторный запуск

Повторить команду. Expected: `Already configured` для прежних компонентов и отсутствие нового initial sweep.

#### Step 6: Проверить новый компонент

- Создать новый non-glossary component после первого setup.
- Убедиться, что add-on не появился автоматически.
- Повторить setup и проверить sweep только для нового компонента.

#### Step 7: Проверить новый target language

- Сначала добавить exact/base/fallback route в effective OpenRouter settings.
- Затем создать target translation у уже настроенного компонента.
- Проверить, что существующий add-on применился без новой строки add-on в БД.
- Повторить setup: Expected `Already configured`, route проходит preflight,
  повторный full initial sweep не создаётся.

---

### Task 5: Финальная верификация и публикация

#### Step 1: Весь command test module

```bash
./rundev.sh test weblate/trans/tests/test_commands.py -- -n0
```

Expected: passed без skip `ComponentTestCase`.

#### Step 2: Полный configured lint/format gate

```bash
uv run prek run --all-files
```

Expected: passed; после autofix повторить targeted tests.

#### Step 3: Pylint

```bash
uv run pylint \
  weblate/trans/management/commands/llm_first_setup.py \
  weblate/trans/tests/test_commands.py
```

Expected: no new failures.

#### Step 4: CI-compatible mypy

```bash
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py \
  | ./scripts/filter-mypy.sh
```

Expected: no new findings in changed code.

#### Step 5: Проверить diff и рабочее дерево

```bash
git diff --check
git status --short
```

Expected: файлы реализации этого плана готовы к commit; любые заранее существовавшие
чужие локальные изменения остаются unstaged и не изменены исполнителем.

#### Step 6: Push

```bash
git push origin HEAD
```

Expected: текущая feature branch опубликована в `origin`.

---

## Вне scope P1

- Авто-наследование setup новым компонентом без повторного запуска команды.
- Автоматическое удаление или консолидация конфликтующих add-on'ов.
- Создание target languages.
- QE score, приоритизация review queue, auto-approve и model cascade — P2/P3.
- Синхронное ожидание OpenRouter из management-команды.
- Изменение default `AutoTranslateAddon` для остальных проектов.

## Риски исполнения

- **Background queue latency.** Команда подтверждает постановку initial sweep, а не завершение LLM-запросов; результат проверяется отдельно.
- **Routing drift.** Add-on уже настроенного компонента увидит добавленный target
  language автоматически и может успеть поставить неуспешную задачу, если route не
  подготовлен заранее. Операторский порядок: route → language → повторный setup.
- **Existing add-on ownership.** Один direct add-on будет приведён к P1-конфигурации. Inherited или multiple direct add-on'ы блокируют setup.
- **Host-side VCS.** Apple Git 2.39.5 ниже `GitRepository.req_version = 2.46`; использовать `PATH=/opt/homebrew/bin:$PATH` или `./rundev.sh test`.
- **Host-side database.** Для host pytest нужны test settings, PostgreSQL variables и предварительный `collectstatic`; container test предпочтительнее.
- **Container resources.** При нестабильных массовых setup errors сначала проверить `docker stats --no-stream`, как описано в `AGENTS.md`.
