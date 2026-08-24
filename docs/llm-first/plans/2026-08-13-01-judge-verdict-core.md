# План 1: Вердикт — ядро LLM-судьи

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task.

**Goal:** прогон судьи на фильтре даёт построчный вердикт, записанный в
`JudgeVerdict`; `critical` уходит в очередь на решение человека (`state 10`,
удерживается штатным `WITHOUT_NEEDS_EDITING`); судейские строки находятся через
`check:judge-flag` / `check:judge-reject` и переживают любой пересчёт чеков.

**Architecture:** вердикт живёт в собственной модели `JudgeVerdict`. `Check`-строка
— **производная**: судейский чек `check_target_unit` читает активный вердикт, поэтому
`run_checks` остаётся единственным писателем строк и они физически не могут разойтись
с `JudgeVerdict` (решение D1 ревью 2026-08-19). Коллегия двух судей судит **каждую**
строку батчами, вердикт круга — строгий из двух; починка — до исчерпания попыток,
затем очередь человека. Всё исполняется внутри существующей Celery-задачи
`auto_translate` через новый режим `judge`. Конфигурация судьи — сайтовая, по образцу
`LOC_KIT_PROFILE_OPENROUTER_*`, выключена по умолчанию.

**Tech Stack:** Python 3.13, Django 5 / PostgreSQL, Celery, httpx через
`weblate.utils.requests.fetch_validated_url`, OpenRouter (strict JSON Schema, батчи),
pytest + `weblate.utils.tests.http_mock`.

**Основание:** `docs/llm-first/designs/2026-08-13-judge-native-ui-design.md` (разделы 1-4,
«Точки касания в коде», раздел «Планы первого тира»). Всё, что дизайн-док отнёс к
планам 2 и 3, здесь не делается.

---

## Что изменил инженерный ревью 2026-08-19

Этот план переписан после plan-review. Шесть решений зафиксированы пользователем;
находки вплетены в задачи. Таблица — чтобы исполнитель понимал, почему код не совпадает
с первой редакцией.

| # | Находка (severity) | Решение | Где в плане |
|---|---|---|---|
| D1 | `run_checks` (`unit.py:2232-2233`) удаляет спроецированные judge-строки на **каждом** сохранении юнита — цель навигации проваливалась (**P0**) | Чек **читает** вердикт (`check_target_unit`), `run_checks` — единственный писатель. Модуль проекции упразднён | Задачи 3, 4 |
| D2 | `pass → state 30 approved` авто-одобряет на сигнале, который в 20% наблюдений настоящего critical возвращает «чисто» (**P1**) | Тир 1: `pass → 20`. `STATE_APPROVED` только при `JUDGE_MAY_APPROVE=True` (дефолт False) | Задача 2 |
| D3 | Петля починки перезаписывала существующий человеческий перевод в обход чекбокса; ~45% починок от ложных флагов (**P1**) | Починка разрешена только для «записываемых» строк (пусто/fuzzy, либо overwrite вкл.) — тот же чекбокс | Задачи 6, 8 |
| D4 | Хранимое поле `verdict` бетонирует маппинг, который R3 открыл заново (**P1**) | Хранить только `max_severity` (+ `unparsed`); `verdict` — property | Задачи 1, 2 |
| D5 | `unparsed`-круг сносил прошлую валидную проекцию и молча не менял состояние; при 403-троттлинге — бесшумный отказ на деньги (**P1**) | `unparsed` никогда не активен; троттлинг/ретрай в клиенте; счётчик `unparsed` в предупреждениях прогона | Задачи 3, 5, 6, 8 |
| D6 | Контракт клиента отличался от **измеренного** плеча D в 5 местах, поэтому ни одно измеренное число не переносилось (**P1**) | Полное выравнивание с плечом D: батчи, `span` строкой, `verdict` от модели, `render_preview`. `back_translation` — единственное осознанное отклонение, помечено как неизмеренное | Задача 5 |

Плюс правки поменьше, вплетённые в задачи: единый источник имён чеков (чеки живут в
ядре `weblate/checks/judge.py`, Q2); улики в промпт починки едут с явным разделителем и
экранированием, чтобы `strip_tags` не съедал `<color=…>` (Q1, задача 3); ссылки на
дизайн — по заголовкам, не по строкам (Q7); жёсткий потолок строк на прогон и честный
счётчик запросов (A9, задача 9); запись состояния сразу после починки, без разрыва
(A10, задача 6); транзакция + `select_for_update` вокруг круга (A8, задача 6);
`judge-*` не попадают в `enforced_checks` (A7, задача 10); миграция `0101`, не `0099`
(A11, задача 1).

> **Поправка по замеру** (полный прогон
> `docs/llm-first/measurements/2026-08-19-severity-recalibration-final.md`, гейты R1-R3):
> severity судьи — **не гарантия** (пропуски настоящих critical 1-5 за прогон, ложные
> critical 4-5 на 124), поэтому `critical → state 10` реализуется как в плане, но
> читается как **очередь на решение человека**, а не автоматический гарант «сломанное
> не уйдёт». Конфигурация клиента (задача 5) — **плечо D** замера: промпт плеча C
> (обязательный `description` + рубрика severity «последствие для игрока», `RUBRIC_RULE`)
> **плюс** детерминированные рендер-превью плейсхолдеров во входе (`render_preview`,
> `RENDER_RULE` в `analysis/probes/st2-zh-recalibration.py`). Плечо C — лучший профиль по шуму
> и precision; рендер-превью поверх него поднимают самый опасный класс
> (плейсхолдер-critical: 24207 0/5→4/5, 24130 2/5→5/5, ячейка `critical→none` 4→0) при
> плоском шуме. Регулятор «второй судья только для повышения до critical» из дизайна
> замером отвергнут — не реализовывать.

---

## Границы плана

### Входит

Строка «План 1» таблицы дизайна (раздел «Планы первого тира»): модель и миграция,
классы судейских чеков, клиент судьи (промпт — плечо D замера 2026-08-19), коллегия
двух судей и починка в Celery, режим `judge` в `AutoForm` с чекбоксом перезаписи,
fail-safe `state 10`, гейт по severity, удержание critical до решения человека, карточка
на юните, обратный перевод в форме, протухание вердикта, исключение судейских чеков из
карточки «Things to check».

### Не входит

| Отложено в | Что именно |
|---|---|
| План 2 | `judge_field` в `search.py`, регистрация в `FILTERS`, судейские ключи в выпадашке формы, `calculate_judge()`, карточка «Оценка ИИ» на обзоре языка, баннер релизной готовности, `NOT has:judge` как дефолтный `q` , счётчик строк и оценка цены под фактический `q` формы |
| План 3 | кнопки решений с обязательной причиной, работа с `resolution`, вкладка «Оценка ИИ» с историей кругов суда, парсер судейских спанов в `Formatter`, отдельный класс бейджа, рендер-превью в UI юнита для продюсера (дизайн, «Рендер-дефекты плейсхолдеров») |
| Вне первого тира | откат по `run_id`, аварийный выключатель на проекте, пер-языковые тиры, категория настроек `JUDGE` с наследованием, пер-компонентные ключи, обобщение промпта на все языковые пары (промпт измерен только на ru→zh_Hans) |

### Два осознанных разрыва, унаследованных из дизайна

1. **Дефолтный `q` режима `judge` остаётся общим для формы (`state:empty`).** Дизайн хочет
   `NOT has:judge`, но фильтр `has:judge` появляется только в плане 2. Строить механизм
   смены дефолта под несуществующий фильтр — работа на выброс. Продюсер в первом тире
   задаёт `q` руками. Приёмочный прогон 2026-08-20 показал цену этого разрыва: счётчик
   строк и оценка цены считают фиксированный `state:<translated`
   (`weblate/trans/views/basic.py:805-812`) вместо `q`, стоящего в форме, поэтому на
   переведённом компоненте форма обещает «0 строк» и затем судит 10
   (`docs/llm-first/measurements/2026-08-20-judge-first-dev-run.md`). Счётчик уходит в план 2 вместе с
   дефолтом: оба упираются в тот же фильтр.
2. **Санкционированного override нет до плана 3.** `critical` держится состоянием `10`;
   «принять вопреки судье» делается штатной сменой состояния при праве `unit.review`.

### Почему не лестница: конструкция, отвергнутая замером

Первая редакция описывала каскад: судья A на всех строках, судья B только на его
flag/reject — с правом вернуть `PASS` поверх флага A. Замер B2' запретил это прямо:
«Ступень B в дизайновом виде — с правом снимать флаг — не добавлять ни при каком выборе
моделей» (`2026-08-13-phase0-measurements.md`, рекомендация 4). Причина измеренная:
специфичность первой ступени 97.6-99.2%, снимать почти нечего, и переголосования снимали
настоящие дефекты — **46 ошибочных снятий из 48** в худшей паре. Сквозной recall каскада
равен `recall(A) × recall(B | эскалированные)` и по построению не может превысить recall
одной ступени A.

Коллегия даёт обратную арифметику, `1 − (1 − recall₁)(1 − recall₂)`, и уронить recall не
может: оба места судят **каждую** строку независимо, вердикт круга — строгий из двух.

Как это разложено по задачам:

| Место | Что стоит в плане |
|---|---|
| Поле модели | `seat`, не `tier` — место в коллегии, не старшинство (задача 1) |
| Порядок строгости | `SEVERITY_RANK` рядом с гейтом (задача 2) |
| Чтение вердикта | `active_round` + `collegium_verdict`; читается круг целиком (задача 3) |
| Настройки | `JUDGE_MODEL_SEAT_1` / `JUDGE_MODEL_SEAT_2` (задача 5) |
| Оркестрация | оба вызова безусловны, между ними нет ни одного `if` (задача 6) |
| Регрессия | `test_no_seat_may_lower_the_other` + мутация в задаче 6 |

Модели выбраны замером: `deepseek/deepseek-v4-pro` и `qwen/qwen3-235b-a22b-2507`.
Одинаковые модели на местах — **валидная конфигурация**, а не ошибка.

---

## Пять нативных механизмов, которые план переиспользует

Прежде чем писать код, пойми эти пять мест — они убирают больше половины ожидаемой работы.

**1. `run_checks` — единственный писатель `Check`-строк (ключ к D1).**
`weblate/trans/models/unit.py:2159-2233` перебирает `CHECKS.target`, и всё, что не
сработало, попадает в `old_checks` и удаляется (строка 2233). Поэтому **нельзя** писать
судейскую `Check`-строку в обход `run_checks` — она будет снесена на первом же сохранении
юнита. Вместо этого судейский чек `check_target_unit` **читает** активный вердикт и
возвращает `True`/`False`, как любой нативный чек. Тогда `run_checks` создаёт и удаляет
судейские строки сам, они не могут разойтись с `JudgeVerdict`, а протухание получается
бесплатно (чек перевычисляется на каждом `run_checks`). Первая редакция плана строила
отдельный модуль проекции — он **упразднён**.

**2. Улики попадают в промпт починки бесплатно.**
`weblate/machinery/llm.py:516,556-562` собирает `failing_checks` для промпта переводчика
и вызывает `check.get_description()` под `override("en")`. Базовый
`Check.get_description()` (`weblate/checks/models.py:121-124`) делегирует в
`check_obj.get_description(self)` — переопределяем и рендерим туда ошибки последнего
вердикта. Значит стадия починки — обычный вызов движка перевода: судейская `Check`-строка
уже записана (механизмом 1), и переводчик видит претензии судьи. **Осторожно**
(находка Q1): `llm.py:401-404` прогоняет описание через `strip_tags(...).split()` — это
вырезает `<color=#RRGGBB>`, `<b>`, `<link>` и склеивает переводы строк. Описание обязано
экранировать разметку и разделять ошибки явным маркером (задача 3, Step 3).

**3. Релизный гейт уже существует.** `commit_policy = WITHOUT_NEEDS_EDITING`
(`weblate/trans/models/project.py`) исключает `FUZZY_STATES = (10, 11, 12)`
(`weblate/utils/state.py:41`, `weblate/trans/models/pending.py`). Достаточно записать
`critical → state 10`. Кастомный экспорт писать не надо.

**4. Навигация приезжает вместе с `Check`-строкой.**
`weblate/trans/filter.py` итерирует `CHECKS` и заводит фильтр `check:<id>` каждому
зарегистрированному классу. Как только чек в `DEFAULT_CHECK_LIST` и строка записана,
работают поиск, бейдж, API и `stats.allchecks`.

