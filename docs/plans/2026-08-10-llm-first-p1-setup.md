# P1 «Строка приходит переведённой» — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Одна идемпотентная management-команда `llm_first_setup <project>`, которая переводит проект в LLM-first режим: review-workflow включён, autotranslate-addon (OpenRouter, mode=translate) установлен на все не-глоссарные компоненты, игровые чеки — enforced.

**Architecture:** Ноль нового кода в ядре Weblate — команда только применяет существующие механизмы: `Project.translation_review` (очередь ревью), `AutoTranslateAddon` с `auto_source=mt` (событийный LLM-перевод: EVENT_COMPONENT_UPDATE срабатывает и на git-пулле, и на загрузке файлов — `weblate/trans/models/translation.py:2065`, `weblate/trans/models/component.py:4730`), `Component.enforced_checks` (брак по маркапу → STATE_NEEDS_REWRITING через встроенную в `Component.save` перепроверку `changed_enforced_checks`, `weblate/trans/models/component.py:1221`). Установка addon'а сразу запускает первый проход (`weblate/addons/base.py:379`).

**Tech Stack:** Django management command, существующие модели `Project`/`Component`/`Addon`, pytest (`ComponentTestCase`).

**Контекст для исполнителя (прочитать перед стартом):**
- `docs/specs/llm-first-product-research.md` — часть 4, раздел P1 (зачем это всё).
- `weblate/addons/autotranslate.py:65-130` — addon и ключи его конфигурации.
- `weblate/addons/management/commands/install_addon.py` — образец установки addon'а из команды (включая `addon.addon.configure(configuration)` для идемпотентного обновления).
- `weblate/utils/management/base.py:23` — `BaseCommand`, от которого наследуемся.

**Решения, принятые заранее (не пересматривать в ходе исполнения):**
- Addon ставится **per-component, глоссарии исключаются** (`component.is_glossary`): проектный scope зацепил бы глоссарные компоненты, а термины Heart Abyss курируются людьми. Цена: после создания нового компонента команду нужно перезапустить (она идемпотентна). Это осознанный trade-off P1.
- `mode=translate`, не `suggest`: при включённом `translation_review` строка со state 20 отображается как «Waiting for review» — это и есть очередь ревью. Auto-approve в P1 нет.
- Check-идентификаторы `game-markup`, `game-line-break` в тестовом окружении хоста не зарегистрированы (`WEBLATE_ADD_CHECK` живёт только в docker-compose) — это не мешает: `enforced_checks` — обычный JSON-список без валидации при `save()`; тесты проверяют содержимое списка.

---

### Task 1: Failing-тесты команды

**Files:**
- Modify: `weblate/trans/tests/test_commands.py` (добавить класс в конец файла; импорты `call_command`, `CommandError`, `StringIO`, `patch`, `ComponentTestCase` уже есть в файле — см. строки 8-33)

**Step 1: Написать failing-тесты**

Добавить в конец `weblate/trans/tests/test_commands.py`:

```python
class LLMFirstSetupTest(ComponentTestCase):
    ADDON_NAME = "weblate.autotranslate.autotranslate"

    def run_setup(self, *args):
        output = StringIO()
        call_command("llm_first_setup", self.project.slug, *args, stdout=output)
        return output.getvalue()

    def get_addons(self, component):
        from weblate.addons.models import Addon

        return Addon.objects.filter(component=component, name=self.ADDON_NAME)

    def create_glossary(self):
        # Скелет; обязательные поля сверить с test_edit.py:1990 /
        # test_views.py:1125 (create_glossary) — см. раздел рисков.
        from weblate.trans.models import Component

        glossary = Component(
            name="Glossary",
            slug="glossary",
            project=self.project,
            is_glossary=True,
            manage_units=True,
            file_format="po-mono",
            filemask="po/*.po",
            template="po/hello.pot",
            new_base="po/hello.pot",
            vcs="local",
            repo="local:",
            push="",
            branch="main",
        )
        glossary.full_clean()
        glossary.save()
        return glossary

    def test_enables_translation_review(self) -> None:
        self.assertFalse(self.project.translation_review)
        self.run_setup()
        self.project.refresh_from_db()
        self.assertTrue(self.project.translation_review)

    def test_installs_addon_with_expected_configuration(self) -> None:
        self.run_setup()
        addons = self.get_addons(self.component)
        self.assertEqual(addons.count(), 1)
        self.assertEqual(
            addons[0].configuration,
            {
                "auto_source": "mt",
                "mode": "translate",
                "engines": ["openrouter"],
                "q": "state:<translated",
                "threshold": 80,
                "component": None,
            },
        )

    def test_skips_glossary_components(self) -> None:
        glossary = self.create_glossary()
        self.run_setup()
        glossary.refresh_from_db()
        self.assertEqual(self.get_addons(glossary).count(), 0)
        self.assertEqual(glossary.enforced_checks, [])

    def test_sets_enforced_checks_preserving_existing(self) -> None:
        self.component.enforced_checks = ["same"]
        self.component.save(update_fields=["enforced_checks"])
        self.run_setup()
        self.component.refresh_from_db()
        self.assertEqual(
            self.component.enforced_checks,
            ["same", "game-markup", "game-line-break"],
        )

    def test_idempotent(self) -> None:
        self.run_setup()
        self.run_setup()
        self.assertEqual(self.get_addons(self.component).count(), 1)
        self.component.refresh_from_db()
        self.assertEqual(
            self.component.enforced_checks, ["game-markup", "game-line-break"]
        )

    def test_custom_engine(self) -> None:
        self.run_setup("--engine", "weblate")
        addons = self.get_addons(self.component)
        self.assertEqual(addons[0].configuration["engines"], ["weblate"])

    def test_unknown_project(self) -> None:
        with self.assertRaises(CommandError):
            call_command("llm_first_setup", "no-such-project")

    def test_install_triggers_initial_sweep(self) -> None:
        with patch(
            "weblate.addons.autotranslate.auto_translate_component.delay_on_commit"
        ) as mocked:
            self.run_setup()
        component_ids = {call.args[0] for call in mocked.call_args_list}
        self.assertIn(self.component.pk, component_ids)
```

