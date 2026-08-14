# План 1: Вердикт — ядро LLM-судьи

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task.

**Goal:** прогон судьи на фильтре даёт построчный вердикт, записанный в
`JudgeVerdict`; `critical` не уходит в сборку; судейские строки находятся
через `check:judge-flag` / `check:judge-reject`.

**Architecture:** вердикт живёт в собственной модели `JudgeVerdict`;
`Check`-строка — только проекция ради навигации и фильтров. Лестница
«судья A → судья B другого семейства → починка → человек» исполняется
внутри существующей Celery-задачи `auto_translate`, куда добавляется
режим `judge`. Состояние юнита определяется вердиктом per-unit, а не
константой на прогон. Конфигурация судьи — сайтовая, по образцу
`LOC_KIT_PROFILE_OPENROUTER_*`, выключена по умолчанию.

**Tech Stack:** Python 3.13, Django 5 / PostgreSQL, Celery, httpx через
`weblate.utils.requests.fetch_validated_url`, OpenRouter (strict JSON
Schema), pytest + `weblate.utils.tests.http_mock`.

**Основание:** `docs/LLM-first/2026-08-13-judge-native-ui-design.md`
(разделы 1-4, «Точки касания в коде»). Всё, что в дизайн-доке отнесено к
планам 2 и 3, в этом плане не делается.

---

## Границы плана

### Входит

Строка «План 1» таблицы `2026-08-13-judge-native-ui-design.md:497`:
модель и миграция, классы судейских чеков, клиент судьи, коллегия двух
судей и починка в Celery, режим `judge` в `AutoForm` с чекбоксом перезаписи,
fail-safe `state 10`, гейт по severity, карточка на юните, обратный
перевод в форме, протухание вердикта, исключение судейских чеков из
карточки «Things to check».

### Не входит

| Отложено в | Что именно |
|---|---|
| План 2 | `judge_field` в `search.py`, регистрация в `FILTERS`, судейские ключи в выпадашке формы, `calculate_judge()`, карточка «Оценка ИИ» на обзоре языка, баннер релизной готовности |
| План 3 | кнопки решений с обязательной причиной, работа с `resolution`, вкладка «Оценка ИИ» с историей кругов суда, парсер судейских спанов в `Formatter`, отдельный класс бейджа |
| Вне первого тира | откат по `run_id`, аварийный выключатель на проекте, пер-языковые тиры, категория настроек `JUDGE` с наследованием |

### Два осознанных разрыва, унаследованных из дизайна

1. **Дефолтный `q` режима `judge` остаётся `state:<translated`.** Дизайн
   (`:379-382`) хочет `NOT has:judge`, но фильтр `has:judge` появляется
   только в плане 2. Строить механизм смены дефолта под несуществующий
   фильтр — работа на выброс. Продюсер в первом тире задаёт `q` руками.
   Разрыв зафиксирован в дизайне как «до плана 2 не виден объём
   непокрытого» (`:512-515`).
2. **Санкционированного override нет до плана 3** (`:508-511`).
   `critical` держится состоянием `10`; «принять вопреки судье» делается
   штатной сменой состояния при праве `unit.review`.

### Почему не лестница: конструкция, отвергнутая замером

Первая редакция этого плана описывала в задаче 6 каскад: судья A на всех
строках, судья B только на его flag/reject — и с правом вернуть `PASS`
поверх флага A. План переписан на коллегию 2026-08-14, после прогона на
живом компоненте (`docs/LLM-first/2026-08-14-st2-zh-judge-run.md`).
Раздел оставлен, потому что в коде каскад отличается от коллегии одним
`if`, и вернуть его можно случайно.

Отвергнутая конструкция:

```text
verdict_b = judge(unit, tier=2, attempt=0)
if verdict_b is PASS or UNPARSED: return verdict_b   # ← B снимает флаг A
```

Замер B2' запретил её прямо: «Ступень B в дизайновом виде — с правом
снимать флаг — не добавлять ни при каком выборе моделей»
(`2026-08-13-phase0-measurements.md`, рекомендация 4). Причина
измеренная, а не эстетическая: специфичность первой ступени 97.6-99.2%,
снимать почти нечего, и переголосования снимали настоящие дефекты —
**46 ошибочных снятий из 48** в худшей паре. Сквозной recall каскада
равен `recall(A) x recall(B | эскалированные)` и по построению не может
превысить recall одной ступени A.

Коллегия даёт обратную арифметику, `1 - (1 - recall_1)(1 - recall_2)`, и
уронить recall не может: оба места судят **каждую** строку независимо,
вердикт круга — строгий из двух.

Как это разложено по задачам:

| Место | Что стоит в плане |
|---|---|
| Поле модели | `seat`, не `tier` — место в коллегии, не старшинство (задача 1) |
| Порядок строгости | `SEVERITY_RANK` рядом с гейтом по severity (задача 2) |
| Чтение вердикта | `latest_round` + `collegium_verdict`; проецируется круг целиком, а не последняя записанная строка (задача 4) |
| Настройки | `JUDGE_MODEL_SEAT_1` / `JUDGE_MODEL_SEAT_2` (задача 5) |
| Оркестрация | оба вызова безусловны, между ними нет ни одного `if` (задача 6) |
| Регрессия | `test_no_seat_may_lower_the_other` плюс мутация в задаче 6, Step 5 |
| Счётчик в форме | два вызова на строку, а не полтора (задача 9) |

Модели выбраны замером: `deepseek/deepseek-v4-pro` и
`qwen/qwen3-235b-a22b-2507`. На прогоне 2026-08-14 второе место стоило
около 10% от первого ($0.006 против $0.062 на 124 строках), поэтому
переход от каскада к коллегии почти не меняет деньги — но удваивает
число запросов, и упирается прогон именно в них.

### Контракт `description` для промпта починки

Проводка улик у плана верная: `BaseJudgeCheck.get_description`
(задача 3, Step 3) отдаёт `describe_latest_verdict(check_obj.unit)`, и
`_get_failing_checks_context` кладёт результат в `failing_checks`
промпта переводчика. Правки эта проводка не требует. Не определено
другое — **что именно рендерит `describe_latest_verdict`**, а от этого
зависит, сможет ли переводчик починить строку.

Прогон на zh_Hans показал, из чего рендерить будет нечего: судьи
возвращали улики вида `[critical] mistranslation: 拒绝`. Схема ответа
фазы 0 (`docs/misc/col4-judge-eval.py`, `reply_schema()`) поля
`description` не содержит вовсе — в ней только `span`, `category`,
`severity`. Спан на целевом языке не говорит ни продюсеру, ни
переводчику, что не так.

Отсюда два требования:

1. **Задача 5 (клиент судьи).** Добавить `description` в схему ответа
   как обязательное поле рядом со `span`, `category`, `severity`, и
   прописать в системном промпте адресата: читатель не знает целевого
   языка, поэтому описание обязано содержать обратный перевод спорного
   фрагмента — «написано X, что значит Y, тогда как в источнике Z».
   Развёрнуто — раздел «Контракт `description`: адресат не читает
   целевой язык» в дизайне.
2. **Задача 4 (проекция).** `describe_latest_verdict` рендерит именно
   эти описания построчно. Учти, что `_get_failing_checks_context`
   вызывает `get_description()` под `override("en")`
   (`llm.py:512`), тогда как карточка продюсера (задача 11) читает
   `JudgeVerdict.errors` напрямую. Один и тот же вердикт идёт двумя
   путями, и оба обязаны остаться читаемыми.

**Требование измерено, а не выведено из вкуса.** Ручная разметка того же
прогона (`docs/misc/st2-zh-judge-annotations.json`, 39 флагов) дала
**21 ложную тревогу**, то есть точность коллегии 28%. Почти весь этот
шум — строки, на которых судья не смог бы сформулировать претензию
словами, если бы схема его заставила: `拒绝` для «Отказаться» (24233),
`累计经验与升级所需经验` для «Накопленный опыт и опыта, необходимый для
перехода на следующий уровень» (24246, где перевод ещё и починил
опечатку источника), `超过12个雷区` для «больше 12 минных полей»
(24252). Крайний случай — 24171: `deepseek` поставил `critical` на span
длиной во всё предложение, и предложение это корректно.

Обязательный `description` поэтому работает в обе стороны: он даёт
адресату улику и **отнимает у судьи возможность пометить строку, не
сформулировав, что с ней не так**. Ожидаемый эффект на точность
измеряется планом `plans/2026-08-14-judge-severity-recalibration.md`.

Изменение промпта судьи существенное, а нынешний запечатанный срез
израсходован (`2026-08-13-phase0-measurements.md`, рекомендация 7), —
значит цена этой правки включает новый срез.

### Что проверено и правки не требует

Случай «починка вернула тот же текст» **уже закрыт**: `repair_target`
возвращает `None`, когда результат совпадает с текущим текстом
(задача 6, Step 3), петля на этом останавливается, и это
зафиксировано тестом `test_repair_that_changes_nothing_stops_the_loop`.
Холостая попытка с неизменившимся `target_hash` не сгорает и повторного
суда не вызывает. Отдельного механизма не нужно.

### Одно добавление сверх строки роадмапа

**Счётчик строк под прогон в форме** (задача 9). Дизайн требует его в
разделе «Что нужно загородить» (`:392-394`): `mode=judge` на большом
компоненте — это реальные деньги, а форма сейчас не показывает объём
вообще. План 1 — первое, что умеет тратить деньги, поэтому ограждение
обязано приехать вместе с ним, а не после. Это единственное расширение
относительно строки роадмапа; всё прочее — ровно она.

## Три нативных механизма, которые план переиспользует