**5. Существующий путь машинного перевода.** `AutoTranslate.process_mt` → `fetch_mt` →
`store_results` → `self.update` (`weblate/trans/autotranslate.py:656-712`) уже делает
батчевую, крэш-устойчивую запись MT через `unit.translate`. Режим `judge` **переиспользует**
его для предперевода (находка Q4), не пишет второй путь записи.

---

## Подготовка среды

Выполнить один раз перед задачей 1.

```bash
uv sync --all-extras --dev
```

Тесты гоняются в контейнере — это избавляет от настройки БД и `collectstatic`:

```bash
./rundev.sh test weblate/trans/tests/test_judge.py
```

Контейнер общий с другими сессиями. Если суите внезапно становится плохо (массовые
setup-ERROR, тест вместо 4 с идёт 100 с) — сначала `docker stats --no-stream`, а не поиск
бага в коде.

Линт по изменённым файлам:

```bash
uv run prek run --files <список файлов>
```

Соглашения репозитория, обязательные к соблюдению:

- новые файлы форка несут заголовок
  `# Copyright © HCGameLoc` + `# SPDX-License-Identifier: GPL-3.0-or-later`;
- `from __future__ import annotations` в каждом Python-модуле;
- пользовательские строки переводятся через `gettext_lazy`, **кроме** значений, которые
  уходят в API или в хранилище (вердикты, severity, описания ошибок) — их не локализуем;
- коммиты в формате Conventional Commits.

---

## Задача 1. Модель `JudgeVerdict` и хэши

Хранится **только** `max_severity` и флаг `unparsed`; `verdict` — производное свойство
(решение D4): маппинг severity→verdict R3 открыл заново, а хранимое поле потребовало бы
миграции данных при его смене.

**Files:**

- Create: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0101_judge_verdict.py`
- Modify: `weblate/trans/models/__init__.py` (импорт рядом с `loc_kit`, запись в `__all__`)
- Test: `weblate/trans/tests/test_judge.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.test import SimpleTestCase

from weblate.trans.models.judge import compute_context_hash, compute_target_hash


class JudgeHashTest(SimpleTestCase):
    def test_target_hash_is_stable(self) -> None:
        self.assertEqual(
            compute_target_hash(["La porte est bloquée"]),
            compute_target_hash(["La porte est bloquée"]),
        )

    def test_target_hash_tracks_every_plural_form(self) -> None:
        self.assertNotEqual(
            compute_target_hash(["une porte", "deux portes"]),
            compute_target_hash(["une porte", "trois portes"]),
        )

    def test_target_hash_separator_cannot_be_forged(self) -> None:
        # Naive "\n".join() would collide these two different plural sets.
        self.assertNotEqual(
            compute_target_hash(["a\nb"]),
            compute_target_hash(["a", "b"]),
        )

    def test_context_hash_reacts_to_glossary_and_note(self) -> None:
        base = compute_context_hash(source="Door", note="", glossary_terms=[])
        self.assertNotEqual(
            base, compute_context_hash(source="Door", note="hall", glossary_terms=[])
        )
        self.assertNotEqual(
            base,
            compute_context_hash(
                source="Door", note="", glossary_terms=[("Door", "Porte")]
            ),
        )

    def test_context_hash_ignores_glossary_order(self) -> None:
        self.assertEqual(
            compute_context_hash(
                source="Door", note="", glossary_terms=[("a", "b"), ("c", "d")]
            ),
            compute_context_hash(
                source="Door", note="", glossary_terms=[("c", "d"), ("a", "b")]
            ),
        )
```

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'weblate.trans.models.judge'`

### Step 3: Написать модуль модели

Create `weblate/trans/models/judge.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def _digest(parts: Sequence[str]) -> str:
    """Hash a sequence unambiguously.

    JSON encoding of the whole list keeps element boundaries, so a form
    containing the separator cannot forge another form's digest.
    """
    payload = json.dumps(list(parts), ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_target_hash(target: Sequence[str]) -> str:
    """Hash every plural form of a target."""
    return _digest(target)


def compute_context_hash(
    *, source: str, note: str, glossary_terms: Iterable[tuple[str, str]]
) -> str:
    """Hash what the judge was told besides the target.

    Glossary order is not context, so terms are sorted; a reordered
    glossary must not invalidate a verdict.
    """
    terms = sorted(f"{term}\x1f{translation}" for term, translation in glossary_terms)
    return _digest([source, note, *terms])


class JudgeVerdict(models.Model):
    """One judge opinion about one version of one unit.

    Verdicts are never overwritten: they accumulate per
    ``(unit, run_id, attempt, seat)`` so the collegium and its repair
    loop stay auditable. Only ``max_severity`` and ``unparsed`` are
    stored; the verdict is derived (severity->verdict mapping was
    reopened by measurement R3 and must stay changeable without a data
    migration).
    """

    # Stored and API-facing values: deliberately not localized.
    class Verdict(models.TextChoices):
        PASS = "pass"
        FLAG = "flag"
        REJECT = "reject"
        # Transport failure, never an opinion. Architecture invariant 4.3.
        UNPARSED = "unparsed"

    class Severity(models.TextChoices):
        # Declaration order IS strictness order (see SEVERITY_RANK, task 2).
        NONE = "none"
        MINOR = "minor"
        MAJOR = "major"
        CRITICAL = "critical"

    class Resolution(models.TextChoices):
        ACCEPTED_AS_IS = "accepted_as_is"
        SENT_BACK = "sent_back"
        ESCALATED = "escalated"

    unit = models.ForeignKey(
        "trans.Unit", on_delete=models.deletion.CASCADE, related_name="judge_verdicts"
    )
    # The model's own pass/flag/reject choice, kept as evidence (measured
    # arm D returns it per segment). The gate uses max_severity, not this.
    model_verdict = models.CharField(max_length=10, choices=Verdict, blank=True)
    max_severity = models.CharField(
        max_length=10, choices=Severity, default=Severity.NONE
    )
    # A transport failure is not a severity: it is a separate axis so an
    # unparsed row can never read as a real "none"/pass (invariant 4.3).
    unparsed = models.BooleanField(default=False)
    errors = models.JSONField(default=list, blank=True)
    back_translation = models.TextField(blank=True)
    judge_model = models.CharField(max_length=200)
    # Place in the collegium, not seniority: seat 2 may not lower seat 1.
    seat = models.SmallIntegerField()
    attempt = models.SmallIntegerField(default=0)
    target_hash = models.CharField(max_length=64)
    context_hash = models.CharField(max_length=64)
    run_id = models.UUIDField(default=uuid.uuid4)
    timestamp = models.DateTimeField(auto_now_add=True)

    resolution = models.CharField(max_length=20, choices=Resolution, blank=True)
    resolution_reason = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name="judge_resolutions",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Judge verdict")
        verbose_name_plural = gettext_lazy("Judge verdicts")
        # No default ordering: it would force a sort on every queryset of
        # a table that accumulates. Callers order explicitly.
        indexes = [
            models.Index(fields=["unit", "-timestamp"], name="judge_unit_recent_idx"),
            models.Index(
                fields=["unit", "target_hash", "-timestamp"],
                name="judge_unit_target_idx",
            ),
            models.Index(fields=["run_id"], name="judge_run_idx"),
        ]
        constraints = [
            # One vote per seat per round: a round is reduced to its
            # strictest seat and must not see a seat twice.
            models.UniqueConstraint(
                fields=["unit", "run_id", "attempt", "seat"],
                name="judge_one_vote_per_seat",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit_id}: {self.verdict} (seat {self.seat})"

    def is_stale(self, target: Sequence[str]) -> bool:
        """Whether the judged text differs from the text stored now."""
        return self.target_hash != compute_target_hash(target)
```

### Step 4: Прогнать тест хэшей

Run: `./rundev.sh test weblate/trans/tests/test_judge.py`

Expected: PASS (5 тестов)

### Step 5: Зарегистрировать модель

Modify `weblate/trans/models/__init__.py` — импорт рядом с существующим `loc_kit`
и запись в `__all__`, сохраняя алфавитный порядок:

```python
from weblate.trans.models.judge import JudgeVerdict
```

```text
    "JudgeVerdict",
```

### Step 6: Сгенерировать миграцию

`0099` и `0100` заняты (`0100_llmusagelog` — последняя). Имя явно, чтобы не сгенерировать
`0101` от чужого HEAD:

```bash
./rundev.sh manage makemigrations trans --name judge_verdict
```

Expected: `weblate/trans/migrations/0101_judge_verdict.py` с
`dependencies = [("trans", "0100_llmusagelog"), migrations.swappable_dependency(...)]`.
Заголовок — какой сгенерировал `makemigrations`, руками не править.

### Step 7: Проверить, что миграция применяется и откатывается

```bash
./rundev.sh manage migrate trans
./rundev.sh manage migrate trans 0100
./rundev.sh manage migrate trans
```

Expected: `Applying trans.0101_judge_verdict... OK`, затем `Unapplying...OK`, затем снова
`Applying...OK`.

### Step 8: Коммит

```bash
git add weblate/trans/models/judge.py weblate/trans/models/__init__.py \
  weblate/trans/migrations/0101_judge_verdict.py weblate/trans/tests/test_judge.py
git commit -m "feat(judge): add the JudgeVerdict model and verdict hashes"
```

---

## Задача 2. Гейт по severity и производный вердикт — чистые функции

Единственное место, где решается, что уйдёт в сборку. Проверяемо без БД, LLM и Celery.
Решение D2: `pass → 20`; `STATE_APPROVED` только за флагом `JUDGE_MAY_APPROVE`.

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Test: `weblate/trans/tests/test_judge.py`

### Step 1: Написать падающий тест

Добавить в `weblate/trans/tests/test_judge.py`:

```python
from django.test import override_settings

from weblate.trans.models.judge import (
    JudgeVerdict,
    SEVERITY_RANK,
    state_for_verdict,
    verdict_for_severity,
)
from weblate.utils.state import (
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_TRANSLATED,
)


class JudgeSeverityGateTest(SimpleTestCase):
    def test_severity_maps_to_verdict(self) -> None:
        self.assertEqual(verdict_for_severity("none"), JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict_for_severity("minor"), JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict_for_severity("major"), JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict_for_severity("critical"), JudgeVerdict.Verdict.REJECT)

    def test_severity_rank_is_ordered_by_strictness(self) -> None:
        # SEVERITY_RANK derives from the enum declaration order; this test
        # pins that coupling so a silent reorder cannot invert strictness.
        self.assertLess(SEVERITY_RANK["none"], SEVERITY_RANK["minor"])
        self.assertLess(SEVERITY_RANK["minor"], SEVERITY_RANK["major"])
        self.assertLess(SEVERITY_RANK["major"], SEVERITY_RANK["critical"])

    def test_pass_stops_at_translated_without_the_approve_flag(self) -> None:
        # D2: a probabilistic pass never auto-approves in tier 1.
        self.assertEqual(
            state_for_verdict(
                JudgeVerdict.Verdict.PASS, enable_review=True, may_approve=False
            ),
            STATE_TRANSLATED,
        )

    @override_settings(JUDGE_MAY_APPROVE=True)
    def test_pass_may_approve_only_when_both_flags_hold(self) -> None:
        self.assertEqual(
            state_for_verdict(
                JudgeVerdict.Verdict.PASS, enable_review=True, may_approve=True
            ),
            STATE_APPROVED,
        )
        # No review configured: approval is impossible regardless of the flag.
        self.assertEqual(
            state_for_verdict(
                JudgeVerdict.Verdict.PASS, enable_review=False, may_approve=True
            ),
            STATE_TRANSLATED,
        )

    def test_flag_ships_but_is_not_approved(self) -> None:
        self.assertEqual(
            state_for_verdict(
                JudgeVerdict.Verdict.FLAG, enable_review=True, may_approve=True
            ),
            STATE_TRANSLATED,
        )

    def test_reject_lands_on_a_state_that_does_not_ship(self) -> None:
        from weblate.utils.state import FUZZY_STATES

        state = state_for_verdict(
            JudgeVerdict.Verdict.REJECT, enable_review=True, may_approve=True
        )
        self.assertEqual(state, STATE_FUZZY)
        self.assertIn(state, FUZZY_STATES)

    def test_unparsed_never_changes_state(self) -> None:
        self.assertIsNone(
            state_for_verdict(
                JudgeVerdict.Verdict.UNPARSED, enable_review=True, may_approve=True
            )
        )
```

