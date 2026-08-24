# План: слой 0 — автофикс добавленного терминала и backfill-команда

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.

**Goal:** снимать добавленный переводчиком-моделью терминал `!`, `?`, `:`
(в дополнение к уже отгруженной точке) и один раз пройтись этим же
механизмом по историческим юнитам management-командой
`reapply_autofixes`.

**Architecture:** один автофикс `RemoveAddedFinalStop` расширяется до
четырёх ASCII-знаков и делегирует решение штатным чекам Weblate
(`end_stop`, `end_colon`, `end_question`, `end_exclamation`) по правилу
«снятие обязано **строго сократить** множество падающих терминальных
чеков». Backfill — отдельная `WeblateComponentCommand`, которая по
умолчанию ничего не пишет, а при `--apply` перепроверяет каждый юнит под
блокировкой строки и коммитит по корню репозитория только собственные
изменения.

**Tech Stack:** Python 3.13, Django, Weblate autofix/check registries,
pytest, PostgreSQL, git-бэкенд Weblate.

---

## Статус

Согласован 2026-08-11; переписан 2026-08-12 после инженерного ревью
(17 решений, D1–D17), затем **перемерен на проде 2026-08-12** после ревью
самой редакции.

Числа исходной редакции (`!` 39, `?` 37, `:` 7 = 83; ~1380 исторических
юнитов) **подтверждены**. Ошибочной была промежуточная редакция, которая
объявила их невоспроизводимыми: она мерила по
`dev-docker/data/col4-b0-units.jsonl` — одна языковая пара одного прогона,
3941 юнит из 22 750 — и выдала срез за инстанс.

### Что изменилось против редакции 2026-08-11

| Было | Стало | Почему |
|---|---|---|
| «83 юнита» как объём починки | 92 по source-blind предикату, **70** чинится правилом | предикат «последний символ» не знает языковых веток чеков; расхождение сидит в `?` (42 против 26) |
| «~1380 исторических юнитов» | **1388**, из них `removed-final-stop` 1366 | замер 2026-08-12, суточный дрейф |
| приёмка `«…dépêche !»`, allowlist кавычек в target | target **не** разворачивается, источник разворачивается | замер: 13 из 14 таких юнитов — `en_US` с точкой внутри кавычки по норме; правило их ломало |
| «снимать пару `[NBSP\|NNBSP] + знак`» | снимать знак и **любые** смежные пробельные | обычный U+0020 иначе остаётся висеть |
| `unit.translate(...)` безусловно | preflight + повторный расчёт под `select_for_update` | безусловный вызов ставит `automatically_translated` и пишет юниты без дефекта |
| «фильтры `--project` / `--component`» | штатный `WeblateComponentCommand` | селекция, валидация и `--all` уже реализованы |
| «покомпонентные коммиты» | протокол по корню репозитория + runbook подготовки | `commit_pending()` захватывает связанные компоненты; на живом инстансе pending есть почти всегда |
| доставка = `cp` в `dev-docker/data/python` | fail-closed проверка активного реестра | в dev-контейнере активен только `line-separator-spacing`, на проде — все три |

**Итог (2026-08-15): реализован и проверен.** Автофиксы, команда
`reapply_autofixes`, row locks, scoped repository commit, fingerprint,
production backfill и повторный dry-run завершены успешно.

## Замер (воспроизводимый)

Полные цифры, метод и все спорные строки:
**`docs/llm-first/measurements/2026-08-12-autofix-terminal-punctuation.md`**.
Скрипт: `analysis/probes/autofix-backfill-scan.py`.
Артефакт: `analysis/data/autofix-backfill-2026-08-12.json`.

Прод `hcgameloc-weblate-1`, 2026-08-12, только чтение: 9 компонентов,
22 750 юнитов, из них **13 559** подпадают под backfill (не read-only, не
глоссарий, не исходный язык).

**Объём backfill — основание для команды:**

| Фикс | Юнитов |
|---|---:|
| `removed-final-stop` | 1366 |
| `french-punctuation-spacing` | 19 |
| `zero-width-space` | 7 |
| `end-ellipsis` | 1 |
| **затронуто юнитов** | **1388** |

**Прибавка от расширения правила** (чинит предложенное, не чинит
отгруженное): `!` 37, `?` 26, `:` 7 = **70**. По компонентам: `CoL4/data`
65, `Heart Abyss/temple` 3, `CoL4/LocalizeCommon` 2.

Живые падающие терминальные чеки: `end_stop` 1455, `end_exclamation` 93,
`end_question` 38, `end_interrobang` 18, `end_colon` 7.

Два вывода, которые изменили сам дизайн правила:

1. **Source-blind предикат — верхняя граница, не объём.** «Target
   заканчивается знаком, которого нет в конце источника» даёт 92 против 70.
   Это ровно ошибка HT001/PH002 из разбора Cathedral
   (`docs/llm-first/research/2026-08-11-cathedral-localizer-analysis.md`, раздел 6):
   правило, глядящее только в target, пишет дефект источника на переводчика.
2. **Разворачивать кавычки в target нельзя.** Первая редакция правила давала
   87 вместо 70, и все 17 лишних — порча: `…the inscription "armory."` →
   `…"armory"`. 13 из 14 просмотренных `en_US`, точка внутри кавычки по
   американской норме. Источник разворачивать нужно, и только это
   направление: оно защищает `с криком "Еретик!"`.

Дамп `col4-b0-units.jsonl` сохраняет одну роль — проверка формы правила на
именованных строках с человеческой разметкой
(`analysis/data/col4-b0-annotations.jsonl`). Он собран прототипом с
`fix_id = "terminal-extension-!?:"`, которого в коде нет, поэтому golden set
нормализован конфигурацией, отсутствующей в проде — задача 7.

## Что уже есть (переиспользовать, не переписывать)

| Механизм | Файл | Роль |
|---|---|---|
| `RemoveAddedFinalStop` | `weblate_customization/src/weblate_customization/autofixes.py:58` | расширяем его, второй класс не заводим |
| `AddFrenchPunctuationSpacing` | там же, `:93` | ставит U+00A0 перед `:` и U+202F перед `!`/`?` |
| `fix_target()` | `weblate/trans/autofixes/__init__.py:38` | прогон всех активных автофиксов по порядку |
| `AutoFix.fix_target()` | `weblate/trans/autofixes/base.py:39` | разбор множественных форм, вызывает `fix_single_target` |
| Терминальные чеки | `weblate/checks/chars.py:278,334,369,415` | вся языковая логика (CJK, `hy`, `hi/bn/or`, `sat`, `my`, интерробанги) |
| `BaseCheck.check_chars()` | `weblate/checks/base.py:230` | сравнивает **только** `source[-1]` и `target[-1]` |
| `Unit.translate()` | `weblate/trans/models/unit.py:2358` | единственный поддерживаемый путь записи |
| `WeblateComponentCommand` | `weblate/utils/management/base.py:46` | селекция `--all` / `project` / `project/component` + валидация |
| `Component.commit_pending()` | `weblate/trans/models/component.py:3721` | коммит; `perform_on_link` уводит на корень репозитория |
| `PendingUnitChange.objects.for_component()` | `weblate/trans/models/pending.py:139` | инвариант «чужих pending нет» |
| `WeblateComponentCommandMixin` | `weblate/trans/tests/test_commands.py:407` | готовые тесты селекции |
| `make_unit()` | `weblate/trans/tests/factories.py:129` | несохранённые юниты для `SimpleTestCase` |

