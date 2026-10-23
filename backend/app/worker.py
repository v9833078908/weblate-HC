# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import io
import signal
import threading
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from loc_kit_ingest.parser import parse_component
from loc_kit_ingest.profile import parse_profile
from loc_kit_ingest.writer import render_component, validate_rendered_component
from sqlalchemy import select, update
from translate.storage.xliff import Xliff1File

from app.db import DomainError, Store, revision
from app.integrations.weblate import WeblateClient, WeblateRejected
from app.models import Job, Record
from app.services.exports import build_export
from app.services.manifest import component_mapping_key, component_source, component_targets
from app.services.mt_observation import record_translation_observation
from app.services.retry_policy import retry_delay
from app.services.runs import claim_job, now_iso
from app.settings import Settings


class Waiting(Exception):
    pass


class Cancelled(Exception):
    pass


def archive_component(component, directory: Path):
    profile = parse_profile(
        {"schema_version": component["schema_version"], "components": [component["profile"]]}
    ).components[0]
    parsed = parse_component(profile, component["rows"])
    if component.get("logical_sheet_id") and [
        (u.key, u.values[profile.source_lang]) for u in parsed.units
    ] != [(u["key"], u["values"][profile.source_lang]) for u in component["units"]]:
        raise DomainError(
            "render_membership", "Source group membership differs from parsed archive"
        )
    paths = render_component(profile, parsed, directory)
    if any(d.severity == "error" for d in validate_rendered_component(profile, parsed, directory)):
        raise DomainError("render_validation", "Normalized files failed parse-back validation")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths.values():
            archive.writestr(path.name, path.read_bytes())
    return output.getvalue()


def render_seed(component, source, target):
    # A different extension selects the bilingual parser before the component's
    # monolingual PO parser; PO upload autodetection can pick the wrong variant.
    ns = "urn:oasis:names:tc:xliff:document:1.2"
    root = ET.Element("xliff", {"xmlns": ns, "version": "1.2"})
    file = ET.SubElement(
        root,
        "file",
        {
            "original": "",
            "source-language": source,
            "target-language": target,
            "datatype": "plaintext",
        },
    )
    body = ET.SubElement(file, "body")
    expected = []
    for item in component["units"]:
        value = item["values"].get(target)
        if not value:
            continue
        unit = ET.SubElement(body, "trans-unit", {"id": item["key"], "resname": item["key"]})
        attrs = {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}
        ET.SubElement(unit, "source", attrs).text = item["values"][source]
        ET.SubElement(unit, "target", attrs).text = value
        expected.append((item["key"], item["values"][source], value))
    # XML normalizes literal CR, including CRLF, even with xml:space=preserve.
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;")
    actual = [
        (unit.xmlelement.get("resname"), str(unit.source), str(unit.target))
        for unit in Xliff1File(io.BytesIO(payload)).units
        if unit.istranslatable()
    ]
    if actual != expected:
        raise DomainError("render_validation", "Normalized files failed parse-back validation")
    return payload


