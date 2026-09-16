# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

from rest_framework.throttling import AnonRateThrottle as DRFAnonRateThrottle
from rest_framework.throttling import ScopedRateThrottle as DRFScopedRateThrottle
from rest_framework.throttling import UserRateThrottle as DRFUserRateThrottle

if TYPE_CHECKING:
    from rest_framework.request import Request


def patch_throttle_request(func):
    """Store throttling state in request to be picked up by ThrottlingMiddleware."""

    @wraps(func)
    def patched(self, request: Request, view):
        result = func(self, request, view)
        if hasattr(self, "history"):
            request.META["throttling_state"] = self
        return result

    return patched


class AnonRateThrottle(DRFAnonRateThrottle):
    @patch_throttle_request
    def allow_request(self, request: Request, view):
        return super().allow_request(request, view)


class UserRateThrottle(DRFUserRateThrottle):
    @patch_throttle_request
    def allow_request(self, request: Request, view):
        return super().allow_request(request, view)


class ScopedRateThrottle(DRFScopedRateThrottle):
    """
    Per-endpoint-family rate limit, on top of the general anon/user one.

    Views under ``/api/producer/`` that can reach a paid provider set
    ``throttle_scope = "producer"`` (Task 10, G7): unlike the rest of the
    API, this family spends real money per call and has no interactive UI
    of its own slowing a caller down, so it needs its own ceiling
    independent of the general request-volume budget.
    """

    @patch_throttle_request
    def allow_request(self, request: Request, view):
        return super().allow_request(request, view)
