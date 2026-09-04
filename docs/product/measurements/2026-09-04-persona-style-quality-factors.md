# Persona / Style / language_instructions: что реально влияет на качество художественного перевода

Дата: 2026-09-04. Замер для дизайна бота-интервьюера продюсера (`Bot_Producer_Interviewer_user_flow.md`) и схемы БДХК 2026-09-03 (`BDHC_Weblate_schema_proposal_2026-09-03_2.md`), уточняющий `docs/product/designs/2026-08-28-universal-game-context-db.md`.

Источники: живые настройки 8 прод-проектов `l10n.herocraft.com` через `GET /api/projects/<slug>/machinery_settings/` (снимок: `analysis/data/prod_machinery_prompts_2026-09-04.md`); `note` юнитов `heart-abyss/hub-1` через `/api/translations/heart-abyss/hub-1/ru/units/`; код фока; LQA-документы и сырые данные `analysis/data/`. Все анкоры — относительно корня репозитория фока, кроме явно помеченных.

## 1. Что такое Persona и Style в коде

- `weblate/machinery/llm.py:140-147` — PROMPT начинается с фиксированной строки `You are a professional translation engine specialized in structured localization tasks.`, затем `{persona}`, `{style}`, `{language_instructions}`, затем 28 правил (`:222-250`). Persona и Style — два свободных текста без разной семантики; обработка только `strip()` + точка в конце (`llm.py:416-423`).
- `weblate/machinery/forms.py:500-521` — единственное различие в подсказках UI: Persona = «persona of translator… “You are a squirrel breeder”», Style = «style of translation… “Use informal language”». Persona по замыслу апстрима — кто переводит и в какой предметной области, **не голоса персонажей**.
- `language_instructions` — JSON `{код: текст}`, ≤1000 символов (codepoints) на язык (`forms.py:23`), код обязан существовать в `Language` (`forms.py:553-563`); выбор exact → вариант → базовый код (`llm.py:675-703`); подставляется как `Target-language project instructions:`; правило 24 обязывает следовать.
- Речь **конкретного** персонажа в строке передаётся полем `note` юнита: правило 26 (`llm.py:248`) — «The "note" field carries developer context… such as the speaking character… Use it to choose register, gender agreement, and tone». На проде `heart-abyss/hub-1` `note` заполнен у 396/396 юнитов (`note: "Ray"`, `"Leon"`, `"Hazuo"`…).
- **Судья читает те же поля**: `weblate/trans/judge_loop.py:264-282` склеивает `persona + "\n\n" + style` в `{project_context}` промпта MQM-аннотатора (`weblate/trans/judge_prompts/verdict.txt:1`). Хэш этой пары — часть идентичности вердикта (`judge_loop.py:304`): любое изменение инвалидирует кэш судьи по проекту.

### Что модель видит на строку (переводчик)

`llm.py:1049-1090, 1196-1248`: `source`, `parts`, `context`/`key`, `explanation` (source_unit с фолбэком), `note`, `secondary`, `plural`, `failing_checks`, `glossary_advisories`, `placeholders`, существующий `translation`. Корень: `source_language`, `target_language`, `glossary`, `strings`. Батч переводчика — 10 строк, concurrency 2 (`llm.py:385-397`).

**Соседних реплик нет ни у переводчика, ни у судьи**: `Unit.nearby` — только UI (`weblate/trans/models/unit.py:2329-2367`); судья кладёт в сегмент id, key, source, target, rendered, note, explanation, glossary, checks (`weblate/trans/judge.py:914-923`); план ±1 окна не реализован (`docs/llm-first/plans/2026-08-20-judge-dialog-context.md:1-16`). В данных remediation `previous_source` заполнен у 2/396 юнитов (0.5 %).

Никакого другого project/component-поля (agreement, instructions, check_flags) до промпта не доходит.

## 2. Как прод использует поля сегодня

Снимок 2026-09-04, `korotkij-test` пуст.

