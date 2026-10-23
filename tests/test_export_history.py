# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import io

import pytest
from app.db import DomainError, Store
from app.models import Job
from app.schemas import UploadPatch
from app.services.drafts import create_draft, patch_draft
from app.services.exports import build_export, export_file, export_summary, project_snapshot
from openpyxl import load_workbook


@pytest.mark.parametrize(
    "latest_german,ignored_column", [("Neu", False), ("", False), ("Neu", True)]
)
def test_exports_keep_languages_across_colliding_added_columns_and_repeat_runs(
    tmp_path, latest_german, ignored_column
):
    store = Store(tmp_path)

    def completed(
        run_id,
        code,
        text,
        *,
        project="local",
        remote="remote",
        source="Играть",
        finished=None,
        ignored=False,
    ):
        draft = create_draft(
            store,
            "producer",
            project,
            [
                (
                    "kit.csv",
                    (
                        f"Key,ru,de\nplay,{source},service-data\n"
                        if ignored
                        else f"Key,ru\nplay,{source}\n"
                    ).encode(),
                )
            ],
            "string",
            "quick",
        )
        draft = patch_draft(
            store,
            "producer",
            draft["token"],
            UploadPatch(
                revision=draft["revision"],
                ui_mode="quick",
                current_step="confirm",
                answers={
                    "selected_sheets": [{"file": "kit.csv", "sheet": "kit"}],
                    "source_language": "ru",
                    "included_target_languages": [code],
                    "column_roles": [
                        {"file": "kit.csv", "sheet": "kit", "column": 3, "role": "ignored"}
                    ]
                    if ignored
                    else [],
                },
            ),
        )
        manifest = draft["manifest"]
        exported = build_export(manifest, {"kit-1": {code: {"play": text}}})
        exported["components"] = {
            "kit-1": build_export(manifest, {"kit-1": {code: {"play": text}}})
        }
        export_id = "export:" + run_id
        store.save("export", export_id, "producer", exported)
        with store.session() as session:
            session.add(
                Job(
                    id=run_id,
                    owner="producer",
                    idempotency_key=run_id,
                    status="completed",
                    data={
                        "project": project,
                        "remote_project": remote,
                        "created": run_id,
                        "finished": finished or run_id,
                        "snapshot": {"manifest": manifest},
                        "export_id": export_id,
                    },
                )
            )
        return exported

    original = completed("01", "de", "Spielen")
    completed("02", "es", "Jugar", remote="another-remote")
    completed("03", "fr", "Jouer", project="another-local")
    completed("04", "it", "Gioca", source="Другой исходник")
    completed("05", "en", "Play", ignored=ignored_column)
    store.save(
        "export_pointer",
        "export-pointer:local",
        "producer",
        {"run_id": "05", "export_id": "export:05"},
    )
    summary = export_summary(store, "producer", "local", ["kit-1"])
    assert summary["per_language"] == {
        "de": {"strings": 1, "translated": 1, "empty": 0, "blocked": 0},
        "en": {"strings": 1, "translated": 1, "empty": 0, "blocked": 0},
    }
    payload, _, _ = export_file(
        store,
        "producer",
        "local",
        [],
        "xlsx",
        export_summary(store, "producer", "local", [])["revision"],
    )
    rows = list(load_workbook(io.BytesIO(payload)).active.values)
    if ignored_column:
        assert rows == [
            ("Key", "ru", "de", "en", "de"),
            ("play", "Играть", "service-data", "Play", "Spielen"),
        ]
    else:
        assert rows == [("Key", "ru", "en", "de"), ("play", "Играть", "Play", "Spielen")]

    completed("06", "de", latest_german)
    store.save(
        "export_pointer",
        "export-pointer:local",
        "producer",
        {"run_id": "06", "export_id": "export:06"},
    )
    current = project_snapshot(store, "producer", "local")
    assert current["sheets"][0]["rows"] == [
        ["Key", "ru", "de", "en"],
        ["play", "Играть", latest_german, "Play"],
    ]
    assert current["per_language"]["de"]["empty"] == int(not latest_german)
    saved = store.get("export", "export:01", "producer")
    saved.pop("revision")
    assert saved == original
    with pytest.raises(DomainError, match="Reload the export summary"):
        export_file(store, "producer", "local", ["kit-1"], "xlsx", summary["revision"])
    with pytest.raises(DomainError, match="Not found"):
        project_snapshot(store, "another-owner", "local")
    with pytest.raises(DomainError, match="Unknown content"):
        export_summary(store, "producer", "local", ["missing"])
    # Resuming an older-created run must not hide languages completed afterwards.
    completed("00", "de", "Wieder", finished="07")
    store.save(
        "export_pointer",
        "export-pointer:local",
        "producer",
        {"run_id": "00", "export_id": "export:00"},
    )
    resumed = project_snapshot(store, "producer", "local")
    assert resumed["sheets"][0]["rows"] == [
        ["Key", "ru", "de", "en"],
        ["play", "Играть", "Wieder", "Play"],
    ]
