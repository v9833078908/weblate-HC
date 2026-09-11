# Аудит LQA: anvil-saga/locale_v1-02-import-explained-3 (FR)

> **2026-09-11.** Полное покрытие компонента. Выводы — ревью аналитика (LLM-ассистированное,
> двухпроходное: генерация находок + независимая верификация каждой находки), не штатный
> двухместный судья `weblate/trans/judge.py`. Прод не изменялся, доступ read-only.

**Объём компонента:** 9482 строки, 79 996 слов источника, переведено 100 %, failing-checks — 978 (10,3 %).

## 1. Скоркард MQM-Core

| Метрика | Значение |
|---|---|
| **Режим покрытия** | `full` — просмотрены все юниты |
| **Просмотрено юнитов** | 9482 / 9482 (100 %) |
| **Слов просмотрено (знаменатель MQM)** | 79 996 / 79 996 (100 %) |

| Метрика | Значение | Статус |
|---|---|---|
| **MQM Quality Score (по компоненту)** | **99,60 / 100** | **Fail (Critical Blocker)** — по баллам Grade A, но блокирует 1 критический дефект |
| **Релизный гейт** | Blocked до исправления критического дефекта | 🔴 |
| **Критические (25 pt)** | 1 | 519913 — сломанная escape-последовательность |
| **Мажорные (5 pt)** | 48 | контрсенсы, терминология, риски переполнения UI |
| **Минорные (1 pt)** | 58 | пунктуация, орфография, мелкие сдвиги смысла |
| **Сумма штрафов** | 323 pt | база — все 79 996 слов |

**Методика.** 9482 юнита разобраны 380 партиями по 25 сегментов с игровым контекстом и
терминологией. Первый проход дал 511 предполагаемых дефектов; второй, независимый
проход-верификатор отклонил 423 из них как ложные (самоотзывные формулировки, дефекты
самого русского источника, допустимые идиоматические перефразы, недоказанные ссылки на
«другие сегменты»). Подтверждено 88. К ним добавлены проверенные вручную находки:
1 критический (сверен с близнецом 519914), 3 расхождения финальной пунктуации из чеков
`end_*`, 1 доказанное расхождение имени (`le Roc` в 518341) и 14 измеренных рисков
переполнения коротких UI-слотов. Итого 107 дефектных юнитов (1,1 % компонента).

## 2. Журнал дефектов (все 107)

