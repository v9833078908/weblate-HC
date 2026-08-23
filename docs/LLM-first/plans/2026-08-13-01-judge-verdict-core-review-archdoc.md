# ArchDoc Review: Judge Core Requirements from Design Documents

Review of two design documents against the implementation plan
`docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md`.
All citations are `path:line` with verbatim quotes.

---

## 1. Hard Requirements, Invariants, and Contracts

### 1.1 System Invariants (architecture.md:4.1)

1. **Determinism first.** `docs/LLM-first/llm-first-product-architecture.md:368-369`
   > Всё детерминированно проверяемое проверяется кодом — автофиксами и
   > чеками, не промптом и не судьёй.

2. **Probabilistic verdict never blocks release.** `architecture.md:371-373`
   > Вероятностный вердикт никогда не блокирует релиз. Блокируют только
   > детерминированные проверки; судья — advisory required-check'ом не
   > становится

3. **Different model family.** `architecture.md:374-376`
   > Судья — другое семейство моделей, чем переводчик. Self-preference
   > bias управляется перплексией, а не авторством

4. **Verdict from errors, not reverse.** `architecture.md:377-382`
   > Вердикт выводится из списка ошибок, а не наоборот: судья сначала
   > перечисляет MQM-ошибки со span, категорией и severity (веса
   > critical=25 / major=5 / minor=1), затем вердикт из инвентаря

5. **Back-translation is display-only.** `architecture.md:383-388`
   > Back-translation — display-only артефакт доверия, не скоринговый
   > сигнал. Литература единодушна: RTT как оценщик ненадёжен

6. **Terminology: deterministic first.** `architecture.md:389-393`
   > Терминология: детерминированный матч первым, LLM — только на
   > промахах (паттерн IFMTBench), и только advisory для склоняемых
   > терминов; hard остаётся за forbidden/exact

7. **Per-language trust tiers.** `architecture.md:394-398`
   > Для th/vi/id/hi/fa судья ненадёжен (LoResLM 2025: Spearman 0.14-0.36
   > против 0.34-0.64 у fine-tuned энкодеров) — там pass не авто-одобряется
   > без второго сигнала, авто-reject запрещён.

8. **Every verdict leaves a trace.** `architecture.md:399-401`
   > Каждый вердикт оставляет след: Check-строка для фильтров и
   > статистики плюс комментарий с уликами. Молчаливых переходов
   > состояния нет.

### 1.2 Execution Invariants (architecture.md:4.3)

9. **Unparsable/empty response is transport failure, not verdict.** `architecture.md:428-430`
   > Неразобранный или пустой ответ судьи — сбой транспорта, не
   > вердикт. Ретрай, затем пропуск строки со счётчиком в метриках
   > прогона. Никогда не эскалация в flag/reject.

10. **No-regress on repair.** `architecture.md:431-434`
    > Результат второго прохода принимается, только если множество failing
    > checks не выросло относительно состояния до правки; иначе откат к
    > предыдущему target.

### 1.3 Data Model (UI design.md:1)

11. **`JudgeVerdict` is the store; `Check` is projection only.** `2026-08-13-judge-native-ui-design.md:188-189`
    > `Check`-строка (`judge-flag` / `judge-reject`) — ради виджета,
    > фильтров, бейджей и статистики. Проекция, не хранилище.

12. **No comment written.** `2026-08-13-judge-native-ui-design.md:191-195`
    > Комментарий не пишется. Он был обходным путём при отсутствии
    > модели. Улики рендерит карточка; иначе лента комментариев забивается
    > машинной прозой, а `has:comment` перестаёт означать «человек что-то
    > сказал».

13. **Staleness: `target_hash` mismatch → verdict stale.** `2026-08-13-judge-native-ui-design.md:200-203`
    > `target_hash` != хэш текущего `unit.target` → вердикт протух:
    > `Check`-строка снимается, карточка показывает «вердикт относится к
    > предыдущей версии текста», юнит возвращается в очередь суда.

