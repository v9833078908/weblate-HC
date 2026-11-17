# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline recovery of the exact, already captured MT stage exchanges."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from contextvars import ContextVar
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from weblate_customization.machinery import RoutedLLMTranslation

from weblate.checks.base import Highlight
from weblate.logger import LOGGER
from weblate.machinery.base import MachineTranslationError
from weblate.machinery.llm import (
    llm_batch_component,
    llm_batch_component_id,
    llm_batch_project,
    llm_batch_project_id,
    llm_batch_target_language,
    llm_batch_unit_count,
    llm_usage_record,
)
from weblate.trans.llm_delivery import (
    DeliveryUnresolved,
    read_response,
    record_usage_once,
)
from weblate.trans.models.llm_delivery import ProducerLLMExchange
from weblate.trans.models.mt_fallback import ProducerMTFallbackUnit
from weblate.trans.mt_fallback import compute_audit_revision, source_decision_revision
from weblate.trans.util import join_plural

if TYPE_CHECKING:
    from uuid import UUID

_replay_specs: ContextVar[dict | None] = ContextVar(
    "mt_replay_placeholder_specs", default=None
)


class _ReceiptParser(RoutedLLMTranslation):
    """Parser only; never constructs or sends a provider request."""

    @classmethod
    def _iter_source_placeholder_specs(cls, source_text, unit, source_occurrence=0):
        specs = _replay_specs.get()
        if specs is None:
            return None
        return specs.get(
            (unit.pk if unit is not None else None, source_text, source_occurrence)
        )

    @classmethod
    # ruff: ignore[unused-class-method-argument]
    def _iter_translation_highlights(cls, translation, unit, placeholder_matches):
        # A frozen structured response must carry its protected occurrences as
        # wire placeholders. Do not re-highlight using today's configuration.
        return []


class _LiteLLMReceiptParser(_ReceiptParser):
    """Use the ordinary service identifier for recovered LiteLLM accounting."""

    name = "LiteLLM"


