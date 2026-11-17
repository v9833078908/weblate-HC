# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private durable replies and semantic fences at the billable boundary."""

from __future__ import annotations

import hashlib
import json
import os
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from weblate.trans.models.llm_delivery import ProducerLLMExchange, ProducerLLMWork
from weblate.wizard_auth.grants import guard_paid_run

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

DeliveryState = Literal["reserved", "sending", "received", "unknown"]
RecoveryDisposition = Literal[
    "ready",
    "never_sent",
    "waiting",
    "paid_unknown",
    "paid_invalid",
    "stale",
    "forbidden",
]
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_active_exchange: ContextVar[object | None] = ContextVar(
    "active_llm_exchange", default=None
)


def encode_http_response(response, *, body: str | None = None) -> str:
    return json.dumps(
        {
            "status": response.status_code,
            "body": response.text if body is None else body,
            "headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower()
                in {"retry-after", "content-type", "x-litellm-response-cost"}
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def capture_transport_response(body: str) -> None:
    """Save the body after transport, before status/JSON/schema validation."""
    exchange_id = _active_exchange.get()
    if exchange_id is not None:
        record_response(exchange_id, body)


def captured_response() -> str | None:
    exchange_id = _active_exchange.get()
    if exchange_id is None:
        return None
    try:
        return read_response(exchange_id)
    except DeliveryUnresolved:
        return None


class DeliveryUnresolved(RuntimeError):  # ruff: ignore[error-suffix-on-exception-name]
    """The same work may already have been billed; no second send is allowed."""


@dataclass(frozen=True)
class ExchangeRef:
    id: UUID
    owner: str
    can_send: bool


def _keys(identity: Sequence[str]) -> list[str]:
    if not identity or any(not isinstance(item, str) or not item for item in identity):
        msg = "Invalid delivery identity"
        raise ValueError(msg)
    result = sorted(hashlib.sha256(item.encode()).hexdigest() for item in identity)
    if len(result) != len(set(result)):
        msg = "Duplicate delivery identity"
        raise ValueError(msg)
    return result


# ruff: ignore[complex-structure]
def reserve_exchange(
    identity: Sequence[str],
    request_digest: str,
    policy=None,
    *,
    run_id=None,
    request_codec: str | None = None,
    replay_only: bool = False,
    judge_primary_identity: Sequence[str] | None = None,
    mt_primary_context=None,
) -> ExchangeRef:
    """One transaction claims every semantic member before network I/O."""
    keys = _keys(identity)
    if request_codec not in {
        None,
        "opaque-v1",
        "routed-parts-v1",
        "routed-slots-v2",
        "routed-slots-v3",
        "routed-slots-v4",
    }:
        msg = "Invalid delivery codec"
        raise ValueError(msg)
    if request_codec == "routed-parts-v1" and not replay_only:
        msg = "Legacy delivery is replay-only"
        raise DeliveryUnresolved(msg)
    if len(request_digest) != 64 or any(
        c not in "0123456789abcdef" for c in request_digest
    ):
        msg = "Invalid request digest"
        raise ValueError(msg)
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with transaction.atomic():
            # Shared arbitration boundary, identical to the MT stage path:
            # the Unit rows these identities belong to (sorted PK), then any
            # existing MT stage row for the same canonical work, then the
            # canonical work rows, then the exchange rows. A concurrent legacy
            # enrollment or fallback reservation therefore waits here instead
            # of both passing the absence-of-claim check.
            # ruff: ignore[import-outside-top-level]
            from weblate.trans.models import Unit

            # ruff: ignore[import-outside-top-level]
            from weblate.trans.models.mt_fallback import ProducerMTFallbackUnit

            unit_ids = sorted(
                {
                    int(item.split(":")[1])
                    for item in identity
                    if len(item.split(":")) == 4 and item.split(":")[0] == "mt"
                }
            )
            if unit_ids:
                list(
                    Unit.objects.select_for_update()
                    .filter(pk__in=unit_ids)
                    .order_by("pk")
                )
                stage_rows = list(
                    ProducerMTFallbackUnit.objects.select_for_update()
                    .filter(semantic_key__in=keys)
                    .order_by("semantic_key")
                )
                # A live stage claim on the same canonical work blocks a new
                # primary reservation: the stage owns that unit of work now.
                if any(
                    row.fallback_exchange_id is not None or row.state_blocks_primary
                    for row in stage_rows
                ):
                    msg_0 = "MT stage already claims this canonical work"
                    raise DeliveryUnresolved(msg_0)
                if any(
                    mt_primary_context is None
                    or str(row.authorization_id) != str(mt_primary_context[0])
                    for row in stage_rows
                ):
                    msg_1 = "MT stage belongs to another primary"
                    raise DeliveryUnresolved(msg_1)
            if judge_primary_identity is not None:
                # Lock the original reservation while deciding whether this stage
                # may reserve work. Its exact ordered membership is immutable.
                primary_keys = _keys(judge_primary_identity)
                list(
                    ProducerLLMWork.objects.select_for_update().filter(
                        semantic_key__in=primary_keys
                    )
                )
                if not judge_fallback_allowed(judge_primary_identity):
                    msg_0 = "Judge primary response does not permit fallback"
                    raise DeliveryUnresolved(msg_0)
            existing = list(
                ProducerLLMWork.objects.select_for_update()
                .filter(semantic_key__in=keys)
                .order_by("semantic_key")
                .values_list("exchange_id", flat=True)
            )
            if existing:
                if len(existing) != len(keys) or len(set(existing)) != 1:
                    msg_0 = "Overlapping paid work requires reconciliation"
                    raise DeliveryUnresolved(msg_0)
                saved = ProducerLLMExchange.objects.select_for_update().get(
                    pk=existing[0]
                )
                if saved.request_digest != request_digest or set(
                    saved.work.values_list("semantic_key", flat=True)
                ) != set(keys):
                    msg_0 = "Changed paid request requires reconciliation"
                    raise DeliveryUnresolved(msg_0)
                if saved.request_codec and saved.request_codec != request_codec:
                    msg_0 = "Changed paid codec requires reconciliation"
                    raise DeliveryUnresolved(msg_0)
                if mt_primary_context is not None:
                    # ruff: ignore[import-outside-top-level]
                    from weblate.trans.mt_fallback import (
                        attach_durable_primary_exchange,
                    )

                    attach_durable_primary_exchange(
                        saved, *mt_primary_context, identity
                    )
                if replay_only:
                    return ExchangeRef(saved.pk, saved.owner, False)
                # Only a reserved (provably unsent) operation may be reclaimed.
                if saved.state == "reserved" and saved.lease_until <= timezone.now():
                    saved.owner = uuid4().hex
                    saved.lease_until = timezone.now() + timedelta(seconds=900)
                    saved.save(update_fields=["owner", "lease_until", "updated"])
                    return ExchangeRef(saved.pk, saved.owner, True)
                return ExchangeRef(saved.pk, saved.owner, False)
            if replay_only:
                msg_0 = "No saved delivery for replay"
                raise DeliveryUnresolved(msg_0)
            saved = ProducerLLMExchange.objects.create(
                request_digest=request_digest,
                request_codec=request_codec or "",
                owner=uuid4().hex,
                lease_until=timezone.now() + timedelta(seconds=900),
                run_id=run_id,
            )
            ProducerLLMWork.objects.bulk_create(
                [
                    ProducerLLMWork(
                        semantic_key=hashlib.sha256(item.encode()).hexdigest(),
                        identity=item,
                        position=position,
                        exchange=saved,
                    )
                    for position, item in enumerate(identity)
                ]
            )
            if mt_primary_context is not None:
                # ruff: ignore[import-outside-top-level]
                from weblate.trans.mt_fallback import attach_durable_primary_exchange

                attach_durable_primary_exchange(saved, *mt_primary_context, identity)
            return ExchangeRef(saved.pk, saved.owner, True)
    except IntegrityError as error:
        # Concurrent claim of any member rolls the entire reservation back.
        msg_0 = "Concurrent delivery requires reconciliation"
        raise DeliveryUnresolved(msg_0) from error


def _response_path(exchange_id) -> Path:
    root = Path(settings.DATA_DIR).resolve() / "private-llm-delivery"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        msg = "Unsafe delivery storage"
        raise DeliveryUnresolved(msg)
    return root / f"{exchange_id}.reply"


def record_response(exchange_id, receipt: str) -> DeliveryState:
    """Atomically store the complete body before parsing or applying anything."""
    body = receipt.encode("utf-8")
    if len(body) > MAX_RESPONSE_BYTES:
        msg = "Provider response exceeds private storage limit"
        raise DeliveryUnresolved(msg)
    digest = hashlib.sha256(body).hexdigest()
    path = _response_path(exchange_id)
    # A process may die after rename but before the DB update. The fixed private
    # name lets reconciliation adopt the complete file without another request.
    if not path.exists():
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        msg = "Stored response is immutable"
        raise DeliveryUnresolved(msg)
    ProducerLLMExchange.objects.filter(pk=exchange_id).update(
        state="received",
        response_path=path.name,
        response_digest=digest,
        updated=timezone.now(),
    )
    return "received"


def read_response(exchange_id) -> str:
    saved = ProducerLLMExchange.objects.get(pk=exchange_id)
    path = _response_path(exchange_id)
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size > MAX_RESPONSE_BYTES
    ):
        msg = "No recoverable provider response; paid retry forbidden"
        raise DeliveryUnresolved(msg)
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if saved.response_digest and saved.response_digest != digest:
        msg = "Provider response checksum mismatch"
        raise DeliveryUnresolved(msg)
    if not saved.response_digest:
        ProducerLLMExchange.objects.filter(pk=exchange_id).update(
            state="received",
            response_path=path.name,
            response_digest=digest,
            updated=timezone.now(),
        )
    return body.decode("utf-8")


def exchange_for_work(identity: Sequence[str]):
    keys = _keys(identity)
    identifiers = set(
        ProducerLLMWork.objects.filter(semantic_key__in=keys).values_list(
            "exchange_id", flat=True
        )
    )
    if len(identifiers) != 1:
        msg = "Delivery identity is not recoverable"
        raise DeliveryUnresolved(msg)
    return identifiers.pop()


def legacy_judge_exchange(identity: Sequence[str]):
    """Read a legacy reply and its original ordered membership without sending."""
    if not identity or any(not item.endswith(":pair-fallback") for item in identity):
        msg = "Invalid legacy judge identity"
        raise DeliveryUnresolved(msg)
    work = list(ProducerLLMWork.objects.filter(semantic_key__in=_keys(identity)))
    if len(work) != len(identity) or len({row.exchange_id for row in work}) != 1:
        msg = "Legacy judge work is not recoverable"
        raise DeliveryUnresolved(msg)
    exchange = ProducerLLMExchange.objects.get(pk=work[0].exchange_id)
    members = list(
        exchange.work.order_by("position").values_list("position", "identity")
    )
    if [position for position, _ in members] != list(range(len(members))):
        msg = "Legacy judge membership is incomplete"
        raise DeliveryUnresolved(msg)
    if any(not member.endswith(":pair-fallback") for _, member in members):
        msg = "Invalid legacy judge membership"
        raise DeliveryUnresolved(msg)
    return exchange, [member for _, member in members], read_response(exchange.pk)


def judge_fallback_allowed(identity: Sequence[str]) -> bool:
    """Allow a fallback only for one fully captured transient HTTP response."""
    legacy = []
    for item in identity:
        parts = item.rsplit(":", 4)
        if (
            len(parts) != 5
            or not parts[0].startswith("judge:")
            or parts[-2] not in {"1", "2"}
        ):
            return False
        parts[-2] = "1"
        legacy.append(":".join(parts) + ":pair-fallback")
    if ProducerLLMWork.objects.filter(semantic_key__in=_keys(legacy)).exists():
        return False
    try:
        exchange_id = exchange_for_work(identity)
        exchange = ProducerLLMExchange.objects.get(pk=exchange_id)
        if list(
            exchange.work.order_by("position").values_list("identity", flat=True)
        ) != list(identity):
            return False
        receipt = json.loads(read_response(exchange_id))
    except (
        DeliveryUnresolved,
        ProducerLLMExchange.DoesNotExist,
        TypeError,
        ValueError,
    ):
        return False
    if not isinstance(receipt, dict) or not isinstance(receipt.get("body"), str):
        return False
    status = receipt.get("status")
    return (
        isinstance(status, int)
        and not isinstance(status, bool)
        and (status == 429 or 500 <= status <= 599)
    )


def saved_request_codec(identity: Sequence[str]) -> str | None:
    """Inspect the exact spend fence; a partial/overlapping scope cannot replay."""
    keys = _keys(identity)
    work = list(ProducerLLMWork.objects.filter(semantic_key__in=keys))
    if not work:
        return None
    if len(work) != len(keys) or len({row.exchange_id for row in work}) != 1:
        msg = "Overlapping paid work requires reconciliation"
        raise DeliveryUnresolved(msg)
    saved = ProducerLLMExchange.objects.get(pk=work[0].exchange_id)
    if set(saved.work.values_list("semantic_key", flat=True)) != set(keys):
        msg = "Changed paid work requires reconciliation"
        raise DeliveryUnresolved(msg)
    return saved.request_codec


def record_usage_once(exchange_id, record: Callable[[], None]) -> None:
    with transaction.atomic():
        exchange = ProducerLLMExchange.objects.select_for_update().get(pk=exchange_id)
        if not exchange.usage_recorded:
            record()
            exchange.usage_recorded = True
            exchange.save(update_fields=["usage_recorded", "updated"])


def reconcile_exchange(exchange_id, *, allow_new_charge=False) -> RecoveryDisposition:
    if allow_new_charge:
        msg = "New billing requires a separate audited authorization"
        raise DeliveryUnresolved(msg)
    try:
        read_response(exchange_id)
    except DeliveryUnresolved:
        return "paid_unknown"
    return "ready"


def _send_and_record_response(
    exchange_id: UUID,
    send: Callable[[], str],
    on_response: Callable[[ProducerLLMExchange, str], None] | None,
) -> str:
    if on_response is None:
        response = send()
        record_response(exchange_id, response)
        return response
    # A replay's reservation lock must not pass the receipt before its
    # mandatory attempt association commits. Sending state was already
    # committed, so rollback never makes this work sendable again.
    with transaction.atomic():
        exchange = ProducerLLMExchange.objects.select_for_update().get(pk=exchange_id)
        response = send()
        record_response(exchange_id, response)
        on_response(exchange, response)
    return response


def dispatch_exchange(
    identity: Sequence[str],
    request_digest: str,
    send: Callable[[], str],
    *,
    run_id=None,
    request_codec: str | None = None,
    replay_only: bool = False,
    on_response: Callable[[ProducerLLMExchange, str], None] | None = None,
    judge_primary_identity: Sequence[str] | None = None,
    before_send: Callable[[], None] | None = None,
    mt_primary_context=None,
) -> str:
    if not replay_only:
        guard_paid_run(run_id)
    reference = reserve_exchange(
        identity,
        request_digest,
        run_id=run_id,
        request_codec=request_codec,
        replay_only=replay_only,
        judge_primary_identity=judge_primary_identity,
        mt_primary_context=mt_primary_context,
    )
    if not reference.can_send:
        body = read_response(reference.id)
        if request_codec:
            with transaction.atomic():
                saved = ProducerLLMExchange.objects.select_for_update().get(
                    pk=reference.id
                )
                if saved.request_codec and saved.request_codec != request_codec:
                    msg = "Delivery codec changed during replay"
                    raise DeliveryUnresolved(msg)
                if not saved.request_codec:
                    saved.request_codec = request_codec
                    saved.save(update_fields=["request_codec", "updated"])
        return body
    return dispatch_reserved_exchange(
        reference.id,
        reference.owner,
        send,
        before_send=before_send,
        on_response=on_response,
    )


def dispatch_reserved_exchange(
    exchange_id: UUID,
    owner: str,
    send: Callable[[], str],
    *,
    before_send: Callable[[], None] | None = None,
    on_response: Callable[[ProducerLLMExchange, str], None] | None = None,
) -> str:
    """Send an owned reservation with capture active before status validation."""
    if before_send is not None:
        before_send()
    claimed = ProducerLLMExchange.objects.filter(
        pk=exchange_id,
        owner=owner,
        state="reserved",
        lease_until__gt=timezone.now(),
    ).update(state="sending", updated=timezone.now())
    if not claimed:
        msg = "Delivery ownership changed"
        raise DeliveryUnresolved(msg)
    token = _active_exchange.set(exchange_id)
    try:
        return _send_and_record_response(exchange_id, send, on_response)
    except BaseException:
        # A captured refusal remains received; only a genuinely missing body
        # becomes unknown. Neither outcome releases the financial reservation.
        ProducerLLMExchange.objects.filter(pk=exchange_id, state="sending").update(
            state="unknown", updated=timezone.now()
        )
        raise
    finally:
        _active_exchange.reset(token)


def resolve_run_deliveries(run):
    """Close raw retention only after a successful, fully parsed domain run."""
    if run.status != "completed" or run.finished is None:
        return
    # A durable MT run reports its own state in the summary; a legacy run has
    # no ``mt_state`` key at all and closes on its successful terminal status.
    mt_state = run.summary.get("mt_state")
    if mt_state is not None and mt_state != "completed":
        return
    if run.summary.get("unparsed"):
        return
    # Both the primary and the fallback stage exchange of the same run count:
    # an unresolved receipt is never deleted, and pruning only happens after
    # a terminal processed result.
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.mt_fallback import ProducerMTStageMember

    stage_exchanges = set(
        ProducerMTStageMember.objects.filter(exchange__run=run).values_list(
            "exchange_id", flat=True
        )
    )
    ProducerLLMExchange.objects.filter(
        models.Q(run=run, state="received", resolved_at=None)
        | models.Q(pk__in=stage_exchanges, state="received", resolved_at=None)
    ).update(resolved_at=timezone.now())


def cleanup_delivery_bodies():
    """
    Delete resolved raw content after 30 days; keep permanent spend fences.

    The stage journal keeps its textual material under the same resolved
    30-day retention: occurrence indices, hashes, membership and the canonical
    financial claims survive, so a pruned text never opens a new charge.
    """
    from weblate.trans.models.mt_fallback import (  # ruff: ignore[import-outside-top-level]
        ProducerMTFallbackUnit,
    )

    cutoff = timezone.now() - timedelta(days=30)
    removed = 0
    for ident in (
        ProducerLLMExchange.objects.filter(
            resolved_at__lt=cutoff,
            state="received",
        )
        .filter(
            models.Q(run__status="completed", run__finished__lt=cutoff)
            | models.Q(
                stage_members__unit_stage__authorization__run__status="completed",
                stage_members__unit_stage__authorization__run__finished__lt=cutoff,
            )
        )
        .values_list("pk", flat=True)
        .distinct()
    ):
        with transaction.atomic():
            record = ProducerLLMExchange.objects.select_for_update().get(pk=ident)
            if (
                record.state != "received"
                or not record.resolved_at
                or record.resolved_at >= cutoff
            ):
                continue
            if record.run_id is not None:
                run = record.run
                # The same successful terminal + finished guards apply to a
                # stage exchange as to an ordinary one: a running or unfinished
                # run never has its private text pruned.
                if run.status != "completed" or run.finished is None:
                    continue
                if run.finished >= cutoff:
                    continue
            stage_units = list(
                ProducerMTFallbackUnit.objects.filter(
                    models.Q(stage_members__exchange_id=ident)
                    | models.Q(fallback_exchange_id=ident)
                )
                .select_related("authorization__run")
                .distinct()
            )
            if any(
                unit.authorization.run.status != "completed"
                or unit.authorization.run.finished is None
                or unit.authorization.run.finished >= cutoff
                for unit in stage_units
            ):
                continue
            path = _response_path(ident)
            if path.is_symlink():
                continue
            path.unlink(missing_ok=True)
            record.state = "purged"
            # The frozen wire snapshot is the same private class of data as
            # the raw reply: it is cleared with the body, not kept forever.
            record.request_snapshot = None
            record.save(update_fields=["state", "request_snapshot", "updated"])
            # Private source and answer copies survive until every associated
            # receipt is pruned; unknown or unresolved exchanges retain them.
            for unit in stage_units:
                if not unit.stage_members.exclude(exchange__state="purged").exists():
                    ProducerMTFallbackUnit.objects.filter(pk=unit.pk).update(
                        source_slots=[], primary_answers=[], fallback_answer=""
                    )
            removed += 1
    # Reused or guarded-out members may never have an exchange to visit above.
    # Their private source copies still expire with the successful original run.
    ProducerMTFallbackUnit.objects.filter(
        authorization__run__status="completed",
        authorization__run__finished__lt=cutoff,
    ).exclude(
        stage_members__exchange__state__in=(
            "reserved",
            "sending",
            "received",
            "unknown",
        )
    ).exclude(
        models.Q(fallback_exchange__isnull=False)
        & ~models.Q(fallback_exchange__state="purged")
    ).exclude(source_slots=[], primary_answers=[], fallback_answer="").update(
        source_slots=[], primary_answers=[], fallback_answer=""
    )
    return removed