Прежде чем писать код, пойми эти три места — они убирают примерно
половину ожидаемой работы.

**1. Улики попадают в промпт починки бесплатно.**
`weblate/machinery/llm.py:493-528` собирает `failing_checks` для промпта
переводчика и вызывает `check.get_description()` (строка 516). Базовый
`TargetCheck.get_description(self, check_obj)` можно переопределить и
отрендерить туда ошибки последнего вердикта. Значит стадия починки — это
обычный вызов движка перевода: судейские `Check`-строки уже записаны, и
переводчик видит претензии судьи. Отдельный «промпт починки» не нужен.

**2. Релизный гейт уже существует.**
`commit_policy = WITHOUT_NEEDS_EDITING`
(`weblate/trans/models/project.py:83-89`) исключает
`FUZZY_STATES = (10, 11, 12)` (`weblate/utils/state.py:41`,
`weblate/trans/models/pending.py:292-293`). Достаточно записать
`critical -> state 10`. Кастомный экспорт писать не надо.

**3. Навигация приезжает вместе с `Check`-строкой.**
`weblate/trans/filter.py:75-82` итерирует `CHECKS` и заводит фильтр
`check:<id>` каждому зарегистрированному классу. Как только чек
зарегистрирован и строка записана, работают поиск, бейдж в списках,
карточка «Things to check», API и `stats.allchecks`. Ничего
регистрировать руками не нужно.

## Подготовка среды

Выполнить один раз перед задачей 1.

```bash
cd /Users/eli/.config/superpowers/worktrees/weblate/plan-judge-verdict-core
uv sync --all-extras --dev
```

Тесты гоняются в контейнере — это избавляет от настройки БД и
`collectstatic`:

```bash
./rundev.sh test weblate/trans/tests/test_judge.py
```

Контейнер общий с другими сессиями. Если суите внезапно становится плохо
(массовые setup-ERROR, тест вместо 4 с идёт 100 с) — сначала
`docker stats --no-stream`, а не поиск бага в коде.

Линт по изменённым файлам, без прогона по всему репозиторию:

```bash
uv run prek run --files <список файлов>
```

Соглашения репозитория, обязательные к соблюдению:

- новые файлы форка несут заголовок
  `# Copyright © HCGameLoc` + `# SPDX-License-Identifier: GPL-3.0-or-later`;
- `from __future__ import annotations` в каждом Python-модуле;
- пользовательские строки переводятся через `gettext_lazy`, **кроме**
  значений, которые уходят в API или в хранилище — их не локализуем;
- коммиты в формате Conventional Commits.

---

## Задача 1. Модель `JudgeVerdict` и хэши

**Files:**

