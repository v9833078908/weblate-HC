# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol invariants for the offline dual-reference study."""

from __future__ import annotations

import json
import sys
from base64 import b64encode
from pathlib import Path
from typing import cast

import pytest

PROBE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROBE_ROOT))


def test_arm_a_payload_contains_only_russian_primary_source() -> None:
    from dual_reference_zh.payloads import (  # ruff: ignore[import-outside-top-level]
        build_generation_payload,
    )

    payload = build_generation_payload(
        {
            "record_id": "record-1",
            "ru": ["Русский текст"],
            "en": ["English text"],
            "context": "menu.play",
        },
        arm="A",
    )

    # ruff: ignore[assert]
    assert payload["sources"] == [
        {"language": "ru", "role": "primary", "text": ["Русский текст"]}
    ]


def test_block_schedule_interleaves_arms_and_preserves_every_pair() -> None:
    from dual_reference_zh.execution import (  # ruff: ignore[import-outside-top-level]
        build_block_schedule,
    )

    schedule = build_block_schedule(
        [{"record_id": f"record-{index}"} for index in range(10)],
        arms=("A", "B", "C", "D"),
        batch_size=5,
        seed=917,
    )

    # ruff: ignore[assert]
    assert [task["arm"] for task in schedule[:4]] != ["A", "A", "B", "B"]
    # ruff: ignore[assert]
    assert {task["arm"] for task in schedule[:4]} == {"A", "B", "C", "D"}
    # ruff: ignore[assert]
    assert all(
        [task["records"] for task in schedule[start : start + 4]].count(
            schedule[start]["records"]
        )
        == 4
        for start in (0, 4)
    )


def test_safe_response_metadata_keeps_debuggable_error_without_request_text() -> None:
    from dual_reference_zh.execution import (  # ruff: ignore[import-outside-top-level]
        safe_response_metadata,
    )

    metadata = safe_response_metadata(
        status=403,
        headers={"x-request-id": "req-42", "content-type": "application/json"},
        body='{"error":{"code":"policy","message":"denied"},"prompt":"secret"}',
    )

    # ruff: ignore[assert]
    assert metadata == {
        "status": 403,
        "request_id": "req-42",
        "content_type": "application/json",
        "body_sha256": "25b01e229ef76db36350480834977b0230e49bbe5c0f9426523be91cf8620562",
        "error": {"code": "policy"},
    }


def test_safe_response_metadata_drops_error_message_that_can_contain_a_key_id() -> None:
    from dual_reference_zh.execution import (  # ruff: ignore[import-outside-top-level]
        safe_response_metadata,
    )

    metadata = safe_response_metadata(
        status=403,
        headers={},
        body='{"error":{"code":403,"message":"manage key abc123"}}',
    )

    # ruff: ignore[assert]
    assert metadata["error"] == {"code": 403}


def test_arm_b_payload_contains_only_english_primary_source() -> None:
    from dual_reference_zh.payloads import (  # ruff: ignore[import-outside-top-level]
        build_generation_payload,
    )

    payload = build_generation_payload(
        {
            "record_id": "record-1",
            "ru": ["Русский текст"],
            "en": ["English text"],
            "context": "menu.play",
        },
        arm="B",
    )

    # ruff: ignore[assert]
    assert payload["sources"] == [
        {"language": "en", "role": "primary", "text": ["English text"]}
    ]


@pytest.mark.parametrize(
    ("arm", "expected_sources"),
    [
        (
            "C",
            [
                {"language": "en", "role": "primary", "text": ["English text"]},
                {"language": "ru", "role": "reference", "text": ["Русский текст"]},
            ],
        ),
        (
            "D",
            [
                {"language": "ru", "role": "primary", "text": ["Русский текст"]},
                {"language": "en", "role": "reference", "text": ["English text"]},
            ],
        ),
    ],
)
def test_two_source_arms_mark_primary_and_reference_roles(
    arm: str, expected_sources: list[dict[str, object]]
) -> None:
    from dual_reference_zh.payloads import (  # ruff: ignore[import-outside-top-level]
        build_generation_payload,
    )

    payload = build_generation_payload(
        {
            "record_id": "record-1",
            "ru": ["Русский текст"],
            "en": ["English text"],
            "context": "menu.play",
        },
        arm=arm,
    )

    # ruff: ignore[assert]
    assert payload["sources"] == expected_sources


