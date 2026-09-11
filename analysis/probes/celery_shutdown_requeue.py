# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Measure what a Celery worker does with a running task when it is stopped.

The question the probe answers: after the container is recreated, does the
message come back to the queue immediately, or does it sit in the Redis
``unacked`` hash until ``visibility_timeout`` expires (four hours in
production)?

Each scenario starts a fresh worker on an isolated queue and Redis database,
so nothing in the instance is touched:

``term_kill``
    Today's production behaviour: SIGTERM (warm shutdown, the worker waits for
    the task) followed by SIGKILL after the supervisor's ``stopwaitsecs``.
``quit_late``
    Proposed behaviour for the worker that runs the producer's long tasks:
    SIGQUIT (cold shutdown) with ``worker_soft_shutdown_timeout``.
``quit_early``
    The risk case of the same proposal: a long task **without** ``acks_late``
    receiving the same SIGQUIT.
``quit_short_early``
    The bound of that risk: a short task without ``acks_late`` still finishes
    inside the soft-shutdown window instead of being cancelled.

Run inside the dev container (the repository root is bind-mounted there)::

    docker exec dev-docker-weblate-1 /app/venv/bin/python \
        /app/src/analysis/probes/celery_shutdown_requeue.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import time
from typing import Any

from celery import Celery
from kombu import Connection

BROKER = os.environ.get("PROBE_BROKER", "redis://cache:6379/9")
QUEUE = "probe"
SOFT_SHUTDOWN_TIMEOUT = float(os.environ.get("PROBE_SOFT_SHUTDOWN", "10"))
# What the supervisor does after `stopwaitsecs` when the worker is still alive.
KILL_AFTER = float(os.environ.get("PROBE_KILL_AFTER", "20"))

app = Celery("probe", broker=BROKER, backend=BROKER)
app.conf.update(
    task_default_queue=QUEUE,
    broker_transport_options={"visibility_timeout": 4 * 3600},
    result_backend_transport_options={"visibility_timeout": 4 * 3600},
    worker_prefetch_multiplier=1,
    worker_soft_shutdown_timeout=SOFT_SHUTDOWN_TIMEOUT,
)


def _mark_started(name: str) -> None:
    with Connection(BROKER) as conn:
        conn.default_channel.client.set(f"probe:started:{name}", "1")


@app.task(name="probe.late", acks_late=True, reject_on_worker_lost=True)
def late_task() -> None:
    """Stand-in for auto_translate: long and late-acknowledged."""
    _mark_started("late")
    time.sleep(600)


@app.task(name="probe.early")
def early_task() -> None:
    """Stand-in for the 84 tasks that acknowledge on delivery."""
    _mark_started("early")
    time.sleep(600)


@app.task(name="probe.short")
def short_task() -> str:
    """Model a housekeeping-sized task that must survive a soft shutdown."""
    _mark_started("short")
    time.sleep(3)
    return "finished"


MARKERS = {late_task: "late", early_task: "early", short_task: "short"}


def _client():
    conn = Connection(BROKER)
    return conn, conn.default_channel.client


def _reset(client) -> None:
    client.delete(QUEUE, "unacked", "unacked_index")
    for key in client.keys("probe:started:*"):
        client.delete(key)


def _state(client) -> dict[str, int]:
    return {
        "queue": client.llen(QUEUE),
        "unacked": client.hlen("unacked"),
        "unacked_index": client.zcard("unacked_index"),
    }


def _start_worker() -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "--workdir",
            os.path.dirname(os.path.abspath(__file__)),
            "-A",
            "celery_shutdown_requeue",
            "worker",
            "--queues",
            QUEUE,
            "--concurrency",
            "1",
            "--pool",
            "prefork",
            "--hostname",
            "probe@%h",
            "--loglevel",
            "info",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_started(client, name: str, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"probe:started:{name}"):
            return True
        time.sleep(0.5)
    return False


def run_scenario(name: str, *, task, stop_signal: int) -> dict[str, Any]:
    conn, client = _client()
    try:
        _reset(client)
        worker = _start_worker()
        result = task.apply_async()
        if not _wait_started(client, MARKERS[task]):
            worker.kill()
            worker.communicate(timeout=30)
            msg = f"{name}: task never started"
            raise RuntimeError(msg)
        running = _state(client)

        signalled = time.monotonic()
        worker.send_signal(stop_signal)
        killed = False
        try:
            worker.wait(timeout=KILL_AFTER)
        except subprocess.TimeoutExpired:
            worker.send_signal(signal.SIGKILL)
            killed = True
            worker.wait(timeout=30)
        stop_seconds = round(time.monotonic() - signalled, 1)
        output = worker.stdout.read() if worker.stdout else ""
        # Redis writes from the dying worker are synchronous, but give the
        # transaction a moment before reading the final state.
        time.sleep(1)
        return {
            "scenario": name,
            "while_running": running,
            "after_stop": _state(client),
            "task_state": result.state,
            "sigkill_needed": killed,
            "stop_seconds": stop_seconds,
            "exit_code": worker.returncode,
            "restored_log": "Restoring" in output,
            "soft_shutdown_log": "Soft Shutdown" in output,
        }
    finally:
        _reset(client)
        conn.release()


def main() -> None:
    results = [
        run_scenario(
            "term_kill (today: acks_late + SIGTERM, SIGKILL after stopwaitsecs)",
            task=late_task,
            stop_signal=signal.SIGTERM,
        ),
        run_scenario(
            "quit_late (proposed: acks_late + SIGQUIT)",
            task=late_task,
            stop_signal=signal.SIGQUIT,
        ),
        run_scenario(
            "quit_early (risk: long task without acks_late + SIGQUIT)",
            task=early_task,
            stop_signal=signal.SIGQUIT,
        ),
        run_scenario(
            "quit_short_early (bound: 3 s task without acks_late + SIGQUIT)",
            task=short_task,
            stop_signal=signal.SIGQUIT,
        ),
    ]
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
