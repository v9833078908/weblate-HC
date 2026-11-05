# Хотфикс 2026-11-05: перевод только на Gemini, судьи только через Atlas

Production `l10n.herocraft.com`, контейнер `hcgameloc-weblate-1`, образ
`hcgameloc:llm-cost-46a66d39`. Код не менялся, только конфигурация.

## Что было

`LLMUsageLog` за 22.10–05.11:

| Поток | Модель | Запросов | Почему |
| --- | --- | --- | --- |
| перевод → OpenRouter | `deepseek/deepseek-chat-v3.1` | 802 (ko 513, zh_Hans 289) | глобальный `routing` OpenRouter: `ko`/`zh` → DeepSeek |
| судья → OpenRouter напрямую | `deepseek/deepseek-v4-pro` | 7212 | `JUDGE_FALLBACK_BASE_URL=https://openrouter.ai/api/v1` |
| судья seat 1 → LiteLLM → OpenRouter | `openrouter/deepseek-v4.1-flash` | ~1400 | алиас LiteLLM ведёт на `https://openrouter.ai/api/v1` |
| судья seat 2 | `Kimi K3.1 Pro` | ~900 | `/app/data/settings-override.py` перекрывал env |

## Что сделано

1. Глобальная настройка машинного перевода `openrouter`:
   `routing = {"*": "google/gemini-3.7-flash"}`. Ни один проект `routing` не
   переопределяет.
2. Судьи (env — единственный источник):
   - seat 1 `atlas/deepseek-v4-pro-0813`, `extra_body.thinking.disabled`,
     temperature 0;
   - seat 2 `atlas/qwen3.8-max`, `extra_body.enable_thinking=false`,
     temperature 0;
   - все `JUDGE_FALLBACK_*` пустые;
   - изменены `runtime.private.json` в
     `/srv/hcgameloc-releases/6b211b5a…/` и `/srv/hcgameloc/deploy/.env`;
   - из `settings-override.py` удалены все `JUDGE_*_SEAT_2`; остались
     `JUDGE_OBSERVABILITY_CLEANUP_INTERVAL = 0` и
     `JUDGE_REQUEST_IDLE_TIMEOUT = 120.0`.
3. Контейнер пересоздан `docker compose -p hcgameloc <те же файлы> up -d
   --no-deps weblate`; БД и кеш не перезапускались.

Резервные копии лежат рядом с каждым файлом с суффиксами
`.bak-20261105T*-judge-corp`, `-seat1-0813`, `-before-env-master`.

## Почему seat 1 на 0813

Алиас `deepseek-v4-pro` (одно развёртывание Atlas `d8eafe28`) игнорирует
`thinking.type=disabled` на части запросов: в октябре 1915 из 18 972
запросов судьи с размышлениями, 05.11 — 7 из 38. Проверка на 20 реальных
строках pirate-ships через `_payload` судьи, по три прохода, без записи в БД:

| Модель | С размышлениями |
| --- | --- |
| `deepseek-v4-pro` | 3 из 60 (575, 1265, 1861 токенов) |
| `atlas/deepseek-v4-pro-0813` | 0 из 60 |

Качество вердиктов 0813 против незакреплённого алиаса не сравнивалось.

## Проверка после хотфикса

- Профили: seat 1 `atlas/deepseek-v4-pro-0813`
  (`deepseek-ai/deepseek-v4-pro-0813`), seat 2 `atlas/qwen3.8-max`; оба на
  `https://hcbifrost.herocraft.com/litellm/v1`;
  `judge_fallback_endpoint() is None`.
- Перевод возобновился: `auto_translate` выполняется, батчи Gemini
  `applied`.
- После 14:56 UTC запросов в OpenRouter, кроме Gemini, — 0.
- После старта контейнера (15:37:05 UTC) судьи вызывают только два алиаса
  Atlas, размышлений нет.

Один запуск упал в промежутке, пока seat 2 ещё был Kimi с temperature 0:
pirate-ships zh_Hans, `captain_lore_rarity_captain_cannons_1`, HTTP 400. Его
нужно перезапустить.

## Открыто

- Запасные модели на том же LiteLLM и запрет алиасов с upstream OpenRouter в
  коде: `docs/product/plans/2026-11-05-judge-same-endpoint-model-fallback.md`.
- Вопрос админам LiteLLM: почему `deepseek-v4-pro` иногда игнорирует
  `thinking.type=disabled` (response_id из LLMUsageLog 05.11 14:58–15:07).
