# Judge Run Report Live Severity Buckets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** proposed, awaiting approval. Not started.

**Goal:** Счётчик отчёта прогона перестаёт утверждать, что строка не исправлена, после того как её исправили: строка, чей текст больше не тот, который судил судья, уходит из `critical`/`major`/`minor` в отдельный бакет «Исправлено после прогона» и перестаёт считаться блокирующей.

**Architecture:** Никакого нового поля и никакой миграции. `JudgeVerdict.target_storage_hash` уже хранит md5 точного `Unit.target`, который судья оценил (`weblate/trans/models/judge.py:214`, миграция `0103`), поэтому «текст изменился» — это одно сравнение столбца со столбцом, которое PostgreSQL делает сам через `MD5(F("unit__target"))`; тот же приём уже используется в `judge_status_annotations` (`weblate/trans/models/judge.py:1162`). Правка сосредоточена в `_filter_outcome` (`weblate/trans/views/judge.py:125`): и шапка, и её страница-детализация ходят через эту функцию, поэтому счётчик и список не могут разойтись, а `blocking`, таблица Парето и запросы детализации исправляются сами, потому что все они уже построены на `_filter_outcome`.

**Tech Stack:** Django 5 ORM (`django.db.models.functions.MD5`, `F`, `Q`), PostgreSQL, Django templates + Bootstrap 5, `gettext`/`blocktranslate`, pytest через `./rundev.sh test`.

**Spec:** этот документ самодостаточен. Основание — измерение на проде 2026-09-07, раздел «Evidence» ниже; исходный код прочитан по состоянию на коммит `87a27d7`.

## Evidence

Измерено 2026-09-07 на `l10n.herocraft.com`, прогон `36a5be4a-b2d8-42a7-a934-23eb76800737` (`need-for-greed/ui/es`, 462 строки, завершён 2026-09-04):

| Бакет | Строк | Из них текст уже изменён |
|---|---|---|
| Needs action | 108 | 13 |
| Critical held | 43 | 11 |
| Major not fixed | 49 | 2 |
| Minor noted | 16 | 0 |
| Unparsed / Stale conflict | 0 | 0 |
| Accepted as is | 4 | — |

Плашка «Что делать» утверждает «39 строк не будут опубликованы»: из 43 вычтены 4 решения «оставить как есть», но не вычтены 11 исправленных. Изменения `ivanbelov` в этом переводе после старта прогона: 13 «Предложение принято», 4 «Решение по вердикту судьи» — то есть 13 помеченных дрейфом строк это ровно принятые исправления судьи, один к одному.

Причина расхождения: бакеты фильтруют по сохранённому `JudgeRunUnit.outcome`, который фиксируется в момент прогона, а дрейф вычисляется только для отображаемой строки в `_annotate_row` и в фильтр не входит.

## Global Constraints

- PostgreSQL — единственная поддерживаемая база Weblate; `md5()` на стороне БД считать доступным.
- Миграций в этом плане нет. Ни одного нового поля, ни одного backfill.
- `MD5("unit__target")` **неверно**: `Func` оборачивает обычную строку в `Value`, то есть захешируется литерал `"unit__target"`. Всегда `MD5(F("unit__target"))`.
- `JudgeVerdict.target_storage_hash` nullable: вердикт, записанный до миграции `0103` или не дошедший до backfill (`weblate/trans/judge_loop.py:354-363`), хеша не имеет. «Неизвестно» никогда не читать как «исправлено»: такая строка остаётся в своём бакете. Направление ошибки выбрано осознанно — лишняя работа в списке безопаснее спрятанного дефекта.
- Русскую локаль править **вручную**: широкий `makemessages` в этом репозитории уже приводил к 117 новым `#, fuzzy` записям и молчаливой потере готовых переводов, потому что `msgmerge` сопоставляет новый msgid с похожим старым. Добавить две записи руками, до и после сверить `grep -c '^#, fuzzy'`.
- Имя маршрута `judge-run` и URL отчёта не меняются.
- Перед каждым коммитом проверять `git status --short` и добавлять только файлы текущей задачи явными путями. Не использовать `git add <directory>`: в дереве регулярно лежат чужие незакоммиченные изменения.
- Тесты запускать через `./rundev.sh test` — контейнер уже настроен, хостовому pytest нужны `DJANGO_SETTINGS_MODULE`, PostgreSQL и `collectstatic`.
- Деплой — отдельный, явно одобряемый шаг. Этот план его не включает.

