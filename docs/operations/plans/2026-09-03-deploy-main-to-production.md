# Deploy runbook 2026-09-03: main -> production

Подготовлено 2026-09-03. Сам деплой - по отдельной команде; каждая прод-фаза
требует отдельного одобрения по AGENTS.md. Одноразовый чек-лист: после
выполнения деплоя файл удаляется (не переносится в archive).

## Состояние

| Факт | Значение | Проверено |
|---|---|---|
| Прод checkout | `931de81` | `git rev-parse HEAD` на VPS |
| Прод образ | `53f2ac1` (label revision) | `docker inspect hcgameloc:latest` |
| К выкатке | HEAD main на момент деплоя | ветка чистая, запушена |
| Коммитов впереди | ~70 и растёт с документацией | `git log 931de81..origin/main` |
| Миграций к применению | 6: `0113`..`0118` (trans) | прод на `0112` |
| Контейнер прода | `hcgameloc-weblate-1` Up, healthy | `docker ps` |

Применятся автоматически entrypoint'ом при старте нового контейнера:
`0113_llm_usage_scope`, `0114_judge_request_invalid_kind`,
`0115_judge_run_unit_refused_outcome`, `0116_judge_deferral_closed_retention`,
`0117_judge_verdict_provider`, `0118_judge_run_unit_candidate_stored`.
Все шесть аддитивные (AddField/AlterField с дефолтом/blank) - схема впереди
безопасна для отката кода на предыдущий образ.

## Что едет (три линии)

1. **Judge zero-unparsed** (Tasks 1-6, merge `c12e8c7`): fail-fast на refused
   HTTP (400/401/422 и др. -> `http-request-invalid`, без вердикта, без
   открытия availability-цепи), closed-ретеншн отложек, команда
   `judge_close_refused_verdicts`.
2. **OpenRouter availability fallback + producer triage** (merge `382fd51`,
   `e499e7e`): рефактор эндпоинтов, fallback-эндпоинт (по умолчанию
   отключён), `judge_provider` на вердикте, хранимые repair-кандидаты как
   suggestions, triage-карточка на странице перевода, одноклик-резолюция,
   команда `judge_backfill_candidates`.
3. **Producer editor Pareto** (merge `cb1b1fc` + `4ed812c`): упрощение
   страницы перевода (пагер, вкладки, спецсимволы, карточки), выпиливание
   `TranslatedCheck` для продюсеров, фикс API коммита explanation/flags,
   русская локаль карточки вердикта (33 новых msgstr, mo пересобран
   2026-09-03 09:59).

## Поведенческие изменения без всякой настройки

- **Producer triage не гейтится** (`DEFAULT_CANDIDATE_SEVERITIES=(critical,
  major)`): авто-применение машинных правок сжимается до активного
  `max-length`; остальные REJECT/FLAG складывают кандидата на просмотр
  человеком вместо безнадзорного самоисцеления. Заявленная и принятая в плане
  регрессия (`docs/operations/plans/2026-09-02-judge-fallback-and-triage-rollout.md`).
- **Refused-request fail-fast**: HTTP 4xx-отказ больше не пишет ложный
  unparsed-вердикт и не продолжает прогон.
- **Read-only прогоны теперь покупают MT** на нерешённые critical/major, если
  не передать `candidate_severities=()` - канарейка ниже это учитывает.
- **Интерфейс переводчика**: вкладки machinery/other-languages прямые,
  комментарии в More, карточки Glossary/String information всегда раскрыты,
  пагер previous/position/next, спецсимволы свёрнуты.

## Пререквизиты (проверены read-only 2026-09-03)

- `WEBLATE_JUDGE_ENABLED=1`, `WEBLATE_JUDGE_DEFERRAL_ENABLED=0` (не трогать:
  включение очереди - Task 7 плана zero-unparsed, отдельное одобрение).
- Ноль `WEBLATE_JUDGE_FALLBACK_*` в `/srv/hcgameloc/deploy/.env` - fallback
  физически неактивен до фазы 5.
- Очередь отложек пуста (`JudgeDeferral` незакрытых: 0).
- Бэклог ложных refused-вердиктов: **100** (`unparsed=True` + HTTP 400/401).
- `.env` правок для фазы 1 не требует; новые ключи в
  `deploy/environment.example` информационные (дефолты пустые/выключенные).
