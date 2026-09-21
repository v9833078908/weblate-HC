# План: симметричный read-only — снятие флага разблокирует строку во всех языках

> **Статус:** черновик для утверждения (ревизия 2 после ревью 2026-09-21).
> Реализация и тем более деплой требуют одобрения и не запускаются этим
> документом. Миграций нет; данные прода остаются нетронутыми до ручного
> разблокирования.
>
> **Что изменила ревизия:** цель честно сужена до страницы исходного юнита
> (снятие с любой языковой страницы было недостижимо: `edit_context` отдаёт
> 404 не-источнику не-глоссария до любого редиректа); каскад стал
> одновходовым с предвычисленным счётчиком; тесты API и bulk переписаны под
> достижимый сценарий (источник нёс флаг) плюс негативные кейсы, закрепляющие
> ограничение parked-случая; добавлен read-only проб `original_state` на проде;
> исправлены фактические ссылки на механизм парковки.

## Контекст и цель

Гейм-дизайнер Space Arena сообщил, что флаг «только для чтения» (`read-only`)
на ключах `%RANK_PROMOTION%`, `%NEW_RANK%`, `%WEEKLY%`, `%FOR_WINS%` стоит на
всех языках кроме английского и русского, и не снимается повторной установкой и
снятием флага на английском источнике. Проверено на проде (read-only, сегодня):
у этих ключей 15 юнитов; `en` и `ru` — state 20, флага нет; остальные 13
языков (`de es fa fr hi id ja ko pt_BR th tr vi zh_Hans`) — state 100 и
**свой** `extra_flags: read-only`, target пустой. Это парковка непереводимых
строк (`docs/operations/plans/2026-08-19-space-arena-lockit-producer-view-squadrons.md`),
поставленная скриптом поверх таргет-юнитов, а не флагом источника.

### Почему не снимается сегодня

Флаг `read-only` живёт на двух уровнях:

1. Флаг исходного юнита `source_unit.extra_flags` — подмешивается во все языки
   через `Unit.get_all_flags` (`weblate/trans/models/unit.py:2582-2600`, порядок
   слияния `translation → file → source_unit.extra_flags → self.extra_flags`).
2. Пер-юнитный `extra_flags` таргета — оверрайд, сильнее исходного флага.

Меню «Сервис» → «Mark/Unmark as read-only» (`Unit.get_flag_actions`,
`unit.py:2782-2869`) и кнопка в нём действуют только на **исходном юните**:
`edit_context` (`weblate/trans/views/source.py:31-81`) для не-глоссария
принимает только исходный юнит (не-источник — 404 на `:33-35`; редирект на
источник на `:43-49` существует только для глоссарных таргетов) и зовёт
`unit.update_extra_flags(new_flags)` — тот пишет флаги только в исходный юнит
(`unit.py:2930-2956`), а у остальных языков их не трогает. На не-исходных
юнитах не-глоссария действий с `read-only` в меню нет вообще (`get_flag_actions`
ветвится по `is_source`/`is_glossary`, `:2787`/`:2798`). Bulk edit пишет флаги
только исходным юнитам (`weblate/trans/bulk.py:148-173`) и пропускает запись,
если у самого источника флаг не изменился (`:170`). API PATCH заменяет флаги
только на исходном юните; глоссарному таргету разрешены лишь языковые флаги
`exact`/`not-applicable` (`weblate/api/views.py:4011-4031`), пер-юнитный
`read-only` через API недоступен ни одному писателю.

Итого: разлочить пер-юнитные оверрайды из UI/API сегодня нельзя ничем. Это
не только неудобство ГД, но и единственная реальная дырка в продукте.

## Цель и не-цели

**Цель.** Снятие `read-only` через «Сервис» **на странице исходного юнита
строки** снимает флаг во **всех** языках: и с исходного юнита (если был), и с
каждого таргет-юнита, у которого он стоит в собственном `extra_flags`;
состояния и проверки пересчитываются. Тот же каскад симметрично получают три
других писателя флагов источника — форма «Доп. информация о строке»
(`ContextForm`), bulk edit и API PATCH — в том единственном сценарии, где они
вообще могут его увидеть: **источник нёс `read-only` и его снимают**
(переход флага). Установка остаётся строкой-уровневой (как сегодня: флаг
источника блокирует все языки через объединение флагов).

**Не-цели.**

- Не вводить пер-языковую установку `read-only` с таргета (это не языковой
  флаг).
- Не открывать снятие `read-only` с языковых страниц не-источника: для этого
  пришлось бы ломать гейт 404 (`source.py:33-35`), заводить действия в меню
  таргета и переносить проверку `meta:unit.flag` на перевод источника после
  редиректа. Это отдельный продукт-скейп; текущий баг-репорт (ГД работал с
  английской страницей источника) им не требуется.