14. **Context change: not stale, but re-judged in background.** `2026-08-13-judge-native-ui-design.md:204-206`
    > `context_hash` изменился (глоссарий, note) → вердикт не протухает, но
    > помечается вынесенным по устаревшему контексту и пересуживается
    > фоновым прогоном, а не срочно.

15. **Cache key: `(target_hash, context_hash, judge_model)`.** `2026-08-13-judge-native-ui-design.md:210-211`
    > Пара `(target_hash, context_hash, judge_model)` — естественный ключ кэша:
    > неизменённая строка не пересуживается никогда.

16. **Verdicts accumulate, not overwrite.** `2026-08-13-judge-native-ui-design.md:213-216`
    > Вердикты не перезаписываются, а накапливаются по `(unit, run_id, attempt)`.
    > Это даёт белый ящик коллегии, регрессию при смене промпта и обязательную
    > метрику доли холостых починок

17. **`resolution_reason` mandatory on override.** `2026-08-13-judge-native-ui-design.md:182-185`
    > Причина обязательна при override — симметричное трение: «принять» не
    > должно быть дешевле, чем «не принять». Это же даёт метрику override-rate

### 1.4 Severity Scale and Verdict Semantics (UI design.md:3)

18. **Severity-to-state mapping.** `2026-08-13-judge-native-ui-design.md:537-549`
    > | Вердикт | Состояние | Отгружается | Механизм |
    > |---|---|---|---|
    > | pass | 30 approved | да | авто-одобрение судьёй |
    > | minor | 30 approved | да | ошибки записаны, вес 1 |
    > | major (flag) | 20 translated | **да** | висит в очереди, релиз не блокирует |
    > | critical (reject) | 10 needs editing | **нет** | `WITHOUT_NEEDS_EDITING` отсекает |
    > | детерминированный провал | 11 needs rewriting | нет | `enforced_checks`, отдельно от судьи |

19. **2026-08-19 amendment: `critical → 10` is a human-decision queue, not a guarantee.** `2026-08-13-judge-native-ui-design.md:553-558`
    > `state 10` — это **очередь на решение человека** с удержанием старой
    > версии строки, а экран готовности (план 2) с числом удержанных строк
    > из nice-to-have становится обязательной частью гейта: релизный гейт =
    > детерминированные чеки + разобранная человеком critical-очередь.

20. **Release gate mechanism: `WITHOUT_NEEDS_EDITING`.** `2026-08-13-judge-native-ui-design.md:537,543`
    > `commit_policy = WITHOUT_NEEDS_EDITING`
    > (`weblate/trans/models/project.py:83-89, 339-342`), кастомный экспорт не
    > нужен.

### 1.5 Prompt Contract

21. **Structured output (JSON schema) with error list first.** `architecture.md:388-393`
    > Батч юнитов -> structured output (JSON schema) со списком ошибок
    > {span, категория, severity} -> вердикт: critical -> reject, major ->
    > flag, иначе pass.

22. **`description` field mandatory, self-contained for non-reader of target language.** `2026-08-13-judge-native-ui-design.md:260-264`
    > Описание содержит **обратный перевод спорного фрагмента**: «написано
    > X, что значит Y, тогда как в источнике Z». Спан сам по себе адресату
    > ничего не сообщает.

23. **`description` in interface language, not target language.** `2026-08-13-judge-native-ui-design.md:265`
    > Описание на языке интерфейса продюсера, не на целевом.

24. **Bare span without explanation is invalid `description`.** `2026-08-13-judge-native-ui-design.md:266-267`
    > Голый спан без объяснения — **невалидный `description`**, даже когда
    > вердикт по существу верен.

25. **Cheap validation: description lacking interface-language chars or matching span → marked incomplete.** `2026-08-13-judge-native-ui-design.md:283-287`
    > описание, не содержащее ни одного символа языка интерфейса, либо
    > совпадающее со спаном, помечается как неполная улика. Это не сбой
    > транспорта и вердикт не отменяет — карточка показывает вердикт с
    > пометкой «улика неполная».

