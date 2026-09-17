# JudgePayload

Полный итоговый вывод субагента. Не все исходные гипотезы подтверждены: см. `docs/operations/measurements/2026-09-17-judge-cost-investigation/corrections.md`.

## summary

Read-only audit found that each judge POST repeats a large fixed system prompt and strict response schema, while each segment carries source/target plus optional rendered previews, notes/explanations, clarifications, all matched glossary metadata, and deterministic check names. Output always requires a whole-target back translation and can emit unbounded error descriptions because max_tokens defaults to zero. Usage persistence records provider-reported token/cost fields but loses provider-specific cache fields, has no local price model or prompt-component accounting, and the report command omits cached/reasoning totals.

## files

```json
[
  {
    "path": "weblate/trans/judge.py:879-935",
    "description": "Builds per-segment JSON: source/target, rendered placeholders, note/explanation/clarification, glossary entries, and failing check names."
  },
  {
    "path": "weblate/trans/judge.py:1464-1513",
    "description": "Persists provider usage, nested cached/reasoning fields, provider cost, and batch attribution; skips rows when both token counts are absent."
  },
  {
    "path": "weblate/trans/judge.py:1624-1690",
    "description": "Builds repeated system/user messages, strict schema, optional max_tokens, OpenRouter parameter requirement, and provider-specific reasoning payload."
  },
  {
    "path": "weblate/trans/judge_prompts/verdict.txt:1-119",
    "description": "Fixed judge instructions, one-segment context limitation, glossary semantics, exact JSON contract, and mandatory back_translation."
  },
  {
    "path": "weblate/glossary/models.py:407-475",
    "description": "Glossary prompt serialization and full matched-term selection with explanations and flags."
  },
  {
    "path": "weblate/trans/models/llm_usage.py:31-187",
    "description": "LLMUsageLog fields and run_spend aggregation; provider-reported cost/token model."
  },
  {
    "path": "weblate/trans/management/commands/llm_usage_report.py:23-58",
    "description": "Usage report fields and aggregates; excludes cached/reasoning token columns."
  },
  {
    "path": "weblate/trans/defaults.py:73-95",
    "description": "Reasoning, response format, batch, and max_tokens defaults; max_tokens=0 means no output limit."
  },
  {
    "path": "docs/product/measurements/2026-09-03-judge-thinking-passthrough.md",
    "description": "Measured alias-specific reasoning passthrough: seat 2 no-think dramatically reduced output/latency; seat 1 lost recall and remained thinking-enabled."
  },
  {
    "path": "analysis/data/col4-model-eval.json:1-180",
    "description": "Offline usage examples showing provider/model variance in prompt, cached, completion tokens, cost, retries, and latency."
  }
]
```

## architecture

JudgeRequest is assembled in weblate/trans/judge_loop.py:build_request, glossary matching is source-local/full+variants, and judge.py serializes each batch into one system prompt plus one untrusted JSON user message. Two seats and fallback profiles send independent requests. Raw provider usage is logged at the response seam into LLMUsageLog and aggregated by run, but no local tokenizer or authoritative price table exists.

## report

Ranked findings and recommendations:

1. Highest likely input-cost term: repeated fixed prompt/schema. verdict.txt is sent on every request; the response schema is regenerated per batch and its array bounds encode batch size. Larger batches amortize this fixed cost but increase decode latency, failure blast radius, and split-retry work. Measure prompt_tokens per request against batch size and provider cached_tokens before changing batch width.

2. Highest likely output-cost term: mandatory whole-target back_translation plus English error descriptions. The schema requires back_translation for every segment and verdict.txt requires it to be literal; descriptions are unconstrained. This can dominate completion tokens, especially with reasoning-enabled models. A measured cap or compact evidence format is a candidate optimization only after p95 output and unparsed/refusal rates are known; quality risks are producer usability, missing actionable rationale, and schema refusals.

3. Reasoning control is alias-specific, not generic. The 2026-09-03 measurement shows seat 2 `extra_body.enable_thinking=false` changed mean completion 945→72 and reasoning 870→0 with latency 34.8→2.9s, while the same setting on seat 1 reduced labeled recall and was not adopted. Any further reasoning-off change requires per-model calibration; token savings cannot establish quality preservation.

