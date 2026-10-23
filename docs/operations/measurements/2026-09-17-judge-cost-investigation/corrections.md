# Уточнения после проверки production

Дата: 2026-09-17. Приоритет источников: фактическая production-выгрузка и итоговый `docs/operations/measurements/2026-09-17-judge-cost-investigation/report.md` выше ранних сообщений субагентов. Полные исходные выводы сохранены, включая опровергнутые гипотезы: это история исследования, не параллельные рекомендации.

## Исправленные гипотезы

| Ранний вывод / риск прочтения | Итог проверки |
|---|---|
| При batch=5 должно быть 800 calls; около 3000 указывает на массовые retries/adaptive collapse | Production overrides DeepSeek=2, Qwen=1: база около3000. Retry explosion не объясняет основной объём Anvil. |
| Scheduler ждёт одинаковый batch index | Установленный код использует покрытие `end_offset`, учитывающее разные ширины батчей. |
| Provider cache не работает из-за random boundary | Cached input измерен: DeepSeek54.6%, Qwen90.5%, суммарно Anvil77.7%. Boundary не уничтожил общий стабильный prefix. |
| Следует отключить reasoning Qwen | Уже отключён; reasoning=0 в обоих Anvil run. |
| Сравнение5h20/9h11 доказывает замедление одного и того же объёма | Состав различается:1104 glossary+896 lockit против2000 lockit; median target7/46 symbols. |
| Нулевой cached во втором отчёте означает сломанный cache по завершённым вердиктам | Все первые вердикты291 общих rows появились после старта второго run; завершённый cache не защищает in-flight. Target/context совпадают у всех291. |
| Весь второй run потрачен впустую |1709 rows новые;291 общие. Связанные с overlap запросы — ~$0.74 estimate, не точный ущерб. |
| `repaired=0` и нет MT usage означают отсутствие repair HTTP | Не доказывают: failed POST до usage не учтён. В Anvil310/581 no-candidate; их полное время/число отказавших вызовов неизвестно. |
| Полный glossary и соседние строки раздувают каждый request | Builder передаёт matched glossary; соседних реплик в этом payload нет. |
| LiteLLM cost=None можно суммировать как0 | Это unknown. Сумма priced rows не является стоимостью всего run. |
| QE/успешные regex позволяют безопасно пропустить всю семантическую проверку | Нет: deterministic checks и QE не доказывают отсутствие semantic false negatives. |
| Старый неудачный cascade отвергает все возможные cascades | Отвергает проверенный тогда cascade с пересмотром вердикта. Новый selective routing требует собственной валидации и random audit pass. |
| Старые публичные цены OpenRouter/Qwen/DeepSeek можно применить к proxy alias | Нельзя; оценка отчёта использует actual alias metadata `/model/info`, версия совпадает со snapshot. Биллинг всё равно нужен. |

## Точность денежных выводов

Из5997 Anvil usage rows ни одна не содержит provider-reported cost. Ещё38 attempts без usage:19 LiteLLM deadline,19 OpenRouter auth403. Расчёт$4.23/$5.38 покрывает только сохранённый usage по cache-read/input/output ставкам. Неучтены неизвестные начисления failed POSTs, cache writes, договорные скидки/надбавки. Не называть его точным счётом или доказанной экономией от cache.

Сценарий без cache discount$12.10/$13.25 не является верхней границей фактического счёта. Экстраполяция$122–156 не является сметой оставшегося scope; текущая eligibility, длины, языки и repair mix не измерены заново.

## Рекомендации не равны внедрённым оптимизациям

Подтверждённый дефект: конкурентное исполнение совпадающей задачи и повторные оценки совпадающего контента. Его исправление не должно снижать качество проверки.

B1=2/B2=1 — ранее выбранные профили, не случайная опечатка. Изменения ширины батча, выключение reasoning, удаление seat, смена модели, сокращение prompt/back-translation, QE и cascade — только gated experiments. Нужны human-labeled recall/critical misses, false-critical/false-flag, JSON/cardinality, timeout/parse-rate, token/cost per unit и latency. Ни новая canary, ни deployment не разрешены сохранением этого архива.

Историческое DeepSeek no-think сравнивалось преимущественно со stored verdict: потеря обнаружений — сигнал риска, не полноценная human-gold оценка recall. Не приписывать small-sample experiment доказанную универсальную потерю качества.

## Уточнения внешнего TMS research

- Phrase AI Unit Calculation в прямой проверке родительским агентом описывает AI Checks как1 AIU/10 unlocked **source** words, последующие checks+25%; раннее агентское описание target+criteria words нельзя выдавать за эту текущую формулу.
- Crowdin действительно документирует daily/monthly/per-user limits, но custom providers не учитываются в денежных лимитах; стандартная rate estimate может отличаться от invoice.
- Lokalise scoring и отдельный AI LQA имеют разные правила quota. Отсутствие дополнительного списания AI words за scoring не доказывает бесплатный inference.
- Публичные vendor docs не раскрывают число/размер внутренних моделей, per-language calibration или внутренние batch widths. Нельзя утверждать, что все top TMS используют один judge, sampling или одинаковый cascade.
- Supplier marketing (например80% меньше post-edit) не является ожидаемой экономией HCGameLoc. Шкалу0–100 и auto-approval не переносим в advisory judge.

## Непроверенное

Полный invoice LiteLLM/Atlas, billed timeouts/cache creation, остаточное время вне HTTP windows, полная статистика неуспешных repair calls, непосредственный broker-log повторной доставки и новая human validation текущих aliases по всем языкам остаются неизвестными. Архив не устраняет эти ограничения.