Последний блок — самый важный: он привязывает наш гейт к штатному
`WITHOUT_NEEDS_EDITING` через импорт `FUZZY_STATES` из `weblate/utils/state.py:41`, а не к
константе в тесте.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge.py`

Expected: FAIL — `ImportError: cannot import name 'verdict_for_severity'`

### Step 3: Реализовать

Добавить в `weblate/trans/models/judge.py` **после** класса `JudgeVerdict`:

```python
from weblate.utils.state import STATE_APPROVED, STATE_FUZZY, STATE_TRANSLATED

# Design "Гейт по severity выражается штатными настройками". minor is a
# pass: the errors are recorded, but they do not hold the string back.
_SEVERITY_VERDICT = {
    "none": JudgeVerdict.Verdict.PASS,
    "minor": JudgeVerdict.Verdict.PASS,
    "major": JudgeVerdict.Verdict.FLAG,
    "critical": JudgeVerdict.Verdict.REJECT,
}

# Strictness order, so a round reduces to its strictest seat without a
# seat ever lowering another. Derived from the declared scale: Severity
# is ordered by definition (task 2 pins this with a test).
SEVERITY_RANK = {name: rank for rank, name in enumerate(JudgeVerdict.Severity.values)}


def verdict_for_severity(max_severity: str) -> str:
    """Derive the verdict from the worst error the judge reported."""
    return _SEVERITY_VERDICT[max_severity]


def state_for_verdict(
    verdict: str, *, enable_review: bool, may_approve: bool
) -> int | None:
    """Target state for a verdict, or None when the state must not move.

    ``critical`` lands on STATE_FUZZY, which the project-level
    ``WITHOUT_NEEDS_EDITING`` commit policy already excludes from export.
    ``pass`` stops at STATE_TRANSLATED unless the site opts into judge
    approval (JUDGE_MAY_APPROVE) AND the project has review: measurement
    shows pass misses real criticals, so the judge does not hand out the
    top trust state by default (review D2).
    """
    if verdict == JudgeVerdict.Verdict.UNPARSED:
        return None
    if verdict == JudgeVerdict.Verdict.REJECT:
        return STATE_FUZZY
    if verdict == JudgeVerdict.Verdict.PASS and enable_review and may_approve:
        return STATE_APPROVED
    return STATE_TRANSLATED
```

Добавить свойство `verdict` в тело класса `JudgeVerdict` (рядом с `__str__`):

```python
    @property
    def verdict(self) -> str:
        """Derived, never stored: the severity->verdict mapping is
        reopened by R3 and must change without a data migration (D4)."""
        if self.unparsed:
            return self.Verdict.UNPARSED
        return verdict_for_severity(self.max_severity)
```

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge.py`

Expected: PASS (12 тестов)

### Step 5: Проверить, что тесты ловят баг

Временно поменять местами `MINOR` и `MAJOR` в `class Severity`. Прогнать.

Expected: FAIL на `test_severity_rank_is_ordered_by_strictness`. Вернуть код. Этот шаг
обязателен: денежная чистая функция без мутационной проверки не защищена.

### Step 6: Коммит

```bash
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge.py
git commit -m "feat(judge): map severity to the native shipping gate, verdict derived"
```

---

## Задача 3. Читатель вердикта: круг, коллегия, улики

Это слой, который читают и судейский чек (задача 4), и карточка юнита (задача 11), и
петля починки (задача 6). Живёт в том же модуле модели, чтобы не плодить импортов.

Ключевое отличие от первой редакции (решения D1, D5):

- **Нет отдельной проекции.** `Check`-строки пишет `run_checks` через чек (задача 4).
- **`active_round` фильтрует по `target_hash` текущего текста** — протухание встроено,
  отдельного `is_stale`-ветвления в проекции нет.
- **`unparsed`-круг никогда не активен**: если новейший круг для текущего текста весь
  `unparsed`, `active_round` берёт новейший круг с разобранным местом. Так транспортный
  сбой не гасит прошлый валидный вердикт.

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Test: `weblate/trans/tests/test_judge_round.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_round.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from weblate.trans.models.judge import (
    JudgeVerdict,
    active_round,
    active_verdict,
    collegium_verdict,
    compute_target_hash,
    describe_latest_verdict,
    latest_round,
)
from weblate.trans.tests.test_views import ViewTestCase


class JudgeRoundTest(ViewTestCase):
    def make(self, unit, max_severity, *, unparsed=False, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "ctx")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        return JudgeVerdict.objects.create(
            unit=unit, max_severity=max_severity, unparsed=unparsed, **kwargs
        )

    def test_collegium_takes_the_strictest_seat(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "major", seat=1, run_id=run)
        self.make(unit, "critical", seat=2, run_id=run)
        self.assertEqual(active_verdict(unit).verdict, JudgeVerdict.Verdict.REJECT)

    def test_no_seat_may_lower_the_other(self) -> None:
        # Seat 2 passing must not clear seat 1's flag: the cascade B2'
        # rejected, arriving through the collegium read instead.
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "major", seat=1, run_id=run)
        self.make(unit, "none", seat=2, run_id=run)
        v = active_verdict(unit)
        self.assertEqual(v.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(v.seat, 1)

    def test_a_parsed_seat_outvotes_an_unparsed_one(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=run, unparsed=True)
        self.make(unit, "critical", seat=2, run_id=run)
        self.assertEqual(active_verdict(unit).verdict, JudgeVerdict.Verdict.REJECT)

    def test_an_all_unparsed_round_keeps_the_previous_verdict(self) -> None:
        # D5: a transport-dead re-judge must not erase the last real verdict
        # of unchanged text.
        unit = self.get_unit()
        old = uuid.uuid4()
        self.make(unit, "critical", seat=1, run_id=old)
        self.make(unit, "critical", seat=2, run_id=old)
        new = uuid.uuid4()
        self.make(unit, "none", seat=1, run_id=new, unparsed=True)
        self.make(unit, "none", seat=2, run_id=new, unparsed=True)
        self.assertEqual(active_verdict(unit).verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_verdict_for_other_text_is_not_active(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical", target_hash="stale-hash-matches-nothing")
        self.assertIsNone(active_verdict(unit))
        # ...but latest_round still sees it, for the "previous version" note.
        self.assertEqual(len(latest_round(unit)), 1)
        self.assertTrue(latest_round(unit)[0].is_stale(unit.get_target_plurals()))

    def test_all_unparsed_and_no_prior_verdict_is_none(self) -> None:
        unit = self.get_unit()
        self.make(unit, "none", unparsed=True)
        self.assertIsNone(active_verdict(unit))
        self.assertEqual(active_round(unit), [])

    def test_description_merges_both_seats(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.make(
            unit, "critical", seat=1, run_id=run,
            errors=[{"span": "ВРАТА", "category": "terminology", "severity": "critical",
                     "description": "the Gates are called DOORS here"}],
        )
        self.make(
            unit, "major", seat=2, run_id=run,
            errors=[{"span": "clause", "category": "fluency", "severity": "major",
                     "description": "the second clause has no verb"}],
        )
        description = describe_latest_verdict(unit)
        self.assertIn("the Gates are called DOORS here", description)
        self.assertIn("the second clause has no verb", description)

    def test_description_escapes_markup_and_separates_errors(self) -> None:
        # Q1: llm.py strips tags; the evidence must survive as escaped text
        # and errors must stay distinguishable after normalization.
        unit = self.get_unit()
        self.make(
            unit, "major",
            errors=[
                {"span": "a", "category": "markup", "severity": "major",
                 "description": "target dropped <color=#FF0000>"},
                {"span": "b", "category": "fluency", "severity": "major",
                 "description": "register too formal"},
            ],
        )
        description = describe_latest_verdict(unit)
        self.assertIn("color=#FF0000", description)  # not eaten by strip_tags
        self.assertIn(" | ", description)            # explicit separator
```

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py`

Expected: FAIL — `ImportError: cannot import name 'active_round'`

### Step 3: Реализовать

Добавить в `weblate/trans/models/judge.py`:

```python
from django.utils.html import escape

if TYPE_CHECKING:
    from weblate.trans.models.unit import Unit

JUDGE_ERROR_SEPARATOR = " | "


def latest_round(unit: Unit) -> list[JudgeVerdict]:
    """Every seat of the newest round, stale or not — for the card's
    'previous version' note. Not for projection."""
    newest = unit.judge_verdicts.order_by("-timestamp", "-pk").first()
    if newest is None:
        return []
    return list(
        unit.judge_verdicts.filter(
            run_id=newest.run_id, attempt=newest.attempt
        ).order_by("seat")
    )


def active_round(unit: Unit) -> list[JudgeVerdict]:
    """Newest round that describes the current text and has a parsed seat.

    Staleness is handled by filtering on target_hash. An all-unparsed
    newest round is skipped in favour of the newest parsed one, so a
    transport failure never erases the last real verdict (D5).
    """
    current = compute_target_hash(unit.get_target_plurals())
    newest = (
        unit.judge_verdicts.filter(target_hash=current)
        .order_by("-timestamp", "-pk")
        .first()
    )
    if newest is None:
        return []
    rows = list(
        unit.judge_verdicts.filter(
            target_hash=current, run_id=newest.run_id, attempt=newest.attempt
        ).order_by("seat")
    )
    if any(not row.unparsed for row in rows):
        return rows
    parsed = (
        unit.judge_verdicts.filter(target_hash=current, unparsed=False)
        .order_by("-timestamp", "-pk")
        .first()
    )
    if parsed is None:
        return []
    return list(
        unit.judge_verdicts.filter(
            target_hash=current, run_id=parsed.run_id, attempt=parsed.attempt
        ).order_by("seat")
    )


def collegium_verdict(rows: Sequence[JudgeVerdict]) -> JudgeVerdict | None:
    """The strictest opinion of a round. No seat may lower another.

    A transport failure is not an opinion, so an unparsed row neither
    raises nor lowers the round; only when every seat failed does the
    round read as unparsed.
    """
    if not rows:
        return None
    parsed = [row for row in rows if not row.unparsed]
    if not parsed:
        return rows[0]
    return max(parsed, key=lambda row: (SEVERITY_RANK[row.max_severity], -row.seat))


def active_verdict(unit: Unit) -> JudgeVerdict | None:
    """The collegium verdict that still describes the stored text."""
    return collegium_verdict(active_round(unit))


def describe_latest_verdict(unit: Unit) -> str:
    """Human-readable evidence for the active round, or an empty string.

    Rendered into the check description, which weblate/machinery/llm.py
    feeds to the translator as failing_checks during repair. Both seats
    are merged. Descriptions are escaped and joined with an explicit
    separator: llm.py runs the text through strip_tags().split(), which
    would otherwise eat game markup like <color=#RRGGBB> and collapse
    newlines into one blob (review Q1).
    """
    lines: list[str] = []
    for row in active_round(unit):
        for error in row.errors:
            line = "{}/{}: {}".format(
                error.get("severity", "unspecified"),
                error.get("category", "unspecified"),
                escape(error.get("description", "")),
            )
            if line not in lines:
                lines.append(line)
    return JUDGE_ERROR_SEPARATOR.join(lines)
```

Проверь фактическое имя метода получения плюралов у `Unit` (`get_target_plurals`) и
поправь, если отличается.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_round.py`

Expected: PASS (9 тестов)

### Step 5: Убедиться, что тесты ловят баги

1. В `active_round` убрать пропуск all-unparsed круга (вернуть `rows` без проверки
   `any(...)`). Прогнать. Expected: FAIL на
   `test_an_all_unparsed_round_keeps_the_previous_verdict`. Вернуть.
2. В `describe_latest_verdict` заменить `escape(...)` на голое значение и
   `JUDGE_ERROR_SEPARATOR` на `"\n"`. Прогнать. Expected: FAIL на
   `test_description_escapes_markup_and_separates_errors` **после** прогона через
   `_normalize_check_text` (проверяется в задаче 4, Step 5 — тут только escape/separator).
   Вернуть.

### Step 6: Коммит

```bash
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge_round.py
git commit -m "feat(judge): read the collegium verdict of the current text"
```

---

## Задача 4. Судейские чеки: строка как производная вердикта

Ядро решения D1. Чек **не заполняется извне** — он читает активный вердикт. Так
`run_checks` создаёт и удаляет судейские строки штатно, они не могут разойтись с
`JudgeVerdict`, а протухание бесплатно. Чеки живут в **ядре** (`weblate/checks/judge.py`),
а не в `weblate_customization`: имя чека — единственный источник истины (находка Q2), и
регистрация через `DEFAULT_CHECK_LIST` не требует env-переменных и пересборки контейнера.

