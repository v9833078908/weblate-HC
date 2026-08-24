# Weblate LQA Audit: `ui-multilingual.xlsx` (DE / FR)

> Findings are **analyst-reviewed manually** in this session. No `weblate/trans/judge.py` was invoked; no `audit_component.py` heuristic was trusted blindly (its whitespace-normalising placeholder comparison hides the FR defects, so a brace-balanced byte-level comparator was added). No source under `weblate/`, `weblate_customization/`, `loc_kit_ingest/`, or `.omp/` was modified; no API calls were made; the source xlsx was not edited.
>
> Verdicts: `/tmp/verdicts_de.json`, `/tmp/verdicts_fr.json` (each declares `review_scope: {"coverage":"full"}`; FR file also carries `follow_up_candidates` for items without verified defect evidence).
>
> Audit-trail corrections vs the earlier in-session reports:
> 1. The earlier 12 DE / 6 FR `accuracy/untranslated` Major entries were over-classified - every one of those rows is a valid German / French homograph, placeholder token, or proper noun, i.e. a **false positive of `same`**. Reclassified to `fluency/style` Neutral (0 pt).
> 2. The earlier 3 FR `Critical` `game_engine/broken_placeholder` entries were over-classified - the engine DSL parser behaviour for `{name:cond:>` lexeme boundaries is **not** in `weblate_customization/checks.py` (which only covers mission-DSL `identifier[|...]` per `checks.py:94-102,218-233`); runtime outcome is unverified. Severity dropped to **Major** only where `GameMarkupCheck` is confirmed positive (`amountFormatted`, `timer`), and to **observation only** for `humanTimer`.
> 3. `humanTimer` was previously carried as a scored Minor. With no check firing and no runtime evidence, it cannot simultaneously lower MQM score; it is now stored in a separate `follow_up_candidates` block, excluded from `compute_mqm_score`.
> 4. Step 4 / coverage discipline: the earlier reviews examined only the 31 (DE) / 26 (FR) `same` rows plus three placeholder rows (approximately 60 pairs); MQM scoring was inflated against the full component. This report is based on a full walk of all 466 x 2 = 932 pairs and an explicit `reviewed_unit_ids` set in each verdicts file.

---

## 1. MQM-Core Quality Scorecard

| Metric | DE | FR |
|---|---|---|
| **Coverage mode** | `full` | `full` |
| **Units reviewed** | 466 / 466 (100%) | 466 / 466 (100%) |
| **Words reviewed (MQM denominator)** | 2521 / 2521 (100%) | 2521 / 2521 (100%) |
| **MQM Score** | **100.00 / 100** | **99.60 / 100** |
| **Grade** | **Grade A (Pass)** | **Grade A (Pass)** |
| **Release Gate Status** | Approved for Release | Approved for Release |
| **Critical Defects (25 pt)** | 0 | 0 (none verified; `humanTimer` is follow-up, not a defect) |
| **Major Defects (5 pt)** | 0 | **2** (`amountFormatted`, `timer` - confirmed `GameMarkupCheck` positives) |
| **Minor Defects (1 pt)** | 0 | 0 |
| **Total Penalty Points** | 0 pt over 2521 words | 10 pt over 2521 words |
| **Follow-up candidates (unscored)** | 0 | **1** (`humanTimer` - runtime not verified) |

Math: DE 0 / 2521 x 100 = 0.00 -> 100.00. FR 10 / 2521 x 100 approximately 0.40 -> 99.60. Both Grade A, both release-approved under Step 4's full-coverage gate.

If the `humanTimer` follow-up is later promoted to a scored Major (engine team confirms a parse-time crash), the FR card recomputes to **15 / 2521 x 100 approximately 0.60 -> 99.41**, which is **Grade B**, **not Critical** - a single unverified runtime failure does not trigger Critical auto-reject; that requires 25 pt worth of evidence.

---

## 2. Reviewed Defect Log (MQM Categories)

### DE - Neutral - `fluency/style` (31 entries, 0 pt)

All 31 DE `target == source` rows are false positives of the `same` check. Breakdown:

- 17 template / placeholder / markup tokens: `amount`, `amountFormatted`, `add_amount`, `x_of_y`, `from_to_percent`, `percentAmount`, `highlighted_x_of_y`, `profitPercent`, `(profitPercent)`, `timer`, `select_x_of_y`, `addPercentAmount`, `amountKg`, `addAmountKg`, `storePrice`, `xAmount`
- 12 valid German homographs or English loans: `vibration`, `sound`, `start`, `tutorial`, `pause`, `gold`, `curseRemovalSlot`, `upgradeCraftCurseRemovalSlotsStat`, `geode`, `hearthCurrency`, `upgradeCraftCostStat`, `statEffectHeal`
- 3 proper nouns: `terrain2Title` (Belomar), `terrain2Biome3Title` (Lostwood), `terrain3Title` (Grimdal)

Recommended remediation once uploaded as a Weblate component: apply `ignore-same` per row; no translation change.

### FR - Major - `game_engine/broken_placeholder` (2 entries, scored)

| key | check that fires | byte-level detail |
|---|---|---|
| `amountFormatted` | **Yes** - `GameMarkupCheck` (`placeholder_sequence(EN) != placeholder_sequence(FR)`) | `placeholder_sequence(EN) = ('{value:amount()}', '{value:N0}')`; `placeholder_sequence(FR) = ('{value\u00A0:amount()}', '{value\u00A0:N0}')`. NBSP (`U+00A0`) inside both nested placeholders. Severity Major (5 pt). |
| `timer` | **Yes** - `GameMarkupCheck` (3 placeholder-sequences differ) | `placeholder_sequence(EN) = ('{hours:00}', '{minutes:00}', '{seconds:00}')`; `placeholder_sequence(FR) = ('{hours\u00A0:00}', '{minutes\u00A0:00}', '{seconds\u00A0:00}')`. NBSP inside each inner `{...:00}` block. Severity Major (5 pt). NBSP between top-level blocks (outside any `{...}`) is benign French typography and was excluded from the comparator. |

### FR - Follow-up candidate - unverified engine-runtime concern (1 entry, **not scored**)

| key | check that fires | evidence collected | missing evidence |
|---|---|---|---|
| `humanTimer` | **No** - `placeholder_sequence(EN) == placeholder_sequence(FR) = ('{hours}', '{minutes}', '{seconds}')`. `GameMarkupCheck` does NOT fire. `GameTokenCheck` does NOT apply (mission-DSL `identifier[|...]` only, per `weblate_customization/checks.py:94-102,218-233`). | NBSP (U+00A0) and NNBSP (U+202F) sit between lexemes inside the OUTER `{name:cond:>0?...}` DSL wrapper - which the Weblate placeholder regex does not capture as a separate placeholder sequence. | Engine-runtime parser behaviour on `{name:cond:>` lexeme boundaries when whitespace (including NBSP) splits lexemes is unverified in this audit; no pixel / UI-crash evidence collected. |

Stored in `verdicts_fr.json:follow_up_candidates[0]` and excluded from `compute_mqm_score`. Re-score after engine validation: if runtime proves NBSP-in-DSL-wrapper fatal, this entry promotes from follow-up to Major (penalty +5) and the FR card becomes 15 pt / 2521 -> 99.41 (Grade B). If proven harmless, drop the follow-up entirely.

### FR - Neutral - `fluency/style` (26 entries, 0 pt)

- 19 template / placeholder tokens: `amount`, `add_amount`, `x_of_y`, `from_to_percent`, `percentAmount`, `pointsAmount`, `highlighted_x_of_y`, `profitPercent`, `(profitPercent)`, `select_x_of_y`, `addPercentAmount`, `addAmountPcs`, `addAmountSec`, `amountSec`, `amountKg`, `addAmountKg`, `storePrice`, `xAmount`
- 2 proper nouns: `terrain2Title` (Belomar), `terrain3Title` (Grimdal)
- 5 valid French loans / homographs: `vibration`, `confirmation`, `rare`, `forge`, `pause`, `statEffectToolSpeedNegative` (Fatigue)

