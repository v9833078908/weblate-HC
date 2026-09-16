# Enforced checks и LLM-судья: механизм и перепись демоций

Дата: 2026-09-16. Статус: исследование + замер, без изменений продукта.
Данные: dev-инстанс `localhost:3001` (копия прода на 2026-08-27), REST API,
запросы вида `project:<slug> state:>=translated check:<id>`.

## Вопрос

Нужно ли включать `Component.enforced_checks` (сейчас `[]` у всех
компонентов) для кастомных проверок `game-markup`, `game-line-break`,
`game-number`, `game-token` при том, что на тех же строках работает
LLM-судья? Кто «главный» — судья или детерминированные проверки?

Проверки `game-terminology` в репозитории нет. Ближайшее — встроенная
`check_glossary` (`weblate/checks/glossary.py`), выключенная везде;
измерение `docs/product/measurements/2026-08-11-glossary-enforcement-analysis.md`
и архивное решение `docs/product/archive/2026-08-24-glossary-check-no-space-languages.md`
прямо против её enforced-включения.

## Что делает enforced (факты из кода)

Обычная проверка при провале создаёт строку `Check` и предупреждение;
строка остаётся `translated`/`approved`, уходит в файл и в билд, проверку
можно dismiss. Enforced добавляет ровно три вещи:

1. **Демоция при сохранении.** Любая запись через `Unit.translate()` — ручная
   правка, API PATCH, авто-перевод, mass fix, проекция вердикта судьи — если
   проверка всё ещё падает, переписывает состояние 20/30 в 11
   (`STATE_NEEDS_REWRITING`) и пишет change `ENFORCED_CHECK`
   (`weblate/trans/models/unit.py:2521-2536`). Это пост-фактум, не вето:
   текст сохраняется, «готовой» строка не считается.
2. **Ретроактивная зачистка.** При включении настройки и при каждой загрузке
   репозитория все `translated`/`approved` строки с падающей enforced-проверкой
   переводятся в 11 (`weblate/trans/models/component.py:6913-6927`,
   `4936-4939`). Обратно они сами не поднимаются: mass fix и судья передают в
   `translate()` текущий `unit.state` (`weblate/trans/fix_check.py:519-560`).
3. **Нельзя dismiss.** Серверный инвариант: право `unit.check` возвращает
   `False` для enforced и judge-проверок (`weblate/auth/permissions.py:438-444`),
   `ignore_check` требует это право (`weblate/trans/views/js.py:94-95`).
   Обход — только `Check.set_dismiss()` из `weblate shell` или флаг
   `ignore-game-*` на юните.

Enforced **не** гарантирует исключение из файла: состояние 11 входит в
`FUZZY_STATES` и отфильтровывается только при `commit_policy =
WITHOUT_NEEDS_EDITING` (`weblate/trans/models/pending.py:293-294`); при
`ALL` строка экспортируется. `commit_policy` — настройка проекта.

Судья и enforced:

- `JUDGE_CHECKS` намеренно вычтены из enforced (`unit.py:2526`; дизайн
  `docs/product/plans/2026-08-13-01-judge-verdict-core.md:2240-2312`):
  вероятностное мнение не роняет состояние.
- Судья пишет вердикт через `Unit.translate()`
  (`weblate/trans/autotranslate.py:905-920`,
  `weblate/trans/judge_loop.py:1710-1727`), поэтому судейский `pass` на строке
  с падающей enforced-проверкой немедленно демотируется в 11. Enforced делает
  детерминированные проверки старше судьи по факту, а не по договорённости.
- Судья и LLM-репейр получают все активные проверки в промпт независимо от
  enforced (`judge_loop.py:128-136`, `weblate/machinery/llm.py:585-645`).

## Текущая политика коммитов

- dev (API): `space-arena`, `need-for-greed`, `pirate-ships`, `heart-abyss`,
  `col4` — `commit_policy=20` (`WITHOUT_NEEDS_EDITING`), `translation_review=true`;
  `test`, `judge-repair-probe`, `fixcheck-sandbox` — `0`.
- prod (снимок `docs/operations/plans/2026-08-27-prod-config-sync-and-rollout.md:51-52`):
  все восемь проектов `commit_policy=20`. Живая база не проверялась.

Предпосылка для цепочки «проверка упала → строка не готова → в билд не
попадает» уже есть.

## Перепись: сколько строк уронит включение

Строки `state >= translated` с активной проверкой — ровно те, что
`update_enforced_checks()` переведёт в 11.

| Проект | translated+ | game-markup | game-line-break | game-token | game-number |
|---|---|---|---|---|---|
| space-arena | 73 638 | 619 | 0 | 0 | 349 |
| pirate-ships | 59 476 | 80 | 0 | 5 | 53 |
| col4 | 16 853 | 0 | 5 | 0 | 47 |
| need-for-greed | 3 537 | 2 | 0 | 0 | 0 |
| heart-abyss | 1 928 | 0 | 0 | 0 | 0 |

Три проверки без `game-number`: **711 строк из 155 432 (0,46 %)**. Все они
сейчас в билде — `commit_policy=20` их не фильтрует, потому что они
`translated`.

## Разбор выборок

- **game-markup, space-arena (40) и pirate-ships (30):** 0 ложных
  срабатываний. Классы: тег `<color>`/`<b>` выброшен целиком; другой цвет
  (`#FDFF90` в source, `#FEC600` в target — блок `TUTORIAL_NEW_*` отстал от
  правки source); переведённый плейсхолдер `{PLAYER}` → `{SPIELER}`;
  синтаксически битые `{x{0`, `{EVENT`, `</color/><size>` (fa); перепутанные
  номера `{1}`/`{2}` (pirate-ships `crew_boost`, `fear_and_horror`);
  плейсхолдер `{5}` заменён литералом `10%`; пустой `<color=#E07800></color>`.
- **game-token, pirate-ships (5/5):** идентификаторы переведены
  (`item_type[` → `element_type[`, `item_template[` → `vật phẩm_mẫu[`,
  `skirmish_league_id[` → `Scharmützel-Liga[`). Пустая подстановка в рантайме.
- **game-line-break, col4 (5/5):** `$` потерян или сохранён с пробелом
  (запись до появления автофикса `LineSeparatorSpacing`).
