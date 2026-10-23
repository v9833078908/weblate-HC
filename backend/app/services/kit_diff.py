# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

from app.db import revision
from app.services.manifest import (
    component_mapping_key,
    component_source,
    component_targets,
    logical_sheet_identity,
)


def sheet_identity(component):
    return logical_sheet_identity(component)


def compare_kit(snapshot, manifest, mapping, include_old_empty=False, *, allow_new_groups=False):
    mapping = dict(mapping)
    original_mapping = dict(mapping)
    groups, blocked, used = [], [], set()
    for component in manifest["components"]:
        source_language = component_source(manifest, component)
        targets = component_targets(manifest, component)
        identity = sheet_identity(component)
        mapping_key = component_mapping_key(manifest, component)
        cid = mapping.get(mapping_key)
        remote = snapshot["components"].get(cid)
        create_component = False
        related = {
            value
            for key, value in original_mapping.items()
            if key == identity or key.startswith(identity + "\u001fsource:")
        }
        if (
            cid is None
            and not remote
            and allow_new_groups
            and manifest.get("manifest_version") == 2
            and related
        ):
            cid = component["id"]
            if cid not in snapshot["components"]:
                mapping[mapping_key] = cid
                remote = {
                    "source": source_language,
                    "complete": True,
                    "can_add_source": True,
                    "units": [],
                    "languages": [],
                }
                create_component = True
        logical_old = {}
        for other_id in related:
            other = snapshot["components"].get(other_id, {})
            if not other.get("complete") or other.get("glossary"):
                blocked.append(
                    {"sheet": component["sheet"], "reason": "Неполный снимок связанной группы"}
                )
            for old_unit in other.get("units", []):
                logical_old.setdefault(old_unit["key"], []).append(
                    (other_id, other.get("source"), old_unit["source"])
                )
        if not remote or cid in used or remote.get("glossary"):
            blocked.append(
                {
                    "sheet": component["sheet"],
                    "reason": "Выберите отдельный компонент Weblate для листа",
                }
            )
            continue
        used.add(cid)
        if not remote.get("complete") or remote["source"] != source_language:
            blocked.append(
                {"sheet": component["sheet"], "reason": "Неполный снимок или другой исходный язык"}
            )
            continue
        old = {u["key"]: u for u in remote["units"]}
        if len(old) != len(remote["units"]):
            blocked.append(
                {"sheet": component["sheet"], "reason": "Неоднозначные ключи внешних строк"}
            )
            continue
        group = {
            "local_id": component["id"],
            "source": source_language,
            "create_component": create_component,
            "remote_id": cid,
            "sheet": component["sheet"],
            "new": [],
            "unchanged": [],
            "changed": [],
            "retained": [],
            "old_empty": {},
            "translate": {code: [] for code in targets},
            "seed": {code: {} for code in targets},
        }
        seen = set()
        for unit in component["units"]:
            key, source = unit["key"], unit["values"][source_language]
            if key in seen:
                blocked.append({"sheet": component["sheet"], "reason": "Повтор ключа: " + key})
                continue
            seen.add(key)
            prior_groups = logical_old.get(key, [])
            if prior_groups and (
                len(prior_groups) != 1 or prior_groups[0] != (cid, source_language, source)
            ):
                group["changed"].append(key)
                blocked.append(
                    {
                        "sheet": component["sheet"],
                        "code": "source_changed",
                        "reason": "Изменён язык или исходный текст строки: " + key,
                    }
                )
                continue
            previous = old.get(key)
            if previous and previous["source"] != source:
                group["changed"].append(key)
                blocked.append(
                    {"sheet": component["sheet"], "reason": "Изменён исходный текст: " + key}
                )
                continue
            if previous:
                group["unchanged"].append(key)
            else:
                group["new"].append(
                    {"key": key, "source": source, "explanation": unit.get("explanation", "")}
                )
            for code in targets:
                current = previous.get("targets", {}).get(code, "") if previous else ""
                supplied = unit["values"].get(code, "")
                new_language = code not in remote.get("languages", []) and not any(
                    code in u.get("targets", {}) for u in remote["units"]
                )
                eligible = not previous or new_language or include_old_empty
                if current:
                    continue
                if supplied and eligible:
                    group["seed"][code][key] = supplied
                elif eligible:
                    group["translate"][code].append(key)
                else:
                    group["old_empty"].setdefault(code, []).append(key)
        group["retained"] = sorted(old.keys() - seen)
        if group["new"] and not remote.get("can_add_source"):
            blocked.append(
                {
                    "sheet": component["sheet"],
                    "reason": "Компонент не подтверждает возможность добавления исходных строк",
                }
            )
        groups.append(group)
    return {
        "remote_slug": snapshot["slug"],
        "snapshot_hash": revision(snapshot),
        "manifest_hash": revision(manifest),
        "mapping": copy.deepcopy(mapping),
        "components": groups,
        "blocked": blocked,
        "include_old_empty": include_old_empty,
    }
