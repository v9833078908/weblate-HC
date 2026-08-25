# LLM long-string chunk fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this
> plan task-by-task. Use test-driven-development for every task.

**Status:** proposed, awaiting approval.

**Goal:** A single long multi-paragraph string whose one-shot LLM translation is
rejected by the reply parser gets translated paragraph-by-paragraph and stored as
one string, instead of being silently left empty.

**Architecture:** `BaseLLMTranslation._download_multiple_translations_with_context_cache`
(and its async twin) currently halve a failed batch; a batch of one string cannot be
halved and the error propagates, leaving the unit empty. This plan adds one fallback
at exactly that point: split the single source on blank lines, merge fragments whose
markup placeholders open in one fragment and close in another (or give up), translate
the fragments as one ordinary LLM batch, reassemble with the original separators, and
accept the result only if the reassembled string carries the exact placeholder multiset
of the source. No component changes, no `max-length` flags, no schema changes.

**Tech Stack:** Django, `weblate/machinery/llm.py`, pytest (`weblate/machinery/tests.py`),
dev container via `./rundev.sh test`.

---

## Incident evidence (why this exact fix)

Production `l10n.herocraft.com`, 2026-08-24 13:04-13:06 UTC, component
`need-for-greed/google-play`, auto-translate run by `mt:openrouter`. Worker log
(read-only, `docker logs hcgameloc-weblate-1`) shows for **every** language the same
sequence:

1. `fetching translations for 3 units from OpenRouter, 10 per request` — one batch:
   `gp_name`, `gp_short`, `gp_full`.
2. `Incomplete assistant reply: 2/3.` — the model answers the two short strings and
   drops the 1877-character `gp_full` (20 markup placeholders: `<b>`/`</b>` ×10).
3. The prefix rescue re-asks `gp_full` alone → `Mismatching assistant reply.` The
   `extra_log` for pt_BR shows why: the model put translated text **inside** a
   markup placeholder part and set `translatable: true`:
   `{"type": "placeholder", "text": "Need for Greed: Mining Time é um RPG…", "id": "@@PH0@@", "kind": "markup", "translatable": true}`.
   `_parse_llm_translations` correctly rejects this (placeholder metadata must be
   preserved).
4. A batch of one cannot be halved (`_split_sources` → `None`), the error is raised,
   the unit stays empty. Result: `gp_full` empty in all store languages while
   `gp_name`/`gp_short` are stored.

Ruled out by the same log window: no `finish_reason=length` (no truncation), no HTTP
errors, no rate limits. The defect is deterministic model behavior on long
many-placeholder markup text; short strings with the same markup translate fine.
Paragraphs of `gp_full` carry at most 2 placeholders each.

## Decisions

1. **Fallback triggers only where the current code gives up**: a
   `MachineTranslationError` on a batch that `_split_sources` cannot halve
   (one string), when that string contains at least one `\n\n`. Rate-limit errors
   never trigger it (splitting a refused batch only sends more refused requests).
2. **Safe split rule (mandatory, from review):** never cut between fragments when a
   markup tag opens in one fragment and closes in another. Fragment balance is
   computed from the unit's placeholder mapping (`@@PHn@@` → original content, the
   same mapping `_build_string_payload` sends to the model). Unbalanced fragments
   are merged with their successor; if the tail never balances, the fallback
   declines and the original error propagates. A placeholder token without a
   mapping makes its fragment ineligible for a cut on either side (conservative).
3. **Whole-string validation:** after reassembly,
   `_extract_placeholders(result) == _extract_placeholders(source)` (the existing
   `Counter` over placeholder tokens) must hold, or the fallback declines. Each
   fragment already passed the full per-string parser checks
   (`_parse_llm_translations`), so markup inside fragments is validated by the
   existing machinery, not by anything new.
4. **Fresh translations only:** the fallback declines when the unit already has a
   translation (`unit.translated`), because `_get_message` would attach the whole
   existing translation to every fragment.
