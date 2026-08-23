# MQM-Core Game Profile Specification

This document defines the **MQM-Core Game Profile** (Multidimensional Quality Metrics tailored for Video Game and Software Localization). It serves as the standard error typology, severity weighting, and scoring model for the `weblate-lqa` skill.

---

## 1. Error Typology Hierarchy

The game profile groups errors into **4 core dimensions** with specific subcategories relevant to game assets (UI strings, dialogues, quest descriptions, lore, item names, and battle mechanics):

### 1.1. Accuracy (`accuracy/`)
Evaluates the relationship between the source and target meaning.
- `mistranslation`: The target conveys a different meaning from the source (e.g. gameplay mechanics inverted, wrong subject/object, numbers distorted).
- `context_hallucination`: The translation is based on the string key/context name rather than the actual source text (e.g. translating source "Стрелок" as "Fahrer" because context is `Unit_DriverVermaht`).
- `omission`: Meaningful content present in the source is missing in the target without artistic justification.
- `addition`: Unwarranted content is added in the target that introduces unverified lore or false instructions.
- `untranslated`: Full text or significant phrases left in the source language (unless it is a proper noun/brand name).

### 1.2. Terminology & Lore (`terminology/`)
Evaluates adherence to game canon, approved glossaries, and consistent terminology.
- `glossary_violation`: A target term conflicts with an approved project glossary entry.
- `inconsistent_term`: The same game entity (weapon, faction, currency, rank) is translated differently across components without context rationale.
- `acronym_leak`: An acronym from a secondary language is leaked into the target (e.g. using English `AT` for *Anti-Tank* in German instead of `Pz.-` / `Panzerabwehr-`).
- `inappropriate_register`: A term with the wrong domain meaning is used (e.g. using `Entladen` [cargo unload / weapon discharge] instead of `Aussteigen` [infantry disembark]).

### 1.3. Fluency & Style (`fluency/`)
Evaluates the linguistic correctness and natural tone in the target language.
- `grammar_syntax`: ACTUAL grammatical errors only — wrong case endings, incorrect agreement, or word order a native speaker would flag as broken. A stylistic pattern difference between grammatically CORRECT sibling strings (e.g. one UI hint using a verb phrase where its neighbors use a noun) is NOT `grammar_syntax` — classify it as `style` (Neutral) instead. Test: "would a native-speaker proofreader mark this as wrong," not "does it match nearby strings."
- `spelling_orthography`: Typos, wrong capitalization (e.g. uncapitalized German nouns or incorrect compound spacing *Deppenleerzeichen*).
- `register_tone`: Inconsistent formality (mixing polite *Sie* with informal *Du*, or breaking character voice in narrative letters). <!-- # codespell:ignore -->
- `punctuation`: Missing or malformed terminal punctuation, quotes, or dashes violating target locale conventions.
- `style`: A valid, grammatically correct alternative phrasing, register choice, or structural pattern a native speaker would accept as-is — logged for translator awareness but never penalized (always `Neutral`, 0 pt). Use this instead of `grammar_syntax` whenever the target text is not actually wrong.

### 1.4. Game & Engine (`game_engine/`)
Evaluates technical integrity and UI rendering constraints.
- `broken_placeholder`: Missing, altered, or unescaped placeholders (`{0}`, `{1}`, `%KEY%`, `%s`).
- `markup_damage`: Broken or corrupted rich-text tags (`<b>`, `</b>`, `<color=#...>`, `<size=...>`).
- `keybinding_format`: Malformed or unlocalized shortcut tokens (`[лкм]` vs `[LMT]`, bracket mismatches).
- `overflow_risk`: String length significantly exceeds source length (>150% expansion) in short UI button/label slots where visual truncation is likely.

---

## 2. Severity Levels & Penalty Weighting

Every identified error is assigned one of four severity levels:

| Severity | Penalty Points | Description & Player Impact |
|---|---|---|
| **Neutral** | **0 pt** | Minor stylistic nuance or valid alternative phrasing. Logged for translator feedback; does **not** deduct score points. |
| **Minor** | **1 pt** | Minor typo, punctuation inconsistency, or slightly awkward phrasing that does not affect player understanding or break UI layout. |
| **Major** | **5 pt** | Distorted gameplay mechanic, inappropriate military/lore terminology, English acronym leak, or significant grammatical defect. |
| **Critical** | **25 pt (Auto-Reject)** | Game-breaking issue: corrupted engine tag/placeholder, untranslated leak, inverted game-rule instruction, or major context hallucination. |

---

## 3. MQM Quality Score Formula

The score is computed over the word count of the **exact units that were actually
reviewed** (`review_scope`), never over the full component's word count when the
review is partial. Dividing a partial-sample defect count by the full component's
word count silently assumes every unreviewed string is defect-free, which is never
verified and must not be presented as such.

$$\text{Total Penalties} = \sum (\text{Count}_{\text{Minor}} \times 1) + (\text{Count}_{\text{Major}} \times 5) + (\text{Count}_{\text{Critical}} \times 25)$$

$$\text{MQM Quality Score} = \max\left(0, 100 - \left(\frac{\text{Total Penalties}}{\text{Reviewed Sample Words}} \times 100\right)\right)$$

---

## 4. Sampling & Coverage

MQM has always been sample-based in practice — reviewing 100% of a large component is
rarely feasible. That is fine, but the sample's scope must be **declared explicitly**
and the score must be **scoped to it**, not silently extrapolated:

- **Full coverage** (`{"coverage": "full"}`): every unit in the component was reviewed.
  The resulting score is a legitimate component-wide grade.
- **Partial coverage** (`{"reviewed_unit_ids": [...]}`): only the listed units were
  reviewed — including units checked and found clean, not just units with a defect.
  The score is a **defect-density indicator over that subset only**. It answers "how
  bad are the known defects," never "how good is the whole component" — the unreviewed
  remainder is unverified, not verified-clean.
- A component can **never** receive a release-blocking-free grade (Grade A/B/C) under
  partial coverage, regardless of how high the sample score is. The correct release-gate
  read for partial coverage is always **"Blocked — audit incomplete,"** in addition to
  any Critical-defect block. Extending coverage toward 100% is the only way to earn a
  real component-wide grade.
- When reporting a partial-coverage score, always state the unit and word coverage
  percentages alongside it so a reader cannot mistake it for a full grade.

---

## 5. Quality Release Gates (Release Readiness)

These grades apply **only under full coverage** (`review_scope.coverage == "full"`).
Under partial coverage, skip straight to the "Not gradable" row regardless of score.

| Coverage / Score Range | Quality Grade | Release Recommendation | Action Required |
|---|---|---|---|
| **Partial coverage (any score)** | **Not gradable (partial coverage)** | **Blocked — audit incomplete** | Extend review to remaining units before any release grade can be issued. |
| **Full coverage, $\ge 95.0$** | **Grade A (Excellent)** | **Approved for Release** | No critical blockers. Minor polishes can be backlogged. |
| **Full coverage, $85.0 - 94.9$** | **Grade B (Good)** | **Conditional Approval** | Requires fixing all Critical and Major defects before shipping. |
| **Full coverage, $70.0 - 84.9$** | **Grade C (Fair)** | **Blocked** | Comprehensive post-editing / re-translation pass required. |
| **Full coverage, $< 70.0$** | **Fail (Poor)** | **Rejected** | Model prompt/engine overhaul and full re-translation needed. |

> **Note on Critical Defects:** Regardless of coverage or numerical score, any
> unaddressed **Critical** defect found anywhere in the reviewed sample automatically
> drops the release gate status to **Blocked** until resolved.
