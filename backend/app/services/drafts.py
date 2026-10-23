# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path

from loc_kit_ingest.infer import InferenceError
from loc_kit_ingest.profile import ProfileError
from loc_kit_ingest.reader import ReaderError

from app.db import DomainError
from app.services.kits import analyze_file, materialize_inputs, prepare_kit


def get_draft(store, token, owner):
    draft = store.get("draft", token, owner)
    if draft["expires"] < time.time():
        raise DomainError("not_found", "Draft expired", 404)
    return draft


def public_draft(draft):
    public = {
        key: draft[key]
        for key in [
            "token",
            "revision",
            "kind",
            "ui_mode",
            "current_step",
            "answers",
            "analysis",
            "attention",
            "errors",
        ]
    }
    public["analysis"] = copy.deepcopy(public["analysis"])
    if draft.get("manifest"):
        public["analysis"]["confirmation"] = public_intake_summary(draft["manifest"])
    return public


def public_intake_summary(manifest: dict) -> dict:
    from collections import Counter

    problems = [
        r
        for r in manifest.get("quarantine", [])
        if r.get("reason_code", r.get("reason")) != "blank"
    ]
    blank = len(manifest.get("quarantine", [])) - len(problems)
    totals = dict(manifest["totals"])
    totals.update(input_rows=max(0, totals["input_rows"] - blank), quarantined_rows=len(problems))
    fallback = [
        {
            "file": c["file"],
            "sheet": c["sheet"],
            "row": u.get("original_row", u["row"]),
            "language": u["effective_source"],
            "declared_source": u.get("declared_source", manifest.get("source")),
        }
        for c in manifest.get("components", [])
        for u in c["units"]
        if u.get("source_origin") == "fallback"
    ]
    reasons = dict(Counter(r.get("reason_code", r.get("reason")) for r in problems))
    return {
        "totals": totals,
        "components": [
            {"id": c["id"], "sheet": c["sheet"], "keys": len(c["units"])}
            for c in manifest.get("components", [])
        ],
        "quarantine_reasons": reasons,
        "problem_counts": reasons,
        "fallback_counts": dict(Counter(r["language"] for r in fallback)),
        "fallback_rows": fallback[:500],
        "fallback_details_truncated": len(fallback) > 500,
    }


def restore_latest_kit(store, owner, slug):
    active = [
        d
        for d in store.list("draft", owner)
        if d["project"] == slug and d["kind"] == "string" and d["expires"] > time.time()
    ]
    if active:
        return active[-1]
    versions = [
        v
        for v in store.list("kit_version", owner)
        if v["project"] == slug
        and v.get("kind", "string") == "string"
        and (v["manifest"]["components"] or v["manifest"].get("sheets"))
        and all(c["kind"] == "po" for c in v["manifest"]["components"])
    ]
    if not versions:
        raise DomainError("not_found", "No saved loc-kit", 404)
    version = max(versions, key=lambda v: v["created"])
    relative = Path(version["originals"])
    directory = store.data_dir / relative
    root = (store.data_dir / "versions").resolve()
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or directory.is_symlink()
        or not directory.resolve().is_relative_to(root)
        or not directory.is_dir()
        or any(p.is_symlink() for p in directory.rglob("*"))
    ):
        raise DomainError("original_missing", "Сохранённый лок-кит недоступен", 409)
    manifest = version["manifest"]
    components = manifest["components"]
    logical_sheets = manifest.get("sheets") or components
    draft_sheet_ids = manifest.get("sheet_ids", {})
    names = list(dict.fromkeys(c["file"] for c in logical_sheets))
    mapping = {f["name"]: f["filename"] for f in version.get("files", [])}
    uploads = []
    used = set()
    for name in names:
        if name in mapping:
            filename = Path(mapping[name])
            if filename.name != str(filename):
                raise DomainError("original_missing", "Сохранённый лок-кит недоступен", 409)
            candidates = [directory / filename]
        else:
            # Legacy versions kept UUID filenames. Match every selected sheet/header,
            # and refuse ambiguity instead of choosing an arbitrary workbook.
            candidates = []
            for path in directory.iterdir():
                if path.suffix.lower() not in {".xlsx", ".csv", ".tsv", ".txt"}:
                    continue
                if path.stat().st_size > 20 * 1024 * 1024:
                    continue
                _, sheets, _ = analyze_file(path, name)
                if path.suffix.lower() != ".xlsx":
                    sheets = {Path(name).stem: next(iter(sheets.values()))}
                selected = [c for c in logical_sheets if c["file"] == name]
                if all(
                    c["sheet"] in sheets
                    and len(sheets[c["sheet"]]) > c["header_row"]
                    and c["rows"][c["header_row"]][: len(sheets[c["sheet"]][c["header_row"]])]
                    == list(sheets[c["sheet"]][c["header_row"]])
                    for c in selected
                ):
                    candidates.append(path)
        if len(candidates) != 1 or not candidates[0].is_file() or candidates[0] in used:
            raise DomainError("original_missing", "Не удалось однозначно восстановить лок-кит", 409)
        used.add(candidates[0])
        uploads.append((name, candidates[0].read_bytes()))
    draft = create_draft(store, owner, slug, uploads, "string", version.get("ui_mode", "quick"))
    answers = copy.deepcopy(version.get("answers"))
    if answers is None:
        roles = []
        for c in logical_sheets:
            identity = {"file": c["file"], "sheet": c["sheet"]}
            profile = c["profile"]
            for lang in profile.get("languages", []):
                # Synthetic columns for newly added targets do not exist in the original.
                record = next(r for r in draft["_files"] if r["name"] == c["file"])
                if lang["column"] <= len(record["sheets"][c["sheet"]][c["header_row"]]):
                    roles.append(
                        {
                            **identity,
                            "role": "language",
                            "column": lang["column"],
                            "language": lang["code"],
                        }
                    )
            for field, role in [
                ("key", "key"),
                ("comments", "comment"),
                ("references", "reference"),
                ("ignored_columns", "ignored"),
                ("explanation", "explanation"),
                ("flags", "flags"),
            ]:
                values = profile.get(field, [])
                for item in values if isinstance(values, list) else [values]:
                    if item and "column" in item:
                        roles.append({**identity, "role": role, "column": item["column"]})
        answers = {
            "selected_files": names,
            "selected_sheets": [{"file": c["file"], "sheet": c["sheet"]} for c in logical_sheets],
            "source_language": manifest["source"],
            "included_target_languages": manifest["targets"],
            "column_roles": roles,
        }
    draft.pop("revision")
    draft["answers"] = answers
    draft["sheet_ids"] = draft_sheet_ids
    draft["manifest"] = copy.deepcopy(manifest)
    draft["analysis"]["confirmation"] = {
        "totals": manifest["totals"],
        "components": [
            {"id": c["id"], "sheet": c["sheet"], "keys": len(c["units"])} for c in components
        ],
        "quarantine_reasons": {},
    }
    selected = {(s["file"], s["sheet"]) for s in answers["selected_sheets"]}
    for file in draft["analysis"]["loc_kit"]["files"]:
        file["selected"] = file["path"] in answers["selected_files"]
        for sheet in file["sheets"]:
            sheet["selected"] = (file["path"], sheet["name"]) in selected
    return store.save("draft", draft["token"], owner, draft)


