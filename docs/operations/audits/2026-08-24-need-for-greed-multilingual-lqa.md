# Weblate LQA Audit: need-for-greed / all game components (DE)

> Findings are analyst-reviewed with two conservative LLM-assisted passes in this session. They are not verdicts from `weblate/trans/judge.py`.

## 1. MQM-Core Quality Scorecard

| Metric | Value |
|---|---|
| **Coverage mode** | `sample` |
| **Units reviewed** | 537 / 896 (59.9%) |
| **Words reviewed (MQM denominator)** | 3140 / 5050 (62.2%) |

| Metric | Value | Status |
|---|---|---|
| **MQM Score** | **98.47 / 100** | **Fail (Critical Blocker)** |
| **Release Gate Status** | Blocked (Critical defect must be resolved before release) | BLOCKED |
| **Critical Defects (25 pt)** | 1 | See log |
| **Major Defects (5 pt)** | 3 | See log |
| **Minor Defects (1 pt)** | 8 | See log |
| **Total Penalty Points** | 48 pt | Tool-computed using 3140 reviewed source words |

### Component scorecards

| Component | Coverage | Score | Grade | C/M/m |
|---|---:|---:|---|---:|
| UI | sample | 98.69 | Not gradable (partial coverage) | 0/1/3 |
| Tutorial | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Survey | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Orders | full | 99.60 | Grade A (Pass) | 0/0/3 |
| Loot | full | 98.21 | Grade A (Pass) | 0/1/2 |
| Character dialogue | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Buyers | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Google Play | full | 90.91 | Fail (Critical Blocker) | 1/1/0 |

## 2. Reviewed Defect Log (MQM Categories)

- **MINOR** `dailyRewardsDescription` (Unit 359988, `ui`)
  - **Source:** `Visit the game every day\nand get rewards.`
  - **Target:** `Besuchen Sie das Spiel jeden Tag\nund erhalten Sie Belohnungen.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** „Besuchen Sie das Spiel“ is an unnatural German collocation for launching or entering a game. „Öffnen Sie das Spiel“ conveys the intended daily action naturally.
  - **Recommended target:** `Öffnen Sie das Spiel jeden Tag\nund erhalten Sie Belohnungen.`
- **MINOR** `craftStockFullTitle` (Unit 360138, `ui`)
  - **Source:** `No space for treasure in the storage!`
  - **Target:** `Kein Platz für Schätze in der Lagerung!`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** „Lagerung“ denotes storage as an activity or process and is unnatural as the location that has run out of space. „Lager“ is the appropriate location noun.
  - **Recommended target:** `Kein Platz für Schätze im Lager!`
- **MINOR** `upgradeCraftTitle` (Unit 360213, `ui`)
  - **Source:** `Craft lvl {value}`
  - **Target:** `Herstellen Stufe {value}`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** „Herstellen Stufe“ is not a grammatical German compound or noun phrase. „Herstellungsstufe“ correctly expresses the crafting level and preserves the placeholder.
  - **Recommended target:** `Herstellungsstufe {value}`
- **MAJOR** `chestUsedIn` (Unit 360308, `ui`)
  - **Source:** `Can be opened in <color=#E3BA59>Chest Slot</color> or the <color=#E3BA59>Super Slot</color>.`
  - **Target:** `Kann im <color=#E3BA59>Brustplatz</color> oder im <color=#E3BA59>Super-Platz</color> geöffnet werden.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** In a statement about where something can be opened, “Chest Slot” refers to a slot for a chest. „Brustplatz“ instead uses the anatomical meaning of “chest” and misidentifies the gameplay location.
  - **Recommended target:** `Kann im <color=#E3BA59>Truhenplatz</color> oder im <color=#E3BA59>Super-Platz</color> geöffnet werden.`
- **MINOR** `orderTerrain2.1Step1Description` (Unit 366024, `orders`)
  - **Source:** `Bring me 2 Frozen Items from the frozen rocks`
  - **Target:** `Bringe mir 2 Gefrorene Gegenstände von den gefrorenen Felsen`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** The attributive adjective modifying „Gegenstände“ must be lowercase in the running sentence.
  - **Recommended target:** `Bringe mir 2 gefrorene Gegenstände von den gefrorenen Felsen`
- **MINOR** `orderTerrain2.2Step4Title` (Unit 366052, `orders`)
  - **Source:** `Crab Warmth`
  - **Target:** `Krabbewärme`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** The compound requires the standard combining form „Krabben-“; „Krabbewärme“ is malformed.
  - **Recommended target:** `Krabbenwärme`
- **MINOR** `orderTerrain2.3Step7Description` (Unit 366077, `orders`)
  - **Source:** `Bring me 5 Gold Chunk. They are quite common in the frozen rocks.`
  - **Target:** `Bring mir 5 Goldbrocken. Sie sind ziemlich Gewöhnlich in den gefrorenen Felsen.`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** „gewöhnlich“ is a predicate adjective here and must not be capitalized.
  - **Recommended target:** `Bring mir 5 Goldbrocken. Sie sind ziemlich gewöhnlich in den gefrorenen Felsen.`
- **MINOR** `Frid.Cheap` (Unit 365009, `loot`)
  - **Source:** `Monday 12th Mask`
  - **Target:** `Maske vom Montag, dem 12`
  - **Category:** `fluency/punctuation`
  - **Explanation:** The German ordinal date requires a period after the day number, so “12th” must be rendered as „12.“.
  - **Recommended target:** `Maske vom Montag, dem 12.`
- **MAJOR** `treasure.3g.idol.cheap.t1.1` (Unit 365020, `loot`)
  - **Source:** `Idol of the Mustachedness`
  - **Target:** `Götzenbild der Bärtigkeit`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Mustachedness” specifically denotes having a mustache, whereas „Bärtigkeit“ refers to beardedness generally and omits the defining mustache meaning.
  - **Recommended target:** `Götzenbild der Schnurrbärtigkeit`
- **MINOR** `gem.sapphire.medium.t1.bad` (Unit 365037, `loot`)
  - **Source:** `Lopsided Sapphire`
  - **Target:** `Schiefes Saphir`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** „Saphir“ is masculine, so the adjective must take the masculine nominative ending: „Schiefer Saphir“.
  - **Recommended target:** `Schiefer Saphir`