- Bulk edit и API **не разлочивают parked-случай** (источник без флага,
  оверрайды на таргетах): переход флага источника там не происходит, а
  расширять триггер каскада нельзя — иначе любая несвязанная запись флагов
  источника (`add_flags=python-format`) распакует строку. Массовый unpark
  4 337 запаркованных юнитов остаётся операционным скриптом
  (`docs/operations/plans/2026-08-19-space-arena-lockit-producer-view-squadrons.md`,
  раздел «Откат», `:77-93`). Закреплено негативными тестами (задачи 3, 4) и
  «Ограничениями».
- Не менять права; `meta:unit.flag` (=`source.edit` для не-глоссария,
  `weblate/auth/permissions.py:1304-1314`) остаётся единственным гейтом, как и
  сегодня для «Mark as read-only».
- Не делать миграций и не трогать данные прода: парковки остаются, пока
  кто-то не разблокирует строки вручную (со страницы источника — теперь без
  скрипта).
- Не вводить подтверждение-модал: действие из «Сервиса» остаётся one-click
  `link-post` (как все действия этого меню,
  `weblate/templates/translate.html:204-235`).

## Решение и обоснование

Два уровня: одновходовый каскад на модели (все писатели флагов источника
симметричны в сценарии «флаг источника снят») + явный метод
`unmark_string_read_only` для `edit_context`, который additionally покрывает
parked-случай, где у перехода флага источника нет и каскад нечем зацепить.

### Модель: `Unit.count_read_only_overrides()`

```python
def count_read_only_overrides(self) -> int:
    """Number of target units carrying ``read-only`` in their own extra_flags."""
    if not self.is_source:
        return 0
    return sum(
        1
        for ef in self.unit_set.exclude(pk=self.pk).values_list(
            "extra_flags", flat=True
        )
        if "read-only" in Flags(ef)
    )
```

Парсинг `Flags` точнее `extra_flags__contains`-фильтра (подстрока могла бы
совпасть с именем параметризованного флага). Один запрос; вызывается из меню
только когда у источника нет собственного `read-only` и флаг не унаследован
(см. `get_flag_actions`).

### Модель: `Unit._strip_read_only_overrides(user) -> int` — единственный исполнитель

```python
def _strip_read_only_overrides(self, user: User) -> int:
    """Strip ``read-only`` from every target unit's own extra_flags.

    The single executor of cross-language unlocking; call only on a source
    unit, inside the caller's transaction. For each target (excluding self)
    whose own ``Flags`` contain ``read-only``: remove the flag via
    ``update_extra_flags`` (records an ``EXTRA_FLAGS`` change for that unit),
    then restore the pre-parking state and re-run checks. Shares the
    in-memory source and component with every target so that state
    recomputation sees the caller's already-mutated source flags even when
    the source row has not been persisted yet (``save=False`` writers).
    Returns the number of target units changed.
    """
    changed = 0
    for unit in self.unit_set.select_for_update().exclude(pk=self.pk):
        unit.source_unit = self
        unit.translation.component = self.translation.component
        flags = Flags(unit.extra_flags)
        if "read-only" not in flags:
            continue
        flags.remove("read-only")
        unit.update_extra_flags(flags.format(), user)
        unit.update_state()
        unit.run_checks()
        changed += 1
    if changed and not self.is_batch_update:
        self.translation.component.invalidate_cache()
    return changed
```

Корректность:

- `unit.update_extra_flags` (`unit.py:2930-2956`) пишет `extra_flags`,
  создаёт `Change` (`ActionEvents.EXTRA_FLAGS`, старое→новое), зовёт
  `self.save(update_fields=["extra_flags"], same_content=True)`. На
  не-исходном юните `save` не зовёт `source_unit_save` (только `is_source`,
  `unit.py:869-870`), рекурсии нет; каскад в нём тоже не срабатывает (гейт
  `is_source`).
- Явный `unit.update_state()` после записи флагов необходим: save внутри
  `update_extra_flags` гоняет `run_checks` со `run_checks=True`
  (`unit.py:820`), но в этот момент юнит ещё в `STATE_READONLY`, и
  `run_checks` уходит в readonly-skip (`unit.py:2245-2247`). `update_state`
  (`unit.py:1579-1603`) по ветке `elif self.readonly and self.state !=
  self.original_state` возвращает `original_state` (для парковок — 0 или 20,
  см. «Ограничения», п. 2), и явный `run_checks()` досчитает проверки уже для
  разблокированного юнита.
- `unit.source_unit = self` — не косметика: без шардинга `all_flags` таргета
  (`get_all_flags`, `unit.py:2591-2600`) читает `source_unit.extra_flags`
  лениво из БД; при писателе с `save=False` (ContextForm,
  `weblate/trans/forms.py:1741-1752`) БД ещё держит старый флаг, и
  `update_state` оставил бы таргет в `STATE_READONLY` — «успешное» снятие без
  разблокировки. Тот же приём шардинга уже используется в `source_unit_save`
  для `translation.component` (`unit.py:1077-1079`).