**Step 2: Убедиться, что тесты падают**

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k LLMFirstSetup -- -n0`
(или host-side — команда с env из раздела рисков ниже)

Expected: все тесты FAIL/ERROR с `Unknown command: 'llm_first_setup'`.

**Step 3: Commit**

```bash
git add weblate/trans/tests/test_commands.py
git commit -m "test(trans): add failing tests for llm_first_setup command"
```

### Task 2: Реализация команды

**Files:**
- Create: `weblate/trans/management/commands/llm_first_setup.py`

**Step 1: Написать команду**

```python
# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.addons.autotranslate import AutoTranslateAddon
from weblate.addons.models import Addon
from weblate.trans.models import Project
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

GAME_ENFORCED_CHECKS = ["game-markup", "game-line-break"]


class Command(BaseCommand):
    help = "configures a project for the LLM-first translation flow"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("project", help="Project slug")
        parser.add_argument(
            "--engine",
            action="append",
            default=None,
            help="Machinery engine slug (repeatable, default: openrouter)",
        )

    def handle(self, *args, **options) -> None:
        try:
            project = Project.objects.get(slug=options["project"])
        except Project.DoesNotExist as error:
            msg = f"Project not found: {options['project']}"
            raise CommandError(msg) from error

        configuration = {
            "auto_source": "mt",
            "mode": "translate",
            "engines": options["engine"] or ["openrouter"],
            "q": "state:<translated",
            "threshold": 80,
            "component": None,
        }

        if not project.translation_review:
            project.translation_review = True
            project.save(update_fields=["translation_review"])
            self.stdout.write(f"Enabled translation review on {project}")

        for component in project.component_set.iterator():
            if component.is_glossary:
                continue

            missing = [
                name
                for name in GAME_ENFORCED_CHECKS
                if name not in component.enforced_checks
            ]
            if missing:
                component.enforced_checks = [*component.enforced_checks, *missing]
                component.save(update_fields=["enforced_checks"])
                self.stdout.write(f"Enforced checks updated on {component}")

            addons = Addon.objects.filter(
                component=component, name=AutoTranslateAddon.name
            )
            if addons:
                for addon in addons:
                    addon.addon.configure(configuration)
                self.stdout.write(f"Updated add-on on {component}")
            else:
                AutoTranslateAddon.create(
                    component=component, configuration=configuration
                )
                self.stdout.write(f"Installed add-on on {component}")
```

Замечания для исполнителя:
- `component.save(update_fields=["enforced_checks"])` корректен: `Component.save` явно отслеживает `changed_enforced_checks` (`weblate/trans/models/component.py:1221`) и сам перепроверяет юниты.
- `AutoTranslateAddon.create(...)` с `run=True` (дефолт) сразу запускает `component_update` → первый проход autotranslate (`weblate/addons/base.py:379`). В тестах `delay_on_commit` не срабатывает внутри незакоммиченной транзакции — потому и патчится в `test_install_triggers_initial_sweep`.
- Идемпотентность обновления: `addon.addon.configure(configuration)` — тот же путь, что в `install_addon.py:62`.

**Step 2: Прогнать тесты**

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k LLMFirstSetup -- -n0`
Expected: 8 passed.

**Step 3: Lint**

Run: `uv run prek run --files weblate/trans/management/commands/llm_first_setup.py weblate/trans/tests/test_commands.py`
Expected: passed (или автопочинка формата — перепрогнать).

**Step 4: Commit**

```bash
git add weblate/trans/management/commands/llm_first_setup.py weblate/trans/tests/test_commands.py
git commit -m "feat(trans): add llm_first_setup command for LLM-first project flow"
```

### Task 3: Смоук на dev-инстансе

Ничего не деплоить: репозиторий примонтирован в `/app/src`, команда доступна в контейнере сразу.

**Step 1: Проверить, что openrouter сконфигурирован**