- **MAJOR** `gp_short` (Unit 372401, `google-play`)
  - **Source:** `Join an adventure as a digger! Dig, craft, run from monsters! RPG mining game!`
  - **Target:** `Begib dich als Gräber auf ein Abenteuer! Grabe, stelle her, flüchte vor Monstern! RPG-Minenspiel!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** „Gräber“ is liable to mean “graves” and does not clearly denote the playable digger, while „RPG-Minenspiel“ does not convey an RPG about mining. The replacement accurately identifies a treasure digger and a mining RPG.
  - **Recommended target:** `Begib dich als Schatzgräber auf ein Abenteuer! Grabe, stelle Gegenstände her und flüchte vor Monstern! Ein Mining-RPG!`
- **CRITICAL** `gp_full` (Unit 372402, `google-play`)
  - **Source:** `<b>Need for Greed: Mining Time is an RPG that blends the thrill of mining treasures, crafting valuable items, and selling them for profit.</b>\n\nBrave mysterious lands full of danger and rewards — every dig can bring fortune or trouble. If you enjoy adventure games and mining games, this high-stakes adventure will keep you on edge.\n\n<b>⛏️ Dig, Craft, and Trade!</b>\n Play this RPG as a true Digger: mine deep and craft raw loot into goods, then sell them for gold. Spend profits like in a busi…`
  - **Target:** `[empty]`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The target is empty while the source is a meaningful full Google Play store description.

## 3. Weblate Quality Checks Analysis (Layer 0)

| Check | Count | Classification |
|---|---:|---|
| `duplicate` | 1 | No defect found in the returned German UI row. |
| `inconsistent` | 2 | Not scored: each returned row has no duplicate same-source peer in its component inventory; retain as stale or cross-component diagnostic pending a fresh check refresh. |
| `same` | 47 | All but the French Ball-Roller item were benign names, syntactic placeholders, onomatopoeia, or accepted game terms; Ball-Roller is logged as a Major terminology/untranslated issue. |

## 4. Actionable Remediation Plan

1. **Release blockers:** translate `google-play/gp_full` (the empty full store description) for this language. French must also restore the exact engine expressions in `ui/amountFormatted` and `ui/humanTimer` without spaces inside `{...}`.
2. **String corrections:** apply the recommended targets in the defect log for every Major/Minor item whose recommendation is supplied. For full store descriptions, commission a complete locale-native marketing translation rather than copying the source.
3. **Weblate flags:** after an editor validates the classifications, add `ignore-reused` to Italian Units 368597, 368705, 368819, 368893 and Turkish Units 369529, 369825. Do not add `ignore-game-markup` to French Units 359515/359527 or `ignore-reused` to Turkish Unit 366670. Do not bulk-add `ignore-same`; review each term/name first.
4. **Remaining coverage:** every non-UI component was reviewed in full. The only unreviewed strings are in `ui`: units not at a zero-based multiple-of-five API position and without a current check warning. Review those remaining UI units before claiming a component-wide project grade.


---

# Weblate LQA Audit: need-for-greed / all game components (FR)

> Findings are analyst-reviewed with two conservative LLM-assisted passes in this session. They are not verdicts from `weblate/trans/judge.py`.

## 1. MQM-Core Quality Scorecard

| Metric | Value |
|---|---|
| **Coverage mode** | `sample` |
| **Units reviewed** | 542 / 896 (60.5%) |
| **Words reviewed (MQM denominator)** | 3146 / 5050 (62.3%) |

| Metric | Value | Status |
|---|---|---|
| **MQM Score** | **96.28 / 100** | **Fail (Critical Blocker)** |
| **Release Gate Status** | Blocked (Critical defect must be resolved before release) | BLOCKED |
| **Critical Defects (25 pt)** | 3 | See log |
| **Major Defects (5 pt)** | 8 | See log |
| **Minor Defects (1 pt)** | 2 | See log |
| **Total Penalty Points** | 117 pt | Tool-computed using 3146 reviewed source words |

### Component scorecards

| Component | Coverage | Score | Grade | C/M/m |
|---|---:|---:|---|---:|
| UI | sample | 90.28 | Fail (Critical Blocker) | 2/2/0 |
| Tutorial | full | 99.12 | Grade A (Pass) | 0/1/2 |
| Survey | full | 96.43 | Grade A (Pass) | 0/1/0 |
| Orders | full | 98.68 | Grade A (Pass) | 0/2/0 |
| Loot | full | 98.72 | Grade A (Pass) | 0/1/0 |
| Character dialogue | full | 94.51 | Grade B (Conditional) | 0/1/0 |
| Buyers | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Google Play | full | 92.42 | Fail (Critical Blocker) | 1/0/0 |

## 2. Reviewed Defect Log (MQM Categories)

- **CRITICAL** `amountFormatted` (Unit 359515, `ui`)
  - **Source:** `{value:cond:>99999?{value:amount()}|}{value:cond:<=99999?{value:N0}|}`
  - **Target:** `{value :cond :>99999 ?{value :amount()}|}{value :cond :<=99999 ?{value :N0}|}`
  - **Category:** `game_engine/broken_placeholder`
  - **Explanation:** The target inserts non-breaking and narrow spaces inside the custom formatting expressions, changing the engine syntax from the source and potentially breaking placeholder parsing.
  - **Recommended target:** `{value:cond:>99999?{value:amount()}|}{value:cond:<=99999?{value:N0}|}`
- **CRITICAL** `humanTimer` (Unit 359527, `ui`)
  - **Source:** `{hours:cond:>0?{hours}h. |}{minutes:cond:>0?{minutes}m. |}{seconds:cond:>=0?{seconds}s.|} `
  - **Target:** `{hours :cond :>0 ?{hours}h. |}{minutes :cond :>0 ?{minutes}m. |}{seconds :cond :>=0 ?{seconds}s.|} `
  - **Category:** `game_engine/broken_placeholder`
  - **Explanation:** The target adds non-breaking and narrow spaces within all three conditional placeholders, altering syntax that must remain unchanged for reliable engine parsing.
  - **Recommended target:** `{hours:cond:>0?{hours}h. |}{minutes:cond:>0?{minutes}m. |}{seconds:cond:>=0?{seconds}s.|} `
- **MAJOR** `craftSlot` (Unit 359637, `ui`)
  - **Source:** `Craft slot`
  - **Target:** `Artisanat`
  - **Category:** `accuracy/omission`
  - **Explanation:** “Artisanat” conveys crafting but omits “slot,” so the target no longer identifies the label as a crafting slot.
  - **Recommended target:** `Emplacement d’artisanat`
- **MAJOR** `upgradeCraftTitle` (Unit 359747, `ui`)
  - **Source:** `Craft lvl {value}`
  - **Target:** `Fabriquer niv. {value}`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Fabriquer niv. {value}” uses the infinitive “Fabriquer” where the source denotes a crafting-level label, producing a materially malformed French UI title.
  - **Recommended target:** `Niv. d’artisanat {value}`
- **MAJOR** `DialogueBeforeUpgradeCharacter2` (Unit 367161, `tutorial`)
  - **Source:** `Fine, I'll invest in myself — but only so you stop nagging me!`
  - **Target:** `D'accord, j'investirai en moi — mais seulement si tu arrêtes de me harceler !`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Seulement si” changes the purpose expressed by “only so” into a condition meaning “only if,” materially altering the relationship between investing and stopping the nagging.
  - **Recommended target:** `D'accord, j'investirai en moi — mais seulement pour que tu arrêtes de me harceler !`
- **MINOR** `DialoguePlayerDeathCharacter1` (Unit 367217, `tutorial`)
  - **Source:** `TELL HER I REGRETTED THIS!!!`
  - **Target:** `DITES-LUI QUE JE L'AI REGRETTÉ!!!`
  - **Category:** `fluency/punctuation`
  - **Explanation:** The target omits the required French space before the exclamation-mark cluster.
  - **Recommended target:** `DITES-LUI QUE JE L'AI REGRETTÉ !!!`
- **MINOR** `tip11` (Unit 367231, `tutorial`)
  - **Source:** `Items marked with “?” can be appraised by the Trader.`
  - **Target:** `Les objets marqués d'un « ? » peuvent être évalués par le Marchand.`
  - **Category:** `fluency/punctuation`
  - **Explanation:** The target uses a breaking space before the closing French guillemet; the inner spacing should be non-breaking on both sides.
  - **Recommended target:** `Les objets marqués d'un « ? » peuvent être évalués par le Marchand.`
- **MAJOR** `survey1Question3Answer1` (Unit 366774, `survey`)
  - **Source:** `Run around and loot`
  - **Target:** `Courir et Butin`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The target coordinates the infinitive “Courir” with the noun “Butin,” so it is ungrammatical and fails to express the action “loot.”
  - **Recommended target:** `Courir partout et ramasser du butin`
- **MAJOR** `orderTerrain1.2Step2Title` (Unit 366206, `orders`)
  - **Source:** `Ball-Roller`
  - **Target:** `Ball-Roller`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The English gameplay term “Ball-Roller” remains untranslated in French; its treatment should be confirmed against the project terminology before release.
  - **Recommended target:** `Rouleur de boules`
- **MAJOR** `orderTerrain1.2Step2Thank` (Unit 366208, `orders`)
  - **Source:** `This beetle is a big ball-roller!`
  - **Target:** `Ce scarabée est un grand Ball-Roller !`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The English gameplay term “Ball-Roller” remains untranslated in French; its treatment should be confirmed against the project terminology before release.
  - **Recommended target:** `Ce scarabée est un sacré rouleur de boules !`
- **MAJOR** `treasureGoldRing1GT1.2` (Unit 365287, `loot`)
  - **Source:** `Narrow Stone Bracelet (Gold)`
  - **Target:** `Bracelet en pierre étroite (Or)`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** In “Bracelet en pierre étroite,” the feminine adjective “étroite” modifies “pierre,” whereas the source says that the bracelet itself is narrow.
  - **Recommended target:** `Bracelet étroit en pierre (Or)`
