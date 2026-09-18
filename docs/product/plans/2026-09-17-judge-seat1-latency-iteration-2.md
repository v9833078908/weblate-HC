# Ускорение судьи, итерация 2: seat 1 без потери качества

Статус: **завершён 2026-09-18, все задачи (1–6) исполнены.**
Решения по разделу 8 утверждены владельцем: бюджет — лимиты итерации 1 ($5/слот);
кандидат — K1 (DeepSeek Pro + thinking off); batch seat 2 — 5; корпус — QA-fixture
контролируемый (вариант (a)); гейт-политика — консервативная решает + collegium
публикуется. Все 5 экспериментов проведены: стресс K1 пройден (0 unparsed);
сравнение качества K1 vs C0 проведено (ускорение −70%, recall 100%, ложные флаги 5 vs 5);
финальная связка K1+Qwen b5 vs C0 проведена (ускорение пары −81.1%, recall 100%, ложные флаги 5 vs 5).
Полные отчёты: `docs/product/measurements/2026-09-17-judge-seat1-parse-stability.md`
и `docs/product/measurements/2026-09-18-judge-seat1-quality-and-bundle.md`.

Дата: 2026-09-17. Преемник
`docs/product/plans/2026-09-17-judge-batching-and-latency-experiments.md`
(исчерпан) и его финального отчёта
`docs/product/measurements/2026-09-17-judge-batching-latency-holdout-confirmation.md`.

Этот документ — полный handoff: новая сессия может продолжить с одного
этого файла, не читая историю чата. Ниже: контекст продукта, вся
итерация 1 с числами и артефактами, операционная среда запуска, затем
детальный план итерации 2.

---

## 1. Контекст продукта: что такое судья

LLM-судья оценивает переводы в HCGameLoc (форк Weblate). Архитектура —
два сида, исполняются **параллельно**; wall clock пары = время самого
медленного сида, не сумма. Итог раунда сводится collegium-правилом:

- `weblate/trans/models/judge.py:1279` `collegium_verdict` —
  представитель раунда = строжайший распарсенный сид (при равенстве —
  меньший номер); unparsed-сид «не мнение»: раунд читается по
  распарсенным, и только all-unparsed читается как unparsed.
- `weblate/trans/models/judge.py:1250` `collegium_severity` — ниже
  critical строжайший побеждает; critical при споре второго
  распарсенного сида читается как major (режим
  `JUDGE_CONSENSUS_REJECT`).
- `weblate/trans/models/judge.py:1310` `has_complete_current_evidence` —
  approval требует распарсенный вердикт от **каждого** сконфигурированного
  сида: unparsed seat 1 блокирует approve, даже если seat 2 всё поймал.

Конфигурация сидов читается из Django settings через
`resolve_judge_seat_profile` (`weblate/trans/judge.py:457`):
`JUDGE_MODEL_SEAT_{1,2}`, `JUDGE_REASONING_EFFORT_SEAT_{1,2}`,
`JUDGE_BATCH_SIZE_SEAT_{1,2}`, `JUDGE_STREAM_*`, `JUDGE_RESPONSE_FORMAT_*`,
`JUDGE_REQUEST_DEADLINE` и т.д. (per-seat значение `"inherit"` откатывается
к общему). Reasoning-контроль для LiteLLM — закрытое множество
`_LITELLM_REASONING_VALUES` (`weblate/trans/judge.py:59`):
`""`, `thinking.disabled`, `enable_thinking=false`,
`extra_body.enable_thinking=false`. Payload строит
`_reasoning_payload` (`weblate/trans/judge.py:1640`); рабочее значение
для Qwen — `extra_body.enable_thinking=false` (прокси форвардит
`extra_body` verbatim; top-level поля он либо режектит, либо молча
ест — см. `docs/product/measurements/2026-09-03-judge-thinking-passthrough.md`).
**Числового reasoning budget у LiteLLM нет** — не имитировать через
`max_tokens` (правило итерации 1).

Промпт — strict `json_schema` (`response_format=json_schema`), стриминг с
`stream_options.include_usage`, батчинг нескольких строк в один POST
(сегменты с ID, ответ должен вернуть вердикты на каждый сегмент; чужой/
неверный/неполный набор ID = `invalid-segment` → unparsed вердикт).

