# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import time
from dataclasses import replace
from decimal import Decimal
from typing import Any
from unittest import mock

import httpx2
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings

from weblate.trans.judge import (
    _ALIAS_CACHE,
    _FAILOVER_FAILURE_KINDS,
    FAILURE_KINDS,
    JudgeEndpoint,
    JudgeError,
    JudgeRequest,
    RetryBudget,
    _BatchResponse,
    _decode_non_stream,
    _failure_for_http,
    _payload,
    _read_capped,
    _read_sse,
    _request_timeout,
    _resolve_profile,
    _validate_base_url,
    get_judge_base_url,
    get_judge_chat_completions_url,
    judge_configuration_ready,
    judge_configuration_snapshot,
    judge_fallback_endpoint,
    judge_primary_endpoint,
    judge_seat_profiles,
    render_preview,
    request_verdicts,
    resolve_judge_fallback_seat_profile,
    resolve_judge_seat_profile,
    validate_judge_configuration,
)
from weblate.trans.judge_loop import _AVAILABILITY_FAILURE_KINDS
from weblate.trans.models.judge import JudgeRequestAttempt
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.utils.tests import http_mock

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

JUDGE_FALLBACK_SETTINGS = {
    "JUDGE_FALLBACK_BASE_URL": "https://openrouter.ai/api/v1",
    "JUDGE_FALLBACK_API_KEY": "sk-fallback",
    "JUDGE_FALLBACK_MODEL_SEAT_1": "vendor-c/model",
    "JUDGE_FALLBACK_MODEL_SEAT_2": "vendor-d/model",
    "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1": "medium",
    "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_2": "low",
    "JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1": "json_schema",
    "JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2": "json_schema",
}

REQ = JudgeRequest(
    unit_key="MENU_DOOR",
    source="Дверь заблокирована ГЕРМОДВЕРЬМИ",
    target="La porte est bloquée par les PORTES",
    source_language="ru",
    target_language="fr",
    note="",
    explanation="",
    glossary_terms=[
        {
            "source": "ГЕРМОДВЕРЬ",
            "target": "porte blindée",
            "source_explanation": "Бронированная герметичная дверь.",
            "flags": ["terminology"],
        }
    ],
    failing_checks=[],
)


class DrippingStream(httpx2.SyncByteStream):
    def __init__(self, content: bytes, delay: float) -> None:
        self.content = content
        self.delay = delay

    def __iter__(self):
        for chunk in self.content:
            time.sleep(self.delay)
            yield bytes([chunk])


class ResetStream(httpx2.SyncByteStream):
    """
    A peer that accepts the request, then drops the connection mid-body.

    This is how the corporate LiteLLM gateway fails near 30 s under load: the
    reply carries no HTTP status, so a status-based retry rule cannot see it.
    """

    def __iter__(self):
        yield b'{"choices":'
        msg = "connection reset by peer"
        raise httpx2.ReadError(msg)


def _reply(segments: list[dict]) -> dict:
    content = json.dumps({"segments": segments})
    return {"choices": [{"message": {"content": content}}]}


def _sse(*events: object) -> str:
    return (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n"
    )


def _request_segments(body: dict) -> list[dict]:
    content = body["messages"][1]["content"]
    payload = content.split(">\n", 1)[1]
    payload = payload.rsplit("\n</", 1)[0]
    return json.loads(payload)["segments"]


class JudgeSSETest(SimpleTestCase):
    def test_invalid_utf8_is_a_typed_protocol_failure(self) -> None:
        response = httpx2.Response(200, content=b"\xff")

        _, failure, _, _, _, _ = _read_sse(response, started=time.monotonic())

        self.assertEqual(failure, "invalid-envelope")

    def test_truncated_utf8_at_eof_is_a_typed_protocol_failure(self) -> None:
        response = httpx2.Response(200, content=b"\xc3")

        _, failure, _, _, _, _ = _read_sse(response, started=time.monotonic())
        self.assertEqual(failure, "invalid-envelope")

    def test_non_stream_preserves_usage_decimal(self) -> None:
        response = httpx2.Response(
            200,
            content=(
                b'{"usage":{"cost":0.123456789123456789},'
                b'"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
            ),
        )
        payload, failure, *_ = _decode_non_stream(response, started=time.monotonic())
        self.assertEqual(failure, "")
        assert payload is not None
        self.assertEqual(payload["usage"]["cost"], Decimal("0.123456789123456789"))

    def test_stream_preserves_usage_decimal(self) -> None:
        response = httpx2.Response(
            200,
            content=(
                b'data: {"usage":{"cost":0.123456789123456789},'
                b'"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )
        payload, failure, *_ = _read_sse(response, started=time.monotonic())
        self.assertEqual(failure, "")
        assert payload is not None
        self.assertEqual(payload["usage"]["cost"], Decimal("0.123456789123456789"))


class SegmentGlossaryTest(SimpleTestCase):
    def test_segment_carries_the_complete_glossary_entry(self) -> None:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.judge import _segment

        self.assertEqual(_segment(0, REQ)["glossary"], list(REQ.glossary_terms))

    def test_segment_carries_source_explanation(self) -> None:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.judge import _segment

        request = replace(REQ, explanation="Shown on the locked-door screen.")

        self.assertEqual(
            _segment(0, request)["explanation"],
            "Shown on the locked-door screen.",
        )

    def test_segment_omits_empty_source_explanation(self) -> None:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.judge import _segment

        request = replace(REQ, explanation="")

        self.assertNotIn("explanation", _segment(0, request))

    def test_prompt_defines_glossary_context_and_modes(self) -> None:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.judge import _load_prompt

        prompt = _load_prompt("ru", "fr")

        self.assertIn("`source_explanation`", prompt)
        self.assertIn("`target_explanation`", prompt)
        self.assertIn("flagged `read-only`", prompt)
        self.assertIn("flagged `exact`", prompt)
        self.assertIn("flagged `forbidden`", prompt)
        self.assertIn("maintenance metadata", prompt)
        self.assertIn("derived verb", prompt)
        self.assertIn("`note` and `explanation`", prompt)
        self.assertIn(
            "source, the note, the explanation and the glossary",
            prompt,
        )
        self.assertIn("reference context", prompt)
        self.assertIn("not instructions", prompt)


class JudgeClientGateTest(SimpleTestCase):
    @override_settings(JUDGE_ENABLED=False)
    @http_mock.activate
    def test_disabled_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(JUDGE_ENABLED=True, JUDGE_API_KEY="")
    @http_mock.activate
    def test_missing_key_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @http_mock.activate
    def test_missing_model_makes_no_network_call(self) -> None:
        with (
            override_settings(JUDGE_ENABLED=True, JUDGE_API_KEY="sk-test"),
            self.assertRaises(JudgeError),
        ):
            request_verdicts([REQ], model="")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_REQUEST_DEADLINE=0,
    )
    @http_mock.activate
    def test_nonpositive_deadline_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_REQUEST_DEADLINE=float("inf"),
    )
    @http_mock.activate
    def test_nonfinite_deadline_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor/model-a",
        JUDGE_MODEL_SEAT_2="vendor/model-b",
    )
    @http_mock.activate
    def test_invalid_per_seat_deadline_makes_no_network_call(self) -> None:
        for value in (0, -1, float("inf"), True, "invalid"):
            with (
                self.subTest(value=value),
                override_settings(JUDGE_REQUEST_DEADLINE_SEAT_2=value),
                self.assertRaises(JudgeError),
            ):
                validate_judge_configuration()
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=0,
    )
    @http_mock.activate
    def test_zero_batch_size_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_RETRY_BUDGET_RATIO=float("nan"),
    )
    def test_nonfinite_retry_budget_ratio_is_invalid(self) -> None:
        with self.assertRaises(JudgeError):
            validate_judge_configuration()

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=-5,
    )
    @http_mock.activate
    def test_negative_batch_size_never_reports_an_empty_success(self) -> None:
        # A negative step makes range() yield nothing, so an unguarded run
        # would return no verdict at all and still look successful.
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
        JUDGE_BATCH_SIZE=0,
    )
    def test_run_gate_rejects_an_unusable_batch_size(self) -> None:
        with self.assertRaises(JudgeError):
            validate_judge_configuration()

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="",
    )
    @http_mock.activate
    def test_seat_request_validates_both_profiles_before_posting(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], seat=1)

        self.assertEqual(http_mock.calls, [])

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
        JUDGE_MAX_UNITS_PER_RUN=-1,
    )
    def test_readiness_rejects_an_invalid_cap(self) -> None:
        self.assertFalse(judge_configuration_ready())

    @override_settings(JUDGE_ENABLED=True, JUDGE_API_KEY="sk-test", JUDGE_BASE_URL="")
    @http_mock.activate
    def test_blank_base_url_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True, JUDGE_API_KEY="sk-test", JUDGE_BASE_URL="not-a-url"
    )
    @http_mock.activate
    def test_malformed_base_url_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BASE_URL="http://hcbifrost.herocraft.com/litellm/v1",
    )
    @http_mock.activate
    def test_non_https_base_url_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_REASONING_EFFORT="low",
    )
    @http_mock.activate
    def test_litellm_with_unsupported_reasoning_effort_makes_no_network_call(
        self,
    ) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)