Run: `cd dev-docker && docker compose exec weblate weblate shell -c "from weblate.configuration.models import Setting; print(Setting.objects.filter(category=2, name='openrouter').exists())"`
Expected: `True`. Если `False` — сначала настроить OpenRouter в `/manage/machinery/`, без этого autotranslate молча не даст результатов.

**Step 2: Применить команду к реальному проекту**

Run: `docker compose exec weblate weblate llm_first_setup <project-slug>` (слаг посмотреть на http://localhost:3001/projects/)
Expected: строки `Enabled translation review...`, `Installed add-on on ...` по каждому не-глоссарному компоненту.

**Step 3: Проверить первый проход**

- http://localhost:3001/projects/<slug>/#history — записи «Automatic translation» после установки addon'а.
- Открыть непереведённую ранее строку: target заполнен, состояние «Waiting for review».

**Step 4: Проверить событийный путь (главный сценарий P1)**

- Добавить новую строку: UI компонента → Strings → Add new translation string (или загрузка файла методом «add»).
- Expected: в течение минуты строка переведена во всех целевых языках (change_event: `weblate/addons/autotranslate.py:251` — source-юнит веером на все языки), состояние «Waiting for review».
- Проверить gate: строке с Unity-маркапом руками вписать перевод с испорченным тегом — состояние должно стать «Needs rewriting» (enforced check).

**Step 5: Зафиксировать результат смоука**

Никаких коммитов; при расхождениях — вернуться к Task 2, не подгонять проверки.

### Task 4: Документация

**Files:**
- Modify: `docs/changes.rst` (верхняя, невыпущенная секция)
- Modify: `AGENTS.md` (секция «Project-specific setup»)

**Step 1: Changelog**

В верхнюю секцию `docs/changes.rst` добавить строку в стиле соседних:

```rst
* Added the :program:`llm_first_setup` management command configuring a project for the LLM-first flow (review workflow, event-driven OpenRouter automatic translation, enforced game checks).
```

**Step 2: AGENTS.md**

В список repository-specific parts добавить пункт (после пункта про `weblate_customization/`):

```markdown
- `weblate/trans/management/commands/llm_first_setup.py` - idempotent
  command switching a project to the LLM-first flow: enables
  `translation_review`, installs the autotranslate add-on
  (`auto_source=mt`, `mode=translate`, OpenRouter) on every non-glossary
  component, and enforces `game-markup`/`game-line-break`. Re-run it after
  adding components; glossary components are skipped on purpose.
```

**Step 3: Commit**

```bash
git add docs/changes.rst AGENTS.md
git commit -m "docs: document llm_first_setup command"
```

### Task 5: Финальная верификация

**Step 1:** Run: `./rundev.sh test weblate/trans/tests/test_commands.py -- -n0`
Expected: весь файл зелёный (регрессий в соседних командных тестах нет).

**Step 2:** Run: `uv run prek run --all-files`
Expected: passed. Перед запуском проверить `docker stats --no-stream`, если контейнерные тесты внезапно медленные/падают пачками — см. AGENTS.md про memory starvation.

---

## Вне скоупа P1 (не делать)

- Авто-установка addon'а/enforced_checks при создании компонента из loc-kit — P1 закрывается перезапуском команды; автоматизация — кандидат в P2.
- QE-скор, порог auto-approve, каскад моделей — P2/P3 (`docs/specs/llm-first-product-research.md`).
- Изменение дефолтов `AutoTranslateAddon` (upstream-поведение не трогаем).

## Известные риски исполнения (выявлены при подготовке плана)

- **Host-side git слишком старый.** Apple Git 2.39.5 < `GitRepository.req_version = 2.46` (`weblate/vcs/git.py:406`): `is_supported()` кэширует `False` на уровне класса, и все тесты на `ComponentTestCase`/`ViewTestCase` молча скипаются с "VCS git not available!". Перед прогоном тестов: `PATH=/opt/homebrew/bin:$PATH` (homebrew git 2.55 установлен) или запускать через `./rundev.sh test`.
- **Тесты нуждаются в БД env (host-side).** Полная команда:
  `PATH=/opt/homebrew/bin:$PATH CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 CI_DB_USER=weblate CI_DB_PASSWORD=weblate CI_DB_NAME=<unique> DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest ... -n0`
  Плюс предусловия из `docs/contributing/tests.rst` (collectstatic однажды).
- **Тестовая фикстура glossary в плане — набросок.** Создание `Component(is_glossary=True, vcs="local", repo="local:", ...)` в `test_skips_glossary_components` может потребовать подгонки обязательных полей по образцу `weblate/trans/tests/test_edit.py:1990` / `test_views.py:1125` (`create_glossary`). Это единственная непроверенная часть тест-кода плана; остальное опирается на существующие фикстуры `ComponentTestCase`.
- `test_install_triggers_initial_sweep` опирается на патч `auto_translate_component.delay_on_commit` — верно для текущего кода addon'а (`weblate/addons/autotranslate.py:161`), но если executor меняет имя/путь мока, тест правится, а не удаляется.
