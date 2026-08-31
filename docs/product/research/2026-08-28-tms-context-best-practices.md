# Контекст в TMS: лучшие практики и дизайн для продюсеров

Date: 2026-08-28
Genre: research (synthesis of external sources)
Status: informational; not an approval plan. Phase 2/3 product changes require a separate approved plan.

This document records the full research into how translation management systems capture, refresh, and inject localization context, and how HCGameLoc should design a producer-facing context flow for several dozen producers across published and own-developed games. It contains the synthesis report plus the complete, verbatim findings of four research subagents in the appendices.

---

## 1. Executive summary

Mature TMS platforms do **not** use raw GDD upload as the primary context mechanism. All converge on a **layered context** model:

1. project brief / style guide (sometimes an uploaded file, always distilled into an AI-ready summary),
2. structured glossary with explanations,
3. per-string semantics (note/description) plus technical metadata,
4. visual context (screenshots) mapped to strings,
5. versioning and invalidation when context changes.

A GDD as a document is either a human-facing reference attachment (Smartling job attachments, any file up to 500 MB, not fed to the LLM) or distilled into an AI summary (Phrase/Lokalise/Crowdin style guide -> "AI-Ready Version" injected into prompts). None of the surveyed vendors ships arbitrary semantic retrieval over a GDD as a standard feature [inference - absent from reviewed official docs].

The fork already implements half of this pattern:

- `persona`/`style`/`language_instructions` = an AI-ready style-guide summary (read by both translator and judge as `project_context`);
- glossary with `source_explanation`/`target_explanation` and flags = a term base;
- `note` (developer comment) / `explanation` (translator note) = per-string semantics;
- screenshots exist but do not reach the LLM and are not required.

What is missing: a managed, versioned, reviewed style-guide artifact; a structural string card (screen/feature/speaker/intent); translation/judgment invalidation when context changes; a producer-facing UI with roles and completeness.

---

## 2. Current context model of the fork (verified against code)

- **LLM translation prompt** (`weblate/machinery/llm.py:98-225`, context assembly `1005-1054`, glossary `681-699`): system prompt carries `persona`, `style`, `language_instructions`; per-string payload carries `context`/`key`, `explanation`, `note`, `secondary`, `plural`, `failing_checks`, `placeholders`, `glossary_advisories`; batch glossary carries `source`/`target`/`source_explanation`/`target_explanation`/`flags`. Full glossary travels inline when under `LLM_FULL_GLOSSARY_LIMIT = 300` terms, otherwise source-matched.
- **LLM judge** (`weblate/trans/judge_loop.py:187-205`, `judge_prompts/verdict.txt`): receives `project_context = persona + style` (the game description), plus per-segment `unit.context` **as key only**, `source_unit.note`, glossary, `failing_checks`, `rendered_source`/`rendered_target`, required `back_translation`. The judge does **not** receive `explanation` or screenshots.
- **Human translators additionally see** `Project.instructions` (Markdown, `weblate/trans/models/project.py:257-261`) and screenshots (`Component.screenshot_filemask`, `weblate/trans/models/component.py:680-684`). Neither reaches the LLM.
- **No document/GDD upload surface exists today.** The only upload surfaces are translation-file/component imports (`weblate/api/serializers.py:1921-1926`), the loc-kit glossary ingest (CSV/TSV/XLSX, `weblate/trans/loc_kit.py`), `LocKitImportDraft` (private `FileSystemStorage`, token+owner/session-bound, one-hour cap, Celery cleanup every 900 s), fonts, and screenshots. None of these is a reference-document context feed.

---

## 3. What the market does (survey)

| Platform | Context artifacts | AI/document surface | Freshness mechanism |
|---|---|---|---|
| Crowdin | project/file description, per-string context/labels/max length/plurals; screenshots; glossary with definition/note/POS/status/gender | AI uses file context, glossary, TM, style guide, screenshots, sibling strings; **AI-Ready Version** style-guide summary; AI Alignment drafts glossary terms for review | version branches; Stale Translations Advisor flags affected translations on asset change |
| Lokalise | key description vs technical context; screenshots; glossary; style guide | PDF/DOCX ≤5 MB -> Pro AI **editable concise summary** (<2000 words recommended); not retroactive; no inline enforcement | branches, per-translation history/diff, automations with unverify-on-change |
| Phrase | key description/tags/max length; screenshots; Markdown style guides ≤150 KB | auto-generated AI-optimized style guide; term bases enforce preferred/forbidden terms | job versions on source update; review workflow auto-unverifies translations on source change |
| memoQ | project description; term-base entries with definition/example/image/grammar/forbidden; per-string context IDs/comments/limits | AGT retrieval over TM/LiveDocs/TB within 2048-char context budget; LiveDocs is nearest raw reference corpus | X-Translate for changed source; context-aware TM |
| Transifex | style guide, shared glossary, developer notes, string instructions, context fields, screenshots, comments | no dedicated GDD surface found | source updates preserve history/tags; branches expose divergence |
| Smartling | job attachments (any reference file, human-facing); string instructions/limits; visual context | **Style Rules for AI** (structured) injected into prompts, separate from human Style Guide; prompt tooling with bounded TM/glossary RAG | context capture with age-based replacement (14-30d recommended); does not store per-file versions, so external build/version ID required |
| Localazy | style guide with explicit Game project type; screenshots; glossary; key notes | Producer brief with per-language overrides | branches/history/source-diff |

No vendor documents arbitrary GDD/lore retrieval as standard product surface [inference]. The closest raw-reference precedents are memoQ LiveDocs (match-driven retrieval, not Q&A) and Smartling job attachments (human-facing, not LLM).

---

## 4. Producer data checklist (complete context)

Consolidated from IGDA loc-kit guidance, Allcorrect, Keywords, and vendor field models.

| Area | Required | Deep/optional |
|---|---|---|
| Product frame | title, genre, premise (1 line), setting/lore, audience/age rating, platforms, build/version, links to GDD/wiki/reference build | plot/chapters, mechanics vocabulary |
| Voice/narrative | register/formality, tone preset, punctuation rules, character profiles (image/name/age/gender/rank/personality/speech) | relationship map, speech quirks, emotional intent, minor NPCs/creatures |
| Terminology | source term, definition, subject/domain, preferred target per locale, DNT/forbidden, status, aliases | POS/gender/number, example usage, reference image/URL |
| Per-string semantics | stable key, feature/screen/scene location, content-type enum, human context, speaker/addressee, intent | narrative order, object/system reference, jokes/cultural notes |
| Technical semantics | named placeholders/variables with semantics, reorderability, plural/gender rules, no concatenation, no-translate spans | markup/format instructions, source-file comments |
| UI/audio constraints | max chars/lines or pixels, component type, screenshot per ambiguous/high-risk string | subtitle/audio timing, lip-sync, platform variants |
| Versioning/refresh | source/build/context version, owner, change classification, refresh rule, invalidation rule | history, Q&A with named decision owner |

Key distinction (Lokalise): **description/note (human semantics, editable) != context/key (technical identity)**. For producers the semantic fields are what matter; never conflate them.

---

## 5. Role model and user flow

### Roles

| Role | Permissions |
|---|---|
| Producer / Context Author | create/edit context drafts (own projects), attach screenshots/references, submit; does not approve, does not translate |
| Game / Source Owner | factual/creative approval; edits source metadata |
| Localization Lead / Context Steward | templates and inheritance, high-risk approval, lock/publish, exception queue |
| Linguist / Reviewer | reads approved context, translates/reviews, requests clarification; does not edit approved context |
| Series Steward | shared context assets for series with versioning |
| TMS Admin / Automation | permissions/schema/API/dashboards; no semantic approval |

Operating rule: no Context Author approves their own submission. Low-risk -> Source Owner approval suffices; high-risk/ambiguous/cultural/legal/voice -> Steward or linguist escalation.

### Producer flow

1. Select title template (global/series context inherited read-only with version; only title delta editable).
2. Complete typed project wizard (owners, locales, platforms, style preset, terms, release dates, references; completeness meter distinguishes `not applicable` from blank).
3. Import strings auto-populates key/path/placeholders/constraints; batch tags classify content types.
4. Fill producer queue (missing minimum fields and high-risk first; enums + one-line note, not prose).
5. Submit context packet (records author/timestamp/source-version per string).
6. Game/Source Owner batch-approves factual/creative context (main gate; no linguist needed).
7. Risk-based localization gate (only ambiguous/cultural/legal/voice/layout-critical strings go to Steward/linguist).
8. Translation + linguistic review (approved context + glossary + screenshots + provenance; inline clarification request routes to the responsible owner with SLA).
9. Context change creates a new version and marks affected translations for review on meaning change.
10. Release gate: context readiness + translation + review + stale state; block/waive only high-risk missing context.

---

## 6. Freshness mechanisms (prioritized)

Research subagent `ContextFreshnessStudy` ranked five mechanisms; the recommended minimal set is the first three.

1. **Named context owner + proposal/approval gate** (M effort, reversible). Assign one owner per project context bundle; producers/linguists propose, owner approves; approved entries immutable to ordinary contributors; record who/what/why.
2. **Last-updated, coverage, and stale-state signals** (S-M effort). Show owner, last approved update, age, coverage; mark stale at a threshold and create a filtered recheck queue (never silently treat old context as valid). Smartling recommends 14-30 days for visual context specifically.
3. **Dependency-hash invalidation for AI outputs and judgments** (M effort). Extend the existing judge `context_hash`/request identity (`weblate/trans/models/judge.py`) and the MT cache key (`weblate/machinery/llm.py:1111-1148`) with explicit context-revision IDs; mark prior AI translations/judgments stale on context change while preserving them and requiring an explicit re-run/review; never auto-overwrite approved human translations.
4. Immutable context revisions with human-readable diffs (M-L).
5. Change events and review-task fan-out (M).

---

## 7. Product recommendations for the fork

### Phase 1 - governance, no new code

- Fix the convention: `persona`/`style` = a required, structured style-guide brief (text template, owner, review), not free text.
- `language_instructions` = per-locale overrides.
- Glossary `*_explanation` required for new terms; `note` required for dialogue/UI strings (speaker/screen).
- Assign a context owner and an SLA for clarification requests.

### Phase 2 - product (primary work), principle "human and LLM see the same thing"

1. **`ContextDocument` model** (versioned, owned, approved): Markdown/PDF/DOCX -> deterministic text extract + AI summary, human-editable, injected as `project_context` into both translator and judge prompts.
2. **Structured string card**: screen/feature/speaker/intent/purpose enum + risk tag, backed by an entity reference (characters/items/systems), not free-text `note`.
3. **Screenshots -> context**: `screenshot_filemask` already exists; add OCR/labels and (optionally) multimodal LLM feed; require screenshots for high-risk strings.
4. **Invalidation**: extend the judge `context_hash` (currently source+note+glossary, `judge_loop.py:230-234`) with style-guide version; add style-guide version to the MT cache key.
5. **Producer UI**: wizard template, completeness meter, "no context / no screenshot" filters, batch actions, clarification queue with SLA log.

### Phase 2 security gate (mandatory, before the plan is proposable)

Phase 2 introduces four triggers from the `docs/security/threat-model.rst` "Conditions that change this model": a new public upload endpoint, a new outbound LLM integration (summarization), a new import format (PDF/DOCX parsing), and a prompt-injection surface. The plan must specify, and update `docs/security/threat-model.rst` in the same change for:

