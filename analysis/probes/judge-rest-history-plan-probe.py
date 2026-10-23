# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local REST-history experiment; no product edits or real provider calls."""

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from weblate.api import tests as api_tests
from weblate.auth.models import Group, setup_project_groups
from weblate.configuration.models import Setting, SettingCategory
from weblate.trans.autotranslate import BatchAutoTranslate
from weblate.trans.judge import validate_judge_configuration
from weblate.trans.models.judge import (
    JudgeRequestAttempt,
    JudgeRunUnit,
    JudgeVerdict,
    ProducerRun,
)
from weblate.trans.tests.utils import RepoTestMixin, create_test_user
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED
from weblate.utils.tests import http_mock

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def batch_candidate(*, translation, **kwargs):
    """Replace only the endpoint's constructor binding inside the test process."""
    return BatchAutoTranslate(translation, **kwargs)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test-no-real-provider",
    JUDGE_BASE_URL="https://openrouter.ai/api/v1",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_BATCH_SIZE_SEAT_1=1,
    JUDGE_BATCH_SIZE_SEAT_2=1,
    JUDGE_STREAM_SEAT_1=False,
    JUDGE_STREAM_SEAT_2=False,
    JUDGE_REQUEST_SLEEP=0.0,
    JUDGE_MAX_REPAIR_ATTEMPTS=0,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
    JUDGE_TRANSIENT_HTTP_RETRIES=0,
    JUDGE_TRANSPORT_RETRIES=0,
    JUDGE_PROTOCOL_RETRIES=0,
    JUDGE_FALLBACK_BASE_URL="",
    JUDGE_FALLBACK_API_KEY="",
    JUDGE_FALLBACK_MODEL_SEAT_1="",
    JUDGE_FALLBACK_MODEL_SEAT_2="",
    JUDGE_MAY_APPROVE=False,
)
class RESTHistoryProbe(RepoTestMixin, TransactionTestCase):
    authenticate = api_tests.APIBaseTest.authenticate
    do_request = api_tests.APIBaseTest.do_request

    def setUp(self):
        validate_judge_configuration()
        self.clone_test_repos()
        super().setUp()
        self.component = self.create_component()
        self.component.create_path()
        self.project = self.component.project
        self.project.translation_review = True
        self.project.save(update_fields=["translation_review"])
        setup_project_groups(self, self.project)
        self.translation = self.component.translation_set.get(language_code="cs")
        self.user = create_test_user()
        self.user.groups.add(Group.objects.get(name="Users"))
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.client = APIClient()
        self.translation_kwargs = {
            "language__code": "cs",
            "component__slug": self.component.slug,
            "component__project__slug": self.project.slug,
        }
        self.unit = self.translation.unit_set.get(source="Hello, world!\n")
        self.unit.translate(self.user, ["Ahoj světe!\n"], STATE_TRANSLATED)

    def post_judge(self, *, candidate, q=None, superuser=True, code=200):
        substitute = (
            patch("weblate.api.views.AutoTranslate", batch_candidate)
            if candidate
            else nullcontext()
        )
        with substitute:
            return self.do_request(
                "api:translation-autotranslate",
                self.translation_kwargs,
                method="post",
                format="json",
                superuser=superuser,
                code=code,
                request={
                    "mode": "judge",
                    "q": q or f"id:{self.unit.pk}",
                    "auto_source": "others",
                    "threshold": 80,
                },
            )

    def serve_pass(self):
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
                                            "back_translation": "Hello, world!",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    def evidence(self, name, response):
        self.unit.refresh_from_db()
        print(
            "PROBE:"
            + json.dumps(
                {
                    "scenario": name,
                    "http_status": response.status_code,
                    "response": response.data,
                    "runs": list(
                        ProducerRun.objects.values(
                            "id",
                            "status",
                            "actor_id",
                            "scope_type",
                            "scope_id",
                            "requested_query",
                        )
                    ),
                    "participation": list(
                        JudgeRunUnit.objects.values(
                            "run_id", "unit_id_snapshot", "outcome", "cached"
                        )
                    ),
                    "attempts": list(
                        JudgeRequestAttempt.objects.values(
                            "run_id", "seat", "http_status", "failure_kind"
                        )
                    ),
                    "verdicts": list(
                        JudgeVerdict.objects.values("run_id", "seat", "model_verdict")
                    ),
                    "target": self.unit.target,
                    "state": self.unit.state,
                    "mock_http_calls": len(http_mock.calls),
                },
                default=str,
                ensure_ascii=False,
            )
        )

    @http_mock.activate
    def test_baseline_reproduces_orphan_evidence(self):
        self.serve_pass()
        response = self.post_judge(candidate=False)
        self.evidence("baseline", response)
        self.assertEqual(JudgeVerdict.objects.count(), 2)
        self.assertEqual(
            JudgeRequestAttempt.objects.filter(run__isnull=True).count(), 2
        )
        self.assertEqual(ProducerRun.objects.count(), 0)
        self.assertEqual(JudgeRunUnit.objects.count(), 0)

    @http_mock.activate
    def test_candidate_fresh_and_cached_history(self):
        self.serve_pass()
        response = self.post_judge(candidate=True)
        self.evidence("candidate-fresh", response)
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.COMPLETED)
        self.assertEqual(run.actor_id, self.user.pk)
        self.assertEqual(run.scope_id, str(self.translation.pk))
        self.assertEqual(run.requested_query, f"id:{self.unit.pk}")
        self.assertEqual(JudgeRunUnit.objects.get().unit_id, self.unit.pk)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=run).count(), 2)
        self.assertEqual(JudgeVerdict.objects.filter(run_id=run.pk).count(), 2)
        call_count = len(http_mock.calls)
        response = self.post_judge(candidate=True)
        self.evidence("candidate-cached", response)
        self.assertEqual(ProducerRun.objects.count(), 2)
        self.assertEqual(JudgeRunUnit.objects.count(), 2)
        self.assertEqual(JudgeRunUnit.objects.filter(cached=True).count(), 1)
        self.assertEqual(len(http_mock.calls), call_count)
        self.assertEqual(JudgeRequestAttempt.objects.filter(run=run).count(), 2)

    @http_mock.activate
    def test_candidate_denies_unauthorized_actor(self):
        response = self.post_judge(candidate=True, superuser=False, code=403)
        self.evidence("candidate-denied", response)
        self.assertEqual(ProducerRun.objects.count(), 0)
        self.assertEqual(len(http_mock.calls), 0)

    @http_mock.activate
    def test_candidate_excludes_other_language_unit(self):
        foreign_id = self.component.source_translation.unit_set.first().pk
        response = self.post_judge(candidate=True, q=f"id:{foreign_id}")
        self.evidence("candidate-other-language", response)
        self.assertEqual(JudgeRunUnit.objects.count(), 0)
        self.assertEqual(len(http_mock.calls), 0)

    @http_mock.activate
    def test_candidate_records_http_refusal(self):
        http_mock.register("POST", CHAT_URL, status_code=400, json={})
        response = self.post_judge(candidate=True)
        self.evidence("candidate-http-refusal", response)
        run = ProducerRun.objects.get()
        self.assertEqual(run.status, ProducerRun.Status.FAILED)
        self.assertTrue(run.failure)
        self.assertEqual(JudgeVerdict.objects.count(), 0)
        self.assertFalse(JudgeRequestAttempt.objects.filter(run__isnull=True).exists())

    @override_settings(JUDGE_MAX_UNITS_PER_RUN=0)
    @http_mock.activate
    def test_candidate_records_cap_skip(self):
        response = self.post_judge(candidate=True)
        self.evidence("candidate-zero-cap", response)
        row = JudgeRunUnit.objects.get()
        self.assertEqual(row.skip_reason, JudgeRunUnit.SkipReason.CAP)
        self.assertEqual(len(http_mock.calls), 0)

    @http_mock.activate
    def test_candidate_preserves_approved_target(self):
        self.serve_pass()
        observed_states = []
        for candidate in (False, True):
            self.unit.translate(self.user, ["Ahoj světe!\n"], STATE_APPROVED)
            self.unit.refresh_from_db()
            self.assertEqual(self.unit.state, STATE_APPROVED)
            before_target = self.unit.target
            response = self.post_judge(candidate=candidate)
            self.evidence(
                "candidate-approved" if candidate else "baseline-approved", response
            )
            self.assertEqual(self.unit.target, before_target)
            observed_states.append(self.unit.state)
        # This measures compatibility, not preservation of approval:
        # both paths currently project a pass back to state 20.
        self.assertEqual(observed_states[0], observed_states[1])

    def test_candidate_existing_api_contract(self):
        Setting.objects.update_or_create(
            category=SettingCategory.MT, name="weblate", defaults={"value": {}}
        )
        with patch("weblate.api.views.AutoTranslate", batch_candidate):
            api_tests.TranslationAPITest.test_autotranslate(self, format="json")
        print(
            "PROBE:"
            + json.dumps(
                {"scenario": "candidate-existing-api-contract", "result": "passed"}
            )
        )
