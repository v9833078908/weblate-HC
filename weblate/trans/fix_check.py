# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Server-side engine for the mass-fix-failing-checks feature.

Reuses the existing `Check.get_fixup()` contract (`weblate/checks/base.py`):
`apply_fixup_python` is the Python-side equivalent of the JS single-unit
"Fix string" button (`weblate/static/editor/full.js`), so preview and apply
share the exact same computed text as the live editor.

See `docs/product/plans/2026-08-25-mass-fix-failing-checks.md`, Task 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from django.core.cache import cache
from django.db import transaction
from redis.lock import Lock as RedisLock

from weblate.checks.models import CHECKS
from weblate.trans.actions import ActionEvents
from weblate.trans.autofixes import fix_target
from weblate.trans.file_format_params import DOSLineEndings
from weblate.trans.models import Component, Unit
from weblate.trans.models.unit import NEWLINES
from weblate.trans.util import join_plural
from weblate.utils.cache import is_redis_cache

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from django_redis.cache import RedisCache

    from weblate.auth.models import User
    from weblate.checks.base import BaseCheck, FixupType
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
    already Unicode-aware) and is ignored. Applies every fixup, in order,
    to every plural form.
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
        count = 0 if "g" in flags else 1
        re_flags = re.IGNORECASE if "i" in flags else 0
        result = [
            re.sub(pattern, replacement, text, count=count, flags=re_flags)
            for text in result
        ]
    return result


def _compute_final_target(check_obj: BaseCheck, unit: Unit) -> list[str] | None:
    """
    Compute the final stored target for one candidate.

    Applies the check's fixup, then the same preparation order
    `Unit.translate()` runs around `fix_target()`
    (`weblate/trans/models/unit.py:2412-2436`): multivalue empty-entry
    filtering (or plural-count adjustment otherwise), the fixup's own
    autofix normalization for a non-template translation, and DOS
    line-ending conversion - so preview and apply both compute the exact
    final stored value (Task 2 step 1). Returns `None` when there is no
    fixup or it produces no change from the unit's current target.
    """
    fixups = check_obj.get_fixup(unit)
    if not fixups:
        return None
    old_targets = unit.get_target_plurals()
    new_targets = apply_fixup_python(fixups, old_targets)

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


Bucket = str  # "eligible" | "manual" | "denied"


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
    `manual`: no fixup, no final target change, an uncleared check, a
    conflicting terminal mark, or a newly introduced terminal/
    `punctuation_spacing` failure.
    `eligible`: a permitted final target that satisfies decision 7 in full.
    """
    if user is not None and not user.has_perm("unit.edit", unit):
        return "denied", None
    new_targets = _compute_final_target(check_obj, unit)
    if new_targets is None:
        return "manual", None
    sources = unit.get_source_plurals()
    old_targets = unit.get_target_plurals()
    if not _decision_7_holds(check_obj, unit, sources, old_targets, new_targets):
        return "manual", None
    return "eligible", new_targets


def _verdict_would_go_stale(unit: Unit, new_targets: list[str]) -> bool:
    """
    Report whether the fix would make the unit's newest verdict stale.

    True when the unit's newest judge verdict is current for the present
    text and will stop being current after the fix (Task 2 step 4,
    decision 9). No re-check is queued here - this is a reported count
    only.
    """
    latest = unit.judge_verdicts.order_by("-timestamp").first()
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
    matching = _matching_units(unit_set, project, check_obj).order_by(
        "translation__component_id", "translation_id", "position", "id"
    )
    result = FixCandidates()
    for unit in matching.iterator(chunk_size=200):
        bucket, new_targets = _classify(user, check_obj, unit)
        if bucket == "denied":
            result.denied += 1
        elif bucket == "manual":
            result.manual += 1
        else:
            result.total_eligible += 1
            if new_targets is not None and _verdict_would_go_stale(
                unit, new_targets
            ):
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
    id list means only the checked review rows. Explicit ids are
    intersected with the live active-check query first, so an id that no
    longer matches (already fixed by another run, dismissed, or otherwise
    changed since the preview) is counted as `stale_or_no_change`, never
    submitted. A manual or unresolved row is never submitted either.
    """
    matching = _matching_units(unit_set, project, check_obj)
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
                Unit.objects.filter(pk__in=ids).prefetch().select_for_update()
            ):
                matched_ids.add(unit.pk)
                # Share this call's single Component instance so every
                # unit's batched-check bookkeeping
                # (`component.updated_sources`/`batched_checks`) lands on
                # the same object `run_batched_checks()` reads below,
                # mirroring the existing propagation precedent at
                # `weblate/trans/models/unit.py:2314`.
                unit.translation.component = component
                bucket, new_targets = _classify(user, check_obj, unit)
                if bucket == "denied":
                    result.denied += 1
                    continue
                if bucket == "manual":
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


def acquire_fix_check_lock(key: str, token: str) -> bool:
    """
    Atomically reserve `key` for `token` (the queued task id).

    Set-if-absent on both backends - `client.set(nx=True)` on Redis,
    `cache.add()` elsewhere - so two simultaneous submits cannot both win.
    """
    if is_redis_cache():
        return bool(
            _get_redis_client().set(key, token, nx=True, ex=FIX_CHECK_LOCK_TTL)
        )
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
    script = client.register_script(RedisLock.LUA_REACQUIRE_SCRIPT)
    return bool(script(keys=[key], args=[token, FIX_CHECK_LOCK_TTL * 1000]))


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
    script = client.register_script(RedisLock.LUA_RELEASE_SCRIPT)
    return bool(script(keys=[key], args=[token]))