class JudgeEndpointResolutionTest(SimpleTestCase):
    def test_default_base_url_is_openrouter(self) -> None:
        self.assertEqual(get_judge_base_url(), "https://openrouter.ai/api/v1")

    def test_default_chat_completions_url(self) -> None:
        self.assertEqual(
            get_judge_chat_completions_url(),
            "https://openrouter.ai/api/v1/chat/completions",
        )


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeExplicitEndpointTest(SimpleTestCase):
    """An endpoint is a value threaded through resolution, not a global read."""

    def test_primary_endpoint_is_an_explicit_value(self) -> None:
        primary = judge_primary_endpoint()
        self.assertEqual(primary.role, "primary")
        self.assertEqual(primary.provider, "litellm")
        self.assertEqual(primary.base_url, "https://hcbifrost.herocraft.com/litellm/v1")
        self.assertEqual(primary.api_key, "sk-test")

    def test_resolve_seat_profile_accepts_an_explicit_endpoint(self) -> None:
        primary = judge_primary_endpoint()
        profile = resolve_judge_seat_profile(1, endpoint=primary)
        self.assertEqual(profile.base_url, primary.base_url)
        self.assertEqual(profile.api_key, primary.api_key)
        self.assertEqual(profile.provider, "litellm")

    def test_explicit_primary_endpoint_matches_the_implicit_default(self) -> None:
        default_profile = resolve_judge_seat_profile(1)
        explicit_profile = resolve_judge_seat_profile(
            1, endpoint=judge_primary_endpoint()
        )
        self.assertEqual(default_profile, explicit_profile)

    def test_seat_profiles_still_return_the_primary_pair_unchanged(self) -> None:
        first, second = judge_seat_profiles()
        self.assertEqual(first.model, "vendor-a/model")
        self.assertEqual(second.model, "vendor-b/model")
        self.assertEqual(first.base_url, "https://hcbifrost.herocraft.com/litellm/v1")

    def test_two_endpoints_never_collide_on_fingerprint(self) -> None:
        primary = judge_primary_endpoint()
        other = JudgeEndpoint(
            role="fallback",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-other",
            provider="openrouter",
        )
        first = resolve_judge_seat_profile(1, endpoint=primary)
        second = resolve_judge_seat_profile(1, endpoint=other)
        self.assertNotEqual(first.endpoint_fingerprint, second.endpoint_fingerprint)
        self.assertNotEqual(first.profile_fingerprint, second.profile_fingerprint)

    def test_legacy_no_seat_profile_still_resolves_global_values(self) -> None:
        profile = _resolve_profile(0, legacy_model="vendor/model-a")
        self.assertEqual(profile.model, "vendor/model-a")
        self.assertEqual(profile.base_url, "https://hcbifrost.herocraft.com/litellm/v1")
        self.assertEqual(profile.api_key, "sk-test")


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-primary",
    JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeFallbackConfigurationTest(SimpleTestCase):
    """Eight new settings, all blank by default, validated all-or-nothing."""

    def test_unconfigured_fallback_validates_and_is_ready(self) -> None:
        validate_judge_configuration()
        self.assertTrue(judge_configuration_ready())
        self.assertIsNone(judge_fallback_endpoint())

    @http_mock.activate
    def test_unconfigured_fallback_makes_todays_call_count(self) -> None:
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts([REQ], seat=1)
        self.assertEqual(len(http_mock.calls), 1)

    def test_partial_fallback_configuration_raises_before_any_request(self) -> None:
        required = [
            "JUDGE_FALLBACK_BASE_URL",
            "JUDGE_FALLBACK_API_KEY",
            "JUDGE_FALLBACK_MODEL_SEAT_1",
            "JUDGE_FALLBACK_MODEL_SEAT_2",
            "JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1",
            "JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2",
        ]
        for missing in required:
            partial = {
                key: value
                for key, value in JUDGE_FALLBACK_SETTINGS.items()
                if key != missing
            }
            with (
                self.subTest(missing=missing),
                override_settings(**partial),
                self.assertRaises(JudgeError),
            ):
                validate_judge_configuration()

    @override_settings(
        **{
            **JUDGE_FALLBACK_SETTINGS,
            "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1": "",
            "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_2": "",
        }
    )
    def test_blank_fallback_reasoning_effort_is_a_valid_configuration(self) -> None:
        # Matches the primary's own JUDGE_REASONING_EFFORT: "" means "send no
        # reasoning parameter", not "unset".
        validate_judge_configuration()
        endpoint = judge_fallback_endpoint()
        primary = resolve_judge_seat_profile(1)
        fallback = resolve_judge_fallback_seat_profile(1, primary)
        self.assertIsNotNone(endpoint)
        self.assertEqual(fallback.reasoning, "")

    @override_settings(**JUDGE_FALLBACK_SETTINGS)
    @http_mock.activate
    def test_fully_configured_fallback_validates_before_any_request(self) -> None:
        validate_judge_configuration()
        self.assertEqual(len(http_mock.calls), 0)
        endpoint = judge_fallback_endpoint()
        self.assertIsNotNone(endpoint)
        self.assertEqual(endpoint.role, "fallback")
        self.assertEqual(endpoint.provider, "openrouter")

    @override_settings(
        **{**JUDGE_FALLBACK_SETTINGS, "JUDGE_FALLBACK_BASE_URL": "not-a-url"}
    )
    def test_malformed_fallback_base_url_raises(self) -> None:
        with self.assertRaises(JudgeError):
            validate_judge_configuration()

    @override_settings(
        **{
            **JUDGE_FALLBACK_SETTINGS,
            "JUDGE_FALLBACK_BASE_URL": "https://hcbifrost.herocraft.com/litellm/v1",
        }
    )
    def test_fallback_equal_to_primary_base_url_raises(self) -> None:
        with self.assertRaises(JudgeError):
            validate_judge_configuration()

    @override_settings(JUDGE_BASE_URL="https://openrouter.ai/api/v1")
    def test_rollback_profile_without_fallback_validates(self) -> None:
        validate_judge_configuration()
        self.assertIsNone(judge_fallback_endpoint())

    @override_settings(
        JUDGE_BASE_URL="https://openrouter.ai/api/v1",
        **JUDGE_FALLBACK_SETTINGS,
    )
    def test_rollback_profile_with_fallback_still_configured_raises(self) -> None:
        # The historical OpenRouter endpoint is both primary and fallback here.
        with (
            override_settings(JUDGE_FALLBACK_BASE_URL="https://openrouter.ai/api/v1"),
            self.assertRaises(JudgeError),
        ):
            validate_judge_configuration()

    @override_settings(**JUDGE_FALLBACK_SETTINGS)
    def test_fallback_profile_uses_its_own_reasoning_not_the_primarys(self) -> None:
        with override_settings(JUDGE_REASONING_EFFORT_SEAT_1="thinking.disabled"):
            primary = resolve_judge_seat_profile(1)
            fallback = resolve_judge_fallback_seat_profile(1, primary)
        self.assertEqual(fallback.provider, "openrouter")
        self.assertEqual(fallback.reasoning, "medium")
        body = _payload([REQ], fallback, "")
        self.assertEqual(body["reasoning"], {"effort": "medium", "exclude": True})
        self.assertNotIn("thinking", body)
        self.assertNotIn("thinking.disabled", json.dumps(body))

    @override_settings(
        JUDGE_BASE_URL="https://openrouter.ai/api/v1",
        JUDGE_REASONING_EFFORT_SEAT_1="high",
        JUDGE_FALLBACK_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_FALLBACK_API_KEY="sk-fallback",
        JUDGE_FALLBACK_MODEL_SEAT_1="vendor-c/model",
        JUDGE_FALLBACK_MODEL_SEAT_2="vendor-d/model",
        JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1="thinking.disabled",
        JUDGE_FALLBACK_REASONING_EFFORT_SEAT_2="",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_1="json_schema",
        JUDGE_FALLBACK_RESPONSE_FORMAT_SEAT_2="json_schema",
    )
    def test_reverse_fallback_profile_never_leaks_the_primarys_reasoning(self) -> None:
        primary = resolve_judge_seat_profile(1)
        fallback = resolve_judge_fallback_seat_profile(1, primary)
        self.assertEqual(fallback.provider, "litellm")
        body = _payload([REQ], fallback, "")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning", body)
        self.assertNotIn("high", json.dumps(body.get("reasoning", "")))

    @override_settings(
        **{
            **JUDGE_FALLBACK_SETTINGS,
            "JUDGE_FALLBACK_BASE_URL": "https://hcbifrost.herocraft.com/litellm/v1",
            "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1": "low",
        }
    )
    def test_invalid_fallback_reasoning_for_litellm_provider_raises(self) -> None:
        with self.assertRaises(JudgeError):
            validate_judge_configuration()

    @override_settings(**JUDGE_FALLBACK_SETTINGS)
    def test_snapshot_records_fallback_metadata_without_key_material(self) -> None:
        snapshot = judge_configuration_snapshot()
        self.assertEqual(snapshot["fallback_hostname"], "openrouter.ai")
        self.assertEqual(
            snapshot["fallback_model"], ["vendor-c/model", "vendor-d/model"]
        )
        self.assertEqual(snapshot["fallback_reasoning"], ["medium", "low"])
        self.assertEqual(
            snapshot["fallback_response_format"], ["json_schema", "json_schema"]
        )
        blob = json.dumps(snapshot)
        self.assertNotIn("sk-fallback", blob)
        self.assertNotIn("sk-primary", blob)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeServingIdentityTest(TestCase):
    """Every result carries the identity of the profile that actually served it."""

    @http_mock.activate
    def test_a_successful_primary_result_carries_its_own_serving_identity(
        self,
    ) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        profile = resolve_judge_seat_profile(1)
        [result] = request_verdicts([REQ], seat=1, persist_attempts=True)
        self.assertEqual(result.served_model, profile.model)
        self.assertEqual(result.served_provider, profile.provider)
        self.assertEqual(result.served_profile_fingerprint, profile.profile_fingerprint)
        self.assertEqual(
            result.served_prompt_schema_version, profile.prompt_schema_version
        )

    @override_settings(JUDGE_TRANSIENT_HTTP_RETRIES=0)
    @http_mock.activate
    def test_an_unparsed_result_carries_the_identity_of_the_endpoint_asked_last(
        self,
    ) -> None:
        http_mock.register("POST", CHAT_URL, status_code=500, json={})
        profile = resolve_judge_seat_profile(1)
        [result] = request_verdicts([REQ], seat=1, persist_attempts=True)
        self.assertTrue(result.unparsed)
        self.assertEqual(result.served_provider, profile.provider)
        self.assertEqual(result.served_model, profile.model)
        self.assertEqual(result.served_profile_fingerprint, profile.profile_fingerprint)


