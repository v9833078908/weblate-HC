<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Массовая нормализация пунктуации и механических ошибок

## Цель, решения и статус

**Дата:** 2026-09-09. **Статус:** реализовано, смерджено в `main` (`4124884`) и **развёрнуто на проде** `l10n.herocraft.com` по явному одобрению пользователя: `DEPLOY-OK`, ревизия образа совпадает с checkout, миграция `0123` применена, все семь механических групп доступны. Политика на проде **не применялась** — записи переводов не было.

Приёмка: 525 passed / 102 skipped / 31 subtests, 0 failures на целевых наборах (`test_fix_check.py`, `test_fix_check_view.py`, `test_fix_check_task.py`, `test_mass_fixup.py`, `test_chars_checks.py`); scoped `prek run --files` зелёный; `sphinx-build` без ошибок ссылок.

Критерий покрытия проверен на **Anvil Saga вместо CoL4**: сохранённой локальной выборки CoL4/fr в репозитории нет, а пользователь в сессии 2026-09-09 прямо перенаправил проверку на прод-проект `anvil-saga`. После деплоя проекция переснята **развёрнутым кодом на живых данных** и совпала с локальной до единицы. Результат: 92,5 % пула политики (3 714 из 4 015), сверка пула сходится без остатка. Замер и его границы — `docs/operations/measurements/2026-09-09-anvil-saga-bulk-repair-projection.md`. Критерий про выборку `CoL4/data/fr` (2 092 / 7) этим замером **заменён, а не выполнен**.

Разбивка `manual` по причинам снята на проде: крупнейшая причина — эвристика короткого слова (112 английских строк), остальные 189 отказов содержательные.

Heartbeat на длительном прогоне проверен: `progress_callback` продлевает аренду примерно раз в 2 с против TTL 3600 с, таймаутов задачи не задано, падение воркера приводит к повторной постановке. Полный прогон компонента — около 6 минут, а не 16 минут на язык (прежняя оценка считалась от всех строк вместо проваленных).

Не сделано осознанно: применение политики на проде — это решение продюсера в UI; живая приёмка механических групп (в корпусе Anvil для них почти нет целей — см. «границы» в замере).

Пострелизные правки того же дня, развёрнуты на проде двумя деплоями
(`DEPLOY-OK`, ревизии образа `c636ad4` и `5730169`, health `healthy`):

- `ab06bf2` — на странице исходного языка и в строке исходного языка списка
  проверок кнопка :guilabel:`Исправить` больше не рисуется: `unit.bulk_edit`
  отказывает на источнике без intermediate раньше любой роли и до обхода для
  суперпользователя, поэтому ссылка вела в гарантированный 403. Исходный чек
  правится с области компонента или проекта.
- `c636ad4` — кнопка построчного действия считает отмеченные строки, а не
  длину страницы (раньше страница писала «Исправить выбранные (250)» при нуле
  отмеченных), а действие на всю область прямо сообщает, что чекбоксы на его
  охват не влияют.
- `5730169` — эвристика сокращения считала конечное слово классами
  `[A-Za-z]` и `[А-Яа-яЁё]`, поэтому «pièce» мерилась как «ce», а
  «Propriétés» как «s», и язык с диакритикой отказывался целиком. На
  dev-копии `col4/data/fr` это 189 из 386 удержанных строк — экран политики
  оставался пустым при 375 активных `end_stop`. Классы расширены до
  латинских и кириллических диапазонов целиком; на прод-данных `col4` и
  `anvil-saga` правка не меняет ни одной строки (замер: 3 914 кандидатов,
  из них 0 разблокировано), потому что там нет накопленных французских
  ошибок. Заодно экран `terminal-source` перестал объяснять удержанные
  строки направлением исправления: причина — собственные guards политики.
- `25cf73b` — в `AGENTS.md` зафиксирована ловушка dev-стенда: Granian
  перезагружается, воркеры Celery нет, поэтому изменение `tasks.py`/
  `fix_check.py` даёт задачу, которая возвращает «эта проверка больше не
  подходит для массового исправления» на живом экране политики.
- `41df1b7` — «Исправить» рядом со счётчиком одной проверки вело на
  объединение всех четырёх terminal-проверок: продюсер нажимал «48 строк»
  (`end_stop`, `col4/data/tr`) и видел экран на 85. Теперь каждая проверка
  ведёт на тот же движок append/replace/remove, суженный до её собственных
  строк (`terminal-source-end-stop` и т.д.) — итог экрана равен нажатому
  числу; объединённый `terminal-source` остаётся доступен по ссылке с этого
  экрана, и вся семья делит один замок на область.

Первое живое применение политики на проде продюсером не выполнялось; проверен
прогон на dev-копии `col4/data/fr`: 1 721 строка исправлена, 386 ушли в
ручной разбор, `end_stop` 2 092 → 375, после `5730169` из этого остатка
189 снова стали применимыми (все — удаление лишней точки).

Цель — существенно увеличить число ошибок Anvil Saga и подобных игровых проектов, которые продюсер исправляет массово, без повторного машинного перевода и обязательного просмотра каждой строки. Улучшение кнопки при прежнем наборе алгоритмов недостаточно. Предыдущий вариант этого документа давал только 70 дополнительных срабатываний; он заменён этим планом, а не остаётся параллельным объёмом работ.

