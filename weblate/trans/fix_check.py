# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Server-side engine for the mass-fix-failing-checks feature.

Reuses the existing `Check.get_fixup()` contract (`weblate/checks/base.py`):
`apply_fixup_python` is the Python-side equivalent of the JS single-unit
"Fix string" button (`weblate/static/editor/full.js`), so preview and apply
share the exact same computed text as the live editor.

See `docs/product/plans/2026-08-25-mass-fix-failing-checks.md`, Task 2, and
`docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md`, Tasks
A-B.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import partial
from typing import (  # pylint: disable=unused-import
    TYPE_CHECKING,
    Any,
    Literal,
    cast,
)

from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.db.models import OuterRef, Prefetch, Subquery
from django.utils.translation import gettext_lazy
from redis.lock import Lock as RedisLock

from weblate.checks.chars import terminal_source_edit
from weblate.checks.models import CHECKS
from weblate.checks.utils import highlight_string
from weblate.trans.actions import ActionEvents
from weblate.trans.autofixes import AUTOFIXES, fix_target
from weblate.trans.file_format_params import DOSLineEndings
from weblate.trans.models import Component, Unit
from weblate.trans.models.unit import NEWLINES
from weblate.trans.util import join_plural
from weblate.utils.cache import is_redis_cache

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from django_redis.cache import RedisCache
    from django_stubs_ext import StrOrPromise

    from weblate.auth.models import User
    from weblate.checks.base import BaseCheck, FixupType
    from weblate.checks.chars import TerminalEdit
    from weblate.trans.models import Project
    from weblate.trans.models.unit import UnitQuerySet

# The before/after set decision-7 enforces (docs/product/plans/
# 2026-08-25-mass-fix-failing-checks.md, "Decisions added on revision"
# item 7): every terminal check plus `punctuation_spacing`. Mirrors the
# shape of `_failing()` over `TERMINAL_CHECKS`
# (`weblate_customization/src/weblate_customization/autofixes.py:112-141`),
# extended by `end_interrobang`, which that autofix does not cover.
TERMINAL_CHECK_IDS: tuple[str, ...] = (
    "end_stop",
    "end_colon",
    "end_question",
    "end_exclamation",
    "end_interrobang",
)
PUNCTUATION_SPACING_CHECK_ID = "punctuation_spacing"


_JS_REPLACEMENT_TOKEN_RE = re.compile(r"\$(\$|&|\d{1,2})")


def _translate_js_replacement(replacement: str) -> str:
    r"""
    Translate a JS `String.replace()` pattern into Python `re.sub()` syntax.

    `$1`..`$99` -> `\1`..`\99` (a *capture group* reference -
    `PunctuationSpacingCheck.get_fixup`'s French-spacing fixups are the
    only tiered checks that use one), `$&` -> `\g<0>` (the whole match),
    `$$` -> a literal `$`. Python's replacement string gives backslash no
    meaning of its own outside a group escape, so a literal backslash in
    `replacement` (none of the current fixups emit one, but a future one
    might) is itself escaped first, rather than risking it forming an
    accidental group reference.
    """

    def translate_token(match: re.Match[str]) -> str:
        char = match.group(1)
        if char == "$":
            return "$"
        if char == "&":
            return "\\g<0>"
        return f"\\{char}"

    escaped = replacement.replace("\\", "\\\\")
    return _JS_REPLACEMENT_TOKEN_RE.sub(translate_token, escaped)


def apply_fixup_python(
    fixups: Iterable[FixupType] | None, texts: list[str]
) -> list[str]:
    """
    Apply a `get_fixup()` result the way the editor's `full.js` does.

    Accepts only the `regex` variant, `("regex", pattern, replacement,
    flags)`; raises on `("plurals", …)`, which no tiered check emits.
    Translates JS flags: `g` -> `count=0` (replace every occurrence),
    otherwise `count=1` (replace only the first); `i` -> `re.IGNORECASE`;
    `u` has no Python equivalent to translate (Python `str` patterns are
    already Unicode-aware) and is ignored. Any other flag raises rather
    than being silently dropped: a flag this function cannot translate
    would make the server-side result diverge from what the editor's
    `new RegExp(pattern, flags)` computes. `replacement` is translated from
    JavaScript to Python group-reference syntax - see
    `_translate_js_replacement`. Applies every fixup, in order, to every
    plural form.
    """
    if not fixups:
        return list(texts)
    result = list(texts)
    for fixup in fixups:
        kind = fixup[0]
        if kind != "regex":
            msg = f"apply_fixup_python only supports the 'regex' fixup variant, got {kind!r}"
            raise ValueError(msg)
        regex_fixup = cast("tuple[Literal['regex'], str, str, str]", fixup)
        _, pattern, replacement, flags = regex_fixup
        unknown_flags = set(flags) - {"g", "i", "u"}
        if unknown_flags:
            msg = (
                "apply_fixup_python cannot translate the JavaScript regex "
                f"flags {''.join(sorted(unknown_flags))!r}"
            )
            raise ValueError(msg)
        count = 0 if "g" in flags else 1
        re_flags = re.IGNORECASE if "i" in flags else 0
        python_replacement = _translate_js_replacement(replacement)
        result = [
            re.sub(pattern, python_replacement, text, count=count, flags=re_flags)
            for text in result
        ]
    return result


def _finish_final_target(
    unit: Unit, old_targets: list[str], new_targets: list[str]
) -> list[str] | None:
    """
    Shared tail of the preparation pipeline once a candidate `new_targets` is computed.

    However it was computed - a `get_fixup()`/`terminal_source_edit()`
    regex fixup through `apply_fixup_python` (`_prepare_final_target`), or
    an `AutoFix.fix_target()` direct text transform (Task B's mechanical
    groups, which are not check fixups at all). Runs every preparation
    step `Unit.translate()` runs around `fix_target()`
    (`weblate/trans/models/unit.py:2412-2436`): multivalue empty-entry
    filtering (or plural-count adjustment otherwise), the fixup's own
    autofix normalization for a non-template translation, and DOS
    line-ending conversion. Returns `None` when the result is no change
    from the unit's current target.
    """
    component = unit.translation.component
    if component.is_multivalue:
        new_targets = [target for target in new_targets if target]
        if not new_targets:
            new_targets = [""]
    else:
        new_targets = unit.adjust_plurals(list(new_targets))

    if not unit.translation.is_template:
        new_targets, _applied = fix_target(new_targets, unit)

    if DOSLineEndings.get_value(component.file_format_params):
        new_targets = [NEWLINES.sub("\r\n", target) for target in new_targets]

    if new_targets == old_targets:
        return None
    return new_targets


