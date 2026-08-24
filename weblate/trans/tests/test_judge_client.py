# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import time
from typing import Any
from unittest import mock

import httpx2

from django.test import SimpleTestCase, TestCase, override_settings

from weblate.trans.judge import (
    JudgeError,
    JudgeRequest,
    render_preview,
    request_verdicts,
)
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.utils.tests import http_mock

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

REQ = JudgeRequest(
    unit_key="MENU_DOOR",
    source="Дверь заблокирована ГЕРМОДВЕРЬМИ",
    target="La porte est bloquée par les PORTES",
    source_language="ru",
    target_language="fr",
    note="",
    glossary_terms=[("ГЕРМОДВЕРЬ", "porte blindée")],
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


def _reply(segments: list[dict]) -> dict:
    content = json.dumps({"segments": segments})
    return {"choices": [{"message": {"content": content}}]}


def _request_segments(body: dict) -> list[dict]:
    content = body["messages"][1]["content"]
    payload = content.split(">\n", 1)[1]
    payload = payload.rsplit("\n</", 1)[0]
    return json.loads(payload)["segments"]


class JudgeClientGateTest(SimpleTestCase):
    @override_settings(JUDGE_ENABLED=False)
    @http_mock.activate
    def test_disabled_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY="")
    @http_mock.activate
    def test_missing_key_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)

    @http_mock.activate
    def test_missing_model_makes_no_network_call(self) -> None:
        with (
            override_settings(JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY="sk-test"),
            self.assertRaises(JudgeError),
        ):
            request_verdicts([REQ], model="")
        self.assertEqual(len(http_mock.calls), 0)

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_OPENROUTER_KEY="sk-test",
        JUDGE_REQUEST_DEADLINE=0,
    )
    @http_mock.activate
    def test_nonpositive_deadline_makes_no_network_call(self) -> None:
        with self.assertRaises(JudgeError):
            request_verdicts([REQ], model="vendor/model-a")
        self.assertEqual(len(http_mock.calls), 0)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test-do-not-leak",
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
        request_verdicts([REQ], model="vendor/model-a")
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
        self.assertEqual(len(http_mock.calls), 1)

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
    def test_the_api_key_never_reaches_the_exception_text(self) -> None:
        # A gate failure raises; the key must not be in the message.
        with override_settings(JUDGE_ENABLED=True, JUDGE_OPENROUTER_KEY=""):
            with self.assertRaises(JudgeError) as ctx:
                request_verdicts([REQ], model="vendor/model-a")
            self.assertNotIn("sk-test", str(ctx.exception))


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_OPENROUTER_KEY="sk-test",
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
    JUDGE_OPENROUTER_KEY="sk-test",
    JUDGE_REQUEST_SLEEP=0.0,
)
class JudgeRequestDeadlineTest(TestCase):
    def _dripping_response(self) -> httpx2.Response:
        body = json.dumps(
            _reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            )
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
            _reply(
                [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
            )
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


class JudgeUsageLogTest(TestCase):
    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_OPENROUTER_KEY="sk-test",
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
        request_verdicts([REQ], model="vendor/model-a")
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.prompt_tokens, 11)
        self.assertEqual(row.completion_tokens, 7)
        self.assertEqual(row.total_tokens, 18)
        self.assertEqual(row.cached_tokens, 2)
        self.assertEqual(row.reasoning_tokens, 1)
        self.assertEqual(row.response_id, "resp-1")

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_OPENROUTER_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
    )
    @http_mock.activate
    def test_usage_is_attributed_to_the_project(self) -> None:
        # The judge is a paid path outside machinery, which records the
        # project of every paid request (machinery/openai.py:147). Without
        # the slug, llm_usage_report cannot bill a judge run to anyone.
        payload = _reply(
            [{"id": 0, "verdict": "pass", "errors": [], "back_translation": ""}]
        )
        payload["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
        http_mock.register("POST", CHAT_URL, json=payload)
        request_verdicts([REQ], model="vendor/model-a", project_slug="need-for-greed")
        self.assertEqual(
            LLMUsageLog.objects.get(model="vendor/model-a").project_slug,
            "need-for-greed",
        )

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_OPENROUTER_KEY="sk-test",
        JUDGE_BATCH_SIZE=5,
        JUDGE_REQUEST_SLEEP=0.0,
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
        request_verdicts([REQ], model="vendor/model-a")
        row = LLMUsageLog.objects.get(model="vendor/model-a")
        self.assertEqual(row.prompt_tokens, 11)
        self.assertEqual(row.completion_tokens, 7)


class JudgeReasoningBudgetTest(TestCase):
    """Reasoning tokens were 84% of the first dev run's cost."""

    @override_settings(
        JUDGE_ENABLED=True,
        JUDGE_OPENROUTER_KEY="sk-test",
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
        JUDGE_OPENROUTER_KEY="sk-test",
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
        JUDGE_OPENROUTER_KEY="sk-test",
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
        JUDGE_OPENROUTER_KEY="sk-test",
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
