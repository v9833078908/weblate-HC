# Weblate LQA Audit: victory-banner/common (DE)

> **Audit integrity notice.** All verdicts below are **manual analyst review** (Claude,
> interactive session, 2026-08-22), *not* output from the fork's automated two-seat
> LLM judge (`weblate/trans/judge.py`) — that pipeline was not invoked. Coverage is
> **partial**: 126 of 448 units (28.1%) were reviewed; the rest were covered only by
> Weblate's deterministic checks (Layer 0) and a regex pattern-scan (catches acronym
> leaks, Cyrillic leaks, bracket mismatches — not semantic/lore/register errors). The
> score below is a **defect-density indicator over the reviewed subset**, not a
> projectable grade for the full component. See "Audit Scope" at the end.
>
> Generated with the corrected `weblate-lqa` skill (`review_scope`-aware
> `audit_component.py`) against live `victory-banner/common` (DE) on 2026-08-22.
> Supersedes an earlier draft of this file that misattributed 3 of 8 findings to the
> wrong unit IDs (345025/345037/345039, an unit-id transcription slip while
> hand-authoring the verdicts file) — the correct IDs are 345028/345042/345047. That
> class of bug is now caught automatically by a context-consistency check added to
> `compute_mqm_score`.

## 1. MQM-Core Quality Scorecard

### Review Coverage

| Metric | Value |
|---|---|
| **Coverage mode** | `sample` (PARTIAL) |
| **Units reviewed** | 126 / 448 (28.1%) |
| **Words reviewed (MQM denominator)** | 912 / 1410 (64.7%) |

| Metric | Value | Status |
|---|---|---|
| **MQM Score (SAMPLE-SCOPED, non-projectable)** | **94.85 / 100** | Non-projectable — sample only |
| **Release Gate Status** | **BLOCKED** | 🔴 Critical defect unresolved + audit coverage incomplete |
| **Critical Defects (25 pt)** | 1 | 🔴 Must fix before any release consideration |
| **Major Defects (5 pt)** | 4 | Terminology/mechanic issues |
| **Minor Defects (1 pt)** | 2 | Minor polish |
| **Neutral (0 pt)** | 1 | Style note only, not a defect |
| **Total Penalty Points** | 47 pt | over 912 reviewed words |
| **Component-wide conclusion** | **Not gradable** | 71.9% of units (322/448) never reviewed for meaning/register/lore — see Audit Scope |

$$\text{Sample MQM} = 100 - \left(\frac{47}{912}\times 100\right) = 94.85 \qquad \left(\text{full-component denominator (1410) would wrongly read } 96.67\right)$$

## 2. Reviewed Defect Log (MQM Categories)

- 🔴 **[CRITICAL]** `Unit_DriverVermaht` (Unit 345111):
  - **Source:** `Стрелок`
  - **Target:** `Fahrer`
  - **Category:** `accuracy/context_hallucination`
  - **Explanation:** Translated as "Fahrer" (Driver), apparently keyed off the context string `Unit_DriverVermaht` rather than the actual source word "Стрелок" (Rifleman/Schütze). Cross-checked against the parallel unit `Unit_DriverRKKA` (`Водитель` → `Fahrer`, correct) — that pair now collides on the same target, which is exactly what the `reused` check independently flagged. **Fix:** `Fahrer` → `Schütze`.

- 🟠 **[MAJOR]** `Item_M24Tank` (Unit 345028):
  - **Source:** `ПТ М24`
  - **Target:** `AT M24`
  - **Category:** `terminology/acronym_leak`
  - **Explanation:** English "AT" (Anti-Tank) leaked into German target. Verified via historical/gaming-terminology research: authentic German military nomenclature uses `PaK` (Panzerabwehrkanone) / `Panzer-`; "AT" is not an accepted German abbreviation. **Fix:** `AT M24` → `Panzer-M24` (matches the in-component sibling convention `ButtonPanzerGrenade`: `ПТ граната` → `Panzergranate`).

- 🟠 **[MAJOR]** `Item_RGD33Tank` (Unit 345042):
  - **Source:** `ПТ РГД-33`
  - **Target:** `AT RGD-33`
  - **Category:** `terminology/acronym_leak`
  - **Explanation:** Same acronym leak as above. **Fix:** `AT RGD-33` → `Panzer-RGD-33`.