def _prepare_final_target(
    fixups: Iterable[FixupType] | None, unit: Unit
) -> list[str] | None:
    """
    Apply `fixups` and run the shared preparation pipeline.

    `fixups` is a `get_fixup()`/`terminal_source_edit()` regex list; see
    `_finish_final_target`. Returns `None` when there are no fixups or
    they produce no change.

    Shared by `_compute_final_target` (`get_fixup()`-based checks, Task 2)
    and the terminal-source policy's `_classify_terminal_policy`
    (`terminal_source_edit()`-based, Task A), which computes its fixup
    outside `get_fixup()` (`weblate/checks/chars.py`).
    """
    if not fixups:
        return None
    old_targets = unit.get_target_plurals()
    new_targets = apply_fixup_python(fixups, old_targets)
    return _finish_final_target(unit, old_targets, new_targets)


def _compute_final_target(check_obj: BaseCheck, unit: Unit) -> list[str] | None:
    """Compute the final stored target for one `get_fixup()`-based candidate (Task 2 step 1)."""
    return _prepare_final_target(check_obj.get_fixup(unit), unit)


def _failing_checks(
    check_ids: Iterable[str], unit: Unit, sources: list[str], targets: list[str]
) -> frozenset[str]:
    """Return the subset of `check_ids` that fail for `unit` against `targets`."""
    unit.invalidate_checks_cache()
    failing = set()
    for check_id in check_ids:
        check_obj = CHECKS.get(check_id)
        if check_obj is not None and check_obj.check_target(sources, targets, unit):
            failing.add(check_id)
    return frozenset(failing)


def _decision_7_holds(
    check_obj: BaseCheck,
    unit: Unit,
    sources: list[str],
    old_targets: list[str],
    new_targets: list[str],
) -> bool:
    """
    Enforce the decision-7 before/after contract (Task 2 step 3).

    For a source-side check (`check_obj.source`, e.g. `ellipsis`), target
    checks never run against a source-translation unit
    (`Unit.run_checks()`), so the terminal-set/`punctuation_spacing`
    constraint is vacuous - the only requirement is that the selected check
    itself clears.
    """
    if check_obj.source:
        unit.invalidate_checks_cache()
        return not check_obj.check_source(new_targets, unit)

    before = _failing_checks(
        (*TERMINAL_CHECK_IDS, PUNCTUATION_SPACING_CHECK_ID), unit, sources, old_targets
    )
    after = _failing_checks(
        (*TERMINAL_CHECK_IDS, PUNCTUATION_SPACING_CHECK_ID), unit, sources, new_targets
    )
    unit.invalidate_checks_cache()
    if check_obj.check_target(sources, new_targets, unit):
        # The selected check_id is still in the "after" set.
        return False
    before_terminal = before - {PUNCTUATION_SPACING_CHECK_ID}
    after_terminal = after - {PUNCTUATION_SPACING_CHECK_ID}
    if not after_terminal <= before_terminal:
        # A terminal check newly failing that did not fail before.
        return False
    return not (
        PUNCTUATION_SPACING_CHECK_ID in after
        and PUNCTUATION_SPACING_CHECK_ID not in before
    )


Bucket = str  # "eligible" | "manual" | "no_fixup" | "denied"


def _classify(
    user: User | None, check_obj: BaseCheck, unit: Unit
) -> tuple[Bucket, list[str] | None]:
    """
    Bucket one candidate unit (Task 2 step 3).

    `denied`: no ordinary `unit.edit` permission - for a source template
    this already enforces `unit.template` internally
    (`weblate/auth/permissions.py:552-557`); `source.edit` is deliberately
    never substituted, as it protects a different, metadata-only branch of
    `bulk_perform` (`weblate/trans/bulk.py:150-153`), not a
    `Unit.translate()` target edit.
    `no_fixup`: the check offers this string no fixup at all. For the
    terminal checks that means the failure is the opposite direction -
    the translation carries a mark the source does not have - which the
    autofix layer owns (`RemoveAddedFinalStop`), not this feature. It is
    reported separately because it is the one manual reason a producer
    cannot act on from here, and it counts into `manual` as well.
    `manual`: no final target change, an uncleared check, a conflicting
    terminal mark, or a newly introduced terminal/`punctuation_spacing`
    failure.
    `eligible`: a permitted final target that satisfies decision 7 in full.
    """
    if user is not None and not user.has_perm("unit.edit", unit):
        return "denied", None
    if not check_obj.get_fixup(unit):
        return "no_fixup", None
    new_targets = _compute_final_target(check_obj, unit)
    if new_targets is None:
        return "manual", None
    sources = unit.get_source_plurals()
    old_targets = unit.get_target_plurals()
    if not _decision_7_holds(check_obj, unit, sources, old_targets, new_targets):
        return "manual", None
    return "eligible", new_targets


def _newest_verdict_prefetch() -> Prefetch:
    """
    Prefetch each unit's newest verdict, and only that one.

    `_verdict_would_go_stale` needs the newest verdict per candidate.
    Fetching it per unit is one query per eligible unit, which Task 2
    step 2 forbids on a project scope; prefetching the whole
    `judge_verdicts` relation would instead pull every historical verdict
    of every candidate into memory. The correlated `pk` subquery keeps it
    at one row per unit per prefetch chunk.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import JudgeVerdict

    newest = (
        JudgeVerdict.objects.filter(unit_id=OuterRef("unit_id"))
        .order_by("-timestamp")
        .values("pk")[:1]
    )
    return Prefetch(
        "judge_verdicts",
        queryset=JudgeVerdict.objects.filter(pk=Subquery(newest)).order_by(
            "-timestamp"
        ),
    )


def _verdict_would_go_stale(unit: Unit, new_targets: list[str]) -> bool:
    """
    Report whether the fix would make the unit's newest verdict stale.

    True when the unit's newest judge verdict is current for the present
    text and will stop being current after the fix (Task 2 step 4,
    decision 9). No re-check is queued here - this is a reported count
    only. Reads `judge_verdicts` through the newest-first prefetch above,
    so a prefetched candidate costs no extra query.
    """
    latest = next(iter(unit.judge_verdicts.all()), None)
    if latest is None:
        return False
    if latest.is_stale(unit.get_target_plurals()):
        return False
    return latest.is_stale(new_targets)


@dataclass
class FixPreviewRow:
    """One review-tier preview row: an eligible unit and its computed fix."""

    unit: Unit
    final_target: list[str]
    # Which terminal-source operation produced this row - "append" /
    # "replace" / "remove" - or `None` for the per-check flow, which has
    # no operation concept (Task A).
    operation: str | None = None

    @property
    def final_target_value(self) -> str:
        """`final_target` joined for `format_unit_target`'s `value=`."""
        return join_plural(self.final_target)


