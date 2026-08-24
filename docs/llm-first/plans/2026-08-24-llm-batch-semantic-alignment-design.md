<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# LLM batch semantic alignment verifier design and implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task.
>
> Use `test-driven-development` for Tasks 1-4 and
> `verification-before-completion` for Tasks 5-6.

**Goal:** Build and calibrate a blind, full-matrix semantic verifier that can
detect content misbinding in one LLM producer batch before any future
cache/store integration.

**Architecture:** A strict verifier labels every source-target relation while
the server alone retains the expected mapping. A pure bipartite-graph evaluator
derives the result without numeric weights; the first delivery is a sealed,
read-only, cache-free experiment, not a production gate.

**Tech Stack:** Python 3.13, Django test utilities, `httpx2`,
`stream_validated_url`, OpenRouter strict JSON Schema, pytest, JSON measurement
artifacts, and reStructuredText/Markdown documentation.

---

**Date:** 2026-08-24. **Status:** design approved; implementation plan ready;
implementation not started.

## Goal

Detect whether every translation returned by one LLM batch is semantically bound
to the source item that the producer claims, before the result reaches Weblate's
translation cache or a unit target.

This verifier is a second layer over the opaque producer-ID protocol in
`2026-08-24-llm-batch-string-identity.md`. IDs prove that the producer returned
the requested labels. They do not prove that the content next to each label
translates the corresponding source.

## Problem and evidence

The measured failure class is content misbinding inside a batch:

- four German targets in `heart-abyss/hub-1` were rotated across adjacent
  sources;
- Indonesian `col4/data` unit 88213 holds the content of unit 88214 while the
  content of 88213 is absent;
- a Traditional Chinese defect crossed a segment boundary.

Structural checks over IDs, lengths, order, placeholders or punctuation cannot
prove semantic binding. A normal MQM judge also cannot prove it when it receives
only the already-paired `(source_i, target_i)`: a fluent translation of a nearby
source can still look plausible in isolation.

The production radius scan also rules out identical-target collision as an
independent blocker. Of 57 duplicate-target findings, 55 were legitimate
convergence of identical or synonymous sources, one was a terminology collapse,
and only one belonged to the misbinding class
(`docs/llm-first/measurements/2026-08-24-batch-misalignment-radius-scan.md`).

## Scope

The verifier evaluates one fresh, complete producer request at a time. If the
producer request contains `n` source items, the verifier classifies all `n × n`
source-target relations.

The configured producer batch size is an upper bound, not the measurement
unit. Prefix rescue, halving, duplicate-source deferral and final tails can make
an actual producer request smaller than ten. Every metric records the actual
matrix size.

A previously certified cache hit is a reused pair, not proof that a new batch
was aligned. Cache reuse therefore has its own policy and metrics and is never
counted as a fresh matrix validation.

## Non-goals

- Replacing the opaque producer-ID protocol.
- General MQM quality, terminology, style or severity scoring.
- Automatically reassigning or rewriting targets.
- Deriving confidence from arbitrary numeric weights such as
  `faithful = 4, partial = 2`.
- Treating duplicate targets or multiple local matches as defects by themselves.
- Choosing production judge models or a two-seat consensus policy before a
  separate calibration.
- Enabling a production hard gate as part of the first implementation.

## Independent blind matrix

After producer JSON has parsed and passed exact ID/coverage validation, the
server keeps the validated items in an intermediate ordered form. It does not
yet build `TranslationResult`, cache anything or expose the result to
`AutoTranslate`.

For the verifier request, the server:

1. assigns new request-scoped opaque IDs to sources and targets;
2. uses disjoint ID spaces so a source ID cannot reveal its target ID;
3. independently shuffles the source and target lists;
4. retains the expected mapping only on the server;
5. sends no producer IDs, source positions or expected pairs to the verifier.

The verifier classifies every pair with one semantic relation:

```text
faithful
partial
contradicts
unrelated
```

`ambiguous` is deliberately absent. The relation describes one edge. Ambiguity
is a property derived by the server from a row or the complete assignment.

