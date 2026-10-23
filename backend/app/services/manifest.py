# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


def component_source(manifest: dict, component: dict) -> str:
    return component.get("source", manifest["source"])


def component_targets(manifest: dict, component: dict) -> list[str]:
    return [code for code in manifest["targets"] if code != component_source(manifest, component)]


def logical_sheet_identity(component: dict) -> str:
    return component["file"] + "\u001f" + component["sheet"]


def component_mapping_key(manifest: dict, component: dict) -> str:
    key = logical_sheet_identity(component)
    source = component_source(manifest, component)
    return key if source == manifest["source"] else key + "\u001fsource:" + source
