# Judge segment-count recovery

**Status:** implemented locally; awaiting production canary and deployment approval.

## Goal

Close the response-contract gap that allows a schema-valid but unusable empty
``segments`` array, and recover a residual exhausted primary failure through
the already configured OpenRouter fallback.

The change is prompted by production JudgeRun
``36a5be4a-b2d8-42a7-a934-23eb76800737`` on
``need-for-greed/ui/es``. Its LiteLLM seat 2,
``atlas/qwen3.8-max``, returned HTTP 200, ``finish_reason=stop`` and
``{"segments": []}`` for single-item batches. That response is complete at the
transport layer but fails the judge parser as ``segment-count``.

## Decision

Make two deliberately narrow changes:

1. Generate the strict JSON Schema with the exact cardinality of the current
   request batch. For a single-item seat-2 request, ``segments`` must have
   exactly one item.
2. Once the existing primary protocol retry opportunity for
   **``segment-count`` only** is exhausted, or cannot run within the shared
   retry budget or deadline, make one configured OpenRouter fallback request.
   Keep every other protocol failure outside the fallback policy.
3. Bump the prompt/schema revision so cached verdict identities change with
   the response contract.

This is not a general second-opinion mechanism. It preserves the existing
closed fallback policy for all other failure kinds, including the intentional
``http-auth`` exception to availability failures, but recognizes that an empty
result array for a one-item batch is a repeated provider-contract failure that
the primary retry has already failed to repair.

The schema change addresses the application-side contract gap. It does not
prove why the upstream model returned an empty array, and some providers may
reject array-cardinality keywords. A non-persisting endpoint compatibility
canary is therefore a pre-deployment gate rather than proof that the defect is
eliminated.

## Observed impact

The production run completed all 462 units with no all-seat unparsed outcome,
because seat 1 supplied a parsed opinion for every affected unit. Seat 2
encountered ``segment-count`` on 34 unique units: 16 recovered on protocol
retry and 18 finished with only seat-1 evidence. The retries added 34 paid
requests and 128,555 tokens; the provider did not report prices for those
rows.

## Scope

Included:

- ``weblate/trans/judge.py`` schema cardinality and the one failure-kind policy
  change.
- Focused unit tests in ``weblate/trans/tests/test_judge_client.py``.
- Four ``analysis/probes/`` callers of the private schema helper.
- A small, non-persisting real-endpoint compatibility canary before deployment,
  with separate production approval.

Excluded:

- Changing either production seat model, prompt text, batch width, streaming,
  deadline, thinking settings, or the configured fallback pair.
- Reprocessing or mutating the completed production run.
- Broadening fallback to ``invalid-json``, ``invalid-envelope``,
  ``invalid-segment``, ``empty-response``, or ``finish-length``.
- Changing the durable deferral policy.

No migration is required.

## Implementation tasks

### 1. Pin the schema and parser contracts with tests

In ``weblate/trans/tests/test_judge_client.py``:

1. Add a test for a one-item payload that asserts the generated strict schema
   has ``segments.minItems == 1`` and ``segments.maxItems == 1``.
2. Add the equivalent assertion for a multi-item payload, proving the bounds
   are derived from the actual batch rather than hard-coded for seat 2.
3. Retain the existing parser test for an empty response array and assert it
   remains classified as ``segment-count``. The schema is a prevention layer;
   the parser remains the final defensive gate if a provider ignores schema
   bounds.
4. Add a failover test with this sequence:

   - LiteLLM primary returns an otherwise valid ``{"segments": []}``;
   - its one configured protocol retry returns the same;
   - OpenRouter fallback returns one valid segment;
   - the result is parsed and records OpenRouter as its serving provider.

   Assert the call order is primary, primary retry, fallback. Add a separate
   exhausted-shared-budget test that expects primary, fallback without claiming
   that a retry was sent.
5. Update the closed-set fallback test so it asserts that only
   ``segment-count`` joins the existing failover kinds. Keep explicit
   assertions that every other protocol failure remains primary-only.

### 2. Encode exact response cardinality

In ``weblate/trans/judge.py``:

1. Change ``_response_schema()`` to accept the request batch size.
2. Add both ``minItems`` and ``maxItems`` with that size to the ``segments``
   array schema.
3. Pass ``len(batch)`` from ``_payload()`` into ``_response_schema()``.
4. Bump ``PROMPT_SCHEMA_REVISION`` so the new contract invalidates cached
   verdict request identities.

Do not remove the parser's ``len(segments) != size`` check. Providers can
ignore or partially enforce strict-schema constraints, and the application
must still reject an answer it cannot map one-to-one to units.

### 3. Add the narrow fallback case

In ``weblate/trans/judge.py``:

1. Add ``segment-count`` to ``_FAILOVER_FAILURE_KINDS``.
2. Leave the protocol retry branch unchanged, so the primary gets its bounded
   retry first when the shared budget and deadline allow it.
3. Do not add ``segment-count`` to the availability circuit-breaker set in
   ``weblate/trans/judge_loop.py``. A malformed completed response must not
   open a shared endpoint-health circuit.
4. Allow only one ``segment-count`` fallback at the original batch boundary.
   If it also fails, the existing width-one isolation may retry the primary
   endpoint per item but must not repeat the paid fallback for every item.

The existing fallback flow already records the actual serving profile on the
verdict and usage ledger. No additional persistence path is needed.

### 4. Keep diagnostic probes compatible

Pass the actual request count to ``_response_schema()`` in:

- ``analysis/probes/litellm-seat-stability.py``;
- ``analysis/probes/litellm-complement-smoke.py``;
- ``analysis/probes/litellm-model-compat.py``; and
- ``analysis/probes/litellm-cut-diagnostic.py``.

### 5. Validate locally

Run:

```sh
uv run pytest weblate/trans/tests/test_judge_client.py -k 'schema or fallback or segment'
uv run pytest weblate/trans/tests/test_judge_loop.py
uv run prek run --all-files
```

Review the diff to confirm no prompt, model route, credential, or fallback
configuration value changed.

### 6. Canary and rollout gates

After separate approval to call the production model endpoint, but before
deployment:

1. Run the production canary on a maximum of ten read-only strings, with
   ``use_cache=False``, ``writable_ids=set()``,
   ``candidate_severities=()``, and ``mutating_repairs=False``.
2. Treat this as a compatibility smoke test: require the LiteLLM route to
   accept the bounded schema without HTTP 400 and return one result per input.
   Ten clean strings do not establish a production failure rate.
3. Exercise a controlled, non-mutating test of the new fallback branch only
   if a safe fixture can reliably produce an empty array. Do not manufacture
   an availability outage and do not change the global production model
   configuration to force it.
4. Verify that a fallback-served verdict records
   ``judge_provider=openrouter`` and that the corresponding
   ``JudgeRequestAttempt``/usage row names the fallback provider and model.
5. If compatibility passes, request explicit deployment approval. After
   deployment, use the next full run to measure effectiveness: require zero
   terminal ``segment-count`` results, no schema-rejection response, and no
   material latency or cost regression.
6. Publish the measured result under ``docs/llm-first/measurements/``. If
   either gate fails, retain or restore the current production configuration
   and investigate the provider response contract before rollout.

## Risks and acceptance criteria

The schema constraint is additive for a provider that accepts these keywords.
Such a provider will no longer be allowed to return an empty array. A provider
that ignores the bounds still reaches the parser safely, then can make one
extra paid fallback request after the existing retry opportunity is exhausted.
A provider that rejects the schema blocks deployment at the compatibility
gate.

Accept the patch only when:

- the exact batch cardinality is emitted for one and multiple segments;
- an empty primary response retries once, then falls back once and returns a
  valid provenance-bearing verdict;
- an exhausted shared retry budget goes directly from the primary failure to
  one fallback request;
- a failed multi-item fallback is not repeated during width-one isolation;
- other protocol failures do not fall back;
- no existing availability-fallback test regresses; and
- the pre-deployment canary proves endpoint compatibility; and
- the next full production run has no terminal ``segment-count`` result.
