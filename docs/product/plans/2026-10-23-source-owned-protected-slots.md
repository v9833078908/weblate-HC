# Source-owned protected slots for routed translation

Status: complete; approved implementation deployed and both configured model families verified on production, 2026-10-23.

## Goal and evidence

Pirate Ships sprite repairs must not rely on a model deciding to reproduce a
protected tag. Live probes after the VPN repair returned HTTP 200: DeepSeek
returned only text, which the current provider schema permits and the local
parser correctly refuses. The Gemini diagnostic replies were truncated by the
probe's output cap. Do not weaken the parser or append tags to a finished text.

The existing REST API, project routing, credentials, judge repair application
policy, source and target storage remain unchanged. No provider fallback or
automatic retry is introduced. The user separately approved at most two more
live requests, total additional cost at most $0.10, without storing translations.
After those probes exposed a placement refusal, the user approved a second pair
under the same $0.10 ceiling, still without storing translations.

## Internal model contract

Change only the routed services' private output representation. Inputs retain
`strings`, string ids, source `parts`, and context. Output is a closed object:

```json
{"translations":{"s0":{"text":["Armor against "," weapons: +10%"],"placeholders":{"p0":{"position":1}}}}}
```

For a source with N placeholder parts:

- `translations` requires exactly the input string-id keys.
- `text` requires exactly N+1 strings. A string may be empty. Translate the whole
  source, then split the translated text around protected elements.
- `placeholders` requires exactly `p0` through `p(N-1)`, naming source placeholder
  occurrences in source order, not copying ids or markup into model output.
- Each placeholder has integer `position` in 0..N: insert it immediately before
  that text slot. Equal positions retain source order. Distinct positions allow
  grammar placeholders to reorder. Required slots also preserve repeated source
  placeholders without key collisions.
- Only translatable placeholder parts additionally require a `text` string for
  their wrapped human-readable content.
- The server copies type/id/kind/role/close_id/translatable and immutable content
  from the source. It never guesses absent slots or accepts raw inserted tags.
- Decode to the existing internal parts list and run the unchanged base parser,
  including placeholder multisets, ordered markup/syntax, grammar reorder and
  surrounding-segment boundaries. A position is a model choice, not permission
  to cross protected boundaries.
- Reject missing/extra ids, missing/extra slots, wrong text arity, non-integer or
  out-of-range positions, malformed JSON, and invalid protected placement.
  No old wire-format alias or bare-array pass-through remains.
- Reject duplicate JSON member names at every object level, booleans and
  non-finite numeric positions, and extra fields without coercion.
- Decoder failures record the existing refused usage outcome, reason, and
  original response exactly once before raising `MachineTranslationError`.
  Malformed objects never enter the base parser's JSON-repair path.
- Where immutable markup alternates with N+1 nonempty source text segments,
  require nonempty output text slots (`minLength: 1`) and the corresponding
  fixed occurrence positions (`enum`). This expresses the existing segment
  boundary check before generation; grammar, syntax and empty edge slots retain
  their existing flexible representation.

Use standard closed-object/required/enum/string/array/integer bounds only; no
`contains`, `prefixItems`, `allOf`, or model-specific schema keywords. Provider
schema is strict. The decoder also validates independently for the LiteLLM proxy.
Convert previous assistant examples to the same representation; separate shared
semantic translation rules from the routed output instructions instead of
leaving contradictory old array examples in the prompt.
Match previous target occurrences to source occurrences by token identity and
occurrence index, preserving repeated and reordered examples. Decoder source
selection must mirror request preparation, including explicit plural occurrences.

## Files and outcomes

- [x] `weblate_customization/src/weblate_customization/machinery.py`: schema from
  actual input strings, routed prompt/example representation, strict decoder and
  reconstruction into existing parts. Preserve sync/async routing and transport.