def test_arm_e_review_payload_contains_only_english_and_shared_b_draft() -> None:
    from dual_reference_zh.payloads import (  # ruff: ignore[import-outside-top-level]
        build_review_payload,
    )

    payload = build_review_payload(
        {
            "record_id": "record-1",
            "ru": ["Русский текст"],
            "en": ["English text"],
            "context": "menu.play",
        },
        arm="E",
        b_draft_job_id="generate:record-1:B:1",
        reviewer_seat="seat-1",
    )

    # ruff: ignore[assert]
    assert payload["sources"] == [
        {"language": "en", "role": "primary", "text": ["English text"]}
    ]
    # ruff: ignore[assert]
    assert payload["draft"] == {"job_id": "generate:record-1:B:1"}


def test_arm_f_review_payload_adds_russian_reference_to_the_same_b_draft() -> None:
    from dual_reference_zh.payloads import (  # ruff: ignore[import-outside-top-level]
        build_review_payload,
    )

    payload = build_review_payload(
        {
            "record_id": "record-1",
            "ru": ["Русский текст"],
            "en": ["English text"],
            "context": "menu.play",
        },
        arm="F",
        b_draft_job_id="generate:record-1:B:1",
        reviewer_seat="seat-1",
    )

    # ruff: ignore[assert]
    assert payload["sources"] == [
        {"language": "en", "role": "primary", "text": ["English text"]},
        {"language": "ru", "role": "reference", "text": ["Русский текст"]},
    ]
    # ruff: ignore[assert]
    assert payload["draft"] == {"job_id": "generate:record-1:B:1"}


def test_job_graph_reuses_one_b_draft_for_each_e_and_f_pipeline() -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.prepare import (
        build_jobs,
    )

    jobs = build_jobs([{"record_id": "record-1"}], reviewer_seats=("seat-1", "seat-2"))
    by_id = {job["job_id"]: job for job in jobs}
    b_job_id = "generate:record-1:B:1"

    # ruff: ignore[assert]
    assert len(jobs) == 10
    # ruff: ignore[assert]
    assert all(
        by_id[f"review:record-1:{arm}:{seat}:1"]["depends_on"] == [b_job_id]
        for arm in ("E", "F")
        for seat in ("seat-1", "seat-2")
    )
    # ruff: ignore[assert]
    assert by_id["edit:record-1:E:1"]["depends_on"] == [
        b_job_id,
        "review:record-1:E:seat-1:1",
        "review:record-1:E:seat-2:1",
    ]
    # ruff: ignore[assert]
    assert by_id["edit:record-1:F:1"]["depends_on"] == [
        b_job_id,
        "review:record-1:F:seat-1:1",
        "review:record-1:F:seat-2:1",
    ]


