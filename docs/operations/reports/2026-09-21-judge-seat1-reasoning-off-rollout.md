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

## Открытые риски

1. **Fallback не трогали.** `WEBLATE_JUDGE_FALLBACK_MODEL_SEAT_1=deepseek/deepseek-v4-pro`
   и `..._SEAT_2=qwen/qwen3-235b-a22b-2507` на OpenRouter, обе с пустым
   reasoning-контролем. Это модели фазы 0, и путь живой: за 14 дней 13 и 12
   запросов. При отказе прокси судья уедет на другой профиль с reasoning on,
   к которому замеры B1 неприменимы.
2. **Verdict cache.** Смена профиля меняет его отпечаток, поэтому первая
   переоценка ранее судимого scope пройдёт мимо кэша и будет дороже
   steady-state.
3. **Внешняя валидность B1** держится на agent-seeded фикстурах (16 и 8
   строк). Корпус с экспертной разметкой подготовлен —
   `docs/product/measurements/2026-09-21-judge-expert-labeled-corpus.md`.
