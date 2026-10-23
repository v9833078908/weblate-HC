# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings

from weblate.trans.judge import JudgeRequest, _BatchResponse, request_verdicts
from weblate.trans.models.judge import ProducerRun


@override_settings(
    JUDGE_ENABLED=True, JUDGE_API_KEY="offline-only", JUDGE_REQUEST_SLEEP=0.0
)
class JudgeDeliveryRecoveryTest(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(DATA_DIR=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.run = ProducerRun.objects.create(
            scope_type="component",
            scope_id="1",
            scope_label="Synthetic",
            scope_path="/projects/synthetic/ui/",
            requested_mode="judge",
            cap=10,
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
            first = request_verdicts([self.request], model="vendor/a", run=self.run)
            second = request_verdicts([self.request], model="vendor/a", run=self.run)
        self.assertFalse(first[0].unparsed)
        self.assertFalse(second[0].unparsed)
        self.assertEqual(outbound.call_count, 1)

    def test_unknown_judge_response_does_not_retry_or_fallback(self):
        with patch(
            "weblate.trans.judge._post_batch",
            return_value=_BatchResponse(None, None, "transport"),
        ) as outbound:
            first = request_verdicts([self.request], model="vendor/a", run=self.run)
            second = request_verdicts([self.request], model="vendor/b", run=self.run)
        self.assertTrue(first[0].unparsed)
        self.assertTrue(second[0].unparsed)
        self.assertEqual(outbound.call_count, 1)