# ruff: ignore[complex-structure, too-many-locals]
def _parse_exchange(exchange):
    snapshot = exchange.request_snapshot
    if not isinstance(snapshot, dict):
        return None
    codec = exchange.request_codec
    if codec not in {
        "routed-slots-v2",
        "routed-slots-v3",
        "routed-slots-v4",
        "routed-parts-v1",
    }:
        return None
    parser = snapshot.get("parser")
    slots = snapshot.get("slots")
    if (
        not isinstance(parser, dict)
        or parser.get("codec") != codec
        or snapshot.get("codec") != codec
    ):
        return None
    wire = {key: snapshot.get(key) for key in ("url", "method", "payload")}
    if (
        not isinstance(wire["url"], str)
        or wire["method"] != "post"
        or not isinstance(wire["payload"], dict)
    ):
        return None
    digest = hashlib.sha256(
        json.dumps(wire, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    if digest != exchange.request_digest:
        return None
    members = list(
        exchange.stage_members.select_related("unit_stage__unit__source_unit").order_by(
            "position"
        )
    )
    if not members or not isinstance(slots, list) or len(slots) != len(members):
        return None
    sources = parser.get("sources")
    cleaned = parser.get("cleaned")
    ids = parser.get("string_ids")
    occurrences = parser.get("source_occurrences")
    replacements = parser.get("replacements")
    n = len(members)
    # Older snapshots lack the redundant cleaned field. Empty replacement
    # maps prove that the frozen source itself was the parser input.
    if (
        cleaned is None
        and isinstance(sources, list)
        and isinstance(replacements, list)
        and all(value == {} for value in replacements)
    ):
        cleaned = list(sources)
    if (
        not isinstance(sources, list)
        or not isinstance(cleaned, list)
        or not isinstance(ids, list)
        or not isinstance(replacements, list)
        or any(len(value) != n for value in (sources, cleaned, ids, replacements))
    ):
        return None
    if occurrences == []:
        occurrences = None
    elif (
        not isinstance(occurrences, list)
        or len(occurrences) != n
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in occurrences
        )
    ):
        return None
    if (
        not all(isinstance(value, str) for value in sources + cleaned + ids)
        or len(set(ids)) != n
    ):
        return None
    if not all(
        isinstance(value, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
        for value in replacements
    ):
        return None
    for position, member in enumerate(members):
        stage = member.unit_stage
        unit = stage.unit
        if (
            member.position != position
            or member.stage != exchange.mt_stage
            or slots[position]
            != {"unit_stage_id": str(stage.pk), "source_slot": member.source_slot}
        ):
            return None
        if (
            unit is None
            or member.source_slot >= len(stage.source_slots)
            or sources[position] != stage.source_slots[member.source_slot]
        ):
            return None
        # Structured placeholder validation uses unit metadata; never use a
        # changed unit as a surrogate for the frozen parser context.
        if source_decision_revision(unit) != stage.source_decision_revision or (
            stage.audit_revision
            and compute_audit_revision(unit) != stage.audit_revision
        ):
            return None
    receipt = json.loads(read_response(exchange.pk))
    if (
        not isinstance(receipt, dict)
        or receipt.get("status") != 200
        or not isinstance(receipt.get("body"), str)
    ):
        return None
    body = json.loads(receipt["body"], parse_float=Decimal)
    if not isinstance(body, dict) or body.get("error"):
        return None
    parser_class = (
        _ReceiptParser if exchange.mt_stage == "primary" else _LiteLLMReceiptParser
    )
    engine = parser_class({})
    # ruff: ignore[private-member-access]
    engine._reply_codec.set(codec)
    engine.name = str(parser.get("name") or "OpenRouter")
    engine.usage_run_id = str(exchange.run_id) if exchange.run_id else None
    engine.max_score = parser.get("max_score", 90)
    parser_sources = cleaned if exchange.mt_stage == "primary" else sources
    frozen_specs = parser.get("placeholder_specs")
    if frozen_specs is None:
        # ruff: ignore[private-member-access]
        if any(engine._iter_placeholders(text) for text in parser_sources):
            return None
        frozen_specs = [[] for _ in members]
    if not isinstance(frozen_specs, list) or len(frozen_specs) != n:
        return None
    replay_specs = {}
    occurrence_counts: dict[tuple[int, str], int] = defaultdict(int)
    for index, member in enumerate(members):
        unit_id = member.unit_stage.unit.pk
        key = (unit_id, parser_sources[index])
        occurrence = (
            occurrences[index] if occurrences is not None else occurrence_counts[key]
        )
        occurrence_counts[key] += 1
        saved_specs = frozen_specs[index]
        if saved_specs is None:
            specs = None
        elif isinstance(saved_specs, list):
            specs = []
            for item in saved_specs:
                if (
                    not isinstance(item, list)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or not isinstance(item[1], dict)
                ):
                    return None
                fields = dict(item[1])
                if "forbidden_text" in fields:
                    fields["forbidden_text"] = tuple(fields["forbidden_text"])
                specs.append((item[0], Highlight(**fields)))
        else:
            return None
        replay_specs[unit_id, parser_sources[index], occurrence] = specs
    # Usage is tied to this original exchange, never its primary work lookup.
    translation = exchange.mt_authorization.translation
    project = translation.component.project
    project_token = llm_batch_project.set(project.slug)
    project_id_token = llm_batch_project_id.set(project.pk)
    component_token = llm_batch_component.set(translation.component.slug)
    component_id_token = llm_batch_component_id.set(translation.component_id)
    language_token = llm_batch_target_language.set(translation.language.code)
    unit_count_token = llm_batch_unit_count.set(n)
    usage_token = llm_usage_record.set(None)
    specs_token = _replay_specs.set(replay_specs)
    try:
        model = wire["payload"].get("model", "")
        # ruff: ignore[private-member-access]
        record_usage_once(exchange.pk, lambda: engine._write_llm_usage(body, model, n))
        text = engine.parse_chat_response(body)
        # ruff: ignore[private-member-access]
        answers = engine._parse_llm_slot_translations(
            text,
            list(
                zip(
                    parser_sources,
                    (member.unit_stage.unit for member in members),
                    strict=True,
                )
            ),
            occurrences,
            string_ids=ids,
        )
        return members, [
            engine.uncleanup_text(replacements[i], answer)
            if replacements[i] or parser.get("force_uncleanup")
            else answer
            for i, answer in enumerate(answers)
        ]
    finally:
        _replay_specs.reset(specs_token)
        llm_usage_record.reset(usage_token)
        llm_batch_unit_count.reset(unit_count_token)
        llm_batch_target_language.reset(language_token)
        llm_batch_component_id.reset(component_id_token)
        llm_batch_component.reset(component_token)
        llm_batch_project_id.reset(project_id_token)
        llm_batch_project.reset(project_token)


# ruff: ignore[complex-structure]
def replay_mt_stage_receipts(authorization_id) -> None:
    """Recover complete stage vectors from verified receipts, without sending."""
    exchanges = (
        ProducerLLMExchange.objects.filter(
            mt_authorization_id=authorization_id,
            mt_stage__in=("primary", "fallback"),
            state__in=("received", "sending", "unknown"),
        )
        .select_related(
            "mt_authorization__translation__component__project",
            "mt_authorization__translation__language",
        )
        .order_by("created")
    )
    # A fallback vector may depend on a primary exchange captured later in the
    # database order; always recover all primary slots before fallback slots.
    for stage_name in ("primary", "fallback"):
        for exchange in exchanges.filter(mt_stage=stage_name):
            try:
                parsed = _parse_exchange(exchange)
            except (
                DeliveryUnresolved,
                MachineTranslationError,
                ValueError,
                TypeError,
                KeyError,
                OSError,
                UnicodeError,
            ):
                LOGGER.warning("MT stage receipt %s cannot be replayed", exchange.pk)
                continue
            if parsed is None:
                continue
            members, answers = parsed
            with transaction.atomic():
                rows = ProducerMTFallbackUnit.objects.select_for_update().in_bulk(
                    [member.unit_stage_id for member in members]
                )
                by_stage: dict[UUID, list[tuple[int, str]]] = {}
                for member, answer in zip(members, answers, strict=True):
                    by_stage.setdefault(member.unit_stage_id, []).append(
                        (member.source_slot, answer)
                    )
                for stage_id, entries in by_stage.items():
                    row = rows[stage_id]
                    if stage_name == "primary":
                        dispositions = list(row.primary_dispositions)
                        vector = list(row.primary_answers)
                        if len(dispositions) != len(row.source_slots) or len(
                            vector
                        ) != len(row.source_slots):
                            continue
                        for index, answer in entries:
                            if answer and dispositions[index] in {
                                "unknown",
                                "primary_usable",
                            }:
                                dispositions[index] = "primary_usable"
                                vector[index] = answer
                        row.primary_dispositions = dispositions
                        row.primary_answers = vector
                        row.save(
                            update_fields=["primary_dispositions", "primary_answers"]
                        )
                    else:
                        if (
                            row.fallback_exchange_id != exchange.pk
                            or row.fallback_state in {"applied", "suggested"}
                        ):
                            continue
                        vector = list(row.primary_answers)
                        if len(vector) != len(row.source_slots) or len(
                            row.primary_dispositions
                        ) != len(vector):
                            continue
                        for index, disposition in enumerate(row.primary_dispositions):
                            if disposition != "primary_usable":
                                vector[index] = ""
                        for index, answer in entries:
                            vector[index] = answer
                        if vector and all(vector):
                            row.fallback_answer = join_plural(vector)
                            row.fallback_state = "received"
                            row.save(
                                update_fields=["fallback_answer", "fallback_state"]
                            )
