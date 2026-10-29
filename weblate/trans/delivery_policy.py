# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Server-admitted durable delivery, independent of ordinary producer runs."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied

if TYPE_CHECKING:
    from collections.abc import Generator

    from weblate.trans.models.judge import ProducerRun

_admitted = ContextVar("durable_delivery_admitted", default=False)
WIZARD_DELIVERY_POLICY = "wizard-v1"


@contextmanager
def durable_admission() -> Generator[None, None, None]:
    """Admit delivery after reserving an actor-bound operation receipt."""
    token = _admitted.set(True)
    try:
        yield
    finally:
        _admitted.reset(token)


def admitted_delivery_options() -> dict[str, str]:
    return {"delivery_policy": WIZARD_DELIVERY_POLICY} if _admitted.get() else {}


def uses_durable_delivery(run: ProducerRun | None) -> bool:
    seen = set()
    while run is not None:
        if run.pk in seen:
            msg = "Cyclic delivery ancestry"
            raise PermissionDenied(msg)
        seen.add(run.pk)
        if _run_has_durable_delivery(run):
            return True
        run = run.resumed_from
    return False


def _run_has_durable_delivery(run: ProducerRun | None) -> bool:
    if run is None:
        return False
    options = run.execution_options or {}
    if options.get("delivery_policy") == WIZARD_DELIVERY_POLICY:
        return True
    if options.get("delivery_policy") is not None:
        msg = "Unsupported delivery policy"
        raise PermissionDenied(msg)
    # Older delegated runs were admitted by the trusted OAuth authenticator.
    if options.get("_wizard_delegation"):
        return True
    if run.pk is None:
        return False
    from weblate.trans.models.llm_delivery import (  # ruff: ignore[import-outside-top-level]
        ProducerLLMExchange,
    )
    from weblate.trans.models.producer_mt import (  # ruff: ignore[import-outside-top-level]
        ProducerMTOperation,
    )

    # Never let an old, already admitted operation lose its financial fence,
    # including after a missing marker or a policy migration.
    return (
        ProducerLLMExchange.objects.filter(run_id=run.pk).exists()
        or ProducerMTOperation.objects.filter(run_id=run.pk).exists()
    )


def has_durable_verdict_origin(unit_id: int, seat: int, request_identity: str) -> bool:
    from weblate.trans.models.judge import (  # ruff: ignore[import-outside-top-level]
        JudgeVerdict,
        ProducerRun,
    )

    run_ids = JudgeVerdict.objects.filter(
        unit_id=unit_id, seat=seat, request_identity=request_identity
    ).values("run_id")
    return any(
        uses_durable_delivery(run) for run in ProducerRun.objects.filter(pk__in=run_ids)
    )
