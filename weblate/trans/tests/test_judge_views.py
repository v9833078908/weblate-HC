# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.test import override_settings
from django.urls import reverse

from weblate.trans.models.judge import JudgeVerdict, compute_target_hash
from weblate.trans.tests.test_views import ViewTestCase


@override_settings(JUDGE_ENABLED=True, JUDGE_MAX_REPAIR_ATTEMPTS=1)
class JudgeAutoTranslateViewTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def make_reject(self, unit):
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="critical",
            model_verdict="reject",
            judge_model="vendor/model-a",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash="c",
        )
        unit.run_checks()

    def test_judge_mode_requires_review_permission_at_the_view(self) -> None:
        # The view must refuse the mode even when a crafted POST asks
        # for it while the form would hide it (permission gate).
        self.user.is_superuser = False
        self.user.save()
        url = reverse("auto_translation", kwargs=self.kw_translation)
        response = self.client.post(
            url,
            {
                "mode": "judge",
                "q": "state:empty",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
            },
        )
        # Refused at the permission gate: 403, no run started.
        self.assertEqual(response.status_code, 403)
        self.assertEqual(JudgeVerdict.objects.count(), 0)

    def test_form_shows_how_many_strings_a_run_would_touch(self) -> None:
        response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_auto_row_count")

    def test_form_shows_the_honest_request_estimate(self) -> None:
        response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        # Worst case: strings x 2 seats x (1 + repair attempts).
        self.assertContains(response, "id_auto_request_estimate")

    def test_overwrite_warning_shown_when_judge_verdicts_exist(self) -> None:
        self.make_reject(self.get_unit())
        response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "judge-verdicts-exist")
