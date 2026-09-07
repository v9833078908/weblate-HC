# MT Run Cost Receipt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Продюсер, запустивший обычный машинный перевод, видит на странице отчёта прогона, что было записано и во что это встало в долларах — той же страницей, на которой сейчас живёт отчёт судьи.

**Architecture:** `JudgeRun` переименовывается в `ProducerRun` (state-only миграция, таблица `trans_judgerun` остаётся на месте) и начинает создаваться для запуска автоперевода, который обращается к машинному переводу (`auto_source="mt"`), а не только для судейского. Запуск из памяти переводов (`auto_source="others"`) прогона не создаёт: он ничего не стоит, и чек ему не нужен. Каждая строка `LLMUsageLog` получает FK на прогон, поэтому расход прогона считается одним `aggregate()` без временных окон. Страница отчёта получает общую карточку расхода для обоих режимов и две MT-специфичные карточки вместо судейской триажной.

**Tech Stack:** Django 5 ORM, `SeparateDatabaseAndState`-миграции, Celery, Django templates + Bootstrap 5, crispy-forms, vanilla JS (`weblate/static/loader-bootstrap.js`), pytest через `./rundev.sh test`.
**Review:** 2026-09-07 — implementation-ready after a source-level review. The review closed four correctness holes: every supported automatic-translation scope now gets a restorable receipt; a swallowed MT failure finalizes the receipt as failed; an incomplete price never becomes a per-string price; and the ordinary-MT preview keeps its own eligibility semantics instead of inheriting the judge cap.


## Global Constraints

- Каждый новый Python-файл начинается с `# Copyright © HCGameLoc`, пустой строки-комментария и `# SPDX-License-Identifier: GPL-3.0-or-later`.
- Каждый Python-модуль содержит `from __future__ import annotations`.
- Все пользовательские строки в шаблонах — через `{% translate %}` / `{% blocktranslate %}`; в Python — через `gettext` / `gettext_lazy`. Строки для API, аудита и логов не локализуются.
- Ruff-подавления пишутся человекочитаемыми именами: `# ruff: ignore[assert]`, никогда `# noqa: S101`.
- Коммиты — Conventional Commits: `<type>(<scope>): <description>`.
- Тесты запускаются внутри контейнера: `./rundev.sh test <path>`. Хостовый `uv run pytest` требует отдельной настройки БД и в этом плане не используется.
- Линт после каждой задачи не запускается; один прогон `uv run prek run --all-files` в последней задаче.
- У отдельной строки леджера `cost_usd = NULL` означает «цена не сообщена». Агрегат может показать `0` только как сумму **известных** цен, но рядом с `unpriced_requests > 0` он обязан явно назвать цену неполной; ни карточка, ни средняя цена за строку не имеют права представить такой запуск бесплатным.
- Правило переименования: symbol-aware переименования выполняются инструментом `lsp` (`action: "rename"`), не текстовой заменой.
- URL-имя маршрута `judge-run` **сохраняется** во всех задачах: на него ссылаются шаблоны, `weblate/trans/tasks.py` и внешние закладки продюсеров. Переименовывается модель и Python-символы, не публичный идентификатор маршрута.
- Перед каждым коммитом проверить `git status --short` и добавить только файлы текущей задачи. Не использовать `git add <directory>`: это захватывает чужие изменения в том же дереве.

---

### Task 1: Переименование `JudgeRun` → `ProducerRun`

**Files:**
- Modify: `weblate/trans/models/judge.py:276-339`
- Modify: `weblate/trans/models/__init__.py:36-40,78-82`
- Create: `weblate/trans/migrations/0121_producer_run.py`
- Test: `weblate/trans/tests/test_judge.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `weblate.trans.models.ProducerRun` с теми же полями и `Meta.db_table = "trans_judgerun"`; обратный аксессор `User.producer_runs`; классы `ProducerRun.ScopeType`, `ProducerRun.Status`. `ScopeType` покрывает **каждый** объект, который принимает `BatchAutoTranslate`: `Translation`, `Component`, `Category`, `Project`, `ProjectLanguage`, `Workspace`. Задачи 2-11 используют только это имя.

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `weblate/trans/tests/test_judge.py`:

```python
class ProducerRunModelTest(TestCase):
    def test_model_keeps_the_original_table(self) -> None:
        """The rename is state-only: renaming the table would move judge history."""
        self.assertEqual(ProducerRun._meta.db_table, "trans_judgerun")  # ruff: ignore[private-member-access]

    def test_actor_reverse_accessor_is_producer_runs(self) -> None:
        user = User.objects.create(username="producer-run-actor")
        run = ProducerRun.objects.create(
            actor=user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id="1",
            scope_label="Test",
            scope_path="/projects/test/test/",
            requested_mode="translate",
            cap=100,
        )
        self.assertEqual([item.pk for item in user.producer_runs.all()], [run.pk])

    def test_scope_types_cover_every_automatic_translation_target(self) -> None:
        self.assertEqual(
            {value for value, _label in ProducerRun.ScopeType.choices},
            {
                "translation",
                "component",
                "category",
                "project",
                "project-language",
                "workspace",
            },
        )
```

Импорты в шапке файла: добавить `ProducerRun` в существующий импорт из `weblate.trans.models`, а `User` — из `weblate.auth.models`, если его там ещё нет. `TestCase` берётся из `django.test`.

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py -k ProducerRunModelTest`
Expected: FAIL с `ImportError: cannot import name 'ProducerRun' from 'weblate.trans.models'`

- [ ] **Step 3: Переименовать модель через LSP**

Выполнить через инструмент `lsp`:

```json
{"action": "rename", "file": "weblate/trans/models/judge.py", "line": 276, "symbol": "JudgeRun", "new_name": "ProducerRun"}
```

Это правит определение и все 232 ссылки в `weblate/trans/`, `weblate/api/tests.py` и тестах. Файлы миграций `0107_*` и `0111_*` править **нельзя**: исторические миграции хранят старое имя, и это корректно. Если LSP тронул файл в `weblate/trans/migrations/`, откатить **только этот исторический файл** через `git restore --source=HEAD -- <path>`; новую `0121_producer_run.py` не откатывать.

- [ ] **Step 4: Дописать модель руками**

`weblate/trans/models/judge.py`, класс `ProducerRun`: обновить докстринг, `ScopeType`, `related_name` и `Meta`.

```python
class ProducerRun(models.Model):
    """
    One permission-checked producer launch across one closed scope.

    Both an LLM judge launch and a plain automatic-translation launch are
    the same object: a producer asked for work over a closed scope, and
    the run is what they return to for its outcome and its cost.
    ``requested_mode`` carries which one it was, using the mode values of
    ``AutoForm`` ("translate", "suggest", "fuzzy", "approved", "judge")
    plus the two non-launch passes "recheck" and "drain".
    """
    class ScopeType(models.TextChoices):
        TRANSLATION = "translation"
        COMPONENT = "component"
        CATEGORY = "category"
        PROJECT = "project"
        PROJECT_LANGUAGE = "project-language"
        WORKSPACE = "workspace"
```

`ProjectLanguage.pk` уже является стабильной строкой `"<project-pk>-<language-pk>"` (`weblate/utils/stats.py:1325-1327`), поэтому помещается в существующее `scope_id` без новой колонки. Task 2 восстановит обёртку по обоим PK; `scope_path` остаётся снятым при запуске URL-снимком.

В поле `actor` заменить `related_name="judge_runs"` на `related_name="producer_runs"`.

В `Meta` добавить (сохранив существующий блок `indexes` без изменений — имена индексов заданы явно, поэтому переименование модели их не трогает):

```python
    class Meta:
        # State-only rename: the table still holds every judge run written
        # before this change, so it must not be renamed with the model.
        db_table = "trans_judgerun"
        verbose_name = gettext_lazy("Producer run")
        verbose_name_plural = gettext_lazy("Producer runs")
```

- [ ] **Step 5: Написать миграцию руками**

Create `weblate/trans/migrations/0121_producer_run.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0120_judge_run_unit_repair_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Model name, reverse accessor, table name, display options and the
        # two new application-level scope choices are state only. No existing
        # row, table, column, index, or FK moves.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameModel(old_name="JudgeRun", new_name="ProducerRun"),
                migrations.AlterModelTable(
                    name="producerrun", table="trans_judgerun"
                ),
                migrations.AlterModelOptions(
                    name="producerrun",
                    options={
                        "verbose_name": "Producer run",
                        "verbose_name_plural": "Producer runs",
                    },
                ),
                migrations.AlterField(
                    model_name="producerrun",
                    name="actor",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="producer_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.AlterField(
                    model_name="producerrun",
                    name="scope_type",
                    field=models.CharField(
                        choices=[
                            ("translation", "Translation"),
                            ("component", "Component"),
                            ("category", "Category"),
                            ("project", "Project"),
                            ("project-language", "Project language"),
                            ("workspace", "Workspace"),
                        ],
                        max_length=20,
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
```

- [ ] **Step 6: Проверить, что состояние миграций сходится**

Run: `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations trans --check --dry-run`
Expected: `No changes detected` (БД не нужна). Если печатает предложенную миграцию — состояние не совпало с моделью; сверить `db_table`, `related_name`, `scope_type.choices` и `options`.

