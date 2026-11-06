# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from shutil import rmtree
from tempfile import TemporaryDirectory, mkdtemp
from unittest.mock import patch

import pytest
from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from weblate.trans.judge import (
    JudgeError,
    JudgeRequest,
    _BatchResponse,
    _parse_reply,
    _persist_attempt,
    _resolve_profile,
    resolve_judge_seat_profile,
)
from weblate.trans.judge import (
    request_verdicts as request_verdicts_with_profile,
)
from weblate.trans.llm_delivery import capture_transport_response
from weblate.trans.models.judge import JudgeRequestAttempt, ProducerRun
from weblate.trans.models.llm_delivery import ProducerLLMExchange
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.tasks import cleanup_judge_observability


@pytest.fixture(autouse=True)
def judge_test_route():
    with (
        override_settings(JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1"),
        patch(
            "weblate.trans.judge.resolve_judge_alias",
            side_effect=lambda _url, model, _key: (model, "revision"),
        ),
    ):
        yield


def request_verdicts(requests, *, model=None, seat=None, **kwargs):
    profile = (
        resolve_judge_seat_profile(seat)
        if seat is not None
        else _resolve_profile(0, legacy_model=model)
    )
    return request_verdicts_with_profile(
        requests, profile=profile, model=model, seat=seat, **kwargs
    )


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

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_TRANSIENT_HTTP_RETRIES=0,
        JUDGE_PROTOCOL_RETRIES=0,
    )
    @patch(
        "weblate.trans.judge.resolve_judge_alias",
        side_effect=lambda _url, model, _key: (model, "revision"),
    )
    def test_transient_failure_tries_fallback_for_the_same_seat(self, _alias):
        primary = resolve_judge_seat_profile(1)
        fallback = replace(primary, model="vendor/fallback")
        reply = {
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
        profiles = []

        def send(_payload, profile):
            profiles.append(profile.model)
            if len(profiles) == 1:
                return _BatchResponse(503, None, "http-server", response_complete=True)
            return _BatchResponse(200, reply, "", response_complete=True)

        with (
            patch(
                "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                return_value=fallback,
            ),
            patch("weblate.trans.judge._post_batch", side_effect=send),
        ):
            result = request_verdicts_with_profile([self.request], profile=primary)
        self.assertEqual(profiles, ["vendor/a", "vendor/fallback"])
        self.assertFalse(result[0].unparsed)
        self.assertEqual(result[0].served_model, "vendor/fallback")

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_TRANSIENT_HTTP_RETRIES=0,
    )
    @patch(
        "weblate.trans.judge.resolve_judge_alias",
        side_effect=lambda _url, model, _key: (model, "revision"),
    )
    def test_durable_fallback_replays_both_stages_without_second_charge(self, _alias):
        primary = resolve_judge_seat_profile(1)
        fallback = replace(
            primary,
            model="vendor/fallback",
            upstream_model="vendor/fallback",
            profile_fingerprint="fallback",
        )
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
        models = []

        def send(_payload, profile):
            models.append(profile.model)
            status = 503 if profile.model == primary.model else 200
            body = "busy" if status == 503 else json.dumps(payload)
            capture_transport_response(
                json.dumps({"status": status, "body": body, "headers": {}})
            )
            return _BatchResponse(
                status,
                None if status == 503 else payload,
                "http-server" if status == 503 else "",
                response_complete=True,
            )

        with (
            patch(
                "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                return_value=fallback,
            ),
            patch("weblate.trans.judge._post_batch", side_effect=send),
        ):
            first = request_verdicts_with_profile(
                [self.request], profile=primary, run=self.run
            )
            second = request_verdicts_with_profile(
                [self.request], profile=primary, run=self.run
            )
        self.assertEqual(models, ["vendor/a", "vendor/fallback"])
        self.assertEqual(
            [first[0].served_model, second[0].served_model], ["vendor/fallback"] * 2
        )
        self.assertFalse(first[0].unparsed)
        self.assertEqual(ProducerLLMExchange.objects.count(), 2)
        self.assertEqual(JudgeRequestAttempt.objects.count(), 2)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_BATCH_SIZE_SEAT_1=2,
    )
    @patch(
        "weblate.trans.judge.resolve_judge_alias",
        side_effect=lambda _url, model, _key: (model, "revision"),
    )
    def test_reordered_primary_replays_original_batch_without_new_post(self, _alias):
        profile = resolve_judge_seat_profile(1)
        requests = [
            replace(self.request, unit_key=f"item-{number}") for number in (1, 2)
        ]
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "id": index,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": str(index),
                                    }
                                    for index in range(2)
                                ]
                            }
                        )
                    }
                }
            ]
        }

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(payload), "headers": {}})
            )
            return _BatchResponse(200, payload, "", response_complete=True)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            first = request_verdicts_with_profile(
                requests, profile=profile, run=self.run
            )
            second = request_verdicts_with_profile(
                list(reversed(requests)), profile=profile, run=self.run
            )
            subset = request_verdicts_with_profile(
                [requests[0]], profile=profile, run=self.run
            )
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(subset[0].back_translation, "0")
        self.assertEqual([item.back_translation for item in first], ["0", "1"])
        self.assertEqual([item.back_translation for item in second], ["1", "0"])

    @override_settings(JUDGE_TRANSIENT_HTTP_RETRIES=0, JUDGE_TRANSPORT_RETRIES=0)
    def test_cancel_or_scope_change_prevents_fallback_after_primary(self):
        profile = resolve_judge_seat_profile(1)
        fallback = replace(profile, model="vendor/fallback")
        for reason in ("cancel", "scope"):
            calls = []

            def send(_payload, _profile, calls=calls):
                calls.append(1)
                return _BatchResponse(503, None, "http-server", response_complete=True)

            def cancelled(reason=reason, calls=calls):
                return reason == "cancel" and bool(calls)

            def scope_guard(_batch, reason=reason, calls=calls):
                return reason == "scope" and bool(calls)

            with (
                patch(
                    "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                    return_value=fallback,
                ),
                patch("weblate.trans.judge._post_batch", side_effect=send),
            ):
                result = request_verdicts_with_profile(
                    [self.request],
                    profile=profile,
                    cancelled=cancelled,
                    scope_guard=scope_guard,
                )
            self.assertEqual(len(calls), 1)
            self.assertTrue(result[0].unparsed)

    @override_settings(JUDGE_TRANSIENT_HTTP_RETRIES=0, JUDGE_TRANSPORT_RETRIES=0)
    def test_unknown_fallback_is_never_resent(self):
        profile = resolve_judge_seat_profile(1)
        fallback = replace(
            profile,
            model="vendor/fallback",
            upstream_model="vendor/fallback",
            profile_fingerprint="fallback",
        )
        models = []

        def send(_payload, target):
            models.append(target.model)
            if target.model == profile.model:
                capture_transport_response(
                    json.dumps({"status": 503, "body": "busy", "headers": {}})
                )
                return _BatchResponse(503, None, "http-server", response_complete=True)
            return _BatchResponse(None, None, "transport")

        with (
            patch(
                "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                return_value=fallback,
            ),
            patch("weblate.trans.judge._post_batch", side_effect=send),
        ):
            first = request_verdicts_with_profile(
                [self.request], profile=profile, run=self.run
            )
            second = request_verdicts_with_profile(
                [self.request], profile=profile, run=self.run
            )
        self.assertEqual(models, ["vendor/a", "vendor/fallback"])
        self.assertTrue(first[0].unparsed)
        self.assertTrue(second[0].unparsed)
        self.assertEqual(second[0].failure_kind, "unknown")

    @override_settings(JUDGE_TRANSIENT_HTTP_RETRIES=0, JUDGE_TRANSPORT_RETRIES=0)
    def test_fallback_invalid_request_is_not_retried(self):
        profile = resolve_judge_seat_profile(1)
        fallback = replace(
            profile,
            model="vendor/fallback",
            upstream_model="vendor/fallback",
            profile_fingerprint="fallback",
        )
        calls = []

        def send(_payload, target):
            calls.append(target.model)
            status = 503 if target.model == profile.model else 400
            capture_transport_response(
                json.dumps({"status": status, "body": "refused", "headers": {}})
            )
            return _BatchResponse(
                status,
                None,
                "http-server" if status == 503 else "http-request-invalid",
                response_complete=True,
            )

        with (
            patch(
                "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                return_value=fallback,
            ),
            patch("weblate.trans.judge._post_batch", side_effect=send),
        ):
            for _ in range(2):
                with self.assertRaises(JudgeError):
                    request_verdicts_with_profile(
                        [self.request], profile=profile, run=self.run
                    )
        self.assertEqual(calls, ["vendor/a", "vendor/fallback"])
        self.assertEqual(ProducerLLMExchange.objects.count(), 2)

    @override_settings(JUDGE_TRANSIENT_HTTP_RETRIES=0)
    def test_fallback_capture_before_attempt_link_is_recovered_once(self):
        profile = resolve_judge_seat_profile(1)
        fallback = replace(
            profile,
            model="vendor/fallback",
            upstream_model="vendor/fallback",
            profile_fingerprint="fallback",
        )
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
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
        models = []

        def send(_payload, target):
            models.append(target.model)
            status = 503 if target.model == profile.model else 200
            body = "busy" if status == 503 else json.dumps(payload)
            capture_transport_response(
                json.dumps({"status": status, "body": body, "headers": {}})
            )
            return _BatchResponse(
                status,
                None if status == 503 else payload,
                "http-server" if status == 503 else "",
                response_complete=True,
            )

        def bind(**kwargs):
            if kwargs["profile"].model == fallback.model:
                msg = "fallback binding failed"
                raise RuntimeError(msg)
            return _persist_attempt(**kwargs)

        with (
            patch(
                "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                return_value=fallback,
            ),
            patch("weblate.trans.judge._post_batch", side_effect=send),
        ):
            with (
                patch("weblate.trans.judge._persist_attempt", side_effect=bind),
                self.assertRaisesRegex(RuntimeError, "fallback binding failed"),
            ):
                request_verdicts_with_profile(
                    [self.request], profile=profile, run=self.run
                )
            first = request_verdicts_with_profile(
                [self.request], profile=profile, run=self.run
            )
            second = request_verdicts_with_profile(
                [self.request], profile=profile, run=self.run
            )
        self.assertEqual(models, ["vendor/a", "vendor/fallback"])
        self.assertEqual(first[0].request_attempt_id, second[0].request_attempt_id)
        self.assertIsNotNone(first[0].request_attempt_id)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 2)
        self.assertEqual(LLMUsageLog.objects.filter(run=self.run).count(), 1)

    @override_settings(JUDGE_TRANSIENT_HTTP_RETRIES=0, JUDGE_BATCH_SIZE_SEAT_1=2)
    def test_missing_member_and_unbound_fallback_recover_together(self):
        profile = resolve_judge_seat_profile(1)
        fallback = replace(
            profile,
            model="vendor/fallback",
            upstream_model="vendor/fallback",
            profile_fingerprint="fallback",
        )
        self.run.configuration_snapshot = {
            "model": [profile.model, ""],
            "profile_fingerprint": [profile.profile_fingerprint, ""],
            "endpoint_fingerprint": [profile.endpoint_fingerprint, ""],
            "fallback_model": [fallback.model, ""],
            "fallback_profile_fingerprint": [fallback.profile_fingerprint, ""],
            "fallback_endpoint_fingerprint": [fallback.endpoint_fingerprint, ""],
        }
        self.run.save(update_fields=["configuration_snapshot"])
        requests = [
            replace(
                self.request,
                unit_key=f"item-{number}",
                project_id_snapshot=1,
                component_id_snapshot=number,
                component_slug=f"component-{number}",
            )
            for number in (1, 2)
        ]
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
                                    },
                                    {
                                        "id": 1,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": "Second",
                                    },
                                ]
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
        models = []

        def send(_payload, target):
            models.append(target.model)
            status = 503 if target.model == profile.model else 200
            body = "busy" if status == 503 else json.dumps(payload)
            capture_transport_response(
                json.dumps({"status": status, "body": body, "headers": {}})
            )
            return _BatchResponse(
                status,
                None if status == 503 else payload,
                "http-server" if status == 503 else "",
                response_complete=True,
            )

        def bind(**kwargs):
            if kwargs["profile"].model == fallback.model:
                msg = "fallback binding failed"
                raise RuntimeError(msg)
            return _persist_attempt(**kwargs)

        with (
            patch(
                "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                return_value=fallback,
            ),
            patch("weblate.trans.judge._post_batch", side_effect=send),
        ):
            with (
                patch("weblate.trans.judge._persist_attempt", side_effect=bind),
                self.assertRaisesRegex(RuntimeError, "fallback binding failed"),
            ):
                request_verdicts_with_profile(requests, profile=profile, run=self.run)
            first = request_verdicts_with_profile(
                [requests[1]], profile=profile, run=self.run
            )
            second = request_verdicts_with_profile(
                [requests[1]], profile=profile, run=self.run
            )
        self.assertEqual(models, ["vendor/a", "vendor/fallback"])
        self.assertEqual(first[0].back_translation, "Second")
        self.assertEqual(first[0].request_attempt_id, second[0].request_attempt_id)
        self.assertIsNotNone(first[0].request_attempt_id)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 2)
        usage = LLMUsageLog.objects.get(run=self.run)
        self.assertIsNone(usage.project_id_snapshot)
        self.assertIsNone(usage.component_id_snapshot)
        self.assertEqual(usage.project_slug, "")
        self.assertEqual(usage.component_slug, "")
        self.assertEqual(usage.unit_count, 2)

    @override_settings(JUDGE_BATCH_SIZE_SEAT_1=2, JUDGE_TRANSIENT_HTTP_RETRIES=0)
    def test_fallback_subset_replays_saved_position_without_other_member(self):
        profile = resolve_judge_seat_profile(1)
        fallback = replace(
            profile,
            model="vendor/fallback",
            upstream_model="vendor/fallback",
            profile_fingerprint="fallback",
        )
        requests = [
            replace(self.request, unit_key=f"item-{number}") for number in (1, 2)
        ]
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "id": index,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": str(index),
                                    }
                                    for index in range(2)
                                ]
                            }
                        )
                    }
                }
            ]
        }
        calls = []

        def send(_payload, target):
            calls.append(target.model)
            status = 503 if target.model == profile.model else 200
            body = "busy" if status == 503 else json.dumps(payload)
            capture_transport_response(
                json.dumps({"status": status, "body": body, "headers": {}})
            )
            return _BatchResponse(
                status,
                None if status == 503 else payload,
                "http-server" if status == 503 else "",
                response_complete=True,
            )

        with (
            patch(
                "weblate.trans.judge.resolve_judge_fallback_seat_profile",
                return_value=fallback,
            ),
            patch("weblate.trans.judge._post_batch", side_effect=send),
        ):
            request_verdicts_with_profile(requests, profile=profile, run=self.run)
            result = request_verdicts_with_profile(
                [requests[1]], profile=profile, run=self.run
            )
        self.assertEqual(calls, ["vendor/a", "vendor/fallback"])
        self.assertEqual(result[0].back_translation, "1")
        self.assertEqual(result[0].served_model, "vendor/fallback")

    @override_settings(JUDGE_BATCH_SIZE_SEAT_1=2)
    def test_missing_member_and_unbound_primary_recover_together(self):
        profile = resolve_judge_seat_profile(1)
        self.run.configuration_snapshot = {
            "model": [profile.model, ""],
            "profile_fingerprint": [profile.profile_fingerprint, ""],
            "endpoint_fingerprint": [profile.endpoint_fingerprint, ""],
        }
        self.run.save(update_fields=["configuration_snapshot"])
        requests = [
            replace(self.request, unit_key=f"item-{number}") for number in (1, 2)
        ]
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "id": index,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": str(index),
                                    }
                                    for index in range(2)
                                ]
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        }

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(payload), "headers": {}})
            )
            return _BatchResponse(200, payload, "", response_complete=True)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            with (
                patch(
                    "weblate.trans.judge._persist_attempt",
                    side_effect=RuntimeError("binding failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "binding failed"),
            ):
                request_verdicts_with_profile(requests, profile=profile, run=self.run)
            first = request_verdicts_with_profile(
                [requests[1]], profile=profile, run=self.run
            )
            second = request_verdicts_with_profile(
                [requests[1]], profile=profile, run=self.run
            )
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(first[0].back_translation, "1")
        self.assertEqual(first[0].request_attempt_id, second[0].request_attempt_id)
        self.assertIsNotNone(first[0].request_attempt_id)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 1)
        self.assertEqual(LLMUsageLog.objects.filter(run=self.run).count(), 1)

    @override_settings(JUDGE_BATCH_SIZE_SEAT_1=2)
    def test_unbound_subset_cannot_be_attributed_to_changed_model(self):
        profile = resolve_judge_seat_profile(1)
        self.run.configuration_snapshot = {
            "model": [profile.model, ""],
            "profile_fingerprint": [profile.profile_fingerprint, ""],
            "endpoint_fingerprint": [profile.endpoint_fingerprint, ""],
        }
        self.run.save(update_fields=["configuration_snapshot"])
        requests = [
            replace(self.request, unit_key=f"item-{number}") for number in (1, 2)
        ]
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "id": index,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": str(index),
                                    }
                                    for index in range(2)
                                ]
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        }

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(payload), "headers": {}})
            )
            return _BatchResponse(200, payload, "", response_complete=True)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            with (
                patch(
                    "weblate.trans.judge._persist_attempt",
                    side_effect=RuntimeError("binding failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "binding failed"),
            ):
                request_verdicts_with_profile(requests, profile=profile, run=self.run)
            changed = replace(
                profile,
                model="vendor/changed",
                upstream_model="vendor/changed",
                profile_fingerprint="changed",
            )
            first = request_verdicts_with_profile(
                [requests[1]], profile=changed, run=self.run
            )
            second = request_verdicts_with_profile(
                [requests[1]], profile=changed, run=self.run
            )
        self.assertEqual(outbound.call_count, 1)
        self.assertTrue(first[0].unparsed)
        self.assertTrue(second[0].unparsed)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 0)
        self.assertEqual(LLMUsageLog.objects.filter(run=self.run).count(), 0)

    def test_uncaptured_timeout_is_unknown_on_first_pass_and_replay(self):
        with patch(
            "weblate.trans.judge._post_batch",
            return_value=_BatchResponse(None, None, "deadline"),
        ) as outbound:
            first = request_verdicts([self.request], seat=1, run=self.run)
            second = request_verdicts([self.request], seat=1, run=self.run)
        self.assertEqual(
            [first[0].failure_kind, second[0].failure_kind], ["unknown", "unknown"]
        )
        self.assertEqual(outbound.call_count, 1)

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
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "cost": 0.25},
        }

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(payload), "headers": {}})
            )
            return _BatchResponse(200, payload)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            first = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
            second = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
        self.assertFalse(first[0].unparsed)
        self.assertFalse(second[0].unparsed)
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(first[0].request_attempt_id, second[0].request_attempt_id)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 1)
        self.assertEqual(LLMUsageLog.objects.filter(run=self.run).count(), 1)

    def test_legacy_receipt_replay_does_not_invent_an_attempt(self):
        body = {
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
                                        "back_translation": "",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(body), "headers": {}})
            )
            return _BatchResponse(200, body)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            first = request_verdicts([self.request], seat=1, run=self.run)
            exchange = ProducerLLMExchange.objects.get(run=self.run)
            before = (
                exchange.request_digest,
                exchange.response_digest,
                exchange.state,
                exchange.usage_recorded,
            )
            ProducerLLMExchange.objects.filter(pk=exchange.pk).update(
                request_codec="", judge_attempt=None
            )
            second = request_verdicts([self.request], seat=1, run=self.run)
        self.assertEqual(outbound.call_count, 1)
        self.assertIsNotNone(first[0].request_attempt_id)
        self.assertEqual(second[0].request_attempt_id, first[0].request_attempt_id)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 1)
        exchange.refresh_from_db()
        self.assertEqual(exchange.request_codec, "")
        self.assertEqual(
            (
                exchange.request_digest,
                exchange.response_digest,
                exchange.state,
                exchange.usage_recorded,
            ),
            before,
        )

    def test_retention_preserves_linked_attempt_and_cleans_unlinked_attempt(self):
        body = {
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
                                        "back_translation": "",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(body), "headers": {}})
            )
            return _BatchResponse(200, body)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            first = request_verdicts([self.request], seat=1, run=self.run)
            linked = JudgeRequestAttempt.objects.get(pk=first[0].request_attempt_id)
            ordinary = JudgeRequestAttempt.objects.get(pk=linked.pk)
            ordinary.pk = None
            ordinary.run = None
            ordinary.save(force_insert=True)
            old = timezone.now() - timedelta(days=1000)
            JudgeRequestAttempt.objects.filter(pk__in=[linked.pk, ordinary.pk]).update(
                created_at=old
            )
            cleanup_judge_observability()
            second = request_verdicts([self.request], seat=1, run=self.run)
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(second[0].request_attempt_id, first[0].request_attempt_id)
        self.assertTrue(JudgeRequestAttempt.objects.filter(pk=linked.pk).exists())
        self.assertFalse(JudgeRequestAttempt.objects.filter(pk=ordinary.pk).exists())
        exchange = ProducerLLMExchange.objects.get(run=self.run)
        self.assertEqual(exchange.judge_attempt_id, linked.pk)

    def test_crash_between_capture_and_link_replays_without_inventing_attempt(self):
        body = {
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
                                        "back_translation": "",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(body), "headers": {}})
            )
            return _BatchResponse(200, body)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            with (
                patch(
                    "weblate.trans.judge._persist_attempt",
                    side_effect=RuntimeError("link failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "link failed"),
            ):
                request_verdicts([self.request], seat=1, run=self.run)
            replay = request_verdicts([self.request], seat=1, run=self.run)
        self.assertEqual(outbound.call_count, 1)
        self.assertIsNotNone(replay[0].request_attempt_id)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 1)
        self.assertEqual(
            ProducerLLMExchange.objects.get(run=self.run).judge_attempt_id,
            replay[0].request_attempt_id,
        )

    def test_association_write_failure_is_fatal_and_never_reposts(self):
        body = {
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
                                        "back_translation": "",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }
        original_save = ProducerLLMExchange.save

        def fail_binding(exchange, *args, **kwargs):
            if exchange.judge_attempt_id is not None:
                msg = "binding failed"
                raise RuntimeError(msg)
            return original_save(exchange, *args, **kwargs)

        def send(_payload, _profile):
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(body), "headers": {}})
            )
            return _BatchResponse(200, body)

        with patch("weblate.trans.judge._post_batch", side_effect=send) as outbound:
            with (
                patch.object(ProducerLLMExchange, "save", new=fail_binding),
                self.assertRaisesRegex(RuntimeError, "binding failed"),
            ):
                request_verdicts([self.request], seat=1, run=self.run)
            replay = request_verdicts([self.request], seat=1, run=self.run)
        self.assertEqual(outbound.call_count, 1)
        self.assertIsNotNone(replay[0].request_attempt_id)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=self.run).count(), 1)

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
        JUDGE_FALLBACK_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_FALLBACK_API_KEY="offline-only",
        JUDGE_FALLBACK_MODEL_SEAT_1="vendor/fallback",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1="json_schema",
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
            with override_settings(JUDGE_MODEL_SEAT_1="vendor/c"):
                second = request_verdicts(
                    [self.request], model="vendor/c", seat=1, run=child
                )
        self.assertTrue(first[0].unparsed)
        self.assertTrue(second[0].unparsed)
        self.assertEqual(outbound.call_count, 1)

    @override_settings(
        JUDGE_TRANSIENT_HTTP_RETRIES=0,
        JUDGE_TRANSPORT_RETRIES=0,
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_FALLBACK_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_FALLBACK_API_KEY="offline-only",
        JUDGE_FALLBACK_MODEL_SEAT_1="vendor/fallback",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1="json_schema",
    )
    def test_native_client_uses_configured_fallback_once(self):
        self.run.execution_options = {}
        self.run.save(update_fields=["execution_options"])
        models = []

        def send(_payload, profile):
            models.append(profile.model)
            return _BatchResponse(503, None, "http-server")

        with patch("weblate.trans.judge._post_batch", side_effect=send):
            results = request_verdicts(
                [self.request], model="vendor/a", seat=1, run=self.run
            )
        self.assertEqual(models, ["vendor/a", "vendor/fallback"])
        self.assertTrue(results[0].unparsed)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="offline-only",
    JUDGE_MODEL_SEAT_1="vendor/a",
    JUDGE_MODEL_SEAT_2="vendor/b",
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeDeliveryConcurrentReplayTest(TransactionTestCase):
    def test_replay_waits_for_original_attempt_link(self):
        request = JudgeRequest(
            unit_key="concurrent",
            source="Hello",
            target="Cześć",
            source_language="en",
            target_language="pl",
            note="",
            explanation="",
            glossary_terms=[],
        )
        profile = resolve_judge_seat_profile(1)
        run = ProducerRun.objects.create(
            scope_type="component",
            scope_id="1",
            scope_label="Synthetic",
            scope_path="/",
            requested_mode="judge",
            cap=1,
            execution_options={"delivery_policy": "wizard-v1"},
        )
        body = {
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
                                        "back_translation": "",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }
        captured = threading.Event()
        release = threading.Event()
        replay_started = threading.Event()
        calls = []

        def send(_payload, _profile):
            calls.append(1)
            capture_transport_response(
                json.dumps({"status": 200, "body": json.dumps(body), "headers": {}})
            )
            captured.set()
            if not release.wait(10):
                msg = "test did not release receipt"
                raise RuntimeError(msg)
            return _BatchResponse(200, body, elapsed_ms=37, response_complete=True)

        def invoke(started=None):
            try:
                if started is not None:
                    started.set()
                return request_verdicts_with_profile(
                    [request], profile=profile, run=run
                )[0]
            finally:
                connections.close_all()

        with (
            TemporaryDirectory() as directory,
            override_settings(DATA_DIR=directory),
            patch("weblate.trans.judge._post_batch", side_effect=send),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(invoke)
            self.assertTrue(captured.wait(10))
            second = pool.submit(invoke, replay_started)
            self.assertTrue(replay_started.wait(10))
            release.set()
            first_result, second_result = first.result(), second.result()
        self.assertEqual(calls, [1])
        self.assertEqual(
            first_result.request_attempt_id, second_result.request_attempt_id
        )
        attempt = JudgeRequestAttempt.objects.get(pk=first_result.request_attempt_id)
        self.assertEqual(
            (attempt.run_id, attempt.http_status, attempt.elapsed_ms), (run.pk, 200, 37)
        )


class JudgeCapturedReplyTest(SimpleTestCase):
    def test_truncated_reply_is_not_accepted_by_subset_replay(self):
        payload = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {
                                        "id": 0,
                                        "verdict": "pass",
                                        "errors": [],
                                        "back_translation": "",
                                    }
                                ]
                            }
                        )
                    },
                }
            ]
        }
        self.assertEqual(_parse_reply(payload, 1).failure_kind, "finish-length")
