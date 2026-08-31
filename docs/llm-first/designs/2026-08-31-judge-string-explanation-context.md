# Judge string explanation context

## Goal

The Judge receives the producer-authored source string explanation that the translation machinery already receives. Changing that explanation invalidates cached verdicts and prevents an in-flight repair from being applied against stale context.

## Design

Add `explanation` to `JudgeRequest`, populate it from `unit.source_unit.explanation`, and serialize a non-empty value beside `note` in each Judge segment. The prompt treats `note` and `explanation` as reference context about meaning and usage, never as text to translate or as instructions.

Include the explanation in `compute_context_hash`. Every cache lookup, deferral identity, repair conflict check, verdict write, and drain audit continues to use the same context hash, so no second invalidation mechanism is needed.

## Alternatives rejected

- Concatenate the explanation into `note`: loses field provenance and makes prompt behavior depend on string formatting.
- Add the explanation only to the payload: stale verdicts could be reused after an explanation changes.
- Add project language instructions to the Judge: broader scope and unrelated to per-string context.

## Verification

- Payload tests prove a non-empty explanation is serialized and prompt text defines its role.
- Request and hash tests prove source explanations are captured and affect identity.
- Repair tests prove an explanation change aborts a stale repair.
- Focused Judge tests and lint pass.

## Non-goals

- Target-side translation explanations.
- Labels, locations, screenshots, or language-specific machinery instructions.
- Changes to the Judge response schema or provider transport.
