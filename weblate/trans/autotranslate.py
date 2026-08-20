# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from celery import current_task
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import connections, transaction
from django.db.models import Case, IntegerField, QuerySet, Value, When
from django.db.models.functions import MD5, Lower
from django.utils.translation import gettext, ngettext

from weblate.logger import LOGGER
from weblate.machinery.base import MachineTranslationError
from weblate.machinery.models import MACHINERY
from weblate.trans.actions import ActionEvents
from weblate.trans.judge_loop import run_judge_batch
from weblate.trans.models import (
    Category,
    Component,
    Project,
    Suggestion,
    SuggestionAddResult,
    Translation,
    Unit,
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


# A refusal that outlived the request retries stops the service for everyone, so
# batches of a run started meanwhile are skipped. Wait for a short stop to pass
# rather than dropping their strings, but never hold a run for a long one.
RATE_LIMIT_WAIT = 90
RATE_LIMIT_POLL = 5


def _fetch_machinery_batch(
    *,
    service: BatchMachineTranslation,
    batch: list[Unit],
    user: User | None,
    threshold: int,
    log_translation: Translation | None,
    close_connections: bool,
) -> bool:
    """Fetch a single batch, keeping a failure local to it."""
    try:
        if service.is_rate_limited():
            # The service refused often enough to be stopped for everyone; every
            # remaining string would be dropped without a request anyway.
            return False
        service.batch_translate(batch, user, threshold=threshold)
    except MachineTranslationError as error:
        if log_translation is not None:
            log_translation.log_error("failed automatic translation: %s", error)
        else:
            LOGGER.warning(
                "failed machinery translation from %s: %s",
                service.name,
                error,
            )
    finally:
        # Django only closes connections it opened for a request or a task, so a
        # worker thread has to release its own.
        if close_connections:
            connections.close_all()
    return True


def _wait_for_rate_limit(
    service: BatchMachineTranslation, log_translation: Translation | None
) -> bool:
    """Wait for a stop to pass, up to what a run can afford."""
    deadline = time.monotonic() + min(service.rate_limit_period, RATE_LIMIT_WAIT)
    logged = False
    while True:
        if not service.is_rate_limited():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if not logged and log_translation is not None:
            log_translation.log_info(
                "waiting for %s to accept requests again", service.name
            )
            logged = True
        time.sleep(min(RATE_LIMIT_POLL, remaining))


def _fetch_machinery_batches(
    *,
    service: BatchMachineTranslation,
    batches: list[list[Unit]],
    user: User | None,
    threshold: int,
    log_translation: Translation | None,
    concurrency: int,
    done: Callable[[list[Unit]], None],
) -> list[list[Unit]]:
    """Fetch batches, returning the ones a stopped service refused to be asked."""
    skipped: list[list[Unit]] = []
    if concurrency < 2:
        for batch in batches:
            if _fetch_machinery_batch(
                service=service,
                batch=batch,
                user=user,
                threshold=threshold,
                log_translation=log_translation,
                close_connections=False,
            ):
                done(batch)
            else:
                skipped.append(batch)
        return skipped

    # Progress is reported from this thread because Celery keeps the current
    # task in thread-local storage.
    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="machinery-batch"
    ) as pool:
        futures = {
            pool.submit(
                _fetch_machinery_batch,
                service=service,
                batch=batch,
                user=user,
                threshold=threshold,
                log_translation=log_translation,
                close_connections=True,
            ): batch
            for batch in batches
        }
        for future in as_completed(futures):
            if future.result():
                done(futures[future])
            else:
                skipped.append(futures[future])
    return skipped


def _fetch_machinery_service(
    *,
    service: BatchMachineTranslation,
    batches: list[list[Unit]],
    user: User | None,
    threshold: int,
    log_translation: Translation | None,
    set_progress: Callable[[int], None] | None,
    progress_offset: int,
    concurrency: int,
    on_batch: Callable[[list[Unit]], None] | None,
) -> None:
    """Fetch all batches of one service, in parallel when it allows it."""
    fetched = 0

    def done(batch: list[Unit]) -> None:
        nonlocal fetched
        # Runs on the calling thread, so the callback may touch the database.
        if on_batch is not None:
            on_batch(batch)
        fetched += len(batch)
        if set_progress is not None:
            set_progress(progress_offset + fetched)

    fetch = partial(
        _fetch_machinery_batches,
        service=service,
        user=user,
        threshold=threshold,
        log_translation=log_translation,
        concurrency=concurrency,
        done=done,
    )
    skipped = fetch(batches=batches)
    if skipped and _wait_for_rate_limit(service, log_translation):
        skipped = fetch(batches=skipped)
    # Keep the progress total honest: every batch counts once, asked or not.
    for batch in skipped:
        done(batch)