- 🟠 **[MAJOR]** `Item_TM35` (Unit 345047):
  - **Source:** `ПТ мина ТМ-35`
  - **Target:** `AT-Mine TM-35`
  - **Category:** `terminology/acronym_leak`
  - **Explanation:** Same acronym leak. **Fix:** `AT-Mine TM-35` → `Panzerabwehrmine TM-35`.

- 🟠 **[MAJOR]** `ButtonOutVehicle` (Unit 344994):
  - **Source:** `Выгрузить`
  - **Target:** `Entladen`
  - **Category:** `terminology/inappropriate_register`
  - **Explanation:** "Entladen" means cargo unload / weapon discharge. Infantry/crew dismounting a vehicle needs `Aussteigen`/`Absitzen`. Not just a stylistic call — the `reused` check (Layer 0, deterministic) independently confirms the same target `Entladen` is already used by `ButtonDischarge` (`Разрядить`, weapon discharge), so this is a real functional label collision between two different in-game actions. **Fix:** `Entladen` → `Aussteigen`.

- 🟡 **[MINOR]** `MainTask1_Start` (Unit 345265):
  - **Source:** `...через станцию поедут отступающие эшелоны.`
  - **Target:** `...bevor die sich zurückziehenden Staffeln den Bahnhof passieren.`
  - **Category:** `fluency/register_tone`
  - **Explanation:** "Staffel" reads as air squadron / formation echelon in German; the source clearly means retreating **trains** ("поедут" = will ride/travel through the station — a formation doesn't "ride"). **Fix:** `Staffeln` → `Militärzüge`.

- 🟡 **[MINOR]** `Letter4` (Unit 345294):
  - **Source:** `Здравствуй, Настя.`
  - **Target:** `Hallo, Anastasia.`
  - **Category:** `fluency/register_tone`
  - **Explanation:** All 4 sibling letters keep the diminutive/informal name (`Alexej`, `Katja`, `Kolja`, `Mischka`/`Stepka`); this one alone expands to the formal full name, breaking the established narrative-voice pattern. **Fix:** `Anastasia` → `Nastja`.

- ⚪ **[NEUTRAL — no fix required]** `BuildHelp_LineDeletePoint` (Unit 345240):
  - **Source:** `[пкм] - удалить последнюю точку`
  - **Target:** `[RMT] – Letzten Punkt löschen`
  - **Category:** `fluency/style`
  - **Explanation:** Self-corrected during this re-audit: the German is grammatically correct (standard verb+object UI phrase). The verb-phrase-vs-noun pattern difference against sibling `BuildHelp_*` hints is a style observation, not a `grammar_syntax` defect per this project's own MQM-Core Game Profile definition — does not carry a penalty.

## 3. Weblate Quality Checks Analysis (Layer 0)

16 active check warnings across 3 check types, queried directly via `q=check:<id>` against the live API (deterministic evidence, not linguistic judgment):

| Check ID | Units | Root cause |
|---|---|---|
| `same` | 2 | **False positive.** `MenuButtons_Discord`, `Item_LugerP08` — brand/weapon-model proper nouns correctly left untranslated. |
| `multiple_capital` | 3 | **False positive.** `BuildHelp_LineEnd`, `BuildHelp_LineDeletePoint`, `BuildHelp_LineNewPoint` — source uses lowercase `[лкм]`/`[пкм]`, German target correctly uses uppercase `[LMT]`/`[RMT]` keybinding abbreviations. |
| `reused` | 11 | **Mixed.** 2 are the confirmed MQM defects above (`Unit_DriverVermaht`, `ButtonOutVehicle`). 5 are false positives from legitimate German grammatical convergence (`Sanitäter` identical singular/plural ×3; `zerstört` identical masc/neuter participle ×2). **4 remain genuinely open** — see below. |

### Open item not yet formally scored (methodology gap, disclosed rather than silently added)

`ButtonReload` (Unit 344952, `Перезарядить` → `Nachladen`) and `ButtonReloading` (Unit 344998,
`Перезарядка` → `Nachladen`) collide under `reused` — a verb-command and a noun/status label
sharing one German target. A fix was discussed in-session (`Nachladen` for the action button,
`Nachladevorgang` for the status label) but was never entered into the reviewed-verdicts file
with the same rigor as the 7 scored items above, so it is **not** reflected in the MQM score.
Needs its own review pass before closing.

## 4. Actionable Remediation Plan

### 4.1 String Corrections (7 units, blocks release until applied)

| Unit ID | Context | Current Target (DE) | Corrected Target (DE) |
|---|---|---|---|
| 345111 | `Unit_DriverVermaht` | `Fahrer` | **`Schütze`** |
| 345028 | `Item_M24Tank` | `AT M24` | **`Panzer-M24`** |
| 345042 | `Item_RGD33Tank` | `AT RGD-33` | **`Panzer-RGD-33`** |
| 345047 | `Item_TM35` | `AT-Mine TM-35` | **`Panzerabwehrmine TM-35`** |
| 344994 | `ButtonOutVehicle` | `Entladen` | **`Aussteigen`** |
| 345265 | `MainTask1_Start` | `...zurückziehenden Staffeln...` | **`...zurückziehenden Militärzüge...`** |
| 345294 | `Letter4` | `Hallo, Anastasia.` | **`Hallo, Nastja.`** |

### 4.2 Weblate Flags to Apply (Layer 0 false-positive closure)

| Unit ID(s) | Context | Flag |
|---|---|---|
| 344911, 345026 | `MenuButtons_Discord`, `Item_LugerP08` | `ignore-same` |
| 345239, 345240, 345241 | `BuildHelp_LineEnd/LineDeletePoint/LineNewPoint` | `ignore-multiple-capital` |
| 344972, 344973 | `WasDestroy`, `TARGETDESTROYED` | `ignore-reused` |
| 345073, 345086, 345113 | `Squad_RKKAMedics`, `Squad_VermahtMedics`, `Unit_MedicVermaht` | `ignore-reused` |

Applying 4.1 will also make the `reused` collision on `Unit_DriverVermaht`/`Unit_DriverRKKA` and
`ButtonOutVehicle`/`ButtonDischarge` disappear on its own — no flag needed there.

### 4.3 Before this component can be graded (not just patched)

1. Fix the 7 strings in 4.1 (using the unit IDs in the table above — **do not** reuse
   any unit ID from memory; copy directly from Weblate or this table) and re-run
   `update_checks` — this clears the 1 Critical blocker.
2. Resolve the `ButtonReload`/`ButtonReloading` open item (§3).
3. Extend manual/LLM-judge review to the remaining **322 unreviewed units** (71.9% of
   the component) before quoting any MQM score as representative of
   `victory-banner/common` DE as a whole. Until then, the only defensible statement
   is: *"1 confirmed Critical, 4 confirmed Major, 2 confirmed Minor in the audited
   subset; full-component quality unknown."*

---

## Audit Scope (methodology disclosure, required reading before reusing this score)

| | |
|---|---|
| Component total | 448 units / 1,410 source words |
| Reviewed by analyst (union of visual sample + every heuristic candidate + every scored verdict) | 126 units (28.1%) / 912 words (64.7%) — skewed toward long narrative text (letters, mission briefings), which is word-dense |
| Reviewed only by regex pattern-scan (100% coverage, narrow scope) | Acronym leaks, Cyrillic leaks, bracket-count mismatches only — cannot catch mistranslation, register, or lore errors |
| Reviewed only by Weblate deterministic checks | `same`, `multiple_capital`, `reused` (syntax-level, not meaning-level) |
| Never touched by any method | 322 units (71.9%), mostly short UI/button/item labels outside the sampled domains |

Two numbers were computed and are reported for transparency, neither should be quoted alone:
- **94.85** — sample MQM (47 pt / 912 words), valid only for the 126 reviewed units.
- ~~96.67~~ — **rejected**: same 47 pt divided by the full 1,410-word component denominator;
  mixes a partial-sample numerator with a full-population denominator and silently assumes the
  unreviewed 71.9% is defect-free, which was never verified.

### Correction log for this file

- **2026-08-22, first draft:** used unit IDs 345025/345037/345039 for the
  `Item_M24Tank`/`Item_RGD33Tank`/`Item_TM35` findings — a transcription slip made
  while hand-typing the verdicts JSON (those IDs actually belong to `Item_DP27`,
  `Item_MP40`, `Item_PMD6`, none of which have this defect). Coverage was also
  understated (124/907) because it omitted the units that were reviewed exclusively
  through the heuristic-candidate channel.
- **2026-08-22, this version:** corrected to the true unit IDs 345028/345042/345047
  (re-verified against a fresh live re-fetch of the component) and the properly
  deduplicated coverage figure (126 units / 912 words). `audit_component.py` now
  rejects any verdict whose declared `context` does not match the actual unit at its
  `unit_id`, so this specific mistake cannot recur silently.
