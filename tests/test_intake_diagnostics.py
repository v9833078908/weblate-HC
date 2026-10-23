# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

from app.services.drafts import public_intake_summary


def test_legacy_blanks_removed_without_mutating_history():
    manifest = {
        "totals": {"input_rows": 6060, "imported_rows": 1657, "quarantined_rows": 4403},
        "quarantine": [{"reason_code": "blank"} for _ in range(4382)]
        + [{"reason_code": "missing_source"} for _ in range(21)],
        "components": [],
    }
    before = copy.deepcopy(manifest)
    summary = public_intake_summary(manifest)
    assert summary["totals"] == {"input_rows": 1678, "imported_rows": 1657, "quarantined_rows": 21}
    assert summary["quarantine_reasons"] == {"missing_source": 21}
    assert manifest == before


def test_fallback_summary_is_nonblocking(tmp_path):
    from test_multisource_manifest import prepared

    summary = public_intake_summary(prepared(tmp_path))
    assert summary["fallback_counts"] == {"ru": 1, "en": 1}
    assert [r["row"] for r in summary["fallback_rows"]] == [3, 4]
    assert summary["problem_counts"] == {"missing_source": 1}


def test_formulas_are_ignored_only_in_pure_service_columns(tmp_path):
    from app.services.kits import analyze_file
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Key", "en", "", "Length", "", "ru"])
    sheet.append(["a", "Hello", "=LEN(B2)", "=LEN(B2)", "=LEN(B2)", "=B2"])
    sheet.append(["b", "Hi", "=LEN(B3)", "=LEN(B3)", "unknown text", "Привет"])
    path = tmp_path / "kit.xlsx"
    workbook.save(path)
    analysis, _, _ = analyze_file(path, path.name)
    ignored = {f["column"] for f in analysis["sheets"][0]["formula_cells"] if f["auto_ignore"]}
    assert ignored == {3, 4}


def test_all_empty_allowed_sources_remain_reviewable(tmp_path):
    from test_multisource_manifest import prepared

    manifest = prepared(tmp_path, "Key,de,ru,en,pl,es\na,,,,,Hola\nb,,,,,\n")
    assert manifest["totals"] == {"input_rows": 2, "imported_rows": 0, "quarantined_rows": 2}
    assert {q["reason_code"] for q in manifest["quarantine"]} == {"missing_source"}


def test_problem_pagination_filters_blanks_before_slicing_and_keeps_owner(tmp_path):
    from app.main import create_app
    from app.settings import Settings
    from fastapi.testclient import TestClient

    app = create_app(Settings(data_dir=tmp_path))
    app.state.store.create_user("owner", "a-long-test-password")
    app.state.store.create_user("other", "another-long-password")
    app.state.store.save(
        "draft",
        "legacy",
        "owner",
        {
            "expires": 9999999999,
            "manifest": {
                "quarantine": [{"reason_code": "blank"}] * 4382
                + [{"reason_code": "missing_source", "row": n} for n in range(205)]
            },
        },
    )
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": "owner", "password": "a-long-test-password"})
        first = client.get("/api/producer/uploads/legacy/quarantine/?page_size=200&page=1").json()
        second = client.get("/api/producer/uploads/legacy/quarantine/?page_size=200&page=2").json()
        assert first["count"] == second["count"] == 205
        assert len(first["results"]) == 200 and len(second["results"]) == 5
        assert first["next"] == 2 and second["next"] is None and second["previous"] == 1
        client.post("/auth/login", json={"username": "other", "password": "another-long-password"})
        assert client.get("/api/producer/uploads/legacy/quarantine/").status_code == 404
