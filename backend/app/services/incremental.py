# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time

from app.db import DomainError, revision
from app.services.mt_observation import record_translation_observation
from app.services.profiles import merge_remote_profile
from app.services.project_links import snapshot_project
from app.settings import Settings


def process_incremental(
    data, client, checkpoint, *, snapshot_loader=None, archive_glossary=None, settings=None
):
    """Execute an immutable delta. Every external mutation has a durable intent."""
    project = data["remote_project"]
    plan = data["snapshot"]["change_plan"]
    manifest = data["snapshot"]["manifest"]
    load = snapshot_loader or (lambda: snapshot_project(client, project))
    current = load()
    operations = data["operations"]
    settings = settings or Settings()
    if not any(key.startswith("delta:") for key in operations):
        if revision(current) != plan["snapshot_hash"]:
            raise DomainError("remote_changed", "Внешний проект изменился; нужен новый план", 409)

    def wait(state, *, mt=False):
        if mt:
            pending = record_translation_observation(
                state, settings, run_started=data.get("started")
            )
        else:
            state.setdefault("observe_until", time.time() + 300)
            pending = time.time() < state["observe_until"]
        checkpoint()
        code = "incremental_wait" if pending else "weblate_unknown"
        raise DomainError(
            code, "Ожидается подтверждение операции; повторный запрос не отправляется", 503
        )

    def execute(key, observe, perform):
        key = "delta:" + key
        state = operations.get(key)
        observed = observe()
        if observed:
            operations[key] = {**operations.get(key, {}), "state": "confirmed", "observed": True}
            checkpoint()
            return
        if state and state["state"] != "throttled":
            if state["state"] == "rejected":
                raise DomainError(
                    "remote_rejected", "Операция отклонена; подготовьте исправленный план", 409
                )
            wait(state, mt=key.startswith("delta:mt:"))
        operations[key] = {"state": "intent", "created": time.time()}
        checkpoint()
        try:
            result = perform()
        except DomainError as error:
            if getattr(error, "remote_status", None) == 429:
                operations[key].update(state="throttled", remote_status=429)
                checkpoint()
                raise
            if error.code == "weblate_rejected":
                operations[key].update(state="rejected", remote_status=error.remote_status)
                checkpoint()
                raise
            if error.code != "weblate_unknown":
                raise
        else:
            operations[key].update(state="accepted", result=result)
        checkpoint()
        if not observe():
            wait(operations[key], mt=key.startswith("delta:mt:"))
        operations[key] = {**operations.get(key, {}), "state": "confirmed", "observed": True}
        checkpoint()

    glossary = data["snapshot"].get("glossary")
    if glossary and not any(k.startswith("delta:glossary:") for k in operations):
        from app.services.kit_versions import validate_glossary

        validate_glossary(current, glossary)
    if glossary:
        if (
            not data["snapshot"].get("glossary_confirmed")
            or plan.get("glossary_hash") != revision(glossary)
            or archive_glossary is None
        ):
            raise DomainError(
                "glossary_confirmation", "Отдельно подтвердите просмотренный глоссарий", 409
            )
        # Publish the explicitly reviewed table into a new versioned component.
        # Never upload into or overwrite any existing glossary component.
        for table in glossary["components"]:
            name = "glossary-v-" + revision([glossary, table["id"]])[:24]
            operation = "delta:glossary:" + name
            if name in plan["snapshot"]["components"] and operation not in operations:
                raise DomainError(
                    "glossary_collision",
                    "Глоссарий уже существует; повторная публикация запрещена",
                    409,
                )
            data["phase"] = "publish-glossary"

            def observed(table=table, name=name):
                value = load()["components"].get(name)
                if (
                    not value
                    or not value.get("complete")
                    or not value.get("glossary")
                    or value["source"] != glossary["source"]
                ):
                    return False
                for expected in table["units"]:
                    matches = [
                        u
                        for u in value["units"]
                        if u["source"] == expected["values"][glossary["source"]]
                    ]
                    if len(matches) != 1 or any(
                        matches[0]["targets"].get(code, "") != expected["values"].get(code, "")
                        for code in glossary["targets"]
                    ):
                        return False
                return len(value["units"]) == len(table["units"])

            execute(
                "glossary:" + name,
                observed,
                lambda table=table, name=name: client.create_component(
                    project, name, glossary["source"], archive_glossary(table, name), glossary=True
                ),
            )

    # Merge locales against the observed project profile, never against a global key.
    engine = data["snapshot"].get("engine", "openrouter")
    original = plan["snapshot"].get("profile", {}).get(engine, {})
    profile = merge_remote_profile(original, data["snapshot"]["profile"], manifest["targets"])
    if profile != original:
        data["phase"] = "profile"

        def apply_profile():
            if load().get("profile", {}).get(engine, {}) != original:
                raise DomainError(
                    "remote_changed", "Внешние инструкции изменились; нужен новый план", 409
                )
            return client.set_profile(project, profile)

        execute(
            "profile",
            lambda: load().get("profile", {}).get(engine, {}) == profile,
            apply_profile,
        )
    for group in plan["components"]:
        cid = group["remote_id"]
        source = group.get("source", manifest["source"])
        targets = list(group["translate"])
        if group.get("create_component"):
            local = next(c for c in manifest["components"] if c["id"] == group["local_id"])
            if archive_glossary is None:
                raise DomainError("group_archive", "Нужен проверенный архив новой группы", 409)

            def group_present():
                value = load()["components"].get(cid)
                if value is None:
                    return False
                if value.get("source") != source or value.get("glossary"):
                    raise DomainError(
                        "remote_changed", "Новый компонент занят другим источником", 409
                    )
                return bool(value.get("complete")) and {
                    u["key"]: u["source"] for u in value["units"]
                } == {u["key"]: u["source"] for u in group["new"]}

            execute(
                "component:" + cid,
                group_present,
                lambda: client.create_component(project, cid, source, archive_glossary(local, cid)),
            )
        data["phase"] = "publish-components"

        def component():
            value = load()["components"].get(cid)
            if not value or not value["complete"] or value["source"] != source:
                raise DomainError("remote_changed", "Компонент или исходный язык изменился", 409)
            return value

        for unit in group["new"]:

            def source_present(unit=unit):
                matches = [u for u in component()["units"] if u["key"] == unit["key"]]
                if matches and (len(matches) != 1 or matches[0]["source"] != unit["source"]):
                    raise DomainError("remote_changed", "Конфликт исходной строки", 409)
                return bool(matches)

            mono = plan["snapshot"]["components"].get(cid, {"file_format": "po-mono"}).get(
                "file_format"
            ) in {
                "po-mono",
                "json",
                "strings",
                "properties",
            }
            execute(
                "source:" + cid + ":" + unit["key"],
                source_present,
                lambda unit=unit: client.add_source_unit(
                    project, cid, source, unit["key"], unit["source"], monolingual=mono
                ),
            )
            if unit.get("explanation"):

                def context_present(unit=unit):
                    rows = [u for u in component()["units"] if u["key"] == unit["key"]]
                    if len(rows) != 1 or rows[0]["source"] != unit["source"]:
                        raise DomainError(
                            "remote_changed", "Исходная строка контекста изменилась", 409
                        )
                    current = rows[0].get("explanation", "")
                    if current and current != unit["explanation"]:
                        raise DomainError(
                            "remote_changed",
                            "Внешнее пояснение уже изменено; нужен новый план",
                            409,
                        )
                    return current == unit["explanation"]

                def apply_context(unit=unit):
                    rows = [u for u in component()["units"] if u["key"] == unit["key"]]
                    return client.set_unit_explanation(rows[0]["source_id"], unit["explanation"])

                execute("context:" + cid + ":" + unit["key"], context_present, apply_context)
        for code in targets:
            data["phase"] = "create-languages"
            execute(
                "language:" + cid + ":" + code,
                lambda: code in component()["languages"],
                lambda: client.create_translation(project, cid, code),
            )

            def keyed():
                units = component()["units"]
                rows = {u["key"]: u for u in units}
                expected = {
                    u["key"]: u["source"]
                    for u in plan["snapshot"]["components"].get(cid, {}).get("units", [])
                }
                expected.update({u["key"]: u["source"] for u in group["new"]})
                selected = set(group["seed"][code]) | set(group["translate"][code])
                if len(rows) != len(units) or any(
                    key in rows and rows[key]["source"] != expected.get(key) for key in selected
                ):
                    raise DomainError(
                        "remote_changed", "Исходный текст выбранной строки изменился", 409
                    )
                return rows

            for key, target in group["seed"][code].items():
                unit = keyed().get(key)
                if not unit or code not in unit["target_ids"]:
                    wait(
                        operations.setdefault(
                            "delta:ready:" + cid + ":" + code, {"state": "intent"}
                        )
                    )
                # Preserve a concurrent non-empty translation, even if it differs.
                execute(
                    "seed:" + cid + ":" + code + ":" + key,
                    lambda key=key: bool(keyed()[key]["targets"].get(code)),
                    lambda unit=unit, target=target: client.set_unit_target(
                        unit["target_ids"][code], target
                    ),
                )
            data["phase"] = "machine-translation"
            from app.services.mt_batches import advance_mt_batches, async_mt_enabled

            if async_mt_enabled(settings, project):
                current_rows = keyed()
                keys = group["translate"][code]
                if any(
                    key not in current_rows or code not in current_rows[key]["target_ids"]
                    for key in keys
                ):
                    wait(
                        operations.setdefault(
                            "delta:ready:" + cid + ":" + code, {"state": "intent"}
                        )
                    )
                allowed_ids = [current_rows[key]["target_ids"][code] for key in keys]
                ready = advance_mt_batches(
                    None,
                    "incremental",
                    client,
                    data=data,
                    checkpoint=checkpoint,
                    settings=settings,
                    component=cid,
                    language=code,
                    unit_ids=allowed_ids,
                )
                if not ready:
                    raise DomainError("incremental_wait", "Waiting for durable MT observation", 503)
                continue
            for offset in range(0, len(group["translate"][code]), 100):
                keys = group["translate"][code][offset : offset + 100]
                operation_key = f"mt:{cid}:{code}:{offset}"
                rows = keyed()
                if any(key not in rows or code not in rows[key]["target_ids"] for key in keys):
                    wait(
                        operations.setdefault(
                            "delta:ready:" + cid + ":" + code, {"state": "intent"}
                        )
                    )
                ids = [
                    rows[key]["target_ids"][code]
                    for key in keys
                    if not rows[key]["targets"].get(code)
                ]

                def mt_ready(keys=keys, operation_key=operation_key):
                    current = keyed()
                    filled = sum(bool(current[key]["targets"].get(code)) for key in keys)
                    state = operations.get("delta:" + operation_key)
                    if state and state["state"] in {"intent", "accepted"}:
                        record_translation_observation(
                            state,
                            settings,
                            filled=filled,
                            total=len(keys),
                            run_started=data.get("started"),
                        )
                    return filled == len(keys)

                execute(
                    operation_key,
                    mt_ready,
                    lambda ids=ids: client.start_scoped_translation(project, cid, code, ids),
                )
    latest = load()
    result = {}
    for group in plan["components"]:
        comp = latest["components"][group["remote_id"]]
        result[group["local_id"]] = {
            code: {u["key"]: u["targets"].get(code, "") for u in comp["units"]}
            for code in group["translate"]
        }
    data["remote_snapshot"] = latest
    return result
