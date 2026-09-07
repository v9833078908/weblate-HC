# MT Run Cost Receipt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Продюсер, запустивший обычный машинный перевод, видит на странице отчёта прогона, что было записано и во что это встало в долларах — той же страницей, на которой сейчас живёт отчёт судьи.

**Architecture:** `JudgeRun` переименовывается в `ProducerRun` (state-only миграция, таблица `trans_judgerun` остаётся на месте) и начинает создаваться для запуска автоперевода, который обращается к машинному переводу (`auto_source="mt"`), а не только для судейского. Запуск из памяти переводов (`auto_source="others"`) прогона не создаёт: он ничего не стоит, и чек ему не нужен. Каждая строка `LLMUsageLog` получает FK на прогон, поэтому расход прогона считается одним `aggregate()` без временных окон. Страница отчёта получает общую карточку расхода для обоих режимов и две MT-специфичные карточки вместо судейской триажной.

**Tech Stack:** Django 5 ORM, `SeparateDatabaseAndState`-миграции, Celery, Django templates + Bootstrap 5, crispy-forms, vanilla JS (`weblate/static/loader-bootstrap.js`), pytest через `./rundev.sh test`.

## Global Constraints

- Каждый новый Python-файл начинается с `# Copyright © HCGameLoc`, пустой строки-комментария и `# SPDX-License-Identifier: GPL-3.0-or-later`.
- Каждый Python-модуль содержит `from __future__ import annotations`.
- Все пользовательские строки в шаблонах — через `{% translate %}` / `{% blocktranslate %}`; в Python — через `gettext` / `gettext_lazy`. Строки для API, аудита и логов не локализуются.
- Ruff-подавления пишутся человекочитаемыми именами: `# ruff: ignore[assert]`, никогда `# noqa: S101`.
- Коммиты — Conventional Commits: `<type>(<scope>): <description>`.
- Тесты запускаются внутри контейнера: `./rundev.sh test <path>`. Хостовый `uv run pytest` требует отдельной настройки БД и в этом плане не используется.
- Линт после каждой задачи не запускается; один прогон `uv run prek run --all-files` в последней задаче.
- `cost_usd = NULL` означает «неизвестно», никогда «ноль». Ни одна агрегация не подставляет ноль на месте `NULL`.
- Правило переименования: symbol-aware переименования выполняются инструментом `lsp` (`action: "rename"`), не текстовой заменой.
- URL-имя маршрута `judge-run` **сохраняется** во всех задачах: на него ссылаются шаблоны, `weblate/trans/tasks.py` и внешние закладки продюсеров. Переименовывается модель и Python-символы, не публичный идентификатор маршрута.

---

### Task 1: Переименование `JudgeRun` → `ProducerRun`

**Files:**
- Modify: `weblate/trans/models/judge.py:276-339`
- Modify: `weblate/trans/models/__init__.py:36-40,78-82`
- Create: `weblate/trans/migrations/0121_producer_run.py`
- Test: `weblate/trans/tests/test_judge.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `weblate.trans.models.ProducerRun` с теми же полями и `Meta.db_table = "trans_judgerun"`; обратный аксессор `User.producer_runs`; классы `ProducerRun.ScopeType`, `ProducerRun.Status`. Задачи 2-11 используют только это имя.

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

Это правит определение и все 232 ссылки в `weblate/trans/`, `weblate/api/tests.py` и тестах. Файлы миграций `0107_*` и `0111_*` править **нельзя**: исторические миграции хранят старое имя, и это корректно. Если LSP тронул файл в `weblate/trans/migrations/`, откатить именно этот файл через `git checkout -- <path>`.

- [ ] **Step 4: Дописать модель руками**

`weblate/trans/models/judge.py`, класс `ProducerRun`: обновить докстринг, `related_name` и `Meta`.

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
```

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
        # The model is renamed in state only. Renaming the table would move
        # every stored judge run, and its foreign keys are column-identical.
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
            ],
            database_operations=[],
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
    ]