def test_dry_run_writes_local_summary_without_opening_a_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.run import (
        run_dry_run,
    )

    (tmp_path / "manifest.json").write_text(
        json.dumps({"gates": {"G0": "closed", "G1": "closed"}}),
        encoding="utf-8",
    )
    (tmp_path / "inputs.jsonl").write_text(
        json.dumps({"record_id": "record-1"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "jobs.jsonl").write_text(
        "\n".join(
            json.dumps(job)
            for job in [
                {
                    "job_id": "generate:record-1:A:1",
                    "record_id": "record-1",
                    "stage": "generate",
                },
                {
                    "job_id": "generate:record-1:B:1",
                    "record_id": "record-1",
                    "stage": "generate",
                },
                {
                    "job_id": "review:record-1:E:seat-1:1",
                    "record_id": "record-1",
                    "stage": "review",
                },
                {
                    "job_id": "edit:record-1:E:1",
                    "record_id": "record-1",
                    "stage": "edit",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    import socket  # ruff: ignore[import-outside-top-level]

    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("network used"),
    )
    summary = run_dry_run(tmp_path)

    # ruff: ignore[assert]
    assert summary["job_count"] == 4
    # ruff: ignore[assert]
    assert summary["stages"] == {"edit": 1, "generate": 2, "review": 1}
    # ruff: ignore[assert]
    assert summary["network_requests"] == 0
    # ruff: ignore[assert]
    assert summary["projected_tokens"] is None
    # ruff: ignore[assert]
    assert summary["budget_cap"] is None
    # ruff: ignore[assert]
    assert summary["max_attempts_per_job"] == 1
    # ruff: ignore[assert]
    assert (
        json.loads((tmp_path / "dry-run.json").read_text(encoding="utf-8")) == summary
    )


def test_dry_run_rejects_a_job_with_an_unknown_record_id(tmp_path: Path) -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.run import (
        run_dry_run,
    )

    (tmp_path / "manifest.json").write_text(
        json.dumps({"gates": {"G0": "closed", "G1": "closed"}}),
        encoding="utf-8",
    )
    (tmp_path / "inputs.jsonl").write_text(
        json.dumps({"record_id": "record-1"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "jobs.jsonl").write_text(
        json.dumps(
            {
                "job_id": "generate:record-2:A:1",
                "record_id": "record-2",
                "stage": "generate",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown record_id"):
        run_dry_run(tmp_path)


def test_snapshot_keeps_unreviewed_pairs_in_screening_only(tmp_path: Path) -> None:
    from dual_reference_zh.prepare import (  # ruff: ignore[import-outside-top-level]
        create_study_snapshot,
    )

    result = create_study_snapshot(
        [
            {
                "record_id": "record-1",
                "project": "need-for-greed",
                "component": "ui",
                "id_hash": "42",
                "key": "menu_play",
                "context": "menu_play",
                "ru": ["Играть"],
                "en": ["Play"],
                "human_review_status": "unverified",
            }
        ],
        study_directory=tmp_path,
        study_id="2026-09-16-screening-v1",
        bundle_hashes={"pilot-120.jsonl": "test-hash"},
        reviewer_seats=("seat-1", "seat-2"),
    )

    eligibility = [
        json.loads(line)
        for line in (tmp_path / "eligibility.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    # ruff: ignore[assert]
    assert result["gates"] == {"G0": "closed", "G1": "closed", "G2": "open"}
    # ruff: ignore[assert]
    assert eligibility == [
        {
            "authoring_language": "unknown",
            "confirmatory_status": "excluded",
            "confirmatory_reason": (
                "human_review_status=unverified; "
                "semantic_alignment_review=pending; "
                "authoring_language=unknown"
            ),
            "en_automatically_translated": None,
            "record_id": "record-1",
            "semantic_alignment_review": "pending",
            "screening_status": "included",
        }
    ]
    # ruff: ignore[assert]
    assert json.loads((tmp_path / "splits.json").read_text(encoding="utf-8"))[
        "pilot"
    ] == ["record-1"]


def test_itt_summary_treats_invalid_and_missing_rating_as_not_passed() -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.analyze import (
        summarize_itt,
    )

    summary = summarize_itt(
        [
            {
                "record_id": "record-1",
                "arm": "A",
                "valid_output": True,
                "unusable": False,
            },
            {
                "record_id": "record-2",
                "arm": "A",
                "valid_output": False,
                "unusable": None,
            },
            {
                "record_id": "record-3",
                "arm": "A",
                "valid_output": True,
                "unusable": None,
            },
        ]
    )

    # ruff: ignore[assert]
    assert summary == {
        "assigned": 3,
        "valid_outputs": 2,
        "invalid_outputs": 1,
        "rated": 1,
        "usable": 1,
        "unusable_observed": 1,
        "missing_ratings": 1,
        "unusable_lower_bound": 1 / 3,
        "unusable_upper_bound": 2 / 3,
    }


def test_paired_risk_difference_matches_synthetic_null_and_planted_effect() -> None:
    from dual_reference_zh.analyze import (  # ruff: ignore[import-outside-top-level]
        paired_risk_difference,
    )

    null_outputs = [
        {"record_id": "record-1", "arm": "A", "unusable": False},
        {"record_id": "record-1", "arm": "B", "unusable": False},
        {"record_id": "record-2", "arm": "A", "unusable": True},
        {"record_id": "record-2", "arm": "B", "unusable": True},
    ]
    planted_effect_outputs = [
        {"record_id": "record-1", "arm": "A", "unusable": True},
        {"record_id": "record-1", "arm": "B", "unusable": False},
        {"record_id": "record-2", "arm": "A", "unusable": False},
        {"record_id": "record-2", "arm": "B", "unusable": False},
    ]

    # ruff: ignore[assert]
    assert paired_risk_difference(null_outputs, treatment="B", control="A") == 0
    # ruff: ignore[assert, float-equality-comparison]
    assert (
        paired_risk_difference(planted_effect_outputs, treatment="B", control="A")
        == -0.5
    )


def test_cluster_resampling_preserves_the_shared_b_job_vector() -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.analyze import resample_job_vectors

    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.prepare import build_jobs

    jobs = build_jobs([{"record_id": "record-1"}], reviewer_seats=("seat-1", "seat-2"))
    resampled = resample_job_vectors(
        jobs,
        clusters={"record-1": "scene-1"},
        seed=7,
        samples=2,
    )

    # ruff: ignore[assert]
    assert len(resampled) == 20
    for bootstrap_draw in (0, 1):
        draw_jobs = {
            job["job_id"]: job
            for job in resampled
            if job["bootstrap_draw"] == bootstrap_draw
        }
        b_job_id = "generate:record-1:B:1"
        # ruff: ignore[assert]
        assert all(
            draw_jobs[f"review:record-1:{arm}:{seat}:1"]["depends_on"] == [b_job_id]
            for arm in ("E", "F")
            for seat in ("seat-1", "seat-2")
        )


def test_snapshot_records_the_approved_screening_scope_and_open_g2(
    tmp_path: Path,
) -> None:
    from dual_reference_zh.prepare import (  # ruff: ignore[import-outside-top-level]
        create_study_snapshot,
    )

    create_study_snapshot(
        [
            {
                "record_id": "record-1",
                "project": "need-for-greed",
                "component": "ui",
                "id_hash": "42",
                "key": "menu_play",
                "context": "menu_play",
                "ru": ["Играть"],
                "en": ["Play"],
                "human_review_status": "unverified",
            }
        ],
        study_directory=tmp_path,
        study_id="2026-09-16-screening-v1",
        bundle_hashes={"pilot-120.jsonl": "test-hash"},
        reviewer_seats=("seat-1", "seat-2"),
    )

    preregistration = json.loads(
        (tmp_path / "preregistration.json").read_text(encoding="utf-8")
    )

    # ruff: ignore[assert]
    assert "screening" in (tmp_path / "decisions.md").read_text(encoding="utf-8")
    # ruff: ignore[assert]
    assert "--dry-run" in (tmp_path / "README.md").read_text(encoding="utf-8")
    # ruff: ignore[assert]
    assert preregistration["scope"] == "screening"
    # ruff: ignore[assert]
    assert preregistration["status"] == "incomplete"
    # ruff: ignore[assert]
    assert preregistration["gates"]["G2"] == "open"


def test_prepare_cli_creates_and_dry_runs_a_local_snapshot(tmp_path: Path) -> None:
    from dual_reference_zh.prepare import main  # ruff: ignore[import-outside-top-level]

    bundle_directory = tmp_path / "bundle"
    study_directory = tmp_path / "study"
    bundle_directory.mkdir()
    (bundle_directory / "pilot-120.jsonl").write_text(
        json.dumps(
            {
                "record_id": "record-1",
                "project": "need-for-greed",
                "component": "ui",
                "id_hash": "42",
                "key": "menu_play",
                "context": "menu_play",
                "ru": ["Играть"],
                "en": ["Play"],
                "human_review_status": "unverified",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # ruff: ignore[assert]
    assert (
        main(
            [
                "--bundle-directory",
                str(bundle_directory),
                "--study-directory",
                str(study_directory),
                "--study-id",
                "2026-09-16-screening-v1",
                "--dry-run",
            ]
        )
        == 0
    )
    # ruff: ignore[assert]
    assert (
        json.loads((study_directory / "dry-run.json").read_text(encoding="utf-8"))[
            "job_count"
        ]
        == 10
    )


@pytest.mark.parametrize(
    ("arm", "expected_languages"), [("E", ["en"]), ("F", ["en", "ru"])]
)
def test_editor_payload_uses_the_same_language_treatment_as_review(
    arm: str, expected_languages: list[str]
) -> None:
    from dual_reference_zh.payloads import (  # ruff: ignore[import-outside-top-level]
        build_edit_payload,
    )

    payload = build_edit_payload(
        {
            "record_id": "record-1",
            "ru": ["Русский текст"],
            "en": ["English text"],
            "context": "menu.play",
        },
        arm=arm,
        b_draft_job_id="generate:record-1:B:1",
        reviewer_job_ids=[f"review:record-1:{arm}:seat-1:1"],
    )

    sources = cast("list[dict[str, object]]", payload["sources"])
    # ruff: ignore[assert]
    assert [source["language"] for source in sources] == expected_languages
    # ruff: ignore[assert]
    assert payload["edit_when_reviews_empty"] is True
    # ruff: ignore[assert]
    assert payload["reviewer_job_ids"] == [f"review:record-1:{arm}:seat-1:1"]


def test_generation_prompt_for_arm_a_does_not_expose_english_source() -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.execution import build_generation_messages

    messages = build_generation_messages(
        {
            "record_id": "record-1",
            "ru": ["Русский текст"],
            "en": ["ENGLISH-MUST-NOT-LEAK"],
            "context": "menu.play",
        },
        arm="A",
    )
    serialized = json.dumps(messages, ensure_ascii=False)

    # ruff: ignore[assert]
    assert "Русский текст" in serialized
    # ruff: ignore[assert]
    assert "ENGLISH-MUST-NOT-LEAK" not in serialized


def test_translation_response_is_matched_by_record_id_not_response_order() -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.execution import parse_translation_response

    translations = parse_translation_response(
        {
            "translations": [
                {"record_id": "record-2", "translation": "第二"},
                {"record_id": "record-1", "translation": "第一"},
            ]
        },
        expected_record_ids=("record-1", "record-2"),
    )

    # ruff: ignore[assert]
    assert translations == {"record-1": "第一", "record-2": "第二"}


def test_generation_batch_keeps_each_arm_source_visibility() -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.execution import build_generation_batch_messages

    messages = build_generation_batch_messages(
        [
            {
                "record_id": "record-1",
                "ru": ["Русский текст"],
                "en": ["ENGLISH-MUST-NOT-LEAK"],
                "context": "menu.play",
            }
        ],
        arm="A",
    )

    # ruff: ignore[assert]
    assert "ENGLISH-MUST-NOT-LEAK" not in json.dumps(messages, ensure_ascii=False)


def test_remote_selector_decodes_without_a_temp_file() -> None:
    # ruff: ignore[import-outside-top-level]
    from dual_reference_zh.remote_screening import load_selector

    encoded = b64encode(json.dumps([{"record_id": "record-1"}]).encode()).decode()

    # ruff: ignore[assert]
    assert load_selector(None, encoded) == [{"record_id": "record-1"}]
