# LLM structured reply tolerance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this
> plan task-by-task. Use test-driven-development for every task: every fixture
> below is a real production reply and must fail before the task and pass after.

**Status:** implemented and verified 2026-09-14 on branch
`feat/llm-structured-reply-tolerance` (isolated worktree). Tasks 1-3 complete:
F1-F7 stored, N2/N3 and the three boundary refusals still refused, changelog
and troubleshooting guide updated, non-mutating replay probe added. Verified
host-side (`source scripts/test-database.sh &&
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest
weblate/machinery/tests.py -k "structured or placeholder or wrapper or
usage" -q`, not the `./rundev.sh` container wrapper): 513 passed. Full
file: 6 pre-existing `MistralCustom*`/`OpenAICustom*` DNS failures remain
(confirmed present on unmodified `main` too; container has no outbound DNS)
plus one confirmed pre-existing order-dependent cache-pollution failure in
`MachineTranslationCleanupTest::test_rst_reference_remains_placeholder`
(passes in isolation; reproduces identically on unmodified `main`). `ruff`,
`mypy` on `weblate/machinery/llm.py`, and `prek` on every touched
non-Python file are clean. Implementation is not deployment: production
rollout needs its own approval (see "Rollout").

**Goal:** A structured LLM reply whose translation is correct - every placeholder
token present, in the right order, with the right metadata - is accepted even when
the model shapes the `parts` array differently from the prompt's example. Today
such replies are refused as `Mismatching assistant reply`, and because the caller
sends one string per request, the string stays empty.

**Architecture:** One new canonicalisation step in
`BaseLLMTranslation._normalize_structured_translation`
(`weblate/machinery/llm.py`) rewrites the model's `parts` into the canonical form
the existing validator already understands, and one relaxation removes a rule that
refuses correct translations. Every safety check that decides whether placeholders
survived is unchanged: the per-part metadata comparison, the ordered/reorderable
placement rules, the protected-text and forbidden-text guards, and the final
`_extract_placeholders` multiset comparison in `_validate_translations`. No prompt
change, no retry change, no schema change.

**Tech Stack:** Django, `weblate/machinery/llm.py`, pytest
(`weblate/machinery/tests.py`), dev container via `./rundev.sh test`.

---

## Evidence

Production `l10n.herocraft.com`, project `pirate-ships`, automatic translation
requested by the `swift` pipeline one string per request (`LLMUsageLog.batch_size
= 1` for all 2999 requests since 2026-08-20). Ledger totals for that window: 6200
translation requests, 150 `refused`, 22 `partial`; on Pirate Ships 95 refused of
2999 (gemini-3.7-flash 54, deepseek-chat-v3.1 41), skewed to ja (49), zh_Hans (17),
ko (14).

A non-unit-mutating replay on 2026-09-14 (one direct machinery request for each
of the 30 currently empty Pirate Ships strings, replies captured before parsing)
refused 13 of 30. It did not store a target or change a `Unit`; as billed
requests, its `LLMUsageLog` evidence is deliberately retained. **All 13 replies
carried a correct translation with every placeholder token present.** They fall
into four shapes, all refused by
`_normalize_structured_translation` (`llm.py:2337-2428`) before the multiset check
ever runs:

| Shape | What the model sent | Where refused |
|---|---|---|
| A. Text moved across a syntax placeholder | source `Вы заняли {0} место` → `[text "Has quedado en el puesto ", PH]`; source `…на {0}` → `[text "射撃精度が", PH, text "上昇"]` | `_has_structured_segment_text_mismatch` (2419-2427): a segment that has text in the source must have text in the reply and vice versa |
| B. Key set differs from the example | text part with `"translatable": true`, or with empty `"id": "", "kind": "", "role": "", "close_id": ""`; placeholder part with the token in `"text"` and no `"id"`/`"kind"`/`"translatable"` | a decorated text part reaches the text-part key check (2387); a token in any part's `text` is refused earlier by `_get_structured_part_text` (2158-2161) |
| C. Token inline in a text part | `[text "아군 선원들에게 @@PH8@@개의 … @@PH87@@/초, @@PH103@@초 동안"]` | `_get_structured_part_text` 2158-2161 refuses any text containing a token, although prompt example `llm.py:252` shows exactly this as "Valid" |
| D. Wrapper split into open + text + close | source `<b>Выбери бойца</b>\n\n…` is one wrapper part (`id`, `close_id`, inner `text`), reply is `[PH0 (markup, close_id PH15), text "选择战士", PH15, text "\n\n…"]` | the open and close tokens in `text` are refused by `_get_structured_part_text` (2158-2161) before wrapper metadata is compared |