- **MAJOR** `eggItemReceived` (Unit 364774, `characterdialogue`)
  - **Source:** `An egg! It needs to be hatched.`
  - **Target:** `Un œuf ! Il doit être éclos.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Il doit être éclos” does not correctly express that someone needs to hatch the egg; French requires the causative construction “faire éclore” here.
  - **Recommended target:** `Un œuf ! Il faut le faire éclore.`
- **CRITICAL** `gp_full` (Unit 372411, `google-play`)
  - **Source:** `<b>Need for Greed: Mining Time is an RPG that blends the thrill of mining treasures, crafting valuable items, and selling them for profit.</b>\n\nBrave mysterious lands full of danger and rewards — every dig can bring fortune or trouble. If you enjoy adventure games and mining games, this high-stakes adventure will keep you on edge.\n\n<b>⛏️ Dig, Craft, and Trade!</b>\n Play this RPG as a true Digger: mine deep and craft raw loot into goods, then sell them for gold. Spend profits like in a busi…`
  - **Target:** `[empty]`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The target is empty while the source is a meaningful full Google Play store description.

## 3. Weblate Quality Checks Analysis (Layer 0)

| Check | Count | Classification |
|---|---:|---|
| `end_stop` | 1 | False positives where French/Turkish use a complete unit word rather than the English abbreviation pcs. |
| `game-markup` | 1 | True positive in French amountFormatted: inserted NBSP/narrow-NBSP changes parser syntax; logged Critical. |
| `inconsistent` | 2 | Not scored: each returned row has no duplicate same-source peer in its component inventory; retain as stale or cross-component diagnostic pending a fresh check refresh. |
| `punctuation_spacing` | 5 | False positives caused by emoji shortcodes such as :monocle: and :sunglasses:. |
| `same` | 28 | All but the French Ball-Roller item were benign names, syntactic placeholders, onomatopoeia, or accepted game terms; Ball-Roller is logged as a Major terminology/untranslated issue. |

## 4. Actionable Remediation Plan

1. **Release blockers:** translate `google-play/gp_full` (the empty full store description) for this language. French must also restore the exact engine expressions in `ui/amountFormatted` and `ui/humanTimer` without spaces inside `{...}`.
2. **String corrections:** apply the recommended targets in the defect log for every Major/Minor item whose recommendation is supplied. For full store descriptions, commission a complete locale-native marketing translation rather than copying the source.
3. **Weblate flags:** after an editor validates the classifications, add `ignore-reused` to Italian Units 368597, 368705, 368819, 368893 and Turkish Units 369529, 369825. Do not add `ignore-game-markup` to French Units 359515/359527 or `ignore-reused` to Turkish Unit 366670. Do not bulk-add `ignore-same`; review each term/name first.
4. **Remaining coverage:** every non-UI component was reviewed in full. The only unreviewed strings are in `ui`: units not at a zero-based multiple-of-five API position and without a current check warning. Review those remaining UI units before claiming a component-wide project grade.


---

# Weblate LQA Audit: need-for-greed / all game components (PL)

> Findings are analyst-reviewed with two conservative LLM-assisted passes in this session. They are not verdicts from `weblate/trans/judge.py`.

## 1. MQM-Core Quality Scorecard

| Metric | Value |
|---|---|
| **Coverage mode** | `sample` |
| **Units reviewed** | 528 / 896 (58.9%) |
| **Words reviewed (MQM denominator)** | 3117 / 5050 (61.7%) |

| Metric | Value | Status |
|---|---|---|
| **MQM Score** | **98.14 / 100** | **Fail (Critical Blocker)** |
| **Release Gate Status** | Blocked (Critical defect must be resolved before release) | BLOCKED |
| **Critical Defects (25 pt)** | 1 | See log |
| **Major Defects (5 pt)** | 5 | See log |
| **Minor Defects (1 pt)** | 8 | See log |
| **Total Penalty Points** | 58 pt | Tool-computed using 3117 reviewed source words |

### Component scorecards

| Component | Coverage | Score | Grade | C/M/m |
|---|---:|---:|---|---:|
| UI | sample | 96.94 | Not gradable (partial coverage) | 0/3/3 |
| Tutorial | full | 99.50 | Grade A (Pass) | 0/0/4 |
| Survey | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Orders | full | 98.55 | Grade A (Pass) | 0/2/1 |
| Loot | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Character dialogue | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Buyers | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Google Play | full | 92.42 | Fail (Critical Blocker) | 1/0/0 |

## 2. Reviewed Defect Log (MQM Categories)

- **MINOR** `humanTimer` (Unit 369061, `ui`)
  - **Source:** `{hours:cond:>0?{hours}h. |}{minutes:cond:>0?{minutes}m. |}{seconds:cond:>=0?{seconds}s.|} `
  - **Target:** `{hours:cond:>0?{hours}g. |}{minutes:cond:>0?{minutes}m. |}{seconds:cond:>=0?{seconds}s.|} `
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** “g.” is not the standard Polish abbreviation for “godzina”; “godz.” is the appropriate abbreviation. The conditional syntax and placeholders remain intact.
  - **Recommended target:** `{hours:cond:>0?{hours} godz. |}{minutes:cond:>0?{minutes}m. |}{seconds:cond:>=0?{seconds}s.|}`
- **MINOR** `upgradeTradeBestBuyersRateStat` (Unit 369276, `ui`)
  - **Source:** `You reek of riches`
  - **Target:** `Pachniesz bogactwem`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Pachniesz” conveys a pleasant smell, whereas “reek” has a strongly unpleasant or disparaging connotation. “Cuchniesz” preserves that contrast.
  - **Recommended target:** `Cuchniesz bogactwem`
- **MAJOR** `upgradeCraftTitle` (Unit 369281, `ui`)
  - **Source:** `Craft lvl {value}`
  - **Target:** `Wytwórz poz. {value}`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The source is a label for a crafting level, while “Wytwórz poz. {value}” is an imperative instructing the player to craft a level. This changes the UI stat’s grammatical function and meaning.
  - **Recommended target:** `Poziom wytwarzania {value}`
- **MAJOR** `upgradeCraftChestSlotsStat` (Unit 369286, `ui`)
  - **Source:** `Chest slot`
  - **Target:** `Miejsce na klatkę piersiową`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Klatka piersiowa” denotes the anatomical chest, not a treasure chest. The target therefore misidentifies the gameplay slot.
  - **Recommended target:** `Miejsce na skrzynię`
- **MINOR** `chestSlotDescription` (Unit 369321, `ui`)
  - **Source:** `A chest?\nToss it in — the slot will figure it out.\n\nWhat comes out is known only to it.\nSometimes it’s disturbingly pleased with the result.`
  - **Target:** `Skrzynia?\nWrzuc ją — slot sam to rozgryzie.\n\nCo z niej wyjdzie, wie tylko on.\nCzasami jest niepokojąco zadowolony z rezultatu.`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** The imperative “Wrzuc” is misspelled; the correct Polish form is “Wrzuć.”
  - **Recommended target:** `Skrzynia?\nWrzuć ją — slot sam to rozgryzie.\n\nCo z niej wyjdzie, wie tylko on.\nCzasami jest niepokojąco zadowolony z rezultatu.`
- **MAJOR** `chestUsedIn` (Unit 369376, `ui`)
  - **Source:** `Can be opened in <color=#E3BA59>Chest Slot</color> or the <color=#E3BA59>Super Slot</color>.`
  - **Target:** `Może być otwarty w <color=#E3BA59>Miejscu na klatkę piersiową</color> lub <color=#E3BA59>Super slocie</color>.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Miejsce na klatkę piersiową” refers to a place for the anatomical chest rather than a slot for opening a treasure chest, mistranslating the gameplay location.
  - **Recommended target:** `Może być otwarty w <color=#E3BA59>Miejscu na skrzynię</color> lub <color=#E3BA59>Super slocie</color>.`
- **MINOR** `DialoguerAfterCraftCharacter1` (Unit 367455, `tutorial`)
  - **Source:** `LOOK, HORSE! It shines like my eyes before I met my ex!`
  - **Target:** `PATRZ, KOŃ! Błyszczy jak moje oczy, zanim poznałem moją byłą!`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The common noun used in direct address requires the vocative form “koniu” in standard Polish, not nominative “koń.”
  - **Recommended target:** `PATRZ, KONIU! Błyszczy jak moje oczy, zanim poznałem moją byłą!`
- **MINOR** `DialogueAfterChestCharacter1` (Unit 367499, `tutorial`)
  - **Source:** `DID YOU SEE THAT, HORSE!`
  - **Target:** `WIDZIAŁEŚ TO, KOŃ!`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The direct address should use the vocative “koniu,” not nominative “koń.”
  - **Recommended target:** `WIDZIAŁEŚ TO, KONIU!`
- **MINOR** `DialogueAfterTradeChestCharacter1` (Unit 367506, `tutorial`)
  - **Source:** `YES! I've been recognized. TODAY THIS SWAMP, TOMORROW THE WHOLE WORLD! FORWARD, HORSE!`
  - **Target:** `TAK! Zostałem rozpoznany. DZIŚ TO BAGNO, JUTRO CAŁY ŚWIAT! NAPRZÓD, KOŃ!`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The direct address should use the vocative “koniu,” not nominative “koń.”
  - **Recommended target:** `TAK! Zostałem rozpoznany. DZIŚ TO BAGNO, JUTRO CAŁY ŚWIAT! NAPRZÓD, KONIU!`
- **MINOR** `NewTerrainDispenserCollectingCharacter1` (Unit 367517, `tutorial`)
  - **Source:** `What a ringing rock! As if nature itself is applauding me!`
  - **Target:** `Co za dzwoniąca skała! Jakby sama natura mi oklaskiwała!`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Oklaskiwać” takes a direct object in the accusative. “Mi oklaskiwała” has incorrect case government; “mnie oklaskiwała” is required.
  - **Recommended target:** `Co za dzwoniąca skała! Jakby sama natura mnie oklaskiwała!`
- **MAJOR** `orderTerrain1.2Step2Title` (Unit 366512, `orders`)
  - **Source:** `Ball-Roller`
  - **Target:** `Kula-Rolka`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Kula-Rolka” names an object-like ball/roller compound and does not convey the source’s agentive meaning of a creature that rolls balls.
  - **Recommended target:** `Toczyciel kul`
- **MAJOR** `orderTerrain1.2Step2Thank` (Unit 366514, `orders`)
  - **Source:** `This beetle is a big ball-roller!`
  - **Target:** `Ten chrząszcz to wielka Kula-Rolka!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The target describes the beetle as the object-like compound “Kula-Rolka,” rather than as a creature that rolls balls.
  - **Recommended target:** `Ten chrząszcz to wielki toczyciel kul!`
- **MINOR** `orderTerrain2.2Step6Description` (Unit 366569, `orders`)
  - **Source:** `Collect 8 Cryolis shards from the shells of ice crabs.`
  - **Target:** `Zbierz 8 odłamków Cryolis z muszli lodowych krabów.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** For a crab, “shell” refers to its exoskeletal covering (“pancerz”), while “muszla” denotes a mollusk shell and is inaccurate here.
  - **Recommended target:** `Zbierz 8 odłamków Cryolis z pancerzy lodowych krabów.`
- **CRITICAL** `gp_full` (Unit 376663, `google-play`)
  - **Source:** `<b>Need for Greed: Mining Time is an RPG that blends the thrill of mining treasures, crafting valuable items, and selling them for profit.</b>\n\nBrave mysterious lands full of danger and rewards — every dig can bring fortune or trouble. If you enjoy adventure games and mining games, this high-stakes adventure will keep you on edge.\n\n<b>⛏️ Dig, Craft, and Trade!</b>\n Play this RPG as a true Digger: mine deep and craft raw loot into goods, then sell them for gold. Spend profits like in a busi…`
  - **Target:** `[empty]`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The target is empty while the source is a meaningful full Google Play store description.

## 3. Weblate Quality Checks Analysis (Layer 0)

