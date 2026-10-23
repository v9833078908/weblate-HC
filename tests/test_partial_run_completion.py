# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.services.run_outcomes import run_outcomes


def test_coverage_parsed_pass_and_payment_are_independent():
    units = [
        {
            "unit_id": i,
            "input_revision": f"r{i}",
            "live_seat_results": {"1": "parsed", "2": "parsed"},
        }
        for i in range(1, 1648)
    ]
    data = {
        "mt_batches": {
            "ui:pl": {
                "filled": 1662,
                "total": 1662,
                "payment_unknown": [1661, 1662],
                "exceptions": {},
                "state": "completed_with_exceptions",
            }
        },
        "quality": {
            "units": units,
            "counts": {"processed": 1662, "not_verified": 68},
            "status": "completed_with_exceptions",
        },
    }
    result = run_outcomes(data)
    assert result["translation_coverage"] == {"filled": 1662, "total": 1662, "missing": 0}
    assert result["judge_coverage"] == {
        "both_parsed": 1647,
        "pass": 1594,
        "without_pass": 68,
        "total": 1662,
    }
    assert result["payment_outcome"]["unknown"] == 2
    assert result["payment_outcome"]["scope"] == "machine_translation"
    assert result["quality_outcome"] == "completed_with_exceptions"


def test_verification_coverage_uses_validated_input_not_mt_batches():
    data = {
        "mode": "quality_only",
        "quality_scope": {"unit_ids": list(range(1662))},
        "quality_input_coverage": {"filled": 1662, "total": 1662, "missing": 0},
        "quality": {"counts": {"processed": 1662, "not_verified": 68}},
    }
    assert run_outcomes(data)["translation_coverage"] == {
        "filled": 1662,
        "total": 1662,
        "missing": 0,
    }


def test_unfilled_unknown_is_never_presented_as_ready():
    result = run_outcomes(
        {
            "mt_batches": {
                "ui:pl": {
                    "filled": 8,
                    "total": 10,
                    "payment_unknown": [9, 10],
                    "state": "completed_with_exceptions",
                }
            }
        }
    )
    assert result["translation_coverage"]["missing"] == 2
    assert result["has_exceptions"] is True


def test_changed_artifact_uses_new_content_version_and_retains_old_record(tmp_path):
    from app.db import Store
    from app.services.quality_artifacts import immutable_record_id

    store = Store(tmp_path)
    store.save("export", "export:job", "owner", {"value": "original"})
    assert (
        immutable_record_id(store, "owner", "export", "export:job", {"value": "original"})
        == "export:job"
    )
    updated = immutable_record_id(store, "owner", "export", "export:job", {"value": "later"})
    assert updated.startswith("export:job:v-")
    assert store.get("export", "export:job", "owner")["value"] == "original"