def _canned_response(
    *,
    status_code=None,
    payload=None,
    failure_kind="",
    finish_reason="",
    transport_succeeded=False,
) -> _BatchResponse:
    return _BatchResponse(
        status_code=status_code,
        payload=payload,
        failure_kind=failure_kind,
        finish_reason=finish_reason,
        transport_succeeded=transport_succeeded,
    )


_FALLBACK_PARSED_PAYLOAD = _reply(
    [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-primary",
    JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
    JUDGE_TRANSPORT_RETRIES=0,
    JUDGE_TRANSIENT_HTTP_RETRIES=0,
    JUDGE_PROTOCOL_RETRIES=0,
    **JUDGE_FALLBACK_SETTINGS,
)
class JudgeFailoverTest(TestCase):
    """One fallback attempt per batch per seat, exactly on the permitted kinds."""

    def test_failover_and_availability_kinds_differ_exactly_as_specified(
        self,
    ) -> None:
        self.assertEqual(
            _FAILOVER_FAILURE_KINDS - _AVAILABILITY_FAILURE_KINDS, {"http-auth"}
        )
        self.assertEqual(
            _AVAILABILITY_FAILURE_KINDS - _FAILOVER_FAILURE_KINDS, {"http-other"}
        )

    @staticmethod
    def _run_with_primary_failure(primary_response, *, fallback_response=None, **kw):
        calls = []

        def fake_post_batch(payload, profile):
            calls.append(profile.provider)
            if profile.provider == "litellm":
                return primary_response
            return (
                fallback_response if fallback_response is not None else primary_response
            )

        with mock.patch("weblate.trans.judge._post_batch", side_effect=fake_post_batch):
            [result] = request_verdicts([REQ], seat=1, persist_attempts=True, **kw)
        return result, calls

    def test_transport_fails_over_to_a_parsed_fallback_result(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(failure_kind="transport"),
            fallback_response=_canned_response(
                status_code=200,
                payload=_FALLBACK_PARSED_PAYLOAD,
                transport_succeeded=True,
            ),
        )
        self.assertFalse(result.unparsed)
        self.assertEqual(result.served_provider, "openrouter")
        self.assertEqual(calls, ["litellm", "openrouter"])

    def test_deadline_fails_over(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(failure_kind="deadline"),
            fallback_response=_canned_response(
                status_code=200,
                payload=_FALLBACK_PARSED_PAYLOAD,
                transport_succeeded=True,
            ),
        )
        self.assertFalse(result.unparsed)
        self.assertEqual(calls, ["litellm", "openrouter"])

    def test_http_server_fails_over(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(
                status_code=500, failure_kind="http-server", transport_succeeded=True
            ),
            fallback_response=_canned_response(
                status_code=200,
                payload=_FALLBACK_PARSED_PAYLOAD,
                transport_succeeded=True,
            ),
        )
        self.assertFalse(result.unparsed)
        self.assertEqual(calls, ["litellm", "openrouter"])

    def test_http_rate_limit_fails_over(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(
                status_code=429,
                failure_kind="http-rate-limit",
                transport_succeeded=True,
            ),
            fallback_response=_canned_response(
                status_code=200,
                payload=_FALLBACK_PARSED_PAYLOAD,
                transport_succeeded=True,
            ),
        )
        self.assertFalse(result.unparsed)
        self.assertEqual(calls, ["litellm", "openrouter"])

    def test_http_other_does_not_fail_over(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(
                status_code=402, failure_kind="http-other", transport_succeeded=True
            ),
        )
        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "http-other")
        self.assertEqual(calls, ["litellm"])

    def test_protocol_failure_does_not_fail_over(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(
                status_code=200,
                payload={"choices": [{"message": {"content": "not json"}}]},
                transport_succeeded=True,
            ),
        )
        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "invalid-json")
        self.assertEqual(calls, ["litellm"])

    def test_a_parsed_primary_result_never_fails_over(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(
                status_code=200,
                payload=_reply(
                    [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
                ),
                transport_succeeded=True,
            ),
        )
        self.assertFalse(result.unparsed)
        self.assertEqual(calls, ["litellm"])

    def test_http_auth_with_fallback_success_completes_the_run(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(status_code=401, failure_kind="http-auth"),
            fallback_response=_canned_response(
                status_code=200,
                payload=_FALLBACK_PARSED_PAYLOAD,
                transport_succeeded=True,
            ),
        )
        self.assertFalse(result.unparsed)
        self.assertEqual(result.served_provider, "openrouter")
        self.assertEqual(calls, ["litellm", "openrouter"])

    def test_http_auth_on_both_endpoints_raises_with_no_third_call(self) -> None:
        calls = []

        def fake_post_batch(payload, profile):
            calls.append(profile.provider)
            return _canned_response(status_code=401, failure_kind="http-auth")

        with (
            mock.patch("weblate.trans.judge._post_batch", side_effect=fake_post_batch),
            self.assertRaises(JudgeError),
        ):
            request_verdicts([REQ], seat=1, persist_attempts=True)
        self.assertEqual(calls, ["litellm", "openrouter"])

    @override_settings(**dict.fromkeys(JUDGE_FALLBACK_SETTINGS, ""))
    def test_http_auth_without_a_fallback_raises_immediately(self) -> None:
        calls = []

        def fake_post_batch(payload, profile):
            calls.append(profile.provider)
            return _canned_response(status_code=401, failure_kind="http-auth")

        with (
            mock.patch("weblate.trans.judge._post_batch", side_effect=fake_post_batch),
            self.assertRaises(JudgeError),
        ):
            request_verdicts([REQ], seat=1, persist_attempts=True)
        self.assertEqual(calls, ["litellm"])

    def test_a_fallback_that_also_fails_yields_unparsed_not_an_exception(self) -> None:
        result, calls = self._run_with_primary_failure(
            _canned_response(
                status_code=500, failure_kind="http-server", transport_succeeded=True
            ),
        )
        self.assertTrue(result.unparsed)
        self.assertEqual(calls, ["litellm", "openrouter"])
        self.assertEqual(result.served_provider, "openrouter")

    def test_fallback_fires_even_when_the_retry_budget_is_exhausted(self) -> None:
        budget = RetryBudget(maximum=0)
        result, calls = self._run_with_primary_failure(
            _canned_response(
                status_code=500, failure_kind="http-server", transport_succeeded=True
            ),
            fallback_response=_canned_response(
                status_code=200,
                payload=_FALLBACK_PARSED_PAYLOAD,
                transport_succeeded=True,
            ),
            retry_budget=budget,
        )
        self.assertFalse(result.unparsed)
        self.assertEqual(calls, ["litellm", "openrouter"])
        self.assertEqual(budget.used, 0)

    def test_fallback_attempt_is_persisted_with_distinguishable_identity(
        self,
    ) -> None:
        self._run_with_primary_failure(
            _canned_response(
                status_code=500, failure_kind="http-server", transport_succeeded=True
            ),
            fallback_response=_canned_response(
                status_code=200,
                payload=_FALLBACK_PARSED_PAYLOAD,
                transport_succeeded=True,
            ),
        )
        primary_attempt = JudgeRequestAttempt.objects.get(provider="litellm")
        fallback_attempt = JudgeRequestAttempt.objects.get(provider="openrouter")
        self.assertNotEqual(
            primary_attempt.endpoint_fingerprint, fallback_attempt.endpoint_fingerprint
        )
        self.assertNotEqual(primary_attempt.attempt, fallback_attempt.attempt)

    def test_fallback_response_creates_exactly_one_usage_row(self) -> None:
        fallback_payload = {
            **_FALLBACK_PARSED_PAYLOAD,
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        self._run_with_primary_failure(
            _canned_response(
                status_code=500, failure_kind="http-server", transport_succeeded=True
            ),
            fallback_response=_canned_response(
                status_code=200, payload=fallback_payload, transport_succeeded=True
            ),
            project_slug="proj",
        )
        self.assertEqual(LLMUsageLog.objects.count(), 1)
        row = LLMUsageLog.objects.get()
        self.assertEqual(row.service, "openrouter")
        self.assertEqual(row.model, "vendor-c/model")
        fallback_attempt = JudgeRequestAttempt.objects.get(provider="openrouter")
        self.assertEqual(row.request_attempt_id, fallback_attempt.pk)

    @override_settings(JUDGE_BATCH_SIZE=2, JUDGE_RETRY_BUDGET_RATIO=10)
    def test_width_one_isolation_never_attempts_a_fallback(self) -> None:
        second_request = replace(REQ, unit_key="OTHER_KEY")
        calls = []

        def fake_post_batch(payload, profile):
            calls.append(profile.provider)
            return _canned_response(
                status_code=200,
                payload={"choices": [{"message": {"content": "not json"}}]},
                transport_succeeded=True,
            )

        with mock.patch("weblate.trans.judge._post_batch", side_effect=fake_post_batch):
            results = request_verdicts(
                [REQ, second_request], seat=1, persist_attempts=True
            )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.unparsed for item in results))
        self.assertTrue(all(item.served_provider == "litellm" for item in results))
        self.assertEqual(set(calls), {"litellm"})

    def test_every_failure_kind_fails_over_only_when_it_is_an_availability_kind(
        self,
    ) -> None:
        """
        Enumerate the closed kind set rather than trusting a representative.

        A regression that routed a parser kind to the fallback, or that
        stopped failing over on one availability kind, passes a
        set-difference assertion but fails here.
        """
        fallback = _canned_response(
            status_code=200,
            payload=_FALLBACK_PARSED_PAYLOAD,
            transport_succeeded=True,
        )
        for kind in sorted(FAILURE_KINDS):
            with self.subTest(kind=kind):
                if kind == "http-request-invalid":
                    # A refused request aborts the run outright and must
                    # still never reach the fallback: resending a request
                    # the endpoint rejected is waste, not availability.
                    with self.assertRaises(JudgeError):
                        self._run_with_primary_failure(
                            _canned_response(failure_kind=kind),
                            fallback_response=fallback,
                        )
                    continue
                _result, calls = self._run_with_primary_failure(
                    _canned_response(failure_kind=kind),
                    fallback_response=fallback,
                )
                if kind in _FAILOVER_FAILURE_KINDS:
                    self.assertEqual(calls, ["litellm", "openrouter"])
                else:
                    self.assertEqual(calls, ["litellm"])

    @override_settings(JUDGE_BATCH_SIZE=2, JUDGE_RETRY_BUDGET_RATIO=10)
    def test_a_fallback_protocol_failure_never_triggers_width_one_isolation(
        self,
    ) -> None:
        """
        Isolation must key off the primary's own failure, not the fallback's.

        The fallback is one attempt with no isolation of its own. An
        implementation that narrowed on the fallback's ``invalid-json``
        would pay one POST per unit here.
        """
        second_request = replace(REQ, unit_key="OTHER_KEY")
        calls = []

        def fake_post_batch(payload, profile):
            calls.append(profile.provider)
            if profile.provider == "litellm":
                return _canned_response(status_code=500, failure_kind="http-server")
            return _canned_response(
                status_code=200,
                payload={"choices": [{"message": {"content": "not json"}}]},
                transport_succeeded=True,
            )

        with mock.patch("weblate.trans.judge._post_batch", side_effect=fake_post_batch):
            results = request_verdicts(
                [REQ, second_request], seat=1, persist_attempts=True
            )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.unparsed for item in results))
        self.assertEqual(calls, ["litellm", "openrouter"])


