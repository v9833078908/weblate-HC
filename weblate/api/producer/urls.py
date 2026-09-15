# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""URLs for the producer console API."""

from django.urls import path

from weblate.api.producer.views import ProducerMe, ProducerProjects

urlpatterns = [
    path("me/", ProducerMe.as_view(), name="producer-me"),
    path("projects/", ProducerProjects.as_view(), name="producer-projects"),
]
