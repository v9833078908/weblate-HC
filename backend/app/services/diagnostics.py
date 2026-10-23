# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.db import revision
from app.services.mt_batches import public_mt_observation


def diagnostic_report(run_id, data):
    operations = []
    for key, state in data.get("operations", {}).items():
        parts = key.removeprefix("delta:").split(":")
        operations.append(
            {
                "correlation_id": run_id + ":" + revision(key)[:16],
                "operation": parts[0],
                "component": parts[1] if len(parts) > 1 else None,
                "language": parts[2]
                if parts[0] in {"mt", "language", "seed"} and len(parts) > 2
                else None,
                "state": state["state"],
                "http_status": state.get("remote_status"),
                "created": state.get("created"),
                "observation": state.get("observation"),
                "observe_until": state.get("observe_until"),
            }
        )
    return {
        "run_id": run_id,
        "phase": data.get("phase"),
        "failure_code": data.get("failure_code"),
        "http_status": data.get("remote_status"),
        "safe_reasons": data.get("safe_reasons", []),
        "rate_limit": data.get("rate_limit"),
        "mt_observation": public_mt_observation(data),
        "operations": operations,
        "note": "Журнал визарда и наблюдения API. Логи Django/Celery внешнего сервера недоступны.",
    }