Текущая production-пара (контроль во всех эксперимента): seat 1
`deepseek-v4-pro` (reasoning on, контроль не отправляется, batch 2),
seat 2 `atlas/qwen3.8-max` (`extra_body.enable_thinking=false`, batch 1).
Прокси: `https://hcbifrost.herocraft.com/litellm/v1`, ключ в env
контейнера `WEBLATE_JUDGE_API_KEY`.

## 2. Итерация 1: что сделано (полная история)

План `2026-09-17-judge-batching-and-latency-experiments.md` — batching
(эксперимент A) и замена модели DeepSeek (эксперимент B), затем held-out
подтверждение (Task 4). Исполнено автономно с заранее выданного
разрешения владельца; бюджет: $5/слот cap, 50 attempts/слот, 10 мин/слот.
Фактический расход за всю итерацию 1 — порядка $0.2, включая один orphan
POST после краха Docker.

Инструмент: `analysis/probes/judge-cost-latency-ab.py` (режимы
`--prepare/--dry-run/--execute/--summarize`). Возможности: манифест с
sha256-приколотым корпусом и budget/prices/gates (исполнение отказывает
без заполненных бюджета и цен); per-arm overrides `seat_2_batch_size`,
`seat_1_model`, `seat_1_reasoning` (последние два добавлены коммитом
`2c55d2f9`); заморозка резолвнутых профилей per-arm с отказом при любом
дрейфе (fingerprint/alias_revision/upstream_model/batch/reasoning/
response_format); сброс `JudgeAdaptiveState` на каждый слот (иначе
состояние (endpoint, model, seat) протекает между слотами и ломает
ширину — регресс-тест есть); budget guard вокруг POST (резерв по ценам
манифеста до отправки); запрет dev-docker/production settings, fallback
эндпоинта и deferral; чередование плеч внутри повтора; offline
`--summarize` с качеством/гейтами/распределением ширин/токенами.
Тесты: `weblate/trans/tests/test_judge_cost_latency_ab.py`, 21, зелёные
(`./rundev.sh test weblate/trans/tests/test_judge_cost_latency_ab.py -n 0 --no-cov`).

Эксперименты (артефакты в gitignored `analysis/data/judge-cost-latency-ab/<id>/`:
manifest, journal.jsonl, results/block-*.json, summary.json):

- `real-ab` — smoke, **зконфужен** протечкой adaptive state (seat 2 шёл
  шириной 1/2 вместо 2/5). Причина найдена, пофикшена per-slot сбросом
  (коммит `56009cb3`), оставлен как доказательство необходимости гвардии.
- `real-ab2` — валидный smoke (3 строки cs): ширины соблюдены.
- `real-main` — A: 3 повтора × A0/A2/A5 (Qwen batch 1/2/5). POST
  15/12/9; wall clock 79.2/90.8/81.9 с (A2 +14.6% — за гейтом 5%; A5
  +3.4% — в шуме); расчётная стоимость $0.0478/$0.0389/$0.0276 (−18.5%/
  −42.2%); качество детерминированно одинаковое. Экономия — в
  prompt-токенах Qwen (15879→5499): префикс амортизируется шириной.
- `real-b-smoke` — скрининг двух кандидатов с thinking off:
  `atlas/deepseek-v4-flash-0731` (parse 3/3, reasoning 0, elapsed
  2503–2765 мс) и `deepseek-v4-pro` (parse 3/3, reasoning 0, 3418–4752
  мс). Вердикты обоих = baseline. Выбран flash как самый быстрый.
- `real-main-b` — B: 3 повтора × B0 (контроль pro reasoning on) / B1
  (flash + thinking off), Qwen batch 5. Wall clock пары 79.7 → 27.7 с
  (−65.3%); стоимость $0.0196 → $0.0159 (−19.2%); completion 4744 → 1672,
  reasoning 3084 → 0; вердикты идентичны контролю, unparsed 0. Все гейты
  B пройдены. **pro + thinking off на held-out не проверялся** — в
  основной B пошёл только flash.