@dataclass
class FixCandidates:
    """Result of `collect_fix_candidates` (Task 2 step 5)."""

    shown: list[FixPreviewRow] = field(default_factory=list)
    total_eligible: int = 0
    manual: int = 0
    # Subset of `manual`: the check offers those strings no fixup at all
    # (for a terminal check, the failure is the direction the autofix
    # layer owns), so no further pass from this screen can ever fix them.
    manual_no_fixup: int = 0
    denied: int = 0
    verdicts_no_longer_current: int = 0

    @property
    def remaining(self) -> int:
        return self.total_eligible - len(self.shown)


def _matching_units(
    unit_set: UnitQuerySet, project: Project | None, check_obj: BaseCheck
) -> UnitQuerySet:
    """
    Return one check's active, non-dismissed rows in the given scope.

    Glossary components are excluded: `run_checks()` runs only
    `CHECKS.glossary` there (`weblate/trans/models/unit.py:2181-2183`), so
    a projected `Check` row and a check recomputed here would disagree.
    """
    return unit_set.search(f"check:={check_obj.check_id}", project=project).exclude(
        translation__component__is_glossary=True
    )


def collect_fix_candidates(
    user: User | None,
    unit_set: UnitQuerySet,
    project: Project | None,
    check_obj: BaseCheck,
    *,
    preview_limit: int = 250,
) -> FixCandidates:
    """
    Bucket every currently active row for `check_obj` (Task 2 steps 2-5).

    `shown` carries the first `preview_limit` **eligible** rows, in stable
    `(component, translation, position, id)` order, with their computed
    final target - the review-tier preview. Once those rows are fixed they
    no longer match `check:=<id>`, so reloading the same review
    deterministically advances to the next eligible cohort. The safe tier
    calls this with the scope's full query and never renders `.shown`'s
    text, only the aggregate counts.
    """
    # Only the newest-verdict prefetch is added here: the caller's
    # `unit_set` already carries the scope's own relation prefetches
    # (`weblate/utils/views.py:parse_path_units`), and re-applying
    # `UnitQuerySet.prefetch()` on top of them raises "lookup was already
    # seen with a different queryset".
    matching = (
        _matching_units(unit_set, project, check_obj)
        .prefetch_related(_newest_verdict_prefetch())
        .order_by("translation__component_id", "translation_id", "position", "id")
    )
    result = FixCandidates()
    for unit in matching.iterator(chunk_size=200):
        bucket, new_targets = _classify(user, check_obj, unit)
        if bucket == "denied":
            result.denied += 1
        elif bucket in {"manual", "no_fixup"}:
            result.manual += 1
            if bucket == "no_fixup":
                result.manual_no_fixup += 1
        else:
            result.total_eligible += 1
            if new_targets is not None and _verdict_would_go_stale(unit, new_targets):
                result.verdicts_no_longer_current += 1
            if len(result.shown) < preview_limit and new_targets is not None:
                result.shown.append(FixPreviewRow(unit=unit, final_target=new_targets))
    return result


@dataclass
class FixResult:
    """Result of `perform_fix` (Task 2 step 6)."""

    fixed: int = 0
    denied: int = 0
    manual: int = 0
    stale_or_no_change: int = 0
    verdicts_no_longer_current: int = 0
    aborted: bool = False


def _perform_fix_over(
    user: User | None,
    matching: UnitQuerySet,
    unit_ids: Iterable[int] | None,
    classify: Callable[[User | None, Unit], tuple[Bucket, list[str] | None]],
    *,
    progress_callback: Callable[[int, int], bool] | None,
    progress_every: int,
) -> FixResult:
    """
    Shared batched write loop behind `perform_fix`/`perform_terminal_policy_fix`.

    Task 2 step 6 / Task A. `matching` is the scope's already-filtered
    candidate query - one check's rows via `_matching_units`, or the
    union of four via `_terminal_policy_matching_units`. `unit_ids=None`
    means the full current scope (tier `safe`); an explicit id list means
    only the checked review rows. Explicit ids are intersected with the
    live active-check query first, so an id that no longer matches
    (already fixed by another run, dismissed, or otherwise changed since
    the preview) is counted as `stale_or_no_change`, never submitted.
    `classify` re-derives each unit's bucket and final target fresh,
    under its row lock, exactly as the caller's own preview
    classification does; a manual or unresolved row is never submitted
    either.
    """
    requested_ids: set[int] | None = None
    if unit_ids is not None:
        requested_ids = set(unit_ids)
        matching = matching.filter(id__in=requested_ids)

    id_component_pairs = list(
        matching.order_by(
            "translation__component_id", "translation_id", "position", "id"
        ).values_list("id", "translation__component_id")
    )

    by_component: dict[int, list[int]] = {}
    ordered_component_ids: list[int] = []
    for unit_id, component_id in id_component_pairs:
        if component_id not in by_component:
            by_component[component_id] = []
            ordered_component_ids.append(component_id)
        by_component[component_id].append(unit_id)

    result = FixResult()
    total = len(id_component_pairs)
    processed = 0
    matched_ids: set[int] = set()

    for component_id in ordered_component_ids:
        if result.aborted:
            break
        ids = by_component[component_id]
        component = Component.objects.get(pk=component_id)
        component.start_batched_checks()
        with transaction.atomic():
            for unit in (
                Unit.objects.filter(pk__in=ids)
                .prefetch()
                .prefetch_related(_newest_verdict_prefetch())
                .select_for_update()
            ):
                matched_ids.add(unit.pk)
                # Share this call's single Component instance so every
                # unit's batched-check bookkeeping
                # (`component.updated_sources`/`batched_checks`) lands on
                # the same object `run_batched_checks()` reads below,
                # mirroring the existing propagation precedent at
                # `weblate/trans/models/unit.py:2314`.
                unit.translation.component = component
                bucket, new_targets = classify(user, unit)
                if bucket == "denied":
                    result.denied += 1
                    continue
                if bucket in {"manual", "no_fixup"}:
                    result.manual += 1
                    continue
                if new_targets is None:  # pragma: no cover - defensive
                    result.manual += 1
                    continue
                if _verdict_would_go_stale(unit, new_targets):
                    result.verdicts_no_longer_current += 1
                saved = unit.translate(
                    user,
                    new_targets,
                    unit.state,
                    change_action=ActionEvents.FIX_FAILING_CHECK,
                    propagate=False,
                    select_for_update=False,
                    mark_source_change_fuzzy=False,
                )
                if saved:
                    result.fixed += 1
                else:
                    result.stale_or_no_change += 1
                processed += 1
                tick = processed % progress_every == 0 or processed == total
                if (
                    progress_callback is not None
                    and tick
                    and not progress_callback(processed, total)
                ):
                    result.aborted = True
                    break
        component.invalidate_cache()
        component.run_batched_checks()

    if requested_ids is not None:
        result.stale_or_no_change += len(requested_ids - matched_ids)

    return result