4. Glossary scope is not project-wide or neighbor context: get_matched_glossary_prompt_entries uses full matched terms and variants for the unit, then serializes source/target, both explanations, and flags. This is good scope discipline but can duplicate long metadata per segment. Compacting entries or dropping explanations has terminology-disambiguation risk; first measure bytes/tokens by field and false terminology detections on a labeled slice.

5. There is no neighbor/context duplication in the current judge request. The prompt explicitly limits evidence to one segment and `_segment` has no neighboring units. Project persona/style is inserted into the shared system prompt; it is repeated for every request and varies by project.

6. Cost accounting is provider-dependent and incomplete. `_write_llm_usage` accepts `usage.cost`, then LiteLLM's `x-litellm-response-cost`; it does not compute cost locally. Only nested `cached_tokens` and `reasoning_tokens` names are recognized, numeric non-int variants become zero, and no cache-read/cache-write distinction, currency, rate class, or input/output price is stored. Missing token usage suppresses the usage row. `run_spend` has cached/reasoning totals but the CSV report omits them. Therefore token counts can establish provider-reported usage for rows successfully logged, but cannot establish dollars, cached-input discount, cache eligibility, paid attempts with absent usage, or whether reasoning is included in completion_tokens.

7. Prompt-cache eligibility is unknown. The random boundary token is inserted immediately before variable segment JSON, and schema size changes with batch size; these may reduce reusable prefix length. Whether the provider caches system/schema prefixes, cache TTL, minimum prefix size, and billing discounts are not represented. Request exact provider cache fields and billing export/rate card; do not infer savings from cached_tokens alone.

Requested live evidence/settings: resolved primary/fallback alias and upstream model plus alias revision; provider, response format, batch size, max_tokens, reasoning payload actually sent, temperature, stream; request/response IDs, finish reason, HTTP/retry/fallback outcomes; full redacted usage object including prompt_tokens_details and completion_tokens_details, cache read/write fields if present, provider cost and currency; per-request prompt bytes/hash and field-level byte estimates in an offline dry run; provider dashboard billing export/rate class. Keep HTTP attempts, successful responses, parsed verdicts, logged usage rows, billed requests, strings, and dollars as separate denominators.

## Дополнительные сообщения в ходе исследования

Сохранены только тексты исследовательских выводов, без служебных записей сессии. Порядок хронологический; ранние предположения могут быть опровергнуты следующими сообщениями.

### Сообщение 1