26. **Default prompt for implementation: плечо C (2026-08-19 recalibration).** `2026-08-13-judge-native-ui-design.md:293-297`
    > Промпт по умолчанию для реализации — **плечо C** того замера:
    > `description` + рубрика severity «последствие для игрока».

27. **Placeholder rendering preview in judge input.** `2026-08-13-judge-native-ui-design.md:466-468`
    > рендер-превью в **вход судьи по умолчанию** (плечо D, дёшево)
    > и в **UI юнита для продюсера** (та же подстановка человеку).

### 1.6 Collegium Rules

28. **Two judges, parallel, verdict = strict max of two.** `2026-08-13-judge-native-ui-design.md:99-101`
    > Оба судьи независимо судят каждую строку, вызовы идут параллельно,
    > итоговый вердикт — **строгий из двух**. Ни один судья не вправе понизить
    > вердикт другого.

29. **Judge identity: `deepseek/deepseek-v4-pro` (seat 1) and `qwen/qwen3-235b-a22b-2507` (seat 2).** `2026-08-13-judge-native-ui-design.md:510-511`
    > судьи выбраны (`deepseek/deepseek-v4-pro` и `qwen/qwen3-235b-a22b-2507`,
    > вердикт — строгий из двух)

30. **Seat is not seniority.** Model field: `2026-08-13-judge-native-ui-design.md:172`
    > `seat: SmallInt  # 1 или 2 — место в коллегии, не старшинство`

31. **Collegium replaces 3x-aggregation.** `2026-08-13-judge-native-ui-design.md:147`
    > Коллегия **заменяет 3x-агрегацию** из архитектуры 4.2

### 1.7 Repair Loop Rules

32. **Repair triggered on `reject` verdict.** `2026-08-13-judge-native-ui-design.md:77`
    > Когда чинится перевод: при итоговом вердикте `reject`

33. **Repair translator receives error descriptions, not spans.** `2026-08-13-judge-native-ui-design.md:237-240`
    > Починка подаёт описания ошибок, а не спаны. Промпт переводчика
    > получает «ошибка X в районе Y» вместо точных офсетов.

34. **Up to N repair attempts.** `2026-08-13-judge-native-ui-design.md:95`
    > FIX -->|не починилось| H[Очередь продюсера]

35. **No-regress invariant from 4.3 applies.** `architecture.md:431-434`
    > Результат второго прохода принимается, только если множество failing
    > checks не выросло

### 1.8 Entry Point Rules

36. **Judge mode in AutoForm, explicit human choice.** `2026-08-13-judge-native-ui-design.md:559`
    > Судья вызывается явным выбором человека в существующей форме, а не
    > невидимым фоновым процессом.

37. **Fail-safe: fresh translation written as state 10, raised only by verdict.** `2026-08-13-judge-native-ui-design.md:576-578`
    > Свежепереведённая строка пишется как `10 needs editing` и повышается
    > только вердиктом: несудившееся не отгружается никогда, даже если
    > прогон оборвался.

38. **Existing translations never downgraded by entry.** `2026-08-13-judge-native-ui-design.md:578-579`
    > Уже существующие переводы при этом **не понижаются** — их состояние
    > меняет только вердикт.

39. **Default behavior: translate only empty/needs-editing; existing translations judged only.** `2026-08-13-judge-native-ui-design.md:571-573`
    > По умолчанию режим переводит только пустые и помеченные на правку
    > строки; строку с существующим переводом он только судит.

40. **Overwrite checkbox gated.** `2026-08-13-judge-native-ui-design.md:573-575`
    > Поведение переключается чекбоксом **«Перезаписать существующий
    > перевод»** (по умолчанию выключен).