5. **No recursion:** the fragment batch goes through `_fetch_llm_batch` directly.
   If the fragment batch itself fails, the fallback declines. Bounded: at most one
   extra request per rescued string.
6. **Out of scope, each needing its own approval:**
   - JSON-schema tightening in `weblate_customization/machinery.py` (forbid text
     inside non-translatable placeholder parts) — optional hardening, separate
     increment.
   - Any `max-length` flags — stored-target limits, unrelated to this defect.
   - Production deploy and the re-run of `context:gp_full state:empty` — rollout
     section below, explicit approval required.

## The seam

```text
_download_multiple_translations_with_context_cache()   # sync, llm.py:2624
    except MachineTranslationError:
        halves = self._split_sources(...)
        if halves is None:
            + fallback = self._translate_in_chunks(...)   # NEW
            + if fallback is not None: return fallback
            raise
_adownload_multiple_translations_with_context_cache()  # async twin, same shape
```

New private helpers on `BaseLLMTranslation` (all pure except the one HTTP call):

```python
LLM_CHUNK_SEPARATOR = "\n\n"

def _chunk_fragments(text) -> list[str] | None          # split, None if < 2 fragments
def _merge_unbalanced_fragments(fragments, mapping) -> list[str] | None
def _translate_in_chunks(source_language, target_language, sources) -> DownloadMultipleTranslations | None
```

---

### Task 1: Fragment splitting and the safe-merge rule (pure helpers)

**Files:**
- Modify: `weblate/machinery/llm.py` (near `_split_sources`, `llm.py:2700`)
- Test: `weblate/machinery/tests.py` (same module-level test class as
  `test_translate_continues_a_reply_that_ends_early`, `tests.py:5559`; reuse its
  `get_machine()` fixture)

**Step 1: Write the failing tests**

```python
def test_chunk_fragments_splits_on_blank_lines(self) -> None:
    machine = self.get_machine()
    text = "@@PH1@@Head@@PH2@@\n\nBody one.\n\nBody two."
    self.assertEqual(
        machine._chunk_fragments(text),
        ["@@PH1@@Head@@PH2@@", "Body one.", "Body two."],
    )
    # No blank line -> no fallback candidate.
    self.assertIsNone(machine._chunk_fragments("single paragraph"))

def test_merge_unbalanced_fragments_joins_split_markup(self) -> None:
    machine = self.get_machine()
    # <b> opens in fragment 1 and closes in fragment 2: they must travel together.
    fragments = ["@@PH1@@Bold start", "still bold@@PH2@@", "plain tail"]
    mapping = {"@@PH1@@": "<b>", "@@PH2@@": "</b>"}
    self.assertEqual(
        machine._merge_unbalanced_fragments(fragments, mapping),
        ["@@PH1@@Bold start\n\nstill bold@@PH2@@", "plain tail"],
    )

def test_merge_unbalanced_fragments_declines_when_never_balanced(self) -> None:
    machine = self.get_machine()
    self.assertIsNone(
        machine._merge_unbalanced_fragments(["@@PH1@@open", "never closed"], {"@@PH1@@": "<b>"})
    )

def test_merge_declines_on_unmapped_placeholder_at_boundary(self) -> None:
    machine = self.get_machine()
    # Conservative rule: a token we cannot classify keeps its fragment merged.
    self.assertIsNone(
        machine._merge_unbalanced_fragments(["@@PH9@@x", "y"], {})
    )
```

**Step 2: Run tests to verify they fail**

Run: `./rundev.sh test weblate/machinery/tests.py -k chunk_fragments -k merge_unbalanced -p no:randomly --no-cov -q`
Expected: FAIL — `AttributeError: ... has no attribute '_chunk_fragments'`.

**Step 3: Implement the helpers**

In `weblate/machinery/llm.py`, next to `_split_sources`:

```python
LLM_CHUNK_SEPARATOR = "\n\n"  # module level, near LLM_PREFIX_RESCUE_LIMIT
_MARKUP_OPEN_RE = re.compile(r"^<([a-zA-Z][\w-]*)(?:\s[^>]*)?>$")
_MARKUP_CLOSE_RE = re.compile(r"^</([a-zA-Z][\w-]*)>$")

@classmethod
def _chunk_fragments(cls, text: str) -> list[str] | None:
    fragments = text.split(LLM_CHUNK_SEPARATOR)
    if len(fragments) < 2:
        return None
    return fragments

@classmethod
def _fragment_tag_delta(cls, fragment: str, mapping: dict[str, str]) -> list[str] | None:
    """Open-tag stack this fragment leaves behind; None when unclassifiable."""
    stack: list[str] = []
    for token, _end in cls._iter_placeholders(fragment):
        original = mapping.get(token)
        if original is None:
            return None
        if opened := _MARKUP_OPEN_RE.match(original):
            stack.append(opened.group(1).lower())
        elif closed := _MARKUP_CLOSE_RE.match(original):
            if not stack or stack[-1] != closed.group(1).lower():
                return None
            stack.pop()
        # Non-tag placeholders ({0}, %KEY%) never span fragments: neutral.
    return stack

@classmethod
def _merge_unbalanced_fragments(
    cls, fragments: list[str], mapping: dict[str, str]
) -> list[str] | None:
    merged: list[str] = []
    pending = ""
    for fragment in fragments:
        pending = fragment if not pending else pending + LLM_CHUNK_SEPARATOR + fragment
        delta = cls._fragment_tag_delta(pending, mapping)
        if delta is None and cls._extract_placeholders(pending):
            # Unclassifiable token: only safe as a whole string.
            return None
        if not delta:
            merged.append(pending)
            pending = ""
    if pending:
        return None  # tail never balanced
    return merged if len(merged) > 1 else None
```

Note for the executor: `_iter_placeholders` and `_extract_placeholders` already
exist (`llm.py:1758`); verify their exact names before writing. `_fragment_tag_delta`
treating *any* unmapped placeholder as unclassifiable is deliberately conservative —
`test_merge_declines_on_unmapped_placeholder_at_boundary` pins it.

**Step 4: Run tests to verify they pass**

Run: `./rundev.sh test weblate/machinery/tests.py -k "chunk_fragments or merge_unbalanced or merge_declines" -p no:randomly --no-cov -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "feat(machinery): add safe paragraph fragmenting for long LLM sources"
```

---

### Task 2: The fallback itself, wired into the sync path

**Files:**
- Modify: `weblate/machinery/llm.py:2624-2687` (sync
  `_download_multiple_translations_with_context_cache`)
- Test: `weblate/machinery/tests.py`

**Step 1: Write the failing tests**

All tests patch `machine.fetch_llm_translations` exactly like
`test_translate_continues_a_reply_that_ends_early` (`tests.py:5559`) and count/inspect
requests via the decoded `content` JSON.

