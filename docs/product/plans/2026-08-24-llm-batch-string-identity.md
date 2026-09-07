# LLM batch string identity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the LLM batch protocol check which source string each reply item claims to belong to, so a mislabelled or incomplete reply is refused instead of stored. This is a mitigation, not a guarantee: read "What this fix does and does not catch" before reporting it as a fix.

**Architecture:** Every string in a batch request carries an opaque, request-scoped `id`. The reply must echo it, and the reply is paired with its sources through that id instead of the list position. A batch reply without usable ids is refused under its own log message (`Mismatching assistant reply ids.`) and re-asked in halves by the machinery that already exists, down to single-string requests where position is unambiguous. What this verifies is the *label set* of a reply, not the binding between a label and the content next to it.

**Tech Stack:** Python 3.13, Django, `weblate/machinery/llm.py` (`BaseLLMTranslation`, shared by OpenAI, Azure OpenAI, Mistral and the fork's `RoutedLLMTranslation`), pytest inside the `dev-docker` container.

---

## Why this exists

`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md` §3 measured the defect on
production: in `heart-abyss/hub-1` the German target of `hub1_asuna_1_6` holds the translation of
`_1_7`, `_1_7` holds `_1_8`, `_1_8` holds `_1_9`, and `_1_6`'s own content is absent from German
entirely. All 396 units of all 9 languages are raw `mt:openrouter` output with exactly one human
edit, and the Russian source has not changed since before the run.

The cause is structural, not a bad model day:

1. The prompt asks for "one item per input string … in the same order" and the reply carries **no
   identifier per item** (`weblate/machinery/llm.py:161-205`).
2. `_normalize_translation_items` (`weblate/machinery/llm.py:2429-2455`) assigns
   `translations[index]` to `sources[index]`.
3. The only structural guard is `len(translations) != len(sources)`
   (`weblate/machinery/llm.py:2436`).
4. Per-item validation covers placeholder multisets and literal `@` suffixes only
   (`weblate/machinery/llm.py:2480-2489`).

This component has no placeholders at all, so for a reply of the right length **not one guard can
fire**, whatever the ordering. Worse, the recovery machinery (`PartialLLMReplyError`,
`_split_sources`) repairs *count* mismatches, which trains the pipeline to read a correct count as
evidence of correct alignment.

### What this fix does and does not catch

**Read this before reporting Part A as a fix.** It bounds what may be claimed afterwards.

Verified by the id echo: the *label set* of a reply. Every requested id is present exactly once and
no unknown id appears. That catches a reply that answered a different set of strings than the one
it was given - a dropped input, a duplicated one, an item pulled in from beyond the batch.

Not verified: that the content sitting next to a label is the translation of that label's source. A
model may echo all requested ids, in order, with shifted content, and nothing in this plan detects
it. No protocol can detect a reply that lies about its own labels.

**The production reply carried no ids at all**, so the measurement in §3 cannot tell us which of
the two cases it was. It proves a content shift with one source translated twice and one lost; it
does not prove that the same model, asked for ids, would have produced a mismatched label set. Do
not write that this plan makes the measured defect impossible - it makes one class of it refusable.

One design structurally prevents cross-string misattachment, and the measurement already in this
repository argues against it:

- `batch_size = 1` for LLM services. With one source per request there is nothing to misattach to,
  so the failure cannot be expressed. Shrinking the batch, however, is already known to cost
  translation quality. `analysis/data/col4-batch-size-eval.json` ran the same 150-string COL4
  ru->fr sample through three batch sizes back to back, twice: `end_stop` defects go 18 of 150 at
  batch 20, 28 at batch 10, and **63 at batch 5**, reproducing exactly twenty minutes apart. For
  scale, deleting the punctuation rule from the prompt altogether measured 58-71, so at batch 5 the
  model honours that rule about as well as if it were not there. Cost and latency are not the
  obstacle: prompt caching holds four times the requests to 10-25% more spend, and batch 5 at
  concurrency 4 finished faster than batch 10 at concurrency 2. Batch 20 loses the other way - one
  reply in ten hit the content filter and two were unparsable - so 10 sits at the bottom of a
  U-shaped curve rather than being a compromise. Batch sizes below 5 are **unmeasured**, and the
  trend runs against them. Anyone proposing `batch_size = 1` must first run
  `EVAL_BATCH_SIZE=1 analysis/probes/col4-eval-harness.py` against that same fingerprinted sample
  and show the defect counts; the id echo is not a substitute for that measurement, and this plan
  does not make the decision.

Batch size would not have prevented the defect that prompted this plan in any case. The German
rotation in `docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md` §5.1 spans four
consecutive units, which fits inside a batch of five, and the Indonesian content loss in
`2026-08-24-batch-misalignment-radius-scan.md` §4.1 spans two. The batch-size lever is already at
its measured optimum, so this defect has to be addressed in the protocol.

Operational visibility: a model that never echoes ids does not fail loudly - every n-string batch
degrades into n single-string requests that all succeed, and the only symptom is the OpenRouter
bill. Task A4 therefore refuses with a distinct, stable message, `Mismatching assistant reply
ids.`, which `log_handled_error` writes to the log; a mass degradation is a grep away, and the
`llm_usage_report` management command shows the request-count growth. Watch both after deploying,
per target language: the routing map sends different languages to different models, so one model
may honour the contract while another silently degrades.

Server-side structured outputs were considered and deferred: OpenRouter and OpenAI can enforce a
JSON Schema on the reply (`response_format`, with `provider.require_parameters: true`, as the
fork's loc-kit profile client already does), which would guarantee the *shape* - an `id` field on
every item - at the API instead of the prompt. It would not change what is verifiable: a schema
cannot make the id next to a translation truthful, so the label-set check in Task A4 is needed
either way, and the batch path is shared with Azure OpenAI and Mistral, whose schema support
differs. A follow-up may add `response_format` for `RoutedLLMTranslation` only, on top of this
protocol, never instead of it.

Everything else is detection after the fact, and detection is not prevention. A content-level pass
over stored translations - comparing the language-independent shape of a target (digits,
placeholders, `$`, glossary names, final punctuation) against its own source and its neighbours,
with cross-language consensus - is useful for triage and for estimating how much damage is already
written, but it is incomplete in both directions and was measured to be so on this component: most
of its candidates were benign, and it missed `zh_Hant` 372096/372097, where the shape survived and
only the clause content moved across a segment boundary. Treat such a pass as a way to narrow 3564
units to a few dozen for human review, never as a gate and never as proof that a component is
clean.

---

## Environment

Work **in the main checkout on a branch, not in a git worktree.**

- `dev-docker/` publishes fixed host ports (5434, 1080, 3001) and the compose project name comes
  from the directory basename, so a worktree copy collides with the running stack instead of
  isolating from it.
- Tool paths (`read`, `edit`, `write`) resolve against the session root, not the shell's `cwd`, so
  every edit inside a worktree needs a `../worktree-dir/` prefix on every call. Two edits were lost
  to this in an earlier session.

Test commands run inside the container, which avoids the host PostgreSQL and `collectstatic` setup:

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_name"
```

If a suite that was green starts returning mass setup errors, or one test suddenly takes twenty
times longer, run `docker stats --no-stream` before suspecting the code: the usual cause is the
container sitting at its memory ceiling because sibling compose projects are running.

---

## Part A: per-string identity in the batch protocol

### Task A1: Send an opaque id with every string

**Files:**

- Modify: `weblate/machinery/llm.py` — imports (9-16), constants (231-235), `LLMStringPayload`
  (310-313), `_build_string_payload` (1092-1105), `_get_message` (1169-1214),
  `_prepare_llm_translation` (2874-2890), `_fetch_llm_batch` (2734-2758), `_afetch_llm_batch`
  (2848-2872)
- Test: `weblate/machinery/tests.py` (add to `OpenAITranslationTest`, which starts at line 3802)

Line numbers are from this plan's writing; re-read each region before editing.

**Step 1:** Write the failing test

Add to `OpenAITranslationTest`:

```python
    @http_mock.activate
    def test_request_string_ids_are_unique_and_not_positional(self) -> None:
        machine = self.get_machine()
        observed: list[list[str]] = []

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            observed.append([item["id"] for item in strings])
            return json.dumps(
                [
                    {
                        "id": item["id"],
                        "parts": [{"type": "text", "text": f"{item['source']} (fr)"}],
                    }
                    for item in strings
                ]
            )

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            machine.download_multiple_translations(
                "en", "fr", [(text, None) for text in ("Alpha", "Beta", "Gamma")]
            )

        ids = observed[0]
        self.assertEqual(len(set(ids)), 3)
        # A model can emit 0..n-1 without reading the input, so a positional id
        # would be indistinguishable from the alignment it has to verify.
        self.assertNotEqual(ids, [str(index) for index in range(3)])
        self.assertTrue(all(string_id.startswith("s") for string_id in ids))
```

**Step 2:** Run test to verify it fails

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_request_string_ids_are_unique_and_not_positional"
```

Expected: FAIL with `KeyError: 'id'`.

**Step 3:** Write the implementation

Add the import, sorted after `from operator import itemgetter`:

```python
from secrets import token_hex
```

Add the constants after `LLM_FULL_GLOSSARY_LIMIT = 300`:

```python
# The reply must echo the id of the string it translates, so alignment is
# checked instead of assumed. The id is random rather than the batch position,
# because a model can emit 0..n-1 without reading the input and a positional id
# would prove nothing. The "s" prefix keeps the id a JSON string even when the
# hex digits happen to be decimal, so a model cannot turn it into a number.
LLM_STRING_ID_PREFIX = "s"
LLM_STRING_ID_BYTES = 2
```

Add `id` to the payload type:

```python
class LLMStringPayload(LLMStringContext):
    id: str
    source: str
    parts: list[LLMStringPart]
    translation: NotRequired[str]
```

Add the generator immediately before `_build_string_payload`:

```python
    @staticmethod
    def _build_string_ids(count: int) -> list[str]:
        """Opaque per-request ids the reply has to echo back."""
        ids: list[str] = []
        seen: set[str] = set()
        while len(ids) < count:
            candidate = f"{LLM_STRING_ID_PREFIX}{token_hex(LLM_STRING_ID_BYTES)}"
            if candidate in seen:
                continue
            seen.add(candidate)
            ids.append(candidate)
        return ids
```

Take the id in `_build_string_payload`. Keyword-only and required, so a missed callsite is an
error rather than a silent default:

```python
    def _build_string_payload(
        self,
        source_text: str,
        unit: Unit | None,
        source_language: str | None = None,
        source_occurrence: int = 0,
        *,
        string_id: str,
    ) -> LLMStringPayload:
        return {
            "id": string_id,
            "source": source_text,
            "parts": self._get_string_parts(source_text, unit, source_occurrence),
            **self._get_string_context(
                source_text, unit, source_language, source_occurrence=source_occurrence
            ),
        }
```

In `_get_message`, add the parameter and pass the id through:

```python
    def _get_message(
        self,
        source_language: str,
        target_language: str,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None = None,
        *,
        string_ids: list[str],
    ) -> str:
```

```python
            payload = self._build_string_payload(
                text,
                unit,
                source_language,
                source_occurrence,
                string_id=string_ids[index],
            )
```

In `_prepare_llm_translation`, accept the ids with a default. The default keeps
`analysis/probes/col4-cost-probe.py:34`, `analysis/probes/col4-glossary-probe.py:46` and
`analysis/probes/col4-prompt-probe.py:25` working unchanged; they pass four positional arguments
and only print the request:

```python
    def _prepare_llm_translation(
        self,
        source_language,
        target_language,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None,
        string_ids: list[str] | None = None,
    ) -> tuple[str, str, str, str]:
        if string_ids is None:
            string_ids = self._build_string_ids(len(sources))
        prompt = self._get_prompt(target_language)
        content = self._get_message(
            source_language,
            target_language,
            sources,
            source_occurrences,
            string_ids=string_ids,
        )
```

In `_fetch_llm_batch`, generate the ids in the caller so the same list reaches the parser:

```python
        string_ids = self._build_string_ids(len(sources))
        prompt, content, previous_content, previous_response = (
            self._prepare_llm_translation(
                source_language,
                target_language,
                sources,
                source_occurrences,
                string_ids,
            )
        )
```

Leave `_parse_llm_translations` alone for now; it gets the ids in Task A4. Do the same in
`_afetch_llm_batch`, passing `string_ids` as the fifth positional argument to the
`sync_to_async(self._prepare_llm_translation)` call.

**Step 4:** Run test to verify it passes

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_request_string_ids_are_unique_and_not_positional"
```

Expected: PASS.

**Step 5:** Commit

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "feat(machinery): send an opaque id with every batch string"
```

---

### Task A2: Make the few-shot demonstration echo the ids

The demonstration is the strongest signal in the prompt. If its assistant turn answers without
ids, the model imitates that and every batch degrades. This task is not optional polish.

**Files:**

- Modify: `weblate/machinery/llm.py` — `_build_previous_messages_from_examples` (1426-1455)
- Test: `weblate/machinery/tests.py`

**Step 1:** Write the failing test

```python
    @http_mock.activate
    def test_previous_messages_demonstrate_the_id_contract(self) -> None:
        machine = self.get_machine()
        observed: list[tuple[list[str], list[str]]] = []

        def request_callback(
            _prompt: str,
            content: str,
            previous_content: str,
            previous_response: str,
        ) -> str:
            observed.append(
                (
                    [item["id"] for item in json.loads(previous_content)["strings"]],
                    [item["id"] for item in json.loads(previous_response)],
                )
            )
            strings = json.loads(content)["strings"]
            return json.dumps(
                [
                    {
                        "id": item["id"],
                        "parts": [{"type": "text", "text": "Ahoj"}],
                    }
                    for item in strings
                ]
            )

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            machine.download_multiple_translations("en", "cs", [("Hello", None)])

        demo_request_ids, demo_reply_ids = observed[0]
        self.assertTrue(demo_request_ids)
        # The demonstrated answer pairs itself with the demonstrated request by
        # id, which is exactly what the real reply has to do.
        self.assertEqual(demo_reply_ids, demo_request_ids)
```

**Step 2:** Run test to verify it fails

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_previous_messages_demonstrate_the_id_contract"
```

Expected: FAIL with `KeyError: 'id'`.

**Step 3:** Write the implementation

```python
    def _build_previous_messages_from_examples(
        self,
        source_language: str,
        target_language: str,
        examples: list[LLMPreviousExample],
    ) -> tuple[str, str]:
        example_ids = self._build_string_ids(len(examples))
        return (
            self._build_message(
                source_language,
                target_language,
                [
                    {
                        "id": example_id,
                        "source": example["source"],
                        "parts": self._get_string_parts(example["source"], None),
                    }
                    for example_id, example in zip(example_ids, examples, strict=True)
                ],
                [],
            ),
            # The demonstration is the strongest signal in the prompt, so it
            # answers in the structured form the rules ask for rather than the
            # legacy flat array of strings, and echoes the id of every string so
            # the model imitates the identity contract, not only the shape.
            json.dumps(
                [
                    {
                        "id": example_id,
                        "parts": self._get_string_parts(example["target"], None),
                    }
                    for example_id, example in zip(example_ids, examples, strict=True)
                ],
                ensure_ascii=False,
            ),
        )
```

**Step 4:** Run test to verify it passes

Same command. Expected: PASS.

**Step 5:** Commit

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "feat(machinery): echo string ids in the LLM few-shot demonstration"
```

---

### Task A3: State the id contract in the prompt

**Files:**

- Modify: `weblate/machinery/llm.py` — `PROMPT` (82-206)
- Test: `weblate/machinery/tests.py`

**Step 1:** Write the failing test

```python
    def test_prompt_examples_never_show_an_id_less_structured_item(self) -> None:
        prompt = self.get_machine()._get_prompt("cs")

        # A single example answering without an id teaches the model to answer
        # without one, which is the whole defect.
        self.assertNotIn('{"parts"', prompt)
        self.assertIn('"id"', prompt)
```

**Step 2:** Run test to verify it fails

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_prompt_examples_never_show_an_id_less_structured_item"
```

Expected: FAIL on `assertNotIn`, because lines 198, 201 and 205 all show `{"parts": …}`.

**Step 3:** Write the implementation

Edit `PROMPT`. Remember every literal brace is doubled because the prompt is a format string.

Add the id as the first field of each of the three `strings` schema items (currently at lines
106-107, 151-152 and 154-156):

```text
        {{
            "id": "s1f4",                       // identifier of this string; echo it in the output item
            "source": "source @@PH1@@string",   // text to translate with a non-translatable placeable
```

```text
        {{
            "id": "s2ab",
            "source": "another string"          // text to translate without placeables
        }},
        {{
            "id": "s3cd",
            "source": "rephrased string",       // text to rephrase based on existing translation
            "translation": "existing translation"
        }}
```

Replace rules 11, 13, 14 and 20:

```text
11. Output must be a single JSON array containing one item per input string. Every item must carry the "id" of the input string it translates, copied verbatim. Prefer structured objects with a "parts" array when the input has "parts"; a legacy JSON string cannot carry an id and is accepted only when the input contains exactly one string.
```

```text
13. The number of output elements must exactly match the number of input strings, and the set of output "id" values must exactly match the set of input "id" values, each appearing exactly once. Do not emit empty extra strings, diagnostics, explanations, or metadata.
```

```text
14. For structured output, each item must be an object containing only "id" and "parts". The output parts array must have the same placeholder parts as the input. Text parts may be split or merged. Grammar placeholder parts may be reordered within the same surrounding markup if required by target language grammar; markup and syntax placeholder parts must keep source order. Placeholder part type, id, kind, role, close_id, and translatable values must be preserved.
```

Rule 14 already says "id" about placeholder parts; that is a different id and the sentence about
preserving placeholder part metadata stays as it is.

```text
20. Output contract: Return exactly one JSON array, with no characters before `[` or after `]`. What pairs an item with its input is the "id", not the position.
```

Rewrite the four examples so none of them shows an id-less item:

```text
Valid placeholder and markup handling:
[{{"id": "s1f4", "parts": [{{"type": "text", "text": "Click <a href=\"/x\">log out</a> and use @@PH195@@."}}]}}]

Invalid placeholder handling:
[{{"id": "s1f4", "parts": [{{"type": "text", "text": "Click <a href=\"/x\">log out</a> and use \\@\\@PH195\\@\\@."}}]}}]

Valid final punctuation handling, for the source "Он ушёл" with the existing translation "Il est parti.":
[{{"id": "s1f4", "parts": [{{"type": "text", "text": "Il est parti"}}]}}]

Invalid final punctuation handling, adding a full stop the source does not have:
[{{"id": "s1f4", "parts": [{{"type": "text", "text": "Il est parti."}}]}}]

Respond ONLY with a valid JSON array, one item per input string, each carrying the "id" of the string it translates. Prefer structured objects when "parts" are present:

[{{"id": "s1f4", "parts": [{{"type": "text", "text": "translation 1"}}]}}, {{"id": "s2ab", "parts": [{{"type": "text", "text": "translation 2"}}]}}]
```

**Step 4:** Run test to verify it passes

Same command. Expected: PASS.

**Step 5:** Commit

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "feat(machinery): require the reply to echo the string id in the prompt"
```

---

### Task A4: Pair the reply with its sources by id

This is the task that closes the hole. Everything before it only set up the inputs.

**Files:**

- Modify: `weblate/machinery/llm.py` — `_validate_translations` (2457-2492),
  `_parse_llm_translations` (2892-2946), `_fetch_llm_batch` (2734-2758), `_afetch_llm_batch`
  (2848-2872). `_resolve_reply_order` is new, added next to `_normalize_translation_items`
  (2429-2455), which itself stays unchanged.
- Test: `weblate/machinery/tests.py`

**Step 1:** Write the failing tests

Three tests: the production shape must be refused, a shuffled but correctly labelled reply must be
paired correctly, and an id-less batch reply must degrade instead of being accepted.

```python
    @http_mock.activate
    def test_translate_refuses_a_batch_reply_with_shifted_ids(self) -> None:
        machine = self.get_machine()
        sources = ["Alpha", "Beta", "Gamma"]
        batch_sizes: list[int] = []

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            batch_sizes.append(len(strings))
            if len(strings) == 1:
                return json.dumps(
                    [
                        {
                            "id": strings[0]["id"],
                            "parts": [
                                {
                                    "type": "text",
                                    "text": f"{strings[0]['source']} (fr)",
                                }
                            ],
                        }
                    ]
                )
            # A reply whose labels track what it actually translated: it
            # skipped the first input, so that id is missing and one it was
            # never given takes its place. The length still matches and there
            # is no placeholder to catch it.
            return json.dumps(
                [
                    {
                        "id": item["id"],
                        "parts": [{"type": "text", "text": f"{item['source']} (fr)"}],
                    }
                    for item in strings[1:]
                ]
                + [
                    {
                        "id": "sffff",
                        "parts": [{"type": "text", "text": "Epsilon (fr)"}],
                    }
                ]
            )

        with (
            patch.object(
                machine, "fetch_llm_translations", side_effect=request_callback
            ),
            patch.object(
                machine, "log_handled_error", wraps=machine.log_handled_error
            ) as handled,
        ):
            translations = machine.download_multiple_translations(
                "en", "fr", [(text, None) for text in sources]
            )

        # Refused as a batch, then re-asked in halves down to single strings,
        # where position is unambiguous, so nothing is lost and nothing is
        # stored against the wrong source.
        self.assertEqual(
            {text: translations[text][0]["text"] for text in sources},
            {text: f"{text} (fr)" for text in sources},
        )
        self.assertEqual(batch_sizes[0], 3)
        self.assertGreater(len(batch_sizes), 1)
        # The refusal is the degradation signal: a model that keeps ignoring
        # the contract turns every batch into single-string requests, and this
        # message is the only thing that makes that visible in the logs.
        self.assertTrue(
            any(
                call.args[0].startswith("Mismatching assistant reply ids")
                for call in handled.call_args_list
            )
        )

    @http_mock.activate
    def test_translate_pairs_a_shuffled_batch_reply_by_id(self) -> None:
        machine = self.get_machine()
        sources = ["Alpha", "Beta", "Gamma"]

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            return json.dumps(
                list(
                    reversed(
                        [
                            {
                                "id": item["id"],
                                "parts": [
                                    {
                                        "type": "text",
                                        "text": f"{item['source']} (fr)",
                                    }
                                ],
                            }
                            for item in strings
                        ]
                    )
                )
            )

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            translations = machine.download_multiple_translations(
                "en", "fr", [(text, None) for text in sources]
            )

        self.assertEqual(
            {text: translations[text][0]["text"] for text in sources},
            {text: f"{text} (fr)" for text in sources},
        )

    @http_mock.activate
    def test_translate_refuses_an_id_less_batch_reply(self) -> None:
        machine = self.get_machine()
        batch_sizes: list[int] = []

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            batch_sizes.append(len(strings))
            return json.dumps([f"{item['source']} (fr)" for item in strings])

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            translations = machine.download_multiple_translations(
                "en", "fr", [("Alpha", None), ("Beta", None)]
            )

        # A legacy reply cannot carry an id, so the batch is halved until each
        # request holds one string. Correctness costs requests here; storing an
        # unverified alignment costs a wrong translation.
        self.assertEqual(batch_sizes, [2, 1, 1])
        self.assertEqual(translations["Alpha"][0]["text"], "Alpha (fr)")
        self.assertEqual(translations["Beta"][0]["text"], "Beta (fr)")
```

**Step 2:** Run tests to verify they fail

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_translate_refuses_a_batch_reply_with_shifted_ids"
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_translate_pairs_a_shuffled_batch_reply_by_id"
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_translate_refuses_an_id_less_batch_reply"
```

Expected: all three FAIL. The first stores `Beta (fr)` under `Alpha`; the second stores
`Gamma (fr)` under `Alpha`; the third records `batch_sizes == [2]`.

**Step 3:** Write the implementation

Add the resolver next to `_normalize_translation_items`:

```python
    @classmethod
    def _resolve_reply_order(
        cls, translations: list[JSONValue], string_ids: list[str]
    ) -> list[JSONValue] | None:
        """
        Pair reply items with their source strings through the echoed id.

        A single-string request needs no id: there is only one pairing. For a
        batch, an item without a known, unique id is refused, because a reply of
        the right length says nothing about its order - the caller then re-asks
        the batch in halves instead of storing an unverified alignment.
        """
        if len(string_ids) == 1:
            return list(translations)
        by_id: dict[str, JSONValue] = {}
        for item in translations:
            if not isinstance(item, dict):
                return None
            item_id = item.get("id")
            if not isinstance(item_id, str) or item_id in by_id:
                return None
            by_id[item_id] = item
        if by_id.keys() != set(string_ids):
            return None
        return [by_id[string_id] for string_id in string_ids]
```

Use it in `_validate_translations`, which gains a required keyword-only `string_ids`. The
resolution happens here rather than inside `_normalize_translation_items`, which stays unchanged,
so that the id failure carries its own error message: the generic mismatch and the id mismatch
are different operational events and must be distinguishable in the logs.

```python
    @classmethod
    def _validate_translations(
        cls,
        translations: JSONValue,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None = None,
        *,
        string_ids: list[str],
    ) -> list[str]:
        translations = cls._normalize_translations(translations, len(sources))
        if isinstance(translations, list) and len(translations) == len(sources):
            ordered = cls._resolve_reply_order(translations, string_ids)
            if ordered is None:
                # Distinct from the generic mismatch below: this message is the
                # signal that batches are degrading to halving. Keep it stable
                # and greppable.
                msg = "Mismatching assistant reply ids."
                raise MachineTranslationError(msg)
            translations = ordered
        translation_list = cls._normalize_translation_items(
            translations, sources, source_occurrences
        )
```

The rest of `_validate_translations` is unchanged. A reply that is not a list of the right length
skips the resolution and fails in `_normalize_translation_items` exactly as today.

Thread the ids through `_parse_llm_translations`, where the keyword is required so no caller can
silently opt out of the check:

```python
    def _parse_llm_translations(
        self,
        translations_string: str | None,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None,
        *,
        string_ids: list[str],
    ) -> DownloadMultipleTranslations:
```

```python
            translations = self._validate_translations(
                translations, sources, source_occurrences, string_ids=string_ids
            )
```

In the same `except MachineTranslationError` branch of `_parse_llm_translations`, where the prefix
rescue does not apply, the re-raise currently replaces the cause with a hardcoded generic message
(`llm.py:2942-2944`). Preserve the original message instead, so the id refusal stays
distinguishable in the log line `log_handled_error` writes:

```python
            msg = str(error)
            self.log_handled_error(msg, extra_log=translations_string)
            raise MachineTranslationError(msg) from error
```

Leave the `_validate_translation_prefix` call inside `_parse_llm_translations` untouched here;
Task A5 changes it. Update both fetch paths to pass the ids:

```python
        return self._parse_llm_translations(
            translations_string, sources, source_occurrences, string_ids=string_ids
        )
```

```python
        return await sync_to_async(self._parse_llm_translations)(
            translations_string, sources, source_occurrences, string_ids=string_ids
        )
```

The reply item now carries `id` next to `parts`. `_normalize_structured_translation`
(`weblate/machinery/llm.py:2321-2413`) only requires `"parts" in translation` and checks exact key
sets on the *parts*, not on the item, so the extra key is tolerated. An existing test at
`weblate/machinery/tests.py:5532` already mocks a reply item carrying an extra `key` field, which
is the same tolerance.

**Step 4:** Run the tests to verify they pass

Run all three commands from Step 2. Expected: PASS.

Then prove the tests are real by reverting the fix and watching them fail:

```bash
git stash push weblate/machinery/llm.py
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_translate_pairs_a_shuffled_batch_reply_by_id"
git stash pop
```

Expected: FAIL while stashed. Two of three regression tests written in an earlier session in this
repository passed against the bug on the first attempt; do not skip this step.

**Step 5:** Commit

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "fix(machinery): pair an LLM batch reply with its sources by echoed id"
```

---

### Task A5: Keep the partial-reply rescue honest

A reply that ends early still answered its first strings and the pipeline keeps them. That path
must check ids too, otherwise it becomes the new hole.

**Files:**

- Modify: `weblate/machinery/llm.py` — `_validate_translation_prefix` (2494-2537),
  `_parse_llm_translations` (2892-2946)
- Test: `weblate/machinery/tests.py`

**Step 1:** Write the failing test

```python
    @http_mock.activate
    def test_translate_rescues_only_the_prefix_that_kept_its_ids(self) -> None:
        machine = self.get_machine()
        sources = ["Alpha", "Beta", "Gamma"]

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            if len(strings) == 1:
                answered = strings
            else:
                # Answers the first string correctly, then stops: the reply is
                # short, not shifted, so its prefix is worth keeping.
                answered = strings[:1]
            return json.dumps(
                [
                    {
                        "id": item["id"],
                        "parts": [{"type": "text", "text": f"{item['source']} (fr)"}],
                    }
                    for item in answered
                ]
            )

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            translations = machine.download_multiple_translations(
                "en", "fr", [(text, None) for text in sources]
            )

        self.assertEqual(
            {text: translations[text][0]["text"] for text in sources},
            {text: f"{text} (fr)" for text in sources},
        )

    @http_mock.activate
    def test_translate_rescues_nothing_from_a_reply_with_wrong_ids(self) -> None:
        machine = self.get_machine()

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            strings = json.loads(content)["strings"]
            if len(strings) == 1:
                return json.dumps(
                    [
                        {
                            "id": strings[0]["id"],
                            "parts": [
                                {
                                    "type": "text",
                                    "text": f"{strings[0]['source']} (fr)",
                                }
                            ],
                        }
                    ]
                )
            # One item, labelled with the id of the string that comes after the
            # one it answers. A prefix rescue must not keep it.
            return json.dumps(
                [
                    {
                        "id": strings[1]["id"],
                        "parts": [
                            {"type": "text", "text": f"{strings[0]['source']} (fr)"}
                        ],
                    }
                ]
            )

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            translations = machine.download_multiple_translations(
                "en", "fr", [("Alpha", None), ("Beta", None)]
            )

        # Every string is answered by the single-string retries, and none of
        # them carries the mislabelled text.
        self.assertEqual(translations["Alpha"][0]["text"], "Alpha (fr)")
        self.assertEqual(translations["Beta"][0]["text"], "Beta (fr)")
        self.assertEqual(len(translations["Beta"]), 1)
```

**Step 2:** Run tests to verify they fail

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_translate_rescues_only_the_prefix_that_kept_its_ids"
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_translate_rescues_nothing_from_a_reply_with_wrong_ids"
```

Expected: the first FAILs with `TypeError: … missing 1 required keyword-only argument`, or passes
only after A4's signature change reaches this path; the second FAILs because the mislabelled item
is rescued positionally and `Beta` ends up holding `Alpha (fr)`.

**Step 3:** Write the implementation

```python
    @staticmethod
    def _reply_item_has_id(item: JSONValue, expected_id: str, batch_size: int) -> bool:
        """Whether an item may be paired with the source at its own position."""
        if batch_size == 1:
            return True
        return isinstance(item, dict) and item.get("id") == expected_id
```

```python
    @classmethod
    def _validate_translation_prefix(
        cls,
        translations: JSONValue,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None = None,
        *,
        string_ids: list[str],
    ) -> list[str]:
        """
        Validate the leading replies, stopping at the first unusable one.

        Every item is checked exactly as in :meth:`_validate_translations`, so a
        returned entry is as trustworthy as one from a complete reply.
        """
        if not isinstance(translations, list):
            return []
```

Add the id gate as the first check inside the loop, before the occurrence bookkeeping:

```python
        for index, (source_text, unit) in enumerate(sources):
            if index >= len(translations):
                break
            if not cls._reply_item_has_id(
                translations[index], string_ids[index], len(string_ids)
            ):
                break
```

Pass the ids at the call site in `_parse_llm_translations`:

```python
            prefix = self._validate_translation_prefix(
                self._normalize_translations(translations, len(sources)),
                sources,
                source_occurrences,
                string_ids=string_ids,
            )
```

**Step 4:** Run tests to verify they pass

Both commands from Step 2. Expected: PASS.

**Step 5:** Commit

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "fix(machinery): check echoed ids in the partial LLM reply rescue"
```

---

### Task A6: Update the existing tests that mock id-less multi-string replies

A static mocked reply cannot echo a random id, so those tests need either a request callback or
deterministic ids.

**Files:**

- Modify: `weblate/machinery/tests.py`

**Step 1:** Add the deterministic-id helper

Add to `OpenAITranslationTest`, near `mock_response` (line 3840). Patch the class, not an
instance: `assert_translate` builds its own machine:

```python
    def patch_string_ids(self):
        """Deterministic request ids, so a static mocked reply can echo them."""
        return patch.object(
            self.MACHINE_CLS,
            "_build_string_ids",
            staticmethod(lambda count: [f"s{index}" for index in range(count)]),
        )
```

**Step 2:** Find every failing test

```bash
./rundev.sh test weblate/machinery/tests.py
```

Collect the failures. The measured candidates are the mocked replies with more than one item for a
request with more than one source:

- `weblate/machinery/tests.py:3999` `test_batch_fetches_glossary_terms_once` - mocks
  `'["Ahoj", "Nazdar"]'` for two units and asserts the glossary is fetched **once per batch**.
  Halving would make that three fetches, so this one must become a request callback that echoes
  the ids; do not "fix" it by relaxing the assertion, which is the property the test exists for.
- `weblate/machinery/tests.py:5755` `test_translate_repairs_truncated_structured_json_container`
- `weblate/machinery/tests.py:7458`, `7469` `'["Premier", "Deuxieme", "Troisieme"]` and
  `'["Premier", "Deuxieme"]'`
- `weblate/machinery/tests.py:7302`, `7315`, `7480`, `7498` - check the source count of each; a
  single-source test with a multi-item reply exercises `_normalize_translations` trimming and is
  unaffected, because a one-string request still aligns positionally.

Subclasses inherit these tests: `OpenAICustomTranslationTest` (7878), `MistralTranslationTest`
(8010), `MistralCustomTranslationTest` (8067), `MistralOptionalPromptSettingsTranslationTest`
(8121) and `AzureOpenAITranslationTest` (8130). Fixing the base test fixes all of them.

**Step 3:** Fix each failure

Two patterns. Where the reply must vary with the request, use a callback that echoes
`item["id"]` as in Task A4. Where a static literal is clearer, wrap the call in
`with self.patch_string_ids():` and write `"id": "s0"`, `"id": "s1"` into the literal.

**Step 4:** Run the whole file to verify

```bash
./rundev.sh test weblate/machinery/tests.py
```

Expected: no failures. `weblate/trans/tests/test_autotranslate.py` is separately flaky under xdist
in this container; if it is touched, compare against a baseline run on `HEAD` before blaming this
change.

**Step 5:** Commit

```bash
git add weblate/machinery/tests.py
git commit -m "test(machinery): echo string ids in mocked LLM batch replies"
```

---

### Task A7: Retire the cache entries written by the old protocol

Without this task Part A does not hold. `BatchMachineTranslation.cache_expiry` is
`30 * 24 * 3600` (`weblate/machinery/base.py:144-145`) and `get_translation_cache_key`
(`weblate/machinery/base.py:713-745`) is built from the languages, threshold, the service's own
cache parts and the source text - nothing about the protocol that produced the reply. A
misaligned target cached during the affected runs would keep being served for up to thirty days
after the fix ships, so the pipeline would look fixed while still handing out the same wrong
string. Clearing the cache by hand is not a substitute: it has to happen on every deployment and
nothing fails if it is forgotten.

**Files:**

- Modify: `weblate/machinery/llm.py` - constants (near `LLM_FULL_GLOSSARY_LIMIT`),
  `get_translation_cache_parts` (1131-1167)
- Test: `weblate/machinery/tests.py`

**Step 1:** Write the failing test

```python
    def test_translation_cache_key_carries_the_batch_protocol_version(self) -> None:
        machine = self.get_machine()
        unit = make_unit(code="fr", source="Alpha")
        arguments = (unit, "en", "fr", "Alpha", 75, [])

        key = machine.get_translation_cache_key(*arguments)
        with patch.object(llm, "LLM_BATCH_PROTOCOL_VERSION", 999):
            bumped = machine.get_translation_cache_key(*arguments)

        # A reply produced by the previous protocol was aligned by position, so
        # its cached result must become unreachable rather than outlive the fix.
        self.assertNotEqual(key, bumped)
```

`weblate/machinery/tests.py` already imports `make_unit`; import the module itself for the patch
target, as `from weblate.machinery import llm`, if it is not imported already.

**Step 2:** Run test to verify it fails

```bash
./rundev.sh test "weblate/machinery/tests.py::OpenAITranslationTest::test_translation_cache_key_carries_the_batch_protocol_version"
```

Expected: FAIL, the two keys are equal - which is the defect.

**Step 3:** Write the implementation

Add the constant next to the other LLM constants:

```python
# Bumped whenever the batch request or reply contract changes in a way that
# makes an older cached reply untrustworthy. Version 2 added the per-string id
# the reply must echo; a version 1 reply was aligned by position alone, so its
# cached results must not survive the upgrade.
LLM_BATCH_PROTOCOL_VERSION = 2
```

Include it in the LLM cache parts, in `get_translation_cache_parts`:

```python
        result = (
            f"proto{LLM_BATCH_PROTOCOL_VERSION}",
            self.get_glossary_cache_part(unit),
            self.get_llm_glossary_cache_part(unit),
            *super().get_translation_cache_parts(
                unit,
                source_language,
                target_language,
                text,
                threshold,
                replacements,
                source_occurrence=source_occurrence,
            ),
        )
```

The version is read as a module global on every call, so the test's `patch.object` takes effect.
Scope it to `BaseLLMTranslation`: no other service's alignment changed, and invalidating their
caches would cost real requests for nothing.

**Step 4:** Run test to verify it passes

Same command. Expected: PASS.

**Step 5:** Commit

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "fix(machinery): version the LLM batch protocol in the translation cache key"
```

---

### Task A8: Changelog

**Files:**

- Modify: `docs/changes.rst` - the `.. rubric:: Bug fixes` list of the unreleased
  `Weblate 2026.8.1` section, which starts at line 45 and currently ends at line 58

**Step 1:** Read the section and append one entry

Re-read the range before editing; the line numbers shift with every other change landing in the
same section.

```rst
* Large language model machine translation now pairs each translated string in a batch reply with its source through an identifier the reply must echo, instead of trusting the reply's order. A reply that answers a different set of strings than it was given - dropping one, repeating one, or adding one from beyond the batch - previously matched on length alone and was stored against the wrong strings; it is now refused and re-asked in smaller batches. Suggestions cached by the previous protocol are no longer reused.
```

**Step 2:** Verify the changelog builds

```bash
uv run prek run rst-double-space rst-http sphinx-lint --files docs/changes.rst
```

Expected: all hooks pass.

**Step 3:** Commit

```bash
git add docs/changes.rst
git commit -m "docs(changes): note LLM batch reply identity checking"
```

---

### Task A9: Full verification of Part A

**Step 1:** Static checks on the touched files

```bash
uv run prek run ruff-check ruff-format --files weblate/machinery/llm.py weblate/machinery/tests.py
uv run mypy --show-column-numbers weblate/machinery/llm.py | ./scripts/filter-mypy.sh
uv run pylint weblate/machinery/llm.py
```

Expected: no new findings. Pass explicit hook ids: a bare `prek run --files …` runs every hook and
`ruff-check --fix` has previously modified files outside the given set.

**Step 2:** Run every suite that touches the LLM machinery

```bash
./rundev.sh test weblate/machinery/tests.py
./rundev.sh test weblate_customization/tests/test_machinery.py
./rundev.sh test weblate/trans/tests/test_autotranslate.py
```

Expected: green, modulo the known xdist flakiness of `test_autotranslate.py`, which must be
compared against a baseline run rather than assumed.

**Step 3:** Eyeball a real rendered request

```bash
./rundev.sh exec weblate weblate shell < analysis/probes/col4-prompt-probe.py
```

The probe passes four positional arguments to `_prepare_llm_translation` and still works through
the `string_ids=None` default. Confirm in its output that every item of `strings` carries a
distinct `"id"` starting with `s`, and that the prompt's closing example shows ids.

**Step 4:** Commit nothing, report

Part A is verified when the three suites are green and the rendered request carries ids. Do not
mark this plan done from the unit tests alone: the defect it fixes was invisible to unit tests for
as long as the protocol existed.

---

## Part B: superseded

The `game-number` work moved to
`docs/product/plans/2026-08-24-game-number-value-comparison.md`, which rejected the global
scale table proposed here: one alternation over every language reads a Russian word in a
German string, and folding a target's digits into a scale it cannot verify turns correct
translations red. Do not execute the removed B1-B3 tasks.

---

## Out of scope

- **Repairing the strings already corrupted in production.** All 396 units of nine languages of
  `heart-abyss/hub-1` are raw machine output, and §5-§8 of the measurement lists the individual
  fixes. That needs write access to production and its own approval; this plan only stops the
  pipeline from producing more.
- **The prompt's pre-existing invalid-JSON example.** Lines 192 and 195 render as JSON with
  unescaped inner quotes, which is a separate prompt defect. Task A3 keeps them as they are apart
  from adding the id.
- **`weblate_customization/src/weblate_customization/machinery.py`.** `RoutedLLMTranslation`
  inherits the batch path from `OpenAITranslation` and needs no change; it is fixed by Part A the
  same way every other LLM service is.
