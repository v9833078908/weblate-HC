# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import json

import pytest
from app.db import DomainError, revision
from app.services import incremental


def test_incremental_partial_progress_survives_reload_and_keeps_absolute_bound(
    tmp_path, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(incremental.time, "time", lambda: clock[0])
    selected = [f"new{i}" for i in range(6)]
    units = [{"key": "old", "source": "Old", "targets": {"de": "Alt"}, "target_ids": {"de": 2}}]
    units += [
        {"key": key, "source": key, "targets": {"de": ""}, "target_ids": {"de": 4 + i * 2}}
        for i, key in enumerate(selected)
    ]
    profile = {"persona": "P", "style": "S", "language_instructions": {}}
    snapshot = {
        "slug": "game",
        "profile": {"openrouter": profile},
        "components": {
            "ui": {
                "source": "ru",
                "languages": ["de"],
                "complete": True,
                "file_format": "po-mono",
                "units": units,
            }
        },
    }
    data = {
        "remote_project": "game",
        "operations": {},
        "snapshot": {
            "manifest": {"source": "ru", "targets": ["de"]},
            "profile": profile,
            "change_plan": {
                "snapshot": copy.deepcopy(snapshot),
                "snapshot_hash": revision(snapshot),
                "components": [
                    {
                        "local_id": "kit1",
                        "remote_id": "ui",
                        "new": [],
                        "seed": {"de": {}},
                        "translate": {"de": selected},
                    }
                ],
            },
        },
    }
    saved = tmp_path / "checkpoint.json"

    def checkpoint():
        saved.write_text(json.dumps(data))

    class Remote:
        posts = 0

        def start_scoped_translation(self, project, component, language, ids):
            assert (project, component, language, ids) == (
                "game",
                "ui",
                "de",
                [4, 6, 8, 10, 12, 14],
            )
            self.posts += 1
            raise DomainError("weblate_unknown", "Lost response", 503)

    remote = Remote()
    for elapsed, index in [(0, None), (1200, 1), (2400, 2), (3600, 3), (4800, 4), (6000, 5)]:
        clock[0] = 1000 + elapsed
        if index is not None:
            units[index]["targets"]["de"] = "Neu"
        with pytest.raises(DomainError) as error:
            incremental.process_incremental(
                data, remote, checkpoint, snapshot_loader=lambda: copy.deepcopy(snapshot)
            )
        assert error.value.code == "incremental_wait"
        data = json.loads(saved.read_text())
        observation = data["operations"]["delta:mt:ui:de:0"]
        assert observation["observation"]["filled"] == (index or 0)
        assert observation["observation"]["total"] == 6
        assert observation["observe_until"] <= 8200
    clock[0] = 8201
    with pytest.raises(DomainError) as error:
        incremental.process_incremental(
            data, remote, checkpoint, snapshot_loader=lambda: copy.deepcopy(snapshot)
        )
    assert error.value.code == "weblate_unknown"
    assert remote.posts == 1 and units[0]["targets"]["de"] == "Alt"