41. **`unit.review` right required for judge mode.** `2026-08-13-judge-native-ui-design.md:563`
    > Право `unit.review` обязательно: режим ставит state 30.

---

## 2. План 1 — Table Row and Acceptance Criterion (Verbatim)

**Source:** `2026-08-13-judge-native-ui-design.md:772`

| План | Файл | Содержимое | Готово, когда |
|---|---|---|---|
| **1. Вердикт** | `plans/2026-08-13-01-judge-verdict-core.md` | `JudgeVerdict` + миграция, классы judge-чеков, клиент судьи (промпт — плечо C замера 2026-08-19), коллегия двух судей и починка в Celery, режим `judge` в `AutoForm` + чекбокс перезаписи, fail-safe state 10, удержание critical до решения человека, карточка на юните, обратный перевод в форме, протухание, исключение судейских чеков из «Things to check» | прогон на фильтре даёт построчный вердикт; `critical` не уходит в сборку без решения человека; навигация через `check:judge-*` |

Acceptance criterion verbatim:
> прогон на фильтре даёт построчный вердикт; `critical` не уходит в
> сборку без решения человека; навигация через `check:judge-*`

---

## 3. "Что нужно загородить" and First-Production-Run Gates (Verbatim)

### Что нужно загородить

**Source:** `2026-08-13-judge-native-ui-design.md:582-589`

> ### Что нужно загородить
>
> - **`mode=translate` + `q=judge:pass`** перезапишет одобренное судьёй.
>   Вердикты протухнут корректно, но продюсер потеряет одобренную
>   локализацию, не поняв этого. Нужно предупреждение в форме.
> - **`mode=judge` на большом компоненте** — реальные деньги. Форма должна
>   показывать число строк, попадающих под прогон, до запуска. Сейчас она
>   не показывает его вообще.

### First-Production-Run Gates

**Source:** `2026-08-13-judge-native-ui-design.md:502-528`

> ### Порядок гейтов боевого прогона
>
> Первый прогон судьи, меняющий состояния на проде, требует одновременно:
>
> 1. B2' пройдена — **выполнено 2026-08-13**: судьи выбраны
>    (`deepseek/deepseek-v4-pro` и `qwen/qwen3-235b-a22b-2507`, вердикт —
>    строгий из двух), пороги измерены на запечатанном срезе. Два порога
>    плана при этом переписываются, а не достигаются: auto-pass >= 90%
>    несовместим с корпусом (потолок безошибочного судьи 81.8% при доле
>    дефектов 18.2%), а false-reject <= 2% не сертифицируем на 167 чистых
>    строках даже при нуле ошибок. Решение по обоим — предусловие прогона;
> 2. прод-backfill автофиксов `--apply` выполнен по runbook (код готов:
>    `weblate/trans/management/commands/reapply_autofixes.py`, scoped commit
>    через `Component.commit_pending_subset`);
> 3. отпечаток **слоя 0 целиком** совпадает с отпечатком прод-инстанса.
>    Отпечаток составной, и одного автофиксного мало:
>    - реестр автофиксов — `autofix_fingerprint()`
>      (`weblate/trans/autofixes/__init__.py`);
>    - режим глоссарного матчера и содержимое глоссария — на момент
>      написания не фиксируется ничем, см. риски;
>
>    Иначе судья калиброван на дефектах, которые слой 0 уже снимает;
> 4. золотой набор **ребейзлайнен** после правки матчера: морфология
>    меняет не структуру набора, а входные условия — какие термины лежат
>    в промпте судьи. Пересобирать страты не нужно, нужен повторный
>    прогон с новым отпечатком;
> 5. отдельное согласование траты и изменения прод-данных.

---

## 4. Design's Stance on `has:judge` Filter / Default `q` (Verbatim)

**Source:** `2026-08-13-judge-native-ui-design.md:568-580`

