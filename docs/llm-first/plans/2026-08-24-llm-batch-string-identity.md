# LLM batch string identity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the LLM batch protocol verify which source string each reply item belongs to, so a reply of the correct length can no longer be stored against the wrong strings.

**Architecture:** Every string in a batch request carries an opaque, request-scoped `id`. The reply must echo it, and the reply is paired with its sources through that id instead of the list position. A batch reply without usable ids is refused and re-asked in halves by the machinery that already exists, down to single-string requests where position is unambiguous. Part B is independent: it makes the `game-number` check compare numeric *values* instead of digit multisets, so the same defect class becomes detectable at all.

**Tech Stack:** Python 3.13, Django, `weblate/machinery/llm.py` (`BaseLLMTranslation`, shared by OpenAI, Azure OpenAI, Mistral and the fork's `RoutedLLMTranslation`), `weblate_customization/` checks, pytest inside the `dev-docker` container.

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

Caught: a model that skips an input and shifts the rest up. It drops that input's id along with it,
so the set of echoed ids no longer matches the set that was sent, and the reply is refused. This is
the shape measured on production.

Not caught: a model that deliberately writes the translation of input *i+1* next to the id of input
*i*. No protocol can detect a reply that lies about its own labels. State this limit; do not claim
the alignment is proven.

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

- Modify: `weblate/machinery/llm.py` — `_normalize_translation_items` (2429-2455),
  `_validate_translations` (2457-2492), `_parse_llm_translations` (2892-2946),
  `_fetch_llm_batch` (2734-2758), `_afetch_llm_batch` (2848-2872)
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
            # The production shape: the model skipped the first input and
            # shifted the rest up, pulling one more in from beyond the batch.
            # The length still matches and there is no placeholder to catch it.
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

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
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

Use it in `_normalize_translation_items`, which gains a required keyword-only `string_ids`:

```python
    @classmethod
    def _normalize_translation_items(
        cls,
        translations: JSONValue,
        sources: list[tuple[str, Unit | None]],
        source_occurrences: list[int] | None = None,
        *,
        string_ids: list[str],
    ) -> list[str] | None:
        if not isinstance(translations, list) or len(translations) != len(sources):
            return None

        ordered = cls._resolve_reply_order(translations, string_ids)
        if ordered is None:
            return None
```

and read from `ordered` instead of `translations`:

```python
            normalized = cls._normalize_translation_item(
                ordered[index], source_text, unit, source_occurrence
            )
```

Thread the ids through `_validate_translations`:

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
        translation_list = cls._normalize_translation_items(
            translations, sources, source_occurrences, string_ids=string_ids
        )
```

and through `_parse_llm_translations`, where the keyword is required so no caller can silently opt
out of the check:

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
* Large language model machine translation now pairs each translated string in a batch reply with its source through an identifier the reply must echo, instead of trusting the reply's order. A model that skips a string and shifts the rest produced a reply of the correct length whose translations were stored against the wrong strings, which no placeholder or length check could detect; such a reply is now refused and re-asked in smaller batches. Suggestions cached by the previous protocol are no longer reused.
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

## Part B: make `game-number` compare values, not digits

Independently shippable. Dropping Part B does not affect Part A. It is here because §3.1 of the
measurement showed the check that should have caught this defect class cannot: on the two bounty
strings of `heart-abyss/hub-1` it produced **10 false positives, 1 accidental true positive and 2
false negatives**. The component holds three genuine numeric errors; the check sees one of them,
and only because Chinese numerals carry no ASCII digits for it to compare against.

The cause is that `_numbers` (`weblate_customization/src/weblate_customization/checks.py:282-292`)
compares digit multisets. Japanese writes 100 000 as `10万`, whose digits `[1, 0]` match the
source's `10` exactly, so a tenfold error is invisible; and `10 000` written as `10,000` looks
different from `10 тысяч`, so a correct translation is flagged.

The implementation below was run against the fork's own number regexes (`NUMBER`, `FULL_DATE`,
`URL`, `ORDINAL`, `THOUSANDS`, `_fold_digits`, `_collapse_grouping`) before this plan was written:
all 18 rows of the two matrices in Task B1 come out as stated, and the 15 cases already in
`GameNumberCheckTest` keep their current verdicts. The expected values in the tests are measured,
not guessed - if a row disagrees during execution, the implementation was mistyped, not the
expectation.

### Task B1: Fold scale words and CJK numerals into values

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py` - constants near line 36,
  `_numbers` (282-292)
- Test: `weblate_customization/tests/test_checks.py` - `GameNumberCheckTest` (189)

**Step 1:** Write the failing test

The matrix is the measured production data from the report's §3.1 tables. Source is the Russian
bounty line; every row states what the target means.

```python
    def test_a_scale_word_is_part_of_the_value(self) -> None:
        # Measured on heart-abyss/hub-1 (hub1_guard_1_3): the source promises
        # 10 000 and only Japanese changes the amount.
        source = "Награда - 10 тысяч мон!"
        for target, expected in (
            ("Belohnung - 10 Tausend Mon!", False),
            ("Reward - 10,000 mon!", False),
            ("¡Recompensa: 10 000 mon!", False),
            ("Récompense - 10 000 mons !", False),
            ("Ricompensa - 10 mila mon!", False),
            ("보상은 10천 몬!", False),
            ("奖励一万文!", False),
            ("獎勵一萬文!", False),
            ("報酬は10万文!", True),
        ):
            with self.subTest(target=target):
                self.assertEqual(
                    self.check.check_single(source, target, None), expected
                )

    def test_a_myriad_scale_error_is_reported(self) -> None:
        # hub1_guard_1_4: the source promises 100 000. Japanese renders a
        # million, Simplified Chinese renders a million, and the rest are right.
        source = "Научись считать - ваще та 100 тысяч мон!"
        for target, expected in (
            ("Lern zählen - es sind 100 Tausend Mon!", False),  # codespell:ignore
            ("Learn to count-it's 100,000 mon", False),
            ("¡Aprende a contar, son 100 000 mon!", False),
            ("Impara a contare, sono 100 mila mon!", False),
            ("계산 좀 배워, 10만 몬이라고!", False),
            ("學會數數吧--那可是十萬文!", False),
            ("数え方を覚えろよ、こいつは100万文だぜ!", True),
            ("学学数数吧--那可是一百万文!", True),
        ):
            with self.subTest(target=target):
                self.assertEqual(
                    self.check.check_single(source, target, None), expected
                )
```

**Step 2:** Run tests to verify they fail

The module is imported from `/app/data/python`, not from `src/`, so the copy is part of running
the test at all. Editing `src/` and running the test without copying silently tests the old code:

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest::test_a_scale_word_is_part_of_the_value"
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest::test_a_myriad_scale_error_is_reported"
```

Expected: FAIL. Today the German, Italian and Korean rows pass by accident, the English, Spanish,
French and both Chinese rows report a defect that is not there, and both Japanese rows report
nothing.

**Step 3:** Write the implementation

Add near the other regex constants:

```python
# A scale word carries the zeros a digit would spell out, so "10 тысяч",
# "10 Tausend" and "10천" are one quantity while "10万" is ten times more.
# The tokens do not collide across languages, so one table serves every target
# and the check stays usable without a language argument.
WORD_SCALES: dict[str, int] = {
    "тыс": 1_000,
    "тысяча": 1_000,
    "тысячи": 1_000,
    "тысяч": 1_000,
    "млн": 1_000_000,
    "миллион": 1_000_000,
    "миллиона": 1_000_000,
    "миллионов": 1_000_000,
    "thousand": 1_000,
    "million": 1_000_000,
    "tausend": 1_000,
    "millionen": 1_000_000,
    "mila": 1_000,
    "milione": 1_000_000,
    "milioni": 1_000_000,
    "mil": 1_000,
    "millon": 1_000_000,  # codespell:ignore
    "millón": 1_000_000,
    "millones": 1_000_000,
    "mille": 1_000,
    "millions": 1_000_000,
}
# Scale characters that follow a digit without a space: 10만, 10万, 10千.
CHAR_SCALES: dict[str, int] = {
    "천": 1_000,
    "만": 10_000,
    "억": 100_000_000,
    "十": 10,
    "百": 100,
    "千": 1_000,
    "万": 10_000,
    "萬": 10_000,
    "億": 100_000_000,
    "亿": 100_000_000,
}
CJK_DIGITS: dict[str, int] = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CJK_SMALL_SCALES: dict[str, int] = {"十": 10, "百": 100, "千": 1_000}
CJK_BIG_SCALES: dict[str, int] = {
    "万": 10_000,
    "萬": 10_000,
    "億": 100_000_000,
    "亿": 100_000_000,
}
_WORD_SCALE_ALTERNATION = "|".join(
    sorted((regex.escape(word) for word in WORD_SCALES), key=len, reverse=True)
)
WORD_SCALED_NUMBER = regex.compile(
    rf"(?<!\d)(\d+(?:[.,]\d+)?)\s*({_WORD_SCALE_ALTERNATION})\b",
    regex.IGNORECASE,
)
CHAR_SCALED_NUMBER = regex.compile(
    rf"(?<!\d)(\d+(?:[.,]\d+)?)\s*([{''.join(CHAR_SCALES)}])"
)
# Two or more numeral characters, at least one of them a scale: 一万, 十萬,
# 一百万. A lone 十 in prose is not a quantity.
CJK_NUMBER = regex.compile(
    rf"[{''.join(CJK_DIGITS)}{''.join(CJK_SMALL_SCALES)}{''.join(CJK_BIG_SCALES)}]{{2,}}"
)
```

Add the value helpers above `_numbers`:

```python
def _format_value(value: Decimal) -> str:
    """Render a folded quantity the way the number tokenizer would see it."""
    return format(value.normalize(), "f")


def _cjk_value(run: str) -> Decimal | None:
    """Value of a Chinese or Japanese numeral run, or None when it is not one."""
    if not any(char in CJK_BIG_SCALES or char in CJK_SMALL_SCALES for char in run):
        return None
    total = 0
    section = 0
    digit = 0
    for char in run:
        if char in CJK_DIGITS:
            digit = CJK_DIGITS[char]
        elif char in CJK_SMALL_SCALES:
            section += (digit or 1) * CJK_SMALL_SCALES[char]
            digit = 0
        elif char in CJK_BIG_SCALES:
            total += (section + digit or 1) * CJK_BIG_SCALES[char]
            section = 0
            digit = 0
        else:
            return None
    return Decimal(total + section + digit)


def _fold_scales(text: str) -> str:
    """Rewrite a quantity written with a scale word as the value it states."""

    def word(match: regex.Match[str]) -> str:
        multiplier = WORD_SCALES[match.group(2).casefold()]
        return _format_value(Decimal(match.group(1).replace(",", ".")) * multiplier)

    def char(match: regex.Match[str]) -> str:
        multiplier = CHAR_SCALES[match.group(2)]
        return _format_value(Decimal(match.group(1).replace(",", ".")) * multiplier)

    def cjk(match: regex.Match[str]) -> str:
        value = _cjk_value(match.group())
        return match.group() if value is None else _format_value(value)

    folded = WORD_SCALED_NUMBER.sub(word, text)
    folded = CHAR_SCALED_NUMBER.sub(char, folded)
    return CJK_NUMBER.sub(cjk, folded)
```

Fold inside `_numbers`, after grouping is collapsed so `10,000` is already one token:

```python
def _numbers(text: str, *, drop_ordinals: bool = False) -> Counter[str]:
    """Quantities outside markup, URLs and dates; digits, grouping and scale folded."""
    body = _fold_digits(URL.sub(" ", text))
    body = FULL_DATE.sub(" ", MARKUP.sub(" ", body))
    if drop_ordinals:
        body = ORDINAL.sub(" ", body)
    body = _fold_scales(_collapse_grouping(body))
    return Counter(
        match.group().replace(",", ".").replace("\u066b", ".")
        for match in NUMBER.finditer(body)
    )
```

Add `from decimal import Decimal` to the imports if it is not already there.

**Step 4:** Run tests to verify they pass

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest"
```

Expected: PASS, including the pre-existing cases: the decimal-separator good match, the three
`test_failure_*` cases, thousands grouping, dates, placeholders and the added-number acceptance.

Then revert the implementation and confirm both new tests fail:

```bash
git stash push weblate_customization/src/weblate_customization/checks.py
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test "weblate_customization/tests/test_checks.py::GameNumberCheckTest::test_a_myriad_scale_error_is_reported"
git stash pop
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
```

**Step 5:** Commit

```bash
git add weblate_customization/src/weblate_customization/checks.py weblate_customization/tests/test_checks.py
git commit -m "fix(checks): compare stated values in game-number instead of digit multisets"
```

---

### Task B2: Record the known limit and update the changelog

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py` - `GameNumberCheck`
  docstring (295-310)
- Modify: `docs/changes.rst` - the existing `game-number` entry in the unreleased
  `Weblate 2026.8.1` improvements list, currently line 31

**Step 1:** Note the limit next to the code

Folding a figurative CJK quantity is possible: a source that writes `百万` for "millions"
without a digit now states 1 000 000, and a target that spells it out in words carries no number,
so the check reports it. Say so where a maintainer will read it, in the class docstring:

```python
class GameNumberCheck(TargetCheck):
    """
    Every quantity the source states must survive into the translation.

    Comparison is by value, not by digits: a scale word carries the zeros a
    digit would spell out, so "10 тысяч" equals "10 Tausend" and "10,000" but
    not "10万". A figurative CJK quantity written without a digit, such as
    百万 for "millions", is read as the value it literally states.
    """
```

**Step 2:** Extend the changelog entry instead of adding one

The `game-number` check is itself unreleased - it sits in the unreleased 2026.8.1 improvements
list - so this is a fix to an unreleased feature and gets no entry of its own. Extend the existing
sentence in the same entry so the released description is accurate. Re-read line 31 before editing
and append to that paragraph:

```rst
A quantity written with a scale word is compared by the value it states, so ``10 тысяч``, ``10 Tausend``, ``10,000`` and ``一万`` are one number while ``10万`` is ten times more.
```

**Step 3:** Verify

```bash
uv run prek run rst-double-space rst-http sphinx-lint --files docs/changes.rst
uv run prek run ruff-check ruff-format --files weblate_customization/src/weblate_customization/checks.py
```

Expected: all hooks pass.

**Step 4:** Commit

```bash
git add weblate_customization/src/weblate_customization/checks.py docs/changes.rst
git commit -m "docs(checks): document value comparison in game-number"
```

---

### Task B3: Full verification of Part B

**Step 1:** Deploy the module copy and run the customization suites

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
./rundev.sh test weblate_customization/tests/test_checks.py
./rundev.sh test weblate_customization/tests/test_autofixes.py
./rundev.sh test weblate/checks
```

Expected: green. `weblate/checks` is included because `CHECK_LIST` loads the fork's checks.

**Step 2:** Replay the check over the real component

The report's evidence is a production component, and the Check table only reflects the check code
that ran the last time a unit was touched. Re-run the check locally over the same kit rather than
reading stale rows: load the nine `heart-abyss/hub-1` targets of `hub1_guard_1_3` and
`hub1_guard_1_4` and confirm exactly three firings - `ja` on both strings and `zh_Hans` on
`hub1_guard_1_4` - and no others. Two of the three are invisible to the check today.

**Step 3:** Commit nothing, report

Part B is verified when the two bounty strings produce exactly the two Japanese firings and the
existing check tests are green.

---

### Out of scope

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
