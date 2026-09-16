# Кандидаты корпуса для RU+EN → zh-Hans

Снимок production: **2026-09-16 07:34 UTC** (10:34 MSK). Это read-only
инвентаризация для согласования корпуса; ни переводы, ни LLM-задачи, ни
изменения в production не запускались. Китайские строки в этом отчёте не
считаются gold/reference.

## Что есть в production

В API сейчас 12 проектов. Состав: CoL4 (`LocalizeCommon`, `data`,
`all-glossary`); Pirate Ships (`Localization`, `Glossary`); Heart Abyss
(`temple`, `hub-1`, `all-glossary`); Strategy and Tactics 2 (`summer-update-en`,
`glosssary`); Need For Greed (Buyers, CharacterDialogue, Loot, Orders, Survey,
Tutorial, UI, glossary, Google Play); Space Arena (`lockit`, glossary); Victory
Banner (general, glossary); Anvil Saga (`lockit`, glossary); Dead Shell
(`localization`, glossary); test project pi (`Localization_tst`, glossary).
Curse of Pirates и Chronocracy пока не имеют компонентов.

У всех перечисленных рабочих проектов включён `translation_review`; у всех
`source_review=false`. Исключение -- тестовый `test-project-pi`, где review
выключен. Поэтому состояние source RU не является доказательством вычитки, а
`state=20` у EN означает только «переведено, ожидает review», не вычитку.

Ниже `T/A` означает число translated / approved по метаданным translation.
Approved входит в translated; складывать эти числа не нужно.
`--` означает, что языка в компоненте нет.

