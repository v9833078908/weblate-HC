# План: чек дрейфа повторов (`repeat-drift`) и аудит глоссария

> **For Claude:** REQUIRED SUB-SKILL: выполняй этот план задача за задачей через
> `superpowers:executing-plans`. Каждая задача — TDD: падающий тест, прогон,
> минимальная реализация, прогон, коммит.

**Goal:** детерминированно находить строки, у которых один и тот же текст
источника переведён по-разному под другим ключом, и отдельно — глоссарные
термины, которые противоречат друг другу.

**Architecture:** `RepeatDriftCheck` — батч-`TargetCheck` рядом со стоковыми
`ConsistencyCheck`/`ReusedCheck` в `weblate/checks/consistency.py`. Группа
задаётся новым свойством `Unit.repeat_units`: те же текст источника и язык в
пределах проекта, независимо от ключа. Батч-проход строит группы одним
агрегирующим запросом на языковую группу и решает расхождение в питоне;
`BatchCheckMixin` сверяет строки `Check`. Новый режим распространения
`propagates = "repeat"` снимает чек с соседей группы сразу после правки, а не
до следующего батч-прохода. `audit_glossary` — read-only management-команда
рядом с `glossary_coverage`, сообщающая о дублях и схлопываниях терминов.

**Tech Stack:** Django ORM (PostgreSQL, выражённый индекс
`trans_unit_source_md5`), `weblate.checks.base.BatchCheckMixin`, Celery-задача
`weblate.checks.tasks.finalize_component_checks`, pytest + `ComponentTestCase`
/ `GlossaryTest`.

---

Дата: 2026-08-14. **Редакция 2026-09-03** — переработан по прод-замеру и двум
независимым ревью (архитектурное + сверка кода). Предыдущая редакция
2026-08-20 удалила задачу включения `check_glossary`.

Статус: **не начат.** Не задеплоен нигде. Включение на проде требует
отдельного явного одобрения владельца.

Имя файла осталось `intra-component-consistency-check.md`, хотя чек стал
проектным: на этот путь ссылаются
`docs/llm-first/plans/2026-08-10-git-localization-quality-gate.md:603`,
`docs/llm-first/plans/2026-08-14-judge-severity-recalibration.md:191`,
`docs/llm-first/plans/2026-08-17-session-canon.md:15`,
`docs/llm-first/measurements/2026-08-13-phase0-measurements.md:616` и
`docs/llm-first/vision/llm-first-product-architecture.md:665-671`.
Переименование порвало бы пять ссылок ради косметики.

Место в дорожной карте: `### Фаза 4 — масштабирование, по данным`
(`docs/llm-first/vision/llm-first-product-architecture.md:644`, добавление от
2026-08-22, строки 663-671). Текст карты всё ещё говорит «внутри одного
компонента» и «дрожит на 16%» — обновляется задачей 12.

## Что изменено в редакции 2026-09-03

| # | Было | Стало | Почему |
|---|---|---|---|
| 1 | скоуп — компонент (R1), межкомпонентный дрейф вне объёма | скоуп — проект, как у стоковых батч-чеков | на проде 4 из 122 групп межкомпонентные, а `batch_project_wide=True` и проектный радиус удаления лишних строк уже реализованы в стоке (`weblate/checks/base.py:329-333,356-368`) |
| 2 | глоссарий вне объёма без цифры | глоссарий **исключён явно** и покрыт задачами 9-11 | 201 из 290 расхождений на проде — пара «глоссарный термин ↔ обычная строка», то есть лемма против контекстной формы; к тому же на глоссарных компонентах `run_checks` вообще запускает только `CHECKS.glossary` (`weblate/trans/models/unit.py:2179`), так что чек там физически не сработал бы |
| 3 | флаги `ignore-case`/`ignore-punctuation`/`ignore-whitespace` (R3) | **удалены** | косметика объясняет 6 групп из 122 (5%); гасится штатным `ignore-repeat-drift` без кода |
| 4 | стемминг обязателен в аудите глоссария | **не входит в v1** | стем-дубли на проде: 5 групп, все 5 намеренные (`Артефакт`/`Артефакты`, `Яйцо`/`Яйца`, …) |
| 5 | класс аудита «target термина X = source термина Y» | заменён на «один перевод на несколько терминов» | исходный класс структурно невозможен при ru-источнике и латинских целях: замерено 0; новый даёт 24 ячейки с настоящими дефектами (`скупщик`/`торговец` → один перевод в bg/de/es) |
| 6 | «шумовой пол судьи 16%» | 22-31% | замер n=2 опровергнут замером n=5: `docs/llm-first/measurements/2026-08-18-severity-recalibration-partial.md:56-59`, финал `2026-08-19-severity-recalibration-final.md:72-90` |
| 7 | ссылка `misc/heart-abyss-hub-1-translation-qa.md` | `docs/operations/audits/2026-08-20-heart-abyss-hub-1-translation-qa.md` | файл переехал (`docs/product/plans/2026-08-24-docs-structure-convention.md:182`) |
| 8 | `get_related_checks` назван частью батч-механики | убрано | метод живёт в `weblate/checks/source.py:93-96` и относится к source-чекам |
| 9 | приёмка без числа | приёмка привязана к замеру: 122 группы / 259 юнитов на `need-for-greed` | иначе приёмка непроверяема |
| 10 | группировка по свёрнутому регистру | **точный текст источника**; `MD5(Lower(source))` остаётся только сужением под индекс | замер, из которого взято 122/259, группирует по точному тексту; свёртка регистра сделала бы приёмочное число невоспроизводимым и склеила бы законные пары вида `Атака`/`атака` |
| 11 | «`propagates` не задаётся, устаревшую строку снимет батч» | **новый режим `propagates = "repeat"`** | обычная правка в UI не открывает батч-окно, поэтому строка чека на соседе жила бы до следующей массовой операции; трасса: `weblate/trans/models/unit.py:2198-2295` |
| 12 | взаимодействие с промптом MT/судьи не рассмотрено | `repeat-drift` **не попадает** в `failing_checks` промпта | каждая непогашенная строка чека уходит в промпт как обязательная к исправлению (`weblate/machinery/llm.py:606-631`), а чек висит на всех членах группы, включая правильный: починка гонялась бы за соседом |
| 13 | тесты регистрации опирались на `choices` у `Check.name` | тест идёт через `run_checks` и реестр `CHECKS` | `bulk_create` не вызывает `full_clean`, поэтому `choices` ничего не валидируют (`weblate/checks/base.py:353`) |
| 14 | «мутация: не исключать исходный перевод» | тест строит проект со вторым исходным языком | при одинаковых плюралях исходный юнит и так отсекается фильтром `translation__plural_id`, то есть прежний тест не мог упасть |
| 15 | тесты чека наследовали `ConsistencyCheckTest` | общий хелпер вынесен в миксин | наследование прогоняло бы все тесты `inconsistent`/`reused` второй раз |
| 16 | команда аудита без запуска и без места для baseline | назван запуск и путь baseline | иначе находки некому смотреть |

## Основание

Три независимых замера, из них два — на проде.

| Замер | Среда | Результат |
|---|---|---|
| Трек C фазы 0, `col4/data/fr` | dev-зеркало (`docs/llm-first/measurements/2026-08-13-phase0-measurements.md:1-7`) | 146 групп с одинаковым нормализованным источником, 84 (57.5%) переведены по-разному; стоковый `inconsistent` не пометил **0 из 84**; групп, где хотя бы два юнита делят `context` — 0 (`:505-511`) |
| Аудит `heart-abyss/hub-1` | прод | 23 повторяющиеся строки дали 25 расхождений (FR 16, EN 9) (`docs/operations/audits/2026-08-20-heart-abyss-hub-1-translation-qa.md:147-149`); стоковый чек — 0 из 25 (`:251`); §15.3 ставит класс на второе место по влиянию после судьи |
| `need-for-greed`, 8 компонентов, 18 языков | прод, 2026-09-03 | 5 117 повторных групп, 290 расходящихся; после исключения глоссария — **122 группы, 259 юнитов**, из них 118 внутри компонента и 4 между; стоковый `inconsistent` — 0 из 122; размер группы максимум 6 юнитов (`docs/operations/measurements/2026-09-03-need-for-greed-repeat-drift.md`) |

