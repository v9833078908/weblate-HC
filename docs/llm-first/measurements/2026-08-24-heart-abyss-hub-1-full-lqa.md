# heart-abyss/hub-1: full-coverage LQA across nine target languages

Date: 2026-08-24. Instance: `l10n.herocraft.com`. Component: `heart-abyss/hub-1`,
source language `ru`, format `po-mono`, 396 units / 2371 source words per language,
9 target languages, 100 % translated.

Findings are **analyst-reviewed**: a deterministic full-coverage structural pass plus an
LLM-assisted linguistic review of every unit, with every finding re-validated against real
unit IDs. The fork's two-seat judge (`weblate/trans/judge.py`) was **not** invoked.

## 1. Scope and method

| Parameter | Value |
| --- | --- |
| Units reviewed | **396 / 396** per language (3564 total) |
| Words reviewed (MQM denominator) | **2371 / 2371** per language |
| Coverage mode | `full` for all nine languages |
| Deterministic pass | placeholders, empty targets, source-copy leaks, Cyrillic leaks, digit multisets, target collisions, proper-noun/glossary matching, cross-language shape consensus |
| Linguistic pass | 9 language sample reviews (~30-40 %) + 18 chunk reviews covering the remainder |
| Scoring | `.omp/skills/weblate-lqa/scripts/audit_component.py --verdicts` with `review_scope: {"coverage": "full"}` |

Nothing was written to Weblate. Artifacts: `/tmp/lqa/verdicts_full_<lang>.json`,
`/tmp/lqa/scored_full_<lang>.json`.

## 2. Provenance: these are not translator errors

The component's change history answers the question directly. Per language, via
`/api/translations/heart-abyss/hub-1/<lang>/changes/`:

| Language | `Automatically translated` (action 6) | Human edits (actions 2, 5) | Author | Distinct batch timestamps |
| --- | --- | --- | --- | --- |
| de, en, es, it, ja, ko, zh_Hans, zh_Hant | 396 each | **0** | `mt:openrouter` | 32-40 |
| fr | 396 | **1** | `mt:openrouter` | 36 |

All 3564 target strings are raw machine-translation output from the fork's
`RoutedLLMTranslation` service, with exactly **one** human edit in the entire component.
This is therefore an MT-quality measurement, not an assessment of translator work.

The Russian source is not the cause either. For the German rotation described in §4, the
source string under `hub1_asuna_1_6` has read `У тебя сердце слишком быстро бьётся.`
continuously since it was created on 2026-08-20T13:49:14Z; the only later source edits in
that scene were whitespace normalisations on `_1_1`..`_1_4` and `_1_10`
(action 59, 2026-08-20T17:02:00Z). The German targets were written on
2026-08-24T12:12:14Z, four days after the source was final, and arrived already rotated.

## 3. Root cause: the LLM batch protocol has no per-string identity

`RoutedLLMTranslation` inherits the batch path from
`OpenAITranslation` (`weblate/machinery/openai.py:208`) and
`BaseLLMTranslation` (`weblate/machinery/llm.py:316`).

1. The prompt (`weblate/machinery/llm.py:82-206`) sends `strings: [{source, key, context, …}]`
   and requires, in rules 1, 11, 13 and 20, "a single JSON array containing one item per
   input string … in the same order". **The reply carries no identifier per item.**
2. `_normalize_translation_items` (`weblate/machinery/llm.py:2430-2455`) assigns
   `translations[index]` to `sources[index]`. Alignment is positional and unverified.
3. The only structural guard is a length comparison
   (`weblate/machinery/llm.py:2436`): `len(translations) != len(sources)`.
4. Per-item validation covers placeholder multisets and literal `@` suffixes only
   (`weblate/machinery/llm.py:2480-2489`), repeated for the partial-reply rescue path in
   `_validate_translation_prefix` (`weblate/machinery/llm.py:2494-2537`).

This component contains no placeholders at all, so for a reply of the correct length
**not a single guard can fire**, whatever the internal ordering. A model that drops one
line inside a batch and shifts the rest produces a fully accepted result.

The recovery machinery reinforces the assumption: `PartialLLMReplyError`
(`weblate/machinery/llm.py:238-244`) and `_split_sources`
(`weblate/machinery/llm.py:2701-2712`) repair *count* mismatches, so the pipeline is tuned
to treat a correct count as evidence of correct alignment.

