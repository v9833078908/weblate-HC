# COL4 fr: разметка B0 — error analysis реального вывода

Дата: 2026-08-12. Задача B0 плана
`docs/LLM-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md`.

Разметчики: Fable (Anthropic) — исходные 260 строк;
`openai-codex-gpt-5.6-terra-2026-08-12` — top-up из 300 строк. Человек-
переводчик недоступен. B2 сравнивает результаты судей отдельно по
семейству разметчика и по конструктивным стратам, независимым от него.

## Данные и транспорт

- Корпус: `col4/data/fr` **локального dev-инстанса** — полное зеркало
  прод-прогона 2026-08-11 (Celery task `ff7843b4`, 3941/3941 строк,
  `last_change 2026-08-11T09:20Z`). Прод не затрагивался: анонимный
  API прода отвечает 401, прод-токена в репозитории нет, SSH-доступ к
  проду для выгрузки не использовался.
- Выгрузка: read-only через `weblate shell` **dev**-контейнера
  (`docs/misc/col4-b0-dump.py` -> `dev-docker/data/col4-b0-units.jsonl`),
  глоссарий dev `col4/glossary` fr: 153 пары.
- Нормализация in-flight (по плану B1): `fix_target()` +
  `RemoveAddedFinalStop` + реплика `!?:`-автофикса (quick-wins,
  задача 1) + `AddFrenchPunctuationSpacing`. Итог по корпусу:
  2091 снятая добавленная точка, 47 вставок NBSP/NNBSP, 14 снятий
  добавленного `!?:`.
- Разметка велась по нормализованному таргету: судью нельзя
  калибровать на дефектах, которые слой 0 снимает детерминированно.

## Протокол

Исходная выборка: 260 юнитов, seed 20260812, страты:

| Страта | n | Правило |
| --- | --- | --- |
| random-clean | 212 | 0 failing checks, наивный глоссарный матч чист |
| check:* | 28 | стратифицированно по типам чеков (кэп на тип) |
| gloss-miss | 10 | наивный промах глоссария (containment, без стемминга) |
| longest | 10 | самые длинные таргеты |

Дополнение 2026-08-12: 300 ранее неразмеченных кандидатов
`random-clean-topup-20260812`. Равномерная выборка без возвращения с тем же
seed 20260812 из 1 409 юнитов, исключая уже размеченные, строки с
`failing_checks`, наивные промахи глоссария и юниты терминологической страты
B1. Метки: 253 pass / 47 defect (26 minor / 20 major / 1 critical).
Разметчик top-up: `openai-codex-gpt-5.6-terra-2026-08-12`; каждая запись
сохраняет annotator для раздельной калибровки по семействам моделей.

На юнит: вердикт pass/defect; для дефекта — MQM-теги
(terminology / accuracy / omission / fluency / punctuation / markup),
severity (critical/major/minor), span, критика. Улики разметчика:
source ru, глоссарий (term -> rendering), note, failing checks.
Граница pass: «профессиональный fr-локализатор игр опубликует без
правки». Полные разметки: `docs/misc/col4-b0-annotations.jsonl`
(исходные 260 строк) и
`docs/misc/col4-b0-annotations-topup-20260812.jsonl` (300 строк); поля
label/mqm/severity/span/critique + provenance.

## Сводка

| Метрика | Значение |
| --- | --- |
| Всего размечено | 560 |
| pass | 436 (77.9%) |
| defect | 124 (76 minor / 46 major / 2 critical) |
| MQM-теги дефектов | accuracy 61, fluency 50, terminology 28, punctuation 8, omission 3, markup 2 |
| **Чистый пул: pass** | **419/512 = 81.8%, Wilson 95% CI [78.3%, 84.9%]** |
| Чистый пул: скрытые major | 25 |
| longest: дефектных | 9/10 (6 major) |
| gloss-miss: дефектных | 10/10 (9 major) |

Стабильность: исходная первая волна 119/152 = 78.3%, исходный топ-ап
47/60 = 78.3%; новая выборка 253/300 = 84.3% pass. Сдвиг положительный,
но CI двух волн перекрываются, поэтому не интерпретируется как улучшение
переводчика.

## Ключевые выводы