Почему детерминированный чек, а не судья: повтор — сравнение строк, а не
понимание смысла. `==` решается точно, бесплатно и воспроизводимо, тогда как
два идентичных прогона судьи расходятся на 22-31% юнитов
(`docs/llm-first/measurements/2026-08-19-severity-recalibration-final.md:72-90`).
Судья к тому же видит каждый сегмент батча изолированно, и «два разных перевода
одного текста» в отрыве друг от друга выглядит как законная вариативность: на
hub-1 этот класс дали только ручной аудит и предложенный чек, 0 — судья и 0 —
стоковые чеки.

Почему не покрыто существующим:

| Механизм | Почему не закрывает | Где проверено |
|---|---|---|
| стоковый `inconsistent` | скоуп у него уже проектный (`batch_project_wide = True`, `component__project=component.project`), дыра не в радиусе, а в ключе: группирует по `id_hash = hash(source, context)`; `context` в монолингвальных форматах — ключ строки, различный у каждого юнита, поэтому одинаковый текст под разными ключами в одну группу не попадает | `weblate/checks/consistency.py:84,86,112-160`, `weblate/trans/models/unit.py:377-392` |
| распространение перевода | тот же фильтр `source` **и** `context` | `weblate/trans/models/unit.py:2577-2586` |
| `reused` | обратное направление: разные источники, один перевод | `weblate/checks/consistency.py:172-207` |
| сессионный канон | предотвращение внутри одного прогона автоперевода, не детекция накопленного корпуса; сам план это оговаривает | `docs/llm-first/plans/2026-08-17-session-canon.md:27-31,174-175` |
| гейт `corpus.inconsistent-translation` | **вторая реализация того же правила** по экспортированным файлам, а не потребитель строк чека | `docs/llm-first/plans/2026-08-10-git-localization-quality-gate.md:597-603` |
| `check_glossary` | остаётся `default_disabled` решением 2026-08-11; этим планом не пересматривается | `docs/llm-first/plans/2026-08-11-glossary-morphological-enforcement.md:8-19` |

Единственное определение правила (его обязан цитировать план гейта, а не
изобретать своё): **в пределах одного проекта и одного языка два юнита с
побайтово одинаковым текстом источника обязаны иметь побайтово одинаковый
перевод; юнит с флагом `ignore-repeat-drift` из сравнения исключён; глоссарные
компоненты и исходные переводы в сравнение не входят; сравниваются только
юниты в состоянии `translated`/`approved`.**

## Проверенные факты кода

| Факт | Где |
|---|---|
| `BatchCheckMixin._perform_batch` считает набор из `check_component` истиной: создаёт отсутствующие `Check`, удаляет лишние с тем же `name`, инвалидирует кэш | `weblate/checks/base.py:316-380` |
| `bulk_create` не вызывает `full_clean`, поэтому `choices` у `Check.name` ничего не валидируют | `weblate/checks/base.py:353`, `weblate/checks/models.py:82-97` |
| набор из `check_component` фильтруется ещё раз через `should_skip` | `weblate/checks/base.py:332` |
| `run_checks` берёт чеки из реестра: для глоссарного компонента — `CHECKS.glossary`, для исходного юнита — `CHECKS.source`, для остальных — `CHECKS.target`; незарегистрированный id не выполняется никогда | `weblate/trans/models/unit.py:2176-2196` |
| `ignore_state` пропускает только `state == 0` и read-only, поэтому «нужно править» (10) доходит до живого пути | `weblate/checks/base.py:110-113` |
| распространение поддерживает ровно два режима, третий даёт `ValueError`; точки правки — инициализация набора, ветка удаления и `querymap` | `weblate/trans/models/unit.py:2199`, `:2251-2267`, `:2279-2295` |
| каждая непогашенная строка чека уходит в промпт MT/починки с именем и `get_machine_description`; для глоссарного чека уже есть исключающая ветка | `weblate/machinery/llm.py:606-631` |
| батч-проход запускается задачей `finalize_component_checks` через `delay_on_commit` из `Component.run_batched_checks()` и требует `isinstance(check, BatchCheckMixin)` | `weblate/checks/tasks.py:17-77`, `weblate/trans/models/component.py:4879-4907` |
| `default_disabled=True` включается литеральным флагом `check_id.replace("_","-")`; гасится `ignore-<id>` | `weblate/checks/base.py:90-107` |
| флаги складываются в цепочку workspace → проект → категория → компонент → перевод → юнит, все звенья — `cached_property` | `weblate/trans/models/project.py:817-822`, `weblate/trans/models/component.py:6187-6198`, `weblate/trans/models/translation.py:404-406`, `weblate/trans/models/unit.py:2489-2511` |
| индекс `trans_unit_source_md5` на `(MD5(Lower(source)), translation)` обслуживает точечный поиск по источнику и `md5 IN (...)`; **`GROUP BY md5` он не обслуживает** — планировщик пойдёт по `translation_id` и хеш-агрегации, как и стоковый `inconsistent` | `weblate/trans/models/unit.py:717-726` |
| `TranslationQuerySet.exclude_source()` уже реализует `exclude(language=F("component__source_language"))` | `weblate/trans/models/translation.py:311-316` |
| `UnitQuerySet.prefetch()` и `.prefetch_bulk()` существуют, стоковые батч-чеки вызывают их в этом порядке | `weblate/trans/models/unit.py:183,362` |
| `F` **не импортирован** в `weblate/trans/models/unit.py` (есть `Count, ManyToManyField, Max, Q, Sum, Value`) | `weblate/trans/models/unit.py:17` |
| в `weblate/checks/consistency.py` уже импортированы `MD5`, `Lower`, `Value`, `F`, `Min`, `Max`, `defaultdict`, `format_html`, `format_html_join_comma`, `gettext`, `gettext_lazy`, `STATE_TRANSLATED`, `TargetCheck`, `BatchCheckMixin` и типы `Component`/`Unit`/`Iterable` | `weblate/checks/consistency.py:1-27` |
| `from weblate.logger import LOGGER` — идиома логирования (~20 модулей) | `weblate/logger.py:7` |
| у форка чеки первого класса живут в `weblate/checks/` и регистрируются в `weblate/checks/defaults.py` (прецедент — судейские чеки) | `weblate/checks/defaults.py:93-95` |
| `add_term(source, target, context="")` есть только на `GlossaryTest`; `GlossaryCoverageCommandTest` его не имеет и `self.tempdir` в файле нет | `weblate/glossary/tests.py:157-198`, `:1168-1225` |
| `BaseCommand` форка не переопределяет `add_arguments`, поэтому `--project`/`--baseline` ни с чем не конфликтуют | `weblate/utils/management/base.py:23-42` |
| провал команды сообщается `CommandError`; `sys.exit` в кодовой базе не используется | `weblate/glossary/management/commands/glossary_coverage.py:44-48` |
| снимки документации генерируются `make -C docs update-docs` и автокоммитятся в CI; сборка с warnings-as-errors — `./ci/run-docs` | `docs/Makefile:45-55`, `.github/workflows/docs.yml:25-89`, `ci/run-docs` |
| разделы `docs/admin/management.rst` **не** упорядочены по алфавиту | `docs/admin/management.rst:78-1248` |
| у прод-проекта `need-for-greed` `allow_translation_propagation=True` у 7 обычных компонентов и `False` у `glossary` | замер 2026-09-03 |

## Решения по конструкции