## Sequencing

`docs/product/plans/2026-09-07-mt-run-cost-receipt.md` (implementation-ready, в коде ещё нет: `git grep ProducerRun -- weblate` пусто) переименовывает `JudgeRun` → `ProducerRun` и трогает те же файлы. **Этот план выполнять первым:** он меньше, без миграций, и переименование потом заберёт его механически через LSP rename. Если порядок окажется обратным, отображение символов такое: `weblate/trans/views/judge.py` → файл отчёта после переименования, `JudgeRunUnit` → его переименованный аналог, `judge-run.html` → переименованный шаблон; сами предикат, ярлыки и тексты не меняются.

---

### Task 1: Бакет `fixed-since-run` и живые счётчики строгости

**Files:**

- Modify: `weblate/trans/views/judge.py` (импорты, `_OUTCOME_LABELS`, новые константа и два помощника, `_filter_outcome`, словарь `triage`)
- Test: `weblate/trans/tests/test_judge_views.py` (класс `JudgeRunReportViewTest`)

**Interfaces:**

- Consumes: `JudgeRunUnit.outcome`, `JudgeRunUnit.unit`, `JudgeVerdict.target_storage_hash`, `Unit.target`, `compute_target_storage_hash(target: str) -> str`.
- Produces:
  - `_SEVERITY_OUTCOMES: tuple[str, str, str]` — critical/major/minor.
  - `_fixed_since_run(rows: QuerySet) -> QuerySet`.
  - `_still_holds(rows: QuerySet) -> QuerySet` — точное дополнение предыдущего внутри `rows`.
  - новый валидный `?outcome=fixed-since-run` и ключ `counts["fixed-since-run"]`.
  - `triage["fixed_since_run"]: int` — потребляется Task 3.
- [ ] **Step 1: Написать падающие тесты**

Добавить в конец класса `JudgeRunReportViewTest` (`weblate/trans/tests/test_judge_views.py`). `Unit`, `JudgeVerdict`, `JudgeRunUnit`, `compute_target_storage_hash`, `CaptureQueriesContext`, `connection` в этом файле уже импортированы.