1. **Private storage, not public serving.** Reuse the `LocKitImportDraft` pattern: private `FileSystemStorage` under `DATA_DIR`, unguessable token + owner/session binding, access only through authenticated views with object-level permission. Uploaded documents must never be served as static/media.
2. **Format and size allowlist.** Markdown/PDF/DOCX only; server-side extraction, text-only, layout/embedded content ignored (Lokalise DOCX "text only, layout ignored"; Phrase "Markdown only, images unsupported"). Size cap; validation before storage; reject binary/embedded content.
3. **Retention/deletion.** TTL draft + Celery cleanup mirroring `cleanup_loc_kit_drafts`; deletion on supersede and on project delete; non-localized audit log (per AGENTS.md).
4. **Tenant-scoped retrieval.** Summary and any excerpts strictly project-scoped; a project's document never injects into another project's prompt; injection keyed by (project, document_version).
5. **Prompt-injection isolation.** Document text is untrusted; the human-curated summary is primary; any raw excerpt is delimited as reference with an independent instruction to ignore embedded instructions. Raw-document RAG (Phase 3) requires excerpt citations and isolation before it can be proposed.

Per AGENTS.md "Plan first only for complex feature work", Phase 2 goes out as a separate approved plan under `docs/product/plans/`, not as an item in this report.

### Phase 3 - optional RAG (defer)

Only as a fallback for soft lore, never replacing structured fields: upload -> async chunk/index -> retrieve top-k with citations only when needed. Tradeoffs are documented (Azure RAG, Intento, Smartcat): raw-doc dumps cost tokens and dilute attention; retrieval misses, stale/conflicting chunks, and prompt-injection are real risks.

### Anti-patterns to avoid (documented vendor failures)

- Do not inject a whole GDD into the prompt.
- Do not make the AI summary non-editable.
- Do not apply a new guide version retroactively without an unverified/needs-review marker.
- Do not split "guide for AI" vs "guide for humans" (Smartling anti-pattern).
- Do not expect producers to write prose - enums, presets, one-line notes only.

Key producer KPIs (Lokalise ops guidance): minimum-field fill rate, % high-risk with screenshot, context age, questions per 100 strings, reopens, time to approval - tied to release readiness, not prose volume.

---

## 8. Sources (aggregated)

Rebuilt from the subagent appendices below; each appendix carries its own inline `[verified: URL]` / `[inference]` annotations.

### Vendor field/context models
- Crowdin: https://support.crowdin.com/for-managers/ ; /roles/ ; /string-management/ ; /screenshots/ ; /glossary/
- Lokalise: https://docs.lokalise.com/en/articles/2983736-onboarding-guide-for-project-managers ; 2059009-key-editor-and-key-actions ; 3229220-structured-json (via 3229161) ; 1400629-glossary ; 1400643-comments
- Phrase: https://support.phrase.com/hc/en-us/articles/5784119185436-Keys-Strings ; 5822309698204-Screenshot-Management-Strings ; 11155533567388-Editor-Sidebar-Strings ; 25124298060060-Style-Guides ; 5709733372188-Term-Bases-Overview ; 5821933165596-Ordering-Professional-Translations-Strings
- Transifex: https://help.transifex.com/en/articles/6248331-providing-context ; 6220899-structured-json ; 6318944-additional-tools-in-the-transifex-editor ; 13436452-creating-source-strings-in-the-editor ; 6228999-uploading-screenshots
- Smartling: https://help.smartling.com/hc/en-us/articles/5206468338587-Job-Attachments ; 9351060190107-String-Details ; 360057484273--Overview-of-Visual-Context ; 115004155413-Set-Translation-Length-Limits ; 115003066573-Default-User-Permissions ; 12026027210139-Elements-of-a-Glossary-Entry
- Localazy: https://localazy.com/docs/general/style-guide ; /screenshots ; /glossary ; /translating-strings ; /reviewing-translations ; /api/screenshot-management ; /api/ai-translation-api ; /defining-user-roles

### Document ingestion / RAG
- Phrase: https://support.phrase.com/hc/en-us/articles/25124298060060-Style-Guides ; 20660272640284-AI-Translation-Agent ; 14299433827996-Phrase-Next-GenMT
- Lokalise: https://docs.lokalise.com/en/articles/8217808-style-guide ; 11894216-ai-profiles ; 12292275-ai-frequently-asked-questions
- memoQ: https://docs.memoq.com/current/en/Workspace/pre-translate-with-agt.html ; /helpcenter/Products/memoQ-AGT/FAQ.htm ; /helpcenter/Products/memoQ-AGT/First-steps-with-memoQ-AGT.htm
- Smartling: https://help.smartling.com/hc/en-us/articles/42142862499227-Prompt-Tooling-with-RAG-for-LLM-translations ; 43685908255771-Creating-and-Managing-LLM-Profiles ; 41970369123227-Style-Rules-for-AI ; 115003141253-Uploading-Visual-Context
- Crowdin: https://support.crowdin.com/crowdin-ai/ ; /enterprise/style-guide/ ; /_llms-txt/api/crowdin/file-based/api.users.ai.file-translations.post.txt
- Generic RAG: https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview ; /azure/architecture/ai-ml/guide/rag/rag-information-retrieval ; /azure/search/agentic-retrieval-how-to-answer-synthesis ; https://help.smartcat.com/ai-rag-in-smartcat/ ; https://inten.to/blog/improving-glossary-support-with-retrieval-augmented-generation/

### Freshness / versioning
- Phrase: https://support.phrase.com/hc/en-us/articles/5784094755484-Review-Workflow-Strings ; 5709717879324-Workflow-TMS ; 5856411356316-Branching-Strings ; 5709733372188-Term-Bases-Overview ; 10112337656476-Using-a-Term-Base-Strings
- Lokalise: https://docs.lokalise.com/en/articles/8217808-style-guide ; 4529101-automations ; 2107561-translation-history ; 3184756-webhooks ; 3391861-project-branching
- Crowdin: https://support.crowdin.com/version-management/ ; /enterprise/project-settings/privacy/ ; https://store.crowdin.com/stale-translations-advisor
- Smartling: https://help.smartling.com/hc/en-us/articles/360007996553-Smartling-s-Context-Capture-JavaScript-Library ; 360055897494-Job-Details ; 1260806372849-Translation-Quality-Features-Overview ; 12474770764699-Updating-Existing-Source-Files ; 40432193430555-Glossary-Entry-Suggestions ; 1260805573949-Configurable-Permissions-for-Agency-Owners-Translation-Resource-Managers-Translation-Resources ; 10116101882395-Communicate-with-Content-Owners-and-Translators-Using-Issues
- Transifex: https://help.transifex.com/en/articles/6395505-localizing-a-glossary ; 6318604-reviewing-translations ; 6236849-updating-your-source-content ; 14287841-transifex-editor-filters-reference ; 6240965-the-ultimate-guide-for-managing-your-tasks-in-transifex
- memoQ: https://docs.memoq.com/current/en/Concepts/concepts-permissions-from-online-projec.html ; /Workspace/edit-project-template.html ; https://www.memoq.com/solutions/game-localization/

### Game-l10n practitioner guidance
- IGDA loc kit: https://igda.org/news-archive/high-quality-localization-help-loc-help-you/
- Allcorrect: https://allcorrectgames.com/wp-content/uploads/2023/10/Localization_guide_2_allcorrect.pdf
- Keywords: https://www.keywordsstudios.com/en/about-us/news-events/news/a-step-by-step-guide-to-game-localization/
- GameDeveloper: https://www.gamedeveloper.com/business/a-step-by-step-guide-to-game-localization
- Studio Fugu: https://www.studio-fugu.com/blog-posts/video-game-localization-kit-your-ultimate-guide
- SandVox: https://sandvox.io/loc-kit-guide/
- Lokalise context ops: https://lokalise.com/blog/automate-context-management/
- Translated continuous localization: https://translated.com/resources/how-to-run-continuous-localization-a-step-by-step-guide

---

## Appendix A - ContextCaptureSurvey (verbatim)

Full subagent finding: commercial TMS context workflows and the producer-authored data contract.