- `Component.invalidate_cache` (`weblate/trans/models/component.py:5004-5009`)
  — `transaction.on_commit`, согласованная инвалидация статистики компонента и
  его переводов; гейт `is_batch_update` повторяет `source_unit_save:1083-1084`
  (в bulk-режиме инвалидацией и пачковой пересчёт проверок владеет
  `bulk_perform`).

### Модель: `Unit.unmark_string_read_only(user) -> int` — одновходовая оркестрация

```python
def unmark_string_read_only(self, user: User) -> int:
    """Remove ``read-only`` from this string in every language.

    Strips the flag from the source unit's own extra_flags (if present) and
    from every translation unit that carries it in its own extra_flags,
    then restores states and re-runs checks. Works when called on any unit
    of the string. Returns the number of unlocked target units (excluding
    the source itself).
    """
    verify_in_transaction()
    source = self if self.is_source else self.source_unit
    count = source.count_read_only_overrides()
    own = Flags(source.extra_flags)
    if "read-only" in own:
        own.remove("read-only")
        # update_extra_flags cascades: _strip runs exactly once inside it.
        source.update_extra_flags(own.format(), user)
    elif count:
        # Parked case: the source carries no flag, only the overrides lock.
        source._strip_read_only_overrides(user)
    return count
```

Ветвки непересекающиеся: `_strip` исполняется ровно один раз на вызов — либо
через каскад `update_extra_flags` (флаг источника был), либо напрямую
(parked). Счётчик снимается **до** любых мутаций, поэтому сообщение в UI
показывает верное число языков и в ветке «флаг источника был + оверрайды
были» (первая ревизия плана теряла его: вложенный каскад снимал оверрайды
раньше, а повторный `_strip` возвращал 0). Никакого `self = self.source_unit`
— локальная `source`; вызов на не-источнике разрешён и дёшев (FK уже
закреплён за строкой, `unit.py:803-810`).

### Каскад в `Unit.update_extra_flags` — переход флага источника

В конце `update_extra_flags` (`unit.py:2930-2956`), после записи изменений:

```python
if (
    self.is_source
    and "read-only" in Flags(old)
    and "read-only" not in Flags(extra_flags)
):
    self._strip_read_only_overrides(user)
```

- `old` уже вычислен в теле (`old = self.old_unit["extra_flags"]`, `:2935`);
  ранний возврат при `old == extra_flags` (`:2936-2937`) гарантирует, что в
  каскад попадает только реальный переход.
- Триггер — именно **переход** «был → стал без флага». Расширять его до
  «новых флагов без read-only» нельзя: тогда любая несвязанная запись флагов
  источника снимала бы пер-юнитные парковки.
- Гейт по `save` не нужен: корректность при `save=False` (ContextForm)
  обеспечивает шардинг `unit.source_unit = self` в `_strip`.
- Сигнатура метода меняется с `-> None` на `-> int` (число дополнительно
  разблокированных таргетов); существующие вызывающие код возврата игнорируют,
  `unmark_string_read_only` использует только как факт однократного исполнения.
- Рекурсия невозможна: таргеты не источники, их `update_extra_flags` в ветку
  каскада не заходит.
- Транзакция обеспечена всеми писателями: `edit_context` —
  `@transaction.atomic` (`source.py:30`), `perform_update` API —
  `@transaction.atomic` (`api/views.py:3995`), `bulk_perform` —
  `with transaction.atomic()` (`bulk.py:73`); ContextForm сохраняется внутри
  атомарного `edit_context`. Вопроса «проверит реализатор», как в ревизии 1,
  больше нет.

### `edit_context`: снятие `read-only` идёт через метод строки

В `weblate/trans/views/source.py:62-68` ветка снятия для `read-only`
направляется в модельный метод; **редирект на `:43-49` и гейт 404 на
` :33-35` не трогаются** (для не-глоссария редирект — мёртвый код: POST на
таргет не доходит до него):

```python
if do_add:
    flags.merge(flag)
    new_flags = flags.format()
    if new_flags != unit.extra_flags:
        unit.update_extra_flags(new_flags, request.user)
elif flag == "read-only":
    unlocked = unit.unmark_string_read_only(request.user)
    if unlocked:
        messages.success(
            request,
            ngettext(
                "Unlocked the string in one language.",
                "Unlocked the string in %(count)d languages.",
                unlocked,
            )
            % {"count": unlocked},
        )
else:
    flags.remove(flag)
    new_flags = flags.format()
    if new_flags != unit.extra_flags:
        unit.update_extra_flags(new_flags, request.user)
```