```

- [ ] **Step 6: Проверить, что состояние миграций сходится**

Run: `DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations trans --check --dry-run`
Expected: `No changes detected` (БД не нужна). Если печатает предложенную миграцию — состояние не совпало с моделью; сверить `db_table`, `related_name` и `options`.

- [ ] **Step 7: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_autotranslate.py`
Expected: PASS, ни одного `ImportError`

- [ ] **Step 8: Коммит**

```bash
git add weblate/trans/models/judge.py weblate/trans/models/__init__.py weblate/trans/migrations/0121_producer_run.py weblate/trans/ weblate/api/tests.py
git commit -m "refactor(trans): rename JudgeRun to ProducerRun without moving its table"
```

---

### Task 2: Переименование поверхности отчёта прогона

**Files:**
- Modify: `weblate/trans/models/judge.py` (добавить `RUN_KIND_LABELS`)
- Modify: `weblate/trans/views/judge.py:54-59,308-323,396-427,430-544`
- Modify: `weblate/trans/views/basic.py:82,828,1010-1022,1129-1132`
- Rename: `weblate/templates/judge-run.html` → `weblate/templates/producer-run.html`
- Rename: `weblate/templates/snippets/judge-runs-menu.html` → `weblate/templates/snippets/producer-runs-menu.html`
- Modify: `weblate/templates/snippets/autoform.html:57-59`
- Test: `weblate/trans/tests/test_judge_views.py`

**Interfaces:**
- Consumes: `ProducerRun` из Task 1.
- Produces: `recent_producer_runs(scope, *, user, limit=10) -> list[ProducerRun]`, `producer_run_modes(user, scope) -> tuple[str, ...]`, `user_can_view_producer_run(user, scope, run) -> bool`, view `producer_run(request, pk)`, шаблон `producer-run.html`, сниппет `producer-runs-menu.html`, контекстные ключи `producer_runs` и `producer_last_run`, константы `HISTORY_MODES` и `JUDGE_MODES`. Маршрут по-прежнему называется `judge-run`.

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
```

Хелпер `create_run` расширить необязательным аргументом `requested_mode="judge"` и передавать его в `ProducerRun.objects.create(...)` вместо жёстко прошитой строки (`weblate/trans/tests/test_judge_views.py:2221`).

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k test_report_uses_the_producer_run_template`
Expected: FAIL с `Template 'producer-run.html' was not a template used to render the response`

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

- [ ] **Step 4: Сделать право просмотра зависимым от режима прогона**

`weblate/trans/views/judge.py`: рядом с `HISTORY_MODES` (строка 59) добавить список судейских режимов и две функции; `settings` из `django.conf` в этом модуле уже импортирован.

```python
# The modes whose report shows judge verdicts. Everything else in
# HISTORY_MODES is a plain automatic translation launch.
JUDGE_MODES = ("judge", "recheck", "drain")


def producer_run_modes(user, scope) -> tuple[str, ...]:
    """
    Which of ``HISTORY_MODES`` this user may currently see for this scope.

    A judge run shows verdicts, so it stays behind ``unit.review``. A plain
    automatic translation run shows only its own scope, what it wrote and
    what it cost, so the permission that launched it is the permission that
    reads it. Requiring ``unit.review`` for those would hide the launcher's
    own receipt on every project without a review workflow, because
    ``check_unit_review`` denies that permission outright when
    ``translation_review`` is off - a superuser included.
    """
    if not user.has_perm("translation.auto", scope):
        return ()
    if settings.JUDGE_ENABLED and user.has_perm("unit.review", scope):
        return HISTORY_MODES
    return tuple(mode for mode in HISTORY_MODES if mode not in JUDGE_MODES)


def user_can_view_producer_run(user, scope, run) -> bool:
    """Whether ``user`` currently (not at launch time) may view this run."""
    if not user.has_perm("translation.auto", scope):
        return False
    if run.requested_mode in JUDGE_MODES:
        return settings.JUDGE_ENABLED and user.has_perm("unit.review", scope)
    return True
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

Докстринг дополнить абзацем: судейские режимы попадают в список только при `JUDGE_ENABLED` и `unit.review`, поэтому меню на инстансе с выключенным судьёй показывает только запуски автоперевода.

- [ ] **Step 5: Переименовать шаблоны и контекст**

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

`weblate/trans/views/basic.py`: переименовать локальные переменные и контекстные ключи `judge_runs` → `producer_runs`, `judge_last_run` → `producer_last_run`, поправить импорт на строке 82 и передать пользователя в оба вызова. Вызов на строке 828 становится `recent_producer_runs(obj, user=user)` (взять того же пользователя, которым уже пользуется окружающий код). Блок на строках 1015-1022 сжимается до двух строк, потому что фильтрация по правам теперь внутри функции, а привязка к `JUDGE_ENABLED` для MT-прогонов была бы неверной:

```python
    producer_runs = recent_producer_runs(obj, user=user)
    producer_last_run = producer_runs[0] if producer_runs else None
