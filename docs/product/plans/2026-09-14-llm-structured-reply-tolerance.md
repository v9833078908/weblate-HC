# LLM structured reply tolerance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this
> plan task-by-task. Use test-driven-development for every task: every fixture
> below is a real production reply and must fail before the task and pass after.

**Status:** proposed, awaiting approval. Implementation is not deployment:
production rollout needs its own approval (see "Rollout").

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

A read-only replay on 2026-09-14 (`batch_translate` on the 30 currently empty
Pirate Ships strings, replies captured before parsing, nothing stored) refused 13
of 30. **All 13 replies carried a correct translation with every placeholder
token present.** They fall into four shapes, all refused by
`_normalize_structured_translation` (`llm.py:2337-2428`) before the multiset check
ever runs:

| Shape | What the model sent | Where refused |
|---|---|---|
| A. Text moved across a syntax placeholder | source `Вы заняли {0} место` → `[text "Has quedado en el puesto ", PH]`; source `…на {0}` → `[text "射撃精度が", PH, text "上昇"]` | `_has_structured_segment_text_mismatch` (2419-2427): a segment that has text in the source must have text in the reply and vice versa |
| B. Key set differs from the example | text part with `"translatable": true`, or with empty `"id": "", "kind": "", "role": "", "close_id": ""`; placeholder part with the token in `"text"` and no `"id"`/`"kind"`/`"translatable"` | 2387 (`set(actual) != {"type", "text"}`); `_normalize_structured_placeholder_part` 2173-2195 (`set(actual) != expected_keys`) |
| C. Token inline in a text part | `[text "아군 선원들에게 @@PH8@@개의 … @@PH87@@/초, @@PH103@@초 동안"]` | `_get_structured_part_text` 2158-2161 refuses any text containing a token, although prompt example `llm.py:252` shows exactly this as "Valid" |
| D. Wrapper split into open + text + close | source `<b>Выбери бойца</b>\n\n…` is one wrapper part (`id`, `close_id`, inner `text`), reply is `[PH0 (markup, close_id PH15), text "选择战士", PH15, text "\n\n…"]` | wrapper metadata comparison 2173-2195 |

Shape A alone accounts for the CJK/Thai skew: those languages routinely put the
verb after `{0}` where Russian puts it before.

Not fixed by this plan and still refused, now with a distinguishable reason
(`refusal_reason` / `reply_excerpt` on `LLMUsageLog`, commit `83bc6bb`): a token
both inline in text and as a placeholder part (duplicate; multiset check), a
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
   Unknown keys refuse, keeping `test_translate_rejects_extra_structured_metadata_reply`'s
   spirit at item level.
4. **Inline tokens in a text part are expanded**, not refused: the text is split
   on `LLM_PLACEHOLDER_RE` into alternating text and bare placeholder parts
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
   (1) expand inline tokens in text parts (decision 4); (2) canonicalise every
   part: strip tolerated text decoration (3), resolve placeholder identity (2);
   (3) re-join split wrappers (5); (4) fill missing placeholder metadata from the
   expected part with the same `id`; (5) run the existing loop unchanged except
   for decision 1. Steps 1-4 live in one new classmethod
   `_canonicalize_structured_parts(parts, expected_parts) -> list[dict] | None`
   so the existing loop keeps its shape and its tests.
7. **Refusal reasons stay greppable.** Every refusal still surfaces as
   `Mismatching assistant reply …` through `_validate_translations`; no new
   message text. `docs/product/guides/continuous-localization-loop.md:232`
   remains accurate.

## Out of scope

- A second request for a one-string batch (`_split_sources` returning `None` for
  `len < 2`). Separate small change after this plan is measured.
- Prompt wording. The models already produce the right translation; the shape
  variance is ours to absorb.
- Client-side batching in the `swift` pipeline; halving does not save the
  failing string itself.
- The judge (`weblate/trans/judge*.py`) does not parse `parts` and is untouched.

## Fixtures (verbatim production replies, 2026-09-14 replay)

Each fixture is a `(source, flags, reply)` triple. `{0}` sources use
`unit_args={"flags": "python-brace-format"}`; the `<b>` source uses the
`game-markup` harness from `ConditionalDslLLMTranslationTest`
(`check_models.CHECKS["game-markup"] = GameMarkupCheck()`), which gives `<b>`
and `</b>` a shared `group` so `_get_string_parts` collapses them into one
wrapper part. Token numbers in replies are whatever the request assigned; tests
take them from the request payload as `test_translate_rejects_structured_placeholder_moved_outside_markup`
does, not from these literals.

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
2026-09-14 replay refused it under the exact-text rule at 2211.

Negative fixtures (must still refuse):

N2 (ko, real duplicate): source `Увеличивает точность стрельбы на {0}`, reply
`[{"type":"text","text":"사격 정확도를 PH만큼 증가시킵니다"},{"type":"placeholder","id":PH,"kind":"syntax","text":"","translatable":false}]`
→ refused (`Mismatching assistant reply placeholders.`): after expansion the
token occurs twice.

N3: existing `test_translate_rejects_structured_text_moved_inside_markup`,
`test_translate_rejects_structured_placeholder_metadata_change`,
`test_translate_rejects_structured_empty_wrapper_text`,
`test_translate_rejects_reordered_structured_markup_placeholders` unchanged and
green.

## Task 1: Segment parity only at markup boundaries (shape A)

**Outcome:** F1 and F2 are stored; N3 stays refused. Any string whose only
placeholders are syntax or grammar has exactly one segment.