> | Пункт | Запрос | Зачем |
> |---|---|---|
> | Не судилось | `NOT has:judge` | **дефолт режима judge** |
> | Отклонено судьёй | `judge:reject` | перезапуск суда |
> | Под вопросом | `judge:flag` | то же для major |
> | Вердикт протух | `judge:stale` | пересудить изменённое |
> | Ждёт решения человека | `judge:exhausted` | разбор накопленного |

> Дефолтный `q` меняется вместе с режимом: сейчас он жёстко
> `state:<translated` (`forms.py:1201, 1325-1326`), для `judge` дефолт —
> `NOT has:judge`, единственный фильтр, покрывающий и новые непереведённые
> строки, и старые переведённые, но не судившиеся.

> **Главная проверка после прогона:** `NOT has:judge` должен стать пустым.
> Остаток — это строки, где судья не ответил, а не строки, которые прошли.

The full filter table (for reference): `2026-08-13-judge-native-ui-design.md:420-427`

> | Вопрос продюсера | Фильтр |
> |---|---|
> | Что уйдёт в сборку с одобрением? | `judge:pass` |
> | Что уйдёт, но не подтверждено? | `judge:flag` |
> | Что **не** уйдёт в сборку? | `judge:reject` |
> | Что судья ещё не смотрел? | `NOT has:judge` |
> | Что ждёт моего решения? | `judge:exhausted` |
> | Что я принял вопреки судье? | `judge:override` |

---

## 5. Design/Plan Deviations and Ambiguities

### 5.1 Explicit Deviations from Architecture (Documented in "Расхождения")

These are stated in `2026-08-13-judge-native-ui-design.md:600-642` as
"Расхождения с существующими документами". The plan implements the UI
design, not the architecture, on these 8 points:

1. **Verdict storage: model, not comment.** `2026-08-13-judge-native-ui-design.md:605-606`
   > Архитектура 4.3 предписывает комментарий с уликами. Заменяется
   > моделью `JudgeVerdict`; комментарий не пишется.

2. **`flag` state: 20, not 10.** `2026-08-13-judge-native-ui-design.md:607-609`
   > Архитектура 4.3: `flag → state 10`. Становится `flag → state 20`:
   > иначе major блокировал бы поставку вопреки решению по severity.

3. **Release gate: `WITHOUT_NEEDS_EDITING`, not `APPROVED_ONLY`.** `2026-08-13-judge-native-ui-design.md:610-614`
   > Архитектура 4.5 предполагает политику «Only include approved
   > translations». Заменяется на `WITHOUT_NEEDS_EDITING`

4. **3x-aggregation replaced by collegium.** `2026-08-13-judge-native-ui-design.md:615-616`
   > Архитектура 4.2 требует троекратного прогона на flag/reject.
   > Заменяется вторым семейством моделей.

5. **Entry point: AutoForm mode, not external REST pipeline.** `2026-08-13-judge-native-ui-design.md:628-629`
   > В архитектуре судья — внешний пайплайн поверх REST API. Заменяется
   > режимом формы автоперевода и Celery-задачей.

6. **MVP "no template changes" removed.** `2026-08-13-judge-native-ui-design.md:630-631`
   > MVP «ноль правок шаблонов» (архитектура 4.4) снят: без правок
   > шаблонов вердикт визуально неотличим от детерминированного факта.

7. **CometKiwi no longer a blocker.** `2026-08-13-judge-native-ui-design.md:625-627`
   > Коллегия даёт второй сигнал уже сейчас — **CometKiwi перестаёт
   > быть блокером**.

8. **Calibration protocol changed.** `2026-08-13-judge-native-ui-design.md:617-623`
   > План фазы 0 меряет двух судей параллельно, чтобы выбрать одного.
   > Прогон 2026-08-13 показал, что выбирать одного не надо

### 5.2 Ambiguous / Underspecified Areas

**A. `translation_review` — open question before implementation.** `2026-08-13-judge-native-ui-design.md:643-660`

