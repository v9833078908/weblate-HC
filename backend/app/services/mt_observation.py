# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from datetime import datetime


def record_translation_observation(state, settings, *, filled=None, total=None, run_started=None):
    """Persist a bounded read-only window; only increasing coverage extends it."""
    now = time.time()
    observation = state.get("observation")
    if observation is None:
        # Older releases persisted a five-minute deadline but not its start.
        started = state.get("created")
        if started is None:
            candidates = [state["observe_until"] - 300] if "observe_until" in state else []
            if run_started:
                candidates.append(datetime.fromisoformat(run_started).timestamp())
            # Timestamp-free legacy records use the durable run start: conservative,
            # but never a fresh allowance for an arbitrarily old remote operation.
            started = min(candidates) if candidates else now
        observation = {
            "started": started,
            "deadline": started + settings.mt_max_observation_seconds,
            "last_progress": started,
            "max_filled": 0,
            "filled": 0,
            "total": total,
        }
        state["observation"] = observation
    observation.setdefault(
        "deadline", observation["started"] + min(7200, settings.mt_max_observation_seconds)
    )
    if filled is not None:
        if filled > observation["max_filled"]:
            observation["last_progress"] = now
            observation["max_filled"] = filled
        observation.update(filled=filled, total=total)
    state["observe_until"] = min(
        observation["deadline"],
        observation["last_progress"] + settings.mt_observation_seconds,
    )
    return now < state["observe_until"]
