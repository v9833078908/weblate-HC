# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Celery integration helper tools."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any

from celery import Celery
from celery.contrib.django.task import DjangoTask
from celery.result import AsyncResult
from celery.signals import after_setup_logger, before_task_publish, task_failure
from django.conf import settings
from django.core.cache import cache
from django.core.checks import run_checks

# Type annotation compatibility
# ruff: ignore[unused-lambda-argument]
Celery.__class_getitem__ = classmethod(lambda cls, *args, **kwargs: cls)
# ruff: ignore[unused-lambda-argument]
DjangoTask.__class_getitem__ = classmethod(lambda cls, *args, **kwargs: cls)

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "weblate.settings")

app = Celery[DjangoTask[..., Any]]("weblate")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

TASK_METADATA_TTL = 6 * 3600
# Redis priority 0 is the highest Kombu slot (priority_steps 0/3/6/9). Only a
# publication whose callsite knows a human is watching a progress bar uses it;
# everything else stays on CELERY_TASK_DEFAULT_PRIORITY (3, background).
INTERACTIVE_TASK_PRIORITY = 0


def get_task_metadata_key(task_id: str) -> str:
    return f"task-meta-{task_id}"


def store_task_metadata(
    task_id: str | None,
    *,
    component_id: int | None = None,
    translation_id: int | None = None,
    user_id: int | None = None,
) -> None:
    if not task_id:
        return
    data = {
        "component_id": component_id,
        "translation_id": translation_id,
    }
    if user_id is not None:
        data["user_id"] = user_id
    cache.set(
        get_task_metadata_key(task_id),
        data,
        TASK_METADATA_TTL,
    )


def get_task_metadata(task_id: str) -> dict[str, int | None] | None:
    return cache.get(get_task_metadata_key(task_id))


def delete_task_metadata(task_id: str | None) -> None:
    if not task_id:
        return
    cache.delete(get_task_metadata_key(task_id))


# --- Task liveness -------------------------------------------------------
#
# A closed cache record decides whether a user-facing background task is
# alive, not `AsyncResult.state`: Celery answers PENDING both for "still
# waiting for a worker" and for "the unacknowledged delivery was lost", so
# the state alone cannot tell them apart. `auto_translate*` and
# `fix_failing_checks` are acks_late; on a routine service restart the cold
# shutdown (Task 1) restores such a message to its queue, and this record is
# what lets the producer screen say queued/running truthfully across that
# redelivery. See docs/product/plans/2026-09-11-producer-tasks-survive-deploy.md.


LIVENESS_TASKS = frozenset(
    {
        "weblate.trans.tasks.auto_translate",
        "weblate.trans.tasks.auto_translate_component",
        "weblate.trans.tasks.fix_failing_checks",
    }
)


def get_task_liveness_key(task_id: str) -> str:
    return f"task-liveness-{task_id}"


def register_task_liveness(task_id: str | None) -> None:
    """
    Create the liveness record before the task is published.

    Must run strictly before `apply_async(task_id=...)`: a fast worker can
    start (and heartbeat) the task before the view finishes, and a missing
    record would make the first `retrieve` answer `queued` with no
    heartbeat, or drop the task from the user list altogether. The record
    lives as long as `task-meta`/`user-tasks`.
    """
    if not task_id:
        return
    cache.set(
        get_task_liveness_key(task_id),
        {
            "enabled": True,
            "started_at": time.time(),
            "heartbeat_at": None,
            "attempt": 0,
        },
        TASK_METADATA_TTL,
    )


def delete_task_liveness(task_id: str | None) -> None:
    """Drop the liveness record (publish failure, task cleanup)."""
    if not task_id:
        return
    cache.delete(get_task_liveness_key(task_id))


def heartbeat_task(task_id: str | None) -> None:
    """
    Mark a delivery attempt as started/active.

    Bumps `attempt` and refreshes `heartbeat_at` atomically, so a redelivered
    task is observable as a new attempt over the remaining units instead of
    a continuation of the lost one. Keeping the same TTL refreshes the whole
    record; a task whose record already expired (worker outlived the cache
    entry) simply recreates it.
    """
    if not task_id:
        return
    key = get_task_liveness_key(task_id)
    record = cache.get(key)
    if record is None:
        record = {"enabled": True, "started_at": time.time(), "attempt": 0}
    record["heartbeat_at"] = time.time()
    record["attempt"] = int(record.get("attempt", 0)) + 1
    cache.set(key, record, TASK_METADATA_TTL)