> ### Открытый вопрос перед реализацией
>
> **Producer-only режим и штатный review workflow — дополнительный
> research.** `translation_review=True` превращает `state 20` в штатную
> очередь `unapproved` («ожидает ревью»), а текущая формулировка режима
> `judge` требует `unit.review` и допускает `state 30`. Для среды без
> переводчиков это может создать ложную очередь человека и смешать
> штатное «одобрено» с фактом «LLM-проверка пройдена».
>
> До реализации плана 1 нужно отдельно проверить и зафиксировать:
> 1. запускается ли первый тир с `translation_review=False`; если нет —
>    каким механизмом убирается или заменяется штатная очередь
>    `unapproved`;
> 2. нужно ли режиму `judge` право `unit.review`, или ему нужен отдельный
>    capability/permission для запуска и записи вердикта;
> 3. точное отображение `JudgeVerdict` в `state` и в допуск к сборке,
>    независимое от штатного review workflow.
>
> До закрытия этого вопроса фразы «`translation_review` желательно» и
> «`unit.review` обязательно» считать **временными гипотезами**, а не
> решением дизайна. Ни `translation_review`, ни `unit.review` не должны
> использоваться как доказательство прохождения LLM-проверки или допуска
> строки к релизу.

**B. Minor severity — not in the `JudgeVerdict.verdict` enum.** The model
defines `verdict: pass | flag | reject` (`2026-08-13-judge-native-ui-design.md:163`)
but the severity table shows `minor` as giving `state 30 approved`
(`2026-08-13-judge-native-ui-design.md:540`). The `verdict` field is said to
be "производное от max_severity" (`2026-08-13-judge-native-ui-design.md:164`),
but the mapping `minor → pass` is implicit — an implementer must infer
that `max_severity=minor` maps to `verdict=pass` with `state=30`.

**C. Back-translation model and cost not specified.** The design says BT is
"дешёвой моделью" (`architecture.md:406`) and displayed in the `secondary` slot
(`2026-08-13-judge-native-ui-design.md:327-329`). No model name, no cost budget,
no batch size, no concurrency. An implementer must choose.

**D. Row counter before run — spec says "должна показывать" but no mechanism.** `2026-08-13-judge-native-ui-design.md:588-589`
> Форма должна показывать число строк, попадающих под прогон, до
> запуска. Сейчас она не показывает его вообще.

No code path, no template change, no API endpoint specified.

**E. `mode=translate` + `q=judge:pass` warning — spec says "нужно предупреждение" but no mechanism.** `2026-08-13-judge-native-ui-design.md:584-586`
> Нужно предупреждение в форме.

No template, no JS validation, no form-level check specified.

**F. Span highlighting — plan 1 scope vs plan 3 scope.** Plan 1 includes
"карточка на юните" (`2026-08-13-judge-native-ui-design.md:772`) but span
highlighting via `Formatter` is explicitly in plan 3
(`2026-08-13-judge-native-ui-design.md:774`). The card must show errors
without span highlighting in plan 1 — this is consistent with the
"degradable function" design (`2026-08-13-judge-native-ui-design.md:221-240`),
but an implementer might try to add spans in plan 1.

**G. `description` validation — "cheap validation" is underspecified.** `2026-08-13-judge-native-ui-design.md:283-287`
> описание, не содержащее ни одного символа языка интерфейса, либо
> совпадающее со спаном, помечается как неполная улика.

"Язык интерфейса" — what is the interface language? Russian? English?
Configurable? How is "contains at least one character of interface
language" implemented? The spec says the validation is cheap but
doesn't define the character set.

---

## 6. UI/UX Requirements

### 6.1 Cards

**Card on unit page — "Оценка ИИ".** `2026-08-13-judge-native-ui-design.md:330-331`
> Карточка «Оценка ИИ» — в сайдбаре, перед «Things to check».
> Вердикт, ошибки, мнения обоих судей, кнопки решений.

**Card contents by verdict:** `2026-08-13-judge-native-ui-design.md:355-365`