**Files:**

- Create: `weblate/checks/judge.py`
- Modify: `weblate/checks/defaults.py` (добавить два класса в `DEFAULT_CHECK_LIST`)
- Test: `weblate/checks/tests/test_judge.py`

### Step 1: Написать падающий тест

Create `weblate/checks/tests/test_judge.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from weblate.checks.judge import JUDGE_CHECKS, JudgeFlagCheck, JudgeRejectCheck
from weblate.checks.models import CHECKS, Check
from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.tests.test_views import ViewTestCase


class JudgeCheckTest(ViewTestCase):
    def make(self, unit, max_severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        return JudgeVerdict.objects.create(
            unit=unit, max_severity=max_severity, **kwargs
        )

    def test_checks_are_registered_and_not_enforceable_by_default(self) -> None:
        self.assertIn("judge-flag", CHECKS)
        self.assertIn("judge-reject", CHECKS)
        self.assertEqual(JUDGE_CHECKS, frozenset({"judge-flag", "judge-reject"}))
        for check in (JudgeFlagCheck(), JudgeRejectCheck()):
            self.assertFalse(check.default_disabled)

    def test_reject_verdict_makes_run_checks_create_the_row(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical")  # verdict property -> reject
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-reject", unit.all_checks_names)
        self.assertNotIn("judge-flag", unit.all_checks_names)

    def test_a_pass_verdict_leaves_no_judge_row(self) -> None:
        unit = self.get_unit()
        self.make(unit, "none")
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertFalseSubset = unit.all_checks_names & JUDGE_CHECKS
        self.assertEqual(unit.all_checks_names & JUDGE_CHECKS, set())

    def test_the_projected_row_survives_a_second_run_checks(self) -> None:
        # THE regression this whole design exists for: run_checks must
        # not delete the judge row it did not compute here (review A1/D1).
        unit = self.get_unit()
        self.make(unit, "critical")
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-reject", unit.all_checks_names)
        # A translator edit, a component recheck, `updatechecks` — all of
        # these call run_checks again. The row must still be there.
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("judge-reject", unit.all_checks_names)

    def test_a_new_verdict_replaces_the_row(self) -> None:
        unit = self.get_unit()
        self.make(unit, "critical", run_id=uuid.uuid4())
        unit.run_checks()
        self.make(unit, "major", run_id=uuid.uuid4())
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertEqual(unit.all_checks_names & JUDGE_CHECKS, {"judge-flag"})

    def test_description_carries_escaped_evidence(self) -> None:
        unit = self.get_unit()
        self.make(
            unit, "critical",
            errors=[{"span": "x", "category": "terminology", "severity": "critical",
                     "description": "the Gates are called DOORS here"}],
        )
        Check.objects.filter(unit=unit).delete()
        unit.run_checks()
        unit.clear_checks_cache()
        row = Check.objects.get(unit=unit, name="judge-reject")
        self.assertIn("DOORS", row.get_description())
```

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/checks/tests/test_judge.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'weblate.checks.judge'`

### Step 3: Реализовать классы

Create `weblate/checks/judge.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Judge checks: a Check row derived from the latest JudgeVerdict.

The check never computes anything itself — it reads the active verdict
(weblate.trans.models.judge.active_verdict). That keeps Unit.run_checks
the single writer of judge-* rows, so they cannot diverge from the
verdict and staleness is free (a stale verdict yields no active round,
so run_checks removes the row). The trans-model import is local: this
module is loaded at app start via CHECK_LIST, before models are ready.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy

from weblate.checks.base import TargetCheck

if TYPE_CHECKING:
    from weblate.checks.models import Check
    from weblate.trans.models.unit import Unit

_ROUND_CACHE_KEY = "judge:active-verdict"


class BaseJudgeCheck(TargetCheck):
    """A verdict projected from JudgeVerdict, never computed here."""

    default_disabled = False
    # The verdict this subclass renders as a failing check.
    judge_verdict: str = ""

    def _active_verdict(self, unit: Unit):
        # One fetch per unit per run_checks pass, shared by both judge
        # classes (unit.check_cache is reset per unit — base.py).
        cache = unit.check_cache
        if _ROUND_CACHE_KEY not in cache:
            from weblate.trans.models.judge import active_verdict

            cache[_ROUND_CACHE_KEY] = active_verdict(unit)
        return cache[_ROUND_CACHE_KEY]

    def check_target_unit(self, sources, targets, unit) -> bool:
        verdict = self._active_verdict(unit)
        return verdict is not None and verdict.verdict == self.judge_verdict

    def check_single(self, source: str, target: str, unit) -> bool:
        # Never used: check_target_unit is overridden. Present so the
        # abstract base contract is satisfied.
        return False

    def get_description(self, check_obj: Check) -> str:
        """Render the active verdict's errors for the repair prompt.

        weblate/machinery/llm.py calls get_description() when it builds
        failing_checks for the translator.
        """
        from weblate.trans.models.judge import describe_latest_verdict

        return describe_latest_verdict(check_obj.unit) or self.description


class JudgeFlagCheck(BaseJudgeCheck):
    check_id = "judge-flag"
    judge_verdict = "flag"
    name = gettext_lazy("Judge: questionable")
    description = gettext_lazy(
        "An LLM judge reported a major problem. The string still ships."
    )


class JudgeRejectCheck(BaseJudgeCheck):
    check_id = "judge-reject"
    judge_verdict = "reject"
    name = gettext_lazy("Judge: rejected")
    description = gettext_lazy(
        "An LLM judge reported a critical problem. The string does not ship."
    )


JUDGE_CHECKS = frozenset({JudgeFlagCheck.check_id, JudgeRejectCheck.check_id})
```

Добавить в `weblate/checks/defaults.py`, в конец кортежа `DEFAULT_CHECK_LIST`:

```python
    "weblate.checks.judge.JudgeFlagCheck",
    "weblate.checks.judge.JudgeRejectCheck",
```

Регистрация через `DEFAULT_CHECK_LIST` означает: env-переменные `WEBLATE_ADD_CHECK` и
правку `docker-compose.yml` делать **не надо** — чеки в ядре, доезжают Granian-релоадом.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/checks/tests/test_judge.py`

Expected: PASS (6 тестов). `verdict` — это property, поэтому `active_verdict(...).verdict`
работает без хранимого поля.

### Step 5: Убедиться, что регрессионный тест ловит баг

Это самый важный шаг плана. Временно заставить чек вести себя как «внешне заполняемый»
(вернуть `False` из `check_target_unit` всегда). Прогнать.

Expected: FAIL на `test_reject_verdict_makes_run_checks_create_the_row` и
`test_the_projected_row_survives_a_second_run_checks`. Вернуть код. Тест, который не падает
на возврате старого поведения, ничего не защищает — а старое поведение (внешняя проекция)
и было багом P0.

### Step 6: Коммит

```bash
git add weblate/checks/judge.py weblate/checks/defaults.py \
  weblate/checks/tests/test_judge.py
git commit -m "feat(judge): derive judge check rows from the verdict via run_checks"
```

---

## Задача 5. Настройки судьи и клиент OpenRouter (плечо D, батчи)

Конфигурация сайтовая, выключена по умолчанию — по образцу `LOC_KIT_PROFILE_OPENROUTER_*`.
Контракт запроса/ответа **байт-в-байт совпадает с измеренным плечом D** (решение D6):
батчи по N сегментов, `span` строкой, `verdict` per segment от модели, `render_preview`.
Единственное осознанное отклонение — поле `back_translation` для карточки продюсера;
оно помечено как **неизмеренное** (лишнее выходное поле, риск на метрики минимальный).

**Files:**

- Modify: `weblate/trans/defaults.py` (рядом с блоком `LOC_KIT_PROFILE_*`)
- Modify: `weblate/trans/models/_conf.py` (рядом со строками `LOC_KIT_PROFILE_*`)
- Modify: `weblate/settings_docker.py` (по образцу блока `LOC_KIT_PROFILE_*`, префикс `WEBLATE_`)
- Modify: `weblate/settings_example.py` (по образцу блока `LOC_KIT_PROFILE_*`)
- Create: `weblate/trans/judge.py`
- Create: `weblate/trans/judge_prompts/verdict.txt`
- Test: `weblate/trans/tests/test_judge_client.py`

### Step 1: Объявить настройки

`weblate/trans/defaults.py` — рядом с блоком `LOC_KIT_PROFILE_*`:

```python
# LLM judge (off by default; site-wide, like LOC_KIT_PROFILE_*)
DEFAULT_JUDGE_ENABLED = False
DEFAULT_JUDGE_OPENROUTER_KEY = ""
DEFAULT_JUDGE_MODEL_SEAT_1 = ""
DEFAULT_JUDGE_MODEL_SEAT_2 = ""
DEFAULT_JUDGE_MAX_REPAIR_ATTEMPTS = 1
DEFAULT_JUDGE_BATCH_SIZE = 5
DEFAULT_JUDGE_MAX_UNITS_PER_RUN = 2000
DEFAULT_JUDGE_REQUEST_SLEEP = 0.0
DEFAULT_JUDGE_MAY_APPROVE = False
```

`weblate/trans/models/_conf.py` — рядом со строками `LOC_KIT_PROFILE_*`:

```text
    JUDGE_ENABLED = defaults.DEFAULT_JUDGE_ENABLED
    JUDGE_OPENROUTER_KEY = defaults.DEFAULT_JUDGE_OPENROUTER_KEY
    JUDGE_MODEL_SEAT_1 = defaults.DEFAULT_JUDGE_MODEL_SEAT_1
    JUDGE_MODEL_SEAT_2 = defaults.DEFAULT_JUDGE_MODEL_SEAT_2
    JUDGE_MAX_REPAIR_ATTEMPTS = defaults.DEFAULT_JUDGE_MAX_REPAIR_ATTEMPTS
    JUDGE_BATCH_SIZE = defaults.DEFAULT_JUDGE_BATCH_SIZE
    JUDGE_MAX_UNITS_PER_RUN = defaults.DEFAULT_JUDGE_MAX_UNITS_PER_RUN
    JUDGE_REQUEST_SLEEP = defaults.DEFAULT_JUDGE_REQUEST_SLEEP
    JUDGE_MAY_APPROVE = defaults.DEFAULT_JUDGE_MAY_APPROVE
```

`weblate/settings_docker.py` — по образцу `LOC_KIT_PROFILE_*`, через
`get_env_bool` / `get_env_str` / `get_env_int` (и `get_env_float` для `SLEEP`, если есть;
иначе `float(get_env_str(...))`), префикс `WEBLATE_`.

`weblate/settings_example.py` — по образцу `LOC_KIT_PROFILE_*`.

### Step 2: Написать падающий тест

Create `weblate/trans/tests/test_judge_client.py`. Контракт запроса проверяем против
формы измеренного драйвера (`analysis/probes/st2-zh-recalibration.py`: `build_payload`,
`reply_schema`, `render_segment`).

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json

from django.test import SimpleTestCase, override_settings

from weblate.trans.judge import (
    JudgeError,
    JudgeRequest,
    render_preview,
    request_verdicts,
)
from weblate.utils.tests import http_mock

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

REQ = JudgeRequest(
    unit_key="MENU_DOOR",
    source="Дверь заблокирована ГЕРМОДВЕРЬМИ",
    target="La porte est bloquée par les PORTES",
    source_language="ru",
    target_language="fr",
    note="",
    glossary_terms=[("ГЕРМОДВЕРЬ", "porte blindée")],
    failing_checks=[],
)


def _reply(segments: list[dict]) -> dict:
    content = json.dumps({"segments": segments})
    return {"choices": [{"message": {"content": content}}]}