def touch_task_liveness(task_id: str | None) -> None:
    """Refresh `heartbeat_at` without starting a new attempt (progress)."""
    if not task_id:
        return
    key = get_task_liveness_key(task_id)
    record = cache.get(key)
    if record is None:
        # The task is still running after its record expired; recreate it
        # rather than reporting an unknown task as fresh.
        record = {"enabled": True, "started_at": time.time(), "attempt": 1}
    record["heartbeat_at"] = time.time()
    cache.set(key, record, TASK_METADATA_TTL)


# The `no-update` threshold. Celery retries a failing LLM request four times
# for up to 120 s each plus three backoffs up to 30 s
# (weblate/machinery/base.py), so a normal retry loop stays well under this
# and must not look like an interrupted task.
NO_UPDATE_AFTER = 600


def get_task_liveness(task_id: str | None) -> str | None:
    """
    Compute the user-facing liveness status of a task.

    Returns ``None`` for tasks without a liveness record (everything else
    keeps today's rendering), otherwise one of:

    * ``queued`` - published, no worker heartbeat yet (or an old one: the
      delivery may be waiting for redelivery after a restart);
    * ``running`` - heartbeated within :data:`NO_UPDATE_AFTER`;
    * ``no-update`` - the last heartbeat is older than that. This is not an
      error and promises no recovery; the copy says the task *may* still be
      continuing.
    """
    if not task_id:
        return None
    record = cache.get(get_task_liveness_key(task_id))
    if not record or not record.get("enabled"):
        return None
    heartbeat_at = record.get("heartbeat_at")
    if heartbeat_at is None:
        return "queued"
    if time.time() - float(heartbeat_at) > NO_UPDATE_AFTER:
        return "no-update"
    return "running"


# Tasks the user started are kept for as long as their metadata, so a progress
# bar can be restored on any page the user opens afterwards.
USER_TASKS_TTL = TASK_METADATA_TTL
# Celery answers PENDING for any task it does not know about, so an entry whose
# task was lost (result backend flushed, task never delivered) would otherwise
# sit at zero percent until the whole list expires.
PENDING_TASK_MAX_AGE = 1800


def get_user_tasks_key(user_id: int) -> str:
    return f"user-tasks-{user_id}"


def add_user_task(
    user_id: int,
    task_id: str | None,
    *,
    text: str,
    label: str,
    url: str,
    status_contract: bool = False,
) -> None:
    """
    Remember a background task started by the user.

    `status_contract` marks a task whose result is always a dict with an
    explicit `status` of `completed`/`failed`. The restored flash keeps
    that flag (`weblate/trans/context_processors.py`) so a failed run is
    rendered as a failure after a page reload too, not only in the
    transient flash of the request that queued it.
    """
    if not task_id:
        return
    key = get_user_tasks_key(user_id)
    tasks = [task for task in cache.get(key, []) if task["id"] != task_id]
    tasks.append(
        {
            "id": task_id,
            "started": time.time(),
            "text": str(text),
            "label": str(label),
            "url": url,
            "status_contract": status_contract,
        }
    )
    cache.set(key, tasks, USER_TASKS_TTL)


def get_user_tasks(user_id: int) -> list[dict[str, Any]]:
    """List running tasks started by the user, dropping the settled ones."""
    if not settings.CELERY_RESULT_BACKEND:
        # Without a result backend no task state can be read, so there is
        # nothing to display and nothing to prune.
        return []
    key = get_user_tasks_key(user_id)
    tasks = cache.get(key, [])
    if not tasks:
        return []
    now = time.time()
    running: list[dict[str, Any]] = []
    for task in tasks:
        result: AsyncResult = AsyncResult(task["id"])
        if result.ready():
            continue
        if (
            result.state == "PENDING"
            and now - task["started"] > PENDING_TASK_MAX_AGE
            # A liveness-enabled task stays listed until its cache records
            # expire: its delivery may still be waiting for redelivery after
            # a service restart (cold shutdown, Task 1), and dropping it
            # here would remove the only honest progress surface the user has.
            and get_task_liveness(task["id"]) is None
        ):
            continue
        running.append(task)
    if len(running) != len(tasks):
        if running:
            cache.set(key, running, USER_TASKS_TTL)
        else:
            cache.delete(key)
    return running