def perform_fix(
    user: User | None,
    unit_set: UnitQuerySet,
    project: Project | None,
    check_obj: BaseCheck,
    unit_ids: Iterable[int] | None = None,
    *,
    progress_callback: Callable[[int, int], bool] | None = None,
    progress_every: int = 20,
) -> FixResult:
    """
    Recompute and write every fresh eligible candidate (Task 2 step 6).

    `unit_ids=None` means the full current scope (tier `safe`); an explicit
    id list means only the checked review rows. See `_perform_fix_over` for
    the shared write-loop contract.
    """
    matching = _matching_units(unit_set, project, check_obj)
    return _perform_fix_over(
        user,
        matching,
        unit_ids,
        lambda u, unit: _classify(u, check_obj, unit),
        progress_callback=progress_callback,
        progress_every=progress_every,
    )


# --- Terminal-source policy (Task A) ------------------------------------
#
# One append/replace/remove policy spanning four terminal checks at once
# (`weblate/checks/chars.py`'s `terminal_source_edit`), so a unit failing
# two of them simultaneously - source "." / target "!" fails both
# `end_stop` and `end_exclamation` - is classified, shown and fixed exactly
# once, never double-counted
# (docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md, Task
# A). `end_interrobang` stays its own, separately reviewed check, entirely
# outside this policy.
TERMINAL_SOURCE_POLICY_ID = "terminal-source"
TERMINAL_SOURCE_POLICY_CHECK_IDS: tuple[str, ...] = (
    "end_stop",
    "end_colon",
    "end_question",
    "end_exclamation",
)


def _terminal_policy_edit(unit: Unit) -> tuple[str, TerminalEdit] | None:
    """
    Try each policy check in turn; at most one proposes an edit.

    By construction: `terminal_source_edit` only ever fires for the check
    whose family matches the source's own terminal mark (or, for a
    removal, the mark the target itself carries).
    """
    for check_id in TERMINAL_SOURCE_POLICY_CHECK_IDS:
        edit = terminal_source_edit(CHECKS[check_id], unit)
        if edit is not None:
            return check_id, edit
    return None


def _classify_terminal_policy(
    user: User | None, unit: Unit
) -> tuple[Bucket, list[str] | None]:
    """
    Bucket one candidate unit for the terminal-source policy (Task A).

    Mirrors `_classify`, but resolves the owning check from the unit
    itself instead of taking one as a parameter, and reuses
    `_decision_7_holds` with that resolved check as the veto - the same
    before/after terminal-set/`punctuation_spacing` invariant, just fed a
    target `terminal_source_edit` computed instead of one `get_fixup()`
    computed.
    """
    if user is not None and not user.has_perm("unit.edit", unit):
        return "denied", None
    owner = _terminal_policy_edit(unit)
    if owner is None:
        return "no_fixup", None
    check_id, edit = owner
    new_targets = _prepare_final_target([edit.fixup], unit)
    if new_targets is None:
        return "manual", None
    sources = unit.get_source_plurals()
    old_targets = unit.get_target_plurals()
    if not _decision_7_holds(CHECKS[check_id], unit, sources, old_targets, new_targets):
        return "manual", None
    return "eligible", new_targets


def _union_matching_units(
    unit_set: UnitQuerySet, project: Project | None, check_ids: Iterable[str]
) -> UnitQuerySet:
    """
    Union of active, non-dismissed rows for any of `check_ids` (Task A/B).

    Shared by the terminal-source policy (four checks) and every Task B
    mechanical group that spans more than one check (edge-space's
    `begin_space`/`end_space`); a single-check group reuses
    `_matching_units` instead, which additionally accepts a real
    `check_obj`.
    """
    query = " OR ".join(f"check:={check_id}" for check_id in check_ids)
    return unit_set.search(query, project=project).exclude(
        translation__component__is_glossary=True
    )


def _terminal_policy_matching_units(
    unit_set: UnitQuerySet, project: Project | None
) -> UnitQuerySet:
    """Union of active, non-dismissed rows for any policy check (Task A)."""
    return _union_matching_units(unit_set, project, TERMINAL_SOURCE_POLICY_CHECK_IDS)


@dataclass
class TerminalPolicyCandidates:
    """Result of `collect_terminal_policy_candidates` (Task A)."""

    shown: list[FixPreviewRow] = field(default_factory=list)
    total_eligible: int = 0
    by_operation: dict[str, int] = field(
        default_factory=lambda: {"append": 0, "replace": 0, "remove": 0}
    )
    manual: int = 0
    manual_no_fixup: int = 0
    denied: int = 0
    verdicts_no_longer_current: int = 0

    @property
    def remaining(self) -> int:
        return self.total_eligible - len(self.shown)


def collect_terminal_policy_candidates(
    user: User | None,
    unit_set: UnitQuerySet,
    project: Project | None,
    *,
    preview_limit: int = 250,
) -> TerminalPolicyCandidates:
    """
    Bucket every currently active terminal-policy row (Task A).

    Spans all four policy checks in one pass - see the module comment
    above `TERMINAL_SOURCE_POLICY_ID`.
    """
    matching = (
        _terminal_policy_matching_units(unit_set, project)
        .prefetch_related(_newest_verdict_prefetch())
        .order_by("translation__component_id", "translation_id", "position", "id")
    )
    result = TerminalPolicyCandidates()
    for unit in matching.iterator(chunk_size=200):
        bucket, new_targets = _classify_terminal_policy(user, unit)
        if bucket == "denied":
            result.denied += 1
        elif bucket in {"manual", "no_fixup"}:
            result.manual += 1
            if bucket == "no_fixup":
                result.manual_no_fixup += 1
        else:
            result.total_eligible += 1
            owner = _terminal_policy_edit(unit)
            operation = owner[1].operation if owner is not None else None
            if operation is not None:
                result.by_operation[operation] += 1
            if new_targets is not None and _verdict_would_go_stale(unit, new_targets):
                result.verdicts_no_longer_current += 1
            if len(result.shown) < preview_limit and new_targets is not None:
                result.shown.append(
                    FixPreviewRow(
                        unit=unit, final_target=new_targets, operation=operation
                    )
                )
    return result


def perform_terminal_policy_fix(
    user: User | None,
    unit_set: UnitQuerySet,
    project: Project | None,
    unit_ids: Iterable[int] | None = None,
    *,
    progress_callback: Callable[[int, int], bool] | None = None,
    progress_every: int = 20,
) -> FixResult:
    """Terminal-source policy equivalent of `perform_fix` (Task A)."""
    matching = _terminal_policy_matching_units(unit_set, project)
    return _perform_fix_over(
        user,
        matching,
        unit_ids,
        _classify_terminal_policy,
        progress_callback=progress_callback,
        progress_every=progress_every,
    )