- Create: `weblate/trans/models/judge.py`
- Create: `weblate/trans/migrations/0099_judge_verdict.py`
- Modify: `weblate/trans/models/__init__.py:34,64`
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
    loop stay auditable.
    """

    # Stored and API-facing values: deliberately not localized.
    class Verdict(models.TextChoices):
        PASS = "pass"
        FLAG = "flag"
        REJECT = "reject"
        # Transport failure, never an opinion. Architecture invariant 4.3.
        UNPARSED = "unparsed"

    class Severity(models.TextChoices):
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
    verdict = models.CharField(max_length=10, choices=Verdict)
    max_severity = models.CharField(
        max_length=10, choices=Severity, default=Severity.NONE
    )
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
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["unit", "-timestamp"], name="judge_unit_recent_idx"),
            models.Index(fields=["run_id"], name="judge_run_idx"),
        ]
        constraints = [
            # One vote per seat per round: the projection reduces a round
            # to its strictest seat and must not see a seat twice.
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

Modify `weblate/trans/models/__init__.py` — добавить импорт рядом с
существующим `loc_kit` (строка 34) и запись в `__all__` (строка 64),
сохраняя алфавитный порядок:

```python
from weblate.trans.models.judge import JudgeVerdict
```

```text
    "JudgeVerdict",
```

### Step 6: Сгенерировать миграцию

Run:

```bash
./rundev.sh manage makemigrations trans --name judge_verdict
```

Expected: `weblate/trans/migrations/0099_judge_verdict.py` с
`dependencies = [("trans", "0098_loc_kit_import_draft"), swappable_dependency(...)]`

Открыть файл и проверить, что заголовок совпадает с `0098`:

```python
# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later
```

### Step 7: Проверить, что миграция применяется

Run: `./rundev.sh manage migrate trans`

Expected: `Applying trans.0099_judge_verdict... OK`

### Step 8: Коммит

```bash
git add weblate/trans/models/judge.py weblate/trans/models/__init__.py \
  weblate/trans/migrations/0099_judge_verdict.py weblate/trans/tests/test_judge.py
git commit -m "feat(judge): add the JudgeVerdict model and verdict hashes"
```

---

## Задача 2. Гейт по severity — чистая функция

Отдельная задача, потому что это единственное место, где решается, что
уйдёт в сборку. Оно должно быть проверяемо без БД, LLM и Celery.

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Test: `weblate/trans/tests/test_judge.py`

### Step 1: Написать падающий тест

Добавить в `weblate/trans/tests/test_judge.py`:

```python
from weblate.trans.models.judge import (
    JudgeVerdict,
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

    def test_shipping_gate(self) -> None:
        # Design table 2026-08-13-judge-native-ui-design.md:304-310
        self.assertEqual(
            state_for_verdict(JudgeVerdict.Verdict.PASS, enable_review=True),
            STATE_APPROVED,
        )
        self.assertEqual(
            state_for_verdict(JudgeVerdict.Verdict.FLAG, enable_review=True),
            STATE_TRANSLATED,
        )
        self.assertEqual(
            state_for_verdict(JudgeVerdict.Verdict.REJECT, enable_review=True),
            STATE_FUZZY,
        )

    def test_pass_without_review_stops_at_translated(self) -> None:
        # translation_review=False must not silently grant approval.
        self.assertEqual(
            state_for_verdict(JudgeVerdict.Verdict.PASS, enable_review=False),
            STATE_TRANSLATED,
        )

    def test_unparsed_never_changes_state(self) -> None:
        # A transport failure is not an opinion; the string keeps its state.
        self.assertIsNone(
            state_for_verdict(JudgeVerdict.Verdict.UNPARSED, enable_review=True)
        )

    def test_reject_is_held_by_the_native_commit_policy(self) -> None:
        from weblate.utils.state import FUZZY_STATES

        self.assertIn(
            state_for_verdict(JudgeVerdict.Verdict.REJECT, enable_review=True),
            FUZZY_STATES,
        )
```

Последний тест — самый важный в задаче: он привязывает наш гейт к
штатному `WITHOUT_NEEDS_EDITING`, а не к нашему представлению о нём.
`FUZZY_STATES` объявлен в `weblate/utils/state.py:41` и нигде не
реэкспортируется — импортируй именно оттуда.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge.py`

Expected: FAIL — `ImportError: cannot import name 'verdict_for_severity'`

### Step 3: Реализовать

Добавить в `weblate/trans/models/judge.py`:

```python
from weblate.utils.state import STATE_APPROVED, STATE_FUZZY, STATE_TRANSLATED

# Design 2026-08-13-judge-native-ui-design.md:304-310. minor is a pass:
# the errors are recorded, but they do not hold the string back.
_SEVERITY_VERDICT = {
    "none": JudgeVerdict.Verdict.PASS,
    "minor": JudgeVerdict.Verdict.PASS,
    "major": JudgeVerdict.Verdict.FLAG,
    "critical": JudgeVerdict.Verdict.REJECT,
}


def verdict_for_severity(max_severity: str) -> str:
    """Derive the verdict from the worst error the judge reported."""
    return _SEVERITY_VERDICT[max_severity]


# Order of strictness, so a round can be reduced to its strictest seat
# without a seat ever lowering another. Derived from the declared scale:
# Severity is ordered by definition, _SEVERITY_VERDICT is not.
SEVERITY_RANK = {
    name: rank for rank, name in enumerate(JudgeVerdict.Severity.values)
}


def state_for_verdict(verdict: str, *, enable_review: bool) -> int | None:
    """Target state for a verdict, or None when the state must not move.

    ``critical`` lands on STATE_FUZZY, which the project-level
    ``WITHOUT_NEEDS_EDITING`` commit policy already excludes from export;
    no custom export gate is needed.
    """
    if verdict == JudgeVerdict.Verdict.UNPARSED:
        return None
    if verdict == JudgeVerdict.Verdict.REJECT:
        return STATE_FUZZY
    if verdict == JudgeVerdict.Verdict.PASS and enable_review:
        return STATE_APPROVED
    return STATE_TRANSLATED
```

`_SEVERITY_VERDICT` объявляется после класса `JudgeVerdict`, иначе имя не
разрешится.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge.py`

Expected: PASS (10 тестов)

### Step 5: Коммит

```bash
git add weblate/trans/models/judge.py weblate/trans/tests/test_judge.py
git commit -m "feat(judge): map verdict severity to the native shipping gate"
```

---

## Задача 3. Судейские чеки и их регистрация

Чеки заполняются извне: `check_single` всегда возвращает `False`, строки
пишет проекция из задачи 4. Так класс попадает в `CHECKS`, а значит в
`filter.py`, в API и в статистику, но никогда не срабатывает сам.

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Modify: `dev-docker/docker-compose.yml` (`WEBLATE_ADD_CHECK`)
- Modify: `deploy/environment.example` (`WEBLATE_ADD_CHECK=`)
- Test: `weblate_customization/tests/test_checks.py`

### Step 1: Написать падающий тест

Добавить в `weblate_customization/tests/test_checks.py`:

```python
from weblate_customization.checks import JudgeFlagCheck, JudgeRejectCheck


class JudgeCheckTest(SimpleTestCase):
    def test_judge_checks_never_fire_on_their_own(self) -> None:
        # Externally populated: rows are written by the verdict projection.
        for check in (JudgeFlagCheck(), JudgeRejectCheck()):
            self.assertFalse(check.check_single("source", "target", None))

    def test_judge_checks_are_not_enforceable(self) -> None:
        # Architecture invariant 1: a probabilistic verdict never blocks
        # editing the way a deterministic check does.
        for check in (JudgeFlagCheck(), JudgeRejectCheck()):
            self.assertFalse(check.default_disabled)
            self.assertTrue(check.check_id.startswith("judge-"))
```

Импорт `SimpleTestCase` уже есть в файле, если нет — добавить
`from django.test import SimpleTestCase`.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate_customization/tests/test_checks.py`

Expected: FAIL — `ImportError: cannot import name 'JudgeFlagCheck'`

### Step 3: Реализовать классы

Добавить в `weblate_customization/src/weblate_customization/checks.py`:

```python
class BaseJudgeCheck(TargetCheck):
    """A verdict projected from JudgeVerdict, never computed here.

    The class exists so the verdict gets a name in ``CHECKS``: that alone
    buys the ``check:<id>`` filter, the badge in unit lists and the API,
    without registering anything by hand. Rows are written and removed by
    ``weblate.trans.judge_projection``.
    """

    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        return False

    def get_description(self, check_obj) -> str:
        """Render the latest verdict's errors.

        This is what carries the judge's evidence into the repair prompt:
        ``weblate/machinery/llm.py:516`` calls ``get_description()`` when
        it builds ``failing_checks`` for the translator.
        """
        from weblate.trans.judge_projection import describe_latest_verdict

        return describe_latest_verdict(check_obj.unit) or self.description


class JudgeFlagCheck(BaseJudgeCheck):
    check_id = "judge-flag"
    name = gettext_lazy("Judge: questionable")
    description = gettext_lazy(
        "An LLM judge reported a major problem. The string still ships."
    )


class JudgeRejectCheck(BaseJudgeCheck):
    check_id = "judge-reject"
    name = gettext_lazy("Judge: rejected")
    description = gettext_lazy(
        "An LLM judge reported a critical problem. The string does not ship."
    )
```

Импорт `describe_latest_verdict` — локальный внутри метода: модуль
проекции импортирует модели Django, а `checks.py` грузится на старте
приложения.

### Step 4: Прогнать тест

Run: `./rundev.sh test weblate_customization/tests/test_checks.py`

Expected: FAIL на `describe_latest_verdict` только если тест его дёргает;
два написанных теста должны пройти. Если падает импорт — значит
`get_description` вызвался; проверь, что тесты его не трогают.

### Step 5: Зарегистрировать чеки

Modify `dev-docker/docker-compose.yml`, значение `WEBLATE_ADD_CHECK` —
дописать через запятую, не меняя существующие три:

```text
weblate_customization.checks.JudgeFlagCheck,weblate_customization.checks.JudgeRejectCheck
```

То же в `deploy/environment.example` (там форма `WEBLATE_ADD_CHECK=`).

### Step 6: Доставить модуль в контейнер и перезапустить

`weblate_customization/` в контейнер не устанавливается, а копируется.
Правка `docker-compose.yml` требует полного `./rundev.sh`, а не рестарта:
блок окружения запекается при создании контейнера.

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh
```

### Step 7: Проверить, что чек виден системе

Run:

```bash
./rundev.sh manage shell -c "from weblate.checks.models import CHECKS; print('judge-flag' in CHECKS, 'judge-reject' in CHECKS)"
```

Expected: `True True`

### Step 8: Коммит

```bash
git add weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py \
  dev-docker/docker-compose.yml deploy/environment.example
git commit -m "feat(judge): register externally populated judge checks"
```

---

## Задача 4. Проекция вердикта в `Check` и протухание

**Files:**

- Create: `weblate/trans/judge_projection.py`
- Test: `weblate/trans/tests/test_judge_projection.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_projection.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from weblate.checks.models import Check
from weblate.trans.judge_projection import (
    collegium_verdict,
    describe_latest_verdict,
    latest_round,
    project_verdict,
)
from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.tests.test_views import ViewTestCase


class JudgeProjectionTest(ViewTestCase):
    def make_verdict(self, unit, verdict, **kwargs) -> JudgeVerdict:
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "ctx")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        return JudgeVerdict.objects.create(unit=unit, verdict=verdict, **kwargs)

    def check_names(self, unit) -> set[str]:
        return set(Check.objects.filter(unit=unit).values_list("name", flat=True))

    def test_reject_creates_a_check_row(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.REJECT, max_severity="critical")
        project_verdict(unit)
        self.assertEqual(
            self.check_names(unit) & {"judge-flag", "judge-reject"}, {"judge-reject"}
        )

    def test_pass_leaves_no_judge_row(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.PASS)
        project_verdict(unit)
        self.assertEqual(self.check_names(unit) & {"judge-flag", "judge-reject"}, set())

    def test_the_strict_seat_of_a_round_is_projected(self) -> None:
        # Seat 2 passing must not clear seat 1's rejection: that is the
        # cascade B2' rejected, arriving through the projection instead.
        unit = self.get_unit()
        run_id = uuid.uuid4()
        self.make_verdict(
            unit,
            JudgeVerdict.Verdict.REJECT,
            seat=1,
            run_id=run_id,
            max_severity="critical",
        )
        self.make_verdict(unit, JudgeVerdict.Verdict.PASS, seat=2, run_id=run_id)
        project_verdict(unit)
        self.assertEqual(
            self.check_names(unit) & {"judge-flag", "judge-reject"}, {"judge-reject"}
        )
        self.assertEqual(collegium_verdict(latest_round(unit)).seat, 1)

    def test_a_parsed_seat_outvotes_an_unparsed_one(self) -> None:
        # A transport failure is not an opinion and must not mute the
        # seat that did answer.
        unit = self.get_unit()
        run_id = uuid.uuid4()
        self.make_verdict(unit, JudgeVerdict.Verdict.UNPARSED, seat=1, run_id=run_id)
        self.make_verdict(
            unit,
            JudgeVerdict.Verdict.REJECT,
            seat=2,
            run_id=run_id,
            max_severity="critical",
        )
        project_verdict(unit)
        self.assertEqual(
            self.check_names(unit) & {"judge-flag", "judge-reject"}, {"judge-reject"}
        )

    def test_a_newer_round_replaces_the_previous_projection(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.REJECT, max_severity="critical")
        project_verdict(unit)
        self.make_verdict(
            unit, JudgeVerdict.Verdict.FLAG, max_severity="major", attempt=1
        )
        project_verdict(unit)
        self.assertEqual(
            self.check_names(unit) & {"judge-flag", "judge-reject"}, {"judge-flag"}
        )

    def test_stale_verdict_is_not_projected(self) -> None:
        unit = self.get_unit()
        self.make_verdict(
            unit,
            JudgeVerdict.Verdict.REJECT,
            max_severity="critical",
            target_hash="stale-hash-that-matches-nothing",
        )
        project_verdict(unit)
        self.assertEqual(self.check_names(unit) & {"judge-flag", "judge-reject"}, set())

    def test_unparsed_is_never_projected(self) -> None:
        # A transport failure must not look like a flag. Invariant 4.3.
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.UNPARSED)
        project_verdict(unit)
        self.assertEqual(self.check_names(unit) & {"judge-flag", "judge-reject"}, set())

    def test_projection_keeps_deterministic_checks(self) -> None:
        # The projection owns judge-* rows only; it must not touch others.
        unit = self.get_unit()
        Check.objects.create(unit=unit, name="same", dismissed=False)
        self.make_verdict(unit, JudgeVerdict.Verdict.REJECT, max_severity="critical")
        project_verdict(unit)
        self.assertIn("same", self.check_names(unit))

    def test_description_carries_the_errors_into_the_repair_prompt(self) -> None:
        unit = self.get_unit()
        self.make_verdict(
            unit,
            JudgeVerdict.Verdict.REJECT,
            max_severity="critical",
            errors=[
                {
                    "span_start": 0,
                    "span_end": 4,
                    "category": "terminology",
                    "severity": "critical",
                    "description": "the Gates are called DOORS here",
                }
            ],
        )
        description = describe_latest_verdict(unit)
        self.assertIn("terminology", description)
        self.assertIn("the Gates are called DOORS here", description)

    def test_description_merges_both_seats(self) -> None:
        # The repair has to satisfy the whole collegium, not the seat
        # that happened to be strictest.
        unit = self.get_unit()
        run_id = uuid.uuid4()
        self.make_verdict(
            unit,
            JudgeVerdict.Verdict.REJECT,
            seat=1,
            run_id=run_id,
            max_severity="critical",
            errors=[
                {
                    "span_start": 0,
                    "span_end": 4,
                    "category": "terminology",
                    "severity": "critical",
                    "description": "the Gates are called DOORS here",
                }
            ],
        )
        self.make_verdict(
            unit,
            JudgeVerdict.Verdict.FLAG,
            seat=2,
            run_id=run_id,
            max_severity="major",
            errors=[
                {
                    "span_start": 5,
                    "span_end": 9,
                    "category": "fluency",
                    "severity": "major",
                    "description": "the second clause has no verb",
                }
            ],
        )
        description = describe_latest_verdict(unit)
        self.assertIn("the Gates are called DOORS here", description)
        self.assertIn("the second clause has no verb", description)
```

Проверь фактическое имя метода получения плюралов у `Unit`
(`get_target_plurals` против свойства) и поправь помощник.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_projection.py`

Expected: FAIL — `ModuleNotFoundError: weblate.trans.judge_projection`

### Step 3: Реализовать проекцию

Create `weblate/trans/judge_projection.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Projection of JudgeVerdict rows into Check rows.

The verdict lives in :class:`weblate.trans.models.judge.JudgeVerdict`.
A ``Check`` row is written only so the verdict gets navigation, badges
and statistics for free; it is never the source of truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weblate.checks.models import Check
from weblate.trans.models.judge import SEVERITY_RANK, JudgeVerdict

if TYPE_CHECKING:
    from collections.abc import Sequence

    from weblate.trans.models.unit import Unit

JUDGE_CHECKS = frozenset({"judge-flag", "judge-reject"})

_VERDICT_CHECK = {
    JudgeVerdict.Verdict.FLAG: "judge-flag",
    JudgeVerdict.Verdict.REJECT: "judge-reject",
}


def latest_round(unit: Unit) -> list[JudgeVerdict]:
    """Every seat's opinion about the newest judged version, stale or not."""
    newest = unit.judge_verdicts.order_by("-timestamp", "-pk").first()
    if newest is None:
        return []
    return list(
        unit.judge_verdicts.filter(
            run_id=newest.run_id, attempt=newest.attempt
        ).order_by("seat")
    )