## Инварианты (нарушение любого — баг)

1. **Строгое сокращение.** Автофикс снимает знак только если множество
   падающих терминальных чеков после снятия — строгое подмножество
   множества до снятия. Это одновременно закрывает «не помогло» и
   «поменяли один дефект на другой».
2. **Только измеренная область.** ASCII `. ! ? :`. Полноширинные и
   языкоспецифичные знаки не трогаем — они не встречались в замере.
3. **Обёртки непрозрачны в target.** Если target заканчивается не самим
   знаком — кавычкой, `</b>`, `]`, `%KEY%`, `{0}` — строка неприкосновенна.
   Разворачивается только источник, и только от закрывающих кавычек.
4. **Никогда не опустошать target.** Если после снятия остаётся пустая
   строка — не трогаем.
5. **Запись только при реальном изменении.** Нет изменения target — нет
   ни `Change`, ни `PendingUnitChange`, ни `automatically_translated`.
6. **Свежесть.** Значение, которое пишется, вычислено под
   `select_for_update` на той же строке в той же транзакции.
7. **Область.** `propagate=False`; ни один юнит вне выборки не меняется.
8. **Чужое не коммитим.** Коммит по корню репозитория выполняется, только
   если во всей его семье нет pending-изменений, кроме наших.
9. **Fail closed.** Нет требуемого автофикса в активном реестре — команда
   падает, а не рапортует «ноль изменений».
10. **State.** State сохраняется; понижать его вправе только
    `enforced_checks` штатным путём.

## Граф задач

```text
Задача 1  решение по ":" (замер готов, нужен вердикт владельца)
    |
Задача 2  автофикс терминала  ──────────────┐
    |                                        |
Задача 3  apply_autofixes() + fingerprint    |
    |                                        |
Задача 4  команда: селекция и dry-run        |
    |                                        |
Задача 5  --apply под локом                  |
    |                                        |
Задача 6  коммит по корню репозитория        |
    |                                        |
Задача 7  parity с судьёй  <─────────────────┘
    |
Задача 8  доставка в dev + smoke
    |
Задача 9  changelog, docs, финальные проверки
```

Строго последовательно: задачи 4–6 правят один файл команды и один файл
тестов.

---

## Задача 1. Решение по двоеточию (D11) — замер выполнен

Замер прода выполнен 2026-08-12; скрипт, артефакт и все семь строк лежат в
репозитории (`docs/llm-first/measurements/2026-08-12-autofix-terminal-punctuation.md`, раздел 5).
Писать сканер больше не нужно — нужно **решение владельца**.

Все семь юнитов с добавленным двоеточием — турецкий, компонент `CoL4/data`,
одна конструкция: русский источник заканчивается глаголом речи без
двоеточия, турецкий перевод ставит двоеточие перед прямой речью, которая
лежит следующим юнитом и склеивается движком.

| Юнит | Источник (хвост) | Перевод (хвост) |
|---|---|---|
| 11049 | `Кто-то из курсантов вскрикнул` | `Harbiyelilerden biri bağırdı:` |
| 12012 | `а суть вот в чём` | `Asıl konu şu:` |
| 12628 | `обратился к вам` | `…sana hitap ediyor:` |
| 12629 | `прицепил значок к вашей форме` | `…sana hitap ediyor:` |
| 12875 | `вторая схема, отвечающая за включение приборов` | `…ikinci sigorta ise şu şekilde:` |
| 13739 | `Поспевая за Настей, вы спрашиваете` | `…ayak uydurarak soruyorsun:` |
| 14245 | `Он хватает вас за рубашку и говорит` | `…tuttu ve dedi ki:` |

Это одно решение в одном языке, а не семь наблюдений.

**Рекомендация плана: `:` из правила исключить.** Основание то же, что у
найденного замером случая `en_US` (точка внутри кавычки): источник опускает
двоеточие из-за движковой склейки, а не потому, что оно лишнее. Отдача —
7 юнитов из 70; риск — типографский регресс в языке, который никто из
участников не вычитывает.

**Развилка, от неё зависит задача 2:**

- `:` исключён (рекомендуется) → `TERMINAL_MARKS = ".!?"`, тест
  `test_removes_added_colon_with_nbsp` заменяется на
  `test_colon_is_out_of_scope` с обратным утверждением, прибавка правила
  становится 63 юнита;
- `:` разрешён → `TERMINAL_MARKS = ".!?:"`, тесты как написаны, прибавка 70.

Дальше по плану `:` **включён**, чтобы не переписывать код при любом исходе:
исключение — это удаление одного символа из константы и инверсия одного
теста.

## Задача 2. Автофикс терминала `!`, `?`, `:` (D1, D16, D11)

**Files:**

- Modify: `weblate_customization/src/weblate_customization/autofixes.py:14-32,58-90`
- Test: `weblate_customization/tests/test_autofixes.py:65-119`

### Step 1: Написать падающие тесты

Дописать в `weblate_customization/tests/test_autofixes.py` внутрь класса
`RemoveAddedFinalStopTest` (существующие семь тестов не трогать — они
фиксируют поведение точки и обязаны продолжать проходить):

