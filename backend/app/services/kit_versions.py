# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import shutil
import time
import uuid
from pathlib import Path

from app.db import DomainError, revision
from app.services.drafts import get_draft
from app.services.kit_diff import compare_kit
from app.services.profiles import get_profile, merge_remote_profile
from app.services.project_links import get_link, snapshot_project


def preview_changes(
    store,
    owner,
    slug,
    remote,
    upload_id,
    expected,
    mapping,
    include_old_empty=False,
    *,
    engine="openrouter",
    glossary_upload_id=None,
    glossary_revision=None,
):
    link = get_link(store, owner, slug)
    if not link:
        raise DomainError("not_connected", "Сначала подключите проект Weblate", 409)
    draft = get_draft(store, upload_id, owner)
    if draft["project"] != slug or draft["revision"] != expected:
        raise DomainError("stale-revision", "Повторите просмотр лок-кита", 409)
    if not draft.get("manifest") or draft.get("errors"):
        raise DomainError("kit_not_ready", "Сначала подтвердите листы и языки", 409)
    historical_mapping = link.get("component_map", {})
    if (
        not link["read_only"]
        and link.get("origin") == "created-v2"
        and any(
            key in historical_mapping and historical_mapping[key] != value
            for key, value in mapping.items()
        )
    ):
        raise DomainError(
            "component_mapping_conflict",
            "Лист уже связан с компонентом Weblate. Сохраните прежнюю привязку, чтобы проверить изменения исходных строк.",
            409,
        )
    glossary = prepare_glossary(
        store,
        owner,
        slug,
        draft["manifest"],
        {"glossary_upload_id": glossary_upload_id, "glossary_revision": glossary_revision},
    )
    snapshot = snapshot_project(remote, link["remote_slug"])
    validate_glossary(snapshot, glossary)
    plan = compare_kit(
        snapshot,
        draft["manifest"],
        {**link.get("component_map", {}), **mapping},
        include_old_empty,
        allow_new_groups=not link["read_only"] and link.get("origin") == "created-v2",
    )
    generated = get_profile(store, owner, slug, engine).get("generated")
    original = snapshot.get("profile", {}).get(engine, {})
    profile_preview = (
        {
            "engine": engine,
            "before": original,
            "after": merge_remote_profile(original, generated, draft["manifest"]["targets"]),
        }
        if generated
        else None
    )
    version = retain_version(store, owner, slug, draft)
    version_id = version["id"]
    value = {
        **plan,
        "id": "change-plan:" + uuid.uuid4().hex,
        "project": slug,
        "upload_id": upload_id,
        "draft_revision": expected,
        "version_id": version_id,
        "read_only": link["read_only"],
        "snapshot": snapshot,
        "glossary_hash": revision(glossary),
        "glossary_preview": glossary,
        "profile_hash": revision(generated),
        "profile_preview": profile_preview,
        "expires": time.time() + 600,
    }
    return store.save("change_plan", value["id"], owner, value)


def confirm_changes(store, owner, slug, remote, plan_id, expected):
    plan = store.get("change_plan", plan_id, owner)
    link = get_link(store, owner, slug)
    if (
        not link
        or plan["project"] != slug
        or plan["revision"] != expected
        or plan["expires"] < time.time()
    ):
        raise DomainError("stale-revision", "Повторите просмотр изменений", 409)
    if plan["blocked"]:
        raise DomainError(
            "changes_blocked", "Исключите проблемные листы или исправьте сопоставление", 409
        )
    snapshot = snapshot_project(remote, link["remote_slug"])
    if revision(snapshot) != plan["snapshot_hash"]:
        raise DomainError("remote_changed", "Внешние данные изменились; пересчитайте план", 409)
    # Local confirmation is allowed for read-only links; starting writes is guarded separately.
    return store.save(
        "change_plan",
        plan_id,
        owner,
        {**{k: v for k, v in plan.items() if k != "revision"}, "confirmed": True},
        expected=expected,
    )