The strict response schema is logically:

```text
items:
  - source_id
    target_id
    relation
```

The server rejects a response unless it contains exactly `n × n` unique cells,
uses only issued IDs, covers every pair once and uses only the four relation
values. The verifier returns no assignment, confidence score or recommended
repair.

## Faithful graph and deterministic assignment rules

The server constructs a bipartite graph whose edges are exactly the cells marked
`faithful`. `partial`, `contradicts` and `unrelated` remain diagnostic evidence
but are not assignment edges.

It then computes three facts without weights:

```text
expected_valid:
  every edge in the producer's expected mapping is faithful

perfect_matching_exists:
  the faithful graph has a one-to-one matching covering all sources and targets

alternative_perfect_matching_exists:
  the graph has a complete matching different from the expected mapping
```

Multiple faithful edges in one target row create row-level ambiguity. They do
not necessarily create assignment-level ambiguity: the rest of the graph can
still force a unique complete matching.

| Graph result | Outcome | Meaning |
| --- | --- | --- |
| Expected mapping is faithful; no alternative complete matching | `semantic_binding_pass` | The expected mapping is uniquely supported. |
| Expected mapping is faithful; an alternative complete matching exists | `semantic_assignment_ambiguous` | Every expected pair is semantically acceptable, but equivalent alternatives exist. This is not evidence of misalignment. |
| Expected mapping is not faithful; a complete faithful matching exists | `semantic_misalignment` | The content supports a different one-to-one assignment. |
| Expected mapping is not faithful; no complete faithful matching exists | `semantic_translation_failure` | At least one source or target cannot be covered. The cause can be loss, duplication, an unrelated target or an ordinary bad translation; it is not labelled as a proven permutation. |
| No complete valid verifier response exists | `semantic_validator_unparsed` | Operational failure, never a semantic pass. Transport and schema details remain separate failure metadata. |

An off-diagonal faithful edge is retained as evidence but never blocks by
itself. A local collision matters only when the complete graph cannot cover the
batch or supports a mapping incompatible with the producer's expected mapping.

## Prevention boundary

The current native MQM judge is not a prevention layer. Its
`AutoTranslate.process_judge()` first calls `process_mt()`, writes fresh targets,
reloads the units and only then judges them
(`weblate/trans/autotranslate.py:760-793`). A state such as `needs rewriting`
does not quarantine the text from `Unit.target`.

The current `on_batch` callback is also too late. It runs after
`service.batch_translate()`, while machinery cache writes occur in
`_apply_downloaded_translations()` (`weblate/machinery/base.py:1159-1180`).

The alignment boundary belongs inside the LLM machinery parse path:

```text
producer response
  -> parse JSON
  -> validate producer IDs and exact coverage
  -> construct blind source and target sets
  -> obtain and validate the semantic matrix
  -> evaluate the faithful graph
  -> only after a permitted outcome build TranslationResult
  -> only after that allow cache and AutoTranslate application
```

The natural insertion point is in `_parse_llm_translations()` after
`_validate_translations()` and before `_build_translation_results()`
(`weblate/machinery/llm.py:2892-2946`). Prefix rescue must use the same boundary:
every accepted prefix is verified as the complete producer request it actually
represents before it can enter `PartialLLMReplyError.translations`.

## Cache invariant and rollout namespace

Putting the verifier before `cache.set()` is insufficient because
`_prepare_translation_download()` reads existing entries first
(`weblate/machinery/base.py:1024-1044`). Old batch-derived entries could bypass
the new verifier.

### Initial shadow experiment

The sealed calibration harness bypasses all translation cache reads and writes.
It evaluates frozen, complete producer batches from the sealed corpus and calls
only the alignment verifier. It never generates or stores a new Weblate target.
This holds the producer output constant across repeated verifier runs, keeps the
experiment independent of historical cache state and isolates verifier noise
from producer noise.

### Future enforcement

Enforcement remains out of scope until calibration passes and receives separate
approval. Its cache namespace must include at least:

- alignment prompt fingerprint;
- alignment response-schema version;
- graph-policy version;
- judge-seat configuration fingerprint;
- rollout mode, so `shadow` entries cannot become `enforce` entries.

The enforcement path must never read the pre-verifier namespace. A fresh result
can enter the enforce namespace only after an allowed graph outcome. A later hit
from that namespace certifies only that `(source, target)` pair under the stored
fingerprint. It is reported as `cache_pair_reused`, not
`fresh_batch_validated`, and is excluded from fresh-batch recall, false-block
and ambiguity denominators.

If implementation cannot preserve that distinction cleanly, alignment-enabled
translation must bypass cache rather than weaken the invariant.

## Failure and retry policy

No verifier outcome ever causes automatic target reassignment.

During the initial shadow experiment all outcomes are recorded and none alter a
translation because the harness is read-only.

A future calibrated enforcement policy may treat a confirmed
`semantic_misalignment` or `semantic_translation_failure` as a specialized
`MachineTranslationError`. The existing LLM machinery can then retry the failed
request in halves. The rejected result must not be cached, built into a
`TranslationResult` or applied to a unit. If a singleton still fails, it remains
untranslated.

`semantic_assignment_ambiguous` is not a proven misalignment. Because every
expected edge is faithful, it is recorded separately and must not be counted as
a misbinding true positive. The eventual store/block policy for this outcome is
a calibration decision.

A missing, timed-out, unparsable or incomplete verifier answer is an operational
failure, not a semantic pass. Shadow metrics report these separately. A future
hard gate must fail closed or retry according to an explicitly approved policy.

## Separate verifier subsystem

The alignment verifier is separate from the existing MQM judge:

- a different prompt and strict response schema;
- a separate prompt/version fingerprint;
- a separate usage category;
- separate result records and metrics;
- no MQM severity fields;
- no reuse of paired-MQM calibration numbers.

The HTTP transport, strict JSON-Schema plumbing and two independently configured
judge seats may be reused. Seat models and consensus logic are selected only
after measuring each seat and candidate union/intersection policies on the
alignment corpus.

## Calibration corpus

The first corpus contains:

1. Real defects:
   - the four-string German rotation;
   - the Indonesian duplicate/loss case;
   - the Traditional Chinese cross-segment content exchange.
2. Mutations of sealed clean production batches:
   - swap two targets;
   - rotate three to five targets;
   - duplicate one target and drop another;
   - preserve producer IDs while moving target content;
   - insert a target from a neighbouring batch;
   - insert a completely unrelated target.
3. Hard-clean controls:
   - identical sources;
   - synonymous sources;
   - short UI strings;
   - nearly identical dialogue lines;
   - plurals;
   - placeholders;
   - CJK numbers;
   - targets that legitimately converge for different sources.
4. At least five identical runs of every candidate seat/configuration.

The harness also includes two protocol controls:

- independently shuffle serialization and opaque IDs while preserving content
  binding; this must remain clean;
- preserve expected IDs while permuting content; this must be detected.

## Measurements and decision gates

Metrics are computed at batch level first, then unit/cell level:

- damaged-batch recall;
- misbound-unit recall;
- clean-batch false-block rate;
- assignment-indeterminate rate;
- row-ambiguity rate;
- flip rate across identical runs;
- exact-matrix parsed rate;
- latency and cost;
- outcome counts by actual matrix size;
- `fresh_batch_validated` and `cache_pair_reused` as separate populations.

At the measured radius of 27,436 units, ideal batches of ten imply about 2,744
matrices and 274,400 classified relations per seat. Two seats imply about
548,800 relations. Even a 1% clean-batch false-block rate would create roughly
27 blocked batches, so cell-level accuracy cannot be used as the rollout gate.

No numeric production threshold is chosen in this design. The sealed experiment
must publish the measured trade-offs, after which the hard-gate policy and seat
consensus require a separate decision and approval.

## Observability

Every shadow matrix record contains enough information to reproduce the
server-side verdict without exposing credentials:

- producer and verifier prompt fingerprints;
- producer model and verifier seat identifiers;
- source and target language;
- actual matrix size;
- request-scoped shuffle seed or equivalent deterministic permutation record;
- opaque-ID maps stored with restricted diagnostic access;
- exact relation matrix;
- expected mapping;
- graph outcome and failure metadata;
- latency, token usage and cost;
- cache policy (`bypassed`, `fresh_verified`, or `certified_pair_reused`).

Metrics must never label a cache reuse as a freshly checked batch. Credentials,
provider headers and unrestricted project content are not written to logs.

## Acceptance boundary for the first implementation

The first implementation is complete only when a read-only, cache-free harness:

1. produces a strict full matrix for every frozen complete producer batch;
2. derives outcomes entirely with the deterministic graph rules above;
3. reproduces all three real defects in the sealed corpus;
4. keeps the hard-clean controls distinct from proven misalignment;
5. reports five-run stability, parsed rate, latency and cost;
6. performs no cache write, unit write, automatic reassignment or production
   deployment.

Production shadow integration and enforcement are separate increments requiring
new approval after the measurement report is reviewed.

## Implementation plan

The implementation stops at the sealed shadow measurement. It does not connect
the verifier to `RoutedLLMTranslation`, `BatchAutoTranslate`, machinery cache or
`Unit.translate`. Production integration gets a new plan only after this
experiment has been reviewed.

### Task 1: Implement the deterministic faithful-graph evaluator

**Files:**

- Create: `weblate/trans/alignment.py`
- Create: `weblate/trans/tests/test_alignment.py`

#### Step 1: Write the failing graph-contract tests

Add `AlignmentGraphTest` with small explicit matrices covering:

1. the expected matching is uniquely faithful -> `semantic_binding_pass`;
2. one row has two faithful edges but the complete matching is unique ->
   `semantic_binding_pass` with row ambiguity recorded;
3. the expected mapping and another complete matching are both faithful ->
   `semantic_assignment_ambiguous`;
4. the expected mapping is not faithful but a different complete matching
   exists -> `semantic_misalignment`;
5. duplicate/drop leaves no complete faithful matching ->
   `semantic_translation_failure`;
6. a local off-diagonal faithful edge without a complete alternative does not
   become `semantic_misalignment`.

Add `AlignmentMatrixValidationTest` covering a missing cell, duplicate cell,
unknown source ID, unknown target ID, invalid relation, non-bijective expected
mapping and an empty batch. Every malformed matrix must raise
`AlignmentMatrixError`; it must never return a pass-like decision.

The public shape pinned by the tests is:

```python
class AlignmentRelation(StrEnum):
    FAITHFUL = "faithful"
    PARTIAL = "partial"
    CONTRADICTS = "contradicts"
    UNRELATED = "unrelated"


class AlignmentOutcome(StrEnum):
    PASS = "semantic_binding_pass"
    AMBIGUOUS = "semantic_assignment_ambiguous"
    MISALIGNMENT = "semantic_misalignment"
    TRANSLATION_FAILURE = "semantic_translation_failure"


@dataclass(frozen=True)
class AlignmentCell:
    source_id: str
    target_id: str
    relation: AlignmentRelation


@dataclass(frozen=True)
class AlignmentDecision:
    outcome: AlignmentOutcome
    expected_valid: bool
    perfect_matching_exists: bool
    alternative_perfect_matching_exists: bool
    row_ambiguous_target_ids: tuple[str, ...]


def evaluate_alignment(
    *,
    source_ids: Sequence[str],
    target_ids: Sequence[str],
    expected_sources: Mapping[str, str],
    cells: Sequence[AlignmentCell],
) -> AlignmentDecision:
    ...
```

#### Step 2: Run the test and prove RED

```bash
./rundev.sh test weblate/trans/tests/test_alignment.py
```

Expected: collection or import fails because `weblate.trans.alignment` does not
exist.

#### Step 3: Implement the minimum unweighted matcher

Validate exact Cartesian coverage before constructing the graph. Implement a
deterministic augmenting-path maximum matcher; batch size is at most ten, so no
weighted solver or external dependency is justified.