- [x] `weblate/machinery/llm.py`, only if needed: expose a shared semantic prompt
  boundary so routed services reuse existing translation rules without the old
  output-format instructions. Plain OpenAI output stays behaviorally unchanged.
- [x] `weblate_customization/tests/test_machinery.py`: retain routing/usage/form
  tests, migrate fake HTTP replies, replace incidental schema-copy assertions
  with consumer behavior and negative boundary cases. Test real sprite restoration,
  missing slots, extra slots/ids, repeated markers, grammar reordering, wrapper
  text, ordered-markup refusal, no-marker text, batches and sync/async paths.
- [x] Update the owning producer guide and unreleased changelog after smoke proof.
- [x] Commit only task files, fast-forward main, push, deploy from the clean
  worktree. Preserve the unrelated operations-plan edit in the main checkout.

## Verification and rollout gates

Run failing-before regressions for the saved all-text omission and source-owned
slot reconstruction before implementation. Run routed tests plus affected base
LLM and judge/API tests against isolated native PostgreSQL, with fake HTTP only.
Use a standalone sync/async smoke to exercise schema-to-decoder-to-exact-tag
restoration. Review before merging. Verify actual running OCI revision and worker
health after rollout, not only the deployment command.
Include markup-boundary versus syntax-mobility cases, equal-position stable
ordering, endpoints and adjacent markers, wrapper empty/forbidden content,
wrong singleton identity, duplicate keys, and malformed/missing-slot refusal
accounting in sync and async paths. A schema rejection or a material rise in
decoder refusals rolls back the application revision, never to permissive
decoding or tag-appending fallback behavior.

Use at most two additional real requests, one per configured model family, with
an adequate output cap and a conservative per-request price ceiling of $0.05.
Record results idempotently before sending, inspect saved replies instead of
repeating paid calls, and verify markup multiset equality and unchanged stored
unit target/state. No mass translation run or historical queued-run restart.

## Observed verification

- Failing-before protected-slot suite: 8 failed assertions/subtests, including
  the old schema accepting a text-only reply without the sprite occurrence.
- Final routed and plain OpenAI suites: 232 passed, 2 skipped. The broader run
  also passed all 193 selected judge-loop, judge-autotranslate and REST cases.
  Its only failure was an old prompt-wording test; deleted rather than re-pinned.
- Standalone downloads: exact source sprite restored for OpenRouter and LiteLLM,
  both sync and async; unit target/state unchanged; fake HTTP, no paid requests.
- Plain-provider rendered prompt SHA-256 unchanged:
  `ebd75d2bddb0adeaddc155a0dda7bcc9cd0e4d599cb3273c662b5ea93c0efce9`.
- Scoped pre-commit checks passed, with the previously established baseline
  global REUSE failure excluded. Code review found no actionable defects.
- `fad32b18` was deployed and independently verified in the running image.
  Gemini passed exact markup preservation; DeepSeek returned the required slot
  but put all text before it, correctly refused by the unchanged validator.
  These two probes cost $0.00953011, without storing translations.
- Placement refinement: three failing-before schema subtests; all 25 protected
  slot regressions pass after requiring interior text and fixed markup positions.
  Follow-up review found no actionable defect; scoped pre-commit checks passed.
- Production checkout and running OCI image independently verified at
  `03ef9d855455f65c168ff791748d80c506d55cb4`; healthy container, login HTTP 200,
  all supervisor programs running.
- Final English Gemini probe: HTTP 200, finish `stop`, usable decoded answer,
  exact sprite and full markup multiset preserved, stored unit target/state
  unchanged; cost $0.01022850.
- Final Korean DeepSeek probe: HTTP 200, finish `stop`, usable decoded answer,
  exact sprite and full markup multiset preserved, stored unit target/state
  unchanged; cost $0.00199375.
- Final pair cost $0.01222225, below its separately approved $0.10 ceiling.
  Replies were recorded idempotently in private files, without retries or
  saving translations. No historical queued run or mass translation was launched.
