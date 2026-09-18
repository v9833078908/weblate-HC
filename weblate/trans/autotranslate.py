# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from celery import current_task
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, QuerySet, Value, When
from django.db.models.functions import MD5, Lower
from django.utils import timezone
from django.utils.translation import gettext, ngettext

from weblate.checks.models import CHECKS
from weblate.machinery.base import (
    MachineTranslationError,
    MachineTranslationServiceError,
)
from weblate.machinery.models import MACHINERY
from weblate.trans.actions import ActionEvents
from weblate.trans.judge import (
    JUDGE_SEATS,
    JudgeError,
    judge_configuration_snapshot,
    judge_initial_request_count,
    validate_judge_configuration,
)
from weblate.trans.judge_loop import (
    DEFAULT_CANDIDATE_SEVERITIES,
    _run_cancel_requested,
    build_request,
    run_judge_batch,
)
from weblate.trans.machinery import MachineryBatchOutcome, fetch_machinery_matches
from weblate.trans.models import (
    Category,
    Component,
    Project,
    Suggestion,
    SuggestionAddResult,
    Translation,
    Unit,
)
from weblate.trans.models.judge import (
    JudgeRunUnit,
    JudgeVerdict,
    ProducerRun,
    compute_context_hash,
    compute_target_hash,
    current_verdict,
    has_complete_current_evidence,
    state_for_verdict,
)
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.util import is_plural, split_plural
from weblate.utils.celery import touch_task_liveness
from weblate.utils.state import (
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_READONLY,
    STATE_TRANSLATED,
)
from weblate.utils.stats import ProjectLanguage
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from weblate.auth.models import User
    from weblate.auth.results import PermissionResult
    from weblate.machinery.base import BatchMachineTranslation, UnitMemoryResultDict
    from weblate.utils.state import StringState


# A refusal that outlived the request retries stops the service for everyone, so
# batches of a run started meanwhile are skipped. Wait for a short stop to pass
# rather than dropping their strings, but never hold a run for a long one.


@dataclass(frozen=True, slots=True)
class JudgeScopePreview:
    matched: int
    processed: int
    remaining: int
    writable: int
    initial_calls: int
    worst_case_calls: int


@dataclass(frozen=True, slots=True)
class MTScopePreview:
    matched: int
    writable: int
    per_translation: list[tuple[Translation, int]]


@dataclass(frozen=True, slots=True)
class JudgeSummary:
    """
    Producer-facing tally of one judge run's outcomes.

    ``repaired`` counts verdicts whose retained round has ``attempt > 0``:
    a repair-then-rejudge cycle happened, not that the severity improved.
    """

    evaluated: int = 0
    nothing_blocking: int = 0
    minor_noted: int = 0
    major_not_fixed: int = 0
    critical_held: int = 0
    unparsed: int = 0
    repaired: int = 0
    untranslated: int = 0
    cap_remainder: int = 0

    def __add__(self, other: JudgeSummary) -> JudgeSummary:
        if not isinstance(other, JudgeSummary):
            return NotImplemented
        return JudgeSummary(
            evaluated=self.evaluated + other.evaluated,
            nothing_blocking=self.nothing_blocking + other.nothing_blocking,
            minor_noted=self.minor_noted + other.minor_noted,
            major_not_fixed=self.major_not_fixed + other.major_not_fixed,
            critical_held=self.critical_held + other.critical_held,
            unparsed=self.unparsed + other.unparsed,
            repaired=self.repaired + other.repaired,
            untranslated=self.untranslated + other.untranslated,
            cap_remainder=self.cap_remainder + other.cap_remainder,
        )


JUDGE_CHUNK_SIZE: int = 100


def format_judge_summary(summary: JudgeSummary) -> str:
    """Build the one producer-facing completion message for a judge run."""
    message = gettext(
        "%(evaluated)d evaluated: %(nothing_blocking)d with no blocking concern, "
        "%(minor_noted)d minor noted, %(major_not_fixed)d major not fixed, "
        "%(critical_held)d critical held, %(unparsed)d unparsed."
    ) % {
        "evaluated": summary.evaluated,
        "nothing_blocking": summary.nothing_blocking,
        "minor_noted": summary.minor_noted,
        "major_not_fixed": summary.major_not_fixed,
        "critical_held": summary.critical_held,
        "unparsed": summary.unparsed,
    }
    if summary.repaired:
        message += " " + ngettext(
            "%(repaired)d string was repaired and re-judged.",
            "%(repaired)d strings were repaired and re-judged.",
            summary.repaired,
        ) % {"repaired": summary.repaired}
    if summary.untranslated:
        message += " " + ngettext(
            "%(untranslated)d string had no translation to judge.",
            "%(untranslated)d strings had no translation to judge.",
            summary.untranslated,
        ) % {"untranslated": summary.untranslated}
    if summary.cap_remainder:
        message += " " + gettext(
            "%(remaining)d matching strings remain because of the per-run cap."
        ) % {"remaining": summary.cap_remainder}
    return message


def _summarize_verdicts(
    verdicts: dict[int, JudgeVerdict], *, cap_remainder: int, untranslated: int = 0
) -> JudgeSummary:
    """Tally one judge run's retained verdicts into producer-facing buckets."""
    nothing_blocking = minor_noted = major_not_fixed = critical_held = 0
    unparsed = repaired = 0
    for verdict in verdicts.values():
        if verdict.unparsed:
            unparsed += 1
        elif verdict.effective_severity == JudgeVerdict.Severity.CRITICAL:
            critical_held += 1
        elif verdict.effective_severity == JudgeVerdict.Severity.MAJOR:
            major_not_fixed += 1
        elif verdict.effective_severity == JudgeVerdict.Severity.MINOR:
            minor_noted += 1
        else:
            nothing_blocking += 1
        if verdict.attempt > 0:
            repaired += 1
    return JudgeSummary(
        evaluated=len(verdicts),
        nothing_blocking=nothing_blocking,
        minor_noted=minor_noted,
        major_not_fixed=major_not_fixed,
        critical_held=critical_held,
        unparsed=unparsed,
        repaired=repaired,
        untranslated=untranslated,
        cap_remainder=cap_remainder,
    )


def _judge_phase(
    completed_batches: int, *, initial_calls: int, worst_case_calls: int
) -> tuple[str, int, int]:
    """Split a judge run's batch count into its judging/repairing phase."""
    if completed_batches <= initial_calls:
        return "judging", completed_batches, initial_calls
    return (
        "repairing",
        completed_batches - initial_calls,
        worst_case_calls - initial_calls,
    )


def check_auto_translate_permission(
    user: User | None, translation: Translation, mode: str
) -> bool | PermissionResult:
    # Add-on users identify generated changes rather than authorize the operation.
    if user is None or (user.is_bot and user.username.startswith("addon:")):
        return True
    if not (permission := user.has_perm("translation.auto", translation)):
        return permission
    if mode == "suggest":
        if not translation.restrict_direct_editing:
            return True
        return user.has_perm("suggestion.add", translation)
    if mode == "judge":
        # The judge decides the state per verdict, including approved;
        # the same review right as the "approved" mode is required.
        return user.has_perm("unit.review", translation)
    return user.has_perm("meta:unit.direct_edit", translation)


@dataclass(frozen=True, slots=True)
class PreparationScope:
    """
    The closed pre-judge machine-translation scope, fixed before any paid call.

    ``unit_ids`` covers every selected string in the selected
    component/language pairs, translated or not; ``missing_ids`` is the
    subset whose target plurals are all empty at snapshot time. Judge's own
    capped scope is computed from these pairs separately and is never
    recounted after MT through changed state filters.
    """

    unit_ids: tuple[int, ...]
    missing_ids: tuple[int, ...]
    per_language_missing: dict[str, int]
    mt_engine: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "unit_ids": sorted(self.unit_ids),
            "missing_ids": sorted(self.missing_ids),
            "per_language_missing": dict(sorted(self.per_language_missing.items())),
            "mt_engine": self.mt_engine,
        }

    @staticmethod
    def from_json(payload: Mapping[str, Any]) -> PreparationScope:
        return PreparationScope(
            unit_ids=tuple(int(pk) for pk in payload.get("unit_ids", [])),
            missing_ids=tuple(int(pk) for pk in payload.get("missing_ids", [])),
            per_language_missing={
                str(language): int(count)
                for language, count in payload.get("per_language_missing", {}).items()
            },
            mt_engine=(
                str(engine)
                if isinstance(engine := payload.get("mt_engine"), str)
                else None
            ),
        )


def unit_missing_translation(unit: Unit) -> bool:
    """Whether the unit has no stored target text in any plural form."""
    return not any(unit.get_target_plurals())


def _target_forms_count(unit: Unit) -> int:
    """Count the plural form slots this unit's target actually carries."""
    return len(split_plural(unit.target))


def unit_target_incomplete(unit: Unit) -> bool:
    """
    Whether the unit's target is partial for its plural cardinality.

    A fully empty unit is *missing* (the preparation MT fills it); a fully
    written one is ready. Only a unit whose stored forms are fewer than the
    language requires is incomplete: a singular unit written by MT has one
    slot filled and is ready, while a plural unit with only some forms
    filled blocks readiness and must not be machine-overwritten.
    """
    if not unit.is_plural:
        return False
    targets = unit.get_target_plurals()
    expected = unit.translation.plural.number
    filled = sum(bool(text) for text in targets)
    return 0 < filled < expected


class _AttemptCounter:
    """Attempt-local `updated / eligible` counter shared by a whole batch."""

    __slots__ = ("eligible", "snapshot_taken", "updated")

    def __init__(self) -> None:
        self.updated = 0
        self.eligible: int | None = None
        self.snapshot_taken = False


class BaseAutoTranslate:
    updated: int = 0
    progress_steps: int = 0
    # Slice of the overall task progress this instance reports into. A batch
    # gives each translation its own slice so the percentage never goes back.
    progress_range: tuple[int, int] = (0, 100)
    # Attempt-local user-facing counter (plan 2026-09-11, Task 2): `updated`
    # counts units whose translation was actually stored; `eligible` is the
    # permission-filtered snapshot taken before the first mutation of this
    # delivery attempt. Both live on the BatchAutoTranslate and are shared
    # with the child AutoTranslate instances (assigned directly, like
    # `progress_range` below) so a redelivered task starts a new snapshot
    # over the remaining `state:empty` units instead of summing incomparable
    # snapshots.
    batch_counter: _AttemptCounter | None = None

    def __init__(
        self,
        *,
        user: User | None,
        q: str,
        mode: str,
        component_wide: bool = False,
        unit_ids: list[int] | None = None,
        allow_non_shared_tm_source_components: bool = False,
    ) -> None:
        self.user: User | None = user
        self.q: str = q
        self.mode: str = mode
        self.component_wide: bool = component_wide
        self.unit_ids: list[int] | None = unit_ids
        self.allow_non_shared_tm_source_components = (
            allow_non_shared_tm_source_components
        )
        self.failure_message: str | None = None
        self.warnings: list[str] = []
        self.judge_summary: JudgeSummary | None = None

    def get_message(self) -> str:
        if self.mode == "judge" and self.judge_summary is not None:
            return format_judge_summary(self.judge_summary)
        if self.updated == 0:
            return gettext("Automatic translation completed, no strings were updated.")
        message = ngettext(
            "Automatic translation completed, %d string was updated.",
            "Automatic translation completed, %d strings were updated.",
            self.updated,
        )
        try:
            return message % self.updated
        except TypeError:
            return message

    def get_task_meta(self) -> dict[str, Any]:
        """Return a metadata dictionary for Celery task progress tracking."""
        raise NotImplementedError

    def add_warning(self, warning: str) -> None:
        if warning not in self.warnings:
            self.warnings.append(warning)

    def get_warnings(self) -> list[str]:
        return self.warnings

    def set_progress(
        self,
        current: int,
        *,
        phase: str | None = None,
        phase_current: int | None = None,
        phase_total: int | None = None,
    ) -> None:
        if current_task and current_task.request.id:
            # Every progress write is also a liveness heartbeat, so an
            # actively working task is never reported as `no-update`.
            touch_task_liveness(current_task.request.id)
            low, high = self.progress_range
            # Never mutate the shared meta dict: Celery's AsyncResult keeps
            # a reference to the first meta it sees per state, and a test's
            # recorded call list would otherwise replay the last values.
            meta = dict(self.get_task_meta())
            if self.batch_counter is not None:
                counter = self.batch_counter
                meta["done"] = counter.updated
                meta["total"] = counter.eligible or counter.updated
            meta["progress"] = low + (high - low) * current // self.progress_steps
            if phase is not None:
                meta["phase"] = phase
                meta["phase_current"] = phase_current
                meta["phase_total"] = phase_total
            current_task.update_state(state="PROGRESS", meta=meta)

    @staticmethod
    def _record_skipped_judge_units(
        run: ProducerRun,
        units: Sequence[Unit],
        reason: JudgeRunUnit.SkipReason,
    ) -> None:
        for unit in units:
            request = build_request(unit)
            # Never overwrite a row that already has a terminal outcome: a
            # preparation failure must not erase an earlier cap or
            # permission reason recorded for the same unit.
            existing = JudgeRunUnit.objects.filter(
                run=run, unit_id_snapshot=unit.id
            ).first()
            if (
                existing is not None
                and existing.outcome != JudgeRunUnit.Outcome.PENDING
            ):
                # A terminal row (cap/permission/passed/…) keeps its reason;
                # only a fresh or still-PENDING row takes this skip.
                continue
            JudgeRunUnit.objects.update_or_create(
                run=run,
                unit_id_snapshot=unit.id,
                defaults={
                    "unit": unit,
                    "translation_id": unit.translation_id,
                    "component_id": unit.translation.component_id,
                    "project_id": unit.translation.component.project_id,
                    "input_target": unit.get_target_plurals(),
                    "input_target_hash": compute_target_hash(unit.get_target_plurals()),
                    "context_hash": compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                        clarification=request.clarification,
                    ),
                    "outcome": JudgeRunUnit.Outcome.SKIPPED,
                    "skip_reason": reason,
                    "before_target": unit.get_target_plurals(),
                    "after_target": unit.get_target_plurals(),
                },
            )


