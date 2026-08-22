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
- `grammar_syntax`: Grammatical errors, wrong case endings, incorrect agreement, or awkward word order.
- `spelling_orthography`: Typos, wrong capitalization (e.g. uncapitalized German nouns or incorrect compound spacing *Deppenleerzeichen*).
- `register_tone`: Inconsistent formality (mixing polite *Sie* with informal *Du*, or breaking character voice in narrative letters).
- `punctuation`: Missing or malformed terminal punctuation, quotes, or dashes violating target locale conventions.

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

The overall quality score of a translation component is computed over the entire word count of the evaluated sample:

$$\text{Total Penalties} = \sum (\text{Count}_{\text{Minor}} \times 1) + (\text{Count}_{\text{Major}} \times 5) + (\text{Count}_{\text{Critical}} \times 25)$$

$$\text{MQM Quality Score} = \max\left(0, 100 - \left(\frac{\text{Total Penalties}}{\text{Total Source Words}} \times 100\right)\right)$$

---

## 4. Quality Release Gates (Release Readiness)

| Score Range | Quality Grade | Release Recommendation | Action Required |
|---|---|---|---|
| **$\ge 95.0$** | **Grade A (Excellent)** | **Approved for Release** | No critical blockers. Minor polishes can be backlogged. |
| **$85.0 - 94.9$** | **Grade B (Good)** | **Conditional Approval** | Requires fixing all Critical and Major defects before shipping. |
| **$70.0 - 84.9$** | **Grade C (Fair)** | **Blocked** | Comprehensive post-editing / re-translation pass required. |
| **$< 70.0$** | **Fail (Poor)** | **Rejected** | Model prompt/engine overhaul and full re-translation needed. |

> **Note on Critical Defects:** Even if the numerical MQM score is $\ge 95$, any unaddressed **Critical** defect automatically drops the release gate status to **Blocked** until resolved.
