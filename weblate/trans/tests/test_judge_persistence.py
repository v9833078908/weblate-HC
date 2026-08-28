# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.test import SimpleTestCase

from weblate.trans.models.judge import (
    JudgeRequestAttempt,
    compute_judge_request_identity,
    safe_configuration_snapshot,
)


class JudgePersistenceTest(SimpleTestCase):
    def test_failure_kinds_match_the_closed_transport_contract(self) -> None:
        self.assertEqual(
            list(JudgeRequestAttempt.FailureKind.values),
            [
                "transport",
                "deadline",
                "response-too-large",
                "http-auth",
                "http-rate-limit",
                "http-server",
                "http-other",
                "empty-response",
                "invalid-json",
                "invalid-envelope",
                "segment-count",
                "invalid-segment",
                "finish-length",
                "unknown",
            ],
        )

    def test_configuration_snapshot_rejects_unsafe_data(self) -> None:
        self.assertEqual(
            safe_configuration_snapshot(
                {
                    "provider": "litellm",
                    "endpoint_fingerprint": "a" * 64,
                    "profile_fingerprint": "b" * 64,
                }
            ),
            {
                "provider": "litellm",
                "endpoint_fingerprint": "a" * 64,
                "profile_fingerprint": "b" * 64,
            },
        )
        with self.assertRaisesRegex(ValueError, "api_key"):
            safe_configuration_snapshot({"api_key": "not-safe"})

    def test_request_identity_changes_with_profile_context_and_text_hashes(
        self,
    ) -> None:
        kwargs = {
            "unit_id": 1,
            "target_hash": "a" * 64,
            "context_hash": "b" * 64,
            "project_context_hash": "c" * 64,
            "source_language": "en",
            "target_language": "ru",
            "profile_fingerprint": "d" * 64,
            "prompt_schema_version": "e" * 64,
        }
        identity = compute_judge_request_identity(**kwargs)
        self.assertEqual(identity, compute_judge_request_identity(**kwargs))
        self.assertNotEqual(
            identity,
            compute_judge_request_identity(
                **{**kwargs, "profile_fingerprint": "f" * 64}
            ),
        )