class AutoTranslate(BaseAutoTranslate):
    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        *,
        translation: Translation,
        user: User | None,
        q: str,
        mode: str,
        component_wide: bool = False,
        unit_ids: list[int] | None = None,
        allow_non_shared_tm_source_components: bool = False,
        overwrite_existing: bool = False,
        judge_limit: int | None = None,
        judge_uncapped: bool = False,
        producer_run: ProducerRun | None = None,
        judge_pretranslate: bool = True,
        judge_mutating_repairs: bool = True,
        judge_candidate_severities: tuple[str, ...] = DEFAULT_CANDIDATE_SEVERITIES,
        judge_proposal_only: bool = False,
    ) -> None:
        super().__init__(
            user=user,
            q=q,
            mode=mode,
            component_wide=component_wide,
            unit_ids=unit_ids,
            allow_non_shared_tm_source_components=(
                allow_non_shared_tm_source_components
            ),
        )
        self.translation: Translation = translation
        translation.component.start_batched_checks()
        self.progress_base = 0
        self.written: set[int] = set()
        self.target_state = STATE_TRANSLATED
        self.overwrite_existing = overwrite_existing
        self.judge_limit = judge_limit
        self.judge_uncapped = judge_uncapped
        self.producer_run = producer_run
        self.judge_pretranslate = judge_pretranslate
        self.judge_mutating_repairs = judge_mutating_repairs
        self.judge_candidate_severities = judge_candidate_severities
        self.judge_proposal_only = judge_proposal_only
        self.judge_units_matched = 0
        self.judge_units_processed = 0
        self.judge_units_remaining = 0
        # D2/fail-safe: a judge translation never starts shippable; the
        # verdict decides the final state per string.
        self.fresh_translation_state = STATE_FUZZY
        if self.mode == "fuzzy":
            self.target_state = STATE_FUZZY
        elif self.mode == "approved" and translation.enable_review:
            self.target_state = STATE_APPROVED
        # Mandatory pre-judge preparation writes only units still missing at
        # store time; a human's concurrent write is never overwritten.
        self.preparation_write_lock = False
        # Snapshot of the sources the preparation fetch was based on: a unit
        # whose source changed between fetch and store never receives the
        # stale answer (C5).
        self.preparation_source_hashes: dict[int, str] | None = None

    def get_units(self):
        units = self.translation.unit_set.exclude(state=STATE_READONLY)
        if self.unit_ids is not None:
            units = units.filter(pk__in=self.unit_ids)
        if self.mode == "suggest":
            units = units.filter(suggestion__isnull=True)
        return units.search(self.q, parser="unit")

    def get_task_meta(self) -> dict[str, Any]:
        return {"translation": self.translation.pk}

    def update(
        self, unit: Unit, state: StringState, target: list[str], user=None
    ) -> None:
        if isinstance(target, str):
            target = [target]
        max_length = unit.get_max_length()
        max_length_check = CHECKS.get("max-length")
        replace = (
            max_length_check.get_replacement_function(unit)
            if max_length_check is not None
            else lambda text: text
        )
        over_max_length = any(len(replace(item)) > max_length for item in target)
        if self.mode == "suggest" or (over_max_length and self.mode != "judge"):
            _, result = Suggestion.objects.add(
                unit,
                target,
                request=None,
                vote=False,
                user=user or self.user,
                raise_exception=False,
            )
            if result == SuggestionAddResult.CREATED:
                self.updated += 1
                self._count_attempt_update()
        else:
            if (
                state == STATE_APPROVED
                and self.user is not None
                and not self.user.has_perm("unit.review", unit)
            ):
                return
            # Ensure deferred changes accumulate on the right Translation instance
            unit.translation = self.translation
            unit.is_batch_update = True
            unit.translate(
                user or self.user,
                target,
                state,
                change_action=ActionEvents.AUTO,
                propagate=False,
                select_for_update=self.mode == "judge",
            )
            self.updated += 1
            self._count_attempt_update()

    def _count_attempt_update(self) -> None:
        """Add one stored unit to the attempt-local counter."""
        if self.batch_counter is not None:
            self.batch_counter.updated += 1

    def post_process(self) -> None:
        if self.updated > 0:
            self.translation.log_info("finalizing automatic translation")
            self.translation.store_update_changes()
            if not self.component_wide:
                self.translation.component.run_batched_checks()
            self.translation.invalidate_cache()
            if self.user:
                self.user.profile.increase_count("translated", self.updated)

    def collect_other_translations(
        self, filtered_sources, component_ids: list[int]
    ) -> dict[str, list[str]]:
        """Collect candidate translations while preserving source priority."""
        translations: dict[str, list[str]] = {}
        mismatched_translation_ids: set[int] = set()
        target_plural_id = self.translation.plural_id

        if component_ids:
            component_priority = {
                component_id: index for index, component_id in enumerate(component_ids)
            }
            translation_priority: dict[str, int] = {}
            source_units = (
                filtered_sources.annotate(
                    component_priority=Case(
                        *[
                            When(
                                translation__component_id=component_id,
                                then=priority,
                            )
                            for component_id, priority in component_priority.items()
                        ],
                        output_field=IntegerField(),
                    )
                )
                .order_by("component_priority", "translation_id")
                .values_list(
                    "translation__component_id",
                    "source",
                    "target",
                    "translation_id",
                    "translation__plural_id",
                )
            )
            for (
                component_id,
                source,
                target,
                translation_id,
                plural_id,
            ) in source_units:
                if plural_id != target_plural_id and (
                    is_plural(source) or is_plural(target)
                ):
                    mismatched_translation_ids.add(translation_id)
                    continue
                priority = component_priority[component_id]
                if priority >= translation_priority.get(source, len(component_ids)):
                    continue
                translations[source] = split_plural(target)
                translation_priority[source] = priority
        else:
            source_units = filtered_sources.values_list(
                "source", "target", "translation_id", "translation__plural_id"
            ).order_by("translation_id")
            for source, target, translation_id, plural_id in source_units:
                if plural_id != target_plural_id and (
                    is_plural(source) or is_plural(target)
                ):
                    mismatched_translation_ids.add(translation_id)
                    continue
                translations.setdefault(source, split_plural(target))

        mismatched_components = (
            Component.objects.filter(translation__in=mismatched_translation_ids)
            .defer_huge()
            .prefetch()
            .distinct()
            .order_project()
        )
        for component in mismatched_components:
            self.add_warning(
                gettext(
                    "Plural forms in %(component)s do not match the target translation. "
                    "Automatic translation skipped pluralized strings and processed only single-form strings."
                )
                % {"component": component}
            )

        return translations

    @transaction.atomic
    def process_others(self, source_component_ids: list[int] | None) -> None:
        """Perform automatic translation based on other components."""
        sources = Unit.objects.filter(
            translation__language=self.translation.language,
            state__gte=STATE_TRANSLATED,
        )
        # Read-only units can have STATE_READONLY even when their target is
        # empty, so state__gte=STATE_TRANSLATED is not enough to find usable
        # translations. The lower-MD5 lookup matches the trans_unit_target_md5
        # index and keeps this exclusion cheap on large components.
        sources = sources.exclude(target__lower__md5=MD5(Value("")))
        source_language = self.translation.component.source_language
        component_ids = list(dict.fromkeys(source_component_ids or []))
        if component_ids:
            components = list(Component.objects.filter(id__in=component_ids))
            component_map = {component.id: component for component in components}
            if len(component_map) != len(component_ids):
                raise Component.DoesNotExist

            for component_id in component_ids:
                component = component_map[component_id]
                if not self.allow_non_shared_tm_source_components and (
                    not component.project.contribute_shared_tm
                    and component.project != self.translation.component.project
                ):
                    msg = "Project has disabled contribution to shared translation memory."
                    raise PermissionDenied(msg)
                if component.source_language != source_language:
                    msg = "Component have different source languages."
                    raise PermissionDenied(msg)
            sources = sources.filter(translation__component_id__in=component_ids)
        else:
            project = self.translation.component.project
            sources = sources.filter(
                translation__component__project=project,
                translation__component__source_language=source_language,
            ).exclude(translation=self.translation)

        # Use memory_db for the query in case it exists. This is supposed
        # to be a read-only replica for offloading expensive translation
        # queries.
        if "memory_db" in settings.DATABASES:
            sources = sources.using("memory_db")

        # Get source MD5s
        source_md5s = list(
            self.get_units()
            .annotate(source__lower__md5=MD5(Lower("source")))
            .values_list("source__lower__md5", flat=True)
        )

        # Fetch available translations
        filtered_sources = sources.filter(source__lower__md5__in=source_md5s)
        translations = self.collect_other_translations(filtered_sources, component_ids)

        # Fetch translated unit IDs
        # Cannot use get_units() directly as SELECT FOR UPDATE cannot be used with JOIN
        unit_ids = list(
            self.get_units()
            .filter(
                source__lower__md5__in=[
                    MD5(Lower(Value(translation))) for translation in translations
                ]
            )
            .values_list("id", flat=True)
        )
        units = (
            Unit.objects.filter(pk__in=unit_ids)
            .prefetch()
            .prefetch_bulk()
            .select_for_update()
        )
        self.progress_steps = len(units)

        for pos, unit in enumerate(units):
            # Get update
            try:
                target = translations[unit.source]
            except KeyError:
                # Happens due to case-insensitive lookup
                continue

            self.set_progress(pos)

            # No save if translation is same or unit does not exist
            if unit.state == self.target_state and unit.target == target:
                continue
            # Copy translation
            self.update(unit, self.target_state, target)

        self.post_process()

    def fetch_mt(
        self,
        engines_list: list[str],
        threshold: int,
        on_batch: Callable[[list[Unit]], None] | None = None,
        on_failure: Callable[[MachineryBatchOutcome], None] | None = None,
    ) -> dict[int, UnitMemoryResultDict]:
        """Get the translations."""
        units: list[Unit] = list(self.get_units().select_related("source_unit"))
        num_units = len(units)
        if not num_units:
            return {}

        machinery_settings = self.translation.component.project.get_machinery_settings()

        engines: list[BatchMachineTranslation] = sorted(
            (
                MACHINERY[engine](setting)
                for engine, setting in machinery_settings.items()
                if engine in MACHINERY and engine in engines_list
            ),
            key=lambda engine: engine.get_rank(),
            reverse=True,
        )
        if num_units and not engines:
            if engines_list:
                self.add_warning(
                    gettext(
                        "The selected machine translation engines (%(engines)s) are "
                        "not configured for this project."
                    )
                    % {"engines": ", ".join(sorted(engines_list))}
                )
            else:
                self.add_warning(
                    gettext(
                        "No machine translation engine was selected, so no strings "
                        "were machine translated."
                    )
                )
        run_id = str(self.producer_run.pk) if self.producer_run is not None else None
        for engine in engines:
            engine.usage_run_id = run_id

        # With a single service each batch is fetched and stored in one step,
        # so the bar counts strings once; otherwise fetching fills its first
        # half and storing the second.
        incremental = on_batch is not None and len(engines) == 1
        self.progress_base = 0 if incremental else len(engines) * num_units
        # Estimate number of strings to translate, this is adjusted in process_mt
        self.progress_steps = self.progress_base + num_units

        translations = fetch_machinery_matches(
            units=units,
            user=self.user,
            services=engines,
            threshold=threshold,
            set_progress=self.set_progress,
            log_translation=self.translation,
            on_batch=on_batch,
            on_failure=on_failure,
        )
        for engine in engines:
            if not engine.is_rate_limited():
                continue
            self.translation.log_error(
                "%s is rate limited, some strings were left untranslated",
                engine.name,
            )
            self.add_warning(
                gettext(
                    "%(service)s refused further requests, so some strings were "
                    "left untranslated. Try again later."
                )
                % {"service": engine.name}
            )
        if not incremental:
            self.set_progress(self.progress_base)
        return translations

    def fetch_mt_checked(
        self,
        engines_list: list[str],
        threshold: int,
        on_batch: Callable[[list[Unit]], None] | None = None,
    ) -> tuple[dict[int, UnitMemoryResultDict], list[MachineryBatchOutcome]]:
        """Fetch translations and report every classified batch failure."""
        failures: list[MachineryBatchOutcome] = []

        def observe(outcome: MachineryBatchOutcome) -> None:
            failures.append(outcome)
            if outcome.reason_code == (
                MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED
            ):
                self.add_warning(
                    gettext(
                        "%(service)s refused the request: the translation quota "
                        "is exhausted. Ask the administrator to top it up, then "
                        "run the operation again."
                    )
                    % {"service": outcome.service}
                )
            elif outcome.reason_code == (
                MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT
            ):
                self.add_warning(
                    gettext(
                        "%(service)s refused the request: the credit balance "
                        "is spent. Ask the administrator to top it up, then "
                        "run the operation again."
                    )
                    % {"service": outcome.service}
                )
            elif outcome.reason_code is not None:
                self.add_warning(
                    gettext("%(service)s failed: %(reason)s")
                    % {"service": outcome.service, "reason": outcome.error or ""}
                )

        translations = self.fetch_mt(
            engines_list,
            threshold,
            on_batch=on_batch,
            on_failure=observe,
        )
        return translations, failures

    def process_mt(
        self, engines: list[str], threshold: int, *, empty_only: bool = False
    ) -> None:
        """Perform automatic translation based on machine translation."""
        if empty_only and not self.overwrite_existing:
            # Mandatory pre-judge preparation writes only units still missing
            # at store time; a human's concurrent write is never overwritten.
            if not self.unit_ids:
                self.post_process()
                return
            store_unit_ids = set(self.unit_ids)
            # Capture the sources this fetch is based on; store_results
            # refuses an answer whose source changed in between (C5).
            self.preparation_source_hashes = {
                unit.pk: compute_context_hash(
                    source=unit.source,
                    note="",
                    explanation="",
                    glossary_terms=(),
                )
                for unit in self.get_units()
                .filter(pk__in=store_unit_ids)
                .select_related("source_unit")
            }
            self.preparation_write_lock = True
            try:
                translations, failures = self.fetch_mt_checked(engines, int(threshold))
                translations = {
                    unit_id: result
                    for unit_id, result in translations.items()
                    if unit_id in store_unit_ids
                }
                if translations:
                    self.store_results(translations)
            finally:
                self.preparation_write_lock = False
            self.progress_steps = self.progress_base + len(translations)
            self.post_process()
            fatal = next(
                (
                    outcome
                    for outcome in failures
                    if outcome.reason_code
                    in {
                        MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
                        MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT,
                        MachineTranslationServiceError.REASON_AUTHENTICATION,
                        MachineTranslationServiceError.REASON_PERMISSION,
                    }
                ),
                None,
            )
            if fatal is not None and self.failure_message is None:
                self.failure_message = (
                    gettext("Automatic translation failed: %s") % fatal.error
                )
                if fatal.error not in self.warnings:
                    self.add_warning(self.failure_message)
            return

        translations, failures = self.fetch_mt_checked(
            engines, int(threshold), on_batch=self.store_batch
        )

        # Adjust total number to show correct progress
        self.progress_steps = self.progress_base + len(translations)

        # Anything a batch callback did not cover, for instance when several
        # services were queried.
        remaining = {
            unit_id: result
            for unit_id, result in translations.items()
            if unit_id not in self.written
        }
        if remaining:
            self.store_results(remaining)
        self.post_process()
        fatal = next(
            (
                outcome
                for outcome in failures
                if outcome.reason_code
                in {
                    MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
                    MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT,
                    MachineTranslationServiceError.REASON_AUTHENTICATION,
                    MachineTranslationServiceError.REASON_PERMISSION,
                }
            ),
            None,
        )
        if fatal is not None and self.failure_message is None:
            # A confirmed refusal is an operation failure even when earlier
            # batches stored translations: the completion message must not
            # claim success while strings were left untranslated.
            self.failure_message = (
                gettext("Automatic translation failed: %s") % fatal.error
            )
            if fatal.error not in self.warnings:
                self.add_warning(self.failure_message)

    def store_batch(self, units: list[Unit]) -> None:
        """Store one fetched batch so a crash cannot discard the whole run."""
        results = {
            unit.id: unit.machinery
            for unit in units
            if unit.machinery and any(unit.machinery["quality"])
        }
        if results:
            self.store_results(results)

    def store_results(self, translations: dict[int, UnitMemoryResultDict]) -> None:
        with transaction.atomic():
            self.translation.log_info("updating %d strings", len(translations))
            for unit in (
                self.translation.unit_set.filter(id__in=translations.keys())
                .select_for_update()
                .prefetch_bulk()
            ):
                translation: UnitMemoryResultDict = translations[unit.pk]
                # Use first existing origin for user
                # (there can be blanks for missing plurals)
                user: User | None = None
                for origin in translation["origin"]:
                    if origin is not None:
                        user = origin.user
                        break
                if self.preparation_write_lock:
                    # Empty-only preparation: re-read under the row lock and
                    # never overwrite a target a human wrote since the fetch.
                    # The skipped unit is not counted as written. prefetch_bulk
                    # populated related caches, not the row's own target.
                    fresh = (
                        type(unit)
                        .objects.filter(pk=unit.pk)
                        .values_list("target", flat=True)
                        .first()
                    )
                    source_unchanged = (
                        self.preparation_source_hashes is None
                        or self.preparation_source_hashes.get(unit.pk)
                        == compute_context_hash(
                            source=unit.source,
                            note="",
                            explanation="",
                            glossary_terms=(),
                        )
                    )
                    if (
                        fresh is not None
                        and not any(form for form in split_plural(fresh))
                        and source_unchanged
                    ):
                        self.update(
                            unit,
                            self.target_state,
                            translation["translation"],
                            user=user,
                        )
                        self.written.add(unit.pk)
                else:
                    # Copy translation
                    self.update(
                        unit,
                        self.target_state,
                        translation["translation"],
                        user=user,
                    )
                    self.written.add(unit.pk)
            # Flush the deferred changes of this transaction; a later crash
            # then leaves stored translations with their history intact.
            self.translation.store_update_changes()
        self.set_progress(self.progress_base + len(self.written))

    def preview_judge_scope(self) -> tuple[JudgeScopePreview, list[Unit]]:
        """Return the ordered, capped judge scope used by execution."""
        validate_judge_configuration()
        if self.judge_uncapped or (
            self.producer_run is not None and self.producer_run.execution_version >= 1
        ):
            limit = self.judge_limit
        else:
            limit = (
                settings.JUDGE_MAX_UNITS_PER_RUN
                if self.judge_limit is None
                else self.judge_limit
            )
        units = (
            self.get_units().select_related("source_unit").order_by("position", "pk")
        )
        matched = units.count()
        selected = list(units[:limit]) if limit is not None else list(units)
        processed = len(selected)
        writable = sum(
            not unit.translated or self.overwrite_existing for unit in selected
        )
        initial_calls = judge_initial_request_count(processed)
        if initial_calls is None:
            raise JudgeError(gettext("The LLM judge is not configured."))
        return (
            JudgeScopePreview(
                matched=matched,
                processed=processed,
                remaining=matched - processed,
                writable=writable,
                initial_calls=initial_calls,
                worst_case_calls=initial_calls
                * (settings.JUDGE_MAX_REPAIR_ATTEMPTS + 1),
            ),
            selected,
        )

    def process_judge(  # ruff: ignore[too-many-locals, too-many-statements, complex-structure]
        self, *, engines: list[str], threshold: int, prepare: bool = True
    ) -> None:
        preview, units = self.preview_judge_scope()
        self.judge_units_matched = preview.matched
        self.judge_units_processed = preview.processed
        self.judge_units_remaining = preview.remaining
        if not units:
            self.judge_summary = JudgeSummary(cap_remainder=preview.remaining)
            return
        if (
            self.overwrite_existing
            and self.producer_run is not None
            and not prepare
            and self.producer_run.preparation_phase == "ready"
        ):
            # A batch judge run whose mandatory preparation already ran never
            # overwrites existing text; an overwrite request belongs to a
            # plain MT run. Direct process_judge callers and runs whose
            # preparation never started keep the historical behavior.
            raise JudgeError(
                gettext("Overwrite cannot be combined with the judge mode.")
            )
        if self.producer_run is not None:
            # A redelivered attempt (acks_late/reject_on_worker_lost) must
            # neither lose a unit's already-recorded outcome nor pay for it
            # again (Task 5a, B4): a PENDING row reserves every unit up
            # front, and a non-PENDING row from an earlier delivery
            # excludes it here.
            already_done = set(
                JudgeRunUnit.objects.filter(
                    run=self.producer_run,
                    unit_id_snapshot__in=[unit.id for unit in units],
                )
                .exclude(outcome=JudgeRunUnit.Outcome.PENDING)
                .values_list("unit_id_snapshot", flat=True)
            )
            if already_done:
                units = [unit for unit in units if unit.id not in already_done]
                if not units:
                    self.judge_summary = JudgeSummary(cap_remainder=preview.remaining)
                    return
            # Up-front reservation for this delivery's judge units: an
            # existing PENDING or terminal row is left untouched.
            for unit in units:
                request = build_request(unit)
                JudgeRunUnit.objects.get_or_create(
                    run=self.producer_run,
                    unit_id_snapshot=unit.id,
                    defaults={
                        "unit": unit,
                        "translation_id": unit.translation_id,
                        "component_id": unit.translation.component_id,
                        "project_id": unit.translation.component.project_id,
                        "input_target": unit.get_target_plurals(),
                        "input_target_hash": compute_target_hash(
                            unit.get_target_plurals()
                        ),
                        "context_hash": compute_context_hash(
                            source=request.source,
                            note=request.note,
                            explanation=request.explanation,
                            glossary_terms=request.glossary_terms,
                            clarification=request.clarification,
                        ),
                        "outcome": JudgeRunUnit.Outcome.PENDING,
                    },
                )
        writable_ids = {
            unit.id for unit in units if not unit.translated or self.overwrite_existing
        }
        if not self.judge_pretranslate or self.judge_proposal_only or not prepare:
            # A proposal-only run judges the currently stored text and cannot
            # reach either the MT or state-mutating repair paths; a batch
            # judge run receives its preparation from the outer global pass.
            writable_ids = set()

        # Phase 1: pre-translate writable strings through the native MT path.
        base_low, base_high = self.progress_range
        split = base_low + (base_high - base_low) // 10
        if writable_ids:
            saved_ids, saved_state, saved_range = (
                self.unit_ids,
                self.target_state,
                self.progress_range,
            )
            self.unit_ids = list(writable_ids)
            self.target_state = self.fresh_translation_state
            self.progress_range = (base_low, split)
            try:
                self.process_mt(engines, threshold)
            finally:
                self.unit_ids, self.target_state, self.progress_range = (
                    saved_ids,
                    saved_state,
                    saved_range,
                )

        order = Case(
            *[When(pk=unit.pk, then=index) for index, unit in enumerate(units)],
            output_field=IntegerField(),
        )
        units = list(
            self.translation.unit_set.filter(pk__in=[unit.pk for unit in units])
            .annotate(scope_order=order)
            .order_by("scope_order")
            .prefetch()
            .prefetch_source()
        )
        untranslated_units: list[Unit] = []
        judge_units: list[Unit] = []
        for unit in units:
            if any(unit.get_target_plurals()):
                judge_units.append(unit)
            else:
                untranslated_units.append(unit)
        units = judge_units
        writable_ids.intersection_update(unit.id for unit in units)
        if untranslated_units:
            if self.producer_run is not None:
                self._record_skipped_judge_units(
                    self.producer_run,
                    untranslated_units,
                    JudgeRunUnit.SkipReason.UNTRANSLATED,
                )
            self.add_warning(
                ngettext(
                    "%d string had no translation to judge.",
                    "%d strings had no translation to judge.",
                    len(untranslated_units),
                )
                % len(untranslated_units)
            )

        self.progress_range = (split, base_high)
        self.progress_steps = preview.worst_case_calls
        if not units:
            try:
                self.set_progress(self.progress_steps)
            finally:
                self.progress_range = (base_low, base_high)
            self.judge_summary = _summarize_verdicts(
                {},
                cap_remainder=preview.remaining,
                untranslated=len(untranslated_units),
            )
            self.post_process()
            return

        completed_batches = 0
        initial_calls = preview.initial_calls

        def tick(_requests, _results) -> None:
            nonlocal completed_batches
            completed_batches += 1
            phase, phase_current, phase_total = _judge_phase(
                completed_batches,
                initial_calls=initial_calls,
                worst_case_calls=preview.worst_case_calls,
            )
            self.set_progress(
                min(completed_batches, self.progress_steps),
                phase=phase,
                phase_current=phase_current,
                phase_total=phase_total,
            )

        self.progress_range = (split, base_high)
        self.progress_steps = preview.worst_case_calls
        input_targets = {unit.id: unit.get_target_plurals() for unit in units}
        try:
            run_kwargs = {} if self.producer_run is None else {"run": self.producer_run}
            verdicts = run_judge_batch(
                units,
                writable_ids=writable_ids,
                user=self.user,
                on_batch=tick,
                candidate_severities=self.judge_candidate_severities,
                mutating_repairs=self.judge_mutating_repairs
                and not self.judge_proposal_only,
                **run_kwargs,
            )
            if completed_batches < self.progress_steps:
                self.set_progress(self.progress_steps)
        finally:
            self.progress_range = (base_low, base_high)
        for language_code in sorted(
            getattr(verdicts, "unsupported_repair_languages", set())
        ):
            self.add_warning(
                gettext(
                    "No repair engine is configured for %(language)s; strings "
                    "needing a repair candidate in this language were left "
                    "without one."
                )
                % {"language": language_code}
            )
        self.judge_summary = _summarize_verdicts(
            verdicts,
            cap_remainder=preview.remaining,
            untranslated=len(untranslated_units),
        )
        final_snapshots = {
            unit.id: (unit.target, unit.state) for unit in units if unit.id in verdicts
        }
        projections: set[int] = set()
        stale_conflicts: set[int] = set()
        unparsed = 0
        for unit in (
            self.translation.unit_set.filter(pk__in=verdicts)
            .prefetch()
            .prefetch_source()
        ):
            verdict = verdicts.get(unit.id)
            if verdict is None:
                continue
            with transaction.atomic():
                locked = (
                    self.translation.unit_set.select_for_update()
                    .prefetch()
                    .prefetch_source()
                    .get(pk=unit.pk)
                )
                current = current_verdict(locked)
                if (
                    current is None
                    or current.pk != verdict.pk
                    or (locked.target, locked.state) != final_snapshots[unit.id]
                ):
                    stale_conflicts.add(unit.id)
                    continue
                if self.judge_proposal_only:
                    projections.add(unit.id)
                    continue
                if verdict.verdict == JudgeVerdict.Verdict.UNPARSED:
                    unparsed += 1
                state = state_for_verdict(
                    verdict.verdict,
                    enable_review=self.translation.enable_review,
                    may_approve=(
                        settings.JUDGE_MAY_APPROVE
                        and has_complete_current_evidence(locked, seats=JUDGE_SEATS)
                    ),
                    current_state=locked.state,
                )
                if (
                    locked.state != STATE_APPROVED
                    and verdict.verdict == JudgeVerdict.Verdict.PASS
                    and any(
                        check.name == "max-length" for check in locked.active_checks
                    )
                ):
                    # A repair-exhausted over-budget candidate must not ship
                    # just because the judge approved its content.
                    state = STATE_FUZZY
                if state is not None and locked.state != state:
                    self.update(locked, state, locked.get_target_plurals())
                projections.add(unit.id)
        if unparsed:
            self.add_warning(
                ngettext(
                    "%d string was left unjudged (the judge did not answer).",
                    "%d strings were left unjudged (the judge did not answer).",
                    unparsed,
                )
                % unparsed
            )
        self.post_process()
        if self.producer_run is not None:
            repair_status: dict[int, str] = getattr(verdicts, "repair_status", {})
            initial_severity: dict[int, str] = getattr(verdicts, "initial_severity", {})
            attempt_counts: dict[int, int] = getattr(verdicts, "attempt_counts", {})
            cached_unit_ids: set[int] = getattr(verdicts, "cached_unit_ids", set())
            for unit in units:
                verdict = verdicts.get(unit.id)
                if verdict is None:
                    request = build_request(unit)
                    JudgeRunUnit.objects.update_or_create(
                        run=self.producer_run,
                        unit_id_snapshot=unit.id,
                        defaults={
                            "unit": unit,
                            "translation_id": unit.translation_id,
                            "component_id": unit.translation.component_id,
                            "project_id": unit.translation.component.project_id,
                            "input_target": input_targets[unit.id],
                            "input_target_hash": compute_target_hash(
                                input_targets[unit.id]
                            ),
                            "context_hash": compute_context_hash(
                                source=request.source,
                                note=request.note,
                                explanation=request.explanation,
                                glossary_terms=request.glossary_terms,
                                clarification=request.clarification,
                            ),
                            "outcome": JudgeRunUnit.Outcome.UNPARSED,
                            "before_target": input_targets[unit.id],
                            "after_target": unit.get_target_plurals(),
                        },
                    )
                    continue
                severity_outcomes: dict[str, str] = {
                    JudgeVerdict.Severity.NONE: JudgeRunUnit.Outcome.PASSED,
                    JudgeVerdict.Severity.MINOR: JudgeRunUnit.Outcome.MINOR,
                    JudgeVerdict.Severity.MAJOR: JudgeRunUnit.Outcome.MAJOR,
                    JudgeVerdict.Severity.CRITICAL: JudgeRunUnit.Outcome.CRITICAL,
                }
                outcome = (
                    JudgeRunUnit.Outcome.STALE_CONFLICT
                    if unit.id in stale_conflicts
                    else (
                        JudgeRunUnit.Outcome.UNPARSED
                        if verdict.unparsed
                        else severity_outcomes[verdict.effective_severity]
                    )
                )
                JudgeRunUnit.objects.update_or_create(
                    run=self.producer_run,
                    unit_id_snapshot=unit.id,
                    defaults={
                        "unit": unit,
                        "translation_id": unit.translation_id,
                        "component_id": unit.translation.component_id,
                        "project_id": unit.translation.component.project_id,
                        "input_target": input_targets[unit.id],
                        "input_target_hash": compute_target_hash(
                            input_targets[unit.id]
                        ),
                        "context_hash": verdict.context_hash,
                        "verdict": verdict,
                        "outcome": outcome,
                        "repair_status": repair_status.get(
                            unit.id, JudgeRunUnit.RepairStatus.NOT_ATTEMPTED
                        ),
                        "initial_severity": initial_severity.get(
                            unit.id, verdict.effective_severity
                        ),
                        "final_severity": verdict.effective_severity,
                        "attempt_count": attempt_counts.get(
                            unit.id, verdict.attempt + 1
                        ),
                        "before_target": input_targets[unit.id],
                        "after_target": unit.get_target_plurals(),
                        "cached": unit.id in cached_unit_ids,
                        "projection_succeeded": unit.id in projections,
                    },
                )

    def _dispatch(
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
        prepare: bool = True,
    ) -> None:
        if self.mode == "judge":
            self.process_judge(engines=engines, threshold=threshold, prepare=prepare)
        elif auto_source == "mt":
            self.process_mt(engines, threshold)
        else:
            self.process_others(source_component_ids)

    def perform(
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
        prepare: bool = True,
    ) -> str:
        translation = self.translation
        self.failure_message = None
        translation.log_info(
            "starting automatic translation (%s) %s: %s: %s",
            self.mode,
            current_task.request.id if current_task and current_task.request.id else "",
            auto_source,
            ", ".join(engines)
            if engines
            else ", ".join(str(item) for item in source_component_ids or []),
        )
        try:
            self._dispatch(
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                source_component_ids=source_component_ids,
                prepare=prepare,
            )
        except (JudgeError, MachineTranslationError, Component.DoesNotExist) as error:
            translation.log_error("failed automatic translation: %s", error)
            self.failure_message = gettext("Automatic translation failed: %s") % error
            self.add_warning(self.failure_message)
            return self.failure_message

        if self.failure_message is not None:
            # A confirmed refusal recorded by the dispatch (for instance a
            # spent MT quota) is an operation failure even when earlier
            # batches stored translations.
            translation.log_error(
                "failed automatic translation: %s", self.failure_message
            )
            return self.failure_message

        translation.log_info("completed automatic translation")

        return self.get_message()