German batches held roughly 12 units (34 distinct timestamps for 396 units);
`hub1_asuna_1_5`..`_1_9` were written in the same second, and the corrupted window sits
inside that batch.

### 3.1 A second, separate defect: `game-number` cannot see myriad-scale errors

`GameNumberCheck` (`weblate_customization/checks.py`) compares the digit multiset of source
and target. On the bounty joke (`hub1_guard_1_3` = 10 000, `hub1_guard_1_4` = 100 000):

| Language | `_1_3` target | Value | Check fired | Verdict |
| --- | --- | --- | --- | --- |
| ja | `10万文！` | 100 000 | **no** | **false negative, 10x error invisible** |
| zh_Hans | `一万文！` | 10 000 ✓ | yes | false positive |
| zh_Hant | `一萬文！` | 10 000 ✓ | yes | false positive |
| en, es, fr | `10,000` / `10 000` | ✓ | yes | false positive (thousands separator) |
| de, it, ko | `10 Tausend` / `10 mila` / `10천` | ✓ | no | correct silence |

| Language | `_1_4` target | Value | Check fired | Verdict |
| --- | --- | --- | --- | --- |
| ja | `100万文` | 1 000 000 | **no** | **false negative, 10x error invisible** |
| zh_Hans | `一百万文` | 1 000 000 | yes | true positive, but only because CJK numerals carry no ASCII digits |
| zh_Hant | `十萬文` | 100 000 ✓ | yes | false positive |
| ko | `10만 몬` | 100 000 ✓ | yes | false positive |
| en, es, fr | `100,000` / `100 000` | ✓ | yes | false positive |
| de, it | `100 Tausend` / `100 mila` | ✓ | no | correct silence |

Japanese writes 100 000 as `10万`, whose digits `[1,0]` match the source's `10` exactly.
The check is blind precisely where myriad-scale errors occur, and noisy where values are
correct. Of the 11 firings: **10 false positives**, **1 true positive** (zh_Hans 371763,
and only because CJK numerals carry no ASCII digits at all), plus **2 false negatives**
that never fire (ja 370970 and 370971, the only two genuine numeric errors in the
component).

## 4. Scorecards (full coverage)

Grade bands come from `references/mqm-game-profile.md` §5. A critical defect blocks release
regardless of score.

| Language | MQM | Critical | Major | Minor | Neutral | Penalty | Grade | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `de` | **89.12** | **4** | 24 | 38 | 0 | 258 pt | Fail (Critical Blocker) | Blocked |
| `ja` | **93.97** | **1** | 17 | 33 | 3 | 143 pt | Fail (Critical Blocker) | Blocked |
| `ko` | **96.71** | **1** | 6 | 23 | 0 | 78 pt | Fail (Critical Blocker) | Blocked |
| `it` | 95.82 | 0 | 7 | 64 | 0 | 99 pt | Grade A | Approved for Release |
| `zh_Hans` | 96.42 | 0 | 14 | 15 | 2 | 85 pt | Grade A | Approved for Release |
| `fr` | 96.71 | 0 | 10 | 28 | 2 | 78 pt | Grade A | Approved for Release |
| `es` | 96.79 | 0 | 7 | 41 | 1 | 76 pt | Grade A | Approved for Release |
| `zh_Hant` | 97.17 | 0 | 8 | 27 | 0 | 67 pt | Grade A | Approved for Release |
| `en` | 97.22 | 0 | 7 | 31 | 0 | 66 pt | Grade A | Approved for Release |

`Approved for Release` is the profile's own gate for Grade A and does not mean defect-free:
the Major defects listed in §6 remain outstanding in those six languages. Under
`references/mqm-game-profile.md` §5 only Grade B and below gate on Major defects.

## 5. Critical defects

### 5.1 German: four-unit rotation with content loss (`hub1_asuna_1_6`..`_1_9`)

| Unit | Context | Russian source | Current German | Correct German |
| --- | --- | --- | --- | --- |
| 370314 | `hub1_asuna_1_6` | `У тебя сердце слишком быстро бьётся.` | `W-was?..` | `Dein Herz schlägt viel zu schnell.` |
| 370315 | `hub1_asuna_1_7` | `Ч-что?..` | `Mir wird schlecht.` | `W-was?..` |
| 370316 | `hub1_asuna_1_8` | `Меня сейчас стошнит.` | `Und warum bist du so stachelig?` | `Mir wird schlecht.` |
| 370317 | `hub1_asuna_1_9` | `А ты чего такой колючий?` | `Ich muss kotzen.` | `Und warum bist du so stachelig?` |

