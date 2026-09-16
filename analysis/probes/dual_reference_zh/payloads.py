# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build explicit source-role payloads without Weblate or network access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

GENERATION_SOURCE_ROLES = {
    "A": (("ru", "primary"),),
    "B": (("en", "primary"),),
    "C": (("en", "primary"), ("ru", "reference")),
    "D": (("ru", "primary"), ("en", "reference")),
}


def build_generation_payload(
    record: Mapping[str, Any], *, arm: str
) -> dict[str, object]:
    """Build the source-role payload used by a generation arm."""
    source_roles = GENERATION_SOURCE_ROLES.get(arm)
    if source_roles is None:
        msg = f"Unsupported generation arm: {arm}"
        raise ValueError(msg)

    return {
        "record_id": str(record["record_id"]),
        "context": str(record["context"]),
        "arm": arm,
        "sources": [
            {
                "language": language,
                "role": role,
                "text": record[language],
            }
            for language, role in source_roles
        ],
    }


def build_review_payload(
    record: Mapping[str, Any],
    *,
    arm: str,
    b_draft_job_id: str,
    reviewer_seat: str,
) -> dict[str, object]:
    """Build an E/F reviewer payload bound to its immutable B draft."""
    if arm not in {"E", "F"}:
        msg = f"Unsupported review arm: {arm}"
        raise ValueError(msg)

    sources = [{"language": "en", "role": "primary", "text": record["en"]}]
    if arm == "F":
        sources.append({"language": "ru", "role": "reference", "text": record["ru"]})

    return {
        "record_id": str(record["record_id"]),
        "context": str(record["context"]),
        "arm": arm,
        "reviewer_seat": reviewer_seat,
        "draft": {"job_id": b_draft_job_id},
        "sources": sources,
    }


def build_edit_payload(
    record: Mapping[str, Any],
    *,
    arm: str,
    b_draft_job_id: str,
    reviewer_job_ids: list[str],
) -> dict[str, object]:
    """Build an E/F editor payload without skipping empty review feedback."""
    if arm not in {"E", "F"}:
        msg = f"Unsupported edit arm: {arm}"
        raise ValueError(msg)

    sources = [{"language": "en", "role": "primary", "text": record["en"]}]
    if arm == "F":
        sources.append({"language": "ru", "role": "reference", "text": record["ru"]})
    return {
        "record_id": str(record["record_id"]),
        "context": str(record["context"]),
        "arm": arm,
        "draft": {"job_id": b_draft_job_id},
        "sources": sources,
        "reviewer_job_ids": reviewer_job_ids,
        "edit_when_reviews_empty": True,
    }
