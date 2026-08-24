# Need for Greed major translation corrections Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** Approved 2026-08-24.

**Goal:** Correct the 58 analyst-reviewed Major and Critical Need for Greed translation defects from the 2026-08-24 LQA audit, excluding Google Play.

**Architecture:** Weblate is the source of truth. A correction is identified only by the tuple `(component, language, unit ID, key)`, never by source text alone. Before a batch write, retrieve every unit and require its source and current target to equal the audit's recorded values. A mismatch is skipped and reported. Write only the approved target, then reread the unit and verify the target, source, state, flags, markup, placeholders and tokens.

**Tech Stack:** Weblate MCP read/write endpoints, production Weblate audit diagnostics, `docs/operations/audits/2026-08-24-need-for-greed-multilingual-lqa.md`.

---

## Task 1: Capture a guarded production preview

**Files:**

- Create: `docs/operations/reports/2026-08-24-need-for-greed-major-corrections.md`
- Read: `docs/operations/audits/2026-08-24-need-for-greed-multilingual-lqa.md`

1. Retrieve every unit in Tasks 2-7 from production Weblate by exact component, language and key.
2. Confirm the returned unit ID is the listed ID and capture its source, target, state and flags.
3. Compare source and target to the audit. A discrepancy is a per-unit guard failure: do not write it and record it in the report.
4. Publish the preview in the report before the first production write: component, language, unit ID, key, old target and approved new target. Never include credentials.

## Task 2: German corrections

**Language:** `de`  
**Count:** 2

- `(ui, 360308, chestUsedIn)` -> `Kann im <color=#E3BA59>Truhenplatz</color> oder im <color=#E3BA59>Super-Platz</color> geöffnet werden.`
- `(loot, 365020, treasure.3g.idol.cheap.t1.1)` -> `Götzenbild der Schnurrbärtigkeit`

Write only source- and old-target-matched rows in separate `ui/de` and `loot/de` batches. Reread and assert exact targets.

## Task 3: French critical and major corrections

**Language:** `fr`  
**Count:** 10

- `(ui, 359515, amountFormatted)` -> `{value:cond:>99999?{value:amount()}|}{value:cond:<=99999?{value:N0}|}`
- `(ui, 359527, humanTimer)` -> `{hours:cond:>0?{hours}h. |}{minutes:cond:>0?{minutes}m. |}{seconds:cond:>=0?{seconds}s.|} `
- `(ui, 359637, craftSlot)` -> `Emplacement d’artisanat`
- `(ui, 359747, upgradeCraftTitle)` -> `Niv. d’artisanat {value}`
- `(tutorial, 367161, DialogueBeforeUpgradeCharacter2)` -> `D'accord, j'investirai en moi — mais seulement pour que tu arrêtes de me harceler !`
- `(survey, 366774, survey1Question3Answer1)` -> `Courir partout et ramasser du butin`
- `(orders, 366206, orderTerrain1.2Step2Title)` -> `Rouleur de boules`
- `(orders, 366208, orderTerrain1.2Step2Thank)` -> `Ce scarabée est un sacré rouleur de boules !`
- `(loot, 365287, treasureGoldRing1GT1.2)` -> `Bracelet étroit en pierre (Or)`
- `(characterdialogue, 364774, eggItemReceived)` -> `Un œuf ! Il faut le faire éclore.`

Write guarded component/language batches and reread every row. For `amountFormatted` and `humanTimer`, assert byte-for-byte target equality, including `humanTimer`'s terminal space and no spaces inside engine expressions.

## Task 4: Polish corrections

**Language:** `pl`  
**Count:** 5