Shape A alone accounts for the CJK/Thai skew: those languages routinely put the
verb after `{0}` where Russian puts it before.

Not fixed by this plan and still refused, now with a distinguishable reason
(`refusal_reason` / `reply_excerpt` on `LLMUsageLog`, commit `83bc6bb`): a token
both inline in text and as a placeholder part (duplicate; the second canonical
part has no expected part left to consume and is refused as `items`), a
translatable wrapper emptied of text, any metadata value that contradicts the
source. These are genuine contract violations.

## Decisions

1. **Segment parity is a markup rule, not a syntax rule.** The segment check
   exists to catch text moved into or out of markup
   (`test_translate_rejects_structured_text_moved_inside_markup`). A syntax
   placeholder (`{0}`, `%s`) is an inline value; text moving around it is
   translation, not corruption. Only a placeholder part of `kind == "markup"`
   starts a new segment. Grammar placeholders already do not (they are
   reorderable). The final token multiset and order checks still guard `{0}`.
2. **Canonicalise, then validate; never guess metadata that contradicts the
   source.** A missing field is filled from the expected part; a present field
   with a different value still refuses (`test_translate_rejects_structured_placeholder_metadata_change`
   stays). Identity of a placeholder part is `id` when it is a non-empty string,
   else `text` when `text` is exactly one `@@PHn@@` token. A part with neither
   is refused.
3. **Text parts tolerate decoration, not identity.** Extra keys on a text part
   are ignored only when they are `translatable` (any value) or
   `id`/`kind`/`role`/`close_id` with an empty string or `null` value. A text
   part with a non-empty `id` is refused: that is a placeholder part in disguise.
   Unknown keys refuse. `test_translate_rejects_extra_structured_metadata_reply`
   covers the analogous strictness for an extra reply item, not extra keys on a
   part; this plan adds the part-key regression.
4. **Inline tokens in a text part are expanded**, not refused: after the text
   part itself is canonicalised, its text is split on `LLM_PLACEHOLDER_RE` into
   alternating text and bare placeholder parts
   (`{"type": "placeholder", "id": token}`), which then go through the same
   metadata fill-in as any other placeholder part. This makes shapes B and C one
   mechanism. A text part still may not contain protected highlight text
   (`_has_protected_highlight_text`), unchanged.
5. **A split wrapper is re-joined before validation.** When a canonical
   placeholder part identifies as an expected wrapper's `id` and does not carry
   inner text, the following parts up to a placeholder part identifying as that
   wrapper's `close_id` are consumed: they must all be text parts, and their
   concatenation becomes the wrapper's `text`. The rebuilt part is validated by
   the existing translatable-wrapper branch (empty text with non-empty expected
   text still refuses; protected/forbidden text still refuses). No matching
   close part → refuse. A placeholder part between open and close → refuse
   (nested markup inside a collapsed wrapper never occurs, because
   `_get_string_parts` only collapses adjacent same-group tokens).
6. **Order of operations inside `_normalize_structured_translation`:**
   (1) canonicalise every raw part in place: validate its type and text, strip
   tolerated text decoration (3), resolve placeholder identity (2); (2) expand
   inline tokens in the now-clean text parts (4); (3) re-join split wrappers
   (5); (4) fill missing placeholder metadata from the expected part with the
   same `id`; (5) run the existing loop unchanged except for decision 1. Steps
   1-4 live in one new classmethod
   `_canonicalize_structured_parts(parts, expected_parts) -> list[dict] | None`
   so the existing loop keeps its shape and its tests.
7. **Refusal reasons stay greppable; troubleshooting is corrected.** Every
   refusal still surfaces as `Mismatching assistant reply …` through
   `_validate_translations`; no new message text. The current claim in
   `docs/product/guides/continuous-localization-loop.md:232` that every such
   refusal is a transient model failure is false: it must direct an operator to
   the usage row's `refusal_reason` and `reply_excerpt`, and say that re-asking
   is not a remedy for a repeatable contract violation.

## Out of scope