class JudgeClientGateTest(SimpleTestCase):
    @override_settings(JUDGE_ENABLED=False)
    @http_mock.activate
    def test_disabled_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY="")
    @http_mock.activate
    def test_missing_key_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test-do-not-leak",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeClientTest(SimpleTestCase):
    @http_mock.activate
    def test_parses_a_verdict(self) -> None:
        http_mock.register("POST", CHAT_URL, json=_reply([{
            "id": 0, "verdict": "reject",
            "errors": [{"span": "PORTES", "category": "terminology",
                        "severity": "critical",
                        "description": "«ВРАТА» rendered as «DOORS»; glossary says Gates"}],
            "back_translation": "The door is blocked by the DOORS",
        }]))
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(result.max_severity, "critical")   # derived from errors
        self.assertEqual(result.model_verdict, "reject")
        self.assertIn("Gates", result.errors[0]["description"])
        self.assertIn("DOORS", result.back_translation)

    @http_mock.activate
    def test_max_severity_is_derived_from_the_worst_error(self) -> None:
        http_mock.register("POST", CHAT_URL, json=_reply([{
            "id": 0, "verdict": "flag",
            "errors": [
                {"span": "a", "category": "fluency", "severity": "minor", "description": "x"},
                {"span": "b", "category": "style", "severity": "major", "description": "y"},
            ],
            "back_translation": "",
        }]))
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(result.max_severity, "major")

    @http_mock.activate
    def test_no_errors_is_severity_none(self) -> None:
        http_mock.register("POST", CHAT_URL, json=_reply([{
            "id": 0, "verdict": "pass", "errors": [], "back_translation": "",
        }]))
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(result.max_severity, "none")

    @http_mock.activate
    def test_batches_many_requests_and_keeps_order(self) -> None:
        # D6: one HTTP call per batch of JUDGE_BATCH_SIZE, results aligned
        # to input order by segment id.
        reqs = [REQ, REQ, REQ]
        http_mock.register("POST", CHAT_URL, json=_reply([
            {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""},
            {"id": 1, "verdict": "reject",
             "errors": [{"span": "x", "category": "omission", "severity": "critical",
                         "description": "z"}], "back_translation": ""},
            {"id": 2, "verdict": "pass", "errors": [], "back_translation": ""},
        ]))
        results = request_verdicts(reqs, model="vendor/model-a")
        self.assertEqual([r.max_severity for r in results], ["none", "critical", "none"])
        self.assertEqual(len(http_mock.calls), 1)

    @http_mock.activate
    def test_sends_strict_schema_batch_and_requires_providers_to_honour_it(self) -> None:
        http_mock.register("POST", CHAT_URL, json=_reply([
            {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]))
        request_verdicts([REQ], model="vendor/model-a")
        body = json.loads(http_mock.calls[0].request.content)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertTrue(body["provider"]["require_parameters"])
        self.assertEqual(body["model"], "vendor/model-a")
        user_msg = json.loads(body["messages"][1]["content"])
        self.assertIn("segments", user_msg)

    @http_mock.activate
    def test_render_preview_is_attached_when_placeholders_are_present(self) -> None:
        # Arm D: rendered pair goes into the segment (user precondition).
        req = JudgeRequest(
            unit_key="K", source="{0} 个国家 {1}", target="{1} 的 {0}",
            source_language="ru", target_language="zh_Hans", note="",
            glossary_terms=[], failing_checks=[],
        )
        http_mock.register("POST", CHAT_URL, json=_reply([
            {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]))
        request_verdicts([req], model="vendor/model-a")
        segment = json.loads(http_mock.calls[0].request.content)["segments"][0]
        self.assertIn("rendered_source", segment)
        self.assertIn("rendered_target", segment)

    def test_render_preview_returns_none_without_placeholders(self) -> None:
        self.assertIsNone(render_preview("plain text"))
        self.assertIsNotNone(render_preview("has {0} slot"))

    @http_mock.activate
    def test_malformed_json_makes_the_batch_unparsed(self) -> None:
        http_mock.register("POST", CHAT_URL,
                           json={"choices": [{"message": {"content": "not json"}}]})
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)

    @http_mock.activate
    def test_http_error_makes_the_batch_unparsed(self) -> None:
        http_mock.register("POST", CHAT_URL, status=500, json={})
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)

    @http_mock.activate
    def test_the_api_key_never_reaches_the_exception_text(self) -> None:
        # A gate failure raises; the key must not be in the message.
        with override_settings(JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY=""):
            with self.assertRaises(JudgeError) as ctx:
                request_verdicts([REQ], model="vendor/model-a")
            self.assertNotIn("sk-test", str(ctx.exception))
```

Проверь точную сигнатуру `http_mock.register` по
`weblate/trans/tests/test_loc_kit_profile_suggester.py` и подгони вызовы.

### Step 3: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_client.py`

Expected: FAIL — `ModuleNotFoundError: weblate.trans.judge`

### Step 4: Написать промпт

Create `weblate/trans/judge_prompts/verdict.txt` — плечо D
(`SYSTEM_PROMPT` + `GLOSSARY_RULE` + `DESCRIPTION_RULE` + `RUBRIC_RULE` + `RENDER_RULE`
из `analysis/probes/st2-zh-recalibration.py:72-165`, скопировать дословно). Языковую пару и
жанр вынести в подставляемые поля системного промпта (`{source_language}`,
`{target_language}`) — измеренный промпт зашит на ru→zh_Hans, а продукту нужны 15 языков;
жанр оставить как есть (проектная деталь). **Отметить в комментарии файла:** обобщение
пары языков замером не покрыто и подлежит проверке на первом боевом прогоне.

### Step 5: Написать клиент

Create `weblate/trans/judge.py`. Формируй по образцу
`weblate/trans/loc_kit.py:397-525`: фиксированный хост, `strict: True`,
`provider.require_parameters`, `raise_for_status=False`, `follow_redirects=False`,
таймаут 120 с, единственный тип исключения, ключ не попадает ни в текст ошибки, ни во
фрейм-локал.

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""OpenRouter client for the LLM judge (measured arm D).

Separate from RoutedLLMTranslation on purpose: the judge is not a machine
translation service. Mirrors the loc-kit profile client
(weblate/trans/loc_kit.py): fixed host, strict schema, one exception
type, no user-supplied endpoint/key/model. Requests are batched
(JUDGE_BATCH_SIZE segments per HTTP call) exactly as the measurement was
run — the measured noise/precision/recall/cost numbers assume batching.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.translation import gettext as _

from weblate.trans.models.judge import SEVERITY_RANK
from weblate.utils.requests import fetch_validated_url

if TYPE_CHECKING:
    from collections.abc import Sequence

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
JUDGE_REQUEST_TIMEOUT = 120
SEVERITIES = ("none", "minor", "major", "critical")
# Measured category set (st2-zh-recalibration.py:59-68).
CATEGORIES = (
    "terminology", "mistranslation", "omission", "addition",
    "fluency", "punctuation", "markup", "register",
)
# Deterministic, order-revealing sample values (measured driver).
_SAMPLE_VALUES = ("3", "7", "15", "28", "42", "56", "64", "77")
_PLACEHOLDER_RE = re.compile(r"\{\[PARAM(\d+)\]\}|\{(\d+)\}|%([A-Za-z_]+)%")


class JudgeError(Exception):
    """A judge gate failure: disabled or misconfigured. Transport and
    parse failures do NOT raise — they yield an unparsed result so one
    bad batch never aborts a run (D5)."""


@dataclass(frozen=True)
class JudgeRequest:
    unit_key: str
    source: str
    target: str
    source_language: str
    target_language: str
    note: str
    glossary_terms: Sequence[tuple[str, str]]
    failing_checks: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class JudgeResult:
    max_severity: str
    model_verdict: str
    errors: list[dict]
    back_translation: str
    unparsed: bool = False


UNPARSED = JudgeResult(
    max_severity="none", model_verdict="", errors=[], back_translation="", unparsed=True
)


def render_preview(text: str) -> str | None:
    """Substitute sample values into engine placeholders; None if none."""
    def sub(match: re.Match[str]) -> str:
        param, plain, named = match.groups()
        if named is not None:
            return _SAMPLE_VALUES[sum(named.encode()) % len(_SAMPLE_VALUES)]
        return _SAMPLE_VALUES[int(param or plain) % len(_SAMPLE_VALUES)]

    rendered, count = _PLACEHOLDER_RE.subn(sub, text)
    return rendered if count else None
```

Дальше в модуле:

1. `_load_prompt()` — читает `judge_prompts/verdict.txt` через `importlib.resources`, как
   `load_profile_prompt` в `loc_kit.py`.
2. `_response_schema()` — строгая схема **измеренной формы** (плечо D):
   верх `{"segments": [segment]}`; `segment` = `{id:int, verdict:enum[pass,flag,reject],
   errors:[error], back_translation:string}` (все в `required`); `error` =
   `{span:string, category:enum[CATEGORIES], severity:enum[minor,major,critical],
   description:string}` (все в `required`, `additionalProperties:false`). `back_translation`
   — отклонение от измеренной схемы, помечено комментарием как неизмеренное поле.
3. `_segment(index, req)` — как `render_segment`: `{id, key, source, target}`; при наличии
   плейсхолдеров добавить `rendered_source` / `rendered_target` (арм D); при `note`,
   `glossary`, `failing_checks` — добавить их.
4. `_max_severity(errors)` — `max(errors по SEVERITY_RANK[severity])` или `"none"` при
   пустом списке. Так `max_severity` **выводится** из измеренного per-error `severity`,
   а не запрашивается отдельно (D6).
5. `request_verdicts(requests, *, model) -> list[JudgeResult]`:
   - Гейты до сети: `settings.JUDGE_ENABLED`, `settings.JUDGE_OPENROUTER_KEY`, непустой
     `model` — каждый поднимает `JudgeError` (единственный случай исключения).
   - Разбить `requests` на батчи по `settings.JUDGE_BATCH_SIZE`.
   - На каждый батч — один POST (`payload` с `model`, `stream:False`,
     `response_format.json_schema` (`name:"judge_verdicts"`, `strict:True`),
     `provider.require_parameters:True`, `messages` из системного промпта и
     `{"segments":[...]}`), заголовок с ключом собирать инлайн (как `loc_kit.py:453-456`).
   - Любой сбой батча (транспорт, `status>=400`, нечитаемый JSON, рассинхрон длины,
     неизвестная `severity`/`category`) → **весь батч = `[UNPARSED]*len(batch)`**, как в
     измеренном `judge_batch`. Не поднимать, не подставлять дефолтный вердикт.
   - Результаты вернуть в порядке входа, выровняв по `id` сегмента.
   - Между батчами — `time.sleep(settings.JUDGE_REQUEST_SLEEP)` (D5: троттлинг; замер
     `final:8-10` фиксирует 403-блок без него). Один ретрай на батч при `status==429/403`
     с удвоенным сном — но не более.
   - На каждый успешный разбор батча — записать `usage`-блок ответа в
     `LLMUsageLog` зеркалом `record_llm_usage`
     (`weblate/machinery/openai.py:120-158`: never-raises, токены и
     `cost` как биллинг OpenRouter, `response_id`). Судья — платный
     путь мимо машинерии; путь починки (`RoutedLLMTranslation` ←
     `OpenAITranslation`) логируется этим же механизмом, учёт обязан
     быть симметричным.

### Step 6: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_client.py`

Expected: PASS.

### Step 7: Убедиться, что тест ловит баг

Временно вернуть дефолтный вердикт вместо `UNPARSED` при сбое разбора. Прогнать.
Expected: FAIL на `test_malformed_json_makes_the_batch_unparsed`. Вернуть код.

### Step 8: Коммит

```bash
git add weblate/trans/judge.py weblate/trans/judge_prompts/ \
  weblate/trans/defaults.py weblate/trans/models/_conf.py \
  weblate/settings_docker.py weblate/settings_example.py \
  weblate/trans/tests/test_judge_client.py
git commit -m "feat(judge): add the batched OpenRouter judge client (arm D)"
```

---

## Задача 6. Коллегия и петля починки

Чистая оркестрация поверх клиента: без Django-форм, без Celery. Оба места судят **каждую**
строку батчами, вердикт круга — строгий из двух, понижать друг друга места не вправе.
Дальше петля починки до исчерпания попыток, затем очередь человека. Правки против первой
редакции: батч-ориентированность (D6), гейт починки по «записываемым» строкам (D3),
запись состояния сразу после починки (A10), транзакция вокруг круга (A8).

**Files:**