- [ ] **Step 7: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_autotranslate.py`
Expected: PASS, ни одного `ImportError`

- [ ] **Step 8: Коммит**

```bash
git add weblate/trans/models/judge.py weblate/trans/models/__init__.py weblate/trans/migrations/0121_producer_run.py weblate/trans/autotranslate.py weblate/trans/judge.py weblate/trans/judge_loop.py weblate/trans/tasks.py weblate/trans/views/basic.py weblate/trans/views/judge.py weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_deferrals.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_commands.py weblate/api/tests.py
git commit -m "refactor(trans): rename JudgeRun to ProducerRun without moving its table"
```

---

### Task 2: Переименование поверхности отчёта прогона

**Files:**
- Modify: `weblate/trans/models/judge.py` (добавить `RUN_KIND_LABELS`)
- Modify: `weblate/trans/views/judge.py:43-60,308-323,396-427,430-544`
- Modify: `weblate/trans/views/basic.py:82,775-856,1010-1022,1129-1132`
- Rename: `weblate/templates/judge-run.html` → `weblate/templates/producer-run.html`
- Rename: `weblate/templates/snippets/judge-runs-menu.html` → `weblate/templates/snippets/producer-runs-menu.html`
- Modify: `weblate/templates/snippets/autoform.html:57-60`
- Modify: `weblate/templates/snippets/judge-readiness.html:3-37`
- Test: `weblate/trans/tests/test_judge_views.py`

**Interfaces:**
- Consumes: `ProducerRun` из Task 1.
- Produces: `recent_producer_runs(scope, *, user, limit=10) -> list[ProducerRun]`, `producer_run_modes(user, scope) -> tuple[str, ...]`, `user_can_view_producer_run(user, scope, run) -> bool`, view `producer_run(request, pk)`, шаблон `producer-run.html`, сниппет `producer-runs-menu.html`, контекстные ключи `producer_runs` и `producer_last_run`, константы `HISTORY_MODES`, `JUDGE_MODES`, `MT_LAUNCH_MODES`. `judge_queue` остаётся именем существующего контекста, но получает `can_run_judge`: при одном `translation.auto` он может показать историю MT, не выдавая судейский CTA. Маршрут по-прежнему называется `judge-run`.

- [ ] **Step 1: Написать падающие тесты**

В `weblate/trans/tests/test_judge_views.py` заменить обращения к контекстному ключу `judge_runs` на `producer_runs`, а к `judge_last_run` — на `producer_last_run` (строки 1664, 1671, 1905, 1907, 2025, 2044 на момент написания плана; найти их `grep -n "judge_runs\|judge_last_run" weblate/trans/tests/test_judge_views.py`).

Добавить в класс `JudgeRunReportViewTest` (`weblate/trans/tests/test_judge_views.py:2197`), у которого уже есть хелперы `enable_review()` и `create_run(scope=None, *, status=..., scope_type=...)`:

```python
    def test_report_uses_the_producer_run_template(self) -> None:
        self.enable_review()
        run = self.create_run()
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertTemplateUsed(response, "producer-run.html")

    def test_translation_run_needs_only_the_launch_permission(self) -> None:
        """A plain MT run exposes its own scope and cost, never a verdict.

        The project has no review workflow here, so ``unit.review`` is
        denied outright (``weblate/auth/permissions.py`` refuses it when
        ``translation_review`` is off, even for a superuser). Requiring it
        would hide the launcher's own receipt.
        """
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        run = self.create_run(requested_mode="translate")
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertEqual(response.status_code, 200)

    def test_judge_run_still_needs_review(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        run = self.create_run(requested_mode="judge")
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertEqual(response.status_code, 404)

    def test_report_restores_category_and_project_language_scopes(self) -> None:
        self.enable_review()
        category = self.create_category(project=self.project)
        project_language = ProjectLanguage(self.project, self.translation.language)
        for scope, scope_type in (
            (category, ProducerRun.ScopeType.CATEGORY),
            (project_language, ProducerRun.ScopeType.PROJECT_LANGUAGE),
        ):
            with self.subTest(scope=scope):
                run = self.create_run(scope=scope, scope_type=scope_type)
                response = self.client.get(
                    reverse("judge-run", kwargs={"pk": run.pk})
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["scope"].pk, scope.pk)
```

Хелпер `create_run` расширить необязательным аргументом `requested_mode="judge"` и передавать его в `ProducerRun.objects.create(...)` вместо жёстко прошитой строки (`weblate/trans/tests/test_judge_views.py:2221`). Импортировать `ProjectLanguage` из `weblate.utils.stats`. Эти два scope-теста обязательны: `auto_translation` принимает Category и ProjectLanguage (`weblate/trans/views/edit.py:1710-1753`), а старый `_create_judge_run` их отвергает.

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k 'producer_run_template or translation_run_needs_only or restores_category'`
Expected: FAIL: шаблон ещё старый, а `ProducerRun.ScopeType` / `_get_scope` ещё не знают новые scope.

- [ ] **Step 3: Переименовать символы через LSP**

Три вызова инструмента `lsp`:

```json
{"action": "rename", "file": "weblate/trans/views/judge.py", "line": 396, "symbol": "recent_judge_runs", "new_name": "recent_producer_runs"}
```
```json
{"action": "rename", "file": "weblate/trans/views/judge.py", "line": 319, "symbol": "user_can_view_judge_run", "new_name": "user_can_view_producer_run"}
```
```json
{"action": "rename", "file": "weblate/trans/views/judge.py", "line": 431, "symbol": "judge_run", "new_name": "producer_run"}
```

После третьего — проверить, что в `weblate/urls.py` запись стала `weblate.trans.views.judge.producer_run` при неизменном `name="judge-run"`.

- [ ] **Step 4: Восстановить scope и сделать право просмотра зависимым от режима**

`weblate/trans/views/judge.py`: импортировать `Category` из `weblate.trans.models`, `Language` из `weblate.lang.models` и `ProjectLanguage` из `weblate.utils.stats`. В `_SCOPE_MODELS` добавить `ProducerRun.ScopeType.CATEGORY: Category`. Перед обычным поиском модели в `_get_scope` добавить отдельный путь для не-ORM ProjectLanguage:

```python
    if run.scope_type == ProducerRun.ScopeType.PROJECT_LANGUAGE:
        try:
            project_id, language_id = map(int, run.scope_id.split("-", 1))
            return ProjectLanguage(
                Project.objects.get(pk=project_id),
                Language.objects.get(pk=language_id),
            )
        except (Project.DoesNotExist, Language.DoesNotExist, ValueError) as error:
            raise Http404 from error
```

Нельзя заменять это родительским `Project`: пользователь может иметь `translation.auto` лишь на одном языке (`weblate/trans/tests/test_autotranslate.py:598-628`), и отчёт должен повторно проверяться в том же узком scope, в котором он был запущен.

Рядом с `HISTORY_MODES` добавить явные множества режимов. Не трактовать неизвестный будущий `requested_mode` как MT: это мог бы открыть судейский отчёт без `unit.review`.

```python
# Modes that contain verdict data and must retain the review gate.
JUDGE_MODES = ("judge", "recheck", "drain")
# Modes produced by AutoForm that contain only the launcher-visible receipt.
MT_LAUNCH_MODES = ("translate", "suggest", "fuzzy", "approved")


def producer_run_modes(user, scope) -> tuple[str, ...]:
    """Which of ``HISTORY_MODES`` this user may currently see for this scope."""
    if not user.has_perm("translation.auto", scope):
        return ()
    may_review = settings.JUDGE_ENABLED and user.has_perm("unit.review", scope)
    return tuple(
        mode
        for mode in HISTORY_MODES
        if mode in MT_LAUNCH_MODES or (may_review and mode in JUDGE_MODES)
    )


def user_can_view_producer_run(user, scope, run) -> bool:
    """Whether ``user`` currently (not at launch time) may view this run."""
    if not user.has_perm("translation.auto", scope):
        return False
    if run.requested_mode in MT_LAUNCH_MODES:
        return True
    if run.requested_mode in JUDGE_MODES:
        return settings.JUDGE_ENABLED and user.has_perm("unit.review", scope)
    return False
```

Тело `user_can_view_producer_run` после `lsp rename` заменяется целиком на этот вариант; вызов во вьюхе (`weblate/trans/views/judge.py:436`) получает третий аргумент:

```python
    if not user_can_view_producer_run(request.user, scope, run):
        raise Http404
```

`recent_producer_runs` начинает фильтровать режимы по правам и требует пользователя:

```python
def recent_producer_runs(
    scope: Translation | Component | Project | Workspace,
    *,
    user,
    limit: int = 10,
) -> list[ProducerRun]:
```

В теле заменить фильтр на `requested_mode__in=producer_run_modes(user, scope)` и добавить ранний выход, чтобы страница без прав не платила за запрос:

```python
    modes = producer_run_modes(user, scope)
    if not modes:
        return []
```

Докстринг дополнить абзацем: судейские режимы попадают в список только при `JUDGE_ENABLED` и `unit.review`, а MT-режимы — при `translation.auto`. Категорийные и project-language прогоны всегда открываются по `report_url` завершившейся задачи: у существующих scope-меню нет точного SQL-предиката для их составного покрытия, и добавление ещё одной истории/витрины намеренно вне этого чека.

- [ ] **Step 5: Переименовать шаблоны, контекст и открыть MT-историю без review**

```bash
git mv weblate/templates/judge-run.html weblate/templates/producer-run.html
git mv weblate/templates/snippets/judge-runs-menu.html weblate/templates/snippets/producer-runs-menu.html
```

`weblate/trans/views/judge.py`: в `render(...)` заменить `"judge-run.html"` на `"producer-run.html"`.

`weblate/templates/producer-run.html:9`: заменить крошку на режимную подпись:

```html
  <li class="breadcrumb-item">{{ run_kind_label }}</li>
```

`weblate/trans/models/judge.py`: рядом с классом `ProducerRun` добавить подписи режимов. Они живут в модуле модели, а не вьюхи, потому что их читает и метод модели из Task 9; импорт вьюхи из модели закрыл бы цикл.

```python
#: Human label for a run's launch mode, shown as the report's breadcrumb and
#: in the run-history menu. Mirrors AutoForm's own mode labels so the report
#: names the run the same way the form that launched it did.
RUN_KIND_LABELS: dict[str, StrOrPromise] = {
    "judge": gettext_lazy("Judge run"),
    "recheck": gettext_lazy("Judge re-check"),
    "drain": gettext_lazy("Deferred judge retry"),
    "translate": gettext_lazy("Automatic translation run"),
    "suggest": gettext_lazy("Automatic suggestion run"),
    "fuzzy": gettext_lazy("Automatic translation run"),
    "approved": gettext_lazy("Automatic translation run"),
}
```

`StrOrPromise` импортируется под `TYPE_CHECKING` из `django_stubs_ext`, как в `weblate/machinery/llm.py:122-123`.

`weblate/trans/views/judge.py`: импортировать `RUN_KIND_LABELS` из `weblate.trans.models.judge` и добавить в контекст `render(...)`:

```python
            "run_kind_label": RUN_KIND_LABELS.get(
                run.requested_mode, gettext("Producer run")
            ),
```

`weblate/trans/views/basic.py`: переименовать локальные переменные и контекстные ключи `judge_runs` → `producer_runs`, `judge_last_run` → `producer_last_run`, поправить импорт на строке 82 и передать пользователя в оба вызова. Вызов на строке 828 становится `recent_producer_runs(obj, user=user)`.

В `judge_queue_strip_context` не оставлять старний ранний выход `if not can_run: return None`: он требует `unit.review`, поэтому до этой правки скрывает историю запущенного MT от самого пользователя. Разделить права:

```python
    if not user.has_perm("translation.auto", obj):
        return None
    can_run_judge = judge_configuration_ready() and user.has_perm(
        "unit.review", obj
    )
    runs = recent_producer_runs(obj, user=user)
    if not can_run_judge and not runs:
        return None
```

Считать `counts`, `hand_off_ready`, `breakdown_url` и `run_url` только при `can_run_judge`; добавить `"can_run_judge": can_run_judge` в возвращаемый словарь. В `judge-readiness.html` показывать count/ready/download, :guilabel:`Breakdown by check` и :guilabel:`Run the judge` только внутри `{% if judge_queue.can_run_judge %}`. При `False` и непустом `runs` карточка остаётся как история MT: заголовок `{% translate "Automatic translation runs" %}` и только `producer-runs-menu.html`. Так permission `translation.auto` даёт путь к своему чеку, но не раскрывает ни вердикт, ни судейские кнопки.

В `JudgeQueueStripViewTest` добавить сценарий с ролью ровно `translation.auto`, `translation_review=False` и сохранённым `requested_mode="translate"`: компонентная страница показывает URL отчёта и текст `Automatic translation runs`, но не `Run the judge`; direct URL этого MT-прогона возвращает 200. Роль создать по существующему паттерну `JudgeProducerTriageViewTest.grant` (строки 2799-2816): `Role` с `Permission.objects.get(codename="translation.auto")`, `Group(..., project_selection=SELECTION_ALL, language_selection=SELECTION_ALL)`, затем `self.user.clear_permissions_cache()`.

`weblate/templates/snippets/autoform.html:57-60`:

```html
      {% if producer_runs %}
        {% include "snippets/producer-runs-menu.html" with runs=producer_runs current_path=request.path %}
      {% endif %}
```

Проверить только переименованные surface-символы: `grep -rn "judge-runs-menu\|judge_runs\|judge_last_run\|judge-run.html" weblate/`. Допустимы устойчивые публичные/судейские имена: маршрут `judge-run`, классы `JudgeRunUnit`, метаданные кандидата `judge_run_id` и исторические миграции.

- [ ] **Step 6: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add weblate/trans/views/judge.py weblate/trans/views/basic.py weblate/trans/models/judge.py weblate/templates/producer-run.html weblate/templates/snippets/producer-runs-menu.html weblate/templates/snippets/judge-readiness.html weblate/templates/snippets/autoform.html weblate/urls.py weblate/trans/tests/test_judge_views.py
git commit -m "refactor(trans): name the run report after the producer launch, not the judge"
```

---

### Task 3: FK `LLMUsageLog.run`

**Files:**
- Modify: `weblate/trans/models/llm_usage.py:90-120`
- Create: `weblate/trans/migrations/0122_llm_usage_run.py`
- Test: `weblate/trans/tests/test_llm_usage.py`

**Interfaces:**
- Consumes: `ProducerRun` из Task 1.
- Produces: поле `LLMUsageLog.run` (nullable FK на `trans.ProducerRun`, `on_delete=SET_NULL`, `related_name="usage_logs"`) и индекс `llm_usage_run_recent_idx` по `("run", "-created_at")`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `weblate/trans/tests/test_llm_usage.py`:

```python
class LLMUsageRunLinkTest(TestCase):
    def _run(self) -> ProducerRun:
        return ProducerRun.objects.create(
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id="1",
            scope_label="Test",
            scope_path="/projects/test/test/",
            requested_mode="translate",
            cap=100,
        )

    def test_usage_row_links_to_a_run(self) -> None:
        run = self._run()
        log = LLMUsageLog.objects.create(model="m1", prompt_tokens=1, run=run)
        self.assertEqual([item.pk for item in run.usage_logs.all()], [log.pk])

    def test_deleting_a_run_keeps_the_financial_row(self) -> None:
        run = self._run()
        log = LLMUsageLog.objects.create(
            model="m1", prompt_tokens=1, cost_usd=Decimal("0.5"), run=run
        )
        run.delete()
        log.refresh_from_db()
        self.assertIsNone(log.run_id)
        self.assertEqual(log.cost_usd, Decimal("0.5"))
```

Импорт `ProducerRun` добавить к существующему импорту моделей в шапке файла.

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py -k LLMUsageRunLinkTest`
Expected: FAIL с `TypeError: LLMUsageLog() got unexpected keyword arguments: 'run'`

- [ ] **Step 3: Добавить поле**

`weblate/trans/models/llm_usage.py`, сразу после поля `request_attempt` (строка 96):

```python
    #: The producer launch this request belongs to. ``SET_NULL`` mirrors
    #: ``request_attempt``: deleting a run must never delete or rewrite a
    #: financial row. ``None`` means the request was made outside any run,
    #: for instance an interactive suggestion in the editor.
    run = models.ForeignKey(
        "trans.ProducerRun",
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_logs",
    )
```

В `Meta.indexes` добавить, после `llm_usage_attempt_recent_idx`:

```python
            models.Index(
                fields=["run", "-created_at"],
                name="llm_usage_run_recent_idx",
            ),
```

- [ ] **Step 4: Сгенерировать миграцию**

Run: `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations trans --name llm_usage_run`
Expected: создан `weblate/trans/migrations/0122_llm_usage_run.py` с `AddField` для `run` и `AddIndex` для `llm_usage_run_recent_idx`, зависимость — `0121_producer_run`. Дописать в шапку файла лицензионный заголовок и `from __future__ import annotations`, как в соседних миграциях.

- [ ] **Step 5: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add weblate/trans/models/llm_usage.py weblate/trans/migrations/0122_llm_usage_run.py weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): link each LLM usage row to the producer run that paid for it"
```

---

### Task 4: Атрибуция расхода к прогону

**Files:**
- Modify: `weblate/machinery/base.py:107-123`
- Modify: `weblate/machinery/openai.py:178-196`
- Modify: `weblate/trans/judge.py:1482-1502`
- Modify: `weblate/trans/autotranslate.py:589-597`
- Modify: `weblate/trans/judge_loop.py:170-190,1468`
- Test: `weblate/trans/tests/test_llm_usage.py`, `weblate/machinery/tests.py`, `weblate/trans/tests/test_judge_client.py`

**Interfaces:**
- Consumes: `LLMUsageLog.run` из Task 3.
- Produces: атрибут `BatchMachineTranslation.usage_run_id: str | None`, читаемый в `BaseOpenAITranslation._write_llm_usage`; параметр `repair_targets(units, user, *, run_id=None)`. Судейские строки берут прогон из `request_attempt.run_id`.

Почему атрибут сервиса, а не `ContextVar`: батчи машинерии выполняются в пуле потоков (`weblate/trans/machinery.py:199-225`), а `ThreadPoolExecutor` не копирует контекст вызывающего. Существующие `llm_batch_*` работают потому, что их устанавливают **внутри** рабочего потока. Идентификатор прогона известен снаружи, поэтому он живёт на экземпляре сервиса, который потоки только читают.

- [ ] **Step 1: Написать падающий тест**

Добавить в `weblate/trans/tests/test_llm_usage.py`:

```python
class LLMUsageRunAttributionTest(TestCase):
    def test_service_attribute_defaults_to_none(self) -> None:
        self.assertIsNone(BatchMachineTranslation.usage_run_id)
