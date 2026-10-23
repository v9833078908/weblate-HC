# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from datetime import datetime

ARCHIVED = "archived_unknown"
PREFIX = "run-isolation:"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def payload_digest(job: Mapping) -> str:
    raw = job["data"]
    if not isinstance(raw, str):
        raise ValueError("Isolation requires the stored payload, not a reserialized copy")
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_isolation_record(job: Mapping, record: Mapping) -> dict:
    """Validate an immutable local archive; never infer a provider/billing outcome."""
    try:
        value = record["data"]
        if isinstance(value, str):
            value = json.loads(value)
        data = json.loads(job["data"])
        fields = {
            "schema",
            "run_id",
            "owner",
            "project",
            "remote_project",
            "idempotency_key",
            "payload_sha256",
            "operations_sha256",
            "original_status",
            "lease_until",
            "created_at",
            "operator",
            "reason",
            "backup_manifest_sha256",
            "reconciliation_sha256",
        }
        valid = (
            isinstance(value, dict)
            and set(value) == fields
            and type(value["schema"]) is int
            and value["schema"] == 1
            and record["id"] == PREFIX + job["id"]
            and record["kind"] == "run_isolation"
            and record["owner"] == job["owner"] == value["owner"]
            and record["revision"] == digest(value)
            and value["run_id"] == job["id"]
            and value["project"] == data["project"]
            and value["remote_project"] == data["remote_project"]
            and value["idempotency_key"] == job["idempotency_key"]
            and value["payload_sha256"] == payload_digest(job)
            and value["operations_sha256"] == digest(data["operations"])
            and value["original_status"] == "unknown"
            and job["status"] == ARCHIVED
            and not job["lease_owner"]
            and type(job["lease_until"]) in (int, float)
            and job["lease_until"] == value["lease_until"] <= time.time()
            and all(isinstance(value[k], str) and value[k].strip() for k in ("reason", "operator"))
            and all(
                re.fullmatch(r"[0-9a-f]{64}", value[k])
                for k in ("backup_manifest_sha256", "reconciliation_sha256")
            )
            and datetime.fromisoformat(value["created_at"]).utcoffset() is not None
        )
        if not valid:
            raise ValueError("Isolation record does not match the archived job")
        return value
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError("Malformed isolation record") from error