- `real-holdout` — Task 4: новый de-корпус (4 юнита: 2 корректных
  немецких перевода `pass`, 2 посеянных major-дефекта; labels_origin
  `agent-seeded-controlled`), 3 повтора × H0 (исходная пара) / H1
  (связка A5+B1), один эксперимент. **Reject**: major recall 0/2 против
  2/2 (гейт −2 п.п.), terminal unparsed 4 (+16.7 п.п. при допуске +1).
  Скорость (−78.8%) и стоимость (−59.2%) свои гейты прошли.

Коммиты итерации 1: `56009cb3` (per-slot adaptive reset), `37171ff5`
(запись A), `2c55d2f9` (per-arm seat-1 overrides + восстанавливающий
context manager + тесты), `97de5641` (запись B), `5358cd1c` (отчёт
holdout + статус плана).

## 3. Установленные факты (с путями к артефактам)

1. **Задержка пары упирается в seat 1.** Сиды параллельны; у
   `deepseek-v4-pro` reasoning on p95 одного запроса до 70 с
   (`real-holdout` H0), ~80% его completion-токенов — reasoning. Seat 2
   (Qwen) стабильно 2–7 с.
2. **Отключение reasoning — главный ускоритель.** pro +
   `extra_body.enable_thinking=false`: 3.4–4.8 с, parse 3/3,
   reasoning-токены 0 в usage — контроль реально доходит до upstream
   (`real-b-smoke`, слот s002). На held-out не проверялся (см. выше).
3. **flash + thinking off — reject** (`real-holdout`): 2 повтора из 3
   `invalid-segment` на батче из двух дефектных строк; в распарсенном
   повторе пропущен subtler-дефект (выброшенная ссылка: none у seat 1
   при major у seat 2). Грубый дефект (выдуманное содержание) ловил
   (critical).
4. **flash + thinking ON не измерялся никогда.**
5. **Qwen batch 5 (seat 2)**: −42% стоимости (`real-main`), качество
   держалось дважды — `real-main` (вердикты идентичны A0) и
   `real-holdout` (оба дефекта во всех повторах, 0 false flags, ширина
   фактически 4 из 5 из-за размера корпуса). Wall clock пары почти не
   меняет (ждём seat 1) — это кандидат на стоимость, не на скорость.
6. **Продакшн-семантика пары мягче гейта пробы.** Гейт пробы:
   «unparsed сид = нет покрытия пары» (консервативно, preregistered).
   Продакшн `collegium_verdict` читает раунд по распарсенным: под
   продакшн-семантикой H1 ловил бы оба дефекта через seat 2 во всех
   повторах. Реальная цена unparsed seat 1 в продакшне: unparsed-retry
   раунды (латентность/деньги — `judge_loop.py:1479`), блокировка
   approval (`has_complete_current_evidence`), потеря второго
   независимого сида. **Reject верен; но будущие сравнения должны
   публиковать обе агрегации** (решающий гейт — консервативная,
   вторичная метрика — collegium).
7. **Методологический урок:** 3-строчный smoke скрыл schema-нестабильность
   flash. Parse-stability стресс на десятках строк × повторы обязан
   предшествовать платному сравнению качества.
8. **Сожжённые для подбора корпуса** (не переиспользовать):
   `analysis/data/judge-cost-latency-ab/real-corpus.json` (cs, задачи
   1–3) и `analysis/data/judge-cost-latency-ab/holdout-corpus.json`
   (de, Task 4).

## 4. Операционная среда запуска (для новой сессии)

Репозиторий: HCGameLoc (`v9833078908/weblate-HC`), worktree — текущий.
Dev-стек Docker: контейнер `dev-docker-weblate-1` (web на
`localhost:3001`), QA Postgres опубликован на **5434** (не 5433),
база/юзер/пароль `weblate`. Команды тестов — через `./rundev.sh test …`
или host-side `uv run pytest` с `DJANGO_SETTINGS_MODULE=weblate.settings_test`
и `CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 …`.

Платный запуск слота (паттерн обёртки; временные скрипты итерации 1
удалены — восстановить по этому описанию):