| # | Решение | Альтернатива | Почему |
|---|---|---|---|
| D1 | группировка по тексту источника, `context` в ключ не входит | стоковая группировка по `id_hash` | `id_hash` включает ключ; ключи у дублей различны, класс невидим (0 из 84, 0 из 25, 0 из 122) |
| D2 | скоуп — проект, `batch_project_wide = True` | скоуп — компонент | на проде 4 из 122 групп межкомпонентные; проектный скоуп уже реализован в стоке. Цена честно: на каждый финализ компонента — по одному агрегату на языковую группу под `project.checks_lock` с ретраем по таймауту, ровно как у `inconsistent`/`reused` |
| D3 | точный текст источника решает; `MD5(Lower(source))` — только сужение под индекс, финальное сравнение в питоне по `unit.source` | группировка по свёрнутому регистру | приёмочные 122/259 замерены по точному тексту; свёртка склеила бы законные пары `Атака`/`атака` и сделала бы число невоспроизводимым. Точное сравнение снимает и риск md5-коллизии |
| D4 | компоненты с `is_glossary=True` исключены явным фильтром | положиться на `allow_translation_propagation=False` | исключение снимает 168 из 290 групп; на глоссарных компонентах target-чеки и так не запускаются (`unit.py:2179`), но полагаться на это как на настройку нельзя |
| D5 | `allow_translation_propagation=True` обязателен, как в стоке | ослабить гейт | ослабление пришлось бы синхронно вносить в три места (живой путь, `check_component`, радиус удаления в `_perform_batch`), иначе строки создаются и не убираются |
| D6 | сравниваются только `state >= STATE_TRANSLATED`, и живой путь тоже возвращает `False` для меньших состояний; исходные переводы исключены на уровне SQL | как в стоке (только батч фильтрует состояние) | иначе «нужно править» (10) помечается живым путём и снимается следующим батчем — мигание; исходный юнит имеет `target == source` и делает группу тривиальной |
| D7 | группировка внутри `translation__plural_id` | join на `Language` | plural привязан к языку, и запрос остаётся без join на `trans_component`/`trans_translation`. Оговорка: у языка может быть несколько объектов `Plural` (разные формулы у форматов), тогда группа распадётся; то же ограничение у стокового `inconsistent` |
| D8 | `default_disabled = True`, включается флагом `repeat-drift` на проекте или компоненте | включён глобально | дубли законны не везде; включение — решение владельца |
| D9 | **новый режим `propagates = "repeat"`** поверх `Unit.repeat_units` | `propagates = None` (устаревшую строку снимет батч) / `propagates = "source"` | `"source"` удалял бы строки на соседях по `source+context` — не тот набор; `None` оставлял бы строку на соседе до следующей массовой операции, потому что обычная правка в UI батч-окна не открывает. Замер: группы максимум по 6 юнитов, то есть распространение по группе дёшево |
| D10 | косметических флагов нет | три флага свёрток | 6 групп из 122 (5%) и 3 из 25 (12%) на hub-1; `ignore-repeat-drift` закрывает это без кода |
| D11 | `batch_limit = 200` **на языковую группу**, с предупреждением в лог при достижении | без капа / стоковые молчаливые `[:100]` и `[:20]` | второй запрос — `IN`-список, а не OR-развёртка, поэтому кап нужен как страховка; на проде максимум 13 групп на язык, и молчаливое усечение недопустимо |
| D12 | чек живёт в `weblate/checks/consistency.py`, регистрируется в `weblate/checks/defaults.py` | `weblate_customization/…/checks.py` | ничего игрового в нём нет, он делит внутренности с `ConsistencyCheck`; прецедент — судейские чеки; попутно не нужен шаг `cp -r` в dev-контейнер |
| D13 | `repeat-drift` не передаётся в промпт MT/починки | передавать как обычный чек | промпт требует «исправь так, чтобы все перечисленные чеки прошли», а чек висит на всех членах группы, включая правильный; починка гонялась бы за соседом и могла бы осциллировать |
| D14 | чек **никогда не ставится в `enforced_checks`** — только документируется | разрешить принудительный | принудительный чек переводит все члены группы, включая правильный, в «нужно перезаписать» |
| D15 | аудит глоссария — read-only команда, а не чек | чек на глоссарных юнитах | находка касается пары терминов, а не строки; к тому же target-чеки на глоссарных компонентах не выполняются |
| D16 | у аудита есть `--baseline`; файл лежит в `analysis/data/glossary-audit/<project>.baseline` и трекается | всегда ненулевой код возврата | на проде обе находки класса A — намеренные омонимы миграции; без baseline команда в CI бесполезна. Запуск: вручную в контейнере после каждого импорта глоссария из loc-kit и перед релизом; в CI её ставить нельзя — у CI нет данных инстанса, а прогон на проде требует одобрения |

## Задача 0. Замер и проба — **выполнено 2026-09-03**

**Файлы:**

- Создано: `analysis/probes/source-repeat-drift.py`
- Создано: `analysis/data/source-repeat-drift-2026-09-03/need-for-greed.json`
- Создано: `docs/operations/measurements/2026-09-03-need-for-greed-repeat-drift.md`

```bash
PROD_WEBLATE_API_TOKEN=... uv run python analysis/probes/source-repeat-drift.py \
    --captured-at 2026-09-03
```

Секция `fixtures_ignore_punctuation` в JSON — источник регрессионных фикстур:
25 худших групп с ключами, компонентами и переводами.

## Задача 1. `Unit.repeat_units`, класс чека и живой путь

**Файлы:**

- Изменить: `weblate/trans/models/unit.py:17` (добавить `F` в импорт), новое свойство рядом с `propagated_units` (`:2577-2586`)
- Изменить: `weblate/checks/consistency.py` (новый класс после `ReusedCheck`, то есть после строки 293)
- Изменить: `weblate/checks/tests/test_consistency_checks.py` (вынести хелпер в миксин, добавить класс тестов)

**Шаг 1: вынести существующий хелпер в миксин** (чтобы не наследовать
`ConsistencyCheckTest` целиком и не прогонять его тесты дважды). В
`test_consistency_checks.py` заменить начало `ConsistencyCheckTest`:

```python
class SameSourceUnitsMixin:
    """Create units with an explicit context, source and target."""

    def setUp(self) -> None:
        super().setUp()
        self.other = self.create_link_existing()
        self.translation_1 = self.component.translation_set.get(language__code="cs")
        self.translation_2 = self.other.translation_set.get(language__code="cs")
        self._id_hash = 1000

    def add_unit(
        self,
        translation,
        context: str,
        source: str,
        target: str,
        increment: bool = True,
    ): ...  # тело переносится без изменений из строк 168-186


class ConsistencyCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    pass  # остальные тесты класса остаются на месте
```

**Шаг 2. Написать падающий тест.**

```python
class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def test_same_source_different_key_drifts(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")

        self.assertTrue(check.check_target_unit([], [], unit))

    def test_same_source_same_translation_is_clean(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Ahoj")

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_case_distinct_sources_are_not_one_group(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "hello", "Nazdar")

        self.assertFalse(check.check_target_unit([], [], unit))
```

Третий тест — барьер решения D3: он падает, если группировать по свёрнутому
регистру.

**Шаг 3. Убедиться, что тесты падают.**

```bash
source scripts/test-database.sh && DJANGO_SETTINGS_MODULE=weblate.settings_test \
    uv run pytest weblate/checks/tests/test_consistency_checks.py -k RepeatDrift -x
```

Ожидание: `ImportError: cannot import name 'RepeatDriftCheck'`.

**Шаг 4: реализация.** В `weblate/trans/models/unit.py` добавить `F` в импорт
из `django.db.models` и свойство после `propagated_units`:

```python
class Unit(models.Model):
    @cached_property
    def repeat_units(self) -> UnitQuerySet:
        """Units with the same source text under another key, project-wide."""
        translation = self.translation
        component = translation.component
        return (
            Unit.objects.filter(
                translation__component__project_id=component.project_id,
                translation__component__allow_translation_propagation=True,
                translation__component__is_glossary=False,
                translation__plural_id=translation.plural_id,
                source__lower__md5=MD5(Lower(Value(self.source))),
                source=self.source,
                state__gte=STATE_TRANSLATED,
            )
            .exclude(
                translation__language_id=F("translation__component__source_language_id")
            )
            .exclude(pk=self.pk)
            .prefetch()
        )
```

В `weblate/checks/consistency.py` (новый импорт только
`from weblate.logger import LOGGER`, он понадобится в задаче 5):