def collegium_verdict(rows: Sequence[JudgeVerdict]) -> JudgeVerdict | None:
    """The strictest opinion of a round.

    No seat may lower another. The cascade where a second model could
    clear the first one's flag was rejected by measurement B2': 46 of 48
    overturns deleted a real defect. A transport failure is not an
    opinion, so an ``unparsed`` row neither raises nor lowers the round;
    only when every seat failed does the round read as ``unparsed``.
    """
    if not rows:
        return None
    parsed = [row for row in rows if row.verdict != JudgeVerdict.Verdict.UNPARSED]
    if not parsed:
        return rows[0]
    return max(parsed, key=lambda row: (SEVERITY_RANK[row.max_severity], -row.seat))


def active_round(unit: Unit) -> list[JudgeVerdict]:
    """The newest round, or nothing when it describes older text."""
    rows = latest_round(unit)
    if not rows or rows[0].is_stale(unit.get_target_plurals()):
        return []
    return rows


def active_verdict(unit: Unit) -> JudgeVerdict | None:
    """The collegium verdict that still describes the stored text."""
    return collegium_verdict(active_round(unit))


def project_verdict(unit: Unit) -> None:
    """Sync the judge-* Check rows of a unit with its active verdict.

    Only judge rows are touched: deterministic checks belong to
    ``Unit.run_checks`` and must survive untouched.
    """
    verdict = active_verdict(unit)
    wanted = None
    if verdict is not None:
        wanted = _VERDICT_CHECK.get(verdict.verdict)

    stale_names = JUDGE_CHECKS - ({wanted} if wanted else set())
    Check.objects.filter(unit=unit, name__in=stale_names).delete()
    if wanted:
        Check.objects.get_or_create(
            unit=unit, name=wanted, defaults={"dismissed": False}
        )
    unit.invalidate_checks_cache()


def describe_latest_verdict(unit: Unit) -> str:
    """Human-readable evidence for the active round, or an empty string.

    Rendered into the check description, which
    ``weblate/machinery/llm.py:516`` feeds to the translator as
    ``failing_checks`` during the repair stage. Both seats are merged:
    the repair has to satisfy everything the collegium found, not only
    the seat that happened to be strictest.
    """
    lines: list[str] = []
    for row in active_round(unit):
        for error in row.errors:
            line = "{}/{}: {}".format(
                error.get("severity", "unspecified"),
                error.get("category", "unspecified"),
                error.get("description", ""),
            )
            if line not in lines:
                lines.append(line)
    return "\n".join(lines)
```

Проверь имя метода инвалидации кэша чеков у `Unit`
(`weblate/trans/models/unit.py:2381` использует `invalidate_checks_cache`).

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_projection.py`

Expected: PASS (10 тестов)

### Step 5: Убедиться, что тест на протухание ловит баг

Временно убрать проверку `is_stale` из `active_round` (вернуть
`latest_round(unit)` напрямую), прогнать снова.

Expected: FAIL на `test_stale_verdict_is_not_projected`. Вернуть код.

Этот шаг обязателен: тест, который не падает на отсутствии фичи, ничего
не защищает.

### Step 6: Коммит

```bash
git add weblate/trans/judge_projection.py weblate/trans/tests/test_judge_projection.py
git commit -m "feat(judge): project verdicts into check rows with staleness"
```

---

## Задача 5. Настройки судьи и клиент OpenRouter

Конфигурация сайтовая и выключена по умолчанию — по образцу
`LOC_KIT_PROFILE_OPENROUTER_*`. Пер-компонентных ключей в первом тире
нет: дизайн относит их во «Вне первого тира» (`:541-543`).

**Files:**

- Modify: `weblate/trans/defaults.py:46-50`
- Modify: `weblate/trans/models/_conf.py:92-97`
- Modify: `weblate/settings_docker.py:1653-1670`
- Modify: `weblate/settings_example.py:1011-1014`
- Create: `weblate/trans/judge.py`
- Test: `weblate/trans/tests/test_judge_client.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_client.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json

from django.test import SimpleTestCase, override_settings

from weblate.trans.judge import JudgeError, JudgeRequest, request_verdict
from weblate.utils.tests import http_mock

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

REQUEST = JudgeRequest(
    source="Дверь заблокирована ГЕРМОДВЕРЬМИ",
    target="La porte est bloquée par les PORTES",
    source_language="ru",
    target_language="fr",
    note="",
    glossary_terms=[("ГЕРМОДВЕРЬ", "porte blindée")],
    failing_checks=[],
)


def _reply(payload: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class JudgeClientGateTest(SimpleTestCase):
    @override_settings(JUDGE_ENABLED=False)
    @http_mock.activate
    def test_disabled_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdict(REQUEST, model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY="")
    @http_mock.activate
    def test_missing_key_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdict(REQUEST, model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)


@override_settings(JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY="sk-test-do-not-leak")
class JudgeClientTest(SimpleTestCase):
    @http_mock.activate
    def test_parses_a_verdict(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                {
                    "max_severity": "critical",
                    "errors": [
                        {
                            "span_start": 26,
                            "span_end": 32,
                            "category": "terminology",
                            "severity": "critical",
                            "description": (
                                "«ВРАТА» is rendered as «DOORS»; the glossary "
                                "calls this location the Gates"
                            ),
                        }
                    ],
                    "back_translation": "The door is blocked by the DOORS",
                }
            ),
        )
        result = request_verdict(REQUEST, model="vendor/model-a")
        self.assertEqual(result.max_severity, "critical")
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Gates", result.errors[0]["description"])
        self.assertIn("DOORS", result.back_translation)

    @http_mock.activate
    def test_an_error_without_a_description_raises(self) -> None:
        # A bare span in the target language is not evidence: the producer
        # does not read it and the repair prompt cannot act on it.
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                {
                    "max_severity": "major",
                    "errors": [
                        {
                            "span_start": 0,
                            "span_end": 2,
                            "category": "fluency",
                            "severity": "major",
                        }
                    ],
                    "back_translation": "whatever",
                }
            ),
        )
        with self.assertRaises(JudgeError):
            request_verdict(REQUEST, model="vendor/model-a")

    @http_mock.activate
    def test_sends_a_strict_schema_and_requires_providers_to_honour_it(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                {
                    "max_severity": "none",
                    "errors": [],
                    "back_translation": "",
                }
            ),
        )
        request_verdict(REQUEST, model="vendor/model-a")
        body = json.loads(http_mock.calls[0].request.content)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertTrue(body["provider"]["require_parameters"])
        self.assertEqual(body["model"], "vendor/model-a")

    @http_mock.activate
    def test_malformed_json_raises_rather_than_inventing_a_verdict(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json={"choices": [{"message": {"content": "not json at all"}}]},
        )
        with self.assertRaises(JudgeError):
            request_verdict(REQUEST, model="vendor/model-a")

    @http_mock.activate
    def test_unknown_severity_raises(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                {
                    "max_severity": "catastrophic",
                    "errors": [],
                    "back_translation": "",
                }
            ),
        )
        with self.assertRaises(JudgeError):
            request_verdict(REQUEST, model="vendor/model-a")

    @http_mock.activate
    def test_http_error_raises(self) -> None:
        http_mock.register("POST", CHAT_URL, status=500, json={})
        with self.assertRaises(JudgeError):
            request_verdict(REQUEST, model="vendor/model-a")

    @http_mock.activate
    def test_the_api_key_never_reaches_the_exception_text(self) -> None:
        http_mock.register("POST", CHAT_URL, status=401, json={})
        with self.assertRaises(JudgeError) as ctx:
            request_verdict(REQUEST, model="vendor/model-a")
        self.assertNotIn("sk-test-do-not-leak", str(ctx.exception))
```

