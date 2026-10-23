# Исследование стоимости LLM-судей

Дата: 2026-09-17. Источник: omp `01a0ae7e-199b-75c5-9ce9-eaaac04093d6`.

Единый архив исследования Anvil Saga / Victory Banner: полный отчёт, выводы всех пяти субагентов, их 14 дополнительных сообщений и обезличенные числовые доказательства. Архив не является согласованным планом реализации или разрешением на деплой.

## Порядок чтения

1. [docs/operations/measurements/2026-09-17-judge-cost-investigation/report.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/report.md) — итоговый разбор, измерения, уравнение стоимости, TMS, приоритеты и неизвестные.
2. [docs/operations/measurements/2026-09-17-judge-cost-investigation/corrections.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/corrections.md) — какие ранние гипотезы опровергнуты и какие рекомендации остаются экспериментами.
3. [docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/methodology.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/methodology.md) — методика, полнота ledger и воспроизведение.
4. Полные результаты по направлениям перечислены ниже.

## Выводы субагентов

| Агент | Направление | Полный вывод и дополнительные сообщения |
|---|---|---|
| JudgeExecution | Execution, batching, retries, cache, redelivery | [docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgeExecution.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgeExecution.md) |
| JudgePayload | Prompt, glossary, output, reasoning, accounting | [docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgePayload.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgePayload.md) |
| JudgeQualityEvidence | Исторические измерения качества и ограничения gold set | [docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgeQualityEvidence.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgeQualityEvidence.md) |
| JudgeBestPractices | Публикации и практики экономичной LLM/QE-оценки | [docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgeBestPractices.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/JudgeBestPractices.md) |
| TMSBenchmark | Phrase, Smartling, Lokalise, Crowdin, memoQ, RWS | [docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/TMSBenchmark.md](/docs/operations/measurements/2026-09-17-judge-cost-investigation/subagents/TMSBenchmark.md) |

Рядом с каждым Markdown сохранён полный исходный результат агента в JSON. `JudgeExecution-initial.json` сохраняет первоначальный, впоследствии исправленный вывод; не использовать его гипотезу batch=5 как состояние production. Их неизменённый текст также записан в `subagents/supplemental-messages.json`. Дополнительные сообщения сохранены без служебных оболочек и метаданных сессии, в исходном порядке.

## Доказательства

- `docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/production-usage.json.gz` — allowlist числовых request/usage записей, идентификаторов и безопасных configuration snapshots; 19 runs, 12329 attempts, 12070 usage rows. Не включает prompts, provider errors, исходники функций и произвольный текст запроса.
- `docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/overlap-payload.json.gz` — 291 общая строка Anvil, хэши/времена вердиктов, длины targets, 24 диагностических payload: размеры полей, не их содержимое.
- `docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/proxy-rate-metadata.json` — rate metadata двух aliases, без credentials. Не invoice.
- `docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/run-aggregates.json` — агрегаты четырёх больших запусков.
- `docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/verify.py` — автономная проверка чисел, связей и контрольных сумм; ничего не вызывает на проде и не обращается к LLM.
- `docs/operations/measurements/2026-09-17-judge-cost-investigation/manifest.json` — контрольные суммы всех файлов архива, кроме самого manifest.

## Ограничения и статус

- Anvil: **5997 unpriced usage rows**, ещё **38 attempts без usage**. Деньги в отчёте — estimate, не фактический счёт и не гарантированная экономия.
- `$122–156` — только линейный сценарий на исторический полный объём 57996 строк, **не стоимость оставшегося Anvil**. Уникальный текущий eligible scope не пересчитывался.
- Batch/model/reasoning/cascade изменения не внедрены и не проверены новой canary; требуют отдельного разрешения и контроля качества, parse/timeout-rate.
- Сессии JSONL, tool logs, credential metadata, ключи, полные provider errors и тексты переводов в архив не копировались.
- Данные проведённого исследования сохранены без нового обращения к production.

## Предыдущий контекст

Прочитаны omp-сессии `01a0ae1c-371a-777c-8434-a2c70796cd59` и `01a0ae23-c8cc-73d5-937f-9a933ab1ce7d`.

Предыдущие факты и пользовательские решения: `docs/operations/measurements/2026-09-17-anvil-saga-judge-investigation.md`.
Существующие планы: `docs/product/plans/2026-09-17-mt-prerequisite-before-judge.md` и `docs/product/plans/2026-09-17-judge-redelivery-and-coverage.md`. Сохранение исследования не означает согласования их реализации.

## Воспроизведение

Из корня репозитория:

```sh
python3 docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/verify.py
```

Проверка работает только с файлами этой папки, не требует Django, ключей, Docker, сети или платных запросов.
