# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Views for the managed repeat queue."""

from __future__ import annotations

from django.urls import reverse

from weblate.trans.tests.test_views import ViewTestCase


class RepeatQueueViewTest(ViewTestCase):
    """The queue remains project/language scoped even without a policy."""

    def test_queue_requires_project_access_and_renders_empty_scope(self) -> None:
        response = self.client.get(
            reverse(
                "repeat-queue",
                kwargs={"project": self.project.slug, "language": "cs"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Repeat queue")
        self.assertContains(response, "No active repeat rule exists")