| Проект | persona | style | LI (символы JSON) | LI языков |
|---|---|---|---|---|
| col4 | 1008 | 3005 | 4 | 0 |
| pirate-ships | 786 | 1853 | 6612 | 14 |
| heart-abyss | 2416 | 1795 | 3348 | 10 |
| strategy-and-tactics-2 | 940 | 3991 | 896 | 1 |
| need-for-greed | 1450 | 7210 | 4 | 0 |
| space-arena | 407 | 906 | 1089 | 13 |
| victory-banner | 1116 | 3360 | 324 | 1 |

### Категории содержимого persona+style (≈ символы, категории не дизъюнктны)

| Проект | Роль | Факты игры | Обращение | Тон/мат | Голоса персонажей | Термины/DNT | Техника (маркап/длина) | Числа | Консистентность | Пунктуация/длина | Итого |
|---|---|---|---|---|---|---|---|---|---|---|---|
| col4 | 421 | 196 | 0 | 523 | 0 | 681 | 1140 | 0 | 354 | 659 | 4015 |
| heart-abyss | 335 | 0 | 0 | 954 | ≈2430 (два списка) | 230 | 509 | 0 | 249 | 0 | 4231 |
| need-for-greed | 1229 | 461 | 225 | 1005 | ~0 (Digger/лошадь) | 2098 | 2103 | 352 | 354 | 971 | 8691 |
| pirate-ships | 787 | 0 | 423 | 227 | 0 (архетипы) | 186 | 1135 | 293 | 0 | 0 | 2688 |
| space-arena | 412 | 0 | 0 | 241 | 0 | 269 | 401 | 0 | 0 | 0 | 1351 |
| strategy-and-tactics-2 | 954 | 0 | 0 | 595 | 0 | 1261 | 810 | 0 | 322 | 591 | 4957 |
| victory-banner | 1130 | 0 | 0 | 432 | 0 | 2104 | 286 | 0 | 0 | 536 | 4502 |

Наблюдения:

1. Самые большие блоки — терминология и техника движка, не голос. NFG: 2103 символа — спецификация `{value:cond:…}`; col4 — `$`-разделитель и DSL. В схеме 2026-09-03 для этого нет поля.
2. Сложившаяся конвенция: **Persona = роль + бриф игры** («You are a senior game localization specialist working on "Need for Greed"… The player is a Digger who…»), **Style = сеттинг/тон, обращение к игроку, голоса персонажей, термины, техника** (снимок `:212-230`, `:234-345`).
3. Голоса персонажей поимённо — только heart-abyss, и там два несинхронных списка: persona (`:116-132`, 15 персонажей, формат «Имя - роль, регистр, примеры реплик») и style (`:143`); persona пишет «Ray», style — «Rei»; Seri-Lightning и Saint есть только в style. Остальные проекты: NFG — роли Digger/лошадь; pirate-ships — три архетипа; col4, space-arena, s&t2, victory-banner — 0 персонажей.
4. В persona heart-abyss лежит **инструкция судье** («Register severity: a register mismatch is minor unless…», `:138`) — поле уже используется как настройка потребителя.
5. Generic-правила переизобретаются: «identical source strings → identical translations» в 4/7 (col4:61, nfg:323, s&t2:567, vb:626); «overflow is a shipped bug» ×4; «never leave Cyrillic» ×3 (дублирует `CyrillicLeakCheck`, `weblate_customization/checks.py:451`); «glossary as law» — правило 4 промпта, переписано в nfg:223, vb:600, col4:42, s&t2:540; «never change final punctuation» — правило 27, в nfg:226, vb:602.
6. `language_instructions`: образец — space-arena (27–129 символов на язык: обращение, RTL, пробелы). Антипаттерны: pirate-ships — 9 языков с одним скопированным абзацем про запятую (482 симв., `:380-428`); s&t2 zh_Hans — мини-глоссарий на 875/1000 (`:583-587`), невидимый человеку через UI глоссария; heart-abyss ko/zh — разовые QA-заметки «существующие переводы битые, не копируй» (`:178, :186, :190`), которые протухнут.
7. Бриф-поля схемы почти не заполнены: `target_audience` явно один раз («rated Teen, not childish», nfg:237); `notification_style`/`patchnotes_style` — один раз (pirate-ships:373-374); core_loop/key_modes/monetization/live_ops — нигде.