```

И в `weblate/machinery/tests.py`, в класс, где уже проверяется запись `LLMUsageLog` (рядом с `test_usage_recorded`):

```python
    @http_mock.activate
    def test_usage_is_billed_to_the_run(self) -> None:
        run = ProducerRun.objects.create(
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id="1",
            scope_label="Test",
            scope_path="/projects/test/test/",
            requested_mode="translate",
            cap=100,
        )
        self.mock_response_priced()
        machine = self.get_machine()
        machine.usage_run_id = str(run.pk)
        self.assert_translate(
            self.SUPPORTED, self.SOURCE_TRANSLATED, self.EXPECTED_LEN, machine=machine
        )
        self.assertEqual(LLMUsageLog.objects.get().run_id, run.pk)
```

В `JudgeUsageLogTest` в `weblate/trans/tests/test_judge_client.py` добавить второй, независимый от MT, чек судейской проводки:

```python
    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_is_billed_to_the_judge_run(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "cost": 0.001,
        }
        http_mock.register("POST", CHAT_URL, json=payload)
        run = ProducerRun.objects.create(
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id="1",
            scope_label="Test",
            scope_path="/projects/test/test/",
            requested_mode="judge",
            cap=100,
        )
        request_verdicts(
            [REQ],
            model="vendor/model-a",
            run=run,
            persist_attempts=True,
        )
        self.assertEqual(
            LLMUsageLog.objects.get(model="vendor/model-a").run_id, run.pk
        )
```

Add `ProducerRun` to that file's model import. This guards the only path where MT's instance attribute is not involved.

`assert_translate` уже принимает keyword-only `machine` (`weblate/machinery/tests.py:457-492`), поэтому вызывать ровно этот хелпер. Импорты: в `test_llm_usage.py` добавить `BatchMachineTranslation` из `weblate.machinery.base` и `ProducerRun` из `weblate.trans.models`; в `weblate/machinery/tests.py` добавить `ProducerRun` к импортам моделей.

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py -k LLMUsageRunAttributionTest weblate/machinery/tests.py -k test_usage_is_billed_to_the_run weblate/trans/tests/test_judge_client.py -k usage_is_billed_to_the_judge_run`
Expected: FAIL: MT-путь ещё не знает `usage_run_id`, судейская строка ещё не получает `run_id`.

- [ ] **Step 3: Добавить атрибут сервиса**

`weblate/machinery/base.py`, в тело класса `BatchMachineTranslation` рядом с `accounting_key` (строка 122):

```python
    #: Producer run this service instance works for, as a string primary key,
    #: or ``None`` outside a run (an interactive suggestion in the editor).
    #: Set on the instance rather than in a ContextVar because batches run in
    #: a thread pool that does not copy the caller's context; worker threads
    #: only ever read it.
    usage_run_id: str | None = None
```

- [ ] **Step 4: Писать прогон в MT-строку**

`weblate/machinery/openai.py`, в вызов `LLMUsageLog.objects.create(...)` (строка 178), после `batch_size=batch_size,`:

```python
            run_id=self.usage_run_id,
```

- [ ] **Step 5: Писать прогон в судейскую строку**

`weblate/trans/judge.py`, в вызов `LLMUsageLog.objects.create(...)` (строка 1482), после `request_attempt=request_attempt,`:

```python
        run_id=request_attempt.run_id if request_attempt is not None else None,
```

- [ ] **Step 6: Прокинуть прогон в bulk-MT**

`weblate/trans/autotranslate.py`, в `fetch_mt` сразу после сортировки `engines` (после строки 597):

```python
        run_id = str(self.judge_run.pk) if self.judge_run is not None else None
        for engine in engines:
            engine.usage_run_id = run_id
```

Атрибут `self.judge_run` уже существует на `AutoTranslate` и заполняется из `BatchAutoTranslate._perform` (`weblate/trans/autotranslate.py:1420`). В Task 6 он начнёт быть непустым и для не-судейских режимов; здесь код уже готов к этому.

- [ ] **Step 7: Прокинуть прогон в починку**

`weblate/trans/judge_loop.py`, подпись `repair_targets` (строка 170):

```python
def repair_targets(
    units: list[Unit], user: User | None, *, run_id: str | None = None
) -> dict[int, list[str]]:
```

Сразу после `engine = MACHINERY[engine_id](setting)` (строка 187):

```python
    engine.usage_run_id = run_id
```

Вызов на строке 1468:

```python
        repairs = (
            repair_targets(repairable_units, user, run_id=run_id)
            if repairable_units
            else {}
        )
```

Вызов на строке 2297 (генерация кандидата из редактора) не меняется: у него нет прогона, и его расход остаётся вне прогонов — это правильно.

- [ ] **Step 8: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py -k LLMUsage weblate/machinery/tests.py -k test_usage_is_billed_to_the_run weblate/trans/tests/test_judge_client.py -k 'usage_is_billed_to_the_judge_run or usage_is_recorded_for_a_successful_batch' weblate/trans/tests/test_judge_loop.py`
Expected: PASS

- [ ] **Step 9: Коммит**

```bash
git add weblate/machinery/base.py weblate/machinery/openai.py weblate/trans/judge.py weblate/trans/autotranslate.py weblate/trans/judge_loop.py weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_judge_client.py weblate/machinery/tests.py
git commit -m "feat(trans): bill judge, pretranslation and repair requests to their run"
```

---

### Task 5: Агрегация расхода прогона

**Files:**
- Modify: `weblate/trans/models/llm_usage.py:125-153`
- Test: `weblate/trans/tests/test_llm_usage.py`

**Interfaces:**
- Consumes: `LLMUsageLog.run` из Task 3.
- Produces:

```python
@dataclass(frozen=True)
class RunSpend:
    requests: int
    unusable_requests: int
    strings_sent: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    cost_usd: Decimal
    unpriced_requests: int