# --- Mechanical groups (Task B) ------------------------------------------
#
# Six deterministic, non-terminal repairs, each its own named group rather
# than "run every autofix": double spaces, the two edge-space split
# policies, `$` line-separator spacing, French punctuation spacing, the
# trailing-ellipsis form, and a stray zero-width space
# (docs/product/plans/2026-09-09-producer-bulk-punctuation-repair.md, Task
# B). Every group is independently verified: the check(s) it targets must
# stop failing, and - unlike `DoubleSpaceCheck.get_fixup` and friends on
# their own - no `highlight_string`-protected span's own text may change
# either (`_protected_spans_preserved`). `game-number`, `game-token`,
# `game-markup`, `game-length`, `cyrillic-leak`, `duplicate`, `reused`,
# `multiple_capital` and similar semantic/structural checks are
# deliberately absent: no group here ever claims them.


def _protected_spans_preserved(
    unit: Unit, old_targets: list[str], new_targets: list[str]
) -> bool:
    """
    Whether every protected span's own text survived a mechanical edit unchanged.

    Spans are those `highlight_string` marks (Task B). `DoubleSpaceCheck.
    get_fixup` and its siblings do not skip protected spans themselves,
    so clearing the targeted check is not proof placeholder/markup
    content survived - every mechanical candidate is independently
    verified here, regardless of which check proposed it. Compares the
    *sequence of highlighted substrings*, not positions - positions shift
    when whitespace elsewhere changes length, but a genuinely untouched
    span's own text does not.
    """
    if len(old_targets) != len(new_targets):  # pragma: no cover - defensive
        return False
    for old_target, new_target in zip(old_targets, new_targets, strict=True):
        old_spans = [
            old_target[highlight.start : highlight.end]
            for highlight in highlight_string(old_target, unit)
        ]
        new_spans = [
            new_target[highlight.start : highlight.end]
            for highlight in highlight_string(new_target, unit)
        ]
        if old_spans != new_spans:
            return False
    return True


def _mechanical_edit_holds(
    check_ids: Iterable[str],
    unit: Unit,
    sources: list[str],
    old_targets: list[str],
    new_targets: list[str],
) -> bool:
    """
    Veto for a mechanical-group candidate: every named check must stop failing.

    Task B. Every protected span must also survive unchanged. Unlike
    `_decision_7_holds`, mechanical checks are independent of each other
    - there is no shared "strictly shrinks" terminal-set invariant to
    honour, only "does not still fail" for each of `check_ids`.
    """
    after = _failing_checks(check_ids, unit, sources, new_targets)
    unit.invalidate_checks_cache()
    if after:
        return False
    return _protected_spans_preserved(unit, old_targets, new_targets)


def _classify_mechanical(
    user: User | None,
    unit: Unit,
    check_ids: tuple[str, ...],
    compute_new_targets: Callable[[Unit], list[str] | None],
) -> tuple[Bucket, list[str] | None]:
    """
    Shared classification shape for every Task B mechanical group.

    `compute_new_targets` returns the group's fully prepared candidate
    target - already through `_prepare_final_target`/`_finish_final_target`
    - or `None` when the unit is not eligible for this group at all
    (`no_fixup`: wrong sub-policy, provider inactive, or nothing to do).
    `check_ids` names every check the edit must clear; see
    `_mechanical_edit_holds`.
    """
    if user is not None and not user.has_perm("unit.edit", unit):
        return "denied", None
    new_targets = compute_new_targets(unit)
    if new_targets is None:
        return "no_fixup", None
    sources = unit.get_source_plurals()
    old_targets = unit.get_target_plurals()
    if not _mechanical_edit_holds(check_ids, unit, sources, old_targets, new_targets):
        return "manual", None
    return "eligible", new_targets


def _double_space_new_targets(unit: Unit) -> list[str] | None:
    return _prepare_final_target(CHECKS["double_space"].get_fixup(unit), unit)


def _edge_space_counts(text: str) -> tuple[int, int]:
    """(leading, trailing) ASCII-space counts - tabs/newlines never count."""
    return len(text) - len(text.lstrip(" ")), len(text) - len(text.rstrip(" "))


def _edge_space_fixup(unit: Unit, *, source_edge: bool) -> list[FixupType] | None:
    """
    Compute the edge-space fixup for one of Task B's two split policies.

    `source_edge=False` ("remove extra edge whitespace"): only an edge
    where source has zero spaces and target has some. `source_edge=True`
    ("reproduce source edge whitespace"): only an edge where source has a
    nonzero count that differs from target's. The two conditions are
    mutually exclusive per edge - a unit is never claimed by both policies
    on the same edge - and each edge is evaluated independently, so both
    may fire together for the same unit (e.g. a leading remove and a
    trailing source-sync at once). Reuses `BeginSpaceCheck`/
    `EndSpaceCheck.get_fixup()` verbatim for the actual replacement text -
    eligibility gating in front of the same `get_fixup`, not a new
    provider (plan, "Остальные механические правила").
    """
    source = unit.source_string
    target = unit.target
    source_leading, source_trailing = _edge_space_counts(source)
    target_leading, target_trailing = _edge_space_counts(target)
    if source_edge:
        begin_eligible = source_leading not in {0, target_leading}
        end_eligible = source_trailing not in {0, target_trailing}
    else:
        begin_eligible = source_leading == 0 and target_leading != 0
        end_eligible = source_trailing == 0 and target_trailing != 0
    fixups: list[FixupType] = []
    if begin_eligible:
        fixups.extend(CHECKS["begin_space"].get_fixup(unit) or [])
    if end_eligible:
        fixups.extend(CHECKS["end_space"].get_fixup(unit) or [])
    return fixups or None


def _edge_space_remove_new_targets(unit: Unit) -> list[str] | None:
    return _prepare_final_target(_edge_space_fixup(unit, source_edge=False), unit)


def _edge_space_source_new_targets(unit: Unit) -> list[str] | None:
    return _prepare_final_target(_edge_space_fixup(unit, source_edge=True), unit)


def _autofix_new_targets(fix_id: str, unit: Unit) -> list[str] | None:
    """
    Run autofix `fix_id`'s `fix_target()` and finish through the shared pipeline.

    Task B. `AUTOFIXES.get()` never imports `weblate_customization`
    directly - an optional provider that is not configured simply makes
    its group unavailable everywhere, never a core import error
    ("Конкретные providers доступны только через активный AUTOFIXES",
    plan).
    """
    autofix = AUTOFIXES.get(fix_id)
    if autofix is None:
        return None
    old_targets = unit.get_target_plurals()
    new_targets, changed = autofix.fix_target(old_targets, unit)
    if not changed:
        return None
    return _finish_final_target(unit, old_targets, new_targets)


