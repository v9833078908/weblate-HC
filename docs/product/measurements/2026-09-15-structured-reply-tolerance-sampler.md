# Structured reply tolerance — production sampler

Дата: 2026-09-15. Прогон платного, не мутирующего sampler'а
`analysis/probes/llm-structured-reply-replay.py` на проде после деплоя
`main` `a61089c` (см. план
`docs/product/plans/2026-09-14-llm-structured-reply-tolerance.md`, Rollout,
шаг 2).

## Условия

- Проект `pirate-ships`, движок `openrouter`, пустых строк: 27.
- Probe делает один `download_multiple_translations` на строку (никогда
  `batch_translate`), поэтому не обновляет `Unit.machinery`, квоту и не
  меняет состояние юнита. Каждый вызов — реальный billed запрос и пишет
  аудит-строку в `LLMUsageLog`.

## Итог

`outcomes: {'applied': 26, 'refused': 1}` — 26 из 27 принято, 1 отказ.

Из 26 принятых большинство раньше (до canonicalization) вернуло бы
`Mismatching assistant reply`: модель вписывала токен инлайн в text-part
(`"text": "@@PH0@@위"`), переставляла grammar-placeholder
(`Has quedado en el puesto @@PH10@@`, `@@PH10@@. sırayı aldın`),
свободно двигала текст вокруг syntax-плейсхолдера. Это и есть те формы,
которые новый `_canonicalize_structured_parts` теперь нормализует.

## Единственный отказ — корректный, не регрессия

Unit `537401`
(`Бросает огненную бочку во врага... наносит {0} огненного урона в радиусе {1} узлов... на {2}с.`):

- Источник требует порядок `@@PH55@@,@@PH85@@,@@PH132@@` (= `{0},{1},{2}`).
- Модель (китайский) переставила их: `...在@@PH85@@节半径内造成@@PH55@@点火焰伤害...@@PH132@@...` —
  радиус впереди урона.
- Для анонимных плейсхолдеров порядок несёт смысл, reorder = правильный
  refusal (`Mismatching assistant reply items.`, `LLMUsageLog` row 13004,
  `outcome='refused'`). Это осмысленная ошибка модели, а не форма ответа.

## Связь юнит ↔ LLMUsageLog

26 принятых: rows 12978–13003 (`outcome='applied'`). 1 отказ: row 13004
(`outcome='refused'`, `refusal_reason='Mismatching assistant reply items.'`).

## Unit IDs (все 27)

411118, 411120, 411136, 411138, 411139, 416692, 416694, 416695, 416752,
416756, 416758, 417798, 417800, 417807, 417808, 417809, 417850, 418011,
418027, 537153, 537154, 537159, 537161, 537346, 537350, 537394, 537401.

Полный сырой вывод (asked parts, raw reply, accepted/refused, usage row)
сохранён на рабочей машине в `/tmp/hc-sampler-run.log` на момент прогона;
воспроизводим `LLMUsageLog` rows 12978–13004.

## Вывод

Новый парсер принимает корректные по содержанию ответы независимо от того,
как модель оформила `parts`, и по-прежнему отвергает настоящий reorder
анонимных плейсхолдеров. Residual refusal rate на этой выборке 1/27 (3.7%),
и он осмысленный. Шаг 4 плана (решение о single-string retry) — после
week-later анализа `LLMUsageLog` (шаг 3).
