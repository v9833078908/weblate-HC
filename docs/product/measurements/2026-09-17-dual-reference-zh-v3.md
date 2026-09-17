# Dual-reference zh-Hans: recovery and v3 screening

## Current status

The owner authorized an eight-record end-to-end smoke of all six conditions,
followed by the frozen 120-record screening pilot only if the smoke succeeds.
No confirmatory inference or production translation update is authorized here.

## Registered changes before inference

- Fix the circular reference in terminal failure records. Fault injection
  reproduced the v2 serialization failure before the fix.
- Persist every attempt and completed batch to an append-only JSONL journal,
  flushing and synchronizing each event. Successful attempts retain their parsed
  output as well as the receipt. Publish the final JSON atomically. Refuse to
  overwrite an existing journal or result.
- Apply at most two attempts to transport errors, malformed responses and
  the registered transient HTTP statuses. HTTP 403 remains terminal.
- Validate translations, reviews and ratings, including consistency between
  severity and the unusable flag. Invalid responses never become a pass.
- Keep randomized paired blocks and stable input order for later stages.
- Increase Gemini's maximum output from 1024 to 8192 tokens for every
  generation/editor condition before inspecting v3 outputs. Model, temperature,
  five-record batches and two production judge aliases remain unchanged.
- Retain the documented non-streaming judge deviation. Read frozen local
  texts, verifying their hashes; do not fetch game Units again.

The smoke contains the shortest and longest combined RU/EN record in each of
the four strata, with ties broken by record ID. This is a technical coverage
sample, not an efficacy estimate. Its outcomes are not pooled with the pilot.
Both runs use seed 20260916 and preserve the same B draft for E and F.

Before full inference, the smoke must provide 48 valid translations, 96 valid
proxy ratings and 32 valid reviewer records, with no terminal batch failure.
The final artifact and durable journal must parse, agree and include every
assigned ID. All six conditions must be included in the offline analysis.

Local registrations and artifacts:

- `/Users/eli/Downloads/dual-reference-zh-2026-09-16/study/2026-09-17-screening-v3-smoke/`
- `/Users/eli/Downloads/dual-reference-zh-2026-09-16/study/2026-09-17-screening-v3-pilot/`

The existing no-monetary-cap decision is retained with a fixed operation
budget: 8 smoke records, then 120 pilot records only after the gate, at most
two attempts per job. v1 and v2 outcomes are not pooled into v3.

## Offline validation

35 protocol and recovery tests passed with no model calls. They include
terminal-failure serialization, timeout/504/malformed-response recovery,
durable batch readback after forced process exit and rejection of malformed rating/review/translation
items. Scoped Ruff check and formatting passed. The wider pre-commit run
reported unrelated repository-wide license and spelling failures outside
these changed files.

No claim about the superiority of EN or two references follows from passing
the smoke. The corpus remains screening-only; human review provenance and
independent Chinese LQA remain unestablished.
