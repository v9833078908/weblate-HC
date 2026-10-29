# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

from django.test import TestCase, override_settings
from django.utils import timezone

from weblate.trans.llm_delivery import (
    DeliveryUnresolved,
    capture_transport_response,
    dispatch_exchange,
    reserve_exchange,
    resolve_run_deliveries,
)
from weblate.trans.models.judge import ProducerRun
from weblate.trans.models.llm_delivery import ProducerLLMExchange
from weblate.trans.tasks import cleanup_judge_observability


class LLMDeliveryTest(TestCase):
    def setUp(self):
        self.directory = mkdtemp()
        self.addCleanup(rmtree, self.directory)
        self.override = override_settings(DATA_DIR=self.directory)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_codec_change_cannot_reuse_a_paid_identity(self):
        calls = []
        dispatch_exchange(
            ["codec:1"],
            "a" * 64,
            lambda: calls.append(1) or "saved",
            request_codec="routed-slots-v2",
        )
        with self.assertRaises(DeliveryUnresolved):
            dispatch_exchange(
                ["codec:1"],
                "a" * 64,
                lambda: calls.append(2) or "new",
                request_codec="routed-parts-v1",
                replay_only=True,
            )
        self.assertEqual(calls, [1])

    def test_legacy_replay_never_sends_or_reclaims_unsent_work(self):
        ref = reserve_exchange(["legacy:reserved"], "b" * 64)
        ProducerLLMExchange.objects.filter(pk=ref.id).update(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        calls = []
        for identity in (["legacy:reserved"], ["legacy:absent"]):
            with self.subTest(identity=identity), self.assertRaises(DeliveryUnresolved):
                dispatch_exchange(
                    identity,
                    "b" * 64,
                    lambda: calls.append(1) or "paid",
                    request_codec="routed-parts-v1",
                    replay_only=True,
                )
        self.assertEqual(calls, [])
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)

    def test_legacy_metadata_adoption_requires_a_matching_saved_reply(self):
        calls = []
        dispatch_exchange(["legacy:paid"], "c" * 64, lambda: "old raw")
        saved = ProducerLLMExchange.objects.get()
        original = (saved.request_digest, saved.response_digest, saved.owner)
        result = dispatch_exchange(
            ["legacy:paid"],
            "c" * 64,
            lambda: calls.append(1) or "paid",
            request_codec="routed-parts-v1",
            replay_only=True,
        )
        saved.refresh_from_db()
        self.assertEqual(result, "old raw")
        self.assertEqual(saved.request_codec, "routed-parts-v1")
        self.assertEqual(
            (saved.request_digest, saved.response_digest, saved.owner), original
        )
        self.assertEqual(calls, [])

    def test_saved_response_survives_parser_failure_without_second_charge(self):
        charged = []

        def send():
            charged.append("sent")
            return '{"answer":"complete raw response"}'

        first = dispatch_exchange(["unit:1:source-r1:pl"], "a" * 64, send)
        # A parser can die here; a redelivered task receives the same durable body.
        second = dispatch_exchange(["unit:1:source-r1:pl"], "a" * 64, send)
        self.assertEqual(first, second)
        self.assertEqual(charged, ["sent"])

    def test_lost_response_blocks_different_key_model_and_chunk(self):
        charged = []

        def lost():
            charged.append("paid")
            msg = "response lost after acceptance"
            raise TimeoutError(msg)

        with self.assertRaises(TimeoutError):
            dispatch_exchange(["unit:1:r1:pl", "unit:2:r1:pl"], "a" * 64, lost)
        with self.assertRaises(DeliveryUnresolved):
            dispatch_exchange(["unit:2:r1:pl"], "b" * 64, lost)
        self.assertEqual(charged, ["paid"])

    def test_one_judge_seat_is_reused_but_other_seat_can_run(self):
        calls = []

        def send():
            calls.append(1)
            return '{"verdict":"PASS"}'

        dispatch_exchange(["judge:unit:1:audit-r1:seat1"], "a" * 64, send)
        dispatch_exchange(["judge:unit:1:audit-r1:seat1"], "a" * 64, send)
        dispatch_exchange(["judge:unit:1:audit-r1:seat2"], "b" * 64, send)
        self.assertEqual(len(calls), 2)

    def test_missing_saved_body_never_authorizes_a_new_charge(self):
        calls = []

        def send():
            calls.append(1)
            return "saved"

        dispatch_exchange(["unit:1:r1:pl"], "a" * 64, send)
        record = ProducerLLMExchange.objects.get()
        (Path(self.directory) / "private-llm-delivery" / record.response_path).unlink()
        with self.assertRaises(DeliveryUnresolved):
            dispatch_exchange(["unit:1:r1:pl"], "a" * 64, send)
        self.assertEqual(len(calls), 1)

    def test_transport_body_is_recoverable_even_if_http_validation_crashes(self):
        calls = []

        def send():
            calls.append(1)
            capture_transport_response("full raw body")
            msg = "JSON validation failed after the body arrived"
            raise ValueError(msg)

        with self.assertRaises(ValueError):
            dispatch_exchange(["unit:3:r1:pl"], "c" * 64, send)
        self.assertEqual(
            dispatch_exchange(["unit:3:r1:pl"], "c" * 64, send), "full raw body"
        )
        self.assertEqual(calls, [1])

    def test_periodic_cleanup_purges_only_resolved_terminal_raw_bodies(self):
        old = timezone.now() - timedelta(days=31)
        run = ProducerRun.objects.create(
            scope_type="project",
            scope_id="1",
            scope_label="Offline",
            scope_path="/projects/offline/",
            requested_mode="translate",
            cap=1,
            status="completed",
            finished=old,
            summary={"mt_state": "completed"},
        )
        dispatch_exchange(
            ["mt:resolved"], "d" * 64, lambda: "old complete response", run_id=run.pk
        )
        resolve_run_deliveries(run)
        saved = ProducerLLMExchange.objects.get(run=run)
        ProducerLLMExchange.objects.filter(pk=saved.pk).update(resolved_at=old)
        dispatch_exchange(
            ["mt:unresolved"], "e" * 64, lambda: "must remain recoverable"
        )
        cleanup_judge_observability()
        self.assertFalse(
            (
                Path(self.directory) / "private-llm-delivery" / saved.response_path
            ).exists()
        )
        calls = []
        with self.assertRaises(DeliveryUnresolved):
            dispatch_exchange(
                ["mt:resolved"], "d" * 64, lambda: calls.append(1) or "paid"
            )
        self.assertEqual(calls, [])
        self.assertEqual(
            dispatch_exchange(["mt:unresolved"], "e" * 64, lambda: "paid"),
            "must remain recoverable",
        )
