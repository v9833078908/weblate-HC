# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Literal

from django.db import connections
from django.utils.translation import gettext

from weblate.logger import LOGGER
from weblate.machinery.base import (
    MachineTranslationError,
    MachineTranslationServiceError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from weblate.auth.models import User
    from weblate.machinery.base import BatchMachineTranslation, UnitMemoryResultDict
    from weblate.trans.models import Translation, Unit


# A refusal that outlived the request retries stops the service for everyone, so
# batches of a run started meanwhile are skipped. Wait for a short stop to pass
# rather than dropping their strings, but never hold a run for a long one.
RATE_LIMIT_WAIT = 90
RATE_LIMIT_POLL = 5


@dataclass(frozen=True, slots=True)
class MachineryBatchOutcome:
    """
    The classified result of one batch request, handed to the caller thread.

    ``status`` distinguishes a batch the service answered ("success"), one
    it refused with an error ("failed"), and one that was never asked
    because the service was already stopped ("skipped"). ``reason_code``
    carries the confirmed classification from
    :class:`MachineTranslationServiceError` when one exists.
    """

    status: Literal["success", "failed", "skipped"]
    service: str
    unit_ids: tuple[int, ...]
    reason_code: str | None = None
    error: str | None = None


@dataclass(slots=True)
class _ServiceRun:
    """Per-service fetch state: refusals observed while fetching."""

    failures: list[MachineryBatchOutcome] = field(default_factory=list)
    fatal_reason: str | None = None

    @property
    def fatally_stopped(self) -> bool:
        # A confirmed quota/auth/permission refusal never heals within a
        # run: later batches of the same service are not even asked.
        return self.fatal_reason in {
            MachineTranslationServiceError.REASON_QUOTA_EXHAUSTED,
            MachineTranslationServiceError.REASON_INSUFFICIENT_CREDIT,
            MachineTranslationServiceError.REASON_AUTHENTICATION,
            MachineTranslationServiceError.REASON_PERMISSION,
        }

    def observe(self, outcome: MachineryBatchOutcome) -> None:
        if outcome.status == "failed":
            self.failures.append(outcome)
            if outcome.reason_code is not None and self.fatal_reason is None:
                self.fatal_reason = outcome.reason_code


def _fetch_machinery_batch(
    *,
    service: BatchMachineTranslation,
    batch: list[Unit],
    user: User | None,
    threshold: int,
    log_translation: Translation | None,
    close_connections: bool,
) -> MachineryBatchOutcome:
    """Fetch a single batch, classifying a failure instead of hiding it."""
    unit_ids = tuple(unit.id for unit in batch)
    try:
        if service.is_rate_limited():
            # The service refused often enough to be stopped for everyone; every
            # remaining string would be dropped without a request anyway.
            return MachineryBatchOutcome(
                status="skipped", service=service.name, unit_ids=unit_ids
            )
        service.batch_translate(batch, user, threshold=threshold)
    except MachineTranslationServiceError as error:
        if log_translation is not None:
            log_translation.log_error("failed automatic translation: %s", error)
        else:
            LOGGER.warning(
                "failed machinery translation from %s: %s",
                service.name,
                error,
            )
        return MachineryBatchOutcome(
            status="failed",
            service=service.name,
            unit_ids=unit_ids,
            reason_code=error.reason_code,
            error=error.safe_message,
        )
    except MachineTranslationError as error:
        if log_translation is not None:
            log_translation.log_error("failed automatic translation: %s", error)
        else:
            LOGGER.warning(
                "failed machinery translation from %s: %s",
                service.name,
                error,
            )
        return MachineryBatchOutcome(
            status="failed",
            service=service.name,
            unit_ids=unit_ids,
            reason_code=MachineTranslationServiceError.REASON_PROVIDER_UNAVAILABLE,
            # The raw provider detail stays in the server log only: this
            # outcome is serialized into durable user-visible warnings.
            error=gettext("The provider refused the request."),
        )
    finally:
        # Django only closes connections it opened for a request or a task, so a
        # worker thread has to release its own.
        if close_connections:
            connections.close_all()
    return MachineryBatchOutcome(
        status="success", service=service.name, unit_ids=unit_ids
    )


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
    run: _ServiceRun,
) -> list[list[Unit]]:
    """
    Fetch batches, returning the ones a stopped service refused to be asked.

    Every observed outcome lands in ``run``; a confirmed fatal refusal
    (spent quota, bad credentials, missing permission) stops the remaining
    batches of this service from even being asked.
    """
    skipped: list[list[Unit]] = []
    if concurrency < 2:
        for batch in batches:
            if run.fatally_stopped:
                skipped.append(batch)
                continue
            outcome = _fetch_machinery_batch(
                service=service,
                batch=batch,
                user=user,
                threshold=threshold,
                log_translation=log_translation,
                close_connections=False,
            )
            run.observe(outcome)
            if outcome.status == "skipped":
                skipped.append(batch)
            else:
                done(batch)
        return skipped

    # Progress is reported from this thread because Celery keeps the current
    # task in thread-local storage. Batches submitted while the service is
    # healthy stay in flight and their results are kept; a fatal refusal
    # only stops submissions that have not happened yet.
    futures: dict = {}
    remaining = list(batches)

    def drain() -> None:
        while remaining or futures:
            while remaining and not run.fatally_stopped and len(futures) < concurrency:
                batch = remaining.pop(0)
                futures[
                    pool.submit(
                        _fetch_machinery_batch,
                        service=service,
                        batch=batch,
                        user=user,
                        threshold=threshold,
                        log_translation=log_translation,
                        close_connections=True,
                    )
                ] = batch
            if not futures:
                break
            ready = [future for future in futures if future.done()]
            if not ready:
                ready = [next(as_completed(futures))]
            for future in ready:
                batch = futures.pop(future)
                outcome = future.result()
                run.observe(outcome)
                if outcome.status == "skipped":
                    skipped.append(batch)
                else:
                    done(batch)
        # Everything never submitted after a fatal refusal is skipped.
        skipped.extend(remaining)

    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="machinery-batch"
    ) as pool:
        drain()
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
    run: _ServiceRun,
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
        run=run,
    )
    skipped = fetch(batches=batches)
    if (
        skipped
        and not run.fatally_stopped
        and _wait_for_rate_limit(service, log_translation)
    ):
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
    on_failure: Callable[[MachineryBatchOutcome], None] | None = None,
) -> dict[int, UnitMemoryResultDict]:
    """
    Fetch machinery matches without applying them to units.

    ``on_batch`` receives every batch as soon as it is fetched, on the calling
    thread. It is ignored for more than one service, because a unit's best
    result is only known once every service has answered. ``on_failure``
    receives each classified batch failure on the calling thread; without it
    failures are only logged, preserving the previous silent behavior.
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

        run = _ServiceRun()
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
            run=run,
        )
        if on_failure is not None:
            for outcome in run.failures:
                on_failure(outcome)

    return {
        unit.id: unit.machinery
        for unit in units
        if unit.machinery and any(unit.machinery["quality"])
    }