- **game-number (41):** смешанно. Реальные и гейм-плейные: pirate-ships
  `200` → `1200` (durability, шесть языков), `4 уровне` → `3级`,
  `20%/30%` → `30%/10%`, `по 200 урона` выброшено; space-arena fa `7 Hours` →
  `۵`, `30` → `۲۰`, ko `BM.2` → `BM.1`. Ложные: число словом (`1 battle` →
  `ein`/`एक`/`satu`), ординал (`1-st` → «первый»), `360 degree` → «во всех
  направлениях», выдуманные шифры в col4 (`ёЛ-К4` → `X-MAS-3`). Оценка FP по
  выборке ≈ 40 %.

## Вывод

1. **`game-markup`, `game-token`, `game-line-break` — включать enforced.**
   0 FP на 75 образцах; каждый провал — видимый в игре дефект; судья не должен
   быть тем, кто ловит потерянный `{0}`. Для компании без переводчиков это
   упрощает UX: сигнал переезжает из «жёлтого значка, который никто не
   смотрит» в «строка выпала из готовых», а dismiss-решение с продюсера
   снимается.
2. **`game-number` — не включать.** ≈40 % FP; enforced-FP означает строку,
   застрявшую в 11 с выходом только через флаг `ignore-game-number`.
   Оставить предупреждением и судье. Отдельно: pirate-ships `200 → 1200`
   в шести языках стоит починить сейчас.
3. **Глоссарная проверка — не включать** (см. измерения выше).
4. **Блокер до включения:** после починки строка сама не возвращается в 20.
   Нужен путь «починил → 20» (LLM-репейр по 711 строкам на dev, затем
   проверка остатка), иначе продюсер получит 711 строк, которые не сможет
   вернуть в готовые без клика по каждой.
5. Включение на проде — деплой, требует отдельного одобрения. Включать сразу
   на всех пяти проектах: heart-abyss/need-for-greed дадут 0–2 демоции, а
   space-arena и pirate-ships надо чинить в любом случае.

Отчёты скаутов по каждой проверке (механизм, тесты, измерения) легли в
основу разделов выше; ссылки на код приведены по состоянию на этот день.

## Приложение: выборки