Findings (read-only): payload is weblate/trans/judge.py:879-935,1624-1690. Every HTTP request repeats full verdict.txt system prompt; schema JSON is rebuilt per batch (`minItems=maxItems=batch size`) and user JSON repeats boundary + warning + all segments. Random token_hex boundary inserted immediately before segments, potentially harms provider prefix-cache beyond stable warning. No neighbor/context duplication: each request segment only key/source/target, optional rendered pair, note/explanation/clarification, full matched glossary, failing checks; prompt explicitly says one segment, no unseen conventions. `_segment` lines 901-934; no neighbors. Glossary source weblate/glossary/models.py:407-475: matched full=True/include_variants=True, excludes not-applicable/untranslated non-readonly, serializes source/target + both explanations + flags; dedup only exact serialized entry. Thus glossary variants/explanations/flags can dominate input and are per-segment repeated, but scope is source-match, not project-wide. `forms.py:704-716` explanation max 1000 chars; clarification also sent if present. Checks are only names (`failing_checks` list), no descriptions (judge rules say don't repeat deterministic checks). Outputs require every segment id/verdict/errors/back_translation (`judge.py:883-903`), and prompt demands whole back_translation + English descriptions (`verdict.txt` lines ~74-123); errors can be verbose/unbounded except provider max_tokens. `defaults.py:72-79`: default reasoning empty, json_schema, stream false, temperature 0, max_tokens 0 means no output limit. Profile validation `judge.py:486-546`; only sends max_tokens if nonzero at 1683-84. Response-schema strict/additionalProperties false, but schema input token cost is provider-dependent/unknown.
Usage/accounting: `_write_llm_usage` `judge.py:1464-1513` records provider prompt/completion/total, nested prompt cached_tokens, nested completion reasoning_tokens, provider cost; silently skips row when both prompt/completion absent. `total_tokens` fallback prompt+completion; no local tokenizer, no per-field breakdown, no prompt hash/bytes, no cache eligibility/hit type, no input/output/reasoning prices. `LLMUsageLog` model `weblate/trans/models/llm_usage.py:31-180`; report command only prompt/completion/cost (`management/commands/llm_usage_report.py:21-60`), omits cached/reasoning in CSV. Cost source: provider `usage.cost`, fallback LiteLLM header `x-litellm-response-cost` (`judge.py:1057-1071`,1473-1476); docs comment says hcbifrost usage has counts only. No authoritative price table; `cost_usd` is provider-reported/nullable. Cached-input billing cannot be derived from total tokens or cached_tokens alone without provider rate card and whether cached tokens are included in prompt_tokens; ask provider billing export/rate card + request/response usage fields.
Measured artifacts: prior doc `docs/product/measurements/2026-09-03-judge-thinking-passthrough.md` confirms seat2 extra_body no-think 945→72 completion, 870→0 reasoning, 34.8→2.9s; seat1 no-think recall loss, so reasoning-off is model-specific, not a generic optimization. Offline provider examples `analysis/data/col4-model-eval.json:1-180`: batch10 150-string arms show prompt 67,828–89,638, cached 21,173–51,254, completion 7,069–72,081; costs $0.023677–$0.809038, proving model/provider variance and that cached_tokens exists in external harness, but not directly comparable (different harness/prompt). `analysis/data/col4-judge-probe.json` has tiny 2-record probes with prompt 526–1318 and completion 40–188; use only illustrative. Existing docs note alias/provider behavior; do not infer exact billing from token counts.
Priority hypotheses: P0 measure per-request usage by seat/model/run with prompt/completion/cached/reasoning/cost + batch size; distinguish attempted/HTTP/parsed/paid. P1 reduce output: retain structured error essentials but cap descriptions/back_translation (quality risk: producer usability/recall); enforce max_tokens only after observed p95 and refusal/unparsed rate. P1 seat2 no-think already high leverage measured, but require larger labeled calibration; never disable seat1 blindly. P2 glossary pruning/compact encoding: send only applicable matched terms and concise explanations; quality risk terminology misses. P2 remove duplicated fixed prompt/schema only if provider supports cached prefix or prompt caching; random boundary and per-batch schema may defeat cache; verify with provider cached-input fields. P3 test batch size with provider usage/latency (larger batches amortize system/schema but force serial decode and increase failure split/retry blast radius). Requested live fields/settings: exact resolved seat/fallback alias/upstream + alias revision, response_format, max_tokens, reasoning payload actually sent, batch size; request_id/response_id, finish_reason, HTTP/retry/fallback, provider usage prompt_tokens_details (cached_tokens/cache_creation if any), completion_tokens_details.reasoning_tokens, cost + currency/rate class; prompt byte/hash and per-field byte/token estimates in offline dry-run. Unknown: whether provider counts schema/system in prompt_tokens, cache TTL/prefix rules, cached token discount, whether LiteLLM header cost includes cache/reasoning markup.

### Сообщение 2

Exact anchors: verdict.txt:1-6 fixed role/rules; :44-48 exact JSON + four fields; :51-60 note/explanation + glossary; :116-119 mandatory full back_translation. Defaults exact `weblate/trans/defaults.py:73-79,82-95`. Usage report header/totals omit cached/reasoning: `weblate/trans/management/commands/llm_usage_report.py:23-58`; row output :77-100.

### Сообщение 3

Additional accounting caveats: `_usage_integer` (`judge.py:1430-1431`) accepts only Python int; provider-specific numeric/string fields or alternate names are treated as zero. Only `prompt_tokens_details.cached_tokens` and `completion_tokens_details.reasoning_tokens` are recognized (`:1481-1509`); cache read/write fields under other names are lost. `run_spend` does aggregate cached/reasoning (`llm_usage.py:164-187`) but `llm_usage_report` drops them. A missing/zero usage payload causes no LLMUsageLog row (`judge.py:1477-1480`), so transport/stream responses with absent usage can be financially unobservable even if billed; HTTP attempts remain separate. `total_tokens` fallback is prompt+completion, so cannot reconstruct provider billing components. Ask live payload samples (redacted) from each alias and provider dashboard invoice/export to map field semantics and cached-input rates.