def _line_separator_spacing_new_targets(unit: Unit) -> list[str] | None:
    return _autofix_new_targets("line-separator-spacing", unit)


def _end_ellipsis_new_targets(unit: Unit) -> list[str] | None:
    return _autofix_new_targets("end-ellipsis", unit)


def _zero_width_space_new_targets(unit: Unit) -> list[str] | None:
    return _autofix_new_targets("zero-width-space", unit)


def _punctuation_spacing_new_targets(unit: Unit) -> list[str] | None:
    """
    Fix existing wrong spacing, then add missing spacing when active.

    The core `PunctuationSpacing` autofix fixes existing wrong spacing;
    only when the fork's own `AddFrenchPunctuationSpacing` autofix is
    active does its missing-spacing insertion also run on top of that
    result - "combine French wrong/missing spacing into one group only
    when both allowlisted providers are present; core-only fixing of
    already-wrong spacing may have its own honestly named group without
    promising to insert missing ones" (plan).

    Deliberately the two `AutoFix`es, not `PunctuationSpacingCheck.
    get_fixup()`: unlike them, that check's own fixup already both fixes
    *and* adds in one pass (it is what the single-unit editor's "Fix
    string" button uses via `weblate/static/editor/full.js`), so it cannot
    represent the "core-only" group on its own.
    """
    fix_wrong = AUTOFIXES.get("punctuation-spacing")
    if fix_wrong is None:
        return None
    old_targets = unit.get_target_plurals()
    step1, _changed = fix_wrong.fix_target(old_targets, unit)
    add_missing = AUTOFIXES.get("french-punctuation-spacing")
    if add_missing is not None:
        step2, _changed = add_missing.fix_target(step1, unit)
    else:
        step2 = step1
    if step2 == old_targets:
        return None
    return _finish_final_target(unit, old_targets, step2)


@dataclass(frozen=True, slots=True)
class MechanicalGroup:
    """One Task B mechanical-group definition."""

    check_ids: tuple[str, ...]
    compute_new_targets: Callable[[Unit], list[str] | None]
    # Autofixes `compute_new_targets` itself depends on, beyond `check_ids`
    # (`AUTOFIXES.get()` returning `None` already makes `compute_new_targets`
    # return `None` for every unit; this only sharpens
    # `mechanical_group_available`'s cheap, query-free signal to match).
    required_autofix_ids: tuple[str, ...] = ()


def _mechanical_groups() -> dict[str, MechanicalGroup]:
    """
    Build the mechanical-group registry fresh on every call.

    Not at import time, so `AUTOFIXES`/`CHECKS` availability
    (`WEBLATE_ADD_CHECK`/`WEBLATE_ADD_AUTOFIX`, test `override_settings`)
    is always read fresh rather than cached from process start.
    """
    return {
        "double-space": MechanicalGroup(("double_space",), _double_space_new_targets),
        "edge-space-remove": MechanicalGroup(
            ("begin_space", "end_space"), _edge_space_remove_new_targets
        ),
        "edge-space-source": MechanicalGroup(
            ("begin_space", "end_space"), _edge_space_source_new_targets
        ),
        "line-separator-spacing": MechanicalGroup(
            ("game-line-break",),
            _line_separator_spacing_new_targets,
            required_autofix_ids=("line-separator-spacing",),
        ),
        "punctuation-spacing": MechanicalGroup(
            ("punctuation_spacing",),
            _punctuation_spacing_new_targets,
            required_autofix_ids=("punctuation-spacing",),
        ),
        "end-ellipsis": MechanicalGroup(
            ("end_ellipsis",),
            _end_ellipsis_new_targets,
            required_autofix_ids=("end-ellipsis",),
        ),
        "zero-width-space": MechanicalGroup(
            ("zero-width-space",),
            _zero_width_space_new_targets,
            required_autofix_ids=("zero-width-space",),
        ),
    }


MECHANICAL_GROUP_IDS: tuple[str, ...] = (
    "double-space",
    "edge-space-remove",
    "edge-space-source",
    "line-separator-spacing",
    "punctuation-spacing",
    "end-ellipsis",
    "zero-width-space",
)


def mechanical_group_available(group_id: str) -> bool:
    """
    Whether `group_id` has every required check/autofix configured right now.

    A cheap, query-free signal for the entry point (Task D), not a
    guarantee any row is actually eligible.
    """
    group = _mechanical_groups()[group_id]
    if not all(CHECKS.get(check_id) is not None for check_id in group.check_ids):
        return False
    return all(
        AUTOFIXES.get(fix_id) is not None for fix_id in group.required_autofix_ids
    )


def collect_mechanical_group_candidates(
    group_id: str,
    user: User | None,
    unit_set: UnitQuerySet,
    project: Project | None,
    *,
    preview_limit: int = 250,
) -> FixCandidates:
    """Bucket every currently active row for one Task B mechanical group."""
    if not mechanical_group_available(group_id):
        return FixCandidates()
    group = _mechanical_groups()[group_id]
    if len(group.check_ids) == 1:
        matching = _matching_units(unit_set, project, CHECKS[group.check_ids[0]])
    else:
        matching = _union_matching_units(unit_set, project, group.check_ids)
    matching = matching.prefetch_related(_newest_verdict_prefetch()).order_by(
        "translation__component_id", "translation_id", "position", "id"
    )
    result = FixCandidates()
    for unit in matching.iterator(chunk_size=200):
        bucket, new_targets = _classify_mechanical(
            user, unit, group.check_ids, group.compute_new_targets
        )
        if bucket == "denied":
            result.denied += 1
        elif bucket in {"manual", "no_fixup"}:
            result.manual += 1
            if bucket == "no_fixup":
                result.manual_no_fixup += 1
        else:
            result.total_eligible += 1
            if new_targets is not None and _verdict_would_go_stale(unit, new_targets):
                result.verdicts_no_longer_current += 1
            if len(result.shown) < preview_limit and new_targets is not None:
                result.shown.append(FixPreviewRow(unit=unit, final_target=new_targets))
    return result