Импорт: `from django.utils.translation import gettext, ngettext`.
Шаблон не меняется: `translate.html:204-235` рендерит `flag_actions` через
`data-href="{% url 'edit_context' pk=unit.pk %}"` с
`data-params='{"removeflag":"read-only"}'` — та же ссылка, новая семантика.

### `get_flag_actions`: предлагать «Unmark as read-only» при парковке

В `weblate/trans/models/unit.py:2787-2797` (ветка `is_source`, не-глоссарий):

```python
if self.is_source:
    inherited = (
        "read-only" in translation.all_flags
        or "read-only" in component.all_flags
    )
    overrides = (
        0 if inherited or "read-only" in flags else self.count_read_only_overrides()
    )
    if "read-only" in flags or overrides:
        if overrides or not inherited:
            result.append(
                ("removeflag", "read-only", gettext("Unmark as read-only"))
            )
    else:
        result.append(("addflag", "read-only", gettext("Mark as read-only")))
```

Матрица поведения (с учётом `flags = self.all_flags`, `:2783`):

| источник `read-only` | унаследован | оверрайды | сегодня | после |
|---|---|---|---|---|
| есть | нет | любые | Unmark | Unmark (без изменений) |
| есть | да | любые | — | — (без изменений) |
| нет | да | любые | — | — (см. «Ограничения», п. 5) |
| нет | нет | есть | **Mark** (ловушка) | **Unmark** (фикс) |
| нет | нет | нет | Mark | Mark |

Жёсткое предусловие «не предлагать снятие при унаследованном флаге»
сохраняется (при `inherited` счётчик оверрайдов не вызывается, и `Unmark` не
появляется никогда — ровно как в ревизии 1 и как сегодня). Пока есть
оверрайды, «Mark as read-only» скрыт: чтобы поставить флаг всей строке,
оператор сначала снимает парковку; смешивать в меню два разных действия на
одном переключателе не нужно.

### Поведение, которое меняется

- «Unmark as read-only» на странице источника: плюс к флагу источника снимает
  пер-юнитные оверрайды всех языков и показывает `messages.success` с числом
  разблокированных языков (0 — без сообщения, как сегодня).
- Страница источника parked-строки: меню показывает «Unmark as read-only»
  вместо «Mark as read-only» (см. матрицу).
- Bulk edit и API PATCH, снимающие `read-only` **с источника, который его
  нёс**: вместе с флагом источника снимают и пер-юнитные оверрайды (каскад).
  Parked-случай (источник без флага) — не снимают (не-цель; негативные
  тесты).
- Глоссарий: «Unmark as untranslatable» на источнике глоссария теперь тоже
  снимает пер-юнитные `read-only` оверрайды таргетов, если они есть (UI их не
  ставит — API отказывает, `api/views.py:4011-4031`; мог поставить скрипт).
  Пер-языковые `exact`/`forbidden`/`not-applicable` каскад не трогает — он
  снимает **только** `read-only`.
- Снятие `read-only` с глоссарного таргета, который несёт **свой**
  `read-only` (только скриптом): раньше — с этого таргета, теперь — со всей
  строки (редирект `:43-49` при собственном флаге не срабатывает, но ветка
  `elif flag == "read-only"` в `edit_context` берёт метод строки).

### Поведение, которое не меняется

- Установка `read-only` (`do_add`) — по-прежнему source-level: флаг источника
  блокирует все языки через объединение флагов (`source_unit_save` пересчитает
  состояния таргетов, `unit.py:1068-1084`).
- `translation.check_flags` / `component.check_flags`-уровневый `read-only`:
  меню снятие не предлагает (жёсткий гейт `inherited`); снятие оверрайдов при
  унаследованном замке недостижимо из UI и закреплено как ограничение.
- `FillReadOnlyAddon`, `is_readonly()` из формата файла, перевод-уровневые
  блокировки: вне области.
- `priority`, `variant`, `terminology`, `exact`, `forbidden`,
  `not-applicable`: не трогаются.
- Языковые страницы не-источника не-глоссария: 404 на `edit_context`
  (`source.py:33-35`) остаётся; новых пунктов меню на таргетах не появляется.

## Файлы и ответственность

