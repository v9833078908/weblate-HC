# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""One-shot, offline cleanup of the three explicitly approved wizard cards."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from uuid import UUID

APPROVED = [
    ("producer", "test-5dacb142c2714e46"),
    ("producer", "wizard-58022f2c0c50452a"),
    ("producer", "wizard-058c83ed9e414dbb"),
]


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from string_values(child)


def connect(data_dir: Path) -> sqlite3.Connection:
    db = data_dir / "wizard.sqlite"
    if db.is_symlink():
        raise ValueError("Database symlink is forbidden")
    connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def file_manifest(data_dir: Path, folder: str, uid: str) -> dict:
    if str(UUID(uid)) != uid:
        raise ValueError("Invalid directory UUID")
    base = data_dir / folder
    path = base / uid
    if base.is_symlink() or path.is_symlink():
        raise ValueError("Directory symlink is forbidden")
    if path.resolve().parent != base.resolve() or base.resolve().parent != data_dir:
        raise ValueError("Directory escapes data root")
    if not path.exists():
        return {"path": folder + "/" + uid, "exists": False, "files": []}
    if not path.is_dir():
        raise ValueError("Expected directory")
    files = []
    for child in sorted(path.rglob("*")):
        if child.is_symlink():
            raise ValueError("File symlink is forbidden")
        if child.is_file():
            files.append(
                {
                    "path": str(child.relative_to(path)).replace("\\", "/"),
                    "sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
                }
            )
        elif not child.is_dir():
            raise ValueError("Special files are forbidden")
    return {"path": folder + "/" + uid, "exists": True, "files": files}