```python
    def test_removes_added_exclamation_with_narrow_space(self) -> None:
        # Prod shape (unit 178826): the model added the mark and
        # AddFrenchPunctuationSpacing already put U+202F in front of it.
        unit = make_unit(source="А давайте", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Allons-y\u202f!"], unit), (["Allons-y"], True)
        )

    def test_removes_added_colon_with_nbsp(self) -> None:
        # Prod shape (unit 179518).
        unit = make_unit(source="Старик сделал небольшую паузу и продолжил", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Le vieil homme fait une pause et reprend\u00a0:"], unit),
            (["Le vieil homme fait une pause et reprend"], True),
        )

    def test_removes_added_question_with_plain_space(self) -> None:
        unit = make_unit(source="Можно ли выпустить плесень", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Peut-on libérer la moisissure ?"], unit),
            (["Peut-on libérer la moisissure"], True),
        )

    def test_keeps_a_terminal_inside_a_closing_quote(self) -> None:
        # Measured on prod: reaching behind the quote repaired 17 units and
        # broke 13 of them. `…the inscription "armory."` is correct en_US
        # typography, and the rule must not touch it.
        unit = make_unit(source="Впереди двери с надписью \u00abоружейная\u00bb", code="en")
        target = 'Ahead are large double doors with the inscription "armory."'
        self.assertEqual(self.fix.fix_target([target], unit), ([target], False))

    def test_keeps_a_terminal_inside_a_closing_guillemet(self) -> None:
        unit = make_unit(source="Скорее, депеша", code="fr")
        target = "\u00abVite, la d\u00e9p\u00eache\u202f!\u00bb"
        self.assertEqual(self.fix.fix_target([target], unit), ([target], False))

    def test_keeps_terminal_when_the_quoted_source_has_it(self) -> None:
        # Prod shape (unit 180448): the source mark hides behind a quote, so
        # the target mark is correct even though end_exclamation fails. This is
        # the one direction that IS unwrapped - the source side.
        unit = make_unit(source='Старейшина бежит на вас с криком "Еретик!"', code="fr")
        target = "L'Ancien se précipite sur toi en criant Hérétique\u202f!"  # codespell:ignore
        self.assertEqual(self.fix.fix_target([target], unit), ([target], False))

    def test_keeps_a_mark_wrapped_in_markup(self) -> None:
        unit = make_unit(source="Скорее", code="fr")
        self.assertEqual(
            self.fix.fix_target(["<b>Vite\u202f!</b>"], unit),
            (["<b>Vite\u202f!</b>"], False),
        )

    def test_keeps_full_width_marks(self) -> None:
        unit = make_unit(source="Скорее", code="ja")
        self.assertEqual(self.fix.fix_target(["急いで！"], unit), (["急いで！"], False))

    def test_keeps_repeated_marks(self) -> None:
        unit = make_unit(source="Les pionniers rient", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Les pionniers rient!!"], unit),
            (["Les pionniers rient!!"], False),
        )

    def test_keeps_interrobang(self) -> None:
        unit = make_unit(source="Ты серьёзно", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Tu es sérieux ?!"], unit), (["Tu es sérieux ?!"], False)
        )

    def test_refuses_to_empty_the_target(self) -> None:
        unit = make_unit(source="Осторожно, ликвидаторы", code="fr")
        self.assertEqual(self.fix.fix_target(["!"], unit), (["!"], False))

    def test_keeps_a_double_terminal_it_cannot_settle(self) -> None:
        # Dropping the dot would expose a question mark the source lacks:
        # the failing-check set changes instead of shrinking.
        unit = make_unit(source="Les pionniers rient", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Vraiment?."], unit), (["Vraiment?."], False)
        )

    def test_mark_specific_ignore_flag_disables_the_fix(self) -> None:
        unit = make_unit(
            source="А давайте", code="fr", flags="ignore-end-exclamation"
        )
        self.assertEqual(
            self.fix.fix_target(["Allons-y\u202f!"], unit), (["Allons-y\u202f!"], False)
        )

    def test_fixes_every_plural_form(self) -> None:
        unit = make_unit(
            source=["Attends", "Attendez"], target=["", ""], code="fr"
        )
        self.assertEqual(
            self.fix.fix_target(["Attends\u202f!", "Attendez\u202f!"], unit),
            (["Attends", "Attendez"], True),
        )
```

И новый класс в конце файла — порядок в `AUTOFIX_LIST` и идемпотентность:

```python
class TerminalAndFrenchSpacingOrderTest(SimpleTestCase):
    """Terminal removal runs before French spacing and survives a second pass."""

    fixes = (RemoveAddedFinalStop(), AddFrenchPunctuationSpacing())

    def run_fixes(self, target: list[str], unit) -> list[str]:
        for fix in self.fixes:
            target, _changed = fix.fix_target(target, unit)
        return target

    def test_no_dangling_space_survives_the_pair(self) -> None:
        unit = make_unit(source="А давайте", code="fr")
        first = self.run_fixes(["Allons-y!"], unit)
        self.assertEqual(first, ["Allons-y"])

    def test_second_pass_changes_nothing(self) -> None:
        unit = make_unit(source="А давайте", code="fr")
        first = self.run_fixes(["Allons-y\u202f!"], unit)
        self.assertEqual(self.run_fixes(first, unit), first)
```

### Step 2: Убедиться, что тесты падают

Run: `./rundev.sh test weblate_customization/tests/test_autofixes.py -k "terminal or exclamation or colon or question or guillemet or plural or interrobang or repeated or empty"`
Expected: FAIL — новые тесты падают (`(['Allons-y\u202f!'], False) != (['Allons-y'], True)`),
семь старых тестов точки проходят.

Хостовая альтернатива (нужен git ≥ 2.46 из brew, иначе git-тесты
молча скипаются — для этого файла не критично, он `SimpleTestCase`):
`DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate_customization/tests/test_autofixes.py -v`

### Step 3: Реализация

Заменить блок импортов и константы (`autofixes.py:14-32`):

```python
from weblate.checks.chars import (
    FRENCH_PUNCTUATION_MISSING_RE_NBSP,
    FRENCH_PUNCTUATION_MISSING_RE_NNBSP,
    EndColonCheck,
    EndExclamationCheck,
    EndQuestionCheck,
    EndStopCheck,
    PunctuationSpacingCheck,
)
from weblate.checks.utils import highlight_string
from weblate.trans.autofixes.base import AutoFix
from weblate_customization.checks import (
    SEPARATOR_SPACE,
    GameLineBreakCheck,
    separator_is_tight,
)

if TYPE_CHECKING:
    from weblate.checks.base import TargetCheck
    from weblate.trans.models import Unit

HUGGING_SEPARATOR = re.compile(rf"{SEPARATOR_SPACE}*\${SEPARATOR_SPACE}*")
SPACING_CHARACTERS = re.compile(r"[ \u00a0\u202f\u2009]")
TRAILING_SPACING = re.compile(r"[ \u00a0\u202f\u2009]+$")

# ASCII only, as measured: docs/llm-first/measurements/2026-08-12-autofix-terminal-punctuation.md.
TERMINAL_MARKS = ".!?:"
# Closing quotes stripped from the SOURCE before comparing, so a source mark
# hiding behind one is still seen (prod unit 180448, `с криком "Еретик!"`).
# The target is never unwrapped - see the class docstring for the measurement
# that rejected it.
CLOSING_QUOTES = '»"”'
TRAILING_QUOTES = re.compile(rf"[{re.escape(CLOSING_QUOTES)}]+$")
# One instance each: checks are stateless, and the registry keeps singletons too.
TERMINAL_CHECKS: tuple[TargetCheck, ...] = (
    EndStopCheck(),
    EndColonCheck(),
    EndQuestionCheck(),
    EndExclamationCheck(),
)
```

Заменить класс целиком (`autofixes.py:58-90`):