```python
REPEAT_DRIFT_CHECK_ID = "repeat-drift"


class RepeatDriftCheck(TargetCheck, BatchCheckMixin):
    """Check for a repeated source string translated differently under another key."""

    check_id = REPEAT_DRIFT_CHECK_ID
    name = gettext_lazy("Inconsistent repeat")
    description = gettext_lazy(
        "The same source string is translated differently under a different key."
    )
    default_disabled = True
    batch_project_wide = True
    skip_suggestions = True
    batch_limit = 200

    def check_target_unit(
        self, sources: list[str], targets: list[str], unit: Unit
    ) -> bool:
        component = unit.translation.component
        if component.is_glossary or not component.allow_translation_propagation:
            return False
        if unit.state < STATE_TRANSLATED:
            # The batch pass compares translated units only; flagging a
            # needs-editing unit here would flap on the next batch.
            return False

        if component.batch_checks:
            return self.handle_batch(unit, component)

        return unit.repeat_units.exclude(target=unit.target).exists()

    def check_single(self, source: str, target: str, unit: Unit) -> bool:
        """Target strings are checked in check_target_unit."""
        return False
```

**Шаг 5: прогнать тесты.** Ожидание: 3 passed.

**Шаг 6. Коммит.**

```bash
git add weblate/trans/models/unit.py weblate/checks/consistency.py \
    weblate/checks/tests/test_consistency_checks.py
git commit -m "feat(checks): detect a repeated source translated differently"
```

## Задача 2. Гейты живого пути

**Файлы:**

- Изменить: `weblate/checks/tests/test_consistency_checks.py`

**Шаг 1. Написать падающие тесты.**

```python
class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def test_glossary_component_is_ignored(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")
        unit.translation.component.is_glossary = True

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_propagation_disabled_is_ignored(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")
        unit.translation.component.allow_translation_propagation = False

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_needs_editing_unit_is_ignored(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Nazdar")
        unit.state = STATE_FUZZY

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_other_language_is_not_a_repeat(self) -> None:
        check = RepeatDriftCheck()
        german = self.component.translation_set.get(language__code="de")
        self.add_unit(german, "greet_intro", "Hello", "Hallo")
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Ahoj")

        self.assertFalse(check.check_target_unit([], [], unit))

    def test_source_language_translation_is_excluded(self) -> None:
        check = RepeatDriftCheck()
        czech = Language.objects.get(code="cs")
        self.other.source_language = czech
        self.other.save(update_fields=["source_language"])
        # add_unit would create the pair inside one translation here, so build
        # the source-side unit directly.
        self.translation_2.unit_set.create(
            id_hash=9001,
            position=9001,
            context="greet_source",
            source="Hello",
            target="Nazdar",
            state=STATE_TRANSLATED,
        )
        unit = self.add_unit(self.translation_1, "greet_outro", "Hello", "Ahoj")

        self.assertFalse(check.check_target_unit([], [], unit))
```

Импорты в шапке файла: `STATE_FUZZY` из `weblate.utils.state`, `Language` из
`weblate.lang.models` — добавить, если их там нет.

Последний тест — барьер: он падает, если убрать `exclude` по исходному языку
из `Unit.repeat_units`. Третий падает, если убрать проверку состояния из
`check_target_unit`. Первые два не сохраняют объекты в БД специально: гейт
обязан читать компонент юнита, а не БД.

**Шаг 2-4:** прогнать `-k RepeatDrift`, довести реализацию, прогнать снова.

**Шаг 5. Коммит.**

```bash
git add weblate/checks/tests/test_consistency_checks.py weblate/trans/models/unit.py \
    weblate/checks/consistency.py
git commit -m "test(checks): pin repeat-drift scope gates"
```

## Задача 3. Регистрация чека

**Файлы:**

- Изменить: `weblate/checks/defaults.py:57` (после `ReusedCheck`)
- Изменить: `weblate/checks/tests/test_consistency_checks.py`

**Шаг 1: написать падающий тест** — через настоящий вход, а не через
`Check.name`:

```python
class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def test_registered_and_reachable_through_run_checks(self) -> None:
        self.assertIn("repeat-drift", CHECKS)
        Component.objects.filter(project=self.project).update(
            check_flags="repeat-drift"
        )
        translation = Translation.objects.get(pk=self.translation_1.pk)
        first = self.add_unit(translation, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(translation, "greet_outro", "Hello", "Nazdar")

        second.run_checks()

        self.assertIn("repeat-drift", second.all_checks_names)
        self.assertIn("repeat-drift", Unit.objects.get(pk=first.pk).all_checks_names)
```

Перечитывание `Translation` обязательно: `Component.all_flags` и
`Project.effective_check_flags` — `cached_property`, и объекты, созданные до
`update`, помнят пустые флаги.

**Шаг 2: прогнать.** Ожидание: `AssertionError: 'repeat-drift' not found in
CHECKS` — незарегистрированный чек `run_checks` не выполняет вовсе
(`weblate/trans/models/unit.py:2188-2196`).

**Шаг 3. Реализация.**

```text
    "weblate.checks.consistency.ReusedCheck",
    "weblate.checks.consistency.RepeatDriftCheck",
```

**Шаг 4: прогнать.** Ожидание: passed. Второе утверждение проходит только
благодаря распространению из задачи 6 — до неё оставить его закомментированным
с пометкой `# см. задачу 6` и раскомментировать там.

**Шаг 5. Коммит.**

```bash
git add weblate/checks/defaults.py weblate/checks/tests/test_consistency_checks.py
git commit -m "feat(checks): register repeat-drift as a disabled-by-default check"
```

## Задача 4. Батч-путь `check_component` и форма SQL

**Файлы:**

- Изменить: `weblate/checks/consistency.py`
- Изменить: `weblate/checks/tests/test_consistency_checks.py`

**Шаг 1. Написать падающие тесты.**

```python
class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def test_check_component_finds_cross_component_drift(self) -> None:
        check = RepeatDriftCheck()
        self.assertEqual(list(check.check_component(self.component)), [])

        first = self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        self.assertEqual(
            {unit.pk for unit in check.check_component(self.component)},
            {first.pk, second.pk},
        )

    def test_check_component_ignores_agreeing_repeats(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        self.add_unit(self.translation_2, "greet_outro", "Hello", "Ahoj")

        self.assertEqual(list(check.check_component(self.component)), [])

    def test_aggregate_groups_by_source_not_context(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        with CaptureQueriesContext(connection) as queries:
            list(check.check_component(self.component))

        aggregate_sql = next(
            query["sql"].upper() for query in queries if "MIN(" in query["sql"].upper()
        )
        self.assertIn("MD5(LOWER", aggregate_sql)
        self.assertNotIn('"TRANS_UNIT"."CONTEXT"', aggregate_sql)
        self.assertNotIn('"TRANS_UNIT"."ID_HASH"', aggregate_sql)
        self.assertNotIn('"TRANS_COMPONENT"', aggregate_sql)
```

Третий тест — главный мутационный барьер плана: группировка по `id_hash` или
по `(source, context)` его роняет.

**Шаг 2: прогнать.** Ожидание: `NotImplementedError` из
`BatchCheckMixin.check_component`.

**Шаг 3. Реализация.**

```python
class RepeatDriftCheck(TargetCheck, BatchCheckMixin):
    def check_component(self, component: Component) -> Iterable[Unit]:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models import Translation, Unit

        translation_ids_by_plural: dict[int, list[int]] = defaultdict(list)
        for translation_id, plural_id in (
            Translation.objects.filter(
                component__project=component.project,
                component__allow_translation_propagation=True,
                component__is_glossary=False,
            )
            .exclude_source()
            .values_list("id", "plural_id")
        ):
            translation_ids_by_plural[plural_id].append(translation_id)

        for plural_id, translation_ids in sorted(translation_ids_by_plural.items()):
            candidates = (
                Unit.objects.filter(
                    translation_id__in=translation_ids, state__gte=STATE_TRANSLATED
                )
                .annotate(source_md5=MD5(Lower("source")))
                .values("source_md5")
                .annotate(min_target=Min("target"), max_target=Max("target"))
                .filter(min_target__lt=F("max_target"))
                .order_by("source_md5")[: self.batch_limit]
            )
            hashes = [row["source_md5"] for row in candidates]
            if not hashes:
                continue
            if len(hashes) == self.batch_limit:
                LOGGER.warning(
                    "repeat-drift: hit the %d group cap on %s (plural %d)",
                    self.batch_limit,
                    component.project.slug,
                    plural_id,
                )

            units = (
                Unit.objects.filter(
                    translation_id__in=translation_ids, state__gte=STATE_TRANSLATED
                )
                .annotate(source_md5=MD5(Lower("source")))
                .filter(source_md5__in=hashes)
                .prefetch()
                .prefetch_bulk()
            )

            # The aggregate narrows case-insensitively because that is the
            # indexed expression; the decision is made on the exact source.
            groups: dict[str, list[Unit]] = defaultdict(list)
            for unit in units:
                groups[unit.source].append(unit)
            for members in groups.values():
                if len({member.target for member in members}) > 1:
                    yield from members
```