```json
{
  "problem": "Survey leading commercial TMS context workflows and industry game-localization practice, then derive the producer-authored data contract for complete context in the HCGameLoc Weblate fork.",
  "options": [
    {
      "platform": "Crowdin",
      "artifacts_and_authors": "Project name/description and file-level context; per-string identifier, context, labels, plurals, and maximum translation length. Managers upload TM/glossary/style-guide resources; Developers/Translation Requestors edit source context/key/max length; Owner/Managers assign resources; all project roles can discuss. [verified: https://support.crowdin.com/for-managers/] [verified: https://support.crowdin.com/roles/] [verified: https://support.crowdin.com/string-management/] Screenshots are uploaded and tagged to source strings by OCR or manually; labels organize screenshots. [verified: https://support.crowdin.com/screenshots/] Glossary concepts/terms support definition, subject, notes, URL/figure, term description, POS, type, status, gender, and multilingual translations. [verified: https://support.crowdin.com/glossary/]",
      "game_specific": "Unity integration syncs string/asset tables and tagged context screenshots. [verified: https://crowdin.com/blog/unity-game-localization-with-crowdin/] A dedicated GDD/character-sheet surface was not found in reviewed docs. [inference]",
      "fit": "Strong fit for a layered producer flow: project brief + per-string semantics + visual context + glossary. Existing Django/Weblate context fields can map to these layers without adopting Crowdin-specific concepts. [inference]",
      "effort": "M; reversible. [inference]"
    },
    {
      "platform": "Lokalise",
      "artifacts_and_authors": "Project-manager onboarding covers screenshots, glossary, and style guides. Screenshots get title/description/tags and key links, with OCR/manual highlight. [verified: https://docs.lokalise.com/en/articles/2983736-onboarding-guide-for-project-managers/] Keys have name, human-readable Description, technical Context, tags, and per-key character limit. Structured JSON explicitly distinguishes updateable translator notes from technical context/key identity. [verified: https://docs.lokalise.com/en/articles/2059009-key-editor-and-key-actions] [verified: https://docs.lokalise.com/en/articles/3229161-structured-json] Glossary terms support general and per-language descriptions/translations; glossary terms cannot themselves carry screenshots. [verified: https://docs.lokalise.com/en/articles/1400629-glossary/] Key/translation comments are threaded and mentionable; translators can ask PMs. [verified: https://docs.lokalise.com/en/articles/1400643-comments]",
      "game_specific": "Projects explicitly cover apps, websites, or games; platform-specific key naming is supported. [verified: https://docs.lokalise.com/en/articles/1400460-projects] [verified: https://docs.lokalise.com/en/articles/1400489-keys-and-platforms] No native GDD/build/character-sheet feature found in reviewed docs. [inference]",
      "fit": "Useful precedent for separating technical context IDs from producer-readable notes, a distinction important to avoid losing semantic context. [inference]",
      "effort": "M; reversible. [inference]"
    },
    {
      "platform": "Phrase (Strings/TMS)",
      "artifacts_and_authors": "Keys carry name, description, tags, default translation, and advanced max-character length. [verified: https://support.phrase.com/hc/en-us/articles/5784119185436-Keys-Strings] Admin/Project Manager uploads screenshots to any project; Developers upload for assigned projects; screenshots have names/descriptions, OCR/manual key attachment, and multiple references per key. [verified: https://support.phrase.com/hc/en-us/articles/5822309698204-Screenshot-Management-Strings] Editor exposes format metadata (XLIFF notes, ARB placeholders, Qt context), comments, screenshots, term-base and TM matches. [verified: https://support.phrase.com/hc/en-us/articles/11155533567388-Editor-Sidebar-Strings] Only organization Admins create/edit style guides; PMs attach them per target locale; translators view them. [verified: https://support.phrase.com/hc/en-us/articles/25124298060060-Style-Guides] Term bases provide approved concept terms/translations and are highlighted in the editor. [verified: https://support.phrase.com/hc/en-us/articles/5709733372188-Term-Bases-Overview] Translation orders expose key descriptions, screenshots, style-guide content, custom briefing, max chars, and placeholder instructions. [verified: https://support.phrase.com/hc/en-us/articles/5821933165596-Ordering-Professional-Translations-Strings]",
      "game_specific": "Official Unity plugin syncs String Table Collections, key descriptions/max lengths, and current game-view screenshots; collections can be organized by Weapons, Items, Characters. [verified: https://support.phrase.com/hc/en-us/articles/15979838858140-Unity-Strings/]",
      "fit": "Strong game precedent: producer/developer metadata is attached directly to segments instead of hidden in a separate loc-kit. [inference]",
      "effort": "M/L if Unity integration is desired; M for generic context fields; reversible. [inference]"
    },
    {
      "platform": "memoQ",
      "artifacts_and_authors": "Project metadata includes Project, Client, Subject, Domain, language pair, and Description; description appears on Dashboard and to online-project users. [verified: https://docs.memoq.com/current/en/Workspace/create-new-project-from-template.html] [verified: https://docs.memoq.com/current/en/Workspace/memoq-online-project-s-general.html] PMs manage projects/resources and have Admin rights; translators/reviewers can add terms with permission; Terminologists review term bases. [verified: https://docs.memoq.com/current/en/Concepts/concepts-permissions-from-online-projec.html] Term-base entries are multilingual concepts with definition, example, image, note, domain/subject/client/project, grammar/POS/gender/number, forbidden terms, and matching rules. [verified: https://docs.memoq.com/current/en/Concepts/concepts-term-bases-inside-an-entry.html] Per-string context IDs, comments, and length limits can be imported from XML/Excel fields/XPath; notes/discussions can attach to a document, segment, or selected text with severity and threads. [verified: https://docs.memoq.com/current/en/Workspace/multilingual-xml-files.html] [verified: https://docs.memoq.com/current/en/Workspace/multilingual-excel-and-delimit.html] [verified: https://docs.memoq.com/current/en/Workspace/notes-and-discussions.html]",
      "game_specific": "memoQ's game-localization offering cites custom filters for game files, Gridly/Voiseed integrations, X-Translate for changed source, and string IDs stored in TM for precise context matches. [verified: https://www.memoq.com/solutions/game-localization/] No dedicated per-string screenshot/GDD surface found in reviewed docs; term-base images are concept references, not UI screenshots. [inference]",
      "fit": "Best precedent for structured import metadata, rich terminology, and context-aware versioned TM; less direct precedent for screenshot-first producer UX. [inference]",
      "effort": "M/L; reversible. [inference]"
    },
    {
      "platform": "Transifex",
      "artifacts_and_authors": "Style guide covers company/product/target-market background, brand personality/tone/voice, grammar/syntax/date rules, and language/culture instructions; glossary covers fixed terms and DNT; developers put notes in source files; String Instructions are editor-authored for short or placeholder strings; context fields are supported in PO/JSON/XLIFF/XLSX/Qt; character limits, screenshots, and comments are recommended. [verified: https://help.transifex.com/en/articles/6248331-providing-context] Structured JSON has string, context, developer_comment, character_limit, and plurals; ICU plural/variable syntax is supported. [verified: https://help.transifex.com/en/articles/6220899-structured-json] Maintainers/admins create source strings and set tags/limits; comments/issues support questions and mentions. [verified: https://help.transifex.com/en/articles/6318944-additional-tools-in-the-transifex-editor] [verified: https://help.transifex.com/en/articles/13436452-creating-source-strings-in-the-editor/] Screenshots are uploaded and OCR/manual-mapped to strings. [verified: https://help.transifex.com/en/articles/6228999-uploading-screenshots]",
      "game_specific": "No game-specific native feature found in reviewed docs; Native SDK/Fileless are continuous application workflows rather than game features. [verified: https://help.transifex.com/en/articles/6379324-transifex-native] [inference]",
      "fit": "Very strong precedent for explicit separation of context, developer comment, instruction, character limit, plural/variable data. [inference]",
      "effort": "M; reversible. [inference]"
    },
    {
      "platform": "Smartling",
      "artifacts_and_authors": "Job Briefing carries job description/details; Job Attachments accept arbitrary non-translatable reference files, visible to all job users; any role can upload, while PM/Account Owner controls deletion. [verified: https://help.smartling.com/hc/en-us/articles/5206468338587-Job-Attachments-A-helpful-resource-for-your-translation-tasks] String Details expose instructions (file/manual), attachments, history, source metadata, visual context, keys/variants/tags. [verified: https://help.smartling.com/hc/en-us/articles/9351060190107-String-Details] Visual Context supports screenshots/images, PDF, HTML, MP4/GIF/video with OCR/manual mapping; HTML can dynamically show translated text while images/PDF are static. [verified: https://help.smartling.com/hc/en-us/articles/360057484273--Overview-of-Visual-Context] Character limits are set on source strings, apply to all target languages, and appear in CAT-tool QA. [verified: https://help.smartling.com/hc/en-us/articles/115004155413-Set-Translation-Length-Limits] Account Owners/PMs manage glossary/style-guide/TM/QA assets; translation resources consume them in CAT. Glossary entries support definition/context, reference image, language-specific terms/notes, example strings, DNT/blocklist. [verified: https://help.smartling.com/hc/en-us/articles/115003066573-Default-User-Permissions] [verified: https://help.smartling.com/hc/en-us/articles/12026027210139-Elements-of-a-Glossary-Entry]",
      "game_specific": "No dedicated game feature found; arbitrary Job Attachments can carry a build, GDD, or video, and visual video/screen capture is general-purpose. [inference]",
      "fit": "Best precedent for allowing a producer's full reference bundle (GDD/build/video) beside structured per-string metadata. [inference]",
      "effort": "M; reversible. [inference]"
    },
    {
      "platform": "Localazy",
      "artifacts_and_authors": "Style Guide has explicit Project Type=Game, project URLs/description, industry/domain, brand voice, formality/tone/sentiment/preferred gender, transcreation/accuracy, general instructions, and per-language overrides; Managers/Owners author it, others view. [verified: https://localazy.com/docs/general/style-guide] Screenshots link to source keys by OCR/manual mapping and carry comment/tags/metadata; Reviewer is the minimum role for upload. [verified: https://localazy.com/docs/general/screenshots] [verified: https://localazy.com/docs/api/screenshot-management] Glossary terms have source term, description, translations, case/whole-word rules, and translatable/DNT status; examples and contextual usage are recommended. [verified: https://localazy.com/docs/general/glossary] Translation UI includes Suggestions, Similar, Versions, Languages, Screenshots, and Comments; source translation notes can come from file comments/formats and Owners/Managers can edit them. [verified: https://localazy.com/docs/general/translating-strings] [verified: https://localazy.com/docs/general/reviewing-translations] API accepts key, comment, source/plurals, and lengthLimit. [verified: https://localazy.com/docs/api/ai-translation-api]",
      "game_specific": "The explicit Game project type is the main game-specific support found; no dedicated GDD/build/character-sheet store found. [verified: https://localazy.com/docs/general/style-guide] [inference]",
      "fit": "Strong precedent for a producer-facing project brief with language-specific overrides and game selection, plus adjacent notes/screenshots/glossary. [inference]",
      "effort": "M; reversible. [inference]"
    }
  ],
  "comparison_table": [
    {"platform": "Crowdin", "effort": "M [inference]", "risk": "Low-M [inference]", "stack_fit": "Layered metadata maps cleanly to Django/Weblate fields [inference]", "maintenance": "Branches, screenshot replacement, context/advisor coverage [verified: https://support.crowdin.com/version-management/]", "key_tradeoff": "Rich context/advisor/Unity ecosystem, but GDD/character knowledge remains external [inference]"},
    {"platform": "Lokalise", "effort": "M [inference]", "risk": "Low-M [inference]", "stack_fit": "Clear notes-vs-technical-context split [inference]", "maintenance": "Branches/history/source-diff support refresh [verified: https://docs.lokalise.com/en/articles/3391861-project-branching]", "key_tradeoff": "Simple producer UX, but glossary cannot carry screenshots [verified: https://docs.lokalise.com/en/articles/1400629-glossary/]"},
    {"platform": "Phrase", "effort": "M/L for Unity-like game capture [inference]", "risk": "Medium integration dependency [inference]", "stack_fit": "Per-key metadata + game-view captures closely match desired flow [inference]", "maintenance": "Branch sync/merge and unverify-on-source-change [verified: https://support.phrase.com/hc/en-us/articles/5856411356316-Branching-Strings]", "key_tradeoff": "Strongest native Unity/game-context precedent, but style-guide authoring is admin-gated [verified: https://support.phrase.com/hc/en-us/articles/25124298060060-Style-Guides]"},
    {"platform": "memoQ", "effort": "M/L [inference]", "risk": "Medium [inference]", "stack_fit": "Strong structured import/TB model [inference]", "maintenance": "X-Translate and context-aware TM [verified: https://www.memoq.com/solutions/game-localization/]", "key_tradeoff": "Rich terminology and versioned context, weaker screenshot-first UX [inference]"},
    {"platform": "Transifex", "effort": "M [inference]", "risk": "Low-M [inference]", "stack_fit": "Explicit context/comment/limits/plural schema [inference]", "maintenance": "Source updates preserve history/tags and branches expose divergence [verified: https://help.transifex.com/en/articles/6236849-updating-your-source-content]", "key_tradeoff": "Excellent string metadata contract, little native game-specific support found [inference]"},
    {"platform": "Smartling", "effort": "M [inference]", "risk": "Low-M [inference]", "stack_fit": "Arbitrary attachments plus structured instructions [inference]", "maintenance": "Strong history but same-name upload overwrites and no per-file versions [verified: https://help.smartling.com/hc/en-us/articles/12474770764699-Updating-Existing-Source-Files]", "key_tradeoff": "Best full reference-bundle model, but external build/version discipline is required [inference]"},
    {"platform": "Localazy", "effort": "M [inference]", "risk": "Low-M [inference]", "stack_fit": "Producer brief fields and per-language overrides map directly [inference]", "maintenance": "Branches/history/source-difference and screenshot update support [verified: https://docs.lokalise.com/en/articles/2107561-translation-history]", "key_tradeoff": "Only reviewed set with explicit Game project type, but no dedicated GDD/build store found [inference]"}
  ],
  "producer_complete_context_checklist": [
    {
      "area": "Product frame",
      "required_data": "Game title; genre; setting/lore/world rules; plot/chapter/quest structure; mechanics and system vocabulary; target audience; age rating; target markets; release platforms; source/build/content-pack version; official site/wiki/GDD/reference build/playthrough links.",
      "evidence": "IGDA loc-kit asks for title, audience, website/wiki, platforms, PEGI, plot/chapters, mechanics/genre; Allcorrect emphasizes build/screenshots and world/mechanics descriptions; Keywords defines familiarization and loc-kit scope. [verified: https://igda.org/news-archive/high-quality-localization-help-loc-help-you/] [verified: https://allcorrectgames.com/wp-content/uploads/2023/10/Localization_guide_2_allcorrect.pdf] [verified: https://www.keywordsstudios.com/en/about-us/news-events/news/a-step-by-step-guide-to-game-localization/]"
    },
    {
      "area": "Voice and narrative",
      "required_data": "Source style guide: register/formality, tone/voice by content type, punctuation/capitalization/abbreviations, cultural rules, transcreation policy, player-address gender and per-locale overrides. Character profiles: image, name, age, gender, status/rank, personality, biography, relationships, speech quirks/accent/coarseness/politeness, emotional intent. Include minor NPCs/creatures where grammar or voice can vary.",
      "evidence": "IGDA explicitly lists character profiles, gender, relationships, speech, and style-guide rules; Localazy exposes formality/tone/sentiment/preferred gender and language overrides. [verified: https://igda.org/news-archive/high-quality-localization-help-loc-help-you/] [verified: https://localazy.com/docs/general/style-guide]"
    },
    {
      "area": "Terminology",
      "required_data": "Concept glossary with source term, definition/meaning, domain/subject, preferred target translation per locale, DNT/forbidden flag, aliases/status, POS, grammatical gender/number, example usage, pronunciation or reference image/URL where needed. Include names of characters, factions, places, items, abilities, creatures and game systems.",
      "evidence": "Crowdin, Smartling and memoQ all support rich concept definitions, notes, images, grammatical metadata and preferred/forbidden terms; IGDA calls out objects, creatures, systems and references. [verified: https://support.crowdin.com/glossary/] [verified: https://help.smartling.com/hc/en-us/articles/12026027210139-Elements-of-a-Glossary-Entry] [verified: https://docs.memoq.com/current/en/Concepts/concepts-term-bases-inside-an-entry.html] [verified: https://igda.org/news-archive/high-quality-localization-help-loc-help-you/]"
    },
    {
      "area": "Per-string semantics",
      "required_data": "Stable key/ID; feature/file/scene/chapter/screen location; content type (button/header/tooltip/dialogue/item/etc.); human-readable context/instruction; speaker/addressee and number of addressees; intent/emotion; referenced object/system/joke; logical narrative order; duplicate-string disambiguation. Keep technical key/context identity separate from translator-readable notes.",
      "evidence": "Transifex separates context and developer_comment; Lokalise distinguishes technical Context from notes; IGDA requires UI type, string location, speaker/addressee/emotion/references and logical order. [verified: https://help.transifex.com/en/articles/6220899-structured-json] [verified: https://docs.lokalise.com/en/articles/3229161-structured-json] [verified: https://igda.org/news-archive/high-quality-localization-help-loc-help-you/]"
    },
    {
      "area": "Technical semantics",
      "required_data": "Named placeholders/variables/tags with semantic descriptions and data type; allowed reordering; plural/gender/select rules; no concatenated sentence fragments; no-translate spans; formatting/markup instructions; source-file comments/notes captured as instructions.",
      "evidence": "IGDA style-guide checklist includes tags/variables and their meanings; Transifex supports ICU variables/plurals and developer comments; Phrase requires placeholder handling guidance in briefing/style guide; Keywords recommends named placeholders and avoiding concatenation. [verified: https://igda.org/news-archive/high-quality-localization-help-loc-help-you/] [verified: https://help.transifex.com/en/articles/6220899-structured-json] [verified: https://support.phrase.com/hc/en-us/articles/5821933165596-Ordering-Professional-Translations-Strings] [verified: https://www.keywordsstudios.com/en/about-us/news-events/news/a-step-by-step-guide-to-game-localization/]"
    },
    {
      "area": "UI/audio constraints",
      "required_data": "Per-string maximum characters, lines, or pixels; component type and font/style/size; screenshot or visual context mapped to exact string and state; layout/state notes; text expansion allowance; subtitle/audio timing, speaker, delivery and lip-sync metadata where applicable; reference video/build and reproducible access path.",
      "evidence": "IGDA and Allcorrect require character/line limits, screenshots/build and logical context; Smartling supports visual context files and source-string limits; memoQ supports pixel-based limits with font/style/size metadata; Keywords cites 20-35% expansion and screenshots/video. [verified: https://igda.org/news-archive/high-quality-localization-help-loc-help-you/] [verified: https://allcorrectgames.com/wp-content/uploads/2023/10/Localization_guide_2_allcorrect.pdf] [verified: https://help.smartling.com/hc/en-us/articles/115004155413-Set-Translation-Length-Limits] [verified: https://docs.memoq.com/11-2/en/Workspace/edit-qa-settings.html] [verified: https://www.keywordsstudios.com/en/about-us/news-events/news/a-step-by-step-guide-to-game-localization/]"
    },
    {
      "area": "Versioning and refresh",
      "required_data": "Source/build/content-pack/branch ID; context artifact version and capture date; owner of updates; change classification (typo vs changed meaning); screenshot remap/refresh rule; translation invalidation/review rule; history; comments/Q&A with named decision owner and resolved decision. Ensure source and context update atomically.",
      "evidence": "Crowdin, Lokalise, Phrase and Transifex provide branches/source-diff/history; memoQ X-Translate handles changed source; Smartling preserves unchanged strings but does not store each uploaded file version, making external version/build IDs necessary. [verified: https://support.crowdin.com/version-management/] [verified: https://docs.lokalise.com/en/articles/3391861-project-branching] [verified: https://support.phrase.com/hc/en-us/articles/5856411356316-Branching-Strings] [verified: https://help.transifex.com/en/articles/6236849-updating-your-source-content] [verified: https://www.memoq.com/solutions/game-localization/] [verified: https://help.smartling.com/hc/en-us/articles/12474770764699-Updating-Existing-Source-Files]"
    }
  ],
  "recommendation": "Recommendation for human approval: adopt the common layered model rather than a single GDD upload. Make the producer flow capture (1) a versioned project brief and links/reference bundle, (2) structured style/voice and character/world knowledge, (3) a rich glossary, (4) per-string semantic and technical metadata, (5) mapped screenshots/build/video and hard UI/audio constraints, and (6) threaded questions/decisions with refresh ownership. This is the intersection of Crowdin/Lokalise/Phrase/Transifex/Smartling/Localazy field models and the IGDA/Allcorrect/Keywords game loc-kit guidance. [inference] Phrase's Unity capture is the strongest game-specific precedent, while Smartling's arbitrary Job Attachments justify allowing a full GDD/build bundle; neither should replace structured per-string fields. [inference]"
}
```