| Verdict | Appearance | Contents |
|---|---|---|
| pass | collapsed | "Принято · модель · когда"; раскрытие — BT и что проверялось |
| minor | collapsed | то же + список minor-замечаний |
| flag / major | expanded | ошибки со спанами, мнения обоих судей, история починок, плашка «отгружается, но не подтверждено» |
| reject / critical | expanded | то же + «не уйдёт в сборку» + предложение исправления как suggestion |
| stale | grey | «вердикт относится к предыдущей версии», кнопка «пересудить» |
| unparsable | grey | «ответ судьи не разобран» — сбой транспорта, **не вердикт** |

**Separate card on language overview.** `2026-08-13-judge-native-ui-design.md:444-460`
> Виджет `Strings status` остаётся детерминированным; рядом встаёт
> вторая карточка той же конструкции

### 6.2 Back-Translation Display

**In the form body, in `secondary` slot.** `2026-08-13-judge-native-ui-design.md:327-329`
> Обратный перевод — в теле формы, в слоте `secondary`
> (`weblate/templates/translate.html:101-108`). Это существующий нативный
> паттерн «та же строка в другом рендере для справки», через
> `format_unit_target`.

**Labeled as "approximate reconstruction".** `2026-08-13-judge-native-ui-design.md:331`
> Подпись — «приблизительная реконструкция», чтобы её не приняли за эталон.

### 6.3 Exclusion from "Things to Check"

**Judge checks excluded from the `unit.all_checks` card.** `2026-08-13-judge-native-ui-design.md:342-348`
> судейские `Check`-строки **исключаются из карточки «Things to
> check»** и живут только как проекция для навигации. Точки касания:
> - `translate.html:619` — итерировать отфильтрованный список
> - `translate.html:593` — условие показа карточки, иначе она
>   отрендерится пустой при единственном судейском чеке

**`judge-*` never in `enforced_checks`.** `2026-08-13-judge-native-ui-design.md:352`
> `judge-*` никогда не попадает в `enforced_checks` (нарушило бы
> инвариант 1 архитектуры)

**Dismiss disabled for judge checks.** `2026-08-13-judge-native-ui-design.md:352-354`
> механизм `Dismiss` у судейских чеков отключается — у нас есть решение
> с обязательной причиной, а два конкурирующих override дают дыру в
> аудите.

### 6.4 Visual Design Principle

**Outline = opinion, fill = fact.** `2026-08-13-judge-native-ui-design.md:350`
> Принцип оформления: **контур = мнение, заливка = факт**.

**Separate badge class.** `2026-08-13-judge-native-ui-design.md:347-349`
> `weblate/trans/templatetags/translations.py:933-936` `unit_state_class`
> — отдельный класс бейджа, чтобы мнение и факт не были одной красной
> точкой

### 6.5 What NOT to Show

**No numeric score.** `2026-08-13-judge-native-ui-design.md:371-372`
> Не показываем число. Ни 0-100, ни процент уверенности. Продюсер не
> может проверить число, а число создаёт ложную точность.

**Show what judge saw and ignored.** `2026-08-13-judge-native-ui-design.md:373-375`
> Показываем, что судья видел и что проигнорировал — глоссарные
> термины в контексте, полученные `failing_checks`.

**Show judge disagreement.** `2026-08-13-judge-native-ui-design.md:376-377`
> Показываем разногласие судей. Согласие мест — сильный сигнал,
> расхождение — слабый; скрывать его нельзя.

### 6.6 Decision Buttons

**Only on strings that reached a human.** `2026-08-13-judge-native-ui-design.md:385`
> Только на строках, дошедших до человека

**Three buttons.** `2026-08-13-judge-native-ui-design.md:387-391`
> - **Принять как есть** — модалка с обязательной причиной, `state → 30`,
>   `resolution = accepted_as_is`.
> - **Вернуть в работу** — сброс попыток, новый круг суда.
> - **Эскалировать** — `resolution = escalated`, отдельный фильтр для
>   внешнего лингвиста.