```python
LONG_SOURCE = (
    "@@PH1@@Need for Greed is an RPG.@@PH2@@"
    "\n\nBrave mysterious lands full of danger."
    "\n\n@@PH3@@Dig, Craft, and Trade!@@PH4@@"
    "\n\nPlay this RPG as a true Digger."
)

def test_translate_falls_back_to_chunks_for_a_single_long_string(self) -> None:
    machine = self.get_machine()
    requested: list[int] = []

    def request_callback(_prompt, content, _pc, _pr):
        strings = [item["source"] for item in json.loads(content)["strings"]]
        requested.append(len(strings))
        if len(strings) == 1:
            return json.dumps(["mangled @@PH1@@ reply"])  # whole-string attempt fails
        return json.dumps([f"{text} (fr)" for text in strings])  # fragments succeed

    with patch.object(machine, "fetch_llm_translations", side_effect=request_callback):
        translations = machine.download_multiple_translations(
            "en", "fr", [(LONG_SOURCE, None)]
        )

    # One whole-string attempt, then one fragment batch of 4.
    self.assertEqual(requested, [1, 4])
    result = translations[LONG_SOURCE][0]["text"]
    self.assertEqual(result.count("\n\n"), 3)
    for token in ("@@PH1@@", "@@PH2@@", "@@PH3@@", "@@PH4@@"):
        self.assertIn(token, result)

def test_chunk_fallback_declines_without_blank_lines(self) -> None:
    machine = self.get_machine()
    with (
        patch.object(
            machine,
            "fetch_llm_translations",
            return_value=json.dumps(["bad", "extra"]),
        ),
        self.assertRaises(MachineTranslationError),
    ):
        machine.download_multiple_translations("en", "fr", [("one line only", None)])

def test_chunk_fallback_declines_on_rate_limit(self) -> None:
    machine = self.get_machine()
    calls: list[int] = []

    def request_callback(_prompt, content, _pc, _pr):
        calls.append(len(json.loads(content)["strings"]))
        raise MachineryRateLimitError("rate-limited upstream")

    with (
        patch.object(machine, "fetch_llm_translations", side_effect=request_callback),
        self.assertRaises(MachineryRateLimitError),
    ):
        machine.download_multiple_translations("en", "fr", [(LONG_SOURCE, None)])
    self.assertEqual(calls, [1])  # no fragment batch after a refusal

def test_chunk_fallback_failure_propagates_original_error(self) -> None:
    machine = self.get_machine()

    def request_callback(_prompt, content, _pc, _pr):
        strings = json.loads(content)["strings"]
        return json.dumps(["broken"] * (len(strings) + 1))  # every reply mismatches

    with (
        patch.object(machine, "fetch_llm_translations", side_effect=request_callback),
        self.assertRaises(MachineTranslationError),
    ):
        machine.download_multiple_translations("en", "fr", [(LONG_SOURCE, None)])
```

**Step 2: Run tests to verify they fail**

Run: `./rundev.sh test weblate/machinery/tests.py -k chunk_fallback -p no:randomly --no-cov -q`
Expected: the first test FAILS (error raised instead of fallback); the decline tests
may already pass — that is fine, they pin behavior.

**Step 3: Implement `_translate_in_chunks` and wire it in**

```python
def _translate_in_chunks(
    self,
    source_language,
    target_language,
    sources: list[tuple[str, Unit | None]],
) -> DownloadMultipleTranslations | None:
    """Rescue one long string by translating its paragraphs as a batch."""
    if len(sources) != 1:
        return None
    text, unit = sources[0]
    if unit is not None and unit.translated:
        return None  # _get_message would attach the whole old translation per fragment
    fragments = self._chunk_fragments(text)
    if fragments is None:
        return None
    mapping = self._build_string_payload(text, unit, source_language, 0).get(
        "placeholders", {}
    )
    fragments = self._merge_unbalanced_fragments(fragments, mapping)
    if fragments is None:
        return None
    try:
        fragment_results = self._fetch_llm_batch(
            source_language,
            target_language,
            [(fragment, unit) for fragment in fragments],
            None,
        )
    except MachineryRateLimitError:
        raise
    except MachineTranslationError:
        return None
    parts: list[str] = []
    queues = {key: list(value) for key, value in fragment_results.items()}
    for fragment in fragments:
        queue = queues.get(fragment)
        if not queue:
            return None
        parts.append(queue.pop(0)["text"])
    joined = LLM_CHUNK_SEPARATOR.join(parts)
    if self._extract_placeholders(joined) != self._extract_placeholders(text):
        return None
    self.log_handled_error(
        f"Rescued a long string in {len(fragments)} chunks.", extra_log=None
    )
    return self._build_translation_results([joined], sources)
```

Executor notes:
- Verify `_build_string_payload`'s exact signature (`llm.py:1092`) — the plan assumes
  `(source_text, unit, source_language, source_occurrence)`; adjust the call if it
  differs, the mapping key is `"placeholders"` (see `LLMStringContext`, `llm.py:307`).
- Passing the same `unit` for each fragment keeps glossary and project context; the
  `unit.translated` guard above prevents the existing-translation duplication.

Wiring, sync path (`llm.py:2664-2667`):