Content loss is confirmed: no German target anywhere in the 396 units renders "your heart is
beating too fast". The only German string containing `Herz` is `hub1_asuna_archive_1_11`
("Und du hast ein heißes Herz"), which belongs to a different scene. Meanwhile
`Меня сейчас стошнит` is rendered twice, as two different variants
(`Mir wird schlecht` and `Ich muss kotzen`). All eight other languages are correctly
aligned here, which is how the defect was isolated.

### 5.2 Korean: meaning inversion (`hub1_fisherman_quest_7_10`, unit 371598)

Source `Так что да. Было пиздец как несложно.` means "it was fucking **easy**"
(`пиздец как` intensifies `несложно` = not difficult). The target
`그래서 말이야. 존나게 쉬운 일도 아니었어.` means "it was **not** an easy thing", reversing the
quest-chain payoff. All eight other languages render it as easy (`en` "fuckin' easy",
`zh_Hant` `根本不難`), confirming the source reading.
Fix: `그래서 말이야. 존나게 쉬웠어.`

### 5.3 Japanese: agent/possessor reversal (`hub1_asuna_1_10`, unit 371110)

`Боишься, что я уведу твоего друга?` became `俺の友達を奪うのが怖いのか？`, which inverts who
threatens whom and writes Asuna's line with the masculine pronoun `俺`.
Fix: `私がお前の友達を奪うのが怖いのか？`

## 6. Systematic findings

Defect counts by normalised category over all 414 logged verdicts:

| Category | de | en | es | fr | it | ja | ko | zh_Hans | zh_Hant | Total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| accuracy / other | 18 | 6 | 16 | 11 | 35 | 15 | 10 | 8 | 8 | 127 |
| register | 21 | 15 | 15 | 8 | 16 | 20 | 12 | 4 | 13 | 124 |
| grammar / agreement | 19 | 2 | 8 | 7 | 13 | 5 | 0 | 1 | 0 | 55 |
| terminology / glossary | 1 | 6 | 1 | 6 | 2 | 7 | 3 | 12 | 5 | 43 |
| omission / addition | 4 | 5 | 1 | 2 | 3 | 3 | 3 | 3 | 4 | 28 |
| punctuation | 1 | 0 | 6 | 5 | 1 | 2 | 0 | 1 | 2 | 18 |
| calque / fluency | 2 | 4 | 2 | 0 | 1 | 2 | 1 | 0 | 1 | 13 |
| misalignment / inversion | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 2 | 2 | 6 |

### 6.1 Register flattening is the dominant failure mode (124 findings)

The Russian deliberately contrasts Yaeko's refined speech against the crude register of the
guard, the fisherman, Unagi and Leon. The MT systematically neutralises this in both
directions:

- Obscenity softened: `de` (`пиздец` -> `verdammt`, `ЕБАТЬ` -> `VERDAMMT`, `ржать` -> `lachen`),
  `zh_Hans`/`zh_Hant`/`ko` (`пиздеж` -> `垃圾` / `廢話` / neutral), `ja` (`ЕПТ` -> `くそっ`),
  `es` (`нажирается` -> `se emborracha`, `че` -> neutral `qué`), `fr`
  (`поперлись` -> `on est allés voir`).
- Obscenity invented where the source has none: `zh_Hans` adds `他妈` to
  `hub1_overlook_1_1` and `hub1_scrappy_1_7`, whose sources carry only capitalised emphasis.
- Refined characters given street register: `en` gives Yaeko `ain't no`, `why'd ya`, <!-- # codespell:ignore -->
  `Empire'll wanna`; `ja` gives her `お前ら`, `だぜ` and the masculine `俺` <!-- # codespell:ignore -->
  (unit 371035); `ja` uses `てめえ` for a neutral Russian `тебе`.

### 6.2 German address forms are broken system-wide (19 grammar findings)

German mixes `du`/`ihr`/`Sie` against the source's consistent plural, and drops plural <!-- # codespell:ignore -->
imperatives to singular: units 370248, 370257, 370299, 370307, 370311, 370312, 370348,
370088, 370089, 370091, 370095, 370127. `hub1_teahouse_1_10` also carries a bare gender
error, `die halbe Imperium` for neuter `das Imperium`.