- `(ui, 369281, upgradeCraftTitle)` -> `Poziom wytwarzania {value}`
- `(ui, 369286, upgradeCraftChestSlotsStat)` -> `Miejsce na skrzynię`
- `(ui, 369376, chestUsedIn)` -> `Może być otwarty w <color=#E3BA59>Miejscu na skrzynię</color> lub <color=#E3BA59>Super slocie</color>.`
- `(orders, 366512, orderTerrain1.2Step2Title)` -> `Toczyciel kul`
- `(orders, 366514, orderTerrain1.2Step2Thank)` -> `Ten chrząszcz to wielki toczyciel kul!`

Write guarded component/language batches. Reread all rows and verify markup preservation.

## Task 5: Italian corrections

**Language:** `it`  
**Count:** 8

- `(ui, 368815, upgradeCraftTitle)` -> `Livello creazione {value}`
- `(tutorial, 367350, DialoguePickLootIntroTrader2)` -> `Scegli il meglio di ciò che puoi portare al magazzino.`
- `(tutorial, 367365, DialogueBeforeUpgradeCharacter2)` -> `Va bene, investirò in me stesso — ma solo per farti smettere di assillarmi!`
- `(survey, 366842, survey1Question3Answer1)` -> `Correre in giro e fare bottino`
- `(orders, 366411, orderTerrain1.2Step2Description)` -> `Portami uno Scarabeo d'oro. Sono grandi coleotteri che si possono trovare mentre strisciano tra i cespugli.`
- `(orders, 366412, orderTerrain1.2Step2Thank)` -> `Questo scarabeo è un vero campione nel far rotolare palline!`
- `(loot, 365616, treasure2GIdolCheapT1.2)` -> `Idolo della Testa Bruciacchiata`
- `(loot, 365625, Frid.Cheap)` -> `Maschera di lunedì 12`

Write guarded component/language batches and reread every row.

## Task 6: Turkish corrections

**Language:** `tr`  
**Count:** 16

- `(ui, 369527, humanTimer)` -> `{hours:cond:>0?{hours} sa. |}{minutes:cond:>0?{minutes} dk. |}{seconds:cond:>=0?{seconds} sn.|} `
- `(ui, 369657, craftItem)` -> `Eşya üret`
- `(ui, 369662, craftClosedRecipeDescription)` -> `Yetersiz Demirhane Seviyesi\n\nHer yükseltmeyle giderek daha nadir ve değerli hazineler üretebileceksiniz. Bu fırını efsanelere yaraşır bir demirhaneye dönüştürme zamanı!`
- `(ui, 369672, craftStockFullTitle)` -> `Depoda hazine için yer yok!`
- `(ui, 369894, dealOfTheDayDescription)` -> `Yeni teklifler şu süre sonra sunulacak:`
- `(tutorial, 367554, DialoguePickLootIntroTrader2)` -> `Depoya taşıyabileceğin en iyi eşyaları seç.`
- `(tutorial, 367606, DialogueClaimedChestCharacter1)` -> `Evet, sandık burada ama anahtar yok — ama sihirli demirhaneyi unuttun, kısrak! Her şeyi her şeyle yapabilir.`
- `(tutorial, 367637, tip9)` -> `Yeni mücevher tariflerinin kilidini açmak için demirhaneyi yükseltin.`
- `(orders, 366615, orderTerrain1.2Step2Description)` -> `Bana bir Altın Skarabe getir. Bunlar çalılıklarda sürünürken bulunabilen büyük böceklerdir.`
- `(orders, 366657, orderTerrain2.1Step4Description)` -> `Orta Boy Donmuş Elmasın buzunu çözün. Donmuş elmaslar genellikle tüm kayalarda bulunur.`
- `(orders, 366670, orderTerrain2.2Step6Title)` -> `Yengeç Sıcak Paltosu`
- `(loot, 365878, artifactBirdEntity)` -> `Talihin Yumurta Sarısı`
- `(loot, 365881, artifactBook)` -> `Kadim Kitap`
- `(loot, 365890, goldSmallScarab)` -> `Altın Skarabe`
- `(loot, 365953, gem.diamond.big.bad)` -> `Kenarından Parça Kopmuş Büyük Elmas`
- `(loot, 365963, frozen.artifact.book.t4.1)` -> `Donmuş Kadim Kitap`

