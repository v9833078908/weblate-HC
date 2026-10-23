# ST2 summer RU glossary template plan

**Goal:** Extract terminology-only Russian entries from `/Users/eli/Downloads/ST2 summer update locales.xlsx` into a Traditional Chinese glossary template.

**Output:** `/Users/eli/Downloads/ST2-summer-glossary-template.csv`

**Columns:** `ru`, `zh_TW`, `note`.

**Selection:** Include only direct, named game entities and mechanics: the new mine unit, division-experience ranks and feature name, trade-agreement mechanic, and named trade countries. Exclude full UI sentences, events, tutorial instructions, offers, statuses, and generic labels.

**Target handling:** `zh_TW` remains intentionally empty because the source workbook contains Russian only. The output is a translation worksheet, not an importable TBX glossary until every target cell is populated.

**Out of scope:** Inventing Chinese translations, adding terms derived only from prose, or uploading to the running Weblate instance.