```

`weblate/templates/snippets/autoform.html:57-59`:

```html
      {% if producer_runs %}
        {% include "snippets/producer-runs-menu.html" with runs=producer_runs current_path=request.path %}
      {% endif %}
```

Проверить остальные использования: `grep -rn "judge-runs-menu\|judge_runs\|judge_last_run\|judge-run.html" weblate/` — не должно остаться ни одного совпадения, кроме `name="judge-run"` в `weblate/urls.py` и `reverse("judge-run", ...)`.

- [ ] **Step 6: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add weblate/trans/views/judge.py weblate/trans/views/basic.py weblate/trans/models/judge.py weblate/templates weblate/urls.py weblate/trans/tests/test_judge_views.py
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
- Test: `weblate/trans/tests/test_llm_usage.py`, `weblate/machinery/tests.py`

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

Хелперы `mock_response_priced`, `get_machine`, `assert_translate` и константы `SUPPORTED` / `SOURCE_TRANSLATED` / `EXPECTED_LEN` уже используются существующим тестом `test_usage_recorded` в этом же классе; если `assert_translate` не принимает `machine=`, вызвать `machine.translate(...)` напрямую тем же способом, каким это делает соседний тест.

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py -k LLMUsageRunAttributionTest weblate/machinery/tests.py -k test_usage_is_billed_to_the_run`
Expected: FAIL с `AttributeError: type object 'BatchMachineTranslation' has no attribute 'usage_run_id'`

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

Run: `./rundev.sh test weblate/trans/tests/test_llm_usage.py weblate/machinery/tests.py -k LLMUsage weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py`
Expected: PASS

- [ ] **Step 9: Коммит**

