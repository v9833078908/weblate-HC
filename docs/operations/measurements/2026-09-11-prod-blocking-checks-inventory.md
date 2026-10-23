<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Инвентаризация проваленных проверок на проде по check_id

**Дата:** 2026-09-11. **Инстанс:** `l10n.herocraft.com`, 10 проектов.
**Режим:** read-only, только `GET /api/units/?q=…&limit=1` (счётчики).
**Пробник:** `analysis/probes/prod-blocking-checks.py`, 95 check_id из этого
чекаута.

Замер снят **до** деплоя изменений 2026-09-11 (`1e2ff60`), то есть на проде
`game-length` ещё включён везде, а `repeat-drift` ещё не помечен advisory.
Разделение на advisory/blocking ниже — это проекция нового поведения на
сегодняшние данные, а не то, что прод показывает в UI.

## Итог по инстансу (срабатывания, не юниты)

| Проверка | Хитов | Тир |
|---|---|---|
| `repeat-drift` | 6911 | advisory |
| `end_stop` | 4184 | blocking |
| `multiple_failures` | 3314 | агрегат |
| `end_exclamation` | 2596 | blocking |
| `game-length` | 1943 | opt-in после деплоя |
| `ellipsis` | 1097 | blocking (source) |
| `end_question` | 1009 | blocking |
| `multiple_capital` | 271 | blocking |
| `end_interrobang` | 268 | blocking |
| `end_space` | 255 | blocking |
| `judge-flag` | 251 | вердикты судьи |
| `reused` | 188 | blocking |
| `same` | 175 | blocking |
| `game-number` | 124 | blocking |
| `cyrillic-leak` | 109 | blocking |
| `escaped_newline` | 107 | blocking |
| `duplicate` | 104 | blocking |
| `judge-note` | 96 | вердикты судьи |
| `end_ellipsis` | 88 | blocking |
| `judge-reject` | 51 | вердикты судьи |
| `game-markup` | 39 | blocking |
| `max-length` | 31 | blocking |
| `newline-count` | 26 | blocking |
| `punctuation_spacing` | 22 | blocking |
| `end_colon` | 21 | blocking |
| `double_space` / `game-line-break` / `translated` | 15 / 15 / 15 | |
| `end_newline` / `xml-tags` | 4 / 4 | |
| `inconsistent` / `begin_space` | 2 / 1 | |

**`repeat-drift` — крупнейшая одиночная проверка инстанса: 6911 хитов.**
Она advisory по построению: срабатывает на каждом члене группы одинаковых
исходников, включая корректный перевод. После деплоя эти 6911 уходят из
блокирующего счётчика.

## По проектам

| Проект | `has:check` | blocking-хитов | `repeat-drift` | `game-length` |
|---|---|---|---|---|
| anvil-saga | 10834 | 10240 | 3339 | 703 |
| pirate-ships | 2931 | 501 | 2089 | 401 |
| col4 | 2109 | 1062 | 621 | 528 |
| korotkij-test | 859 | 817 | 140 | 64 |
| heart-abyss | 774 | 580 | 255 | 19 |
| need-for-greed | 629 | 424 | 322 | 1 |
| dead-shell | 624 | 565 | 129 | 50 |
| space-arena | 188 | 26 | 0 | 162 |
| victory-banner | 159 | 136 | 14 | 15 |
| strategy-and-tactics-2 | 110 | 131 | 2 | 0 |

`space-arena`: 162 из 188 срабатываний — `game-length`, и `repeat-drift` там
выключен решением от 2026-09-04. После деплоя у проекта остаётся 26 хитов
`multiple_failures`, то есть счётчик проверок перестаёт быть фоновым шумом.

## Главная находка: `escaped_newline`, 107 строк

Класс, который ломает игру, а не косметику: литеральный `\n` исходника
пропал из перевода. Строка, которая должна печататься тремя строками,
печатается одной.

- `korotkij-test` — 66, `dead-shell` — 36, `anvil-saga` — 5.
- Больше всего в ja и ko: `\n` заменён пробелом или удалён вовсе.
- Есть и хуже: в ko 515718 и 535640, ja 514755 остался **оборванный
  escape** — `몬스터가 더 많은 피해를 줍니다.\적은 보조 도구.` и
  `ダメージを与えます.\より少ない援助キット.` Движок такое покажет как
  мусорный символ.
- Тот же класс дал единственный critical полного аудита Anvil Saga FR
  (519913, `docs/operations/audits/2026-09-11-anvil-saga-fr-lqa.md`): там
  наоборот, литеральный `\n` превратился в настоящий перенос строки, а
  строка-близнец 519914 экранирование сохранила.

`newline-count` (26) — соседний класс той же природы.

Weblate это видит **сегодня**, без единой доработки. Никто не смотрел,
потому что 107 хитов лежат под 6911 advisory и 1943 opt-in.

## Прочие блокирующие классы, которые стоит разобрать

- `same` 175 (`need-for-greed` 136): перевод совпал с исходником. Часть —
  легитимные имена собственные, лечится флагом `ignore-same`, но 136 на один
  проект — это не про имена.
- `cyrillic-leak` 109 (`need-for-greed` 82): кириллица в нелатинском целевом
  языке, то есть кусок русского уехал в перевод как есть.
- `game-markup` 39 и `xml-tags` 4: разметка не совпадает с исходником.
- `punctuation_spacing` 22, все в `korotkij-test`: французские неразрывные
  пробелы. После деплоя число вырастет — исправлена слепота проверки к
  сериям двойной пунктуации (`Quoi?!`).

## Что этот замер не говорит

- Это **срабатывания, а не юниты**: одна строка может провалить несколько
  проверок, поэтому суммы по проекту больше числа строк. `multiple_failures`
  считает именно такие строки и в сумму блокирующих входит как агрегат.
- Ложноположительные не отделены. Для `end_*` доля ложных на игровом
  диалоге известна по аудиту Anvil Saga и невелика, для `same` и `duplicate`
  — наоборот, велика.
- `judge-*` (398 суммарно) — это вердикты судьи, а не детерминированные
  проверки; судья запускался только на `heart-abyss`, `need-for-greed`,
  `col4`, `victory-banner` и `strategy-and-tactics-2`.

## Воспроизведение

```sh
python3 analysis/probes/prod-blocking-checks.py
```

Токен читается из `PROD_WEBLATE_API_TOKEN` в окружении или из `.env.local` в
корне репозитория. ~13 минут, 10 × 95 запросов, ничего не пишет.