---

## Appendix B - ContextFreshnessStudy (verbatim)

Full subagent finding: keeping producer context current (ownership, staleness, versioning, invalidation).

```json
{
  "problem": "Decide which product mechanisms can keep producer-provided localization context current as HCGameLoc evolves: assign ownership, expose staleness, version/diff context assets, and invalidate AI translation/judge outputs when their inputs change. The fork is a Django/Python Weblate derivative with Celery/Redis, existing glossary/screenshots/project instructions, producer-facing judge runs, and persisted context/request hashes; there is no document/GDD upload surface today.",
  "options": [
    {
      "priority": 1,
      "name": "Named context owner plus proposal/approval gate",
      "what_it_is": "Treat a project context bundle (persona/style, glossary entries, instructions, screenshots and future briefs) as an owned resource. Producers or linguists can propose changes; a named project owner/localization manager approves publication. Keep approved entries immutable to ordinary contributors and record who changed what and why.",
      "fit": "Fits the existing Django project/user/action model and current surfaces: Project.instructions, glossary models, screenshots, producer-facing judge views, and history events in weblate/trans/actions.py. Existing judge payload assembly in weblate/trans/judge_loop.py and glossary matching in weblate/glossary/models.py are natural consumers of an approved bundle. This is an integration judgment, not a claim about an unimplemented Weblate feature [inference].",
      "pros": [
        "Clear accountability without making linguists responsible for product meaning. Crowdin separates read-only, manage-drafts, and full glossary access; managers retain control [verified: https://support.crowdin.com/enterprise/project-settings/privacy/].",
        "Allows linguists to surface missing or incorrect context while keeping final terminology/context approval with a manager. Smartling supports glossary suggestions from any CAT user, with account owner/project manager approval before activation [verified: https://help.smartling.com/hc/en-us/articles/40432193430555-Glossary-Entry-Suggestions].",
        "Separates permission to manage glossary, style guide, TM, and QC assets, rather than one broad editor role [verified: https://help.smartling.com/hc/en-us/articles/1260805573949-Configurable-Permissions-for-Agency-Owners-Translation-Resource-Managers-Translation-Resources]."
      ],
      "cons_risks": [
        "One-person ownership can become a bottleneck; use draft proposals and a small approval SLA [inference].",
        "Approval gate alone does not reveal which already-produced AI outputs used the old context [inference].",
        "Adding a new resource lifecycle and permissions has more UI/schema work than a timestamp-only solution [inference]."
      ],
      "effort_reversibility": "M; highly reversible if proposals and approvals are additive and old revisions remain readable [inference].",
      "sources": [
        "https://support.crowdin.com/enterprise/project-settings/privacy/",
        "https://help.smartling.com/hc/en-us/articles/40432193430555-Glossary-Entry-Suggestions",
        "https://help.smartling.com/hc/en-us/articles/1260805573949-Configurable-Permissions-for-Agency-Owners-Translation-Resource-Managers-Translation-Resources",
        "https://support.phrase.com/hc/en-us/articles/5709733372188-Term-Bases-Overview"
      ]
    },
    {
      "priority": 2,
      "name": "Last-updated, coverage, and stale-state signals",
      "what_it_is": "Show each context asset's owner, last approved update, age, affected locales/content classes, and coverage. At a threshold, mark it stale and create a filtered recheck queue rather than silently treating old context as valid. For visual context, optionally use a separate short refresh threshold.",
      "fit": "The fork already exposes screenshot coverage alerts and judge-stale filtering, and judge freshness is computed in weblate/trans/views/edit.py from hashes produced by weblate/trans/models/judge.py. Additive metadata can sit beside existing project instructions, glossary and screenshot records; no new document ingestion is required for the first increment [inference].",
      "pros": [
        "Cheap, legible producer signal: a stale badge and queue are more actionable than an annual reminder [inference].",
        "Smartling exposes context coverage, timestamps and unmatched strings, and its Context Capture library can replace context older than a configured age; its documented recommendation is 14-30 days for visual context [verified: https://help.smartling.com/hc/en-us/articles/360055897494-Job-Details] [verified: https://help.smartling.com/hc/en-us/articles/360007996553-Smartling-s-Context-Capture-JavaScript-Library].",
        "Crowdin's Stale Translations Advisor turns resource changes into affected-string labels, reasons and confidence, rather than only a report [verified: https://store.crowdin.com/stale-translations-advisor].",
        "Crowdin's editor lets a translator request missing context and notify a project manager; missing-context and screenshot filters make gaps findable [verified: https://support.crowdin.com/enterprise/online-editor/]."
      ],
      "cons_risks": [
        "Age is only a proxy: a recently edited but wrong brief can be worse than an old stable one [inference].",
        "Automatic age refresh of screenshots can create privacy/storage concerns; Smartling recommends staging/test capture where sensitive data may appear [verified: https://help.smartling.com/hc/en-us/articles/360007996553-Smartling-s-Context-Capture-JavaScript-Library].",
        "A stale flag without affected-unit mapping creates triage work and will be ignored [inference]."
      ],
      "effort_reversibility": "S-M; reversible and useful even before full versioning [inference].",
      "sources": [
        "https://help.smartling.com/hc/en-us/articles/360055897494-Job-Details",
        "https://help.smartling.com/hc/en-us/articles/360007996553-Smartling-s-Context-Capture-JavaScript-Library",
        "https://store.crowdin.com/stale-translations-advisor",
        "https://support.crowdin.com/enterprise/online-editor/"
      ]
    },
    {
      "priority": 3,
      "name": "Dependency-hash invalidation for AI outputs and judgments",
      "what_it_is": "Make every generated translation or quality judgment addressable by the complete input identity: source/target, locale, context-bundle revision, glossary revision, prompt/profile revision, model/provider, and relevant checks. When a context dependency changes, retain the old result for audit but mark it stale and enqueue retranslation/rejudgment; do not overwrite approved human translations by default.",
      "fit": "The fork already has the core shape: weblate/trans/models/judge.py computes context_hash and compute_judge_request_identity, while JudgeVerdict stores context_hash, project_context_hash, request_identity, profile_fingerprint and prompt_schema_version. weblate/trans/judge_loop.py builds the request, and weblate/trans/autotranslate.py persists run metadata. Extend the identity with explicit context-resource revision IDs rather than inventing a parallel cache [verified local code: weblate/trans/models/judge.py; verified local code: weblate/trans/judge_loop.py; verified local code: weblate/trans/autotranslate.py].",
      "pros": [
        "Prevents a stored verdict or AI translation from being presented as current after a context change [inference].",
        "Smartling documents that quality-check results can change when glossary, TM or QC configuration changes even if the translation does not; this supports re-evaluating stored quality evidence against current dependencies [verified: https://help.smartling.com/hc/en-us/articles/1260806372849-Translation-Quality-Features-Overview].",
        "Phrase explicitly says term-base updates must be manually synced to the MT glossary, demonstrating that downstream AI resources can lag unless a sync/invalidation edge exists [verified: https://support.phrase.com/hc/en-us/articles/10112337656476-Using-a-Term-Base-Strings].",
        "Lokalise supports minimum-change triggers, force-overwrite as an explicit option, and automatic unverified marking, while style-guide changes do not retroactively affect completed translations [verified: https://docs.lokalise.com/en/articles/4529101-automations] [verified: https://docs.lokalise.com/en/articles/8217808-style-guide].",
        "Preserving old results makes rollback and audit possible; this is also consistent with Smartling's treatment of LQA as a historical snapshot [verified: https://help.smartling.com/hc/en-us/articles/1260806372849-Translation-Quality-Features-Overview]."
      ],
      "cons_risks": [
        "A broad style-guide or glossary revision can invalidate many rows and trigger expensive AI work; use affected-unit mapping, explicit re-run controls and risk/locale filters [inference].",
        "Hashing only the text currently visible to a judge misses human-only instructions or screenshots; those must either be included in the model input or explicitly excluded from the AI contract [inference].",
        "Rejudging is not equivalent to changing a translation; keep translation status and judgment status separate [inference]."
      ],
      "effort_reversibility": "M; highly reversible because old rows remain and the invalidation key can be rolled back [inference].",
      "sources": [
        "https://help.smartling.com/hc/en-us/articles/1260806372849-Translation-Quality-Features-Overview",
        "https://support.phrase.com/hc/en-us/articles/10112337656476-Using-a-Term-Base-Strings",
        "https://docs.lokalise.com/en/articles/4529101-automations",
        "https://docs.lokalise.com/en/articles/8217808-style-guide"
      ]
    },
    {
      "priority": 4,
      "name": "Immutable context revisions with human-readable diffs",
      "what_it_is": "Publish context as revisioned bundles rather than mutable text. Each revision records author, approver, timestamp, rationale, locale/scope, and a machine-readable diff; runs and translations point to the exact revision. Permit rollback and show affected units before approval.",
      "fit": "Uses existing Django persistence and action/history conventions; the existing judge run records and request hashes can reference a bundle revision. It can begin with glossary/instructions metadata and later include uploaded briefs/GDDs, avoiding a dependency on the absent document-upload surface [inference].",
      "pros": [
        "Phrase creates new job versions for continuous source updates and can propagate versions through workflow steps; it also exports workflow changes as an HTML comparison [verified: https://support.phrase.com/hc/en-us/articles/5709717879324-Workflow-TMS].",
        "Transifex shows glossary history with added/removed diff highlighting, and supports locking reviewed glossary translations [verified: https://help.transifex.com/en/articles/6395505-localizing-a-glossary].",
        "Lokalise retains per-translation history with diff, actor, tool, timestamp and restore; its snapshots provide a whole-project restore point, though snapshots do not include translation history [verified: https://docs.lokalise.com/en/articles/2107561-translation-history].",
        "Diffs let producers approve a targeted terminology or instruction change without rereading an entire brief [inference]."
      ],
      "cons_risks": [
        "Resource-level versioning is more data/UI than a simple last-updated field, and vendors differ in how deeply they version style guides versus strings [inference].",
        "A diff does not automatically identify semantically affected translations; it needs dependency matching or a reviewer-selected scope [inference].",
        "Immutable revisions can increase storage and require a retention policy [inference]."
      ],
      "effort_reversibility": "M-L; reversible if old revisions are retained and the active pointer can be switched back [inference].",
      "sources": [
        "https://support.phrase.com/hc/en-us/articles/5709717879324-Workflow-TMS",
        "https://help.transifex.com/en/articles/6395505-localizing-a-glossary",
        "https://docs.lokalise.com/en/articles/2107561-translation-history"
      ]
    },
    {
      "priority": 5,
      "name": "Change events and review-task fan-out",
      "what_it_is": "On approved context publication, emit one event containing project, asset, revision, owner, affected locales/content classes and impact count. Notify the owner and linguists, create a review/retranslation queue, and close the item when the new translation/judgment is accepted. Keep notifications actionable and deduplicated.",
      "fit": "Fits the fork's existing action/event registry in weblate/trans/actions.py and Celery/Redis runtime; current producer judge summaries and stale filters can become the destination UI [inference].",
      "pros": [
        "Lokalise webhooks provide real-time integration events for translation updates and completed tasks [verified: https://docs.lokalise.com/en/articles/3184756-webhooks].",
        "Phrase notifies linguists on source changes and job workflow transitions [verified: https://support.phrase.com/hc/en-us/articles/5784094755484-Review-Workflow-Strings] [verified: https://support.phrase.com/hc/en-us/articles/5709717879324-Workflow-TMS].",
        "Smartling routes source issues to account owners/project managers and translation issues to the relevant linguist with configurable notifications [verified: https://help.smartling.com/hc/en-us/articles/10116101882395-Communicate-with-Content-Owners-and-Translators-Using-Issues].",
        "A queue with a reason and direct filter is more likely to be acted on than broadcast email [inference]."
      ],
      "cons_risks": [
        "Notification volume can become noise if every minor edit emits an event; batch and severity-filter changes [inference].",
        "Events alone do not guarantee downstream consumers fetched the current state; use idempotent revision IDs and re-read authoritative state [inference].",
        "More workflow automation can accidentally overwrite human translations unless status/approval guards are explicit [inference]."
      ],
      "effort_reversibility": "M; reversible if events are additive and consumers can be disabled [inference].",
      "sources": [
        "https://docs.lokalise.com/en/articles/3184756-webhooks",
        "https://support.phrase.com/hc/en-us/articles/5784094755484-Review-Workflow-Strings",
        "https://support.phrase.com/hc/en-us/articles/5709717879324-Workflow-TMS",
        "https://help.smartling.com/hc/en-us/articles/10116101882395-Communicate-with-Content-Owners-and-Translators-Using-Issues"
      ]
    }
  ],
  "comparison_table": [
    {"option": "1. Named owner + approval gate", "effort": "M", "risk": "Low-Medium; bottleneck risk", "stack_fit": "High; maps to Django users/actions, glossary/screenshots/project instructions", "maintenance": "Low after setup", "key_tradeoff": "Accountability/control versus approval latency"},
    {"option": "2. Age/coverage/stale signals", "effort": "S-M", "risk": "Low; age can be a false proxy", "stack_fit": "High; existing screenshot coverage and judge-stale surfaces", "maintenance": "Low", "key_tradeoff": "Very cheap visibility versus limited semantic accuracy"},
    {"option": "3. Dependency-hash invalidation", "effort": "M", "risk": "Medium; fan-out/cost and incomplete input coverage", "stack_fit": "Very high; existing judge context/request hashes and persisted run fields", "maintenance": "Medium; identity schema must evolve", "key_tradeoff": "Strong correctness versus re-run cost"},
    {"option": "4. Immutable revisions + diffs", "effort": "M-L", "risk": "Medium; scope mapping/storage", "stack_fit": "High; Django history/run records; document upload can wait", "maintenance": "Medium", "key_tradeoff": "Auditability/rollback versus more product surface"},
    {"option": "5. Change events + queues", "effort": "M", "risk": "Medium; notification storms/overwrite hazards", "stack_fit": "High; existing actions and Celery/Redis", "maintenance": "Medium", "key_tradeoff": "Fast adoption versus event orchestration complexity"}
  ],
  "recommendation": {
    "text": "Recommendation for human approval: start with a deliberately small three-part set: (1) assign one named producer/project owner and a draft->approved gate for context changes; (2) show last-approved timestamp, owner, coverage and a stale/recheck filter; (3) extend the already-existing AI/judge request identity with explicit context revision/dependency IDs, so changed context marks prior AI translations/judgments stale while preserving them and requiring an explicit re-run/review. Add one batched notification when a stale queue is created, but defer full document/GDD ingestion and generalized immutable diff UI until this loop proves useful. This combination materially improves freshness without forcing a small team to maintain a new document system: ownership prevents silent edits, staleness makes work visible, and hashes prevent old AI evidence from masquerading as current. The exact stale threshold should be content-class-specific; Smartling's 14-30 day guidance is for visual context, not a universal rule [verified: https://help.smartling.com/hc/en-us/articles/360007996553-Smartling-s-Context-Capture-JavaScript-Library]. The rejudgment/invalidation design is a recommendation derived from vendor behavior and the fork's existing hash fields, not a claim that external TMS platforms expose one universal standard [inference].",
    "simplest_set": [
      "Named owner + approval gate",
      "Last-updated/coverage/stale queue",
      "Dependency revision/hash invalidation with explicit re-run and preserved history"
    ],
    "defer": [
      "Full document/GDD upload",
      "Cross-asset semantic impact analysis",
      "Automatic overwriting of existing human translations"
    ]
  }
}
```

