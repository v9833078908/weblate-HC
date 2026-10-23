# Pirate Ships: черновик сведения Weblate с beta (2026-09-24)

Черновик шага 2 из
`docs/operations/plans/2026-08-15-pirate-ships-production-cleanup-json-migration.md`.
Только чтение: в Weblate и в игровой репозиторий ничего не записано. После
заморозки пересчитать на свежих снимках тем же скриптом, иначе список
устареет: Weblate менялся даже во время этой сверки.

## Источники

| Сторона | Снимок |
| --- | --- |
| beta | `Tempest_f2p` `2026-09-15_beta` `de967f5b1fe095dbdcb5e5591192bdfcd22a0925`, 16 файлов `Assets/Resources/Data/Localization_*.json` через REST SCM-Manager `content` |
| Weblate | production ZIP `pirate-ships/localization-json`, 2026-09-24 10:53, SHA-256 ZIP `8b10c3621dff6d06390834734ff35b1744522149e595b985df32867972682fb3` |
| Даты изменений в Weblate | `last_updated` расходящихся единиц через `GET /api/translations/pirate-ships/localization-json/<lang>/units/` и журнал `GET /api/projects/pirate-ships/changes/` |

Пересчёт:

```sh
python3 analysis/probes/pirate-ships-reconcile.py BETA_DIR WEBLATE_DIR \
  analysis/data/pirate-ships-reconciliation-<date> [UNITS_JSON]
```

Даты коммитов beta получить не удалось: `changesets` и `changeset` в
SCM-Manager не ответили за 40-60 с. Поэтому правила ниже не опираются на
то, какая сторона изменена позже, кроме правок Weblate за 2026-09-24.

## Итог

| Действие | Количество | Файл |
| --- | --- | --- |
| Переименовать ключ | 277: 275 `dialog_*` -> `dialogue_*` и 2 `*crew_captain_heal_throw` -> `*captain_heal_throw` | `keys.csv`, `rename` |
| Добавить ключ из beta | 158, из них 12 `description_resourse_tips_*` без перевода ни на один язык | `keys.csv`, `add` |
| Удалить ключ | 8 | `keys.csv`, `delete` |
| Разные значения | 595 ячеек | `cells.csv` |

## Правила для ячеек

Колонка `take` - предлагаемое значение, `rule` - почему.

| `rule` | Ячеек | Решение |
| --- | --- | --- |
| `cleanup` | 447 (443 в переименованных диалогах) | Weblate: отличие только в `...`/`…`, пробелах, неразрывных пробелах во `fr` или финальной точке - результат августовского cleanup |
| `ru-ellipsis` | 22 RU | Weblate: `...` -> `…` в RU, августовская нормализация source |
| `ru-english-in-weblate` | 4 RU | beta: в Weblate вместо русского исходника английский текст |
| `ru-team-edit` | 4 RU | beta: команда поправила RU (`течени` -> `течение`, `завершен` -> `завершено`, `а так же` -> `а также`, точка в `territorial_wars_info_lighthouse_desc`) |
| `follows-ru` | 32 | beta: переводы ключа, чей RU берётся из beta |
| `filled-in-weblate` | 14 | Weblate: в beta `""`, в Weblate перевод есть |
| `newer-in-weblate` | 8 | Weblate: изменено в Weblate 2026-09-24, в beta ещё нет |
| `review` | 64 | beta по умолчанию: при неясности остаётся то, что игра уже показывает. Нужен просмотр |

Что внутри `review`:

- `tutorial_hint_soldier_card_select`, `tutorial_hint_rifleman_card_select2`
  (30 ячеек): в beta переводы повторяют разметку RU, а в Weblate с
  2026-09-22 другие версии без `<size=14>`, в статусе «требует
  переписывания». Предлагается beta. Сама разметка RU вложена неверно
  (`<size=14><b>…</size>…</b>`) - это дефект исходника для команды.
- `ability_description_items_penetration` `kr`: в Weblate русский текст
  вместо корейского; beta верна.
- `ability_description_shield_steel_damage_null` `cn`: в Weblate перевод
  другого умения; beta соответствует RU.
- `tw_item_name_building_gold_mine` `cn/kr/jp`,
  `territorial_wars_info_gold_mine_desc` `jp`, `6_use_amulets` `nl`,
  `description_resourse_tips_not_enough_captains_medals` `en`: обе версии
  допустимы, выбор редакторский.
- Переименованные ключи (26 ячеек: 24 в диалогах и 2 в
  `ability_description_captain_heal_throw` `th/kr`): в beta значения из
  старого файла, в Weblate после августовской обработки; в диалогах
  расходятся знаки в конце (`!`/`.`, `……`/`。`, `?`/`.`). Решать по каждой
  строке.

## Что отправить команде игры

1. **`tw_item_name_building_portal`.** В beta RU «Ворота Столицы», EN
   «Capital Gate». В Weblate 2026-09-24 07:52 RU заменён на английский
   «Castle Gate», и все языки переведены заново с английского. Что
   задумывалось: переименовать в «Ворота замка» или только поправить EN?
2. **Английский в RU.** `daily39_description`, `cbp_use_amulet_battle`,
   `cbp_use_amulet_battle2` получили английский RU в Weblate (коммит
   2026-09-15). Берём русский из beta.
3. **Многоточие в RU.** В 22 ключах Weblate хранит `…` вместо `...`
   (решение августовского cleanup); после переноса RU в beta тоже изменится.
4. **Разметка tutorial-подсказок** в RU вложена неверно, см. выше.

## Наблюдения о процессе

- В проверенных окнах журнала правки сделаны пользователем `Swift`:
  2026-09-22 09:01 загрузка файлов (939 строк обновлено, 16 удалено);
  2026-09-24 07:50-07:53 изменено 15 исходных строк, затем 33 автоперевода
  `mt:openrouter`.
- Английский текст в RU совпадает с триггером скилла
  `weblate-push-translate` «обнови перевод <id> <new_value>»: у скилла один
  текстовый параметр, и он записывается как русский исходник. Это вывод
  по журналу, скрипты скилла не проверялись.