_SLASHED_PRIMARY = "https://openrouter.ai/api/v1/"


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-primary",
    JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeEndpointCanonicalizationTest(SimpleTestCase):
    """
    One destination is one endpoint, whatever way the operator spelled it.

    The request path always dropped the trailing slash, so a guard that
    compared raw strings let two spellings of one endpoint through.
    """

    def test_validation_drops_a_trailing_slash(self) -> None:
        self.assertEqual(
            _validate_base_url("https://openrouter.ai/api/v1/"),
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(
            _validate_base_url("https://openrouter.ai/api/v1"),
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(_validate_base_url("https://host/"), "https://host")

    def test_a_slash_only_difference_is_one_endpoint_fingerprint(self) -> None:
        with override_settings(JUDGE_BASE_URL="https://openrouter.ai/api/v1"):
            bare = resolve_judge_seat_profile(1).endpoint_fingerprint
        with override_settings(JUDGE_BASE_URL=_SLASHED_PRIMARY):
            slashed = resolve_judge_seat_profile(1).endpoint_fingerprint
        self.assertEqual(bare, slashed)

    @override_settings(JUDGE_BASE_URL=_SLASHED_PRIMARY, **JUDGE_FALLBACK_SETTINGS)
    def test_a_slash_only_difference_is_rejected_as_the_same_endpoint(self) -> None:
        with self.assertRaises(JudgeError):
            validate_judge_configuration()

    @override_settings(JUDGE_BASE_URL=_SLASHED_PRIMARY, **JUDGE_FALLBACK_SETTINGS)
    def test_the_same_endpoint_is_refused_before_any_direct_request(self) -> None:
        calls = []

        def fake_post_batch(payload, profile):
            calls.append(profile.provider)
            return _canned_response(status_code=200, transport_succeeded=True)

        with (
            mock.patch("weblate.trans.judge._post_batch", side_effect=fake_post_batch),
            self.assertRaises(JudgeError),
        ):
            request_verdicts([REQ], seat=1)
        self.assertEqual(calls, [])

    @override_settings(
        **{**JUDGE_FALLBACK_SETTINGS, "JUDGE_FALLBACK_API_KEY": ""},
    )
    def test_a_partial_fallback_is_refused_before_any_direct_request(self) -> None:
        """
        Refuse a half-configured fallback before any request is sent.

        request_verdicts is a documented direct-caller API and the probe uses
        it. A blank key must never reach an Authorization header.
        """
        calls = []

        def fake_post_batch(payload, profile):
            calls.append(profile.provider)
            return _canned_response(status_code=200, transport_succeeded=True)

        with (
            mock.patch("weblate.trans.judge._post_batch", side_effect=fake_post_batch),
            self.assertRaises(JudgeError),
        ):
            request_verdicts([REQ], seat=1)
        self.assertEqual(calls, [])

    @override_settings(
        **{
            **JUDGE_FALLBACK_SETTINGS,
            "JUDGE_FALLBACK_REASONING_EFFORT_SEAT_1": "inherit",
        }
    )
    def test_the_literal_inherit_is_not_a_fallback_value(self) -> None:
        """The fallback has no inherit semantics; the literal is a mistake."""
        with self.assertRaises(JudgeError):
            validate_judge_configuration()


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test-do-not-leak",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeClientTest(SimpleTestCase):
    @http_mock.activate
    def test_parses_a_verdict(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "reject",
                        "errors": [
                            {
                                "span": "PORTES",
                                "category": "terminology",
                                "severity": "critical",
                                "description": "«ВРАТА» rendered as «DOORS»; glossary says Gates",
                            }
                        ],
                        "back_translation": "The door is blocked by the DOORS",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(result.max_severity, "critical")  # derived from errors
        self.assertEqual(result.model_verdict, "reject")
        self.assertIn("Gates", result.errors[0]["description"])
        self.assertIn("DOORS", result.back_translation)

    @http_mock.activate
    def test_ignores_reasoning_content_when_content_is_valid(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json={
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
                            ),
                            "reasoning_content": '{"segments": "untrusted"}',
                        }
                    }
                ]
            },
        )

        [result] = request_verdicts([REQ], model="vendor/model-a")

        self.assertFalse(result.unparsed)

    @http_mock.activate
    def test_does_not_parse_reasoning_content_without_content(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning_content": json.dumps({"segments": []}),
                        }
                    }
                ]
            },
        )

        [result] = request_verdicts([REQ], model="vendor/model-a")

        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "invalid-envelope")

    @http_mock.activate
    def test_max_severity_is_derived_from_the_worst_error(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "flag",
                        "errors": [
                            {
                                "span": "a",
                                "category": "fluency",
                                "severity": "minor",
                                "description": "x",
                            },
                            {
                                "span": "b",
                                "category": "fluency",
                                "severity": "major",
                                "description": "y",
                            },
                        ],
                        "back_translation": "",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(result.max_severity, "major")

    @http_mock.activate
    def test_no_errors_is_severity_none(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "pass",
                        "errors": [],
                        "back_translation": "",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(result.max_severity, "none")

    @http_mock.activate
    def test_model_instruction_is_ignored_for_an_error_free_pass(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "pass",
                        "errors": [],
                        "back_translation": "",
                        "instruction": "Rewrite it.",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(result.instruction, "")

    @http_mock.activate
    def test_instruction_none_is_ignored_for_an_error_free_pass(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "pass",
                        "errors": [],
                        "back_translation": "",
                        "instruction": "None",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(result.instruction, "")

    @http_mock.activate
    def test_missing_instruction_key_is_accepted(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json={
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
            },
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)

    @http_mock.activate
    def test_null_instruction_is_ignored(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json={
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
                                            "instruction": None,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(result.instruction, "")

    @http_mock.activate
    def test_long_instruction_is_ignored(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "reject",
                        "errors": [
                            {
                                "span": "x",
                                "category": "fluency",
                                "severity": "critical",
                                "description": "y",
                            }
                        ],
                        "back_translation": "",
                        "instruction": "x" * 1001,
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)

    @http_mock.activate
    def test_empty_instruction_is_ignored(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "reject",
                        "errors": [
                            {
                                "span": "x",
                                "category": "fluency",
                                "severity": "critical",
                                "description": "y",
                            }
                        ],
                        "back_translation": "",
                        "instruction": "   ",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)

    @http_mock.activate
    def test_model_generated_instruction_is_ignored_when_errors_present(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "reject",
                        "errors": [
                            {
                                "span": "x",
                                "category": "fluency",
                                "severity": "critical",
                                "description": "y",
                            }
                        ],
                        "back_translation": "",
                        "instruction": "Replace the wrong term with the glossary term.",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(result.instruction, "")

    @http_mock.activate
    def test_batches_many_requests_and_keeps_order(self) -> None:
        # D6: one HTTP call per batch of JUDGE_BATCH_SIZE, results aligned
        # to input order by segment id.
        reqs = [REQ, REQ, REQ]
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""},
                    {
                        "id": 1,
                        "verdict": "reject",
                        "errors": [
                            {
                                "span": "x",
                                "category": "omission",
                                "severity": "critical",
                                "description": "z",
                            }
                        ],
                        "back_translation": "",
                    },
                    {"id": 2, "verdict": "pass", "errors": [], "back_translation": ""},
                ]
            ),
        )
        results = request_verdicts(reqs, model="vendor/model-a")
        self.assertEqual(
            [r.max_severity for r in results], ["none", "critical", "none"]
        )
        self.assertEqual(len(http_mock.calls), 1)

    @http_mock.activate
    def test_sends_strict_schema_batch_and_requires_providers_to_honour_it(
        self,
    ) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)
        body = json.loads(http_mock.calls[0].request.content)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertTrue(body["provider"]["require_parameters"])
        self.assertEqual(body["model"], "vendor/model-a")
        user_msg = {
            "segments": _request_segments(body),
        }
        self.assertIn("segments", user_msg)

    @http_mock.activate
    def test_render_preview_is_attached_when_placeholders_are_present(self) -> None:
        # Arm D: rendered pair goes into the segment (user precondition).
        req = JudgeRequest(
            unit_key="K",
            source="{0} 个国家 {1}",
            target="{1} 的 {0}",
            source_language="ru",
            target_language="zh_Hans",
            note="",
            explanation="",
            glossary_terms=[],
            failing_checks=[],
        )
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts([req], model="vendor/model-a")
        body = json.loads(http_mock.calls[0].request.content)
        segment = _request_segments(body)[0]
        self.assertIn("rendered_source", segment)
        self.assertIn("rendered_target", segment)
        self.assertIn("untrusted_translation_data_", body["messages"][1]["content"])

    def test_render_preview_returns_none_without_placeholders(self) -> None:
        self.assertIsNone(render_preview("plain text"))
        self.assertIsNotNone(render_preview("has {0} slot"))

    def test_render_preview_substitutes_named_braces(self) -> None:
        # 156 units on production use {name}; without this branch they
        # reach the judge with no rendered pair at all.
        rendered = render_preview("Requires {level} of {faction}")
        self.assertIsNotNone(rendered)
        assert rendered is not None
        self.assertNotIn("{level}", rendered)
        self.assertNotIn("{faction}", rendered)
        # Distinct names must render distinctly, or a swapped pair reads
        # as correct.
        self.assertNotEqual(
            render_preview("{level}"),
            render_preview("{faction}"),
        )
        # The bracketed dialect keeps its own branch: {[PARAM0]} must not
        # be eaten as a named placeholder called "[PARAM0]".
        self.assertEqual(render_preview("{[PARAM0]}"), render_preview("{0}"))

    @http_mock.activate
    def test_malformed_json_makes_the_batch_unparsed(self) -> None:
        http_mock.register(
            "POST", CHAT_URL, json={"choices": [{"message": {"content": "not json"}}]}
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)

    @http_mock.activate
    def test_http_error_makes_the_batch_unparsed(self) -> None:
        http_mock.register("POST", CHAT_URL, status_code=500, json={})
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(len(http_mock.calls), 2)

    @mock.patch("weblate.trans.judge.time.sleep")
    @http_mock.activate
    def test_only_rate_limit_errors_are_retried(self, sleep) -> None:
        http_mock.register("POST", CHAT_URL, status_code=429, json={})
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(len(http_mock.calls), 2)
        sleep.assert_called_once()

    @mock.patch("weblate.trans.judge.time.sleep")
    @mock.patch("weblate.trans.judge.time.monotonic", return_value=100.0)
    @http_mock.activate
    def test_retry_after_past_deadline_is_not_retried(self, monotonic, sleep) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            status_code=429,
            headers={"Retry-After": "60"},
            json={},
        )

        [result] = request_verdicts(
            [REQ],
            model="vendor/model-a",
            retry_deadline=150.0,
        )

        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "http-rate-limit")
        self.assertEqual(len(http_mock.calls), 1)
        sleep.assert_not_called()

    @mock.patch("weblate.trans.judge.time.sleep")
    @http_mock.activate
    def test_transport_reset_is_retried_and_recovers(self, sleep) -> None:
        # The corporate LiteLLM gateway closes a connection near 30 s under
        # load. The reply carries no status, so the rate-limit branch cannot
        # see it: measured at 33% of requests for one route, 0% for another
        # (docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md).
        http_mock.register_callback(
            "POST",
            CHAT_URL,
            lambda _request: httpx2.Response(200, stream=ResetStream()),
        )
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)
        self.assertEqual(len(http_mock.calls), 2)
        sleep.assert_called_once()

    @override_settings(JUDGE_TRANSPORT_RETRIES=2)
    @mock.patch("weblate.trans.judge.time.sleep")
    @http_mock.activate
    def test_transport_retry_budget_is_spent_then_the_batch_is_unparsed(
        self, sleep
    ) -> None:
        for _ in range(4):
            http_mock.register_callback(
                "POST",
                CHAT_URL,
                lambda _request: httpx2.Response(200, stream=ResetStream()),
            )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(len(http_mock.calls), 3)
        self.assertEqual(sleep.call_count, 2)

    @override_settings(JUDGE_TRANSPORT_RETRIES=0)
    @http_mock.activate
    def test_transport_retries_can_be_disabled(self) -> None:
        http_mock.register_callback(
            "POST",
            CHAT_URL,
            lambda _request: httpx2.Response(200, stream=ResetStream()),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(len(http_mock.calls), 1)

    @override_settings(JUDGE_TRANSPORT_RETRIES=2)
    @mock.patch("weblate.trans.judge.time.sleep")
    @http_mock.activate
    def test_transport_retry_does_not_extend_the_rate_limit_retry(self, sleep) -> None:
        # A 429 keeps its single retry: the transport budget is independent and
        # must not turn a paid rate-limit refusal into three paid attempts.
        for _ in range(4):
            http_mock.register("POST", CHAT_URL, status_code=429, json={})
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(len(http_mock.calls), 2)
        self.assertEqual(sleep.call_count, 1)

    @http_mock.activate
    def test_malformed_required_fields_make_the_batch_unparsed(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "pass",
                        "errors": [
                            {
                                "span": "x",
                                "category": "fluency",
                                "severity": "minor",
                            }
                        ],
                        "back_translation": "",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)

    @http_mock.activate
    def test_unhashable_verdict_returns_invalid_segment_not_a_crash(self) -> None:
        # "verdict": [] once raised TypeError during set membership instead
        # of classifying the reply as an invalid segment.
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": [], "errors": [], "back_translation": ""}]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "invalid-segment")

    @http_mock.activate
    def test_unhashable_severity_returns_invalid_segment_not_a_crash(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "flag",
                        "errors": [
                            {
                                "span": "x",
                                "category": "fluency",
                                "severity": {},
                                "description": "d",
                            }
                        ],
                        "back_translation": "",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "invalid-segment")

    @http_mock.activate
    def test_unhashable_category_returns_invalid_segment_not_a_crash(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [
                    {
                        "id": 0,
                        "verdict": "flag",
                        "errors": [
                            {
                                "span": "x",
                                "category": [],
                                "severity": "minor",
                                "description": "d",
                            }
                        ],
                        "back_translation": "",
                    }
                ]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "invalid-segment")

    @http_mock.activate
    def test_the_api_key_never_reaches_the_exception_text(self) -> None:
        # A gate failure raises; the key must not be in the message.
        with override_settings(JUDGE_ENABLED=True, JUDGE_API_KEY=""):
            with self.assertRaises(JudgeError) as ctx:
                request_verdicts([REQ], model="vendor/model-a")
            self.assertNotIn("sk-test", str(ctx.exception))

    @override_settings(JUDGE_BASE_URL="https://openrouter.ai/api/v1/")
    @http_mock.activate
    def test_trailing_slash_base_url_still_resolves_the_chat_endpoint(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertFalse(result.unparsed)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeRequestLoggingTest(SimpleTestCase):
    @http_mock.activate
    def test_batch_outcome_is_logged_with_elapsed_time(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        with self.assertLogs("weblate.trans.judge", level="INFO") as logs:
            request_verdicts([REQ], model="vendor/model-a")
        joined = "\n".join(logs.output)
        self.assertIn("batch", joined)
        self.assertIn("ms", joined)

    @http_mock.activate
    def test_failed_batch_is_logged_at_warning(self) -> None:
        http_mock.register("POST", CHAT_URL, status_code=500, json={})
        with self.assertLogs("weblate.trans.judge", level="WARNING") as logs:
            request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(any("500" in line for line in logs.output))


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeRequestDeadlineTest(TestCase):
    def _dripping_response(self) -> httpx2.Response:
        body = json.dumps(
            _reply([{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}])
        ).encode()
        return httpx2.Response(200, stream=DrippingStream(body, 0.01))

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_REQUEST_DEADLINE=0.1)
    @http_mock.activate
    def test_deadline_marks_a_dripping_batch_unparsed_without_usage_or_retry(
        self,
    ) -> None:
        http_mock.register_callback(
            "POST",
            CHAT_URL,
            lambda _request: self._dripping_response(),
        )
        started = time.monotonic()
        [result] = request_verdicts([REQ], model="vendor/model-a")
        elapsed = time.monotonic() - started

        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "deadline")
        self.assertLess(elapsed, 1)
        self.assertEqual(LLMUsageLog.objects.count(), 0)
        self.assertEqual(len(http_mock.calls), 1)

    @override_settings(JUDGE_BATCH_SIZE=1, JUDGE_REQUEST_DEADLINE=0.1)
    @http_mock.activate
    def test_deadline_marks_only_the_dripping_batch_unparsed(self) -> None:
        http_mock.register_callback(
            "POST",
            CHAT_URL,
            lambda _request: self._dripping_response(),
        )
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )

        first, second = request_verdicts([REQ, REQ], model="vendor/model-a")

        self.assertTrue(first.unparsed)
        self.assertFalse(second.unparsed)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_REQUEST_DEADLINE=3)
    @http_mock.activate
    def test_chunked_body_inside_deadline_parses_normally(self) -> None:
        body = json.dumps(
            _reply([{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}])
        ).encode()
        http_mock.register_callback(
            "POST",
            CHAT_URL,
            lambda _request: httpx2.Response(
                200,
                stream=DrippingStream(body, 0.01),
            ),
        )

        [result] = request_verdicts([REQ], model="vendor/model-a")

        self.assertFalse(result.unparsed)

    @override_settings(
        JUDGE_REQUEST_DEADLINE=3,
        JUDGE_REQUEST_DEADLINE_SEAT_1="0.05",
        JUDGE_REQUEST_DEADLINE_SEAT_2="3",
        JUDGE_MODEL_SEAT_1="vendor/model-a",
        JUDGE_MODEL_SEAT_2="vendor/model-b",
    )
    @http_mock.activate
    def test_each_seat_uses_its_own_absolute_deadline(self) -> None:
        for _ in range(2):
            http_mock.register_callback(
                "POST",
                CHAT_URL,
                lambda _request: self._dripping_response(),
            )

        [first] = request_verdicts([REQ], seat=1)
        [second] = request_verdicts([REQ], seat=2)

        self.assertTrue(first.unparsed)
        self.assertEqual(first.failure_kind, "deadline")
        self.assertFalse(second.unparsed)

    @override_settings(
        JUDGE_REQUEST_DEADLINE=120,
        JUDGE_REQUEST_DEADLINE_SEAT_1="7",
        JUDGE_REQUEST_DEADLINE_SEAT_2="300",
        JUDGE_MODEL_SEAT_1="vendor/model-a",
        JUDGE_MODEL_SEAT_2="vendor/model-b",
    )
    @mock.patch("weblate.trans.judge.time.sleep")
    @http_mock.activate
    def test_rate_limit_sleep_is_capped_by_the_seat_deadline(self, sleep) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            status_code=429,
            headers={"Retry-After": "60"},
            json={},
        )
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )

        [result] = request_verdicts([REQ], seat=1)

        self.assertFalse(result.unparsed)
        sleep.assert_called_once_with(7)

    @override_settings(JUDGE_BATCH_SIZE=5, JUDGE_REQUEST_DEADLINE=30)
    @mock.patch("weblate.trans.judge.MAX_BATCH_RESPONSE_BYTES", 32)
    @http_mock.activate
    def test_oversized_body_is_unparsed_without_being_buffered(self) -> None:
        body = b"x" * 4096
        http_mock.register_callback(
            "POST",
            CHAT_URL,
            lambda _request: httpx2.Response(200, content=body),
        )

        with self.assertLogs("weblate.trans.judge", level="WARNING") as logs:
            [result] = request_verdicts([REQ], model="vendor/model-a")

        self.assertTrue(result.unparsed)
        self.assertTrue(any("response-too-large" in line for line in logs.output))
        self.assertEqual(LLMUsageLog.objects.count(), 0)

    @override_settings(
        JUDGE_BATCH_SIZE=4,
        JUDGE_REQUEST_DEADLINE=0.1,
        JUDGE_MODEL_SEAT_1="vendor/model-a",
        JUDGE_MODEL_SEAT_2="vendor/model-b",
    )
    @http_mock.activate
    def test_a_deadline_shrinks_the_batch_for_the_rest_of_the_same_run(self) -> None:
        """
        A slow seat must not spend a whole run on batches it cannot finish.

        On 2026-08-31 a dev canary lost every verdict of one seat this way: all
        five of its batches were cut at the deadline, because the batch size
        was fixed before the loop and the adaptive shrink recorded after the
        first cut could not take effect until the next run.
        """
        http_mock.register_callback(
            "POST",
            CHAT_URL,
            lambda _request: self._dripping_response(),
        )

        request_verdicts([REQ] * 8, seat=1, adaptive=True)

        sizes = [
            len(_request_segments(json.loads(call.request.content)))
            for call in http_mock.calls
        ]
        self.assertEqual(sizes[0], 4)
        self.assertEqual(sizes[1:], [2, 1, 1])


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeOnBatchTest(SimpleTestCase):
    @override_settings(JUDGE_BATCH_SIZE=1)
    @http_mock.activate
    def test_is_called_once_per_completed_batch_in_input_order(self) -> None:
        requests = [replace(REQ, unit_key=str(index)) for index in range(3)]
        for _ in requests:
            http_mock.register(
                "POST",
                CHAT_URL,
                json=_reply(
                    [
                        {
                            "id": 0,
                            "verdict": "pass",
                            "errors": [],
                            "back_translation": "",
                        }
                    ]
                ),
            )
        seen: list[tuple[list[str], int]] = []

        request_verdicts(
            requests,
            model="vendor/model-a",
            on_batch=lambda batch_requests, batch_results: seen.append(
                ([request.unit_key for request in batch_requests], len(batch_results))
            ),
        )

        self.assertEqual(seen, [(["0"], 1), (["1"], 1), (["2"], 1)])

    @override_settings(JUDGE_BATCH_SIZE=5)
    @mock.patch("weblate.trans.judge.time.sleep")
    @http_mock.activate
    def test_retry_is_one_completed_batch(self, sleep) -> None:
        http_mock.register("POST", CHAT_URL, status_code=429, json={})
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        seen: list[int] = []

        request_verdicts(
            [REQ],
            model="vendor/model-a",
            on_batch=lambda _requests, results: seen.append(len(results)),
        )

        self.assertEqual(len(http_mock.calls), 2)
        self.assertEqual(seen, [1])
        sleep.assert_called_once()

    @override_settings(JUDGE_BATCH_SIZE=5)
    @http_mock.activate
    def test_unparsed_batch_still_calls_callback(self) -> None:
        http_mock.register("POST", CHAT_URL, status_code=500, json={})
        seen = []

        request_verdicts(
            [REQ],
            model="vendor/model-a",
            on_batch=lambda batch_requests, results: seen.append(
                (batch_requests, results)
            ),
        )

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], [REQ])
        self.assertTrue(seen[0][1][0].unparsed)