- Процесс с `DJANGO_SETTINGS_MODULE=weblate.settings_test`,
  `CI_DB_HOST=127.0.0.1`, `CI_DB_PORT=5434`, `CI_DB_NAME/USER/PASSWORD=weblate`.
- `django.setup()`, затем зеркалить настройки судьи из контейнера:
  `JUDGE_ENABLED=True`, `JUDGE_API_KEY` = вывод
  `docker exec dev-docker-weblate-1 printenv WEBLATE_JUDGE_API_KEY`
  (только в память процесса; **никогда не печатать/писать в файлы**),
  `JUDGE_BASE_URL=https://hcbifrost.herocraft.com/litellm/v1`,
  `JUDGE_MODEL_SEAT_1=deepseek-v4-pro`,
  `JUDGE_MODEL_SEAT_2=atlas/qwen3.8-max`, `JUDGE_BATCH_SIZE=2`,
  `JUDGE_BATCH_SIZE_SEAT_1/2="inherit"` (arm override применит своё),
  `JUDGE_STREAM_SEAT_1/2=True`, `JUDGE_REASONING_EFFORT_SEAT_1=""`,
  `JUDGE_REASONING_EFFORT_SEAT_2="extra_body.enable_thinking=false"`,
  `JUDGE_RESPONSE_FORMAT_SEAT_1/2="json_schema"`,
  `JUDGE_REQUEST_DEADLINE=120.0`, `JUDGE_REQUEST_SLEEP=0`,
  `JUDGE_DEFERRAL_ENABLED=False`, `JUDGE_FALLBACK_*=""` (пусто — гвардия
  требует). Retries/unparsed-rounds — производственные дефолты.
- Импортировать probe через `importlib.util.spec_from_file_location`,
  вызвать `cmd_execute(Namespace(manifest=AB_MANIFEST, slot=sNNN,
  resume=False, allow_out_of_order=False))` c env `AB_MANIFEST=<путь>`.
- Слоты исполнять строго по расписанию манифеста подряд (нарушение
  порядка = отказ без `--allow-out-of-order`).

Операционные заметки:

- **Транзиентный дрейф alias revision** LiteLLM (наблюдался 3 раза за
  итерацию 1, в т.ч. дважды в `real-main`): прокси иногда отдаёт другую
  ревизию `deepseek-v4-pro` (`e412036b…` → `c0801b6a…`). Гвардия
  заморозки отказывает **до любого POST** ($0); повторный запуск того же
  слота обычно чист. Не отключать гвардию.
- **Крах Docker-демона** workstation (1 раз, середина `real-holdout`
  s003): один POST успел уйти (≈$0.0019, измерен по usage), слот
  перезапущен с нуля после восстановления. Если БД умирает в середине
  слота, journal теряет `slot_interrupted` — сверять выжившие POST с
  `JudgeRequestAttempt` по времени.
- **Цены:** прокси per-model ставки не публикует (`/model/info` всё 0);
  ставки из
  `docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/proxy-rate-metadata.json`
  (pro: 6.19962e-7 / 7.89815e-8 / 1.239924e-6; qwen3.8-max: 2e-6 /
  2.5e-7 / 6e-6 — input/cache-read/output за токен). Для flash-вариантов
  ставки нет: консервативная верхняя граница — ставка pro, фиксировать в
  `prices_source` манифеста.
- **Корпуса/артефакты не коммитить** (gitignored), в docs — только
  обезличенное; ключи только в env.

## 5. Цель и границы итерации 2

- Снизить wall clock пары судьи (гейт −20% как в итерации 1; амбиция
  −50%+ при pro+off) без провала гейтов качества; стоимость без роста
  (+5% макс, ожидается экономия).
- Варьируется только профиль seat 1 (модель и/или reasoning-control) и,
  на финальной связке, batch seat 2. Продуктовые контракты не меняем:
  никаких новых reasoning-полей, имитации budget через `max_tokens`,
  изменений `judge_loop`/collegium-политики без отдельного согласования.
- Rollout вне плана: отдельно canary, загрузка конфигурации workers,
  инвалидация verdict cache при смене profile (первая переоценка старого
  scope дороже steady-state) — не инициировать автоматически.

