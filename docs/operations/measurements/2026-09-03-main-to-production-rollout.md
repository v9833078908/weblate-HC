# Замер: выкатка main на прод и включение функционала, 2026-09-03

Инстанс: `l10n.herocraft.com` (`hc-srv15-localizer`). Прод был на `931de81`
(образ `53f2ac1`), стал на `39c0893`. Выполнены фазы 1-7 и 9 ранбука
`docs/operations/plans/2026-09-03-deploy-main-to-production.md` (удалён как
одноразовый); фаза 8 (backfill кандидатов) не выполнялась по решению
владельца.

## Фаза 1. Деплой образа

| Факт | Значение |
|---|---|
| Первая выкатка | `f02d8f2`, `DEPLOY-OK`, healthy за 295 s, логин 200 |
| Вторая выкатка (фикс стоимости) | `39c0893`, `DEPLOY-OK`, healthy за 193 s, логин 200 |
| image revision | равен выкаченному HEAD в обоих случаях |
| Миграции | `0113`-`0118` - `[X]`, применены entrypoint'ом |

Помеха, не связанная с прод-кодом: локальный Docker Desktop падал дважды и
обрывал VPN-шлюз `hc-vpn-gw`. Удалённый скрипт деплоя detached, поэтому обрыв
туннеля выкатку не прерывал - вердикт дочитан из `/tmp/hc-deploy.log`.

## Фазы 2 и 4. Канарейка до и после отключения ризонинга

Обе канарейки: `analysis/probes/judge-rollout-canary.py`, translation 46
(`heart-abyss/temple`, en), 25 юнитов, `writable_ids=set()`,
`candidate_severities=()`, `use_cache=False`. В контейнере прода нет
`/app/src`, поэтому проба копируется `docker cp` в `/app/data`.

| Метрика | До (`thinking.disabled` / пусто) | После (`extra_body.enable_thinking=false` на seat 2) |
|---|---|---|
| Вердикт пробы | `CANARY-OK` | `CANARY-OK` |
| Время прогона | 563 s | 230 s |
| Реквесты / вердикты | 38 / 50 | 38 / 50 |
| Терминальных unparsed | 0 | 0 |
| Обслуживающие провайдеры | `['litellm']` | `['litellm']` |
| Fallback-попыток | 0 | 0 (эндпоинт уже настроен) |
| Батчи seat 2 | 5.6-110.3 s | 1.5-4.2 s |
| reasoning-токенов seat 2 | 11 451 при 13 463 completion (85%) | **0** при 1 290 completion |
| reasoning-токенов seat 1 | сохраняется (8 993 при 10 176) | сохраняется - решение по recall |
| first byte seat 1 p95 | 4 504 ms | 4 836 ms |
| first byte seat 2 p95 | 2 064 ms | 2 573 ms |

Прод подтвердил замер `39fbeaa`: на прокси `hcbifrost` работает только
`extra_body`-форма. Значение `thinking.disabled`, стоявшее на seat 1, было
доказуемо инертным - заменено на пустое.

## Фаза 3. Атрибуция LLM-расходов - частично

Новый scope миграции `0113` работает: на 38 свежих judge-строках
`project_id_snapshot`, `component_id_snapshot` и `target_language_code`
заполнены, `unattributed = 0`. Исторические 6 820 строк остаются без
компонента и языка - их писали прежние writer'ы, задним числом это не
восстановить.

Цена по-прежнему не пишется, и причина измерена:

| Запрос к `hcbifrost` | `usage.cost` | `x-litellm-response-cost` | `...-cost-original` |
|---|---|---|---|
| без стрима | отсутствует | `1.0539354e-05` | `1.0539354e-05` |
| со стримом | отсутствует | отсутствует | `0.0` |

LiteLLM не кладёт цену в `usage` вообще, а заголовок при стриминге отдать не
может - он уходит до конца потока. Коммит `39c0893` читает заголовок как
резерв (тесты `test_usage_prices_a_header_only_cost`,
`test_usage_cost_field_wins_over_the_header`), и это закрывает нестримовый
путь, но оба судейских сиденья работают с `stream=true`, поэтому все 38
свежих judge-строк остались `cost_usd IS NULL`.

**Решение владельца 2026-09-03: judge-строки остаются без цены.** Это
ограничение прокси, а не дефект: токены и атрибуция пишутся полностью,
деньги считает сам LiteLLM (`x-litellm-key-spend`). Отвергнуты как
ненужные усложнения: дотягивание цены из `/spend/logs?request_id=`,
локальный расчёт по `/model/info` (оценка, а не факт биллинга - у прокси
есть заголовки скидки и маржи) и выключение стриминга у судей (потеря
инкрементального first byte и idle-таймаута по чанкам). Поэтому
`priced_complete=yes` в `llm_usage_report` для judge-операции недостижим
намеренно, а заголовочный резерв из `39c0893` остаётся полезным для
нестримовых путей и для fallback-эндпоинта. Машинный перевод не затронут:
он идёт через OpenRouter, который отдаёт `usage.cost`.