| Check | Count | Classification |
|---|---:|---|
| `inconsistent` | 2 | Not scored: each returned row has no duplicate same-source peer in its component inventory; retain as stale or cross-component diagnostic pending a fresh check refresh. |
| `same` | 36 | All but the French Ball-Roller item were benign names, syntactic placeholders, onomatopoeia, or accepted game terms; Ball-Roller is logged as a Major terminology/untranslated issue. |

## 4. Actionable Remediation Plan

1. **Release blockers:** translate `google-play/gp_full` (the empty full store description) for this language. French must also restore the exact engine expressions in `ui/amountFormatted` and `ui/humanTimer` without spaces inside `{...}`.
2. **String corrections:** apply the recommended targets in the defect log for every Major/Minor item whose recommendation is supplied. For full store descriptions, commission a complete locale-native marketing translation rather than copying the source.
3. **Weblate flags:** after an editor validates the classifications, add `ignore-reused` to Italian Units 368597, 368705, 368819, 368893 and Turkish Units 369529, 369825. Do not add `ignore-game-markup` to French Units 359515/359527 or `ignore-reused` to Turkish Unit 366670. Do not bulk-add `ignore-same`; review each term/name first.
4. **Remaining coverage:** every non-UI component was reviewed in full. The only unreviewed strings are in `ui`: units not at a zero-based multiple-of-five API position and without a current check warning. Review those remaining UI units before claiming a component-wide project grade.


---

# Weblate LQA Audit: need-for-greed / all game components (IT)

> Findings are analyst-reviewed with two conservative LLM-assisted passes in this session. They are not verdicts from `weblate/trans/judge.py`.

## 1. MQM-Core Quality Scorecard

| Metric | Value |
|---|---|
| **Coverage mode** | `sample` |
| **Units reviewed** | 534 / 896 (59.6%) |
| **Words reviewed (MQM denominator)** | 3126 / 5050 (61.9%) |

| Metric | Value | Status |
|---|---|---|
| **MQM Score** | **97.47 / 100** | **Fail (Critical Blocker)** |
| **Release Gate Status** | Blocked (Critical defect must be resolved before release) | BLOCKED |
| **Critical Defects (25 pt)** | 1 | See log |
| **Major Defects (5 pt)** | 8 | See log |
| **Minor Defects (1 pt)** | 14 | See log |
| **Total Penalty Points** | 79 pt | Tool-computed using 3126 reviewed source words |

### Component scorecards

| Component | Coverage | Score | Grade | C/M/m |
|---|---:|---:|---|---:|
| UI | sample | 98.66 | Not gradable (partial coverage) | 0/1/3 |
| Tutorial | full | 98.38 | Grade A (Pass) | 0/2/3 |
| Survey | full | 96.43 | Grade A (Pass) | 0/1/0 |
| Orders | full | 98.15 | Grade A (Pass) | 0/2/4 |
| Loot | full | 96.41 | Grade A (Pass) | 0/2/4 |
| Character dialogue | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Buyers | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Google Play | full | 92.42 | Fail (Critical Blocker) | 1/0/0 |

## 2. Reviewed Defect Log (MQM Categories)

- **MINOR** `craftSelectChestNotEnoughTitle` (Unit 368715, `ui`)
  - **Source:** `No chests in stock`
  - **Target:** `Nessun Forzieri in magazzino`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Nessun” is singular, but “Forzieri” is plural. The noun must be singular here.
  - **Recommended target:** `Nessun forziere in magazzino`
- **MINOR** `terrain0Description` (Unit 368755, `ui`)
  - **Source:** `Every hero starts somewhere!\nBuried here are your first treasures — and your first troubles. This is where you'll learn to mine, smelt, show off, and survive.`
  - **Target:** `Ogni eroe inizia da qualche parte!\nQui sono sepolti i tuoi primi tesori — e i tuoi primi problemi. Qui imparerai a estrarre, fondere, sfoggiare e sopravvivere.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Sfoggiare” is transitive and lacks an object here, so it does not correctly render the intransitive “show off.”
  - **Recommended target:** `Ogni eroe inizia da qualche parte!\nQui sono sepolti i tuoi primi tesori — e i tuoi primi problemi. Qui imparerai a estrarre, fondere, metterti in mostra e sopravvivere.`
- **MINOR** `craftNoEggs` (Unit 368790, `ui`)
  - **Source:** `No eggs for the incubator`
  - **Target:** `Nessuna Uova per l'Incubatrice`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Singular “Nessuna” cannot modify plural “Uova.”
  - **Recommended target:** `Nessun uovo per l'incubatrice`
- **MAJOR** `upgradeCraftTitle` (Unit 368815, `ui`)
  - **Source:** `Craft lvl {value}`
  - **Target:** `Crea liv. {value}`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The noun “Craft” in the level label is rendered as the imperative “Crea,” changing “Craft level” into “Create level.”
  - **Recommended target:** `Livello creazione {value}`
- **MAJOR** `DialoguePickLootIntroTrader2` (Unit 367350, `tutorial`)
  - **Source:** `Pick the best of what you can carry to the storage.`
  - **Target:** `Piccone il meglio di ciò che puoi portare al Magazzino.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Pick” means “choose” here, but “Piccone” is the noun “pickaxe,” so the target no longer instructs the player to select items.
  - **Recommended target:** `Scegli il meglio di ciò che puoi portare al magazzino.`
- **MAJOR** `DialogueBeforeUpgradeCharacter2` (Unit 367365, `tutorial`)
  - **Source:** `Fine, I'll invest in myself — but only so you stop nagging me!`
  - **Target:** `Va bene, investirò in me stesso — ma solo se smetti di assillarmi!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The source gives stopping the nagging as the purpose of investing, while “solo se” incorrectly makes it a condition that must be met first.
  - **Recommended target:** `Va bene, investirò in me stesso — ma solo per farti smettere di assillarmi!`
- **MINOR** `DialogueAfterUpgradeCharacter2` (Unit 367368, `tutorial`)
  - **Source:** `Well, there was no need to insist! You have your own fetishes — hooves and oats.`
  - **Target:** `Beh, non c'era bisogno di insistere! Hai le tue feticci — zoccoli e avena.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Masculine plural “feticci” requires “i tuoi,” not feminine “le tue.”
  - **Recommended target:** `Beh, non c'era bisogno di insistere! Hai i tuoi feticci — zoccoli e avena.`
- **MINOR** `DialoguePlayerDeathCharacter1` (Unit 367421, `tutorial`)
  - **Source:** `TELL HER I REGRETTED THIS!!!`
  - **Target:** `DILEI CHE ME NE SONO PENTITO!!!`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** “DILEI” is not the correct imperative for “tell her”; the correct form is “DILLE.”
  - **Recommended target:** `DILLE CHE ME NE SONO PENTITO!!!`
- **MINOR** `tip10` (Unit 367434, `tutorial`)
  - **Source:** `Remove curses at the Altar or in the Super Slot.`
  - **Target:** `Rimuovi le maledizioni all'Altare o nello Super slot.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Because the compound begins with “Super,” the correct contraction is “nel Super Slot,” not “nello Super slot.”
  - **Recommended target:** `Rimuovi le maledizioni all'Altare o nel Super Slot.`
- **MAJOR** `survey1Question3Answer1` (Unit 366842, `survey`)
  - **Source:** `Run around and loot`
  - **Target:** `Corri in giro e Bottino`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The verb “loot” is rendered as the noun “Bottino,” leaving the action phrase ungrammatical and removing the looting action.
  - **Recommended target:** `Correre in giro e fare bottino`
- **MAJOR** `orderTerrain1.2Step2Description` (Unit 366411, `orders`)
  - **Source:** `Bring me a Golden Scarab. They are large beetles that can be found crawling in the bushes.`
  - **Target:** `Portami uno Scarabeo d'oro. Sono grandi coleotteri che si possono trovare strisciare tra i cespugli.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Si possono trovare strisciare” is syntactically invalid; the crawling action must be linked to “trovare” with a construction such as “mentre strisciano.”
  - **Recommended target:** `Portami uno Scarabeo d'oro. Sono grandi coleotteri che si possono trovare mentre strisciano tra i cespugli.`
- **MAJOR** `orderTerrain1.2Step2Thank` (Unit 366412, `orders`)
  - **Source:** `This beetle is a big ball-roller!`
  - **Target:** `Questo scarabeo è un grande rullo di sfere!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Rullo di sfere” denotes a roller-like object rather than describing a beetle that rolls balls.
  - **Recommended target:** `Questo scarabeo è un vero campione nel far rotolare palline!`
- **MINOR** `orderTerrain1.2Step3Description` (Unit 366414, `orders`)
  - **Source:** `Bring me a Medium Geode. It sometimes appears in ordinary rocks.`
  - **Target:** `Portami un Geode medio. A volte appare nelle rocce comuni.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Italian “geode” is feminine, so both the article and adjective must be feminine.
  - **Recommended target:** `Portami una Geode media. A volte appare nelle rocce comuni.`
- **MINOR** `orderTerrain2.1Step1Description` (Unit 366432, `orders`)
  - **Source:** `Bring me 2 Frozen Items from the frozen rocks`
  - **Target:** `Portami 2 oggetti Congelato dalle rocce congelate`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Plural masculine “oggetti” is incorrectly modified by singular “Congelato.”
  - **Recommended target:** `Portami 2 oggetti congelati dalle rocce congelate`
