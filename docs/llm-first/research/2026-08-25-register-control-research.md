# Register flattening in LLM machine translation: mechanisms, evidence, and what to measure first

Date: 2026-08-25. Research document, no implementation. Motivated by
`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md` §6.1, where
**124 of 414** analyst-confirmed defects across nine target languages are register
defects — the largest single class, present in every language.

The audit describes the symptom: the Russian deliberately contrasts Yaeko's refined
speech against the crude register of Leon, the guard, the fisherman and Unagi, and the
MT neutralises the contrast in both directions at once. Obscenity is softened
(`пиздец` -> `verdammt`, `ЕПТ` -> `くそっ`), obscenity is invented where the source had
only capitalised emphasis (`zh_Hans` adds `他妈`), and refined characters are given
street register (`en` gives Yaeko `ain't no`; `ja` gives her `お前ら`, `だぜ` and the
masculine `俺`).

This document answers three questions: what control already exists, why it did not
work, and what has to be measured before anything is built.

## 1. Four channels already carry context into the prompt

All four are implemented in `main` and, by their own code paths, are expected to be
active for `heart-abyss` production runs. The exact request payloads of the
2026-08-24 run were **not** re-fetched for this document — the API token in
`weblate-mcp/.env` authenticates only against the dev instance, and production
returned 401 — so the strongest available evidence for the production shape is the
archived payload in
`docs/operations/audits/2026-08-20-heart-abyss-hub-1-translation-qa.md:584-592`.

| Channel | Granularity | Code | What it carries for `heart-abyss` |
| --- | --- | --- | --- |
| `persona` + `style` | site -> project, field by field | `weblate/machinery/llm.py:1198-1201`, `weblate/machinery/forms.py:481-502`, `weblate/trans/models/project.py:1332-1351` | the setting (Hallspeak, 18th-century Japan, yokai). The fields are plain `CharField(Textarea)` with **no length limit** |
| `language_instructions` | target language | `weblate/machinery/llm.py:379-383`, `weblate/machinery/forms.py:503-566` | typography only; capped per entry by `LLM_LANGUAGE_INSTRUCTION_LENGTH` |
| `note` per string | unit | `weblate/machinery/llm.py:528-537`, prompt rule 26 at `weblate/machinery/llm.py:202` | **the speaking character**, 395 of 396 units |
| glossary `source_explanation` / `target_explanation` | term, only when matched in the string | `weblate/glossary/models.py:425-431`, prompt schema `weblate/machinery/llm.py:112-113` | name canon and character gender (audit §13.5) |

The speaker chain is verifiable end to end without touching production.
`infer_component` routes **any** populated non-key, non-language column into
`comments` (`loc_kit_ingest/infer.py:337-385`); `writer.py:52-57` writes each one as
`addnote(origin="developer")` into the source-language PO; the result is visible on
disk in `dev-docker/data/vcs/heart-abyss/temple/ru.po`, which opens with `#. Joe`,
`#. Leon`, `#. Ray` and carries 555 developer comments. The producer's kit supplies
it: `~/Downloads/Heart Abyss_Localization - HUB 1 .csv` has the column layout
`id, Character, ru, en, fr, it, de, ja`.

Prompt rule 26 is explicit about the intent: *"The `note` field carries developer
context about the string, such as the speaking character … Use it to choose register,
gender agreement, and tone."* It landed in `6e94ea3` and `b00e6a1`, both dated
**2026-08-07** — seventeen days before the run the audit measured.

One trap for anyone re-reading the corpus files:
`analysis/data/heart-abyss-hub-1-metrics.json` reports `"comments": 0`, which sits
beside `"suggestions": 0` and counts Weblate discussion comments, not developer
notes. It does not contradict the `note: "Ray"` payload.

## 2. Why the existing channels did not prevent the defect

**A name is not a specification.** No channel states *which* register belongs to
Yaeko and which to Unagi. The prompt instructs the model to choose a register from
the note and then supplies a bare token, `"Ray"`. The model fills the gap by
inference, and inference regresses to the mean.

**`persona` describes the game, not the voices.** For COL4 the persona already
contains an explicit ban on softening crude strings, and it was measured travelling
into the request intact (`docs/llm-first/measurements/2026-08-11-col4-fr-autotranslate-report.md:58-60`).
For `heart-abyss` the same field holds a setting description. The mechanism is proven
in this fork; the content was never written for this game.