### 6.3 Glossary compliance (43 findings)

| Term | Correct | Violating languages |
| --- | --- | --- |
| `мон` (currency) | `문` / `文` / `몬` | `ko` 371432, `zh_Hans` 371828, `zh_Hant` 372224 use generic "money" |
| `Трущобы` | `Slums` / `Bas-Fonds` / `Bassifondi` | `en` 343206, `fr` 343602, `it` 370444 |
| `Чайный домик` | `maison de thé` | `fr` 343637 (`salon de thé`), 343787 and 343790 (capitalisation) |
| `наг` | `纳迦` | `zh_Hans` 371693 renders the race as `蛇` (snake) |
| `саке` | `日本酒` | `ja` 370983 |
| `бани` | `銭湯` | `ja` 370988 renders it as `サウナ` |
| Хадзуо | `一夫` | `zh_Hans` 371625, 371721, 371744, 371858, 371872, 371879 use `哈祖欧` and `哈祖奥` |
| Яэко | `八重子` (zh_Hant), `ヤエコ` (ja) | `zh_Hant` 372218, 372220, 372222 use `矢重子`; `ja` 370876, 371030, 371032, 371034 use kanji against a katakana glossary entry |

For Japanese Яэко the deviation is consistent across all four occurrences and the kanji
suits the setting; correcting the **glossary** rather than the strings retracts 20 penalty
points and lifts `ja` from 93.97 to **94.81** (143 pt -> 123 pt). The critical in §5.3 still
blocks the language regardless of score.

### 6.4 Segment-boundary redistribution in both Chinese variants

`hub1_teahouse_1_10` + `_1_11` form one Russian sentence split across two dialogue boxes.
Chinese word order moved the temporal clause ahead of the predicate, so the reward clause
and the street clause swapped boxes in **both** variants:

- `zh_Hant`: `孩子，帝國有一半的人會想在你到達隔壁街道之前……` + `把你交出去領賞。`
- `zh_Hans`: `孩子，帝国一半人会在你走到下个街口前……` + `就想着拿你去领赏。`

Concatenated the meaning is complete and grammatical, so this is not content loss and is
scored **major**, not critical. Read box by box, however, each line is incomplete. `ja` and
`en` keep the clauses in their own segments.

## 7. Layer 0: Weblate check triage

182 check firings across the nine languages.

| check_id | de | en | es | fr | it | ja | ko | zh_Hans | zh_Hant | Total | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `end_stop` | 5 | 12 | 8 | 46 | 2 | 11 | 2 | 3 | 6 | 95 | mixed |
| `end_exclamation` | 0 | 5 | 3 | 22 | 0 | 2 | 1 | 0 | 0 | 33 | mixed |
| `end_question` | 5 | 2 | 7 | 1 | 2 | 1 | 4 | 1 | 2 | 25 | mostly source-driven |
| `game-number` | 0 | 2 | 2 | 2 | 0 | 0 | 1 | 2 | 2 | 11 | see §3.1 |
| `duplicate` | 1 | 1 | 1 | 1 | 0 | 0 | 2 | 1 | 1 | 8 | false positive |
| `ellipsis` | 0 | 0 | 2 | 1 | 0 | 0 | 2 | 0 | 1 | 6 | false positive |
| `reused` | 0 | 0 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 4 | false positive |

- `duplicate` fires on faithfully reproduced source repetition (`Не-не-не` -> `Nee, nee, nee`).
- `reused` collapses `hub1_teahouse_1_26` and `hub1_teahouse_2_35`, whose Russian sources
  differ **only** in final punctuation; in `fr` it fires on two `…` units.
- A **source-side defect** drives much of the `end_*` cluster: `hub1_teahouse_2_35` (ru) is
  missing its terminal `?`, proven by its twin `hub1_teahouse_1_26`, which carries the
  identical sentence with `?`. The same holds for `hub1_fisherman_quest_6_37`. Four and
  three of nine languages respectively "corrected" this independently, and the checks are
  reporting those corrections. Fix the Russian, not nine targets.
- Genuine target-side punctuation defects concentrate in `fr` (46 + 22 firings, an
  inconsistent house style rather than a deliberate one) and `en` (12 `end_stop`).

## 8. Remediation

### 8.1 Content, blocking

