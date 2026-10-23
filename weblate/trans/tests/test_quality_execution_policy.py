# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from contextvars import Context
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from weblate.trans.autotranslate import AutoTranslate, BatchAutoTranslate
from weblate.trans.judge import JUDGE_SEATS, JudgeResult, resolve_judge_seat_profile
from weblate.trans.judge_loop import (
    _cached_verdict,
    build_request,
    judge_project_context,
    repair_targets,
    run_judge_batch,
)
from weblate.trans.judge_quality import current_quality_round
from weblate.trans.models import ProducerRun, Project, Unit
from weblate.trans.models.judge import active_verdicts
from weblate.trans.quality_execution import (
    VerificationChangedError,
    project_digest,
    protected_scope,
    verification_context,
)
from weblate.trans.tests.test_views import ViewTestCase


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="offline-only",
    JUDGE_REQUEST_SLEEP=0.0,
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class QualityExecutionPolicyTest(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.project.translation_review = True
        self.project.save(update_fields=["translation_review"])
        self.unit = self.get_unit()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def verification_run(self):

        self.unit.refresh_from_db()
        return ProducerRun.objects.create(
            actor=self.user,
            scope_type="project",
            scope_id=str(self.project.pk),
            scope_label="Synthetic",
            scope_path=self.project.get_absolute_url(),
            requested_mode="judge",
            cap=1,
            scope_snapshot=[self.unit.pk],
            execution_options={
                "execution_mode": "verify_only",
                "protected_scope": protected_scope([self.unit]),
                "protected_project": project_digest(self.project),
            },
        )

    def test_unit_save_cannot_change_target_state_flags_or_explanation(self):

        for field, value in [
            ("target", "Changed"),
            ("state", 30),
            ("extra_flags", "read-only"),
            ("explanation", "Changed"),
        ]:
            with self.subTest(field=field):
                run = self.verification_run()
                original = getattr(self.unit, field)
                with self.assertRaises(PermissionDenied), verification_context(run):
                    setattr(self.unit, field, value)
                    self.unit.save(update_fields=[field])
                self.unit.refresh_from_db()
                self.assertEqual(getattr(self.unit, field), original)

    def test_bulk_update_cannot_bypass_verification(self):

        run = self.verification_run()
        with self.assertRaises(PermissionDenied), verification_context(run):
            Unit.objects.filter(pk=self.unit.pk).update(target="Forbidden")

    def test_fk_update_cannot_move_unit_out_of_protected_project(self):
        run = self.verification_run()
        project = Project.objects.create(
            name="Other", slug="other", translation_review=True
        )
        other = self.create_po(project=project, name="Other", slug="other")
        target_translation = other.translation_set.get(
            language=self.unit.translation.language
        )
        with self.assertRaises(PermissionDenied), verification_context(run):
            self.unit.translation = target_translation
            self.unit.save(update_fields=["translation"])

    def test_check_metadata_is_allowed_without_invalidating_content_snapshot(self):

        run = self.verification_run()
        original = self.unit.target, self.unit.state, self.unit.last_updated
        with verification_context(run):
            self.unit.run_checks()
        self.unit.refresh_from_db()
        self.assertEqual(
            (self.unit.target, self.unit.state, self.unit.last_updated), original
        )

    def test_external_edit_is_reported_stale_and_never_rolled_back(self):

        run = self.verification_run()
        with self.assertRaises(VerificationChangedError), verification_context(run):
            Context().run(
                lambda: Unit.objects.filter(pk=self.unit.pk).update(
                    target="Human", last_updated=timezone.now()
                )
            )
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Human")

    def test_external_aba_invalidates_old_snapshot(self):

        run = self.verification_run()
        original = self.unit.target
        with self.assertRaises(VerificationChangedError), verification_context(run):
            Context().run(
                lambda: Unit.objects.filter(pk=self.unit.pk).update(
                    target="Temporary", last_updated=timezone.now()
                )
            )
            Context().run(
                lambda: Unit.objects.filter(pk=self.unit.pk).update(
                    target=original, last_updated=timezone.now()
                )
            )

    def test_glossary_aba_invalidates_verification_without_changing_scope_unit(self):
        glossary = self.create_po(project=self.project, name="Terms", slug="terms")
        glossary.is_glossary = True
        glossary.save(update_fields=["is_glossary"])
        term = glossary.translation_set.get(
            language=self.unit.translation.language
        ).unit_set.first()
        run = self.verification_run()
        original = term.target
        with self.assertRaises(VerificationChangedError), verification_context(run):
            for target in ("Temporary", original):
                Context().run(
                    lambda value=target: Unit.objects.filter(pk=term.pk).update(
                        target=value, last_updated=timezone.now()
                    )
                )

    def test_native_caller_flags_cannot_enable_mutating_work(self):

        run = self.verification_run()
        auto = AutoTranslate(
            translation=self.unit.translation,
            user=self.user,
            q="",
            mode="judge",
            producer_run=run,
            judge_pretranslate=True,
            judge_proposal_only=False,
            judge_mutating_repairs=True,
            judge_candidate_severities=("major", "critical"),
        )
        self.assertFalse(auto.judge_pretranslate)
        self.assertFalse(auto.judge_mutating_repairs)
        self.assertTrue(auto.judge_proposal_only)
        self.assertEqual(auto.judge_candidate_severities, ())

    def test_batch_caller_cannot_enable_preparation_or_repairs(self):

        run = self.verification_run()
        batch = BatchAutoTranslate(
            self.project,
            user=self.user,
            q="",
            mode="judge",
            producer_run_id=str(run.pk),
            judge_pretranslate=True,
            judge_mutating_repairs=True,
            judge_proposal_only=False,
            judge_candidate_severities=("major",),
        )
        self.assertFalse(batch.judge_pretranslate)
        self.assertFalse(batch.judge_mutating_repairs)
        self.assertTrue(batch.judge_proposal_only)
        self.assertEqual(batch.judge_candidate_severities, ())
        with self.assertRaises(PermissionDenied), verification_context(run):
            repair_targets([self.unit], self.user, run_id=run.pk)

    def estimate(self):
        Unit.objects.filter(pk=self.unit.pk).update(target="Ready", state=20)
        response = self.api.post(
            f"/api/producer/projects/{self.project.slug}/runs/estimate/",
            {
                "kind": "judge",
                "scope": {"unit_ids": [self.unit.pk]},
                "execution_mode": "verify_only",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def test_estimate_and_run_bind_immutable_execution_mode(self):
        estimated = self.estimate()
        row = ProducerRun.objects.get(pk=estimated.data["estimate_id"])
        self.assertEqual(row.execution_options["execution_mode"], "verify_only")
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            response = self.api.post(
                f"/api/producer/projects/{self.project.slug}/runs/",
                {
                    "kind": "judge",
                    "scope": {"unit_ids": [self.unit.pk]},
                    "estimate_id": str(row.pk),
                    "execution_mode": "verify_only",
                },
                format="json",
            )
        self.assertIn(response.status_code, {200, 201}, response.data)
        run = ProducerRun.objects.get(pk=response.data["id"])
        self.assertEqual(run.execution_options["judge_candidate_severities"], [])
        self.assertTrue(run.execution_options["judge_skip_preparation"])

    def test_verify_estimate_cannot_be_downgraded_to_repair_at_start(self):
        estimate = self.estimate()
        response = self.api.post(
            f"/api/producer/projects/{self.project.slug}/runs/",
            {
                "kind": "judge",
                "scope": {"unit_ids": [self.unit.pk]},
                "estimate_id": estimate.data["estimate_id"],
                "execution_mode": "repair",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_verify_estimate_rejects_empty_targets_without_preparatory_mt(self):
        Unit.objects.filter(pk=self.unit.pk).update(target="", state=0)
        response = self.api.post(
            f"/api/producer/projects/{self.project.slug}/runs/estimate/",
            {
                "kind": "judge",
                "scope": {"unit_ids": [self.unit.pk]},
                "execution_mode": "verify_only",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_forged_apply_request_cannot_mutate_in_verification_context(self):
        run = self.verification_run()
        response = self.api.post(
            f"/api/producer/projects/{self.project.slug}/decisions/apply/",
            {
                "execution_context": str(run.pk),
                "items": [
                    {"unit": self.unit.pk, "revision": "0" * 64, "candidate_id": 99999}
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def change_after_request(self, kind, run):
        if kind == "aba":
            original = self.unit.target
            for target in ("Temporary", original):
                Context().run(
                    lambda value=target: Unit.objects.filter(pk=self.unit.pk).update(
                        target=value, last_updated=timezone.now()
                    )
                )
        elif kind == "cancel":
            ProducerRun.objects.filter(pk=run.pk).update(status="cancel_requested")
        else:
            type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)

    def assert_no_current_evidence(self):
        self.unit.refresh_from_db()
        self.assertEqual(current_quality_round(self.unit), [])
        self.assertIsNone(active_verdicts([self.unit])[self.unit.pk])
        self.assertIsNone(
            _cached_verdict(
                self.unit,
                build_request(self.unit),
                {seat: resolve_judge_seat_profile(seat) for seat in JUDGE_SEATS},
                judge_project_context(self.project),
            )
        )
        response = self.api.post(
            f"/api/producer/projects/{self.project.slug}/quality/snapshot/",
            {
                "language": self.unit.translation.language.code,
                "scope": {"unit_ids": [self.unit.pk]},
            },
            format="json",
        )
        if response.status_code == 200:
            self.assertEqual(response.data["judged_revisions"], {})

    def test_changed_verification_response_is_rejected_before_publication(self):
        for kind in ("aba", "cancel", "permission"):
            with self.subTest(kind=kind):
                self.user.is_active = True
                self.user.save(update_fields=["is_active"])
                run = self.verification_run()

                def respond(jobs, kind=kind, run=run, **kwargs):
                    self.change_after_request(kind, run)
                    for job in jobs:
                        job.persist(
                            job.requests,
                            [
                                JudgeResult("none", "pass", [], "")
                                for request in job.requests
                            ],
                        )

                with (
                    patch("weblate.trans.judge_loop._run_seats", side_effect=respond),
                    self.assertRaises((VerificationChangedError, PermissionDenied)),
                ):
                    run_judge_batch(
                        [self.unit],
                        writable_ids=set(),
                        user=self.user,
                        run=run,
                        use_cache=False,
                    )
                self.assert_no_current_evidence()

    def test_previously_persisted_verification_cannot_revive_after_aba_or_cancel(self):
        for kind in ("aba", "cancel", "permission"):
            with self.subTest(kind=kind):
                self.user.is_active = True
                self.user.save(update_fields=["is_active"])
                run = self.verification_run()

                def respond(jobs, **kwargs):
                    for job in jobs:
                        job.persist(
                            job.requests,
                            [
                                JudgeResult("none", "pass", [], "")
                                for request in job.requests
                            ],
                        )

                with patch("weblate.trans.judge_loop._run_seats", side_effect=respond):
                    run_judge_batch(
                        [self.unit],
                        writable_ids=set(),
                        user=self.user,
                        run=run,
                        use_cache=False,
                    )
                self.change_after_request(kind, run)
                self.assert_no_current_evidence()