class JudgeUsageLogTest(TestCase):
    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_is_recorded_for_a_successful_batch(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json={
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
                                        },
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "cost": 0.001,
                    "prompt_tokens_details": {"cached_tokens": 2},
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
                "id": "resp-1",
            },
        )
        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.prompt_tokens, 11)
        self.assertEqual(row.completion_tokens, 7)
        self.assertEqual(row.total_tokens, 18)
        self.assertEqual(row.cached_tokens, 2)
        self.assertEqual(row.reasoning_tokens, 1)
        self.assertEqual(row.response_id, "resp-1")

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_is_attributed_to_the_project(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register("POST", CHAT_URL, json=payload)
        request_verdicts(
            [
                replace(
                    REQ,
                    project_id_snapshot=101,
                    component_id_snapshot=102,
                    component_slug="ui",
                )
            ],
            model="vendor/model-a",
            project_slug="need-for-greed",
            persist_attempts=True,
        )
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.project_id_snapshot, 101)
        self.assertEqual(row.project_slug, "need-for-greed")
        self.assertEqual(row.component_id_snapshot, 102)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_is_attributed_to_component_language_and_service(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "cost": "__COST__",
        }
        http_mock.register(
            "POST",
            CHAT_URL,
            content=json.dumps(payload).replace('"__COST__"', "0.123456789123456789"),
        )

        request_verdicts(
            [
                replace(
                    REQ,
                    project_id_snapshot=101,
                    component_id_snapshot=102,
                    component_slug="ui",
                )
            ],
            model="vendor/model-a",
            project_slug="need-for-greed",
            persist_attempts=True,
        )

        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.service, "openrouter")
        self.assertEqual(row.project_id_snapshot, 101)
        self.assertEqual(row.project_slug, "need-for-greed")
        self.assertEqual(row.component_id_snapshot, 102)
        self.assertEqual(row.component_slug, "ui")
        self.assertEqual(row.target_language_code, "fr")
        self.assertEqual(row.cost_usd, Decimal("0.123456789123456789"))

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_records_zero_provider_cost(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "cost": 0}
        http_mock.register("POST", CHAT_URL, json=payload)

        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)

        self.assertEqual(
            LLMUsageLog.objects.get(model="vendor/model-a").cost_usd,
            Decimal(0),
        )

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_marks_out_of_scale_cost_unpriced(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "cost": "__COST__",
        }
        http_mock.register(
            "POST",
            CHAT_URL,
            content=json.dumps(payload).replace('"__COST__"', "0.1234567891234567891"),
        )

        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)

        self.assertIsNone(LLMUsageLog.objects.get(model="vendor/model-a").cost_usd)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_leaves_a_mixed_batch_unattributed(self) -> None:
        payload = _reply(
            [
                {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""},
                {"id": 1, "verdict": "pass", "errors": [], "back_translation": ""},
            ]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register("POST", CHAT_URL, json=payload)
        request_verdicts(
            [
                replace(
                    REQ,
                    project_id_snapshot=101,
                    component_id_snapshot=102,
                    component_slug="ui",
                ),
                replace(
                    REQ,
                    unit_key="OTHER",
                    project_id_snapshot=101,
                    component_id_snapshot=103,
                    component_slug="loot",
                ),
            ],
            model="vendor/model-a",
            project_slug="need-for-greed",
            persist_attempts=True,
        )
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(
            (
                row.project_id_snapshot,
                row.project_slug,
                row.component_id_snapshot,
                row.component_slug,
                row.target_language_code,
            ),
            (None, "", None, "", ""),
        )

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
        JUDGE_PROTOCOL_RETRIES=0,
    )
    @http_mock.activate
    def test_usage_is_recorded_when_the_batch_fails_to_parse(self) -> None:
        payload: dict[str, Any] = {"choices": [{"message": {"content": "not json"}}]}
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register(
            "POST",
            CHAT_URL,
            json=payload,
        )
        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.prompt_tokens, 11)
        self.assertEqual(row.completion_tokens, 7)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_operation_and_unit_count(self) -> None:
        reqs = [REQ, replace(REQ, unit_key="MENU_DOOR_2")]
        payload = _reply(
            [
                {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""},
                {"id": 1, "verdict": "pass", "errors": [], "back_translation": ""},
            ]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register("POST", CHAT_URL, json=payload)
        request_verdicts(reqs, model="vendor/model-a", persist_attempts=True)
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.operation, LLMUsageLog.Operation.JUDGE)
        self.assertEqual(row.unit_count, 2)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @mock.patch("weblate.trans.judge.time.sleep")
    @http_mock.activate
    def test_billed_retry_logs_actual_batch_size(self, sleep) -> None:
        reqs = [REQ, replace(REQ, unit_key="MENU_DOOR_2")]
        usage = {"prompt_tokens": 11, "completion_tokens": 7, "cost": 0.002}
        http_mock.register("POST", CHAT_URL, status_code=429, json={"usage": usage})
        retry_payload = _reply(
            [
                {"id": 0, "verdict": "pass", "errors": [], "back_translation": ""},
                {"id": 1, "verdict": "pass", "errors": [], "back_translation": ""},
            ]
        )
        retry_payload["usage"] = usage
        http_mock.register("POST", CHAT_URL, json=retry_payload)

        request_verdicts(reqs, model="vendor/model-a", persist_attempts=True)

        rows = list(LLMUsageLog.objects.order_by("id"))
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.unit_count for row in rows], [2, 2])
        self.assertTrue(
            all(row.operation == LLMUsageLog.Operation.JUDGE for row in rows)
        )
        sleep.assert_called_once()

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_accounting_failure_does_not_break_judging(self) -> None:
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register("POST", CHAT_URL, json=payload)
        with mock.patch.object(
            LLMUsageLog._default_manager,  # ruff: ignore[private-member-access]
            "create",
            side_effect=DatabaseError("boom"),
        ):
            results = request_verdicts(
                [REQ], model="vendor/model-a", persist_attempts=True
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].model_verdict, "pass")
        self.assertEqual(LLMUsageLog.objects.count(), 0)