---

## Appendix C - DocumentIngestionStudy (verbatim)

Full subagent finding: document/GDD ingestion options and tradeoffs.

```json
{
  "Problem": "Choose how a Django/Python Weblate fork should ingest large game reference documents and supply useful localization context, while preserving control, cost discipline, freshness, and quality.",
  "vendor_features": {
    "Phrase": {
      "what_ships": "Phrase Style Guides accepts UTF-8 Markdown only (150 KB max; images unsupported), stores guides in a shared Assets library, attaches them per locale to TMS/Strings/Studio, and auto-generates an AI-optimized version. Phrase AI Translation Agent and Next GenMT use project TMs via RAG/few-shot examples; term bases supply preferred/forbidden terminology. No reviewed official doc documents arbitrary GDD/lore retrieval.",
      "fit": "Maps to existing project persona/style/language_instructions and glossary fields, but introduces a document-to-summary workflow rather than raw-doc RAG.",
      "sources": [
        "https://support.phrase.com/hc/en-us/articles/25124298060060-Style-Guides",
        "https://support.phrase.com/hc/en-us/articles/20660272640284-AI-Translation-Agent",
        "https://support.phrase.com/hc/en-us/articles/14299433827996-Phrase-Next-GenMT"
      ]
    },
    "Lokalise": {
      "what_ships": "Lokalise Style Guide accepts PDF/DOCX up to 5 MB; PDF structure is preserved while DOCX imports text only. Sync with Pro AI summarizes the uploaded guide into an editable concise version used for AI Translation tasks. Docs recommend under 2,000 words and warn that long or contradictory guides slow/hurt output. Custom AI Profiles optionally RAG-retrieve TM or existing reviewed/tagged translations; RAG profiles prioritize examples over style guides. Default profile uses style guides, descriptions, glossaries, and task instructions without RAG. Approximately 500 clean examples are recommended, with source examples <=1,000 chars. No generic GDD/lore KB retrieval documented.",
      "fit": "Strong evidence for an explicit upload -> summarize -> human edit -> inject path alongside current fields; profile-scoped TM retrieval is an optional later layer.",
      "sources": [
        "https://docs.lokalise.com/en/articles/8217808-style-guide",
        "https://docs.lokalise.com/en/articles/11894216-ai-profiles",
        "https://docs.lokalise.com/en/articles/12292275-ai-frequently-asked-questions"
      ]
    },
    "memoQ": {
      "what_ships": "memoQ Adaptive Generative Translation uses Azure OpenAI and instant domain adaptation from TM, LiveDocs corpora (aligned/previous documents), and term bases. Operators choose scope, match threshold, hit count, term-base priority, omit-short-terms, and formality. AGT limits each segment to 512 chars and total source/reference/glossary context to 2,048 chars; context characters count usage. No custom prompt. LiveDocs is the closest surveyed example to raw reference-document ingestion, but AGT sends retrieved matches, not a whole GDD, and no citation UI is documented.",
      "fit": "Existing Weblate translation memory and per-unit context could support a match-driven intermediate; adding arbitrary docs would need corpus/index ownership outside current machinery.",
      "sources": [
        "https://docs.memoq.com/current/en/Workspace/pre-translate-with-agt.html",
        "https://docs.memoq.com/helpcenter/Products/memoQ-AGT/FAQ.htm",
        "https://docs.memoq.com/helpcenter/Products/memoQ-AGT/First-steps-with-memoQ-AGT.htm"
      ]
    },
    "Smartling": {
      "what_ships": "Prompt Tooling with RAG in LLM Profiles injects up to 10 TM examples per string, detected glossary source/target or DNT terms, and locale-specific Style Rules for AI. RAG examples are references, not guaranteed reuse. Separate TM Match Insertion and AI-Enhanced Glossary Term Insertion provide stronger safety nets. Style Guides may have human-facing attachments, while Style Rules for AI are structured prompt rules. HTML/image/PDF Visual Context uploads are visual context/OCR mappings, not documented raw text knowledge-base RAG.",
      "fit": "Supports structured always-on fields plus bounded retrieved examples and deterministic enforcement; visual screenshots can remain separate human/AI visual-context inputs.",
      "sources": [
        "https://help.smartling.com/hc/en-us/articles/42142862499227-Prompt-Tooling-with-RAG-for-LLM-translations",
        "https://help.smartling.com/hc/en-us/articles/43685908255771-Creating-and-Managing-LLM-Profiles",
        "https://help.smartling.com/hc/en-us/articles/41970369123227-Style-Rules-for-AI",
        "https://help.smartling.com/hc/en-us/articles/115003141253-Uploading-Visual-Context"
      ]
    },
    "Crowdin": {
      "what_ships": "Crowdin AI prompts can include project/file context, sibling strings, glossary, TM suggestions, assigned style guides, screenshots, and reusable AI Snippets. Missing file context can be AI-summarized. Native Style Guide accepts MD/PDF/DOCX/XLSX and maintains an editable AI-Ready Version summary used in AI prompts. AI Alignment drafts glossary terms from human translations for review. AI translation APIs accept glossary/TM/style-guide IDs, instructions, and image attachments (max 10); no reviewed official doc describes generic reference-document RAG.",
      "fit": "Closest product pattern is structured file/project context plus human-reviewed style-guide summary, matching current persona/style/glossary design.",
      "sources": [
        "https://support.crowdin.com/crowdin-ai/",
        "https://support.crowdin.com/enterprise/style-guide/",
        "https://support.crowdin.com/_llms-txt/api/crowdin/file-based/api.users.ai.file-translations.post.txt"
      ]
    },
    "generic_patterns": {
      "what_ships": "Azure AI Search documents a classic RAG pipeline (indexers/skills chunking, vectorization, one query, LLM) and agentic retrieval (query planning, parallel subqueries, structured grounding/citations/activity metadata). It explicitly flags token limits, latency, security/governance, and recommends chunking, hybrid keyword+vector retrieval, semantic reranking, and incremental indexing for freshness. Answer synthesis can return source citations but costs additional LLM tokens. Smartcat uses exact TM reuse, otherwise one best fuzzy TM pair in prompt, otherwise no context; Intento recommends retrieving only relevant glossary terms because full glossaries can distract/cost more and warns noisy/outdated TM harms RAG.",
      "sources": [
        "https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview?tabs=docs",
        "https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-information-retrieval",
        "https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-answer-synthesis",
        "https://help.smartcat.com/ai-rag-in-smartcat/",
        "https://inten.to/blog/improving-glossary-support-with-retrieval-augmented-generation/"
      ]
    },
    "options": [
      {
        "name": "Structured fields only",
        "what_it_is": "Keep persona, style, target-language instructions, glossary, unit metadata, and QA constraints as the complete AI context contract.",
        "stack_fit": "Lowest-risk fit for current weblate/machinery/llm.py, machinery/forms.py, and weblate/trans/judge.py/judge_loop.py; no index or file pipeline.",
        "pros": [
          "Lowest latency and token cost [inference]",
          "Highest producer control and straightforward versioning/audit [inference]",
          "Matches Phrase/Lokalise/Crowdin AI-ready summary patterns [verified: vendor URLs above]"
        ],
        "cons": [
          "Does not retain broad lore/quest facts unless manually distilled [inference]",
          "Manual curation and coverage gaps [inference]"
        ],
        "effort": "S; highly reversible"
      },
      {
        "name": "Raw-document RAG",
        "what_it_is": "Upload GDD/lore/style files, extract/OCR and structure them, chunk/embed/index, retrieve top-k passages for each unit, and inject passage citations/versions.",
        "stack_fit": "Large addition beside current prompt and judge code; would require document storage, async ingestion, index, ACL/scoping, refresh/versioning, retrieval evaluation, and prompt-injection defenses [inference]. Azure confirms these are core RAG concerns [verified: Microsoft URLs above].",
        "pros": [
          "Broad factual coverage and less manual distillation [inference]",
          "Fresh updates can be indexed without retraining [verified: Intento URL; Microsoft URL]",
          "Can expose provenance/page/section citations [verified: Azure answer-synthesis URL]"
        ],
        "cons": [
          "Extra ingestion/retrieval latency and recurring embedding/index/query-token cost [verified: Azure URLs; memoQ context-cost URL]",
          "Retrieval misses, irrelevant/conflicting chunks, stale docs, and untrusted instructions can mislead model [verified for relevance/token/noise: Azure/Intento; prompt-injection risk is inference]",
          "Security and permission trimming are non-optional [verified: Azure RAG URL]"
        ],
        "effort": "L; medium reversibility"
      },
      {
        "name": "Distilled document summary",
        "what_it_is": "Accept PDF/DOCX/Markdown, extract text, generate an AI-ready summary, require producer edit/approval, and inject only approved summary plus selected terms/rules.",
        "stack_fit": "Natural extension of current text fields; mirrors Lokalise and Crowdin product behavior and Phrase's AI-optimized style guide [verified: vendor URLs above].",
        "pros": [
          "Much smaller/cheaper prompt than raw docs [verified: Lokalise/Azure URLs]",
          "Human-editable, reviewable, and easy to version [verified: Lokalise/Crowdin URLs]",
          "Good control over persona/style/terminology [inference]"
        ],
        "cons": [
          "Summary may omit a needed lore fact or introduce a summarization error [inference]",
          "Still requires reprocessing/review when source docs change [inference]",
          "Not enough for deep, per-quest factual retrieval [inference]"
        ],
        "effort": "M; reversible"
      },
      {
        "name": "Hybrid structured + bounded RAG",
        "what_it_is": "Keep structured fields authoritative and always-on; optionally retrieve only relevant, approved document chunks for lore/setting questions. Attach document ID/version/section/page to each chunk; expose citations and retrieval trace; allow extraction of approved rules/terms into structured fields.",
        "stack_fit": "Best fit for current prompts: extend payload/context assembly in weblate/machinery/llm.py and judge payload only after separate ingestion/index service exists; preserves existing glossary/check/placeholder contract [inference]. Vendor patterns support this division: structured Style Rules/TB plus bounded TM/document retrieval [verified: Phrase/Lokalise/Smartling/memoQ/Smartcat URLs].",
        "pros": [
          "Balances broad lore coverage with deterministic persona/style/glossary control [inference]",
          "Retrieval cost scales with relevant chunks rather than entire GDD [verified: Azure/Intento URLs]",
          "Fresh documents can be re-indexed while approved structured fields remain stable [verified: Azure/Intento URLs]",
          "Citations/section provenance support producer review and debugging [verified: Azure answer-synthesis URL]"
        ],
        "cons": [
          "Most engineering and policy complexity; requires deciding precedence when retrieved prose conflicts with structured fields [inference]",
          "Needs chunk/retrieval evaluation and document ACL/version lifecycle [verified: Azure RAG URL]",
          "Potential extra latency on calls that invoke retrieval [verified: Azure RAG URL]"
        ],
        "effort": "L; reversible if feature-flagged"
      }
    ],
    "comparison_table": [
      {"option": "Structured fields only", "effort": "S", "risk": "Low", "stack_fit": "Excellent", "maintenance": "Low", "key_tradeoff": "Control/latency over lore coverage"},
      {"option": "Raw-document RAG", "effort": "L", "risk": "High", "stack_fit": "Poor today", "maintenance": "High", "key_tradeoff": "Coverage/freshness over cost/control"},
      {"option": "Distilled document summary", "effort": "M", "risk": "Medium", "stack_fit": "Very good", "maintenance": "Medium", "key_tradeoff": "Compact control over factual completeness"},
      {"option": "Hybrid structured + bounded RAG", "effort": "L", "risk": "Medium if gated", "stack_fit": "Best target architecture", "maintenance": "Medium-high", "key_tradeoff": "Broad coverage with explicit precedence and retrieval governance"}
    ],
    "recommendation": "Recommendation for human approval: adopt structured fields as the authoritative baseline now, add a producer-facing document upload that produces an editable, versioned AI-ready summary, and defer raw chunk retrieval to an opt-in hybrid phase. If raw RAG is added, retrieve only approved, project-scoped/language-scoped chunks for lore facts; never replace persona/style/glossary/checks with retrieved prose. Inject bounded top-k excerpts with document version and page/section citations, log the retrieval set, define precedence (structured hard constraints > glossary/QA/placeholder rules > retrieved lore > model priors), and provide a no-hit fallback. This follows the strongest common vendor pattern: Phrase/Lokalise/Crowdin distill uploaded guides into compact AI-ready context, while memoQ/Smartling/Smartcat retrieve bounded linguistic examples rather than dumping whole corpora. It preserves the fork's current low-cost text contract while leaving a controlled path to broad GDD coverage."
  }
}
```

