# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

import pytest
from app.db import DomainError, Store, revision
from app.routes.runs import QualityPolicy
from app.services.quality_cycle import advance_quality_cycle
from app.services.quality_state import create_quality_cycle, update_quality_cycle
from test_full_quality_cycle import FullRemote


class VerificationRemote(FullRemote):
    def quality_snapshot(self, project, language, scope):
        return {
            **super().quality_snapshot(project, language, scope),
            "protected_content_digest": revision(self.revisions),
        }

    def producer_me(self):
        result = super().producer_me()
        result["capabilities"].update(quality_execution_policy=1, verify_only_immutable=1)
        return result

    def judge_estimate(self, project, scope, *, execution_mode="repair"):
        assert execution_mode == "verify_only"
        return {**super().judge_estimate(project, scope), "execution_mode": execution_mode}

    def start_quality_judge(self, project, estimate, scope, key, *, execution_mode="repair"):
        assert execution_mode == "verify_only"
        return super().start_quality_judge(project, estimate, scope, key)

    def apply_quality_candidates(self, *args, **kwargs):
        pytest.fail("Verification must not apply candidates")

    def apply_repeat_plan(self, *args, **kwargs):
        pytest.fail("Verification must not propagate repeats")

    def recheck_full_quality(self, *args, **kwargs):
        pytest.fail("Verification has no changed recipients")


def test_1662_verification_preserves_all_revisions_and_skips_mutations(tmp_path):
    store = Store(tmp_path)
    remote = VerificationRemote(count=1662)
    before = copy.deepcopy(remote.revisions)
    scope = {"unit_ids": list(range(1, 1663))}
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote",
        {
            "revision": "verify-v1",
            "workflow_version": 2,
            "execution_mode": "verify_only",
            "max_repair_rounds": 0,
            "require_no_mt": True,
        },
    )
    for _ in range(15):
        store.engine.dispose()
        store = Store(tmp_path)
        cycle = advance_quality_cycle(
            store, "owner", cycle["id"], remote, scope=scope, language="pl"
        )
        if cycle["phase"] == "completed":
            break
        update_quality_cycle(store, cycle["id"], next_wakeup=0)
    assert cycle["phase"] == "completed"
    assert remote.revisions == before
    assert len(remote.starts) == 1
    assert cycle["counts"]["automatically_corrected"] == 0


def test_verify_policy_allows_zero_repair_rounds():
    policy = QualityPolicy(execution_mode="verify_only", max_repair_rounds=0)
    assert policy.execution_mode == "verify_only"


def test_old_server_fails_before_judge_start(tmp_path):
    from app.services.quality_preflight import run_quality_preflight

    remote = FullRemote()
    with pytest.raises(DomainError, match="verification"):
        run_quality_preflight(remote, "remote", {"unit_ids": [1, 2]}, execution_mode="verify_only")
    assert remote.starts == []


def test_partial_mt_quality_scope_keeps_full_coverage_and_excludes_empty_targets():
    from app.services.quality_preflight import filled_quality_scope

    selected, excluded = filled_quality_scope(
        [
            {"id": 1, "target": ["Ready"]},
            {"id": 2, "target": [""]},
            {"id": 3, "target": ["one", ""]},
            {"id": 4, "target": ["Human"]},
        ]
    )
    assert selected == {"unit_ids": [1, 4]}
    assert excluded == [2, 3]


def test_verify_export_rejects_external_content_change_without_undo(tmp_path):
    from app.services.quality_export import download_stable_quality_export

    remote = VerificationRemote()
    store = Store(tmp_path)
    scope = {"unit_ids": [1, 2]}
    cycle = create_quality_cycle(
        store,
        "owner",
        "job",
        "remote",
        {
            "revision": "verify-v1",
            "workflow_version": 2,
            "execution_mode": "verify_only",
            "max_repair_rounds": 0,
            "require_no_mt": True,
        },
    )
    for _ in range(12):
        cycle = advance_quality_cycle(
            store, "owner", cycle["id"], remote, scope=scope, language="pl"
        )
        if cycle["phase"] == "completed":
            break
    remote.revisions["1"] = "external-human-change"
    with pytest.raises(DomainError, match="protected"):
        download_stable_quality_export(
            store, "owner", cycle["id"], remote, lambda: {"file": "safe"}
        )
    assert remote.revisions["1"] == "external-human-change"
