# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.test import override_settings

from weblate.trans.forms import AutoForm
from weblate.trans.tests.test_views import ViewTestCase


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
