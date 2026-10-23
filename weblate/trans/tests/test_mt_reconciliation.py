# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

from django.utils import timezone
from rest_framework.test import APIClient

from weblate.trans.llm_delivery import dispatch_exchange
from weblate.trans.models import ProducerMTOperation, Unit
from weblate.trans.models.judge import compute_decision_revision
from weblate.trans.models.producer_mt import unit_revision
from weblate.trans.tests.test_views import ViewTestCase


class MTReconciliationTest(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.project.machinery_settings = {"openrouter": {"key": "offline-only"}}
        self.project.save(update_fields=["machinery_settings"])
        self.unit = self.get_unit()
        self.other = self.unit.translation.unit_set.exclude(pk=self.unit.pk).first()
        Unit.objects.filter(pk__in=[self.unit.pk, self.other.pk]).update(
            target="", state=0
        )
        Unit.objects.filter(pk=self.other.pk).update(
            source=self.unit.source, context="synthetic-other"
        )
        self.unit.refresh_from_db()
        self.other.refresh_from_db()
        self.key = str(uuid4())
        self.url = f"/api/producer/projects/{self.project.slug}/mt/batches/"
        self.payload = {
            "component": self.component.slug,
            "language": "cs",
            "engine": "openrouter",
            "threshold": 80,
            "units": [
                {"unit_id": u.pk, "revision": unit_revision(u)}
                for u in [self.unit, self.other]
            ],
        }
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            response = self.api.post(
                self.url, self.payload, format="json", HTTP_IDEMPOTENCY_KEY=self.key
            )
        self.assertEqual(response.status_code, 202, response.data)
        self.operation = ProducerMTOperation.objects.get(key=self.key)
        self.operation.state = "provider_unknown"
        self.operation.save()
        self.operation.run.status = "partial"
        self.operation.run.save()
        self.operation.units.filter(unit=self.unit).update(state="provider_unknown")

    def reconcile(self):
        return self.api.post(self.url + self.key + "/reconcile/", {}, format="json")

    def test_unknown_and_never_sent_are_distinct_without_provider_calls(self):
        with patch("weblate.trans.mt_operations.build_engine") as outbound:
            first = self.reconcile()
            second = self.reconcile()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        result = first.data["reconciliation"]
        self.assertEqual(result["paid_unknown"], [self.unit.pk])
        self.assertEqual(result["never_sent"], [self.other.pk])
        self.assertEqual(outbound.call_count, 0)
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.state, "provider_unknown")

    def test_manual_filling_resolves_coverage_without_rewriting_receipt(self):
        Unit.objects.filter(pk=self.unit.pk).update(
            target="Human translation", state=20
        )
        response = self.reconcile()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["reconciliation"]["ready"], [self.unit.pk])
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.state, "provider_unknown")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Human translation")

    def test_only_proven_never_sent_tail_can_be_admitted_under_new_key(self):
        self.assertEqual(self.reconcile().status_code, 200)
        payload = {**self.payload, "units": [self.payload["units"][1]]}
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            accepted = self.api.post(
                self.url, payload, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())
            )
            unknown = self.api.post(
                self.url,
                {**self.payload, "units": [self.payload["units"][0]]},
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            )
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(unknown.status_code, 409)

    def test_saved_raw_response_is_parsed_and_applied_without_provider(self):
        identity = (
            f"mt:{self.unit.pk}:cs:{compute_decision_revision(self.unit.source_unit)}"
        )
        raw = json.dumps(
            {
                "status": 200,
                "headers": {},
                "body": json.dumps(
                    {
                        "choices": [
                            {"message": {"content": json.dumps(["Recovered offline"])}}
                        ]
                    }
                ),
            }
        )
        dispatch_exchange([identity], "a" * 64, lambda: raw)
        with patch(
            "weblate.machinery.base.fetch_validated_url",
            side_effect=AssertionError("paid request forbidden"),
        ) as outbound:
            response = self.reconcile()
        self.assertEqual(response.status_code, 200)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Recovered offline\n")
        self.assertEqual(outbound.call_count, 0)

    def test_source_audit_change_invalidates_old_empty_target_reservation(self):
        Unit.objects.filter(pk=self.other.source_unit_id).update(
            last_updated=timezone.now()
        )
        response = self.reconcile()
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.other.pk, response.data["reconciliation"]["stale"])