- Create: `weblate/trans/judge_loop.py`
- Test: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_loop.py`. Клиент подменяется целиком — сети нет.

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest import mock

from django.test import override_settings

from weblate.trans.judge import JudgeResult
from weblate.trans.judge_loop import run_judge_batch
from weblate.trans.models.judge import JudgeVerdict
from weblate.trans.tests.test_views import ViewTestCase


def result(severity, verdict, **kw):
    errs = [] if severity == "none" else [
        {"span": "x", "category": "terminology", "severity": severity, "description": "d"}
    ]
    return JudgeResult(max_severity=severity, model_verdict=verdict, errors=errs,
                       back_translation=kw.get("bt", ""), unparsed=kw.get("unparsed", False))

PASS = result("none", "pass")
MAJOR = result("major", "flag")
CRITICAL = result("critical", "reject")
DEAD = JudgeResult("none", "", [], "", unparsed=True)


@override_settings(
    JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model", JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
class JudgeLoopTest(ViewTestCase):
    def run_batch(self, seat_results, repair=None, writable=True):
        # seat_results: list of (list-per-seat-call) results, consumed in order.
        client = mock.Mock(side_effect=[[r] for r in seat_results])
        unit = self.get_unit()
        writable_ids = {unit.id} if writable else set()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_target", return_value=repair),
        ):
            verdicts = run_judge_batch([unit], writable_ids=writable_ids, user=self.user)
        return unit, verdicts[unit.id], client

    def test_both_seats_judge_every_string(self) -> None:
        unit, verdict, client = self.run_batch([PASS, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_no_seat_may_lower_the_other(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_the_same_holds_when_the_strict_seat_votes_second(self) -> None:
        _, verdict, _ = self.run_batch([PASS, MAJOR])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_verdict_takes_the_higher_severity(self) -> None:
        _, verdict, _ = self.run_batch([MAJOR, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_each_seat_uses_its_configured_model(self) -> None:
        _, _, client = self.run_batch([PASS, PASS])
        models = [c.kwargs["model"] for c in client.call_args_list]
        self.assertEqual(models, ["vendor-a/model", "vendor-b/model"])

    def test_unparsed_neither_raises_nor_lowers_the_other_seat(self) -> None:
        _, verdict, _ = self.run_batch([CRITICAL, DEAD], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_clean_seat_next_to_an_unparsed_one_still_passes(self) -> None:
        _, verdict, _ = self.run_batch([PASS, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)

    def test_unparsed_from_both_seats_is_unparsed(self) -> None:
        _, verdict, _ = self.run_batch([DEAD, DEAD])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.UNPARSED)

    def test_confirmed_defect_triggers_one_repair_judged_by_both_seats(self) -> None:
        unit, verdict, client = self.run_batch(
            [CRITICAL, CRITICAL, PASS, PASS], repair=["fixed text"])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(client.call_count, 4)

    def test_exhausted_loop_returns_the_last_negative_verdict(self) -> None:
        _, verdict, _ = self.run_batch(
            [CRITICAL, CRITICAL, CRITICAL, CRITICAL], repair=["still wrong"])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_repair_that_changes_nothing_stops_the_loop(self) -> None:
        _, verdict, client = self.run_batch([CRITICAL, CRITICAL], repair=None)
        self.assertEqual(client.call_count, 2)

    def test_a_human_string_is_not_repaired_when_not_writable(self) -> None:
        # D3/A3: overwrite off => the unit is not in writable_ids => a
        # false critical never rewrites the human translation.
        unit = self.get_unit()
        unit.translate(self.user, ["Human translation"], 20)
        _, verdict, client = self.run_batch(
            [CRITICAL, CRITICAL], repair=["MACHINE OVERWRITE"], writable=False)
        self.assertEqual(self.get_unit().target, "Human translation")

    def test_repair_sees_the_round_verdict_projected(self) -> None:
        # Ordering guard: run_checks() projects the round's Check row
        # before repair_target builds its prompt from failing_checks.
        seen = []

        def spy(unit, user):
            seen.append({c.name for c in unit.all_checks})
            return ["fixed text"]

        client = mock.Mock(side_effect=[[r] for r in (CRITICAL, CRITICAL, PASS, PASS)])
        unit = self.get_unit()
        with (
            mock.patch("weblate.trans.judge_loop.request_verdicts", client),
            mock.patch("weblate.trans.judge_loop.repair_target", side_effect=spy),
        ):
            run_judge_batch([unit], writable_ids={unit.id}, user=self.user)
        self.assertEqual(seen, [{"judge-reject"}])

    def test_every_verdict_of_one_run_shares_the_run_id(self) -> None:
        unit, _, _ = self.run_batch([CRITICAL, CRITICAL, PASS, PASS], repair=["fixed"])
        self.assertEqual(
            len(set(unit.judge_verdicts.values_list("run_id", flat=True))), 1)

    def test_each_seat_votes_once_per_round(self) -> None:
        unit, _, _ = self.run_batch([CRITICAL, CRITICAL, PASS, PASS], repair=["fixed"])
        self.assertEqual(
            set(unit.judge_verdicts.values_list("attempt", "seat")),
            {(0, 1), (0, 2), (1, 1), (1, 2)})
```

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py`

Expected: FAIL — `ModuleNotFoundError: weblate.trans.judge_loop`

### Step 3: Реализовать коллегию и петлю

Create `weblate/trans/judge_loop.py` с публичными `repair_target` и `run_judge_batch`.

`repair_target(unit, user) -> list[str] | None`:

- взять настройки машинерии проекта (`unit.translation.component.project.get_machinery_settings()`);
- взять движок `AutoForm.DEFAULT_ENGINE` (`"openrouter"`), если сконфигурирован, иначе `None`;
- вызвать `engine.translate(unit, user)` (`weblate/machinery/base.py`), взять лучший результат;
- вернуть `None`, если результата нет или текст совпадает с текущим.

Улики судьи в промпт подставлять **не надо**: судейская `Check`-строка записана
`run_checks` (задача 4), а `weblate/machinery/llm.py` кладёт её в `failing_checks` вместе с
`get_description()`. Отсюда жёсткий порядок круга: `unit.run_checks()` обязан отработать
**до** `repair_target`.

`run_judge_batch(units, *, writable_ids, user) -> dict[int, JudgeVerdict]`:

```text
run_id = uuid4()
pending = list(units)
verdicts = {}
for attempt in 0..JUDGE_MAX_REPAIR_ATTEMPTS:      # inclusive
    for seat, model in ((1, SEAT_1), (2, SEAT_2)):
        requests = [build_request(u) for u in pending]
        results  = request_verdicts(requests, model=model)   # batched inside
        with transaction.atomic():                            # A8
            for u, r in zip(pending, results):
                write JudgeVerdict(u, r, seat=seat, attempt=attempt, run_id=run_id)
    next_pending = []
    for u in pending:
        u.clear_checks_cache(); u.check_cache.clear()
        u.run_checks()                # projects the round's Check row (task 4)
        v = active_verdict(u)
        verdicts[u.id] = v
        if v is None or v.verdict in (PASS, UNPARSED):
            continue
        if attempt == JUDGE_MAX_REPAIR_ATTEMPTS:
            continue
        if u.id not in writable_ids:  # D3/A3: never rewrite a human string
            continue
        new_target = repair_target(u, user)
        if new_target is None:
            continue
        u.translate(user, new_target, fresh_state)   # A10: state written now,
                                                      # not left to the caller
        next_pending.append(u)
    pending = next_pending
    if not pending:
        break
return verdicts
```

`fresh_state` — `STATE_FUZZY` (10): починенная, но не пересуженная строка не должна стоять
в отгружаемом состоянии. Оба вызова места безусловны; ни один `if` между ними не
появляется. `build_request` собирает `JudgeRequest` из `unit.source`, `unit.target`, кодов
языков, `note`, глоссария (`weblate.glossary.models.get_glossary_terms(unit)`) и имён
активных чеков; `JudgeError` из клиента невозможен внутри (клиент возвращает `UNPARSED`,
не бросает) — гейты проверяются вызывающим кодом до батча. Внутренняя запись
`JudgeVerdict` кладёт `max_severity`, `unparsed`, `model_verdict`, `errors`,
`back_translation`, `target_hash`, `context_hash`, `run_id`, `seat`, `attempt`,
`judge_model`.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py`

Expected: PASS (15 тестов)

### Step 5: Проверить, что тесты ловят баги

1. Дать месту 2 право снимать флаг: после первого места добавить
   `if rows_seat2 is PASS: return PASS`. Expected: FAIL на `test_no_seat_may_lower_the_other`
   и `test_the_same_holds_when_the_strict_seat_votes_second`. Вернуть.
2. Убрать гейт `writable_ids` у починки. Expected: FAIL на
   `test_a_human_string_is_not_repaired_when_not_writable`. Вернуть. Это баг A3.
3. Передать одну и ту же модель на оба места. Expected: PASS — одинаковые модели валидны.
4. Поменять порядок в круге: `repair_target` до `u.run_checks()`. Expected: FAIL на
   новом тесте `test_repair_sees_the_round_verdict_projected` — он проверяет, что в момент
   вызова мока `repair_target` судейская `Check`-строка круга уже записана на юните
   (улики едут в промпт через `failing_checks`, задача 3). Вернуть.

### Step 6: Коммит

```bash
git add weblate/trans/judge_loop.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): judge every string with a two-seat collegium and repair"
```

---

## Задача 7. Режим `judge` в форме автоперевода

Решение D3: чекбокс перезаписи гейтит и предперевод, и починку. Находка Q9: поле
`overwrite_existing` осмысленно только в режиме `judge` — валидируется только там.

**Files:**

- Modify: `weblate/trans/forms.py` (`AutoForm`: поле рядом с `threshold`, choices, layout, `clean`)
- Test: `weblate/trans/tests/test_judge_form.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_form.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from weblate.trans.forms import AutoForm
from weblate.trans.tests.test_views import ViewTestCase


class JudgeAutoFormTest(ViewTestCase):
    def modes(self, user):
        return [c[0] for c in AutoForm(obj=self.component, user=user).fields["mode"].choices]

    def test_judge_mode_requires_review_permission(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        self.assertIn("judge", self.modes(self.user))

    def test_judge_mode_hidden_without_review_permission(self) -> None:
        self.user.is_superuser = False
        self.user.save()
        self.assertNotIn("judge", self.modes(self.user))

    def test_overwrite_checkbox_defaults_to_off(self) -> None:
        form = AutoForm(obj=self.component, user=self.user)
        self.assertFalse(form.fields["overwrite_existing"].initial)
        self.assertFalse(form.fields["overwrite_existing"].required)

    def test_overwrite_is_rejected_outside_judge_mode(self) -> None:
        # Q9: the checkbox is meaningless for translate/fuzzy/approved.
        self.user.is_superuser = True
        self.user.save()
        form = AutoForm(
            obj=self.component, user=self.user,
            data={"mode": "translate", "auto_source": "mt",
                  "engines": [], "threshold": 80, "overwrite_existing": True},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("overwrite_existing", form.errors)
```

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_form.py`

Expected: FAIL — `judge` отсутствует в choices

### Step 3: Реализовать

Modify `weblate/trans/forms.py`. Поле рядом с `threshold` (~строка 1225):

```python
    overwrite_existing = forms.BooleanField(
        label=gettext_lazy("Overwrite the existing translation"),
        required=False,
        initial=False,
        help_text=gettext_lazy(
            "By default the judge mode only translates empty strings and "
            "strings marked for editing; strings that already have a "
            "translation are judged, not rewritten."
        ),
    )
```

Расширить блок choices (там же, где `approved`, — он ставит state 30 и требует
`unit.review`):

```python
        if user is not None and (user.has_perm("unit.review", obj) or obj is None):
            choices.append(("approved", gettext("Add as approved translation")))
            choices.append(("judge", gettext("Add as translation with an LLM judge")))
```

Добавить `Field("overwrite_existing")` в layout после `SearchField("q")`. В `clean`:

```python
        if cleaned.get("overwrite_existing") and cleaned.get("mode") != "judge":
            self.add_error(
                "overwrite_existing",
                gettext("Overwrite applies only to the LLM judge mode."),
            )
```

**Не менять** `self.helper = FormHelper(self)` и не добавлять `form_tag`: эта форма
рендерится тегом `{% crispy %}` штатно.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_form.py`

Expected: PASS (4 теста)

### Step 5: Коммит

```bash
git add weblate/trans/forms.py weblate/trans/tests/test_judge_form.py
git commit -m "feat(judge): add the judge mode and overwrite switch to AutoForm"
```

---

## Задача 8. `AutoTranslate.process_judge`: предперевод + суд + состояние по вердикту

Ключевое отличие режима: у существующих `target_state` — константа на прогон, у судейского
состояние определяется вердиктом каждой строки. Предперевод **переиспользует** нативный
`process_mt` (находка Q4), ограниченный «записываемыми» строками (D3). Жёсткий потолок
строк на прогон (A9), fail-safe state 10 (дизайн, инвариант 2).

**Files:**