- **MINOR** `orderTerrain2.3Step1Title` (Unit 366469, `orders`)
  - **Source:** `Going Big`
  - **Target:** `Andare in grande`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Andare in grande” is a non-idiomatic calque of “Going Big”; the idiomatic expression is “fare le cose in grande.”
  - **Recommended target:** `Fare le cose in grande`
- **MINOR** `orderTerrain2.3Step5Description` (Unit 366479, `orders`)
  - **Source:** `Bring me an Opal. It's a rare find in the frozen rocks.`
  - **Target:** `Portami un Opale. È un ritrovamento Raro nelle rocce Congelato.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Plural feminine “rocce” is incorrectly modified by singular masculine “Congelato.”
  - **Recommended target:** `Portami un Opale. È un ritrovamento raro nelle rocce congelate.`
- **MINOR** `geodeMedium` (Unit 365559, `loot`)
  - **Source:** `Geode (Medium)`
  - **Target:** `Geode (medio)`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Italian “geode” is feminine, so the size adjective must be “media.”
  - **Recommended target:** `Geode (media)`
- **MINOR** `geodeSmall` (Unit 365579, `loot`)
  - **Source:** `Geode (Small)`
  - **Target:** `Geode (piccolo)`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Italian “geode” is feminine, so the size adjective must be “piccola.”
  - **Recommended target:** `Geode (piccola)`
- **MINOR** `geodeGemSmall` (Unit 365580, `loot`)
  - **Source:** `Valuable Geode (Small)`
  - **Target:** `Geode prezioso (piccolo)`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Italian “geode” is feminine, but both adjectives are masculine in the target.
  - **Recommended target:** `Geode preziosa (piccola)`
- **MINOR** `geodeGemMedium` (Unit 365581, `loot`)
  - **Source:** `Valuable Geode (Medium)`
  - **Target:** `Geode prezioso (medio)`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** Italian “geode” is feminine, but both adjectives are masculine in the target.
  - **Recommended target:** `Geode preziosa (media)`
- **MAJOR** `treasure2GIdolCheapT1.2` (Unit 365616, `loot`)
  - **Source:** `Idol of the Singed Noggin`
  - **Target:** `Idolo della Nuca Bruciacchiata`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Noggin” means the head, whereas “nuca” specifically means the nape or back of the neck, changing the named body part.
  - **Recommended target:** `Idolo della Testa Bruciacchiata`
- **MAJOR** `Frid.Cheap` (Unit 365625, `loot`)
  - **Source:** `Monday 12th Mask`
  - **Target:** `Maschera del 12° Lunedì`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Monday 12th” refers to Monday the 12th, while “12° Lunedì” means the twelfth Monday.
  - **Recommended target:** `Maschera di lunedì 12`
- **CRITICAL** `gp_full` (Unit 372417, `google-play`)
  - **Source:** `<b>Need for Greed: Mining Time is an RPG that blends the thrill of mining treasures, crafting valuable items, and selling them for profit.</b>\n\nBrave mysterious lands full of danger and rewards — every dig can bring fortune or trouble. If you enjoy adventure games and mining games, this high-stakes adventure will keep you on edge.\n\n<b>⛏️ Dig, Craft, and Trade!</b>\n Play this RPG as a true Digger: mine deep and craft raw loot into goods, then sell them for gold. Spend profits like in a busi…`
  - **Target:** `[empty]`
  - **Category:** `accuracy/omission`
  - **Explanation:** The target is completely blank while the source contains a full, meaningful store description with feature copy and formatting.

## 3. Weblate Quality Checks Analysis (Layer 0)

| Check | Count | Classification |
|---|---:|---|
| `inconsistent` | 2 | Not scored: each returned row has no duplicate same-source peer in its component inventory; retain as stale or cross-component diagnostic pending a fresh check refresh. |
| `reused` | 4 | Italian pairs are grammatical convergence/case variants; Turkish Trade/Trading is convergence, but Crab Warmcoat -> Yengeç Sıcaklığı collides with Crab Warmth and is logged Major. |
| `same` | 38 | All but the French Ball-Roller item were benign names, syntactic placeholders, onomatopoeia, or accepted game terms; Ball-Roller is logged as a Major terminology/untranslated issue. |

## 4. Actionable Remediation Plan

1. **Release blockers:** translate `google-play/gp_full` (the empty full store description) for this language. French must also restore the exact engine expressions in `ui/amountFormatted` and `ui/humanTimer` without spaces inside `{...}`.
2. **String corrections:** apply the recommended targets in the defect log for every Major/Minor item whose recommendation is supplied. For full store descriptions, commission a complete locale-native marketing translation rather than copying the source.
3. **Weblate flags:** after an editor validates the classifications, add `ignore-reused` to Italian Units 368597, 368705, 368819, 368893 and Turkish Units 369529, 369825. Do not add `ignore-game-markup` to French Units 359515/359527 or `ignore-reused` to Turkish Unit 366670. Do not bulk-add `ignore-same`; review each term/name first.
4. **Remaining coverage:** every non-UI component was reviewed in full. The only unreviewed strings are in `ui`: units not at a zero-based multiple-of-five API position and without a current check warning. Review those remaining UI units before claiming a component-wide project grade.


---

# Weblate LQA Audit: need-for-greed / all game components (TR)

> Findings are analyst-reviewed with two conservative LLM-assisted passes in this session. They are not verdicts from `weblate/trans/judge.py`.

## 1. MQM-Core Quality Scorecard

| Metric | Value |
|---|---|
| **Coverage mode** | `sample` |
| **Units reviewed** | 533 / 896 (59.5%) |
| **Words reviewed (MQM denominator)** | 3129 / 5050 (62.0%) |

| Metric | Value | Status |
|---|---|---|
| **MQM Score** | **96.32 / 100** | **Fail (Critical Blocker)** |
| **Release Gate Status** | Blocked (Critical defect must be resolved before release) | BLOCKED |
| **Critical Defects (25 pt)** | 1 | See log |
| **Major Defects (5 pt)** | 16 | See log |
| **Minor Defects (1 pt)** | 10 | See log |
| **Total Penalty Points** | 115 pt | Tool-computed using 3129 reviewed source words |

### Component scorecards

| Component | Coverage | Score | Grade | C/M/m |
|---|---:|---:|---|---:|
| UI | sample | 95.17 | Not gradable (partial coverage) | 0/5/4 |
| Tutorial | full | 97.75 | Grade A (Pass) | 0/3/3 |
| Survey | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Orders | full | 98.02 | Grade A (Pass) | 0/3/0 |
| Loot | full | 92.82 | Grade B (Conditional) | 0/5/3 |
| Character dialogue | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Buyers | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Google Play | full | 92.42 | Fail (Critical Blocker) | 1/0/0 |

## 2. Reviewed Defect Log (MQM Categories)

- **MAJOR** `humanTimer` (Unit 369527, `ui`)
  - **Source:** `{hours:cond:>0?{hours}h. |}{minutes:cond:>0?{minutes}m. |}{seconds:cond:>=0?{seconds}s.|} `
  - **Target:** `{hours:cond:>0?{hours}s. |}{minutes:cond:>0?{minutes}d. |}{seconds:cond:>=0?{seconds}dk.|} `
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The target mislabels the time units: “d.” is not the standard abbreviation for minutes, and “dk.” denotes minutes rather than seconds. This can make displayed durations materially incorrect.
  - **Recommended target:** `{hours:cond:>0?{hours} sa. |}{minutes:cond:>0?{minutes} dk. |}{seconds:cond:>=0?{seconds} sn.|} `
- **MINOR** `weightInformation` (Unit 369587, `ui`)
  - **Source:** `The capacity of the horse’s bag is an important indicator.\nThe weight of found items affects their transportation.\nUpgrade your bag to increase its capacity and carry more items.`
  - **Target:** `At çantasının Kapasite'si önemli bir göstergedir.\nBulunan eşyaların ağırlığı taşımalarını etkiler.\nKapasite'sini artırmak ve daha fazla eşya taşımak için çantanızı yükseltin.`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** “Kapasite” is a common noun, so it should not be capitalized or separated from its suffix with an apostrophe. The error occurs twice.
  - **Recommended target:** `At çantasının kapasitesi önemli bir göstergedir.\nBulunan eşyaların ağırlığı taşımalarını etkiler.\nKapasitesini artırmak ve daha fazla eşya taşımak için çantanızı yükseltin.`
- **MAJOR** `craftItem` (Unit 369657, `ui`)
  - **Source:** `Craft item`
  - **Target:** `Öğe Zanaat`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Öğe Zanaat” is not a grammatical Turkish command or noun phrase and does not convey the requested crafting action.
  - **Recommended target:** `Eşya üret`
- **MAJOR** `craftClosedRecipeDescription` (Unit 369662, `ui`)
  - **Source:** `Insufficient Forge Level\n\nWith each upgrade, you'll be able to craft increasingly rare and valuable treasures. It's time to turn this furnace into a forge of legends!`
  - **Target:** `Yetersiz Dövme Seviyesi\n\nHer yükseltmeyle, giderek daha nadir ve değerli hazineler üretebileceksiniz. Bu fırını bir efsaneler dövmesine dönüştürme zamanı!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The forge is mistranslated as “Dövme.” In the final sentence, “efsaneler dövmesine” reads as a tattoo rather than a forge, substantially corrupting the meaning.
  - **Recommended target:** `Yetersiz Demirhane Seviyesi\n\nHer yükseltmeyle giderek daha nadir ve değerli hazineler üretebileceksiniz. Bu fırını efsanelere yaraşır bir demirhaneye dönüştürme zamanı!`
