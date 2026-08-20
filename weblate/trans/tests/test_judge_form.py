# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from weblate.trans.forms import AutoForm
from weblate.trans.tests.test_views import ViewTestCase


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
                "auto_source": "mt",
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
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
                "q": "state:empty",
                "overwrite_existing": True,
            },
        )
        self.assertTrue(form.is_valid())