Detect an alternative to the expected matching by removing each expected edge
in turn and asking whether a perfect matching still exists. If an alternative
matching differs from the expected mapping, at least one expected edge is absent
from it, so this test is complete.

Do not expose scores, confidence or a "best source" API.

#### Step 4: Run the owning test

```bash
./rundev.sh test weblate/trans/tests/test_alignment.py
```

Expected: all graph and malformed-matrix cases pass.

#### Step 5: Commit

```bash
git add weblate/trans/alignment.py weblate/trans/tests/test_alignment.py
git commit -m "feat(alignment): add deterministic batch matching"
```

### Task 2: Build the blind strict-matrix verifier client

**Files:**

- Create: `weblate/trans/alignment_client.py`
- Create: `weblate/trans/alignment_prompts/__init__.py`
- Create: `weblate/trans/alignment_prompts/verdict.txt`
- Create: `weblate/trans/tests/test_alignment_client.py`
- Modify: `weblate/trans/alignment.py`

Do not modify `weblate/trans/judge.py` or its MQM prompt. The alignment client
may reuse `stream_validated_url` and the existing bounded-response pattern, but
it owns a distinct prompt, schema, parser, fingerprint and result type.

#### Step 1: Write the failing blinding and schema tests

Pin these observable contracts:

- source and target opaque IDs are disjoint and unrelated to producer IDs;
- source and target serialization orders are independently derived from a
  caller-supplied seed;
- the same seed reproduces the exact payload;
- changing the seed changes order/IDs without changing the expected mapping;
- the request schema requires exactly `n × n` cells;
- output order is irrelevant;
- a missing, duplicate or unknown pair produces
  `semantic_validator_unparsed`, never a partial decision;
- the client returns provider usage in memory and creates no `LLMUsageLog`;
- input strings are wrapped as untrusted data and cannot become prompt
  instructions;
- neither the API key nor authorization header appears in raised errors or
  logs.

Use `http_mock` as in `weblate/trans/tests/test_judge_client.py`; no real network
request belongs in this task.

The client-facing types are:

```python
@dataclass(frozen=True)
class AlignmentPair:
    producer_id: str
    source: str
    target: str


@dataclass(frozen=True)
class AlignmentUsage:
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    cost_usd: Decimal | None


@dataclass(frozen=True)
class AlignmentVerification:
    decision: AlignmentDecision | None
    cells: tuple[AlignmentCell, ...]
    usage: AlignmentUsage
    prompt_fingerprint: str
    schema_fingerprint: str
    unparsed: bool
    failure_kind: str


def request_alignment(
    pairs: Sequence[AlignmentPair],
    *,
    source_language: str,
    target_language: str,
    model: str,
    api_key: str,
    seed: str,
) -> AlignmentVerification:
    ...
```

`semantic_validator_unparsed` remains an operational output in serialized
records; it is represented by `unparsed=True` rather than a fake
`AlignmentDecision`.

#### Step 2: Prove RED

```bash
./rundev.sh test weblate/trans/tests/test_alignment_client.py
```

Expected: import fails because the client and prompt do not exist.

#### Step 3: Write the alignment-only prompt

The prompt must tell the model to classify every pair independently with only
the four relation labels. It must explicitly forbid choosing an assignment,
returning confidence, omitting unlikely pairs or treating a fluent target as
proof that it belongs to the displayed source.

Keep source and target arrays separate. The user message contains independently
shuffled opaque IDs and text only; it does not contain producer IDs, expected
pairs or original positions.

Hash the exact prompt bytes and canonical JSON Schema. Return both fingerprints
with every result.

#### Step 4: Implement strict request/response handling

Build dynamic JSON Schema with `minItems == maxItems == n * n`, then perform the
stronger Cartesian uniqueness validation locally. Use the same fixed OpenRouter
host and bounded streaming helper as the current judge, but keep request and
usage logic in `alignment_client.py`.

No Django model, cache API or translation machinery import is allowed in this
module. Usage is returned to the caller with the logical category
`semantic_alignment_shadow`; it is not persisted.