A research caveat applies to Appendix C wording: vendor docs generally advertise RAG over linguistic assets (TM/glossary/style rules), not arbitrary GDD/lore knowledge bases. Phrase/Lokalise/Crowdin document uploads are style-guide ingestion followed by AI-ready summarization, not raw-doc retrieval. memoQ LiveDocs is the nearest raw reference corpus, but its AGT context is retrieved matches within a 2,048-char budget with no custom-prompt or citation UI. Treat the absence of documented generic GDD RAG as "not documented in reviewed official docs", not proof the feature cannot exist.

---

## Appendix D - ProducerOrgDesign (verbatim)

Full subagent finding: producer roles, minimal data contract, user-flow, and fill-rate incentives.

```json
{
  "report": "1. **Problem**\n\nHCGameLoc needs dozens of non-linguist producers to supply useful game-localization context without turning linguists into a data-entry or clarification bottleneck. The decision is how to divide authorship, factual approval, linguistic escalation, reuse, and accountability while fitting the Django/PostgreSQL Weblate fork's existing Project/Component/Translation/Unit model and current Project.instructions, Unit.context, Unit.note, Unit.explanation, screenshots, glossary, and LLM payloads.\n\n2. **Options**\n\n### Option A - Central context steward owns every context decision\n\n**What it is.** Producers submit a short brief and raw references, but a localization PM/context steward normalizes, completes, and approves all project and string context. Linguists only consume approved context and request changes.\n\n**Fit with this stack.** This is the smallest conceptual extension of the existing weblate/trans/models/project.py (Project.instructions, review settings), weblate/trans/models/unit.py (context, note, explanation, details), and weblate/trans/loc_kit.py. A new producer submission surface could write drafts alongside existing fields; the current LLM translation/judge payload can continue consuming approved fields. [inference]\n\n**Evidence and why it is a known pattern.** Crowdin separates Manager/Developer source-editing rights from Proofreader approval rights and Translator suggestion rights. [verified: https://support.crowdin.com/roles/] Transifex separates Project Maintainers, who update resources, from Reviewers, who approve translations. [verified: https://help.transifex.com/en/articles/6240965-the-ultimate-guide-for-managing-your-tasks-in-transifex] memoQ gives project managers broad resource permissions while translators/reviewers receive narrower project-scoped permissions. [verified: https://docs.memoq.com/current/en/Concepts/concepts-permissions-from-online-projec.html]\n\n**Pros.** Highest consistency and clearest accountability for factual and creative context; easy to enforce a single approval and audit trail before context reaches translation or LLM workflows; low risk of dozens of producers inventing incompatible terminology or contradictory lore. [inference]\n\n**Cons / risks.** Recreates the exact linguist/PM bottleneck the assignment is trying to remove; context owners become a new central operations team and may delay source-string intake; producers have weak ownership incentives because their work is handed off before it becomes usable. [inference]\n\n**Rough effort and reversibility.** M effort; highly reversible because it can be implemented as a workflow around existing fields before adding inheritance or deep automation. [inference]\n\n**Sources.** Crowdin roles [verified: https://support.crowdin.com/roles/]; Transifex task ownership and subtasks [verified: https://help.transifex.com/en/articles/6240965-the-ultimate-guide-for-managing-your-tasks-in-transifex]; memoQ project permissions [verified: https://docs.memoq.com/current/en/Concepts/concepts-permissions-from-online-projec.html].\n\n### Option B - Distributed producer authorship, source-owner approval, risk-based linguistic escalation\n\n**What it is.** Producers fill structured project and string cards, attach references, and submit. A game/source owner approves factual and creative meaning. Only ambiguous, high-risk, or linguistically sensitive context goes to a localization lead/linguist; ordinary context does not wait for linguist approval.\n\n**Fit with this stack.** It maps directly to existing Project and Unit records and existing project/component permissions. The fork would add a producer-facing context draft/status layer, typed forms, and queue/dashboard views while preserving the current per-string fields used by translation and judge prompts. Existing weblate/trans/loc_kit.py is a natural place to reuse/import project-level references, and weblate/trans/views/source.py/source-facing views are the likely integration point for producer queues. [inference]\n\n**Evidence and why it is a known pattern.** Crowdin's Developer/Translation Requestor can edit source text, context, and keys while Proofreaders approve translations, demonstrating separation between source/context authoring and approval. [verified: https://support.crowdin.com/roles/] Phrase explicitly separates project-manager, developer, and linguist roles and permits project-level overrides. [verified: https://support.phrase.com/hc/en-us/articles/5793349215900-Phrase-User-Management] Transifex recommends developer notes/string instructions/context fields and provides reviewer/proofreading stages separately. [verified: https://help.transifex.com/en/articles/6248331-providing-context] [verified: https://help.transifex.com/en/articles/6318604-reviewing-translations]\n\n**Pros.** Scales authorship across dozens of producers without requiring linguists to inspect every string; keeps factual approval with the people who know game mechanics, lore, and release intent (practitioner guidance: the developer/game owner performs product research and supplies the loc-kit [verified: https://www.gamedeveloper.com/business/a-step-by-step-guide-to-game-localization]); lets linguists spend time on ambiguity and high-risk exceptions; works with lightweight enums, tags, defaults, and one-line notes. [inference]\n\n**Cons / risks.** Source owners can approve factually plausible but linguistically unusable descriptions unless the risk classifier routes edge cases to a localization lead; requires explicit status, provenance, and stale-context rules; producer participation needs measurable queues and reminders. [inference]\n\n**Rough effort and reversibility.** M/L effort; moderately reversible. The form, status, and role layer can be introduced independently, while later inheritance and automation remain optional. [inference]\n\n**Sources.** Crowdin roles/editor [verified: https://support.crowdin.com/roles/] [verified: https://support.crowdin.com/online-editor/]; Phrase roles/review [verified: https://support.phrase.com/hc/en-us/articles/5793349215900-Phrase-User-Management] [verified: https://support.phrase.com/hc/en-us/articles/5784094755484-Review-Workflow-Strings]; Transifex context/review [verified: https://help.transifex.com/en/articles/6248331-providing-context] [verified: https://help.transifex.com/en/articles/6318604-reviewing-translations].\n\n### Option C - Federated series context with versioned inheritance and local producer deltas\n\n**What it is.** Context is layered as global brand/publisher rules, series/franchise rules, title rules, and string-specific overrides. A series steward approves reusable assets; each title producer fills only the delta. Inherited context is immutable by version, with explicit title overrides and provenance.\n\n**Fit with this stack.** The repository already has Project.workspace and project-level shared/workspace translation-memory settings in weblate/trans/models/project.py, and weblate/trans/loc_kit.py provides a project-level asset concept. The design should not conflate translation memory/glossary reuse with semantic context: context needs its own scope, version, and override lineage. [inference]\n\n**Evidence and why it is a known pattern.** Lokalise supports shared glossaries across projects, but warns that shared changes take effect in all projects using the glossary. [verified: https://docs.lokalise.com/en/articles/1400629-glossary] Crowdin supports shared TMs/glossaries assigned to relevant projects and can save only approved translations to TM. [verified: https://support.crowdin.com/translation-memory/] [verified: https://support.crowdin.com/glossary/] memoQ project templates can automatically bind project settings, people, TMs, and term bases, including matching resources by client/domain/subject. [verified: https://docs.memoq.com/current/en/Workspace/edit-project-template.html] [verified: https://docs.memoq.com/12-0/en/Workspace/edit-template-translation-memories.html]\n\n**Pros.** Best long-run throughput for sequels and related titles; preserves consistency for names, factions, UI conventions, and brand tone across titles; makes context completeness measurable at the layer where it is missing; project templates make the producer flow repeatable (memoQ documents templates as a way to create projects consistently with little PM work [verified: https://docs.memoq.com/current/en/Workspace/edit-project-template.html]). [inference]\n\n**Cons / risks.** Highest implementation complexity (scope resolution, versioning, stale inherited assets, conflict resolution, permission boundaries); shared glossary/context changes can unintentionally affect other projects (Lokalise shared glossary behavior [verified: https://docs.lokalise.com/en/articles/1400629-glossary]); a series steward can become a new bottleneck unless ordinary title-level overrides remain producer-owned. [inference]\n\n**Rough effort and reversibility.** L effort; least reversible once many titles depend on inheritance. [inference]\n\n**Sources.** Lokalise shared glossary [verified: https://docs.lokalise.com/en/articles/1400629-glossary]; Crowdin shared TM/glossary [verified: https://support.crowdin.com/translation-memory/] [verified: https://support.crowdin.com/glossary/]; memoQ templates and resource matching [verified: https://docs.memoq.com/current/en/Workspace/edit-project-template.html] [verified: https://docs.memoq.com/12-0/en/Workspace/edit-template-translation-memories.html].\n\n3. **Comparison table**\n\n| Option | Effort | Risk | Stack-fit | Maintenance | Key tradeoff |\n|---|---:|---|---|---|---|\n| A. Central steward | M | Low context-quality risk; high queue/bottleneck risk | High: wraps current Project/Unit fields and loc_kit | Medium | Quality control over producer throughput |\n| B. Distributed producer + source-owner gate | M/L | Medium: factual errors need escalation rules | High: typed forms/statuses can sit beside current Django models and LLM payload | Medium | Scales authorship while accepting risk-based review |\n| C. Federated versioned inheritance | L | Medium/high: stale or leaked shared context | Medium initially, high after implementation; aligns with workspace and loc_kit concepts | High | Maximum reuse versus lineage complexity |\n\n### Recommended role set and permission matrix\n\n**Recommendation for human approval: Option B as the base, with a narrow Option C inheritance layer for global and series assets.** This is a recommendation, not a decision. It retains producer self-service, puts factual approval with game owners, reserves linguists for exceptions, and adds reuse only where the organization can maintain versioned ownership.\n\nThe external TMS sources document translation roles more explicitly than context-approval roles. The context-specific separation below is therefore a proposed synthesis, marked as inference, grounded in their source-author/reviewer/translator distinctions. [inference]\n\n| Permission | Producer Context Author | Game/Source Owner | Localization Lead / Context Steward | Series Steward | Linguist / Translation Reviewer | TMS Admin / Automation |\n|---|---|---|---|---|---|---|\n| View project and string context | Yes | Yes | Yes | Yes | Yes | Yes |\n| Create/edit context drafts | Own projects/strings | Assigned titles | All assigned projects | Shared series layer | No; comments/requests only | Schema/API only, no semantic edits |\n| Upload/link screenshots, video, build, references | Yes for assigned title | Yes | Yes | Shared assets | No, except issue attachment | Automated import only |\n| Add glossary terms | Draft/proposed | Draft/proposed and title terms | Approve/edit shared and title terms | Approve shared series terms | Propose terms; no publish | Import/export only |\n| Submit context packet | Yes | Yes | Yes | Yes for shared layer | No | No |\n| Approve factual/creative context | No | Yes for assigned title | Yes for escalated/high-risk and policy compliance | Yes for shared layer | No | No |\n| Lock/publish approved context | No | No | Yes | Yes for shared layer | No | Workflow operation only |\n| Request clarification | Yes | Yes | Yes | Yes | Yes; request routes to owner | No |\n| Edit source key/text/technical metadata | No, except context-owned metadata | Yes under source-owner policy | Limited to context metadata | Shared conventions only | No | API/import where authorized |\n| Translate | No | Optional, normally no | Optional, normally no | No | Yes | No |\n| Approve translations | No | No | Optional process owner, not linguistic approver | No | Yes within assigned languages | No |\n| Manage templates/inheritance | No | No | Yes title templates | Yes shared series templates | No | Configure only under steward policy |\n| View completeness/KPIs | Own queue | Assigned titles | All assigned projects | Series rollup | Own work and requests | Organization-wide operational metrics |\n\n**Operating rule.** No Producer Context Author approves their own submitted context. For low-risk fields, Game/Source Owner approval is sufficient; high-risk, ambiguous, cultural, legal, voice, and market-sensitive fields require Context Steward or linguist escalation. [inference]\n\n### Minimal producer field checklist\n\nThe checklist intentionally distinguishes fields that are cheap and structured from deeper reference material. Game-l10n guidance consistently identifies source files/IDs, visual context, style, glossary, character/mechanics information, and communication/Q&A as loc-kit components. [verified: https://www.gamedeveloper.com/business/a-step-by-step-guide-to-game-localization] [verified: https://crowdin.com/blog/game-localization] [verified: https://www.studio-fugu.com/blog-posts/video-game-localization-kit-your-ultimate-guide]\n\n**Required once per project/title:** 1) Title ID, title/version, series/franchise (if any), producer owner, source-owner approver, escalation contact [inference]; 2) target locales/markets, platforms, launch wave, source freeze/release date [verified: https://www.gamedeveloper.com/business/a-step-by-step-guide-to-game-localization]; 3) genre, one-line premise or selected game archetype, target audience, age rating/content rating, content sensitivity tags (enums where possible) [inference]; 4) tone/style preset (serious/comedic/cozy/dark/heroic/technical), formality and profanity policy, publisher/brand DNT terms [verified: https://help.transifex.com/en/articles/6248331-providing-context]; 5) core terms list (character names, factions, locations, abilities, items, currencies, mechanics, trademarks, DNT) [verified: https://www.gamedeveloper.com/business/a-step-by-step-guide-to-game-localization] [verified: https://sandvox.io/loc-kit-guide/]; 6) at least one reference asset (playable build, recorded walkthrough, or stable video) and screenshots for key UI/gameplay flows [verified: https://sandvox.io/loc-kit-guide/] [verified: https://www.keywordsstudios.com/en/about-us/news-events/news/a-step-by-step-guide-to-game-localization/]; 7) technical rules (placeholder/markup syntax, plural/gender behavior, character/line limits, font/RTL constraints, source repository/file scope) [verified: https://crowdin.com/blog/game-localization]; 8) Q&A/escalation channel and response SLA [verified: https://www.studio-fugu.com/blog-posts/video-game-localization-kit-your-ultimate-guide].\n\n**Required per string (key/path imported automatically):** 1) stable key/ID and source text (file/path/component structural defaults) [verified: https://crowdin.com/blog/game-localization] [verified: https://www.studio-fugu.com/blog-posts/video-game-localization-kit-your-ultimate-guide]; 2) content type enum (button/CTA, menu label, tooltip, error, tutorial, quest, item/ability name, item/ability description, dialogue, subtitle, system message, marketing, legal, other) [inference]; 3) location/feature/screen/scene enum or path plus player action/state [verified: https://help.transifex.com/en/articles/6248331-providing-context] [verified: https://docs.lokalise.com/en/articles/2059009-key-editor-and-key-actions]; 4) dialogue-only fields (speaker, addressee, speaker gender, relationship, emotion/register) [verified: https://crowdin.com/blog/game-localization] (character notes: personality, register, relationships, quirks [verified: https://sandvox.io/loc-kit-guide/]); 5) purpose/meaning controls (action/object, warning vs instruction vs confirmation, polarity, name/verb/noun/sentence) with short text only for ambiguous cases [inference]; 6) technical confirmation (placeholders, variables, markup/tags, plural forms, variable semantics; auto-detect and confirm rather than retype) [verified: https://crowdin.com/blog/game-localization] [verified: https://help.transifex.com/en/articles/6248331-providing-context]; 7) space constraint (max chars/lines or explicit 'not constrained') [verified: https://help.transifex.com/en/articles/6248331-providing-context]; 8) risk tag (ambiguous, high-stakes, cultural/legal, mature, voice-over timing, layout-critical, ordinary) [inference]; 9) screenshot/reference link for every ambiguous/layout-critical/high-risk string [verified: https://docs.lokalise.com/en/articles/2045882-screenshots] [verified: https://help.transifex.com/en/articles/6229026-mapping-strings-to-screenshots]; 10) glossary/DNT link where applicable, otherwise explicit 'no controlled term' [inference].\n\n**Optional deeper fields:** chapter/quest/scene order, preceding/following lines, branch conditions, related keys [verified: https://www.studio-fugu.com/blog-posts/video-game-localization-kit-your-ultimate-guide]; character profile, speech quirks, relationship map, emotion, performance direction, voice-over timing [verified: https://sandvox.io/loc-kit-guide/] [verified: https://www.keywordsstudios.com/en/about-us/news-events/news/a-step-by-step-guide-to-game-localization/]; mechanics/effect formula, object image, ability demo video, item rarity, progression/system definitions [verified: https://www.studio-fugu.com/blog-posts/video-game-localization-kit-your-ultimate-guide]; puns, cultural references, source inspiration, legal/market restrictions, transcreation intent [verified: https://crowdin.com/blog/game-localization] [verified: https://www.keywordsstudios.com/en/about-us/news-events/news/a-step-by-step-guide-to-game-localization/]; screenshot coordinates, live/in-context preview, animation state, line wrapping, font fallback, platform variants [verified: https://help.transifex.com/en/articles/6229026-mapping-strings-to-screenshots] [verified: https://docs.lokalise.com/en/articles/2045882-screenshots]; links to approved examples, previous title/series context, related TM entries, replacement history [verified: https://support.crowdin.com/translation-memory/] [verified: https://docs.lokalise.com/en/articles/1409589-translation-memory]; GDD/document upload, full narrative bible, audio references, regional market briefs (optional references, not prerequisites) [inference].\n\n### Recommended producer user-flow and approval gates\n\n1. Choose title template (global and series context inherited read-only with version/provenance; only title delta editable) [inference].\n2. Complete project wizard (typed fields, owners, locales, platforms, style preset, terms, release dates, references; completeness distinguishes 'not applicable' from blank) [verified: https://support.phrase.com/hc/en-us/articles/10825060868380-Custom-Fields].\n3. Import strings (file/key/path, placeholders, markup, plural structure, known constraints auto-populated; batch tags/actions classify content) [verified: https://docs.lokalise.com/en/articles/2059009-key-editor-and-key-actions] [verified: https://help.transifex.com/en/articles/14287841-transifex-editor-filters-reference].\n4. Fill producer queue (missing minimum fields and high-risk first; enums/autocomplete, screenshots/video, one-line explanation only when controls do not resolve ambiguity) [inference].\n5. Submit context packet (Project status becomes 'Submitted'; each string records author, timestamp, source/version, inherited fields, changed fields) [inference].\n6. Game/source-owner gate (batch-approves factual/creative context; rejects with structured reason or returns a bounded subset; main approval gate, no linguist) [inference].\n7. Risk-based localization gate (Context Steward/linguist reviews only ambiguous, cultural/legal, voice, layout-critical, or context-readiness-failed strings) [inference].\n8. Translation and linguistic review (linguists see approved context, glossary, screenshots, technical constraints, provenance; inline clarification request routes to responsible producer/source owner with SLA) [verified: https://support.crowdin.com/online-editor/].\n9. Context change handling (source/key/path/screenshot/glossary/semantic change creates a new context version and marks affected translations for review on meaning change) [verified: https://support.phrase.com/hc/en-us/articles/5784094755484-Review-Workflow-Strings].\n10. Release gate (dashboard shows context readiness + translation + review + QA + stale state; block/waive only high-risk missing context) [verified: https://translated.com/resources/how-to-run-continuous-localization-a-step-by-step-guide].\n\n**Approval ownership summary.** Producer authors; Game/Source Owner approves factual/creative meaning; Context Steward approves shared-layer changes and high-risk/context-readiness exceptions; Linguist approves translations, not producer context; TMS Admin controls schema/permissions/automation but does not semantically approve. [inference]\n\n### How to keep fill-rate high\n\n1. Make the minimum small and typed (dropdowns, checkboxes, tags, autocomplete, 'not applicable', auto-populated structural fields; avoid per-string paragraphs) [verified: https://lokalise.com/blog/automate-context-management/].\n2. Use defaults and inheritance (template defaults for title metadata, tone, platform, glossary, DNT rules; inherit stable global/series fields; require only changed deltas) [verified: https://docs.memoq.com/current/en/Workspace/edit-project-template.html] [verified: https://support.crowdin.com/glossary/] [verified: https://docs.lokalise.com/en/articles/1400629-glossary].\n3. Capture context at source creation (import key/path/component, variable semantics, plural structure, file context; CI/build hooks attach screenshots) [verified: https://lokalise.com/blog/automate-context-management/] [verified: https://crowdin.com/blog/software-localization].\n4. Provide bulk operations (select feature/scene and apply content type, screen, user state, risk, limits, screenshot to many strings; filter views for 'context missing', 'no screenshot', 'high risk') [verified: https://help.transifex.com/en/articles/6229026-mapping-strings-to-screenshots] [verified: https://help.transifex.com/en/articles/14287841-transifex-editor-filters-reference] [verified: https://docs.lokalise.com/en/articles/2045882-screenshots].\n5. Make missing context owner-specific (dashboard/notifications by title, feature, producer, age, with links and due dates) [verified: https://help.transifex.com/en/articles/14287841-transifex-editor-filters-reference] [verified: https://support.crowdin.com/project-settings/privacy-collaboration/] [verified: https://help.transifex.com/en/articles/6240965-the-ultimate-guide-for-managing-your-tasks-in-transifex].\n6. Use risk-based gates, not universal friction (require screenshot/build evidence and specialist approval only for ambiguous, layout-critical, legal/cultural, voice, high-impact strings) [inference].\n7. Turn context into release readiness (title-level target: minimum-field completion, high-risk screenshot coverage, open-request age, stale-context count; make producer queue visible in release dashboard; explicit exceptions) [verified: https://translated.com/resources/how-to-run-continuous-localization-a-step-by-step-guide].\n8. Measure rework, not prose volume ('% minimum-complete', '% high-risk complete', context requests per 100 strings, median request resolution time, reopened/retranslated strings after context change, first-pass review, producer/owner SLA) [verified: https://lokalise.com/blog/automate-context-management/].\n9. Version shared context and show stale markers (show which title/string versions are affected by changed source/screenshot/glossary/inherited rule) [verified: https://support.phrase.com/hc/en-us/articles/5784094755484-Review-Workflow-Strings] [verified: https://translated.com/resources/how-to-run-continuous-localization-a-step-by-step-guide].\n10. Reward closure and quality (on-time minimum completeness, low context-request/rework rates, high-risk coverage; not optional prose volume) [inference].\n\n**Recommendation.** Human approval is requested for a hybrid of Option B and the limited, versioned part of Option C: distributed producers should author structured drafts; Game/Source Owners should approve factual and creative context; Context Stewards should own templates, shared/series inheritance, and only high-risk linguistic/context exceptions; linguists should translate/review and request clarification, not serve as universal context approvers. This best fits the current Django/Weblate model and current LLM context fields while removing the central bottleneck, and it preserves a path to sequel/series reuse without accepting silent cross-title changes. The main tradeoff is that HCGameLoc must add explicit context status, ownership, provenance, stale-version handling, and owner queues; those controls are the price of scaling producer input safely. [inference]"
}
```