def perform_mechanical_group_fix(
    group_id: str,
    user: User | None,
    unit_set: UnitQuerySet,
    project: Project | None,
    unit_ids: Iterable[int] | None = None,
    *,
    progress_callback: Callable[[int, int], bool] | None = None,
    progress_every: int = 20,
) -> FixResult:
    """Recompute and write every fresh eligible candidate for one Task B mechanical group."""
    if not mechanical_group_available(group_id):
        return FixResult()
    group = _mechanical_groups()[group_id]
    if len(group.check_ids) == 1:
        matching = _matching_units(unit_set, project, CHECKS[group.check_ids[0]])
    else:
        matching = _union_matching_units(unit_set, project, group.check_ids)
    return _perform_fix_over(
        user,
        matching,
        unit_ids,
        lambda u, unit: _classify_mechanical(
            u, unit, group.check_ids, group.compute_new_targets
        ),
        progress_callback=progress_callback,
        progress_every=progress_every,
    )


# Task 4 step 5: an explicit reservation guarding against two simultaneous
# submits for the same (check, scope). Immutable scope identity plus the
# check, never the actor - `docs/product/plans/2026-08-25-mass-fix-failing-
# checks.md`, Task 4 step 5. Same order of magnitude as `TASK_METADATA_TTL`
# (`weblate/utils/celery.py:44`) and the Redis expiry `WeblateLock` uses
# (`weblate/utils/lock.py:48`); a lease, not a run-duration estimate.
FIX_CHECK_LOCK_TTL = 3600


def fix_check_lock_key(check_id: str, scope_type: str, scope_pk: int | str) -> str:
    """Build the concurrency-guard cache key for one (check, scope) pair."""
    return f"fix-check-lock-{check_id}-{scope_type}-{scope_pk}"


def _get_redis_client():
    return cast("RedisCache", cache).client.get_client(write=True)


# `register_script()` only computes the script SHA locally, so one
# registration per source is enough for the process; the client is passed
# per call so a reconfigured cache (tests override `CACHES`) still talks to
# its own connection. Task 4 step 5 requires registering once rather than
# on every refresh/release tick.
_LUA_SCRIPTS: dict[str, Any] = {}


def _lua_script(client, source: str):
    script = _LUA_SCRIPTS.get(source)
    if script is None:
        script = client.register_script(source)
        _LUA_SCRIPTS[source] = script
    return script


def acquire_fix_check_lock(key: str, token: str) -> bool:
    """
    Atomically reserve `key` for `token` (the queued task id).

    Set-if-absent on both backends - `client.set(nx=True)` on Redis,
    `cache.add()` elsewhere - so two simultaneous submits cannot both win.
    """
    if is_redis_cache():
        return bool(_get_redis_client().set(key, token, nx=True, ex=FIX_CHECK_LOCK_TTL))
    return cache.add(key, token, timeout=FIX_CHECK_LOCK_TTL)


def refresh_fix_check_lock(key: str, token: str) -> bool:
    """
    Extend the reservation lease held by `token`.

    Redis only: `GET` -> compare token -> `PEXPIRE`, byte-for-byte
    redis-py's own `Lock.reacquire()` script
    (`RedisLock.LUA_REACQUIRE_SCRIPT`), reused here rather than reaching
    into `Lock.local.token`, which is thread-local and not addressable
    across the request/worker boundary. A token mismatch means another run
    owns the reservation and is a lost lease, never a silent no-op. A
    non-Redis cache has no cross-process compare-and-set, so the guard
    degrades to a plain lease that simply expires; this always reports
    success there, which is the documented non-Redis behaviour.
    """
    if not is_redis_cache():
        return True
    client = _get_redis_client()
    script = _lua_script(client, RedisLock.LUA_REACQUIRE_SCRIPT)
    return bool(
        script(keys=[key], args=[token, FIX_CHECK_LOCK_TTL * 1000], client=client)
    )


def release_fix_check_lock(key: str, token: str) -> bool:
    """
    Release the reservation held by `token`.

    Redis only: `GET` -> compare token -> `DEL`, byte-for-byte redis-py's
    own `Lock.do_release()` script (`RedisLock.LUA_RELEASE_SCRIPT`). A
    token mismatch means the lease already lapsed and is recorded, never
    retried. A non-Redis cache has no early release at all; the
    reservation simply expires, so this always reports success there
    without touching the key.
    """
    if not is_redis_cache():
        return True
    client = _get_redis_client()
    script = _lua_script(client, RedisLock.LUA_RELEASE_SCRIPT)
    return bool(script(keys=[key], args=[token], client=client))


# Task 5 step 4 / Task 2 step 6: a review-tier submit may only carry rows the
# actor actually saw with their diff and checkbox. The rendered cohort is
# handed out as a signed, identity-bound payload and verified on POST, so a
# crafted request cannot repair review-tier candidates that were never
# previewed. Liveness is still re-checked at apply time, so a previewed row
# that went stale degrades to `stale_or_no_change`.
FIX_CHECK_COHORT_SALT = "weblate.trans.fix_check.cohort"


def _cohort_salt(
    *, user_id: int | None, check_id: str, scope_type: str, scope_pk: int | str
) -> str:
    return f"{FIX_CHECK_COHORT_SALT}:{user_id}:{check_id}:{scope_type}:{scope_pk}"


def dump_fix_check_cohort(
    unit_ids: Iterable[int],
    *,
    user_id: int | None,
    check_id: str,
    scope_type: str,
    scope_pk: int | str,
) -> str:
    """Sign the previewed unit ids for this actor, check and scope."""
    return signing.dumps(
        sorted(unit_ids),
        salt=_cohort_salt(
            user_id=user_id,
            check_id=check_id,
            scope_type=scope_type,
            scope_pk=scope_pk,
        ),
    )


def load_fix_check_cohort(
    payload: str,
    *,
    user_id: int | None,
    check_id: str,
    scope_type: str,
    scope_pk: int | str,
    max_size: int = 250,
) -> set[int]:
    """
    Verify a signed cohort payload and return its unit ids.

    Raises `signing.BadSignature` for a tampered, foreign, expired or
    oversized payload; the caller turns that into a form error.
    """
    unit_ids = signing.loads(
        payload,
        salt=_cohort_salt(
            user_id=user_id,
            check_id=check_id,
            scope_type=scope_type,
            scope_pk=scope_pk,
        ),
        max_age=FIX_CHECK_LOCK_TTL,
    )
    if not isinstance(unit_ids, list) or len(unit_ids) > max_size:
        msg = "Invalid mass-fix review cohort payload"
        raise signing.BadSignature(msg)
    return {int(unit_id) for unit_id in unit_ids}