- **MAJOR** `craftStockFullTitle` (Unit 369672, `ui`)
  - **Source:** `No space for treasure in the storage!`
  - **Target:** `Depolama'da Mücevherat için yer yok!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Mücevherat” means jewelry and incorrectly narrows the source term “treasure.” “Depolama'da” also incorrectly attaches a suffix to a common noun with an apostrophe.
  - **Recommended target:** `Depoda hazine için yer yok!`
- **MINOR** `storageDescription` (Unit 369772, `ui`)
  - **Source:** `All discovered items are stored here.\nSpace is limited — the warehouse isn’t made of rubber.\nUpgrade your storage so your greed doesn’t hit the walls.`
  - **Target:** `Keşfedilen tüm öğeler burada saklanır.\nAlan sınırlıdır — depo kauçuktan yapılmamıştır.\nAçgözlülüğünüz duvarlara çarpmaması için depolama alanınızı yükseltin.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The subordinate construction ending in “çarpmaması” requires the preceding possessive subject to be in the genitive: “Açgözlülüğünüzün,” not “Açgözlülüğünüz.”
  - **Recommended target:** `Keşfedilen tüm öğeler burada saklanır.\nAlan sınırlıdır — depo kauçuktan yapılmamıştır.\nAçgözlülüğünüzün duvarlara çarpmaması için depolama alanınızı yükseltin.`
- **MINOR** `polishingSlotDescription` (Unit 369792, `ui`)
  - **Source:** `Pick any geode, rough stone, or even a ruined ancient treasure —\nthe workbench will polish it, repair it, and bring out its shine.\n\nWhat comes out can be socketed, crafted, or sold for a tidy profit.`
  - **Target:** `Herhangi bir Jeot, Ham taş veya hatta harap olmuş eski bir hazine seçin —\ntezgah onu parlatacak, onaracak ve parlaklığını ortaya çıkaracak.\n\nÇıkan şey soketlenebilir, üretilebilir veya iyi bir karla satılabilir.`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** The standard spelling for profit is “kâr”; “kar” denotes snow.
  - **Recommended target:** `Herhangi bir Jeot, Ham taş veya hatta harap olmuş eski bir hazine seçin —\ntezgah onu parlatacak, onaracak ve parlaklığını ortaya çıkaracak.\n\nÇıkan şey soketlenebilir, üretilebilir veya iyi bir kârla satılabilir.`
- **MINOR** `retargetingDescription2` (Unit 369857, `ui`)
  - **Source:** `Your progress is still here.`
  - **Target:** `İlerlemeniz hala burada.`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** For the adverb meaning “still,” the standard spelling is “hâlâ”; “hala” means paternal aunt.
  - **Recommended target:** `İlerlemeniz hâlâ burada.`
- **MAJOR** `dealOfTheDayDescription` (Unit 369894, `ui`)
  - **Source:** `New offers will be available in`
  - **Target:** `Yeni teklifler şu tarihte mevcut olacak:`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The source introduces a remaining duration, whereas “şu tarihte” introduces a calendar date. This misrepresents a countdown as a date.
  - **Recommended target:** `Yeni teklifler şu süre sonra sunulacak:`
- **MINOR** `DialogueTimeIsUpHorse1` (Unit 367549, `tutorial`)
  - **Source:** `Neigh!`
  - **Target:** `Kişnemek!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Kişnemek!” is the infinitive “to neigh,” not a rendering of the horse's vocalized neigh.
  - **Recommended target:** `İhihiii!`
- **MAJOR** `DialoguePickLootIntroTrader2` (Unit 367554, `tutorial`)
  - **Source:** `Pick the best of what you can carry to the storage.`
  - **Target:** `Depolama'ya taşıyabileceğin en iyi şeyleri Kazma.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Kazma” here reads as “don't dig” or as the noun “pickaxe,” rather than the instruction to select the best items. The core tutorial action is mistranslated.
  - **Recommended target:** `Depoya taşıyabileceğin en iyi eşyaları seç.`
- **MINOR** `exitDoorDescription2` (Unit 367583, `tutorial`)
  - **Source:** `Green? Is she… feeling sick?`
  - **Target:** `Yeşil mi? O… hasta mı hissediyor?`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Hasta mı hissediyor?” is an ungrammatical calque in this sense; Turkish requires a reflexive construction such as “kendini kötü hissetmek.”
  - **Recommended target:** `Yeşil mi? O… kendini kötü mü hissediyor?`
- **MINOR** `DialogueOrdersThanksHorse1` (Unit 367596, `tutorial`)
  - **Source:** `Neigh!`
  - **Target:** `Kişniyor!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Kişniyor!” means “It is neighing,” turning the horse's vocalized neigh into a narrative statement.
  - **Recommended target:** `İhihiii!`
- **MAJOR** `DialogueClaimedChestCharacter1` (Unit 367606, `tutorial`)
  - **Source:** `Yes, the chest is here but there's no key — but you forgot about the magic forge, mare! It can do anything with anything.`
  - **Target:** `Evet, sandık burada ama anahtar yok — ama sihirli dövmeyi unuttun, kısrak! Her şeyi her şeyle yapabilir.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Magic forge” is mistranslated as “sihirli dövme,” which denotes magical forging or a magical tattoo rather than the forge workstation.
  - **Recommended target:** `Evet, sandık burada ama anahtar yok — ama sihirli demirhaneyi unuttun, kısrak! Her şeyi her şeyle yapabilir.`
- **MAJOR** `tip9` (Unit 367637, `tutorial`)
  - **Source:** `Upgrade the Forge to unlock new jewelry recipes.`
  - **Target:** `Yeni Mücevherat tariflerinin kilidini açmak için Dövme'yi yükseltin.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The upgradable Forge building is rendered as “Dövme,” an activity or tattoo rather than a forge. This misidentifies the building the player must upgrade.
  - **Recommended target:** `Yeni mücevher tariflerinin kilidini açmak için demirhaneyi yükseltin.`
- **MAJOR** `orderTerrain1.2Step2Description` (Unit 366615, `orders`)
  - **Source:** `Bring me a Golden Scarab. They are large beetles that can be found crawling in the bushes.`
  - **Target:** `Bana bir Altın Böcek getir. Bunlar çalılıklarda sürünen büyük böceklerdir.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Scarab” is reduced to the generic “Böcek,” removing the specific creature and named-item identity required by the order.
  - **Recommended target:** `Bana bir Altın Skarabe getir. Bunlar çalılıklarda sürünürken bulunabilen büyük böceklerdir.`
- **MAJOR** `orderTerrain2.1Step4Description` (Unit 366657, `orders`)
  - **Source:** `Thaw a Medium Diamond. Frozen diamonds are often found in all rocks.`
  - **Target:** `Orta Boy bir Elması eritin. Donmuş elmaslar genellikle tüm kayalarda bulunur.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Eritin” instructs the player to melt the diamond itself, while the source requires thawing a frozen diamond. This materially changes the objective action.
  - **Recommended target:** `Orta Boy Donmuş Elmasın buzunu çözün. Donmuş elmaslar genellikle tüm kayalarda bulunur.`
- **MAJOR** `orderTerrain2.2Step6Title` (Unit 366670, `orders`)
  - **Source:** `Crab Warmcoat`
  - **Target:** `Yengeç Sıcaklığı`
  - **Category:** `accuracy/omission`
  - **Explanation:** “Yengeç Sıcaklığı” means “Crab Warmth” and omits the “coat” element of the named item “Crab Warmcoat.”
  - **Recommended target:** `Yengeç Sıcak Paltosu`
- **MAJOR** `artifactBirdEntity` (Unit 365878, `loot`)
  - **Source:** `Yolk of Fortune`
  - **Target:** `Servet Yumurtası`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Yolk” means “yumurta sarısı,” but the target names a whole egg, changing the identity of the object.
  - **Recommended target:** `Talihin Yumurta Sarısı`
- **MAJOR** `artifactBook` (Unit 365881, `loot`)
  - **Source:** `Ancient Tome`
  - **Target:** `Antik Tomar`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Tome” denotes a substantial book or volume, whereas “Tomar” denotes a roll or scroll, changing the named object.
  - **Recommended target:** `Kadim Kitap`
- **MAJOR** `goldSmallScarab` (Unit 365890, `loot`)
  - **Source:** `Golden Scarab`
  - **Target:** `Altın Böcek`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Scarab” is translated as the generic “Böcek,” removing the specific creature and item identity.
  - **Recommended target:** `Altın Skarabe`
- **MINOR** `Tablet.Cheap` (Unit 365927, `loot`)
  - **Source:** `Mystery-Crap Stone`
  - **Target:** `Gizemli-Çöp Taş`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Gizemli-Çöp Taş” is not a well-formed Turkish noun compound; the head noun requires the compound suffix in “Çöp Taşı.”
  - **Recommended target:** `Gizemli Çöp Taşı`
- **MINOR** `Jur.Cheap` (Unit 365935, `loot`)
  - **Source:** `Colophony Mosquito`
  - **Target:** `Kolofon Sivrisinek`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The Turkish noun compound is missing the required suffix on its head noun: “Kolofon Sivrisineği.”
  - **Recommended target:** `Kolofon Sivrisineği`