1. **«0 failing checks + глоссарий» != чистая строка: 21.7% дефектов,
   включая 5 major.** Гипотеза плана подтверждена измерением: чистая
   страта B1 обязана строиться на метке разметчика, иначе скрытые
   дефекты считались бы false-flag судьи и калибровали бы его в
   снисходительность.
2. **Длина — сильнейший предиктор дефекта**: 9/10 самых длинных строк
   дефектны (6 major). B1/B2 стратифицировать по длине; синтетика на
   коротких строках занизит сложность.
3. **Фрагментация реалий — главный терминологический режим отказа.**
   ГИГАХРУЩ отрендерен «Gigastructure», «Giga-Chrunch», «Gigahrush»,
   «Giga-Khrouchtch», «Gigakrouch» (5+ вариантов в одной игре);
   Гермодверь — «porte étanche/anti-explosion/blindée»; САМОСБОР —
   «SAMOSBOR/auto-assemblage/auto-assemblé/ASSEMBLAGE»; Партия ->
   «Parti/parti/Faction»; Юля -> «Yulia/Julia»; Старейшина ->
   «Ancien/aîné». Судья с глоссарием в промпте ловит это на юните;
   без глоссария — не поймает никто.
4. **tu/vous — системный сбой голоса**: tu-нарратив против
   vous-конвенции (181347, 181353, 181354), vous между сверстниками
   (179330), vous от учителя ребёнку (181141), tutoiement источника
   на «Вы» (178323). Конвенции корпуса обязаны попасть в улики судьи.
5. **Регистр**: добавленная обсценность (180782 «putain de boulot»),
   выхолощенная маскированная обсценность (179485). Рейтинговый риск.
6. **Идиоматические коллизии кальки**: «c'est tout elle» (177800),
   «toucher du doigt» (180094), «bon plan» (180600), «suite dans les
   idées» (180381) — детерминированно не ловятся, материал для
   few-shot судьи.
7. **Выдуманные единицы сеттинга заменяются бытовыми**: «три
   семисменка» -> «trois quarts d'heure» (179603) — масштаб врёт на
   порядки, юмор уничтожен.
8. **Порча буквенно-цифровых кодов**: кир. В -> лат. B (178484:
   коридор 81В слился с блоком 245Б), Н -> H (181049 «Mu-R1-H0»).
   Детерминируемо проверяемо.

## Слепые пятна слоя 0 (кандидаты в quick wins, требуют согласования)

- **Кириллица в fr-таргете не проверяется ничем**: 180318
  («6ЕСП0ЛЗНА9_БМЖКА3532» остался кириллицей, потерян каламбур
  «бесполезная бумажка»), 180681 («Чернобог te dévore !») — оба с
  0 failing checks. Регэкс-чек тривиален и детерминирован.
- **punctuation_spacing молчит на кластерах `?!`/`!?`**: 180025
  («mordre!?»), 180299 («Père?!») — NNBSP отсутствует, чек не сработал.
- **Реестр автофиксов dev-контейнера отстал от compose**: env
  контейнера содержит только `LineSeparatorSpacing`, хотя
  `docker-compose.yml` объявляет три (контейнер создан до правки;
  нужен полный `./rundev.sh`). Прод при прогоне 2026-08-11 имел
  `RemoveAddedFinalStop` — расхождение сред фиксировать при сборке B1.
