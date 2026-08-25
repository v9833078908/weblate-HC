# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import TYPE_CHECKING

from django.db import connections

from weblate.logger import LOGGER
from weblate.machinery.base import MachineTranslationError
from weblate.trans.models import Translation, Unit

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from weblate.auth.models import User
    from weblate.machinery.base import BatchMachineTranslation, UnitMemoryResultDict


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