```python
    def critical_row(self, run, unit, *, category: str = "mistranslation"):
        """A row whose verdict records the exact text it graded."""
        verdict = self.make_verdict_with_error(
            unit, severity="critical", category=category
        )
        return self.add_row(
            run, unit, outcome=JudgeRunUnit.Outcome.CRITICAL, verdict=verdict
        )

    def retarget(self, unit, text: str) -> None:
        """Change only the stored text, with none of Unit.save's side effects."""
        Unit.objects.filter(pk=unit.pk).update(target=text)

    def test_an_edited_string_leaves_its_severity_bucket(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        row = self.critical_row(run, unit)

        counts = self.client.get(self.report_url(run)).context["counts"]
        self.assertEqual(counts["critical"], 1)
        self.assertEqual(counts["actionable"], 1)
        self.assertEqual(counts["fixed-since-run"], 0)

        self.retarget(unit, "Совсем другой перевод")

        counts = self.client.get(self.report_url(run)).context["counts"]
        self.assertEqual(counts["critical"], 0)
        self.assertEqual(counts["actionable"], 0)
        self.assertEqual(counts["fixed-since-run"], 1)

        listed = self.client.get(
            self.report_url(run), {"outcome": "fixed-since-run"}
        )
        self.assertEqual([entry.pk for entry in listed.context["page_obj"]], [row.pk])

    def test_an_edited_critical_stops_blocking_the_hero_card(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        self.critical_row(run, unit)
        self.assertEqual(
            self.client.get(self.report_url(run)).context["triage"]["blocking"], 1
        )

        self.retarget(unit, "Исправленный перевод")

        triage = self.client.get(self.report_url(run)).context["triage"]
        self.assertEqual(triage["blocking"], 0)
        self.assertEqual(triage["fixed_since_run"], 1)
        self.assertEqual(triage["total_actionable"], 0)

    def test_a_verdict_without_a_storage_hash_is_never_read_as_fixed(self) -> None:
        # Verdicts written before migration 0103, and those its backfill
        # could not reach, carry no storage hash at all. "Unknown" must not
        # read as "fixed": that would hide a real defect.
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        row = self.critical_row(run, unit)
        JudgeVerdict.objects.filter(pk=row.verdict_id).update(target_storage_hash=None)

        self.retarget(unit, "Другой текст без учтённого хеша")

        counts = self.client.get(self.report_url(run)).context["counts"]
        self.assertEqual(counts["critical"], 1)
        self.assertEqual(counts["fixed-since-run"], 0)

    def test_an_unparsed_row_is_never_read_as_fixed(self) -> None:
        # An unparsed row has no usable verdict, so no later edit can clear
        # it: it needs a re-check whatever the text now says.
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        self.add_row(run, unit, outcome=JudgeRunUnit.Outcome.UNPARSED)

        self.retarget(unit, "Правка, которая ничего не доказывает")

        counts = self.client.get(self.report_url(run)).context["counts"]
        self.assertEqual(counts["unparsed"], 1)
        self.assertEqual(counts["actionable"], 1)
        self.assertEqual(counts["fixed-since-run"], 0)

    def test_the_two_buckets_partition_the_severity_rows(self) -> None:
        # The header count and its own drill-down list must never disagree,
        # so the pair must be an exact partition, not two overlapping filters.
        self.enable_review()
        run = self.create_run()
        units = list(self.translation.unit_set.all()[:3])
        self.assertEqual(len(units), 3)
        for unit in units:
            self.critical_row(run, unit)
        self.retarget(units[0], "Единственная исправленная строка")

        counts = self.client.get(self.report_url(run)).context["counts"]
        self.assertEqual(counts["critical"] + counts["fixed-since-run"], 3)
        self.assertEqual(counts["critical"], 2)
        self.assertEqual(counts["fixed-since-run"], 1)

    def test_the_pareto_table_drops_a_fixed_string(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        self.critical_row(run, unit, category="terminology")
        categories = self.client.get(self.report_url(run)).context["categories"]
        self.assertEqual([entry["category"] for entry in categories], ["terminology"])

        self.retarget(unit, "Терминология поправлена")

        self.assertEqual(self.client.get(self.report_url(run)).context["categories"], [])

    def test_the_new_bucket_adds_no_per_row_query(self) -> None:
        self.enable_review()
        run = self.create_run()
        self.critical_row(run, self.get_unit())
        with CaptureQueriesContext(connection) as small:
            self.client.get(self.report_url(run))
        for offset in range(60):
            self.add_row(
                run,
                unit_id_snapshot=910000 + offset,
                outcome=JudgeRunUnit.Outcome.MINOR,
            )
        with CaptureQueriesContext(connection) as large:
            self.client.get(self.report_url(run))
        self.assertEqual(len(large.captured_queries), len(small.captured_queries))
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k "JudgeRunReportViewTest and (fixed or partition or pareto or unparsed_row or per_row_query)" -q`
Expected: FAIL. `test_an_edited_string_leaves_its_severity_bucket` падает с `KeyError: 'fixed-since-run'` на `context["counts"]`; после появления ключа — на `assertEqual(counts["critical"], 0)`, потому что счётчик остаётся 1.

- [ ] **Step 3: Записать в вердикты фикстур текст, который они судили**

В `JudgeRunReportViewTest` два помощника создают вердикт без `target_storage_hash`, чего в проде не бывает: `judge_loop.py:328` пишет хеш всегда. Фикстура должна повторять прод, иначе новый предикат тестируется на нереальных данных.

`weblate/trans/tests/test_judge_views.py`, в `make_verdict` добавить строку после `target_hash=...`:

```python
            target_storage_hash=compute_target_storage_hash(unit.target),
```

То же в `make_verdict_with_error`, после её `target_hash=...`.

- [ ] **Step 4: Добавить импорты и константу строгих исходов**

`weblate/trans/views/judge.py`, заменить строки 26-27:

```python
from django.db.models import Case, CharField, F, Q, Value, When
from django.db.models.functions import Cast, MD5
```

Строку 34 заменить на:

```python
from weblate.trans.models.judge import (
    SEVERITY_RANK,
    JudgeVerdict,
    compute_target_storage_hash,
)
```

После кортежа `_ACTIONABLE_OUTCOMES` (строка 70) добавить:

```python
# The three buckets that assert "this text still has this defect", and so
# the only ones an edit can answer. Unparsed and stale-conflict carry no
# usable verdict at all: they need a re-check, not a rewrite, so no later
# edit may clear them.
_SEVERITY_OUTCOMES = (_OUTCOME.CRITICAL, _OUTCOME.MAJOR, _OUTCOME.MINOR)
```

- [ ] **Step 5: Добавить ярлык бакета**

`weblate/trans/views/judge.py`, в `_OUTCOME_LABELS` сразу после строки `"stale-conflict": gettext_lazy("Stale conflict"),` добавить:

```python
    # Placed next to the actionable group it drains: a producer reads
    # "Needs action 95" and "Fixed since this run 13" side by side.
    "fixed-since-run": gettext_lazy("Fixed since this run"),
```

- [ ] **Step 6: Написать предикат и его точное дополнение**

`weblate/trans/views/judge.py`, вставить перед `def _filter_outcome`:

```python
def _fixed_since_run(rows: QuerySet) -> QuerySet:
    """
    Rows whose graded text is no longer the unit's stored text.

    ``JudgeVerdict.target_storage_hash`` is an md5 of the exact stored
    ``Unit.target`` the judge was shown (``compute_target_storage_hash``,
    migration ``0103``), so this is one column-to-column comparison the
    database does itself - the same ``MD5(...)`` match
    ``judge_status_annotations`` already relies on. No Python pass over the
    run's rows, and no second snapshot column to keep in sync.

    ``MD5(F(...))``, never ``MD5("...")``: ``Func`` wraps a plain string in
    ``Value``, which would hash the literal field name.

    A verdict with no hash (written before ``0103``, or missed by its
    backfill - see ``weblate/trans/judge_loop.py``) stays in its severity
    bucket. Unknown must not read as fixed: the producer then keeps work
    that is already done, which is the safe direction; the reverse hides a
    real defect.
    """
    return rows.filter(
        unit__isnull=False,
        outcome__in=_SEVERITY_OUTCOMES,
        verdict__target_storage_hash__isnull=False,
    ).exclude(verdict__target_storage_hash=MD5(F("unit__target")))


def _still_holds(rows: QuerySet) -> QuerySet:
    """
    The exact complement of :func:`_fixed_since_run` inside ``rows``.

    Written as a positive disjunction rather than ``exclude()`` of that
    queryset: a header count and its own drill-down list must never
    disagree, and the two directions are easier to prove complementary
    when both are ordinary filters over the same joins.
    """
    return rows.filter(
        Q(unit__isnull=True)
        | ~Q(outcome__in=_SEVERITY_OUTCOMES)
        | Q(verdict__target_storage_hash__isnull=True)
        | Q(verdict__target_storage_hash=MD5(F("unit__target")))
    )
```

- [ ] **Step 7: Развести бакеты в `_filter_outcome`**

`weblate/trans/views/judge.py`, в `_filter_outcome` заменить ветку `actionable` (строки 127-128) на:

```python
    if key == "actionable":
        # A string whose text the producer already replaced is not work:
        # it leaves this list and shows up under "fixed-since-run".
        return _still_holds(rows.filter(outcome__in=_ACTIONABLE_OUTCOMES))
    if key == "fixed-since-run":
        return _fixed_since_run(rows)
```

И заменить последнюю строку функции (`return rows.filter(outcome=key)`) на:

```python
    if key in _SEVERITY_OUTCOMES:
        return _still_holds(rows.filter(outcome=key))
    return rows.filter(outcome=key)
```

- [ ] **Step 8: Отдать счётчик в `triage`**

`weblate/trans/views/judge.py`, в словарь `triage` добавить после `"needs_recheck": needs_recheck,`:

```python
        "fixed_since_run": counts["fixed-since-run"],
```

- [ ] **Step 9: Запустить тесты и убедиться, что они проходят**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k JudgeRunReportViewTest -q`
Expected: PASS, включая семь новых тестов и все ранее существовавшие тесты класса.

- [ ] **Step 10: Коммит**

```bash
git status --short
git add weblate/trans/views/judge.py weblate/trans/tests/test_judge_views.py
git commit -m "fix(judge): drop an already-edited string from the report's severity buckets

The buckets filter on the frozen JudgeRunUnit.outcome, so a string a
producer fixed after the run kept being counted as unfixed: production run
36a5be4a reported 43 held critical rows of which 11 had already been
rewritten, and the hero card claimed 39 strings would not ship.

A row whose verdict's target_storage_hash no longer matches md5 of the
unit's stored target moves to the new fixed-since-run bucket. One
column-to-column comparison, no migration; blocking, the Pareto table and
every drill-down correct themselves because they already route through
_filter_outcome. A verdict with no stored hash keeps its bucket."
```

---

### Task 2: Пометка строки и её действие говорят о судимом тексте

**Files:**

- Modify: `weblate/trans/views/judge.py` (`_annotate_row`)
- Test: `weblate/trans/tests/test_judge_views.py` (класс `JudgeRunReportViewTest`)

**Interfaces:**

- Consumes: `_SEVERITY_OUTCOMES`, `compute_target_storage_hash` из Task 1; `row.verdict.target_storage_hash`, `row.input_target`.
- Produces: `row.current_target_matches: bool` с новой семантикой (судимый текст, а не снимок прогона) и `row.action` для дрейфующей строки.

Зачем: после Task 1 на странице появляются две разные идеи «текст изменился» — бакет считает по хешу вердикта, а пометка в ячейке по снимку `input_target`. Они могут расходиться (вердикт из более ранней попытки), и тогда строка лежит в бакете «исправлено», а её собственная ячейка молчит. Пометка приводится к тому же признаку, что и бакет.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `JudgeRunReportViewTest`:

```python
    def test_the_row_marker_follows_the_graded_text(self) -> None:
        # The bucket keys on the verdict's own text; the row's cell must say
        # the same thing, or a row lands under "fixed" while its cell claims
        # the text is unchanged.
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        row = self.critical_row(run, unit)
        # The run snapshot still matches, but the graded text does not.
        JudgeVerdict.objects.filter(pk=row.verdict_id).update(
            target_storage_hash=compute_target_storage_hash("текст прошлой попытки")
        )
        response = self.client.get(
            self.report_url(run), {"outcome": "fixed-since-run"}
        )
        self.assertEqual(len(response.context["page_obj"]), 1)
        self.assertFalse(response.context["page_obj"][0].current_target_matches)
        self.assertContains(response, "current text changed since this run")

    def test_a_hashless_verdict_still_compares_the_run_snapshot(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        row = self.critical_row(run, unit)
        JudgeVerdict.objects.filter(pk=row.verdict_id).update(target_storage_hash=None)
        self.retarget(unit, "Текст, изменённый без хеша вердикта")
        response = self.client.get(self.report_url(run), {"outcome": "critical"})
        self.assertFalse(response.context["page_obj"][0].current_target_matches)
        self.assertContains(response, "current text changed since this run")

    def test_a_fixed_row_offers_a_re_check_not_a_fix(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        self.critical_row(run, unit)
        self.retarget(unit, "Уже исправлено вручную")
        response = self.client.get(
            self.report_url(run), {"outcome": "fixed-since-run"}
        )
        self.assertEqual(
            str(response.context["page_obj"][0].action), "Re-check this string"
        )
        self.assertNotContains(response, "Fix and re-check")
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k "JudgeRunReportViewTest and (row_marker or hashless or re_check_not_a_fix)" -q`
Expected: FAIL. `test_the_row_marker_follows_the_graded_text` — `assertFalse` получает `True`, потому что снимок совпадает; `test_a_fixed_row_offers_a_re_check_not_a_fix` — action равен `Fix and re-check`.

- [ ] **Step 3: Считать пометку по судимому тексту**

`weblate/trans/views/judge.py`, в `_annotate_row` заменить блок присваивания `row.current_target_matches` (строки 271-273) на:

```python
    verdict = row.verdict
    if verdict is not None and verdict.target_storage_hash:
        # Agree with the report's buckets by construction: they key on the
        # text the judge actually graded, so the row's own cell must too.
        row.current_target_matches = (  # type: ignore[attr-defined]
            unit is not None
            and verdict.target_storage_hash
            == compute_target_storage_hash(unit.target)
        )
    else:
        # No graded text on record - a pre-0103 verdict, or a row with no
        # verdict at all. The run's own snapshot is all there is.
        row.current_target_matches = (  # type: ignore[attr-defined]
            unit is not None and unit.get_target_plurals() == row.input_target
        )
```

Ниже в той же функции строка `primary = row.verdict.primary_error if row.verdict else None` заменяется на использование уже полученной переменной:

```python
    primary = verdict.primary_error if verdict is not None else None
```

- [ ] **Step 4: Предложить перепроверку вместо правки**

`weblate/trans/views/judge.py`, в `_annotate_row` заменить начало цепочки выбора действия (`if unit is None: row.action = ""` и следующий `elif`) так, чтобы дрейф проверялся первым:

```python
    if unit is None:
        row.action = ""  # type: ignore[attr-defined]
    elif not row.current_target_matches:
        # The graded text is gone, so neither "fix" nor "review" is honest:
        # only a re-check can say anything about the text that is there now.
        # Existing msgid, already translated - see judge-verdict.html.
        row.action = gettext_lazy("Re-check this string")  # type: ignore[attr-defined]
    elif row.repair_status == _REPAIR.CANDIDATE_STORED:
```

Остальные ветки цепочки не трогать.

- [ ] **Step 5: Запустить тесты и убедиться, что они проходят**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k JudgeRunReportViewTest -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git status --short
git add weblate/trans/views/judge.py weblate/trans/tests/test_judge_views.py
git commit -m "fix(judge): key the report row marker on the graded text

The row cell compared the run's input_target snapshot while the new
fixed-since-run bucket compares the verdict's own graded text, so a row
could be listed as fixed while its cell said the text was unchanged. The
marker now uses the verdict's stored hash whenever it has one and falls
back to the snapshot only for a hashless verdict.

A drifted row also stops offering \"Fix and re-check\": the graded text is
gone, so the only honest action is the re-check the string page already
provides."
```

---

### Task 3: Строка «Исправлено после прогона» в карточке «Что делать»

**Files:**

- Modify: `weblate/templates/judge-run.html` (карточка `What to do`)
- Modify: `weblate/locale/ru/LC_MESSAGES/django.po`
- Modify: `weblate/locale/ru/LC_MESSAGES/django.mo` (перекомпиляция)
- Test: `weblate/trans/tests/test_judge_views.py` (класс `JudgeRunReportViewTest`)

**Interfaces:**

- Consumes: `triage["fixed_since_run"]` из Task 1.
- Produces: видимая строка со ссылкой `?outcome=fixed-since-run`.
- [ ] **Step 1: Написать падающий тест**

Добавить в `JudgeRunReportViewTest`:

```python
    def test_the_hero_card_names_the_strings_fixed_after_the_run(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        self.critical_row(run, unit)
        clean = self.client.get(self.report_url(run))
        self.assertNotContains(clean, "outcome=fixed-since-run")

        self.retarget(unit, "Исправлено продюсером")

        response = self.client.get(self.report_url(run))
        self.assertContains(response, "1 string was fixed after this run.")
        self.assertContains(response, "?outcome=fixed-since-run")
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k hero_card_names -q`
Expected: FAIL — `assertContains` не находит `1 string was fixed after this run.`

- [ ] **Step 3: Добавить строку в шаблон**

`weblate/templates/judge-run.html`, внутри `<div class="card-body">` карточки `What to do`, сразу после закрывающего `{% endif %}` блока `triage.candidates` (строка 70) и перед закрывающим `</div>`:

```html
      {% if triage.fixed_since_run %}
        {# The counts above exclude these rows; say so, or the drop looks #}
        {# like the report losing strings. #}
        <p class="text-muted small mt-2 mb-0">
          <a href="?outcome=fixed-since-run">{% blocktranslate count counter=triage.fixed_since_run %}{{ counter }} string was fixed after this run.{% plural %}{{ counter }} strings were fixed after this run.{% endblocktranslate %}</a>
        </p>
      {% endif %}
```

- [ ] **Step 4: Записать русский перевод вручную**

Сначала снять контрольную метрику, по которой потом видно, не появились ли новые `fuzzy`:

Run: `grep -c '^#, fuzzy' weblate/locale/ru/LC_MESSAGES/django.po`
Записать полученное число.

`weblate/locale/ru/LC_MESSAGES/django.po`: найти запись `msgid "%(counter)s string will not ship until you fix it."` (блок из пяти строк с `msgstr[0..2]`) и сразу после её пустой строки вставить две записи:

```po
#: weblate/templates/judge-run.html:74
#, python-format
msgid "%(counter)s string was fixed after this run."
msgid_plural "%(counter)s strings were fixed after this run."
msgstr[0] "%(counter)s строка исправлена после этого прогона."
msgstr[1] "%(counter)s строки исправлены после этого прогона."
msgstr[2] "%(counter)s строк исправлено после этого прогона."

#: weblate/trans/views/judge.py:84
msgid "Fixed since this run"
msgstr "Исправлено после прогона"
```

`makemessages` не запускать: номера строк в `#:` — справочные, на сборку они не влияют.

- [ ] **Step 5: Проверить, что перевод не внёс fuzzy, и скомпилировать**

```bash
grep -c '^#, fuzzy' weblate/locale/ru/LC_MESSAGES/django.po
msgfmt --check --statistics -o /dev/null weblate/locale/ru/LC_MESSAGES/django.po
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py compilemessages -l ru
```

Expected: число `fuzzy` совпадает с записанным в Step 4; `msgfmt --check` без ошибок; `compilemessages` завершается успешно и `weblate/locale/ru/LC_MESSAGES/django.mo` изменён.

- [ ] **Step 6: Запустить тесты и убедиться, что они проходят**

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py -k "JudgeRunReportViewTest or JudgeCardLocalizationTest" -q`
Expected: PASS.

- [ ] **Step 7: Проверить отрисовку на живой странице**

```bash
./rundev.sh logs --tail 5 weblate
```

Затем в браузере: открыть локальный отчёт прогона (`http://localhost:3001/judge-runs/<uuid>/`, логин `admin`/`admin`) для прогона, в котором есть исправленная после прогона строка, и убедиться глазами, что: строка «исправлена после этого прогона» видна под кнопками, ведёт на `?outcome=fixed-since-run`, а на самой странице бакета строка помечена «текст изменился с момента прогона». Если подходящего прогона в деве нет — создать его: запустить судью на одном компоненте, затем изменить текст одной осуждённой строки в редакторе.

- [ ] **Step 8: Коммит**

```bash
git status --short
git add weblate/templates/judge-run.html weblate/locale/ru/LC_MESSAGES/django.po weblate/locale/ru/LC_MESSAGES/django.mo weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): name the strings fixed after a judge run on its report

The severity counts now exclude them, so without a line saying so the drop
reads as the report losing strings. The sentence links to the bucket."
```

---

### Task 4: Документация и полный регресс

**Files:**

- Modify: `docs/admin/checks.rst` (описание отчёта прогона)
- Modify: `docs/changes.rst` (пункт про отчёт прогона в неизданной секции)

**Interfaces:**

- Consumes: поведение, закреплённое Task 1-3.
- Produces: ничего для кода.
- [ ] **Step 1: Описать бакет в руководстве администратора**

`docs/admin/checks.rst`: найти предложение, начинающееся `The run report lists checked, matched, cached, and`, и заменить его вместе со следующим (заканчивающимся `matching paginated list of strings.`) на:

```rst
as one durable run, addressed by its own URL, independent of the Celery task
result that started it. The run report lists checked, matched, cached, and
skipped counts, and repaired, rolled back, minor, major, critical, unparsed,
stale-conflict, accepted-as-is, and escalated outcomes; each count opens the
matching paginated list of strings. The three severity counts are live: a
string whose text no longer matches what the judge graded leaves them for a
:guilabel:`Fixed since this run` count, and stops being reported as
blocking. A verdict recorded before the run report existed has no record of
the text it graded, so such a string keeps its severity count.
```

- [ ] **Step 2: Дописать пункт changelog**

`docs/changes.rst`: в пункте неизданной секции, начинающемся `* Every LLM judge producer launch is now recorded as one durable`, дописать перед завершающим `See :ref:`llm-judge`.` предложение:

```rst
The critical, major and minor counts are live: a string edited after the run
moves to a separate :guilabel:`Fixed since this run` count instead of being
reported as still broken.
```

Отдельного пункта не создавать: отчёт прогона выпущен не был, и правка описывает ту же неизданную функциональность.

- [ ] **Step 3: Линтеры на изменённые файлы**

```bash
uv run prek run --files weblate/trans/views/judge.py weblate/trans/tests/test_judge_views.py weblate/templates/judge-run.html docs/admin/checks.rst docs/changes.rst
```

Expected: `ruff check`, `ruff format`, `djLint`, rst-хуки и `codespell` — Passed. `typos` падает на `analysis/data/**` независимо от этой задачи; если он падает, убедиться, что ни один изменённый файл не назван в его выводе.

- [ ] **Step 4: Полный судейский регресс**

```bash
./rundev.sh test weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_autotranslate.py -q
```

Expected: PASS без ошибок и без новых падений. Число тестов не меньше, чем до правки (`281 passed, 19 subtests` на `test_judge_views.py` + `test_judge.py` по состоянию на `48a0dd4`, плюс одиннадцать новых).

- [ ] **Step 5: Коммит**

```bash
git status --short
git add docs/admin/checks.rst docs/changes.rst
git commit -m "docs(judge): state that the report's severity counts are live"
```

---

## Acceptance

- `?outcome=fixed-since-run` — валидный фильтр; его счётчик в шапке равен числу строк в его же списке.
- Для любого прогона: `critical + major + minor + unparsed + stale-conflict == actionable`, и `actionable + fixed-since-run` равно прежнему значению `actionable`.
- Строка, чей текст больше не тот, что судил судья, не входит ни в `blocking`, ни в таблицу Парето, ни в дефолтный список.
- Вердикт без `target_storage_hash` не считается исправленным никогда.
- Строка `unparsed` не считается исправленной никогда.
- Число запросов страницы не зависит от числа строк прогона.
- Одиннадцать новых тестов проходят, ни один существующий не удалён и не ослаблен.

## Post-deploy verification (отдельное одобрение)

Только чтение, на проде, после отдельно одобренного деплоя. Прогон `36a5be4a-b2d8-42a7-a934-23eb76800737`:

| Показатель | До (измерено 2026-09-07) | Ожидается |
|---|---|---|
| Needs action | 108 | 95 |
| Critical held | 43 | 32 |
| Major not fixed | 49 | 47 |
| Minor noted | 16 | 16 |
| Fixed since this run | — | 13 |
| «строк не будут опубликованы» | 39 | не больше 32 |

Точный ожидаемый `blocking` не называется: он равен числу критичных строк, у которых нет решения «оставить как есть», а пересечение четырёх таких решений с одиннадцатью исправленными критичными строками не измерено. Числа 32/47/13 верны при условии, что хеш вердикта согласен со снимком прогона на каждой строке — именно это наблюдалось 2026-09-07, но проверка обязана перечитать их с прода, а не принять на веру.

## Out of scope

- Автоматическая перепроверка исправленной строки. Она уже происходит отдельным механизмом (одностроковый прогон `recheck`), и её результат читается на карточке строки.
- Переклассификация `JudgeRunUnit.outcome` в базе. Строка остаётся неизменяемой записью участия; меняется только то, как отчёт её показывает.
- `stale-conflict` и `unparsed`: у них нет пригодного вердикта, поэтому правка текста о них ничего не сообщает.
- Переименование `JudgeRun` → `ProducerRun`: отдельный план, см. «Sequencing».
- Деплой.