def extract_task_kwargs(body) -> dict[str, Any]:
    if isinstance(body, dict):
        kwargs = body.get("kwargs")
        return kwargs if isinstance(kwargs, dict) else {}
    if isinstance(body, (list, tuple)) and len(body) >= 2 and isinstance(body[1], dict):
        return body[1]
    return {}


@before_task_publish.connect
def store_published_task_metadata(headers=None, body=None, **kwargs) -> None:
    if not isinstance(headers, dict):
        return
    task_kwargs = extract_task_kwargs(body)
    component_id = task_kwargs.get("component_id")
    translation_id = task_kwargs.get("translation_id")
    if component_id is None and translation_id is None:
        return
    store_task_metadata(
        headers.get("id"),
        component_id=component_id,
        translation_id=translation_id,
    )


@task_failure.connect
def handle_task_failure(task_id="", exception=None, **kwargs) -> None:
    task_kwargs = kwargs.get("kwargs")
    if isinstance(task_kwargs, dict) and task_kwargs.get("activity_log_id") is not None:
        # ruff: ignore[import-outside-top-level]
        from weblate.addons.events import AddonActivityLogStatus

        # ruff: ignore[import-outside-top-level]
        from weblate.addons.tasks import update_addon_activity_log

        update_addon_activity_log(
            task_kwargs["activity_log_id"],
            str(exception),
            status=AddonActivityLogStatus.ERROR,
            task_count=task_kwargs.get("activity_log_task_count"),
        )

    # ruff: ignore[import-outside-top-level]
    from weblate.utils.errors import report_error

    report_error(
        f"Failure while executing task {task_id}",
        skip_error_reporting=True,
        print_tb=True,
        level="error",
    )


@app.on_after_configure.connect
def configure_error_handling(sender, **kwargs) -> None:
    """Rollbar and Sentry integration."""
    # ruff: ignore[import-outside-top-level]
    from weblate.utils.errors import init_error_collection

    init_error_collection(celery=True)


@after_setup_logger.connect
def show_failing_system_check(sender, logger, **kwargs) -> None:
    if settings.DEBUG:
        for check in run_checks(include_deployment_checks=True):
            # Skip silenced checks and Celery one
            # (it fails when started from Celery startup)
            if check.is_silenced() or check.id == "weblate.E019":
                continue
            logger.warning("%s", check)


def get_queue_length(queue="celery"):
    with app.connection_or_acquire() as conn:  # type: ignore[attr-defined]
        return conn.default_channel.queue_declare(
            queue=queue, durable=True, auto_delete=False
        ).message_count


def get_queue_list() -> set[str]:
    """List queues in Celery."""
    result = {"celery"}
    for route in settings.CELERY_TASK_ROUTES.values():
        if "queue" in route:
            result.add(route["queue"])
    return result


def get_queue_stats() -> dict[str, int]:
    """Calculate queue stats."""
    return {queue: get_queue_length(queue) for queue in get_queue_list()}


def get_task_progress(task):
    """Return progress of a Celery task."""
    # Completed task
    if task.ready():
        return 100
    # In progress
    result = task.result
    if task.state == "PROGRESS" and result is not None:
        return result["progress"]

    # Not yet started
    return 0


def is_celery_queue_long():
    """
    Check whether celery queue is too long.

    It does trigger if it is too long for at least one hour. This way peaks are
    filtered out, and no warning need be issued for big operations (for example
    site-wide autotranslation).
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import Translation

    cache_key = "celery_queue_stats"
    queues_data = cache.get(cache_key, {})

    # Hours since epoch
    current_hour = int(time.time() / 3600)
    test_hour = current_hour - 1

    # Fetch current stats
    stats = get_queue_stats()

    # Update counters
    if current_hour not in queues_data:
        # Delete stale items
        for key in list(queues_data.keys()):
            if key < test_hour:
                del queues_data[key]
        # Add current one
        queues_data[current_hour] = stats

        # Store to cache
        cache.set(cache_key, queues_data, 7200)

    # Do not fire if we do not have counts for two hours ago
    if test_hour not in queues_data:
        return False

    # Check if any queue got bigger
    base = queues_data[test_hour]
    thresholds: dict[str, int] = defaultdict(lambda: 50)
    # Set the limit to avoid trigger on auto-translating all components
    # nightly.
    thresholds["translate"] = max(1000, Translation.objects.count() // 30)
    return any(
        stat > thresholds[key] and base.get(key, 0) > thresholds[key]
        for key, stat in stats.items()
    )