```python
class RemoveAddedFinalStop(AutoFix):
    """
    Drop terminal punctuation the source does not have.

    An LLM adds one to roughly a third of the strings of a game corpus, where
    captions and button labels are unpunctuated on purpose. The four end checks
    decide, so every language branch they implement - the short-source
    shortcut, the ellipsis rule, interrobangs, CJK, Armenian, Devanagari,
    Santali, Burmese - is honoured without being restated here.

    Two rules keep this narrow. The removal has to shrink the set of failing
    terminal checks strictly: a removal that settles nothing, or that trades
    one mismatch for another, is not a repair. And only the SOURCE is
    unwrapped from closing quotes, never the target. Unwrapping the source is
    what keeps a source mark hidden behind a quote (``с криком "Еретик!"``)
    from reading as punctuation the model invented. Unwrapping the target was
    measured on prod and rejected: it reached 17 more units and degraded 13 of
    them, all ``en_US`` strings placing the full stop inside the quotes per US
    convention (``the inscription "armory."``). The opposite direction, a mark
    lost in translation, is not repairable and stays a check.
    """

    fix_id = "removed-final-stop"
    name = gettext_lazy("Added final punctuation")

    @staticmethod
    def get_related_checks():
        return list(TERMINAL_CHECKS)

    @staticmethod
    def _failing(source: str, target: str, unit: Unit) -> frozenset[str]:
        return frozenset(
            check.check_id
            for check in TERMINAL_CHECKS
            if not check.should_skip(unit) and check.check_single(source, target, unit)
        )

    def fix_single_target(
        self, target: str, source: str, unit: Unit
    ) -> tuple[str, bool]:
        if not target or target[-1] not in TERMINAL_MARKS:
            return target, False
        if len(target) > 1 and target[-2] == target[-1]:
            # Dropping one dot of an unfinished ellipsis, or one of a doubled
            # mark, would only make it odder.
            return target, False
        stripped = TRAILING_SPACING.sub("", target[:-1])
        if not stripped:
            # The mark is the whole translation; blanking it is not a repair.
            return target, False
        source_body = TRAILING_QUOTES.sub("", source)
        before = self._failing(source_body, target, unit)
        if not before:
            return target, False
        if not self._failing(source_body, stripped, unit) < before:
            return target, False
        return stripped, True
```

Дописать импорт в тестовый файл (`test_autofixes.py:10-14`) уже есть
`AddFrenchPunctuationSpacing` и `RemoveAddedFinalStop` — добавлять нечего.

### Step 4: Убедиться, что тесты проходят

Run: `./rundev.sh test weblate_customization/tests/test_autofixes.py`
Expected: PASS, все тесты класса (7 старых + 13 новых) и новый класс порядка.

### Step 5: Проверить, что тест ловит регресс

Временно вернуть в `fix_single_target` строку
`if not target.endswith(".") or target.endswith(".."): return target, False`
первой, прогнать тесты, убедиться, что новые падают, вернуть код обратно.
Ревью прошлых задач показало: тест, написанный после фикса, может
проходить и против бага.

### Step 6: Commit

```bash
git add weblate_customization/src/weblate_customization/autofixes.py weblate_customization/tests/test_autofixes.py
git commit -m "feat(autofixes): remove added terminal punctuation, not only the full stop"
```

---

## Задача 3. `apply_autofixes()` и отпечаток реестра (D10, D13, D15)

Команде нужны стабильные идентификаторы сработавших фиксов (`fix_target`
возвращает локализованные `name`) и упорядоченный отпечаток активного
реестра.

**Files:**

- Modify: `weblate/trans/autofixes/__init__.py:38-48`
- Test: `weblate/trans/tests/test_autofix.py`

### Step 1: Тесты

Дописать в `weblate/trans/tests/test_autofix.py`:

```python
class AutofixHelpersTest(SimpleTestCase):
    def test_apply_autofixes_matches_fix_target(self) -> None:
        unit = make_unit(source="Foo...")
        self.assertEqual(
            apply_autofixes(["Bar..."], unit)[0], fix_target(["Bar..."], unit)[0]
        )

    def test_apply_autofixes_reports_identifiers(self) -> None:
        unit = make_unit(source="Foo…")
        self.assertEqual(apply_autofixes(["Bar..."], unit)[1], ["ellipsis"])

    def test_empty_target_is_left_alone(self) -> None:
        unit = make_unit(source="Foo")
        self.assertEqual(apply_autofixes([], unit), ([], []))

    def test_fingerprint_is_ordered(self) -> None:
        self.assertEqual(autofix_fingerprint(), tuple(AUTOFIXES.keys()))
```

Импорты в шапке файла:
`from weblate.trans.autofixes import AUTOFIXES, apply_autofixes, autofix_fingerprint, fix_target`.
Идентификатор `ellipsis` взять фактический — он равен
`ReplaceTrailingDotsWithEllipsis.fix_id`; если не совпал, подставить
значение из `AUTOFIXES.keys()`.

### Step 2: Прогнать, убедиться в падении

Run: `./rundev.sh test weblate/trans/tests/test_autofix.py -k AutofixHelpers`
Expected: FAIL — `ImportError: cannot import name 'apply_autofixes'`.

### Step 3: Реализация

Заменить `weblate/trans/autofixes/__init__.py:38-48`:

```python
def _run_autofixes(target: list[str], unit: Unit) -> tuple[list[str], list[AutoFix]]:
    """Apply each autofix in order, collecting the ones that changed the target."""
    applied: list[AutoFix] = []
    for fix in AUTOFIXES.values():
        target, fixed = fix.fix_target(target, unit)
        if fixed:
            applied.append(fix)
    return target, applied


def fix_target(target: list[str], unit: Unit) -> tuple[list[str], list[StrOrPromise]]:
    """Apply each autofix to the target translation."""
    if target == []:
        return target, []
    target, applied = _run_autofixes(target, unit)
    return target, [fix.name for fix in applied]


def apply_autofixes(target: list[str], unit: Unit) -> tuple[list[str], list[str]]:
    """Apply each autofix, reporting stable identifiers instead of labels."""
    if target == []:
        return target, []
    target, applied = _run_autofixes(target, unit)
    return target, [fix.get_identifier() for fix in applied]


def autofix_fingerprint() -> tuple[str, ...]:
    """Return the ordered identifiers of the active autofixes."""
    return tuple(AUTOFIXES.keys())
```

Добавить в `TYPE_CHECKING`-блок: `from django_stubs_ext import StrOrPromise`.

### Step 4: Прогнать

Run: `./rundev.sh test weblate/trans/tests/test_autofix.py`
Expected: PASS, включая существующие тесты автофиксов.

### Step 5: Commit

```bash
git add weblate/trans/autofixes/__init__.py weblate/trans/tests/test_autofix.py
git commit -m "feat(autofixes): expose stable identifiers and an ordered registry fingerprint"
```

---

## Задача 4. Команда: селекция, исключения, dry-run (D5, D6, D8, D9, D10, D13)

**Files:**

- Create: `weblate/trans/management/commands/reapply_autofixes.py`
- Test: `weblate/trans/tests/test_commands.py`

### Step 1: Тесты

Дописать в `weblate/trans/tests/test_commands.py`:

```python
class ReapplyAutofixesCommandTest(ComponentTestCase, WeblateComponentCommandMixin):
    """Selection, validation and --all come from WeblateComponentCommand."""

    command_name = "reapply_autofixes"
    expected_string = "Active autofixes:"


class ReapplyAutofixesTest(ViewTestCase):
    SOURCE = "Thank you for using Weblate."

    def setUp(self) -> None:
        super().setUp()
        self.unit = self.get_unit(self.SOURCE)
        Unit.objects.filter(pk=self.unit.pk).update(
            target="Merci\u202f!", state=STATE_TRANSLATED
        )

    def run_command(self, *args: str) -> str:
        output = StringIO()
        call_command("reapply_autofixes", "test/test", *args, stdout=output)
        return output.getvalue()

    def test_dry_run_reports_without_writing(self) -> None:
        changes = Change.objects.count()
        pending = PendingUnitChange.objects.count()
        result = self.run_command()
        self.assertIn("1 unit to change", result)
        self.assertIn("removed-final-stop", result)
        self.assertIn("Merci", result)
        self.assertIn("--apply", result)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci\u202f!")
        self.assertEqual(Change.objects.count(), changes)
        self.assertEqual(PendingUnitChange.objects.count(), pending)

    def test_apply_repairs_and_keeps_state(self) -> None:
        self.run_command("--apply")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci")
        self.assertEqual(self.unit.state, STATE_TRANSLATED)
        self.assertTrue(
            self.unit.change_set.filter(action=ActionEvents.AUTO).exists()
        )

    def test_clean_unit_is_never_written(self) -> None:
        other = self.get_unit("Hello, world!\n")
        Unit.objects.filter(pk=other.pk).update(
            target="Ahoj svete!\n", automatically_translated=False
        )
        before = other.change_set.count()
        self.run_command("--apply")
        other.refresh_from_db()
        self.assertEqual(other.target, "Ahoj svete!\n")
        self.assertFalse(other.automatically_translated)
        self.assertEqual(other.change_set.count(), before)

    def test_second_apply_changes_nothing(self) -> None:
        self.run_command("--apply")
        changes = Change.objects.count()
        result = self.run_command("--apply")
        self.assertIn("0 units to change", result)
        self.assertEqual(Change.objects.count(), changes)

    def test_readonly_unit_is_skipped(self) -> None:
        Unit.objects.filter(pk=self.unit.pk).update(state=STATE_READONLY)
        self.run_command("--apply")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci\u202f!")

    def test_glossary_component_is_skipped(self) -> None:
        self.component.create_glossary()
        glossary = Component.objects.get(project=self.project, is_glossary=True)
        result = self.run_command_for(glossary)
        self.assertIn("skipped (glossary)", result)

    def run_command_for(self, component: Component) -> str:
        output = StringIO()
        call_command(
            "reapply_autofixes",
            "/".join(component.get_url_path()),
            stdout=output,
        )
        return output.getvalue()

    def test_diff_examples_are_capped(self) -> None:
        translation = self.get_translation()
        for index, unit in enumerate(translation.unit_set.all()):
            Unit.objects.filter(pk=unit.pk).update(target=f"Merci {index}\u202f!")
        result = self.run_command()
        self.assertIn("more", result)
        self.assertLessEqual(
            len([line for line in result.splitlines() if " -> " in line]), 5
        )

    @override_settings(AUTOFIX_LIST=["weblate.trans.autofixes.chars.RemoveZeroSpace"])
    def test_missing_required_autofix_fails_closed(self) -> None:
        with self.assertRaises(CommandError):
            self.run_command()
```

Дополнить импорты файла:

```python
from weblate.trans.actions import ActionEvents
from weblate.trans.models import Change, Component, Translation, Unit
from weblate.trans.models.pending import PendingUnitChange
from weblate.utils.state import STATE_READONLY, STATE_TRANSLATED
```

Проверить фактическую строку источника `"Thank you for using Weblate."`
в тестовом PO; если её нет, взять любую строку без `!` на конце из
`self.get_translation().unit_set.all()` и поправить константу.

### Step 2: Прогнать, убедиться в падении

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k Reapply`
Expected: FAIL — `Unknown command: 'reapply_autofixes'`.

### Step 3: Реализация (dry-run часть)

Создать `weblate/trans/management/commands/reapply_autofixes.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Re-apply the active autofixes to translations that were stored earlier.

Autofixes only run on write (``Unit.translate`` -> ``fix_target``), so a unit
translated before a fix existed keeps its defect forever. This command replays
the whole active ``AUTOFIX_LIST`` over stored units, reports what would change,
and writes only with an explicit ``--apply``.

    scan (read-only)            apply (per repository root)
    ---------------------       ------------------------------------------
    for each component          take the repository lock
      skip glossary               refuse if foreign pending changes exist
      skip template               for each candidate:
      skip read-only                 lock the row, recompute, translate()
      apply_autofixes()           refuse to commit if foreign work appeared
      collect + count             commit once, without pushing
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.trans.autofixes import apply_autofixes, autofix_fingerprint
from weblate.trans.models import Unit
from weblate.utils.management.base import WeblateComponentCommand
from weblate.utils.state import STATE_READONLY

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

    from weblate.trans.models import Component

# Without it the run is a false green: it would report zero changes because the
# fix is not registered, not because the corpus is clean.
REQUIRED_FIX_IDS = frozenset({"removed-final-stop"})
DIFF_EXAMPLES = 5
DIFF_WIDTH = 60


def render(text: str) -> str:
    """Render a target for the terminal: escaped, on one line, truncated."""
    shown = text if len(text) <= DIFF_WIDTH else f"{text[:DIFF_WIDTH]}…"
    # isprintable() is False for newlines, control characters and every
    # non-ASCII space, so NBSP and NNBSP stay visible in the diff.
    return "".join(
        char if char.isprintable() else f"\\u{ord(char):04x}" for char in shown
    )


class Command(WeblateComponentCommand):
    help = "re-applies the active autofixes to stored translations"

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="write the repairs and commit them; without it nothing is written",
        )

    def check_registry(self) -> None:
        fingerprint = autofix_fingerprint()
        self.stdout.write(f"Active autofixes: {', '.join(fingerprint)}")
        missing = REQUIRED_FIX_IDS.difference(fingerprint)
        if missing:
            msg = (
                f"Required autofixes are not active: {', '.join(sorted(missing))}. "
                "Check WEBLATE_ADD_AUTOFIX in the running environment "
                "(printenv inside the container, not docker-compose.yml)."
            )
            raise CommandError(msg)

    @staticmethod
    def candidate_units(component: Component):
        """Stored units the autofixes are allowed to touch."""
        translations = [
            translation
            for translation in component.translation_set.all()
            # translate() skips autofixes for templates, so a template unit
            # would be marked as automatically translated without a repair.
            if not translation.is_template
        ]
        return (
            Unit.objects.filter(translation__in=translations)
            .exclude(state=STATE_READONLY)
            .order_by("pk")
        )

    def scan(self, component: Component) -> tuple[list[int], Counter[str], list[str]]:
        """Report the units the autofixes would change. Writes nothing."""
        changed: list[int] = []
        counters: Counter[str] = Counter()
        examples: list[str] = []
        units = self.candidate_units(component).prefetch()
        for unit in units.iterator(chunk_size=1000):
            original = unit.get_target_plurals()
            candidate, applied = apply_autofixes(list(original), unit)
            if candidate == original:
                continue
            changed.append(unit.pk)
            counters.update(applied)
            if len(examples) < DIFF_EXAMPLES:
                examples.append(
                    f"  {render(' | '.join(original))} -> {render(' | '.join(candidate))}"
                )
        return changed, counters, examples

    def report(
        self,
        component: Component,
        changed: list[int],
        counters: Counter[str],
        examples: list[str],
    ) -> None:
        detail = ", ".join(f"{key} {value}" for key, value in sorted(counters.items()))
        plural = "unit" if len(changed) == 1 else "units"
        self.stdout.write(
            f"{component}: {len(changed)} {plural} to change"
            + (f" ({detail})" if detail else "")
        )
        for example in examples:
            self.stdout.write(example)
        if len(changed) > len(examples):
            self.stdout.write(f"  … +{len(changed) - len(examples)} more")

    def handle(self, *args, **options) -> None:
        self.check_registry()
        components = self.get_components(**options).order_by("pk")
        groups: dict[int, list[Component]] = defaultdict(list)
        for component in components:
            if component.is_glossary:
                self.stdout.write(f"{component}: skipped (glossary)")
                continue
            groups[component.linked_component_id or component.pk].append(component)

        for group in groups.values():
            for component in group:
                changed, counters, examples = self.scan(component)
                self.report(component, changed, counters, examples)

        if not options["apply"]:
            self.stdout.write("Dry run: nothing written. Use --apply to write.")
