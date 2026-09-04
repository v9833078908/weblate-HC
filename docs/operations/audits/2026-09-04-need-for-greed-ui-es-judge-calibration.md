# Judge calibration audit: Need For Greed / UI - Spanish

Run: `https://l10n.herocraft.com/judge-runs/36a5be4a-b2d8-42a7-a934-23eb76800737/`
(production, completed 2026-09-04, 1 h 39 min, actor Hero Craft Admin, scope
`NOT has:judge` over `need-for-greed/ui/es`).

The object of this audit is the judge, not the translation. Findings are
analyst-reviewed manually this session; the judge's verdicts are the data being
measured, never the reference.

## 1. What the run did

| Metric | Value |
|---|---|
| Units matched / checked / cached | 462 / 462 / 0 |
| Passed | 354 |
| Needs action | 108 (43 critical held, 49 major not fixed, 16 minor noted) |
| Suggested fixes generated | 92 |
| Unparsed verdicts | 0 |
| Stale verdicts | 0 |
| Seat disagreement | 0 |

Mechanically the run is clean: full coverage of the translation, no transport
failures, no stale verdicts, no cache reuse masking the result. Everything below
is about the *content* of the verdicts.

## 2. Review scope

| Metric | Value |
|---|---|
| Coverage mode | `sample` |
| Units reviewed | 177 / 462 (38.3%) |
| Words reviewed (MQM denominator) | 979 / 2094 (46.8%) |

The sample is deliberately unbalanced, because the question is judge behaviour,
not component quality:

- **all 108 flagged units** (precision), and
- **69 units from the passed bucket** (recall): a 45-unit systematic sample
  (every 8th unit by id) plus the 28 passed units the deterministic glossary
  matcher raised as candidates.

Nothing here may be projected onto the 285 units nobody opened. The MQM score
below is sample-scoped and not gradable component-wide.

Reference data used, all pulled from production: the 300-term `need-for-greed`
Spanish glossary component, and the project's OpenRouter `persona` and `style`
prompts (`/machinery/need-for-greed/openrouter/`), which are what the judge is
entitled to enforce beyond the source text.

## 3. Calibration result

Gate mapping: `none`/`minor` -> `pass`, `major` -> `flag`, `critical` -> `reject`.

| Metric | Value |
|---|---|
| Comparable units | 177 / 177 |
| Gate agreement | 110 (62.1%) |
| **Misses** (analyst Major/Critical, judge passed) | **0** |
| False alarms (judge flagged/rejected, analyst clean) | 16 |
| Judge stricter than analyst | 67 |
| Judge laxer than analyst | 0 |

| Analyst \ Judge | `pass` | `flag` | `reject` |
|---|---|---|---|
| `none` | 70 | 3 | 13 |
| `minor` | 15 | 24 | 14 |
| `major` | 0 | 22 | 13 |
| `critical` | 0 | 0 | 3 |

Per judge bucket, what the analyst found:

| Judge bucket | n | analyst critical | major | minor | clean |
|---|---|---|---|---|---|
| Critical held | 43 | 3 | 13 | 14 | 13 |
| Major not fixed | 49 | 0 | 22 | 24 | 3 |
| Minor noted | 16 | 0 | 0 | 8 | 8 |

**The single defining number: the judge held 43 strings as Critical; 3 of them
are Critical.** 30 % of the Critical bucket has no defect at all.

## 4. What the judge does well

1. **Recall is the strong side.** Across 69 reviewed passed-bucket units - the
   45-unit systematic sample and every glossary candidate the deterministic
   matcher raised - not one Major or Critical defect survived review. The judge
   is not letting real damage through.
2. **Glossary enforcement is essentially exact.** Every glossary citation in the
   Major bucket was re-verified term by term against the live glossary and all
   of them are correct: `Склад`->`Almacén`, `Плавильня`->`Horno`, `Птица`->`Ave`,
   `Взлом`->`Espacio para cofres`, `Супер слот`->`Súper espacio`,
   `Вставка`->`Inserción`, `Звездопад`->`Caída de estrellas`,
   `Заблудье`->`Bosque Perdido`, `Искатель руды`->`Buscaminerales`,
   `Проклятый предмет`->`Reliquia maldita`, `Покупатель`->`Comprador`,
   `Необычный`->`Poco común`, `Оценка`->`Evaluación`. 22 of 31 terminology
   findings in the Major bucket are confirmed Major; none is fabricated.
3. **It beats the deterministic matcher on sense disambiguation.** The stem
   matcher raised 28 glossary candidates inside the passed bucket
   (`Торговая лавка` for `Торговец`, `Искатель самоцветов` for `Искатель`,
   singular/plural pairs). The judge passed every one of them, correctly.
4. **It catches the genuinely destructive rewrites.** The three confirmed
   Criticals are real and would ship broken copy: unit 398550 replaces the
   second sentence with an invented joke, 398580 discards an entire upgrade
   description in favour of "¡La audiencia grita de alegría!", 398458 renders
   the glossary slot name `Взлом` as `Apertura`.

## 5. Where the judge is wrong

### 5.1 Severity inflation is the dominant failure mode

67 of 177 units are graded stricter than review supports and none laxer. The
distribution is one-sided: the judge has effectively collapsed "this could be
better" into `critical`. 27 of the 43 Critical-held strings are real defects
graded one or two levels too high; 13 are not defects.

Consequence on the run page: "43 строки не будут опубликованы" is, on this
evidence, roughly 3 strings that must not ship and 40 that are being held by a
gate they do not belong in.