- **MAJOR** `gem.diamond.big.bad` (Unit 365953, `loot`)
  - **Source:** `Chipped Big Diamond`
  - **Target:** `Yontulmuş Büyük Elmas`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** For a gem, “chipped” indicates damage from a piece breaking off. “Yontulmuş” instead describes intentional carving and fails to convey the defective condition.
  - **Recommended target:** `Kenarından Parça Kopmuş Büyük Elmas`
- **MAJOR** `frozen.artifact.book.t4.1` (Unit 365963, `loot`)
  - **Source:** `Frozen Ancient Tome`
  - **Target:** `Donmuş Antik Tomar`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Tome” denotes a book or volume, whereas “Tomar” denotes a roll or scroll, changing the frozen item's identity.
  - **Recommended target:** `Donmuş Kadim Kitap`
- **MINOR** `treasureCryolisEarring2GT1.2` (Unit 365974, `loot`)
  - **Source:** `Moonlit Earrings`
  - **Target:** `Ay Işığı Küpeler`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Ay Işığı Küpeler” is an ill-formed Turkish construction for “Moonlit Earrings.” The adjectival form “Ay Işıklı” is required here.
  - **Recommended target:** `Ay Işıklı Küpeler`
- **CRITICAL** `gp_full` (Unit 372429, `google-play`)
  - **Source:** `<b>Need for Greed: Mining Time is an RPG that blends the thrill of mining treasures, crafting valuable items, and selling them for profit.</b>\n\nBrave mysterious lands full of danger and rewards — every dig can bring fortune or trouble. If you enjoy adventure games and mining games, this high-stakes adventure will keep you on edge.\n\n<b>⛏️ Dig, Craft, and Trade!</b>\n Play this RPG as a true Digger: mine deep and craft raw loot into goods, then sell them for gold. Spend profits like in a busi…`
  - **Target:** `[empty]`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The target is empty while the source is a meaningful full Google Play store description.

## 3. Weblate Quality Checks Analysis (Layer 0)

| Check | Count | Classification |
|---|---:|---|
| `end_colon` | 2 | UI punctuation warning is benign; the Turkish dealOfTheDayDescription has a separate Major accuracy finding. |
| `end_stop` | 2 | False positives where French/Turkish use a complete unit word rather than the English abbreviation pcs. |
| `inconsistent` | 2 | Not scored: each returned row has no duplicate same-source peer in its component inventory; retain as stale or cross-component diagnostic pending a fresh check refresh. |
| `reused` | 4 | Italian pairs are grammatical convergence/case variants; Turkish Trade/Trading is convergence, but Crab Warmcoat -> Yengeç Sıcaklığı collides with Crab Warmth and is logged Major. |
| `same` | 32 | All but the French Ball-Roller item were benign names, syntactic placeholders, onomatopoeia, or accepted game terms; Ball-Roller is logged as a Major terminology/untranslated issue. |
- Heuristic candidates `AT` in Units 367601 and 367608 are false positives: `at` is Turkish for “horse”, not an English acronym leak.

## 4. Actionable Remediation Plan

1. **Release blockers:** translate `google-play/gp_full` (the empty full store description) for this language. French must also restore the exact engine expressions in `ui/amountFormatted` and `ui/humanTimer` without spaces inside `{...}`.
2. **String corrections:** apply the recommended targets in the defect log for every Major/Minor item whose recommendation is supplied. For full store descriptions, commission a complete locale-native marketing translation rather than copying the source.
3. **Weblate flags:** after an editor validates the classifications, add `ignore-reused` to Italian Units 368597, 368705, 368819, 368893 and Turkish Units 369529, 369825. Do not add `ignore-game-markup` to French Units 359515/359527 or `ignore-reused` to Turkish Unit 366670. Do not bulk-add `ignore-same`; review each term/name first.
4. **Remaining coverage:** every non-UI component was reviewed in full. The only unreviewed strings are in `ui`: units not at a zero-based multiple-of-five API position and without a current check warning. Review those remaining UI units before claiming a component-wide project grade.


---

# Weblate LQA Audit: need-for-greed / all game components (ID)

> Findings are analyst-reviewed with two conservative LLM-assisted passes in this session. They are not verdicts from `weblate/trans/judge.py`.

## 1. MQM-Core Quality Scorecard

| Metric | Value |
|---|---|
| **Coverage mode** | `sample` |
| **Units reviewed** | 532 / 896 (59.4%) |
| **Words reviewed (MQM denominator)** | 3121 / 5050 (61.8%) |

| Metric | Value | Status |
|---|---|---|
| **MQM Score** | **96.16 / 100** | **Fail (Critical Blocker)** |
| **Release Gate Status** | Blocked (Critical defect must be resolved before release) | BLOCKED |
| **Critical Defects (25 pt)** | 1 | See log |
| **Major Defects (5 pt)** | 18 | See log |
| **Minor Defects (1 pt)** | 5 | See log |
| **Total Penalty Points** | 120 pt | Tool-computed using 3121 reviewed source words |

### Component scorecards

| Component | Coverage | Score | Grade | C/M/m |
|---|---:|---:|---|---:|
| UI | sample | 98.14 | Not gradable (partial coverage) | 0/2/1 |
| Tutorial | full | 96.75 | Grade A (Pass) | 0/5/1 |
| Survey | full | 96.43 | Grade A (Pass) | 0/1/0 |
| Orders | full | 97.63 | Grade A (Pass) | 0/3/3 |
| Loot | full | 93.59 | Grade B (Conditional) | 0/5/0 |
| Character dialogue | full | 94.51 | Grade B (Conditional) | 0/1/0 |
| Buyers | full | 100.00 | Grade A (Pass) | 0/0/0 |
| Google Play | full | 90.91 | Fail (Critical Blocker) | 1/1/0 |

## 2. Reviewed Defect Log (MQM Categories)

- **MAJOR** `craftItem` (Unit 368259, `ui`)
  - **Source:** `Craft item`
  - **Target:** `Kerajinan item`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Kerajinan” is a noun meaning craft/handicraft, so it does not render the action label “Craft item.”
  - **Recommended target:** `Buat item`
- **MAJOR** `craftClosedRecipeDescription` (Unit 368264, `ui`)
  - **Source:** `Insufficient Forge Level\n\nWith each upgrade, you'll be able to craft increasingly rare and valuable treasures. It's time to turn this furnace into a forge of legends!`
  - **Target:** `Level Tempa Tidak Cukup\n\nDengan setiap peningkatan, Anda akan dapat membuat harta karun yang semakin langka dan berharga. Saatnya mengubah tungku ini menjadi tempa legenda!`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Tempa” denotes the act of forging rather than the forge facility, making “Level Tempa” and “tempa legenda” materially incorrect.
  - **Recommended target:** `Level Bengkel Tempa Tidak Memadai\n\nDengan setiap peningkatan, Anda akan dapat membuat harta karun yang semakin langka dan berharga. Saatnya mengubah tungku ini menjadi bengkel tempa legendaris!`
- **MINOR** `craftNoInlay` (Unit 368399, `ui`)
  - **Source:** `No items to insert`
  - **Target:** `Tidak ada item untuk Sisipkan`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “untuk Sisipkan” is not a grammatical rendering of items available to be inserted; the passive form is needed.
  - **Recommended target:** `Tidak ada item untuk disisipkan`
- **MAJOR** `DialogueAfterUpgradeCharacter2` (Unit 367266, `tutorial`)
  - **Source:** `Well, there was no need to insist! You have your own fetishes — hooves and oats.`
  - **Target:** `Yah, tidak perlu memaksa! Kamu punya fetis sendiri — kuku dan gandum.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Gandum” means wheat, not oats, changing the specific food named; “kuku kuda” also conveys the intended hooves more precisely than generic “kuku.”
  - **Recommended target:** `Yah, tidak perlu memaksa! Kamu punya fetis sendiri — kuku kuda dan oat.`
- **MAJOR** `DialogueBeforeBirdCharacter2` (Unit 367293, `tutorial`)
  - **Source:** `A cage on my back? It's the Treasure-Hunting Canary! Now let's see where the dog is buried.`
  - **Target:** `Kandang di punggungku? Ini adalah Kenari Pemburu Harta Karun! Sekarang mari kita lihat di mana anjing itu dikubur.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The idiomatic reference to discovering the hidden crux or secret is translated literally as locating a buried dog, producing a different meaning.
  - **Recommended target:** `Kandang di punggungku? Ini Kenari Pemburu Harta Karun! Sekarang mari kita cari tahu di mana letak rahasianya.`