```

### Step 4: Прогнать

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k Reapply`
Expected: тесты dry-run, селекции, glossary, readonly, cap и fail-closed
проходят; `test_apply_*` и `test_second_apply_*` падают (запись ещё не
реализована).

### Step 5: Commit

```bash
git add weblate/trans/management/commands/reapply_autofixes.py weblate/trans/tests/test_commands.py
git commit -m "feat(trans): add reapply_autofixes dry-run reporting"
```

---

## Задача 5. `--apply`: запись под блокировкой (D2, D3, D12, D14)

**Files:**

- Modify: `weblate/trans/management/commands/reapply_autofixes.py`
- Test: `weblate/trans/tests/test_commands.py`

### Step 1: Тест на гонку

Дописать в `ReapplyAutofixesTest`:

```python
    def test_concurrent_edit_is_not_overwritten(self) -> None:
        # The edit has to land between the scan and the row lock. Hooking
        # Unit.translate would be too late: by then repair() has already read
        # the row under select_for_update and recomputed, so even a correct
        # implementation writes the scanned value and the test fails on good
        # code. Command.get_user() runs once, after the scan and before
        # apply_group takes any lock, which is exactly the window.
        from weblate.trans.management.commands.reapply_autofixes import Command

        original_get_user = Command.get_user

        def edit_first(command):
            Unit.objects.filter(pk=self.unit.pk).update(target="Merci beaucoup")
            return original_get_user(command)

        with patch.object(Command, "get_user", autospec=True, side_effect=edit_first):
            self.run_command("--apply")
        self.unit.refresh_from_db()
        # Not merely "not Merci": the translator's text must survive intact.
        self.assertEqual(self.unit.target, "Merci beaucoup")

    def test_translate_receives_the_unfixed_target_and_fixes_it(self) -> None:
        # repair() hands translate() the UNFIXED target on purpose: translate()
        # runs fix_target internally (unit.py:2406) and that call is what
        # records unit.fixups. Both halves are pinned here because each can
        # break alone: pre-fixing in the command would lose the fixups, and a
        # translate() that stopped applying autofixes would turn every repair
        # into a no-op write with a Change attached.
        captured: list[list[str]] = []
        original_translate = Unit.translate

        def spy(unit, user, new_target, *args, **kwargs):
            captured.append(list(new_target))
            return original_translate(unit, user, new_target, *args, **kwargs)

        with patch.object(Unit, "translate", autospec=True, side_effect=spy):
            self.run_command("--apply")
        self.assertEqual(captured, [["Merci\u202f!"]])
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Merci")

    def test_apply_does_not_propagate_to_another_component(self) -> None:
        second = Component.objects.create(
            name="Test 2",
            slug="test-2",
            project=self.project,
            repo=self.git_repo_path,
            vcs="git",
            filemask="po/*.po",
            template="",
            file_format="po",
            new_base="",
            allow_translation_propagation=True,
        )
        other = second.translation_set.get(language_code="cs").unit_set.get(
            source=self.SOURCE
        )
        Unit.objects.filter(pk=other.pk).update(target="Merci\u202f!")
        self.run_command("--apply")
        other.refresh_from_db()
        self.assertEqual(other.target, "Merci\u202f!")
```

Что тест различает: корректная реализация под локом перечитывает уже
исправленный переводчиком target, автофиксам снимать нечего, `repair()`
возвращает `None`, запись не выполняется. Наивная реализация, пишущая
посчитанный на скане `candidate`, затирает правку на `Merci` и тест падает.

Инвариант 6 (свежесть) — единственный, который защищает чужую работу от
перезаписи, поэтому его мутационная проверка (Step 5) выполняется первой.

### Step 2: Прогнать

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k Reapply`
Expected: FAIL на apply-тестах.

### Step 3: Реализация

Добавить импорты:

```python
from django.db import transaction

from weblate.auth.models import User
from weblate.trans.actions import ActionEvents
```

Добавить методы и подключить их в `handle`:

```python
    def get_user(self) -> User:
        return User.objects.get_or_create_bot(
            scope="weblate", name="autofix", verbose="Autofix backfill"
        )

    def repair(self, unit_id: int, user: User) -> list[str] | None:
        """
        Repair one unit, or report that it no longer needs one.

        The whole decision is retaken inside the row lock: the scan ran
        without one, and a translator or an import may have changed the unit
        since. ``translate`` receives the freshly read target, applies the
        autofixes itself (unit.py:2404) and records the fixups.
        """
        with transaction.atomic():
            unit = Unit.objects.select_for_update().prefetch().get(pk=unit_id)
            if unit.state == STATE_READONLY or unit.translation.is_template:
                return None
            original = unit.get_target_plurals()
            candidate, applied = apply_autofixes(list(original), unit)
            if candidate == original:
                return None
            unit.translate(
                user,
                original,
                unit.state,
                change_action=ActionEvents.AUTO,
                propagate=False,
                select_for_update=False,
            )
            return applied
```

В `handle` после отчёта, когда `options["apply"]`:

```python
            written: list[int] = []
            stale = 0
            for component in group:
                for unit_id in candidates[component.pk]:
                    if self.repair(unit_id, user) is None:
                        stale += 1
                        continue
                    written.append(unit_id)
            self.stdout.write(f"{root}: {len(written)} written, {stale} stale")
```

`candidates` — словарь `component.pk -> changed`, наполняемый на этапе
scan; scan и apply обязаны использовать один и тот же список, иначе
счётчики dry-run и записи разойдутся.

### Step 4: Прогнать

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k Reapply`
Expected: PASS для всех тестов, кроме коммит-теста задачи 6.

### Step 5: Мутационная проверка

Порядок по риску: инвариант 6 первым, он единственный защищает чужую
работу. Мутации применять **по одной**, оригинальный текст держать в буфере
редактора, а не в `.bak`-файле: второй `cp file file.bak` затирает
нетронутую копию уже мутированной и восстановление оставляет мутацию в
дереве.