**Шаг 4: прогнать.** Ожидание: все тесты класса зелёные.

**Шаг 5. Коммит.**

```bash
git add weblate/checks/consistency.py weblate/checks/tests/test_consistency_checks.py
git commit -m "feat(checks): add the project-wide repeat-drift batch pass"
```

## Задача 5. Сверка строк `Check`, гашение флагом и кап

**Файлы:**

- Изменить: `weblate/checks/tests/test_consistency_checks.py`

**Шаг 1. Написать падающие тесты.**

```python
class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def enable_repeat_drift(self):
        Component.objects.filter(project=self.project).update(
            check_flags="repeat-drift"
        )
        return Component.objects.get(pk=self.component.pk)

    def test_batch_pass_creates_and_clears_rows(self) -> None:
        check = RepeatDriftCheck()
        first = self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        check.perform_batch(self.enable_repeat_drift())
        self.assertEqual(
            set(
                Check.objects.filter(name="repeat-drift").values_list(
                    "unit_id", flat=True
                )
            ),
            {first.pk, second.pk},
        )

        Unit.objects.filter(pk=second.pk).update(target="Ahoj")
        check.perform_batch(self.enable_repeat_drift())
        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 0)

    def test_ignore_flag_keeps_the_unit_clean(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")
        Unit.objects.filter(pk=second.pk).update(extra_flags="ignore-repeat-drift")

        check.perform_batch(self.enable_repeat_drift())

        self.assertEqual(
            list(
                Check.objects.filter(name="repeat-drift").values_list(
                    "unit_id", flat=True
                )
            ),
            [self.translation_1.unit_set.get(context="greet_intro").pk],
        )

    def test_group_cap_is_reported(self) -> None:
        check = RepeatDriftCheck()
        check.batch_limit = 1
        self.add_unit(self.translation_1, "one_a", "One", "Jeden")
        self.add_unit(self.translation_1, "one_b", "One", "Jedna")
        self.add_unit(self.translation_1, "two_a", "Two", "Dva")
        self.add_unit(self.translation_1, "two_b", "Two", "Dvě")

        with self.assertLogs("weblate", level="WARNING") as logs:
            units = list(check.check_component(self.component))

        self.assertIn("hit the 1 group cap", "\n".join(logs.output))
        self.assertEqual(len(units), 2)
```

Второй тест — барьер решения D10: гашение обязано работать через штатный
`ignore-repeat-drift`, и `_perform_batch` отфильтровывает погашенный юнит
через `should_skip` (`weblate/checks/base.py:332`).

**Шаг 2-4:** прогнать, довести, прогнать снова.

**Шаг 5. Коммит.**

```bash
git add weblate/checks/tests/test_consistency_checks.py weblate/checks/consistency.py
git commit -m "test(checks): pin repeat-drift row reconciliation, ignore flag and cap"
```

## Задача 6. Распространение: снять чек с соседей сразу после правки

**Файлы:**

- Изменить: `weblate/checks/base.py` (тип `propagates`)
- Изменить: `weblate/trans/models/unit.py:2199`, `:2251-2267`, `:2279-2295`
- Изменить: `weblate/checks/consistency.py` (`propagates = "repeat"`)
- Изменить: `weblate/checks/tests/test_consistency_checks.py`

**Шаг 1. Написать падающие тесты.**

```python
class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def test_fixing_one_member_clears_the_sibling(self) -> None:
        component = self.enable_repeat_drift()
        translation = Translation.objects.get(pk=self.translation_1.pk)
        first = self.add_unit(translation, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(translation, "greet_outro", "Hello", "Nazdar")
        second.run_checks()
        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 2)

        second = Unit.objects.get(pk=second.pk)
        second.translate(self.user, "Ahoj", STATE_TRANSLATED)

        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 0)
        self.assertNotIn("repeat-drift", Unit.objects.get(pk=first.pk).all_checks_names)
        self.assertIsNotNone(component)

    def test_breaking_one_member_flags_the_sibling(self) -> None:
        self.enable_repeat_drift()
        translation = Translation.objects.get(pk=self.translation_1.pk)
        first = self.add_unit(translation, "greet_intro", "Hello", "Ahoj")
        second = self.add_unit(translation, "greet_outro", "Hello", "Ahoj")
        second.run_checks()
        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 0)

        second = Unit.objects.get(pk=second.pk)
        second.translate(self.user, "Nazdar", STATE_TRANSLATED)

        self.assertEqual(Check.objects.filter(name="repeat-drift").count(), 2)
        self.assertIn("repeat-drift", Unit.objects.get(pk=first.pk).all_checks_names)
```

**Шаг 2: прогнать.** Ожидание: первый тест оставляет строку на `first`
(снимать её некому), второй — не создаёт её вовсе.

**Шаг 3: реализация.** В `weblate/checks/base.py` расширить тип:

```text
    propagates: Literal["source", "target", "repeat"] | None = None
```

В `weblate/trans/models/unit.py`:

```text
        propagation: set[Literal["source", "target", "repeat"]] = set()
```

в ветке удаления (`:2253`) добавить третий случай:

```text
                        elif check_obj.propagates == "repeat":
                            propagated_units = self.repeat_units
                            values = set(
                                propagated_units.values_list("target", flat=True)
                            )
```

и в `querymap` (`:2282`):

```text
                "repeat": self.repeat_units,
```

В `weblate/checks/consistency.py` добавить чеку `propagates = "repeat"`.

**Шаг 4: прогнать.** Ожидание: оба теста зелёные; раскомментировать второе
утверждение теста задачи 3.

**Шаг 5. Коммит.**

```bash
git add weblate/checks/base.py weblate/trans/models/unit.py \
    weblate/checks/consistency.py weblate/checks/tests/test_consistency_checks.py
git commit -m "feat(checks): propagate repeat-drift across the repeat group"
```

## Задача 7. Описание находки для человека

**Файлы:**

- Изменить: `weblate/checks/consistency.py`
- Изменить: `weblate/checks/tests/test_consistency_checks.py`

**Шаг 1. Написать падающий тест.**

```python
class RepeatDriftCheckTest(SameSourceUnitsMixin, ComponentTestCase):
    def test_description_lists_the_other_renderings(self) -> None:
        check = RepeatDriftCheck()
        self.add_unit(self.translation_1, "greet_intro", "Hello", "Ahoj")
        unit = self.add_unit(self.translation_2, "greet_outro", "Hello", "Nazdar")

        description = check.get_description(Check(unit=unit, name="repeat-drift"))

        self.assertIn("Ahoj", description)
        self.assertNotIn("Nazdar", description)
```

**Шаг 2: прогнать.** Ожидание: «Ahoj» в описании нет.

**Шаг 3: реализация** (по образцу `ReusedCheck.get_description`,
`weblate/checks/consistency.py:209-227`)

```python
class RepeatDriftCheck(TargetCheck, BatchCheckMixin):
    def get_description(self, check_obj):
        unit = check_obj.unit
        others = (
            unit.repeat_units.exclude(target=unit.target)
            .values_list("target", flat=True)
            .distinct()
        )
        if not others:
            return super().get_description(check_obj)
        return format_html(
            "{} {}",
            gettext("The same source string is translated differently elsewhere:"),
            format_html_join_comma(
                "{}", ((self.format_value(other),) for other in others)
            ),
        )
```

**Шаг 4-5. Прогнать и закоммитить.**

```bash
git add weblate/checks/consistency.py weblate/checks/tests/test_consistency_checks.py
git commit -m "feat(checks): show the diverging renderings in the repeat-drift description"
```