### Прод-факт → поле схемы 2026-09-03

| Прод-факт | Поле |
|---|---|
| «mobile mining RPG… published for Android» | genre + платформа (поля нет) |
| «Digger mines treasure, crafts… no combat» | core_loop |
| «rated Teen, not childish» | target_audience |
| «Soviet-flavoured post-apocalyptic dystopia…» | setting_and_era |
| «dark and serious with black humour» | world_tone / tone |
| «Address the player informally… tu/du/ty» | player_address |
| «Leon… informal address, shouts in capitals… obscene» | character_voices |
| «Japanese-derived proper nouns transliterated per glossary» | names_policy |
| «Push notifications: short, urgent, one CTA» | notification_style |
| «obscenity fidelity works in both directions» | profanity_policy (нужен смысл «по источнику») |

**Не ложится ни в одно поле**: синтаксис плейсхолдеров/маркапа и cond-выражений; бюджет длины UI; числа/даты/разделители по языку; правила консистентности; QA-заметки о состоянии данных; издатель/платформа/Steam-теги; мини-глоссарий в LI; ролевая рамка «You have shipped mobile games before» (335–1229 символов на проект без информационной ценности).

## 3. Измеренные дефекты художественного перевода

### heart-abyss/hub-1 full-LQA (396 реплик × 9 языков, чистый MT)

`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md:7-30, :165-172`:

| Класс | Кол-во | Примеры | Анкор |
|---|---|---|---|
| accuracy | 127 | «Недавно»→«Non», «ночевать»→«arrêter», «собрат»→«frère»; 3/4 пропусков судьи требуют соседней реплики | audit `:120-144, :528-540` |
| register / голос | 124 | MT смягчает мат Леона и добавляет мат нейтральным (zh_Hans 他妈); Яэко получает «ain't no / why'd ya»; ja てめえ/俺 у нейтральных; FR один Tsuru→Leon то vous, то tu | lqa `:174-188`, audit `:145-166` |
| grammar / agreement | 55 | de du/ihr/Sie в 12 юнитах; «die halbe Imperium» | lqa `:193-195` |
| terminology | 43 | teahouse 4 варианта FR, bathhouse 3, Akito→Akira, Jinko/Jinkos; в глоссарии нет «мон», «Трущобы» | audit `:193-207` |
| omission/addition | 28 | добавленное название лавки | audit `:169-190` |
| punctuation | 18 | FR 66/396 потеря финальной пунктуации | audit `:63-89` |
| юмор | — | «BE HARD AS A ROCK» — непреднамеренная двусмысленность | audit `:169-190` |

audit = `docs/operations/audits/2026-08-20-heart-abyss-hub-1-translation-qa.md`. Глоссарий: 12 терминов, привязан к 96/396 юнитов FR (audit `:610-626`).

### heart-abyss remediation — 92 применённые правки по классам

`analysis/data/hub1-remediation-2026-08-25/fixes.json`, легенда `REVIEW.md:5,145,345,371,559`; `jq -r '.[]|[.lang,.defect_class]|@tsv' fixes.json | sort | uniq -c`:

| Язык | A термин | B обращение | C граница батча | D регистр/мат | E глоссарий | Итого |
|---|---|---|---|---|---|---|
| de | – | 17 | – | 3 | – | 20 |
| en | 1 | – | – | 7 | – | 8 |
| es | 1 | 4 | – | 2 | – | 7 |
| fr | 4 | 4 | – | 2 | – | 10 |
| it | 1 | 4 | – | 1 | – | 6 |
| ja | 2 | – | – | 5 | 1 | 8 |
| ko | 2 | 3 | – | 4 | – | 9 |
| zh_Hans | 8 | – | 2 | 5 | – | 15 |
| zh_Hant | 4 | 1 | 2 | 2 | – | 9 |
| **Итого** | **23 (25 %)** | **33 (36 %)** | **4 (4 %)** | **31 (34 %)** | 1 | 92 |