| # | Мутация | Обязан упасть |
|---|---|---|
| 1 | в `repair()` использовать `candidate`, посчитанный на скане, вместо повторного расчёта под локом | `test_concurrent_edit_is_not_overwritten` |
| 2 | `if candidate == original: return None` → `pass` | `test_clean_unit_is_never_written` |
| 3 | убрать `propagate=False` | `test_apply_does_not_propagate_to_another_component` |
| 4 | передать в `translate()` `candidate` вместо `original` | `test_translate_receives_the_unfixed_target_and_fixes_it` |

Мутация 1 — единственная, которая имитирует правдоподобную ошибку
реализации («зачем считать дважды»), поэтому если она не роняет тест,
тест бесполезен независимо от того, что он проверяет.

### Step 6: Commit

```bash
git add weblate/trans/management/commands/reapply_autofixes.py weblate/trans/tests/test_commands.py
git commit -m "feat(trans): write autofix repairs under a row lock"
```

---

## Задача 6. Коммит по корню репозитория (D4, D17)

`Component.commit_pending()` собирает pending-изменения компонента **и
связанных с ним** (`component.py:3734`), а `perform_on_link` уводит
вызов на корень репозитория (`component.py:255`). Поэтому «покомпонентно»
не даёт контролируемого коммита: в него может попасть чужая работа.

Практическая гарантия — не глобальная пауза, а инвариант: чужие pending
до записи и после записи. Если чужая правка появилась в окне — не
коммитим и возвращаем ненулевой код; наши изменения остаются в pending и
уедут штатным планировщиком.

**Инвариант верный, но сам по себе делает команду неприменимой.**
`DEFAULT_COMMIT_PENDING_HOURS = 24` (`weblate/trans/defaults.py:17`):
на живом инстансе pending-изменения лежат до суток, поэтому «чужие pending
есть» — норма, а не исключение, и первый прод-запуск напечатает `refusing`.
Поэтому к инварианту прилагается runbook подготовки (задача 8), а не
смягчение проверки.

Отдельно: dev-стенд этот класс отказа **не воспроизводит** — на нём сейчас
`PendingUnitChange` ноль и ни один компонент не заблокирован. Зелёный смоук
на dev ничего не говорит о поведении на проде.

**Files:**

- Modify: `weblate/trans/management/commands/reapply_autofixes.py`
- Test: `weblate/trans/tests/test_commands.py`

### Step 1: Тесты

```python
    def test_apply_commits_the_repository_once(self) -> None:
        with patch.object(Component, "commit_pending", return_value=True) as commit:
            self.run_command("--apply")
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(commit.call_args.kwargs["skip_push"], True)

    def test_foreign_pending_changes_block_the_commit(self) -> None:
        self.edit_unit("Hello, world!\n", "Ahoj svete!\n")
        with patch.object(Component, "commit_pending") as commit:
            with self.assertRaises(CommandError):
                self.run_command("--apply")
        commit.assert_not_called()
```

### Step 2: Прогнать

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k "commits_the_repository or foreign_pending"`
Expected: FAIL.

### Step 3: Реализация

```python
from weblate.trans.models.pending import PendingUnitChange


    @staticmethod
    def foreign_pending(root: Component, written: list[int]) -> bool:
        """Any pending change in this repository family that is not ours."""
        return (
            PendingUnitChange.objects.for_component(
                root, apply_filters=False, include_linked=True
            )
            .exclude(unit_id__in=written)
            .exists()
        )

    def apply_group(self, root: Component, group: list[Component], candidates) -> bool:
        user = self.get_user()
        with root.repository.lock:
            if self.foreign_pending(root, []):
                self.stderr.write(
                    f"{root}: foreign pending changes, refusing to touch this repository"
                )
                return False
            written: list[int] = []
            stale = 0
            for component in group:
                for unit_id in candidates[component.pk]:
                    if self.repair(unit_id, user) is None:
                        stale += 1
                        continue
                    written.append(unit_id)
            self.stdout.write(f"{root}: {len(written)} written, {stale} stale")
            if not written:
                return True
            if self.foreign_pending(root, written):
                self.stderr.write(
                    f"{root}: foreign pending changes appeared during the run; "
                    "repairs stay pending and are not committed"
                )
                return False
            root.commit_pending("autofix backfill", user, skip_push=True)
        return True
```

В `handle` собрать результат и завершиться ошибкой при неудаче:

```python
        failures = [root for root, ok in results if not ok]
        if failures:
            msg = "Some repositories were not committed: " + ", ".join(
                str(root) for root in failures
            )
            raise CommandError(msg)
```

### Step 4: Прогнать

Run: `./rundev.sh test weblate/trans/tests/test_commands.py -k Reapply`
Expected: PASS целиком.

### Step 5: Commit

```bash
git add weblate/trans/management/commands/reapply_autofixes.py weblate/trans/tests/test_commands.py
git commit -m "feat(trans): commit autofix backfill per repository root only"
```

---

## Задача 7. Parity нормализации с судьёй (D15)

Golden set собран прототипом (`fixups: ["terminal-extension-!?:"]`,
`normalization: fix_target+RemoveAddedFinalStop+terminal-!?:+AddFrenchPunctuationSpacing`),
которого в коде нет. Пока отпечаток не зафиксирован в обоих контурах,
калибровка судьи может оценивать не ту популяцию, что прод.

**Files:**

- Modify: `analysis/probes/col4-judge-goldenset-build.py`
- Modify: `docs/llm-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md`

**Step 1:** прочитать сборщик и найти, где формируется литерал
`normalization`.

**Step 2:** добавить обязательный аргумент `--autofix-fingerprint`
(строка вида `line-separator-spacing,removed-final-stop,…`), записывать
его в метаданные набора вместо литерала, значение брать из строки
`Active autofixes:` вывода `reapply_autofixes`.

**Step 3:** пересобрать набор и убедиться в байтовой стабильности:

```bash
uv run python analysis/probes/col4-judge-goldenset-build.py --autofix-fingerprint "<из команды>"
git diff --stat analysis/data/col4-judge-golden.json
```

Ожидание: меняются только метаданные. Если поменялись сами строки —
прототип и отгруженный автофикс расходятся: зафиксировать расхождение в
`docs/llm-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md`
и пересчитать затронутые страты, а не подгонять код под набор.

**Step 4:** в задаче B1 фазы 0 записать правило: первый пишущий прогон
судьи допускается только при совпадении отпечатка набора и отпечатка
прод-инстанса, иначе — явный rebaseline.

**Step 5: Commit**

```bash
git add analysis/probes/col4-judge-goldenset-build.py analysis/data/col4-judge-golden.json docs/llm-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md
git commit -m "chore(llm-first): pin the autofix fingerprint into the judge golden set"
```

---

## Задача 8. Доставка в dev и smoke (D10)

Копирования пакета недостаточно: `AUTOFIX_LIST` читается из
`WEBLATE_ADD_AUTOFIX` при старте процесса
(`weblate/settings_docker.py:1301`, `weblate/utils/environment.py:182`).
`dev-docker/docker-compose.yml:62` перечисляет три кастомных автофикса, а
запущенный контейнер отдаёт только `LineSeparatorSpacing` — он создан до
правки compose.

**Step 1: Скопировать пакет**

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
```