## Задача 8. Чек не уходит в промпт машинного перевода

**Файлы:**

- Изменить: `weblate/machinery/llm.py:606-631`
- Изменить: `weblate/machinery/tests.py` (или существующий тест
  `_get_failing_checks_context` — найти по `failing_checks`)

**Шаг 1: написать падающий тест** — юнит с единственной строкой
`repeat-drift` не даёт ни одного элемента `failing_checks`:

```text
    def test_repeat_drift_is_not_a_machine_defect(self) -> None:
        unit = ...  # юнит с Check(name="repeat-drift", dismissed=False)
        checks, advisories = LLMTranslation._get_failing_checks_context(
            unit, unit.source
        )
        self.assertEqual(checks, [])
        self.assertEqual(advisories, [])
```

**Шаг 2: прогнать.** Ожидание: `checks` содержит `{"check_id": "repeat-drift", …}`.

**Шаг 3: реализация** — рядом с существующей веткой `GLOSSARY_CHECK_ID`:

```text
from weblate.checks.consistency import REPEAT_DRIFT_CHECK_ID
...
            if check_id == REPEAT_DRIFT_CHECK_ID:
                # The check fires on every member of a repeat group, including
                # the correct one, and the prompt treats a listed check as a
                # mandatory rewrite: a repair would chase a sibling instead.
                continue
```

**Шаг 4-5. Прогнать и закоммитить.**

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "fix(machinery): keep repeat-drift out of the translation prompt"
```

## Задача 9. `audit_glossary`: класс A — дубль термина с разошедшимися переводами

**Файлы:**

- Создать: `weblate/glossary/management/commands/audit_glossary.py`
- Изменить: `weblate/glossary/tests.py` (новый класс тестов; база — `GlossaryTest`, у которой есть `add_term`, `self.glossary` и `self.glossary_component`)

**Шаг 1. Написать падающий тест.**

```python
class AuditGlossaryCommandTest(GlossaryTest):
    def run_command(self, **kwargs) -> str:
        output = StringIO()
        call_command("audit_glossary", stdout=output, **kwargs)
        return output.getvalue()

    def run_command_expecting_findings(self, **kwargs) -> str:
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("audit_glossary", stdout=output, **kwargs)
        return output.getvalue()

    def test_duplicate_term_with_diverging_targets(self) -> None:
        self.add_term("Chest", "Сундук", context="loot")
        self.add_term("Chest", "Ящик", context="ui")

        output = self.run_command_expecting_findings()

        self.assertIn("duplicate-term", output)

    def test_duplicate_term_with_one_target_is_clean(self) -> None:
        self.add_term("Chest", "Сундук", context="loot")
        self.add_term("Chest", "Сундук", context="ui")

        self.assertIn("no findings", self.run_command())
```

**Шаг 2. Прогнать.**

```bash
source scripts/test-database.sh && DJANGO_SETTINGS_MODULE=weblate.settings_test \
    uv run pytest weblate/glossary/tests.py -k AuditGlossary -x
```

Ожидание: `CommandError: Unknown command: 'audit_glossary'`.

**Шаг 3. Реализация.**

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report glossary terms that contradict each other."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.trans.models import Component
from weblate.utils.management.base import BaseCommand
from weblate.utils.state import STATE_TRANSLATED

if TYPE_CHECKING:
    from django.core.management.base import CommandParser


@dataclass(frozen=True, order=True)
class Finding:
    kind: str
    component: str
    language: str
    subject: str
    detail: str

    @property
    def key(self) -> str:
        return f"{self.kind}\t{self.component}\t{self.language}\t{self.subject}"

    def format(self) -> str:
        return f"{self.key}\t{self.detail}"


class Command(BaseCommand):
    help = "report glossary duplicates and collapsed terms"

    def add_arguments(self, parser: CommandParser) -> None:
        super().add_arguments(parser)
        parser.add_argument("--project", help="limit the audit to one project slug")
        parser.add_argument(
            "--baseline",
            type=Path,
            help="file of accepted finding keys; one per line, '#' starts a comment",
        )

    def handle(self, *args, **options) -> None:
        components = Component.objects.filter(is_glossary=True).order_by(
            "project__slug", "slug"
        )
        if options["project"]:
            components = components.filter(project__slug=options["project"])
            if not components.exists():
                msg = f"No glossary in project: {options['project']}"
                raise CommandError(msg)

        findings: list[Finding] = []
        for component in components:
            findings.extend(self.audit(component))
        findings.sort()

        accepted = self.read_baseline(options["baseline"])
        unaccepted = [finding for finding in findings if finding.key not in accepted]

        for finding in findings:
            marker = " " if finding.key in accepted else "!"
            self.stdout.write(f"{marker} {finding.format()}")
        if not findings:
            self.stdout.write("no findings")

        if unaccepted:
            msg = f"{len(unaccepted)} unaccepted glossary findings"
            raise CommandError(msg)

    def read_baseline(self, path: Path | None) -> set[str]:
        if path is None:
            return set()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            msg = f"Cannot read baseline: {path}"
            raise CommandError(msg) from error
        return {
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        }

    def audit(self, component: Component) -> list[Finding]:
        label = f"{component.project.slug}/{component.slug}"
        source_translation = component.source_translation
        terms = {
            unit.pk: unit.source.strip()
            for unit in source_translation.unit_set.all()
            if unit.source.strip()
        }
        by_source: dict[str, list[int]] = defaultdict(list)
        for pk, source in terms.items():
            by_source[source.lower()].append(pk)

        findings: list[Finding] = []
        for translation in component.translation_set.exclude_source().order_by(
            "language__code"
        ):
            language = translation.language.code
            targets = {
                unit.source_unit_id: unit.target.strip()
                for unit in translation.unit_set.filter(state__gte=STATE_TRANSLATED)
                if unit.target.strip()
            }
            for source, pks in sorted(by_source.items()):
                if len(pks) < 2:
                    continue
                values = sorted({targets[pk] for pk in pks if pk in targets})
                if len(values) > 1:
                    findings.append(
                        Finding(
                            "duplicate-term",
                            label,
                            language,
                            source,
                            " | ".join(values),
                        )
                    )
        return findings
```

**Шаг 4: прогнать.** Ожидание: 2 passed.

**Шаг 5. Коммит.**

```bash
git add weblate/glossary/management/commands/audit_glossary.py weblate/glossary/tests.py
git commit -m "feat(glossary): audit duplicate terms with diverging targets"
```

## Задача 10. `audit_glossary`: класс B — один перевод на несколько терминов

**Файлы:**

- Изменить: `weblate/glossary/management/commands/audit_glossary.py`
- Изменить: `weblate/glossary/tests.py`

**Шаг 1. Написать падающий тест.**

```python
class AuditGlossaryCommandTest(GlossaryTest):
    def test_one_target_for_two_terms(self) -> None:
        self.add_term("Dealer", "Торговец", context="buyer")
        self.add_term("Trader", "Торговец", context="ui")

        output = self.run_command_expecting_findings()

        self.assertIn("collapsed-terms", output)
```

**Шаг 2: прогнать.** Ожидание: `CommandError` не поднят, находки нет.

**Шаг 3: реализация** — внутри цикла по переводам, после класса A:

```text
            by_target: dict[str, set[str]] = defaultdict(set)
            for source_unit_id, target in targets.items():
                source = terms.get(source_unit_id)
                if source:
                    by_target[target.lower()].add(source.lower())
            for target, sources in sorted(by_target.items()):
                if len(sources) > 1:
                    findings.append(
                        Finding(
                            "collapsed-terms",
                            label,
                            language,
                            target,
                            " | ".join(sorted(sources)),
                        )
                    )
```

**Шаг 4-5. Прогнать и закоммитить.**

```bash
git add weblate/glossary/management/commands/audit_glossary.py weblate/glossary/tests.py
git commit -m "feat(glossary): audit terms collapsed into one translation"
```

## Задача 11. `--baseline`, код возврата и мутационные барьеры аудита

**Файлы:**

- Изменить: `weblate/glossary/tests.py`

**Шаг 1. Написать падающие тесты.**

