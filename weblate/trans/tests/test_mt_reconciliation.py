# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch
from uuid import uuid4

import httpx2
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from weblate_customization.checks import GameMarkupCheck
from weblate_customization.machinery import RoutedLLMTranslation

from weblate.checks import models as check_models
from weblate.machinery.openai import BaseOpenAITranslation
from weblate.trans.llm_delivery import dispatch_exchange
from weblate.trans.models import ProducerMTOperation, Unit
from weblate.trans.models.judge import compute_decision_revision
from weblate.trans.models.llm_delivery import ProducerLLMExchange
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.producer_mt import unit_revision
from weblate.trans.mt_operations import build_engine, process_mt_operation
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.tests import http_mock


@override_settings(
    WEBLATE_MACHINERY=(
        *settings.WEBLATE_MACHINERY,
        "weblate_customization.machinery.RoutedLLMTranslation",
    )
)
class MTReconciliationTest(http_mock.MockDNSMixin, ViewTestCase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.project.machinery_settings = {
            "openrouter": {"key": "offline-only", "routing": {"*": "fixture/model"}}
        }
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

    @http_mock.activate
    def test_real_producer_recovers_protected_string_after_stored_reply_interruption(
        self,
    ):
        self.enterContext(
            patch.dict(check_models.CHECKS.data, {"game-markup": GameMarkupCheck()})
        )
        # Establish this synthetic source revision before its first provider call.
        source = 'Collect {0} <sprite name="fire"> coins\n'
        Unit.objects.filter(pk__in=[self.unit.pk, self.unit.source_unit_id]).update(
            source=source,
            extra_flags="python-brace-format",
            last_updated=timezone.now(),
        )
        self.unit.refresh_from_db()
        key = str(uuid4())
        payload = {
            **self.payload,
            "units": [{"unit_id": self.unit.pk, "revision": unit_revision(self.unit)}],
        }
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            accepted = self.api.post(
                self.url, payload, format="json", HTTP_IDEMPOTENCY_KEY=key
            )
        self.assertEqual(accepted.status_code, 202)
        operation = ProducerMTOperation.objects.get(key=key)
        engine = build_engine(operation)
        sent_payloads = []

        def respond(request):
            sent_payloads.append(json.loads(request.content))
            inputs = json.loads(json.loads(request.content)["messages"][-1]["content"])[
                "strings"
            ]
            item = inputs[0]
            self.assertIn("@@PH", item["source"])
            text = [""]
            placements = {}
            for part in item["parts"]:
                if part["type"] == "text":
                    text[-1] += (
                        part["text"]
                        .replace("Collect", "Sbírej")
                        .replace("coins", "mincí")
                    )
                else:
                    placement = {"position": len(text)}
                    if part["translatable"]:
                        placement["text"] = part["text"]
                    placements[f"p{len(placements)}"] = placement
                    text.append("")
            reply = {
                "translations": {
                    item["id"]: {
                        "text": text,
                        "placeholders": placements,
                    }
                }
            }
            return httpx2.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(reply)}}],
                    "usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 2,
                        "total_tokens": 11,
                    },
                },
            )

        http_mock.register_callback(
            "POST", engine.get_chat_completions_url(), callback=respond
        )
        validated = []
        original_parser = RoutedLLMTranslation._parse_llm_translations  # ruff: ignore[private-member-access]

        def interrupt_after_parse(machine, *args, **kwargs):
            result = original_parser(machine, *args, **kwargs)
            validated.append(result)
            msg = "synthetic interruption after storage"
            raise RuntimeError(msg)

        with patch.object(
            RoutedLLMTranslation, "_parse_llm_translations", new=interrupt_after_parse
        ):
            result = process_mt_operation(operation.run_id)
        self.assertEqual(result["state"], "provider_unknown")
        self.assertEqual(len(validated), 1)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        saved = ProducerLLMExchange.objects.get()
        witness = (saved.pk, saved.request_digest, saved.response_digest)
        self.assertEqual(LLMUsageLog.objects.count(), 1)
        replayed_payloads = []
        original_request = BaseOpenAITranslation.request

        def observe_request(machine, method, url, **kwargs):
            replayed_payloads.append(kwargs.get("json"))
            return original_request(machine, method, url, **kwargs)

        with (
            patch.object(BaseOpenAITranslation, "request", new=observe_request),
            patch(
                "weblate.machinery.base.fetch_validated_url",
                side_effect=AssertionError("paid request forbidden"),
            ) as outbound,
        ):
            response = self.api.post(self.url + key + "/reconcile/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(replayed_payloads, sent_payloads)
        self.unit.refresh_from_db()
        self.assertEqual(
            self.unit.target,
            'Sbírej {0} <sprite name="fire"> mincí\n',
            response.data["reconciliation"],
        )
        self.assertEqual(self.unit.source, source)
        self.assertEqual(response.data["reconciliation"]["ready"], [self.unit.pk])
        self.assertEqual(outbound.call_count, 0)
        self.assertEqual(len(http_mock.calls), 1)
        self.assertEqual(LLMUsageLog.objects.count(), 1)
        saved.refresh_from_db()
        self.assertEqual(
            (saved.pk, saved.request_digest, saved.response_digest), witness
        )

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

    def test_saved_body_with_unproven_request_digest_is_not_applied(self):
        identity = (
            f"mt:{self.unit.pk}:cs:{compute_decision_revision(self.unit.source_unit)}"
        )
        string_id = (
            "s"
            + hmac.new(
                settings.SECRET_KEY.encode(), identity.encode(), hashlib.sha256
            ).hexdigest()[:16]
        )
        content = {
            "translations": {
                string_id: {"text": ["Recovered offline"], "placeholders": {}}
            }
        }
        raw = json.dumps(
            {
                "status": 200,
                "headers": {},
                "body": json.dumps(
                    {"choices": [{"message": {"content": json.dumps(content)}}]}
                ),
            }
        )
        dispatch_exchange(
            [identity], "a" * 64, lambda: raw, request_codec="routed-slots-v2"
        )
        with patch(
            "weblate.machinery.base.fetch_validated_url",
            side_effect=AssertionError("paid request forbidden"),
        ) as outbound:
            response = self.reconcile()
        self.assertEqual(response.status_code, 200)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        self.assertEqual(outbound.call_count, 0)

    @http_mock.activate
    def test_exact_saved_response_is_applied_without_another_provider_request(self):
        engine = build_engine(self.operation)

        def respond(request):
            inputs = json.loads(json.loads(request.content)["messages"][-1]["content"])[
                "strings"
            ]
            reply = {
                "translations": {
                    item["id"]: {"text": ["Recovered offline"], "placeholders": {}}
                    for item in inputs
                }
            }
            return httpx2.Response(
                200, json={"choices": [{"message": {"content": json.dumps(reply)}}]}
            )

        http_mock.register_callback(
            "POST", engine.get_chat_completions_url(), callback=respond
        )
        engine._translate_sources(  # ruff: ignore[private-member-access]
            "en",
            "cs",
            [(self.unit.get_source_plurals()[0], self.unit)],
            user=self.user,
            threshold=self.operation.payload["threshold"],
        )
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
