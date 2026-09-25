# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from decimal import Decimal
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from lxml import html

from weblate.trans.autotranslate import BatchAutoTranslate, PreparationScope
from weblate.trans.forms import AutoForm
from weblate.trans.judge_loop import DEFAULT_CANDIDATE_SEVERITIES
from weblate.trans.models.judge import ProducerRun
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.tasks import _producer_run_dispatch_kwargs
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.stats import ProjectLanguage


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeAutoFormTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        # unit.review is denied project-wide unless review is enabled.
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def modes(self, user):
        return [
            choice[0]
            for choice in AutoForm(obj=self.component, user=user).fields["mode"].choices
        ]

    def test_judge_mode_requires_review_permission(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        self.assertIn("judge", self.modes(self.user))

    def test_judge_mode_hidden_without_review_permission(self) -> None:
        self.user.is_superuser = False
        self.user.save()
        self.assertNotIn("judge", self.modes(self.user))

    @override_settings(JUDGE_MODEL_SEAT_2="")
    def test_judge_mode_hidden_when_one_seat_is_not_configured(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        self.assertNotIn("judge", self.modes(self.user))

    @override_settings(JUDGE_ENABLED=False)
    def test_judge_mode_hidden_when_judge_is_disabled(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        self.assertNotIn("judge", self.modes(self.user))

    def test_overwrite_checkbox_defaults_to_off(self) -> None:
        form = AutoForm(obj=self.component, user=self.user)
        self.assertFalse(form.fields["overwrite_existing"].initial)
        self.assertFalse(form.fields["overwrite_existing"].required)

    def test_overwrite_is_rejected_outside_judge_mode(self) -> None:
        # Q9: the checkbox is meaningless for translate/fuzzy/approved.
        self.user.is_superuser = True
        self.user.save()
        form = AutoForm(
            obj=self.component,
            user=self.user,
            data={
                "mode": "translate",
                "auto_source": "others",
                "engines": [],
                "threshold": 80,
                "q": "state:empty",
                "overwrite_existing": True,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("overwrite_existing", form.errors)

    def test_overwrite_is_accepted_in_judge_mode(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        form = AutoForm(
            obj=self.component,
            user=self.user,
            data={
                "mode": "judge",
                "auto_source": "others",
                "engines": [],
                "threshold": 80,
                "q": "state:empty",
                "overwrite_existing": True,
            },
        )
        self.assertTrue(form.is_valid())

    def test_judge_with_mt_and_no_engine_is_accepted(self) -> None:
        # Task 3: a judge scope that is already complete must not be forced
        # to select an engine it will never use; the preparation barrier
        # covers the missing-string case at execution with an explicit
        # per-language blocker.
        self.user.is_superuser = True
        self.user.save()
        form = AutoForm(
            obj=self.component,
            user=self.user,
            data={
                "mode": "judge",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
                "q": "state:empty",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_judge_accepts_when_a_routed_engine_is_configured(self) -> None:
        self.user.is_superuser = True
        self.user.save()
        self.component.project.machinery_settings = {"openrouter": {"key": "test"}}
        self.component.project.save(update_fields=["machinery_settings"])
        form = AutoForm(
            obj=self.component,
            user=self.user,
            data={
                "mode": "judge",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
                "q": "state:empty",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_standalone_mt_without_engine_is_still_rejected(self) -> None:
        # The silent zero-write launch stays refused outside judge mode.
        form = AutoForm(
            obj=self.component,
            user=self.user,
            data={
                "mode": "translate",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
                "q": "state:empty",
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("engines", form.errors)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class VerdictOnlyJudgeLaunchTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.project.translation_review = True
        self.project.save(update_fields=["translation_review"])
        self.project_language = ProjectLanguage(
            self.project, self.get_translation().language
        )
        self.queue_url = reverse(
            "repeat-queue",
            kwargs={
                "project": self.project.slug,
                "language": self.project_language.language.code,
            },
        )

    def test_project_language_link_prefills_verdict_only_launch(self) -> None:
        response = self.client.get(
            self.project_language.get_absolute_url(),
            {
                "mode": "judge",
                "q": "check:repeat-drift",
                "judge_proposal_only": "1",
                "next": self.queue_url,
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["autoform"]
        self.assertEqual(form.initial["mode"], "judge")
        self.assertEqual(form.initial["q"], "check:repeat-drift")
        page = html.fromstring(response.content)
        self.assertEqual(
            page.xpath("//input[@name='judge_proposal_only']/@type"), ["hidden"]
        )
        self.assertEqual(
            page.xpath("//input[@name='judge_proposal_only']/@value"), ["1"]
        )
        self.assertEqual(page.xpath("//input[@name='next']/@value"), [self.queue_url])
        self.assertContains(response, "The judge checks the matching strings")
        self.assertEqual(page.xpath("//input[@name='mode']/@value"), ["judge"])
        self.assertEqual(page.xpath("//input[@name='mode']/@type"), ["hidden"])
        self.assertEqual(page.xpath("//input[@name='auto_source']/@value"), ["others"])
        self.assertEqual(page.xpath("//input[@name='threshold']/@type"), ["hidden"])
        self.assertFalse(page.xpath("//select[@name='mode']"))
        self.assertFalse(page.xpath("//*[@name='engines']"))
        self.assertFalse(page.xpath("//*[@name='overwrite_existing']"))
        self.assertNotContains(response, "translating all strings will")
        self.assertNotContains(response, "Automatic translation takes existing")
        self.assertEqual(
            page.xpath("//input[@id='id_auto_apply']/@value"), ["Run judge check"]
        )

    def test_regular_launch_keeps_translation_controls(self) -> None:
        response = self.client.get(self.project_language.get_absolute_url())
        page = html.fromstring(response.content)
        self.assertEqual(page.xpath("//select[@name='mode']/@name"), ["mode"])
        self.assertTrue(page.xpath("//*[@name='auto_source']"))
        self.assertTrue(page.xpath("//*[@name='overwrite_existing']"))
        self.assertContains(response, "Automatic translation takes existing")
        self.assertEqual(page.xpath("//input[@id='id_auto_apply']/@value"), ["Apply"])

    def test_verdict_only_rejects_write_mode_and_overwrite_tampering(self) -> None:
        for changes, error_field in (
            ({"mode": "translate"}, "mode"),
            ({"overwrite_existing": "on"}, "overwrite_existing"),
        ):
            with self.subTest(changes=changes):
                form = AutoForm(
                    obj=self.project,
                    user=self.user,
                    data={
                        "mode": "judge",
                        "q": "check:repeat-drift",
                        "auto_source": "others",
                        "threshold": 80,
                        "judge_proposal_only": "1",
                        **changes,
                    },
                )
                self.assertFalse(form.is_valid())
                self.assertIn(error_field, form.errors)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_verdict_only_launch_skips_preparation_and_repair_candidates(self) -> None:
        unit = self.get_unit()
        preparation = PreparationScope(
            unit_ids=(unit.pk,),
            missing_ids=(),
            per_language_missing={},
            mt_engine="openrouter",
        )
        with (
            mock.patch.object(
                BatchAutoTranslate,
                "preview_judge_scope_snapshot",
                return_value=(None, [unit]),
            ),
            mock.patch.object(
                BatchAutoTranslate,
                "build_preparation_scope",
                return_value=preparation,
            ) as prepare,
            mock.patch("weblate.trans.views.edit.get_queue_length", return_value=0),
            mock.patch("weblate.trans.views.edit.publish_producer_run_dispatch"),
        ):
            response = self.client.post(
                reverse(
                    "auto_translation",
                    kwargs={"path": self.project_language.get_url_path()},
                ),
                {
                    "mode": "judge",
                    "q": "check:repeat-drift",
                    "auto_source": "mt",
                    "engines": [],
                    "threshold": 80,
                    "judge_proposal_only": "1",
                    "next": self.queue_url,
                },
            )

        self.assertRedirects(response, self.queue_url)
        prepare.assert_not_called()
        run = ProducerRun.objects.get()
        self.assertEqual(run.scope_type, ProducerRun.ScopeType.PROJECT)
        self.assertEqual(run.scope_path, self.project_language.get_absolute_url())
        self.assertEqual(run.requested_query, "check:repeat-drift")
        self.assertIs(run.execution_options["judge_proposal_only"], True)
        self.assertEqual(run.execution_options["judge_candidate_severities"], [])
        self.assertEqual(run.preparation_snapshot, {})
        self.assertEqual(run.preparation_phase, "")
        dispatch = _producer_run_dispatch_kwargs(run)
        self.assertIs(dispatch["judge_proposal_only"], True)
        self.assertEqual(dispatch["judge_candidate_severities"], ())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_regular_judge_launch_preserves_preparation_and_repair_candidates(
        self,
    ) -> None:
        unit = self.get_unit()
        preparation = PreparationScope(
            unit_ids=(unit.pk,),
            missing_ids=(),
            per_language_missing={},
            mt_engine="openrouter",
        )
        with (
            mock.patch.object(
                BatchAutoTranslate,
                "preview_judge_scope_snapshot",
                return_value=(None, [unit]),
            ),
            mock.patch.object(
                BatchAutoTranslate,
                "build_preparation_scope",
                return_value=preparation,
            ) as prepare,
            mock.patch("weblate.trans.views.edit.get_queue_length", return_value=0),
            mock.patch("weblate.trans.views.edit.publish_producer_run_dispatch"),
        ):
            response = self.client.post(
                reverse(
                    "auto_translation",
                    kwargs={"path": self.project_language.get_url_path()},
                ),
                {
                    "mode": "judge",
                    "q": "check:repeat-drift",
                    "auto_source": "mt",
                    "engines": [],
                    "threshold": 80,
                    "next": self.queue_url,
                },
            )

        self.assertRedirects(response, self.queue_url)
        prepare.assert_called_once_with()
        run = ProducerRun.objects.get()
        self.assertIs(run.execution_options["judge_proposal_only"], False)
        self.assertEqual(
            run.execution_options["judge_candidate_severities"],
            list(DEFAULT_CANDIDATE_SEVERITIES),
        )
        self.assertEqual(run.preparation_snapshot, preparation.to_json())
        self.assertEqual(run.preparation_phase, "pending")

    @override_settings(JUDGE_MAX_REPAIR_ATTEMPTS=2)
    def test_verdict_only_preview_prices_initial_judgement_only(self) -> None:
        for model, cost in (("vendor-a/model", "0.01"), ("vendor-b/model", "0.02")):
            for _ in range(5):
                LLMUsageLog.objects.create(
                    model=model,
                    service="openrouter",
                    project_id_snapshot=self.project.pk,
                    project_slug=self.project.slug,
                    operation=LLMUsageLog.Operation.JUDGE,
                    cost_usd=cost,
                    unit_count=1,
                )
        url = reverse(
            "auto_translation_preview", kwargs={"path": self.translation.get_url_path()}
        )
        params = {
            "mode": "judge",
            "q": "state:empty",
            "auto_source": "mt",
            "engines": [],
            "threshold": 80,
        }
        verdict_only = self.client.get(url, {**params, "judge_proposal_only": "1"})
        regular = self.client.get(url, params)

        self.assertEqual(verdict_only.status_code, 200)
        self.assertEqual(regular.status_code, 200)
        self.assertIsNone(verdict_only.json()["preparation"])
        self.assertIsNotNone(regular.json()["preparation"])
        self.assertTrue(verdict_only.json()["judge_cost"]["available"])
        processed = verdict_only.json()["processed"]
        self.assertGreater(processed, 0)
        self.assertEqual(
            Decimal(verdict_only.json()["judge_cost"]["max"]),
            Decimal("0.03") * processed,
        )
        self.assertEqual(
            Decimal(regular.json()["judge_cost"]["max"]),
            Decimal("0.03") * processed * 3,
        )
