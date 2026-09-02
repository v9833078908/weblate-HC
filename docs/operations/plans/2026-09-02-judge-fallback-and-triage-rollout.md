# Выкатка судейского fallback и producer-triage

Дата: 2026-09-02. Статус: подготовлен, **ожидает одобрения на выкатку**. Ни
один шаг ниже не выполнен. Все числа получены локально и read-only проверками
прода; в продовые контейнеры не заходили, management-команды на проде не
запускались.

План сводит гейты двух планов в один порядок:

- `docs/llm-first/plans/2026-09-01-02-judge-openrouter-availability-fallback.md`
- `docs/llm-first/plans/2026-09-01-judge-producer-triage-embed.md`
- `docs/llm-first/plans/2026-08-31-llm-usage-cost-attribution.md` (предпосылка)

## Что установлено

Образ прода собран из `53f2ac1` (2026-09-01 09:05). Checkout
`/srv/hcgameloc` стоит на `931de81`, `main` - на `7f62a51`.

- От образа до `main` - **94 коммита**; от checkout до `main` - 47.
- Checkout опережает образ законно: `931de81` затронул только
  `deploy/environment.example` и документ, а такие файлы по логике
  `deploy/vps.sh` пересборку не вызывают. Живой `/srv/hcgameloc/deploy/.env`
  из шаблона не обновляется автоматически.
- В диапазоне 34 изменённых файла `weblate/**.py`, поэтому деплой пойдёт по
  ветке `action=build`.

Тесты на слитом `main`: **486 passed, 47 subtests, ноль падений** по всем семи
судейским файлам (`test_judge_client`, `test_judge_loop`, `test_judge_deferrals`,
`test_judge_persistence`, `checks/test_judge`, `test_judge_views`,
`test_judge_autotranslate`). Fallback и producer-triage проверены вместе, в
одном прогоне.

### Миграции

Шесть, все в `trans`, цепочка линейная, в других приложениях ничего:

```text
0113_llm_usage_scope                  атрибуция затрат
0114_judge_request_invalid_kind
0115_judge_run_unit_refused_outcome
0116_judge_deferral_closed_retention
0117_judge_verdict_provider           fallback
0118_judge_run_unit_candidate_stored  producer-triage
```

`0117` - `AddField(blank=True)`; проверено на живом Django этой ветки, что
`ALTER TABLE ADD COLUMN` подставляет пустую строку, backfill не нужен. `0118` -
`AlterField` только по списку choices. Остальные additive. Поэтому предыдущий
образ терпит схему, ушедшую вперёд, и откат кода безопасен.

### Порядок «миграция до кода» выполняется структурно

`deploy/vps.sh` не запускает `migrate` сам, а `deploy/docker-compose.yml`
объявляет ровно один сервис `weblate` без отдельного worker-контейнера.
Миграции идут в upstream-entrypoint `/app/bin/start` на каждом старте
(`deploy/Dockerfile:11-12`), до того как nginx, granian и Celery внутри того же
контейнера начнут обслуживать. Значит одно
`docker compose up -d --build weblate` уже соблюдает требование обоих планов:
окна «новый код на старой схеме» не существует, разносить миграцию отдельным
шагом не нужно.

### Окружение прода уже готово к шагу 1

Прочитаны только имена и булевы значения:

```text
WEBLATE_JUDGE_FALLBACK_*        отсутствуют полностью (0 строк)
WEBLATE_JUDGE_ENABLED=1
WEBLATE_JUDGE_MAY_APPROVE=0
WEBLATE_JUDGE_DEFERRAL_ENABLED=0
```

Следствия: fallback после выкатки будет выключен без правок; шаг 4 плана
fallback («очередь здесь не включаем») соблюдён автоматически; требование
канарейки `JUDGE_MAY_APPROVE=0` уже выполнено. Настройки удержания
(`*_RETENTION_DAYS`) в `.env` отсутствуют - применяются дефолты кода, 90 дней.

## Что изменит поведение прода без всякой настройки

Единственное неинертное изменение в этой выкатке - producer-triage, он ничем
не гейтится: `DEFAULT_CANDIDATE_SEVERITIES = (critical, major)`.

- **Сейчас:** любой REJECT/FLAG на записываемой строке машинно правится и
  правка **применяется**.
- **После:** авто-применение сжимается до случаев с активным `max-length`,
  остальное складывает кандидата на просмотр человеком. Это заявленная и
  принятая в плане triage регрессия: безнадзорное самоисцеление мажоров
  прекращается, мажор едет как translated с советующим `judge-flag`.
- **Побочный расход, в планах не названный:** `needs_candidate` намеренно
  игнорирует `writable_ids` и остаток попыток
  (`weblate/trans/judge_loop.py:638-642`). Поэтому read-only прогон, который
  раньше не делал ни одного MT-вызова на правку, теперь вызывает
  `repair_targets` и покупает MT-батч за раунд на каждый нерешённый critical и
  major. Обе половины закреплены существующими тестами:
  `writable_ids=set()` с дефолтными severity даёт `repair_mock.call_count == 1`
  (`weblate/trans/tests/test_judge_loop.py:1443-1445`), а пустой набор -
  `repair_mock.assert_not_called()` и ноль кандидатов (`:1465-1482`).

Правки кода это не требует. Требует одного аргумента в канарейке:
`candidate_severities=()`. Планы fallback обновлены соответственно.

## Открытое решение владельца: гейт атрибуции

