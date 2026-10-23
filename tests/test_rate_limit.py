# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import time
from email.utils import formatdate

import httpx
import pytest
from app.integrations.weblate import WeblateClient, WeblateRejected
from app.models import Job
from app.services.diagnostics import diagnostic_report
from app.services.runs import get_run
from app.settings import Settings
from app.worker import process_once
from test_mt_wait import waiting_run


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "header,expected",
    [("90", 90), ("0", 0), ("-1", None), ("NaN", None), ("invalid", None), (None, None)],
)
def test_rate_limit_preserves_retry_after_without_immediate_replay(
    tmp_path, method, header, expected
):
    token = tmp_path / "token"
    token.write_text("fixture-private-token")
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(429, headers={"Retry-After": header} if header is not None else {})

    client = WeblateClient(Settings(weblate_token_file=str(token)), httpx.MockTransport(handler))
    with pytest.raises(WeblateRejected) as caught:
        client.request(method, "projects/")
    assert getattr(caught.value, "retry_after", "missing") == expected
    assert requests == [method], "429 must be durably deferred, not retried in a tight loop"
    assert "fixture-private-token" not in caught.value.detail


def test_http_date_retry_after_uses_utc_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    response = httpx.Response(429, headers={"Retry-After": formatdate(1090, usegmt=True)})
    assert getattr(WeblateRejected(429, response), "retry_after", None) == 90


def test_polling_429_survives_restart_preserves_progress_and_never_repeats_mt(
    tmp_path, monkeypatch
):
    from app.db import Store

    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch, accepted=True)
    assert process_once(store, settings, remote)
    remote.values["one"] = "Привет"
    clock[0] += 31
    assert process_once(store, settings, remote)
    with store.session() as session:
        before = copy.deepcopy(session.get(Job, run).data["operations"])
    original = remote.download_translation
    reads = []
    refused = [True]

    def download(*args):
        reads.append(clock[0])
        if refused[0]:
            raise WeblateRejected(429, httpx.Response(429, headers={"Retry-After": "90"}))
        return original(*args)

    remote.download_translation = download
    clock[0] += 31
    stamp = clock[0]
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "running"
    with store.session() as session:
        job = session.get(Job, run)
        assert job.data["operations"] == before
        assert job.lease_until == stamp + 90
        assert job.data["rate_limit"]["next_retry_at"] == stamp + 90
        assert job.data["finished"] is None
        report = diagnostic_report(run, job.data)
        assert report["rate_limit"]["next_retry_at"] == stamp + 90
        assert "Привет" not in str(report)
    restarted = Store(tmp_path)
    clock[0] = stamp + 89
    assert not process_once(restarted, settings, remote)
    assert len(reads) == 1
    # Even a premature lease expiry must not bypass the persisted cooldown.
    with store.session() as session:
        session.get(Job, run).lease_until = 0
    assert process_once(restarted, settings, remote)
    assert len(reads) == 1
    clock[0] = stamp + 90
    refused[0] = False
    remote.values.update(two="Пока", three="Играть")
    remote.get_task = lambda *args: {"completed": True}
    assert process_once(restarted, settings, remote)
    assert get_run(store, owner, run)["status"] == "completed"
    assert remote.posts == 1
    assert remote.values["one"] == "Привет"
    with store.session() as session:
        assert not session.get(Job, run).data.get("rate_limit", {}).get("waiting")


def test_missing_retry_after_uses_persisted_increasing_backoff(tmp_path, monkeypatch):
    from app.db import Store

    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch, accepted=True)
    assert process_once(store, settings, remote)

    def refused(*args):
        raise WeblateRejected(429)

    remote.download_translation = refused
    clock[0] += 31
    for attempt, delay in enumerate([5, 10, 20], start=1):
        stamp = clock[0]
        assert process_once(Store(tmp_path), settings, remote)
        assert get_run(store, owner, run)["status"] == "running"
        with store.session() as session:
            data = session.get(Job, run).data
            assert data["rate_limit"]["attempt"] == attempt
            assert data["rate_limit"]["next_retry_at"] == stamp + delay
        clock[0] += delay
    assert remote.posts == 1


def test_definitely_refused_mt_post_is_retried_only_after_cooldown_and_observation(
    tmp_path, monkeypatch
):
    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch, accepted=True)
    original = remote.start_translation
    refused_posts = []

    def start(*args):
        if not refused_posts:
            refused_posts.append(clock[0])
            raise WeblateRejected(429, httpx.Response(429, headers={"Retry-After": "60"}))
        return original(*args)

    remote.start_translation = start
    stamp = clock[0]
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "running"
    assert remote.posts == 0
    clock[0] = stamp + 59
    assert not process_once(store, settings, remote)
    clock[0] = stamp + 60
    assert process_once(store, settings, remote)
    assert remote.posts == 1
    clock[0] += 31
    remote.values.update(one="Привет", two="Пока", three="Играть")
    remote.get_task = lambda *args: {"completed": True}
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "completed"
    assert remote.posts == 1, "An accepted POST must never be replayed"


def test_cancel_during_long_cooldown_does_not_wait_or_contact_weblate(tmp_path, monkeypatch):
    from app.services.runs import cancel_run

    store, owner, run, remote, settings, clock = waiting_run(tmp_path, monkeypatch, accepted=True)
    assert process_once(store, settings, remote)

    def limited(*args):
        raise WeblateRejected(429, httpx.Response(429, headers={"Retry-After": "3600"}))

    remote.download_translation = limited
    clock[0] += 31
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "running"
    cancel_run(store, owner, run)
    assert process_once(store, settings, remote)
    assert get_run(store, owner, run)["status"] == "cancelled"
    assert remote.posts == 1