**Override reason contract.** `2026-08-13-judge-native-ui-design.md:299-304`
> причину пишет продюсер, но пишет он **оценку цены ошибки в продукте**
> (какой экран, сломает ли игрока, горит ли релиз), а не лингвистическое
> суждение.

### 6.7 Release Readiness Banner

**Exact layout specified.** `2026-08-13-judge-native-ui-design.md:466-481`
> ```text
> ┌ Готовность к сборке ────────────────────────────────┐
> │ 76 из 82 строк уйдут в сборку                       │
> │  ● 64  одобрено судьёй                              │
> │  ● 12  под вопросом — уйдут, не подтверждены        │
> │  ● 6   отклонено — НЕ уйдут          [разобрать →]  │
> │  ● 0   не судилось                                  │
> └─────────────────────────────────────────────────────┘
> ```

**Must reuse `commit_policy_skipped`, not recalculate.** `2026-08-13-judge-native-ui-design.md:483-485`
> Число «уйдут в сборку» **переиспользуется**, а не пересчитывается:
> `commit_policy_skipped` уже считается в
> `weblate/trans/models/pending.py:349-355`

**"Не судилось" row is critical.** `2026-08-13-judge-native-ui-design.md:487-488`
> Строка «не судилось» — самая важная: без неё «6 отклонено» читается
> как «остальное хорошо», хотя может означать «остальное не смотрели».

### 6.8 Row Counter in Form

**Required before run.** `2026-08-13-judge-native-ui-design.md:588-589`
> Форма должна показывать число строк, попадающих под прогон, до
> запуска. Сейчас она не показывает его вообще.

### 6.9 "Оценка ИИ" Tab (Plan 3, but referenced in design)

**White-box history.** `2026-08-13-judge-native-ui-design.md:333-334`
> Вкладка «Оценка ИИ» — белый ящик: кто судил, какой моделью, когда,
> что ответил, что менялось между попытками, подсветка спанов.

---

## FLAGGED: Requirements an Implementer Would Likely Miss

1. **`resolution_reason` mandatory on override** — `2026-08-13-judge-native-ui-design.md:182-185`. The model field exists but the "симметричное трение" constraint (accept must not be cheaper than reject) is easy to skip in form validation.

2. **Cheap `description` validation** — `2026-08-13-judge-native-ui-design.md:283-287`. Marking descriptions as incomplete (not rejecting the verdict) is a subtle UX requirement that could be mistaken for "just validate the JSON schema."

3. **`context_hash` change → background re-judge, not stale** — `2026-08-13-judge-native-ui-design.md:204-206`. Implementers might treat any hash mismatch as stale, which would be too aggressive for glossary changes.

4. **"Не судилось" row in release readiness** — `2026-08-13-judge-native-ui-design.md:487-488`. Without this row, the release readiness display is actively misleading. Plan 2 item, but the design is emphatic about its importance.

5. **`mode=translate` + `q=judge:pass` dangerous combination** — `2026-08-13-judge-native-ui-design.md:584-586`. Easy to miss because it's a cross-mode interaction, not a single-mode bug.

6. **`translation_review` open question** — `2026-08-13-judge-native-ui-design.md:643-660`. This is listed as an explicit open question that must be resolved before implementation. An implementer who skips the preamble might hardcode `translation_review=True` and `unit.review` as givens.

7. **Placeholder rendering preview in judge input** — `2026-08-13-judge-native-ui-design.md:466-468`. This is a 2026-08-19 amendment that lives in the "Что осталось судье" section, not in the prompt contract section. Easy to miss.

8. **`judge-*` Dismiss must be disabled** — `2026-08-13-judge-native-ui-design.md:352-354`. Weblate's default `Check` behavior includes a dismiss mechanism; the plan must explicitly disable it for judge checks.