```bash
git add weblate/machinery/base.py weblate/machinery/openai.py weblate/trans/judge.py weblate/trans/autotranslate.py weblate/trans/judge_loop.py weblate/trans/tests/test_llm_usage.py weblate/machinery/tests.py
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

`cost_usd` — сумма только известных цен; `unpriced_requests` — сколько запросов цену не сообщили. Ноль в `cost_usd` при непустом `unpriced_requests` не означает бесплатный прогон, и шаблон обязан показать оба числа.

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
Expected: 7 passed

- [ ] **Step 5: Коммит**

```bash
git add weblate/trans/models/llm_usage.py weblate/trans/tests/test_llm_usage.py
git commit -m "feat(trans): aggregate one run's LLM spend per operation"
```

---

### Task 6: Прогон создаётся для запуска машинного перевода

**Files:**
- Modify: `weblate/trans/autotranslate.py:1193-1280,1354-1390,1407-1424`
- Test: `weblate/trans/tests/test_autotranslate.py`

**Interfaces:**
- Consumes: `ProducerRun` из Task 1, `usage_run_id`-проводку из Task 4.
- Produces: `BatchAutoTranslate.active_producer_run: ProducerRun | None` — непустой для судейского запуска и для любого запуска с `auto_source="mt"`, `None` для запуска из памяти переводов; `AutoTranslate.producer_run` вместо `judge_run`; `ProducerRun.requested_mode` равен режиму `AutoForm`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `weblate/trans/tests/test_autotranslate.py`:

```python
class ProducerRunCreationTest(ViewTestCase):
    def _perform(self, mode: str, *, auto_source: str = "mt") -> BatchAutoTranslate:
        auto = BatchAutoTranslate(
            self.component,
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

    def test_translation_memory_launch_records_no_run(self) -> None:
        """A launch that asks no model has no cost, so it gets no receipt.

        It must also not take one of the ten rows in the scope's run
        history away from a launch that did cost money.
        """
        auto = self._perform("translate", auto_source="others")
        self.assertIsNone(auto.active_producer_run)
        self.assertFalse(ProducerRun.objects.exists())

    def test_failed_launch_is_finalized_as_failed(self) -> None:
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
        run.refresh_from_db()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertEqual(run.failure, "boom")
```

Тесты офлайновые: `"weblate"` не настроен в машинерии тестового проекта, поэтому `fetch_mt` отфильтрует его и ни одного HTTP-запроса не сделает. Прогон при этом создаётся, потому что условие — `auto_source`, а не наличие пригодного движка.

Импорты: `from unittest import mock`, `BatchAutoTranslate` из `weblate.trans.autotranslate`, `ProducerRun` из `weblate.trans.models`, `ViewTestCase` из `weblate.trans.tests.test_views`.

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py -k ProducerRunCreationTest`
Expected: FAIL с `AttributeError: 'BatchAutoTranslate' object has no attribute 'active_producer_run'`

- [ ] **Step 3: Переименовать судейские атрибуты прогона через LSP**

```json
{"action": "rename", "file": "weblate/trans/autotranslate.py", "line": 1056, "symbol": "active_judge_run", "new_name": "active_producer_run"}
```

Затем переименовать конструкторный параметр и атрибут `judge_run` у `AutoTranslate` в `producer_run`: найти его объявление (`grep -n "judge_run" weblate/trans/autotranslate.py`) и выполнить `lsp rename` на объявлении. После этого поправить строку из Task 4, шаг 6, на `self.producer_run`.

- [ ] **Step 4: Создавать прогон для платного запуска**

`weblate/trans/autotranslate.py`, `_create_judge_run` (строка 1248): переименовать в `_create_producer_run` через `lsp rename` и заменить конец функции так, чтобы снимок конфигурации судьи писался только для судейского режима:

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

Заодно заменить сообщение об ошибке в `case _:` на нейтральное:

```python
                msg = "A producer run requires a translation, component, project, or workspace"
```

В `_perform` (строки 1385-1390) заменить создание прогона:

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

и далее по функции заменить локальную `judge_run` на `producer_run` (строки 1386-1387, 1420, 1426-1442 и остальные вхождения в этой функции — найти через `grep -n "judge_run" weblate/trans/autotranslate.py`). Условия, которые относятся именно к судье, обязаны остаться привязанными к `self.mode == "judge"`, а не к наличию прогона: `_record_skipped_judge_units` вызывается только в судейском режиме.

- [ ] **Step 5: Финализировать прогон в любом режиме**

`weblate/trans/autotranslate.py`, `_finish_judge_run` (строка 1313): переименовать в `_finish_producer_run` через `lsp rename`. В `perform` (строки 1362-1375) заменить телом:

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
            self._finish_producer_run(
                self.active_producer_run, ProducerRun.Status.COMPLETED
            )
        return message
```

Если `_perform` уже завершает судейский прогон сам, `_finish_producer_run` не перезапишет его: защита от повторной финализации стоит в самой функции (`weblate/trans/autotranslate.py:1322-1323`).

- [ ] **Step 6: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_views.py`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add weblate/trans/autotranslate.py weblate/trans/tests/test_autotranslate.py
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

    def test_report_names_unpriced_requests_instead_of_showing_zero(self) -> None:
        self.enable_review()
        run = self.create_run()
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
        self.assertContains(response, "did not report a price")

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
    # Cost per string is deliberately divided by what the run actually wrote,
    # not by what its filter matched: dividing by matched strings understates
    # the price of every run that skipped most of its scope.
    written = run.summary.get("written") or 0
    cost_per_written = (
        (translation_spend.cost_usd + judge_spend.cost_usd) / written
        if written and (translation_spend.cost_usd or judge_spend.cost_usd)
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
        {% if cost_per_written %}
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
- Produces: `ProducerRun.summary["written"]` (int) для не-судейских прогонов; контекстные ключи `is_judge_run` (bool) и `language_spend` (`list[dict]` с ключами `language`, `model`, `requests`, `strings_sent`, `cost_usd`, `unpriced_requests`).

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

    def test_translation_run_report_breaks_spend_down_by_language(self) -> None:
        self.enable_review()
        run = self.create_run(requested_mode="translate")
        for language_code, cost in (("fr", "0.10"), ("ja", "0.40")):
            LLMUsageLog.objects.create(
                model=f"vendor/{language_code}",
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
            [(row["language"], row["cost_usd"]) for row in rows],
            [("ja", Decimal("0.40")), ("fr", Decimal("0.10"))],
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
    is_judge_run = run.requested_mode in {"judge", "recheck", "drain"}
    language_spend = [
        {
            "language": row["target_language_code"],
            "model": row["model"],
            "requests": row["requests"],
            "strings_sent": row["strings_sent"] or 0,
            "cost_usd": row["cost_usd"] or Decimal(0),
            "unpriced_requests": row["unpriced_requests"],
        }
        for row in LLMUsageLog.objects.filter(
            run_id=run.pk, operation=LLMUsageLog.Operation.TRANSLATION
        )
        .values("target_language_code", "model")
        .annotate(
            requests=Count("id"),
            strings_sent=Sum("batch_size"),
            cost_usd=Sum("cost_usd"),
            unpriced_requests=Count("id", filter=Q(cost_usd__isnull=True)),
        )
        .order_by("-cost_usd", "target_language_code")
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
            {% blocktranslate with sent=translation_spend.strings_sent %}{{ sent }} strings were sent to the model; the rest came from the machinery cache or were duplicate sources, and cost nothing.{% endblocktranslate %}
          </p>
        {% else %}
          <p class="text-muted mb-0">{% translate "No large language model was used by this run." %}</p>
        {% endif %}
        <p class="mt-2 mb-0">
          <a href="{{ scope_review_url }}">{% translate "Open the strings this run asked for" %}</a>
        </p>
      </div>
    </div>

    {% if language_spend %}
      <div class="card mb-3">
        <div class="card-header">
          <h4 class="card-title">{% translate "Cost by language and model" %}</h4>
        </div>
        <table class="table mb-0">
          <thead>
            <tr>
              <th>{% translate "Language" %}</th>
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

Во вьюху добавить ссылку, по которой открываются строки прогона:

```python
    scope_review_url = (
        _review_url(scope, run.requested_query)
        if run.requested_query
        else scope.get_absolute_url()
    )
```

и передать её в контекст как `"scope_review_url": scope_review_url,`.

- [ ] **Step 6: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_autotranslate.py`
Expected: PASS

- [ ] **Step 7: Коммит**

```bash
git add weblate/trans/autotranslate.py weblate/trans/views/judge.py weblate/templates/producer-run.html weblate/trans/tests
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

В `weblate/trans/tests/test_autotranslate.py`:

```python
    def test_task_result_links_to_the_report(self) -> None:
        result = auto_translate(
            user_id=self.user.id,
            mode="translate",
            q="",
            auto_source="mt",
            source_component_id=None,
            engines=["weblate"],
            threshold=80,
            component_id=self.component.id,
            component_wide=True,
        )
        self.assertTrue(result["report_url"].startswith("/judge-runs/"))
```

Именованные аргументы обязательны: `auto_translate` объявлена как keyword-only (`weblate/trans/tasks.py:989-1013`). Импортировать `auto_translate` из `weblate.trans.tasks`; тест опирается на `CELERY_TASK_ALWAYS_EAGER`, который в тестовых настройках уже включён.

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

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py -k report_url weblate/trans/tests/test_judge_views.py -k history_menu_lists_a_translation_run`
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
git add weblate/trans/tasks.py weblate/static/loader-bootstrap.js weblate/trans/views/judge.py weblate/trans/models/judge.py weblate/templates/snippets/producer-runs-menu.html weblate/trans/tests
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
- Produces: `BatchAutoTranslate.preview_mt_scope() -> MTScopePreview` (поля `matched`, `writable`, `per_translation: list[tuple[Translation, int]]`); эндпоинт `auto_translation_preview` отвечает для любого режима; `pretranslation_cost` — интервал: цена одной строки для языка равна **сумме** по всем выбранным движкам, а границы интервала — минимум и максимум этой суммы по языкам скоупа, умноженные на `writable`; при хотя бы одной неоценимой паре (язык × движок) — `available: false`. Элемент превью переименован в `id_auto_run_preview`.

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

    def request_preview(self, engines: list[str]) -> dict:
        response = self.client.get(
            reverse(
                "auto_translation_preview",
                kwargs={"path": self.component.get_url_path()},
            ),
            {
                "mode": "translate",
                "q": "state:empty",
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
            .filter(state__lt=STATE_TRANSLATED)
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
```

`self.kw_translation` — существующий атрибут `ViewTestCase`, которым пользуются тесты превью на строках 193 и 249. `Decimal`, `LLMUsageLog`, `STATE_READONLY` и `STATE_TRANSLATED` дописать в импорты файла. Настройка машинерии повторяет уже принятый в репозитории приём (`weblate/trans/tests/test_judge_views.py:3132`, `weblate/trans/tests/test_tasks.py:579-583`).

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
        """Matched and writable string counts per target translation."""
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
                overwrite_existing=self.overwrite_existing,
            ).get_units()
            matched += units.count()
            if not self.overwrite_existing:
                units = units.filter(state__lt=STATE_TRANSLATED)
            rows.append((translation, units.count()))
        return MTScopePreview(
            matched=matched,
            writable=sum(count for _translation, count in rows),
            per_translation=rows,
        )
```

Две `COUNT`-запроса на язык. Эндпоинт превью уже дебаунсится на 250 мс и уже строит `AutoTranslate` на язык в судейском режиме, так что профиль запросов не меняется качественно.

- [ ] **Step 4: Отвязать эндпоинт от судейского режима**

`weblate/trans/views/edit.py:1626`:

```python
    if not autoform.is_valid():
        return JsonResponse({"errors": autoform.errors}, status=400)
    mode = autoform.cleaned_data["mode"]
```

Ниже, при создании `BatchAutoTranslate`, передавать `mode=mode`. Судейский блок (`preview_judge_scope()` и расчёт `judge_cost`) обернуть в `if mode == "judge":`, оставив `judge_cost = {"available": False}` по умолчанию, а `matched`/`writable` в ответе брать из `preview_mt_scope()`, которая работает в любом режиме. Судейские ключи (`processed`, `remaining`, `judge_calls_initial`, `judge_calls_worst_case`) в не-судейском режиме отдавать нулями.

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
    if autoform.cleaned_data["auto_source"] == "mt" and engine_ids:
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

`weblate/templates/snippets/autoform.html:60`:

```html
      <p class="d-none" id="id_auto_run_preview" aria-live="polite"></p>
```

`weblate/static/loader-bootstrap.js`: в блоке `document.querySelectorAll("form[data-judge-preview-url]")` заменить селектор превью на `#id_auto_run_preview`, убрать ранний выход по режиму (строки 1352-1357) и разделить текст на две ветви:

```javascript
    const updatePreview = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        /* unchanged fetch block */
      }, 250);
    };
```

Текст собирать так, чтобы судейские числа появлялись только в судейском режиме:

```javascript
            const isJudge = mode.value === "judge";
            const costs = [
              data.pretranslation_cost.available
                ? interpolate(
                    gettext("Estimated machine translation cost: %(min)s to %(max)s USD."),
                    data.pretranslation_cost,
                    true,
                  )
                : gettext("Estimated machine translation cost is unavailable."),
            ];
            if (isJudge) {
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
            const scope = isJudge
              ? interpolate(
                  gettext(
                    "%(matched)s matching strings: %(processed)s will be judge-evaluated, %(writable)s may be pretranslated, and %(remaining)s remain because of the cap.",
                  ),
                  data,
                  true,
                )
              : interpolate(
                  gettext("%(matched)s matching strings, %(writable)s of them writable."),
                  data,
                  true,
                );
            preview.textContent = `${scope} ${costs.join(" ")} ${gettext(
              "Estimates come from this project's recent runs and are not a guarantee.",
            )}`;
            showPreview();
            apply.disabled = isJudge && data.processed === 0;
```

Оставить `mode.addEventListener("change", updatePreview)` и остальные слушатели, заменив имя функции.

- [ ] **Step 7: Запустить тесты**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k preview`
Expected: PASS

- [ ] **Step 8: Проверить вживую**

Открыть <http://localhost:3001/projects/> → любой проект → :guilabel:`Operations` → :guilabel:`Batch automatic translation`, выбрать режим :guilabel:`Add as translation`, источник :guilabel:`Machine translation`. Ожидается: строка превью появляется без выбора судейского режима и содержит либо диапазон в долларах, либо явное «unavailable», и подпись про оценку.

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

В `docs/product/guides/producer-guide-weblate.md` в раздел про автоперевод добавить короткий абзац: где найти отчёт прогона (кнопка в уведомлении о завершении и меню истории прогонов в форме), что означает «цена не сообщена», и почему «отправлено в модель» меньше числа записанных строк (кэш машинерии и дубликаты источника не тарифицируются).

- [ ] **Step 3: Русские переводы новых строк**

Добавить в `weblate/locale/ru/LC_MESSAGES/django.po` записи **только** для строк, введённых этим планом, вручную, не запуская `makemessages` по всему проекту: массовый прогон `msgmerge` помечает несвязанные строки как `fuzzy`, а `msgfmt` их пропускает, что молча откатывает уже готовые переводы.

Строки для перевода: `What it cost`, `Operation`, `Requests`, `Strings sent`, `Tokens`, `USD`, `Machine translation`, `LLM judge`, `What was written`, `Cost by language and model`, `Language`, `Model`, `price unknown`, `Open the strings this run asked for`, `No large language model was used by this run.`, `View run report`, `Producer run`, `Automatic translation run`, `Automatic suggestion run`, `Judge re-check`, `Deferred judge retry`, плюс формы множественного числа для трёх `blocktranslate count` блоков и строки оценки из JS.

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
git add docs weblate/locale/ru/LC_MESSAGES
git commit -m "docs(trans): document the producer run cost report"
git push
```

---

## Out of scope

Явно не входит в этот план, чтобы не расползаться:

- **Список конкретных записанных строк** в MT-отчёте. Судейский список стоит на `JudgeRunUnit`; у автоперевода per-unit записей нет, а `Change` не ссылается на прогон. Отчёт даёт агрегаты и ссылку по сохранённому запросу прогона.
- **Отчёт «Расход LLM» за период** как пятый `Report.Kind`. Это финансовая витрина, а не чек прогона, и она не отвечает на вопрос «сколько стоил вот этот запуск», когда прогоны перекрываются.
- **Бюджеты и лимиты**: пороги, hard stop, трансляция провайдерского `403` про исчерпанный бюджет в читаемую ошибку.
- **Отдельный счётчик попаданий локального кэша машинерии.** Считать его пришлось бы в `weblate/machinery/base.py:1040-1044`, а этот код исполняется в пуле потоков; `len(sources) − Σ len(pending)` для этого не годится, потому что тот же `continue` срабатывает на пустом источнике и на rate limit (`base.py:1010-1011`). Пока отчёт показывает «отправлено в модель» из `batch_size` и честно называет остаток кэшем или дубликатами источника.
- **BYOK-учёт**: чтение `cost_details.upstream_inference_cost` в отдельную колонку. Нужно только при переходе на собственные провайдерские ключи; сейчас расход идёт с кредитов OpenRouter, где `usage.cost` полон.
- **Чек для запуска из памяти переводов** (`auto_source="others"`). Такой запуск не обращается к модели, ничего не стоит и прогона не создаёт; его итог по-прежнему сообщает сообщение о завершении задачи («N strings were updated»). Это же удерживает десять строк истории прогонов за платными запусками.
