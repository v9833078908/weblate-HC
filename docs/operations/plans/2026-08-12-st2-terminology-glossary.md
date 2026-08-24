# ST2 terminology glossary Implementation Plan

**Goal:** Replace the broad ST2 glossary extraction with a terminology-only CSV suitable for the Weblate **Use as glossary** upload path.

**Input:** `/Users/eli/Downloads/ST2-loc.xlsx`

**Output:** `/Users/eli/Downloads/ST2-glossary.csv`

**Columns:** `en`, `zh_CN`, `note`. The note records the originating worksheet and engine key.

## Task 1: Identify eligible terminology families

Extract only named game entities and terminology families, such as countries and alternate states, regions, units, ideologies, leaders, government roles and forms, scenarios, technologies, game modes, card names, and resources. Reject generic UI labels, instructions, event text, mission labels, descriptions, questions, and offers.

## Task 2: Build the glossary

Produce one row per English term. Keep the first populated Chinese translation, deduplicate case-insensitively by English term, and merge source notes for duplicate terms.

## Task 3: Validate the upload profile

Run `infer_glossary_profile` against the CSV and confirm a schema-v2 TBX `record-map` profile with `en` as source, `zh_CN` as target, and `note` as the source explanation.

## Out of scope

- Creating translations missing from the original kit.
- Retaining full-sentence localization units or generic UI labels.
- Editing or deploying the running Weblate instance.