- 🔴 **CRITICAL** `FrenchOrderChapterOneRequirements` (519913) — `game_engine/broken_placeholder`
  - RU: `Изготовить 5 мечей.\nИзготовить 5 щитов.`
  - FR: `Fabriquer 5 épées.
Fabriquer 5 boucliers.`
  - Le \n littéral du source (séquence d'échappement moteur) est devenu un vrai retour à la ligne ; le segment jumeau 519914 conserve « \n ». Rétablir la séquence littérale.
- 🟠 **MAJOR** `PzKuZePzuOLjQiKG` (517764) — `accuracy/mistranslation`
  - RU: `Строители сейчас недоступны! Вы не можете достраивать помещения.`
  - FR: `Les bâtisseurs ne sont pas disponibles pour le moment ! Vous ne pouvez pas agrandir les pièces.`
  - «достраивать помещения» = terminer/achever la construction des pièces, pas « agrandir ». Proposition : « Vous ne pouvez pas achever la construction des pièces. »
- 🟠 **MAJOR** `jfKTwcJlaVPWziYX` (517799) — `accuracy/mistranslation`
  - RU: `Коновал`
  - FR: `Hongreur`
  - «Коновал» désigne un maréchal/vétérinaire de campagne (ou charlatan), pas un «hongreur». Proposer « Maréchal-ferrant » ou « Rebouteux ».
- 🟠 **MAJOR** `sEGgMOzRHZSidPQh` (517898) — `accuracy/mistranslation`
  - RU: `Да... Украли его, стало быть.`
  - FR: `Ouais... On te l'a volée, alors.`
  - « Украли его » = « On me l'a volée » (c'est le fils qui parle de la pierre qu'il détenait), pas « On te l'a volée ». Corriger en « On nous l'a volée / On me l'a volée ».
- 🟠 **MAJOR** `UyGrqiTYIzBnQKcF` (517971) — `accuracy/mistranslation`
  - RU: `Дождитесь прихода коновала. Дней осталось`
  - FR: `Attendez l'arrivée du hongreur. Jours restants`
  - « коновал » ici désigne un médecin/charlatan (péjoratif) soignant les gens, pas un « hongreur ». Proposer : « Attendez l'arrivée du charlatan » ou « du médecin ».
- 🟠 **MAJOR** `mcmvOyUnmlUeacYG` (518190) — `accuracy/mistranslation`
  - RU: `Купец заказал брошь для своей красавицы-дочери. Отец как раз привез с собой драгоценный камень для перстня барона. Может, я найду `
  - FR: `Un marchand a commandé une broche pour sa ravissante fille. Mon père vient justement de rapporter une pierre précieuse pour la bag`
  - «Отец» renvoie au père de la fille du marchand, pas au père d'Arthur : traduire par « Le père a justement rapporté... » plutôt que « Mon père ».
- 🟠 **MAJOR** `xpktNTBivIsIzVgP` (518239) — `accuracy/mistranslation`
  - RU: `Предложить вызвать коновала.`
  - FR: `Proposer de faire venir le hongreur.`
  - «коновал» = rebouteux/charlatan (vétérinaire de fortune), pas « hongreur ». Proposer : « Proposer de faire venir le rebouteux ».
- 🟠 **MAJOR** `lxPnMABUgYPdpPKW` (518380) — `accuracy/mistranslation`
  - RU: `Беженцы? Ну пришли и пришли. Пусть держатся подальше от моей кузницы.`
  - FR: `Des réfugiés ? Qu'ils soient arrivés ou non, qu'ils restent bien loin de ma forge.`
  - «Ну пришли и пришли» = « Bon, ils sont arrivés, et alors ? » ; « Qu'ils soient arrivés ou non » inverse le sens. Proposer : « Des réfugiés ? Bon, ils sont là, et alors. Qu'ils restent loin de ma forge. »
- 🟠 **MAJOR** `SoldierFools5` (518604) — `game_engine/overflow_risk`
  - RU: `Давно так не веселился!`
  - FR: `Ça faisait longtemps que je ne m'étais pas autant amusé !`
  - Bulle/UI courte : 23 → 57 car. (×2.48) — risque de troncature.
- 🟠 **MAJOR** `MerchantHoliday3` (518656) — `game_engine/overflow_risk`
  - RU: `И чего все так нарядились?`
  - FR: `Et pourquoi tout le monde s'est mis sur son trente-et-un ?`
  - Bulle/UI courte : 26 → 58 car. (×2.23) — risque de troncature.
- 🟠 **MAJOR** `PeasantHoliday4` (518667) — `game_engine/overflow_risk`
  - RU: `Почаще бы такие пьянки.`
  - FR: `Si seulement on avait des beuveries comme ça plus souvent.`
  - Bulle/UI courte : 23 → 58 car. (×2.52) — risque de troncature.
- 🟠 **MAJOR** `QuietPhrase1` (518754) — `game_engine/overflow_risk`
  - RU: `Тихо стало. Всегда бы так.`
  - FR: `C'est devenu calme. Si seulement c'était toujours comme ça.`
  - Bulle/UI courte : 26 → 59 car. (×2.27) — risque de troncature.
- 🟠 **MAJOR** `OrderSantaComplete2` (519014) — `accuracy/mistranslation`
  - RU: `Просто отлично! Пойду их поздравлю, а ты начинай праздновать!`
  - FR: `Tout simplement parfait ! Je vais aller leur apporter, et toi, commence à faire la fête !`
  - «Пойду их поздравлю» = « Je vais aller les féliciter/leur souhaiter un joyeux Noël », pas « leur apporter » (objet manquant, sens modifié).
- 🟠 **MAJOR** `ChristmasCustomers3` (519118) — `game_engine/overflow_risk`
  - RU: `Ох и намело снега нынче!`
  - FR: `Eh bien, il en est tombé de la neige cette année !`
  - Bulle/UI courte : 24 → 50 car. (×2.08) — risque de troncature.
- 🟠 **MAJOR** `Chapter1Jean5` (519258) — `fluency/register_tone`
  - RU: `Соболезную, что сказать. А в нашу скромную кузницу зачем пожаловал? С заказами не справляетесь?`
  - FR: `Mes condoléances, que dire d'autre. Et qu'est-ce qui t'amène dans notre modeste forge ? Débordé par les commandes ?`
  - Incohérence tu/vous dans le même segment : « qu'est-ce qui t'amène » puis « Débordé par les commandes ? » traduit le vouvoiement russe « не справляетесь ». Le RU alterne aussi, mais en FR il faut harmoniser : « Tu n'arrives pas à suivre les commandes ? »
- 🟠 **MAJOR** `Chapter1MonthomeryFirst2` (519344) — `fluency/grammar_syntax`
  - RU: `Доброго дня, милорд! Она, безусловно, была лучшей при моем отце. А после его скоропостижной кончины я делаю все возможное, чтобы т`
  - FR: `Bonjour, milord ! Elle était assurément la meilleure du vivant de mon père. Et après sa disparition soudaine, je fais tout mon pos`
  - "pour qu'il en reste ainsi" est incorrect ; lire "pour qu'il en soit toujours ainsi" / "pour que cela reste ainsi".
- 🟠 **MAJOR** `Chapter1MonthomeryFirst5` (519347) — `accuracy/mistranslation`
  - RU: `О, ну тогда сам Бог велел нам помочь!`
  - FR: `Oh, alors le bon Dieu t'ordonne de nous aider !`
  - "сам Бог велел нам помочь" = Dieu lui-même nous ordonne de nous entraider / c'est le ciel qui veut que tu nous aides. La traduction "le bon Dieu t'ordonne" déplace le sujet ; proposer "alors c'est Dieu lui-même qui veut que tu nous aides !"
- 🟠 **MAJOR** `Baronpolish` (519590) — `accuracy/mistranslation`
  - RU: `Полироль для мечей`
  - FR: `Poli pour épées`
  - «Полироль» = produit de polissage/polish, pas « Poli ». Proposer : « Produit à polir pour épées » (cohérent avec 519587).
- 🟠 **MAJOR** `ThirdOrderThirdChoiceSuccess1` (519708) — `accuracy/mistranslation`
  - RU: `Сделал вот механизм. Такой звук ужасающий издает, что любой смельчак из леса сбежит.`
  - FR: `J'ai fabriqué ce mécanisme. Il fait un bruit si terrifiant que n'importe quel téméraire fuirait la forêt.`
  - «смельчак из леса сбежит» = n'importe quel téméraire s'enfuira de la forêt, pas «fuirait la forêt»... en fait le sens est «fuira hors de la forêt». Proposer : « que le plus téméraire s'enfuira de la forêt ».
- 🟠 **MAJOR** `ThirdOrderThirdChoiceFailure1` (519710) — `accuracy/mistranslation`
  - RU: `Сделал вот механизм. Такой звук ужасающий издает, что любой смельчак из леса сбежит.`
  - FR: `J'ai fabriqué un mécanisme. Il fait un bruit si terrifiant que n'importe quel téméraire fuirait la forêt.`
  - Même erreur de sens : « fuirait la forêt » au lieu de « s'enfuira de la forêt ».
- 🟠 **MAJOR** `ContestBetAcceptBigText` (520198) — `accuracy/mistranslation`
  - RU: `А почему бы, собственно, не рискнуть?! Не все же тяжелым трудом зарабатывать.`
  - FR: `Et pourquoi pas tenter le coup, après tout ?! On ne va pas tout gagner à la sueur de son front.`
  - « Не все же тяжелым трудом зарабатывать » = « On ne peut pas toujours gagner son argent à la sueur de son front », pas « On ne va pas tout gagner ».
- 🟠 **MAJOR** `CelebrationAccept` (520218) — `accuracy/mistranslation`
  - RU: `Изготовить 10 чаш для браги`
  - FR: `Fabriquer 10 calices pour la bière`
  - «чаш» (coupes/chopes) rendu par « calices », terme liturgique inadapté et incohérent avec « chopes » utilisé dans les segments liés (520219/520223). Corriger : « Fabriquer 10 chopes pour la bière ».
- 🟠 **MAJOR** `VladStart1` (520668) — `accuracy/mistranslation`
  - RU: `Кужнец, я прифхшел за тфоей корофью!`
  - FR: `Forcheron, fe viens pour ton fang !`
  - «за твоей коровой» = « pour ta vache » (gag: on comprend « sang »/vampire mais la source dit bien vache). La cible « fe viens pour ton fang » supprime le jeu de mots d'origine. Proposer : « Forcheron, fe viens pour ta fache ! »
- 🟠 **MAJOR** `WitcherOrderDeclined` (520796) — `game_engine/overflow_risk`
  - RU: `Губят людей не зелья`
  - FR: `Ce ne sont pas les potions qui tuent les gens`
  - Bulle/UI courte : 20 → 45 car. (×2.25) — risque de troncature.
- 🟠 **MAJOR** `HobinThirdOrderFailResult` (520889) — `terminology/inconsistent_term`
  - RU: `Гобин отправился восвояси.`
  - FR: `Hobin est reparti d'où il venait.`
  - Nom du personnage incohérent : « Гобин » est rendu « Gobin » ailleurs (520883, 520884) mais « Hobin » ici. Corriger en « Gobin ».
- 🟠 **MAJOR** `HobinContestAcceptResult` (520893) — `terminology/inconsistent_term`
  - RU: `Артур предложил Гобину компромиссное решение без кровопролития.`
  - FR: `Arthur a proposé à Hobin un compromis sans effusion de sang.`
  - Incohérence du nom propre : « Hobin » au lieu de « Gobin ». Corriger : « Arthur a proposé à Gobin un compromis... ».
- 🟠 **MAJOR** `JoanThirdChoiceText` (520938) — `game_engine/overflow_risk`
  - RU: `Мечи девам не игрушка!`
  - FR: `Les épées ne sont pas des jouets pour les filles !`
  - Bulle/UI courte : 22 → 50 car. (×2.27) — risque de troncature.
- 🟠 **MAJOR** `JoanSecondOrderFirstSuccessFinal` (521082) — `accuracy/mistranslation`
  - RU: `Щит не помог Жанне, но она не пала духом.`
  - FR: `Le bouclier n'a pas aidé Jeanne, mais elle ne s'est pas découragée.`
  - Incohérence de nom : le RU dit « Жанне » mais toute la série utilise « Danna » ; ici le FR bascule sur « Jeanne ». Uniformiser : « Le bouclier n'a pas aidé Danna… »
- 🟠 **MAJOR** `pluswood` (521444) — `game_engine/markup_damage`
  - RU: `<color=#25E94F>+{0}</color> к уровню работы с древесиной.`
  - FR: `<color=#25E94F>+	{0}</color> au niveau de travail du bois.`
  - Tabulation parasite insérée entre la balise <color> et le placeholder : '+\t{0}'. Corriger en '<color=#25E94F>+{0}</color>'.
- 🟠 **MAJOR** `plussaw` (521445) — `game_engine/markup_damage`
  - RU: `<color=#25E94F>+{0}</color> к уровню обработки бревен.`
  - FR: `<color=#25E94F>+	{0}</color> au niveau de découpe des bûches.`
  - Tabulation parasite avant {0}. Corriger en '<color=#25E94F>+{0}</color>'.
- 🟠 **MAJOR** `DudeAccept1` (521544) — `fluency/register_tone`
  - RU: `Хм, а звучит не так уж и плохо, экие вы затейники с друзьями! Сделаю.`
  - FR: `Hm, ça n'a pas l'air si mal, vous manquez pas d'imagination avec tes copains ! Je vais le faire.`
  - Mélange tu/vous dans le même segment : « vous manquez pas d'imagination avec tes copains ». Corriger : « tu manques pas d'imagination avec tes copains ! »
- 🟠 **MAJOR** `BanditsWarning6` (521698) — `game_engine/overflow_risk`
  - RU: `Да вы чего, да я...`
  - FR: `Mais qu'est-ce que vous racontez, moi je...`
  - Bulle/UI courte : 19 → 43 car. (×2.26) — risque de troncature.
- 🟠 **MAJOR** `ChapterTwoBaronSecond4` (522720) — `accuracy/mistranslation`
  - RU: `Эх, дурья башка! Меч, конечно. Делай гравировку. Текст как в прошлый раз, название страны поменять, надеюсь, сам догадаешься.`
  - FR: `Ah, tête de linotte ! Une épée, bien sûr. Fais la gravure. Le texte comme la dernière fois, pour changer le nom du pays, j'espère `
  - Contresens : «название страны поменять, надеюсь, сам догадаешься» = «change le nom du pays, j'espère que tu devineras tout seul (lequel)». La traduction « pour changer le nom du pays, j'espère que tu y penseras tout seul » déforme l'instruction. Proposer : « L
- 🟠 **MAJOR** `TugsRefuse4` (523000) — `fluency/register_tone`
  - RU: `Был я у вашего Жака, он еще хуже тебя. Прощай.`
  - FR: `J'ai déjà vu votre Jacques, il est encore pire que toi. Adieu.`
  - Mélange tu/vous dans le même segment : « votre Jacques » puis « pire que toi ». Harmoniser : « il est encore pire que vous » (ou tutoyer partout).
- 🟠 **MAJOR** `DangalfFailure2` (523134) — `accuracy/mistranslation`
  - RU: `Ой, не получилась цепочка ваша. Придется вашем другу быть повнимательнее.`
  - FR: `Oups, je n'ai pas réussi votre chaîne. Votre ami devra faire un peu plus attention.`
  - RU « Придется вашем другу быть повнимательнее » = « Votre ami devra être plus prudent/attentif » ; mais surtout « не получилась цепочка ваша » signifie que la chaîne a raté, pas « je n'ai pas réussi votre chaîne » (formulation acceptable) — le défaut porte sur
- 🟠 **MAJOR** `ReaderFeast3` (523193) — `fluency/grammar_syntax`
  - RU: `Опять празднуют! Пойду почитаю.`
  - FR: `Ça fête encore ! Je vais aller lire.`
  - « Ça fête encore ! » est agrammatical/impropre ; proposer « Ils font encore la fête ! ».
- 🟠 **MAJOR** `ReaderFires2` (523204) — `game_engine/overflow_risk`
  - RU: `Главное, что не книги горят.`
  - FR: `L'essentiel, c'est que ce ne soient pas les livres qui brûlent.`
  - Bulle/UI courte : 28 → 63 car. (×2.25) — risque de troncature.
- 🟠 **MAJOR** `ReaderHalloween5` (523261) — `fluency/grammar_syntax`
  - RU: `Опять празднуют! Пойду почитаю.`
  - FR: `Ça fête encore ! Je vais aller lire.`
  - « Ça fête encore ! » est agrammatical/impersonnel incorrect ; proposer « Ils font encore la fête ! » (cf. 523267).
- 🟠 **MAJOR** `ReaderBlizzard4` (523278) — `game_engine/overflow_risk`
  - RU: `Скорее бы домой, к книгам!`
  - FR: `Vivement que je rentre chez moi, auprès de mes livres !`
  - Bulle/UI courte : 26 → 55 car. (×2.12) — risque de troncature.
- 🟠 **MAJOR** `JoanFourthSecondChoiceText` (523417) — `game_engine/overflow_risk`
  - RU: `Как она мне уже надоела!`
  - FR: `Qu'est-ce qu'elle commence à me taper sur les nerfs !`
  - Bulle/UI courte : 24 → 53 car. (×2.21) — risque de troncature.
- 🟠 **MAJOR** `HobinSeventhOrderThird` (523672) — `terminology/inconsistent_term`
  - RU: `Лесная братва`
  - FR: `Les Joyeux Compagnons`
  - Même titre RU « Лесная братва » rendu par « Les Joyeux Compagnons », sans lien avec la source ni avec les autres occurrences. Corriger en « La bande de la forêt ».
- 🟠 **MAJOR** `Fair2PrepareAlone2` (524308) — `game_engine/overflow_risk`
  - RU: `Справлюсь сам, не впервой!`
  - FR: `Je me débrouillerai tout seul, ce n'est pas la première fois !`
  - Bulle/UI courte : 26 → 62 car. (×2.38) — risque de troncature.
- 🟠 **MAJOR** `Fair2ArtFinleyJacqueBad` (524342) — `accuracy/mistranslation`
  - RU: `Финли был крайне недоволен Артуром и радовался, что с дочкой общается не он, а Жан-Жак.`
  - FR: `Finley était très mécontent d'Arthur et se réjouissait que sa fille fréquente Jean-Jacques plutôt que lui.`
  - RU: il se réjouissait que ce ne soit pas lui (Arthur) mais Jean-Jacques qui fréquente sa fille. La traduction FR inverse les référents ('plutôt que lui' renvoie à Jean-Jacques/ambigu). Proposer: « se réjouissait que ce soit Jean-Jacques, et non Arthur, qui fré
- 🟠 **MAJOR** `Chapter3LouisFailure3` (525438) — `accuracy/mistranslation`
  - RU: `Для меня не слишком. А вот для тебя очень даже. До встречи, кузнец Артур!`
  - FR: `Pas pour moi. Mais bien assez pour toi. À la prochaine, forgeron Arthur !`
  - «А вот для тебя очень даже» = « Mais pour toi, si (très important) ». « Mais bien assez pour toi » est confus/contresens. Proposer : « Mais pour toi, ça l'était beaucoup. »
- 🟠 **MAJOR** `Chapter3OliviaSecond2` (525510) — `fluency/punctuation`
  - RU: `Мне тоже Оливия, мне тоже...`
  - FR: `Moi non plus Olivia, moi non plus...`
  - Virgule manquante avant l'apostrophe : « Moi aussi, Olivia, moi aussi... »
- 🟠 **MAJOR** `PlaguePhraseHunter5` (525559) — `game_engine/overflow_risk`
  - RU: `За что нам всё это?!`
  - FR: `Qu'est-ce qu'on a fait pour mériter tout ça ?!`
  - Bulle/UI courte : 20 → 46 car. (×2.3) — risque de troncature.
- 🟠 **MAJOR** `PlaguePhraseKnight5` (525571) — `game_engine/overflow_risk`
  - RU: `Проткнуть бы эту чуму пикой!`
  - FR: `Si seulement je pouvais transpercer cette peste avec une pique !`
  - Bulle/UI courte : 28 → 64 car. (×2.29) — risque de troncature.
- 🟠 **MAJOR** `Fair3Prepare` (525824) — `accuracy/mistranslation`
  - RU: `Встречают по одежке`
  - FR: `L'habit fait le moine`
  - « Встречают по одежке » = on juge sur l'apparence ; « L'habit fait le moine » inverse le proverbe français habituel et prête à confusion. Proposer : « On vous juge sur la mine ».
- 🟠 **MAJOR** `Chapter3VladFailure2` (525862) — `fluency/grammar_syntax`
  - RU: `Влад, тут меня заказами завалило - руки не дошли. Уж ты прости, уверен, что невеста твоя подождет еще чуть-чуть.`
  - FR: `Vlad, je suis croulé sous les commandes, je n'ai pas encore eu le temps. Pardonne-moi, je suis sûr que ta promise pourra patienter`
  - « je suis croulé sous les commandes » est incorrect ; écrire « je croule sous les commandes ».
- 🟡 **MINOR** `NkIKONKGclmABLFC` (517503) — `fluency/punctuation`
  - RU: `Этот работник сыт. Его энергия снижается в 1.5 раза медленнее.`
  - FR: `Cet ouvrier est rassasié. Son énergie diminue 1.5 fois plus lentement.`
  - Séparateur décimal : en FR on utilise la virgule → « 1,5 fois plus lentement ».
- 🟡 **MINOR** `IKlJIUVlprUTctWf` (517511) — `fluency/punctuation`
  - RU: `Этот работник изможден и работает в 1.5 раза медленнее. Отправьте его отдыхать!`
  - FR: `Cet ouvrier est épuisé et travaille 1.5 fois plus lentement. Envoyez-le se reposer !`
  - Séparateur décimal anglais : écrire « 1,5 fois plus lentement ».
- 🟡 **MINOR** `DUeeeNMtHwPjWOdz` (517528) — `fluency/punctuation`
  - RU: `Сонная муха. Энергия этого работника расходуется в 1.5 раза быстрее.`
  - FR: `Mouche endormie. L'énergie de cet ouvrier s'épuise 1.5 fois plus vite.`
  - Séparateur décimal anglais : écrire « 1,5 fois plus vite » (incohérent avec 517527).
- 🟡 **MINOR** `GKtFBqleVEhtwBul` (517853) — `terminology/inconsistent_term`
  - RU: `Это – запас энергии вашего работника. Отправляйте уставшего работника спать, чтобы не допустить брака.`
  - FR: `C'est la réserve d'énergie de votre ouvrier. Envoyez un travailleur fatigué dormir pour éviter les défauts de fabrication.`
  - « работник » rendu par « ouvrier » puis « travailleur » dans le même segment ; uniformiser en « ouvrier ».
- 🟡 **MINOR** `SoldierDebt2` (518541) — `accuracy/mistranslation`
  - RU: `Через день верну, зуб даю!`
  - FR: `Je te rembourserai dans deux jours, juré craché !`
  - «Через день» = « demain » / « dans un jour », pas « dans deux jours ». Proposer : « Je te rembourserai demain, juré craché ! »
- 🟡 **MINOR** `SoldierDebt4` (518543) — `accuracy/mistranslation`
  - RU: `Через день верну, клянусь!`
  - FR: `Je te rembourserai dans deux jours, je le jure !`
  - Même erreur : « Через день » -> « demain », pas « dans deux jours ».
- 🟡 **MINOR** `HunterFools1` (518576) — `accuracy/mistranslation`
  - RU: `Вернусь - выпью всё пиво.`
  - FR: `Quand je rentre, je bois toute la bière.`
  - «Вернусь - выпью всё пиво» = futur : « Quand je rentrerai, je boirai toute la bière. »
- 🟡 **MINOR** `ChristmasKrampus1` (518895) — `accuracy/mistranslation`
  - RU: `Хе-хе-хе, кхм... То есть... Хо-Хо-Хо! Доброй ночи, кузнец!`
  - FR: `Hé hé hé, hum... Je veux dire... Oh, oh, oh ! Bonne nuit, forgeron !`
  - «Хо-Хо-Хо» est le rire du Père Noël : traduire par « Ho, ho, ho ! » et non « Oh, oh, oh ! ».
- 🟡 **MINOR** `HunterPrice1` (519098) — `accuracy/mistranslation`
  - RU: `Не дели шкуру неубитого медведя...`
  - FR: `Ne vends pas la peau de l'ours...`
  - Proverbe tronqué : « Ne vends pas la peau de l'ours avant de l'avoir tué... »
- 🟡 **MINOR** `StoneLeft2` (519433) — `terminology/inconsistent_term`
  - RU: `Валун, да разве мешают они нам? Делаем свое дело, платят золотом.`
  - FR: `Stone, en quoi est-ce qu'ils nous gênent ? On fait notre boulot, et ils paient en or.`
  - « Валун » rendu « Stone » ici mais « le Roc » en 518341 — unifier le nom du personnage.
- 🟡 **MINOR** `EnglandSpell2` (519537) — `fluency/punctuation`
  - RU: `Английские мастера больше не хотят работать над дизайном ваших комнат. Изменение дизайна стоит в 1.5 раза дороже.`
  - FR: `Les artisans anglais ne veulent plus travailler sur l'aménagement de vos pièces. Modifier l'aménagement coûte 1.5 fois plus cher.`
  - Décimale à l'anglaise : écrire « 1,5 fois plus cher » (virgule décimale en FR, comme au segment 519557).
- 🟡 **MINOR** `SecondOrderChoice3` (519677) — `fluency/grammar_syntax`
  - RU: `Знаю я стражу нашу - утащат в два раза больше, а потом еще и склад сожгут. Замок мне от тебя надежный нужен.`
  - FR: `Je la connais, notre garde - ils en voleront deux fois plus et brûleront l'entrepôt par-dessus le marché. C'est d'un bon cadenas d`
  - Tournure fautive : « C'est d'un bon cadenas dont j'ai besoin » (pléonasme). Corriger : « C'est d'un bon cadenas que j'ai besoin » ou « J'ai besoin d'un bon cadenas, solide ». Note aussi l'omission de « надежный/fiable » rendue acceptable.
- 🟡 **MINOR** `BaronSwordsOrderDescription` (519789) — `accuracy/omission`
  - RU: `Барон заказал целую кучу мечей. Зачем ему они - неизвестно, но барон не тот человек, которому можно отказать. Стать придворным куз`
  - FR: `Le Baron a commandé un tas d'épées. On ignore pourquoi, mais ce n'est pas le genre d'homme à qui l'on peut refuser quoi que ce soi`
  - «кузнечной карьеры» non rendu : « le sommet d'une carrière » -> « le sommet d'une carrière de forgeron dans la province ».
- 🟡 **MINOR** `plussmith` (519823) — `game_engine/markup_damage`
  - RU: `<color=#25E94F>+{0}</color> к уровню ковки предметов.`
  - FR: `<color=#25E94F> +{0}</color> au niveau de forge d'objets.`
  - Espace parasite inséré après la balise <color> ("<color=#25E94F> +{0}") ; corriger en "<color=#25E94F>+{0}</color>".
- 🟡 **MINOR** `plusmine` (519824) — `game_engine/markup_damage`
  - RU: `<color=#25E94F>+{0}</color> к уровню добычи руды.`
  - FR: `<color=#25E94F> +{0}</color> au niveau d'extraction de minerai.`
  - Espace parasite après <color=#25E94F> ; écrire "<color=#25E94F>+{0}</color>".
- 🟡 **MINOR** `plusgrind` (519825) — `game_engine/markup_damage`
  - RU: `<color=#25E94F>+{0}</color> к уровню заточки предметов.`
  - FR: `<color=#25E94F> +{0}</color> au niveau d'affûtage d'objets.`
  - Espace parasite après <color=#25E94F> ; écrire "<color=#25E94F>+{0}</color>".
- 🟡 **MINOR** `plusmelt` (519826) — `game_engine/markup_damage`
  - RU: `<color=#25E94F>+{0}</color> к уровню выплавки слитков.`
  - FR: `<color=#25E94F> +{0}</color> au niveau de fonte de lingots.`
  - Espace parasite après <color=#25E94F> ; écrire "<color=#25E94F>+{0}</color>".
- 🟡 **MINOR** `DraculaRefuse2` (520033) — `accuracy/mistranslation`
  - RU: `Повезло тебе, кузнец, что ты весь рудой провонял. Пахнешь невкусно...`
  - FR: `Tu as de la chance, forgeron, de puer autant le minerai. Tu n'as pas l'air très appétissant...`
  - «Пахнешь невкусно» = «Tu ne sens pas bon / tu n'as pas une odeur appétissante», pas «tu n'as pas l'air». Proposer : « Tu ne sens pas très appétissant... »
- 🟡 **MINOR** `FoolsFail1` (520170) — `terminology/inconsistent_term`
  - RU: `Кузнец, где там наши тупые мечи? День Дурака не ждет!`
  - FR: `Forgeron, où sont nos épées émoussées ? Le Jour des fous n'attend pas !`
  - Incohérence de casse pour le nom de la fête : « Jour des fous » ici vs « Jour des Fous » au segment 520159. Uniformiser en « Jour des Fous ».
- 🟡 **MINOR** `WildfireFailText2` (520300) — `terminology/inconsistent_term`
  - RU: `Да кто же тупой косой косит? Черт с этим полем - День Крестьянина отметим как надо!`
  - FR: `Mais qui fauche avec une faux émoussée ? Au diable ce champ, on va fêter le Jour du Paysan comme il se doit !`
  - "День Крестьянина" traduit ailleurs par « Journée du Paysan » (520291, 520297) mais ici « Jour du Paysan ». Harmoniser : « Journée du Paysan ».
- 🟡 **MINOR** `HuntRabbitResult1` (520328) — `terminology/inconsistent_term`
  - RU: `Ох и удалась охота - кролики и вправду просто объеденье! Держи гостинец от Его Королевского Величества.`
  - FR: `Quelle partie de chasse ! Les lapins étaient vraiment un régal. Tiens, un présent de la part de Sa Majesté royale.`
  - Incohérence de casse : « Sa Majesté Royale » en 520320 vs « Sa Majesté royale » ici. Harmoniser en « Sa Majesté Royale ».
- 🟡 **MINOR** `HobinFifthGoodStart2` (520875) — `fluency/spelling_orthography`
  - RU: `Будешь всегда так работать - Лука и в обиду не даст и золотом не обделит!`
  - FR: `Travaille toujours comme ça, et Louka te protègera et ne sera pas avare en or !`
  - Orthographe du futur : « protègera » -> « protégera ».
- 🟡 **MINOR** `JoanSecondSecondChoiceResult2` (521057) — `accuracy/mistranslation`
  - RU: `Карл спрятался от меня в толпе, но благодаря твоим очкам я сразу его увидела. Он поверил, что я избрана и предоставил отряд для бо`
  - FR: `Charles s'était caché au milieu de la foule, mais grâce à tes lunettes, je l'ai tout de suite repéré. Il a cru en mon dessein divi`
  - «поверил, что я избрана» = « il a cru que j'étais l'élue » ; « il a cru en mon dessein divin » est une reformulation qui s'écarte du sens.
- 🟡 **MINOR** `MonkWitchSecondChoiceAccept2` (521168) — `accuracy/mistranslation`
  - RU: `Будь ты проклят, кузнец! Я уже слышу одышку этих чертовых монахов. Не видать тебе счастья никогда!`
  - FR: `Sois maudit, forgeron ! J'entends déjà le souffle court de ces satanés moines. Tu ne connaîtras plus jamais le bonheur !`
  - «Не видать тебе счастья никогда» = « Tu ne connaîtras jamais le bonheur » ; l'ajout de « plus » modifie le sens.
- 🟡 **MINOR** `HobbitsFirstChoiceSuccessResult` (521246) — `fluency/grammar_syntax`
  - RU: `Подмастерье получил от обманщиков по голове и остался без сапог.`
  - FR: `L'apprenti a reçu un coup sur la tête par ces imposteurs et s'est retrouvé sans bottes.`
  - Construction fautive : « a reçu un coup sur la tête par ces imposteurs ». Corriger : « a reçu un coup sur la tête de la part de ces imposteurs ».
- 🟡 **MINOR** `SnowmanPhrase4` (521347) — `fluency/spelling_orthography`
  - RU: `Слава богу, весна уже рядом!`
  - FR: `Dieu merci, le Printemps est tout proche !`
  - Majuscule injustifiée : « le Printemps » -> « le printemps ».
- 🟡 **MINOR** `HobbyWorker` (521397) — `fluency/grammar_syntax`
  - RU: `Работа-хобби. Работник не требует зарплаты, если за день он работал только на верстаке.`
  - FR: `Travail-passion. L'ouvrier n'exige aucun salaire s'il n'a travaillé qu'à l'établi de la journée.`
  - Formulation fautive : « qu'à l'établi de la journée » ; corriger en « que à l'établi durant la journée » / « uniquement à l'établi ce jour-là ».
- 🟡 **MINOR** `GlobalOrderStart8` (521925) — `accuracy/omission`
  - RU: `Артур, я так рад тебя видеть! Я не знаю, как справиться со всеми этими заказами без тебя. Спасай!`
  - FR: `Arthur, je suis tellement content de te voir ! Je ne sais pas comment je ferais pour m'en sortir sans toi. À l'aide !`
  - Omission de « со всеми этими заказами » : proposer « comment je ferais pour venir à bout de toutes ces commandes sans toi ».
- 🟡 **MINOR** `GlobalOrderEnglandFail2` (522047) — `accuracy/mistranslation`
  - RU: `Да устал я что-то. Обойдутся без одного заказа, я и так им уже полно их сделал.`
  - FR: `J'étais un peu fatigué. Ils s'en passeront bien pour une fois, je leur en ai déjà fait plein.`
  - « Да устал я что-то » = présent (« Je suis un peu fatigué »), pas passé. Corriger : « Je suis un peu fatigué, là. »
- 🟡 **MINOR** `ChapterTwoBaronBadComplete1` (522277) — `accuracy/addition`
  - RU: `Так, это что за декорации? Ты смерти моей хочешь, стервец?!`
  - FR: `Minute, c'est quoi ce décor de pacotille ? Tu veux ma mort, espèce de vaurien ?!`
  - « de pacotille » est un ajout absent du RU (« что за декорации »). Proposer : « c'est quoi cette décoration ? »
- 🟡 **MINOR** `AbelardSecondOrderText` (522336) — `accuracy/omission`
  - RU: `Изготовить 7 нательных крестов`
  - FR: `Fabriquer 7 croix`
  - "нательных крестов" rendu par "croix" : l'attribut "pectorales" est omis, et incohérent avec 522338. Proposition : "Fabriquer 7 croix pectorales".
- 🟡 **MINOR** `ChapterTwoElderSecondAllDone1` (522521) — `accuracy/mistranslation`
  - RU: `Все готово! Еще кулон Оливия просила для него сделать, смотри там, чтобы в обозе не затерялся.`
  - FR: `Tout est prêt ! Olivia m'a aussi demandé de fabriquer un pendentif pour son père, fais attention qu'il ne se perde pas dans le con`
  - Le RU dit qu'Olivia a demandé de faire un pendentif « pour lui » (pour Finley) et c'est Arthur qui parle ici ; « m'a demandé de fabriquer » attribue mal l'action et « pour son père » est un ajout. Proposer : « Olivia m'a aussi demandé de lui faire un pendentif
- 🟡 **MINOR** `ChapterTwoJeanSecondFirstText` (522632) — `accuracy/mistranslation`
  - RU: `Кто не рискует, тот не становится придворным кузнецом!`
  - FR: `Qui ne risque rien ne devient pas forgeron royal !`
  - « придворный кузнец » = forgeron de la cour / forgeron du Baron, pas « royal ». Proposer : « Qui ne risque rien ne devient pas forgeron de la cour ! »
- 🟡 **MINOR** `ChapterTwoJeanSecondSecondText` (522634) — `fluency/grammar_syntax`
  - RU: `Пожалуй, не буду рисковать, от Жана-Жака всего ожидать можно.`
  - FR: `Je ferais mieux de ne pas risquer, on peut s'attendre à tout de la part de Jean-Jacques.`
  - « ne pas risquer » employé sans complément est boiteux ; préférer « Je ferais mieux de ne pas prendre de risques ».
- 🟡 **MINOR** `BaronSecondpolish` (522747) — `terminology/glossary_violation`
  - RU: `Полироль для мечей`
  - FR: `Poli pour épées`
  - « Полироль » = produit de polissage ; « Poli » est impropre. Proposer « Polish pour épées » ou « Produit de polissage pour épées ».
- 🟡 **MINOR** `GlobalOrderMerchantsFrance8` (522767) — `fluency/grammar_syntax`
  - RU: `У меня столько заказов от французских купцов. Без старого доброго Антонио их торговля встала бы!`
  - FR: `J'ai tellement de commandes de marchands français. Sans ce bon vieux Antonio, leur commerce serait au point mort !`
  - Élision manquante devant voyelle : « Sans ce bon vieil Antonio » (forme requise devant voyelle).
- 🟡 **MINOR** `MandarinianAccept1` (522808) — `fluency/grammar_syntax`
  - RU: `Заказ необычный, но сделаю. Это для сыночка вашего? Не жарко в робе ему?`
  - FR: `La commande est inhabituelle, mais je vais le faire. C'est pour votre fiston ? Il n'a pas trop chaud sous cette robe ?`
  - Reprise pronominale fautive : « la commande... je vais le faire ». Corriger en « je vais la faire ».
- 🟡 **MINOR** `ReaderHarvesting4` (523212) — `accuracy/mistranslation`
  - RU: `До зимы всего ничего.`
  - FR: `Il ne reste presque rien avant l'hiver.`
  - «До зимы всего ничего» = « L'hiver est presque là / Il ne reste presque plus de temps avant l'hiver ». La formulation « Il ne reste presque rien avant l'hiver » est ambiguë/incorrecte.
- 🟡 **MINOR** `ReaderIron5` (523291) — `accuracy/mistranslation`
  - RU: `Пинцет`
  - FR: `Pincette`
  - «Пинцет» = la pince à épiler / brucelles ; «pincette» (sing.) est impropre. Proposer « Pince ».
- 🟡 **MINOR** `VladFourthSecondChoiceResult6` (523550) — `fluency/punctuation`
  - RU: `Удачи тебе там! Опять остались мы без цирюльника....`
  - FR: `Bonne chance là-bas ! Nous revoilà sans barbier....`
  - Points de suspension mal formés : « barbier.... » → « barbier… » (trois points).
- 🟡 **MINOR** `HobinFifthFirstChoice1` (523590) — `fluency/punctuation`
  - RU: `Будет сделано! Луку давно пора приструнить.`
  - FR: `Ce sera fait ! Il est grand temps de remettre Louka à sa place !`
  - Source finit par « . », cible par « ! » — aligner la ponctuation finale.
- 🟡 **MINOR** `HobinSeventhSecondResult2` (523646) — `fluency/punctuation`
  - RU: `Что...?!`
  - FR: `Quoi...?!`
  - Espace insécable manquante avant « ?! » : « Quoi... ?! »
- 🟡 **MINOR** `DwarvesOrderDescription` (524108) — `fluency/grammar_syntax`
  - RU: `Группа низкорослых искателей приключений пришла к Артуру и позвали его в путешествие с целью исполнить пророчество.`
  - FR: `Un groupe d'aventuriers de petite taille est venu voir Arthur pour l'inviter dans un voyage afin d'accomplir une prophétie.`
  - « inviter dans un voyage » est incorrect : « inviter à un voyage » / « à le suivre dans un voyage ».
- 🟡 **MINOR** `Fair2FinleyGood6` (524273) — `accuracy/omission`
  - RU: `Спасибо, мсье Финли! Я бы с удовольствием еще с вами поболтал, но совсем скоро начинаются танцы!`
  - FR: `Merci, Monsieur Finley ! J'aurais discuté encore avec plaisir, mais les danses vont bientôt commencer !`
  - Omission de « avec vous » (еще с вами поболтал). Proposer : « J'aurais volontiers discuté encore avec vous ».
- 🟡 **MINOR** `Fair2ArtFranceWitches` (524345) — `fluency/spelling_orthography`
  - RU: `Герцог де Ла Кур был рад выступать в роли хозяина ярмарки, и с радостью дегустировал свежие зелья жрицы Брунгильды - новой фаворит`
  - FR: `Le duc De La Cour était ravi de faire office d'hôte de la foire et dégustait avec plaisir les potions fraîches de la prêtresse Bru`
  - Majuscule fautive dans la particule nobiliaire : « Le duc De La Cour » -> « le duc de La Cour » (cf. glossaire).
- 🟡 **MINOR** `Fair2ArtFranceChurch` (524346) — `fluency/spelling_orthography`
  - RU: `Герцог де Ла Кур был рад выступать в роли хозяина ярмарки, и с радостью дегустировал свежее пиво аббата Абеларда - нового фаворита`
  - FR: `Le duc De La Cour était ravi de faire office d'hôte de la foire et dégustait avec plaisir la bière fraîche de l'abbé Abélard - le `
  - « Le duc De La Cour » -> « le duc de La Cour » (particule en minuscule, conforme au glossaire).
- 🟡 **MINOR** `pluscarve` (524602) — `game_engine/markup_damage`
  - RU: `<color=#25E94F>+{0}</color> к уровню резьбы по дереву.`
  - FR: `<color=#25E94F> +{0}</color> au niveau de sculpture sur bois.`
  - Espace parasite ajoutée à l'intérieur de la balise <color> avant +{0}. Corriger : <color=#25E94F>+{0}</color>.
- 🟡 **MINOR** `Chapter3BaronSecondSecond3` (524911) — `fluency/punctuation`
  - RU: `Правда, купцы будут очень недовольны, да и черт с ними! Одни деньги на уме! Коли помрем тут все, кто их тратить-то будет!`
  - FR: `Certes, les marchands vont faire la gueule, mais au diable ces avares ! Ils ne pensent qu'à l'argent ! Si nous crevons tous ici, q`
  - Source finit par « ! », cible par « ? » — restaurer le point d'exclamation.
- 🟡 **MINOR** `Chapter3FinleySecondOrderText` (524944) — `accuracy/mistranslation`
  - RU: `Где чума, там и предприимчивый купец!`
  - FR: `Là où frappe la peste, il y a toujours un Marchand entreprenant !`
  - Majuscule injustifiée à « Marchand » (nom commun, pas un nom propre) ; proposer « un marchand entreprenant ».
- 🟡 **MINOR** `Chapter3DelakurFourthRefuse3` (525270) — `fluency/punctuation`
  - RU: `Теперь подельники Луки будут продолжать топтать гордую землю Франции, потому что нам не хватит оружия добить всех....`
  - FR: `Maintenant, les complices de Louka vont continuer de fouler la fière terre de France, car nous manquerons d'armes pour tous les ac`
  - Quatre points au lieu des points de suspension : écrire « ...achever... » avec « … » (trois points).
- 🟡 **MINOR** `Chapter3BaronFifthFailure1` (525498) — `fluency/grammar_syntax`
  - RU: `Где там молот для Барона? Он сказал ударить тебя им по голове, если плохо получится.`
  - FR: `Où est le marteau pour le Baron ? Il a dit de t'en frapper sur la tête si c'était mal fait.`
  - « te frapper sur la tête » avec « en » est agrammatical/ambigu ; corriger : « de t'en donner un coup sur la tête ».
- 🟡 **MINOR** `Chapter3HeadmanThird2` (525660) — `fluency/punctuation`
  - RU: `Понял я, Артур, все я про тебя понял....`
  - FR: `J'ai compris, Arthur, j'ai tout compris sur toi....`
  - Quatre points au lieu des points de suspension (RU en a 4 aussi mais la norme FR est '...'). Corriger : « j'ai tout compris sur toi... »
- 🟡 **MINOR** `Chapter3ImposterStart2` (525688) — `fluency/punctuation`
  - RU: `Ох, я думал, больше никогда тебя не увижу.... Давай, говори свои новости.`
  - FR: `Oh, je croyais ne plus jamais te revoir.... Allez, quelles sont les nouvelles ?`
  - Source finit par « новости. », cible par « nouvelles ? » — point final perdu.
- 🟡 **MINOR** `Chapter3BaronPrepareExtra3` (525796) — `accuracy/mistranslation`
  - RU: `Очень рад, что мой доспех вас спас, и что вы, наконец, по достоинству оценили мой труд!`
  - FR: `Je suis ravi que mon armure vous ait sauvé et que vous appréciez enfin mon travail à sa juste valeur !`
  - «наконец, по достоинству оценили» est au passé/accompli ; « que vous appréciez enfin » manque le subjonctif attendu après « Je suis ravi ». Proposer : « et que vous appréciiez enfin mon travail à sa juste valeur ».
- 🟡 **MINOR** `Chapter3VladThirdFailure1` (525892) — `fluency/punctuation`
  - RU: `Понимаю вас, меня тоже очень сильно пугает чума, но я абсолютно не представляю, что вы можете сделать, кроме как молиться Господу.`
  - FR: `Je vous comprends, la peste m'effraie beaucoup moi aussi, mais je n'ai absolument aucune idée de ce que vous pourriez faire d'autr`
  - Quatre points au lieu des points de suspension : remplacer « Господу.... » -> « le Seigneur... ».
- 🟡 **MINOR** `Chapter3VladFourFailure1` (525900) — `fluency/punctuation`
  - RU: `Он ужасно устал и почти не спал несколько дней. Больных все больше и больше, я совсем не знаю, что делать. Он может заразиться в л`
  - FR: `Il est terriblement épuisé et n'a presque pas dormi depuis des jours. Il y a de plus en plus de malades, je ne sais plus du tout q`
  - Quatre points en fin de phrase : « à tout moment... » (trois points).
- 🟡 **MINOR** `Fair3GuessingThreeSixthFirst1` (526424) — `accuracy/mistranslation`
  - RU: `Вы выбрали самый эффективный, пусть и менее выгодный, вариант!`
  - FR: `Vous avez choisi l'option la plus efficace, même si elle était la moins rentable !`
  - «пусть и менее выгодный» = « même si moins rentable », pas superlatif « la moins rentable ». Corriger : « même si elle était moins rentable ».
- 🟡 **MINOR** `Chapter3NephewFifthNew1` (526482) — `accuracy/mistranslation`
  - RU: `Здравствуй, Артур, как твои дела?`
  - FR: `Bonjour, Arthur, comment vont les affaires ?`
  - «как твои дела ?» = « comment vas-tu ? » et non « comment vont les affaires ? » (sens restreint ajouté).

## 3. Анализ чеков Weblate (Layer 0), 447 срабатываний на 443 юнитах

| Чек | Юнитов | Вердикт |
|---|---|---|
| `game-length` | 414 | 14 коротких UI-реплик — реальный риск переполнения (×2,08–2,52, см. журнал); 400 — длинные диалоги и описания, расширение fr ≈ ×1,3–1,6, дефектом не является |
| `duplicate` | 21 | Все ложные: мимикрия смеха («ха-ха» → «ha ha», «хе-хе» → «hé hé») и одинаковые реплики барона в альтернативных ветках ярмарки |
| `game-number` | 2 | Оба ложные: «выросли в 2 раза» → «ont doublé», «С 10 вечера» → «De 22 h» |
| `end_stop` | 4 | 2 ложных (внутренний «?» при целой финальной точке: 522653, 523684), 1 минор (525688), 1 — двойной учёт того же юнита |
| `end_question` | 4 | 2 ложных (522653, 523684), 1 минор (525688), 1 (524911) учтён как end_exclamation |
| `end_exclamation` | 2 | Оба минорных (523590, 524911) |

`game-markup`, `game-token`, `game-line-break`, `cyrillic-leak`, `inconsistent`, `same` —
**ноль срабатываний**. Найденная критическая поломка 519913 (литеральный `\n` → реальный
перевод строки) чеками **не ловится** — это пробел в покрытии проверок, а не их ложное молчание.

Отдельно: **дефект самого русского источника** — 525591 `SpawnRate` содержит
`</color=yellow>{0}</color>` (закрывающий тег вместо открывающего). Перевод точно
повторяет источник, поэтому в счёт не входит, но чинить нужно в исходном ките.

## 4. Структура подтверждённых дефектов

| Категория | Юнитов |
|---|---|
| `accuracy/mistranslation` | 38 |
| `game_engine/overflow_risk` | 14 |
| `fluency/punctuation` | 14 |
| `fluency/grammar_syntax` | 12 |
| `terminology/inconsistent_term` | 8 |
| `game_engine/markup_damage` | 7 |
| `fluency/spelling_orthography` | 4 |
| `accuracy/omission` | 4 |
| `fluency/register_tone` | 3 |
| `game_engine/broken_placeholder` | 1 |
| `accuracy/addition` | 1 |
| `terminology/glossary_violation` | 1 |

Повторяющиеся системные паттерны:
- **`Коновал` → «Hongreur»** (517799, 517971, 518239): «коновал» здесь — знахарь/коновал-лекарь, а не «кастратор лошадей». Термин надо зафиксировать в глоссарии.
- **Имя `Гобин`** переведено то «Gobin», то «Hobin» (520889, 520893, 523672 и др.).
- **Имя `Валун`** — «Stone» против «le Roc» (519433).
- **`Вероника`** — «Veronica» против «Véronique» (525887, 525922).
- **Паразитные пробелы/табуляции после `<color=...>`** в бонус-строках `plus*` (519823–519826, 521444, 521445, 524602).
- **Четырёхточие «....»** вместо французского многоточия (523550, 525270, 525660, 525892, 525900).
- **Десятичная точка вместо запятой** в числах «1.5» (517503, 517511, 517528, 519537).

## 5. План исправлений

1. **Блокирующее:** 519913 — вернуть литеральный `\n` (сверить с 519914). До этого релиз закрыт.
2. **Мажорные 48:** контрсенсы (517764, 517898, 518190, 518380, 519014, 519347, 519590, 519708, 519710, 520198, 520218, 520668, 522720, 523134, 524342, 525438, 525510, 525824), терминология «коновал»/«Гобин»/«Лесная братва», грамматика («Ça fête encore !», «je suis croulé»), сломанная разметка `plus*`, 14 переполнений UI.
3. **Минорные 58:** пунктуация (многоточия, неразрывный пробел, десятичная запятая), орфография, мелкие сдвиги смысла и опущения.
4. **Флаги Weblate:** 519547, 519569 → `ignore-game-number`; 21 юнит `duplicate` → `ignore-duplicate`; 522653, 523684 → `ignore-end-stop`, `ignore-end-question`.
5. **В исходный кит:** починить 525591 (`</color=yellow>` → `<color=yellow>`).
6. **В проверки:** добавить проверку сохранности литеральной `\n` — текущий набор чеков её не видит.

## Артефакты

- Полный отчёт скрипта (9482 вердикта): `/tmp/lqa_anvil_fr_full.md`
- Вердикты с `review_scope: full`: `/tmp/lqa_anvil_fr_verdicts_full.json`
- Сырые данные чеков: `/tmp/lqa_anvil_fr_raw.json`
- Классификация game-length (414 юнитов): `/tmp/lqa_anvil_fr_length_class.json`