def create_draft(store, owner, slug, uploads, kind, mode):
    versions = [
        v
        for v in store.list("kit_version", owner)
        if v["project"] == slug and v.get("kind", "string") == kind
    ]
    previous = max(versions, key=lambda v: v["created"])["manifest"] if versions else {}
    sheet_ids = dict(previous.get("sheet_ids", {}))
    for c in previous.get("components", []):
        sheet_ids.setdefault(c["file"] + "\u001f" + c["sheet"], c.get("logical_sheet_id", c["id"]))
    token = str(uuid.uuid4())
    records = materialize_inputs(store.data_dir / "uploads" / token, uploads)
    analysis = {
        "detected_kind": kind,
        "loc_kit": {"files": [r["analysis"] for r in records], "collisions": []},
        "formula_cells": [
            f for r in records for sh in r["analysis"]["sheets"] for f in sh["formula_cells"]
        ],
    }
    draft = {
        "token": token,
        "project": slug,
        "kind": kind,
        "ui_mode": mode,
        "current_step": "select-content",
        "answers": {},
        "analysis": analysis,
        "attention": [],
        "errors": [],
        "expires": time.time() + 3600,
        "_files": records,
        "manifest": None,
        "sheet_ids": sheet_ids,
    }
    return store.save("draft", token, owner, draft)


def patch_draft(store, owner, token, patch):
    draft = get_draft(store, token, owner)
    expected = patch.revision
    draft.pop("revision", None)
    draft["ui_mode"] = patch.ui_mode
    draft["current_step"] = patch.current_step
    if patch.answers is not None:
        answers = patch.answers.model_dump()
        for selected in answers["selected_sheets"]:
            record = next((r for r in draft["_files"] if r["name"] == selected["file"]), None)
            if record is None:
                raise DomainError("sheet_not_found", "Unknown file")
            if selected["sheet"] in {r["sheet"] for r in record["excluded"]}:
                raise DomainError("sheet_not_visible", "Hidden worksheets cannot be selected")
            if selected["sheet"] not in record["sheets"]:
                raise DomainError("sheet_not_found", "Unknown worksheet")
        if len({(r["file"], r["sheet"]) for r in answers["selected_sheets"]}) != len(
            answers["selected_sheets"]
        ):
            raise DomainError("duplicate_sheet", "Select each worksheet once")
        draft["answers"] = answers
        draft["manifest"] = None
        draft["errors"] = []
        draft["analysis"].pop("confirmation", None)
        if (
            answers["source_language"]
            and answers["included_target_languages"]
            and answers["selected_sheets"]
        ):
            try:
                manifest = prepare_kit(draft)
                draft["manifest"] = manifest
                from collections import Counter

                draft["analysis"]["confirmation"] = {
                    "totals": manifest["totals"],
                    "components": [
                        {"id": c["id"], "sheet": c["sheet"], "keys": len(c["units"])}
                        for c in manifest["components"]
                    ],
                    "quarantine_reasons": dict(
                        Counter(row["reason_code"] for row in manifest["quarantine"])
                    ),
                }
            except (DomainError, ValueError, InferenceError, ProfileError, ReaderError) as e:
                draft["errors"] = [
                    {
                        "code": getattr(e, "code", "kit_validation"),
                        "scope": "kit",
                        "detail": str(getattr(e, "detail", e)),
                    }
                ]
        selected = {(s["file"], s["sheet"]) for s in answers["selected_sheets"]}
        for file in draft["analysis"]["loc_kit"]["files"]:
            file["selected"] = file["path"] in answers["selected_files"]
            for sheet in file["sheets"]:
                sheet["selected"] = (file["path"], sheet["name"]) in selected
    draft["expires"] = time.time() + 3600
    return store.save("draft", token, owner, draft, expected)