def run_spend(run_id, operation: str) -> RunSpend
```

`cost_usd` — сумма только известных цен; `unpriced_requests` — сколько запросов цену не сообщили. При `unpriced_requests > 0` ноль в `cost_usd` не означает бесплатный прогон: Task 7 показывает неполноту и не строит среднюю цену строки.

- [ ] **Step 1: Написать падающий тест**

Добавить в `weblate/trans/tests/test_llm_usage.py`:

```python
class RunSpendTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run = ProducerRun.objects.create(
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id="1",
            scope_label="Test",
            scope_path="/projects/test/test/",
            requested_mode="translate",
            cap=100,
        )

    def _row(self, **kwargs) -> LLMUsageLog:
        defaults = {
            "model": "m1",
            "run": self.run,
            "operation": LLMUsageLog.Operation.TRANSLATION,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "batch_size": 4,
        }
        return LLMUsageLog.objects.create(**{**defaults, **kwargs})

    def test_empty_run_reports_zeroes(self) -> None:
        spend = run_spend(self.run.pk, LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(spend.requests, 0)
        self.assertEqual(spend.cost_usd, Decimal(0))
        self.assertEqual(spend.unpriced_requests, 0)

    def test_sums_tokens_strings_and_known_cost(self) -> None:
        self._row(cost_usd=Decimal("0.01"))
        self._row(cost_usd=Decimal("0.02"), batch_size=6, reasoning_tokens=3)
        spend = run_spend(self.run.pk, LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(spend.requests, 2)
        self.assertEqual(spend.strings_sent, 10)
        self.assertEqual(spend.prompt_tokens, 20)
        self.assertEqual(spend.completion_tokens, 10)
        self.assertEqual(spend.reasoning_tokens, 3)
        self.assertEqual(spend.cost_usd, Decimal("0.03"))
        self.assertEqual(spend.unpriced_requests, 0)

    def test_unpriced_rows_are_counted_not_summed_as_zero(self) -> None:
        self._row(cost_usd=Decimal("0.01"))
        self._row(cost_usd=None)
        spend = run_spend(self.run.pk, LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(spend.cost_usd, Decimal("0.01"))
        self.assertEqual(spend.unpriced_requests, 1)

    def test_counts_requests_whose_reply_was_not_fully_applied(self) -> None:
        self._row(outcome=LLMUsageLog.Outcome.APPLIED)
        self._row(outcome=LLMUsageLog.Outcome.PARTIAL)
        self._row(outcome=LLMUsageLog.Outcome.REFUSED)
        spend = run_spend(self.run.pk, LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(spend.unusable_requests, 2)

    def test_operations_never_blend(self) -> None:
        self._row(cost_usd=Decimal("0.01"))
        self._row(
            cost_usd=Decimal("5"),
            operation=LLMUsageLog.Operation.JUDGE,
        )
        translation = run_spend(self.run.pk, LLMUsageLog.Operation.TRANSLATION)
        judge = run_spend(self.run.pk, LLMUsageLog.Operation.JUDGE)
        self.assertEqual(translation.cost_usd, Decimal("0.01"))
        self.assertEqual(judge.cost_usd, Decimal("5"))

    def test_another_runs_rows_are_excluded(self) -> None:
        other = ProducerRun.objects.create(
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id="2",
            scope_label="Other",
            scope_path="/projects/test/other/",
            requested_mode="translate",
            cap=100,
        )
        self._row(cost_usd=Decimal("0.01"))
        self._row(cost_usd=Decimal("9"), run=other)
        spend = run_spend(self.run.pk, LLMUsageLog.Operation.TRANSLATION)
        self.assertEqual(spend.cost_usd, Decimal("0.01"))
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py -k RunSpendTest`
Expected: FAIL с `ImportError: cannot import name 'run_spend'`

- [ ] **Step 3: Реализовать агрегацию**

`weblate/trans/models/llm_usage.py`. В шапку добавить импорты:

```python
from dataclasses import dataclass

from django.db.models import Count, Q, Sum
```

После класса `LLMUsageLog` (перед `recent_cost_range`):

```python
@dataclass(frozen=True)
class RunSpend:
    """
    What one producer run spent on one operation.

    ``cost_usd`` sums only the requests whose price the provider reported.
    ``unpriced_requests`` counts the rest: a run with a zero cost and a
    non-zero unpriced count did not run for free, its price is unknown, and
    both numbers must be shown together.
    """

    requests: int
    unusable_requests: int
    strings_sent: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    cost_usd: Decimal
    unpriced_requests: int


def run_spend(run_id, operation: str) -> RunSpend:
    """Aggregate one run's ledger rows for one operation in a single query."""
    totals = LLMUsageLog.objects.filter(run_id=run_id, operation=operation).aggregate(
        requests=Count("id"),
        unusable_requests=Count(
            "id",
            filter=Q(
                outcome__in=(LLMUsageLog.Outcome.PARTIAL, LLMUsageLog.Outcome.REFUSED)
            ),
        ),
        strings_sent=Sum("batch_size"),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        reasoning_tokens=Sum("reasoning_tokens"),
        cached_tokens=Sum("cached_tokens"),
        cost_usd=Sum("cost_usd"),
        unpriced_requests=Count("id", filter=Q(cost_usd__isnull=True)),
    )
    return RunSpend(
        requests=totals["requests"] or 0,
        unusable_requests=totals["unusable_requests"] or 0,
        strings_sent=totals["strings_sent"] or 0,
        prompt_tokens=totals["prompt_tokens"] or 0,
        completion_tokens=totals["completion_tokens"] or 0,
        reasoning_tokens=totals["reasoning_tokens"] or 0,
        cached_tokens=totals["cached_tokens"] or 0,
        cost_usd=totals["cost_usd"] or Decimal(0),
        unpriced_requests=totals["unpriced_requests"] or 0,
    )
```

- [ ] **Step 4: Запустить тест**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py -k RunSpendTest`
Expected: 6 passed

- [ ] **Step 5: Коммит**

```bash
git add weblate/trans/models/llm_usage.py weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): aggregate one run's LLM spend per operation"
```

---

### Task 6: Прогон создаётся для запуска машинного перевода

**Files:**
- Modify: `weblate/trans/autotranslate.py:1193-1280,1313-1390,1407-1424`
- Test: `weblate/trans/tests/test_autotranslate.py`, `weblate/trans/tests/test_judge_autotranslate.py`

**Interfaces:**
- Consumes: `ProducerRun` из Task 1, `usage_run_id`-проводку из Task 4.
- Produces: `BatchAutoTranslate.active_producer_run: ProducerRun | None` — непустой для судейского запуска и для любого запуска с `auto_source="mt"` на **всех** шести поддержанных scope; `None` для запуска из памяти переводов; `AutoTranslate.producer_run` вместо `judge_run`; `ProducerRun.requested_mode` равен режиму `AutoForm`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `weblate/trans/tests/test_autotranslate.py`:

```python
class ProducerRunCreationTest(ViewTestCase):
    def _perform(
        self,
        mode: str,
        *,
        scope=None,
        auto_source: str = "mt",
    ) -> BatchAutoTranslate:
        auto = BatchAutoTranslate(
            self.component if scope is None else scope,
            user=self.user,
            q="",
            mode=mode,
            component_wide=True,
        )
        auto.perform(
            auto_source=auto_source,
            engines=["weblate"],
            threshold=80,
            source_component_ids=None,
        )
        return auto

    def test_machine_translation_launch_records_a_run(self) -> None:
        auto = self._perform("translate")
        run = auto.active_producer_run
        self.assertIsNotNone(run)
        run.refresh_from_db()
        self.assertEqual(run.requested_mode, "translate")
        self.assertEqual(run.actor, self.user)
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertIsNotNone(run.finished)

    def test_suggest_launch_records_its_own_mode(self) -> None:
        auto = self._perform("suggest")
        self.assertEqual(auto.active_producer_run.requested_mode, "suggest")

    def test_category_and_project_language_launches_record_their_own_scopes(
        self,
    ) -> None:
        category = self.create_category(project=self.component.project)
        project_language = ProjectLanguage(
            self.component.project, self.get_translation().language
        )
        for scope, scope_type in (
            (category, ProducerRun.ScopeType.CATEGORY),
            (project_language, ProducerRun.ScopeType.PROJECT_LANGUAGE),
        ):
            with self.subTest(scope=scope):
                run = self._perform("translate", scope=scope).active_producer_run
                self.assertIsNotNone(run)
                self.assertEqual(run.scope_type, scope_type)
                self.assertEqual(run.scope_id, str(scope.pk))

    def test_translation_memory_launch_records_no_run(self) -> None:
        """A launch that asks no model has no cost, so it gets no receipt."""
        auto = self._perform("translate", auto_source="others")
        self.assertIsNone(auto.active_producer_run)
        self.assertFalse(ProducerRun.objects.exists())

    def test_exception_marks_the_launch_failed(self) -> None:
        auto = BatchAutoTranslate(
            self.component,
            user=self.user,
            q="",
            mode="translate",
            component_wide=True,
        )
        with (
            mock.patch.object(
                BatchAutoTranslate, "_finish_translation", side_effect=ValueError("boom")
            ),
            self.assertRaises(ValueError),
        ):
            auto.perform(
                auto_source="mt",
                engines=["weblate"],
                threshold=80,
                source_component_ids=None,
            )
        run = auto.active_producer_run
        self.assertIsNotNone(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertEqual(run.failure, "boom")

    def test_swallowed_mt_failure_finalizes_the_run_as_failed(self) -> None:
        """AutoTranslate records expected provider errors instead of raising."""

        def fail(auto_translate, **_kwargs) -> str:
            auto_translate.failure_message = "provider unavailable"
            return auto_translate.failure_message

        with mock.patch.object(
            AutoTranslate, "perform", autospec=True, side_effect=fail
        ):
            auto = self._perform("translate")
        run = auto.active_producer_run
        self.assertIsNotNone(run)
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertEqual(run.failure, "provider unavailable")
```

Тесты офлайновые: `"weblate"` не настроен в машинерии тестового проекта, поэтому `fetch_mt` отфильтрует его и ни одного HTTP-запроса не сделает. Прогон при этом создаётся, потому что условие — `auto_source`, а не наличие пригодного движка. Последний тест моделирует путь `AutoTranslate.perform()`: он глотает `MachineTranslationError`, записывает `failure_message` и возвращает его (`weblate/trans/autotranslate.py:1006-1010`); внешний `ValueError` его не покрывает.

Импорты: `from unittest import mock`, `AutoTranslate`, `BatchAutoTranslate` из `weblate.trans.autotranslate`, `ProducerRun` из `weblate.trans.models`, `ProjectLanguage` из `weblate.utils.stats`, `ViewTestCase` из `weblate.trans.tests.test_views`.

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py -k ProducerRunCreationTest`
Expected: FAIL с `AttributeError: 'BatchAutoTranslate' object has no attribute 'active_producer_run'`

- [ ] **Step 3: Переименовать судейские атрибуты прогона через LSP**

```json
{"action": "rename", "file": "weblate/trans/autotranslate.py", "line": 1056, "symbol": "active_judge_run", "new_name": "active_producer_run"}
```

Затем переименовать конструкторный параметр и атрибут `judge_run` у `AutoTranslate` в `producer_run`: найти его объявление (`grep -n "judge_run" weblate/trans/autotranslate.py`) и выполнить `lsp rename` на объявлении. После этого поправить строку из Task 4, Step 6, на `self.producer_run`.

- [ ] **Step 4: Создавать прогон для платного запуска**

`weblate/trans/autotranslate.py`, `_create_judge_run` (строка 1248): переименовать в `_create_producer_run` через `lsp rename`. В начале его `match scope` покрыть каждый тип, принимаемый конструктором:

```python
        match scope:
            case Translation():
                scope_type = ProducerRun.ScopeType.TRANSLATION
            case Component():
                scope_type = ProducerRun.ScopeType.COMPONENT
            case Category():
                scope_type = ProducerRun.ScopeType.CATEGORY
            case Project():
                scope_type = ProducerRun.ScopeType.PROJECT
            case ProjectLanguage():
                scope_type = ProducerRun.ScopeType.PROJECT_LANGUAGE
            case Workspace():
                scope_type = ProducerRun.ScopeType.WORKSPACE
            case _:
                msg = (
                    "A producer run requires a translation, component, category, "
                    "project, project language, or workspace"
                )
                raise ValueError(msg)
```

Заменить конец метода так, чтобы снимок конфигурации судьи писался только для судейского режима:

```python
        return ProducerRun.objects.create(
            actor=self.user,
            task_id=task_id,
            started=timezone.now(),
            scope_type=scope_type,
            scope_id=str(scope.pk),
            scope_label=str(scope),
            scope_path=scope.get_absolute_url(),
            requested_query=self.q,
            requested_mode=self.mode,
            cap=settings.JUDGE_MAX_UNITS_PER_RUN,
            status=ProducerRun.Status.RUNNING,
            configuration_snapshot=(
                judge_configuration_snapshot() if self.mode == "judge" else {}
            ),
        )
```

В `_perform` заменить создание прогона:

```python
        judge_preview = self.preview_judge_scope() if self.mode == "judge" else None
        if judge_preview is not None:
            producer_run = self._adopt_judge_run()
        elif auto_source == "mt":
            # A run is the receipt for a launch that can cost money. A
            # translation-memory launch asks no model, so it gets no receipt
            # and does not compete for the ten rows of run history.
            producer_run = self._create_producer_run()
        else:
            producer_run = None
        self.active_producer_run = producer_run
```

Далее по функции заменить локальную `judge_run` на `producer_run` (строки 1386-1387, 1420, 1426-1442 и остальные вхождения в этой функции — найти через `grep -n "judge_run" weblate/trans/autotranslate.py`). Условия, которые относятся именно к судье, обязаны остаться привязанными к `self.mode == "judge"`, а не к наличию прогона: `_record_skipped_judge_units` вызывается только в судейском режиме.

- [ ] **Step 5: Финализировать прогон в любом режиме**

`weblate/trans/autotranslate.py`, `_finish_judge_run` (строка 1313): переименовать в `_finish_producer_run` через `lsp rename`. В `perform` заменить телом:

```python
    def perform(
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
    ) -> str:
        self.active_producer_run = None
        try:
            message = self._perform(
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                source_component_ids=source_component_ids,
            )
        except Exception as error:
            if self.active_producer_run is not None:
                self._finish_producer_run(
                    self.active_producer_run, ProducerRun.Status.FAILED, str(error)
                )
            raise
        if self.active_producer_run is not None:
            # AutoTranslate turns expected provider failures into
            # ``failure_message`` and returns normally. A receipt must expose
            # that outcome rather than incorrectly claim completion.
            status = (
                ProducerRun.Status.FAILED
                if self.failure_message
                else ProducerRun.Status.COMPLETED
            )
            self._finish_producer_run(
                self.active_producer_run, status, self.failure_message or ""
            )
        return message
```

Если `_perform` уже завершает судейский прогон сам, `_finish_producer_run` не перезапишет его: защита от повторной финализации стоит в самой функции (`weblate/trans/autotranslate.py:1322-1323`).

- [ ] **Step 6: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py -k ProducerRunCreationTest weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_views.py`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add weblate/trans/autotranslate.py weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_autotranslate.py
git commit -m "feat(trans): record a producer run for every paid automatic translation launch"
```
---

### Task 7: Карточка расхода на отчёте прогона

**Files:**
- Modify: `weblate/trans/views/judge.py:519-544`
- Modify: `weblate/templates/producer-run.html:32-34`
- Test: `weblate/trans/tests/test_judge_views.py`

**Interfaces:**
- Consumes: `run_spend` из Task 5, `producer-run.html` из Task 2.
- Produces: контекстные ключи `translation_spend` и `judge_spend` (оба `RunSpend`), `spend_visible` (bool) и `cost_per_written` (`Decimal | None`).

- [ ] **Step 1: Написать падающий тест**

Добавить в `JudgeRunReportViewTest` (`weblate/trans/tests/test_judge_views.py:2197`); `LLMUsageLog` и `Decimal` дописать в импорты файла:

```python
    def test_report_shows_the_runs_cost(self) -> None:
        self.enable_review()
        run = self.create_run()
        LLMUsageLog.objects.create(
            model="m1",
            run=run,
            operation=LLMUsageLog.Operation.TRANSLATION,
            prompt_tokens=10,
            completion_tokens=5,
            batch_size=4,
            cost_usd=Decimal("0.25"),
        )
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertEqual(response.context["translation_spend"].requests, 1)
        self.assertEqual(response.context["translation_spend"].cost_usd, Decimal("0.25"))
        self.assertContains(response, "0.25")

    def test_report_names_unpriced_requests_and_hides_the_average(self) -> None:
        self.enable_review()
        run = self.create_run()
        run.summary = {"written": 1}
        run.save(update_fields=["summary"])
        LLMUsageLog.objects.create(
            model="m1",
            run=run,
            operation=LLMUsageLog.Operation.JUDGE,
            prompt_tokens=10,
            completion_tokens=5,
            batch_size=1,
            cost_usd=None,
        )
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertEqual(response.context["judge_spend"].unpriced_requests, 1)
        self.assertIsNone(response.context["cost_per_written"])
        self.assertContains(response, "did not report a price")
        self.assertNotContains(response, "USD per written string")

    def test_report_keeps_a_zero_valued_complete_average(self) -> None:
        self.enable_review()
        run = self.create_run()
        run.summary = {"written": 2}
        run.save(update_fields=["summary"])
        LLMUsageLog.objects.create(
            model="m1",
            run=run,
            operation=LLMUsageLog.Operation.TRANSLATION,
            batch_size=2,
            cost_usd=Decimal(0),
        )
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertEqual(response.context["cost_per_written"], Decimal(0))
        self.assertContains(response, "0.000000 USD per written string")

    def test_report_omits_the_cost_card_without_any_request(self) -> None:
        self.enable_review()
        run = self.create_run()
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertFalse(response.context["spend_visible"])
        self.assertNotContains(response, "What it cost")
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k cost`
Expected: FAIL с `KeyError: 'translation_spend'`

- [ ] **Step 3: Считать расход во вьюхе**

`weblate/trans/views/judge.py`: добавить импорты `LLMUsageLog` и `run_spend` из `weblate.trans.models.llm_usage`. Перед `return render(...)` (строка 519):

```python
    translation_spend = run_spend(run.pk, LLMUsageLog.Operation.TRANSLATION)
    judge_spend = run_spend(run.pk, LLMUsageLog.Operation.JUDGE)
    # A per-written-string price is an exact figure only when *every* request
    # reported a price. Showing the known subtotal divided by written strings
    # while another request is unpriced would present a lower bound as a price.
    written = run.summary.get("written") or 0
    requests = translation_spend.requests + judge_spend.requests
    unpriced_requests = (
        translation_spend.unpriced_requests + judge_spend.unpriced_requests
    )
    cost_per_written = (
        (translation_spend.cost_usd + judge_spend.cost_usd) / written
        if written and requests and not unpriced_requests
        else None
    )
```

и в словарь контекста:

```python
            "translation_spend": translation_spend,
            "judge_spend": judge_spend,
            "spend_visible": bool(translation_spend.requests or judge_spend.requests),
            "cost_per_written": cost_per_written,
```

- [ ] **Step 4: Нарисовать карточку**

`weblate/templates/producer-run.html`, сразу после блока предупреждений (после строки 32, перед карточкой «What to do»):

```html
  {% if spend_visible %}
    <div class="card mb-3">
      <div class="card-header">
        <h4 class="card-title">{% translate "What it cost" %}</h4>
      </div>
      <table class="table mb-0">
        <thead>
          <tr>
            <th>{% translate "Operation" %}</th>
            <th>{% translate "Requests" %}</th>
            <th>{% translate "Strings sent" %}</th>
            <th>{% translate "Tokens" %}</th>
            <th>{% translate "USD" %}</th>
          </tr>
        </thead>
        <tbody>
          {% if translation_spend.requests %}
            <tr>
              <td>{% translate "Machine translation" %}</td>
              <td>
                {{ translation_spend.requests }}
                {% if translation_spend.unusable_requests %}
                  <span class="text-muted small">({% blocktranslate count counter=translation_spend.unusable_requests %}{{ counter }} reply not fully applied{% plural %}{{ counter }} replies not fully applied{% endblocktranslate %})</span>
                {% endif %}
              </td>
              <td>{{ translation_spend.strings_sent }}</td>
              <td>{{ translation_spend.prompt_tokens }} + {{ translation_spend.completion_tokens }}</td>
              <td>
                {{ translation_spend.cost_usd|floatformat:6 }}
                {% if translation_spend.unpriced_requests %}
                  <br>
                  <span class="text-muted small">{% blocktranslate count counter=translation_spend.unpriced_requests %}{{ counter }} request did not report a price{% plural %}{{ counter }} requests did not report a price{% endblocktranslate %}</span>
                {% endif %}
              </td>
            </tr>
          {% endif %}
          {% if judge_spend.requests %}
            <tr>
              <td>{% translate "LLM judge" %}</td>
              <td>
                {{ judge_spend.requests }}
                {% if judge_spend.unusable_requests %}
                  <span class="text-muted small">({% blocktranslate count counter=judge_spend.unusable_requests %}{{ counter }} reply not fully applied{% plural %}{{ counter }} replies not fully applied{% endblocktranslate %})</span>
                {% endif %}
              </td>
              <td>{{ judge_spend.strings_sent }}</td>
              <td>{{ judge_spend.prompt_tokens }} + {{ judge_spend.completion_tokens }}{% if judge_spend.reasoning_tokens %} ({% blocktranslate with reasoning=judge_spend.reasoning_tokens %}{{ reasoning }} reasoning{% endblocktranslate %}){% endif %}</td>
              <td>
                {{ judge_spend.cost_usd|floatformat:6 }}
                {% if judge_spend.unpriced_requests %}
                  <br>
                  <span class="text-muted small">{% blocktranslate count counter=judge_spend.unpriced_requests %}{{ counter }} request did not report a price{% plural %}{{ counter }} requests did not report a price{% endblocktranslate %}</span>
                {% endif %}
              </td>
            </tr>
          {% endif %}
        </tbody>
      </table>
      <div class="card-footer text-muted">
        {% if cost_per_written is not None %}
          {% blocktranslate with amount=cost_per_written|floatformat:6 %}{{ amount }} USD per written string.{% endblocktranslate %}
        {% endif %}
        {% translate "Prices are the provider's own; a request without a reported price is unknown, never free." %}
        {% with cached=translation_spend.cached_tokens|add:judge_spend.cached_tokens %}
          {% if cached %}
            {% blocktranslate %}{{ cached }} prompt tokens were served from the provider's own cache.{% endblocktranslate %}
          {% endif %}
        {% endwith %}
      </div>
    </div>
  {% endif %}
```

- [ ] **Step 5: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py`
Expected: PASS

- [ ] **Step 6: Коммит**

```bash
git add weblate/trans/views/judge.py weblate/templates/producer-run.html weblate/trans/tests/test_judge_views.py
git commit -m "feat(trans): show what a producer run cost on its own report"
```

---

### Task 8: MT-карточки отчёта и защита судейских

**Files:**
- Modify: `weblate/trans/autotranslate.py` (`_finish_producer_run`)
- Modify: `weblate/trans/views/judge.py:519-544`
- Modify: `weblate/templates/producer-run.html:34-72,109-180`
- Test: `weblate/trans/tests/test_judge_views.py`, `weblate/trans/tests/test_autotranslate.py`

**Interfaces:**
- Consumes: Task 6 (`_finish_producer_run`), Task 7 (карточка расхода).
- Produces: `ProducerRun.summary["written"]` (int) для не-судейских прогонов; контекстные ключи `is_judge_run` (bool) и `language_spend` (`list[dict]` с ключами `language`, `service`, `model`, `requests`, `strings_sent`, `cost_usd`, `unpriced_requests`).

- [ ] **Step 1: Написать падающие тесты**

В `weblate/trans/tests/test_autotranslate.py`, в `ProducerRunCreationTest`:

```python
    def test_run_summary_records_written_strings(self) -> None:
        auto = self._perform("translate")
        run = auto.active_producer_run
        run.refresh_from_db()
        self.assertEqual(run.summary["written"], auto.updated)
```

Добавить в `JudgeRunReportViewTest` (`weblate/trans/tests/test_judge_views.py:2197`):

```python
    def test_translation_run_report_hides_the_judge_triage_card(self) -> None:
        self.enable_review()
        run = self.create_run(requested_mode="translate")
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        self.assertFalse(response.context["is_judge_run"])
        self.assertNotContains(response, "What to do")
        self.assertContains(response, "What was written")

    def test_translation_run_report_breaks_spend_down_by_provider_language_model(
        self,
    ) -> None:
        self.enable_review()
        run = self.create_run(requested_mode="translate")
        for language_code, service, cost in (
            ("fr", "openrouter", "0.10"),
            ("ja", "litellm", "0.40"),
        ):
            LLMUsageLog.objects.create(
                model="vendor/shared-model",
                service=service,
                run=run,
                operation=LLMUsageLog.Operation.TRANSLATION,
                target_language_code=language_code,
                prompt_tokens=10,
                batch_size=5,
                cost_usd=Decimal(cost),
            )
        response = self.client.get(reverse("judge-run", kwargs={"pk": run.pk}))
        rows = response.context["language_spend"]
        self.assertEqual(
            [(row["language"], row["service"], row["model"], row["cost_usd"]) for row in rows],
            [
                ("ja", "litellm", "vendor/shared-model", Decimal("0.40")),
                ("fr", "openrouter", "vendor/shared-model", Decimal("0.10")),
            ],
        )
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k translation_run_report weblate/trans/tests/test_autotranslate.py -k written`
Expected: FAIL с `KeyError: 'is_judge_run'` и `KeyError: 'written'`

- [ ] **Step 3: Писать «записано» в сводку прогона**

`weblate/trans/autotranslate.py`, `_finish_producer_run`: заменить строку, формирующую `summary`, так, чтобы судейская сводка сохранялась, а число записанных строк добавлялось всегда:

```python
        summary = asdict(self.judge_summary or JudgeSummary())
        summary["written"] = self.updated
        run.summary = summary
```

- [ ] **Step 4: Считать разрез по языкам**

`weblate/trans/views/judge.py`, рядом с расчётом расхода из Task 7:

```python
    is_judge_run = run.requested_mode in JUDGE_MODES
    language_spend = [
        {
            "language": row["target_language_code"],
            "service": row["service"],
            "model": row["model"],
            "requests": row["requests"],
            "strings_sent": row["strings_sent"] or 0,
            # This is the known subtotal; the adjacent count makes an
            # unpriced row visibly incomplete rather than silently free.
            "cost_usd": row["cost_usd"] or Decimal(0),
            "unpriced_requests": row["unpriced_requests"],
        }
        for row in LLMUsageLog.objects.filter(
            run_id=run.pk, operation=LLMUsageLog.Operation.TRANSLATION
        )
        .values("target_language_code", "service", "model")
        .annotate(
            requests=Count("id"),
            strings_sent=Sum("batch_size"),
            cost_usd=Sum("cost_usd"),
            unpriced_requests=Count("id", filter=Q(cost_usd__isnull=True)),
        )
        .order_by("-cost_usd", "target_language_code", "service", "model")
    ]
```

Импортировать `Count`, `Q`, `Sum` из `django.db.models` и `Decimal` из `decimal`. Добавить в контекст `"is_judge_run": is_judge_run,` и `"language_spend": language_spend,`.

- [ ] **Step 5: Развести карточки по режиму**

`weblate/templates/producer-run.html`: обернуть судейскую карточку «What to do» (строки 34-72) и карточку «Strings» с вердиктами (строки 109-180) в `{% if is_judge_run %} ... {% endif %}`. Карточка «Where the problems are» уже защищена своим `{% if categories %}`.

Между ними добавить MT-карточки:

```html
  {% if not is_judge_run %}
    <div class="card mb-3">
      <div class="card-header">
        <h4 class="card-title">{% translate "What was written" %}</h4>
      </div>
      <div class="card-body">
        <p>
          {% blocktranslate count counter=run.summary.written|default:0 %}{{ counter }} string was written.{% plural %}{{ counter }} strings were written.{% endblocktranslate %}
        </p>
        {% if translation_spend.requests %}
          <p class="text-muted mb-0">
            {% blocktranslate with sent=translation_spend.strings_sent %}{{ sent }} strings were sent to the model. Other matching strings may have come from the machinery cache, been duplicate sources, or remained unchanged because the model returned no usable result.{% endblocktranslate %}
          </p>
        {% else %}
          <p class="text-muted mb-0">{% translate "No large language model was used by this run." %}</p>
        {% endif %}
        <p class="mt-2 mb-0">
          <a href="{{ scope_query_url }}">{% translate "Open current strings matching this run's filter" %}</a>
        </p>
      </div>
    </div>

    {% if language_spend %}
      <div class="card mb-3">
        <div class="card-header">
          <h4 class="card-title">{% translate "Cost by language, provider, and model" %}</h4>
        </div>
        <table class="table mb-0">
          <thead>
            <tr>
              <th>{% translate "Language" %}</th>
              <th>{% translate "Provider" %}</th>
              <th>{% translate "Model" %}</th>
              <th>{% translate "Requests" %}</th>
              <th>{% translate "Strings sent" %}</th>
              <th>{% translate "USD" %}</th>
            </tr>
          </thead>
          <tbody>
            {% for row in language_spend %}
              <tr>
                <td>{{ row.language|default:"-" }}</td>
                <td>{{ row.service|default:"-" }}</td>
                <td>{{ row.model }}</td>
                <td>{{ row.requests }}</td>
                <td>{{ row.strings_sent }}</td>
                <td>
                  {{ row.cost_usd|floatformat:6 }}
                  {% if row.unpriced_requests %}
                    <span class="text-muted small">({% translate "price unknown" %})</span>
                  {% endif %}
                </td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% endif %}
  {% endif %}
```

Во вьюху добавить ссылку на **текущий** результат сохранённого фильтра — это не список исторически записанных строк:

```python
    scope_query_url = (
        _review_url(scope, run.requested_query)
        if run.requested_query
        else scope.get_absolute_url()
    )
```

и передать её в контекст как `"scope_query_url": scope_query_url,`. `Change` не ссылается на прогон, поэтому обещать «строки, которые спросил этот прогон» было бы неправдой: фильтр мог дать иной набор после следующих изменений.

- [ ] **Step 6: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_autotranslate.py`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add weblate/trans/autotranslate.py weblate/trans/views/judge.py weblate/templates/producer-run.html weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(trans): report what an automatic translation run wrote and what each language cost"
```

---

### Task 9: Путь к отчёту после прогона

**Files:**
- Modify: `weblate/trans/tasks.py:1106-1115,1163-1187`
- Modify: `weblate/static/loader-bootstrap.js:1843-1850`
- Modify: `weblate/trans/views/judge.py:59`
- Test: `weblate/trans/tests/test_autotranslate.py`, `weblate/trans/tests/test_judge_views.py`

**Interfaces:**
- Consumes: Task 6 (`active_producer_run`), Task 2 (`recent_producer_runs`, `HISTORY_MODES`).
- Produces: `result["report_url"]` для любого прогона; кнопка в уведомлении о задаче с нейтральной подписью; MT-прогоны в меню истории.

- [ ] **Step 1: Написать падающие тесты**

В `weblate/trans/tests/test_autotranslate.py` **заменить** существующий `test_non_judge_task_result_has_no_report_url` (строки 940-953): он закрепляет уходящий контракт и после Task 6 обязан исчезнуть.

```python
    def test_non_judge_task_result_links_to_its_report(self) -> None:
        result = auto_translate(
            user_id=self.user.id,
            mode="translate",
            q="",
            auto_source="mt",
            source_component_id=None,
            engines=["weblate"],
            threshold=80,
            component_id=self.component.id,
            enforce_permissions=False,
        )
        run = ProducerRun.objects.get()
        self.assertEqual(
            result["report_url"], reverse("judge-run", kwargs={"pk": run.pk})
        )

    def test_component_mt_task_result_links_to_its_report(self) -> None:
        result = auto_translate_component(
            self.component.id,
            mode="translate",
            q="",
            auto_source="mt",
            engines=["weblate"],
            threshold=80,
            user_id=self.user.id,
            enforce_permissions=False,
        )
        run = ProducerRun.objects.get()
        self.assertEqual(
            result["report_url"], reverse("judge-run", kwargs={"pk": run.pk})
        )
```

`auto_translate` — keyword-only (`weblate/trans/tasks.py:989-1013`); `auto_translate_component` уже импортирован и вызывается существующим судейским тестом рядом со старым контрактом. `ProducerRun` приходит из импорта Task 1.

В `weblate/trans/tests/test_judge_views.py`, в `JudgeRunReportViewTest`:

```python
    def test_history_menu_lists_a_translation_run(self) -> None:
        self.enable_review()
        run = self.create_run(requested_mode="translate")
        self.assertIn(
            run.pk,
            [
                item.pk
                for item in recent_producer_runs(self.component, user=self.user)
            ],
        )
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py -k 'non_judge_task_result_links or component_mt_task_result_links' weblate/trans/tests/test_judge_views.py -k history_menu_lists_a_translation_run`
Expected: FAIL с `KeyError: 'report_url'` и `AssertionError: ... not found in []`

- [ ] **Step 3: Ставить `report_url` для любого прогона**

`weblate/trans/tasks.py`: в четырёх местах, где сейчас стоит `if auto.active_judge_run is not None:` (после переименования из Task 6 — `active_producer_run`), условие остаётся, а ключ формируется так же; менять нужно только строки 1106-1110 и 1178-1186, где раньше прогон существовал лишь в судейском режиме. Проверить, что все четыре блока читают `auto.active_producer_run` и все ставят:

```python
        if auto.active_producer_run is not None:
            result["report_url"] = reverse(
                "judge-run", kwargs={"pk": auto.active_producer_run.id}
            )
```

- [ ] **Step 4: Обобщить подпись кнопки**

`weblate/static/loader-bootstrap.js:1848`:

```javascript
              link.textContent = gettext("View run report");
```

- [ ] **Step 5: Пустить MT-прогоны в меню истории**

`weblate/trans/views/judge.py:59`:

```python
# The launch modes the per-scope run history lists. A producer launch from
# the automatic translation form is one of AutoForm's own modes; the two
# excluded modes ("recheck", "drain") are runs too, but neither is a launch
# a producer returns to a scope page to find. An allowlist rather than an
# exclusion list: a mode added later must opt into the menu explicitly
# instead of silently competing with real launches for its ten rows.
HISTORY_MODES = ("judge", "translate", "suggest", "fuzzy", "approved")
```

Обновить докстринг `recent_producer_runs` (строки 410-419): перечисление исключённых режимов остаётся верным, формулировка «Only ``HISTORY_MODES`` reaches the menu» тоже; убрать утверждение, что в меню попадают только судейские запуски.

В `weblate/templates/snippets/producer-runs-menu.html:33` добавить режим в строку выпадающего списка, чтобы MT и судья различались:

```html
              <span class="d-block">{{ run.created|date:"DATETIME_FORMAT" }} · {{ run.get_requested_mode_label }}{% if run.actor and run.actor != user %} · {{ run.actor.profile.get_user_name }}{% endif %}{% if run.status != "completed" %} · {{ run.get_status_display }}{% endif %}</span>
```

Для этого добавить в `weblate/trans/models/judge.py`, в класс `ProducerRun`, метод рядом с `__str__`. `RUN_KIND_LABELS` объявлён в этом же модуле в Task 2, поэтому импорта не нужно и цикла не возникает:

```python
    def get_requested_mode_label(self) -> str:
        """Short human label for the launch mode, for run-history rows."""
        return str(RUN_KIND_LABELS.get(self.requested_mode, self.requested_mode))
```

- [ ] **Step 6: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_views.py`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add weblate/trans/tasks.py weblate/static/loader-bootstrap.js weblate/trans/views/judge.py weblate/trans/models/judge.py weblate/templates/snippets/producer-runs-menu.html weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(trans): link every finished run to its report and list MT runs in the history"
```

---

### Task 10: Оценка стоимости MT в форме автоперевода

**Files:**
- Modify: `weblate/trans/autotranslate.py:79-87,1131-1167`
- Modify: `weblate/trans/views/edit.py:1608-1704`
- Modify: `weblate/static/loader-bootstrap.js:1330-1442`
- Modify: `weblate/templates/snippets/autoform.html:60`
- Test: `weblate/trans/tests/test_judge_views.py`, `weblate/trans/tests/test_autotranslate.py`

**Interfaces:**
- Consumes: `recent_cost_range` (существующий, `weblate/trans/models/llm_usage.py:126-153`).
- Produces: `BatchAutoTranslate.preview_mt_scope() -> MTScopePreview` для **не-судейского** запуска (поля `matched`, `writable`, `per_translation: list[tuple[Translation, int]]`); эндпоинт `auto_translation_preview` отвечает для любого режима. Для обычного MT `pretranslation_cost` — интервал: цена одной строки для языка равна **сумме** по всем выбранным движкам, а границы интервала — минимум и максимум этой суммы по языкам скоупа, умноженные на `writable`; при хотя бы одной неоценимой паре с ненулевым числом строк — `available: false`. Судейский preview и его cap/кандидатная арифметика остаются отдельной, существующей веткой. Элемент превью переименован в `id_auto_run_preview`.

- [ ] **Step 1: Написать падающие тесты**

Существующие тесты превью лежат в `JudgeAutoTranslateViewTest` (`weblate/trans/tests/test_judge_views.py:75`, запросы к `auto_translation_preview` на строках 129-133 и 192-215) — новые добавить туда же и переиспользовать их настройку проекта и машинерии.

```python
    def test_preview_answers_for_a_translate_run(self) -> None:
        response = self.client.get(
            reverse("auto_translation_preview", kwargs=self.kw_translation),
            {
                "mode": "translate",
                "q": "state:empty",
                "auto_source": "mt",
                "threshold": 80,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["judge_cost"]["available"])
        self.assertIn("pretranslation_cost", payload)

    def configure_routed_engines(self) -> None:
        """Two routed engines that resolve a model for every target language.

        ``"*"`` is the routing fallback key (``resolve_model`` in
        ``weblate_customization/src/weblate_customization/machinery.py:165-180``),
        so the fixture needs no knowledge of the component's language codes.
        """
        self.component.project.machinery_settings = {
            "openrouter": {"key": "test", "routing": {"*": "vendor/cheap"}},
            "litellm": {"key": "test", "routing": {"*": "vendor/dear"}},
        }
        self.component.project.save(update_fields=["machinery_settings"])

    def price_history(self, service: str, model: str, per_unit: str) -> None:
        """Five priced requests, the minimum ``recent_cost_range`` accepts."""
        for _ in range(5):
            LLMUsageLog.objects.create(
                model=model,
                service=service,
                project_id_snapshot=self.component.project_id,
                operation=LLMUsageLog.Operation.TRANSLATION,
                prompt_tokens=10,
                unit_count=2,
                batch_size=2,
                cost_usd=Decimal(per_unit) * 2,
            )

    def request_preview(self, engines: list[str], *, q: str = "state:empty") -> dict:
        response = self.client.get(
            reverse(
                "auto_translation_preview",
                kwargs={"path": self.component.get_url_path()},
            ),
            {
                "mode": "translate",
                "q": q,
                "auto_source": "mt",
                "threshold": 80,
                "engines": engines,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_preview_sums_every_selected_engine_for_a_string(self) -> None:
        """Both engines are asked for every unit, so their prices add up.

        ``fetch_machinery_matches`` iterates every selected service over
        every unit (``weblate/trans/machinery.py:199-225``), so a string
        priced at 0.001 by one engine and 0.005 by the other costs 0.006,
        never "somewhere between 0.001 and 0.005".
        """
        self.configure_routed_engines()
        self.price_history("openrouter", "vendor/cheap", "0.001")
        self.price_history("litellm", "vendor/dear", "0.005")
        payload = self.request_preview(["openrouter", "litellm"])
        cost = payload["pretranslation_cost"]
        self.assertTrue(cost["available"])
        writable = payload["writable"]
        self.assertGreater(writable, 0)
        # Routing is the "*" fallback for both engines, so every target
        # language resolves the same pair of models and the interval
        # collapses onto the exact per-string sum.
        self.assertEqual(Decimal(cost["min"]), Decimal("0.006") * writable)
        self.assertEqual(Decimal(cost["max"]), Decimal("0.006") * writable)

    def test_preview_covers_every_target_language_of_the_scope(self) -> None:
        self.configure_routed_engines()
        self.price_history("openrouter", "vendor/cheap", "0.001")
        payload = self.request_preview(["openrouter"])
        self.assertTrue(payload["pretranslation_cost"]["available"])
        writable = payload["writable"]
        expected = sum(
            translation.unit_set.exclude(state=STATE_READONLY)
            .search("state:empty", parser="unit")
            .count()
            for translation in self.component.translation_set.exclude(
                language=self.component.source_language
            )
        )
        self.assertEqual(writable, expected)
        self.assertEqual(
            Decimal(payload["pretranslation_cost"]["min"]),
            Decimal("0.001") * writable,
        )

    def test_preview_refuses_a_partial_price(self) -> None:
        """Pricing some pairs of a run and dropping the rest is a lie."""
        self.configure_routed_engines()
        self.price_history("openrouter", "vendor/cheap", "0.001")
        cost = self.request_preview(["openrouter", "litellm"])["pretranslation_cost"]
        self.assertFalse(cost["available"])

    def test_preview_is_unavailable_without_any_configured_engine(self) -> None:
        self.component.project.machinery_settings = {}
        self.component.project.save(update_fields=["machinery_settings"])
        cost = self.request_preview(["openrouter"])["pretranslation_cost"]
        self.assertFalse(cost["available"])

    def test_preview_counts_previously_translated_strings_when_asked(self) -> None:
        translation = self.get_translation()
        unit = translation.unit_set.exclude(state=STATE_READONLY).first()
        assert unit is not None
        unit.translate(self.user, ["Already translated"], STATE_TRANSLATED)
        self.configure_routed_engines()
        self.price_history("openrouter", "vendor/cheap", "0.001")
        payload = self.request_preview(["openrouter"], q=f"id:{unit.pk}")
        self.assertEqual(payload["matched"], 1)
        # Standard MT executes AutoTranslate.get_units() directly. Unlike a
        # judge pretranslation, it does not discard an existing target merely
        # because overwrite_existing is false.
        self.assertEqual(payload["writable"], 1)

    def test_preview_does_not_require_price_history_for_zero_row_language(self) -> None:
        translation = self.get_translation()
        unit = translation.unit_set.exclude(state=STATE_READONLY).first()
        assert unit is not None
        self.component.project.machinery_settings = {
            "openrouter": {
                "key": "test",
                "routing": {translation.language.code: "vendor/cheap"},
            }
        }
        self.component.project.save(update_fields=["machinery_settings"])
        self.price_history("openrouter", "vendor/cheap", "0.001")
        cost = self.request_preview(["openrouter"], q=f"id:{unit.pk}")[
            "pretranslation_cost"
        ]
        self.assertTrue(cost["available"])
```

`self.kw_translation` — существующий атрибут `ViewTestCase`, которым пользуются тесты превью на строках 193 и 249. `Decimal`, `LLMUsageLog`, `STATE_READONLY` и `STATE_TRANSLATED` дописать в импорты файла. Настройка машинерии повторяет уже принятый в репозитории приём (`weblate/trans/tests/test_judge_views.py:3132`, `weblate/trans/tests/test_tasks.py:579-583`).

В `JudgeProducerTriageViewTest`, у которого уже есть `grant(["translation.auto"])` и роль без review (строки 2799-2816), добавить регрессию permission-gate:

```python
    def test_mt_preview_needs_only_automatic_translation_permission(self) -> None:
        self.component.project.translation_review = False
        self.component.project.save(update_fields=["translation_review"])
        self.grant(["translation.auto"])
        response = self.client.get(
            reverse("auto_translation_preview", kwargs=self.kw_translation),
            {
                "mode": "translate",
                "q": "state:empty",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["judge_cost"]["available"])
```

This is the end-to-end permission contract: the same user can launch an MT run and inspect its forecast even where the project deliberately has no review workflow.

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k preview`
Expected: FAIL — первый тест получает 400, остальные падают на отсутствии `preview_mt_scope`

- [ ] **Step 3: Добавить судья-независимый обзор скоупа**

`preview_judge_scope` для этого не годится: она вызывает `validate_judge_configuration()` и применяет судейский лимит `JUDGE_MAX_UNITS_PER_RUN` (`weblate/trans/autotranslate.py:1132-1136`), а к запуску, который просит только движок перевода, не относится ни то, ни другое.

`weblate/trans/autotranslate.py`, рядом с `JudgeScopePreview` (строка 79):

```python
@dataclass(frozen=True, slots=True)
class MTScopePreview:
    matched: int
    writable: int
    #: Per target translation, so a per-language price can be applied to the
    #: strings of that language and to no others.
    per_translation: list[tuple[Translation, int]]
```

и метод в `BatchAutoTranslate` рядом с `preview_judge_scope` (строка 1132):

```python
    def preview_mt_scope(self) -> MTScopePreview:
        """Actual non-judge MT eligibility per target translation."""
        rows: list[tuple[Translation, int]] = []
        matched = 0
        for translation in self.translations:
            if not self._can_process_translation(translation):
                continue
            units = AutoTranslate(
                translation=translation,
                user=self.user,
                q=self.q,
                mode=self.mode,
                component_wide=self.component_wide,
                unit_ids=self.unit_ids,
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                overwrite_existing=self.overwrite_existing,
            ).get_units()
            count = units.count()
            matched += count
            # Ordinary MT calls get_units() without the judge-only
            # ``not unit.translated`` filter. A zero-row translation makes no
            # request and therefore must not demand a price history.
            if count:
                rows.append((translation, count))
        return MTScopePreview(
            matched=matched,
            writable=matched,
            per_translation=rows,
        )
```

Один `COUNT` на язык. `writable == matched` намеренно: в не-судейском режиме именно все строки `AutoTranslate.get_units()` пойдут в `fetch_mt`; семантика judge-only `overwrite_existing` сюда не переносится.

- [ ] **Step 4: Отвязать эндпоинт от судейского режима**

`weblate/trans/views/edit.py:1626`:

```python
    autoform = AutoForm(form_obj, request.user, request.GET)
    if not autoform.is_valid():
        return JsonResponse({"errors": autoform.errors}, status=400)
    mode = autoform.cleaned_data["mode"]
    if mode == "judge" and not request.user.has_perm("unit.review", form_obj):
        raise PermissionDenied
    batch = BatchAutoTranslate(
        obj,
        user=request.user,
        q=autoform.cleaned_data["q"],
        mode=mode,
        component_wide=isinstance(obj, Component),
        overwrite_existing=autoform.cleaned_data["overwrite_existing"],
    )
    judge_preview = batch.preview_judge_scope() if mode == "judge" else None
    mt_preview = batch.preview_mt_scope() if mode != "judge" else None
```

Судейская ветка сохраняет текущую семантику полностью: `preview = judge_preview`, `judge_cost` и прежняя, cap-ограниченная оценка pretranslation остаются в `if mode == "judge"`. Для не-судейской ветки использовать `preview = mt_preview`, `judge_cost = {"available": False}`, `processed = remaining = judge_calls_initial = judge_calls_worst_case = 0`; возвращать `matched` и `writable` именно из `mt_preview`. Нельзя подменять judge preview MT-preview: у судьи `processed` ограничен cap, а pretranslate касается только writable строк выбранной capped-последовательности.

- [ ] **Step 5: Оценивать MT по всему скоупу**

Заменить блок `pretranslation_cost` (строки 1667-1692). Ключевая арифметика: `fetch_machinery_matches` спрашивает **каждый** выбранный движок про **каждую** строку (`weblate/trans/machinery.py:199-225`), поэтому цена одной строки — это сумма по движкам, а не самый дешёвый из них. По языкам, наоборот, строки не пересекаются, поэтому суммировать цены языков и умножать на общий `writable` нельзя — это посчитало бы каждую строку столько раз, сколько в скоупе языков. Верная и честная оболочка: минимум и максимум построчной суммы по языкам, умноженные на общий `writable`.

```python
    def _engine_cost_range(configurations, engine_id, translation):
        configuration = configurations.get(engine_id)
        if configuration is None or engine_id not in MACHINERY:
            return None
        machine = MACHINERY[engine_id](configuration)
        resolve_model = getattr(machine, "resolve_model", None)
        if not callable(resolve_model):
            return None
        model = resolve_model(translation.language.code)
        if not isinstance(model, str):
            return None
        return recent_cost_range(
            translation.component.project_id,
            machine.get_identifier(),
            model,
            LLMUsageLog.Operation.TRANSLATION,
        )
```

```python
    pretranslation_cost: dict[str, str | bool] = {"available": False}
    engine_ids = autoform.cleaned_data["engines"]
    if (
        mode != "judge"
        and autoform.cleaned_data["auto_source"] == "mt"
        and engine_ids
    ):
        per_string: list[Decimal] = []
        complete = bool(mt_preview.per_translation)
        for translation, _writable in mt_preview.per_translation:
            configurations = translation.component.project.get_machinery_settings()
            low = high = Decimal(0)
            for engine_id in engine_ids:
                cost_range = _engine_cost_range(configurations, engine_id, translation)
                if cost_range is None:
                    complete = False
                    break
                low += cost_range[0]
                high += cost_range[1]
            if not complete:
                break
            per_string.append(low)
            per_string.append(high)
        # A partial estimate is worse than none: it would price some
        # language-engine pairs of the run and silently drop the others.
        if complete and per_string:
            pretranslation_cost = {
                "available": True,
                "min": format(
                    (min(per_string) * mt_preview.writable).normalize(), "f"
                ),
                "max": format(
                    (max(per_string) * mt_preview.writable).normalize(), "f"
                ),
            }
```

Обоснование границ: общая стоимость равна сумме по языкам `writable_t · p_t`, где `p_t` — построчная сумма цен движков для языка `t`. Поскольку `min_t p_t ≤ p_t ≤ max_t p_t`, произведение крайних значений на общий `writable` даёт корректную оболочку, не требуя показывать построчные счётчики по каждому языку. При однородном роутинге интервал стягивается в точку.

- [ ] **Step 6: Показывать превью в любом режиме**

В `<form>` заменить `data-judge-preview-url` на `data-auto-preview-url`, а строку превью — на:

```html
      <p class="d-none" id="id_auto_run_preview" aria-live="polite"></p>
```

В `weblate/static/loader-bootstrap.js` заменить весь блок `form[data-judge-preview-url]` (1330-1442) на `form[data-auto-preview-url]`. Назвать функцию `updateAutoPreview`; она должна не делать HTTP-запрос для memory-only запуска, но для judge всегда оставаться активной (судья вызывает LLM независимо от radio `auto_source`):

```javascript
    const isJudge = () => mode.value === "judge";
    const usesMachineTranslation = () =>
      isJudge() ||
      form.querySelector('[name="auto_source"]:checked')?.value === "mt";

    const updateAutoPreview = () => {
      if (!usesMachineTranslation()) {
        controller?.abort();
        window.clearTimeout(timer);
        hidePreview();
        apply.disabled = false;
        return;
      }
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        controller?.abort();
        controller = new AbortController();
        const params = new URLSearchParams(new FormData(form));
        fetch(`${form.dataset.autoPreviewUrl}?${params}`, {
          signal: controller.signal,
        })
          .then((response) => {
            if (response.status === 400) {
              return response.json().then(() => {
                const error = new Error("Invalid automatic translation preview");
                error.invalid = true;
                throw error;
              });
            }
            if (!response.ok) {
              throw new Error("Automatic translation preview failed");
            }
            return response.json();
          })
          .then((data) => {
            const judge = isJudge();
            const costs = [
              data.pretranslation_cost.available
                ? interpolate(
                    gettext(
                      "Estimated machine translation cost: %(min)s to %(max)s USD.",
                    ),
                    data.pretranslation_cost,
                    true,
                  )
                : gettext("Estimated machine translation cost is unavailable."),
            ];
            if (judge) {
              costs.push(
                data.judge_cost.available
                  ? interpolate(
                      gettext("Estimated judge cost: %(min)s to %(max)s USD."),
                      data.judge_cost,
                      true,
                    )
                  : gettext("Estimated judge cost is unavailable."),
              );
            }
            const scope = judge
              ? interpolate(
                  gettext(
                    "%(matched)s matching strings: %(processed)s will be judge-evaluated, %(writable)s may be pretranslated, and %(remaining)s remain because of the cap.",
                  ),
                  data,
                  true,
                )
              : interpolate(
                  gettext("%(matched)s matching strings will be considered by the selected machine translation engines."),
                  data,
                  true,
                );
            preview.textContent = `${scope} ${costs.join(" ")} ${gettext(
              "Estimates come from this project's recent runs and are not a guarantee.",
            )}`;
            showPreview();
            apply.disabled = judge && data.processed === 0;
          })
          .catch((error) => {
            if (error.name === "AbortError") {
              return;
            }
            preview.textContent = error.invalid
              ? gettext("Automatic translation preview input is invalid.")
              : gettext(
                  "Automatic translation preview is unavailable. You can still apply this run.",
                );
            showPreview();
            apply.disabled = Boolean(error.invalid);
          });
      }, 250);
    };
```

Bind `updateAutoPreview` to `mode` change, query input, `overwrite_existing`, `engines`, and every `auto_source` radio change; call it once after binding. This preserves abort/debounce and the existing safe `textContent` rendering, removes the judge-only wording, and makes switching MT → memory hide a now-inapplicable cost estimate.

- [ ] **Step 7: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k 'preview or mt_preview_needs_only'`
Expected: PASS

- [ ] **Step 8: Проверить вживую**

Через browser automation открыть <http://localhost:3001/projects/> → любой проект → :guilabel:`Operations` → :guilabel:`Batch automatic translation`. Проверить четыре состояния на реальной DOM-поверхности: (1) non-judge + MT — строка preview показывается и даёт диапазон либо explicit `unavailable`; (2) переключение на `Other translation components` скрывает строку и не блокирует apply; (3) judge сохраняет cap-текст и обе раздельные оценки; (4) пользователь с одним `translation.auto` видит обычный MT-preview без review. Снимок/observed DOM — доказательство; платный запуск не выполнять.

- [ ] **Step 9: Коммит**

```bash
git add weblate/trans/autotranslate.py weblate/trans/views/edit.py weblate/static/loader-bootstrap.js weblate/templates/snippets/autoform.html weblate/trans/tests/test_judge_views.py
git commit -m "feat(trans): estimate machine translation cost for every automatic translation run"
```

---

### Task 11: Документация, локализация и финальная проверка

**Files:**
- Modify: `docs/changes.rst` (верхняя, ещё не выпущенная секция)
- Modify: `docs/product/guides/producer-guide-weblate.md`
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Modify: `docs/product/plans/2026-09-07-mt-run-cost-receipt.md` (статус)

- [ ] **Step 1: Запись в changelog**

В верхнюю неизданную секцию `docs/changes.rst` добавить одну строку:

```rst
* Every automatic translation launch now has its own run report showing what it wrote and what it cost, and the automatic translation form estimates the cost of a machine translation run before it starts.
```

- [ ] **Step 2: Гайд продюсера**

В `docs/product/guides/producer-guide-weblate.md` в раздел про автоперевод добавить короткий абзац: отчёт открывается кнопкой в уведомлении о завершении и из истории на форме; `price unknown` означает, что provider не сообщил стоимость хотя бы одного запроса; «отправлено в модель» считает запросы, а остальные строки могли прийти из кэша, быть дубликатами или остаться без usable-ответа. Ссылка из отчёта открывает **текущие** строки по сохранённому фильтру, а не исторически точный список применённых строк.

- [ ] **Step 3: Русские переводы новых строк**

Добавить в `weblate/locale/ru/LC_MESSAGES/django.po` записи **только** для строк, введённых этим планом, вручную, не запуская `makemessages` по всему проекту: массовый прогон `msgmerge` помечает несвязанные строки как `fuzzy`, а `msgfmt` их пропускает, что молча откатывает уже готовые переводы.

Проверить каждую строку, введённую Task 2 и 7-10. Точный чек-лист msgid: `Producer run`, `Producer runs`, `Judge re-check`, `Deferred judge retry`, `Automatic translation run`, `Automatic suggestion run`, `Automatic translation runs`, `What it cost`, `Operation`, `Requests`, `Strings sent`, `Tokens`, `USD`, `LLM judge`, `What was written`, `Cost by language, provider, and model`, `Language`, `Provider`, `Model`, `price unknown`, `No large language model was used by this run.`, `Open current strings matching this run's filter`, `View run report`, `Estimated machine translation cost: %(min)s to %(max)s USD.`, `Estimated machine translation cost is unavailable.`, `Estimated judge cost: %(min)s to %(max)s USD.`, `Estimated judge cost is unavailable.`, `%(matched)s matching strings will be considered by the selected machine translation engines.`, `Estimates come from this project's recent runs and are not a guarantee.`, `Automatic translation preview input is invalid.`, `Automatic translation preview is unavailable. You can still apply this run.`, а также plural/placeholder формы: `reply not fully applied`, `request did not report a price`, `string was written`, `%(amount)s USD per written string.`, `%(cached)s prompt tokens were served from the provider's own cache.`, длинная строка Task 8 про sent/cache/duplicate/no usable result и существующий judge scope-текст, если его msgid меняется.

Затем скомпилировать:

Run: `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py compilemessages --locale ru`
Expected: без предупреждений; `grep -c '^#, fuzzy' weblate/locale/ru/LC_MESSAGES/django.po` не выросло относительно `git show HEAD:weblate/locale/ru/LC_MESSAGES/django.po | grep -c '^#, fuzzy'`

- [ ] **Step 4: Линт и типы**

Run: `uv run prek run --all-files`
Expected: PASS (или автоправки, которые надо закоммитить)

Run: `uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh`
Expected: без новых ошибок относительно `main`

- [ ] **Step 5: Полная регрессия затронутых наборов**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_deferrals.py weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_llm_usage.py weblate/trans/tests/test_commands.py weblate/machinery/tests.py weblate/api/tests.py`
Expected: PASS

- [ ] **Step 6: Отметить план выполненным**

В шапку этого файла добавить строку `**Status:** implemented YYYY-MM-DD` с фактической датой.

- [ ] **Step 7: Коммит и пуш**

```bash
git add docs/changes.rst docs/product/guides/producer-guide-weblate.md docs/product/plans/2026-09-07-mt-run-cost-receipt.md weblate/locale/ru/LC_MESSAGES/django.po
git commit -m "docs(trans): document the producer run cost report"
git push
```

---

## Out of scope

Явно не входит в этот план, чтобы не расползаться:

- **Список конкретных записанных строк** в MT-отчёте. Судейский список стоит на `JudgeRunUnit`; у автоперевода per-unit записей нет, а `Change` не ссылается на прогон. Отчёт даёт агрегаты и ссылку по сохранённому запросу, которая показывает текущий, а не исторический набор.
- **Отчёт «Расход LLM» за период** как пятый `Report.Kind`. Это финансовая витрина, а не чек прогона, и она не отвечает на вопрос «сколько стоил вот этот запуск», когда прогоны перекрываются.
- **Бюджеты и лимиты**: пороги, hard stop, трансляция провайдерского `403` про исчерпанный бюджет в читаемую ошибку.
- **Отдельный счётчик попаданий локального кэша машинерии.** Считать его пришлось бы в `weblate/machinery/base.py:1040-1044`, а этот код исполняется в пуле потоков; `len(sources) − Σ len(pending)` для этого не годится, потому что тот же `continue` срабатывает на пустом источнике и на rate limit (`base.py:1010-1011`). Отчёт показывает `batch_size` как число строк, отправленных в модель; различие с числом matched/written может также означать неиспользуемый ответ, а не только кэш или дубликат.
- **BYOK-учёт**: чтение `cost_details.upstream_inference_cost` в отдельную колонку. Нужно только при переходе на собственные провайдерские ключи; сейчас расход идёт с кредитов OpenRouter, где `usage.cost` полон.
- **Чек для запуска из памяти переводов** (`auto_source="others"`). Такой запуск не обращается к модели, ничего не стоит и прогона не создаёт; его итог по-прежнему сообщает сообщение о завершении задачи («N strings were updated»). Это же удерживает десять строк истории прогонов за платными запусками.
