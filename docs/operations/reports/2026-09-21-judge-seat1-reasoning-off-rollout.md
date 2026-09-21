# Продакшн: seat 1 без reasoning и seat 2 batch 5 (связка B1)

Дата: 2026-09-21. Инстанс: `l10n.herocraft.com` (`hcgameloc-weblate-1`).
Исполнено по прямому указанию владельца («в seat 1 отключить ризонинг»).
Изменение только конфигурационное: код не менялся, миграций нет.

## Что было обнаружено

Деплой 2026-09-18 (контейнер создан `2026-09-18T18:18:27Z`, код `a1ccdafb`)
не изменил профиль судьи: `deploy/.env` на VPS (mtime — до деплоя) по-прежнему
содержал `WEBLATE_JUDGE_REASONING_EFFORT_SEAT_1=` (пусто) и
`WEBLATE_JUDGE_BATCH_SIZE_SEAT_2=1`. Коммит `3030be26` («configure seat 1
reasoning off and seat 2 batch size 5 in production») правил только шаблон
`deploy/environment.example`, который на живой инстанс не переносится.

Факт по расходу, а не по конфигу — `LLMUsageLog`, `operation=judge`:

| окно | модель | запросов | reasoning-токенов | completion |
|---|---|---:|---:|---:|
| 14 дней до правки | `deepseek-v4-pro` | 4651 | 3 710 888 | 4 198 571 |
| 14 дней до правки | `atlas/qwen3.8-max` | 9140 | 0 | 421 783 |
| после деплоя 18.09 (18:18–18:41) | `deepseek-v4-pro` | 6 | 1 400 | 1 772 |

То есть DeepSeek тратил на размышления ~47% своих completion-токенов и делал
это и после пятничного деплоя.

## Что изменено

`/srv/hcgameloc/deploy/.env` (резервная копия
`/srv/hcgameloc/deploy/.env.bak-2026-09-21-judge-seat1`):

    WEBLATE_JUDGE_REASONING_EFFORT_SEAT_1=extra_body.enable_thinking=false
    WEBLATE_JUDGE_BATCH_SIZE_SEAT_2=5

Контейнер пересоздан (`docker compose up -d weblate`), новый создан
`2026-09-21T07:48:11Z`, статус `healthy`. Перед пересозданием проверено:
активных задач `auto_translate*` нет, очереди `translate` и `celery` пусты.

Это ровно связка B1, измеренная в
`docs/product/measurements/2026-09-18-judge-seat1-quality-and-bundle.md`:
wall clock пары −81.1%, POST −33.3%, prompt-токены −30.4%, p95 42.7 с → 8.1 с,
recall 12/12 против 12/12 у контроля, ложных флагов поровну.

## Проверка

Резолвнутые профили в контейнере (`judge_seat_profiles()`):

    SEAT 1 | deepseek-v4-pro   | reasoning 'extra_body.enable_thinking=false' | batch 2 | deadline 120
    SEAT 2 | atlas/qwen3.8-max | reasoning 'extra_body.enable_thinking=false' | batch 5 | deadline 150

Исходящий payload (`_payload()` на реальных профилях, без отправки):

    SEAT 1 {"model": "deepseek-v4-pro",   "stream": true, "extra_body": {"enable_thinking": false}}
    SEAT 2 {"model": "atlas/qwen3.8-max", "stream": true, "extra_body": {"enable_thinking": false}}

Сквозное подтверждение (`reasoning_tokens = 0` у `deepseek-v4-pro` в
`LLMUsageLog`) появится на первом же реальном прогоне судьи после правки:
платных запросов ради проверки не делалось.

## Fallback (OpenRouter) — выровнен тем же решением