Проверь точную сигнатуру `http_mock.register` по
`weblate/trans/tests/test_loc_kit_profile_suggester.py` и подгони вызовы.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_client.py`

Expected: FAIL — `ModuleNotFoundError: weblate.trans.judge`

### Step 3: Объявить настройки

`weblate/trans/defaults.py` — рядом с блоком `LOC_KIT_PROFILE_*`:

```python
DEFAULT_JUDGE_ENABLED = False
DEFAULT_JUDGE_OPENROUTER_KEY = ""
DEFAULT_JUDGE_MODEL_SEAT_1 = ""
DEFAULT_JUDGE_MODEL_SEAT_2 = ""
DEFAULT_JUDGE_MAX_REPAIR_ATTEMPTS = 1
```

`weblate/trans/models/_conf.py` — рядом со строками 92-97:

```text
    # LLM judge (off by default; site-wide, like LOC_KIT_PROFILE_*)
    JUDGE_ENABLED = defaults.DEFAULT_JUDGE_ENABLED
    JUDGE_OPENROUTER_KEY = defaults.DEFAULT_JUDGE_OPENROUTER_KEY
    JUDGE_MODEL_SEAT_1 = defaults.DEFAULT_JUDGE_MODEL_SEAT_1
    JUDGE_MODEL_SEAT_2 = defaults.DEFAULT_JUDGE_MODEL_SEAT_2
    JUDGE_MAX_REPAIR_ATTEMPTS = defaults.DEFAULT_JUDGE_MAX_REPAIR_ATTEMPTS
```

`weblate/settings_docker.py` — по образцу строк 1653-1670, через
`get_env_bool` / `get_env_str` / `get_env_int`, префикс `WEBLATE_`.

`weblate/settings_example.py` — по образцу строк 1011-1014.

### Step 4: Написать клиент

Create `weblate/trans/judge.py`. Формируй по образцу
`weblate/trans/loc_kit.py:398-500`: фиксированный хост, `strict: True`,
`provider.require_parameters`, `raise_for_status=False`,
`follow_redirects=False`, таймаут 120 с, единственный тип исключения,
ключ не попадает ни в текст ошибки, ни во фрейм-локал.

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""OpenRouter client for the LLM judge.

Separate from RoutedLLMTranslation on purpose: the judge is not a machine
translation service, it has its own lifecycle and its own site-wide
configuration. Mirrors the loc-kit profile client
(``weblate/trans/loc_kit.py``): fixed host, strict schema, one exception
type, no user-supplied endpoint, key or model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.translation import gettext as _

from weblate.utils.requests import fetch_validated_url

if TYPE_CHECKING:
    from collections.abc import Sequence

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
JUDGE_REQUEST_TIMEOUT = 120
SEVERITIES = ("none", "minor", "major", "critical")
CATEGORIES = (
    "terminology",
    "accuracy",
    "fluency",
    "style",
    "locale",
    "markup",
    "other",
)


class JudgeError(Exception):
    """Any judge failure: disabled, misconfigured, transport, or unparsable."""


@dataclass(frozen=True)
class JudgeRequest:
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
    errors: list[dict]
    back_translation: str


def _response_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["max_severity", "errors", "back_translation"],
        "properties": {
            "max_severity": {"type": "string", "enum": list(SEVERITIES)},
            "back_translation": {"type": "string"},
            "errors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "span_start",
                        "span_end",
                        "category",
                        "severity",
                        "description",
                    ],
                    "properties": {
                        "span_start": {"type": "integer", "minimum": 0},
                        "span_end": {"type": "integer", "minimum": 0},
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "severity": {
                            "type": "string",
                            "enum": [s for s in SEVERITIES if s != "none"],
                        },
                        # Read by the producer and fed to the repair prompt
                        # as a failing check. A span in the target language
                        # tells neither of them anything. Non-emptiness is
                        # checked in code: length keywords are outside the
                        # strict structured-output subset.
                        "description": {"type": "string"},
                    },
                },
            },
        },
    }
```

Дальше — `request_verdict(request, *, model) -> JudgeResult`:

1. Гейты до сети: `settings.JUDGE_ENABLED`, `settings.JUDGE_OPENROUTER_KEY`,
   непустой `model` — каждый поднимает `JudgeError`.
2. Промпт судьи держать отдельным файлом, не инлайном: правило
   `~/.claude/CLAUDE.md` «Store prompts in a separate folder». Положить в
   `weblate/trans/judge_prompts/verdict.txt`, читать через
   `importlib.resources`, как это делает `load_profile_prompt`
   в `loc_kit.py`.
3. `payload` с `model`, `stream: False`, `response_format.json_schema`
   (`name: "judge_verdict"`, `strict: True`, схема выше),
   `provider.require_parameters: True`, `messages` из системного промпта и
   JSON-сериализованного `JudgeRequest`.
4. `fetch_validated_url("POST", OPENROUTER_CHAT_COMPLETIONS_URL, headers=...,
   json=payload, timeout=JUDGE_REQUEST_TIMEOUT, raise_for_status=False,
   follow_redirects=False)` в `try`, любое исключение → `JudgeError`.
5. `status_code >= 400` → `JudgeError` с кодом, без тела ответа.
6. Разбор: `choices[0].message.content` → `json.loads` → валидация
   `max_severity in SEVERITIES` и формы `errors`, включая непустой
   `description` у каждой ошибки. Любое несоответствие — `JudgeError`.
   **Никогда не подставлять дефолтный вердикт.**

Что обязан содержать `description`, задаётся промптом, а не схемой:
**обратный перевод спорного фрагмента плюс объяснение, что с ним не
так**, на языке продюсера. Ни спан на целевом языке, ни повтор
категории не годятся — раздел «Контракт `description` для промпта
починки» в начале плана показывает, во что это обходится: и продюсер, и
переводчик получают улику вида `[critical] mistranslation: 拒绝`, по
которой нельзя ни принять решение, ни починить строку.

### Step 5: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_client.py`

Expected: PASS (9 тестов)

### Step 6: Коммит

```bash
git add weblate/trans/judge.py weblate/trans/judge_prompts/ \
  weblate/trans/defaults.py weblate/trans/models/_conf.py \
  weblate/settings_docker.py weblate/settings_example.py \
  weblate/trans/tests/test_judge_client.py
git commit -m "feat(judge): add the site-wide OpenRouter judge client"
```

---

## Задача 6. Коллегия и петля починки

Чистая оркестрация поверх клиента: без Django-форм, без Celery. Оба
места коллегии судят **каждую** строку независимо, вердикт круга —
строгий из двух, понижать друг друга места не вправе. Дальше петля
починки до исчерпания попыток, затем очередь человека.

Раздел «Почему не лестница» в начале плана объясняет причину: право
второй ступени снимать флаг первой замер B2' отверг напрямую —
46 ошибочных снятий из 48 в худшей паре. Тест
`test_no_seat_may_lower_the_other` стоит ровно в той точке, где эта
ошибка уже один раз произошла.

**Files:**

- Create: `weblate/trans/judge_loop.py`
- Test: `weblate/trans/tests/test_judge_loop.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_loop.py`. Клиент подменяется
целиком — сеть в этих тестах не участвует.

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest import mock

from django.test import override_settings

from weblate.trans.judge import JudgeError, JudgeResult
from weblate.trans.judge_loop import run_judge_loop
from weblate.trans.models.judge import JudgeVerdict
from weblate.trans.tests.test_views import ViewTestCase

