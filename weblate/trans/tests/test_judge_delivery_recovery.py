# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from shutil import rmtree
from tempfile import mkdtemp
from unittest.mock import patch

from django.test import TestCase, override_settings

from weblate.trans.judge import JudgeRequest, _BatchResponse, request_verdicts
from weblate.trans.models.judge import ProducerRun
from weblate.trans.models.llm_delivery import ProducerLLMExchange


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="offline-only",
    JUDGE_REQUEST_SLEEP=0.0,
    JUDGE_MODEL_SEAT_1="vendor/a",
    JUDGE_MODEL_SEAT_2="vendor/b",
)
class JudgeDeliveryRecoveryTest(TestCase):
    def setUp(self):
        directory = mkdtemp()
        self.addCleanup(rmtree, directory)
        settings = override_settings(DATA_DIR=directory)
        settings.enable()
        self.addCleanup(settings.disable)
        self.run = ProducerRun.objects.create(
            scope_type="component",
            scope_id="1",
            scope_label="Synthetic",
            scope_path="/projects/synthetic/ui/",
            requested_mode="judge",
            cap=10,
            execution_options={"delivery_policy": "wizard-v1"},
        )
        self.request = JudgeRequest(
            unit_key="synthetic:1:r1",
            source="Hello",
            target="Cześć",
            source_language="en",
            target_language="pl",
            note="",
            explanation="",
            glossary_terms=[],
        )

    def test_redelivery_reuses_complete_judge_response(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "id": 0,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": "Hello",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }
        with patch(
            "weblate.trans.judge._post_batch", return_value=_BatchResponse(200, payload)
        ) as outbound:
            first = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
            second = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
        self.assertFalse(first[0].unparsed)
        self.assertFalse(second[0].unparsed)
        self.assertEqual(outbound.call_count, 1)

    @override_settings(JUDGE_TRANSPORT_RETRIES=1, JUDGE_RETRY_SLEEP=0.0)
    def test_native_run_keeps_transport_retry_without_creating_a_spend_fence(self):
        self.run.execution_options = {}
        self.run.save(update_fields=["execution_options"])
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "id": 0,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": "Hello",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }
        with patch(
            "weblate.trans.judge._post_batch",
            side_effect=[
                _BatchResponse(None, None, "transport"),
                _BatchResponse(200, payload),
            ],
        ):
            results = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
        self.assertFalse(results[0].unparsed)
        self.assertFalse(ProducerLLMExchange.objects.filter(run=self.run).exists())

    @override_settings(JUDGE_STREAM=True, JUDGE_STREAM_SEAT_1=True)
    def test_native_run_keeps_streaming_profile(self):
        self.run.execution_options = {}
        self.run.save(update_fields=["execution_options"])
        profiles = []

        def send(payload, profile):
            profiles.append(profile.stream)
            return _BatchResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "segments": [
                                            {
                                                "id": 0,
                                                "verdict": "pass",
                                                "errors": [],
                                                "back_translation": "Hello",
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            )

        with patch("weblate.trans.judge._post_batch", side_effect=send):
            results = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
        self.assertFalse(results[0].unparsed)
        self.assertEqual(profiles, [True])

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_FALLBACK_BASE_URL="https://openrouter.ai/api/v1",
        JUDGE_FALLBACK_API_KEY="synthetic-fallback",
        JUDGE_FALLBACK_MODEL_SEAT_1="vendor/fallback",
        JUDGE_FALLBACK_MODEL_SEAT_2="vendor/fallback-two",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1="json_schema",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2="json_schema",
    )
    def test_unknown_judge_response_does_not_retry_or_fallback(self):
        with patch(
            "weblate.trans.judge._post_batch",
            return_value=_BatchResponse(None, None, "transport"),
        ) as outbound:
            first = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
            self.run.execution_options = {}
            self.run.save(update_fields=["execution_options"])
            child = ProducerRun.objects.create(
                scope_type="component",
                scope_id="1",
                scope_label="Resume",
                scope_path="/",
                requested_mode="judge",
                cap=1,
                resumed_from=self.run,
            )
            with override_settings(JUDGE_MODEL_SEAT_1="vendor/b"):
                second = request_verdicts(
                    [self.request], model="vendor/b", seat=1, run=child
                )
        self.assertTrue(first[0].unparsed)
        self.assertTrue(second[0].unparsed)
        self.assertEqual(outbound.call_count, 1)

    @override_settings(
        JUDGE_TRANSIENT_HTTP_RETRIES=0,
        JUDGE_TRANSPORT_RETRIES=0,
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_FALLBACK_BASE_URL="https://openrouter.ai/api/v1",
        JUDGE_FALLBACK_API_KEY="synthetic-fallback",
        JUDGE_FALLBACK_MODEL_SEAT_1="vendor/fallback",
        JUDGE_FALLBACK_MODEL_SEAT_2="vendor/fallback-two",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1="json_schema",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2="json_schema",
    )
    def test_native_run_keeps_configured_fallback(self):
        self.run.execution_options = {}
        self.run.save(update_fields=["execution_options"])
        models = []

        def send(payload, profile):
            models.append(profile.model)
            if profile.model == "vendor/a":
                return _BatchResponse(503, None, "http-unavailable")
            return _BatchResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "segments": [
                                            {
                                                "id": 0,
                                                "verdict": "pass",
                                                "errors": [],
                                                "back_translation": "Hello",
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            )

        with patch("weblate.trans.judge._post_batch", side_effect=send):
            results = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
        self.assertEqual(models, ["vendor/a", "vendor/fallback"])
        self.assertFalse(results[0].unparsed, results[0].failure_kind)
        self.assertEqual(results[0].served_model, "vendor/fallback")