def process_once(store, settings, remote=None):
    lease_owner = uuid.uuid4().hex
    claimed = claim_job(store, lease_owner)
    if claimed is None:
        return False
    run_id = claimed["id"]
    not_isolated = ~select(Record.id).where(Record.id == "run-isolation:" + run_id).exists()
    owner = claimed["owner"]
    data = claimed["data"]
    verified_export = None
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(30):
            with store.session() as s:
                s.execute(
                    update(Job)
                    .where(
                        Job.id == run_id,
                        Job.lease_owner == lease_owner,
                        Job.lease_until > time.time(),
                        Job.status != "archived_unknown",
                        not_isolated,
                    )
                    .values(lease_until=time.time() + 900)
                )

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()

    def checkpoint(status="running", release=False, delay=0):
        with store.session() as s:
            job = s.get(Job, run_id)
            if not job or job.lease_owner != lease_owner or job.lease_until <= time.time():
                raise Cancelled()
            from app.services.run_isolation import (
                ensure_project_not_isolated,
                ensure_run_not_isolated,
            )

            ensure_run_not_isolated(s, job)
            ensure_project_not_isolated(
                s, job.data.get("remote_project", ""), job.data.get("project")
            )
            if job.status == "cancel_requested" and status != "cancelled":
                raise Cancelled()
            result = s.execute(
                update(Job)
                .where(
                    Job.id == run_id,
                    Job.lease_owner == lease_owner,
                    Job.lease_until > time.time(),
                    Job.status == job.status,
                    Job.status != "archived_unknown",
                    not_isolated,
                )
                .values(
                    data=copy.deepcopy(data),
                    status=status,
                    lease_owner="" if release else lease_owner,
                    lease_until=time.time() + delay if release else time.time() + 900,
                )
            )
            if result.rowcount != 1:
                raise Cancelled()

    def operation(key, observe, perform, phase, *, allow_existing=False):
        data["phase"] = phase
        state = data["operations"].get(key)
        if state and state["state"] == "confirmed":
            return state.get("result", {})
        if state and state["state"] not in {"rejected", "throttled"}:
            existing = observe()
            if existing is not None:
                data["operations"][key] = {"state": "confirmed", "result": existing}
                checkpoint()
                return existing
            raise DomainError(
                "weblate_unknown",
                "An earlier operation has an unconfirmed result; it was not repeated",
                503,
            )
        existing = observe()
        if existing is not None and allow_existing:
            data["operations"][key] = {"state": "confirmed", "result": existing}
            checkpoint()
            return existing
        if existing is not None:
            raise DomainError(
                "remote_collision",
                "The reserved remote object already exists; no changes were made",
                409,
            )
        data["operations"][key] = {"state": "intent"}
        checkpoint()
        try:
            result = perform()
        except WeblateRejected as error:
            # A validation refusal is definite. Only these non-MT operations
            # may be attempted again on explicit resume after checking absence.
            if error.remote_status == 429:
                data["operations"][key].update(state="throttled", remote_status=429)
                checkpoint()
            elif error.remote_status == 400:
                data["operations"][key] = {"state": "rejected", "remote_status": 400}
                checkpoint()
            raise
        data["operations"][key] = {"state": "confirmed", "result": result}
        checkpoint()
        return result

    def wait_for_translation(state, detail):
        # Persist the bound across restarts; only GETs are allowed for this intent.
        pending = record_translation_observation(state, settings, run_started=data["started"])
        checkpoint()
        if pending:
            raise Waiting()
        raise DomainError("weblate_unknown", detail, 503)

    try:
        checkpoint()
        cooldown = data.get("rate_limit", {})
        if cooldown.get("waiting"):
            remaining = cooldown["next_retry_at"] - time.time()
            if remaining > 0:
                checkpoint("running", release=True, delay=remaining)
                return True
            cooldown["waiting"] = False
            data.update(failure="", failure_code=None, remote_status=None, finished=None)
            checkpoint()
        data["started"] = data["started"] or now_iso()
        snapshot = data["snapshot"]
        manifest = snapshot["manifest"]
        project = data["remote_project"]
        if snapshot["engine"] != settings.engine:
            raise DomainError(
                "engine_changed", "Restore the engine used by this saved run before resuming", 503
            )
        client = remote or WeblateClient(settings, admission_store=store)
        early_quality = snapshot.get("quality", {})
        if early_quality.get("enabled") and early_quality.get("workflow_version") == 2:
            from app.services.quality_contract import require_full_quality_capabilities

            try:
                if not settings.quality_cycle_enabled or not settings.full_quality_cycle_enabled:
                    raise DomainError(
                        "quality_configuration", "Full quality cycle is disabled", 503
                    )
                require_full_quality_capabilities(client.producer_me())
            except DomainError as error:
                if error.code not in {"quality_configuration", "weblate_configuration"}:
                    raise
                data["phase"] = "quality-waiting-configuration"
                data["quality"] = {
                    "status": "waiting_for_configuration",
                    "phase": "preflight",
                    "counts": {},
                }
                checkpoint()
                raise Waiting() from error
        if (
            remote is None
            and not snapshot.get("persistent")
            and not snapshot.get("change_plan")
            and data.get("mode") != "quality_only"
        ):
            raise DomainError(
                "project_read_only",
                "Повторная запись в прежний проект запрещена; подключите его для чтения",
                403,
            )
        mt_pending = False
        if data.get("mode") == "quality_only":
            from app.services.quality_only import validate_quality_only_job

            validate_quality_only_job(store, owner, data, client)
            translations = {}
        elif snapshot.get("change_plan"):
            from app.services.incremental import process_incremental
            from app.services.project_links import get_link

            link = get_link(store, owner, data["project"])
            if (
                not link
                or link["read_only"]
                or link.get("origin") != "created-v2"
                or link["remote_slug"] != project
            ):
                raise DomainError(
                    "project_read_only", "Запись в существующий проект запрещена", 403
                )
            translations = process_incremental(
                data,
                client,
                checkpoint,
                settings=settings,
                store=store,
                run_id=run_id,
                lease=lease_owner,
                archive_glossary=lambda c, n: archive_component(
                    c, store.data_dir / "runs" / run_id / n
                ),
            )
        else:
            project_result = operation(
                "project",
                lambda: client.get_project(project),
                lambda: (
                    client.create_project(snapshot["name"], project, translation_review=True)
                    if early_quality.get("enabled")
                    else client.create_project(snapshot["name"], project)
                ),
                "create-project",
                allow_existing=data.get("reuse_project", False),
            )
            if (
                early_quality.get("enabled")
                and project_result.get("translation_review") is not True
            ):
                raise DomainError(
                    "quality_configuration",
                    "Translation review must be enabled for this quality project; existing settings were not changed",
                    503,
                )
            if snapshot.get("persistent"):
                from app.services.project_links import get_link

                if not get_link(store, owner, data["project"]):
                    store.save(
                        "project_link",
                        "link:" + data["project"],
                        owner,
                        {
                            "project": data["project"],
                            "remote_slug": project,
                            "read_only": False,
                            "origin": "created-v2",
                            "component_map": {
                                component_mapping_key(manifest, c): c["id"]
                                for c in manifest["components"]
                            },
                            "snapshot": {
                                "slug": project,
                                "name": snapshot["name"],
                                "languages": [],
                                "components": {},
                                "profile": {},
                                "issues": [],
                            },
                            "observed": time.time(),
                        },
                    )
            reused_components = {}
            if data.get("reuse_project"):
                # Recheck on every processing pass, including confirmed operations
                # after an interrupted import/MT. Do this before any profile/file writes.
                for component in manifest["components"]:
                    existing = client.get_component(project, component["id"])
                    if existing is None:
                        raise DomainError(
                            "existing_component_missing",
                            "An existing project component is missing; no files were replaced",
                            409,
                        )
                    source_values = client.download_translation(
                        project, component["id"], component_source(manifest, component)
                    )
                    expected = {
                        unit["key"]: unit["values"][component_source(manifest, component)]
                        for unit in component["units"]
                    }
                    if source_values != expected:
                        raise DomainError(
                            "project_source_changed",
                            "The source content differs from the existing project; adding a language requires the same source strings",
                            409,
                        )
                    reused_components[component["id"]] = existing
            if data["operations"].get("profile", {}).get("state") != "confirmed":
                if "profile" in data["operations"]:
                    current = client.request("GET", f"projects/{project}/machinery_settings/")
                    if current.get(snapshot["engine"]) != snapshot["profile"]:
                        raise DomainError(
                            "weblate_unknown", "The profile update has not been confirmed", 503
                        )
                else:
                    data["operations"]["profile"] = {"state": "intent"}
                    checkpoint()
                    client.set_profile(project, snapshot["profile"])
                data["operations"]["profile"] = {"state": "confirmed"}
                checkpoint()
            # The optional glossary is published before the first paid MT request.
            groups = []
            if snapshot["glossary"]:
                groups.append((snapshot["glossary"], True))
            groups.append((manifest, False))
            for kit, glossary in groups:
                for component in kit["components"]:
                    name = ("glossary-" if glossary else "") + component["id"]
                    key = ("glossary:" if glossary else "component:") + name

                    result = operation(
                        key,
                        lambda: (
                            reused_components[name]
                            if data.get("reuse_project")
                            else client.get_component(project, name)
                        ),
                        lambda: client.create_component(
                            project,
                            name,
                            component_source(kit, component),
                            archive_component(component, store.data_dir / "runs" / run_id / name),
                            glossary=glossary,
                            **(
                                {"allow_translation_propagation": True}
                                if early_quality.get("workflow_version") == 2 and not glossary
                                else {}
                            ),
                        ),
                        "publish-components",
                        allow_existing=data.get("reuse_project", False),
                    )
                    if result.get("task_url"):
                        task = client.get_task(result["task_url"])
                        if not task.get("completed"):
                            raise Waiting()
                        if task.get("result") is False:
                            raise DomainError(
                                "remote_import_failed", "Weblate component import failed", 502
                            )
                    if glossary:
                        continue
                    for code in component_targets(manifest, component):
                        if client.get_translation_status(project, name, code) is None:
                            operation(
                                "language:" + name + ":" + code,
                                lambda: client.get_translation_status(project, name, code),
                                lambda: client.create_translation(project, name, code),
                                "create-languages",
                            )
                            if client.get_translation_status(project, name, code) is None:
                                raise Waiting()
                        # A language object/file exists before Weblate's background
                        # import creates its database units. Upload/MT must wait for
                        # those units, otherwise a successful HTTP response can match
                        # none of the prepared translations.
                        language_status = client.get_translation_status(project, name, code)
                        total = language_status.get("total") if language_status is not None else 0
                        if isinstance(total, bool) or not isinstance(total, int):
                            raise DomainError(
                                "weblate_schema",
                                "Weblate returned an invalid translation count",
                                502,
                            )
                        if total < len(component["units"]):
                            data["phase"] = "create-languages"
                            raise Waiting()
                        data["operations"]["language:" + name + ":" + code] = {"state": "confirmed"}
                        checkpoint()
                        if data.get("reuse_project"):
                            filled = {
                                unit["key"]
                                for unit in component["units"]
                                if unit["values"].get(code)
                            }
                            if filled:

                                def observe_seed():
                                    current = client.download_translation(project, name, code)
                                    return {} if all(current.get(key) for key in filled) else None

                                def upload_seed():
                                    payload = render_seed(
                                        component, component_source(manifest, component), code
                                    )
                                    return client.upload_translation(project, name, code, payload)

                                operation(
                                    "seed-xliff:" + name + ":" + code,
                                    observe_seed,
                                    upload_seed,
                                    "publish-components",
                                    allow_existing=True,
                                )
                                if observe_seed() is None:
                                    raise DomainError(
                                        "translation_seed_pending",
                                        "Uploaded translations have not been confirmed; no machine translation was started",
                                        503,
                                    )
            translations = {}
            for component in manifest["components"]:
                name = component["id"]
                translations[name] = {}
                required_keys = {unit["key"] for unit in component["units"]}
                for code in component_targets(manifest, component):
                    data["phase"] = "machine-translation"
                    key = "mt:" + name + ":" + code
                    state = data["operations"].get(key)

                    from app.services.mt_batches import advance_mt_batches, async_mt_enabled

                    if async_mt_enabled(settings, project):
                        ready = advance_mt_batches(
                            store,
                            run_id,
                            client,
                            data=data,
                            checkpoint=checkpoint,
                            settings=settings,
                            component=name,
                            language=code,
                            lease=lease_owner,
                        )
                        if not ready:
                            mt_pending = True
                            continue
                        translations[name][code] = client.download_translation(project, name, code)
                        continue

                    def observe_translation():
                        try:
                            return client.download_translation(project, name, code)
                        except DomainError as error:
                            transient = error.code == "weblate_unavailable" or (
                                isinstance(error, WeblateRejected) and error.remote_status >= 500
                            )
                            if transient and state and state["state"] in {"intent", "accepted"}:
                                wait_for_translation(state, error.detail)
                            raise

                    translated = observe_translation()
                    needed = {
                        unit["key"] for unit in component["units"] if not unit["values"].get(code)
                    }
                    if state and state["state"] in {"intent", "accepted"}:
                        record_translation_observation(
                            state,
                            settings,
                            filled=sum(bool(translated.get(k)) for k in needed),
                            total=len(needed),
                            run_started=data["started"],
                        )
                        checkpoint()
                    if not required_keys <= translated.keys():
                        if state and state["state"] in {"intent", "accepted"}:
                            wait_for_translation(
                                state,
                                "The MT response is unknown. Resume only observes remote translations; no paid request is repeated",
                            )
                        raise Waiting()
                    if not state or state["state"] == "throttled":
                        if needed and not all(translated.get(k) for k in needed):
                            data["operations"][key] = {"state": "intent", "created": time.time()}
                            record_translation_observation(
                                data["operations"][key],
                                settings,
                                filled=sum(bool(translated.get(k)) for k in needed),
                                total=len(needed),
                                run_started=data["started"],
                            )
                            checkpoint()
                            try:
                                response = client.start_translation(project, name, code)
                            except DomainError as error:
                                if getattr(error, "remote_status", None) == 429:
                                    data["operations"][key].update(
                                        state="throttled", remote_status=429
                                    )
                                    checkpoint()
                                if error.code != "weblate_unknown":
                                    raise
                            else:
                                data["operations"][key].update(state="accepted", result=response)
                            state = data["operations"][key]
                            checkpoint()
                        else:
                            data["operations"][key] = {"state": "confirmed"}
                            checkpoint()
                    if state and state["state"] != "confirmed":
                        response = state.get("result", {})
                        task_url = response.get("task_url")
                        if task_url:
                            task = client.get_task(task_url)
                            if not task.get("completed"):
                                wait_for_translation(
                                    state,
                                    "Translation task exceeded the observation window; no paid retry was sent",
                                )
                        translated = observe_translation()
                        record_translation_observation(
                            state,
                            settings,
                            filled=sum(bool(translated.get(k)) for k in needed),
                            total=len(needed),
                            run_started=data["started"],
                        )
                        complete = all(translated.get(k) for k in needed)
                        if not complete and state["state"] == "intent":
                            wait_for_translation(
                                state,
                                "The MT response is unknown. Resume only observes remote translations; no paid request is repeated",
                            )
                        if not complete and response.get("_http_status") == 202 and not task_url:
                            wait_for_translation(
                                state,
                                "Weblate accepted the request without a observable task. Resume only checks translations",
                            )
                        if not complete:
                            warning = (
                                f"{name}/{code}: some strings remain empty; no automatic paid retry"
                            )
                            if warning not in data["warnings"]:
                                data["warnings"].append(warning)
                        data["operations"][key] = {
                            **state,
                            "state": "confirmed",
                            "result": response,
                        }
                        checkpoint()
                    translations[name][code] = translated

        if mt_pending:
            from app.services.run_recovery import next_mt_wakeup

            data["mt_next_poll_at"] = next_mt_wakeup(data)
            raise Waiting()
        quality_policy = snapshot.get("quality", {})
        if quality_policy.get("enabled"):
            if not settings.quality_cycle_enabled:
                data["phase"] = "quality-waiting-configuration"
                checkpoint()
                raise Waiting()
            quality_language = quality_policy.get("language", "de")
            if quality_language not in manifest["targets"]:
                raise DomainError(
                    "quality_scope",
                    "The requested quality language is not part of this localization run",
                    409,
                )

            def quality_component_name(component):
                if data.get("mode") == "quality_only":
                    return snapshot["quality_only"]["mapping"][
                        component_mapping_key(manifest, component)
                    ]
                plan = snapshot.get("change_plan")
                return (
                    plan["mapping"][component_mapping_key(manifest, component)]
                    if plan
                    else component["id"]
                )

            if "quality_scope" not in data:
                rows = [
                    unit
                    for component in manifest["components"]
                    if quality_language in component_targets(manifest, component)
                    for unit in client.list_units(
                        project, quality_component_name(component), quality_language
                    )
                ]
                from app.services.quality_preflight import filled_quality_scope

                data["quality_input_scope"] = {"unit_ids": sorted(unit["id"] for unit in rows)}
                selected, excluded = filled_quality_scope(rows)
                # Legacy servers expose no per-unit target list: keep their
                # original conservative scope and preparation contract.
                if all("target" not in unit for unit in rows):
                    selected, excluded = data["quality_input_scope"], []
                unit_ids = data["quality_input_scope"]["unit_ids"]
                data["quality_ready_scope"] = selected
                data["quality_untranslated_exceptions"] = excluded
                if not unit_ids:
                    raise DomainError(
                        "quality_scope", "Weblate returned an empty quality scope", 409
                    )
                data["quality_scope"] = {"unit_ids": unit_ids}
                checkpoint()
            from app.services.quality_cycle import (
                advance_quality_cycle,
                handle_quality_error,
                quality_unit_census,
            )
            from app.services.quality_state import create_quality_cycle

            durable_policy = {
                **quality_policy,
                "require_no_mt": True,
                "initial_judge_scope": data.get("quality_ready_scope", data["quality_scope"]),
                "revision": revision(quality_policy),
            }
            cycle = create_quality_cycle(
                store,
                owner,
                run_id,
                project,
                durable_policy,
            )
            data["quality_cycle_id"] = cycle["id"]
            try:
                cycle = advance_quality_cycle(
                    store,
                    owner,
                    cycle["id"],
                    client,
                    scope=data["quality_scope"],
                    language=quality_language,
                )
            except DomainError as error:
                if getattr(error, "remote_status", None) == 429:
                    raise
                if data.get("mode") == "quality_only" and error.code == "quality_input_incomplete":
                    raise
                cycle = handle_quality_error(store, owner, cycle["id"], error)
            data["quality"] = {
                "cycle_id": cycle["id"],
                "phase": cycle["phase"],
                "round": cycle["round"],
                "status": cycle["status"],
                "counts": cycle["counts"],
            }
            checkpoint()
            if cycle["phase"] != "completed":
                raise Waiting()
            data["quality"]["units"] = quality_unit_census(store, cycle["id"])
            if cycle["status"] == "completed_with_exceptions":
                warning = "Quality cycle completed with isolated exceptions"
                if warning not in data["warnings"]:
                    data["warnings"].append(warning)

            # Judge repairs happened after the MT snapshot was loaded. Export
            # only a fresh, post-recheck snapshot.
            def download_quality_translations():
                return {
                    component["id"]: {
                        code: client.download_translation(
                            project, quality_component_name(component), code
                        )
                        for code in component_targets(manifest, component)
                    }
                    for component in manifest["components"]
                }

            if quality_policy.get("workflow_version") == 2:
                from app.services.quality_export import (
                    build_corrected_quality_export,
                    download_stable_quality_export,
                )
                from app.services.quality_state import get_quality_cycle

                def prepare_verified_file():
                    if data.get("mode") == "quality_only":
                        from app.services.quality_only import validate_quality_only_job

                        validate_quality_only_job(store, owner, data, client)
                    values = download_quality_translations()
                    from app.services.project_links import snapshot_project

                    fresh_snapshot = snapshot_project(client, project)
                    return {
                        "translations": values,
                        "remote_snapshot": fresh_snapshot,
                        "export": build_corrected_quality_export(
                            manifest,
                            values,
                            change_plan=snapshot.get("change_plan"),
                            remote_snapshot=fresh_snapshot,
                        ),
                    }

                verified, provenance = download_stable_quality_export(
                    store,
                    owner,
                    cycle["id"],
                    client,
                    prepare_verified_file,
                    include_snapshot=True,
                )
                translations = verified["translations"]
                verified_export = verified["export"]
                verified_export["quality_provenance"] = copy.deepcopy(provenance)
                from app.services.quality_artifacts import (
                    bind_quality_cells,
                    build_quality_artifacts,
                    immutable_record_id,
                )

                final_snapshot = verified["quality_final_snapshot"]
                mapping = {
                    component_mapping_key(manifest, component): quality_component_name(component)
                    for component in manifest["components"]
                }
                bindings = bind_quality_cells(
                    manifest,
                    verified_export,
                    verified["remote_snapshot"],
                    mapping,
                    final_snapshot,
                    quality_language,
                )
                data["export_version_id"] = immutable_record_id(
                    store, owner, "export", "export:" + run_id, verified_export
                )
                quality_artifact = build_quality_artifacts(
                    run_id=run_id,
                    export_id=data["export_version_id"],
                    exported=verified_export,
                    manifest=manifest,
                    final_snapshot=final_snapshot,
                    unit_results=quality_unit_census(store, cycle["id"]),
                    bindings=bindings,
                )
                from app.services.quality_artifact_files import serialize_quality_bundle

                # Validate both deliverables before publishing a ready result.
                serialize_quality_bundle(quality_artifact, verified_export)
                data["quality_artifact_id"] = immutable_record_id(
                    store, owner, "quality_artifact", "quality-artifact:" + run_id, quality_artifact
                )
                if verified["remote_snapshot"] is not None:
                    data["remote_snapshot"] = verified["remote_snapshot"]
                cycle = get_quality_cycle(store, owner, cycle["id"])
                data["quality"].update(
                    status=cycle["status"],
                    counts=cycle["counts"],
                    provenance=provenance,
                    units=quality_unit_census(store, cycle["id"]),
                )
                checkpoint()
            else:
                translations = download_quality_translations()
        data["phase"] = "export"
        checkpoint()
        if (
            snapshot.get("persistent")
            and "remote_snapshot" not in data
            and hasattr(client, "collect")
        ):
            from app.services.project_links import snapshot_project

            data["remote_snapshot"] = snapshot_project(client, project)
        if verified_export is not None:
            exported = verified_export
        else:
            exported = build_export(manifest, translations, data.get("reuse_project", False))
            from app.services.exports import component_exports

            exported["components"] = component_exports(
                manifest, translations, data.get("reuse_project", False)
            )
            if snapshot.get("change_plan"):
                from app.services.exports import version_export

                exported = version_export(
                    manifest, data["remote_snapshot"], snapshot["change_plan"]["mapping"]
                )
        from app.services.quality_artifacts import immutable_record_id

        export_id = immutable_record_id(store, owner, "export", "export:" + run_id, exported)
        completed_data = {
            **copy.deepcopy(data),
            "export_id": export_id,
            "phase": "completed",
            "finished": now_iso(),
            "failure": "",
        }
        # The guarded UPDATE takes the SQLite write lock. Cancellation/lease transfer
        # and publication cannot interleave between the job and its export pointer.
        with store.session() as s:
            changed = s.execute(
                update(Job)
                .where(
                    Job.id == run_id,
                    Job.owner == owner,
                    Job.lease_owner == lease_owner,
                    Job.lease_until > time.time(),
                    Job.status == "running",
                    not_isolated,
                )
                .values(data=completed_data, status="completed", lease_owner="", lease_until=0)
            )
            if changed.rowcount != 1:
                raise Cancelled()
            link = s.get(Record, "link:" + data["project"])
            if (
                link
                and link.owner == owner
                and link.data["remote_slug"] == project
                and data.get("remote_snapshot")
            ):
                value = {
                    **link.data,
                    "snapshot": copy.deepcopy(data["remote_snapshot"]),
                    "observed": time.time(),
                }
                if snapshot.get("change_plan"):
                    value["component_map"] = copy.deepcopy(snapshot["change_plan"]["mapping"])
                link.data, link.revision = value, revision(value)
                from app.services.project_links import update_project_url

                update_project_url(s, owner, data["project"], project)
            records = [
                ("export", export_id, exported),
                (
                    "export_pointer",
                    "export-pointer:" + data["project"],
                    {"export_id": export_id, "run_id": run_id},
                ),
            ]
            if data.get("quality_artifact_id"):
                records.append(("quality_artifact", data["quality_artifact_id"], quality_artifact))
            for kind, key, value in records:
                record = s.get(Record, key)
                if record:
                    if record.owner != owner or record.kind != kind:
                        raise DomainError("not_found", "Not found", 404)
                    if kind in {"export", "quality_artifact"} and record.revision != revision(
                        value
                    ):
                        raise DomainError(
                            "immutable_result",
                            "Published result changed; create a new version",
                            409,
                        )
                    record.data, record.revision = value, revision(value)
                else:
                    s.add(
                        Record(id=key, kind=kind, owner=owner, data=value, revision=revision(value))
                    )
        data = completed_data
    except Waiting:
        delay = max(1, data["mt_next_poll_at"] - time.time()) if data.get("mt_next_poll_at") else 30
        checkpoint("running", release=True, delay=delay)
    except Cancelled:
        if data.get("rate_limit"):
            data["rate_limit"]["waiting"] = False
        data.update(
            phase="cancelled",
            finished=now_iso(),
            failure="Further local operations cancelled; accepted remote MT may continue",
        )
        # An expired lease belongs to a replacement worker and must not be changed.
        with store.session() as s:
            s.execute(
                update(Job)
                .where(
                    Job.id == run_id,
                    Job.lease_owner == lease_owner,
                    Job.lease_until > time.time(),
                    Job.status != "archived_unknown",
                    not_isolated,
                )
                .values(data=copy.deepcopy(data), status="cancelled", lease_owner="", lease_until=0)
            )
    except DomainError as error:
        if getattr(error, "remote_status", None) == 429:
            attempt = data.get("rate_limit", {}).get("attempt", 0) + 1
            delay = getattr(error, "retry_after", None)
            if delay is None:
                delay = retry_delay(min(attempt, 10))
            delay = max(1.0, delay)
            data.update(
                rate_limit={
                    "waiting": True,
                    "attempt": attempt,
                    "retry_after": delay,
                    "next_retry_at": time.time() + delay,
                },
                failure="",
                failure_code="weblate_rate_limited",
                remote_status=429,
                finished=None,
            )
            try:
                checkpoint("running", release=True, delay=delay)
            except Cancelled:
                pass
            return True
        if error.code == "incremental_wait":
            delay = (
                max(1, data["mt_next_poll_at"] - time.time()) if data.get("mt_next_poll_at") else 30
            )
            checkpoint("running", release=True, delay=delay)
            return True
        data.update(
            failure=error.detail,
            failure_code=error.code,
            remote_status=getattr(error, "remote_status", None),
            safe_reasons=getattr(error, "safe_reasons", []),
            finished=now_iso(),
        )
        try:
            checkpoint("unknown" if error.code == "weblate_unknown" else "failed", release=True)
        except Cancelled:
            pass
    except Exception:
        # Do not expose exception text: transport and configuration errors can contain secrets.
        data.update(
            failure="Worker failed; saved operation intents were retained", finished=now_iso()
        )
        try:
            checkpoint("failed", release=True)
        except Cancelled:
            pass
    finally:
        stopped.set()
        thread.join(timeout=1)
    return True


def main():
    settings = Settings()
    store = Store(settings.data_dir)
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    def service_heartbeat():
        while not stopping.is_set():
            store.save("heartbeat", "worker-heartbeat", "system", {"updated": time.time()})
            stopping.wait(15)

    threading.Thread(target=service_heartbeat, daemon=True).start()
    cleaned = 0
    while not stopping.is_set():
        if time.time() - cleaned > 300:
            from app.services.maintenance import cleanup_expired

            cleanup_expired(store)
            cleaned = time.time()
        process_once(store, settings)
        stopping.wait(2)


if __name__ == "__main__":
    main()
