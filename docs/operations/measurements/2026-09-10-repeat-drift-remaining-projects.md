# repeat-drift по остальным восьми проектам и утечка в промпт судьи, 2026-09-10

Инстанс: `l10n.herocraft.com`. Read-only проба
`analysis/probes/source-repeat-drift.py` по всем проектам, кроме уже
включённого `need-for-greed` (включён 2026-09-03, 259 строк — замер
`docs/operations/measurements/2026-09-03-main-to-production-rollout.md`,
фаза 7).

## Две поправки пробы

Проба падала на пяти из восьми проектов по двум причинам, не связанным с
чеком:

1. **404** на паре компонент×язык: компонент не имеет всех языков проекта,
   и несуществующий перевод теперь пропускается.
2. **`next`-ссылки пагинации приходят как `http://`** за TLS-прокси
   (`WEBLATE_SITE_DOMAIN` отдаёт http-схему), а `get()` отказывал во всём,
   что не `https://…/api`. Схема нормализуется до проверки хоста.

## Числа (строгая свёртка, группы только из обычных компонентов)

`regular` — расходящиеся группы без глоссарных компонентов; `gl+reg` —
группы, где члены глоссария и обычных компонентов разошлись вместе (чек
глоссарные юниты исключает, поэтому `regular` — приближение того, что
поставил бы `RepeatDriftCheck`; проба не фильтрует
`allow_translation_propagation`, так что это верхняя оценка).

| Проект | юнитов | расходящиеся группы (strict) | regular | gl+reg |
|---|---|---|---|---|
| anvil-saga | 58 180 | 858 | 804 | 54 |
| pirate-ships | 64 512 | 833 | 762 | 71 |
| space-arena | 78 060 | 1 421 | 552 | 869 |
| col4 | 20 685 | 253 | 243 | 10 |
| heart-abyss | 8 455 | 126 | 126 | 0 |
| victory-banner | 3 856 | 45 | 1 | 44 |
| strategy-and-tactics-2 | 2 052 | 1 | 1 | 0 |
| korotkij-test | 0 | 0 | 0 | 0 |

Материальный дрейф — `anvil-saga`, `pirate-ships`, `space-arena`, `col4`,
`heart-abyss`. `victory-banner` и `strategy-and-tactics-2` чистые;
`space-arena` дополнительно несёт 869 групп, где глоссарий и обычные
компоненты разошлись вместе — это территория `audit_glossary`, а не
`repeat-drift`.

## Утечка в промпт судьи (блокер до расширения)

Замер `docs/product/measurements/2026-09-04-glossary-morphology-vs-repeat-drift.md`
зафиксировал: `build_request` (`weblate/trans/judge_loop.py`) слал судье
`all_checks_names - JUDGE_CHECKS`, куда входил `repeat-drift`. Промпт
`verdict.txt` велит не переоткрывать то, что «код доказал», поэтому на
каждом члене группы — включая правильный перевод — судья получал непрозрачный
дефект, которого не видит, и мог подавить реальную находку. MT/repair-путь
(`llm.py`) этот чек уже отбрасывал; судья — нет. Живо: `need-for-greed`
несёт 253 строки `repeat-drift`, 5 из них на юнитах с `judge-flag`.

Исправлено тем же приёмом, что и в `llm.py`: `build_request` исключает
`REPEAT_DRIFT_CHECK_ID` из `failing_checks` (`weblate/trans/judge_loop.py`),
тест `test_repeat_drift_is_not_sent_back_as_evidence` фиксирует поведение.
Регрессия `test_judge_loop.py` + `test_judge_round.py` — 128 passed.

## Решение

Расширять `repeat-drift` на новые проекты безопасно только после выкатки
правки судьи на прод. Порядок после выкатки: `heart-abyss` и `col4`
(заявлены), затем `anvil-saga`, `pirate-ships` по мере одобрения;
`space-arena` — после прогона `audit_glossary`, потому что его основной
дрейф живёт на стыке глоссария и обычных компонентов. Включение — флаг
`repeat-drift` на проекте плюс `updatechecks`, с сверкой фактического числа
строк с ожидаемым, по схеме фазы 7 роллаута.
