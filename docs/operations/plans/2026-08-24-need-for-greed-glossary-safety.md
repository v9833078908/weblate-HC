# Need for Greed glossary safety implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop the Turkish and Indonesian global `Forge` glossary entries from supplying a wrong sense, correct the unambiguous Turkish `Ancient Tome` entry, and add supported source explanations for every live `Forge` occurrence.

**Architecture:** This is a production data-only operation through the supported Weblate REST API. It does not change ordinary translations, execute a Django shell, edit source notes, or deploy application code. The unsafe global `Forge` entries are excluded only for their target languages; source-unit explanations supply the intended sense to LLM machinery.

**Tech Stack:** Weblate REST API, production `need-for-greed` project, authenticated read-back verification.

---

**Status:** Completed and verified on 2026-08-24

## Scope

- Turkish glossary target Unit 352924: set the language-scoped `not-applicable` flag on `Forge -> Dövme`.
- Indonesian glossary target Unit 352919: set the language-scoped `not-applicable` flag on `Forge -> Tempa`.
- Turkish glossary target Unit 353144: replace `Ancient Tome -> Antik Tomar` with `Kadim Kitap` and append `A substantial book or volume; not a scroll.` to the existing source explanation `[Loot] artifactBook` on source Unit 86469.
- Write source-unit explanations to the nine source units listed below. The supported API writes `explanation`, not `note`.

## Preflight snapshot

The immediately refreshed production snapshot is a mutation precondition:

- Units 352924 and 352919 have `extra_flags: ""`.
- Unit 353144 has target `Antik Tomar`, state `20`, and `extra_flags: ""`.
- Source Unit 86469 has explanation `[Loot] artifactBook`.
- Source Units 356067, 356089, 356090, 357864, 357895, 356068, 356099, 356104, and 356219 have blank explanations.

Any divergence aborts the operation for a fresh review; no PATCH may overwrite concurrent work.

## Source explanations

| Source Unit | Component/key | Explanation |
| --- | --- | --- |
| 356067 | `ui/forge` | `Gameplay building: Forge is the upgradable crafting workshop. Do not translate this label as the act of forging or a tattoo.` |
| 356089 | `ui/craftClosedRecipeTitle` | `Gameplay building: the forge is the upgradable crafting workshop, not the act of forging or a tattoo.` |
| 356090 | `ui/craftClosedRecipeDescription` | `Gameplay building: both "Forge Level" and "forge of legends" refer to the upgradable crafting workshop. Neither occurrence means the act of forging or a tattoo.` |
| 357864 | `tutorial/DialogueClaimedChestCharacter1` | `Gameplay building: "magic forge" is the crafting workshop. It can transform items; it is not the act of forging or a tattoo.` |
| 357895 | `tutorial/tip9` | `Gameplay building: the player upgrades the Forge crafting workshop to unlock recipes.` |
| 356068 | `ui/craftOpenEliteSlotDialog` | `Gameplay verb: "forge" means to craft or process treasures. It does not name the Forge building.` |
| 356099 | `ui/upgradeCraftRecipesStatDescription` | `Gameplay verb: "forge" means to craft items. It does not name the Forge building.` |
| 356104 | `ui/storageUpgradeDescription` | `Gameplay verb: "forged" describes treasures that were crafted. It does not name the Forge building.` |
| 356219 | `ui/craftSlotDescription` | `Gameplay verb: "forge" means to craft a piece of jewelry. It does not name the Forge building.` |

## Execution

1. Refresh and compare the three glossary target records and ten source records to the preflight snapshot. Abort on any mismatch.
2. PATCH Unit 352924 and Unit 352919 with `extra_flags: "not-applicable"`.
3. PATCH Unit 353144 with target `Kadim Kitap` and state `20`, then PATCH source Unit 86469 with `[Loot] artifactBook\n\nA substantial book or volume; not a scroll.`.
4. PATCH the nine source units with their listed explanations.
5. Read every changed record back. Confirm both `Forge` target records are marked `not-applicable`, the Turkish Tome target is `Kadim Kitap`, all ten source explanations match exactly, and no ordinary target string was updated.

## Production result

- Units 352924 and 352919 now carry `not-applicable`; their incorrect language-specific `Forge` targets cannot participate in Turkish or Indonesian glossary matching.
- Unit 353144 now stores Turkish `Kadim Kitap`; source Unit 86469 preserves `[Loot] artifactBook` and appends the Tome meaning constraint.
- Ten source explanations were read back exactly: the Tome term plus nine `Forge` building/verb contexts.
- The nine source strings and their extra flags were unchanged. No selected ordinary Turkish target changed during the rollout window after `2026-08-24T19:08:00Z`.
- Validation detected three Turkish target changes before the rollout source-explanation writes: Units 369662, 367606, and 367637 changed at `18:27Z`. They were treated as concurrent work and were not rolled back.

## Rollback

- PATCH Units 352924 and 352919 with the snapshotted `extra_flags: ""`.
- PATCH Unit 353144 back to target `Antik Tomar` with state `20`.
- PATCH source Unit 86469 back to its snapshotted explanation `[Loot] artifactBook`.
- PATCH source Units 356067, 356089, 356090, 357864, 357895, 356068, 356099, 356104, and 356219 back to their snapshotted blank explanations.

## Out of scope

- Modifying `note`; the REST API exposes it read-only.
- Changing any normal Turkish or Indonesian target translation.
- Global `check_glossary` enforcement.
- A context-scoped terminology check, LLM judge release gate, or application deployment.