#### Step 5: Run the client and graph suites

```bash
./rundev.sh test \
  weblate/trans/tests/test_alignment.py \
  weblate/trans/tests/test_alignment_client.py
```

Expected: both suites pass; mocked requests contain exactly the strict matrix
schema and no expected mapping.

#### Step 6: Commit

```bash
git add \
  weblate/trans/alignment.py \
  weblate/trans/alignment_client.py \
  weblate/trans/alignment_prompts \
  weblate/trans/tests/test_alignment.py \
  weblate/trans/tests/test_alignment_client.py
git commit -m "feat(alignment): add blind matrix verifier client"
```

### Task 3: Seal the real and mutated calibration corpus

**Files:**

- Create: `analysis/data/batch-alignment-golden.json`
- Create: `analysis/probes/batch_alignment_eval.py`
- Create: `analysis/probes/test_batch_alignment_eval.py`
- Read only: `.omp/skills/weblate-lqa/tests/misalignment_regression.json`
- Read only:
  `docs/llm-first/measurements/2026-08-24-batch-misalignment-radius-scan.md`
- Read only:
  `docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md`

#### Step 1: Write failing corpus and mutation tests

Define a closed versioned fixture schema. Each batch carries:

```json
{
  "case_id": "heart-abyss-hub-1-de-rotation",
  "source_language": "ru",
  "target_language": "de",
  "provenance": {"project": "heart-abyss", "component": "hub-1"},
  "truth": "semantic_misalignment",
  "items": [
    {
      "producer_id": "opaque fixture label",
      "unit_id": 370314,
      "context": "hub1_asuna_1_6",
      "source": "source text",
      "target": "observed target text"
    }
  ]
}
```

Tests must reject missing truth, duplicate producer IDs, blank source/target,
unknown outcome labels and a corpus fingerprint mismatch.

Add deterministic materialization tests for:

- swap two;
- rotate three, four and five;
- duplicate/drop;
- correct producer IDs with permuted target content;
- target from a neighbouring batch;
- unrelated target;
- unchanged content with shuffled serialization and IDs.

The mutation operation changes target content while leaving the server's
expected producer mapping fixed.

#### Step 2: Prove RED

```bash
./rundev.sh test analysis/probes/test_batch_alignment_eval.py
```

Expected: import or fixture loading fails because the evaluator and corpus do
not exist.

#### Step 3: Populate and seal all required cases

Copy exact pre-repair German rows from the tracked LQA regression fixture.
Capture the Indonesian 88213/88214 and Traditional Chinese 372096/372097 rows
verbatim from the saved 2026-08-24 audit snapshots, including enough untouched
neighbours to reconstruct their actual producer-batch context. Do not copy
ellipsis-shortened prose from a Markdown report as target data.

Add hard-clean batches containing:

- identical sources;
- synonymous sources;
- short UI labels;
- nearly identical dialogue;
- plurals;
- placeholders;
- CJK numbers;
- legitimate same-target convergence.

Record unit IDs, contexts and source/target hashes in provenance. The fixture is
immutable after its fingerprint is published; corrections create a new corpus
version.

If an exact historical string is no longer available in tracked artifacts,
stop and identify that missing row. Do not reconstruct or translate it from
memory.

#### Step 4: Implement deterministic fixture loading and mutations

Keep corpus loading, mutation materialization and metrics in the analysis
script. Import the production-independent graph/client modules; do not import
`Unit`, `Translation`, `MACHINERY` or Django cache.

#### Step 5: Run the corpus test

```bash
./rundev.sh test analysis/probes/test_batch_alignment_eval.py
```

Expected: every case validates, every mutation is deterministic, and the sealed
fingerprint matches.

#### Step 6: Commit

```bash
git add \
  analysis/data/batch-alignment-golden.json \
  analysis/probes/batch_alignment_eval.py \
  analysis/probes/test_batch_alignment_eval.py
git commit -m "test(alignment): seal semantic binding corpus"
```

### Task 4: Complete the read-only five-run shadow harness

**Files:**