**Согласовано пользователем:** конечный знак исходника может быть эталоном для явно подтверждённой продюсером нормализации перевода. Например, source `!`, китайский target `文本。` → `文本！`. Разрешены групповые действия без обязательного построчного просмотра; неоднозначные случаи исключаются. Это редакционная политика, не доказательство сохранения интонации или правильности перевода. Применение политики к конкретному набору требует подтверждения в UI.

**Архитектурное ограничение пользователя:** дорабатываем существующие `fix_check`, `fix_failing_checks`, lock, preview и task poller. Не добавляем модели, migrations, Celery tasks, историю запусков, cleanup/recovery подсистему или новый REST endpoint. Не расширяем глобальные save-time autofixes до новой редакционной политики.

Основания: CoL4/data/fr, read-only Anvil Saga, исследования двух `scout` на **openai-codex/gpt-5.6-luna**. Действующие документы: `docs/admin/checks.rst`, раздел `mass-fix-failing-checks`; `docs/product/guides/producer-guide-weblate.md`; исторический план `docs/product/plans/2026-08-25-mass-fix-failing-checks.md`. Правила разработки и согласования — `AGENTS.md`.

## Измерение покрытия, а не обещание исправить всё

### Anvil Saga: новая проекция

Production component: `anvil-saga/locale_v1-02-import-explained-3`, по 9 482 строки на язык. Проверенный image revision: `8d5e7eca71e7c4cada9994ffd7b3ae0802f0c856`; image digest: `sha256:4445c7c3243296db15e4a15dcfa9a5459d75f790a5f582247e10c81f6e8bb4ba`. В этом образе нет `weblate.trans.fix_check`; `removed-final-stop` активен. Сравнение с механизмом main — не сравнение с доступной production-кнопкой.

Метод: `./deploy/vps.sh ssh` → отдельный `docker exec` Python с `PYTHONDONTWRITEBYTECODE=1` и `PGOPTIONS=-c default_transaction_read_only=on`. PostgreSQL во всех пяти переводах подтвердил `on`. Текущие `Check(dismissed=False)` сгруппированы по unit; локальные `chars.py` и чистые функции проекции исполнялись в отдельном модуле в памяти, с production-моделями и активными autofixes. `save`, `translate`, `run_checks`, Celery, backfill и deployment не вызывались; файлы production не заменялись.

Проверены простые незавёрнутые одиночные окончания: добавление/замена `.`/`!`/`?`/`:`, удаление лишних `.`/`!`, включая fullwidth варианты. Исключены короткий source ≤4, пустые формы, plurals, хвостовые пробелы, переносы/`$`, закрывающие кавычки/скобки/теги, многоточия, сочетания знаков, технические окончания, числовая точка и возможное сокращение. Кандидат должен совпасть с полным `fix_target` и снять существующее срабатывание без новых terminal/spacing/ellipsis/semicolon ошибок. Для корейского повторная проекция использовала ASCII вместо ошибочного общего CJK-rendering; количества не изменились.

| Перевод | Активных срабатываний всех проверок | Авто до, main | Проекция terminal: строк | Проекция terminal: снятых срабатываний |
| --- | ---: | ---: | ---: | ---: |
| en | 1 320 | 2 | 408 | 710 |
| ja | 1 481 | 0 | 671 | 1 171 |
| ko | 1 339 | 2 | 637 | 1 095 |
| zh_Hant | 1 742 | 0 | 869 | 1 462 |
| zh_Hans | 1 744 | 1 | 869 | 1 462 |
| **Итого** | **7 626** | **5** | **3 454** | **5 900** |

Новая terminal-проекция: **77,37% активных срабатываний** и изменение **3 454 из 4 644 строк с ошибками — 74,38%**. Это не 3 454 полностью исправленных перевода: другие проверки могут остаться. Замена одного знака нередко снимает сразу две проверки, поэтому строки и срабатывания нельзя смешивать.

| Операция terminal | en | ja | ko | zh_Hant | zh_Hans |
| --- | ---: | ---: | ---: | ---: | ---: |
| Добавление | 92 | 115 | 132 | 262 | 262 |
| Замена | 302 | 501 | 458 | 594 | 594 |
| Удаление | 14 | 55 | 47 | 13 | 13 |

Предыдущая узкая доработка давала 75/7 626 = 0,98% автоисправимых срабатываний, включая существующие пять; старые review-добавления давали ещё 902 предложения с обязательным просмотром. Эти старые числа нельзя прибавлять к 5 900: новая проекция включает пересекающиеся строки и меняет политику допуска.

Отдельная проекция механических правил дала 29 допустимых пар `unit/check`: en 6, ja 8, ko 6, zh_Hant 4, zh_Hans 5. Из них 25 — краевые ASCII-пробелы, четыре — double space. Не складываем их уникальные строки с terminal без проверки пересечений. У zh_Hans один double-space кандидат исключён из-за дополнительных изменений полного autofix-прохода. 47 `end_ellipsis` срабатываний не получили предложения существующего `end-ellipsis` provider; одно наличие provider не означает исправимость.

**Ограничения замера:** нет проверки прав конкретного продюсера, полного будущего span/format guard, конкурентной записи и plural-сценариев. Это инженерная проекция алгоритмического покрытия, не production-применение и не нижняя гарантированная граница. При реализации повторить на полном candidate pipeline и объяснить каждый спад относительно этой таблицы; не ослаблять guards ради процента.