**Files and interfaces:** `weblate/machinery/llm.py` -
`_get_structured_expected_part_state` (2289-2323),
`_normalize_ordered_structured_placeholder_part` (2244-2286). New predicate
`_is_structured_placeholder_segment_boundary(expected: LLMPlaceholderPart) -> bool`
returning `expected["kind"] == "markup"`, used by both sites so they cannot
drift.

**Actions:**

- [ ] Write tests F1, F2 (refused today, `assertRaises(MachineTranslationError)`
      inverted to an equality on the stored text).
- [ ] Add the predicate; increment `segment` / return `current_segment + 1` only
      when it holds. A non-boundary ordered placeholder still consumes
      `expected_ordered_parts[0]` (order is still enforced).
- [ ] `segment_has_text` sizing follows the same predicate.

**Verification:**
`./rundev.sh test weblate/machinery/tests.py -k "structured or segment or reordered" -q`

- F1, F2 pass; every `rejects_structured_*` test still passes.

## Task 2: Canonicalise reply parts (shapes B, C, D)

**Outcome:** F3-F7 are stored; N2 and N3 are refused. Metadata that
contradicts the source is never accepted.

**Files and interfaces:** `weblate/machinery/llm.py` - new
`_canonicalize_structured_parts(cls, parts: list[JSONValue], expected_parts: list[LLMStringPart]) -> list[dict[str, JSONValue]] | None`,
called at the top of `_normalize_structured_translation` after the
`isinstance(parts, list)` check; its `None` is the existing `return None`.
Helpers it may use: `LLM_PLACEHOLDER_RE` (268) for expansion, the expected
placeholder parts indexed by `id` for fill-in and by `id` → `close_id` for
wrapper re-join. `_get_structured_part_text` gains no new behaviour: after
canonicalisation a placeholder part's `text` is either `""` or wrapper inner
text, and a text part never contains a token, so the existing refusals there
become unreachable for well-formed input and stay as the guard for malformed
input.

**Actions:**

- [ ] Write tests F3, F4, F5, F6, F7, N2 first.
- [ ] Expansion (decision 4): a text part whose `text` matches
      `LLM_PLACEHOLDER_RE` becomes alternating text / `{"type": "placeholder",
      "id": token}` parts; empty text pieces are dropped.
- [ ] Text decoration (decision 3): drop tolerated keys; refuse a text part with
      a non-empty `id` or an unknown key.
- [ ] Placeholder identity (decision 2): `id` if a non-empty string; else `text`
      when it is exactly one token, then `text` becomes `""`; else refuse. A part
      whose `text` equals its own `id` (N1) also becomes `text: ""`.
- [ ] Wrapper re-join (decision 5) over the canonical sequence.
- [ ] Fill-in: for each placeholder part, copy `kind`, `translatable`, `role`,
      `close_id` from the expected part with the same `id` when the key is
      absent. When no expected part has that `id`, leave the part as is - the
      existing comparison refuses it (`test_translate_rejects_structured_placeholder_metadata_change`).
      When several expected parts share an `id` (repeated placeholder), all
      carry identical metadata by construction (`_get_placeholder_part`), so the
      first suffices.
- [ ] The existing loop runs on the canonical list unchanged.

**Verification:**
`./rundev.sh test weblate/machinery/tests.py -k "structured or placeholder or wrapper or usage" -q`
then the whole file: `./rundev.sh test weblate/machinery/tests.py -q` - only the
six pre-existing `MistralCustom*`/`OpenAICustom*` DNS failures may remain
(they fail on `main` too; container has no outbound DNS).

Risky boundaries to test explicitly: wrapper whose close token never arrives
(refuse); wrapper with a placeholder part inside (refuse); expansion yielding a
token the source does not have (refused downstream by identity fill-in →
metadata comparison); text part with `"id": "@@PH1@@"` (refuse, decision 3).

## Task 3: Documentation and measurement hook

**Outcome:** The changelog states the behaviour; the plan carries a status line;
the replay probe is kept so the same 30 strings can be re-measured after
rollout.

**Files:**

- `docs/changes.rst`, top unreleased section, one bullet after the
  `refusal reason` bullet (line 39): structured LLM replies that split or
  decorate placeholder parts, inline a token in text, or move text across a
  value placeholder are now accepted when every placeholder survives intact.
- `analysis/probes/llm-structured-reply-replay.py`: the replay used for the
  evidence above, parameterised by project slug and limit, printing
  `SOURCE / ASKED / REPLY / RESULT` per string. Read-only for units; each run is
  billed. Header comment states both.

**Verification:** `uv run prek run --files docs/changes.rst analysis/probes/llm-structured-reply-replay.py`
(Ruff, rst hooks); `typos`/`reuse` failures on `analysis/data/` are
pre-existing and out of scope.

## Rollout (separate approval)

1. Deploy `main` (`deploy/vps.sh deploy --build`), apply migration `0124`
   (ledger `refusal_reason`/`reply_excerpt`), restart is part of the deploy.
2. Re-run the replay probe on `pirate-ships` with the same 30 strings: expected
   refusals ≤ 1 of 30 (N2-type duplicate only).
3. After one week, `LLMUsageLog` `outcome="refused"` grouped by
   `refusal_reason` for `operation="translation"`: the `placeholders`/`items`
   share should collapse; anything left is a new shape and gets its own
   fixture.
4. Only then decide on the single-string retry.