| Файл | Изменение |
|---|---|
| `weblate/trans/models/unit.py` | `count_read_only_overrides`, `_strip_read_only_overrides`, `unmark_string_read_only` (рядом с `update_extra_flags`); каскад-хук в конце `update_extra_flags`, сигнатура `-> int`; ветка `is_source` в `get_flag_actions` (`:2787-2797`). |
| `weblate/trans/views/source.py` | `edit_context`: ветка снятия `read-only` → `unmark_string_read_only` + `messages.success` (`ngettext`). Редирект и 404-гейт без изменений. |
| `weblate/api/views.py` | Без изменений кода: `perform_update` уже зовёт `update_extra_flags` (`:4078-4079`), каскад несёт модель. |
| `weblate/trans/bulk.py` | Без изменений кода: `bulk_perform` пишет флаги через `source_unit.update_extra_flags` (`:172`). |
| `docs/changes.rst` | Одна запись в текущей нерелизной секции (`Weblate 2026.8.1`, `:1-4`). |
| `docs/admin/translating.rst` | Одно предложение в `_additional-flags` (снятие с источника теперь снимает и пер-языковые `read-only`). |
| `weblate/trans/tests/test_views.py` | Новые кейсы в `SourceStringsTest` (`:1485`). |
| `weblate/api/tests.py` | Два кейса в `UnitAPITest` (`:12418`): положительный (каскад при снятии с помеченного источника) и негативный (parked — no-op). |
| `weblate/trans/tests/test_search.py` | Расширить `test_bulk_read_only` (`:1101-1117`): позитивный каскад + негативный parked. |
| `weblate/trans/tests/test_loc_kit_ingest_contract.py` | Без правок (только ставят `read-only`; каскад на установке не горит — см. «Ограничения», п. 6). Прогнать как регрессию. |

## Задачи

### Задача 0. Read-only проб `original_state` на проде (требует одобрения)

**Outcome.** Подтверждено, что у всех 13 запаркованных юнитов четырёх ключей
`state == 100`, `original_state` заполнен и согласован с пустым таргетом
(`original_state == 0`), собственного `read-only` в мердже нет нигде кроме
пер-юнитных `extra_flags`. Если проб найдёт юниты со `state == 100` и
`original_state == 100` (паркованные прямой записью state без `original_state`,
`squadrons.md:44-47` такого не делал, но данных с других компонентов это
касается), — разлолок вернул бы их в 100; такие юниты выгружаются списком и
решение об их судьбе принимается до реализации.

**Actions.**

- [ ] Через `./deploy/vps.sh ssh` + `docker exec hcgameloc-weblate-1 weblate
      shell` (база внутри контейнера `http://127.0.0.1:8080`), idempotent
      скрипт, который всегда выходит 0 и печатает traceback вместо assert
      (ssh_retry повторяет падающие команды — урок из памяти проекта): по
      контекстам `%RANK_PROMOTION%`, `%NEW_RANK%`, `%WEEKLY%`, `%FOR_WINS%`
      вывести для каждого юнита `language, state, original_state,
      bool(target), extra_flags`.
- [ ] Записать вывод в этот план (раздел «Результат проба»). Никаких записей
      в БД, никаких `save()`.

**Acceptance.** Список юнитов с аномальным `original_state` пуст либо его
судьба задокументирована в этом плане до старта задачи 1.

### Задача 1. Модель: методы, каскад, меню

**Outcome.** `unmark_string_read_only(user)` на любом юните строки снимает
`read-only` с собственных флагов источника (если есть) и с каждого таргета,
несущего собственный `read-only`, восстанавливает `original_state`, гоняет
`run_checks`, инвалидирует кэш компонента и возвращает число разблокированных
таргетов (без источника). `update_extra_flags` при переходе
`read-only → нет` на источнике автоматически снимает оверрайды (тот же
исполнитель, один проход). Не-`read-only` флаги не трогаются; юниты без
оверрайда не пишутся и не получают `Change`; `old_unit` инициализирован в
`__init__` (`unit.py:811-813`).

**Files and interfaces.**

- `weblate/trans/models/unit.py`:
  - `update_extra_flags` (`:2930-2956`): каскад в конце, `-> int`.
  - `get_flag_actions` (`:2787-2797`): ветка `is_source` по матрице выше.
  - новые методы: `count_read_only_overrides`, `_strip_read_only_overrides`,
    `unmark_string_read_only`.
- Контракт: `unmark_string_read_only` требует транзакцию
  (`verify_in_transaction`), принимает любой юнит строки, возвращает `int`
  (таргеты, без источника). `_strip_read_only_overrides` — приватный, только
  на источнике, тот же контракт по транзакции.

**Actions.**

- [ ] `count_read_only_overrides` (запрос + `Flags`-парсинг).
- [ ] `_strip_read_only_overrides(user)`: `select_for_update().exclude(pk)`,
      шардинг `unit.source_unit = self` и `unit.translation.component`,
      для каждого с оверрайдом → `update_extra_flags` → `update_state` →
      `run_checks`; в конце `invalidate_cache` при `changed and not
      is_batch_update`; вернуть счётчик.
- [ ] `unmark_string_read_only(user)`: `verify_in_transaction`; локальная
      `source`; **сначала** `count = count_read_only_overrides()`; далее ровно
      одна ветка — `update_extra_flags` (каскад) или `_strip` напрямую (parked);
      вернуть `count`.
- [ ] Каскад в `update_extra_flags`: после цикла `generate_change`, условие
      `is_source and "read-only" in Flags(old) and "read-only" not in
      Flags(extra_flags)`; docstring фиксирует новый побочный эффект и `-> int`.