1. `de` - rewrite units 370314-370317 per the table in §5.1.
2. `ko` - unit 371598 -> `그래서 말이야. 존나게 쉬웠어.`
3. `ja` - unit 371110 -> `私がお前の友達を奪うのが怖いのか？`; units 370970 -> `1万文`,
   370971 -> `10万文`.
4. `zh_Hans` - unit 371763 -> `十万文`.

### 8.2 Content, non-blocking

5. Glossary decisions per §6.3, then bulk-apply. Prefer amending the glossary for Japanese
   Яэко.
6. German address forms per §6.2 - a single systematic pass over the 12 listed units.
7. Register restoration per §6.1, starting with the invented obscenity in `zh_Hans` and the
   masculine pronouns given to Asuna and Yaeko in `ja`.
8. Source fixes in `ru`, which benefit all nine languages: add the missing `?` to
   `hub1_teahouse_2_35` and `hub1_fisherman_quest_6_37`.

### 8.3 Flags for confirmed false positives

Derived directly from the 182 recorded firings, not from the earlier sampled report.

- `ignore-duplicate` (8 firings, all false positives - faithfully reproduced source
  repetition): `hub1_guard_1_6` - de 370181, en 343339, es 346426, fr 343735;
  `hub1_teahouse_2_20` - ko 371444, zh_Hans 371840, zh_Hant 372236;
  `hub1_fisherman_quest_6_8` - ko 371548.
- `ignore-reused` (4 firings): `hub1_teahouse_1_26` - es 346377 and `hub1_teahouse_2_35` -
  es 346516, whose Russian sources differ only in final punctuation;
  `hub1_fisherman_quest_3_8` - fr 343744 and `hub1_fisherman_quest_6_26` - fr 343932, which
  are two `…` units. `it` and `zh_Hant` produce no `reused` firings in this component.
- `ignore-ellipsis` (6 firings, locale-correct ellipsis): es 346319, 346491; fr 343906;
  ko 371335, 371441; zh_Hant 372295. Note that ko 371441 also carries an unrelated Major
  register defect (§6.1) - the flag suppresses only the ellipsis check.
- `game-number`: flag the ten firings on **value-correct** strings - en 343336, 343337;
  es 346423, 346424; fr 343732, 343733; ko 371367; zh_Hant 372158, 372159; zh_Hans 371762.
  Do **not** flag zh_Hans 371763, which is a real 10x error and must be fixed per §8.1.
  `ja` produces no firings at all because the check is blind to myriad scale (§3.1), so its
  two real errors cannot be flagged or detected until §8.4 lands.

### 8.4 Engineering, proposed (not yet approved)

**Per-string identity in the LLM batch protocol.**

- `weblate/machinery/llm.py` `PROMPT` (82-206): add an `id` to each input string and require
  every reply item to echo it; update rules 11, 13, 20 and the closing example.
- `_normalize_translation_items` (2430-2455): match on the echoed `id` instead of the list
  index. A missing, duplicated or unknown `id` returns `None`, which routes into the
  existing `Mismatching assistant reply` path.
- `_validate_translation_prefix` (2494-2537): the same matching for the partial-reply
  rescue path.
- Keep positional alignment only for a batch of size 1, where it is unambiguous. An
  id-less legacy reply then degrades through the existing `_split_sources` halving instead
  of being silently accepted.

Regression tests must include a batch whose reply is internally rotated by one position
while keeping the same length and carrying no placeholders; today that case is accepted
silently. Verify each new test by reverting the fix and confirming it fails.

**`GameNumberCheck` myriad scale.** Normalise CJK numerals (`万`, `千`, `百`, `十`) and
Western scale words (`Tausend`, `mila`, `천`, `만`) to a numeric value before comparing, so
the check compares values rather than digit multisets. Today it cannot see a 10x Japanese
error and fires on eight correct strings.

## 9. Artifacts

- `/tmp/lqa/verdicts_full_<lang>.json` - merged full-coverage verdicts, `review_scope: {"coverage": "full"}`.
- `/tmp/lqa/scored_full_<lang>.json` - tool-computed MQM output.
- `/tmp/lqa/full_<lang>_<1|2>.tsv` - the review chunks handed to the linguistic reviewers.
- `/tmp/lqa/glossary_<lang>.tsv` - terminology authority extracted from the `all-glossary` TBX.