Шаг 1 плана fallback требует: «сначала завершить выкатку атрибуции - `0113`
применена, все писатели на новом ledger, её scoped production smoke зелёный».
В проде `0113` не применена, и её миграция едет в том же образе, что и всё
остальное. Развести это можно двумя способами.

**Вариант A (рекомендую).** Один деплой применяет все шесть миграций и весь
код. Затем по порядку: smoke атрибуции (scoped `--summary --days 1`, приёмка
`priced_complete=yes` и `attribution_complete=yes`), и только после его
зелёного - канарейка шага 1 fallback. Смысл гейта сохраняется: fallback
остаётся **не настроенным** (проверено: ноль `FALLBACK`-переменных), поэтому
никакое fallback-поведение до шага 2 физически невозможно, а стоимость
переключений будет спорить уже по атрибутированному ledger. Меньше продовых
переходов.

**Вариант B.** Два деплоя: сначала промежуточный образ на коммите мерджа
атрибуции, его smoke, потом `main`. Соблюдает букву гейта, но выкатывает
промежуточный коммит, который как единица никем не тестировался, и удваивает
окна.

Дальнейшие фазы написаны под вариант A.

## Фазы

Каждая фаза - отдельное одобрение. Ничего не выполнять до ответа по гейту
атрибуции выше.

### Фаза 1. Выкатка образа, fallback выключен

Правок `.env` не требует.

```sh
./deploy/vps.sh deploy --build
```

Приёмка: `DEPLOY-OK`; `image revision` совпадает с выкатываемым коммитом;
шесть миграций применились без ошибки.

### Фаза 2. Smoke атрибуции

По `docs/llm-first/plans/2026-08-31-llm-usage-cost-attribution.md:2339-2356`,
после первого нового LLM-батча. Приёмка: `priced_complete=yes` и
`attribution_complete=yes`; иначе назвать конкретные строки и причину до того,
как сумма попадёт в отчёт. Ожидаемый временный эффект, не регрессия:
observed-cost preview в UI остаётся `available: false`, пока не накопится пять
priced-строк нового ledger на каждый
`(project_id_snapshot, service, model, operation)`.

### Фаза 3. Канарейка «рефактор ничего не изменил»

Через `run_judge_batch` с `writable_ids=set()` **и `candidate_severities=()`**.
Приёмка: fallback не настроен по `judge_configuration_snapshot()`, ноль строк
`judge_provider="openrouter"`, числа воспроизводят 2026-09-01.

### Фаза 4. Настройка fallback на исторический OpenRouter

Primary остаётся на LiteLLM. Восемь `WEBLATE_JUDGE_FALLBACK_*` прописываются в
`/srv/hcgameloc/deploy/.env`, контейнер **пересоздаётся** (environment
запекается при создании). Историческая пара проверена живьём 2026-09-02:
`deepseek/deepseek-v4-pro` + `qwen/qwen3-235b-a22b-2507` с ключом из бэкапа
канарейки, оба эндпоинта ответили 200 и распарсились.

Валидатор требует полноты и различности: base URL fallback не может равняться
primary после канонизации, а половинчатый набор падает до первого запроса.

Приёмка: `judge_configuration_snapshot()` показывает оба эндпоинта; канарейка
по-прежнему даёт ноль строк `judge_provider="openrouter"` - fallback настроен и
простаивает.

### Фаза 5. Ограниченная read-only канарейка

Второй компонент и языковая пара, не более 100 юнитов, `JUDGE_MAY_APPROVE=0`,
`writable_ids=set()`, `candidate_severities=()`. Приёмка: ноль терминальных
unparsed, ноль неожидаемых fallback-попыток, first-byte p95 под 20 с на сид,
ни одного запроса в пределах 25% от его дедлайна.

### Фаза 6. Наблюдение и замер

Три сигнала за полный продовый прогон: доля переключений на сид, время,
потраченное на неудачные primary-попытки (`JudgeRequestAttempt`), и scoped
judge-расход по сервисам (`LLMUsageLog`). Записать в датированный measurement.
Стоимость OpenRouter-fallback точна только при `priced_complete=yes` **и**
`attribution_complete=yes`; LiteLLM `cost_usd=None` и primary-запрос без тела
остаются «неизвестно», никогда не ноль.

## Риски и откат

- **Очередь отложек не включается этой выкаткой.** `DEFERRAL_ENABLED=0` в
  проде, менять нельзя: она относится к
  `docs/llm-first/plans/2026-09-01-03-judge-zero-unparsed.md`.
- **Откат кода** - предыдущий образ, схема впереди безопасна (см. выше).
- **Откат fallback** - это переключение двух эндпоинтов, а не одна настройка:
  сначала очистить все восемь `WEBLATE_JUDGE_FALLBACK_*`, затем поставить
  полный provider-семантичный профиль primary из записанных значений
  OpenRouter (base URL, ключ, оба сида по модели, reasoning effort и
  response format), пересоздать контейнер, убедиться, что
  `judge_configuration_snapshot()` не содержит fallback-эндпоинта, и прогнать
  ограниченную read-only смоку. Валидатор отвергает равенство base URL
  fallback и primary, поэтому очистка обязана идти первой.
- **Два секрета вместо одного** - ротация теперь покрывает и ключ fallback.

## Стоп-условия

Любой неизвестный failure kind; любая fallback-попытка на виде сбоя вне
`_FAILOVER_FAILURE_KINDS` (протокольный сбой или `http-other` 4xx); любой
`401`/`403`, породивший больше одного fallback-вызова; прогон, в котором больше
половины батчей одного сида ушли в fallback (primary нездоров); возвращение
~30-секундного сброса на первом байте из
`docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md`.