Fallback-профиль живой (25 запросов за 14 дней до правки) и уходил на модели
фазы 0 с reasoning on. По решению владельца выровнен с B1
(`/srv/hcgameloc/deploy/.env`, бэкап `.bak-2026-09-21-judge-fallback`):

    WEBLATE_JUDGE_FALLBACK_MODEL_SEAT_1=deepseek/deepseek-v4-pro
    WEBLATE_JUDGE_FALLBACK_MODEL_SEAT_2=qwen/qwen3.8-max-0902
    WEBLATE_JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1=none
    WEBLATE_JUDGE_FALLBACK_REASONING_EFFORT_SEAT_2=none

Протокол OpenRouter другой: не-LiteLLM-профиль шлёт
`{"reasoning": {"effort": <value>, "exclude": true}}`, и «none» —
документированное значение, выключающее reasoning-токены (проверено по
докам OpenRouter 2026-09-21). Значение primary
`extra_body.enable_thinking=false` на этот путь переносить нельзя — ушло бы
буквальным effort-уровнем.

Имена моделей — нативные ID OpenRouter: проверены по публичному
`GET https://openrouter.ai/api/v1/models` (446 моделей), где есть
`deepseek/deepseek-v4-pro` и `qwen/qwen3.8-max-0902`, но **нет** ни
`atlas/qwen3.8-max`, ни `qwen/qwen3.8-max` (первая версия правки несла
именно их и была бы сломана при первом же отказе primary: не-LiteLLM
резолвер имена моделей не валидирует). Прошлый fallback
(`qwen/qwen3-235b-a22b-2507`, `deepseek/deepseek-v4-pro`) был проверен
365+12 запросами за 14 дней; новый профиль замеров не имеет — он
выравнивается по требованию владельца и остаётся untested до первого
падения primary.

Правка применена и **активна**: контейнер пересоздан
(`2026-09-21T08:41:11Z`, `healthy`), резолвнутые профили:

    PRIMARY  seat 1 deepseek-v4-pro   'extra_body.enable_thinking=false' batch 2
    PRIMARY  seat 2 atlas/qwen3.8-max 'extra_body.enable_thinking=false' batch 5
    FALLBACK seat 1 deepseek/deepseek-v4-pro 'none' openrouter
    FALLBACK seat 2 qwen/qwen3.8-max-0902     'none' openrouter

Перед пересозданием: активных задач `auto_translate*` нет, очередь
`translate` пуста (13 сообщений в очереди `celery` переживают рестарт и
деплой не блокируют).

**Инцидент: ключ fallback скомпрометирован при выкладке.** При проверке
`.env` значение `WEBLATE_JUDGE_FALLBACK_API_KEY` (OpenRouter,
`sk-or-v1-…`) попало в лог сессии. Требуется ротация владельцем в
OpenRouter dashboard и обновление `.env` + пересоздание контейнера.

## Открытые риски

1. **Verdict cache.** Смена профиля меняет его отпечаток
   (`profile_fingerprint` включает reasoning/batch/response_format/stream,
   `weblate/trans/judge.py`), поэтому старые вердикты автоматически не
   переиспользуются: **первый полный judge-прогон переоплатит все строки,
   сужённые под старую пару** (в usage за 14 дней ~14.7 тыс. запросов; при
   cap 2000 строк это заметная сумма). Запускать большой прогон стоит
   осознанно, а не «заодно».
2. **Предпросмотр цены завышен вдвое.** `recent_cost_range`
   (`weblate/trans/views/edit.py`) берёт историю по модели без учёта
   `profile_fingerprint`, а она набрана на C0 (reasoning on, ~47%
   completion-токенов — размышления). Пока не наберётся ≥5 ценовых строк
   нового профиля, оценка перед прогоном будет завышена примерно вдвое.
3. **Внешняя валидность B1** держится на agent-seeded фикстурах (16 и 8
   строк). Корпус с экспертной разметкой подготовлен —
   `docs/product/measurements/2026-09-21-judge-expert-labeled-corpus.md`;
   платный прогон на нём требует отдельного подтверждения бюджета.
