# Phase 2 report - need-for-greed source language en -> ru (2026-09-02)

Inputs: `kit-original/` (fresh ZIP, Phase 1, downloaded after the
commit-on-download side effect), `phase0.json` (DB truth),
`orders-lost-keys.json` (9 keys). Script:
`analysis/probes/nfg-ru-source-prepare.py`. Working copy: `kit-work/`.

## 2.1 Lost keys (orders)

- 9 keys from `orders-lost-keys.json` added to `orders/ru.po` with the
  Russian text recovered from the change log (`details.source`), each
  inserted right after its sibling (`...Title`/`...Description`/`...Thank`)
  of the same step prefix. All 9 found their anchor; none appended at EOF.
- Same 9 keys added to the other 17 `orders/*.po` (incl. `en.po`) with an
  empty `msgstr` (9 x 17 = 153 insertions).
- Round-trip check: re-parsing `orders/ru.po` with translate-storage
  reproduces all 9 texts byte-exact (multi-line wrapping is cosmetic only).
- Conflicts: 0. None of the 9 keys was already present in the fresh kit.
- EN/other languages are filled by machine translation from the ru source
  in Phase 5, not manually.

## 2.2 Homoglyphs (ui/en.po)

- `dailyRewardClaimDescription`: `Х2` (Cyrillic Х) -> `X2` (Latin X).
- `terrainProgressInfoSlide2`: `ADORЕ` (Cyrillic Е) -> `ADORE`.
- `ui/ru.po` left untouched (`Х2` stays as the legitimate Russian text).
- After the fix, `en.po` in the kit has zero Cyrillic in all 7 components.

## 2.3 Other .po files

Untouched. `Language:` headers already correct (verified in all 18 files
per component; counts per component re-verified after the run: every file
parses, unit counts equal Phase 0 + 9 for orders only).

## 2.4 Glossary (rebuilt from DB, not from old TBX)

- 17 files `tbx/<lang>.tbx` rebuilt from `phase0.json` for the ru-source
  layout: source `ru`, targets `en` + 16 others (17 files; the old kit had
  17 files with `en` as source and no `en.tbx`). `ru.tbx` deleted,
  `en.tbx` created: the old English source term becomes the `en`
  translation.
- Per term: `<descrip>` = source-unit explanation from the DB (302/302,
  of which 301 are the deterministic `[Section] key` template and 1 is a
  real note, `Ancient Tome`); `<note>` (translator origin) = target
  explanation - 0 across all languages, so none written.
- Flags: `weblate-flags` copied from the DB (all 302 are `terminology`).
- 4 homonym groups separated by section (section = old `en` term, which is
  the member-unique part): Белоземье (Belomar/Whitelands), Сундук (Buried
  Chest/Chest), Сундук Конунга (Chest of the Jarl/Jarl Chest), Звездопад
  (Meteorfall/Starfall). Result: 302 distinct contexts per file, nothing
  folded.
- Parse-back with the same stack: 0 errors; terms = 302 = DB source units;
  descrips = 302 = DB explanations; contexts unique in all 17 files.

## 2.5 Output zips

`kit-ru-source/{buyers,characterdialogue,loot,orders,survey,tutorial,ui}.zip`
(18 `*.po` each, flat) and `kit-ru-source/glossary.zip` (17
`tbx/*.tbx`). 8 zips total.

## Open questions for the owner (blocking Phase 3+)

1. Global machinery `routing`: `resolve_model('en')` (and the `en` entry in
   `language_instructions`) is not readable through the API and needs shell
   access or an owner check before Phase 5 auto-translate. The project
   override (openrouter persona/style) was confirmed.
2. Phase 5 state question: after auto-translate from the ru source, should
   non-ru languages land as suggestions or as `needs-editing` translated
   state? Space Arena precedent used plain machine translation into files.
3. The 2026-09-01 log removed 29 distinct `HelpInfo` keys (522 events), but
   per owner instruction only the 9 keys from `orders-lost-keys.json` were
   carried. The remaining 20 keys had empty ru targets in the old state and
   their en text is not in the kit - confirm they are genuinely obsolete.
4. `ui` has `check_flags = max-length:110` added 2026-09-01 11:33; it will
   be replicated on `ui-ru` in Phase 3.
5. The add-then-delete incident (2026-09-01, every `orders` addition was
   immediately removed by "Resource updated", cause unknown) mandates the
   Phase 3.4 probe: add a source string on `buyers-ru`, confirm it survives,
   delete it, before recreating the rest.