Примеры:

- zh_Hans, unit 488448: source заканчивается `читать!`, target `这个书架空空如也，购买一些书籍供工人阅读。` → тот же target с `！`.
- en, unit 460554: `Не получается.` / `I can't!` → `I can't.`. Редакционная нормализация осознанно меняет интонационный знак.
- ko: `Общие настройки.` / `게임 설정` → `게임 설정.`, **не** `게임 설정。`.
- en, unit 460578: source о надёжном заказчике, target `The young lady next to him is his daughter, Olivia.`. Пунктуация нормализуется, но смысловое расхождение остаётся. Не объявлять это исправлением перевода или ошибкой импорта без отдельного расследования; не скрывать прочие ошибки и не повышать state.

### CoL4 и переносимость правил

Сохранённая локальная REST-выборка CoL4/data/fr: 2 092 `end_stop`, семь `end_exclamation`. Все 2 092 targets заканчиваются ASCII-точкой при отсутствии её в source; три — `...`. Применение строгих лексических guards новой проекции к этой выборке оставило **1 915 точек и шесть восклицаний**; исключены 149 возможных сокращений, 19 завёрнутых source/target хвостов, шесть числовых точек, три многоточия и один quoted-source случай для восклицания. Это локальная классификация без полного save/permission pipeline; французский пробел перед удаляемым знаком тоже должен войти в preview.

Два корпуса подтверждают разные основные сценарии: удаление в CoL4, замена в Anvil Saga. Правила не зависят от slug проекта, unit ID или текста конкретной игры. Не вводить corpus-specific исключения. Русские source-проверки Anvil (`ellipsis` 711 и `multiple_failures` 1 297) не включены в новый прирост; новый режим source не меняет.

## Выбранный объём правил

### Terminal: явная редакционная нормализация

Одна политика `terminal-source`, версия в snapshot. Внутри неё операции append/replace/remove, показываемые отдельными подытогами; одно подтверждение может применить весь подходящий набор этой политики. Это не повышение всех terminal checks из `review` в `safe`.

| Source / target | Действие |
| --- | --- |
| Source заканчивается одним `.`, `!`, `?`, `:` соответствующей поддержанной семьи; target без знака | Добавить языковой знак, если target непуст и конец не защищён |
| Source с одним явным знаком; target с другим одиночным знаком | Заменить последний знак, не дописать второй: `。` → `！`, `!` → `.`, `.` → `?` и т. п. |
| Source без конечного знака; target с одиночным `.`/`!` либо `。`/`！` | Удалить знак и только относящийся к нему французский пробел; непустое содержимое сохранить |
| Source без знака; target `?`/`:` | Не удалять: есть измеренные грамматические вопросы и прямая речь без source-знака |
| Уже допустимый вариант той же семьи, нет соответствующей активной ошибки | Не канонизировать ради стиля, не менять строку |
| `...`, `…`, `!!`, `?!`, `!?`, `⁈`/`⁉`, fullwidth сочетания, source `;` | Не включать в эту политику; существующее interrobang review остаётся отдельным |

Точная source-family берётся из source, не из имени проверяемой ошибки. В частности, CJK source `:` не превращается в точку только потому, что `EndStopCheck` и `EndColonCheck` принимают пересекающиеся наборы. Для явного source `:` допустимо предложить только colon-family `：`, если есть соответствующая активная ошибка; уже допустимое target `。` при отсутствии ошибки не меняется. Source `;` остаётся вне новой политики. При неоднозначности locale-предикатов — отказ, не fallback к ASCII.

Языковой renderer — существующий `_terminal_mark` и source-предикаты из `weblate/checks/chars.py`, без второй таблицы в engine. Исправить renderer: fullwidth только `language.is_base({'ja', 'zh'})`, корейский — ASCII. `Language.is_cjk()` глобально не менять; checker acceptance совместимых вариантов сохраняется. Французский: NBSP перед `:`, NNBSP перед `?`/`!`, кроме `fr_CA`; Greek/Arabic question rendering и остальные существующие языковые особенности не теряются. Сложные неподтверждённые семейства не угадывать; skipped checks (`th`, `jbo` и другие ветви) не обходить.

**Строгие исключения:** пустой/whitespace-only source или target; source ≤4 для общего нового terminal-режима; любое многоточие/повторный terminal run; конечная цифра с точкой; URL/email/path/version-like окончание; возможное сокращение перед точкой; source/target с конечной кавычкой, закрывающей скобкой, тегом, whitespace; переносы и игровой `$`. Начальная измеренная эвристика сокращений — конечное слово длиной 1–3 латинские буквы перед точкой, дополнительно кириллица в source. Это консервативный фильтр, не лингвистический детектор: распространённые короткие слова тоже могут быть исключены.

Защищённые markup/placeholder/syntax spans проверяются существующим `highlight_string` и форматными парсерами; edit span не может пересечь их или стать частью незакрытого токена. Внутренняя часть target, числа, placeholder sequence, engine identifiers, markup tokens и порядок не меняются. Для plain-terminal это локальная замена суффикса, не глобальный regex по строке.