**A batch mixes characters.** `batch_size = 10` (`weblate/machinery/llm.py:345`) and
`_get_pending_batch` walks pending keys in order, deduplicating identical text and
ignoring `note` entirely (`weblate/machinery/base.py:1135-1148`). One request can hold
Yaeko and Leon under a single global style instruction.

**The judge cannot surface register even when it finds it.** Its own rubric files
register under the lowest severity — `judge_prompts/verdict.txt:21-31`:
*"minor - style, register or awkward phrasing that does not change meaning"* — and
`weblate/trans/models/judge.py:215-245` maps minor to `pass`. No `judge-flag`, no
queue, no gate. Register precision has never been measured: across the whole judge
measurement history it appears as five findings in one 124-unit run
(`docs/llm-first/measurements/2026-08-14-st2-zh-judge-run.md:57-59`), against overall
collegium precision 0.51-0.56 and recall 0.67-0.75
(`docs/llm-first/measurements/2026-08-19-severity-recalibration-final.md`).

## 3. Measurement on this corpus

Run against `analysis/data/heart-abyss-hub-1-units.tsv` (396 units, `ru` source with
`en` and `fr` targets), offline, no model call. The lexicon is reproduced verbatim
below so the numbers can be recomputed; it is applied with `re.I` and a leading `\b`.

```
ru: бля\w*|хуй\w*|хуё\w*|хер(?:ов|ня|ню|ни)\w*|хрен\w*|пизд\w*|[её]б\w*|заеб\w*
    |сук[аиуе]\b|говн\w*|дерьм\w*|жоп\w*|ср[ае]т\w*|сран\w*|мраз\w*|гнид\w*|ебен\w*
    |[её]пт\w*|нахрен|охрен\w*|нажир\w*|поп[её]рл\w*|мудак\w*|мудил\w*|похуй|пох\b
    |долбо\w*|тварь|сволоч\w*|мерзав\w*
en: fuck\w*|fuckin|shit\w*|damn\w*|goddamn\w*|hell\b|ass\b|asshole\w*|bastard\w*
    |bitch\w*|crap\w*|piss\w*|pissin\w*|screw(?:ed|ing)?\b|bloody\b|freakin\w*
    |frickin\w*|dick\b|prick\b|scumbag\w*|bugger\w*
fr: putain\w*|merde\w*|merdique|connard\w*|conne\b|con\b|cons\b|conneries?\b|bordel\b
    |foutre|foutu\w*|fous\b|fout\b|chier|chiant\w*|salope\w*|salaud\w*|b[âa]tard\w*
    |encul\w*|niquer?\b|cr[ée]tin\w*|emmerd\w*|saloperie\w*|pétasse\w*|bougre\b
```

| Quantity | `ru` | `en` | `fr` |
| --- | --- | --- | --- |
| units carrying a crude marker | 13 | - | - |
| of those, marker preserved | - | 8 / 13 | 10 / 13 |
| units with a target marker where the source has none | - | 13 | 8 |
| total markers in the component | 13 | 20 | 18 |

Markers per 100 units by speaker:

| Speaker | units | `ru` | `en` | `fr` |
| --- | --- | --- | --- | --- |
| Leon | 121 | 9.9 | 13.2 | 14.0 |
| Ray | 82 | 0.0 | 0.0 | 0.0 |
| **Yaeko** | 34 | **0.0** | **5.9** | 0.0 |
| Unagi | 26 | 0.0 | 3.8 | 3.8 |
| Ichiro | 16 | 6.2 | 6.2 | 0.0 |

Three readings, and the third matters most.