- Замечание: `WEBLATE_REMOVE_CHECK` в продовом `.env` не содержит
  `weblate.checks.consistency.TranslatedCheck` (environment.example обновлён
  коммитом `411fc54`). Отдельная правка `.env`, только по одобрению.
- Локальное дерево чистое (`git status --porcelain` пуст): nfg-артефакты
  миграции закоммичены, typos/reuse приведены в порядок - `deploy_stack`
  больше не блокируется.

## Тесты (хост, изолированная БД `weblate_deployready`, порт 5434)

- Точечные editor-тесты поверх правок `4ed812c`: **118 passed, 22 skipped**
  (`test_edit.py -k "tab or glossary_card or string_info or machinery"`).
- Полная judge-регрессия (12 файлов): **648 passed, 62 subtests passed**
  за 9:59 на `4ed812c`.

## Фазы (каждая - отдельное одобрение)

### Фаза 1. Деплой образа, fallback выключен

```sh
./deploy/vps.sh deploy --build
```

`--build` обязателен: менялись `weblate/`, шаблоны, статика, локаль.
Приёмка: `DEPLOY-OK`; image revision == выкаченному HEAD (показан в выводе
деплоя как `checkout:`); `weblate showmigrations trans` - `[X]` на
`0113`..`0118`; логин 200.

### Фаза 2. Канарейка "рефактор ничего не изменил" + генерация новых LLM-строк

```sh
docker compose exec -T weblate env JUDGE_CANARY_TRANSLATION_ID=<id> \
  weblate shell -c "exec(open('/app/src/analysis/probes/judge-rollout-canary.py').read())"
```

Компонент с уже распарсенными вердиктами; read-only
(`writable_ids=set()`, `candidate_severities=()` - обязательно, иначе покупает
MT). Этот прогон сам создаёт свежие `LLMUsageLog`-строки нового ledger
(миграция `0113`), которые нужны фазе 3. Приёмка: fallback не настроен по
`judge_configuration_snapshot()`, ноль строк `judge_provider="openrouter"`,
ноль отказов, числа воспроизводят 2026-09-01.

### Фаза 3. Smoke атрибуции LLM-расходов (включение llm costs)

Ledger пишется всеми writers сразу после деплоя, но новый scope (`0113`)
накапливается только на новых строках. После канарейки фазы 2:

```sh
docker compose exec -T weblate weblate llm_usage_report --summary --days 1
```

Приёмка: `priced_complete=yes`, `attribution_complete=yes` на свежих строках.
Иначе - назвать конкретные строки и причину до попадания суммы в отчёт.
Ожидаемый временный эффект, не регрессия: observed-cost preview в UI остаётся
`available: false`, пока не накопится пять priced-строк нового ledger на
каждый `(project_id_snapshot, service, model, operation)`.

### Фаза 4. Историческая чистка refused-вердиктов (деструктивная, guarded)

`--expected-count` обязателен при любом вызове (в dry-run не сверяется,
поэтому ведём его от замера 2026-09-03 и сверяем глазами с напечатанным
`total:`; при `--confirm` расхождение = автоматический abort):

```sh
weblate judge_close_refused_verdicts --expected-count 100            # dry-run: сверить total
weblate judge_close_refused_verdicts --expected-count <total> --confirm
```

Ожидаемый кандидат на 2026-09-03: 100 (замер выше). Приёмка: ровно N вердиктов
удалено, run-unit строки реклассифицированы в `refused`, attempt ledger цел.
Mismatch = автоматический abort.

### Фаза 5. Включение fallback на OpenRouter (обязательна, .env + recreate)

Primary остаётся на LiteLLM. Дописать в `/srv/hcgameloc/deploy/.env` (предварительно
`cp .env .env.bak-$(date +%Y%m%dT%H%M%SZ)-fallback`):

