# План: запасные модели судьи на том же корпоративном LiteLLM

> **Статус:** черновик, ждёт утверждения. Код не менялся.

## Цель

Судьи работают только через корпоративный LiteLLM
(`https://hcbifrost.herocraft.com/litellm/v1`). Когда основная модель места
недоступна, тот же батч один раз уходит в запасную модель **на том же
endpoint с тем же ключом**:

| Место | Основная (upstream) | Запасная (upstream) |
| --- | --- | --- |
| 1 | `atlas/deepseek-v4-pro-0813` (Atlas), `extra_body.thinking.disabled` | `atlas/deepseek-v4.1-flash` (Atlas), `extra_body.thinking.disabled` |
| 2 | `atlas/qwen3.8-max` (Atlas), `extra_body.enable_thinking=false` | `Kimi K3.1 Pro` (api.kimi.com) |

OpenRouter для судей не используется ни основным, ни запасным каналом, в
том числе через LiteLLM. Правило владельца: среди алиасов LiteLLM
предпочитать провайдера Atlas (`https://api.atlascloud.ai/v1`), а не
алиасы, которые сам LiteLLM отправляет в OpenRouter.

> LiteLLM `/model/info`, 2026-11-05: алиас `openrouter/deepseek-v4.1-flash`
> (бывший основной seat 1 до 05.11) ведёт на `https://openrouter.ai/api/v1`,
> то есть корпоративный ключ сам отправлял его в OpenRouter. Вместо него
> взят `atlas/deepseek-v4.1-flash`: 10/10 ответов с `extra_body.thinking`
> без размышлений.

## Состояние на 2026-11-05

- Production: основные `atlas/deepseek-v4-pro-0813` + `atlas/qwen3.8-max` на LiteLLM
  (хотфикс 05.11, см.
  `docs/operations/reports/2026-11-05-judge-corporate-only-hotfix.md`),
  все восемь `JUDGE_FALLBACK_*` пустые (`judge_fallback_endpoint() is None`).
  Резервные копии: `runtime.private.json.bak-20261105T145532Z-judge-corp` в
  `/srv/hcgameloc-releases/6b211b5a…/` и `settings-override.py.bak-*` в томе
  `hcgameloc_weblate-data`.
- `weblate/trans/judge.py:judge_fallback_endpoint` отклоняет fallback, чей
  канонический URL равен `JUDGE_BASE_URL`; судья тогда целиком становится
  «не настроен». Это и запрещает нужную конфигурацию.
- `_FAILOVER_FAILURE_KINDS` включает `http-auth`, исходя из того, что у
  fallback другой ключ у другого провайдера.
- `_request_batch` обнуляет `fallback_profile` для durable-запусков
  (`run is not None`): `ProducerRun` из UI fallback не получает вовсе.
  Production-вызовы `deepseek/deepseek-v4-pro` через OpenRouter (7212 за
  24.10–05.11) шли с non-durable пути.

## Что меняется

1. **Правило вместо «другой endpoint» — «другая пара endpoint+модель на
   каждом месте».** `judge_fallback_endpoint` разрешает
   `JUDGE_FALLBACK_BASE_URL == JUDGE_BASE_URL`;
   `_validate_fallback_configuration` отказывает, если для какого-то места
   запасная модель совпадает с основной на том же endpoint (с учётом
   `upstream_model` алиаса: два алиаса одной модели — тоже не fallback).
   Пустой `JUDGE_FALLBACK_API_KEY` при совпадающем URL наследует
   `JUDGE_API_KEY`, чтобы ключ не дублировался в env.
2. **Классы отказа для того же endpoint.** При совпадающем endpoint из
   failover исключаются `http-auth` (тот же ключ даст тот же 401/403) и
   ошибки, которые circuit breaker считает недоступностью всего endpoint
   (`transport` с отказом соединения). Остаются `deadline`,
   `http-rate-limit`, `http-server` — то, что у LiteLLM бывает на уровне
   отдельной deployment-модели. Точный список фиксируется тестами.
3. **Fallback для durable-запусков — отдельное решение (вне объёма, если не
   утверждено).** Без него запасные модели не помогут основному сценарию из
   UI. Причина запрета — сохранение полного тела до разбора и запрет на
   оплачиваемый fallback по частичному стриму; для `stream=False` его можно
   снять, записывая fallback-попытку как отдельный `JudgeRequestAttempt` с
   `role=fallback`. Требует отдельного утверждения как следующий инкремент.
4. **Запрет OpenRouter за LiteLLM.** `resolve_judge_alias` уже читает
   `/model/info`; судья отказывает в профиле (основном и запасном), если
   `litellm_params.api_base` алиаса — хост `openrouter.ai`. Без этого
   правило «судьи только по корпоративной подписке» держится лишь на выборе
   имени алиаса.
5. **Аудит.** `_fallback_configuration_snapshot` уже пишет
   `fallback_hostname`/`fallback_model`; добавить флаг
   `fallback_same_endpoint`, чтобы отчёты не принимали такую попытку за
   внешний провайдер. `LLMUsageLog.service` для неё остаётся `litellm`.
6. **Документация.** `docs/admin/config.rst` (`JUDGE_FALLBACK_BASE_URL`):
   заменить абзац «may not equal JUDGE_BASE_URL» на правило о паре
   endpoint+модель и наследование ключа; раздел об откате переписать.
   Запись в `docs/changes.rst`.
7. **Production-конфигурация (после выкладки, отдельное разрешение).** В
   `runtime.private.json` и `deploy/.env`:
   `JUDGE_FALLBACK_BASE_URL=https://hcbifrost.herocraft.com/litellm/v1`,
   модели из таблицы, `REASONING_EFFORT_SEAT_1=extra_body.thinking.disabled`,
   `REASONING_EFFORT_SEAT_2=reasoning_effort.low`, `RESPONSE_FORMAT=json_schema`.
   Kimi требует `temperature=1` (05.11 при 0 был HTTP 400); fallback сейчас
   наследует температуру основного места (0), поэтому либо добавить
   `JUDGE_FALLBACK_TEMPERATURE_SEAT_N`, либо проверить Kimi при 0 до
   включения — решить в задаче 2.

## Задачи

1. Тесты (red): совпадающий URL + разные модели принимается; совпадающая
   модель/алиас на том же URL отклоняется; пустой fallback-ключ наследуется
   только при совпадающем URL; `http-auth` на том же endpoint не вызывает
   fallback; конфигурация с OpenRouter продолжает работать как раньше.
   Файлы: `weblate/trans/tests/test_judge_client.py`,
   `weblate/trans/tests/test_judge_loop.py`.
2. Реализация в `weblate/trans/judge.py` (пункты 1, 2, 4, 5) и, если нужно,
   `JUDGE_FALLBACK_TEMPERATURE_SEAT_N` в `defaults.py`/`settings_docker.py`/
   `settings_example.py`/`deploy/environment.example`.
3. Документация (пункт 6).
4. Проверка: `./rundev.sh test weblate/trans/tests/test_judge_client.py
   weblate/trans/tests/test_judge_loop.py`, `prek`, `mypy`; живой запрос к
   LiteLLM через non-durable путь с принудительной ошибкой основной модели
   (несуществующий алиас в dev) — fallback-попытка записана с
   `service=litellm`.
5. PR, ревью, merge; выкладка и пункт 7 — с отдельного разрешения.

## Вне объёма

- Fallback для durable `ProducerRun` (пункт 3) — следующий инкремент.
- Любой судья через OpenRouter.
- Маршрутизация перевода (решено 05.11: везде `google/gemini-3.7-flash`).
