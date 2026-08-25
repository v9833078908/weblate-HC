# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from celery import current_task
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, IntegerField, QuerySet, Value, When
from django.db.models.functions import MD5, Lower
from django.utils.translation import gettext, ngettext

from weblate.machinery.base import MachineTranslationError
from weblate.machinery.models import MACHINERY
from weblate.trans.actions import ActionEvents
from weblate.trans.judge import JUDGE_SEATS, JudgeError, validate_judge_configuration
from weblate.trans.judge_loop import run_judge_batch
from weblate.trans.machinery import fetch_machinery_matches
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
    JudgeVerdict,
    current_verdict,
    state_for_verdict,
)
from weblate.trans.util import is_plural, split_plural
from weblate.utils.state import (
    STATE_APPROVED,
    STATE_FUZZY,
    STATE_READONLY,
    STATE_TRANSLATED,
)
from weblate.utils.stats import ProjectLanguage
from weblate.workspaces.models import Workspace

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from weblate.auth.models import User
    from weblate.auth.results import PermissionResult
    from weblate.machinery.base import BatchMachineTranslation, UnitMemoryResultDict
    from weblate.utils.state import StringState


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


class BaseAutoTranslate:
    updated: int = 0
    progress_steps: int = 0
    # Slice of the overall task progress this instance reports into. A batch
    # gives each translation its own slice so the percentage never goes back.
    progress_range: tuple[int, int] = (0, 100)

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

    def get_message(self) -> str:
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

    def set_progress(self, current: int) -> None:
        if current_task and current_task.request.id and self.progress_steps:
            low, high = self.progress_range
            current_task.update_state(
                state="PROGRESS",
                meta=self.get_task_meta()
                | {"progress": low + (high - low) * current // self.progress_steps},
            )


class AutoTranslate(BaseAutoTranslate):
    def __init__(
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
        self.judge_units_processed = 0
        # D2/fail-safe: a judge translation never starts shippable; the
        # verdict decides the final state per string.
        self.fresh_translation_state = STATE_FUZZY
        if self.mode == "fuzzy":
            self.target_state = STATE_FUZZY
        elif self.mode == "approved" and translation.enable_review:
            self.target_state = STATE_APPROVED

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
        if self.mode == "suggest" or any(len(item) > max_length for item in target):
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
    ) -> dict[int, UnitMemoryResultDict]:
        """Get the translations."""
        units: list[Unit] = list(self.get_units().select_related("source_unit"))
        num_units = len(units)

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

    def process_mt(self, engines: list[str], threshold: int) -> None:
        """Perform automatic translation based on machine translation."""
        translations = self.fetch_mt(engines, int(threshold), on_batch=self.store_batch)

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
                .prefetch_bulk()
                .select_for_update()
            ):
                translation: UnitMemoryResultDict = translations[unit.pk]
                # Use first existing origin for user
                # (there can be blanks for missing plurals)
                user: User | None = None
                for origin in translation["origin"]:
                    if origin is not None:
                        user = origin.user
                        break
                # Copy translation
                self.update(
                    unit,
                    self.target_state,
                    translation["translation"],
                    user=user,
                )
            # Flush the deferred changes of this transaction; a later crash
            # then leaves stored translations with their history intact.
            self.translation.store_update_changes()
        self.written.update(translations)
        self.set_progress(self.progress_base + len(self.written))

    def process_judge(self, *, engines: list[str], threshold: int) -> None:
        validate_judge_configuration()
        units = list(self.get_units().select_related("source_unit"))
        judge_limit = (
            settings.JUDGE_MAX_UNITS_PER_RUN
            if self.judge_limit is None
            else self.judge_limit
        )
        if len(units) > judge_limit:
            warning = ngettext(
                "Judge run refused: %(n)d string exceeds the per-run cap of %(cap)d.",
                "Judge run refused: %(n)d strings exceed the per-run cap of %(cap)d.",
                len(units),
            ) % {"n": len(units), "cap": judge_limit}
            self.failure_message = warning
            self.add_warning(warning)
            return
        writable_ids = {
            unit.id
            for unit in units
            if (not unit.translated or self.overwrite_existing)
        }

        # Phase 1: pre-translate the writable strings via the native MT
        # path, scoped by unit_ids, written at needs-editing. Reuses
        # process_mt / fetch_mt / store_results / update — no second
        # write path (plan mechanism 5).
        base_low, base_high = self.progress_range
        split = base_low + (base_high - base_low) // 10
        # set_progress has no clamp. Phase 2 restarts its counter, so the
        # phases use disjoint ranges to keep task progress monotonic.
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
        # Phase 1 may have written targets, so the judged instances are always
        # reloaded, whether or not anything was writable.
        units = list(
            self.translation.unit_set.filter(pk__in=[unit.id for unit in units])
            .prefetch()
            .prefetch_source()
        )

        # Phase 2: judge everything in q; the state is decided per verdict.
        judged = 0

        def tick(_requests, results) -> None:
            nonlocal judged
            judged += len(results)
            self.set_progress(min(judged, self.progress_steps))

        self.progress_range = (split, base_high)
        # A defect is re-judged by both seats once per repair attempt, so the
        # denominator is the worst case. Sizing it to one round would clamp the
        # bar at the phase maximum and freeze it for the whole repair loop.
        self.progress_steps = (
            len(units) * len(JUDGE_SEATS) * (settings.JUDGE_MAX_REPAIR_ATTEMPTS + 1)
        )
        try:
            verdicts = run_judge_batch(
                units,
                writable_ids=writable_ids,
                user=self.user,
                on_batch=tick,
            )
        finally:
            self.progress_range = (base_low, base_high)
        self.judge_units_processed = len(units)
        final_snapshots = {
            unit.id: (unit.target, unit.state) for unit in units if unit.id in verdicts
        }
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
                    # The target or glossary context changed while requests
                    # were in flight. Never apply a verdict to a different
                    # version.
                    continue
                if verdict.verdict == JudgeVerdict.Verdict.UNPARSED:
                    unparsed += 1
                state = state_for_verdict(
                    verdict.verdict,
                    enable_review=self.translation.enable_review,
                    may_approve=settings.JUDGE_MAY_APPROVE,
                )
                if state is not None and locked.state != state:
                    self.update(locked, state, locked.get_target_plurals())
        if unparsed:  # D5: never a silent no-op on money spent
            self.add_warning(
                ngettext(
                    "%d string was left unjudged (the judge did not answer).",
                    "%d strings were left unjudged (the judge did not answer).",
                    unparsed,
                )
                % unparsed
            )
        self.post_process()

    def _dispatch(
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
    ) -> None:
        if self.mode == "judge":
            self.process_judge(engines=engines, threshold=threshold)
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
            )
        except (JudgeError, MachineTranslationError, Component.DoesNotExist) as error:
            translation.log_error("failed automatic translation: %s", error)
            self.failure_message = gettext("Automatic translation failed: %s") % error
            self.add_warning(self.failure_message)
            return self.failure_message

        translation.log_info("completed automatic translation")

        return self.get_message()


