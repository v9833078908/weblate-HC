# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.test import SimpleTestCase

from weblate.trans.provider_recovery import provider_recovery_capabilities


class ProviderRecoveryTest(SimpleTestCase):
    def test_unverified_route_never_claims_paid_idempotency_or_body_lookup(self):
        for route in ("openrouter", "litellm", "unknown"):
            capabilities = provider_recovery_capabilities(route)
            self.assertFalse(capabilities.billing_idempotency)
            self.assertFalse(capabilities.complete_response_lookup)
            self.assertFalse(capabilities.safe_paid_replay)