```python
class AuditGlossaryCommandTest(GlossaryTest):
    def test_baseline_accepts_a_known_finding(self) -> None:
        self.add_term("Chest", "Сундук", context="loot")
        self.add_term("Chest", "Ящик", context="ui")
        output = self.run_command_expecting_findings()
        key = next(
            line[2:].rsplit("\t", 1)[0]
            for line in output.splitlines()
            if line.startswith("! duplicate-term")
        )

        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "accepted.baseline"
            baseline.write_text(f"# deliberate homonym\n{key}\n", encoding="utf-8")

            self.assertIn("duplicate-term", self.run_command(baseline=baseline))

    def test_case_only_difference_is_not_a_finding(self) -> None:
        self.add_term("Chest", "Сундук", context="loot")
        self.add_term("Chest", "сундук", context="ui")

        self.assertIn("no findings", self.run_command())

    def test_untranslated_term_is_not_a_finding(self) -> None:
        self.add_term("Chest", "Сундук", context="loot")
        self.add_term("Chest", "Ящик", context="ui")
        self.glossary.unit_set.filter(context="ui").update(state=STATE_EMPTY)

        self.assertIn("no findings", self.run_command())

    def test_unknown_project_fails_loudly(self) -> None:
        with self.assertRaises(CommandError):
            self.run_command(project="no-such-project")
```

Второй тест роняет реализацию, если сравнение станет регистрозависимым;
третий — если убрать `state__gte=STATE_TRANSLATED` (`add_term` всегда создаёт
юнит в состоянии `translated`, поэтому состояние в тесте меняется явно);
четвёртый — если убрать проверку пустого фильтра проектов. Импорты в шапке:
`tempfile`, `Path`, `STATE_EMPTY`.

**Шаг 2-4:** прогнать `-k AuditGlossary`, довести, прогнать снова.

**Шаг 5. Коммит.**

```bash
git add weblate/glossary/tests.py weblate/glossary/management/commands/audit_glossary.py
git commit -m "test(glossary): pin the audit baseline and folding rules"
```

## Задача 12. Документация, переводы и журнал изменений

**Файлы:**

- Изменить: `docs/snippets/checks-autogenerated.rst`, `docs/snippets/check-flags-autogenerated.rst` (генерируются)
- Изменить: `docs/admin/checks.rst` (проза после сгенерированного блока чека)
- Изменить: `docs/admin/management.rst` (после `reapply_autofixes`, в блоке форковых команд; файл **не** упорядочен по алфавиту, так что порядок тематический)
- Изменить: `docs/changes.rst` (верхняя нерелизная секция `Weblate 2026.8.1`)
- Изменить: `weblate/locale/ru/LC_MESSAGES/django.po` (три новые строки — вручную)
- Изменить: `docs/llm-first/vision/llm-first-product-architecture.md:665-671`

**Шаг 1. Перегенерировать снимки.**

```bash
make -C docs update-docs
```

Ожидание: в диффе появляются `.. _check-repeat-drift:` и флаг `repeat-drift`.

**Шаг 2: добавить прозу к чеку** — после сгенерированного блока
`check-repeat-drift` в `docs/admin/checks.rst`, по образцу соседних чеков:
что чек находит, что группу помечает целиком (включая правильный вариант),
что гасится `ignore-repeat-drift`, и что **в
:ref:`component-enforced_checks` его ставить не следует** — принудительный
режим перевёл бы всю группу в «нужно перезаписать».

**Шаг 3: описать команду** в `docs/admin/management.rst`:

```rst
audit_glossary
--------------

.. weblate-admin:: audit_glossary

Reports glossary terms that contradict each other: one term translated two
different ways in the same language, and two different terms sharing one
translation. The command only reads, and exits with an error when it finds
something that is not accepted in a baseline file. Run it after a glossary
import and before a release; there is no scheduled job, because the audit
needs a live instance database.

.. weblate-admin-option:: --project PROJECT

    Limits the audit to one project slug instead of every glossary on the
    instance.

.. weblate-admin-option:: --baseline PATH

    Reads accepted findings from :file:`PATH`, one key per line, with ``#``
    starting a comment. A deliberate homonym belongs here; every other finding
    keeps the run failing.
```

**Шаг 4: журнал изменений** — в `.. rubric:: New features`:

```rst
* Added an optional ``repeat-drift`` :ref:`check <checks>` that flags the same source string translated differently under another key, which the stock consistency check cannot see because it groups by the string key as well as the source text.
* Added a read-only ``audit_glossary`` management command reporting glossary terms that contradict each other.
```

**Шаг 5: русские переводы** — вручную дописать три строки в
`weblate/locale/ru/LC_MESSAGES/django.po` («Inconsistent repeat», описание
чека, «The same source string is translated differently elsewhere:») и
скомпилировать:

```bash
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py compilemessages
```

**`makemessages` широко не запускать**: он затягивает нечёткие пары по всему
файлу и молча ломает уже переведённые строки — инцидент зафиксирован в памяти
проекта; правим только нужные записи.

**Шаг 6: обновить карту** — в
`docs/llm-first/vision/llm-first-product-architecture.md:665-671` заменить
«внутри одного компонента» на проектный скоуп и «16%» на «22-31%», сослаться
на замер 2026-09-03.

**Шаг 7. Собрать документацию.**

```bash
./ci/run-docs
```

Ожидание: сборка без предупреждений (в скрипте включены `--nitpicky
--fail-on-warning`).

**Шаг 8. Коммит.**

```bash
git add docs/ weblate/locale/ru/LC_MESSAGES/django.po weblate/locale/ru/LC_MESSAGES/django.mo
git commit -m "docs: document the repeat-drift check and audit_glossary"
```

## Задача 13. Линтеры, типы и регрессия

**Шаг 1. Линт и формат.**

```bash
uv run prek run --all-files
```

**Шаг 2. Типы.**

```bash
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Ожидание: новых ошибок нет (расширение `Literal` в `propagates` типизировано).

**Шаг 3. Pylint.**

```bash
uv run pylint weblate/checks/consistency.py weblate/trans/models/unit.py \
    weblate/glossary/management/commands/audit_glossary.py
```

**Шаг 4. Регрессия по затронутым подсистемам.**

```bash
source scripts/test-database.sh && DJANGO_SETTINGS_MODULE=weblate.settings_test \
    uv run pytest weblate/checks weblate/glossary weblate/machinery \
    weblate/trans/tests/test_models.py
```

Ожидание: зелено; поведение `inconsistent`/`reused` не изменилось (правка
`run_checks` добавляет режим, не меняя существующие два).

**Шаг 5: коммит** (если линтеры что-то поправили)

```bash
git add -A && git commit -m "style: satisfy linters on the repeat-drift changes"
```

## Задача 14. Проверка в dev-контейнере и план запроса

**Шаг 1.** Правки лежат в `weblate/`, каталог смонтирован в `/app/src`,
Granian перезагрузится сам; `cp -r` нужен только для
`weblate_customization/`.

**Шаг 2. Прогнать тесты в контейнере.**

```bash
WEBLATE_PORT=3001 ./rundev.sh test weblate/checks/tests/test_consistency_checks.py -k RepeatDrift
```

**Шаг 3. Включить флаг и пересчитать.**

```bash
docker exec dev-docker-weblate-1 weblate updatechecks --lang cs <project>/<component>
```

Ожидание: `Operation completed`; в UI на строке появляется карточка
:guilabel:`Inconsistent repeat` со списком других переводов.

**Шаг 4: снять план запроса** (проверка R3 — индекс агрегат не обслуживает)

```bash
docker exec dev-docker-weblate-1 weblate dbshell -- \
    -c "EXPLAIN ANALYZE SELECT md5(lower(source)), min(target), max(target)
        FROM trans_unit WHERE translation_id IN (...) AND state >= 20
        GROUP BY 1 HAVING min(target) < max(target);"
```

Записать тип узла (`HashAggregate`/`GroupAggregate`) и время в раздел
«Проверка» этого плана.

**Шаг 5: убедиться, что чек снимается правкой** — привести один перевод к
другому и увидеть, что карточка исчезла **сразу** (задача 6), без пересчёта.

## Задача 15. Сверка ожидания с продом и решение о включении

**Шаг 1: повторить пробу** (read-only, без изменений на проде)

```bash
PROD_WEBLATE_API_TOKEN=... uv run python analysis/probes/source-repeat-drift.py \
    --captured-at <дата>