| Проект / компонент | Исходный | RU | EN | zh-Hans | Жанр / замечание |
| --- | --- | ---: | ---: | ---: | --- |
| [Pirate Ships / Localization](https://l10n.herocraft.com/projects/pirate-ships/localization-json/) | RU | 3893/0 | 3892/0 | -- | общий JSON |
| [Heart Abyss / temple](https://l10n.herocraft.com/projects/heart-abyss/temple/) | RU | 688/0 | 641/0 | -- | общий |
| [Heart Abyss / hub-1](https://l10n.herocraft.com/projects/heart-abyss/hub-1/) | RU | 396/0 | 394/0 | 395/0 | общий, контекст у всех строк |
| [Need For Greed / CharacterDialogue](https://l10n.herocraft.com/projects/need-for-greed/characterdialogue/) | RU | 25/0 | 25/0 | -- | диалоги |
| [Need For Greed / Loot](https://l10n.herocraft.com/projects/need-for-greed/loot/) | RU | 154/0 | 154/0 | -- | предметы |
| [Need For Greed / Tutorial](https://l10n.herocraft.com/projects/need-for-greed/tutorial/) | RU | 102/0 | 102/0 | -- | туториал |
| [Need For Greed / UI](https://l10n.herocraft.com/projects/need-for-greed/ui/) | RU | 463/0 | 463/11 | -- | UI |
| [Space Arena / lockit](https://l10n.herocraft.com/projects/space-arena/lockit/) | EN | 4871/0 | source | -- | не RU-source корпус |
| [Victory Banner / general](https://l10n.herocraft.com/projects/victory-banner/general/) | RU | 537/0 | 537/11 | 535/0 | общий |
| [Anvil Saga / lockit](https://l10n.herocraft.com/projects/anvil-saga/locale_v1-02-import-explained-3/) | RU | 9482/0 | 9482/0 | 9481/0 | большой общий корпус |
| [Dead Shell / localization](https://l10n.herocraft.com/projects/dead-shell/localization/) | RU | 963/0 | 963/0 | -- | общий |

Остальные неглоссарные RU/EN-компоненты: CoL4 `LocalizeCommon` и `data` --
только RU; Victory Banner, Anvil Saga, Need For Greed, Heart Abyss, Pirate Ships
и Dead Shell имеют свои TBX-глоссарии. В трёх языках присутствуют TBX у Need For
Greed (300), Space Arena (339), Victory Banner (156) и Anvil Saga (184). Это
терминологические таблицы, а не корпус UI/диалогов, поэтому они не входят в
основные кандидаты. Наличие связанного glossary -- полезный контекст для
будущей QA, но не свидетельство вычитки строк.

## Проверенное выравнивание и происхождение

Стабильный ключ -- пара `(id_hash, context)`: `id_hash` уникален внутри
translation, а context оставлен в ключе как явная защита от ошибок импорта.
Для двух малых кандидатов последовательно прочитаны все unit pages трёх
языков; в отчёт не записывались тексты.

| Компонент | Уникальных ключей RU / EN / zh | Пересечение всех трёх | Дубликаты | Пропуски RU/EN/zh в пересечении | EN: state 20 / state 30 | zh-Hans: state 20 / 11 / 30 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Heart Abyss / hub-1 | 396 / 396 / 396 | 396 | 0 / 0 / 0 | 0 / 0 / 0 | 394 / 0 | 395 / 1 / 0 |
| Victory Banner / general | 537 / 537 / 537 | 537 | 0 / 0 / 0 | 0 / 0 / 0 | 526 / 11 | 535 / 2 / 0 |

Heart Abyss даёт 394 выровненные пары с заполненным RU и EN;
Victory Banner -- 537 пар. В Heart Abyss у всех 396 unit есть `note` и context; comments, labels,
suggestions и explanations отсутствуют. В Victory Banner context есть у всех
537, а notes/comments/labels/suggestions/explanations отсутствуют. Это важно:
context существует, но сам по себе не даёт редакторского provenance.

Для Anvil Saga метаданные дают 9 482 RU, 9 482 EN и 9 481 zh-Hans; запросы
`state:approved` дали 0 во всех трёх языках, `state:translated` -- 9 482 / 9
482 / 9 481, `has:comment` -- 0, `has:note` -- 330. Полную выгрузку ключей
умышленно остановили после 2 250 последовательных RU-строк: production
ограничивает страницу 50 строками, и полное трёхъязычное пересечение потребует
около 570 GET. Следовательно, число **9 481 не объявляется подтверждённым
пересечением**: перед включением Anvil в эксперимент нужен отдельный
read-only preflight выравнивания.

Текущие китайские результаты не имеют approved-строк: Heart Abyss -- 0,
Victory Banner -- 0, Anvil Saga -- 0. В Heart Abyss 374 zh-Hans unit помечены
как automatically translated; в Victory Banner -- 537. Они пригодны только как
существующее production-состояние/материал для последующей диагностики, но не
как эталон для оценки качества нового вывода.

## Кандидаты для согласования

1. **Рекомендуемая на согласование жанровая смесь: 120 строк.** Need For Greed:
   UI 30 + Tutorial 30 + Loot 30; Heart Abyss / hub-1: dialogue 30. Отсутствие
   исторического zh-Hans в Need For Greed не ограничивает генеративный
   эксперимент: эти прошлые zh-Hans результаты всё равно не являются gold.
   У Need For Greed есть RU и полностью заполненный EN по metadata, но
   выравнивание всех ключей `(id_hash, context)` ещё не проверялось; до
   фиксации списка нужен read-only preflight. Для Heart Abyss выравнивание
   подтверждено. Ни один из этих 120 unit пока не имеет подтверждённого
   human-review provenance.
2. **Проверенная альтернативная смесь: Victory Banner 90 + Heart Abyss 30.**
   Все 537 ключей Victory Banner и все 396 ключей Heart Abyss трёхъязычно
   выровнены. Это даёт более широкий общий срез, но EN approved-signal в
   Victory Banner есть только у 11 строк, а у Heart Abyss отсутствует.
3. **Минимальный проверенный seed: Victory Banner / general, 11 строк.** Все
   537 ключей трёхъязычно выровнены; из них ровно 11 EN имеют Weblate
   `state=30` (approved). Seed удобен для проверки процесса, но размер слишком
   мал для сильного вывода о gain.
4. **Крупный будущий корпус: Anvil Saga / lockit, до 9 481 потенциальных
   трилингвальных ключей.** Он даёт масштаб и 330 строк с note, но сейчас не
   имеет EN approved и exact alignment ещё не подтверждён. Разумная будущая
   выборка -- предварительно согласованный стратифицированный срез после
   read-only preflight, а не весь компонент.

Need For Greed полезен как отдельный жанровый контроль (UI 463, tutorial 102,
loot/items 154, dialogue 25; всего 744 RU+EN строк по metadata), но zh-Hans в
этих компонентах отсутствует. EN approved: UI 11, остальные три -- 0; notes и
comments в EN -- 0. Это не препятствие для нового zh-Hans вывода; ограничение
состоит только в отсутствии заранее проверенного historical zh-Hans reference.

## Как измерять gain второго опорного языка после выбора

Этот снимок **не измеряет gain**: пользователь запретил запуск тестов, а
существующий zh-Hans не gold. Полный дизайн эксперимента A--F зафиксирован в
`docs/product/designs/2026-09-16-dual-reference-zh-experiment.md`; после
согласования корпуса следует выполнять именно его. EN `state=20` нельзя
называть human-reviewed.

Даже EN `state=30` подтверждает лишь Weblate approved-state. Чтобы назвать
его «вычитанным человеком», владелец корпуса должен подтвердить происхождение
11 Victory Banner строк и допустимость их использования. До такого
подтверждения кандидаты описаны как структурно пригодные, а external/human
review provenance для них не подтверждён.

## Воспроизведение

Токен был прочитан программно из `PROD_WEBLATE_API_TOKEN` в `.env.local` или
`deploy/.env.local` и никогда не выводился. Последовательные GET к
`/api/projects/`, `/api/projects/{project}/components/`,
`/api/components/{project}/{component}/translations/` дали inventory и
coverage. Для Heart Abyss и Victory Banner последовательно читались
`/api/translations/{project}/{component}/{language}/units/?limit=500`, после
чего агрегировались только ключи и поля состояния/контекста. Для Anvil Saga
использовались metadata и bounded GET с `q=state:approved`,
`q=state:translated`, `q=has:comment`, `q=has:note`; raw unit texts не
сохранялись.