**Почему без общего tail parser:** нынешние checks сравнивают raw endpoints через `BaseCheck.check_chars`; разбор знака перед кавычкой/тегом требует согласованного изменения семантики проверок. В custom autofix уже зафиксировано ухудшение 13 из 17 дополнительно захваченных en_US quoted-строк. Для измеренных 77,37% этот риск не нужен. Завёрнутые хвосты показаны как исключения, а не обещаны в реализации; не добавлять parser/check rewrite в этот план незаметно.

### Остальные механические правила

Закрытый список операций, а не «запустить все autofixes». Конкретные providers доступны только через активный `AUTOFIXES`; отсутствие optional customization — понятное исключение, не импорт custom package из core.

| Группа | Реализация и разрешённое изменение | Граница |
| --- | --- | --- |
| Двойные пробелы | `DoubleSpaceCheck.get_fixup`: ASCII runs → один пробел | Source не содержит двойных пробелов; не трогать protected spans |
| Краевые ASCII-пробелы | `BeginSpaceCheck` / `EndSpaceCheck.get_fixup`, сверка с `SameBookendingWhitespace` save parity | Только указанный край, не tabs/newlines/внутреннее содержимое; пустой target неизменен |
| Пробелы вокруг `$` | `line-separator-spacing` / `LineSeparatorSpacing` | Только tight-source separator; количество и порядок `$` неизменны, ignore flag сохранён |
| Французские пробелы перед пунктуацией | `punctuation-spacing` и при наличии `french-punctuation-spacing` | Только whitespace в разрешённых позициях, `fr_CA`/ignore/highlight guards; ни один знак не заменяется |
| Форма конечного многоточия | `end-ellipsis` / `ReplaceTrailingDotsWithEllipsis` | Только source `…` и target `...`; не добавлять отсутствующее многоточие и не удалять существующее |
| Лишний zero-width space | `zero-width-space` / `RemoveZeroSpace` | Source без U+200B, не Khmer, не внутри protected token |

Краевые пробелы не объявлять безусловным улучшением: в Anvil есть source с вероятно случайным конечным пробелом, и стандартный autofix копирует его в target. UI раздельно показывает **«Удалить лишние краевые пробелы»** и **«Воспроизвести краевые пробелы исходника»**, с видимыми символами. Второе — отдельная явно подтверждаемая групповая политика, не скрытая часть terminal/double-space. Source исправляется отдельно, не этим действием.

Разделение краевых политик — eligibility перед тем же `get_fixup`, не новый provider: remove-only допускает только source с нулём ASCII-пробелов на выбранном краю и target с ненулём; source-edge — ненулевое исходное число пробелов и отличающееся target-число. Это два непересекающихся набора. Пробелы на другом краю и любые tabs/newlines остаются вне выбранной операции; если общий save-проход меняет их, срабатывает `normalization_conflict`.

Глобальный `DoubleSpaceCheck.get_fixup` сам по себе не защищает spans. До допуска любого механического кандидата определить изменяемые участки и сравнить защищённое содержимое до/после. Если предложенный regex/provider меняет хотя бы один protected span, отклонить unit целиком как `protected_span`; не считать очистку check доказательством сохранности placeholder/markup. То же правило применяется к edge-space, ellipsis и U+200B; нового masking/rewrite framework не нужно.

Нормализацию French wrong/missing spacing можно объединить в одну явно описанную группу только при наличии обоих allowlisted providers; их IDs, порядок и версия операции входят в контракт. Preview включает весь её разрешённый whitespace diff. Если custom provider отсутствует, combined группа недоступна; core-only исправление уже существующих неправильных пробелов может иметь собственную честно названную группу, без обещания вставить недостающие. Остальные providers не присоединяются автоматически. Если ошибка `$` содержит одновременно потерянный разделитель, spacing-проекция, не снимающая выбранную проверку, не применяется.

**Не исправляем механически:** `game-number`, `game-token`, `game-markup`, `game-length`, `cyrillic-leak`, `duplicate`, `reused`, `multiple_capital`, missing format placeholders/URLs, `newline-count`/`escaped_newline`, glossary/judge/смысл. Не вставлять числа/теги наугад, не удалять повторы, не обрезать текст по лимиту, не транслитерировать утечки. `BleachHTML` не средство чинить игровую разметку. Control/BOM не получают фиктивную UI-группу без поддержанного диагностического контракта. Новые source edits, LLM/rejudge и массовая отмена вне объёма.

## Один candidate pipeline и существующая задача

### Контракт предложения

В `weblate/trans/fix_check.py` расширить существующую классификацию, не создавать отдельный repair framework. Внутренний контракт предложения: `rule_id`, `rule_version`, `policy`, operation, полные before/after targets, множество очищаемых checks и reason при отказе. Это новые поля/контракт, не утверждение о существующих symbols. Политики: существующие safe, явно подтверждённые группы, existing shown-only review. Одна строка не учитывается дважды из-за `end_stop` + `end_exclamation`.

Порядок:

