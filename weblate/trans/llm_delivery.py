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
from django.db import IntegrityError, transaction
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


def reserve_exchange(
    identity: Sequence[str],
    request_digest: str,
    policy=None,
    *,
    run_id=None,
    request_codec: str | None = None,
    replay_only: bool = False,
) -> ExchangeRef:
    """One transaction claims every semantic member before network I/O."""
    keys = _keys(identity)
    if request_codec not in {
        None,
        "opaque-v1",
        "routed-parts-v1",
        "routed-slots-v2",
        "routed-slots-v3",
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
            existing = list(
                ProducerLLMWork.objects.select_for_update()
                .filter(semantic_key__in=keys)
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


def dispatch_exchange(
    identity: Sequence[str],
    request_digest: str,
    send: Callable[[], str],
    *,
    run_id=None,
    request_codec: str | None = None,
    replay_only: bool = False,
) -> str:
    if not replay_only:
        guard_paid_run(run_id)
    reference = reserve_exchange(
        identity,
        request_digest,
        run_id=run_id,
        request_codec=request_codec,
        replay_only=replay_only,
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
    claimed = ProducerLLMExchange.objects.filter(
        pk=reference.id,
        owner=reference.owner,
        state="reserved",
        lease_until__gt=timezone.now(),
    ).update(state="sending", updated=timezone.now())
    if not claimed:
        msg = "Delivery ownership changed"
        raise DeliveryUnresolved(msg)
    token = _active_exchange.set(reference.id)
    try:
        response = send()
        record_response(reference.id, response)
    except BaseException:
        # Never release semantic claims on timeout, cancellation, parser failure
        # or process redelivery. The saved raw reply (if any) remains recoverable.
        ProducerLLMExchange.objects.filter(pk=reference.id, state="sending").update(
            state="unknown", updated=timezone.now()
        )
        raise
    finally:
        _active_exchange.reset(token)
    return response


def resolve_run_deliveries(run):
    """Close raw retention only after a successful, fully parsed domain run."""
    if run.status != "completed" or run.finished is None:
        return
    if run.summary.get("mt_state") != "completed" and (
        "unparsed" not in run.summary or run.summary["unparsed"] != 0
    ):
        return
    ProducerLLMExchange.objects.filter(
        run=run, state="received", resolved_at=None
    ).update(resolved_at=timezone.now())


def cleanup_delivery_bodies():
    """Delete resolved raw content after 30 days; keep permanent spend fences."""
    cutoff = timezone.now() - timedelta(days=30)
    removed = 0
    for ident in ProducerLLMExchange.objects.filter(
        resolved_at__lt=cutoff,
        run__status="completed",
        run__finished__lt=cutoff,
        state="received",
    ).values_list("pk", flat=True):
        with transaction.atomic():
            record = ProducerLLMExchange.objects.select_for_update().get(pk=ident)
            if (
                record.state != "received"
                or not record.resolved_at
                or record.resolved_at >= cutoff
            ):
                continue
            path = _response_path(ident)
            if path.is_symlink():
                continue
            path.unlink(missing_ok=True)
            record.state = "purged"
            record.save(update_fields=["state", "updated"])
            removed += 1
    return removed