- [ ] `get_flag_actions` по матрице; `overrides` не считается при `inherited`.

**Verification.**

- Новые кейсы (ORM-уровень, `weblate/trans/tests/test_views.py::SourceStringsTest`,
  все внутри `with transaction.atomic()`):
  - `test_unmark_read_only_parked_override`: parked-состояние собирается явно
    (`Unit.objects.filter(pk=cs.pk).update(extra_flags="read-only",
    state=STATE_READONLY, original_state=STATE_EMPTY)`; ре-фетч экземпляра —
    `.update()` in-memory не обновляет), у источника флага нет;
    `source.unmark_string_read_only(user)` → вернул 1; у cs:
    `extra_flags == ""`, `state == STATE_EMPTY`, `"read-only" not in
    all_flags`, есть `Change` `EXTRA_FLAGS` на cs; en/ru-подобные таргеты без
    оверрайда не получили ни записи, ни `Change`.
  - `test_unmark_read_only_preserves_other_flags`: cs с
    `extra_flags="read-only ignore-max-length"` → после снятия
    `ignore-max-length` цел, `read-only` нет.
  - `test_unmark_read_only_source_flag_and_overrides`: у источника
    `read-only` + оверрайд на cs → метод вернул **предвычисленный** счётчик
    (1), оверрайд снят (регрессия против двойного прохода с нулевым
    счётчиком).
  - `test_unmark_read_only_no_overrides`: источник с флагом, оверрайдов нет →
    0, флаг источника снят, повторный вызов идемпотентен (0, без записей).
  - `test_get_flag_actions_parked_menu`: на parked-источнике меню отдаёт
    `("removeflag", "read-only", …)` и не отдаёт `addflag`; при
    `component.check_flags="read-only"` оверрайдов меню не отдаёт ничего.
- Существующие кейсы остаются зелёными: `SourceStringsTest::test_edit_readonly`
  (`:1501`), `test_toggle_flags` (`:1649`), `test_edit_check_flags` (`:1562`)
  — оверрайдов не используют; каскад без оверрайдов no-op.
  `test_loc_kit_ingest_contract.py` — кейсы только **ставят** `read-only`
  (установка каскад не зажигает).
- Команды: `./rundev.sh test weblate/trans/tests/test_views.py -k SourceStringsTest`,
  `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py`.

### Задача 2. View: `edit_context` направляет снятие на всю строку

**Outcome.** `POST /edit_context/{source_pk}/` с `removeflag=read-only` снимает
флаг со всей строки (источник + пер-юнитные оверрайды) независимо от того, был
ли флаг на источнике; при разблокированных таргетах показывается
`messages.success` с числом языков (1 — единственная форма). Установка
`addflag=read-only` и снятие прочих флагов — без изменений. POST на не-источник
не-глоссария по-прежнему 404.

**Files and interfaces.**

- `weblate/trans/views/source.py:62-68` (`edit_context`), импорт `ngettext`.
- Шаблон не меняется (`translate.html:204-235` — те же `link-post` ссылки).

**Actions.**

- [ ] Ветка `elif flag == "read-only":` в снятии → `unmark_string_read_only` +
      условный `messages.success` (`ngettext`).
- [ ] `do_add` и generic-снятие других флагов — как есть.

**Verification.**

- `test_unmark_read_only_cascade_ui` (`SourceStringsTest`): parked cs (сборка
  как в задаче 1, через явный `.update(extra_flags, state, original_state)`);
  `POST edit_context pk=source.pk {"removeflag": "read-only"}`, `follow=True`
  → redirect на страницу источника; cs: `state == STATE_EMPTY`,
  `"read-only" not in all_flags/extra_flags`; таргет без оверрайда не тронут;
  в страницах содержит «Unlocked the string in one language.»
  (в `captureOnCommitCallbacks(execute=True)`, как `test_search.py:1121`, —
  чтобы on_commit-инвалидация отработала).
- `test_edit_readonly_context_form_cascade`: у источника
  `extra_flags="read-only"` (ORM), у cs оверрайд (ORM);
  `POST edit_context pk=source.pk {"extra_flags": ""}` (ветка `ContextForm`,
  `update_extra_flags(save=False)`) → оба сняты, cs восстановлен —
  регрессия на порядок записи при `save=False` (шардинг in-memory источника).
- `test_unmark_read_only_no_message`: без оверрайдов — 200/redirect,
  сообщения нет, поведение как сегодня.
- `test_edit_readonly`, `test_toggle_flags` остаются зелёными.
- Команды: `./rundev.sh test weblate/trans/tests/test_views.py -k SourceStringsTest`.

### Задача 3. API: PATCH флагов источника каскадит в сценарии перехода

