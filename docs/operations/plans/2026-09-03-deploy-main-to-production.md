# Deploy runbook 2026-09-03: main (4ed812c) -> production

Подготовлено 2026-09-03. Сам деплой - по отдельной команде; каждая прод-фаза
требует отдельного одобрения по AGENTS.md. Документ - подготовка и чек-лист,
никакие прод-изменения здесь не выполнены и не одобрены.

## Состояние

| Факт | Значение | Проверено |
|---|---|---|
| Прод checkout | `931de81` | `git rev-parse HEAD` на VPS |
| Прод образ | `53f2ac1` (label revision) | `docker inspect hcgameloc:latest` |
| К выкатке | `4ed812c` (= origin/main) | ветка чистая, запушена |
| Коммитов впереди | 68 | `git log 931de81..origin/main` |
| Миграций к применению | 6: `0113`..`0118` (trans) | прод на `0112` |
| Контейнер прода | `hcgameloc-weblate-1` Up, healthy | `docker ps` |

Применятся автоматически entrypoint'ом при старте нового контейнера:
`0113_llm_usage_scope`, `0114_judge_request_invalid_kind`,
`0115_judge_run_unit_refused_outcome`, `0116_judge_deferral_closed_retention`,
`0117_judge_verdict_provider`, `0118_judge_run_unit_candidate_stored`.
Все шесть аддитивные (AddField/AlterField с дефолтом/blank) - схема впереди
безопасна для отката кода на предыдущий образ.

## Что едет (68 коммитов, три линии)

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
  физически неактивен до фазы 4.
- Очередь отложек пуста (`JudgeDeferral` незакрытых: 0).
- Бэклог ложных refused-вердиктов: **100** (`unparsed=True` + HTTP 400/401).
- `.env` правок для фазы 1 не требует; новые ключи в
  `deploy/environment.example` информационные (дефолты пустые/выключенные).
- Замечание: `WEBLATE_REMOVE_CHECK` в продовом `.env` не содержит
  `weblate.checks.consistency.TranslatedCheck` (environment.example обновлён
  коммитом `411fc54`). Отдельная правка `.env`, только по одобрению.

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
Приёмка: `DEPLOY-OK`; image revision == `4ed812c`;
`weblate showmigrations trans` - `[X]` на `0113`..`0118`; логин 200.

### Фаза 2. Smoke атрибуции (гейт варианта A)

После первого нового LLM-батча:

```sh
docker compose exec -T weblate weblate llm_usage_report --summary --days 1
```

Приёмка: `priced_complete=yes`, `attribution_complete=yes`. Иначе - назвать
строки и причину до попадания суммы в отчёт.

### Фаза 3. Канарейка "рефактор ничего не изменил"

```sh
docker compose exec -T weblate env JUDGE_CANARY_TRANSLATION_ID=<id> \
  weblate shell -c "exec(open('/app/src/analysis/probes/judge-rollout-canary.py').read())"
```

Компонент с уже распарсенными вердиктами; read-only
(`writable_ids=set()`, `candidate_severities=()` - обязательно, иначе покупает
MT). Приёмка: fallback не настроен по `judge_configuration_snapshot()`, ноль
строк `judge_provider="openrouter"`, ноль отказов.

### Фаза 4. Историческая чистка refused-вердиктов (деструктивная, guarded)

```sh
weblate judge_close_refused_verdicts --expected-count 0            # dry-run: снять реальный total
weblate judge_close_refused_verdicts --expected-count <N>          # повторный dry-run: сверка
weblate judge_close_refused_verdicts --expected-count <N> --confirm
```

Ожидаемый кандидат на 2026-09-03: 100 (замер выше). Приёмка: ровно N вердиктов
удалено, run-unit строки реклассифицированы в `refused`, attempt ledger цел.
Mismatch = автоматический abort.

### Фаза 5. Настройка fallback (опционально, отдельное одобрение)

Восемь `WEBLATE_JUDGE_FALLBACK_*` в `.env` (историческая пара
`deepseek/deepseek-v4-pro` + `qwen/qwen3-235b-a22b-2507`, ключ из бэкапа
канарейки), пересоздание контейнера, повтор канарейки: ноль
`judge_provider="openrouter"` (настроен и простаивает). Детали и стоп-условия:
`docs/operations/plans/2026-09-02-judge-fallback-and-triage-rollout.md`
(фазы 4-6).

### Фаза 6. Backfill кандидатов для бэклога (опционально, тратит деньги)

```sh
weblate judge_backfill_candidates <project>/<component>            # dry-run классификация
weblate judge_backfill_candidates <project>/<component> --write --user <u> --limit <n>
```

Только явный scope (`--all` отвергается). Сначала dry-run, потом write с
лимитом.

### Фаза 7. Очередь отложек - НЕ в этой выкатке

`WEBLATE_JUDGE_DEFERRAL_ENABLED=1` - Task 7 плана
`docs/llm-first/plans/2026-09-01-03-judge-zero-unparsed.md`: dev-lifecycle,
scheduler proof, controlled drain (два отдельных одобрения). Не включать.

## Откат

- **Код**: предыдущий образ (`53f2ac1`); схема впереди безопасна.
- **Fallback**: сначала очистить все восемь `WEBLATE_JUDGE_FALLBACK_*`, потом
  полный primary-профиль, пересоздать контейнер (валидатор отвергает равенство
  base URL, очистка обязана идти первой).
- **Отложки** (если когда-либо включены): `JUDGE_DEFERRAL_OPERATOR_STOPPED=1`
  - recreate - блокирует платные drain-вызовы, строки сохраняются.

## Стоп-условия (сводные)

Неизвестный failure kind; fallback-попытка вне `_FAILOVER_FAILURE_KINDS`;
401/403, породивший >1 fallback-вызова; >50% батчей сида в fallback; refusal в
healthy-контроле; cleanup-кандидат вне `unparsed=True`+HTTP 400/401;
expected-count mismatch; вердикт, привязанный к refused-попытке; возвращение
~30-секундного reset на первом байте.