- Modify: `analysis/probes/batch_alignment_eval.py`
- Modify: `analysis/probes/test_batch_alignment_eval.py`

#### Step 1: Write failing orchestration and metric tests

Inject a fake requester and prove:

- every case/model/repeat produces exactly one result;
- one unparsed response is recorded and does not abort later cases;
- all five identical repeats use the same prompt seed;
- a separate order-sensitivity arm changes only the shuffle seed;
- no seat may erase another seat's result;
- batch truth and predicted outcome produce a batch confusion table;
- flip rate compares identical repeats only;
- parsed rate, latency, tokens and cost use explicit denominators;
- metrics separate `fresh_batch_validated` from `cache_pair_reused`, with the
  latter fixed at zero in this cache-free harness;
- `--dry-run` performs no network call.

Do not implement a production consensus rule. Report each seat and candidate
union/intersection calculations side by side.

#### Step 2: Prove RED

```bash
./rundev.sh test analysis/probes/test_batch_alignment_eval.py
```

Expected: new CLI/metric assertions fail.

#### Step 3: Implement the CLI

Required arguments and defaults:

```text
--corpus analysis/data/batch-alignment-golden.json
--model <repeatable; exactly the configurations being measured>
--repeats 5
--seed alignment-shadow-v1
--output <required for a live run>
--dry-run
--limit-cases <optional paid probe bound>
```

Read the API key only from `WEBLATE_JUDGE_OPENROUTER_KEY`; never accept it on
the command line or print it. The structured result includes:

- corpus, prompt and schema fingerprints;
- model and logical usage category;
- actual matrix size;
- raw matrix and deterministic graph outcome;
- expected truth;
- parsed/failure status;
- latency and provider-reported usage/cost;
- per-seat batch/unit metrics and five-run flip rate.

The script never calls the producer, Weblate API, ORM, cache or unit write
paths. It evaluates fixed producer outputs so model variation is isolated to the
alignment verifier.

#### Step 4: Verify offline behavior

```bash
uv run python analysis/probes/batch_alignment_eval.py \
  --model seat-a \
  --model seat-b \
  --repeats 5 \
  --dry-run
```

Expected: exit 0, print the sealed fingerprints and exact request/relation
counts, make zero HTTP requests and create no output file.

Then run:

```bash
./rundev.sh test \
  weblate/trans/tests/test_alignment.py \
  weblate/trans/tests/test_alignment_client.py \
  analysis/probes/test_batch_alignment_eval.py
```

Expected: all alignment tests pass.

#### Step 5: Commit

```bash
git add analysis/probes/batch_alignment_eval.py \
  analysis/probes/test_batch_alignment_eval.py
git commit -m "feat(alignment): add cache-free shadow harness"
```

### Task 5: Run the paid sealed experiment

**Files:**

- Create after a successful run:
  `analysis/data/batch-alignment-shadow-v1.json`

This task makes external paid LLM calls. Stop and obtain explicit approval for
the named models, maximum request count and spending cap before running it.
Approval of this document is not approval of the paid run or of production
access.

#### Step 1: Run a bounded probe

From an environment with allowed OpenRouter egress and the key supplied through
the environment:

```bash
uv run python analysis/probes/batch_alignment_eval.py \
  --model '<seat-1-model>' \
  --model '<seat-2-model>' \
  --repeats 1 \
  --limit-cases 2 \
  --output /tmp/batch-alignment-probe.json
```

Expected: both cases return exact matrices or explicit operational failures;
the output reports actual cost and contains no credential.

If the workstation has no provider egress, stop. Running the probe through
`deploy/vps.sh`, `weblate shell` or a production container requires separate
explicit production-operation approval; do not silently use it as a fallback.

#### Step 2: Review the probe before scaling

Verify:

- matrix coverage is exactly `n × n`;
- fingerprints match the dry-run;
- provider usage is present;
- no response or log contains the API key;
- projected full-run requests and cost stay inside the approved cap.

#### Step 3: Run five identical repeats