def retain_version(store, owner, slug, draft):
    version_id = "kit-version:" + revision([owner, slug, draft["token"], draft["manifest"]])
    try:
        return store.get("kit_version", version_id, owner)
    except DomainError as error:
        if error.status != 404:
            raise
    token = str(uuid.UUID(draft["token"]))
    source = (store.data_dir / "uploads" / token).resolve()
    if (
        source.parent != (store.data_dir / "uploads").resolve()
        or not source.is_dir()
        or any(p.is_symlink() for p in source.rglob("*"))
    ):
        raise DomainError("original_missing", "Исходный лок-кит недоступен", 409)
    relative = Path("versions") / version_id.split(":", 1)[1] / "originals"
    destination = store.data_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name("copy-" + uuid.uuid4().hex)
        shutil.copytree(source, temporary)
        temporary.rename(destination)
    version = {
        "id": version_id,
        "project": slug,
        "upload_id": draft["token"],
        "originals": relative.as_posix(),
        "kind": draft["kind"],
        "ui_mode": draft["ui_mode"],
        "answers": copy.deepcopy(draft["answers"]),
        "files": [{"name": r["name"], "filename": Path(r["path"]).name} for r in draft["_files"]],
        "manifest": copy.deepcopy(draft["manifest"]),
        "created": time.time(),
        "previous": next(
            (
                v["id"]
                for v in sorted(
                    store.list("kit_version", owner), key=lambda v: v["created"], reverse=True
                )
                if v["project"] == slug
            ),
            None,
        ),
    }
    return store.save("kit_version", version_id, owner, version)


def prepare_glossary(store, owner, slug, manifest, body):
    glossary = None
    if body.get("glossary_upload_id"):
        g = get_draft(store, body["glossary_upload_id"], owner)
        if (
            g["project"] != slug
            or g["kind"] != "glossary"
            or g["revision"] != body.get("glossary_revision")
        ):
            raise DomainError("glossary_revision", "Reload the glossary decisions", 409)
        if not g["manifest"]:
            # Existing UI accepts one prepared glossary table. Multiple sheets need an explicit selection.
            sheets = [
                {"file": r["name"], "sheet": name} for r in g["_files"] for name in r["sheets"]
            ]
            if len(sheets) != 1:
                raise DomainError(
                    "glossary_selection", "Upload a glossary containing one visible worksheet"
                )
            from loc_kit_ingest.infer import InferenceError
            from loc_kit_ingest.profile import ProfileError
            from loc_kit_ingest.reader import ReaderError

            from app.services.kits import prepare_kit

            g["answers"] = {
                "source_language": manifest["source"],
                "included_target_languages": manifest["targets"],
                "selected_sheets": sheets,
            }
            try:
                glossary = prepare_kit(g)
            except (DomainError, InferenceError, ProfileError, ReaderError) as error:
                selection = sheets[0]
                raise DomainError(
                    "glossary_validation",
                    f"Не удалось разобрать глоссарий «{selection['file']}», "
                    f"лист «{selection['sheet']}». Проверьте заголовки языков, "
                    "ключи/контекст, пояснения и флаги терминов. "
                    "Исправьте таблицу и загрузите её повторно либо уберите глоссарий.",
                ) from error
        else:
            glossary = g["manifest"]
        if glossary["source"] != manifest["source"]:
            raise DomainError("glossary_source", "Glossary source does not match")
        g["manifest"] = glossary
        retain_version(store, owner, slug, g)
    return glossary


def validate_glossary(snapshot, glossary):
    if not glossary:
        return
    existing = {
        u["source"]
        for c in snapshot["components"].values()
        if c.get("glossary")
        for u in c["units"]
    }
    seen = set()
    for table in glossary["components"]:
        for unit in table["units"]:
            source = unit["values"][glossary["source"]]
            if source in existing or source in seen:
                raise DomainError(
                    "glossary_overlap",
                    "Термин уже существует в глоссарии; исключите повторы из новой таблицы",
                    409,
                )
            seen.add(source)