```python
        except MachineTranslationError as error:
            halves = self._split_sources(sources, source_occurrences)
            if halves is None:
                rescued = self._translate_in_chunks(
                    source_language, target_language, sources
                )
                if rescued is not None:
                    return rescued
                raise
```

`MachineryRateLimitError` is already re-raised one `except` earlier, so the fallback
never sees a refusal; the dedicated test pins that.

**Step 4: Run tests to verify they pass**

Run: `./rundev.sh test weblate/machinery/tests.py -k "chunk_fallback or falls_back_to_chunks" -p no:randomly --no-cov -q`
Expected: PASS.

**Step 5: Run the neighbouring regression tests**

Run: `./rundev.sh test weblate/machinery/tests.py -k "continues_a_reply or does_not_split or keeps_half or every_half" -p no:randomly --no-cov -q`
Expected: PASS — halving and prefix rescue unchanged for multi-string batches.

**Step 6: Commit**

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "feat(machinery): translate a long single string in paragraph chunks when the whole-string reply is rejected"
```

---

### Task 3: The async twin

**Files:**
- Modify: `weblate/machinery/llm.py:2790-2846`
  (`_adownload_multiple_translations_with_context_cache`)
- Test: `weblate/machinery/tests.py`

**Step 1: Write the failing test**

Mirror `test_translate_falls_back_to_chunks_for_a_single_long_string` with
`AsyncMock` on `afetch_llm_translations` and `async_to_sync`, exactly like
`test_async_translate_continues_a_reply_that_ends_early` (`tests.py:5590`).

**Step 2: Run it to verify it fails**

Run: `./rundev.sh test weblate/machinery/tests.py -k async_translate_falls_back -p no:randomly --no-cov -q`
Expected: FAIL.

**Step 3: Implement `_atranslate_in_chunks`**

Same body as `_translate_in_chunks` with `await self._afetch_llm_batch(...)`; wire it
into the async `except MachineTranslationError` branch identically. The pure helpers
from Task 1 are shared.

**Step 4: Run the test to verify it passes, then the full LLM test selection**

Run: `./rundev.sh test weblate/machinery/tests.py -k "llm or LLM or chunk or OpenAI or openrouter" -p no:randomly --no-cov -q`
Expected: PASS. (Full-file run is slow; the executor may run the whole file once at
the end: `./rundev.sh test weblate/machinery/tests.py --no-cov -q`.)

**Step 5: Lint and commit**

```bash
uv run prek run ruff-check ruff-format --files weblate/machinery/llm.py weblate/machinery/tests.py
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "feat(machinery): async twin of the long-string chunk fallback"
```

---

### Task 4: Changelog and push

**Files:**
- Modify: `docs/changes.rst` (top, unreleased section)

**Steps:**
1. Add one concise entry: long single strings rejected by the LLM reply parser are
   now retried paragraph-by-paragraph and stored as one string.
2. `uv run prek run --files docs/changes.rst` (hook-scoped; never bare `--files`).
3. `git add docs/changes.rst && git commit -m "docs: changelog for the LLM long-string chunk fallback"`.
4. `git push origin main`.

---

## Rollout (NOT part of this plan's execution — needs its own explicit approval)

1. Deploy: `./deploy/vps.sh deploy` (image rebuild — `weblate/` changed).
2. Re-run auto-translate on `need-for-greed/google-play` per language with
   `q = context:gp_full state:empty`, engine `openrouter`, mode `translate`.
3. Verify the six previously empty units (`372402 372411 376663 372417 372429 372414`
   and the other store languages) are non-empty, placeholders intact, and run an LQA
   spot-check on the produced marketing copy before the producer takes it.

## Optional follow-up (separate increment, separate approval)

Tighten `RoutedLLMTranslation._reply_format` (`weblate_customization/machinery.py:264`)
so placeholder parts with `"translatable": false` must carry `"text": ""` via a
`oneOf`; `provider.require_parameters` then makes the provider reject the exact
malformed shape observed in production. Deploy requires the
`cp -r weblate_customization/src/weblate_customization dev-docker/data/python/` step
for dev and an image rebuild for prod.