class BatchAutoTranslate(BaseAutoTranslate):
    translations: QuerySet[Translation] | Sequence[Translation]

    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        obj: Translation | Component | Category | Project | ProjectLanguage | Workspace,
        *,
        user: User | None,
        q: str,
        mode: str,
        component_wide: bool = False,
        unit_ids: list[int] | None = None,
        allow_non_shared_tm_source_components: bool = False,
        enforce_permissions: bool = True,
        overwrite_existing: bool = False,
        producer_run_id: str | None = None,
        judge_pretranslate: bool = True,
        judge_mutating_repairs: bool = True,
        judge_candidate_severities: tuple[str, ...] = DEFAULT_CANDIDATE_SEVERITIES,
        judge_proposal_only: bool = False,
    ) -> None:
        super().__init__(
            user=user,
            q=q,
            mode=mode,
            component_wide=component_wide,
            unit_ids=unit_ids,
            allow_non_shared_tm_source_components=(
                allow_non_shared_tm_source_components
            ),
        )
        self._task_meta: dict[str, Any] = {}
        self.workspace_source_component_ids: dict[int, list[int]] | None = None
        self.enforce_permissions = enforce_permissions
        self.overwrite_existing = overwrite_existing
        self.producer_run_id = producer_run_id
        self.judge_pretranslate = judge_pretranslate
        self.judge_mutating_repairs = judge_mutating_repairs
        self.judge_candidate_severities = judge_candidate_severities
        self.judge_proposal_only = judge_proposal_only
        self.judge_scope = obj
        # Attempt-local counter for the user-facing `done/total`; created in
        # _perform so every delivery attempt (including a redelivered one)
        # starts from a fresh snapshot of the remaining units.
        self._attempt_counter = _AttemptCounter()
        self.batch_counter = self._attempt_counter
        self.active_producer_run: ProducerRun | None = None
        # Judge tally carried over from earlier continuation chunks. It is
        # filled in ``_perform`` from the run's durable summary; a batch that
        # never gets there (a direct ``_finish_producer_run`` caller, the
        # legacy-snapshot refusal) has nothing to carry.
        self._chunk_prior_summary: dict[str, int] | None = None

        match obj:
            case Translation():
                self.translations = [obj]
                self._task_meta = {"translation": obj.pk}
            case Component():
                self.translations = obj.translation_set.select_related(
                    "language"
                ).exclude_source()
                self._task_meta = {"component": obj.pk}
            case Category():
                self.translations = (
                    Translation.objects.filter(component__category=obj)
                    .select_related("language", "component", "component__project")
                    .exclude_source()
                )
                self._task_meta = {"category": obj.pk}
            case Project():
                self.translations = (
                    Translation.objects.filter(component__project=obj)
                    .select_related("language", "component", "component__project")
                    .exclude_source()
                )
                self._task_meta = {"project": obj.pk}
            case ProjectLanguage():
                self.translations = list(
                    obj.action_translation_set.select_related("language")
                    .exclude_source()
                    .prefetch()
                )
                self._task_meta = {
                    "project": obj.project.pk,
                    "language": obj.language.pk,
                }
            case Workspace():
                components = Component.objects.filter(project__workspace=obj)
                if user is not None:
                    components = components.filter_access(user)
                self.translations = (
                    Translation.objects.filter(component__in=components)
                    .select_related("language", "component", "component__project")
                    .exclude_source()
                )
                source_component_ids: dict[int, list[int]] = {}
                for source_language_id, component_id in components.filter(
                    source_language_id__isnull=False
                ).values_list("source_language_id", "pk"):
                    source_component_ids.setdefault(source_language_id, []).append(
                        component_id
                    )
                self.workspace_source_component_ids = source_component_ids
                self.allow_non_shared_tm_source_components = True
                self._task_meta = {"workspace": str(obj.pk)}
            case _:  # pragma: no cover
                msg = "Unsupported object type for BatchAutoTranslate"
                raise ValueError(msg)
        if isinstance(self.translations, QuerySet):
            self.translations = self.translations.order_by(
                "component_id", "language_id", "pk"
            )
        else:
            self.translations = sorted(
                self.translations,
                key=lambda translation: (
                    translation.component_id,
                    translation.language_id,
                    translation.pk,
                ),
            )
        self._preload_workflow_settings()
        self.progress_steps = len(self.translations)

    def get_task_meta(self) -> dict[str, Any]:
        return self._task_meta

    def preview_judge_scope(self, *, execution_version: int = 1) -> JudgeScopePreview:
        """Aggregate the permission-filtered judge scope."""
        validate_judge_configuration()
        judge_uncapped = execution_version >= 1
        remaining = None if judge_uncapped else settings.JUDGE_MAX_UNITS_PER_RUN
        matched = processed = writable = initial_calls = worst_case_calls = 0
        for translation in self.translations:
            if not self._can_process_translation(translation):
                continue
            auto_translate = AutoTranslate(
                user=self.user,
                translation=translation,
                q=self.q,
                mode="judge",
                component_wide=self.component_wide,
                unit_ids=self.unit_ids,
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                overwrite_existing=self.overwrite_existing,
                judge_limit=remaining,
                judge_uncapped=judge_uncapped,
            )
            preview, _units = auto_translate.preview_judge_scope()
            matched += preview.matched
            processed += preview.processed
            writable += preview.writable
            initial_calls += preview.initial_calls
            worst_case_calls += preview.worst_case_calls
            if remaining is not None:
                # The cap is one shared budget, but the whole
                # permission-filtered scope still has to be counted: the
                # remainder is what the cap left unjudged, and stopping the
                # loop here would both hide it from the report and silence
                # the cap warning for later translations.
                remaining = max(0, remaining - preview.processed)
        return JudgeScopePreview(
            matched=matched,
            processed=processed,
            remaining=matched - processed,
            writable=writable,
            initial_calls=initial_calls,
            worst_case_calls=worst_case_calls,
        )

    def preview_judge_scope_snapshot(
        self, *, execution_version: int = 1
    ) -> tuple[JudgeScopePreview, list[Unit]]:
        """Return the ordered units behind a judge estimate."""
        validate_judge_configuration()
        judge_uncapped = execution_version >= 1
        remaining = None if judge_uncapped else settings.JUDGE_MAX_UNITS_PER_RUN
        selected: list[Unit] = []
        matched = writable = initial_calls = worst_case_calls = 0
        for translation in self.translations:
            if not self._can_process_translation(translation):
                continue
            preview, units = AutoTranslate(
                user=self.user,
                translation=translation,
                q=self.q,
                mode="judge",
                component_wide=self.component_wide,
                unit_ids=self.unit_ids,
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                overwrite_existing=self.overwrite_existing,
                judge_limit=remaining,
                judge_uncapped=judge_uncapped,
            ).preview_judge_scope()
            matched += preview.matched
            writable += preview.writable
            initial_calls += preview.initial_calls
            worst_case_calls += preview.worst_case_calls
            selected.extend(units)
            if remaining is not None:
                # See ``preview_judge_scope``: the budget is shared, the
                # counted scope is not truncated by it.
                remaining = max(0, remaining - preview.processed)
        return (
            JudgeScopePreview(
                matched=matched,
                processed=len(selected),
                remaining=matched - len(selected),
                writable=writable,
                initial_calls=initial_calls,
                worst_case_calls=worst_case_calls,
            ),
            selected,
        )

    def preview_mt_scope(self) -> MTScopePreview:
        rows: list[tuple[Translation, int]] = []
        matched = 0
        for translation in self.translations:
            if not self._can_process_translation(translation):
                continue
            units = AutoTranslate(
                user=self.user,
                translation=translation,
                q=self.q,
                mode=self.mode,
                component_wide=self.component_wide,
                unit_ids=self.unit_ids,
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                overwrite_existing=self.overwrite_existing,
            ).get_units()
            count = units.count()
            matched += count
            if count:
                rows.append((translation, count))
        return MTScopePreview(matched=matched, writable=matched, per_translation=rows)

    def build_preparation_scope(self) -> PreparationScope:
        """
        Fix the closed preparation scope before any paid call (C1/C2).

        Permission-filtered selected pairs come from ``get_units()`` over the
        original query, *before* the judge cap: a late language past the cap
        is still prepared, and a language without the direct-edit permission
        is a pre-flight blocker rather than a silent skip.
        """
        unit_ids: list[int] = []
        missing_ids: list[int] = []
        per_language_missing: dict[str, int] = {}
        engines: set[str] = set()
        for translation in self.translations:
            if not self._can_process_translation(translation):
                continue
            units = list(
                AutoTranslate(
                    user=self.user,
                    translation=translation,
                    q=self.q,
                    mode=self.mode,
                    component_wide=self.component_wide,
                    unit_ids=self.unit_ids,
                    allow_non_shared_tm_source_components=(
                        self.allow_non_shared_tm_source_components
                    ),
                    overwrite_existing=self.overwrite_existing,
                )
                .get_units()
                .select_related("source_unit")
            )
            missing = [unit for unit in units if not any(unit.get_target_plurals())]
            if (
                missing
                and self.user is not None
                and not bool(self.user.has_perm("meta:unit.direct_edit", translation))
            ):
                raise PermissionDenied(
                    gettext(
                        "Machine translation of missing strings in %(language)s "
                        "requires direct edit permission."
                    )
                    % {"language": translation.language.code}
                )
            unit_ids.extend(unit.pk for unit in units)
            missing_ids.extend(unit.pk for unit in missing)
            if missing:
                language_code = translation.language.code
                per_language_missing[language_code] = per_language_missing.get(
                    language_code, 0
                ) + len(missing)
                engine = self._preparation_engine_for(translation)
                if engine is None:
                    # No engine can fill this language; the preparation
                    # barrier blocks the judge phase with this warning, and
                    # estimate reports the blocker before any paid call.
                    self.add_warning(
                        gettext(
                            "No machine translation engine is configured for "
                            "%(language)s; missing strings cannot be prepared."
                        )
                        % {"language": language_code}
                    )
                else:
                    engines.add(engine)
        if len(engines) > 1:
            raise JudgeError(
                gettext(
                    "The preparation scope spans projects with different machine "
                    "translation engines; run each project separately."
                )
            )
        return PreparationScope(
            unit_ids=tuple(unit_ids),
            missing_ids=tuple(missing_ids),
            per_language_missing=per_language_missing,
            mt_engine=next(iter(engines), None),
        )

    @staticmethod
    def _preparation_engine_for(translation: Translation) -> str | None:
        """Return the routed MT engine a mandatory preparation would use, if any."""
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.forms import configured_routed_engine

        return configured_routed_engine(
            translation.component.project.get_machinery_settings()
        )

    # Mandatory pre-judge preparation over the whole selected scope (C3):
    # one pass fills the missing strings of every language before any judge
    # request, and a confirmed refusal or a remaining gap fails the run
    # before the judge phase starts.

    def _set_preparation_phase(self, run: ProducerRun, phase: str) -> None:
        run.preparation_phase = phase
        ProducerRun.objects.filter(pk=run.pk).update(preparation_phase=phase)

    def _finalize_preparation_failure(
        self,
        run: ProducerRun,
        scope: PreparationScope,
        failure: str,
    ) -> str:
        """Record a failed preparation and skip every reserved judge row."""
        self.failure_message = failure
        ProducerRun.objects.filter(pk=run.pk).update(
            preparation_phase="blocked",
            failure=failure,
        )
        blocked_units = list(
            Unit.objects.filter(pk__in=scope.unit_ids).select_related(
                "translation", "translation__component"
            )
        )
        self._record_skipped_judge_units(
            run, blocked_units, JudgeRunUnit.SkipReason.MT_PREREQUISITE
        )
        JudgeRunUnit.objects.filter(
            run=run, outcome=JudgeRunUnit.Outcome.PENDING
        ).update(
            outcome=JudgeRunUnit.Outcome.SKIPPED,
            skip_reason=JudgeRunUnit.SkipReason.MT_PREREQUISITE,
        )
        return failure

    def _find_incomplete_units(self, scope: PreparationScope) -> dict[str, list[int]]:
        """Plural strings in scope with only some forms filled."""
        incomplete: dict[str, list[int]] = {}
        for unit in Unit.objects.filter(pk__in=scope.unit_ids).select_related(
            "translation", "translation__component"
        ):
            if unit_target_incomplete(unit):
                incomplete.setdefault(unit.translation.language.code, []).append(
                    unit.pk
                )
        return incomplete

    def _run_preparation(
        self,
        run: ProducerRun,
        scope: PreparationScope,
        threshold: int,
        engines: list[str] | None = None,
    ) -> str | None:
        """
        Fill the scope's missing strings, or return a failure message.

        Returns ``None`` when every selected string is ready to be judged.
        A ``ready`` row in the database is never trusted blindly: the closed
        scope is re-read and any *remaining* gap still blocks.
        """
        remaining = Unit.objects.filter(pk__in=scope.missing_ids).select_related(
            "translation", "translation__component"
        )
        # A human write since the snapshot satisfies the requirement without
        # another paid call (C5: crash/redelivery never pays twice).
        still_missing: dict[int, list[int]] = {}
        for unit in remaining:
            if unit_missing_translation(unit):
                still_missing.setdefault(unit.translation_id, []).append(unit.pk)
        if not still_missing:
            incomplete = self._find_incomplete_units(scope)
            if incomplete:
                return self._finalize_preparation_failure(
                    run,
                    scope,
                    gettext(
                        "Judges were not started: %(count)d strings have only "
                        "some of their plural forms filled. Fill them manually "
                        "and start again."
                    )
                    % {"count": sum(len(ids) for ids in incomplete.values())},
                )
            self._set_preparation_phase(run, "ready")
            return None

        self._set_preparation_phase(run, "preparing")

        translations_by_id = {
            translation.pk: translation for translation in self.translations
        }
        for translation_id, missing_unit_ids in still_missing.items():
            translation = translations_by_id.get(translation_id)
            if translation is None:
                # The unit's translation left the scope after the snapshot:
                # an explicit scope change, not a reason to call it ready.
                return self._finalize_preparation_failure(
                    run,
                    scope,
                    gettext(
                        "Judges were not started: the scope changed while the "
                        "run was waiting. Start a new run."
                    ),
                )
            auto_translate = AutoTranslate(
                user=self.user,
                translation=translation,
                q=self.q,
                mode=self.mode,
                component_wide=self.component_wide,
                unit_ids=list(missing_unit_ids),
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                producer_run=run,
            )
            engine_ids = [engine for engine in (engines or []) if engine]
            if scope.mt_engine and scope.mt_engine not in engine_ids:
                engine_ids.append(scope.mt_engine)
            auto_translate.process_mt(
                engine_ids,
                threshold,
                empty_only=True,
            )
            self.updated += auto_translate.updated
            for warning in auto_translate.get_warnings():
                self.add_warning(warning)
            if auto_translate.failure_message is not None:
                return self._finalize_preparation_failure(
                    run,
                    scope,
                    gettext(
                        "Judges were not started. Machine translation stopped: "
                        "%(reason)s Ask the administrator, then start a new run."
                    )
                    % {"reason": auto_translate.failure_message},
                )
            if _run_cancel_requested(run):
                # G4: the operator asked to stop between preparation
                # batches. Already stored MT stays; the finalize path maps
                # the requested state to cancelled/partial.
                return None

        # Global barrier: re-read every missing id; only a fully ready scope
        # opens the judge phase (C3). A scope with no usable engine is never
        # "ready": the judge evaluates whatever text exists, and missing
        # strings are honestly skipped per unit, so the leftover check does
        # not block a run that could not have paid for preparation.
        leftover = [
            unit.pk
            for unit in Unit.objects.filter(pk__in=scope.missing_ids)
            if unit_missing_translation(unit)
        ]
        incomplete = self._find_incomplete_units(scope)
        if (leftover or incomplete) and scope.mt_engine is not None:
            return self._finalize_preparation_failure(
                run,
                scope,
                gettext(
                    "Judges were not started: %(missing)d strings still have no "
                    "translation%(incomplete)s. Review the warnings, then start "
                    "a new run."
                )
                % {
                    "missing": len(leftover),
                    "incomplete": (
                        ""
                        if not incomplete
                        else gettext(
                            " and %(count)d strings have only some plural forms filled"
                        )
                        % {"count": sum(len(ids) for ids in incomplete.values())}
                    ),
                },
            )
        self._set_preparation_phase(run, "ready")
        return None

    def _preload_workflow_settings(self) -> None:
        self.translations = list(self.translations)
        projects: dict[int, Project] = {}
        project_languages: dict[int, dict[int, ProjectLanguage]] = {}

        for translation in self.translations:
            project = translation.component.project
            project = projects.setdefault(project.pk, project)
            languages = project_languages.setdefault(project.pk, {})
            if translation.language_id not in languages:
                languages[translation.language_id] = ProjectLanguage(
                    project, translation.language
                )

        for project_id, languages in project_languages.items():
            projects[project_id].project_languages.preload_workflow_settings(
                languages.values()
            )

        for translation in self.translations:
            translation.__dict__["workflow_settings"] = project_languages[
                translation.component.project_id
            ][translation.language_id].workflow_settings

    def _count_eligible_units(self) -> int:
        """Total units matching the run's permission-filtered query."""
        total = 0
        for translation in self.translations:
            if not self._can_process_translation(translation):
                continue
            total += (
                AutoTranslate(
                    user=self.user,
                    translation=translation,
                    q=self.q,
                    mode=self.mode,
                    component_wide=self.component_wide,
                    unit_ids=self.unit_ids,
                    allow_non_shared_tm_source_components=(
                        self.allow_non_shared_tm_source_components
                    ),
                    overwrite_existing=self.overwrite_existing,
                )
                .get_units()
                .count()
            )
        return total

    def _producer_execution_version(self) -> int:
        """
        Return the execution contract of the run this dispatch belongs to.

        ``0`` is the historical capped contract, ``1`` the full-scope one.
        The dispatched task carries only a run id, so the version is read
        from the row itself; a batch with no run at all is a direct console
        call and keeps the historical cap.
        """
        if self.producer_run_id is None:
            return 0
        return (
            ProducerRun.objects.filter(pk=self.producer_run_id)
            .values_list("execution_version", flat=True)
            .first()
            or 0
        )

    def _adopt_producer_run(self) -> ProducerRun:
        """
        Claim the pre-created run for this dispatch, or create a fresh one.

        A producer one-unit re-check and a project-scoped console judge run
        both queue their run before dispatching this task, so the caller can
        surface a known run id immediately. The worker may only adopt a run
        whose recorded scope, purpose, and query match what the caller
        committed to; a mismatched run is failed and the task refuses it.
        Resuming a redelivered task (RUNNING with a matching ``task_id``)
        returns the same row, mirroring the loc-kit dispatch ledger's
        fencing by reserved task UUID.
        """
        if self.producer_run_id is None:
            return self._create_producer_run()
        task_id = (
            current_task.request.id if current_task and current_task.request.id else ""
        )
        superseded = False
        with transaction.atomic():
            claimed = (
                ProducerRun.objects.select_for_update()
                .filter(pk=self.producer_run_id)
                .first()
            )
            matches = claimed is not None and self._producer_run_request_matches(
                claimed
            )
            if matches and claimed.status in {
                ProducerRun.Status.COMPLETED,
                ProducerRun.Status.FAILED,
                ProducerRun.Status.CANCELLED,
                ProducerRun.Status.PARTIAL,
            }:
                return claimed
            if matches and claimed.status == ProducerRun.Status.CANCEL_REQUESTED:
                claimed.status = ProducerRun.Status.CANCELLED
                claimed.finished = timezone.now()
                claimed.save(update_fields=["status", "finished"])
                return claimed
            if claimed is not None and claimed.status not in {
                ProducerRun.Status.QUEUED,
                ProducerRun.Status.RUNNING,
            }:
                claimed = None
            if (
                matches
                and claimed is not None
                and claimed.status == ProducerRun.Status.RUNNING
            ):
                if task_id and (
                    claimed.task_id == task_id
                    or str(claimed.dispatch_task_id or "") == task_id
                ):
                    if claimed.task_id != task_id:
                        claimed.task_id = task_id
                        claimed.save(update_fields=["task_id"])
                    return claimed
                if task_id:
                    # The delivery names a generation other than the run's
                    # current one, so this worker is a stale redelivery: it
                    # must not continue, and the run is marked failed so it
                    # cannot stay RUNNING with nobody able to finish it.
                    superseded = True
                claimed = None
            if (
                matches
                and claimed is not None
                and claimed.status == ProducerRun.Status.QUEUED
            ):
                claimed.status = ProducerRun.Status.RUNNING
                claimed.started = timezone.now()
                if task_id:
                    claimed.task_id = task_id
                claimed.save(update_fields=["status", "started", "task_id"])
                return claimed
        if superseded:
            failed_run = ProducerRun.objects.filter(pk=self.producer_run_id).first()
            if failed_run is not None:
                failed_run.status = ProducerRun.Status.FAILED
                failed_run.finished = timezone.now()
                failed_run.failure = gettext(
                    "This delivery was superseded by another task generation."
                )
                failed_run.save(update_fields=["status", "finished", "failure"])
            msg = gettext("This delivery was superseded by another task generation.")
            raise ValueError(msg)
        # Both failure paths run outside the claim transaction so the FAILED
        # transition below is not rolled back by the exception that follows.
        if claimed is None:
            msg = gettext("The queued run is no longer available.")
            raise ValueError(msg)
        claimed.status = ProducerRun.Status.FAILED
        claimed.finished = timezone.now()
        claimed.failure = gettext("The request does not match the queued run.")
        claimed.save(update_fields=["status", "finished", "failure"])
        msg = claimed.failure
        raise ValueError(msg)

    def _judge_scope_ids(self) -> list[int]:
        """Return the judge scope in the exact per-translation order the pass uses."""
        ordered_ids: list[int] = []
        for translation in self.translations:
            auto_translate = AutoTranslate(
                user=self.user,
                translation=translation,
                q=self.q,
                mode=self.mode,
                component_wide=self.component_wide,
                unit_ids=self.unit_ids,
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                overwrite_existing=self.overwrite_existing,
            )
            ordered_ids.extend(
                auto_translate.get_units()
                .order_by("position", "pk")
                .values_list("pk", flat=True)
            )
        return ordered_ids

    def _apply_cap_skips(self, run: ProducerRun) -> None:
        """
        Record judge-cap skips for the closed scope once, globally.

        The judge scope is the first ``run.cap`` units of the closed
        preparation scope in the stable per-translation order; everything
        past it is SKIPPED with reason ``cap`` *before* any preparation MT
        runs, so a preparation failure never overwrites the more specific
        reason with ``mt-prerequisite``.
        """
        cap = run.cap if run.cap is not None else 0
        ordered_ids = self._judge_scope_ids()
        if len(ordered_ids) <= cap:
            return
        skipped = list(
            Unit.objects.filter(pk__in=ordered_ids[cap:]).select_related(
                "translation", "translation__component"
            )
        )
        self._record_skipped_judge_units(run, skipped, JudgeRunUnit.SkipReason.CAP)

    def _reserve_judge_rows(self, run: ProducerRun) -> None:
        """Create the up-front PENDING reservation for the judge scope."""
        cap = run.cap if run.cap is not None else 0
        # Same per-translation query order the judge pass uses.
        ordered_ids = self._judge_scope_ids()
        ordered = list(
            Unit.objects.filter(pk__in=ordered_ids[:cap]).select_related(
                "translation", "translation__component"
            )
        )
        ordered.sort(key=lambda unit: ordered_ids.index(unit.pk))
        for unit in ordered:
            request = build_request(unit)
            JudgeRunUnit.objects.get_or_create(
                run=run,
                unit_id_snapshot=unit.id,
                defaults={
                    "unit": unit,
                    "translation_id": unit.translation_id,
                    "component_id": unit.translation.component_id,
                    "project_id": unit.translation.component.project_id,
                    "input_target": unit.get_target_plurals(),
                    "input_target_hash": compute_target_hash(unit.get_target_plurals()),
                    "context_hash": compute_context_hash(
                        source=request.source,
                        note=request.note,
                        explanation=request.explanation,
                        glossary_terms=request.glossary_terms,
                        clarification=request.clarification,
                    ),
                    "outcome": JudgeRunUnit.Outcome.PENDING,
                },
            )

    def _producer_run_request_matches(self, run: ProducerRun) -> bool:
        """
        Whether a pre-created run matches this worker's own dispatch.

        A translation-scoped re-check is identified by its exact
        single-unit query alone: ``get_auto_translate_target`` always
        resolves the translation itself in production, but tests exercise
        the same claim through a wider dispatch object, so scope identity
        is not required for this kind. Every other producer-run kind is
        identified by its own recorded scope and query, matched against the
        object this worker actually received.
        """
        if (
            run.scope_type == ProducerRun.ScopeType.TRANSLATION
            and run.requested_mode == "recheck"
        ):
            expected_unit = (
                self.unit_ids[0] if self.unit_ids and len(self.unit_ids) == 1 else None
            )
            return (
                expected_unit is not None
                and run.requested_query == f"id:{expected_unit}"
            )
        try:
            scope_type = self._scope_type_for(self.judge_scope)
        except ValueError:
            return False
        return (
            run.scope_type == scope_type
            and run.scope_id == str(self.judge_scope.pk)
            and run.requested_query == self.q
        )

    @staticmethod
    def _scope_type_for(
        scope: Translation
        | Component
        | Category
        | Project
        | ProjectLanguage
        | Workspace,
    ) -> str:
        match scope:
            case Translation():
                return ProducerRun.ScopeType.TRANSLATION
            case Component():
                return ProducerRun.ScopeType.COMPONENT
            case Category():
                return ProducerRun.ScopeType.CATEGORY
            case Project():
                return ProducerRun.ScopeType.PROJECT
            case ProjectLanguage():
                return ProducerRun.ScopeType.PROJECT_LANGUAGE
            case Workspace():
                return ProducerRun.ScopeType.WORKSPACE
            case _:
                msg = gettext(
                    "A producer run requires a translation, component, category, "
                    "project, project language, or workspace"
                )
                raise ValueError(msg)

    def _create_producer_run(self) -> ProducerRun:
        task_id = (
            current_task.request.id if current_task and current_task.request.id else ""
        )
        if task_id:
            existing_runs = list(ProducerRun.objects.filter(task_id=task_id))
            if existing_runs:
                if len(existing_runs) != 1 or not self._producer_run_request_matches(
                    existing_runs[0]
                ):
                    msg = gettext("The task identity does not match the requested run.")
                    raise ValueError(msg)
                return existing_runs[0]

        scope = self.judge_scope
        scope_type = self._scope_type_for(scope)
        idempotency_key = f"celery:{task_id}" if task_id else ""
        create_kwargs = {
            "actor": self.user,
            "task_id": task_id,
            "started": timezone.now(),
            "scope_type": scope_type,
            "scope_id": str(scope.pk),
            "scope_label": str(scope),
            "scope_path": scope.get_absolute_url(),
            "requested_query": self.q,
            "requested_mode": self.mode,
            "cap": settings.JUDGE_MAX_UNITS_PER_RUN,
            "status": ProducerRun.Status.RUNNING,
            "configuration_snapshot": (
                judge_configuration_snapshot() if self.mode == "judge" else {}
            ),
            "idempotency_key": idempotency_key,
        }
        try:
            with transaction.atomic():
                return ProducerRun.objects.create(**create_kwargs)
        except IntegrityError as error:
            if not idempotency_key:
                raise
            existing = ProducerRun.objects.get(
                actor=self.user,
                scope_type=scope_type,
                scope_id=str(scope.pk),
                requested_mode=self.mode,
                idempotency_key=idempotency_key,
            )
            if not self._producer_run_request_matches(existing):
                msg = gettext("The task identity does not match the requested run.")
                raise ValueError(msg) from error
            return existing

    def _finish_producer_run(
        self,
        run: ProducerRun,
        status: ProducerRun.Status,
        failure: str = "",
    ) -> None:
        # A run is finalized exactly once: a second call (for example a
        # redundant exception handler further up the same call stack)
        # must never overwrite an already-terminal run.
        if run.status in {
            ProducerRun.Status.COMPLETED,
            ProducerRun.Status.FAILED,
            ProducerRun.Status.CANCELLED,
            ProducerRun.Status.PARTIAL,
        }:
            return
        if run.status == ProducerRun.Status.CANCEL_REQUESTED:
            # G4: a cancellation that landed after some rows were already
            # judged is a partial result, not a fully cancelled run -- the
            # producer still gets back whatever evidence was completed.
            status = (
                ProducerRun.Status.PARTIAL
                if JudgeRunUnit.objects.filter(run=run)
                .exclude(outcome=JudgeRunUnit.Outcome.PENDING)
                .exists()
                else ProducerRun.Status.CANCELLED
            )
        run.status = status
        run.finished = timezone.now()
        run.failure = failure
        # A failure path may have updated the phase directly in the
        # database (``_finalize_preparation_failure``); the in-memory copy
        # predates that update, so the summary must read the durable value.
        run.preparation_phase = (
            ProducerRun.objects.filter(pk=run.pk)
            .values_list("preparation_phase", flat=True)
            .first()
            or run.preparation_phase
        )
        summary = asdict(self.judge_summary or JudgeSummary())
        if run.requested_mode in {"judge", "recheck", "drain"}:
            outcomes = dict(
                JudgeRunUnit.objects.filter(run=run)
                .values_list("outcome")
                .annotate(count=Count("pk"))
            )
            summary.update(
                evaluated=sum(
                    count
                    for outcome, count in outcomes.items()
                    if outcome != JudgeRunUnit.Outcome.SKIPPED
                )
                + (
                    self._chunk_prior_summary["evaluated"]
                    if self._chunk_prior_summary
                    else 0
                ),
                nothing_blocking=outcomes.get(JudgeRunUnit.Outcome.PASSED, 0)
                + (
                    self._chunk_prior_summary["nothing_blocking"]
                    if self._chunk_prior_summary
                    else 0
                ),
                minor_noted=outcomes.get(JudgeRunUnit.Outcome.MINOR, 0)
                + (
                    self._chunk_prior_summary["minor_noted"]
                    if self._chunk_prior_summary
                    else 0
                ),
                major_not_fixed=outcomes.get(JudgeRunUnit.Outcome.MAJOR, 0)
                + (
                    self._chunk_prior_summary["major_not_fixed"]
                    if self._chunk_prior_summary
                    else 0
                ),
                critical_held=outcomes.get(JudgeRunUnit.Outcome.CRITICAL, 0)
                + (
                    self._chunk_prior_summary["critical_held"]
                    if self._chunk_prior_summary
                    else 0
                ),
                unparsed=outcomes.get(JudgeRunUnit.Outcome.UNPARSED, 0)
                + (
                    self._chunk_prior_summary["unparsed"]
                    if self._chunk_prior_summary
                    else 0
                ),
                untranslated=JudgeRunUnit.objects.filter(
                    run=run,
                    outcome=JudgeRunUnit.Outcome.SKIPPED,
                    skip_reason=JudgeRunUnit.SkipReason.UNTRANSLATED,
                ).count()
                + (
                    self._chunk_prior_summary["untranslated"]
                    if self._chunk_prior_summary
                    else 0
                ),
                cap_remainder=run.summary.get(
                    "cap_remainder", summary["cap_remainder"]
                ),
            )
        if run.requested_mode == "translate":
            # Aggregate LLM translation refusals so a run whose only signal
            # of failure lives in LLMUsageLog still surfaces it: a single-
            # string batch that was refused is terminal (the batch splitter
            # cannot halve one string), and a run where every translation
            # was refused is a partial outcome, not a clean COMPLETED.
            translation_usage = (
                LLMUsageLog.objects.filter(
                    run=run,
                    operation=LLMUsageLog.Operation.TRANSLATION,
                )
                .exclude(outcome="")
                .exclude(outcome=LLMUsageLog.Outcome.APPLIED)
            )
            has_applied = LLMUsageLog.objects.filter(
                run=run,
                operation=LLMUsageLog.Operation.TRANSLATION,
                outcome=LLMUsageLog.Outcome.APPLIED,
            ).exists()
            refusal_rows = (
                translation_usage.values_list("outcome", "refusal_reason")
                .order_by()
                .annotate(count=Count("pk"))
            )
            has_refused = any(
                row[0] == LLMUsageLog.Outcome.REFUSED for row in refusal_rows
            )
            for outcome, reason, count in refusal_rows:
                if outcome == LLMUsageLog.Outcome.REFUSED:
                    warning = ngettext(
                        "LLM translation refused: %(reason)s (%(count)d request).",
                        "LLM translation refused: %(reason)s (%(count)d requests).",
                        count,
                    ) % {"reason": reason, "count": count}
                else:
                    warning = ngettext(
                        "LLM translation partially refused: %(reason)s (%(count)d request).",
                        "LLM translation partially refused: %(reason)s (%(count)d requests).",
                        count,
                    ) % {"reason": reason, "count": count}
                if warning not in self.warnings:
                    self.warnings.append(warning)
            if (
                has_refused
                and not has_applied
                and run.status
                not in {
                    ProducerRun.Status.FAILED,
                    ProducerRun.Status.CANCELLED,
                    ProducerRun.Status.PARTIAL,
                }
                and status == ProducerRun.Status.COMPLETED
            ):
                status = ProducerRun.Status.PARTIAL
                run.status = status
        summary["written"] = self.updated
        if run.preparation_snapshot:
            # C5/C6: the preparation outcome survives finalization as its
            # own summary block, never merged into the judge tally.
            try:
                scope = PreparationScope.from_json(run.preparation_snapshot)
            except (TypeError, ValueError, KeyError):
                scope = None
            summary["mt_preparation"] = {
                "selected": len(scope.unit_ids) if scope else None,
                "missing_initial": len(scope.missing_ids) if scope else None,
                "written": self.updated,
                "remaining": len(
                    [
                        unit
                        for unit in Unit.objects.filter(
                            pk__in=(scope.missing_ids if scope else ())
                        )
                        if unit_missing_translation(unit)
                    ]
                )
                if scope
                else None,
                "per_language_remaining": (
                    dict(scope.per_language_missing) if scope else {}
                ),
                "phase": run.preparation_phase,
                "reason_code": (
                    "mt-prerequisite" if run.preparation_phase == "blocked" else ""
                ),
            }
        run.summary = summary
        run.warnings = self.get_warnings()
        run.save(update_fields=["status", "finished", "failure", "summary", "warnings"])

    def _can_process_translation(self, translation: Translation) -> bool:
        return not self.enforce_permissions or bool(
            check_auto_translate_permission(self.user, translation, self.mode)
        )

    def _finish_translation(
        self, auto_translate: AutoTranslate, judge_remaining: int | None
    ) -> int | None:
        if judge_remaining is not None:
            judge_remaining -= auto_translate.judge_units_processed
        if auto_translate.failure_message:
            self.failure_message = auto_translate.failure_message
        self.updated += auto_translate.updated
        for warning in auto_translate.get_warnings():
            self.add_warning(warning)
        if auto_translate.judge_summary is not None:
            self.judge_summary = (
                auto_translate.judge_summary
                if self.judge_summary is None
                else self.judge_summary + auto_translate.judge_summary
            )
        return judge_remaining

    def perform(
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
    ) -> str:
        self.active_producer_run = None
        try:
            message = self._perform(
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                source_component_ids=source_component_ids,
            )
        except Exception as error:
            if (
                self.active_producer_run is not None
                and self.active_producer_run.execution_version == 0
            ) or (
                (
                    self.active_producer_run is not None
                    and self.active_producer_run.execution_version >= 1
                )
                and isinstance(
                    error,
                    (DatabaseError, MemoryError, SystemExit, KeyboardInterrupt),
                )
            ):
                self._finish_producer_run(
                    self.active_producer_run, ProducerRun.Status.FAILED, str(error)
                )
            raise
        if (
            self.active_producer_run is not None
            and self.active_producer_run.status
            not in {
                ProducerRun.Status.COMPLETED,
                ProducerRun.Status.FAILED,
                ProducerRun.Status.CANCELLED,
                ProducerRun.Status.PARTIAL,
            }
        ):
            if (
                self.active_producer_run.execution_version >= 1
                and not self.failure_message
            ):
                pass
            else:
                status = (
                    ProducerRun.Status.FAILED
                    if self.failure_message
                    else ProducerRun.Status.COMPLETED
                )
                self._finish_producer_run(
                    self.active_producer_run, status, self.failure_message or ""
                )
        return message

    def _perform(  # ruff: ignore[complex-structure]
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
    ) -> str:
        judge_preview = (
            self.preview_judge_scope(
                execution_version=self._producer_execution_version()
            )
            if self.mode == "judge"
            else None
        )
        preparation_scope: PreparationScope | None = None
        if (
            self.mode == "judge"
            and self.overwrite_existing
            and self.producer_run_id is not None
            and ProducerRun.objects.filter(
                pk=self.producer_run_id, requested_mode="judge"
            ).exists()
        ):
            # A queued bulk judge run never overwrites existing text; an
            # overwrite request belongs to a plain MT run. A direct batch
            # without a queued run keeps the historical behavior.
            failure = gettext("Overwrite cannot be combined with the judge mode.")
            self.add_warning(gettext("Automatic translation failed: %s") % failure)
            self.failure_message = gettext("Automatic translation failed: %s") % failure
            return self.failure_message
        if judge_preview is not None:
            producer_run = self._adopt_producer_run()
            if self.mode == "judge" and producer_run is not None:
                # Only a producer-dispatched bulk judge run (phase
                # ``judge-project``) carries the mandatory preparation
                # contract; a queued run without a snapshot predates it and
                # must not silently resume with the new MT volume. Older
                # direct/queued test launches build their scope here.
                if (
                    self.producer_run_id is not None
                    and not producer_run.preparation_snapshot
                    and producer_run.requested_mode == "judge"
                    and producer_run.dispatch_phase == "judge-project"
                ):
                    self._finish_producer_run(
                        producer_run,
                        ProducerRun.Status.FAILED,
                        gettext(
                            "This run was queued before the mandatory "
                            "translation step existed and cannot resume. "
                            "Start a new run."
                        ),
                    )
                    raise JudgeError(producer_run.failure)
                if producer_run.preparation_snapshot:
                    preparation_scope = PreparationScope.from_json(
                        producer_run.preparation_snapshot
                    )
                else:
                    preparation_scope = self.build_preparation_scope()
                    producer_run.preparation_snapshot = preparation_scope.to_json()
                    producer_run.preparation_phase = "pending"
                    producer_run.save(
                        update_fields=["preparation_snapshot", "preparation_phase"]
                    )
        elif auto_source == "mt":
            producer_run = self._create_producer_run()
        else:
            producer_run = None
        self.active_producer_run = producer_run
        if producer_run is not None and producer_run.execution_version >= 1:
            # Each continuation chunk is a fresh BatchAutoTranslate with a
            # fresh judge_summary; accumulate the durable tally across chunk
            # boundaries so the final COMPLETED summary reports the full run.
            prior = producer_run.summary or {}
            self._chunk_prior_summary = {
                "evaluated": prior.get("evaluated", 0),
                "nothing_blocking": prior.get("nothing_blocking", 0),
                "minor_noted": prior.get("minor_noted", 0),
                "major_not_fixed": prior.get("major_not_fixed", 0),
                "critical_held": prior.get("critical_held", 0),
                "unparsed": prior.get("unparsed", 0),
                "repaired": prior.get("repaired", 0),
                "untranslated": prior.get("untranslated", 0),
            }
        else:
            self._chunk_prior_summary = None
        if producer_run is not None and producer_run.status in {
            ProducerRun.Status.COMPLETED,
            ProducerRun.Status.FAILED,
            ProducerRun.Status.CANCELLED,
            ProducerRun.Status.PARTIAL,
        }:
            return gettext("Automatic translation completed.")
        judge_remaining = judge_preview.processed if judge_preview is not None else None
        selected_workspace_source_component_ids: dict[int, list[int]] | None = None
        if (
            producer_run is not None
            and preparation_scope is not None
            # The reservation belongs to the run's first dispatch only: a
            # continuation chunk re-enters ``_perform`` with the same closed
            # scope, and repeating it would resolve the glossary and issue a
            # ``get_or_create`` round trip for every unit of the whole
            # snapshot on every chunk -- O(scope) per chunk, O(scope * chunks)
            # per run. The rows are already durable, and a version-1 run's
            # coverage counts unreserved scope as pending, so a continuation
            # loses nothing by skipping it. A redelivered first dispatch
            # repeats an idempotent call; a cancelled or finished run never
            # reaches this point.
            and (producer_run.execution_version < 1 or not producer_run.scope_cursor)
        ):
            # Judge-cap skips are recorded once, globally, before any
            # preparation MT: the reason must survive a preparation failure
            # instead of being overwritten by ``mt-prerequisite``. The judge
            # scope itself is reserved up front as PENDING rows so a crash
            # leaves honest coverage rather than empty evidence.
            self._reserve_judge_rows(producer_run)
            self._apply_cap_skips(producer_run)
        # Global preparation barrier (C3): fill every missing string in the
        # closed scope before any judge request of any language. A confirmed
        # refusal or a remaining gap fails the whole run here. The mandatory
        # preparation runs when this dispatch asked for machine translation
        # (``auto_source="mt"``) and is not proposal-only; a proposal-only
        # run judges the stored text as-is, and an overwrite request never
        # uses the empty-only preparation.
        # A single-unit re-check and the deferred-retry drain are durable
        # purposes judged on the stored text: they never widen into a paid
        # project-wide preparation (C3).
        purpose_judges_stored_text = producer_run is not None and (
            producer_run.requested_mode in {"recheck", "drain"}
        )
        preparation_requested = (
            auto_source == "mt"
            and producer_run is not None
            and preparation_scope is not None
            and not self.judge_proposal_only
            and not self.overwrite_existing
            and not purpose_judges_stored_text
        )
        if (
            preparation_requested
            and producer_run is not None
            and preparation_scope is not None
            and producer_run.status
            not in {
                ProducerRun.Status.COMPLETED,
                ProducerRun.Status.FAILED,
                ProducerRun.Status.CANCELLED,
                ProducerRun.Status.PARTIAL,
            }
        ):
            preparation_failure = self._run_preparation(
                producer_run, preparation_scope, threshold, engines
            )
            if preparation_failure is not None:
                if judge_preview is not None and judge_preview.remaining:
                    self.judge_summary = replace(
                        self.judge_summary or JudgeSummary(),
                        cap_remainder=judge_preview.remaining,
                    )
                self._finish_producer_run(
                    producer_run,
                    ProducerRun.Status.FAILED,
                    preparation_failure,
                )
                return preparation_failure
            if _run_cancel_requested(producer_run):
                # The preparation stopped for a cancellation, not readiness;
                # the finalize path maps it to cancelled/partial.
                self._finish_producer_run(producer_run, ProducerRun.Status.FAILED, "")
                return self.failure_message or self.get_message()
        elif (
            auto_source == "mt"
            and producer_run is not None
            and preparation_scope is not None
            and not self.judge_proposal_only
            and not self.overwrite_existing
            and not purpose_judges_stored_text
        ):
            # Nothing to prepare, or no engine to prepare with: the closed
            # scope is re-checked (a human write or a ready snapshot never
            # pays a probe) and the phase is recorded before the judge pass.
            self._run_preparation(producer_run, preparation_scope, threshold, engines)
        if (
            self.mode != "judge"
            and auto_source == "others"
            and source_component_ids is not None
            and self.workspace_source_component_ids is not None
        ):
            selected_workspace_source_component_ids = {}
            for selected_source_language_id, component_id in Component.objects.filter(
                pk__in=source_component_ids, source_language_id__isnull=False
            ).values_list("source_language_id", "pk"):
                if selected_source_language_id is not None:
                    selected_workspace_source_component_ids.setdefault(
                        selected_source_language_id, []
                    ).append(component_id)

        # The judge phase skips only local MT pretranslation when the global
        # preparation actually ran (auto_source="mt" with a missing scope);
        # otherwise each translation prepares its own writable strings.
        globally_prepared = (
            producer_run is not None
            and preparation_scope is not None
            and preparation_requested
            and producer_run.preparation_phase == "ready"
        )
        if producer_run is not None and producer_run.execution_version >= 1:
            return self._perform_chunk_loop(
                producer_run=producer_run,
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                globally_prepared=globally_prepared,
            )
        for pos, translation in enumerate(self.translations, start=1):
            auto_translate = AutoTranslate(
                user=self.user,
                translation=translation,
                q=self.q,
                mode=self.mode,
                component_wide=self.component_wide,
                unit_ids=self.unit_ids,
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                overwrite_existing=self.overwrite_existing,
                judge_limit=judge_remaining,
                producer_run=producer_run,
                judge_pretranslate=self.judge_pretranslate,
                judge_mutating_repairs=self.judge_mutating_repairs,
                judge_candidate_severities=self.judge_candidate_severities,
                judge_proposal_only=self.judge_proposal_only,
            )
            # Share the attempt-local counter so every nested progress write
            # reports the batch-wide `done/total`, not one translation's.
            auto_translate.batch_counter = self._attempt_counter
            if not self._can_process_translation(translation):
                if self.mode == "judge" and producer_run is not None:
                    self._record_skipped_judge_units(
                        producer_run,
                        list(auto_translate.get_units().order_by("position", "pk")),
                        JudgeRunUnit.SkipReason.PERMISSION,
                    )
                self.set_progress(pos)
                continue
            if not self._attempt_counter.snapshot_taken:
                # Attempt-local eligible snapshot (Task 2): the complete
                # permission-filtered unit count over the whole scope,
                # taken before the first mutation of this delivery attempt.
                # A redelivered task re-runs this after the stored units
                # left the `q` filter, so the new attempt counts only the
                # remaining ones. No progress write here: the batch's own
                # set_progress(pos) below publishes the snapshot.
                self._attempt_counter.eligible = self._count_eligible_units()
                self._attempt_counter.snapshot_taken = True
            auto_translate.progress_range = (
                100 * (pos - 1) // self.progress_steps,
                100 * pos // self.progress_steps,
            )

            effective_source_component_ids = source_component_ids
            if (
                self.mode != "judge"
                and auto_source == "others"
                and self.workspace_source_component_ids is not None
            ):
                source_language_id = translation.component.source_language_id
                if selected_workspace_source_component_ids is None:
                    effective_source_component_ids = (
                        []
                        if source_language_id is None
                        else self.workspace_source_component_ids.get(
                            source_language_id, []
                        )
                    )
                elif source_language_id is None:
                    self.add_warning(
                        gettext(
                            "Automatic translation skipped some translations because "
                            "selected source components use a different source language."
                        )
                    )
                    self.set_progress(pos)
                    continue
                else:
                    effective_source_component_ids = (
                        selected_workspace_source_component_ids.get(
                            source_language_id, []
                        )
                    )
                    if not effective_source_component_ids:
                        self.add_warning(
                            gettext(
                                "Automatic translation skipped some translations because "
                                "selected source components use a different source language."
                            )
                        )
                        self.set_progress(pos)
                        continue

                effective_source_component_ids = [
                    component_id
                    for component_id in effective_source_component_ids
                    if component_id != translation.component_id
                ]
                if not effective_source_component_ids:
                    if selected_workspace_source_component_ids is not None:
                        self.set_progress(pos)
                        continue
                    self.add_warning(
                        gettext(
                            "Automatic translation skipped some translations because "
                            "no other source components were available."
                        )
                    )
                    self.set_progress(pos)
                    continue

            # A failure here propagates to perform()'s own handler, which
            # finalizes the run exactly once (active_producer_run stays set);
            # finalizing here too would double-write the same FAILED run.
            auto_translate.perform(
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                source_component_ids=effective_source_component_ids,
                prepare=not globally_prepared,
            )
            judge_remaining = self._finish_translation(auto_translate, judge_remaining)
            self.set_progress(pos)

        if judge_preview is not None and judge_preview.remaining:
            self.judge_summary = replace(
                self.judge_summary or JudgeSummary(),
                cap_remainder=judge_preview.remaining,
            )
            self.add_warning(
                gettext("Judge run skipped because the per-run string cap was reached.")
            )
        if producer_run is not None:
            self._finish_producer_run(
                producer_run,
                (
                    ProducerRun.Status.FAILED
                    if self.failure_message
                    else ProducerRun.Status.COMPLETED
                ),
                self.failure_message or "",
            )
        return self.failure_message or self.get_message()

    def _perform_chunk_loop(
        self,
        *,
        producer_run: ProducerRun,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        globally_prepared: bool,
    ) -> str:
        from weblate.trans.tasks import (  # ruff: ignore[import-outside-top-level]
            publish_producer_run_dispatch,
        )

        cursor = producer_run.scope_cursor
        snapshot = producer_run.scope_snapshot or []
        chunk_ids = snapshot[cursor : cursor + JUDGE_CHUNK_SIZE]
        if not chunk_ids:
            self._finish_producer_run(producer_run, ProducerRun.Status.COMPLETED, "")
            return gettext("Automatic translation completed.")

        unit_translation_map = dict(
            Unit.objects.filter(pk__in=chunk_ids).values_list("pk", "translation_id")
        )
        chunk_by_translation: dict[int, list[int]] = {}
        for unit_id in chunk_ids:
            trans_id = unit_translation_map.get(unit_id)
            if trans_id is not None:
                chunk_by_translation.setdefault(trans_id, []).append(unit_id)

        # A cancellation requested before this continuation worker started must
        # not pay for a full chunk: check before the first translation loop.
        if _run_cancel_requested(producer_run):
            self._finish_producer_run(producer_run, ProducerRun.Status.CANCELLED, "")
            return gettext("Automatic translation cancelled.")

        for pos, translation in enumerate(self.translations, start=1):
            trans_chunk_ids = chunk_by_translation.get(translation.pk)
            if not trans_chunk_ids:
                continue
            auto_translate = AutoTranslate(
                user=self.user,
                translation=translation,
                q=self.q,
                mode=self.mode,
                component_wide=self.component_wide,
                unit_ids=trans_chunk_ids,
                allow_non_shared_tm_source_components=(
                    self.allow_non_shared_tm_source_components
                ),
                overwrite_existing=self.overwrite_existing,
                judge_uncapped=True,
                producer_run=producer_run,
                judge_pretranslate=False,
                judge_mutating_repairs=self.judge_mutating_repairs,
                judge_candidate_severities=self.judge_candidate_severities,
                judge_proposal_only=self.judge_proposal_only,
            )
            auto_translate.batch_counter = self._attempt_counter
            if not self._can_process_translation(translation):
                self._record_skipped_judge_units(
                    producer_run,
                    list(auto_translate.get_units().order_by("position", "pk")),
                    JudgeRunUnit.SkipReason.PERMISSION,
                )
                self.set_progress(pos)
                continue

            auto_translate.perform(
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                source_component_ids=None,
                prepare=not globally_prepared,
            )
            self._finish_translation(auto_translate, None)
            self.set_progress(pos)
            if self.failure_message:
                self._finish_producer_run(
                    producer_run, ProducerRun.Status.FAILED, self.failure_message
                )
                return self.failure_message
            if _run_cancel_requested(producer_run):
                self._finish_producer_run(
                    producer_run, ProducerRun.Status.CANCELLED, ""
                )
                return gettext("Automatic translation cancelled.")

        new_cursor = cursor + len(chunk_ids)
        has_more = new_cursor < len(snapshot)
        if has_more:
            with transaction.atomic():
                locked = ProducerRun.objects.select_for_update().get(pk=producer_run.pk)
                if locked.status == ProducerRun.Status.CANCEL_REQUESTED:
                    self._finish_producer_run(locked, ProducerRun.Status.CANCELLED, "")
                    return gettext("Automatic translation cancelled.")
                if locked.status != ProducerRun.Status.RUNNING:
                    return gettext("Automatic translation stopped.")
                locked.scope_cursor = new_cursor
                next_task_id = uuid4()
                locked.dispatch_task_id = next_task_id
                locked.dispatch_requested_at = timezone.now()
                locked.dispatch_published_at = None
                locked.dispatch_attempts = 0
                locked.dispatch_error = ""
                locked.save(
                    update_fields=[
                        "scope_cursor",
                        "dispatch_task_id",
                        "dispatch_requested_at",
                        "dispatch_published_at",
                        "dispatch_attempts",
                        "dispatch_error",
                    ]
                )
            publish_producer_run_dispatch(run_id=producer_run.pk)
            return gettext("Automatic translation chunk completed.")

        producer_run.scope_cursor = new_cursor
        ProducerRun.objects.filter(pk=producer_run.pk).update(scope_cursor=new_cursor)
        self._finish_producer_run(producer_run, ProducerRun.Status.COMPLETED, "")
        return gettext("Automatic translation completed.")