PASS = JudgeResult(max_severity="none", errors=[], back_translation="ok")
MAJOR = JudgeResult(
    max_severity="major",
    errors=[
        {
            "span_start": 0,
            "span_end": 1,
            "category": "style",
            "severity": "major",
            "description": "register is too formal for a barracks line",
        }
    ],
    back_translation="meh",
)
CRITICAL = JudgeResult(
    max_severity="critical",
    errors=[
        {
            "span_start": 0,
            "span_end": 1,
            "category": "terminology",
            "severity": "critical",
            "description": "«MAX» left in Latin script; the glossary wants 最大",
        }
    ],
    back_translation="wrong",
)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
class JudgeLoopTest(ViewTestCase):
    def run_with(self, results, repair=None):
        with (
            mock.patch(
                "weblate.trans.judge_loop.request_verdict", side_effect=results
            ) as client,
            mock.patch(
                "weblate.trans.judge_loop.repair_target", return_value=repair
            ),
        ):
            unit = self.get_unit()
            verdict = run_judge_loop(unit, user=self.user)
        return unit, verdict, client

    def test_both_seats_judge_every_string(self) -> None:
        unit, verdict, client = self.run_with([PASS, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(unit.judge_verdicts.count(), 2)

    def test_no_seat_may_lower_the_other(self) -> None:
        # The construction B2' rejected. Measured cost of the opposite:
        # 46 of 48 overturns deleted a real defect.
        _, verdict, _ = self.run_with([MAJOR, PASS])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.assertEqual(verdict.max_severity, "major")

    def test_the_same_holds_when_the_strict_seat_votes_second(self) -> None:
        _, verdict, _ = self.run_with([PASS, MAJOR])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_verdict_takes_the_higher_severity(self) -> None:
        _, verdict, _ = self.run_with([MAJOR, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)
        self.assertEqual(verdict.max_severity, "critical")

    def test_each_seat_uses_its_configured_model(self) -> None:
        # Contract: seat N calls SEAT_N. Whether the two differ is a
        # deployment choice, not an invariant.
        _, _, client = self.run_with([PASS, PASS])
        models_used = [call.kwargs["model"] for call in client.call_args_list]
        self.assertEqual(models_used, ["vendor-a/model", "vendor-b/model"])

    def test_unparsed_neither_raises_nor_lowers_the_other_seat(self) -> None:
        _, verdict, _ = self.run_with([CRITICAL, JudgeError("boom")], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)

    def test_a_clean_seat_next_to_an_unparsed_one_still_passes(self) -> None:
        _, verdict, _ = self.run_with([PASS, JudgeError("boom")])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)

    def test_unparsed_from_both_seats_is_unparsed(self) -> None:
        _, verdict, _ = self.run_with([JudgeError("boom"), JudgeError("boom")])
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(verdict.max_severity, "none")

    def test_confirmed_defect_triggers_one_repair_judged_by_both_seats(self) -> None:
        _, verdict, client = self.run_with(
            [CRITICAL, CRITICAL, PASS, PASS], repair=["fixed text"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.PASS)
        self.assertEqual(verdict.attempt, 1)
        self.assertEqual(client.call_count, 4)

    def test_exhausted_loop_returns_the_last_negative_verdict(self) -> None:
        _, verdict, _ = self.run_with(
            [CRITICAL, CRITICAL, CRITICAL, CRITICAL], repair=["still wrong"]
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)
        self.assertEqual(verdict.attempt, 1)

    def test_repair_that_changes_nothing_stops_the_loop(self) -> None:
        # No point re-judging identical text; that is a wasted call.
        _, verdict, client = self.run_with([CRITICAL, CRITICAL], repair=None)
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.REJECT)
        self.assertEqual(client.call_count, 2)

    def test_every_verdict_of_one_run_shares_the_run_id(self) -> None:
        unit, _, _ = self.run_with(
            [CRITICAL, CRITICAL, PASS, PASS], repair=["fixed text"]
        )
        self.assertEqual(
            len(set(unit.judge_verdicts.values_list("run_id", flat=True))), 1
        )

    def test_each_seat_votes_exactly_once_per_round(self) -> None:
        unit, _, _ = self.run_with(
            [CRITICAL, CRITICAL, PASS, PASS], repair=["fixed text"]
        )
        self.assertEqual(
            set(unit.judge_verdicts.values_list("attempt", "seat")),
            {(0, 1), (0, 2), (1, 1), (1, 2)},
        )
```

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py`

Expected: FAIL — `ModuleNotFoundError: weblate.trans.judge_loop`

### Step 3: Реализовать коллегию и петлю

Create `weblate/trans/judge_loop.py` с двумя публичными функциями.

`repair_target(unit, user) -> list[str] | None`:

- взять настройки машинерии проекта
  (`unit.translation.component.project.get_machinery_settings()`);
- взять движок `AutoForm.DEFAULT_ENGINE` (`"openrouter"`), если он
  сконфигурирован, иначе вернуть `None`;
- вызвать `engine.translate(unit, user)`
  (`weblate/machinery/base.py:847`), взять лучший результат;
- вернуть `None`, если результата нет или текст совпадает с текущим.

Улики судьи в промпт подставлять **не надо**: они уже там. Судейские
`Check`-строки записаны проекцией, а `weblate/machinery/llm.py:493-528`
кладёт их в `failing_checks` вместе с `get_description()`. Отсюда
жёсткий порядок внутри круга: `project_verdict(unit)` обязан отработать
**до** `repair_target`, иначе переводчик получит улики прошлой попытки.

`run_judge_loop(unit, *, user) -> JudgeVerdict`:

```text
run_id = uuid4()
for attempt in 0..JUDGE_MAX_REPAIR_ATTEMPTS:
    rows = [judge(unit, seat=1, attempt=attempt, run_id=run_id),
            judge(unit, seat=2, attempt=attempt, run_id=run_id)]
    project_verdict(unit)                 # один раз на круг, не на место
    verdict = collegium_verdict(rows)
    if verdict is PASS or UNPARSED: return verdict
    if attempt == JUDGE_MAX_REPAIR_ATTEMPTS: return verdict
    new_target = repair_target(unit, user)
    if new_target is None: return verdict          # чинить нечем
    write new_target to the unit          # состояние ставит вызывающий код
return verdict
```

Оба вызова безусловны. Ни один `if` между ними не появляется: как только
второе место начинает зависеть от вердикта первого, схема превращается в
отвергнутый каскад.

Модель места берётся из `JUDGE_MODEL_SEAT_1` / `JUDGE_MODEL_SEAT_2`.
Замер 2026-08-13 выбрал `deepseek/deepseek-v4-pro` и
`qwen/qwen3-235b-a22b-2507`. Замер 2026-08-14 на живом компоненте это
переоткрыл: второе семейство не дало ни одной уникальной находки при
двенадцатикратной цене. Модели на местах — настройка, финальный выбор
за замером на n=5 (`plans/2026-08-14-judge-severity-recalibration.md`).

Внутренний `judge(...)`:

- собирает `JudgeRequest` из `unit.source`, `unit.target`, кодов языков,
  `note` исходного юнита, глоссария через
  `weblate.glossary.models.get_glossary_terms(unit)` и имён активных
  чеков;
- зовёт `request_verdict(request, model=...)`;
- `JudgeError` → пишет `JudgeVerdict` с `verdict="unparsed"`,
  `max_severity="none"`, пустыми `errors`;
- иначе `verdict_for_severity(result.max_severity)`;
- всегда пишет `target_hash`, `context_hash`, `run_id`, `seat`,
  `attempt`, `judge_model`.

Проекцию `judge(...)` не зовёт: круг проецируется целиком после обоих
мест, иначе строка успеет постоять под вердиктом одного места.

`collegium_verdict(rows)` живёт в `judge_projection.py` (задача 4) —
её же читают карточка юнита и сама проекция.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_loop.py`

Expected: PASS (13 тестов)

### Step 5: Проверить, что тесты ловят баг

Две мутации подряд, каждая возвращается после прогона.

1. Дать месту 2 право снимать флаг: после первого вызова добавить
   `if rows[1].verdict == PASS: return rows[1]`.

   Expected: FAIL на `test_no_seat_may_lower_the_other` и
   `test_the_same_holds_when_the_strict_seat_votes_second`.

   Это и есть отвергнутая замером конструкция. Тест существует затем,
   чтобы она не вернулась случайно.

2. Передать одну и ту же модель на оба места.

   Expected: PASS. Одинаковые модели на местах — **допустимая
   конфигурация**, а не ошибка, и теста, который её запрещает, быть не
   должно.

   Первая редакция плана требовала здесь `test_seats_use_different_models`
   со ссылкой на дизайн (`:132-138`). Замер 2026-08-14
   (`docs/LLM-first/2026-08-14-st2-zh-judge-run.md`) снял основание:
   `deepseek` за два прогона не нашёл ничего сверх двух прогонов `qwen`
   при двенадцатикратной цене, а собственный шум каждой модели —
   11-16% юнитов. Что ставить на места, решает замер на n=5
   (`plans/2026-08-14-judge-severity-recalibration.md`), а код обязан
   выполнить любую валидную конфигурацию.

### Step 6: Коммит

```bash
git add weblate/trans/judge_loop.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): judge every string with a two-seat collegium"
```

---

## Задача 7. Режим `judge` в форме автоперевода

**Files:**

- Modify: `weblate/trans/forms.py:1328-1344`
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
    def modes(self, user) -> list[str]:
        form = AutoForm(obj=self.component, user=user)
        return [choice[0] for choice in form.fields["mode"].choices]

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
```

`judge` появляется там же, где `approved` (`forms.py:1333-1334`), потому
что ставит `state 30`.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_form.py`

Expected: FAIL — `judge` отсутствует в choices

### Step 3: Реализовать

Modify `weblate/trans/forms.py`. Добавить поле рядом с `threshold`
(строка 1225-1230):

```text
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

Расширить блок choices (строки 1328-1335):

```text
        if user is not None and (user.has_perm("unit.review", obj) or obj is None):
            choices.append(("approved", gettext("Add as approved translation")))
            choices.append(("judge", gettext("Add as translation with an LLM judge")))
```

Добавить поле в layout (строки 1338-1344), внутрь `Div` машинного
перевода или отдельным `Field("overwrite_existing")` после `SearchField("q")`.

**Не менять** `self.helper = FormHelper(self)` и не добавлять
`form_tag`: эта форма рендерится тегом `{% crispy %}` штатно, и
существующее поведение трогать нельзя.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_form.py`

Expected: PASS (3 теста)

### Step 5: Коммит

```bash
git add weblate/trans/forms.py weblate/trans/tests/test_judge_form.py
git commit -m "feat(judge): add the judge mode and overwrite switch to AutoForm"
```

---

## Задача 8. `AutoTranslate`: состояние по вердикту, fail-safe 10

Ключевое отличие режима от всех существующих: у них `target_state` —
константа на прогон (`autotranslate.py:360-364`), у судейского состояние
определяется вердиктом каждой строки.

**Files:**

- Modify: `weblate/trans/autotranslate.py:334-372`
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
from weblate.trans.models.judge import JudgeVerdict
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_APPROVED, STATE_FUZZY, STATE_TRANSLATED


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeAutoTranslateTest(ViewTestCase):
    def perform(self, verdict_kind, *, q="", overwrite=False):
        def fake_loop(unit, **kwargs):
            return JudgeVerdict.objects.create(
                unit=unit,
                verdict=verdict_kind,
                max_severity="none",
                judge_model="vendor-a/model",
                seat=1,
                target_hash="h",
                context_hash="c",
            )

        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q=q,
            mode="judge",
            overwrite_existing=overwrite,
        )
        with mock.patch(
            "weblate.trans.autotranslate.run_judge_loop", side_effect=fake_loop
        ):
            auto.process_judge()
        return auto

    def test_reject_lands_on_a_state_that_does_not_ship(self) -> None:
        from weblate.utils.state import FUZZY_STATES

        self.perform(JudgeVerdict.Verdict.REJECT)
        unit = self.get_unit()
        self.assertEqual(unit.state, STATE_FUZZY)
        self.assertIn(unit.state, FUZZY_STATES)

    def test_flag_ships_but_is_not_approved(self) -> None:
        self.perform(JudgeVerdict.Verdict.FLAG)
        self.assertEqual(self.get_unit().state, STATE_TRANSLATED)

    def test_unparsed_leaves_the_state_untouched(self) -> None:
        unit = self.get_unit()
        before = unit.state
        self.perform(JudgeVerdict.Verdict.UNPARSED)
        self.assertEqual(self.get_unit().state, before)

    def test_existing_translation_is_judged_not_rewritten(self) -> None:
        # Rule 1: without the overwrite switch a translated string keeps
        # its text; only the verdict may move its state.
        unit = self.get_unit()
        unit.translate(self.user, ["Human translation"], STATE_TRANSLATED)
        self.perform(JudgeVerdict.Verdict.PASS)
        self.assertEqual(self.get_unit().target, "Human translation")

    def test_fresh_translation_starts_at_needs_editing(self) -> None:
        # Rule 2 fail-safe: an interrupted run must never leave an
        # unjudged string in a shippable state.
        auto = AutoTranslate(
            translation=self.get_translation(),
            user=self.user,
            q="state:empty",
            mode="judge",
        )
        self.assertEqual(auto.fresh_translation_state, 10)
```

Проверь фактические помощники `ViewTestCase` (`get_translation`,
`get_unit`) и подставь реальные имена.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py`

Expected: FAIL — `AutoTranslate.__init__` не принимает `overwrite_existing`

### Step 3: Реализовать

Modify `weblate/trans/autotranslate.py`:

1. `AutoTranslate.__init__` — принять `overwrite_existing: bool = False`,
   сохранить в `self.overwrite_existing`. Добавить
   `self.fresh_translation_state = STATE_FUZZY` (значение `10`).
   Существующую цепочку `if self.mode == "fuzzy" ... elif "approved"`
   **не трогать**: в режиме `judge` `self.target_state` не используется.

2. Новый метод `process_judge(self) -> None`:

```text
units = self.get_units()
self.progress_steps = len(units)
for pos, unit in enumerate(units):
    self.set_progress(pos)
    needs_translation = not unit.translated or self.overwrite_existing
    if needs_translation:
        target = fetch machine translation for this unit
        if target: write it with state = self.fresh_translation_state
    if not unit.target:
        continue                       # нечего судить
    verdict = run_judge_loop(unit, user=self.user)
    state = state_for_verdict(verdict.verdict,
                              enable_review=self.translation.enable_review)
    if state is not None:
        self.update(unit, state, unit.get_target_plurals())
self.post_process()
```

3. `perform()` — ветка для судейского режима перед `auto_source`:

```text
        if self.mode == "judge":
            self.process_judge()
```

`self.update()` уже умеет всё нужное: пишет через `unit.translate`,
уважает право `unit.review` при `STATE_APPROVED` (строки 395-400),
считает `self.updated`. Переиспользуй его, не пиши второй путь записи.

### Step 4: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py`

Expected: PASS (5 тестов)

### Step 5: Проверить, что fail-safe ловит баг

Временно поставить `self.fresh_translation_state = STATE_TRANSLATED`.
Прогнать.

Expected: FAIL на `test_fresh_translation_starts_at_needs_editing`.
Вернуть код.

### Step 6: Коммит

```bash
git add weblate/trans/autotranslate.py weblate/trans/tests/test_judge_autotranslate.py
git commit -m "feat(judge): decide unit state per verdict in automatic translation"
```

---

## Задача 9. Celery, вью и счётчик строк

**Files:**

- Modify: `weblate/trans/tasks.py:964-1000`
- Modify: `weblate/trans/views/edit.py:1424-1530`
- Modify: `weblate/trans/autotranslate.py` (`BatchAutoTranslate`)
- Modify: `weblate/templates/` — шаблон формы автоперевода
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Написать падающий тест

Create `weblate/trans/tests/test_judge_views.py` с двумя проверками:

```text
    def test_judge_mode_requires_review_permission_at_the_view(self) -> None:
        # The form hides the choice; the view must refuse it anyway.
        self.user.is_superuser = False
        self.user.save()
        response = self.client.post(
            self.translation.get_absolute_url().replace("/translations/", "/auto/"),
            {"mode": "judge", "q": "state:empty", "auto_source": "mt"},
            follow=True,
        )
        self.assertNotContains(response, "judge")

    def test_form_shows_how_many_strings_a_run_would_touch(self) -> None:
        # Design :392-394 — mode=judge on a big component is real money.
        response = self.client.get(self.translation.get_absolute_url())
        self.assertContains(response, "id_auto_row_count")
```

Точный URL автоперевода возьми из `weblate/trans/views/edit.py:1424` и
из `urls.py`; не угадывай.

### Step 2: Прогнать и убедиться, что падает

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py`

Expected: FAIL

### Step 3: Пробросить режим и чекбокс

1. `weblate/trans/tasks.py` — в сигнатуру `auto_translate` добавить
   `overwrite_existing: bool = False` и передать в конструктор
   `BatchAutoTranslate` / `AutoTranslate`.
2. `weblate/trans/views/edit.py` — в месте, где собираются аргументы
   задачи из `form.cleaned_data`, добавить `overwrite_existing`.
3. `check_auto_translate_permission` (`autotranslate.py:261`) —
   режим `judge` требует `unit.review`, как `approved`.

### Step 4: Счётчик строк

В шаблоне формы автоперевода вывести число строк, попадающих под текущий
`q`, с `id="id_auto_row_count"`, и обновлять его при смене фильтра тем же
механизмом, каким уже живёт поле поиска. Если готового AJAX-эндпоинта
нет — рендерить серверный счётчик при загрузке страницы и отдельно
подписать, что число отражает фильтр на момент открытия. Точность здесь
менее важна, чем порядок величины перед тратой денег.

Рядом со счётчиком показать, что коллегия делает **два вызова на
строку**: оба места судят каждую строку, второе не по эскалации.
Прогон 2026-08-14 на 124 строках zh_Hans парой
`deepseek-v4-pro` + `qwen3-235b` стоил $0.068, то есть $0.00055 за
строку. Второе место добавляет к деньгам около 10% (`$0.006` против
`$0.062`), но удваивает число запросов — и упирается прогон именно в
них: те же 124 строки заняли 13 минут.

### Step 5: Прогнать тесты

Run: `./rundev.sh test weblate/trans/tests/test_judge_views.py`

Expected: PASS

### Step 6: Прогнать смежный суит

Run: `./rundev.sh test weblate/trans/tests/test_autotranslate.py`

Expected: без новых падений. **Этот файл флакует под xdist**: при
падениях сначала прогони его же на неизменённом `HEAD~1` и сравни, а
потом уже считай регрессией.

### Step 7: Коммит

```bash
git add weblate/trans/tasks.py weblate/trans/views/edit.py \
  weblate/trans/autotranslate.py weblate/templates/ \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): wire the judge mode through Celery and the view"