1. Scope/actor/flags/active non-dismissed check, target-only условия и доступность конкретной операции.
2. Чистая проекция только выбранной операции; plurals/multivalue по существующему отображению форм (`AutoFix.fix_target`), не одна source-форма для всех targets. Неизменные формы сохраняются; неоднозначная требующая правки форма исключает unit целиком. Пустую форму не превращать в знак. Неизвестное отображение — отказ.
3. Полная подготовка, как в `_compute_final_target` и `Unit.translate`: plurals/multivalue, active autofixes, DOS endings. Final target обязан совпасть с разрешённым projected target; лишние изменения — `normalization_conflict`, не молчаливое расширение согласия. Не отключать стандартный autofix-проход.
4. Выбранная проверка очищается. Для terminal проверяется весь соответствующий набор, включая `end_interrobang`, `end_ellipsis`, `end_semicolon`, `punctuation_spacing`; новые failures запрещены. Эти дополнительные checks — veto/after invariants, не разрешение генерировать операции для `;`, interrobang или многоточия. Relevant terminal mismatches не заменяются другим несовпадением. Для остальных групп — собственная проверка плюс затрагиваемые pure checks и структурные инварианты. Не вызывать платные/batch/LLM проверки ради preview.
5. Digest полного предложения и условий, затем snapshot. Preview и apply используют одну функцию; исключения: `unsupported`, `ambiguous_tail`, `protected_span`, `normalization_conflict`, `provider_unavailable`, `stale`, `denied` с переводимыми пояснениями.

Не менять все `get_fixup`/`mass_fixup` на permissive. Старый editor append остаётся review; explicit-policy bulk не должен появиться при обычном сохранении или crafted review POST. Для простого ASCII removal adapter может вызвать активный `removed-final-stop` только внутри подтверждаемой операции, если результат проходит общий контракт. Удаление fullwidth `。`/`！` — **новая explicit-policy операция engine**, не способность существующего provider. Глобальный `RemoveAddedFinalStop` остаётся ASCII-only, состав и порядок `AUTOFIXES` не меняются. Removal diff содержит только знак и относящийся к нему French spacing. Не создавать два расходящихся массива языковых правил.

### Снимок и подтверждение

Сохраняем короткоживущий общий Django cache, без БД истории. Новый контракт применяется ко всем новым explicit-policy группам, не только прежнему removal. Существующие safe/source и показанные review сценарии не мигрируют на новую платформу.

- До доступного Apply проверить общий Redis (`is_redis_cache()`) либо однопроцессный eager test (`CELERY_TASK_ALWAYS_EAGER`); non-eager LocMem не подходит. Cache miss всегда fail-closed, не live query fallback.
- GET preview создаёт UUID snapshot и signed handle; это не изменение переводов. TTL/start deadline = `PENDING_TASK_MAX_AGE` (сейчас 1 800 секунд). Pagination относится к тому же snapshot.
- Snapshot содержит actor, scope, entry check, выбранную policy/rule/version, ordered autofix fingerprint, IDs строк/translation/component/project/language, digests source/target plurals, state/machine-origin/effective flags и влияющих format/plural/template настроек, ожидаемый after digest. Не хранить полные тексты, ORM objects, credentials. IDs/fingerprint не заменяют версию алгоритма.
- POST подтверждает конкретную policy и `selection=all|page`; `all` — только допустимые IDs выбранной группы из snapshot. Другая группа, scope, версия или actor не принимаются. `page` требует signed cohort показанной страницы. Legacy review не включается через policy-all.
- Counts и diff относятся к моменту анализа. При чтении страницы свежие разрешённые units сверяются с digest; изменившаяся строка не показывается как прежнее подтверждённое предложение. Скрытые языки не попадают даже в counts исключений.
- Атомарный `cache.add` claim по snapshot UUID с selection digest/task ID: повтор того же POST возвращает ту же задачу; другая selection отклоняется. Не удалять неопределённый/чужой claim при enqueue failure.
- Expired/evicted snapshot или worker, впервые запущенный после deadline, ничего не пишет. Worker сразу публикует PROGRESS; поздний PENDING запуск после исчезновения индикатора не разрешён.
- Перед первой записью атомарно пометить сам snapshot `mutation_started`, не отдельный вытесняемый marker. Хранение snapshot/claim продлевается с существующим lock lease/heartbeat, исходное разрешение на старт — нет. Duplicate delivery после marker не продолжает mutation; cache loss/lease loss — остановка, не восстановление под тем же UUID. Это защита от повторной записи, не resumable subsystem.

### Запись, права и результат

Расширить существующие `fix_failing_checks`/`perform_fix` operation/snapshot веткой. Сохранить lock `(check, scope)` без отдельного конкурирующего lock по operation. Разные entry checks/overlapping scopes дополнительно защищены row locks и исходными digests: уже изменённый unit не перезаписывается.