```text
===== space-arena / game-markup (first 40) =====
[de] key=%TUTORIAL_NEW_1_1% id=4828
  S: Hey, wake up, Captain!⏎⏎I’m <color=#FFDE1A>Nea</color>, the avatar of your neural implant. You may not remember everything, but I can help you.⏎⏎The first priority is to build you a new spaceship.⏎⏎<color=#FEC600>Tap the
  T: He, wachen Sie auf, Captain!⏎⏎Ich bin Nea - der Avatar deines Neuralink-Implantats. Du kannst dich vielleicht nicht mehr an alles erinnern, aber ich werde dir helfen.⏎⏎Die erste Priorität ist es, ein neues Raumschiff für
[de] key=%TUTORIAL_NEW_2_7% id=4836
  S: Isn't she pretty? ⏎<color=#FDFF90>Confirm the build</color>, and the ship will be ready to fly.
  T: Ist sie nicht hübsch?! ⏎Bestätige den Bau und das Schiff ist bereit zum Fliegen.
[de] key=%TUTORIAL_NEW_4_3% id=4841
  S: Let's <color=#FDFF90>get back to the Hangar</color>. We need to try out our new weaponry.⏎⏎<color=#FEC600>Tap this section to see your ship.</color>
  T: Tolles Zeug! Lass uns <color=#FEC600>zurück zum Hangar gehen.</color>
[de] key=%TUTORIAL_NEW_5_2% id=4843
  S: Replace two small guns with the new big one.⏎⏎<color=#FDFF90>Drag and drop</color> the new weapon in place of the old ones.
  T: Ersetze zwei kleine Waffen durch die neue große.⏎⏎<color=#FEC600>Ziehe und lege</color> die neue Waffe anstelle der alten hin.
[de] key=%TUTORIAL_NEW_5_3% id=4844
  S: You're all done! <color=#FDFF90>Confirm the build</color> to save your changes.
  T: Du bist fertig! <color=#FEC600>Bestätige den Bau</color>, um die Änderungen zu speichern.
[de] key=%TUTORIAL_NEW_8_1% id=4848
  S: You can <color=#FFDE1A>modify the ship</color> using <color=#B6F8EF>Celestium</color>. The ship will be bigger and in better shape!⏎⏎<color=#FEC600>Tap the Mods button.</color>
  T: Mit <color=#B6F8EF>Celestium</color> kann jedes Schiff erweitert werden! Indem Sie Rumpfmodifikationen dafür kaufen!⏎⏎<color=#FEC600>Tippe auf die Schaltfläche "Mods".</color>
[de] key=%TUTORIAL_NEW_8_2% id=4849
  S: Here, you can see the <color=#FFDE1A>modifications</color> available for the ship. ⏎⏎<color=#FEC600>Choose modification I.</color>
  T: Hier kannst du die verfügbaren Modifikationen für das Schiff sehen.⏎⏎<color=#FEC600>Wähle Modifikation I.</color>
[de] key=%TUTORIAL_NEW_11% id=4855
  S: Build this ship, start with any module and <color=#FDFF90>follow the tips!</color>
  T: Baue dieses Schiff, fange mit einem beliebigen Modul an und <color=#FEC600>folge den Tipps!</color>
[de] key=%COMPENSATION_2.3.4_TO_2.3.5_INFO% id=6913
  S: <size=50>We have changed and reset the progress of parameter:⏎⏎<size=80><color=#FFF261>Explosion radius</color></size>⏎⏎Therefore, we decided to <color=#FFF261>RETURN</color> part of the chips and loans.⏎It is not possib
  T: <size=50>Wir haben den Fortschritt der Parameter geändert und zurückgesetzt:⏎⏎Explosionsradius⏎⏎Aus diesem Grund haben wir uns entschlossen, einen Teil der Chips und Kredite <color=#FFF261>zurückzugeben.</color>⏎Es ist a
[de] key=%GRIND_TIME_HEADER% id=7081
  S: <color=pink>Valentine's</color> <color=white>day</color>
  T: <color=pink>Valentinstag</color>
[de] key=%MAIL_OFFERWALL_REWARD_MESSAGE% id=7189
  S: Congrats, {PLAYER}. You got a reward.
  T: Herzlichen Glückwunsch, {SPIELER}. Du hast eine Belohnung bekommen.
[de] key=%MAP_EVENT% id=8406
  S: <br><size=200%>EVENT</size>
  T: <br><size=130%>VERANSTALTUNG</size>
[de] key=%BP_BANNER_TEXT% id=9364
  S: <size=75%><color=#FFD243>PILOT</color></size>⏎PASS
  T: PILOTENPASS
[es] key=%TUTORIAL_NEW_1_1% id=9654
  S: Hey, wake up, Captain!⏎⏎I’m <color=#FFDE1A>Nea</color>, the avatar of your neural implant. You may not remember everything, but I can help you.⏎⏎The first priority is to build you a new spaceship.⏎⏎<color=#FEC600>Tap the
  T: ¡Hey, despierta, capitán!⏎⏎Soy Nea - avatar de su implante neuralink. Puede que no recuerdes todo, pero te ayudaré.⏎⏎La primera prioridad es construir una nueva nave espacial para ti.⏎⏎<color=#FEC600>Pulsa el botón de la
[es] key=%TUTORIAL_NEW_2_3% id=9658
  S: Okay, great. Also, the ship needs a source of energy, so we’ll add a <color=#FFDE1A>reactor</color>.
  T: Bien. Una nave necesita una fuente de energía, así que vamos a ponerle una.
[es] key=%TUTORIAL_NEW_4_3% id=9667
  S: Let's <color=#FDFF90>get back to the Hangar</color>. We need to try out our new weaponry.⏎⏎<color=#FEC600>Tap this section to see your ship.</color>
  T: ¡Qué pasada! <color=#FEC600>Volvamos al Hangar.</color>
[es] key=%TUTORIAL_NEW_5_2% id=9669
  S: Replace two small guns with the new big one.⏎⏎<color=#FDFF90>Drag and drop</color> the new weapon in place of the old ones.
  T: Reemplaza aquellas dos armas pequeñas por la grande.⏎ ⏎Para ello, <color=#FEC600>arrastra</color> el arma nueva sobre las antiguas.
[es] key=%TUTORIAL_NEW_5_3% id=9670
  S: You're all done! <color=#FDFF90>Confirm the build</color> to save your changes.
  T: ¡Ya está! <color=#FEC600>Acepta el diseño</color> para guardar los cambios.
[es] key=%TUTORIAL_NEW_8_1% id=9674
  S: You can <color=#FFDE1A>modify the ship</color> using <color=#B6F8EF>Celestium</color>. The ship will be bigger and in better shape!⏎⏎<color=#FEC600>Tap the Mods button.</color>
  T: ¡Usando <color=#B6F8EF>Celestium</color> se puede expandir cualquier nave! ¡Comprando modificaciones de casco para ella!⏎⏎<color=#FEC600>Toque el botón "Mods".</color>
[es] key=%TUTORIAL_NEW_8_2% id=9675
  S: Here, you can see the <color=#FFDE1A>modifications</color> available for the ship. ⏎⏎<color=#FEC600>Choose modification I.</color>
  T: Aquí puedes ver las modificaciones disponibles para la nave.⏎⏎<color=#FEC600>Elige la modificación I.</color>
[es] key=%TUTORIAL_NEW_11% id=9681
  S: Build this ship, start with any module and <color=#FDFF90>follow the tips!</color>
  T: Construye esta nave, comienza con cualquier módulo y <color=#FEC600>sigue los consejos.</color>
[es] key=%TUTORIAL_NEW_14% id=9685
  S: Well done! I'll see you soon, but in the meantime, find yourself a better ship. ⏎⏎Complete tasks, take part in battles, and level up to unlock new Arena modes and get access to huge ships!⏎⏎Go for it, <color=#FFDE1A>%PLA
  T: ¡Bien hecho! Nos vemos pronto, pero mientras tanto, búscate una nave mejor. ⏎⏎¡Ahora necesitas completar tareas, participar en batallas y subir de nivel para desbloquear nuevos modos de batalla y obtener acceso a naves e
[es] key=%TUTORIAL_5% id=9690
  S: [TOP]Good stuff. Also, the ship needs a source of energy, so we will place a <color=#FFDE1A>reactor</color>.
  T: [TOP]Bien. Una nave necesita una fuente de energía, así que vamos a ponerle una.
[es] key=%TUTORIAL_23_0_1% id=9717
  S: Well done, <color=#FFDE1A>%PLAYER%</color>. You're just starting to get the hang of it, but I have a feeling you're in for a big...[BR][CIGAR]Oh, another newcomer... I hope you're not one of those who doesn't know that <
  T: ¡Buen trabajo, %PLAYER%! Apenas estás empezando a entenderlo, pero tengo la sensación de que te espera algo grande...[BR][CIGAR]Oh, otro recién llegado... Espero que no seas de esos que no saben que <color=#E36748>los re
[es] key=%TUTORIAL_23_7% id=9725
  S: Now you need to complete tasks, participate in battles and level up to unlock new battle modes and gain access to huge ships!⏎⏎Go for it, <color=#FFDE1A>%PLAYER%</color>!
  T: ¡Ahora necesitas completar tareas, participar en batallas y subir de nivel para desbloquear nuevos modos de batalla y obtener acceso a naves enormes!⏎⏎¡Sigue así, <color=#FDFF90>%PLAYER%!</color>
[es] key=%T_CB6% id=9744
  S: In Class Battles, ships fight within the same class. For example, fighter against fighter.⏎⏎<b>Press the button</b> to start the first battle.
  T: En las Batallas de Clases, las naves luchan dentro de la misma clase. Por ejemplo, caza contra caza.⏎⏎Pulsa el botón para empezar la primera batalla.
[es] key=%SEASON_INFO_NEXT_RESET% id=11765
  S: At the beginning of next season your ranking points will be reset to {0}
  T: Al comienzo de la próxima temporada, tus puntos de calificación se restablecerán a:
[es] key=%BP_RESTART_POPUP_TOOLTIP_DESC% id=14216
  S: - Commanders who have completed <color=#FFB800>50 stages</color> of the current Pass can access the Singularity screen, or all Commanders if no calendar Pass is active.⏎⏎- In the Singularity section, you can replay one o
  T: - Los comandantes que hayan completado <color=#FFB800>50 etapas</color> del pase actual pueden acceder a la pantalla de Singularity, o todos los comandantes si no hay un pase calendario activo en ese momento.⏎⏎- En la se
[fa] key=%TUTORIAL_NEW_1_1% id=14480
  S: Hey, wake up, Captain!⏎⏎I’m <color=#FFDE1A>Nea</color>, the avatar of your neural implant. You may not remember everything, but I can help you.⏎⏎The first priority is to build you a new spaceship.⏎⏎<color=#FEC600>Tap the
  T: هی، بیدار شو، کاپیتان! من آواتار ایمپلنت عصبی شما هستم. من به شما کمک می کنم تا مسائل را مرتب کنید.⏎اولین کاری که باید بکنی اینه که یک بدنه ی سفینه بخری تا بتونی یک سفینه برای خودت بسازی.⏎⏎<color=#FEC600>برای شروع روی دک
[fa] key=%TUTORIAL_NEW_1_2% id=14481
  S: To build a ship, you'll need a hull. Let's buy a basic <color=#FFDE1A>Light Fighter</color>.⏎⏎<color=#FEC600>Tap the "Buy" button.</color>
  T: برای اینکه یاد بگیری چطوری سفینه بسازی از این بدنه ی سفینه استفاده کن.⏎⏎<color=#FEC600>روی دکمه خریدن بزن.</color>
[fa] key=%TUTORIAL_NEW_4_3% id=14493
  S: Let's <color=#FDFF90>get back to the Hangar</color>. We need to try out our new weaponry.⏎⏎<color=#FEC600>Tap this section to see your ship.</color>
  T: اسلحه فوق العاده ایه! بهتره <color=#FEC600>برگردیم به آشیانه سفینه.</color>
[fa] key=%TUTORIAL_NEW_5_2% id=14495
  S: Replace two small guns with the new big one.⏎⏎<color=#FDFF90>Drag and drop</color> the new weapon in place of the old ones.
  T: اسلحه بزرگ جدید رو جایگزین دو اسلحه کوچک کن.⏎<color=#FEC600>اسلحه جدید رو بگیر</color> و به سمت جایگاه اسلحه های قبلی بکش.
[fa] key=%TUTORIAL_NEW_5_3% id=14496
  S: You're all done! <color=#FDFF90>Confirm the build</color> to save your changes.
  T: انجامش دادی! <color=#FEC600>ساخت رو تایید کن</color> تا تغییرات رو ذخیره کنی.
[fa] key=%TUTORIAL_NEW_8_1% id=14500
  S: You can <color=#FFDE1A>modify the ship</color> using <color=#B6F8EF>Celestium</color>. The ship will be bigger and in better shape!⏎⏎<color=#FEC600>Tap the Mods button.</color>
  T: با استفاده از <color=#B6F8EF>سلستیوم</color> هر سفینه ای میتونه گسترش پیدا کنه! از طریق خریدن تغییرات بدنه برای اون سفینه!⏎⏎<color=#FEC600>روی دکمه تغییرات بزن.</color>
[fa] key=%TUTORIAL_NEW_8_2% id=14501
  S: Here, you can see the <color=#FFDE1A>modifications</color> available for the ship. ⏎⏎<color=#FEC600>Choose modification I.</color>
  T: اینجا میتونی تغییرات موجود برای یک سفینه رو ببینی.⏎⏎<color=#FEC600>تغییر 1 رو انتخاب کن.</color>
[fa] key=%TUTORIAL_NEW_11% id=14507
  S: Build this ship, start with any module and <color=#FDFF90>follow the tips!</color>
  T: این سفینه رو بساز. با هر کدوم از تجهیزات که خواستی شروع کن و <color=#FEC600>نکات رو دنبال کن!</color>
[fa] key=%T_CB6% id=14570
  S: In Class Battles, ships fight within the same class. For example, fighter against fighter.⏎⏎<b>Press the button</b> to start the first battle.
  T: در نبردهای کلاس، کشتی ها در همان کلاس می جنگند. مثلا جنگنده علیه مبارز.⏎⏎برای شروع اولین نبرد دکمه را فشار دهید.
[fa] key=%COMPENSATION_2.2_INFO% id=16460
  S: <size=50>We changed and improved the module hacking system.⏎ ⏎Now, when you increase a module's level, you no longer need:⏎ ⏎<size=80><color=#FFF261>Overclocking Chips</color></size>⏎ ⏎So, we decided to <color=#FFF261>RE
  T: ⏎<size=50>ما سیستم هک کردن تجهیزات رو تغییر دادیم و بهبود بخشیدیم.⏎ ⏎اکنون هنگامی که سطح تجهیزات رو افزایش میدی، دیگه نیازی نیست که:⏎ ⏎<size=80><color=#FFF261>کارکرد تراشه ها رو افزایش بدی</color/><size>⏎ ⏎بنابراین ما تص
[fa] key=%WINSTREAK_REWARD_TOOLTIP% id=16904
  S: Winstreak x{0}
  T: مسیر پیروزی {x{0
[fa] key=%EVENT_MAIL_UNIV% id=16949
  S: You took {PLACE} place in the {EVENT} event, which took place from {START_DATE} to {END_DATE}, and get the rewards listed below.
  T: در جایگاه {PLACE} در رویداد {EVENT قرار گرفتی که این رویداد از {START_DATE} تا {END_DATE} برگزار شد و جوایز زیر رو دریافت کردی.

===== space-arena / game-number (first 20) =====
[de] key=%TASK5_DESCR% id=5958
  S: Lose 1 Ranked battle
  T: Verliere ein Gewertete Gefecht
[fa] key=%BANNER_SPUTNIK1_TITLE% id=15100
  S: 1-st satellite in space!
  T: اولین ماهواره در فضا!
[fa] key=%MAIL_UPDATE_32700_MESSAGE% id=16892
  S: Commanders! Update 3.27 is out. Here's what's in it:⏎- price of Standard Crypto-cases has been reduced by 40%, price of Elite Crypto-cases has been reduced by 25%. Visuals and content of Standard and Elite Crypto-cases h
  T: فرماندهان! آپدیت 3.27 منتشر شد. در اینجا چیزی است که در آن وجود دارد:⏎- قیمت کیسهای رمزنگاری خلبانان استاندارد 40٪ کاهش یافته است، قیمت کیسهای رمزنگاری خلبانان نخبه 25٪ کاهش یافته است. تصاویر و محتوای کیسهای رمزنگاری خلب
[fa] key=%CLAN_BOSSES_INFO_POPUP_MESSAGE% id=18543
  S: All clan players can battle a certain number of Clan Bosses during the Clan Battle Season. Each clan independently activates bosses. To do this, a Leader or an Officer of a clan must pay a certain amount of Clan Credits
  T: همه بازیکنان قبیله می توانند در طول فصل نبرد قبیله با تعداد معینی از باس های قبیله مبارزه کنند. هر قبیله به طور مستقل رئیس ها را فعال می کند. برای انجام این کار، یک رهبر یا یک افسر یک قبیله باید مقدار مشخصی از Clan Credi
[fa] key=%VIP_POPUP_BENEFIT% id=19201
  S: Get <color=#FFB800>3900% Value!</color> and Save <color=#FFB800>Up to 7 Hours a Month</color> by Skipping Ads
  T: <color=#FFB800>ارزش ۳۹۰۰٪</color> و <color=#FFB800>۵ ساعت صرفه‌جویی ماهانه</color> بدون تبلیغات
[fa] key=%VIP_POPUP_DESCRIPTION% id=19202
  S: <color=#FFB800>• Instant Ad Rewards:</color> Claim up to 30 daily without watching videos⏎⏎<color=#FFB800>• Exclusive Golden Badge</color> for your Profile⏎⏎<color=#FFB800>• Golden Warp Jump Animation</color> for All Shi
  T: <color=#FFB800>• دریافت فوری پاداش تبلیغاتی:</color> تا ۲۰ بار در روز بدون تماشای ویدیو⏎⏎<color=#FFB800>• نشان طلایی ویژه</color> برای پروفایل⏎⏎<color=#FFB800>• انیمیشن پرش وارپ طلایی</color> برای همه سفینه‌ها⏎⏎<color=#F
[fr] key=%LASER_TURRET2X2_DESC% id=20682
  S: A Laser Beam that can target and strike enemies in a 360 degree angle around you. Best when passing over the enemy and striking them on the sides and back where armor is scarce.
  T: Son faisceau laser peut attaquer les ennemis quelle que soit leur direction. À utiliser idéalement en dépassant l'ennemi pour le frapper à revers, là où le blindage est faible.
[fr] key=%FLAK_ROCKET_TURRET3X3_DESC% id=20694
  S: Blasts the enemy with a hail of low-guidance rockets that shreds unshielded flanks like they were paper. Capable of firing in a 360 degree angle.
  T: Assaille l'ennemi de roquettes faiblement guidées qui déchirent les parois non protégées comme du papier. Capable de tirer dans toutes les directions.
[fr] key=%CLAN_BOSSES_INFO_POPUP_MESSAGE% id=23369
  S: All clan players can battle a certain number of Clan Bosses during the Clan Battle Season. Each clan independently activates bosses. To do this, a Leader or an Officer of a clan must pay a certain amount of Clan Credits
  T: Tous les joueurs de clan peuvent affronter un certain nombre de chefs de clan pendant la saison des combats de clans. Chaque clan active indépendamment les boss. Pour ce faire, un Leader ou un Officier d'un clan doit pay
[hi] key=%BANNER_SPUTNIK1_TITLE% id=24752
  S: 1-st satellite in space!
  T: अंतरिक्ष में पहला उपग्रह!
[hi] key=%D6_TITLE% id=25299
  S: Shall we fight 1 battle?
  T: क्या हम एक लड़ाई लड़ें?
[hi] key=%MAIL_UPDATE_32700_MESSAGE% id=26544
  S: Commanders! Update 3.27 is out. Here's what's in it:⏎- price of Standard Crypto-cases has been reduced by 40%, price of Elite Crypto-cases has been reduced by 25%. Visuals and content of Standard and Elite Crypto-cases h
  T: कमांडर्स! अपडेट 3.27 आ गया है। इसमें ये है:⏎- मानक पायलट क्रिप्टो-मामले की कीमत 40% कम कर दी गई है, अति श्रेष्ठ पायलट क्रिप्टो-मामले की कीमत 25% कम कर दी गई है। मानक पायलट क्रिप्टो-मामले और अति श्रेष्ठ पायलट क्रिप्टो-माम
[id] key=%BANNER_SPUTNIK1_TITLE% id=29578
  S: 1-st satellite in space!
  T: Satelit pertama di luar angkasa!
[id] key=%D6_TITLE% id=30125
  S: Shall we fight 1 battle?
  T: Apakah kita akan bertarung satu ronde?
[ja] key=%D6_TITLE% id=34951
  S: Shall we fight 1 battle?
  T: バトルに参加しませんか？
[ja] key=%MAIL_UPDATE_32700_MESSAGE% id=36196
  S: Commanders! Update 3.27 is out. Here's what's in it:⏎- price of Standard Crypto-cases has been reduced by 40%, price of Elite Crypto-cases has been reduced by 25%. Visuals and content of Standard and Elite Crypto-cases h
  T: 指揮官たち！ 3.27アップデートがリリースされました。  中身は次のとおりです。：⏎- スタンダードパイロットクリプトケース の価格が40%、エリートパイロットクリプトケース の価格が25%引き下げられました。スタンダードパイロットクリプトケース とエリートパイロットクリプトケース のビジュアルと内容が更新されました。ナノスティムレーター と ナノスティムレーター V2 が両方のケースに追加されました。その他の報酬のドロップ率は、価格
[ko] key=%NOTIFY_OFFER_0USD_DESC% id=39791
  S: Mjollnir Mk2 is available for you! BIG SHIP! BIG DISCOUNT!
  T: 거대한! 묠니르 Mk II를 사용할 수 있는 엄청난 할인!
[ko] key=%LEVEL_UP_UNLOCKED_BALLISTIC_TURRET2X2_3 id=40460
  S: Purchase of "Vulcan Turret <color=#F9FF96FF>BM.2</color>" module on Black Market
  T: 암시장서 "발칸 터렛 <color=#F9FF96FF>BM.1</color>" 모듈 구매
[ko] key=%LEVEL_UP_UNLOCKED_LASER1X2_3% id=40462
  S: Purchase of "Laser Beam <color=#F9FF96FF>BM.2</color>" module on Black Market
  T: 암시장서 "레이저 빔 <color=#F9FF96FF>BM.1</color>" 모듈 구매
[ko] key=%MAIL_UPDATE_32700_MESSAGE% id=41022
  S: Commanders! Update 3.27 is out. Here's what's in it:⏎- price of Standard Crypto-cases has been reduced by 40%, price of Elite Crypto-cases has been reduced by 25%. Visuals and content of Standard and Elite Crypto-cases h
  T: 지휘관님! 3.27 업데이트가 나왔습니다. 그 내용은 다음과 같습니다:⏎- 스탠다드파일럿 크립토 케이스들 가격이 40%, 엘리트파일럿 크립토 케이스들 가격이 25% 인하되었습니다. 스탠다드파일럿 크립토 케이스들와 엘리트파일럿 크립토 케이스들의 외형 및 내용물이 업데이트되었습니다. 두 케이스 모두에 나노자극기와 나노자극기 V2가 추가되었습니다. 다른 보상의 드롭률은 가격 인하에 비례하여 조정되

===== pirate-ships / game-markup (first 30) =====
[zh_Hans] key=tutorial_message_first_overload id=246676
  S: Перегруженный корабль не сможет выйти из бухты. <color=#E07800>Сними часть оборудования</color>, перетащив за пределы палубы.
  T: 如果船舶<color=#E07800>超载</color>，它将无法离开港口。将一些设备<color=#E07800>拖出甲板</color>以减轻重量。
[zh_Hans] key=ability_description_captain_shock_freezi id=248040
  S: После трёх успешных атак выпускает стрелу, которая временно замораживает пушки и матросов на {0} сек. в радиусе {1}м вокруг места попадания.
  T: 成功攻击三次后，射出一支箭，在命中点周围{1}m半径内将火炮和水手暂时冻结，持续{0}s。
[zh_Hans] key=ability_description_captain_iceberg id=248042
  S: Борт покрывается толстой коркой льда, которая снижает урон кораблю от всех пушек на {4}, кроме огненных, на {1} сек. Перезарядка: {0} сек.
  T: 船体覆盖着一层厚厚的冰层，在{1}秒内减少所有炮弹（火炮除外）对船的伤害{4}。冷却时间：{0}秒。
[zh_Hans] key=description_resourse_tips_not_enough_fac id=249217
  S: Не хватает <b>жетонов фракции</b>. Их можно получить, выполняя задания фракции.
  T:  faction 代币不足。您可以通过完成 faction 任务来获得它们。
[de] key=tutorial_message_first_overload id=250411
  S: Перегруженный корабль не сможет выйти из бухты. <color=#E07800>Сними часть оборудования</color>, перетащив за пределы палубы.
  T: Wenn das Schiff <color=#E07800>überladen ist</color>, kann es nicht segeln. <color=#E07800>Reduziere das Gewicht</color> des Schiffes, indem du einige der schweren Elemente entfernst.
[de] key=tutorial_message_first_shield id=250412
  S: Чем ниже <color=#E07800>вес корабля</color>, тем <color=#E07800>быстрее</color> он сближается с вражеским кораблём. Быстрое сближение - хороший выбор для <color=#E07800>абордажной команды.</color>
  T: Je schwerer das Schiff, <color=#E07800></color>desto <color=#E07800>langsamer</color> nähert es sich einem anderen Schiff
[de] key=tutorial_message_intro_ship id=250424
  S: Твой корабль? Что ж, для начала сгодится! Давай <color=#1DEEFC>укомплектуем его</color>!
  T: Dein Schiff? Nun, es wird für den Anfang reichen! Lass uns es ausrüsten!
[de] key=ability_description_cannon_bad_landing id=251579
  S: С вероятностью 50% скелет взорвется при приземлении, нанося 100% урона всем вражеским матросам в радиусе одного метра
  T: Mit einer 50%igen Chance wird das Skelett bei der Landung explodieren und allen feindlichen Seeleuten im Umkreis von einem Meter 100% Schaden zufügen.
[de] key=ability_description_crew_boost id=251608
  S: Каждые {0}с. матрос ускоряется в {1} раз(а) на {2}с.
  T: Alle {0}s. wird der Matrose für {2}s um {1}-mal beschleunigt.
[de] key=ability_description_captain_ally_damage id=251708
  S: Увеличивает силу атаки всех матросов ближнего боя на {4} и восстанавливает им {5} от максимального здоровья.⏎Продолжительность: {3}с.⏎Перезарядка: {0}с.⏎Не действует на скелетов
  T: Erhöht die Angriffskraft aller Nahkampf-Matrosen um {4} und stellt 10% ihrer maximalen Gesundheit wieder her.⏎Dauer: {3}s.⏎Abklingzeit: {0}s.⏎Wirkt nicht auf Skelette.
[de] key=ability_description_captain_shock_freezi id=251775
  S: После трёх успешных атак выпускает стрелу, которая временно замораживает пушки и матросов на {0} сек. в радиусе {1}м вокруг места попадания.
  T: Nach drei erfolgreichen Angriffen feuert einen Pfeil ab, der Kanonen und Matrosen im Umkreis von {1}m um den Einschlagspunkt vorübergehend für {0}s einfriert.
[de] key=description_resourse_tips_not_enough_fac id=252952
  S: Не хватает <b>жетонов фракции</b>. Их можно получить, выполняя задания фракции.
  T: Nicht genügend Fraktions-Token. Du kannst sie durch das Abschließen von Fraktionsaufgaben erhalten.
[en] key=description_resourse_tips_not_enough_fac id=256687
  S: Не хватает <b>жетонов фракции</b>. Их можно получить, выполняя задания фракции.
  T: Not enough Faction Tokens. You can get them by completing faction tasks.
[es] key=tutorial_message_first_overload id=257881
  S: Перегруженный корабль не сможет выйти из бухты. <color=#E07800>Сними часть оборудования</color>, перетащив за пределы палубы.
  T: Si el barco <color=#E07800>está sobrecargado</color>, no podrá navegar. <color=#E07800>Reduce el peso</color> del barco eliminando algunos de los elementos pesados.
[es] key=tutorial_message_first_shield id=257882
  S: Чем ниже <color=#E07800>вес корабля</color>, тем <color=#E07800>быстрее</color> он сближается с вражеским кораблём. Быстрое сближение - хороший выбор для <color=#E07800>абордажной команды.</color>
  T: Cuanto más pesado sea el barco <color=#E07800></color>, <color=#E07800>más lento</color> se acercará a otro barco
[es] key=ability_description_crew_second_detonati id=259115
  S: С шансом {0} в момент взрыва кидает {1} гранаты в разные стороны
  T: Lanza {1} granadas en diferentes direcciones con una probabilidad de {0} en el momento de la explosión
[es] key=description_resourse_tips_not_enough_fac id=260422
  S: Не хватает <b>жетонов фракции</b>. Их можно получить, выполняя задания фракции.
  T: No tienes suficientes fichas de facción. Puedes obtenerlas completando tareas de facción.
[fr] key=tutorial_message_first_overload id=261616
  S: Перегруженный корабль не сможет выйти из бухты. <color=#E07800>Сними часть оборудования</color>, перетащив за пределы палубы.
  T: Si le navire <color=#E07800>est surchargé</color>, il ne pourra pas naviguer. <color=#E07800>Réduire le poids</color> du navire en supprimant certains des éléments lourds.
[fr] key=tutorial_message_first_shield id=261617
  S: Чем ниже <color=#E07800>вес корабля</color>, тем <color=#E07800>быстрее</color> он сближается с вражеским кораблём. Быстрое сближение - хороший выбор для <color=#E07800>абордажной команды.</color>
  T: Plus le navire est lourd <color=#E07800></color>, plus <color=#E07800>lent</color> il s'approchera d'un autre navire
[fr] key=ability_description_crew_second_detonati id=262850
  S: С шансом {0} в момент взрыва кидает {1} гранаты в разные стороны
  T: Lance {1} grenades dans différentes directions avec une probabilité de {0} au moment de l'explosion
[fr] key=description_resourse_tips_not_enough_fac id=264157
  S: Не хватает <b>жетонов фракции</b>. Их можно получить, выполняя задания фракции.
  T: Pas assez de jetons de faction. Vous pouvez en obtenir en effectuant des tâches de faction.
[id] key=tutorial_message_first_overload id=265351
  S: Перегруженный корабль не сможет выйти из бухты. <color=#E07800>Сними часть оборудования</color>, перетащив за пределы палубы.
  T: Jika kapal <color=#E07800>kelebihan muatan</color>, kapal tidak akan bisa berlayar. <color=#E07800>Kurangi berat</color> kapal dengan menghapus beberapa elemen berat.
[id] key=tutorial_message_first_shield id=265352
  S: Чем ниже <color=#E07800>вес корабля</color>, тем <color=#E07800>быстрее</color> он сближается с вражеским кораблём. Быстрое сближение - хороший выбор для <color=#E07800>абордажной команды.</color>
  T: Semakin berat kapal <color=#E07800></color>, semakin <color=#E07800>lambat</color> kapal akan mendekati kapal lain
[id] key=ability_description_crew_second_detonati id=266585
  S: С шансом {0} в момент взрыва кидает {1} гранаты в разные стороны
  T: Melemparkan {1} granat ke arah yang berbeda dengan peluang {0} saat meledak
[id] key=ability_description_fear_and_horror id=266666
  S: Шанс {2}, что вражеские матросы испугаются и начнут в панике бегать по палубе в течение {1}с.⏎Перезарядка: {0}с.
  T: Peluang {2} bahwa pelaut musuh akan takut dan mulai berlari-lari di sekitar dek dalam kepanikan selama {2} detik.⏎Cooldown: {0} detik.
[id] key=description_resourse_tips_not_enough_fac id=267892
  S: Не хватает <b>жетонов фракции</b>. Их можно получить, выполняя задания фракции.
  T: Tidak cukup Token Faksi. Anda bisa mendapatkannya dengan menyelesaikan tugas faksi.
[it] key=tutorial_message_first_overload id=269086
  S: Перегруженный корабль не сможет выйти из бухты. <color=#E07800>Сними часть оборудования</color>, перетащив за пределы палубы.
  T: Se la nave <color=#E07800>è sovraccarica</color>, non potrà navigare. <color=#E07800>Riduci il peso</color> della nave rimuovendo alcuni degli elementi pesanti.
[it] key=tutorial_message_first_shield id=269087
  S: Чем ниже <color=#E07800>вес корабля</color>, тем <color=#E07800>быстрее</color> он сближается с вражеским кораблём. Быстрое сближение - хороший выбор для <color=#E07800>абордажной команды.</color>
  T: Più è pesante la nave, <color=#E07800></color>più <color=#E07800>lentamente</color> si avvicinerà a un'altra nave
[it] key=tutorial_message_intro_ship id=269099
  S: Твой корабль? Что ж, для начала сгодится! Давай <color=#1DEEFC>укомплектуем его</color>!
  T: La tua nave? Beh, sarà sufficiente per cominciare! Equipaggiamola!
[it] key=description_resourse_tips_not_enough_wea id=270520
  S: Не хватает <b><color=#7736E7>Жетонов Стихий</color></b>
  T: Monete Elementali insufficienti

===== pirate-ships / game-token (first 5) =====
[de] key=mission_descr_hold_league_place_for_days id=251055
  S: Удержись skirmish_league_place[|на {0} месте] skirmish_league_id[|в {0}|в любой лиге] несколько дней подряд
  T: Bleiben Sie in der Scharmützel-Rangliste[|auf Platz {0}] Scharmützel-Liga[|in {0}|in jeder Liga] mehrere Tage hintereinander
[pl] key=mission_descr_hold_league_place_for_days id=284670
  S: Удержись skirmish_league_place[|на {0} месте] skirmish_league_id[|в {0}|в любой лиге] несколько дней подряд
  T: Pozostań na miejscu w ligowej rozgrywce[|w {0} miejscu] ligowej rozgrywce[|w {0}|w dowolnej lidze] przez kilka dni z rzędu
[pl] key=mission_descr_equip_items id=284679
  S: Установи item_type[|{0}] на корабль
  T: Zainstaluj element_type[|{0}] na statku
[vi] key=mission_descr_unlock_craft_receipt id=299620
  S: Купи item_template[|{0}]
  T: Mua vật phẩm_mẫu[|{0}]
[vi] key=mission_descr_ship_unlock id=299621
  S: Купи item_template[|{0}]
  T: Mua vật phẩm_mẫu[|{0}]

===== pirate-ships / game-number (first 20) =====
[zh_Hans] key=tutorial_hint_ship_gear_4 id=246754
  S: Отлично! Улучшай свои корабли, чтобы открыть дополнительные ячейки снаряжения.⏎Следующая открывается на 4 уровне корабля.
  T: 很棒！升级您的船只以打开额外的装备插槽。⏎下一个在船只的3级时开放。
[zh_Hans] key=ability_description_boarding_hooks3 id=247955
  S: Корабль ускоряется, выстреливая 4 абордажных крюка, наносящих по 200 урона и сбивающих матросов врага с ног. Во время ускорения вражеские стрелки и пушки промахиваются с 20% вероятностью.
  T: 舰船加速，发射 4 个接舷钩，钩住敌舰的侧面并将其拉近。加速期间，敌方远程水手和大炮有 20% 的几率落空。
[zh_Hans] key=ability_description_boarding_hooks4 id=247957
  S: Корабль ускоряется, выстреливая 6 абордажных крюков, наносящих по 300 урона и сбивающих матросов врага с ног. Во время ускорения вражеские стрелки и пушки промахиваются с 20% вероятностью.
  T: 舰船加速，发射 6 个接舷钩，钩住敌舰的侧面并将其拉近。加速期间，敌方远程水手和大炮有 20% 的几率落空。
[zh_Hans] key=ability_name_summon_damage_venom id=249172
  S: Призыв кракена 2
  T: 召唤海怪
[zh_Hans] key=ability_description_crew_captain_campaig id=249390
  S: Щит с прочностью 200. Принимает на себя весь урон вместо капитана до полного разрушения
  T: 耐久值为1200的盾牌。在完全破坏前替代船长承受所有伤害
[de] key=ability_name_summon_damage_venom id=252907
  S: Призыв кракена 2
  T: Kraken-Beschwörung
[de] key=ability_description_crew_captain_campaig id=253125
  S: Щит с прочностью 200. Принимает на себя весь урон вместо капитана до полного разрушения
  T: Schild mit 1200 Haltbarkeit. Nimmt allen Schaden anstelle des Kapitäns auf, bis er vollständig zerstört ist
[en] key=ability_name_summon_damage_venom id=256642
  S: Призыв кракена 2
  T: Kraken Summon
[en] key=ability_description_crew_captain_campaig id=256860
  S: Щит с прочностью 200. Принимает на себя весь урон вместо капитана до полного разрушения
  T: Shield with 1200 durability. Absorbs all damage instead of the captain until fully destroyed
[es] key=ability_name_summon_damage_venom id=260377
  S: Призыв кракена 2
  T: Invocación del Kraken
[es] key=ability_description_crew_captain_campaig id=260595
  S: Щит с прочностью 200. Принимает на себя весь урон вместо капитана до полного разрушения
  T: Escudo con 1200 de durabilidad. Absorbe todo el daño en lugar del capitán hasta que se destruya por completo
[fr] key=ability_name_summon_damage_venom id=264112
  S: Призыв кракена 2
  T: Invocation du Kraken
[fr] key=ability_description_crew_captain_campaig id=264330
  S: Щит с прочностью 200. Принимает на себя весь урон вместо капитана до полного разрушения
  T: Bouclier avec 1200 de durabilité. Absorbe tous les dégâts à la place du capitaine jusqu'à sa destruction complète
[id] key=ability_description_boarding_hooks3 id=266630
  S: Корабль ускоряется, выстреливая 4 абордажных крюка, наносящих по 200 урона и сбивающих матросов врага с ног. Во время ускорения вражеские стрелки и пушки промахиваются с 20% вероятностью.
  T: Kapal berakselerasi, menembakkan 4 kait yang mengunci sisi kapal musuh dan menariknya lebih dekat. Selama akselerasi, pelaut jarak jauh dan meriam musuh memiliki peluang 20% untuk meleset.
[id] key=ability_description_boarding_hooks4 id=266632
  S: Корабль ускоряется, выстреливая 6 абордажных крюков, наносящих по 300 урона и сбивающих матросов врага с ног. Во время ускорения вражеские стрелки и пушки промахиваются с 20% вероятностью.
  T: Kapal berakselerasi, menembakkan 6 kait yang mengunci sisi kapal musuh dan menariknya lebih dekat. Selama akselerasi, pelaut jarak jauh dan meriam musuh memiliki peluang 20% untuk meleset.
[id] key=ability_description_ship_collision_explo id=266634
  S: Бочки с горючей смесью взрываются при столкновении, нанося урон в 20% прочности корабля и 30% от максимального здоровья матросов, уничтожая щиты и поджигая палубу.
  T: Tong berisi campuran pembakar meledak saat bertabrakan, memberikan kerusakan sebesar 30% dari daya tahan kapal dan 10% dari kesehatan maksimum pelaut, menghancurkan perisai dan membakar dek kapal.
[id] key=ability_name_summon_damage_venom id=267847
  S: Призыв кракена 2
  T: Pemanggilan Kraken
[id] key=ability_description_crew_captain_campaig id=268065
  S: Щит с прочностью 200. Принимает на себя весь урон вместо капитана до полного разрушения
  T: Perisai dengan daya tahan 1200. Menyerap semua kerusakan menggantikan kapten hingga hancur total
[it] key=ability_name_summon_damage_venom id=271582
  S: Призыв кракена 2
  T: Evocazione del Kraken
[it] key=ability_description_crew_captain_campaig id=271800
  S: Щит с прочностью 200. Принимает на себя весь урон вместо капитана до полного разрушения
  T: Scudo con 1200 di durabilità. Assorbe tutti i danni al posto del capitano fino alla completa distruzione
```