### 5.2 Verifiable factual errors in verdicts

These are not judgement calls - each is a checkable claim the judge got wrong.

| Unit | Judge claim | Fact |
|---|---|---|
| 398369 | `Aumenta` is "formal or third-person singular", violating the tú rule | `aumenta` **is** the tú imperative; the formal form is `aumente`. The verdict inverts Spanish grammar. |
| 398738 | The object pronoun `te` was omitted from `ADOREN` | `te` is present in the target, immediately before the `<color=...>` tag. The judge read across the markup and hallucinated an omission. |
| 398372 | The glossary defines `Искатель` as the Digger class | The glossary says `Искатель` -> `Personaje`. The target `Nivel de personaje` **follows** the glossary; the judge rejected it while citing that glossary. |
| 398355 | `Descargar` primarily means "download"; the correct term is `Descarguar` | `Descargar` is the standard Spanish verb for unloading cargo. `Descarguar` is not a Spanish word. |
| 398344 | `h.` is an English abbreviation not used in Spanish | The project's own `style` prompt gives `{hours}h. |{minutes}m. |{seconds}s.` as the reference format for this exact string. The judge rejected a string for matching its own instructions. |

Add three more where the verdict is reasoning, not observation: 398438
(`Oferta del comprador` and "buys for" are the same direction), 398542 (`buscar
gemas` on a detector-duration stat), 398685 (`Disponible en:` is the normal
Spanish frame for a countdown).

### 5.3 Glossary cited correctly, applied to the wrong sense

Unit 398432, `До прибытия следующего искателя:` -> `Hasta que llegue el próximo
cliente:`. The judge correctly quotes `Искатель` -> `Personaje` and then demands
it here, where `искатель` means an arriving customer, not the player character.
The same rigidity that makes section 4.2 strong produces a false alarm the
moment a glossary term carries a second sense.

### 5.4 The judge overrode the glossary it calls law

Unit 398375, `Крафт` -> `Fabricar`. The verdict states that the glossary entry
matches, then rejects it anyway on the noun-phrase rule. The persona prompt says
"You treat the project glossary as law". A judge that flags glossary-conformant
strings makes the glossary unusable as an appeal.

### 5.5 The label/register rule is applied inconsistently

The judge flagged infinitive button labels `Seleccionar` (398594), `Proceder`
(398595), `Tomar` (398363) and `Introducir` (398719), and passed `Abrir`
(398724), `Cancelar` (398461), `Comerciar` (398642) and `Recoger todo` (398760)
in the same run. Same rule, same string shape, opposite outcomes. These do not
appear as misses in the table above only because the gate mapping sends Minor to
`pass`.

The same inconsistency hits `предмет`: `artículos` is a Major terminology defect
in 398614 and 398491, while `artículo` (398611) and `elementos` (398612) pass in
near-identical strings.

### 5.6 One category misuse

Unit 398769 is held Critical under `Пунктуация` for CRLF versus LF line endings.
That is a file-transport artifact, not a translation defect, and nothing in the
project instructions covers it. In the same string the judge missed a real minor
issue: the target mixes curly `“ ”` with straight `" "` quotes.

## 6. Reference scorecard (sample-scoped)

Not a component grade - the denominator is the 177 reviewed units.

| Metric | Value |
|---|---|
| MQM score (sample-scoped, non-projectable) | 69.05 / 100 |
| Grade | Not gradable (partial coverage); Critical present |
| Critical / Major / Minor | 3 / 35 / 53 |
| Total penalty | 303 pt over 979 reviewed words |

## 7. Recommendations

Ordered by expected effect on the judge, not on the translation.

1. **Fix severity calibration first.** It is the whole problem. Anchor the prompt
   with worked examples: Critical only where the player is actively misled or
   the string is functionally broken (398550, 398580, 398458 are three usable
   ones); Major for glossary violations and mechanic distortion; Minor for
   register, number agreement and phrasing.
2. **Make the glossary terminal.** If a target matches the glossary, no verdict
   may reject it on a style rule (398375). Style rules apply where the glossary
   is silent.
3. **Require a quotation for every claim of omission or a missing word.** Both
   398738 and 398372 would have failed that check.
4. **Bind glossary terms to a sense.** A term must be enforced only where the
   source explanation or key supports that sense (398432).
5. **State the label/register rule as a checkable rule, or drop it.** The current
   split between flagged and passed infinitives is arbitrary, and it is what
   produced the largest group of over-severe Criticals.
6. **Remove whitespace and line-ending observations from the judge's scope.** The
   `LineSeparatorSpacing` autofix and the `game-*` checks own that layer.
7. **Recheck the source.** Several verdicts are correct about the target and
   still not the translator's fault: the persona declares English as the source
   language while the actual source is Russian, and the "labels, not commands"
   rule is broken by Russian imperative sources (`Подкрути здоровье!`,
   `Разгони кирку!`).
8. **Re-run after the prompt change and compare bucket sizes on this same
   translation.** This audit's per-bucket table is the baseline.

## 8. Reproduction

```sh
python .claude/skills/weblate-lqa/scripts/audit_component.py \
  --url https://l10n.herocraft.com --project need-for-greed --component ui --lang es \
  --verdicts verdicts.json --judge-export judge_export.json --no-glossary
```

`judge_export.json` was reconstructed from the run page's outcome buckets
(`?outcome=critical|major|minor|passed|unparsed|stale-conflict`), joined to unit
ids through `checksum` = `hash_to_checksum(id_hash)`; all 462 rows matched, and
the bucket totals reproduce the run page exactly.