- Modify: `weblate/trans/autotranslate.py` (`AutoTranslate.__init__`, `process_judge`, `perform`)
- Test: `weblate/trans/tests/test_judge_autotranslate.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_autotranslate.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest import mock

from django.test import override_settings

from weblate.trans.autotranslate import AutoTranslate
from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_FUZZY, STATE_TRANSLATED


@override_settings(
    JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model", JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_UNITS_PER_RUN=2000,
)
class JudgeAutoTranslateTest(ViewTestCase):
    def perform(self, verdict_kind, *, severity="none", q="", overwrite=False):
        def fake_batch(units, *, writable_ids, user):
            out = {}
            for u in units:
                out[u.id] = JudgeVerdict.objects.create(
                    unit=u, max_severity=severity, model_verdict=verdict_kind,
                    unparsed=(verdict_kind == JudgeVerdict.Verdict.UNPARSED),
                    judge_model="vendor-a/model", seat=1,
                    target_hash=compute_target_hash(u.get_target_plurals()),
                    context_hash="c")
            return out

        auto = AutoTranslate(translation=self.get_translation(), user=self.user,
                             q=q, mode="judge", overwrite_existing=overwrite)
        with mock.patch("weblate.trans.autotranslate.run_judge_batch",
                        side_effect=fake_batch):
            auto.process_judge(engines=[], threshold=80)
        return auto

    def test_reject_lands_on_a_state_that_does_not_ship(self) -> None:
        from weblate.utils.state import FUZZY_STATES
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.REJECT, severity="critical")
        self.assertIn(self.get_unit().state, FUZZY_STATES)

    def test_flag_ships_but_is_not_approved(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.FLAG, severity="major")
        self.assertEqual(self.get_unit().state, STATE_TRANSLATED)

    def test_unparsed_leaves_the_state_untouched(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["some target"], STATE_TRANSLATED)
        before = self.get_unit().state
        self.perform(JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(self.get_unit().state, before)

    def test_existing_translation_is_judged_not_rewritten(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Human translation"], STATE_TRANSLATED)
        # No overwrite: process_mt must not run on this unit.
        with mock.patch.object(AutoTranslate, "process_mt") as mt:
            self.perform(JudgeVerdict.Verdict.PASS, q="")
        self.assertEqual(self.get_unit().target, "Human translation")

    def test_fresh_translation_starts_at_needs_editing(self) -> None:
        auto = AutoTranslate(translation=self.get_translation(), user=self.user,
                             q="state:empty", mode="judge")
        self.assertEqual(auto.fresh_translation_state, STATE_FUZZY)

    def test_a_run_over_the_cap_is_refused(self) -> None:
        with override_settings(JUDGE_MAX_UNITS_PER_RUN=0):
            auto = self.perform(JudgeVerdict.Verdict.PASS)
        self.assertIsNotNone(auto.failure_message)
```

Проверь фактические помощники `ViewTestCase` (`get_translation`, `get_unit`) и подставь.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py`

Expected: FAIL — `__init__` не принимает `overwrite_existing`

### Step 3: Реализовать

Modify `weblate/trans/autotranslate.py`:

1. `AutoTranslate.__init__` — принять `overwrite_existing: bool = False`, сохранить.
   Добавить `self.fresh_translation_state = STATE_FUZZY`. Существующую цепочку
   `if self.mode == "fuzzy" ... elif "approved"` **не трогать**: в режиме `judge`
   `self.target_state` для суда не используется.

2. Новый метод `process_judge(self, *, engines, threshold) -> None`:

```text
units = list(self.get_units().select_related("source_unit"))
if len(units) > settings.JUDGE_MAX_UNITS_PER_RUN:
    self.failure_message = gettext(
        "Judge run refused: %(n)d strings exceed the per-run cap of %(cap)d."
    ) % {"n": len(units), "cap": settings.JUDGE_MAX_UNITS_PER_RUN}
    return
writable_ids = {u.id for u in units if (not u.translated or self.overwrite_existing)}

# Phase 1: pre-translate the writable strings via the native MT path,
# scoped by unit_ids, written at needs-editing. Reuses process_mt /
# fetch_mt / store_results / update (mechanism 5) — no second write path.
if writable_ids:
    saved_ids, saved_state = self.unit_ids, self.target_state
    self.unit_ids = list(writable_ids)
    self.target_state = self.fresh_translation_state
    try:
        self.process_mt(engines, threshold)
    finally:
        self.unit_ids, self.target_state = saved_ids, saved_state

# Phase 2: judge everything in q, decide state per verdict.
verdicts = run_judge_batch(self.get_units(), writable_ids=writable_ids, user=self.user)
unparsed = 0
for unit in self.get_units():
    verdict = verdicts.get(unit.id)
    if verdict is None:
        continue
    if verdict.verdict == JudgeVerdict.Verdict.UNPARSED:
        unparsed += 1
    state = state_for_verdict(
        verdict.verdict,
        enable_review=self.translation.enable_review,
        may_approve=settings.JUDGE_MAY_APPROVE,
    )
    if state is not None and unit.state != state:
        self.update(unit, state, unit.get_target_plurals())
if unparsed:                                   # D5: never a silent no-op
    self.add_warning(ngettext(
        "%d string was left unjudged (the judge did not answer).",
        "%d strings were left unjudged (the judge did not answer).",
        unparsed) % unparsed)
self.post_process()
```

3. `perform()` — ветка судейского режима перед `auto_source`:

```text
        if self.mode == "judge":
            self.process_judge(engines=engines, threshold=threshold)
            return self.get_message()
```

`self.update()` уже пишет через `unit.translate`, уважает право `unit.review` при
`STATE_APPROVED`, считает `self.updated`. Переиспользуй его, не пиши второй путь.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py`

Expected: PASS (6 тестов)

### Step 5: Проверить, что fail-safe и cap ловят баг

1. Временно `self.fresh_translation_state = STATE_TRANSLATED`. Expected: FAIL на
   `test_fresh_translation_starts_at_needs_editing`. Вернуть.
2. Временно снять проверку cap. Expected: FAIL на `test_a_run_over_the_cap_is_refused`. Вернуть.

### Step 6: Коммит

```bash
git add weblate/trans/autotranslate.py weblate/trans/tests/test_judge_autotranslate.py
git commit -m "feat(judge): per-verdict state, MT reuse, per-run cap in AutoTranslate"
```

---

## Задача 9. Celery, вью, счётчик строк и предупреждение о перезаписи

**Files:**

- Modify: `weblate/trans/tasks.py` (`auto_translate` сигнатура)
- Modify: `weblate/trans/views/edit.py` (сборка аргументов задачи; `check_auto_translate_permission`)
- Modify: `weblate/trans/autotranslate.py` (`BatchAutoTranslate`, `check_auto_translate_permission`)
- Modify: `weblate/templates/` — шаблон формы автоперевода
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_views.py`. Точный URL автоперевода взять из
`weblate/trans/views/edit.py` и `urls.py`, не угадывать. Слабые `assertNotContains`
усилить: сперва `assertEqual(response.status_code, 200)`, потом отрицание.

```text
    def test_judge_mode_requires_review_permission_at_the_view(self) -> None:
        self.user.is_superuser = False
        self.user.save()
        response = self.client.post(<auto-url>,
            {"mode": "judge", "q": "state:empty", "auto_source": "mt"}, follow=True)
        self.assertEqual(response.status_code, 200)
        # the view refuses the mode the form hides
        <assert the run did not start: no JudgeVerdict rows, or a permission message>

    def test_form_shows_how_many_strings_and_requests_a_run_would_touch(self) -> None:
        response = self.client.get(self.translation.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_auto_row_count")
```

### Step 2: Прогнать — FAIL

### Step 3: Пробросить режим и права

1. `weblate/trans/tasks.py` — в сигнатуру `auto_translate` добавить
   `overwrite_existing: bool = False` и передать в `BatchAutoTranslate` / `AutoTranslate`.
2. `weblate/trans/views/edit.py` — в месте сборки аргументов задачи из
   `form.cleaned_data` добавить `overwrite_existing`.
3. `check_auto_translate_permission` (`autotranslate.py`) — режим `judge` требует
   `unit.review`, как `approved`. Вью обязан отказать даже если форма скрыла выбор.

### Step 4: Счётчик строк и честная цена

В шаблоне формы вывести число строк под текущий `q` с `id="id_auto_row_count"`, обновляя
его тем же механизмом, что и поле поиска; если готового AJAX нет — серверный счётчик при
загрузке с подписью, что число отражает фильтр на момент открытия.

Рядом показать **честную верхнюю оценку числа запросов** (находка A9): не «два вызова на
строку», а `строк × 2 места × (1 + JUDGE_MAX_REPAIR_ATTEMPTS)` в худшем случае, плюс
предперевод MT для пустых. Прогон 2026-08-14 на 124 строках zh_Hans парой
`deepseek-v4-pro` + `qwen3-235b` стоил $0.068 при батче 5; **пер-строчная стоимость и время
зависят от `JUDGE_BATCH_SIZE`** — при батче 5 те же 124 строки заняли 13 минут (50
запросов), меньший батч умножает и время, и риск 403.

Предупреждение о перезаписи (дизайн, «Что нужно загородить»): если у компонента уже есть
судейские вердикты, показать в форме статическое предупреждение, что запуск `translate`/
`approved` с широким `q` перезапишет одобренные судьёй строки. Фильтр `has:judge` появится
в плане 2; здесь — текст, не механизм.

### Step 5: Прогнать тесты — PASS

### Step 6: Прогнать смежный суит

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py`

Expected: без новых падений. **Флакует под xdist**: при падениях сперва прогони на
неизменённом `HEAD~1` и сравни, потом считай регрессией.

### Step 7: Коммит

```bash
git add weblate/trans/tasks.py weblate/trans/views/edit.py \
  weblate/trans/autotranslate.py weblate/templates/ \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): wire the judge mode through Celery, the view, and a counter"
```

---

## Задача 10. Судейские чеки — вне «Things to check» и вне `enforced_checks`

Вероятностное мнение не должно стоять в одной карточке с детерминированным фактом под тем
же красным `alert.svg`, и не должно уводить строку в `state 11` как enforced-провал
(находка A7).

**Files:**

- Modify: `weblate/trans/models/unit.py` (`deterministic_checks`; guard в enforced-ветке)
- Modify: `weblate/templates/translate.html` (строки 593, 619)
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Написать падающий тест

```text
    def test_judge_check_is_absent_from_things_to_check(self) -> None:
        unit = self.get_unit()
        # verdict -> run_checks -> judge-reject row
        self.make_reject(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Judge: rejected")

    def test_judge_check_still_reaches_navigation_and_filters(self) -> None:
        unit = self.get_unit(); self.make_reject(unit)
        self.assertIn("judge-reject", unit.all_checks_names)

    def test_deterministic_check_still_renders(self) -> None:
        unit = self.get_unit()
        Check.objects.create(unit=unit, name="same", dismissed=False)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Unchanged translation")

    def test_card_is_hidden_when_only_a_judge_check_remains(self) -> None:
        unit = self.get_unit(); self.make_reject(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Things to check")

    def test_a_judge_check_cannot_be_enforced(self) -> None:
        # A7: judge-reject in enforced_checks must not push state 11.
        unit = self.get_unit()
        unit.translation.component.enforced_checks = ["judge-reject"]
        ... assert an enforced judge-reject does not set STATE_NEEDS_REWRITING
```

`make_reject` — хелпер: создать critical-вердикт и вызвать `unit.run_checks()`.

### Step 2: Прогнать — FAIL на первом, четвёртом, пятом

### Step 3: Реализовать

Добавить в `weblate/trans/models/unit.py` рядом с `all_checks`:

```python
    @property
    def deterministic_checks(self) -> list[Check]:
        """Checks shown as facts: judge verdicts render in their own card."""
        from weblate.checks.judge import JUDGE_CHECKS

        return [check for check in self.all_checks if check.name not in JUDGE_CHECKS]
```

В enforced-ветке `translate()` (около `unit.py:2444-2449`) исключить судейские из
множества, чтобы `judge-reject` не считался enforced-провалом:

```python
        from weblate.checks.judge import JUDGE_CHECKS
        enforced_hit = (
            self.all_checks_names & set(component.enforced_checks)
        ) - JUDGE_CHECKS
        if self.state >= STATE_TRANSLATED and component.enforced_checks and enforced_hit:
```

В `weblate/templates/translate.html` заменить `unit.all_checks` на
`unit.deterministic_checks` в **обоих** местах — в условии показа карточки (строка 593) и
в цикле (строка 619). Остальные условия строки 593 (`comments_to_check`, `display_checks`,
`is_blocked_by_commit_policy`, …) не трогать.

Замечание по dismiss (дизайн: судейские чеки не dismissible): с производным чеком (задача 4)
`dismissed` инертен — `active_verdict`/`describe_latest_verdict` читают вердикты, не
`Check.dismissed`, а карточка исключает судейские из цикла. Отдельного механизма не нужно.

### Step 4: Прогнать — PASS

### Step 5: Убедиться, что тест на пустую карточку ловит баг

Вернуть `unit.all_checks` в условие строки 593, оставив фильтр в цикле. Прогнать. Expected:
FAIL на `test_card_is_hidden_when_only_a_judge_check_remains`. Вернуть код.

### Step 6: Коммит

```bash
git add weblate/trans/models/unit.py weblate/templates/translate.html \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): keep judge verdicts out of the deterministic card and enforcement"
```

---

## Задача 11. Карточка «Оценка ИИ» на юните

Дизайн (раздел «Содержимое карточки»): контур — мнение, заливка — факт; число не
показываем; показываем разногласие мест; протухший вердикт помечаем, не прячем. Плюс
находка Q3: если `context_hash` вердикта не совпадает с текущим контекстом (сменился
глоссарий/нота), карточка ставит пометку «контекст изменился».

**Files:**

- Create: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/templates/translate.html` (перед блоком «Things to check», строка 593)
- Modify: `weblate/trans/views/edit.py` (контекст юнита)
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Написать падающий тест

```text
    def test_card_shows_the_verdict_and_the_model(self) -> None:
        unit = self.get_unit(); self.make_reject(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "vendor/model-a")

    def test_card_never_shows_a_score(self) -> None:
        unit = self.get_unit(); self.make_flag(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_judge_card")   # card is present
        self.assertNotContains(response, "confidence")   # but no number

    def test_card_shows_seat_disagreement(self) -> None:
        unit = self.get_unit()
        self.make_round(unit, seat1="critical", seat2="none")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "id_judge_card")
        <assert both seats' opinions are rendered>

    def test_stale_verdict_is_marked_not_hidden(self) -> None:
        unit = self.get_unit(); self.make_reject(unit, target_hash="stale")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "previous version")

    def test_unparsed_is_not_shown_as_a_verdict(self) -> None:
        unit = self.get_unit(); self.make_unparsed(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "rejected")

    def test_no_verdict_means_no_card(self) -> None:
        response = self.client.get(self.get_unit().get_absolute_url())
        self.assertNotContains(response, "id_judge_card")
