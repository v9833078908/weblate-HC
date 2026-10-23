# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

import pytest
from app.db import DomainError


def artifact_inputs():
    manifest = {
        "source": "en",
        "targets": ["de"],
        "components": [
            {
                "id": "c",
                "file": "kit.xlsx",
                "sheet": "Strings",
                "profile": {
                    "languages": [{"code": "en", "column": 2}, {"code": "de", "column": 3}]
                },
                "units": [
                    {"key": "one", "row": 2, "values": {"en": "Play"}},
                    {"key": "two", "row": 4, "values": {"en": "Play"}},
                ],
            }
        ],
    }
    exported = {
        "sheets": [
            {
                "id": "c",
                "file": "kit.xlsx",
                "name": "Strings",
                "rows": [
                    ["key", "en", "de", "fr"],
                    ["one", "Play", "Spielen", "Jouer"],
                    ["", "", "", ""],
                    ["two", "Play", "Spiel", "Jouer"],
                ],
            }
        ],
        "quality_provenance": {
            "language": "de",
            "scope_revision": "scope-1",
            "snapshot_hash": "hash-1",
            "status": "completed_with_exceptions",
            "counts": {"processed": 2, "automatically_corrected": 1},
        },
    }
    snapshot = {
        "complete": True,
        "scope_revision": "scope-1",
        "snapshot_hash": "hash-1",
        "unit_ids": [11, 12],
        "revisions": {"11": "r1", "12": "r2"},
        "judged_revisions": {"11": "r1", "12": "r2"},
        "issues": [],
    }
    bindings = [
        {
            "unit_id": uid,
            "component_id": "c",
            "key": key,
            "sheet_id": "c",
            "row": row,
            "column": 3,
            "language": "de",
            "revision": rev,
        }
        for uid, key, row, rev in [(11, "one", 2, "r1"), (12, "two", 4, "r2")]
    ]
    return dict(
        run_id="run-1",
        export_id="export:run-1",
        exported=exported,
        manifest=manifest,
        final_snapshot=snapshot,
        bindings=bindings,
        unit_results=[],
    )


def build(values):
    from app.services.quality_artifacts import build_quality_artifacts

    return build_quality_artifacts(**values)


def test_only_current_required_residuals_are_pink_and_not_source_matched():
    values = artifact_inputs()
    values["final_snapshot"]["issues"] = [
        {"group_id": "term", "kind": "glossary", "hard": True, "advisory": False, "unit_ids": [12]},
        {"group_id": "style", "kind": "check", "hard": False, "advisory": True, "unit_ids": [11]},
    ]
    values["unit_results"] = [
        {
            "unit_id": 11,
            "severity": "major",
            "input_revision": "old",
            "live_seat_results": {"1": "parsed", "2": "parsed"},
        }
    ]
    original = copy.deepcopy(values)
    result = build(values)
    assert result["highlights"] == [
        {"sheet_id": "c", "row": 4, "column": 3, "category": "blocking"}
    ]
    assert result["issues"][1]["target"] == "Spiel"
    assert result["summary"]["blocking_cells"] == 1
    assert values == original


def test_unverified_is_yellow_and_parsed_is_not_pass():
    values = artifact_inputs()
    values["final_snapshot"]["judged_revisions"] = {"11": "r1"}
    values["unit_results"] = [
        {
            "unit_id": 12,
            "input_revision": "r2",
            "severity": "minor",
            "live_seat_results": {"1": "parsed", "2": "parsed"},
        }
    ]
    result = build(values)
    assert result["highlights"] == [
        {"sheet_id": "c", "row": 4, "column": 3, "category": "unverified"}
    ]
    assert result["issues"][0]["unverified"] is True
    assert result["issues"][0]["blocking"] is False
    assert result["issues"][0]["judge_1"] == "parsed"


def test_blocking_overrides_yellow_but_report_keeps_both_reasons():
    values = artifact_inputs()
    values["final_snapshot"]["judged_revisions"].pop("12")
    values["final_snapshot"]["issues"] = [
        {
            "group_id": "repeat",
            "kind": "repeat-drift",
            "hard": False,
            "advisory": True,
            "unit_ids": [11, 12],
        }
    ]
    result = build(values)
    assert [h["category"] for h in result["highlights"]] == ["blocking", "blocking"]
    assert result["summary"]["blocking_cells"] == 2
    assert result["summary"]["unverified_cells"] == 1
    assert result["summary"]["highlighted_cells"] == 2
    assert result["summary"]["repeat_groups"] == 1
    assert any(i["unit_id"] == 12 and i["unverified"] for i in result["issues"])


@pytest.mark.parametrize(
    "defect", ["missing", "source_column", "wrong_language", "revision", "foreign", "collision"]
)
def test_missing_or_unsafe_binding_prevents_publication(defect):
    values = artifact_inputs()
    if defect == "missing":
        values["bindings"].pop()
    elif defect == "source_column":
        values["bindings"][1]["column"] = 2
    elif defect == "wrong_language":
        values["bindings"][1]["language"] = "fr"
    elif defect == "revision":
        values["bindings"][1]["revision"] = "old"
    elif defect == "foreign":
        values["bindings"][1]["unit_id"] = 99
    else:
        values["bindings"][1].update(row=2, key="one")
    with pytest.raises(DomainError):
        build(values)


