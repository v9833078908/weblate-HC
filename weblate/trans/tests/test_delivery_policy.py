# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import override_settings
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.test import APIClient

from weblate.api.producer.operations import execute_quality_operation
from weblate.trans.delivery_policy import (
    admitted_delivery_options,
    uses_durable_delivery,
)
from weblate.trans.judge_loop import repair_targets
from weblate.trans.models import ProducerRun
from weblate.trans.models.producer_quality import ProducerQualityOperation
from weblate.trans.tests.test_views import ViewTestCase


class DeliveryPolicyTest(ViewTestCase):
    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="synthetic",
        JUDGE_MODEL_SEAT_1="vendor/a",
        JUDGE_MODEL_SEAT_2="vendor/b",
    )
    def test_api_start_persists_policy_only_for_admitted_quality_receipt(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        api = APIClient()
        api.force_authenticate(self.user)
        for keyed in (False, True):
            with self.subTest(keyed=keyed):
                estimate = api.post(
                    reverse(
                        "api:producer-project-judge-estimate",
                        kwargs={"slug": self.project.slug},
                    ),
                    {"kind": "judge", "scope": {"query": ""}},
                    format="json",
                )
                self.assertEqual(estimate.status_code, 200)
                response = api.post(
                    reverse(
                        "api:producer-project-run-start",
                        kwargs={"slug": self.project.slug},
                    ),
                    {
                        "kind": "judge",
                        "scope": {"query": ""},
                        "estimate_id": estimate.data["estimate_id"],
                        "delivery_policy": "wizard-v1",
                    },
                    format="json",
                    **({"HTTP_IDEMPOTENCY_KEY": str(uuid4())} if keyed else {}),
                )
                self.assertEqual(response.status_code, 201)
                run = ProducerRun.objects.get(pk=response.data["id"])
                self.assertEqual(
                    run.execution_options.get("delivery_policy"),
                    "wizard-v1" if keyed else None,
                )

    def test_only_reserved_quality_operation_admits_durable_run(self):
        for keyed in (False, True):
            with self.subTest(keyed=keyed):
                request = SimpleNamespace(
                    user=self.user,
                    headers={"Idempotency-Key": str(uuid4())} if keyed else {},
                    path="/quality/start",
                    data={},
                )
                self.assertEqual(admitted_delivery_options(), {})

                def create(operation):
                    run = ProducerRun.objects.create(
                        scope_type="project",
                        scope_id=str(self.project.pk),
                        scope_label="test",
                        scope_path="/",
                        requested_mode="judge",
                        cap=1,
                        execution_options=admitted_delivery_options(),
                    )
                    return Response({"id": str(run.pk)}, status=201)

                response = execute_quality_operation(
                    request=request,
                    project=self.project,
                    action="judge-start",
                    callback=create,
                )
                run = ProducerRun.objects.get(pk=response.data["id"])
                self.assertEqual(uses_durable_delivery(run), keyed)
                self.assertEqual(admitted_delivery_options(), {})

    def test_failed_acceptance_rolls_back_receipt_and_resets_admission(self):
        request = SimpleNamespace(
            user=self.user,
            headers={"Idempotency-Key": str(uuid4())},
            path="/quality/start",
            data={},
        )

        def refuse(operation):
            self.assertEqual(
                admitted_delivery_options(), {"delivery_policy": "wizard-v1"}
            )
            return Response({"error": "refused"}, status=403)

        response = execute_quality_operation(
            request=request, project=self.project, action="judge-start", callback=refuse
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProducerQualityOperation.objects.filter(
                key=request.headers["Idempotency-Key"]
            ).exists()
        )
        self.assertEqual(admitted_delivery_options(), {})

    def test_native_repair_keeps_engine_retry_but_wizard_repair_is_single_attempt(self):
        unit = self.get_unit()
        for durable in (False, True):
            with self.subTest(durable=durable):
                run = ProducerRun.objects.create(
                    scope_type="project",
                    scope_id=str(self.project.pk),
                    scope_label="test",
                    scope_path="/",
                    requested_mode="judge",
                    cap=1,
                    execution_options={"delivery_policy": "wizard-v1"}
                    if durable
                    else {},
                )
                engine = SimpleNamespace(
                    batch_size=10, retry_attempts=3, durable_single_attempt=False
                )
                self.project.machinery_settings = {"openrouter": {"key": "synthetic"}}
                self.project.save(update_fields=["machinery_settings"])
                observed = []

                def fetch(observations, **kwargs):
                    service = kwargs["services"][0]
                    observations.append(
                        (service.retry_attempts, service.durable_single_attempt)
                    )
                    return {
                        unit.pk: {
                            "translation": ["fixed"],
                            "quality": [90],
                            "origin": [None],
                        }
                    }

                with (
                    patch(
                        "weblate.trans.judge_loop.MACHINERY",
                        {"openrouter": lambda _config, engine=engine: engine},
                    ),
                    patch(
                        "weblate.trans.judge_loop.fetch_machinery_matches",
                        side_effect=partial(fetch, observed),
                    ),
                ):
                    self.assertEqual(
                        repair_targets([unit], self.user, run_id=run.pk),
                        {unit.pk: ["fixed"]},
                    )
                self.assertEqual(observed, [(0, True)] if durable else [(3, False)])