def fetch_machinery_matches(
    *,
    units: list[Unit],
    user: User | None,
    services: Sequence[BatchMachineTranslation],
    threshold: int,
    set_progress: Callable[[int], None] | None = None,
    log_translation: Translation | None = None,
    on_batch: Callable[[list[Unit]], None] | None = None,
) -> dict[int, UnitMemoryResultDict]:
    """
    Fetch machinery matches without applying them to units.

    ``on_batch`` receives every batch as soon as it is fetched, on the calling
    thread. It is ignored for more than one service, because a unit's best
    result is only known once every service has answered.
    """
    num_units = len(units)
    if len(services) != 1:
        on_batch = None

    for pos, translation_service in enumerate(services):
        batch_size = translation_service.batch_size
        batches = [
            units[batch_start : batch_start + batch_size]
            for batch_start in range(0, num_units, batch_size)
        ]
        concurrency = max(1, min(translation_service.batch_concurrency, len(batches)))
        if log_translation is not None:
            log_translation.log_info(
                "fetching translations for %d units from %s, %d per request, %d in parallel",
                num_units,
                translation_service.name,
                batch_size,
                concurrency,
            )

        _fetch_machinery_service(
            service=translation_service,
            batches=batches,
            user=user,
            threshold=threshold,
            log_translation=log_translation,
            set_progress=set_progress,
            progress_offset=pos * num_units,
            concurrency=concurrency,
            on_batch=on_batch,
        )

    return {
        unit.id: unit.machinery
        for unit in units
        if unit.machinery and any(unit.machinery["quality"])
    }


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
    return user.has_perm("meta:unit.direct_edit", translation)


class BaseAutoTranslate:
    updated: int = 0
    progress_steps: int = 0

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
            current_task.update_state(
                state="PROGRESS",
                meta=self.get_task_meta()
                | {"progress": 100 * current // self.progress_steps},
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
                select_for_update=False,
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
        from weblate.trans.models.judge import JudgeVerdict, state_for_verdict

        units = list(self.get_units().select_related("source_unit"))
        if len(units) > settings.JUDGE_MAX_UNITS_PER_RUN:
            self.failure_message = ngettext(
                "Judge run refused: %(n)d string exceeds the per-run cap of %(cap)d.",
                "Judge run refused: %(n)d strings exceed the per-run cap of %(cap)d.",
                len(units),
            ) % {"n": len(units), "cap": settings.JUDGE_MAX_UNITS_PER_RUN}
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
        if writable_ids:
            saved_ids, saved_state = self.unit_ids, self.target_state
            self.unit_ids = list(writable_ids)
            self.target_state = self.fresh_translation_state
            try:
                self.process_mt(engines, threshold)
            finally:
                self.unit_ids, self.target_state = saved_ids, saved_state

        # Phase 2: judge everything in q; the state is decided per verdict.
        verdicts = run_judge_batch(units, writable_ids=writable_ids, user=self.user)
        unparsed = 0
        for unit in units:
            verdict = verdicts.get(unit.id)
            if verdict is None:
                continue
            if verdict.verdict == JudgeVerdict.Verdict.UNPARSED:
                unparsed += 1
            state = state_for_verdict(
                verdict.verdict,
                enable_review=self.translation.enable_review,
                may_approve=settings.JUDGE_MAY_APPROVE,
            )
            if state is not None and unit.state != state:
                self.update(unit, state, unit.get_target_plurals())
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
            if self.mode == "judge":
                self.process_judge(engines=engines, threshold=threshold)
                return self.get_message()
            if auto_source == "mt":
                self.process_mt(engines, threshold)
            else:
                self.process_others(source_component_ids)
        except (MachineTranslationError, Component.DoesNotExist) as error:
            translation.log_error("failed automatic translation: %s", error)
            self.failure_message = gettext("Automatic translation failed: %s") % error
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

    def perform(
        self,
        *,
        auto_source: Literal["mt", "others"],
        engines: list[str],
        threshold: int,
        source_component_ids: list[int] | None,
    ) -> str:
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
            self.updated += auto_translate.updated
            for warning in auto_translate.get_warnings():
                self.add_warning(warning)
            self.set_progress(pos)

        return self.get_message()