class BatchAutoTranslate(BaseAutoTranslate):
    translations: QuerySet[Translation] | Sequence[Translation]

    def __init__(
        self,
        obj: Translation | Component | Category | ProjectLanguage | Workspace,
        *,
        user: User | None,
        q: str,
        mode: str,
        component_wide: bool = False,
        unit_ids: list[int] | None = None,
        allow_non_shared_tm_source_components: bool = False,
        enforce_permissions: bool = True,
        overwrite_existing: bool = False,
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
        self._preload_workflow_settings()
        self.progress_steps = len(self.translations)

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

    def get_task_meta(self) -> dict[str, Any]:
        return self._task_meta

    def _can_process_translation(self, translation: Translation) -> bool:
        return not self.enforce_permissions or bool(
            check_auto_translate_permission(self.user, translation, self.mode)
        )

    def _finish_translation(
        self, auto_translate: AutoTranslate, judge_remaining: int | None
    ) -> int | None:
        if judge_remaining is not None and not auto_translate.failure_message:
            judge_remaining -= auto_translate.judge_units_processed
        if auto_translate.failure_message:
            self.failure_message = auto_translate.failure_message
        self.updated += auto_translate.updated
        for warning in auto_translate.get_warnings():
            self.add_warning(warning)
        return judge_remaining

    def perform(
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
    ) -> str:
        if self.mode == "judge":
            validate_judge_configuration()
        judge_remaining = (
            settings.JUDGE_MAX_UNITS_PER_RUN if self.mode == "judge" else None
        )
        selected_workspace_source_component_ids: dict[int, list[int]] | None = None
        if (
            auto_source == "others"
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

        for pos, translation in enumerate(self.translations, start=1):
            if not self._can_process_translation(translation):
                self.set_progress(pos)
                continue
            if self.mode == "judge" and not judge_remaining:
                self.add_warning(
                    gettext(
                        "Judge run skipped because the per-run string cap was reached."
                    )
                )
                self.set_progress(pos)
                continue

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
            )
            auto_translate.progress_range = (
                100 * (pos - 1) // self.progress_steps,
                100 * pos // self.progress_steps,
            )

            effective_source_component_ids = source_component_ids
            if (
                auto_source == "others"
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

            auto_translate.perform(
                auto_source=auto_source,
                engines=engines,
                threshold=threshold,
                source_component_ids=effective_source_component_ids,
            )
            judge_remaining = self._finish_translation(auto_translate, judge_remaining)
            self.set_progress(pos)

        return self.failure_message or self.get_message()