```

**Шаг 2: сверить с замером 2026-09-03** — ожидание для `need-for-greed` при
исключённом глоссарии: 122 группы, 259 юнитов (строгое сравнение цели и
точный текст источника). Расхождение больше ±10% означает, что корпус
изменился, и приёмочное число надо пересчитать до включения.

**Шаг 3: остановиться и спросить владельца.** Включение флага `repeat-drift`
на живом проекте — изменение прод-конфигурации, требующее явного одобрения по
`AGENTS.md`. В запросе показать: ожидаемые 259 строк чека, что чек advisory и
не блокирует ни отправку, ни ревью, и что в `enforced_checks` он не ставится.

**Шаг 4:** после одобрения включить флаг на проекте, прогнать `updatechecks`
по компонентам, сверить фактическое число строк `repeat-drift` с ожидаемым.

**Шаг 5:** записать результат новым дневным замером в
`docs/operations/measurements/`; этот план не переписывать, а сослаться на
него.

## Приёмка плана

- [ ] `RepeatDriftCheck` реализован в `weblate/checks/consistency.py`,
      зарегистрирован в `weblate/checks/defaults.py`, `default_disabled=True`.
- [ ] `Unit.repeat_units` группирует по точному тексту источника и языку,
      исключая глоссарные компоненты, исходные переводы и компоненты без
      распространения.
- [ ] Мутация «группировать по `id_hash`/`context`» роняет
      `test_aggregate_groups_by_source_not_context`.
- [ ] Мутация «свернуть регистр источника» роняет
      `test_case_distinct_sources_are_not_one_group`.
- [ ] Мутация «не исключать исходный перевод» роняет
      `test_source_language_translation_is_excluded` (проект со вторым
      исходным языком).
- [ ] Мутация «не проверять состояние в живом пути» роняет
      `test_needs_editing_unit_is_ignored`.
- [ ] Незарегистрированный чек роняет
      `test_registered_and_reachable_through_run_checks` (тест идёт через
      `run_checks` и реестр `CHECKS`, а не через `choices` у `Check.name`).
- [ ] `perform_batch` создаёт строки на всех членах разошедшейся группы и
      удаляет их, когда группа сошлась; `ignore-repeat-drift` оставляет юнит
      чистым; достижение капа логируется и проверено тестом.
- [ ] Правка одного члена группы снимает чек с соседей **сразу**
      (`propagates = "repeat"`), обратное направление тоже проверено.
- [ ] Юнит с одним лишь `repeat-drift` не даёт элементов `failing_checks` в
      промпте машинного перевода.
- [ ] Описание чека перечисляет другие переводы и не повторяет собственный.
- [ ] `audit_glossary` работает read-only на всех глоссариях инстанса,
      `--project` фильтрует и ругается на неизвестный слаг, `--baseline`
      принимает находку, непринятая находка даёт `CommandError`.
- [ ] Мутация «сравнение стало регистрозависимым» роняет
      `test_case_only_difference_is_not_a_finding`.
- [ ] `docs/changes.rst`, `docs/admin/checks.rst`, `docs/admin/management.rst`,
      снимки `docs/snippets/*-autogenerated.rst`, три русские строки и раздел
      карты обновлены; `./ci/run-docs` проходит.
- [ ] `prek`, `mypy`, `pylint` и `pytest weblate/checks weblate/glossary
      weblate/machinery` зелёные.
- [ ] Проверено в dev-контейнере: чек виден в UI, снимается правкой сразу,
      план запроса агрегата записан.
- [ ] Ожидание по проду сверено пробой; включение на проде **не выполняется**
      без отдельного одобрения.
- [ ] `check_glossary` остаётся выключенным; решение 2026-08-11 не
      пересматривается.

## Не входит

- **Стемминг в аудите глоссария.** Замер: 5 стем-групп, все 5 намеренные.
  Вернуть можно только под новый замер с настоящими находками.
- **Класс «перевод термина равен источнику другого термина».** Замерено 0
  находок при ru-источнике; пересмотреть при появлении проекта, где источник и
  цель в одном скрипте.
- **Косметические флаги чека**: 5% находок, гасится штатными средствами.
- **Участие глоссария в чеке дрейфа**: закрывается задачами 9-11.
- **`enforced_checks`**: чек туда не добавляется, только документируется
  запрет.
- **Предотвращение дрейфа** (канон в прогоне автоперевода):
  `docs/llm-first/plans/2026-08-17-session-canon.md`. Общий хелпер
  нормализации из решения 3 того плана остаётся его риском R5; этот план
  сознательно не заводит второй хелпер — он сравнивает точные строки, и
  нормализовать нечего.
- **Интеграция с CI-гейтом** `corpus.inconsistent-translation`: правило
  определено выше один раз, и план гейта обязан цитировать его, а не
  изобретать свою нормализацию
  (`docs/llm-first/plans/2026-08-10-git-localization-quality-gate.md:597-603`).
- **Включение на проде и любой прод-прогон**: только по отдельному одобрению.

## Риски

| # | Риск | Смягчение |
|---|---|---|
| R1 | законная вариативность помечена как дрейф (обращение, число, длина кнопки) | чек advisory и `default_disabled`; решение по группе — за человеком; на юнит ставится `ignore-repeat-drift`; в `enforced_checks` чек не добавляется |
| R2 | правка одного члена группы оставляет устаревшие строки на остальных | снято задачей 6: режим `propagates = "repeat"` пересчитывает соседей в той же транзакции. Остаточная стоимость: на каждую правку — по одному `run_checks` на члена группы; замер даёт максимум 6 членов |
| R3 | стоимость агрегата на большом проекте | агрегат — хеш-агрегация по юнитам проекта на языковую группу, тот же класс, что у стокового `inconsistent`; индекс `trans_unit_source_md5` обслуживает живой путь и `md5 IN (...)`, но **не** `GROUP BY`; план запроса снимается в задаче 14 |
| R4 | кап скрывает часть находок | кап на языковую группу, достижение логируется и проверено тестом; на проде максимум 13 групп на язык против капа 200 |
| R5 | правка `run_checks` затрагивает распространение стоковых чеков | добавляется третья ветка, две существующие не меняются; регрессия `weblate/checks` и `weblate/trans/tests/test_models.py` в задаче 13 |
| R6 | глоссарий вернётся в сравнение, если на нём включат распространение | исключение продублировано явным `is_glossary=False`, а `run_checks` на глоссарных компонентах выполняет только `CHECKS.glossary` |
| R7 | аудит глоссария станет вечно красным из-за намеренных омонимов | `--baseline` с принятыми находками в `analysis/data/glossary-audit/<project>.baseline`; на проде это ровно 2 находки (`Белоземье`, `Сундук Конунга`) |
| R8 | у языка несколько объектов `Plural` — группа распадётся | ограничение общее со стоковым `inconsistent`; зафиксировано в D7, кода не требует |
| R9 | правило разойдётся с CI-гейтом | правило определено в этом плане один раз и цитируется гейтом |

## Отношение к судье

| Класс дефекта | Кто ловит | Причина |
|---|---|---|
| глоссарный термин нарушен | судья, глоссарий подаётся в промпт | замер zh_Hans: 3 из 3 cross-дефектов; `check_glossary` остаётся выключенным |
| ошибка внутри строки | судья | правило «conformance necessary but never sufficient» в промпте |
| одинаковый источник, разный перевод под другим ключом | `RepeatDriftCheck` (задачи 1-7) | детерминированно, 0% флапа против 22-31% шума судьи; в промпт починки чек не передаётся (задача 8), потому что группу нельзя починить переписыванием одного члена |
| глоссарий противоречит себе | `audit_glossary` (задачи 9-11) | чеки перевода сверяются с глоссарием как с истиной и потому слепы к его самопротиворечию |
| структура семейства ключей | никто; открытый пункт | |

## Проверка

Заполняется при исполнении: дата, команда, наблюдение.