- Перед запуском и записью — текущие actor/scope permissions `unit.bulk_edit` + `unit.edit`, а затем per-unit permission и свежая иерархия под row lock. Перемещённый component/translation, изменённые source/target/state/flags/config, dismissed/deleted check — skip. Не применять новые совпадения после snapshot.
- Обычные компонентные транзакции и batched checks. Число 250 ограничивает страницу, не весь набор. Нет произвольного усечения; масштаб не проверяется только на 250 строках.
- `Unit.translate` с текущим state, `FIX_FAILING_CHECK`, `propagate=False`; сохранить `automatically_translated` в Unit и PendingUnitChange по существующему примеру `reapply_autofixes.Command.repair`. Автор правки — пользователь. Не восстанавливать старый target/автора и не обходить audit.
- Source, sibling translations, flags, labels, explanations, historical judge verdicts не менять. Changed target делает старый verdict stale обычным способом; нормализация не означает approval и не запускает платный rejudge.
- Не запускать CLI `reapply_autofixes`: он применяет другой объём от bot и имеет отдельные VCS safeguards. Существующий scheduler обрабатывает pending; наша task не делает принудительный Git commit/push.
- Progress увеличивает processed для каждой выбранной строки, включая skip/denied; refresh lease не зависит от числа успешных правок. Fixed считается после commit; rollback текущего компонента не засчитывается.
- Handled failure после commits — failed/partial с известными committed counters. Worker/backend loss — `counts_known=false`, не выдуманные нули/успех. После сбоя `run_batched_checks` использовать существующий `component.schedule_update_checks`; не обещать recovery при потере worker.
- Для новой ветки task metadata **user_id-only**, scope передаётся через snapshot. `TasksViewSet.get_task` иначе предпочитает translation/component доступ actor-проверке. Учесть `store_published_task_metadata`; result не содержит текстов или скрытых IDs. API/poller прежние; неизвестный результат показан явно.

## Сценарий продюсера

Один существующий экран, Bootstrap/jQuery, без отдельного центра фоновых операций.

1. В списке ошибок — **«Посмотреть исправления»**, доступное по поддерживаемой операции и scope/language permissions, а не только наличию `get_fixup`. Не считать дорогостоящий eligible для каждой проверки при открытии всей таблицы.
2. Явная область `проект / компонент / язык`. Группы: **«Нормализовать конечные знаки по исходнику»**, доступные механические операции, **«Требуют просмотра»**, **«Не изменяем»**. На terminal-группе объяснение: «Знаки будут приведены к исходнику. Смысл и качество перевода не проверяются».
3. Показать N уникальных строк и отдельно число снимаемых срабатываний; terminal append/replace/remove подытоги. Source, before/after и все оставшиеся диагностические флаги доступны в preview; форматирование whitespace видно.
4. **«Применить ко всем N подходящим строкам»** выбранной policy либо **«Применить выбранные на странице (N)»**. Просмотр всех страниц доступен, но не обязателен для явно подтверждённой policy. Нет заранее подтверждённого checkbox политики, нет смешения разных политик одной скрытой кнопкой.
5. Legacy review — только показанные выбранные строки, максимум 250. Новая terminal policy не является техническим обходом этого контракта: она имеет отдельное описание/подтверждение и server-side eligibility; оставшиеся ambiguous строки не попадают в неё.
6. Исключения с честной причиной и доступной ссылкой на строку. При N=0 нет Apply/ложного обещания; есть причины и действие для оставшегося набора. Перед Apply — no-undo и число устаревающих verdicts, не предупреждение на каждом шаге.
7. Прежний task flash/poller: очередь → обработано X/N → changed/skipped/failed/partial/unknown. Reload использует existing user tasks; временный cache не история. Сообщение при потере результата: часть строк могла измениться, проверить текущие ошибки.

Keyboard/focus, native labelled controls, `aria-live`, long/RTL/narrow layouts; никакой nested crispy form. Следовать `ACCESSIBILITY.md` и `docs/contributing/frontend.rst`. Все новые UI-строки переводимы; RU каталоги обновлять только для новых msgids, без массового gettext backlog.

## Задачи реализации

### A. Детерминированная terminal-проекция и языковая корректность

**Результат:** append/replace/remove по согласованной policy дают общий before/after; изменение source authority не затрагивает обычное сохранение. **Файлы:** `weblate/checks/chars.py` renderer/source predicates; `weblate/trans/fix_check.py` candidate/classification/preparation; guards через `weblate/checks/utils.py`; existing custom provider не расширять глобально.

- [ ] Проверить LSP references изменяемых symbols; выделить переиспользуемую подготовку target из `_compute_final_target`, не копировать `Unit.translate` pipeline.
- [ ] Реализовать строгую terminal policy, закрытые rule IDs/version/reasons, полный набор relevant checks и structural/span guards.
- [ ] Исправить ja/zh против ko renderer; не менять `Language.is_cjk` и приём допустимой старой типографики checker-ами.
- [ ] Обновить затронутые behavioral tests, а не просто переименовать tier-assertions. Новая policy не равна `mass_fixup='safe'` у каждого terminal check.

**Проверка:** source `!` / zh `文本。` → `文本！`; source `?` / en `Text.` → `Text?`; source `.` / ko `내용!` → `내용.`; ja/zh fullwidth, fr/fr_CA, Greek/Arabic и прежние renderer cases. CJK source `:` не выбирает `。` по имени check. `?`/`:` без source-знака, repeated/ellipsis/quote/tag/URL/numeric/abbreviation/multiline/`$`/protected token не меняются. Source/empty target не меняются. Plural mapping и ambiguous form — unit-level refusal. Final saved value равен preview; идемпотентный повтор не меняет target. Команды включают `weblate/checks/tests/test_chars_checks.py`, `weblate/checks/tests/test_mass_fixup.py`, `weblate/trans/tests/test_fix_check.py`, `weblate/trans/tests/test_autofix.py`.

### B. Механические группы через существующие преобразования