Речь персонажей (B+D) = 64/92 = 70 %. Фактор языкозависим: de — T/V-формы (85 %), zh_Hans — терминология (53 %).

### strategy-and-tactics-2 zh_Hans (стратегия, не диалоги)

`analysis/data/st2-zh-recal/st2-zh-annot-*.json`, 124 юнита, 20 дефектных: глоссарий 8 («ход»→天 вместо 回合), смысл 6, композитная двусмысленность 4, порядок плейсхолдеров 1, остаток MAX 1; регистр 0; контекст соседей нужен 5/20. Таксономия судьи (`judge-repair-2026-08-25/run-01.json`, 55 ошибок): mistranslation 27, omission 12, fluency 12, terminology 4.

### victory-banner DE

`docs/operations/audits/2026-08-22-victory-banner-common-de-lqa.md:20-85`: «Стрелок»→«Fahrer» (галлюцинация из ключа `Unit_DriverVermaht`), «Настя»→«Anastasia», «Выгрузить»→«Entladen» (омоним с разрядкой оружия), AT вместо PaK — дефекты знания игры.

### Эффект project_context у судьи

`docs/llm-first/measurements/2026-08-20-judge-prompt-universalization-run.md:55-80`: с persona/style как project_context `missed_critical = 0` во всех прогонах против baseline; нейтральный фолбэк — FP 20 / noise 39 против 13 / 26.

### Модельное сравнение

`game_pulse_saas/docs/research/2026-08-10-localization-model-comparison.md`: 10 строк × 10 языков × 20 моделей; измерены модель, батч (~10 строк, ~200 токенов, <60 с, `:133-134`), формат чисел (`:89-96`), plural (`:130-132`). Persona/style/glossary/соседний контекст не сравнивались (`:24-27`). Художественный вывод только качественный: DeepSeek v4 Flash «livelier tone» для banter при 14–49 с (`:117-121`).

### NFG interim/phase2

`analysis/data/nfg-ru-source-2026-09-02/` — операционный лог миграции en→ru-source: 302 термина с `terminology` (`phase2-report.md:46`), `check:glossary` 0 нарушений (`interim-report-2026-09-02.md:75-76`), persona/style override активен (`phase2-report.md:66`); замера качества до/после нет.

## 4. Ранжированные факторы качества

| # | Фактор | Доля | Чем закрывается | Кто |
|---|---|---|---|---|
| 1 | Регистр/мат конкретного персонажа | 34 % правок; 124 LQA | таблица голосов в Style + `note` спикера + honorific в `language_instructions` | данные; анкета блок A |
| 2 | Форма обращения между парами персонажей | 36 % правок; de 17/20 | матрица «кто→кому» в Style / LI; поля в схеме нет | данные; блок A |
| 3 | Терминология/имена | 25 % heart-abyss; 40 % st2; 43 LQA | глоссарий с `terminology`/`gender`, не текст (12 терминов → 43 дефекта) | данные; блок B |
| 4 | Смысл без соседней реплики | 3/4 пропусков судьи; 25 % st2 | ±1 окно в payload — только код | код фока |
| 5 | Род/число/согласование | 55 LQA | пол персонажей, диминутивы в voices/glossary | блок A/B |
| 6 | Техника (маркап, `$`, cond, длина UI) | 30–50 % символов Style; 4 % дефектов | поля техники в схеме нет | схема; блок C |
| 7 | Юмор/игра слов | единичные | `humor` + пример в `explanation` строки | слабый вклад |
| 8 | Модель/батч/числа | измеримо | настройка движка | вне БДХК |

## 5. Следствия для схемы и бота