```

### Step 2: Прогнать — FAIL

### Step 3: Реализовать

1. Во вью юнита положить в контекст: `judge_round = latest_round(unit)`,
   `judge_verdict = active_verdict(unit)`, `judge_stale` (есть `latest_round`, но нет
   `active` и последний круг протух), `judge_context_changed` (`judge_verdict` есть и его
   `context_hash != compute_context_hash(текущие source/note/glossary)`).
2. `weblate/templates/snippets/judge-verdict.html` — карточка по образцу существующей
   `card` из `translate.html`, с `id="id_judge_card"`. Содержимое по таблице дизайна:
   - `pass`/`minor` — свёрнута, «Принято · модель · когда»;
   - `flag`/`reject` — развёрнута, список ошибок (категория + severity + описание), мнения
     **обоих** мест и явная отметка при расхождении; плашка «не уйдёт в сборку» для `reject`;
   - протух — серая, «relates to a previous version» (кнопка «пересудить» — план 3);
   - `unparsed` — серая, «ответ судьи не разобран», **не** как вердикт;
   - при `judge_context_changed` — пометка «контекст изменился с момента суда».
3. `{% include %}` в `translate.html` в колонку сайдбара **перед** блоком «Things to check».

Оформление: не переиспользуй красный `alert.svg`. По `ACCESSIBILITY.md` состояние не
кодируется только цветом — рядом текст/иконка; карточка достижима с клавиатуры.

### Step 4: Прогнать — PASS

### Step 5: Смоук в браузере

Открыть <http://localhost:3001/> (admin/admin), найти юнит с записанным вердиктом,
убедиться глазами: карточка выше «Things to check», числа нет, судейского чека в «Things to
check» нет, оба места показаны. **Кликать реальные контролы**, не отправлять формы
программно.

### Step 6: Коммит

```bash
git add weblate/templates/snippets/judge-verdict.html weblate/templates/translate.html \
  weblate/trans/views/edit.py weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): show the verdict card on the unit page"
```

---

## Задача 12. Обратный перевод в теле формы

**Files:**

- Modify: `weblate/templates/translate.html` (после блока `secondary`, строка **108**, вне `{% if secondary %}`)
- Test: `weblate/trans/tests/test_judge_views.py`

Находка Q6: цикл `secondary` — строки 102-107, `{% endfor %}` на 107, `{% endif %}` на 108.
Вставка «после 107» попадёт **внутрь** `{% if secondary %}` и исчезнет на юнитах без
вторичных языков. Вставлять **после строки 108**.

### Step 1: Написать падающий тест

```text
    def test_back_translation_renders_next_to_the_string(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit, back_translation="The door is blocked by the DOORS")
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The door is blocked by the DOORS")

    def test_back_translation_is_labelled_as_a_reconstruction(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit, back_translation="Whatever")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Approximate reconstruction")

    def test_stale_verdict_does_not_render_its_back_translation(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit, back_translation="Outdated", target_hash="stale")
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Outdated")

    def test_back_translation_shows_without_secondary_languages(self) -> None:
        # Q6: the block must live outside {% if secondary %}.
        unit = self.get_unit()   # component without secondary languages
        self.make_flag(unit, back_translation="Visible anyway")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Visible anyway")
```

### Step 2: Прогнать — FAIL

### Step 3: Реализовать

В `translate.html` **после строки 108** (`{% endif %}` блока `secondary`) добавить
`<div class="form-group">` с подписью «Approximate reconstruction», рендерящий
`judge_verdict.back_translation` только когда вердикт активен (не протух) и текст непустой.
Слот выглядит как сосед `secondary` — «та же строка в другом рендере для справки».

### Step 4: Прогнать — PASS (4 теста)

### Step 5: Коммит

```bash
git add weblate/templates/translate.html weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): render the back translation beside the string"
```

---

## Задача 13. Документация и приёмка

**Files:**

- Modify: `docs/changes.rst` (верхняя, невыпущенная секция)
- Modify: `docs/admin/machine.rst` или `docs/admin/checks.rst` (одна страница, расширить существующий раздел)
- Modify: `docs/security/threat-model.rst`
- Modify: `deploy/environment.example` (`WEBLATE_JUDGE_*`)

### Step 1: Changelog

Одна краткая запись в верхнюю секцию `docs/changes.rst` со ссылкой на документацию режима.
Не трогать секции выпущенных версий.

### Step 2: Документация

Описать: что делает режим `judge`; настройки, включающие его (`:setting:` на `JUDGE_*`);
что `critical` удерживается штатным `WITHOUT_NEEDS_EDITING` как **очередь на человека**, а
не гарантия; что `pass` не одобряет без `JUDGE_MAY_APPROVE`; что чекбокс перезаписи
выключен по умолчанию и гейтит и предперевод, и починку. Стиль — как на соседней странице.

### Step 3: `deploy/environment.example`

Добавить `WEBLATE_JUDGE_ENABLED=`, `WEBLATE_JUDGE_OPENROUTER_KEY=`,
`WEBLATE_JUDGE_MODEL_SEAT_1=`, `WEBLATE_JUDGE_MODEL_SEAT_2=`,
`WEBLATE_JUDGE_MAX_REPAIR_ATTEMPTS=`, `WEBLATE_JUDGE_BATCH_SIZE=`,
`WEBLATE_JUDGE_MAX_UNITS_PER_RUN=`, `WEBLATE_JUDGE_REQUEST_SLEEP=`,
`WEBLATE_JUDGE_MAY_APPROVE=` — все закомментированы/пустые (выключено по умолчанию).

### Step 4: Проверка модели угроз

`docs/security/threat-model.rst`, раздел «Conditions that change this model». Новый
исходящий интеграционный класс (OpenRouter из `weblate/trans/judge.py`) под него
**подпадает** — обновить в этом же изменении. Отметить: все строки компонента (source,
target, глоссарий) уходят на OpenRouter; ключ сайтовый, не пер-компонентный, не попадает в
текст ошибок.

### Step 5: Полный линт

```bash
uv run prek run --all-files
```

Если `typos`/`rumdl` ругаются на чужие файлы — не трогать, коммитить только свои.

### Step 6: Типы и pylint по изменённым модулям

```bash
uv run pylint weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/checks/judge.py weblate/trans/models/judge.py
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Expected: без новых ошибок относительно базовой ветки.

### Step 7: Приёмочный прогон целиком

Критерий готовности из дизайна: «прогон на фильтре даёт построчный вердикт; `critical` не
уходит в сборку без решения человека; навигация через `check:judge-*`».

1. В dev-инстансе включить судью:

```bash
WEBLATE_JUDGE_ENABLED=1 WEBLATE_JUDGE_OPENROUTER_KEY=... \
WEBLATE_JUDGE_MODEL_SEAT_1=... WEBLATE_JUDGE_MODEL_SEAT_2=... \
WEBLATE_PORT=3001 ./rundev.sh
```

2. Маленький компонент, режим «Add as translation with an LLM judge», `q` на 5-10 строк,
   чекбокс перезаписи выключен, запустить.
3. Проверить в UI:
   - у каждой строки появился вердикт (карточка «Оценка ИИ»);
   - строки с `critical` стоят в «Needs editing»;
   - поиск `check:judge-reject` их находит **и находит после повторного `updatechecks`**
     (регрессия D1: строка переживает пересчёт);
   - на странице git-статуса они посчитаны как пропущенные политикой;
   - судейских строк нет в «Things to check».
4. Записать наблюдение (сколько строк, на скольких места разошлись, сколько починок,
   сколько `unparsed`) в `analysis/data/` отдельным файлом.

### Step 8: Коммит и пуш

```bash
git add docs/ deploy/environment.example
git commit -m "docs(judge): document the judge translation mode"
/usr/bin/git push -u origin plan/judge-verdict-core
```

`/usr/bin/git` намеренно: brew-git на этой машине зависает на `osxkeychain` при пуше.

---

## Приёмка плана целиком

- [ ] Миграция `0101` применяется и откатывается.
- [ ] `JudgeVerdict` накапливает записи, не перезаписывает; хранит `max_severity` +
      `unparsed`, `verdict` — свойство.
- [ ] **Спроецированная `judge-*` строка переживает повторный `run_checks`** (регрессия D1
      — главный тест плана).
- [ ] `pass` без `JUDGE_MAY_APPROVE` даёт `20`, а не `30`; с флагом и `enable_review` — `30`.
- [ ] `critical` даёт состояние из `FUZZY_STATES`, проверено импортом из штатного модуля.
- [ ] `unparsed` нигде не выглядит как `flag`/`reject`; all-unparsed круг не гасит прошлый
      вердикт; число `unparsed` за прогон видно в предупреждениях.
- [ ] Ни одно место не понижает вердикт другого: `flag` + `pass` = `flag`, зафиксировано.
- [ ] Существующий человеческий перевод без чекбокса перезаписи **не переписывается ни
      предпереводом, ни починкой** (D3/A3), проверено тестом без мока петли.
- [ ] Контракт клиента совпадает с измеренным плечом D (батчи, `span` строкой, `verdict`
      от модели, `render_preview`); `back_translation` помечено как неизмеренное отклонение.
- [ ] Улики судьи доезжают до промпта починки с экранированием и разделителем (Q1),
      `<color=…>` не съедается.
- [ ] Судейские чеки не в «Things to check» и не в `enforced_checks`; находятся через
      `check:judge-*`.
- [ ] Прогон сверх `JUDGE_MAX_UNITS_PER_RUN` отклоняется до траты денег.
- [ ] Каждая мутация из шагов «убедиться, что тест ловит баг» (задачи 2, 3, 4, 6, 8, 10)
      действительно роняет свой тест.

## Что этот план сознательно не закрывает

Гейты первого боевого прогона на проде (дизайн, «Порядок гейтов боевого прогона»)
остаются в силе и **этим планом не снимаются**: B2' с измеренными порогами, прод-backfill
автофиксов `--apply`, совпадение отпечатка автофиксов, отдельное согласование траты.
`JUDGE_MAY_APPROVE` включается только после того, как план 2 даст продюсеру экран
готовности. Промпт измерен только на ru→zh_Hans; обобщение на другие пары языков —
неизмеренное, подлежит проверке на первом прогоне. План даёт работающий механизм, а не
разрешение потратить деньги на проде.