1. **Preservation of 61 % (`en`) and 77 % (`fr`) is normal, not anomalous.** A
   subtitle study of 1,862 examples reports preservation of 51.7-63.1 %
   ([Vestnik](https://journals.uni-lj.si/Vestnik/article/view/9762)); a literary study
   finds 55 % of profanity omitted and 40 % softened, with characterisation altered
   ([JoLL](https://doi.org/10.24071/joll.v19i2.2119)).
2. **Invented markers at 3.3 % (`en`) and 2.0 % (`fr`) sit inside the published
   added-toxicity range** of 0-5 % measured over 164 languages
   ([Findings EMNLP 2023](https://aclanthology.org/2023.findings-emnlp.642/)).
3. **A lexicon detector for this class is not viable yet, and the same measurement
   shows why.** The first pass of this probe used bare substrings and reported 58
   crude sources and 48 softenings — almost all noise: `еб` matched `тебе`, `con`
   matched `conclu` / `consultez` / `contact`, `cul` matched `Cul-de-sac`. That is the
   documented glossary failure repeating exactly (`НИИ` inside `предназначении`,
   `docs/llm-first/measurements/2026-08-11-glossary-enforcement-analysis.md:339-346`).
   With boundaries fixed, hand review of the 13 `en` candidates leaves roughly three
   clear escalations (`фигня` -> `what the fuck`, `Мне все равно` -> `I don't give a
   shit`, `Без понятия` -> `No fuckin' idea`), about four arguable, and about six
   false — and the false ones come from **source-side lexicon gaps**: `херь`,
   `охеревший`, `дрянь` and `чёрта` are absent from the list, so faithful
   compensation looks like invention. Precision 25-50 %, the same band as mechanisms
   this fork has already rejected.

The Yaeko row is the clearest single number in the table, and it needs the same
honesty: her two `en` markers translate `испоганишь` and `мелкая дрянь`, which are
harsh in Russian but absent from the lexicon. The direction of the finding survives;
its magnitude is confounded by source-side recall. A validated measurement needs a
register-graded lexicon per language, and no such resource exists for `ru` into our
nine targets.

## 4. External state of the art

| Mechanism | Measured strength | Applicability here |
| --- | --- | --- |
| Side constraints / register tags in NMT | 96-98 % politeness control en->de ([Sennrich et al.](https://aclanthology.org/N16-1005/)) | needs training, unavailable to us |
| CoCoA-MT contrastive fine-tuning | M-Acc 81.8 % in-domain from 400-1,000 labelled examples ([Findings NAACL 2022](https://aclanthology.org/2022.findings-naacl.47/)) | en -> de/es/fr/it/ja/hi; `ru` source not covered |
| Zero-shot formality transfer | formal 98.8 % vs **informal 51.6 %** ([IWSLT 2023](https://aclanthology.org/2023.iwslt-1.30/)) | the asymmetry runs against us: lowering register is the harder direction |
| **Prompting an LLM for formality** | M-Acc .86 (GPT-3) / .83 (ChatGPT), compliance 98.9-100 % ([WMT 2023](https://aclanthology.org/2023.wmt-1.49/)) | our actual instrument; validated on Japanese |
| **Few-shot exemplars outweigh instructions** | 25k runs, 6 models, 12 pairs ([arXiv 2401.12097](https://arxiv.org/html/2401.12097v3)) | strongest available lever, but needs approved examples first |
| Speaker metadata in a language model | perplexity -5.4 % / -6.5 % ([LREC 2024](https://aclanthology.org/2024.lrec-main.1202/)) | indirect. **No study compares a global persona against per-string speaker instructions on game dialogue** |
| Scene context (manga MT) | +4.24 BLEU; more than one preceding scene **degrades** ([LREC 2024](https://aclanthology.org/2024.lrec-main.1505/)) | bounds any appetite for long context |
| Japanese honorific classification | F1 .91 in-domain, .81 under genre shift ([ANLP 2023](https://anlp.jp/proceedings/annual_meeting/2023/pdf_dir/D2-5.pdf)) | detection only, and licensing is unclear |

Commercial precedent, and its ceiling: DeepL exposes a request-level `formality`
parameter over a fixed language list
([docs](https://developers.deepl.com/api-reference/translate/request-translation))
and request-level [custom instructions](https://developers.deepl.com/docs/customize/custom-instructions)
capped at **10 instructions of 300 characters** for de/en/es/fr/it/ja/ko/zh, with no
speaker field; Amazon Translate offers `FORMAL`/`INFORMAL` per request and profanity
masking that its own documentation calls literal rather than contextual;
[Lingo.dev brand voices](https://lingo.dev/en/docs/platform/brand-voices) inject
exactly one locale-specific voice text per request;
[Smartling Style Rules for AI](https://help.smartling.com/hc/en-us/articles/41970369123227-Style-Rules-for-AI)
attach at account or package level per locale pair. **No vendor offers per-speaker
register control.** This fork already ships the same class of knob one level down:
`DeepLMachineryForm.formality` at `weblate/machinery/forms.py:425-439`.

The closest external system is not a TMS at all. The Unreal dialogue plugin
[SUDS](https://github.com/sinbad/SUDS/blob/master/docs/LocalisationTranslatorComments.md)
writes `Speaker`, `SpeakerGender` **and** `ListenerGender` into PO comments
automatically. We have the first; Japanese and Korean address systems need the third.

Rating boards constrain the answer from the other side: ESRB separates mild/moderate
from explicit/frequent profanity ([ratings guide](https://www.esrb.org/ratings-guide/)),
PEGI splits bad language across 12/16/18 ([labels](https://pegi.info/what-do-the-labels-mean)),
and [CERO](https://www.cero.gr.jp/en/publics/index/17/) reviews all stored
expressions and may withhold a rating over banned ones. Fidelity to obscenity is
therefore a **per-market** parameter, and belongs beside the language, not beside the
character.

## 5. Recommendation: one voice declaration, two consumers

The architectural point: a register specification is needed by the generator **and**
by any future detector, and it must be a single object — exactly as the glossary today
feeds both the translator and the judge. Building a detector before the declaration
exists is incoherent, because nothing tells it whether crudeness in a given line is
authorial or a defect.

Keep the dimension at O(C+L), not O(C x L): *who a character is* stated once in the
source language, *how that register is realised* stated once per target language.

| # | Change | Code | Basis |
| --- | --- | --- | --- |
| **V0** | A voice table for the ~15 characters, plus an explicit obscenity-fidelity rule, written into the project `persona` / `style` | none | the fields are unbounded (`weblate/machinery/forms.py:481-502`) and COL4 proves such a ban reaches the request |
| **V1** | Per-language realisation rules in `language_instructions`: `ja` pronoun and copula tiers (`俺`/`私`, `だぜ`/`です`), `ko` speech levels, `de`/`fr`/`it`/`es` T-V and imperative number, `en` contractions and eye-dialect | none | `weblate/machinery/forms.py:503-566`; LLM formality prompting measures M-Acc .83-.86 |
| **V2** | Enrich the kit itself: `Character` becomes `Ray \| polite, no profanity`, or a second note column | none | `loc_kit_ingest/infer.py:337-385` already routes any column into `#.` |
| **V3** | `character_instructions`: a keyed map from `note` value to instruction, modelled on `language_instructions`, injected in `_get_string_context` | small | the suggestion cache invalidates itself, because `get_translation_cache_parts` hashes that context (`weblate/machinery/llm.py:1118-1137`). Bound count and length as DeepL does, 10 x 300 |
| **V4** | Per-character few-shot exemplars drawn from approved strings | medium | strongest in the literature, but only after a first human pass exists |
| **V5** | Group a batch by speaker | medium | `weblate/machinery/base.py:1135-1148`. Benefit **unproven**; the manga result warns that more context can hurt |

V0 through V2 change no code and are individually reversible. V3 is the product form
of V0 and V2 and mirrors an existing, already-validated pattern rather than adding a
new abstraction.

Detection stays a later, separately gated safeguard, never part of this step:

1. the judge rubric has to change first, or register remains invisible by construction
   (§2);
2. candidate deterministic signals — obscenity added where the source has none, `俺` /
   `お前` / `てめえ` against a "refined" declaration, T-V drift inside one scene — all
   read the same declaration V0-V3 produces;
3. nothing is enabled before its false-positive rate is measured, per the precedent in
   `docs/llm-first/measurements/2026-08-11-glossary-enforcement-analysis.md`.

## 6. Measurement gates

- **The gold set has to be rebuilt.** `/tmp/lqa/verdicts_full_<lang>.json` and
  `scored_full_<lang>.json` are gone, and no per-unit verdict file for `heart-abyss`
  survives under `analysis/` or `.omp/skills/weblate-lqa/`. The 124 findings exist
  only as prose in the audit.
- **Production access is missing in this session.** `l10n.herocraft.com/api` answers
  401 with the token in `weblate-mcp/.env`; the dev instance holds only `temple` and
  `terms`, so `hub-1` cannot be re-queried locally. Any live measurement needs a
  working token.
- **V0 is measurable for free** on the 396 units of `ru` -> `en`/`fr`: same corpus,
  same engine, the only difference being the text of `persona`. A judge pass over
  `hub-1` costs about $1.4 per 1,000 strings by the earlier measurement
  (`docs/operations/audits/2026-08-20-heart-abyss-hub-1-translation-qa.md` §15.1).
- **Sample size.** For a precision estimate at +/-5 pp with 95 % confidence the
  worst case needs 384 labelled firings; the finite-population correction against
  N = 3,564 brings that to 347. Recall is the harder side: 124 defects is 3.5 %
  prevalence, so positives must be stratified and enriched and the result reported as
  weighted precision and recall with bootstrap intervals. Correct any observed rate
  with Rogan-Gladen, `θ = (p̂ + q₀ - 1) / (q₁ + q₀ - 1)`
  ([arXiv 2511.21140](https://doi.org/10.48550/arxiv.2511.21140)).
- **Double annotation is mandatory.** Register is the weakest MQM category for
  agreement: Cohen κ of .37 / .20 / .22 / .27 against an overall κ near .51
  ([Klubicka et al.](https://ufal.mff.cuni.cz/pbml/108/art-klubicka-toral-sanchez-cartagena.pdf)),
  and style scores Pearson .52 against .86 for content
  ([ar5iv 2204.07549](https://ar5iv.labs.arxiv.org/html/2204.07549)). A single
  annotator proves nothing here.
- **A per-segment verdict is the wrong unit of observation.** Speaker verification
  from isolated utterances reaches only 41-60 % even for humans
  ([ACL 2024](https://aclanthology.org/2024.acl-long.307.pdf)). Character and scene
  are the units; §3's per-speaker density table is the shape a metric should take.
- **Split the category before counting it.** [MQM-Core](https://themqm.org/the-mqm-full-typology/)
  separates `Language register`, `Grammatical register` (its own example is `du` for
  `Sie`), `Unjustified euphemism` (source offensiveness watered down) and `Offensive`
  (target offensive where the source is not). The first two are rule-reachable; the
  last two are not. Pooling them into one "register" bucket destroys the signal that
  decides which mechanism applies.

## 7. What not to do

- **A naive obscenity lexicon check.** §3 rejects it: 25-50 % precision, and it fails
  both on word boundaries and on source-lexicon recall.
- **Formality classifiers as a gate.** Validated models cover only en/fr/it/pt
  ([s-nlp/mdeberta-base-formality-ranker](https://huggingface.co/s-nlp/mdeberta-base-formality-ranker),
  accuracy .799); Japanese has a published classifier with unclear licensing and
  genre shift; Korean has only a rule-derived classifier validated against its own
  rules; **`zh_Hans` and `zh_Hant` have no usable detector at all**. The metric
  meta-study is blunt about the failure mode: binary classifiers fail to identify the
  best system 59 % of the time ([EMNLP 2021](https://aclanthology.org/2021.emnlp-main.100.pdf)).
- **Widening context speculatively.** The manga result degrades beyond one preceding
  scene, and our own judge plan already scopes dialogue context to the immediate
  neighbours (`docs/llm-first/plans/2026-08-20-judge-dialog-context.md:71-99`).
- **Treating obscenity fidelity as one global rule.** Rating boards make it a
  per-market decision (§4).

## 8. Open decisions

1. **Production credentials**, or permission to load `hub-1` into the dev instance.
   Without either, everything touching live `hub-1` data stays an offline analysis of
   the `ru`/`en`/`fr` columns in `analysis/data/`.
2. **Who authors the voice table.** Fifteen characters, one or two lines each. It is
   authorial knowledge and not derivable from the corpus; §3 can seed it (Leon holds
   every obscenity marker in the component, Yaeko holds none in the source) but the
   canon is the producer's call.
3. **Obscenity-fidelity policy per market**, as a `language_instructions` entry rather
   than a character attribute.
4. **Whether V3 becomes a plan.** V0-V2 need no code and no plan; V3 onwards do, under
   `docs/llm-first/plans/`.

## 9. Roadmap placement

`docs/llm-first/vision/llm-first-product-architecture.md` has no phase that owns
register control at generation time. Phase 0 owns measurement, Phase 2 the judge,
Phase 4 context expansion. Nothing in the repository proposes per-character or
per-scene style control: `2026-08-20-judge-dialog-context.md:64-65` explicitly
excludes speaker labels from its scope, and `2026-08-17-session-canon.md` is scoped to
per-run term consistency. This document is therefore Phase 0 work — measure the
mechanism before a phase adopts it — and V3 would be its first candidate for Phase 2
or Phase 4.