- A second request for a one-string batch (`_split_sources` returning `None` for
  `len < 2`). Separate small change after this plan is measured.
- Prompt wording. The models already produce the right translation; the shape
  variance is ours to absorb.
- Client-side batching in the `swift` pipeline; halving does not save the
  failing string itself.
- The judge (`weblate/trans/judge*.py`) does not parse `parts` and is untouched.

## Fixtures (verbatim production replies, 2026-09-14 replay)

Each fixture is a `(source, flags, reply)` triple. F1 and F2 must temporarily
register `GameMarkupCheck` with the saved-and-restored `CHECKS` pattern in
`ConditionalDslLLMTranslationTest`; its `check_highlight()` makes `{0}` a
`syntax` placeholder, exactly as in production. Without that registration,
`python-brace-format` makes `{0}` a `grammar` placeholder and the current parser
already accepts text moving around it
(`test_translate_accepts_split_text_around_reordered_structured_placeholder`).
F3-F5 and F7 use `unit_args={"flags": "python-brace-format"}` unless a test
also needs `GameMarkupCheck`. F6 instead uses
`unit_args={"flags": "xml-text"}`, mirroring
`test_translate_rejects_structured_empty_wrapper_text`: `XMLTagsCheck` calls
`pair_markup_highlights`, which gives `<b>` and `</b>` a shared, translatable
markup group and makes `_get_string_parts` emit one wrapper part. `GameMarkupCheck`
is not the wrapper harness: its highlights have no group. Token numbers in
replies are whatever the request assigned; callbacks capture those values from
the request payload, then assertions run *after* the request. A negative test
uses `assertRaisesMessage(MachineTranslationError, ...)`, never a bare
`assertRaises` satisfied by a callback's `AssertionError`.

F1 (A, es): source `Вы заняли {0} место`, reply
`[{"type":"text","text":"Has quedado en el puesto "},{"type":"placeholder","text":"","id":PH,"kind":"syntax","translatable":false}]`
→ expected stored `Has quedado en el puesto {0}`.

F2 (A, ja): source `Увеличивает точность стрельбы на {0}`, reply
`[{"type":"text","text":"射撃精度が"},PH,{"type":"text","text":"上昇"}]`
→ `射撃精度が{0}上昇`.

F3 (B text decoration, nl): source `+{0}/сек`, reply text parts carry
`"id":"","kind":"","role":"","close_id":"","translatable":true` → `+{0}/sec`.

F4 (B placeholder without metadata, ko): source `{0} место`, reply
`[{"type":"placeholder","text":PH,"translatable":false},{"type":"text","text":"위"}]`
→ `{0}위`.

F5 (C, ko): source `Бросает {0} бочки с лечебным зельем в союзных матросов (приоритет — раненым). Лечение: {1}/с в течение {2}с`,
reply one text part `아군 선원들에게 PH0개의 치유 물약 통을 던집니다 (우선순위: 부상자). 치유량: PH1/초, PH2초 동안`
→ same string with `{0}`, `{1}`, `{2}` restored in that order.

F6 (D, zh_Hans, from the 2026-09-12 log): source
`<b>Выбери бойца</b>\n\nБойцы имеют больше здоровья, чем Стрелки и сильны в ближнем бою`,
reply `[{"type":"placeholder","text":OPEN,"kind":"markup","close_id":CLOSE},{"type":"text","text":"选择战士"},{"type":"placeholder","text":CLOSE},{"type":"text","text":"\n\n战士比射手拥有更多生命值，且擅长近战"}]`
→ `<b>选择战士</b>\n\n战士比射手拥有更多生命值，且擅长近战`.

F7 (B, token echoed in both `text` and `id`, ko): source
`{0} возродился и снова готов выполнять приказы.`, reply
`[{"type":"placeholder","text":PH,"id":PH,"kind":"syntax","translatable":false},{"type":"text","text":"가 부활하여 다시 명령을 수행할 준비가 되었습니다."}]`
→ `{0}가 부활하여 다시 명령을 수행할 준비가 되었습니다.`. A non-wrapper part
whose `text` is exactly its own `id` is treated as `text: ""` (decision 2); the
2026-09-14 replay refused it at `_get_structured_part_text` before the
exact-text comparison.

Negative fixtures (must still refuse):