@pytest.mark.parametrize(
    "severity,revision,blocking",
    [
        ("major", "r2", False),
        ("critical", "r2", False),
        ("minor", "r2", False),
        ("major", "old", False),
    ],
)
def test_run_severity_is_not_evidence_of_current_critical_issue(severity, revision, blocking):
    values = artifact_inputs()
    values["final_snapshot"]["judged_revisions"].pop("12")
    values["unit_results"] = [
        {"unit_id": 12, "severity": severity, "input_revision": revision, "live_seat_results": {}}
    ]
    result = build(values)
    assert result["summary"]["blocking_cells"] == int(blocking)


def test_clean_file_has_empty_report_and_ignores_historical_major():
    values = artifact_inputs()
    values["unit_results"] = [{"unit_id": 12, "severity": "major", "input_revision": "old"}]
    result = build(values)
    assert result["issues"] == []
    assert result["highlights"] == []
    assert result["summary"]["processed"] == 2


def test_final_snapshot_proves_both_approvals_even_if_earlier_decision_was_repaired():
    values = artifact_inputs()
    values["final_snapshot"]["issues"] = [
        {
            "group_id": "glossary",
            "kind": "glossary",
            "hard": True,
            "advisory": False,
            "unit_ids": [11],
        }
    ]
    values["unit_results"] = [
        {"unit_id": 11, "severity": "major", "input_revision": "before-repair"}
    ]
    result = build(values)
    assert result["issues"][0]["judge_1"] == "approved"
    assert result["issues"][0]["judge_2"] == "approved"
    assert result["summary"]["judge_seats"] == {"1": {"approved": 2}, "2": {"approved": 2}}


def test_remote_binding_requires_source_key_and_exact_scope():
    from app.services.quality_artifacts import bind_quality_cells

    values = artifact_inputs()
    remote = {
        "issues": [],
        "components": {
            "remote": {
                "complete": True,
                "source": "en",
                "units": [
                    {
                        "key": "one",
                        "source": "Play",
                        "target_ids": {"de": 11},
                        "targets": {"de": "Spielen"},
                    },
                    {
                        "key": "two",
                        "source": "Play",
                        "target_ids": {"de": 12},
                        "targets": {"de": "Spiel"},
                    },
                ],
            }
        },
    }
    result = bind_quality_cells(
        values["manifest"],
        values["exported"],
        remote,
        {"kit.xlsx\u001fStrings": "remote"},
        values["final_snapshot"],
        "de",
    )
    assert result == values["bindings"]
    remote["components"]["remote"]["units"][1]["source"] = "Changed"
    with pytest.raises(DomainError):
        bind_quality_cells(
            values["manifest"],
            values["exported"],
            remote,
            {"kit.xlsx\u001fStrings": "remote"},
            values["final_snapshot"],
            "de",
        )


def test_multiple_occurrences_and_sheets_use_exact_emitted_coordinates():
    values = artifact_inputs()
    second = copy.deepcopy(values["manifest"]["components"][0])
    second.update(id="c2", file="second.xlsx", sheet="Second")
    values["manifest"]["components"].append(second)
    sheet = copy.deepcopy(values["exported"]["sheets"][0])
    sheet.update(id="c2", file="second.xlsx", name="Second")
    values["exported"]["sheets"].append(sheet)
    values["bindings"].extend(
        [{**b, "component_id": "c2", "sheet_id": "c2"} for b in list(values["bindings"])]
    )
    values["final_snapshot"]["judged_revisions"].pop("12")
    result = build(values)
    assert result["highlights"] == [
        {"sheet_id": "c", "row": 4, "column": 3, "category": "unverified"},
        {"sheet_id": "c2", "row": 4, "column": 3, "category": "unverified"},
    ]
    assert result["summary"]["processed"] == 2
    assert result["summary"]["unverified_cells"] == 2


def test_v2_added_language_uses_export_column_and_original_row():
    from app.services.quality_artifacts import bind_quality_cells

    values = artifact_inputs()
    manifest = values["manifest"]
    manifest["manifest_version"] = 2
    component = manifest["components"][0]
    component.update(logical_sheet_id="logical", header_row=0)
    component["units"][1].update(row=3, original_row=4)
    profile = {"header_row": 1, "languages": [{"code": "en", "column": 2}]}
    manifest["sheets"] = [{"id": "logical", "profile": profile}]
    values["exported"]["sheets"][0]["id"] = "logical"
    for binding in values["bindings"]:
        binding["sheet_id"] = "logical"
    assert build(values)["highlights"] == []
    remote = {
        "issues": [],
        "components": {
            "remote": {
                "complete": True,
                "source": "en",
                "units": [
                    {
                        "key": "one",
                        "source": "Play",
                        "target_ids": {"de": 11},
                        "targets": {"de": "Spielen"},
                    },
                    {
                        "key": "two",
                        "source": "Play",
                        "target_ids": {"de": 12},
                        "targets": {"de": "Spiel"},
                    },
                ],
            }
        },
    }
    assert (
        bind_quality_cells(
            manifest,
            values["exported"],
            remote,
            {"kit.xlsx\u001fStrings": "remote"},
            values["final_snapshot"],
            "de",
        )
        == values["bindings"]
    )