- **MAJOR** `DialogueClaimedChestCharacter1` (Unit 367300, `tutorial`)
  - **Source:** `Yes, the chest is here but there's no key — but you forgot about the magic forge, mare! It can do anything with anything.`
  - **Target:** `Ya, peti ada di sini tapi tidak ada kunci — tapi kau lupa tentang tempa ajaib, kuda betina! Itu bisa melakukan apa saja dengan apa saja.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** The facility “magic forge” is rendered with “tempa,” which denotes forging as an action rather than the named facility.
  - **Recommended target:** `Ya, petinya ada di sini, tetapi tidak ada kunci — tapi kau lupa tentang bengkel tempa ajaib, kuda betina! Tempat itu bisa membuat apa saja dari apa pun.`
- **MAJOR** `DialogueNewTerrainStartedCharacter1` (Unit 367310, `tutorial`)
  - **Source:** `Different terrain. Soggy ground. Ugh, the air reeks of meaning!`
  - **Target:** `Medan berbeda. Tanah becek. Ugh, udara berbau busuk!`
  - **Category:** `accuracy/omission`
  - **Explanation:** The target says only that the air stinks, omitting “of meaning” and thereby losing the line’s central wordplay.
  - **Recommended target:** `Medan berbeda. Tanah becek. Ugh, udara ini sarat makna sampai menyengat!`
- **MINOR** `DialoguePlayerRevivedCharacter1` (Unit 367322, `tutorial`)
  - **Source:** `That was close, but now I'm ready for anything! I'll try to swing the pickaxe a bit quieter…`
  - **Target:** `Itu nyaris, tapi sekarang aku siap untuk apa pun! Aku akan mencoba mengayunkan beliung sedikit lebih pelan…`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Itu nyaris” is an incomplete and unnatural expression for “That was close.”
  - **Recommended target:** `Nyaris saja, tapi sekarang aku siap menghadapi apa pun! Aku akan mencoba mengayunkan beliung sedikit lebih pelan…`
- **MAJOR** `tip9` (Unit 367331, `tutorial`)
  - **Source:** `Upgrade the Forge to unlock new jewelry recipes.`
  - **Target:** `Tingkatkan Tempa untuk membuka resep Perhiasan baru.`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Tempa” names the forging action rather than the Forge facility that the player must upgrade.
  - **Recommended target:** `Tingkatkan Bengkel Tempa untuk membuka resep perhiasan baru.`
- **MAJOR** `survey1Question3Answer2` (Unit 366809, `survey`)
  - **Source:** `Craft and sell`
  - **Target:** `Kerajinan dan jual`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Kerajinan” is a noun and “jual” is an uninflected verb, so the target does not preserve the two coordinated actions “Craft and sell.”
  - **Recommended target:** `Membuat dan menjual barang`
- **MINOR** `orderTerrain0.1Step1Thank` (Unit 366285, `orders`)
  - **Source:** `Excellent — these nuggets will add nicely to my collection.`
  - **Target:** `Bagus sekali — bongkahan ini akan menambah koleksiku dengan indah.`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “akan menambah koleksiku dengan indah” is malformed and incorrectly attaches “dengan indah” to the act of adding rather than describing the nuggets as a welcome addition.
  - **Recommended target:** `Bagus sekali — bongkahan ini akan menjadi tambahan yang bagus untuk koleksiku.`
- **MAJOR** `orderTerrain1.1Step4Title` (Unit 366297, `orders`)
  - **Source:** `Dead Man’s Chest`
  - **Target:** `Peti Mati`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Peti mati” means a coffin, not a dead man’s chest.
  - **Recommended target:** `Peti Orang Mati`
- **MINOR** `orderTerrain1.1Step5Thank` (Unit 366303, `orders`)
  - **Source:** `This silver pleasantly stings the palms...`
  - **Target:** `Perak ini dengan menyenangkan menyengat telapak tangan...`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The placement and construction of “dengan menyenangkan menyengat” are unnatural in Indonesian.
  - **Recommended target:** `Perak ini terasa menyengat dengan nikmat di telapak tangan…`
- **MINOR** `orderTerrain1.2Step5Description` (Unit 366318, `orders`)
  - **Source:** `Bring me a Lion Ruby. It can be found in geodes with red crystals.`
  - **Target:** `Bawakan aku Ruby Singa. Itu bisa ditemukan di geode dengan kristal merah.`
  - **Category:** `fluency/spelling_orthography`
  - **Explanation:** The English spelling “Ruby” is retained instead of the Indonesian form “Rubi.”
  - **Recommended target:** `Bawakan aku Rubi Singa. Itu bisa ditemukan di geode dengan kristal merah.`
- **MAJOR** `orderTerrain2.1Step2Description` (Unit 366343, `orders`)
  - **Source:** `Find 2 Silver Nuggets inside ice cubes and warm them.`
  - **Target:** `Temukan 2 Silver Nugget di dalam es batu dan hangatkan.`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The required item name “Silver Nugget” remains in English within the Indonesian instruction.
  - **Recommended target:** `Temukan 2 Nugget Perak di dalam es batu, lalu hangatkan.`
- **MAJOR** `orderTerrain2.1Step3Description` (Unit 366348, `orders`)
  - **Source:** `Find 2 Gold Nuggets inside ice cubes and warm them.`
  - **Target:** `Temukan 2 Gold Nugget di dalam es batu dan hangatkan.`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The required item name “Gold Nugget” remains in English within the Indonesian instruction.
  - **Recommended target:** `Temukan 2 Nugget Emas di dalam es batu, lalu hangatkan.`
- **MAJOR** `roughMaskT1` (Unit 365431, `loot`)
  - **Source:** `Broken Visage Mask`
  - **Target:** `Topeng Visage Rusak`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The meaningful common noun “Visage” remains untranslated in the Indonesian item name.
  - **Recommended target:** `Topeng Wajah Rusak`
- **MAJOR** `treasureGoldMask2GT1.1` (Unit 365448, `loot`)
  - **Source:** `Golden Visage Mask`
  - **Target:** `Topeng Visage Emas`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The meaningful common noun “Visage” remains untranslated in the Indonesian item name.
  - **Recommended target:** `Topeng Wajah Emas`
- **MAJOR** `treasure2GIdolCheapT1.2` (Unit 365462, `loot`)
  - **Source:** `Idol of the Singed Noggin`
  - **Target:** `Berhala Noggin yang Hangus`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The English slang noun “Noggin,” meaning head, remains untranslated.
  - **Recommended target:** `Berhala Kepala Hangus`
- **MAJOR** `Frid` (Unit 365470, `loot`)
  - **Source:** `Friday Night Mask`
  - **Target:** `Topeng Malam Jumat`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Malam Jumat” conventionally denotes the night before Friday, whereas “Friday Night” denotes Friday evening/night.
  - **Recommended target:** `Topeng Jumat Malam`
- **MAJOR** `Frid.Cheap` (Unit 365471, `loot`)
  - **Source:** `Monday 12th Mask`
  - **Target:** `Masker Senin ke-12`
  - **Category:** `accuracy/mistranslation`
  - **Explanation:** “Senin ke-12” means the twelfth Monday, not a Monday that falls on the 12th day of a month.
  - **Recommended target:** `Topeng Senin Tanggal 12`
- **MAJOR** `frostible` (Unit 364819, `characterdialogue`)
  - **Source:** `N-n-n-need to \nw-w-warm up!`
  - **Target:** `P-p-perlu \nm-m-memanaskan!`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** “Memanaskan” is transitive and lacks an object here; warming oneself requires “menghangatkan diri.”
  - **Recommended target:** `P-p-perlu \nm-m-menghangatkan diri!`
- **MAJOR** `gp_short` (Unit 372413, `google-play`)
  - **Source:** `Join an adventure as a digger! Dig, craft, run from monsters! RPG mining game!`
  - **Target:** `Bergabunglah dalam petualangan sebagai penggali! Gali, kerajinan, lari dari monster! Game penambangan RPG!`
  - **Category:** `fluency/grammar_syntax`
  - **Explanation:** The noun “kerajinan” does not render the imperative action “craft,” breaking the coordinated sequence of commands.
  - **Recommended target:** `Bergabunglah dalam petualangan sebagai penggali! Gali, buat barang, dan lari dari monster! Game penambangan RPG!`
- **CRITICAL** `gp_full` (Unit 372414, `google-play`)
  - **Source:** `<b>Need for Greed: Mining Time is an RPG that blends the thrill of mining treasures, crafting valuable items, and selling them for profit.</b>\n\nBrave mysterious lands full of danger and rewards — every dig can bring fortune or trouble. If you enjoy adventure games and mining games, this high-stakes adventure will keep you on edge.\n\n<b>⛏️ Dig, Craft, and Trade!</b>\n Play this RPG as a true Digger: mine deep and craft raw loot into goods, then sell them for gold. Spend profits like in a busi…`
  - **Target:** `[empty]`
  - **Category:** `accuracy/untranslated`
  - **Explanation:** The target is empty while the source is a meaningful full Google Play store description.

## 3. Weblate Quality Checks Analysis (Layer 0)

| Check | Count | Classification |
|---|---:|---|
| `inconsistent` | 2 | Not scored: each returned row has no duplicate same-source peer in its component inventory; retain as stale or cross-component diagnostic pending a fresh check refresh. |
| `same` | 46 | All but the French Ball-Roller item were benign names, syntactic placeholders, onomatopoeia, or accepted game terms; Ball-Roller is logged as a Major terminology/untranslated issue. |

## 4. Actionable Remediation Plan

1. **Release blockers:** translate `google-play/gp_full` (the empty full store description) for this language. French must also restore the exact engine expressions in `ui/amountFormatted` and `ui/humanTimer` without spaces inside `{...}`.
2. **String corrections:** apply the recommended targets in the defect log for every Major/Minor item whose recommendation is supplied. For full store descriptions, commission a complete locale-native marketing translation rather than copying the source.
3. **Weblate flags:** after an editor validates the classifications, add `ignore-reused` to Italian Units 368597, 368705, 368819, 368893 and Turkish Units 369529, 369825. Do not add `ignore-game-markup` to French Units 359515/359527 or `ignore-reused` to Turkish Unit 366670. Do not bulk-add `ignore-same`; review each term/name first.
4. **Remaining coverage:** every non-UI component was reviewed in full. The only unreviewed strings are in `ui`: units not at a zero-based multiple-of-five API position and without a current check warning. Review those remaining UI units before claiming a component-wide project grade.