N2 (ko, real duplicate): source `Увеличивает точность стрельбы на {0}`, reply
`[{"type":"text","text":"사격 정확도를 PH만큼 증가시킵니다"},{"type":"placeholder","id":PH,"kind":"syntax","text":"","translatable":false}]`
→ refused with `Mismatching assistant reply items.`: expansion produces two
placeholder parts; after the first consumes the only expected part, the second
has no ordered or reorderable expected part left to consume.

N3: repair `test_translate_rejects_structured_text_moved_inside_markup` before
using it as the markup boundary regression. The source produces **one** collapsed
wrapper part, not two. Its callback returns that wrapper with
`"text": "Enregistrer le bouton"` followed by `{"type": "text", "text": ""}`;
the test asserts the exact `Mismatching assistant reply items.` message. Existing
metadata-change, empty-wrapper, and reordered-markup tests remain green, but
their callbacks are not the proof for N3.

## Task 1: Segment parity only at markup boundaries (shape A)

**Outcome:** F1 and F2 are stored; N3 stays refused. Any string whose only
placeholders are syntax or grammar has exactly one segment.

**Files and interfaces:** `weblate/machinery/llm.py` -
`_get_structured_expected_part_state` (2289-2323),
`_normalize_ordered_structured_placeholder_part` (2244-2286), and
`weblate/machinery/tests.py` -
`OpenAITranslationTest.test_translate_rejects_structured_text_moved_inside_markup`
plus `ConditionalDslLLMTranslationTest`'s temporary `GameMarkupCheck`
registration pattern. New predicate
`_is_structured_placeholder_segment_boundary(expected: LLMPlaceholderPart) -> bool`
returning `expected["kind"] == "markup"`, used by both parser sites so they
cannot drift.

**Actions:**

- [x] Repair N3 before changing the parser: replace the callback's impossible
      two-placeholder assertion with the one-wrapper reply described above and
      assert `Mismatching assistant reply items.` with `assertRaisesMessage`.
      Never make a callback assertion the condition that demonstrates refusal:
      `BaseMachineTranslation._handle_download_error` wraps it as a
      `MachineTranslationError`.
- [x] Write F1 and F2 in the game-markup registration scope before changing the
      parser. They must raise `MachineTranslationError` on current code; after
      Task 1 they assert the exact stored translation. This is the only test
      setup that exercises the production `syntax` classification of `{0}`.
- [x] Add the predicate; increment `segment` / return `current_segment + 1` only
      when it holds. A non-boundary ordered placeholder still consumes
      `expected_ordered_parts[0]` (order is still enforced).
- [x] `segment_has_text` sizing follows the same predicate.

**Verification:**
`./rundev.sh test weblate/machinery/tests.py -k "structured or segment or reordered" -q`

- F1, F2 pass; every `rejects_structured_*` test still passes.

## Task 2: Canonicalise reply parts (shapes B, C, D)

**Outcome:** F3-F7 are stored; N2 and N3 are refused. Metadata that
contradicts the source is never accepted.

**Files and interfaces:** `weblate/machinery/llm.py` - new
`_canonicalize_structured_parts(cls, parts: list[JSONValue], expected_parts: list[LLMStringPart]) -> list[dict[str, JSONValue]] | None`,
called after `_get_string_parts` builds `expected_parts` and before the existing
normalisation loop; its `None` is the existing `return None`. Its input boundary
is strict: every part must be a dictionary with a string `type` (`"text"` or
`"placeholder"`) and a string `text`; no omitted or non-string field is repaired.
Helpers it may use: `LLM_PLACEHOLDER_RE` (268) for expansion, the expected
placeholder parts indexed by `id` for fill-in and by `id` → `close_id` for
wrapper re-join. `_get_structured_part_text` gains no new behaviour: after
canonicalisation a placeholder part's `text` is either `""` or wrapper inner
text, and a text part never contains a token, so the existing refusals there
become unreachable for well-formed input and stay as the guard for malformed
input.

**Actions:**

- [x] Write tests F3, F4, F5, F6, F7, N2 first, plus the three boundary
      refusals below (missing wrapper close, nested placeholder in split
      wrapper, non-empty text-part `id`). Each negative assertion matches
      `Mismatching assistant reply items.`; callbacks only return their reply,
      keeping request observations for assertions after the call.
