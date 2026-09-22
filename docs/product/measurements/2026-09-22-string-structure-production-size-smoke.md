<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Production-size smoke переименования и перемещения строк

**Дата:** 2026-09-22
**Ветка:** `feat/string-identity-authoritative-sync`
**План:** `docs/product/plans/2026-09-22-string-key-rename-and-reorder.md`

## Объём

Локальная проверка воспроизводила production-размер компонента Space Arena:

- 15 monolingual flat JSON stores;
- 4 876 ключей в template;
- 73 140 `Unit` rows в PostgreSQL;
- target subsets с физически отсутствующими ключами;
- один Rename и один Move через публичные методы `Translation`;
- реальный repository lock, запись JSON, parse-back, DB convergence и локальный
  Git commit;
- `push_on_commit=False`, production и shared dev instance не изменялись.

Quality checks компонента были отключены только при создании синтетической
fixture. Они не являются частью synchronous Rename/Move path и на повторном
setup создавали многоминутный проход по всем 73 140 строкам. Сами операции,
locks, stores, PostgreSQL updates, cache finalization и Git commits не
подменялись.

## Результаты

Первый model run обнаружил линейный N+1 update позиций:

| Фаза | Время |
| --- | ---: |
| Rename | 6,058 с |
| Move | 47,335 с |
| Всего | 53,393 с |

После замены отдельных `UPDATE` на `bulk_update(position, batch_size=1000)`:

| Фаза | Время |
| --- | ---: |
| Rename | 5,462 с |
| Move | 21,845 с |
| Всего | 27,308 с |

Итоговый synchronous run укладывается в установленный планом предел 30 секунд
и оставляет 10,99-кратный запас к production proxy timeout 300 секунд.

Отдельный format/Git probe тех же 15 stores без Django model setup занял:

| Фаза | Время |
| --- | ---: |
| Parse | 0,196 с |
| Mutation | 0,123 с |
| Write | 0,035 с |
| Parse-back | 5,446 с |
| Git commit | 0,053 с |
| Всего | 5,852 с |

## Вывод

Synchronous контракт подтверждён на согласованном production-подобном объёме.
Главная стоимость Move находится в блокировке и пакетном обновлении позиций всех
затронутых sibling Units; после устранения N+1 она остаётся внутри обоих
временных гейтов плана.
