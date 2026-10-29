# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified financial capabilities, independent of caching and HTTP request IDs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderRecoveryCapabilities:
    billing_idempotency: bool = False
    complete_response_lookup: bool = False
    safe_paid_replay: bool = False


def provider_recovery_capabilities(route: str) -> ProviderRecoveryCapabilities:
    """
    Fail closed until a route's billing and full-body guarantees are verified.

    A cache can miss; a generation ID or cost lookup is not the complete answer.
    Neither currently configured route has a verified recoverable-body contract.
    """
    return ProviderRecoveryCapabilities()