**Outcome.** `PATCH /api/units/{source_pk}/` с `{"extra_flags": ""}` на
источнике, **несущем** `read-only`, снимает и пер-юнитные оверрайды (каскад в
`update_extra_flags`). `PATCH` на флаколесс-источнике (parked-случай) —
no-op: оверрайды остаются (закреплённое ограничение, не регрессия). На
не-исходном юните `extra_flags` с `read-only` по-прежнему запрещён
(`api/views.py:4011-4031`).

**Files and interfaces.**

- Код не меняется: `perform_update` (`weblate/api/views.py:3995-4087`) зовёт
  `unit.update_extra_flags(data["extra_flags"], user)` (`:4078-4079`).
- Тесты: `weblate/api/tests.py::UnitAPITest` (`:12418`).

**Actions.**

- [ ] `test_patch_source_extra_flags_cascades_read_only`: источник с
      `read-only`, cs с пер-юнитным оверрайдом (ORM-сборка parked-поля state,
      ре-фетч) → PATCH источника `{"extra_flags": ""}` → у cs пусто и
      `state != STATE_READONLY`.
- [ ] `test_patch_flagless_source_keeps_parked_override`: источник без флага,
      cs с оверрайдом → PATCH источника `{"extra_flags": ""}` → 200, у cs
      оверрайд **цел** (документирует ранний возврат `unit.py:2936`).

**Verification.**

- `./rundev.sh test weblate/api/tests.py::UnitAPITest -k "extra_flags or read_only"`.
- Существующие глоссарные кейсы флагов (`test_*_glossary_*_flags`) зелёные:
  `exact` — языковой флаг, каскад его не трогает.

### Задача 4. Bulk edit: снятие с помеченного источника каскадит

**Outcome.** Bulk `remove_flags=read-only` на фильтре, накрывающем источник,
**несущий** `read-only`, снимает и пер-юнитные оверрайды. Parked-случай
(источник без флага) bulk edit не разлочивает: `bulk.py:170` не зовёт
`update_extra_flags`, когда `new_flags == extra_flags` источника — это
причина, а не «источник не попал в фильтр» (при `state=-1` идентификаторы
источников собираются и из read-only таргетов, `bulk.py:78-85`). Массовый
unpark остаётся скриптом (`squadrons.md:77-93`).

**Files and interfaces.**

- Код не меняется: `bulk_perform` (`weblate/trans/bulk.py:163-173`) пишет
  флаги через `source_unit.update_extra_flags` (там же `is_batch_update=True`,
  каскад наследует батч-режим и не дёргает `invalidate_cache`).
- Тесты: `weblate/trans/tests/test_search.py::test_bulk_read_only`
  (`:1101-1117`).

**Actions.**

- [ ] Расширить `test_bulk_read_only`: после существующего add→remove цикла —
      сценарий «источник с `read-only` + оверрайд на cs» (ORM-сборка) →
      `remove_flags=read-only` → оверрайд cs снят, state восстановлен.
- [ ] Негативный кейс: parked (источник без флага, оверрайд на cs) →
      `remove_flags=read-only` с `q`, накрывающим cs → оверрайд **остался**;
      в комментарии теста — почему (гейт `bulk.py:170`, см. «Ограничения», п. 1).
- [ ] Убедиться, что счётчик «N strings were updated» не меняется из-за
      каскада (он считает единицы bulk-цикла, `_strip` на него не влияет).

**Verification.**

- `./rundev.sh test weblate/trans/tests/test_search.py -k bulk_read_only`.

### Задача 5. Документация

**Outcome.** Changelog и строчка в справке о флагах честно описывают новое
поведение и его границы.

**Files and interfaces.**

- `docs/changes.rst` — текущая нерелизная секция (`Weblate 2026.8.1`, `:1-4`).
- `docs/admin/translating.rst` — `_additional-flags` (`:81`).
- `docs/product/guides/producer-guide-weblate.md` — опционально (раздела про
  read-only/парковку там нет; не создавать ради одного абзаца).

**Actions.**

- [ ] Запись в changelog: «Unmarking a string as read-only on the source
      string page now also removes per-language read-only overrides and
      restores the translations in every language at once; bulk edit and the
      API do the same when the source string itself carried the flag» (одна
      строка, без «without a script» — массовый снятие остаётся скриптом).
- [ ] Предложение в `docs/admin/translating.rst` про кросс-языковое снятие.
- [ ] Здесь же, в плане: «Результат проба» (задача 0).

**Verification.**

- `uv run prek run --all-files` (включает `codespell`/`typos` по docs).

## Ограничения и рискованные границы

1. **Parked-случай разблокируется только построчно, с источника.** Ни bulk,
   ни API его не снимают (нет перехода флага источника — см. задачи 3/4);
   массовый unpark 4 337 юнитов Space Arena — скрипт `squadrons.md:77-93`
   (или построчное «Unmark» после этого плана, если ключей мало). Для
   четырёх ключей из баг-репорта — ровно 4 клика.