All false-positives of `same`. Recommended: `ignore-same`.

### DE / FR - Other categories (Layer 1 walk, 0 findings)

After the full 932-pair pass:

- `cyrillic_leak` - 0 DE, 0 FR
- `number_mismatch` (after markup strip) - 0 DE, 0 FR
- `markup_damage` (tag-count mismatch with non-empty tags) - 0 DE, 0 FR
- `accuracy/untranslated` (target == source where source has non-Latin script) - 0

Each unit was walked for every category above; a clean unit produces no verdict entry but is still in `reviewed_unit_ids`.

---

## 3. Weblate Quality Checks Analysis (Layer 0)

| Check | DE hits | FR hits | Outcome |
|---|---|---|---|
| `same` (target == source) | 31 | 26 | All 31 (DE) and 26 (FR) classified as False Positives (placeholder tokens / proper nouns / valid homographs). |
| `markup_imbalance` (tag-count mismatch with non-empty tags on both sides) | 0 | 0 | - |
| `placeholder_sequence` differs (`GameMarkupCheck`) | 0 | **2** keys (`amountFormatted`, `timer`) | True Positive - verified by `weblate/trans/protected_tokens.py:placeholder_sequence`. |
| Byte-level NBSP inside balanced braces (NOT a Weblate check) | 0 | **3** keys (`amountFormatted`, `humanTimer`, `timer`) | `amountFormatted` and `timer` already covered; `humanTimer` is follow-up. |
| `cyrillic_leak` | 0 | 0 | - |
| `number_mismatch` (after markup strip) | 0 | 0 | - |

`audit_component.py`'s shipped heuristic normalises whitespace inside `{...}` before comparison - re-implemented with a brace-balanced raw-bytes comparator so the FR placeholder-sequence drift surfaces.

---

## 4. Actionable Remediation Plan

### 4.1 String Corrections

No paste-ready rewrites in this report (audit-only deliverable). The two confirmed `GameMarkupCheck` FR rows require the project translator to regenerate the values such that the captured `{[^{}]*}` placeholder sequences are byte-equal to EN. The mechanical rule the translator can apply: **inside every `{...}` block, the only acceptable whitespace is the same one EN uses between the same lexemes**; NBSP / NNBSP only outside braces where French typography requires it.

### 4.2 Weblate Flags

Once the kit is uploaded as a Weblate component:

- Apply `ignore-same` once per row on the 31 DE and 26 FR documented false-positives (key list in section 2).
- For `amountFormatted` and `timer`: do **not** apply any flag - corrected strings pass `GameMarkupCheck` outright, and leaving the check active guards future drift.
- For `humanTimer`: no flag either way; record the engine-team answer in component notes.

### 4.3 Coverage statement

`coverage_mode: full` for both languages. `reviewed_unit_ids` lists every unit id 1-466 for both DE and FR; the verified word denominator is 2521 / 2521 in each language. No partial-coverage caveats apply.

---

## Open follow-ups

1. `humanTimer` (FR) - confirm with the engine team whether the `:cond:>` outer wrapper tolerates NBSP between lexemes. If sensitive, the follow-up promotes to a scored Major (FR card becomes 99.41 / Grade B, **not** Critical); if harmless, drop the follow-up entirely.
2. Project glossary review - decide once whether `Sound`, `Tutorial`, `Likes`, `Pause`, `Start`, etc. should be canonicalised per language or kept as project-loans. The `ignore-same` recommendation only papers over `same`; it does not record the design decision.

## File state

- `/Users/eli/Downloads/ui-multilingual.xlsx` - **untouched** (mtime `2026-08-22 19:59:01`, size 74186 bytes).
- `/tmp/verdicts_de.json`, `/tmp/verdicts_fr.json` - written with the corrected verdicts and (FR) the `follow_up_candidates` block.
- `/tmp/lqa-reports/ui-multilingual-lqa-de-fr.md` - source report.
- No source under `weblate/`, `weblate_customization/`, `loc_kit_ingest/`, or `.omp/` was modified.
