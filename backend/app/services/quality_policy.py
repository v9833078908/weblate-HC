# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


def two_seats_parsed(results: dict) -> bool:
    return results == {"1": "parsed", "2": "parsed"}


def acceptable_candidate(decision: dict) -> bool:
    return (
        decision.get("applicability") == "ready"
        and decision.get("candidate_verified") is True
        and decision.get("candidate_acceptable") is True
        and two_seats_parsed(decision.get("seat_results", {}))
        and type(decision.get("candidate_id")) is int
        and not decision.get("approval_loss")
        and not decision.get("terminology_conflict")
    )


def required_issue(issue: dict) -> bool:
    return issue.get("hard") is True or issue.get("kind") == "repeat-drift"