**Step 2: Проверить фактическое окружение**

```bash
docker compose -f dev-docker/docker-compose.yml exec -T weblate printenv WEBLATE_ADD_AUTOFIX
```

Ожидание:
`weblate_customization.autofixes.LineSeparatorSpacing,weblate_customization.autofixes.RemoveAddedFinalStop,weblate_customization.autofixes.AddFrenchPunctuationSpacing`.

**Step 3: Пересоздать стенд — только после отдельного согласования**

`./rundev.sh` перезапускает общий стенд (AGENTS.md: пересборка
dev-docker — деплой). Запросить подтверждение, затем
`WEBLATE_PORT=3001 ./rundev.sh` и повторить шаг 2.

**Step 4: Smoke dry-run**

```bash
./rundev.sh exec weblate weblate reapply_autofixes col4/data
```

Ожидание: строка `Active autofixes:` содержит `removed-final-stop`;
далее счётчики и до пяти diff-примеров с видимыми `\u202f` / `\u00a0`;
последняя строка — `Dry run: nothing written.` Проверить, что новых
`Change` не появилось.

**Step 5: Записать результат** в `docs/llm-first/measurements/2026-08-12-autofix-terminal-punctuation.md`
(отпечаток реестра, счётчики, дата).

### Runbook прод-прогона

Прод-прогон — деплой: выполняется только после отдельного явного
согласования, по одному компоненту. Замер показал, что цель фактически
одна: `CoL4/data` — 1384 юнита backfill и 65 из 70 терминальных.

Без подготовки первый же запуск упрётся в инвариант «чужое не коммитим»:
переводчики сохраняют непрерывно, а коммит идёт раз в сутки. Порядок
снимает окно, а не проверку.

```bash
P=CoL4/data
weblate lock_translation "$P"          # переводчики больше не создают pending
weblate commit_pending --age 0 "$P"    # чужая работа уезжает своим коммитом
weblate reapply_autofixes "$P"         # dry-run: счётчики и 0 foreign pending
weblate reapply_autofixes "$P" --apply # запись + один коммит по корню
weblate unlock_translation "$P"
```

`lock_translation` и `unlock_translation` уже существуют
(`weblate/trans/management/commands/lock_translation.py`) и наследуют тот
же `WeblateComponentCommand`, что и новая команда, поэтому селектор
компонента везде одинаковый. Нового кода runbook не требует.

Проверки между шагами:

| После шага | Что должно быть верно |
|---|---|
| `lock_translation` | компонент заблокирован в UI, новых `PendingUnitChange` не появляется |
| `commit_pending` | `PendingUnitChange` по корню репозитория — ноль |
| dry-run | `Active autofixes:` содержит `removed-final-stop`; счётчики близки к 1384 / 65 |
| `--apply` | `written` совпадает со счётчиком dry-run, `stale` мал, ровно один коммит |
| повторный dry-run | `0 units to change` |

Если между `commit_pending` и `--apply` кто-то успел сохранить правку,
команда откажется коммитить и вернёт ненулевой код — это штатный исход,
а не сбой: снять блокировку, повторить цикл.

---

## Задача 9. Документация и финальные проверки

**Step 1: Changelog** — в верхнюю (неизданную) секцию `docs/changes.rst`:
новая команда `reapply_autofixes` и расширение автофикса терминала.

**Step 2: Линт и типы**

```bash
uv run prek run --files weblate/trans/management/commands/reapply_autofixes.py weblate/trans/autofixes/__init__.py weblate_customization/src/weblate_customization/autofixes.py weblate/trans/tests/test_commands.py weblate_customization/tests/test_autofixes.py weblate/trans/tests/test_autofix.py
uv run pylint weblate/trans/management/commands/reapply_autofixes.py weblate/trans/autofixes/__init__.py
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

`prek` запускать точечно по файлам: полный прогон переформатирует чужие
файлы.

**Step 3: Тесты затронутых модулей**

```bash
./rundev.sh test weblate_customization/tests/test_autofixes.py
./rundev.sh test weblate/trans/tests/test_autofix.py
./rundev.sh test weblate/trans/tests/test_commands.py -k Reapply
```

**Step 4: Базовая линия для флейков** — `weblate/trans/tests/test_autotranslate.py`
и git-бэкенд флейкуют независимо от изменений
(`RepositoryCommandError: Invalid revision range`). Перед тем как счесть
падение регрессом, прогнать тот же тест на неизменённом коммите.

**Step 5: Commit и push**

```bash
git add docs/changes.rst
git commit -m "docs(changes): note the terminal autofix and reapply_autofixes"
/usr/bin/git push origin main
```

`/usr/bin/git` — обход зависания brew-git на osxkeychain.

---

## Вне объёма

- Восстановление потерянной пунктуации: дописать знак детерминированно
  нельзя, остаётся чекам и судье.
- CJK и языкоспецифичные терминалы (`hy`, `hi/bn/or`, `sat`, `my`): в
  замере не встречались.
- Разбор HTML, скобок и плейсхолдеров как прозрачных обёрток; разворот
  кавычек в target — измерен и отклонён (портит `en_US`).
- Ключ `--fix` для выбора одного автофикса: команда сознательно
  переигрывает весь активный `AUTOFIX_LIST` и отчитывается по каждому.
- Ключ `--push`: коммит всегда локальный (`skip_push=True`).
- Глобальная пауза редактирования всего инстанса: окно снимается
  покомпонентным `lock_translation` в runbook задачи 8.
- Изменение самих чеков.
- Прод-прогон `--apply`: деплой, отдельное согласование.

## Секвенирование с судьёй

- Трек B фазы 0 команду не ждёт: golden set нормализуется автофиксами при
  сборке. Но задача 7 обязана быть выполнена **до** первого пишущего
  прогона судьи.
- Прод-прогон `--apply` — предусловие первого пишущего прогона судьи
  (фаза 2): иначе судья тратит бюджет и очередь flag на юнитах, которые
  код чистит бесплатно. Порядок — runbook в задаче 8: согласование,
  `lock_translation`, `commit_pending --age 0`, dry-run, `--apply`,
  `unlock_translation`.

## Приёмка плана целиком

1. `weblate_customization/tests/test_autofixes.py` — зелёный, включая
   семь исходных тестов точки.
2. `weblate/trans/tests/test_commands.py -k Reapply` — зелёный.
3. Каждый новый тест проверен мутацией: с откаченным фиксом он падает.
4. Dry-run на dev печатает отпечаток реестра с `removed-final-stop`,
   ожидаемые счётчики и ноль записей.
5. `--apply` на одном dev-компоненте: цели починены, state сохранён,
   юниты без дефекта не тронуты, ровно один коммит на корень репозитория.
6. Повторный `--apply`: `0 units to change`, ноль новых `Change`.
7. `docs/llm-first/measurements/2026-08-12-autofix-terminal-punctuation.md` содержит вердикт по `:`.
8. Отпечаток автофиксов совпадает у golden set и прод-инстанса.
9. Прод-runbook пройден на `CoL4/data` целиком, включая повторный dry-run
   с `0 units to change`.