```bash
uv run python analysis/probes/batch_alignment_eval.py \
  --model '<seat-1-model>' \
  --model '<seat-2-model>' \
  --repeats 5 \
  --output analysis/data/batch-alignment-shadow-v1.json
```

Expected: the output contains all cases for both seats and all five repeats,
plus a separate order-sensitivity arm. Any missing/unparsed request remains in
the denominator.

#### Step 4: Validate the result artifact

```bash
uv run python analysis/probes/batch_alignment_eval.py \
  --validate-output analysis/data/batch-alignment-shadow-v1.json
```

Expected: fingerprints, record counts, cost totals and metric denominators
recompute exactly from raw records.

#### Step 5: Commit the immutable measurement data

```bash
git add analysis/data/batch-alignment-shadow-v1.json
git commit -m "test(alignment): record shadow calibration"
```

### Task 6: Publish the measurement and stop at the decision gate

**Files:**

- Create:
  `docs/llm-first/measurements/2026-08-24-batch-semantic-alignment-shadow.md`
- Modify: `docs/llm-first/measurements/judge-measurements-index.md`
- Modify:
  `docs/llm-first/plans/2026-08-24-llm-batch-semantic-alignment-design.md`

#### Step 1: Write the measurement report from the artifact

Report, without hand-recomputing:

- corpus and prompt/schema fingerprints;
- each seat/configuration and five-run count;
- damaged-batch and misbound-unit recall;
- clean-batch false-block rate;
- assignment-indeterminate and row-ambiguity rates;
- parsed and flip rates;
- latency, tokens and cost;
- results by matrix size;
- all three real cases individually;
- hard-clean false positives individually;
- candidate seat-combination policies, without selecting one implicitly.

State explicitly that a successful sealed experiment does not prove production
reliability. A failed gate remains a failed result; do not tune on the sealed
cases and report the tuned result as test performance.

#### Step 2: Record the decision

Choose one evidence-backed status:

```text
rejected
revise and remeasure on a new development split
candidate for a separately approved production-shadow plan
```

Do not write a hard-gate implementation task into this document. That would be
new scope and requires a new approved plan with cache namespace, fresh-batch
versus cached-pair policy, retry semantics and downstream write-path tests.

#### Step 3: Run focused formatting and lint

```bash
uv run prek run rumdl-fmt --files \
  docs/llm-first/plans/2026-08-24-llm-batch-semantic-alignment-design.md \
  docs/llm-first/measurements/2026-08-24-batch-semantic-alignment-shadow.md \
  docs/llm-first/measurements/judge-measurements-index.md

uv run prek run \
  trailing-whitespace \
  end-of-file-fixer \
  mixed-line-ending \
  codespell \
  --files \
  docs/llm-first/plans/2026-08-24-llm-batch-semantic-alignment-design.md \
  docs/llm-first/measurements/2026-08-24-batch-semantic-alignment-shadow.md \
  docs/llm-first/measurements/judge-measurements-index.md
```

Expected: all focused hooks pass.

#### Step 4: Run the complete owning verification

```bash
./rundev.sh test \
  weblate/trans/tests/test_alignment.py \
  weblate/trans/tests/test_alignment_client.py \
  analysis/probes/test_batch_alignment_eval.py

uv run python analysis/probes/batch_alignment_eval.py \
  --validate-output analysis/data/batch-alignment-shadow-v1.json
```

Expected: tests pass and the committed measurement recomputes exactly.

#### Step 5: Record documentation decisions

No changelog entry: this increment adds an unsupported, read-only experiment and
does not change user-visible Weblate behavior. No threat-model update: no
product endpoint, setting, background task, cache path or stored target uses the
verifier. Revisit both decisions in any production integration plan.

#### Step 6: Commit and push

```bash
git add \
  docs/llm-first/plans/2026-08-24-llm-batch-semantic-alignment-design.md \
  docs/llm-first/measurements/2026-08-24-batch-semantic-alignment-shadow.md \
  docs/llm-first/measurements/judge-measurements-index.md
git commit -m "docs(llm-first): record alignment shadow results"
git fetch origin
git rebase origin/main
git push origin HEAD
```
