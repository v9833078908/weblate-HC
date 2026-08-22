# Weblate Quality Checks & Diagnostic Guide

This reference provides a definitive guide to interpreting, debugging, and resolving quality checks in the Weblate translation management system for game localization projects.

---

## 1. Built-in Weblate Quality Checks

### 1.1. `same` (`SameCheck` — `weblate/checks/same.py`)
- **What it checks:** Tests whether a translation unit's source is identical to its target (`source == target`).
- **Typical Causes:**
  - *True Positive (Defect):* The string was accidentally left untranslated or machine translation failed to translate the string.
  - *False Positive (Benign):* The string is a brand name, service, proper noun, technical acronym, or untranslatable product name (e.g. `Discord`, `Luger P08`, `MG-34`, `Facebook`).
- **Remediation:**
  - If untranslated: translate the string.
  - If valid identical proper noun: add the flag `ignore-same` (or `read-only`) to the unit's `extra_flags`.

---

### 1.2. `multiple_capital` (`MultipleCapitalCheck` — `weblate/checks/chars.py`)
- **What it checks:** Scans the target for sequences of 2 or more uppercase letters (`\p{Lu}{2,}`) that do not appear in uppercase in the source string.
- **Typical Causes:**
  - *True Positive (Defect):* Accidental shouting/caps-lock typos in translation (e.g. `CLick HERE`).
  - *False Positive (Benign):* Localized UI control keybindings and abbreviations where the source was written in lowercase (e.g. Russian `[лкм] / [пкм]` translated as German `[LMT] / [RMT]` or English `[LMB] / [RMB]`).
- **Remediation:**
  - If typo: fix target casing.
  - If valid keybinding acronym: add the flag `ignore-multiple-capital` or normalize the source string to uppercase (e.g. `[ЛКМ]`).

---

### 1.3. `reused` (`ReusedCheck` — `weblate/checks/consistency.py`)
- **What it checks:** Flags when **different source strings** within the same component share the **exact same target string** (`Unit.objects.same_target(unit).exists()`).
- **Typical Causes:**
  - *True Positive (Mistranslation / Context Hallucination):* Two distinct game concepts were erroneously merged (e.g. `Unit_DriverVermaht` with source `Стрелок` translated as `Fahrer` because the context key contained `Driver`, colliding with `Unit_DriverRKKA` `Водитель` $\to$ `Fahrer`).
  - *True Positive (Domain Confusion):* `ButtonOutVehicle` (`Выгрузить`) translated as `Entladen` (cargo/weapon), colliding with `ButtonDischarge` (`Разрядить`) $\to$ `Entladen`.
  - *False Positive (Grammatical Convergence):*
    - Singular and Plural identity in target language (e.g. German *der Sanitäter* [sg] vs *die Sanitäter* [pl] both translating `Санитар` and `Санитары`).
    - Gender neutralization (e.g. German predicate participle `zerstört` for both Russian masculine `уничтожен` and neuter `уничтожено`).
- **Remediation:**
  - If mistranslation: fix the target translation so each concept has its proper term.
  - If legitimate linguistic convergence: add the flag `ignore-reused` to the affected units.

---

### 1.4. `inconsistent` (`ConsistencyCheck` — `weblate/checks/consistency.py`)
- **What it checks:** Flags when the **exact same source string** has different translations in different units across the component.
- **Typical Causes:**
  - *True Positive (Defect):* Accidental synonym usage or inconsistency in UI buttons.
  - *False Positive (Benign):* Homographs where context dictates different meanings (e.g. `Tank` = armoured vehicle vs fluid container; `Lead` = metal vs guide).
- **Remediation:**
  - If inconsistent: align to the approved glossary.
  - If legitimate homograph: add the flag `ignore-inconsistent`.

---

## 2. Custom Game Checks (`weblate_customization/checks.py`)

### 2.1. `game-markup` (`GameMarkupCheck`)
- **What it checks:** Ensures Unity rich-text tags (`<color=#RRGGBB>`, `<size=N>`, `<b>`, `<i>`, `<link>`, `<sprite name="...">`) in target match the source multiset exactly.
- **Remediation:** Fix broken or missing opening/closing tags. Flag: `ignore-game-markup`.

### 2.2. `game-line-break` (`GameLineBreakCheck`)
- **What it checks:** Ensures the Hero Craft engine line separator `$` is neither lost nor added, and that no whitespace hugs it tightly.
- **Remediation:** Remove whitespace around `$` or restore missing `$`. Flag: `ignore-game-line-break`.

### 2.3. `game-token` (`GameTokenCheck`)
- **What it checks:** Validates that engine substitution identifiers (`item_type[|{0}]`, `skirmish_league_id[gen|в {0}]`) survive into target. Brackets without `|` are treated as normal prose.
- **Remediation:** Restore the exact lookup key before the bracket. Flag: `ignore-game-token`.

### 2.4. `game-number` (`GameNumberCheck`)
- **What it checks:** Ensures all numbers from source appear in target (with normalized decimal separators and date handling).
- **Remediation:** Fix missing or altered numbers. Flag: `ignore-game-number`.

### 2.5. `cyrillic-leak` (`CyrillicLeakCheck`)
- **What it checks:** Flags any Cyrillic characters appearing in non-Cyrillic target languages (EN, DE, FR, ES, JA, ZH, etc.).
- **Remediation:** Translate remaining Cyrillic text. Flag: `ignore-cyrillic-leak`.

### 2.6. `game-length` (`GameLengthCheck`)
- **What it checks:** Flags strings that exceed maximum pixel or character length thresholds for the given UI slot.
- **Remediation:** Shorten target or use standard abbreviations. Flag: `ignore-game-length`.

---

## 3. Flag Remediation Matrix

| Check ID | Ignore Flag Name | When to Apply |
|---|---|---|
| `same` | `ignore-same` | Valid proper nouns, brand names, untranslatable weapon models (`Discord`, `Luger P08`). |
| `multiple_capital` | `ignore-multiple-capital` | Valid localized uppercase shortcuts (`[LMT]`, `[RMT]`, `[LMB]`). |
| `reused` | `ignore-reused` | Valid grammatical convergence (e.g. identical singular/plural nouns or uninflected participles). |
| `inconsistent` | `ignore-inconsistent` | Valid homographs requiring different context translations. |
| `end_stop` | `ignore-end-stop` | Valid abbreviations with trailing dots (`Porta-av.`, `St.`). |
| `game-number` | `ignore-game-number` | Valid linguistic restructuring of ordinal numbers. |
| `game-token` | `ignore-game-token` | Intentional divergence in engine substitution syntax. |
| `game-line-break` | `ignore-game-line-break` | Exceptions to the tight `$` separator rule. |

### How to apply flags via Weblate API
To add a flag without losing existing flags, format the comma-separated `extra_flags` field on the unit:
```http
PATCH /api/units/{unit_id}/
Authorization: Token {token}
Content-Type: application/json

{
  "extra_flags": "ignore-same"
}
```