- Урок для quick-wins задачи 1: условие снятия `!?:` обязано
  учитывать закрывающие кавычки источника («…Еретик!\"») — наивная
  реплика без этого снимала законный терминал (2 юнита на корпус);
  штатная реализация через `EndStopCheck.check_single` это покрывает.

## Сверка классов мутаций B1 с реальностью

| Класс из плана | Статус | Основание |
| --- | --- | --- |
| порча/потеря `@@PHn@@` (critical) | **неприменим к col4** | `@@PH` в корпусе: 0 вхождений; класс оставить для компонентов с плейсхолдерами |
| инверсия смысла/отрицание (critical) | **подтверждён** | 179849 (следовать->mener), 180322 (актор), 179557 (скоуп отрицания), 180800 (тип вопроса) |
| непереведённая кириллица (critical) | **подтверждён** | 180318, 180681 — оба реальные, оба мимо чеков |
| потеря числа (major) | гипотетический | в 260 юнитах не встречен (цифры в 61 юните корпуса) |
| пропуск куска (major) | слабо подтверждён | потери кавычек-рамок 179603, 180448; крупных пропусков нет |
| подмена глоссарного термина (major) | **подтверждён, богато** | фрагментация реалий, п. 3 выводов |

Новые классы для списка мутаций (детерминированно синтезируемы):

- **порча буквенно-цифрового кода** (major): подмена одной буквы кода
  (В->B, Н->H) — по образцу 178484/181049;
- **смена лица tu/vous** (major): Vous->Tu в нарративной строке — по
  образцу 181347;
- **вброс обсценности** (major): вставка «putain de» перед
  существительным — по образцу 180782.

Идиоматические коллизии и потерю каламбуров синтезировать нельзя —
они уходят в few-shot улики промпта судьи, не в мутации.

## Выходы B0

- **Чистая страта B1: 419 юнитов** (label=pass, random-clean и
  random-clean-topup-20260812), сплиты 63 train / 189 dev / 167 test.
  Цель n >= 150 на измеряемом test-срезе закрыта; `unit_id` и annotator
  хранятся в JSONL.
- База для мутаций: те же 419 чистых строк, строго внутри их сплита.
- Дрейф повторов для трека C: «Следить» -> Surveiller (180745) /
  Observer (180646), papa/Père (178838/178852), fauteuil/chaise
  (179603/180974) — реальные пары для пробника.
- Конвенции корпуса для промпта судьи (B2): vous-нарратив,
  инфинитив в ANSWER_*, нарративный презенс в RESULT_*, глоссарий с
  реестром реалий, NNBSP перед двойной пунктуацией.

## Приложение: pass-заметки

- 177707: «Бетоноточка меня побери» -> «Sainte tôle ondulée» — транскреация уместна; следить за консистентностью, если это сквозная реалия
- 178044: «Pionnier» с заглавной здесь vs «pionnier» в 177707 — корпусная непоследовательность, юнит сам по себе валиден
- 178072: после нормализации (снята добавленная точка) дефектов нет
- 178105: длинный нарратив передан точно; «Khrouchtchevka» — верная транслитерация
- 178201: после нормализации NNBSP добавлен
- 178324: разговорное «чё» слегка сглажено — приемлемое смягчение регистра
- 178422: «vétéran du travail» — намеренная калька советской реалии, уместна в сеттинге
- 178477: reused-чек — свойство корпуса (один таргет на разные источники), сам юнит валиден
- 178552: «tord-boyaux» — отличная находка для «самогона», обрыв фразы сохранён
- 178643: мем «Ленин — гриб» сохранён, читается
- 178826: после !?:-реплики терминал снят, чисто
- 178862: «marqué votre présence» — лёгкая калька, но контекст проясняет схему; публикуемо
- 179228: «Nom de Zeus» — эвфемизм на эвфемизм, регистр сохранён
- 179290: «Finissez-le» vs «Achever» в 179280 — вариативность глагола для «добить», события разные, допустимо
- 179417: анаколуф источника грамотно выправлен
- 179440: «rester sur le sol» после падения = остаться лежать; смысл цел
- 179518: нарративный презенс — конвенция корпуса, смена времени оправдана
- 179547: «VOUS» капсом — рабочая передача почтительного «Вы» с эмфазой
- 179660: «de toutes vos forces» vs «soi» в 179478 — лёгкий разнобой лица в ANSWER-конвенции, юнит валиден
- 179802: «gosses» — допустимое разговорное усиление в крике
- 179906: «condensateur de flux» — узнаваемый вариант отсылки к BTTF (канон дубляжа «convecteur temporel», но литеральный вариант в ходу)
- 180075: «sont après moi» — разговорное, «à mes trousses» чище; публикуемо
- 180164: «Jelenin» — корректная франц. транслитерация Желенина, цитата узнаваема
- 181003: источник без «?» — терминал снят реплика-фиксом, зеркально источнику
- 181158: «портфель» точнее «cartable» в школьном сеттинге, но «sac» публикуемо
- 181509: «Liquidateurs» с заглавной в названии пути — приемлемый тайтл-кейс UI
- 181550: «Savant Tortionnaire» — удачный тайтл для «Учёного Изувера»
- 181601: «Картёжник» с азартным оттенком слегка нейтрализован — для тайтла публикуемо
- 180002: «gigoter» точен для младенческого контекста EVENT_6
- 180646: «Следить» -> Observer здесь vs Surveiller в 180745 — дрейф повторов, материал для трека C
- 180747: голое «Lance» без объекта шероховато, но в игровом окрике публикуемо
- 181488: «Maîtresse» — точный школьный термин
- 181507: Б->B корректно по GOST (ошибка в 178484 была именно В->B)
- 181532: «Camarade de combat» — советский флейвор уместен, хоть идиома «compagnon d'armes»

## Приложение: все дефекты (77)

| unit_id | страта | sev | MQM | span | критика |
| --- | --- | --- | --- | --- | --- |
| 179051 | check:game-line-break | critical | markup | `Très bien dit ! Meurs !` | потерян движковый разделитель $ (game-line-break чек это и поймал) — класс мутаций «порча разметки» подтверждён реальным дефектом |
| 177800 | random-clean-topup | major | accuracy | `C'EST TOUT ELLE !` | «Это всё она!» (обвинение) столкнулось с идиомой «c'est tout elle» = «это так на неё похоже»: «C'EST ELLE, TOUT ÇA !» |
| 178243 | gloss-miss | major | terminology | `porte anti-explosion` | глоссарий: Гермодверь -> porte étanche; «porte anti-explosion» — незакреплённый вариант сквозной реалии |
| 178484 | gloss-miss | major | terminology,accuracy | `auto-assemblage; couloir 81B` | глоссарий: самосбор -> Samosbor — «auto-assemblage» разрушает ключевую реалию; плюс «81В» (кир. В->V) слито с «245Б» (Б->B) в один «B» — коллизия игровых идентификаторов |
| 178672 | gloss-miss | major | terminology,fluency | `auto-assemblage, vous desséchant` | снова auto-assemblage вместо Samosbor (глоссарий); плюс причастие «vous desséchant» цепляется к «vous», а не к самосбору — надо «qui vous dessèche» |
| 179231 | check:end_exclamation | major | accuracy,terminology | `espèce de déchet auto-assemblé` | проклятие «самосбор тебя подери» превращено в оскорбление адресата («ты — авто-собранный мусор») — смысловой сдвиг; и снова разрушена реалия Samosbor |
| 179330 | gloss-miss | major | terminology,fluency | `Julia; Vous avez` | глоссарий: Юля -> Yulia, в 177795 так и есть — «Julia» ломает имя сквозного персонажа; плюс «вы» между сверстниками — надо «Tu as trop de chance !» |
| 179603 | longest | major | accuracy | `pendant trois quarts d'heure` | «три семисменка» — выдуманная единица времени сеттинга, а не «45 минут»: масштаб врёт на порядки, мрачный юмор источника уничтожен; плюс потеряна кавычка-рамка дневниковой записи |
| 179618 | check:newline-count | major | terminology,markup,accuracy | `Auto-assemblage; \n\n` | Samosbor снова разрушен (глоссарий); _x000b_ источника заменён на \n\n — newline-count чек ловит детерминированно; «куковать» (пересидеть) -> «trimer» (вкалывать) — сдвиг |
| 179752 | gloss-miss | major | terminology | `Giga-Chrunch` | глоссарий: ГИГАХРУЩ -> GIGASTRUCTURE (в 179524 соблюдён); «Giga-Chrunch» — бессмысленная искажённая транслитерация, третий вариант реалии в корпусе |
| 179849 | check:end_stop | major | accuracy | `Mener tout le monde` | инверсия роли: «следовать за всеми» = suivre, а не mener — игрок ведёт вместо того чтобы идти следом; живой пример класса мутаций «инверсия смысла» |
| 180215 | gloss-miss | major | terminology | `un ASSEMBLAGE` | глоссарий: САМОСБОР -> SAMOSBOR; уже четвёртый вариант реалии в корпусе (auto-assemblage, auto-assemblé, ASSEMBLAGE) |
| 180274 | gloss-miss | major | terminology | `L'aîné` | глоссарий: Старейшина -> Ancien (в 180448 соблюдён); «l'aîné» = старший брат — другая роль |
| 180322 | random-clean | major | accuracy | `d'obtenir cette référence par elle-même` | актор перепутан: Юля просит ВАС получить справку самим, fr = «получить самой» (и тут же «Il a fallu s'exécuter» противоречит); «справка» = attestation, не référence |
| 180621 | gloss-miss | major | terminology | `auto-assemblage; porte blindée` | двойной глоссарный пробой: Samosbor + Гермодверь -> porte étanche (уже третий вариант: anti-explosion, blindée) |
| 180782 | check:end_stop | major | accuracy | `ton putain de boulot` | добавлена обсценность, которой нет в источнике — эскалация регистра с рейтинговыми рисками |
| 180800 | check:end_interrobang | major | accuracy | `Mais comment ?` | «Ну как?!» — вопрос о результате («Alors ?!»), fr спрашивает о способе; плюс потерян интерробанг (чек поймал) |
| 180875 | longest | major | terminology,fluency | `Gigahrush; serait insufflé` | глоссарий: ГИГАХРУЩ -> Gigastructure; «Gigahrush» ×3 — уже пятый вариант реалии; и «вселятся» -> «insufflé dans lequel» — ломаный синтаксис |
| 180883 | longest | major | terminology,fluency | `La Faction; Seule moi peux` | глоссарий: Партия -> Parti (в 179547/180158 соблюдён) — «La Faction» ломает реалию; «Moi seule peux» / «Je suis la seule à pouvoir» |
| 180974 | longest | major | terminology,accuracy | `Gigahrush; le prenez dans vos bras; la chaise` | опять Gigahrush (глоссарий GIGASTRUCTURE); «берёте на руки» -> «prendre dans ses bras» = «обнять» — сцена искажена комично; «кресло» экстрактора = fauteuil (179603), тут chaise |
| 180998 | longest | major | terminology | `le Giga-Khrouchtch` | шестой вариант реалии ГИГАХРУЩ в корпусе (Gigastructure, Giga-Chrunch, Gigahrush, Giga-Khrouchtch…) — фрагментация ключевого имени мира |
| 181299 | gloss-miss | major | terminology | `la communauté` | глоссарий: община -> Commune — реалия секты, «communauté» её растворяет |
| 181347 | random-clean | major | fluency | `Tu te diriges` | нарратив корпуса — vous («Vous restez…», «vous voyez…»); tu-нарратив ломает голос рассказчика |
| 181353 | check:end_colon | major | fluency | `Le gamin t'a suivi… te dit` | тот же tu-нарратив + passé composé в презенс-нарративе; «за рубашку» -> «par le col» допустимо |
| 181354 | random-clean | major | fluency | `Tu n'as senti` | tu-нарратив (класс 181347); «почуяли» -> «flairé» точнее, но не блокер |
| 181391 | longest | major | terminology,fluency | `Gigakrouch; être sentient` | седьмой вариант ГИГАХРУЩ; «sentient» — англицизм: «être doué de raison» |
| 181615 | random-clean | major | accuracy | `Bannière de la Patrie` | «Хозяйство» — реалия сеттинга, не «Patrie»: подмена сущности + внесённый национальный пафос; «вынос знамени» = «avoir porté la bannière» |
| 177719 | random-clean | minor | accuracy | `la compote` | компот — напиток; fr «compote» — фруктовое пюре (faux ami); ожидаемо «le kompot» |
| 177795 | check:duplicate | minor | fluency | `Hé, hé, hé` | «Ай-яй-яй» — укоризна; «Hé, hé, hé» читается как смешок/оклик — сдвиг тона; ожидаемо «Oh là là» / «Tss tss» |
| 177823 | random-clean-topup | minor | accuracy,fluency | `Repasser de toutes mes forces` | «гладить» без контекста события двусмысленно (caresser?); «repasser что есть сил» абсурдно — требует сверки с событием; и «mes» рвёт vos/soi-конвенцию ANSWER-строк |
| 177848 | random-clean | minor | fluency | `si officiel` | калька; о регистре речи по-французски — «si formel» / «tant de formalités» |
| 177916 | random-clean | minor | fluency | `Ne résistez pas` | ANSWER-строки корпуса — инфинитив («Prendre…», «Sauter…»); императив ломает конвенцию: «Ne pas résister» |
| 177919 | random-clean-topup | minor | fluency | `Frapper brusquement la hampe sur votre pied` | инструмент и цель перепутаны местами: «abattre la hampe sur votre pied» |
| 178123 | random-clean | minor | accuracy | `le bureau` | «парта» в школьном сеттинге — «pupitre»; «bureau» читается как учительский/офисный стол |
| 178203 | random-clean | minor | terminology | `Krouchtchevka` | транслитерация без h: Хрущёв -> Khrouchtchev, значит «khrouchtchevka»; в 178105 корректно — расхождение внутри корпуса |
| 178205 | random-clean-topup | minor | fluency | `J'y vais déjà` | калька «уже»: «C'est bon, je m'en vais» |
| 178323 | random-clean-topup | minor | fluency | `t'aider` | источник на «Вы» («Вам помогать пришёл») — tutoiement инвертирует вежливость |
| 178481 | random-clean | minor | fluency | `Entrez tous ensemble` | ANSWER-строка: конвенция корпуса — инфинитив; «Entrer tous ensemble» |
| 178605 | random-clean | minor | accuracy | `vous arrache à l'oubli` | «небытие» ~ беспамятство; идиома «arracher à l'oubli» = «спасти от забвения» — сдвиг смысла; ожидаемо «au néant» / «à l'inconscience» |
| 178622 | random-clean | minor | fluency | `Douter` | «Засомневаться» как действие-выбор — «Se méfier» / «Hésiter»; голое «Douter» без объекта неестественно |
| 178838 | check:multiple_capital | minor | accuracy | `YO-L-K4; papa` | каламбур ёЛ-К4 ~ ЁЛКА потерян транслитерацией — просится адаптация (напр. SA-P1N); и «papa» здесь против «Père» в 178852 — непоследовательность |
| 178839 | random-clean | minor | accuracy,terminology | `Habiller l'objet` | наряжать (ёлку) — «décorer», не «habiller»; и «l'objet» против «l'article» в 178838 для того же «изделия» |
| 178852 | check:multiple_capital | minor | fluency | `le corps mort; Père` | «corps mort» — морской термин, неестественно («dépouille»/«corps sans vie»); «Père» против «papa» в 178838 |
| 179010 | random-clean | minor | fluency | `Crier davantage` | «дальше» = продолжать: «Continuer à crier»; «davantage» = «громче/больше» |
| 179471 | random-clean-topup | minor | fluency | `De quels objets parlez-vous ?` | «Каких таких?» — скептический эхо-вопрос: «Lesquels ?» / «Comment ça ?»; формальная экспликация гасит колкость |
| 179485 | random-clean-topup | minor | accuracy | `Oh, le Parti` | маскированная обсценность «Ё****я Партия» полностью выхолощена — ожидаемо «Ce p*** de Parti…» с сохранением цензурной маски |
| 179524 | longest | minor | fluency,accuracy | `Le Gigastructure; plus beaucoup de temps` | род: «structure» женского — «La Gigastructure»; и «нам осталось немного [душ]» превращено во «времени» — сдвиг, который продолжение фразы частично спасает |
| 179530 | random-clean | minor | accuracy | `entendu` | сакральная заглавная «Услышал» (культ-спик) потеряна — ожидаемо «je vous ai Entendu» |
| 179557 | random-clean | minor | accuracy | `JE NE T'AI PAS APPELÉ` | «Я звал НЕ ТЕБЯ» — скоуп отрицания: «CE N'EST PAS TOI QUE J'AI APPELÉ» |
| 179754 | random-clean | minor | fluency,accuracy | `sourit... se mit à rêver` | passé simple против нарративного презенса корпуса; «задумалась» -> «devint pensive», а не «se mit à rêver» |
| 179796 | random-clean | minor | accuracy | `Faire semblant d'attraper` | притвориться, что БУДЕТЕ ловить (намерение) ≠ изображать ловлю: «Faire semblant de vouloir attraper» |
| 179900 | check:end_stop | minor | fluency | `occupé avec des affaires scientifiques` | «occupé par mes travaux scientifiques»; «affaires scientifiques» читается как науко-бизнес |
| 180025 | random-clean | minor | punctuation | `mordre!?` | нет NNBSP перед «!?» — и punctuation_spacing чек НЕ сработал на кластере «!?»: слепое пятно слоя 0, зафиксировать |
| 180031 | random-clean | minor | fluency | `Montrer où` | «Покажи куда» — прямая речь к NPC, не описание действия: «Montre-moi où» |
| 180072 | longest | minor | accuracy | `révèle la perspicacité` | «яви прозрение» — «accorde-nous la clairvoyance»; «perspicacité» (проницательность) + без датива смысл ломается локально |
| 180094 | random-clean | minor | fluency | `te touchera du doigt` | калька столкнулась с чужой идиомой: «toucher du doigt» = «ухватить суть»; надо «personne ne posera un doigt sur toi» |
| 180143 | random-clean | minor | fluency | `travaillait` | имперфект после нарративного презенса рвёт повествование; «travaille» (источник тоже рваный, но fr обязан сгладить) |
| 180158 | gloss-miss | minor | fluency,terminology | `la Parti; l'institut` | «le Parti» — род; «Qui le Parti m'envoie-t-il»; «l'institut» — усечение глоссарного «Institut de recherche» (допустимо в повторе, но фиксирую) |
| 180175 | random-clean | minor | accuracy | `Un objet important ?` | «объект» в военно-промышленном смысле = «site»/«installation»; «objet» — вещь |
| 180299 | random-clean | minor | punctuation | `Père?!` | нет NNBSP перед «?!» — второй случай слепого пятна punctuation_spacing на кластерах ?!/!? |
| 180381 | random-clean-topup | minor | accuracy | `tu as de la suite dans les idées` | «губа не дура» = «tu ne te refuses rien !»; «suite dans les idées» = упорство — другая идиома |
| 180416 | random-clean-topup | minor | fluency,punctuation | `Pourquoi vous réjouissez-vous ?` | возмущённое «?!» потеряно, инверсия слишком вежлива для окрика: «Vous vous réjouissez de quoi ?!» |
| 180448 | check:end_exclamation | minor | punctuation,fluency | `sur toi en criant Hérétique !` | кавычки вокруг крика потеряны — «en criant « Hérétique ! » »; и «sur toi» ломает vous-конвенцию нарратива |
| 180600 | random-clean | minor | fluency | `Bon plan` | «bon plan» — лексикализованная идиома («выгодное предложение»); с вокативом читается странно: «Excellent plan, Aristarkh Betonovich !» |
| 180761 | random-clean | minor | accuracy | `Se distraire` | «отвлечься [от дела] и слушать» = «s'interrompre et écouter»; «se distraire» = развлечься |
| 180878 | random-clean | minor | fluency | `tu m'as empêchée` | «empêcher qqn» без комплемента неполно: «tu m'en as empêchée» / «tu t'y es opposé» |
| 180968 | random-clean-topup | minor | accuracy | `Et si c'était plus simple ?` | «А можно простыми словами?» — просьба переформулировать: «Vous pouvez le dire plus simplement ?»; fr читается как гипотеза |
| 180970 | longest | minor | fluency | `ligotée par de puissants supports` | «ligoter» (связать верёвкой) конфликтует с «supports» (крепления): «immobilisée par de puissantes fixations» |
| 181009 | random-clean | minor | fluency | `Fuir complètement` | «нафиг» — сниженный интенсив: «Foutre le camp» / «Se tirer d'ici»; «complètement» — плоская литеральщина |
| 181049 | random-clean | minor | accuracy | `Mu-R1-H0` | кир. Н -> лат. N (GOST): «Mu-R1-N0»; «H» — визуальная, не фонетическая замена; риск расхождения игровых кодов (ср. 81В->81B в 178484) |
| 181141 | random-clean | minor | fluency | `vous a attaqué` | учитель — школьнику: по-французски tu; вы-обращение к ребёнку ломает регистр сцены |
| 181213 | random-clean | minor | fluency | `a juré; Le Liquidateur` | passé composé против нарративного презенса; «выругался» = «a lâché un juron» («jurer» первично «клясться»); заглавная L против строчной в корпусе |
| 181460 | random-clean | minor | accuracy | `Enseigne` | «прапор» — сухопутный «adjudant»; «enseigne» — флотский чин и «вывеска» разом, сбивает |
| 181476 | random-clean-topup | minor | fluency | `Enroué` | кличка «Хриплый» — «le Rauque» (ср. «voix rauque» в 178605); «Enroué» = простуженный, слабо как прозвище |
| 181489 | random-clean-topup | minor | fluency | `Intimidateur` | квебецизм/канцелярит; европейский fr: «la Brute» / «le Caïd» |
| 181496 | random-clean-topup | minor | terminology | `Carte du parti` | Партия — реалия с заглавной (Parti в корпусе): «Carte du Parti» |
| 181608 | random-clean | minor | fluency | `familier avec` | англицизм: «familier de» / «Vous connaissez des rituels…» |