**Зависимость:** candidate contract A, shared `fix_check.py` имеет одного integration owner. **Результат:** перечисленные механические правила доступны явно, неподдержанные не маскируются под исправимые. **Файлы:** `weblate/trans/fix_check.py`, существующие `weblate/trans/autofixes/chars.py`/`whitespace.py`, custom autofix registry; тесты `weblate/trans/tests/test_autofix.py`, `weblate_customization/tests/test_autofixes.py`, `weblate/checks/tests/test_mass_fixup.py`.

- [ ] Подключить closed mapping checks → check-fixup/active providers, с отдельными source-edge policies и French pair.
- [ ] Ограничить edits заявленной областью; exact save parity, ignore/locale/format/empty/plural guards.
- [ ] В UI/классификации отделить no proposal/provider unavailable/semantic cases; никогда автоматически включать весь `AUTOFIXES` registry.

**Проверка:** double-space вне и внутри protected span; source-double исключён. Край с ASCII-space не превращается в newline/tabs normalization; добавление source-space требует своей policy. Tight `Line$Next` исправляет только hugging spaces, lost `$` не восстанавливает, loose currency `$` не меняет. French spacing не меняет URLs/markup/fr_CA; source `…` + target `...` даёт `…`, прочее не угадывает. U+200B с source/Khmer/token guards. Game-number/token/markup/length/duplicate/reused/capital не получают механическое предложение. Дополнительный сторонний autofix — отказ, не незаявленная правка.

### C. Вся подтверждённая выборка существующей задачей

**Зависимость:** A/B contracts. **Результат:** 251+ и тысячи candidates применяются одним подтверждением, с защитой stale/scope/delivery. **Файлы:** `weblate/trans/fix_check.py:perform_fix`; `weblate/trans/views/search.py:fix_check/_resolve_fix_check_selection`; `weblate/trans/forms.py:FixCheckConfirmForm`; `weblate/trans/tasks.py:fix_failing_checks`; existing task metadata/poller consumers.

- [ ] Ввести общий snapshot для новых policy groups, подписанное согласие, pagination/all/page/claim/deadline; не создавать новую модель/task/API.
- [ ] Провести operation/snapshot через существующие consumers, сохранить review cohort; отклонять подмену policy/version/IDs.
- [ ] Реализовать revalidation после row lock, provenance сохранение, lease/progress/partial/unknown контракты.

**Проверка:** snapshot на 251 строку, новая 252-я после анализа не изменяется; изменение source/target/flags/config/иерархии → skip. Отозванные права/чужой handle/language denial/review-all — никаких записей. LocMem non-eager и evicted snapshot — fail-closed. PENDING после 1 800 s не пишет; started до deadline может закончить позже, redelivery не продолжает mutation. Concurrent end_stop/end_exclamation/overlapping scopes не дают повторную правку. Первый component committed, второй failed → точные partial counters. Worker loss → unknown. Сохранены Unit/Pending machine-origin/state/source/judge history. Команды: `weblate/trans/tests/test_fix_check.py`, `weblate/trans/tests/test_fix_check_task.py`, `weblate/trans/tests/test_fix_check_view.py`.

### D. Входы, подтверждение политики и доступный результат

**Зависимость:** A–C contracts; окончательная integration после C. **Файлы:** `weblate/trans/checklists.py`, `weblate/checks/views.py`, при необходимости `weblate/trans/views/edit.py`; `weblate/templates/snippets/translation.html`, `weblate/templates/check_list.html`, `weblate/templates/translate.html`, `weblate/templates/fix_check.html`; `weblate/static/loader-bootstrap.js`, при необходимости `weblate/static/styles/main.css`; RU `django.po`/`djangojs.po`.

- [ ] Убрать тупик eligibility-only-by-get_fixup во всех существующих входах, сохранить permission gates.
- [ ] Реализовать группы/подытоги/исключения/diffs/видимые пробелы, явное policy confirmation и раздельные all/page/review actions.
- [ ] Показать очередь/progress/partial/unknown/reload; ни generic 404, ни N=0 не выдаются за успех.
- [ ] Проверить form ownership, keyboard/focus, native labels, long/RTL/narrow; scoped i18n.

**Проверка:** реальный разрешённый dev/test surface с 0/1/7/251/2 092/3 454 строками, mixed policies/unsupported, denied/expired/double click/reload/back, partial/unknown. Desktop 1440 и narrow 390 px, keyboard-only от входа до результата. DOM/navigation через Lightpanda, visual screenshots через доступный Chromium либо явное ограничение visual проверки. Не применять к CoL4/prod без отдельного разрешения. Поведенческие view tests, а не assertions по исходному JS.

### E. Подтвердить покрытие и обновить документацию

**Зависимость:** A–D. **Результат:** существенный прирост обеспечен новым runtime pipeline, а не только throwaway regex или красивым UI.