## 6. Кандидаты

- **K1 (приоритет):** `deepseek-v4-pro` +
  `extra_body.enable_thinking=false`. Однофакторное изменение той же
  модели; лучший неподтверждённый профиль (факт 2).
- **K2 (опционально, по решению владельца):**
  `atlas/deepseek-v4-flash-0731`, reasoning on (контроль не
  отправляется). Быстрый тир с сохранённым reasoning; не измерялся
  никогда (факт 4).
- Контроль во всех сравнениях — исходная пара (pro reasoning on + Qwen
  batch 1).

## 7. Задачи

### Задача 1. Корпус для parse-stability стресса (офлайн)

Новый корпус 24–32 строки, **не** из сожжённых: несколько языков/страт,
уникальные family на запись. Разметка не оценивается — достаточно
технически валидных меток (все `pass` или смесь), `labels_origin`
пометить явно (например `stability-only`). Сборка по паттерну итерации
1: юниты QA DB перевести в `STATE_TRANSLATED`, target синхронизировать из
БД (`join_plural(unit.get_target_plurals())`), unit_id реальные.

Проверка: `--prepare` регистрирует эксперимент, `--dry-run` сходится по
арифметике POST.

### Задача 2. Parse-stability стресс (платно, дёшево)

Эксперимент `stability-k1k2` (или `stability-k1`, если утверждён только
K1): 2 плеча × 3 повтора, seat 2 зафиксирован одинаково в обоих плечах
(Qwen batch 5 — экономно; либо batch 1 — консервативно; на вывод о
seat 1 не влияет, зафиксировать выбор в манифесте).

Регистрация (пример):

    uv run python analysis/probes/judge-cost-latency-ab.py --prepare \
        --corpus analysis/data/judge-cost-latency-ab/<новый-корпус>.json \
        --experiment-id stability-k1k2 \
        --repeats 3 --held-out-fraction 0.0 \
        --seat-1-model deepseek-v4-pro \
        --seat-2-model atlas/qwen3.8-max \
        --arms '{"K1": {"title": "pro + thinking off", "overrides": {"seat_2_batch_size": 5, "seat_1_model": "deepseek-v4-pro", "seat_1_reasoning": "extra_body.enable_thinking=false"}}, "K2": {"title": "flash + thinking on", "overrides": {"seat_2_batch_size": 5, "seat_1_model": "atlas/deepseek-v4-flash-0731"}}}'

Затем заполнить manifest (prices/prices_source/budget/est_tokens/gates,
`control_arm: K1` — гейты качества здесь не решают), `--dry-run`,
платные слоты обёрткой, `--summarize`.

**Гейт: 0 terminal unparsed после ретраев на кандидата** — иначе
кандидат отбрасывается до платного сравнения качества. Попутно
зафиксировать: p50/p95 elapsed по seat 1, reasoning-токены в usage
(=0 для K1), распределение ширин.

### Задача 3. Новый held-out корпус качества (решение владельца)

- **(a) QA-fixture контролируемый** — как de-корпус итерации 1, но
  больше и на нескольких языках фикстур: быстрый, автономный,
  agent-seeded разметка, слабая внешняя валидность (честная пометка в
  отчёте). Годится, чтобы отсечь неработающее до обращения к людям.
- **(b) Production-derived с human labels** по
  en/fr/ja/ko/zh_Hans/zh_Hant — требование исходного плана, так и не
  выполненное: нужен владелец корпусов, люди на разметку **до просмотра
  ответов плеч**, правила закрытого контента (в docs не переносить).
  Единственный валидный путь к production-adopt.

Рекомендация: (b) если цель — adopt; (a) если цель — отсев.

### Задача 4. Основное сравнение качества (платно)