- [x] Text decoration (decision 3): validate and then drop tolerated keys;
      refuse a text part with a non-empty `id` or an unknown key.
- [x] Placeholder identity (decision 2): `id` if a non-empty string; else `text`
      when it is exactly one token, then `text` becomes `""`; else refuse. A part
      whose `text` equals its own `id` (F7) also becomes `text: ""`.
- [x] Expansion (decision 4): a now-clean text part whose `text` **contains**
      one or more exact `LLM_PLACEHOLDER_RE` matches becomes alternating text /
      `{"type": "placeholder", "id": token}` parts; empty text pieces are
      dropped.
- [x] Wrapper re-join (decision 5) over the canonical sequence.
- [x] Fill-in: for each placeholder part, copy `kind`, `translatable`, `role`,
      `close_id` from the expected part with the same `id` when the key is
      absent. When no expected part has that `id`, leave the part as is - the
      existing comparison refuses it (`test_translate_rejects_structured_placeholder_metadata_change`).
      When several expected parts share an `id` (repeated placeholder), all
      carry identical metadata by construction (`_get_placeholder_part`), so the
      first suffices.
- [x] The existing loop runs on the canonical list unchanged.

**Verification:**
`./rundev.sh test weblate/machinery/tests.py -k "structured or placeholder or wrapper or usage" -q`
then the whole file: `./rundev.sh test weblate/machinery/tests.py -q` - only the
six pre-existing `MistralCustom*`/`OpenAICustom*` DNS failures may remain
(they fail on `main` too; container has no outbound DNS).

Risky boundaries to test explicitly: wrapper whose close token never arrives
(refuse); wrapper with a placeholder part inside (refuse); expansion yielding a
token the source does not have (refused downstream by identity fill-in →
metadata comparison); text part with `"id": "@@PH1@@"` (refuse, decision 3).

## Task 3: Documentation and observational probe

**Outcome:** The changelog and troubleshooting guide state the new behaviour
truthfully. A paid, non-unit-mutating probe samples current replies with enough
attribution to investigate residual shapes; static F1-F7 tests, not a second
model call, prove acceptance of the captured 2026-09-14 replies.

**Files:**

- `docs/changes.rst`, top unreleased section, one bullet after the
  `refusal reason` bullet (line 39): structured LLM replies that split or
  decorate placeholder parts, inline a token in text, or move text across a
  value placeholder are now accepted when every placeholder survives intact.
- `docs/product/guides/continuous-localization-loop.md`, troubleshooting row
  232: distinguish a JSON parse failure (retry may help) from
  `Mismatching assistant reply` (inspect `LLMUsageLog.refusal_reason` and the
  bounded reply excerpt; retry is not a remedy for a repeatable contract
  violation).
- `analysis/probes/llm-structured-reply-replay.py`: a GPL-3.0-or-later Python
  probe with the repository copyright/SPDX header. It makes one direct
  `download_multiple_translations` request per sampled unit - never
  `batch_translate`, so it never updates `Unit.machinery` or quota accounting -
  and is parameterised by project slug and limit. It prints the unit ID, source,
  asked parts, raw reply, outcome and refusal reason for each fresh request.
  Its header says that it does not write units but deliberately writes a billed
  `LLMUsageLog` audit row. It does not claim to replay the old response bodies.

**Verification:** `uv run prek run --files docs/changes.rst docs/product/guides/continuous-localization-loop.md analysis/probes/llm-structured-reply-replay.py`
(Ruff, rst hooks); `typos`/`reuse` failures on `analysis/data/` are
pre-existing and out of scope.

## Rollout (separate approval)

1. Deploy `main` (`deploy/vps.sh deploy --build`). The image entrypoint runs
   migrations on startup; verify that `trans.0124_llm_usage_refusal_evidence`
   is applied rather than attempting a separate manual migration step.
2. Run the paid sampler on `pirate-ships`; retain its unit IDs and raw replies
   with the run evidence. It is an observation of new stochastic model output,
   not proof that the 13 captured replies are accepted.
3. After one week, group `LLMUsageLog` rows with `operation="translation"` and
   `outcome="refused"` by `refusal_reason`; compare their count and share with
   the 2026-09-14 baseline. Every residual `Mismatching assistant reply` gets a
   saved excerpt and becomes a new static fixture before any further parser
   relaxation.
4. Only then decide on the single-string retry.