```sh
WEBLATE_JUDGE_FALLBACK_BASE_URL=https://openrouter.ai/api/v1
WEBLATE_JUDGE_FALLBACK_API_KEY=<openrouter-key>
WEBLATE_JUDGE_FALLBACK_MODEL_SEAT_1=deepseek/deepseek-v4-pro
WEBLATE_JUDGE_FALLBACK_MODEL_SEAT_2=qwen/qwen3-235b-a22b-2507
WEBLATE_JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1=
WEBLATE_JUDGE_FALLBACK_REASONING_EFFORT_SEAT_2=
WEBLATE_JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1=json_schema
WEBLATE_JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2=json_schema
```

Историческая пара проверена живьём 2026-09-02: оба эндпоинта ответили 200 и
распарсились. Ключ нужен OpenRouter-аккаунтовый (в продовом `.env` и бэкапах
его нет - в бэкапе канарейки лежит только loc-kit-ключ; валидатор требует
непустой `JUDGE_FALLBACK_API_KEY`, иначе `JudgeError` до первого запроса).
Контейнер **пересоздаётся** (`docker compose up -d weblate` из `deploy/`):
environment запекается при создании.

Приёмка: `judge_configuration_snapshot()` показывает оба эндпоинта; повтор
канарейки фазы 2 даёт ноль строк `judge_provider="openrouter"` - fallback
настроен и простаивает (срабатывает только при сбое primary). Детали,
наблюдение и стоп-условия:
`docs/operations/plans/2026-09-02-judge-fallback-and-triage-rollout.md`
(фазы 4-6).

### Фаза 6. Backfill кандидатов для бэклога (опционально, тратит деньги)

```sh
weblate judge_backfill_candidates <project>/<component>            # dry-run классификация
weblate judge_backfill_candidates <project>/<component> --write --user <u> --limit <n>
```

Только явный scope (`--all` отвергается). Сначала dry-run, потом write с
лимитом.

### Фаза 7. Финальная проверка включения

После всех фаз, одним заходом:

```sh
docker compose exec -T weblate weblate shell -c "
from weblate.trans.judge import judge_configuration_snapshot, judge_fallback_endpoint
from weblate.trans.models.judge import JudgeVerdict
snap = judge_configuration_snapshot()
print('judge enabled:', snap['enabled'])
print('fallback configured:', judge_fallback_endpoint() is not None)
print('openrouter-served verdicts:', JudgeVerdict.objects.filter(judge_provider='openrouter').count())
"
```

Приёмка: `judge enabled: True`; `fallback configured: True`; канарейка после
recreate по-прежнему даёт ноль openrouter-вердиктов (fallback в резерве, не в
работе); `llm_usage_report --summary --days 1` зелёный; triage-карточка
видна на странице перевода юнита с активным REJECT/FLAG (кандидат -
`Suggestion` со специальным автором, кнопки Use/Keep/Re-check); русская
локаль карточки на месте.

### Очередь отложек - НЕ в этой выкатке

`WEBLATE_JUDGE_DEFERRAL_ENABLED=1` - Task 7 плана
`docs/llm-first/plans/2026-09-01-03-judge-zero-unparsed.md`: dev-lifecycle,
scheduler proof, controlled drain (два отдельных одобрения). Не включать.

## Откат

- **Код**: предыдущий образ (`53f2ac1`); схема впереди безопасна.
- **Fallback**: очистить все восемь `WEBLATE_JUDGE_FALLBACK_*` в `.env` и
  пересоздать контейнер; primary (LiteLLM) при этом не меняется.
  Переключение primary на OpenRouter - отдельная операция: сначала очистка
  fallback-полей, потом полный provider-профиль primary (валидатор отвергает
  равенство base URL, очистка обязана идти первой).
- **Отложки** (если когда-либо включены): `JUDGE_DEFERRAL_OPERATOR_STOPPED=1`
  - recreate - блокирует платные drain-вызовы, строки сохраняются.

## Стоп-условия (сводные)

Неизвестный failure kind; fallback-попытка вне `_FAILOVER_FAILURE_KINDS`;
401/403, породивший >1 fallback-вызова; >50% батчей сида в fallback; refusal в
healthy-контроле; cleanup-кандидат вне `unparsed=True`+HTTP 400/401;
expected-count mismatch; вердикт, привязанный к refused-попытке; возвращение
~30-секундного reset на первом байте.