2. **Восстановление `original_state`.** Парковка ставилась не через
   `update_state`, а `bulk_update(["extra_flags", "original_state", "state"])`
   с `original_state = state (0)` (`squadrons.md:44-47`); инвариант
   «оригинал сохранён» от этого не сломан, но проверяется прогоном задачи 0
   до реализации. Если проб найдёт юниты с `original_state == 100` (прямая
   запись state), `update_state` вернул бы их в 100 — такие выносятся в
   отдельное решение.
3. **Статистика после разлока падает туда, где её завышала парковка.**
   Запаркованные пустые таргеты считались «готовыми» (state 100 ≥
   TRANSLATED, `squadrons.md:124-126`); снятие возвращает их в
   непереведённые. Для ГД это ожидаемо (строки снова видны к переводу), но
   числа «переведено» в дашборде уменьшатся — сказать дизайнеру до первого
   клика.
4. **Откат не сохранят форму.** Обратный «Mark as read-only» лочит **все**
   языки строки, включая `en`/`ru`, которые парковкой не трогали; частичная
   перепарковка прежнего подмножества — только скрипт.
5. **Оверрайд под унаследованным замком.** При `read-only` на уровне
   перевода/компонента меню не показывает ни Mark, ни Unmark (жёсткий гейт
   `inherited` — как сегодня), поэтому пер-юнитный оверрайд в такой конфигурации
   из UI недостижим. К прод-кейсу не относится: у `en`/`ru` state 20 без
   флага, значит translation/component-level `read-only` нет. Документировано,
   не чинится.
6. **Тесты loc-kit.** `test_loc_kit_ingest_contract.py` (`:2225-2240`,
   `:3446-3466`) только ставят `read-only` через `update_extra_flags` на
   источнике; условие каскада (`"read-only" in Flags(old)`) на установке
   ложен. Правок не ждут; если реализатор найдёт кейс, снимающий флаг с
   источника и ожидающий сохранения оверрайда, — правит ассерт в том же PR и
   объясняет почему (в ревизии 1 такой кейс был найден grep'ом: его нет).
7. **История изменений.** При снятии с помеченного источника с оверрайдами
   каждый разблокированный таргет получает два `EXTRA_FLAGS` Change: зеркальный
   цикл самого `update_extra_flags` источника (`unit.py:2940-2956`, пишет на
   все таргеты — существующее поведение любого source-level изменения флагов)
   и собственный `update_extra_flags` таргета в `_strip`. Принято как шум
   аудита; менять зеркальный цикл — вне скоупа.
8. **Производительность.** Снятие с помеченного источника = три прохода по
   `unit_set`: зеркальный цикл `Change` внутри `update_extra_flags`
   (`unit.py:2940-2941`), пересчёт `source_unit_save` (`:1077-1082`) и `_strip`
   с `update_state` + `run_checks` на каждый оверрайд-таргет; для 15 языков —
   пренебрежимо. При parked-сборе (`_strip` без записи источника) — один проход.
   `count_read_only_overrides` — один запрос на рендер страницы источника,
   только когда у источника нет собственного флага и флаг не унаследован
   (меню рендерится на странице единицы, не в списках).
9. **Права.** Гейт один — `meta:unit.flag` на перевод исходного юнита
   (`source.py:39`, `permissions.py:1304-1314`); действие теперь мутирует все
   языки строки. Асимметрия «флаг-установка-на-всю-строку vs
   проверка-на-источнике» существовала и до плана (Mark блокирует все языки);
   скоуп «со страницы источника» (не-цель 2) не добавляет новых переносов
   проверки. Ослабления под per-language роли не вводятся.

## Интеграция и владение документами

- `docs/changes.rst`: одна запись в текущей нерелизной секции (задача 5).
- `docs/admin/translating.rst` (`_additional-flags`, `:81`): одно предложение
  о кросс-языковом снятии (задача 5).
- `docs/operations/plans/2026-08-19-space-arena-lockit-producer-view-squadrons.md`:
  без правок; раздел «Откат» (`:77-93`) остаётся операционной инструкцией для
  массового unpark, на который ссылается «Ограничения», п. 1.
- `docs/product/guides/producer-guide-weblate.md`: опционально, только если
  появится раздел про парковку.

## Коммиты

Следовать Conventional Commits (`AGENTS.md`). Одна задача — один коммит:
`chore(prod): probe original_state of parked space arena strings` (задача 0,
если вывод фиксируется только в плане — без коммита кода),
`feat(trans): cascade read-only removal across languages` (задача 1),
`feat(trans): unlock string-wide read-only from the source tools menu`
(задача 2), `test(api): pin read-only cascade and parked no-op` (задача 3),
`test(trans): pin bulk read-only cascade and parked no-op` (задача 4),
`docs: describe symmetric read-only unmark` (задача 5). Не деплоить без
одобрения; миграций нет.