- [ ] Повторить read-only проекцию полной реализации на Anvil и CoL4; при отсутствии разрешённого live access использовать разрешённую локальную копию. Отдельно counts по языку/rule, unique units, active checks cleared, exclusions, semantic-risk examples, rights/config/normalization потери. Не складывать пересечения.
- [ ] Проверить репрезентативные before/after каждого языка/операции и все guard категории; зафиксировать опасные примеры и остающиеся смысловые расхождения. Не писать corpus-specific skip для отдельных ID.
- [ ] На разрешённой isolated dev/test копии реально применить группу >250 и масштаб тысячи строк; проверить сохранённые targets, remaining checks, state/provenance, отсутствие source/sibling изменений, повторный запуск. Замерить длительность/число DB queries и heartbeat; убрать avoidable повторную проекцию на каждое срабатывание одного unit. Не вводить произвольный performance SLA без замера.
- [ ] `docs/admin/checks.rst`: source-authoritative policy против semantic-safe/review, все механические группы, exclusions/expiry/no undo/no history. `docs/product/guides/producer-guide-weblate.md`, шаг 9: массовая детерминированная обработка вместо повторного MT; не переписывать посторонние разделы.
- [ ] `docs/admin/management.rst`: отличие от operator backfill. `docs/security/threat-model.rst`: explicit policy allowlist, snapshot/signing/actor scope, cache/delivery, no global save-policy widening. `docs/changes.rst`: только upcoming/unreleased при необходимости.
- [ ] Один интеграционный прогон, scoped lint, удалить временные probes; записать фактические доказательства и остаточные ограничения в этот документ, commit/push. Deployment/production Apply — отдельное разрешение.

После `uv sync --all-extras --dev` и DB/static setup по `docs/contributing/tests.rst`:

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/checks/tests/test_chars_checks.py weblate/checks/tests/test_mass_fixup.py weblate/trans/tests/test_fix_check.py weblate/trans/tests/test_fix_check_view.py weblate/trans/tests/test_fix_check_task.py weblate/trans/tests/test_autofix.py weblate_customization/tests/test_autofixes.py
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest weblate/trans/tests/test_commands.py::ReapplyAutofixesCommandTest weblate/trans/tests/test_commands.py::ReapplyAutofixesTest
```

Не запускать независимые pytest одновременно в одном checkout/reuse DB. Scoped lint: `uv run prek run --files <изменённые файлы>`; русский gettext — `msgfmt --check`. Это будущие команды проверки реализации, не выполненные тесты данного plan-only изменения.

## Зависимости и приёмка

A → B/C → D integration → E. Исследование правил A/B независимо, но изменения общего `fix_check.py` сериализуются одним владельцем. UI templates можно готовить после фиксации contracts; не придумывать параллельные payload schemas. Exported-symbol changes — через LSP references и обновление всех реальных consumers.

- [ ] Не менее половины активных target-check срабатываний Anvil устраняется полным проверенным pipeline на сопоставимом корпусе; это **предложенный критерий согласования**, не гарантия из предварительных 77,37%. Если защитные проверки опускают покрытие ниже 50%, работа не объявляется выполненной: объяснить причины и согласовать пересмотр объёма/критерия, не ослаблять безопасность.
- [ ] CoL4 сохраняет массовое удаление лишней пунктуации; покрытие сверено с исходными 2 092/7, все потери относительно лексических 1 915/6 объяснены.
- [ ] Новые replacement/CJK и механические операции реально доступны через UI; реализация одной кнопки или только старого removal не закрывает план.
- [ ] Одна явно подтверждённая policy исправляет >250, без обязательного просмотра каждой строки; unmatched/changed/ambiguous/denied строки не захватываются.
- [ ] Реальный after совпадает с preview; нет новых relevant failures, повреждения protected content/source/state/provenance/judge history. Исчезновение punctuation-check не выдаётся за проверку смысла.
- [ ] Известный partial и неизвестный результат различимы; права/cache/TTL/duplicate-delivery/overlap/review-cohort сценарии проверены.
- [ ] Нет новых models/migrations/tasks/history/recovery subsystem и скрытого изменения глобальных autofixes.

**Одобрение:** пользователь согласовал направление и source-authoritative terminal policy, а также написание расширенного плана. Численный критерий, полный технический объём и реализация этого документа ещё требуют одобрения. Production остаётся read-only.

## Доказательства планирования

Два независимых исследования на Luna покрыли terminal/locale/check границы и остальные механические правила. Main проверил ключевые реализации, запустил read-only Anvil projection по пяти языкам, повторил её с корректным ko renderer и expanded terminal guard, классифицировал сохранённую CoL4 выборку. Код приложения, модели, переводы и running stack не менялись.

Сохранены замечания предыдущего architecture/security review: shared cache precondition, actor-only task metadata, scope revalidation, start deadline по `PENDING_TASK_MAX_AGE`, атомарный mutation marker и честный partial/unknown. Новая policy распространяет эти гарантии на append/replace/mechanical groups; прежнее ограничение «all только removal» более не действует.

Повторный разбор расширенного документа на Luna уточнил границы fullwidth removal против save-time provider, veto checks, protected-span отказа, source-edge policies и доступности French pair. Предложение добавить новый removal-only whitespace provider отклонено: существующий `get_fixup` уже удаляет край при нулевом числе source-пробелов, достаточно явно разделить eligibility.

Scoped проверка документа: `SKIP=reuse,typos uv run --no-sync prek run --files docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md`. Hooks документа, включая doccmd/codespell/kingfisher/rumdl, прошли. `reuse`/`typos` исключены из-за ранее установленных общерепозиторийных ошибок вне плана (`.omp/lsp.json` licensing и существующие `analysis/data/` corpora); это не заявление о чистом project-wide lint. Тесты приложения не запускались: изменён только план.