class JudgeReasoningBudgetTest(TestCase):
    """Reasoning tokens were 84% of the first dev run's cost."""

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_reasoning_is_absent_by_default(self) -> None:
        # The measurement was run without the parameter; sending one by
        # default would invalidate it.
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts([REQ], model="vendor/model-a")
        body = json.loads(http_mock.calls[0].request.content)
        self.assertNotIn("reasoning", body)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
        JUDGE_REASONING_EFFORT="low",
    )
    @http_mock.activate
    def test_reasoning_effort_is_sent_when_configured(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts([REQ], model="vendor/model-a")
        body = json.loads(http_mock.calls[0].request.content)
        # exclude: the judge never reads the trace, so paying to ship it
        # back is pure waste.
        self.assertEqual(body["reasoning"], {"effort": "low", "exclude": True})


class JudgePromptContextTest(TestCase):
    """The prompt must carry the project's setting, never a hardcoded one."""

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_prompt_carries_the_project_context(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts(
            [REQ],
            model="vendor/model-a",
            project_context="A dark fantasy world of Hollspeak.",
        )
        prompt = json.loads(http_mock.calls[0].request.content)["messages"][0][
            "content"
        ]
        self.assertIn("A dark fantasy world of Hollspeak.", prompt)
        self.assertNotIn("{project_context}", prompt)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_prompt_falls_back_to_the_neutral_context(self) -> None:
        # A project that configured nothing must not inherit another
        # project's setting: that is how a WWII register produced a false
        # major on a post-apocalyptic quest.
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts([REQ], model="vendor/model-a")
        prompt = json.loads(http_mock.calls[0].request.content)["messages"][0][
            "content"
        ]
        self.assertNotIn("{project_context}", prompt)
        for genre in ("World War II", "strategy", "military"):
            self.assertNotIn(genre, prompt)
        self.assertIn("not specified", prompt)


LITELLM_CHAT_URL = "https://hcbifrost.herocraft.com/litellm/v1/chat/completions"


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeLiteLLMPayloadTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        # Alias resolution is exercised separately; these payload tests
        # must observe only the chat-completions POST.

        _ALIAS_CACHE.clear()

    @staticmethod
    def _post_calls():
        return [call for call in http_mock.calls if call.request.method == "POST"]

    @override_settings(JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1")
    @http_mock.activate
    def test_litellm_payload_has_neither_usage_nor_provider(self) -> None:
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            json={
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
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )
        request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)
        body = json.loads(self._post_calls()[0].request.content)
        self.assertNotIn("usage", body)
        self.assertNotIn("provider", body)
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.prompt_tokens, 11)
        self.assertEqual(row.completion_tokens, 7)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
        JUDGE_REASONING_EFFORT_SEAT_1="thinking.disabled",
        JUDGE_REASONING_EFFORT_SEAT_2="enable_thinking=false",
        JUDGE_RESPONSE_FORMAT_SEAT_1="json_object",
        JUDGE_RESPONSE_FORMAT_SEAT_2="json_schema",
        JUDGE_STREAM_SEAT_1=False,
        JUDGE_STREAM_SEAT_2=False,
        JUDGE_BATCH_SIZE_SEAT_1=2,
        JUDGE_BATCH_SIZE_SEAT_2=5,
    )
    @http_mock.activate
    def test_per_seat_litellm_profiles_control_payloads(self) -> None:
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )
        request_verdicts([REQ], seat=1)
        request_verdicts([REQ], seat=2)

        first, second = (
            json.loads(call.request.content) for call in self._post_calls()
        )
        self.assertEqual(first["model"], "weblate-judge-deepseek-v4-pro")
        self.assertEqual(first["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", first)
        self.assertEqual(first["response_format"], {"type": "json_object"})
        self.assertEqual(second["model"], "atlas/qwen3.8-max")
        self.assertFalse(second["enable_thinking"])
        self.assertNotIn("thinking", second)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
    )
    @http_mock.activate
    def test_alias_resolution_resolves_upstream_and_invalidates_on_retarget(
        self,
    ) -> None:

        info_url = "https://hcbifrost.herocraft.com/litellm/v1/model/info"

        def alias_reply(model: str, upstream: str):
            return {
                "data": [{"model_name": model, "litellm_params": {"model": upstream}}]
            }

        http_mock.register(
            "GET",
            info_url,
            json=alias_reply("weblate-judge-deepseek-v4-pro", "deepseek/upstream-a"),
        )
        http_mock.register(
            "GET",
            info_url,
            json=alias_reply("weblate-judge-deepseek-v4-pro", "deepseek/upstream-b"),
        )
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )

        first = resolve_judge_seat_profile(1)
        self.assertEqual(first.upstream_model, "deepseek/upstream-a")
        self.assertTrue(first.alias_revision)

        # Cached resolution: resolving again adds no new capability call
        # (seat 1's alias is cached; seat 2 resolves once and caches too).
        resolve_judge_seat_profile(1)
        alias_calls = [call for call in http_mock.calls if call.request.method == "GET"]
        self.assertEqual(len(alias_calls), 2)
        resolve_judge_seat_profile(1)
        alias_calls = [call for call in http_mock.calls if call.request.method == "GET"]
        self.assertEqual(len(alias_calls), 2)

        # An operator retarget: expiry forces a re-lookup for seat 1, and
        # both the model and profile fingerprints must change so the
        # verdict cache is invalidated with unchanged environment variables.
        key = ("https://hcbifrost.herocraft.com/litellm/v1", first.model)
        _ALIAS_CACHE[key] = (first.upstream_model, first.alias_revision, 0.0)
        second = resolve_judge_seat_profile(1)
        self.assertEqual(second.upstream_model, "deepseek/upstream-b")
        self.assertNotEqual(first.model_fingerprint, second.model_fingerprint)
        self.assertNotEqual(first.profile_fingerprint, second.profile_fingerprint)

    @override_settings(
        JUDGE_BASE_URL="https://openrouter.ai/api/v1",
        JUDGE_MODEL_SEAT_1="vendor/model-a",
        JUDGE_MODEL_SEAT_2="vendor/model-b",
    )
    @http_mock.activate
    def test_non_litellm_provider_never_resolves_aliases(self) -> None:
        profile = resolve_judge_seat_profile(1)
        # Non-LiteLLM endpoints have no alias layer: the profile records
        # the configured model as its own upstream and no revision.
        self.assertEqual(profile.upstream_model, "vendor/model-a")
        self.assertEqual(profile.alias_revision, "")
        self.assertEqual(
            [call for call in http_mock.calls if call.request.method == "GET"],
            [],
        )

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_REASONING_EFFORT="none",
    )
    @http_mock.activate
    def test_unmapped_model_with_reasoning_disabled_makes_no_network_call(
        self,
    ) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
    )
    @http_mock.activate
    def test_dripping_alias_discovery_degrades_after_absolute_deadline(
        self,
    ) -> None:
        info_url = "https://hcbifrost.herocraft.com/litellm/v1/model/info"
        body = json.dumps(
            {
                "data": [
                    {
                        "model_name": "weblate-judge-deepseek-v4-pro",
                        "litellm_params": {"model": "deepseek/upstream-a"},
                    }
                ]
            }
        ).encode()
        http_mock.register_callback(
            "GET",
            info_url,
            lambda _request: httpx2.Response(200, stream=DrippingStream(body, 0.05)),
        )
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )

        with mock.patch("weblate.trans.judge._ALIAS_INFO_TIMEOUT", 0.05):
            started = time.monotonic()
            profile = resolve_judge_seat_profile(1)
            elapsed = time.monotonic() - started

        # Each individual read succeeds well inside a per-op transport
        # timeout; only an absolute wall-clock deadline on the whole
        # discovery call can bound a slow-trickle peer.
        self.assertLess(elapsed, 1)
        self.assertEqual(profile.upstream_model, "weblate-judge-deepseek-v4-pro")
        self.assertEqual(profile.alias_revision, "")

    def test_capped_reader_rejects_an_oversized_chunk_without_buffering_it(
        self,
    ) -> None:
        response = httpx2.Response(200, content=b"0123456789")

        self.assertIsNone(_read_capped(response, cap=4))

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
        JUDGE_REASONING_EFFORT_SEAT_1="thinking.disabled",
        JUDGE_REASONING_EFFORT_SEAT_2="enable_thinking=false",
        JUDGE_STREAM_SEAT_1=True,
        JUDGE_STREAM_SEAT_2=True,
    )
    @http_mock.activate
    def test_streaming_profiles_include_usage_and_parse_sse(self) -> None:
        output = json.dumps(
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
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            content=_sse(
                {
                    "choices": [
                        {
                            "delta": {"reasoning_content": "not verdict text"},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"content": output[:20]},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"content": output[20:]},
                            "finish_reason": "stop",
                        }
                    ]
                },
                {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
            ),
        )

        [result] = request_verdicts([REQ], seat=2)

        body = json.loads(self._post_calls()[0].request.content)
        self.assertFalse(result.unparsed, result.failure_kind)
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertFalse(body["enable_thinking"])

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
        JUDGE_STREAM_SEAT_2=True,
        JUDGE_RESPONSE_FORMAT_SEAT_2="json_object",
    )
    @http_mock.activate
    def test_streamed_response_id_is_retained_for_attempt_correlation(self) -> None:
        output = json.dumps(
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
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            content=_sse(
                {"id": "chatcmpl-streamed-42", "choices": []},
                {"choices": [{"delta": {"content": output}, "finish_reason": "stop"}]},
            ),
        )

        [result] = request_verdicts([REQ], seat=2, persist_attempts=True)

        self.assertFalse(result.unparsed, result.failure_kind)

        attempt = JudgeRequestAttempt.objects.get(pk=result.request_attempt_id)
        self.assertEqual(attempt.response_id, "chatcmpl-streamed-42")

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
        JUDGE_REASONING_EFFORT_SEAT_1="thinking.disabled",
        JUDGE_REASONING_EFFORT_SEAT_2="enable_thinking=false",
        JUDGE_STREAM_SEAT_1=True,
    )
    @http_mock.activate
    def test_stream_finish_length_is_not_retried(self) -> None:
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            content=_sse(
                {
                    "choices": [
                        {
                            "delta": {"content": "{}"},
                            "finish_reason": "length",
                        }
                    ]
                }
            ),
        )

        [result] = request_verdicts([REQ], seat=1)

        self.assertEqual(result.failure_kind, "finish-length")
        self.assertEqual(len(self._post_calls()), 1)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
        JUDGE_REASONING_EFFORT_SEAT_1="thinking.disabled",
        JUDGE_REASONING_EFFORT_SEAT_2="enable_thinking=false",
        JUDGE_STREAM_SEAT_1=True,
        JUDGE_PROTOCOL_RETRIES=0,
    )
    @http_mock.activate
    def test_stream_without_done_is_an_invalid_envelope(self) -> None:
        event = {
            "choices": [
                {
                    "delta": {
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
                    "finish_reason": "stop",
                }
            ]
        }
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            content=f"data: {json.dumps(event)}\n\n",
        )

        [result] = request_verdicts([REQ], seat=1)

        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "invalid-envelope")

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
        JUDGE_REASONING_EFFORT_SEAT_1="thinking.disabled",
        JUDGE_REASONING_EFFORT_SEAT_2="enable_thinking=false",
    )
    def test_profile_resolution_includes_atlas_qwen(self) -> None:
        first, second = resolve_judge_seat_profile(1), resolve_judge_seat_profile(2)
        self.assertEqual(first.model, "weblate-judge-deepseek-v4-pro")
        self.assertEqual(second.model, "atlas/qwen3.8-max")

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
        JUDGE_MODEL_SEAT_1="weblate-judge-deepseek-v4-pro",
        JUDGE_MODEL_SEAT_2="atlas/qwen3.8-max",
        JUDGE_REASONING_EFFORT="enable_thinking=false",
        JUDGE_RESPONSE_FORMAT="json_object",
        JUDGE_STREAM=True,
        JUDGE_BATCH_SIZE=3,
        JUDGE_TEMPERATURE=0.25,
        JUDGE_MAX_TOKENS=123,
    )
    def test_per_seat_inherit_uses_the_global_profile_values(self) -> None:
        profile = resolve_judge_seat_profile(2)

        self.assertEqual(profile.model, "atlas/qwen3.8-max")
        self.assertEqual(profile.reasoning, "enable_thinking=false")
        self.assertEqual(profile.response_format, "json_object")
        self.assertTrue(profile.stream)
        self.assertEqual(profile.batch_size, 3)
        self.assertEqual(profile.temperature, 0.25)
        self.assertEqual(profile.max_tokens, 123)

    @override_settings(
        JUDGE_BASE_URL="https://openrouter.ai/api/v1",
        JUDGE_MODEL_SEAT_1="vendor/model-a",
        JUDGE_MODEL_SEAT_2="vendor/model-b",
        JUDGE_REQUEST_DEADLINE=120,
        JUDGE_REQUEST_DEADLINE_SEAT_1="inherit",
        JUDGE_REQUEST_DEADLINE_SEAT_2="300",
    )
    def test_per_seat_deadlines_resolve_without_invalidating_cache(self) -> None:
        first, second = judge_seat_profiles()

        self.assertEqual(first.request_deadline, 120)
        self.assertEqual(second.request_deadline, 300)
        self.assertEqual(
            judge_configuration_snapshot()["request_deadline"],
            [120, 300],
        )
        self.assertEqual(_request_timeout(first).connect, 120)
        self.assertEqual(_request_timeout(second).connect, 300)

        with override_settings(JUDGE_REQUEST_DEADLINE_SEAT_2="240"):
            changed = resolve_judge_seat_profile(2)
        self.assertEqual(second.profile_fingerprint, changed.profile_fingerprint)

    @override_settings(
        JUDGE_BASE_URL="https://hcbifrost.herocraft.com/litellm/v1",
    )
    @http_mock.activate
    def test_litellm_reasoning_is_absent_by_default(self) -> None:
        http_mock.register(
            "POST",
            LITELLM_CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )

        request_verdicts([REQ], model="deepseek-v4-pro")

        body = json.loads(self._post_calls()[0].request.content)
        self.assertNotIn("enable_thinking", body)
        self.assertNotIn("thinking", body)
        self.assertNotIn("reasoning", body)

    @override_settings(JUDGE_REASONING_EFFORT="none")
    @http_mock.activate
    def test_openrouter_none_keeps_generic_reasoning_payload(self) -> None:
        http_mock.register(
            "POST",
            CHAT_URL,
            json=_reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            ),
        )

        request_verdicts([REQ], model="vendor/model-a")

        body = json.loads(http_mock.calls[0].request.content)
        self.assertEqual(body["reasoning"], {"effort": "none", "exclude": True})
        self.assertNotIn("enable_thinking", body)
        self.assertNotIn("thinking", body)