```

---

## Задача 10. Исключить судейские чеки из «Things to check»

Вероятностное мнение не должно стоять в одной карточке с
детерминированным фактом под тем же красным `alert.svg`.

**Files:**

- Modify: `weblate/trans/models/unit.py` (новое свойство)
- Modify: `weblate/templates/translate.html:593,619`
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Написать падающий тест

```text
    def test_judge_check_is_absent_from_things_to_check(self) -> None:
        unit = self.get_unit()
        Check.objects.create(unit=unit, name="judge-reject", dismissed=False)
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Judge: rejected")

    def test_judge_check_still_reaches_navigation_and_filters(self) -> None:
        # The projection must keep working for check:judge-reject.
        unit = self.get_unit()
        Check.objects.create(unit=unit, name="judge-reject", dismissed=False)
        self.assertIn("judge-reject", unit.all_checks_names)

    def test_deterministic_check_still_renders(self) -> None:
        unit = self.get_unit()
        Check.objects.create(unit=unit, name="same", dismissed=False)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Unchanged translation")

    def test_card_is_hidden_when_only_a_judge_check_remains(self) -> None:
        # Otherwise the card renders empty.
        unit = self.get_unit()
        Check.objects.create(unit=unit, name="judge-reject", dismissed=False)
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Things to check")
```

Последний тест самый ценный: именно его пропускает наивная правка,
которая фильтрует цикл `:619`, но не условие показа `:593`.

### Step 2: Прогнать и убедиться, что падает

Expected: FAIL на первом и четвёртом тестах.

### Step 3: Реализовать

Добавить в `weblate/trans/models/unit.py` рядом с `all_checks`
(строка 2082):

```text
    @property
    def deterministic_checks(self) -> list[Check]:
        """Checks shown as facts: judge verdicts render in their own card."""
        from weblate.trans.judge_projection import JUDGE_CHECKS

        return [check for check in self.all_checks if check.name not in JUDGE_CHECKS]
```

В `weblate/templates/translate.html` заменить `unit.all_checks` на
`unit.deterministic_checks` в обоих местах — в условии показа карточки
(строка 593) и в цикле (строка 619). Остальные условия строки 593 не
трогать.

### Step 4: Прогнать тесты

Expected: PASS (4 теста)

### Step 5: Убедиться, что тест на пустую карточку ловит баг

Вернуть `unit.all_checks` в условие строки 593, оставив фильтр в цикле.
Прогнать.

Expected: FAIL на `test_card_is_hidden_when_only_a_judge_check_remains`.
Вернуть код.

### Step 6: Коммит

```bash
git add weblate/trans/models/unit.py weblate/templates/translate.html \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): keep judge verdicts out of the deterministic check card"
```

---

## Задача 11. Карточка «Оценка ИИ» на юните

**Files:**

- Create: `weblate/templates/snippets/judge-verdict.html`
- Modify: `weblate/templates/translate.html:591-599`
- Modify: `weblate/trans/views/edit.py` (контекст юнита)
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Написать падающий тест

```text
    def test_card_shows_the_verdict_and_the_model(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.REJECT,
                          max_severity="critical")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "vendor/model-a")

    def test_card_never_shows_a_score(self) -> None:
        # Design :202-203 — a number the producer cannot verify creates
        # false precision.
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.FLAG, max_severity="major")
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "confidence")

    def test_stale_verdict_is_marked_not_hidden(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.REJECT,
                          max_severity="critical", target_hash="stale")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "previous version")

    def test_unparsed_is_not_shown_as_a_verdict(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.UNPARSED)
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "rejected")

    def test_no_verdict_means_no_card(self) -> None:
        response = self.client.get(self.get_unit().get_absolute_url())
        self.assertNotContains(response, "id_judge_card")