## Фаза 5. Чистка ложных refused-вердиктов

`weblate judge_close_refused_verdicts --expected-count 100`: dry-run напечатал
`total: 100` (50 на `atlas/qwen3.8-max` + 50 на
`weblate-judge-deepseek-v4-pro`, все HTTP 400), прогон с `--confirm` удалил
ровно 100 вердиктов и реклассифицировал 25 `JudgeRunUnit` в `refused`. После:
вердиктов вида `unparsed=True` + HTTP 400/401 - **0**, attempt-ledger цел
(78 попыток с 400/401 сохранены).

## Фаза 6. Fallback на OpenRouter

Восемь `WEBLATE_JUDGE_FALLBACK_*` записаны в `/srv/hcgameloc/deploy/.env`
(бэкап `.env.bak-*-fallback`), ключ взят из site-wide настройки сервиса
`openrouter` (`Setting category=2`), контейнер пересоздан. Снимок
конфигурации: `fallback_hostname=openrouter.ai`,
`fallback_model=['deepseek/deepseek-v4-pro', 'qwen/qwen3-235b-a22b-2507']`,
`fallback_response_format=['json_schema', 'json_schema']`. Канарейка после
пересоздания: `fallback attempts: 0`, вердиктов с
`judge_provider='openrouter'` - **0** при 100 с `litellm`. Fallback настроен и
простаивает, как и требует приёмка.

## Фаза 7. `repeat-drift` и аудит глоссария

Ожидание, посчитанное на проде по правилу плана (проект, язык, точный текст
источника, без глоссария, без исходных переводов, `state >= translated`):
**122 расходящиеся группы, 259 юнитов** - ровно замер
`docs/operations/measurements/2026-09-03-need-for-greed-repeat-drift.md`.

`Project.check_flags = 'repeat-drift'` на `need-for-greed`,
`weblate updatechecks need-for-greed` (247 s). Факт: **259 строк**
`repeat-drift`, все в `need-for-greed`, компоненты `loot`, `orders`, `survey`,
`tutorial`, `ui`. Карточка рендерится: `Непоследовательный повтор` на
странице юнита 389635 (HTTP 200). Остальные семь проектов не включены -
приёмочного числа для них нет.

`weblate audit_glossary --project need-for-greed`: 47 находок. 22 из них -
намеренные омонимы ru-миграции (`Белоземье` в 18 языках, `Сундук Конунга` в
6), приняты в `analysis/data/glossary-audit/need-for-greed.baseline`. С
baseline команда сообщает **25 непринятых находок**, и это настоящие дефекты
глоссария, которые надо править переводчикам:

| Класс | Термины | Языки |
|---|---|---|
| `collapsed-terms` | `скупщик` / `торговец` → один перевод | bg, cs, de, es, fil, fr, hu, id, it, lt, lv, nl, ro, tr |
| `collapsed-terms` | `яйца` / `яйцо` | cs, id |
| `collapsed-terms` | `лошадь` / `скакуха` | fil, id |
| `collapsed-terms` | прочие схлопывания | `артефакт`, `гео́да`, `самоцвет`, `металл`, `редкий`, `легендарный`, `усталость`, `сундук`, `магазин`, `крабовая шуба` |

## Фаза 9. Финальная проверка включения

```text
judge enabled: True
deferral enabled: False
fallback configured: True
reasoning per seat: ['', 'extra_body.enable_thinking=false']
openrouter-served verdicts: 0
litellm-served verdicts: 100
repeat-drift flags: {'need-for-greed': 'repeat-drift'}
repeat-drift rows: 259
open deferrals: 0
```

Карточка вердикта на юните 14578 (`reject`, нерешённый) отдаёт HTTP 200 и
русскую локаль: `Вердикт`, `Отклонено`, `Оставить как есть`,
`Перепроверить`. Кнопки `Использовать` нет - у вердикта нет сохранённого
кандидата, потому что фаза 8 (backfill) не выполнялась.

Распределение вердиктов на проде после выкатки: `pass` 3 318, `flag` 285,
`reject` 95, пустых 41; исходы run-unit: `passed` 687, `unparsed` 275,
`major` 131, `minor` 60, `critical` 51, `refused` 25.

## Что осталось открытым

- **Цена judge-вызовов** (фаза 3): закрыто решением владельца - строки
  остаются без цены, см. фазу 3.
- **25 находок аудита глоссария**: правка терминов, не код.
- **`repeat-drift` на остальных семи проектах**: каждый требует своей пробы.
- **Очередь отложек** (`WEBLATE_JUDGE_DEFERRAL_ENABLED=1`): не включалась,
  Task 7 плана `docs/llm-first/plans/2026-09-01-03-judge-zero-unparsed.md`.
- **Backfill кандидатов** (фаза 8): исключён владельцем из этой выкатки.
