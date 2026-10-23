# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.services import kit_diff


def test_source_switch_is_conflict_across_groups(tmp_path):
    from test_multisource_manifest import prepared

    manifest = prepared(tmp_path)
    remote = {
        "slug": "game",
        "components": {
            "old": {
                "source": "de",
                "complete": True,
                "can_add_source": True,
                "units": [{"key": "b", "source": "Мир", "targets": {"pl": "Keep"}}],
            }
        },
    }
    plan = kit_diff.compare_kit(
        remote, manifest, {"kit.csv\u001fkit": "old"}, allow_new_groups=True
    )
    assert any(b.get("code") == "source_changed" for b in plan["blocked"])
    assert not any(u["key"] == "b" for g in plan["components"] for u in g["new"])


def test_new_fallback_group_requires_owned_permission(tmp_path):
    from test_multisource_manifest import prepared

    manifest = prepared(tmp_path)
    remote = {
        "slug": "game",
        "components": {
            "kit-1": {
                "source": "de",
                "complete": True,
                "can_add_source": True,
                "units": [{"key": "a", "source": "Hallo", "targets": {}}],
            }
        },
    }
    mapping = {"kit.csv\u001fkit": "kit-1"}
    assert kit_diff.compare_kit(remote, manifest, mapping)["blocked"]
    plan = kit_diff.compare_kit(remote, manifest, mapping, allow_new_groups=True)
    assert not plan["blocked"]
    assert [g["source"] for g in plan["components"] if g.get("create_component")] == ["ru", "en"]
    assert plan["mapping"]["kit.csv\u001fkit\u001fsource:ru"] == "kit-1-src-ru"


def test_repeated_fallback_rows_do_not_translate_again(tmp_path):
    from app.services.manifest import component_mapping_key
    from test_multisource_manifest import prepared

    manifest = prepared(tmp_path)
    remote = {
        "slug": "game",
        "components": {
            c["id"]: {
                "source": c["source"],
                "complete": True,
                "can_add_source": True,
                "languages": c["targets"],
                "units": [
                    {
                        "key": u["key"],
                        "source": u["values"][c["source"]],
                        "targets": {code: "Keep" for code in c["targets"]},
                    }
                    for u in c["units"]
                ],
            }
            for c in manifest["components"]
        },
    }
    mapping = {component_mapping_key(manifest, c): c["id"] for c in manifest["components"]}
    plan = kit_diff.compare_kit(remote, manifest, mapping, allow_new_groups=True)
    assert not plan["blocked"]
    assert not any(
        g.get("create_component") or g["new"] or any(g["translate"].values())
        for g in plan["components"]
    )


def test_diff_preserves_old_targets_and_requires_explicit_component_mapping():
    remote = {
        "slug": "game",
        "components": {
            "ui": {
                "source": "ru",
                "complete": True,
                "can_add_source": True,
                "units": [
                    {
                        "key": "same",
                        "source": "Привет",
                        "targets": {"de": "Hallo"},
                        "target_ids": {"de": 2},
                    },
                    {"key": "changed", "source": "Старый", "targets": {}},
                    {"key": "removed", "source": "Убрано", "targets": {}},
                ],
            }
        },
    }
    manifest = {
        "source": "ru",
        "targets": ["de", "en"],
        "components": [
            {
                "id": "kit2",
                "file": "kit.xlsx",
                "sheet": "UI",
                "units": [
                    {"key": "same", "values": {"ru": "Привет", "de": "Overwrite"}},
                    {"key": "changed", "values": {"ru": "Новый"}},
                    {"key": "new", "values": {"ru": "Новая"}},
                ],
            }
        ],
    }
    assert hasattr(kit_diff, "compare_kit"), "kit diff is missing"
    assert kit_diff.compare_kit(remote, manifest, {})["blocked"]
    plan = kit_diff.compare_kit(remote, manifest, {"kit.xlsx\u001fUI": "ui"})
    group = plan["components"][0]
    assert [u["key"] for u in group["new"]] == ["new"]
    assert group["retained"] == ["removed"]
    assert group["changed"] == ["changed"]
    assert group["translate"] == {"de": ["new"], "en": ["same", "new"]}
    assert group["seed"] == {"de": {}, "en": {}}
    assert plan["blocked"]  # changed source needs exclusion/decision