```

### Step 2: Прогнать и убедиться, что падает

Expected: FAIL

### Step 3: Реализовать

1. Во вью юнита положить в контекст `judge_round` — `latest_round(unit)`,
   `judge_verdict` — `collegium_verdict(judge_round)`, и флаг
   `judge_verdict_stale`. Карточка показывает вердикт коллегии, но
   перечисляет мнения обоих мест: согласие двух семейств — сильный
   сигнал, расхождение — слабый, и скрывать его дизайн запрещает
   (`:295-296`).
2. `weblate/templates/snippets/judge-verdict.html` — карточка по образцу
   существующей `card` из `translate.html:594-598`, с `id="id_judge_card"`.
   Содержимое по таблице дизайна (`:188-195`):
   - `pass` / `minor` — свёрнута, «Принято · модель · когда»;
   - `flag` / `reject` — развёрнута, список ошибок с категорией и
     severity, оба места коллегии и явная отметка, если они разошлись;
     плашка «не уйдёт в сборку» для `reject`;
   - протух — серая, «relates to a previous version», кнопка «пересудить»
     появится в плане 3, здесь только текст;
   - `unparsed` — серая, «ответ судьи не разобран», **не** как вердикт.
3. Подключить `{% include %}` в `translate.html` в колонку сайдбара
   **перед** блоком «Things to check» (строка 593), как требует дизайн
   (`:155`).

Оформление: контур — мнение, заливка — факт. Не переиспользуй красный
`alert.svg` детерминированных чеков. По `ACCESSIBILITY.md` состояние не
должно кодироваться только цветом: рядом с цветом обязателен текст или
иконка, и карточка должна быть достижима с клавиатуры.

### Step 4: Прогнать тесты

Expected: PASS (5 тестов)

### Step 5: Смоук в браузере

Открыть <http://localhost:3001/> (admin/admin), найти юнит с записанным
вердиктом, убедиться глазами: карточка выше «Things to check», числа
нет, судейский чек в «Things to check» не появился.

Кликать реальные контролы, а не отправлять формы программно.

### Step 6: Коммит

```bash
git add weblate/templates/snippets/judge-verdict.html \
  weblate/templates/translate.html weblate/trans/views/edit.py \
  weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): show the verdict card on the unit page"
```

---

## Задача 12. Обратный перевод в теле формы

**Files:**

- Modify: `weblate/templates/translate.html:100-108`
- Test: `weblate/trans/tests/test_judge_views.py`

### Step 1: Написать падающий тест

```text
    def test_back_translation_renders_next_to_the_string(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.FLAG, max_severity="major",
                          back_translation="The door is blocked by the DOORS")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "The door is blocked by the DOORS")

    def test_back_translation_is_labelled_as_a_reconstruction(self) -> None:
        # It must never be mistaken for a reference translation.
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.FLAG, max_severity="major",
                          back_translation="Whatever")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Approximate reconstruction")

    def test_stale_verdict_does_not_render_its_back_translation(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, JudgeVerdict.Verdict.FLAG, max_severity="major",
                          back_translation="Outdated", target_hash="stale")
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Outdated")
```

Третий тест защищает от худшего варианта: показать реконструкцию
**прежнего** текста рядом с новым.

### Step 2: Прогнать и убедиться, что падает

Expected: FAIL

### Step 3: Реализовать

В `weblate/templates/translate.html` сразу после цикла `secondary`
(строка 107) добавить блок той же формы `<div class="form-group">` с
подписью «Approximate reconstruction», рендерящий
`judge_verdict.back_translation` только когда вердикт активен (не
протух) и текст непустой.

Слот `secondary` — существующий нативный паттерн «та же строка в другом
рендере для справки», поэтому блок должен выглядеть как его сосед, а не
как новая сущность.

### Step 4: Прогнать тесты

Expected: PASS (3 теста)

### Step 5: Коммит

```bash
git add weblate/templates/translate.html weblate/trans/tests/test_judge_views.py
git commit -m "feat(judge): render the back translation beside the string"
```

---

## Задача 13. Документация и приёмка

**Files:**

- Modify: `docs/changes.rst` (верхняя, невыпущенная секция)
- Modify: `docs/admin/machine.rst` или `docs/admin/checks.rst` — куда
  логичнее ложится описание судейского режима; выбрать одну страницу и
  расширить существующий раздел, а не создавать новый
- Modify: `deploy/environment.example` — переменные `WEBLATE_JUDGE_*`

### Step 1: Changelog

Добавить одну краткую запись в верхнюю секцию `docs/changes.rst` со
ссылкой на документацию режима. Не трогать секции выпущенных версий.

### Step 2: Документация

Описать: что делает режим `judge`, какие настройки его включают, что
`critical` удерживается штатным `WITHOUT_NEEDS_EDITING`, что чекбокс
перезаписи выключен по умолчанию. Стиль — как на соседней странице,
предложения короткие, ссылки через `:setting:` / `:ref:`.

### Step 3: Проверка модели угроз

Открыть `docs/security/threat-model.rst`, раздел «Conditions that change
this model». Новый исходящий интеграционный класс (OpenRouter из
`weblate/trans/judge.py`) под него **подпадает**: обновить документ в
этом же изменении.

### Step 4: Полный линт

Run:

```bash
uv run prek run --all-files
```

Expected: pass. Если `typos` или `rumdl` ругаются на чужие файлы — не
трогать их, коммитить только свои.

### Step 5: Типы и pylint по изменённым модулям

Run:

```bash
uv run pylint weblate/trans/judge.py weblate/trans/judge_loop.py \
  weblate/trans/judge_projection.py weblate/trans/models/judge.py
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Expected: без новых ошибок относительно базовой ветки.

### Step 6: Приёмочный прогон целиком

Критерий готовности из дизайна (`:497`): «прогон на фильтре даёт
построчный вердикт; `critical` не уходит в сборку; навигация через
`check:judge-*`».

1. В dev-инстансе включить судью:

```bash
WEBLATE_JUDGE_ENABLED=1 WEBLATE_JUDGE_OPENROUTER_KEY=... \
WEBLATE_JUDGE_MODEL_SEAT_1=... WEBLATE_JUDGE_MODEL_SEAT_2=... \
WEBLATE_PORT=3001 ./rundev.sh
```

2. Выбрать маленький компонент, в форме автоперевода взять режим
   «Add as translation with an LLM judge», `q` на 5-10 строк, чекбокс
   перезаписи выключен, запустить.
3. Проверить в UI:
   - у каждой строки появился вердикт (карточка «Оценка ИИ»);
   - строки с `critical` стоят в состоянии «Needs editing»;
   - поиск `check:judge-reject` их находит;
   - на странице git-статуса они посчитаны как пропущенные политикой;
   - судейских строк нет в карточке «Things to check».
4. Записать наблюдение (сколько строк, на скольких места коллегии
   разошлись, сколько починок) в `docs/misc/` отдельным файлом. Прогон
   на живом компоненте уже есть — `docs/LLM-first/2026-08-14-st2-zh-judge-run.md`,
   124 строки zh_Hans, согласие судей 72.6%, — но он сделан внешним
   скриптом мимо продукта; этот будет первым через сам режим.

### Step 7: Коммит и пуш

```bash
git add docs/
git commit -m "docs(judge): document the judge translation mode"
/usr/bin/git push -u origin plan/judge-verdict-core
```

`/usr/bin/git` намеренно: brew-git на этой машине зависает на
`osxkeychain` при пуше.

---

## Приёмка плана целиком

- [ ] Миграция `0099` применяется и откатывается.
- [ ] `JudgeVerdict` накапливает записи, не перезаписывает.
- [ ] `pass` без `translation_review` даёт `20`, а не `30`.
- [ ] `critical` даёт состояние из `FUZZY_STATES`, и это проверено
      импортом из штатного модуля, а не константой в тесте.
- [ ] `unparsed` нигде не выглядит как `flag` или `reject`.
- [ ] Протухший вердикт не проецируется в `Check` и не показывает свой
      обратный перевод.
- [ ] Каждое место берёт свою настроенную модель; одинаковые модели на
      местах — валидная конфигурация, а не ошибка.
- [ ] Ни одно место не понижает вердикт другого: `flag` + `pass` даёт
      `flag`, и это зафиксировано тестом.
- [ ] Улики судьи доезжают до промпта починки без отдельного механизма.
- [ ] Судейские чеки не рендерятся в «Things to check», но находятся
      через `check:judge-*`.
- [ ] Свежепереведённая, но несудившаяся строка остаётся в `10`.
- [ ] Существующий перевод без чекбокса перезаписи не переписывается.
- [ ] Каждая мутация из шагов «убедиться, что тест ловит баг» (задачи 4,
      6, 8, 10) действительно роняет свой тест.

## Что этот план сознательно не закрывает

Гейты первого боевого прогона на проде (дизайн `:517-529`) остаются в
силе и **этим планом не снимаются**: B2' с измеренными порогами,
прод-backfill автофиксов `--apply`, совпадение отпечатка автофиксов,
отдельное согласование траты. План даёт работающий механизм, а не
разрешение потратить деньги на проде.