# --- Policy resolution (Task C) -------------------------------------------
#
# One identifier space behind `fix_check`: a legacy tiered check
# (`safe`/`review`, Task 2, unchanged) or one of the new explicit policies
# (Task A's `terminal-source`, Task B's seven mechanical groups). The two
# families never collide - every mechanical group id is hyphenated
# (`edge-space-remove`) while every `CHECKS` id this feature ever tiered is
# underscored (`double_space`) - so `resolve_fix_policy` can check the new
# names first and fall through to the registry unchanged.
_MECHANICAL_GROUP_LABELS: dict[str, tuple[StrOrPromise, StrOrPromise]] = {
    "double-space": (
        gettext_lazy("Collapse double spaces"),
        gettext_lazy("Consecutive spaces are collapsed into one, matching the source."),
    ),
    "edge-space-remove": (
        gettext_lazy("Remove extra edge whitespace"),
        gettext_lazy(
            "Leading or trailing whitespace the source does not have is removed."
        ),
    ),
    "edge-space-source": (
        gettext_lazy("Reproduce source edge whitespace"),
        gettext_lazy(
            "Leading or trailing whitespace is synced to the source's own count."
        ),
    ),
    "line-separator-spacing": (
        gettext_lazy("Fix spacing around the $ line separator"),
        gettext_lazy(
            "Whitespace hugging the $ line separator is removed. The number "
            "and order of separators is never changed."
        ),
    ),
    "punctuation-spacing": (
        gettext_lazy("Fix French punctuation spacing"),
        gettext_lazy(
            "The non-breaking space French double punctuation needs is "
            "corrected where present, and added where missing if the "
            "matching provider is configured."
        ),
    ),
    "end-ellipsis": (
        gettext_lazy("Use a single ellipsis character"),
        gettext_lazy(
            'A trailing "..." is replaced with a single ellipsis character '
            "to match the source."
        ),
    ),
    "zero-width-space": (
        gettext_lazy("Remove stray zero-width spaces"),
        gettext_lazy("A zero-width space the source does not have is removed."),
    ),
}


@dataclass(frozen=True, slots=True)
class FixPolicy:
    """
    One resolved, fixable identity behind `fix_check` (Task C).

    Either a single legacy tiered check (`tier` is `safe`/`review`, exactly
    as Task 2 shipped it) or one of the new explicit policies (`tier` is
    `explicit`: `terminal-source`, or one of `MECHANICAL_GROUP_IDS`), which
    - unlike `safe` (always the whole scope) or `review` (always exactly
    the previewed page) - support choosing between the two: "apply to all
    N matching strings" or "apply the selected page". `collect`/`perform`
    are already bound to this policy's own identity, so a caller never
    branches on which family it is: `policy.collect(user, unit_set,
    project, preview_limit=…)` and `policy.perform(user, unit_set, project,
    unit_ids=…, progress_callback=…, progress_every=…)`.
    """

    id: str
    name: StrOrPromise
    description: StrOrPromise
    tier: Literal["safe", "review", "explicit"]
    # Every check this policy's edit clears - one for a legacy check, four
    # for `terminal-source`, one or two for a mechanical group. Drives the
    # "Browse" search link (`url_id`), never the classification itself.
    check_ids: tuple[str, ...]
    collect: Callable[..., FixCandidates | TerminalPolicyCandidates]
    perform: Callable[..., FixResult]

    @property
    def url_id(self) -> str:
        """A `q=` search-filter value matching every row this policy could touch."""
        return " OR ".join(f"check:{check_id}" for check_id in self.check_ids)


def resolve_fix_policy(name: str) -> FixPolicy | None:
    """
    Resolve a `fix_check` URL path segment to a `FixPolicy`, or `None`.

    `None` when `name` names neither a mass-fix-tiered check nor a Task
    A/B policy. A mechanical group whose required check/autofix is not
    configured resolves to `None` too - "the entry point is available by
    supported operation ... not merely by `get_fixup` presence" cuts both
    ways: an unsupported operation is not offered at all, rather than
    offered and then failing (plan, Task D).
    """
    if name == TERMINAL_SOURCE_POLICY_ID:
        return FixPolicy(
            id=name,
            name=gettext_lazy("Normalize terminal marks to source"),
            description=gettext_lazy(
                "Marks will be brought in line with the source. Meaning and "
                "translation quality are not checked."
            ),
            tier="explicit",
            check_ids=TERMINAL_SOURCE_POLICY_CHECK_IDS,
            collect=collect_terminal_policy_candidates,
            perform=perform_terminal_policy_fix,
        )
    if name in MECHANICAL_GROUP_IDS:
        if not mechanical_group_available(name):
            return None
        group_name, group_description = _MECHANICAL_GROUP_LABELS[name]
        group = _mechanical_groups()[name]
        return FixPolicy(
            id=name,
            name=group_name,
            description=group_description,
            tier="explicit",
            check_ids=group.check_ids,
            collect=partial(collect_mechanical_group_candidates, name),
            perform=partial(perform_mechanical_group_fix, name),
        )
    check_obj = CHECKS.get(name)
    if check_obj is not None and check_obj.mass_fixup is not None:
        return FixPolicy(
            id=name,
            name=check_obj.name,
            description=check_obj.description,
            tier=check_obj.mass_fixup,
            check_ids=(check_obj.check_id,),
            collect=partial(collect_fix_candidates, check_obj=check_obj),
            perform=partial(perform_fix, check_obj=check_obj),
        )
    return None


# check_id -> the `FixPolicy` id a "Fix" link for that failing check should
# point to (Task D). Every terminal check funnels into the one combined
# `terminal-source` policy - `_terminal_policy_edit`'s own per-unit routing
# decides append/replace/remove, never this mapping.
# `begin_space`/`end_space` are genuinely ambiguous per unit - which of the
# two split edge policies applies depends on that specific unit's own
# source - so they default to the removal policy, the more common shape
# measured on Anvil Saga; `fix_check.html` cross-links to the other one
# from there. Every check Task A/B does not touch (`kabyle-characters`,
# `ellipsis`, `end_interrobang`, ...) is deliberately absent: it still
# resolves through its own single-check tier, unchanged.
CHECK_TO_POLICY_ID: dict[str, str] = {
    "end_stop": TERMINAL_SOURCE_POLICY_ID,
    "end_colon": TERMINAL_SOURCE_POLICY_ID,
    "end_question": TERMINAL_SOURCE_POLICY_ID,
    "end_exclamation": TERMINAL_SOURCE_POLICY_ID,
    "double_space": "double-space",
    "begin_space": "edge-space-remove",
    "end_space": "edge-space-remove",
    "game-line-break": "line-separator-spacing",
    "punctuation_spacing": "punctuation-spacing",
    "end_ellipsis": "end-ellipsis",
    "zero-width-space": "zero-width-space",
}


def fix_check_policy_id_for_check(check_id: str) -> str:
    """
    Resolve the `FixPolicy` id a "Fix" link for `check_id` should point to.

    Falls back to `check_id` itself unchanged for every check Task A/B
    does not touch, which still resolves through its own single-check tier
    via `resolve_fix_policy` exactly as before.
    """
    return CHECK_TO_POLICY_ID.get(check_id, check_id)