1. **Persona** рендерить как бриф игры без ролевой строки (уже в шаблоне `llm.py:141`) и без дублей правил 2/4/18/19/27. **Style** — тон, обращение, голоса, FORBIDDEN, DNT, техника. **Локальные overrides** — только в `language_instructions` (≤1000 codepoints/язык, коды из `Language`). DNT/имена — дополнительно в глоссарий через `append_glossary_terms` с `terminology`/`read-only`.
2. Персонажи — первый и главный блок анкеты: имя, `character_key` как в `note`/ключах, пол, роль, регистр, уровень мата (none/mild/strong/по источнику), 1–2 примерные реплики, особенности (капс, растянутые гласные, канцелярит), матрица обращений по парам с per-language исключениями, отключение/переопределение по локали.
3. Термины и имена собирать боту в глоссарий (с родом и translate/transliterate/keep), не в Style.
4. Техблок вернуть в схему: синтаксис плейсхолдеров и cond-выражений, теги движка и их поведение, разделитель строк, бюджет длины UI, формат чисел/дат.
5. Бриф сократить до premise, setting_and_era, world_tone, target_audience (рейтинг влияет на мат), content_types; core_loop/key_modes/monetization/live_ops — необязательные для других потребителей.
6. `profanity_policy` нужен смысл «по источнику, в обе стороны» — heart-abyss: смягчение сильного мата — ошибка, добавление мата нейтральному — ошибка.
7. Generic-правила (консистентность одинаковых строк, длина UI, кириллица, глоссарий как закон) — в шаблон PROMPT, не в карточку. Judge-инструкции («Register severity») — в шаблон судьи, рендерер их не генерирует.
8. Бюджет: persona+style уходят и переводчику, и судье; считать на пару полей по фактическому `_get_prompt(lang)`, показывать в превью бота вместе с `language_instructions`.
9. Публикация из БДХК перезапишет ручные persona/style, в которых сейчас лежит содержание, отсутствующее в схеме (техника, QA-заметки, judge-настройки) — предупреждать и показывать diff.
10. Соседний контекст (±1 реплика) — кодовый долг фока (`judge-dialog-context.md`), вопросами не закрывается.

## 6. Q7 «Forbidden rules» — примеры, привязанные к измеренным дефектам

Формулировка: «Что переводчику запрещено делать с текстом игры? Не темы, а вольности: смягчать, украшать, исправлять, нормализовать.» Multi-select, каждая кнопка — отдельное `forbidden_rule` с `applies_to`.

Голос и грубость (класс D, 34 %):
- Не смягчать мат и грубость источника — сила равна источнику (heart-abyss `:119-120`).
- Не добавлять мат/сленг там, где в источнике его нет (zh_Hans 他妈; col4 `:33`).
- Не облагораживать грубого персонажа и не огрублять утончённого (Яэко → «ain't no»).
- Не делать нейтральную наррацию грубее, чем она есть (col4 `:34-35`).

Юмор и авторские искажения:
- Не объяснять и не выбрасывать шутку — переделать образ, сохранить эффект (NFG `:243-244`).
- Не превращать сеттинг в пародию/пастиш (pirate-ships `:361`).
- Не нормализовать КАПС, растянутые гласные, намеренные опечатки (heart-abyss `:147`).
- Не добавлять драму, восклицания, пояснения (NFG `:229-230`).

Обращение и грамматика (класс B, 36 %):
- Не переключать ты/вы внутри одной пары персонажей (FR Tsuru→Leon).
- Не переводить уменьшительные имена полной формой (victory-banner Настя).
- Не менять время повествования между строками (col4 `:37-38`).

Имена и термины (класс A, 25 %):
- Не переводить имена по смыслу — транслит/глоссарий (heart-abyss `:146`, space-arena `:452`).
- Не ре-транслитерировать восточные имена через английский (heart-abyss ja/zh `:174, :186`).
- Не придумывать второе написание термина из глоссария (teahouse ×4 FR).

Локально-специфичные → `language_instructions[lang]`: en без британских форм; es Испания/LatAm; pt-BR/pt-PT не смешивать; zh упрощённые/традиционные не смешивать; ja/ko не выравнивать персонажей на один уровень вежливости.

Не предлагать как forbidden_rule: плейсхолдеры/маркап (правила 2, 18, 19), финальная пунктуация (27), кириллица (`CyrillicLeakCheck`), консистентность одинаковых строк (кандидат в PROMPT), обещания возврата/сроков (саппорт, `applies_to=support`, в Weblate не рендерится).