class JudgeHttpFailureMappingTest(SimpleTestCase):
    """The whole safety argument for refusals lives in this table."""

    REQUEST_INVALID = {400, 404, 405, 406, 415, 422}
    SIZE_DEPENDENT = {413, 431}

    def test_failure_for_http_table(self) -> None:
        for status in sorted(self.REQUEST_INVALID):
            with self.subTest(status=status):
                self.assertEqual(_failure_for_http(status), "http-request-invalid")
        for status in sorted(self.SIZE_DEPENDENT):
            with self.subTest(status=status):
                self.assertEqual(_failure_for_http(status), "http-other")
        # Unclassified 4xx keep today's behaviour.
        for status in (402, 409, 418, 451):
            with self.subTest(status=status):
                self.assertEqual(_failure_for_http(status), "http-other")
        # Unchanged kinds.
        self.assertEqual(_failure_for_http(401), "http-auth")
        self.assertEqual(_failure_for_http(403), "http-auth")
        self.assertEqual(_failure_for_http(429), "http-rate-limit")
        for status in (500, 502, 503):
            with self.subTest(status=status):
                self.assertEqual(_failure_for_http(status), "http-server")
        self.assertEqual(_failure_for_http(None), "")
        self.assertEqual(_failure_for_http(200), "")

    def test_request_invalid_is_a_declared_kind(self) -> None:
        self.assertIn("http-request-invalid", FAILURE_KINDS)
        self.assertEqual(
            JudgeRequestAttempt.FailureKind.HTTP_REQUEST_INVALID,
            "http-request-invalid",
        )

    def test_request_invalid_is_not_an_availability_failure(self) -> None:
        # A rejected request is not evidence about endpoint health and must
        # not open the shared circuit.
        self.assertNotIn("http-request-invalid", _AVAILABILITY_FAILURE_KINDS)
        self.assertIn("http-other", _AVAILABILITY_FAILURE_KINDS)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test-do-not-leak",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_BATCH_SIZE=5,
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeRefusedRequestTest(TestCase):
    """A refused request must never become a verdict or a paid retry."""

    @http_mock.activate
    def test_refused_request_raises_before_any_retry(self) -> None:
        http_mock.register("POST", CHAT_URL, status_code=400, json={})
        on_batch = mock.Mock()
        with self.assertRaises(JudgeError) as ctx:
            request_verdicts(
                [REQ],
                model="vendor/model-a",
                persist_attempts=True,
                on_batch=on_batch,
            )
        self.assertEqual(len(http_mock.calls), 1)
        on_batch.assert_not_called()
        message = str(ctx.exception)
        self.assertIn("refused the request (HTTP 400)", message)
        self.assertNotIn("sk-test-do-not-leak", message)
        self.assertNotIn("openrouter.ai", message)
        self.assertNotIn("model-a", message)

    @http_mock.activate
    def test_refusal_without_persistence_still_aborts(self) -> None:
        # The abort is not a persistence side effect: a caller that asked for
        # no ledger row must still not receive an unparsed opinion.
        http_mock.register("POST", CHAT_URL, status_code=404, json={})
        with self.assertRaises(JudgeError) as ctx:
            request_verdicts([REQ], model="vendor/model-a")
        self.assertIn("HTTP 404", str(ctx.exception))
        self.assertEqual(len(http_mock.calls), 1)

    @http_mock.activate
    def test_refused_attempt_row_survives_the_abort(self) -> None:
        http_mock.register("POST", CHAT_URL, status_code=422, json={})
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a", persist_attempts=True)
        attempt = JudgeRequestAttempt.objects.get()
        self.assertEqual(attempt.failure_kind, "http-request-invalid")
        self.assertFalse(attempt.parsed)
        self.assertFalse(attempt.transport_succeeded)
        self.assertEqual(attempt.http_status, 422)

    @http_mock.activate
    def test_size_dependent_413_is_not_a_refusal(self) -> None:
        # 413 stays http-other: adaptive halving must still get its chance.
        http_mock.register("POST", CHAT_URL, status_code=413, json={})
        [result] = request_verdicts([REQ], model="vendor/model-a")
        self.assertTrue(result.unparsed)
        self.assertEqual(result.failure_kind, "http-other")