def build_cleanup_manifest(data_dir: Path, projects: list[tuple[str, str]]) -> dict:
    data_dir = data_dir.resolve()
    if not projects or len(set(projects)) != len(projects) or not set(projects) <= set(APPROVED):
        raise ValueError("Project is outside the approved allowlist")
    with connect(data_dir) as db:
        records = [dict(row) for row in db.execute("SELECT * FROM records ORDER BY id")]
        jobs = [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY id")]
    values = {row["id"]: json.loads(row["data"]) for row in records}
    selected_jobs = [
        row for row in jobs if (row["owner"], json.loads(row["data"]).get("project")) in projects
    ]
    for row in selected_jobs:
        if (
            row["status"] in {"queued", "running", "unknown", "cancel_requested"}
            or row["lease_until"] > time.time()
        ):
            raise ValueError("Job is active")
        operations = json.loads(row["data"]).get("operations", {})
        if any(op.get("state") in {"intent", "accepted"} for op in operations.values()):
            raise ValueError("Job has an unconfirmed operation")
    selected = set()
    for owner, slug in projects:
        card = next(
            (
                row
                for row in records
                if row["id"] == slug and row["kind"] == "project" and row["owner"] == owner
            ),
            None,
        )
        if card is None:
            raise ValueError("Approved project is missing")
        selected.add(card["id"])
        for row in records:
            if row["owner"] != owner:
                continue
            if (
                (row["kind"] == "profile" and row["id"] == "profile:" + slug)
                or (row["kind"] == "export_pointer" and row["id"] == "export-pointer:" + slug)
                or (
                    row["kind"] in {"draft", "bdhc_preview"}
                    and values[row["id"]].get("project") == slug
                )
            ):
                selected.add(row["id"])
    exports = {json.loads(row["data"]).get("export_id") for row in selected_jobs}
    exports |= {values[key].get("export_id") for key in selected}
    exports.discard(None)
    for export_id in exports:
        match = next(
            (row for row in records if row["id"] == export_id and row["kind"] == "export"), None
        )
        if match is None or match["owner"] not in {owner for owner, _ in projects}:
            raise ValueError("Export association is invalid")
        selected.add(export_id)
    selected_job_ids = {row["id"] for row in selected_jobs}
    for row in records + jobs:
        inside = row["id"] in (selected if "kind" in row else selected_job_ids)
        if not inside and json.loads(row["data"]).get("export_id") in exports:
            raise ValueError("Export is shared with another project")
    dirs = [
        ("uploads", key)
        for key in selected
        if next(row for row in records if row["id"] == key)["kind"] == "draft"
    ]
    dirs += [("runs", row["id"]) for row in selected_jobs]
    files = [file_manifest(data_dir, folder, uid) for folder, uid in sorted(dirs)]
    for row in records + jobs:
        inside = row["id"] in (selected if "kind" in row else selected_job_ids)
        if not inside:
            for entry in files:
                directory = (data_dir / entry["path"]).resolve()
                for text in string_values(json.loads(row["data"])):
                    try:
                        path = Path(text)
                        path = (path if path.is_absolute() else data_dir / path).resolve()
                    except (OSError, ValueError):
                        continue
                    if path.is_relative_to(directory):
                        raise ValueError("File directory is shared with another project")
    return {
        "version": 1,
        "projects": [list(pair) for pair in sorted(projects)],
        "records": [
            {"id": row["id"], "kind": row["kind"], "sha256": digest(row)}
            for row in records
            if row["id"] in selected
        ],
        "jobs": [{"id": row["id"], "sha256": digest(row)} for row in selected_jobs],
        "files": files,
    }


def apply_cleanup_manifest(
    data_dir: Path, manifest: dict, archive_dir: Path, expected_digest: str
) -> dict:
    data_dir = data_dir.resolve()
    archive_dir = archive_dir.resolve()
    if archive_dir.is_relative_to(data_dir):
        raise ValueError("Archive must be outside the live data directory")
    if digest(manifest) != expected_digest:
        raise ValueError("Manifest digest changed")
    projects = [tuple(pair) for pair in manifest["projects"]]
    with sqlite3.connect(data_dir / "wizard.sqlite") as db:
        db.execute("BEGIN IMMEDIATE")
        if digest(build_cleanup_manifest(data_dir, projects)) != expected_digest:
            raise ValueError("Project data changed since the manifest was prepared")
        archive_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        (archive_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (archive_dir / "manifest.sha256").write_text(expected_digest, encoding="ascii")
        with connect(data_dir) as source, sqlite3.connect(archive_dir / "wizard.sqlite") as backup:
            source.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Backup failed integrity check")
        (archive_dir / "wizard.sqlite").chmod(0o600)
        for entry in manifest["files"]:
            if entry["exists"]:
                destination = archive_dir / "files-backup" / entry["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(data_dir / entry["path"], destination)
                for file in entry["files"]:
                    if (
                        hashlib.sha256((destination / file["path"]).read_bytes()).hexdigest()
                        != file["sha256"]
                    ):
                        raise ValueError("File backup failed verification")
        for entry in manifest["records"]:
            if db.execute("DELETE FROM records WHERE id=?", (entry["id"],)).rowcount != 1:
                raise ValueError("Record changed")
        for entry in manifest["jobs"]:
            if db.execute("DELETE FROM jobs WHERE id=?", (entry["id"],)).rowcount != 1:
                raise ValueError("Job changed")
        db.commit()
    # The full database and verified file copies already exist if moving fails.
    moved = []
    report = {
        "deleted_records": len(manifest["records"]),
        "deleted_jobs": len(manifest["jobs"]),
        "archived_directories": moved,
        "remaining_directories": [entry["path"] for entry in manifest["files"] if entry["exists"]],
    }
    result_path = archive_dir / "result.json"
    result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        for entry in manifest["files"]:
            if not entry["exists"]:
                continue
            destination = archive_dir / "removed" / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(data_dir / entry["path"]), str(destination))
            moved.append(entry["path"])
            report["remaining_directories"].remove(entry["path"])
            result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-digest")
    args = parser.parse_args()
    if args.apply:
        if not args.archive_dir or not args.expected_digest:
            parser.error("Applying requires archive-dir and expected-digest")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        print(
            json.dumps(
                apply_cleanup_manifest(
                    args.data_dir, manifest, args.archive_dir, args.expected_digest
                )
            )
        )
    else:
        manifest = build_cleanup_manifest(args.data_dir, APPROVED)
        args.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "digest": digest(manifest),
                    "records": len(manifest["records"]),
                    "jobs": len(manifest["jobs"]),
                    "directories": sum(entry["exists"] for entry in manifest["files"]),
                }
            )
        )


if __name__ == "__main__":
    main()