Один эксперимент, 3 повтора, плечи чередуются: контроль (исходная пара)
против выживших по задаче 2 кандидатов (оба или один). Оба сида
реальные, verdict cache off (дефолт runner'а), batch seat 1 = 2 везде.

Гейты качества preregistered (унаследованы): major recall −2 п.п. макс,
false flags +3 п.п. макс, terminal unparsed +1 п.п. макс, 0 новых
подтверждённых critical miss. **Гейт-политика агрегации пары —
утвердить владельцу заранее:** консервативная (решающий гейт) +
collegium-семантика (обязательная вторичная метрика в отчёте);
фактически runner сегодня считает только консервативную — collegium
числа рассчитываются офлайн из block-файлов (вердикты обоих сидов там
есть; правило `collegium_verdict` воспроизводимо в 10 строк кода).

### Задача 5. Финальная связка (платно)

Прошедший по качеству профиль seat 1 + Qwen batch 5 на seat 2 против
исходной пары, 3 повтора, **ещё не использованный** срез held-out.
Гейты: wall clock пары −20% (амбициозно −50%), стоимость +5% макс,
качество как в задаче 4. Внедряемой считается только связка, измеренная
целиком в одном эксперименте; сложение результатов отдельных
экспериментов запрещено (правило итерации 1).

### Задача 6. Отчёт и решение

Датированный отчёт в `docs/product/measurements/`: точные конфигурации,
хэши (corpus sha256, fingerprints заморозок, коммит кода), offline-команда
сводки, сравнение с исходной парой, обе агрегации качества, поязыковые
размеры, интервалы, отклонения протокола, unknown cost. Допустим
inconclusive с явной причиной. Обновить статус этого плана. Commit/push
только обезличенного; временные скрипты удалить, runner и манифесты
оставить.

## 8. Открытые решения (утвердить до платных стадий)

1. Бюджет: численные limits (money cap, attempts, wall clock на слот и
   на эксперимент), provider-side quota или явное принятие остаточного
   риска unknown charges.
2. Корпус задачи 3: вариант (a) или (b); для (b) — кто размечает и
   доступ к корпусам.
3. Кандидаты: только K1 или K1+K2.
4. Гейт-политика агрегации пары (задача 4): подтвердить «консервативная
   решает + collegium публикуется».
5. Batch seat 2 в стрессе задачи 2 (5 или 1).

## 9. Правила, унаследованные от итерации 1

- Human labels до просмотра ответов плеч; агентская разметка — только с
  явной пометкой `labels_origin` и оговоркой в отчёте.
- Не подбирать следующий вариант на корпусе, на котором прошлый не прошёл.
- Никаких скрытых изменений продукта в probe; новое поле контракта —
  отдельное согласование и новый baseline.
- Commit/push только обезличенных артефактов; корпуса и результаты — в
  gitignored `analysis/data/`; ключи только в env процесса.

## 10. Статус задач 1–2 (2026-09-17)

Решения владельца (см. статус в шапке): бюджет — лимиты итерации 1;
кандидат — только K1; batch seat 2 — 5; корпус задачи 3 — вариант (a).
Открытым остаётся пункт 4 (гейт-политика) — подтвердить до регистрации
эксперимента задачи 4.

**Задача 1 исполнена.** Корпус
`analysis/data/judge-cost-latency-ab/stability-corpus.json` (gitignored):
30 записей, 30 уникальных семейств, 6 языков (es, pt, ja, zh_Hans, tr, fr),
4 проекта, 2 направления (en→X, ru→X), страты ui/long/ambiguous/terminology,
Unity-разметка и TBX-термины включены; `labels_origin: stability-only`;
юниты QA-базы в STATE_TRANSLATED, target синхронизирован из БД, база не
мутирована. Сожжённые корпуса не задействованы.

**Задача 2 исполнена — гейт пройден.** Полный отчёт:
`docs/product/measurements/2026-09-17-judge-seat1-parse-stability.md`.
Эксперимент `stability-k1b` (1 плечо K1 × 3 повтора × 7 групп, 21 слот):
69 POST, **0 terminal unparsed, 0 retries, 0 unmetered**, reasoning-токены 0;
seat 1 batch-2: p50 3.6 с / p95 6.9 с (контроль с reasoning on: p95 до 70 с);
стоимость $0.13. K1 допущен к задаче 4. Сравнение с итерацией 1 — в отчёте.

Отклонения (детали в отчёте):

- **Фикс runner'а:** `load_slot_units` не скоупил юниты по группе слота —
  первый слот `stability-k1` прогнал весь корпус смешанными по переводам
  батчами (21 POST, $0.0578, 0 unparsed; слот исключён из gate-оценки,
  эксперимент перерегистрирован как `stability-k1b`). Фикс: фильтр по
  группе + assertion одной переводческой идентичности; регрессионный тест
  `test_slot_loads_only_its_own_language_group`, suite 22/22 зелёные.
- **Дрейф alias revision** плотнее, чем в итерации 1 (волны на ~половине
  слотов, включая 3 подряд); каждый отказ до POST, $0, ретраи чистые.

## 11. Статус задач 3–6 (2026-09-18)

Полный отчёт с таблицами и LQA-анализом:
`docs/product/measurements/2026-09-18-judge-seat1-quality-and-bundle.md`.

**Задача 3 исполнена.** Сформированы два независимых QA-fixture корпуса
(вариант (a), честная пометка `labels_origin: agent-seeded-controlled`):

- `analysis/data/judge-cost-latency-ab/quality-corpus-t4.json`: 16 записей
  (8 clean, 8 defects), 4 языка (es, fr, ja, pt), 4 проекта. Использован
  в задаче 4.
- `analysis/data/judge-cost-latency-ab/quality-corpus-t5.json`: 8 записей
  (4 clean, 4 defects), 4 языка. Зарезервирован и использован исключительно
  как held-out срез в задаче 5.

**Задача 4 исполнена (сравнение качества K1 против C0).**
Эксперимент `quality-k1` (24 слота, 3 повтора × 4 группы × 2 плеча):

- Скорость: wall clock сократился на **70.3%** (674.4 с → 200.5 с),
  completion-токены на **77.2%**, reasoning-токены устранены (**25 709 → 0**),
  HTTP elapsed p95 рухнул с **42.5 с до 6.0 с** (−85.9%).
- Полнота (Recall): **100% на обоих плечах** (24/24 по сумме 3 повторов).
- Ложные флаги: **5 из 24 у C0 vs 5 из 24 у K1** (абсолютное равенство).
- Гейт probe: на последнем повторе у K1 2 флага против 1 у C0 (разница
  в 1 строку покупки на ja), из-за чего на малой выборке N=8 clean бутстрап
  high95 дал 37.5% (порог 15.0%) → формальный вердикт probe `fail`.
  Collegium-метрика показала строго одинаковое число ложных флагов.

**Задача 5 исполнена (финальная связка B1 против C0).**
Эксперимент `bundle-k1` (24 слота, held-out срез T5): связка DeepSeek Pro
(thinking off) + Qwen 3.8 Max (batch 5) против исходной пары:

- Скорость пары: wall clock сократился на **81.1%** (365.2 с → 69.0 с —
  **1.15 мин вместо 6.1 мин**), амбициозный гейт плана (>=50%) перевыполнен.
- Сетевые POST: сокращение на **33.3%** (36 → 24 POST).
- Prompt-токены: сокращение на **30.4%** (67 469 → 46 976).
- HTTP elapsed p95: **42.7 с → 8.1 с** (−81.0%).
- Полнота (Recall): **100% на обоих плечах** (12/12).
- Ложные флаги: **5 из 12 у C0 vs 5 из 12 у B1** (абсолютное равенство).

**Отклонения и исправления рантайма:**

- **Дрейф ревизий прокси:** выявлено, что балансировщик Bifrost возвращает
  две версии хэша алиаса `deepseek-v4-pro` (`c0801b6a` и `e412036b`) из-за
  8 опциональных null-полей в схеме сериализации LiteLLM. Апстрим-модель
  и провайдер идентичны (`openai/deepseek-ai/deepseek-v4-pro` на AtlasCloud).
  В `run-paid-slot.py` добавлена нормализация `_DRIFT_ALIAS_MAPPING`,
  устранившая ложные отказы балансировщика.

**Задача 6 исполнена.** Оформлен датированный отчёт
`docs/product/measurements/2026-09-18-judge-seat1-quality-and-bundle.md`,
план актуализирован. Временные шелл-скрипты в `/tmp/` очищены.
План итерации 2 полностью завершён.