Write guarded component/language batches and reread every row. Preserve the exact terminal space in `humanTimer`.

## Task 7: Indonesian corrections

**Language:** `id`  
**Count:** 17

- `(ui, 368259, craftItem)` -> `Buat item`
- `(ui, 368264, craftClosedRecipeDescription)` -> `Level Bengkel Tempa Tidak Memadai\n\nDengan setiap peningkatan, Anda akan dapat membuat harta karun yang semakin langka dan berharga. Saatnya mengubah tungku ini menjadi bengkel tempa legendaris!`
- `(tutorial, 367266, DialogueAfterUpgradeCharacter2)` -> `Yah, tidak perlu memaksa! Kamu punya fetis sendiri — kuku kuda dan oat.`
- `(tutorial, 367293, DialogueBeforeBirdCharacter2)` -> `Kandang di punggungku? Ini Kenari Pemburu Harta Karun! Sekarang mari kita cari tahu di mana letak rahasianya.`
- `(tutorial, 367300, DialogueClaimedChestCharacter1)` -> `Ya, petinya ada di sini, tetapi tidak ada kunci — tapi kau lupa tentang bengkel tempa ajaib, kuda betina! Tempat itu bisa membuat apa saja dari apa pun.`
- `(tutorial, 367310, DialogueNewTerrainStartedCharacter1)` -> `Medan berbeda. Tanah becek. Ugh, udara ini sarat makna sampai menyengat!`
- `(tutorial, 367331, tip9)` -> `Tingkatkan Bengkel Tempa untuk membuka resep perhiasan baru.`
- `(survey, 366809, survey1Question3Answer2)` -> `Membuat dan menjual barang`
- `(orders, 366297, orderTerrain1.1Step4Title)` -> `Peti Orang Mati`
- `(orders, 366343, orderTerrain2.1Step2Description)` -> `Temukan 2 Nugget Perak di dalam es batu, lalu hangatkan.`
- `(orders, 366348, orderTerrain2.1Step3Description)` -> `Temukan 2 Nugget Emas di dalam es batu, lalu hangatkan.`
- `(loot, 365431, roughMaskT1)` -> `Topeng Wajah Rusak`
- `(loot, 365448, treasureGoldMask2GT1.1)` -> `Topeng Wajah Emas`
- `(loot, 365462, treasure2GIdolCheapT1.2)` -> `Berhala Kepala Hangus`
- `(loot, 365470, Frid)` -> `Topeng Jumat Malam`
- `(loot, 365471, Frid.Cheap)` -> `Topeng Senin Tanggal 12`
- `(characterdialogue, 364819, frostible)` -> `P-p-perlu \nm-m-menghangatkan diri!`

Write guarded component/language batches and reread every row.

## Task 8: Verify and publish the correction record

**Files:**

- Create: `docs/operations/reports/2026-08-24-need-for-greed-major-corrections.md`
- Modify: `docs/changes.rst` only if a current unreleased section exists and this released user-visible correction needs a note.

1. Reread every source-matched, written unit and assert exact source/target equality; preserve state and flags unless Weblate changed them as a direct saving consequence.
2. Run quality diagnostics for every touched component-language pair. The French technical expressions must not fail `game-markup`; no changed target may introduce a markup, placeholder, token, number or line-break failure.
3. Write the report with old and new targets, unit IDs, verification results, and skipped guard failures. Do not claim a full component release pass because the UI audit was sampled.
4. Commit and push the plan and report using `fix(translations): correct need for greed major defects`.

## Out of scope

- Every `google-play` string, including empty `gp_full` translations and defective short descriptions.
- Minor, Neutral, stale and false-positive diagnostics.
- UI rows not inspected by the audit sample.
- Any language or key absent from the audit's Major/Critical defect log.
- `ignore-*` flags; flags are not a substitute for a correction.
