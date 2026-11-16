# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-worker arbitration and lifetime of permanent MT financial witnesses."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from shutil import rmtree
from tempfile import mkdtemp
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connections
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from weblate_customization.machinery import RoutedLiteLLMTranslation

from weblate.machinery.llm import llm_delivery_usage
from weblate.trans.autotranslate import AutoTranslate, _AttemptCounter
from weblate.trans.llm_delivery import (
    DeliveryUnresolved,
    cleanup_delivery_bodies,
    read_response,
    reserve_exchange,
    resolve_run_deliveries,
)
from weblate.trans.models import Unit
from weblate.trans.models.judge import ProducerRun
from weblate.trans.models.llm_delivery import ProducerLLMExchange, ProducerLLMWork
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.mt_fallback import (
    ProducerMTFallbackAuthorization,
    ProducerMTFallbackUnit,
    ProducerMTStageMember,
    canonical_identity,
)
from weblate.trans.mt_fallback import (
    authorize_mt_credit_fallback,
    dispatch_mt_fallback_exchange,
    dispatch_mt_primary_exchange,
    enroll_mt_credit_scope,
    enroll_unit_stages,
    mark_slot_disposition,
    source_decision_revision,
    stage_send_guard,
)
from weblate.trans.tests.test_mt_fallback import VALID_SETTINGS, request_fixture
from weblate.trans.tests.utils import RepoTestMixin, create_test_user
from weblate.wizard_auth.grants import WizardAuthorizationRequired


class MTStageBoundaryTest(RepoTestMixin, TransactionTestCase):
    """Threads use separate committed connections, not TestCase's transaction."""

    def setUp(self) -> None:
        super().setUp()
        directory = mkdtemp()
        self.addCleanup(rmtree, directory)
        override = override_settings(DATA_DIR=directory)
        override.enable()
        self.addCleanup(override.disable)
        self.clone_test_repos()
        self.component = self.create_component()
        self.component.create_path()
        self.translation = self.component.translation_set.get(language__code="cs")
        self.user = create_test_user()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.unit = (
            self.translation.unit_set.filter(state=0)
            .select_related("source_unit")
            .first()
        )
        self.assertIsNotNone(self.unit, "fixture needs one empty target")
        self.producer_run = self._run()
        self.authorization = enroll_mt_credit_scope(
            run=self.producer_run,
            translation=self.translation,
            units=[self.unit],
            machinery_settings=VALID_SETTINGS,
        )

    def _run(self):
        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )

    @staticmethod
    def _thread(function):
        connections.close_all()
        try:
            return function()
        finally:
            connections.close_all()

    def _packet(self, stage):
        snapshot = {
            "method": "post",
            "url": "https://offline.example/v1/chat/completions",
            "payload": {
                "model": "QWEN3.7-plus"
                if stage == "fallback"
                else "google/gemini-3.7-flash",
                "messages": [{"role": "user", "content": "private frozen request"}],
            },
            "codec": "routed-slots-v4",
            "slots": [{"unit_stage_id": str(self.unit_stage.pk), "source_slot": 0}],
        }
        wire = {key: snapshot[key] for key in ("method", "url", "payload")}
        digest = hashlib.sha256(
            json.dumps(wire, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return snapshot, digest

    def _credit_receipt(self):
        self.unit_stage = enroll_unit_stages(self.authorization, [self.unit])[0]
        snapshot, digest = self._packet("primary")
        result = dispatch_mt_primary_exchange(
            self.authorization.pk,
            ((self.unit_stage.pk, 0),),
            digest,
            lambda: json.dumps(
                {
                    "status": 402,
                    "body": '{"error":{"code":402,"message":"Insufficient credits"}}',
                    "headers": {},
                }
            ),
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
            run_id=self.producer_run.pk,
        )
        mark_slot_disposition(self.unit_stage, 0, "credit_refused")
        authorize_mt_credit_fallback(self.authorization.pk)
        return result

    def _fallback(self, send, **kwargs):
        snapshot, digest = self._packet("fallback")
        return dispatch_mt_fallback_exchange(
            self.authorization.pk,
            ((self.unit_stage.pk, 0),),
            digest,
            send,
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
            run_id=self.producer_run.pk,
            **kwargs,
        )

    def test_concurrent_legacy_enrollment_and_durable_primary_have_one_winner(self):
        barrier = threading.Barrier(2, timeout=10)
        other_run = self._run()
        identity = canonical_identity(
            self.unit.pk,
            self.translation.language.code,
            source_decision_revision(self.unit),
        )

        def legacy():
            barrier.wait()
            try:
                unit = Unit.objects.select_related("source_unit").get(pk=self.unit.pk)
                enroll_unit_stages(self.authorization, [unit])
            except (DeliveryUnresolved, ValueError):
                return "blocked"
            return "claimed"

        def durable():
            barrier.wait()
            try:
                reserve_exchange(
                    [identity],
                    "a" * 64,
                    request_codec="routed-slots-v4",
                    run_id=other_run.pk,
                )
            except (DeliveryUnresolved, ValueError):
                return "blocked"
            return "claimed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._thread, function)
                for function in (legacy, durable)
            ]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(results), ["blocked", "claimed"])
        self.assertEqual(
            ProducerMTFallbackUnit.objects.filter(identity=identity).count()
            + ProducerLLMWork.objects.filter(identity=identity).count(),
            1,
        )

    def test_two_fallback_workers_and_redelivery_make_one_transport(self):
        primary = self._credit_receipt()
        sending = threading.Event()
        finish = threading.Event()
        calls = []
        body = json.dumps(
            {"status": 200, "body": '{"answer":"private answer"}', "headers": {}}
        )

        def send():
            calls.append("charged")
            sending.set()
            if not finish.wait(timeout=10):
                msg = "test did not release the in-flight transport"
                raise TimeoutError(msg)
            return body

        def competing():
            try:
                return self._fallback(send)
            except DeliveryUnresolved:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self._thread, lambda: self._fallback(send))
            try:
                self.assertTrue(sending.wait(timeout=10))
                second = executor.submit(self._thread, competing)
                self.assertIsNone(second.result(timeout=10))
            finally:
                finish.set()
            result = first.result(timeout=10)
        replay = self._fallback(send)
        self.assertEqual(calls, ["charged"])
        self.assertEqual(replay.exchange_id, result.exchange_id)
        self.assertEqual(read_response(result.exchange_id), body)
        self.assertNotEqual(result.exchange_id, primary.exchange_id)
        self.assertEqual(ProducerLLMExchange.objects.count(), 2)
        self.assertEqual(
            ProducerMTStageMember.objects.filter(stage="fallback").count(), 1
        )

    def test_pruning_removes_every_private_text_copy_but_keeps_spend_fences(self):
        primary = self._credit_receipt()
        fallback = self._fallback(
            lambda: json.dumps({"status": 200, "body": "private answer", "headers": {}})
        )
        ProducerMTFallbackUnit.objects.filter(pk=self.unit_stage.pk).update(
            primary_answers=["private primary answer"],
            fallback_answer="private fallback answer",
        )
        old = timezone.now() - timedelta(days=31)
        ProducerRun.objects.filter(pk=self.producer_run.pk).update(
            status="completed", finished=old
        )
        self.producer_run.refresh_from_db()
        resolve_run_deliveries(self.producer_run)
        ProducerLLMExchange.objects.filter(run=self.producer_run).update(
            resolved_at=old
        )
        members = list(
            ProducerMTStageMember.objects.values_list(
                "pk", "exchange_id", "source_slot"
            )
        )
        digests = dict(ProducerLLMExchange.objects.values_list("pk", "request_digest"))
        self.assertEqual(cleanup_delivery_bodies(), 2)
        for ident in (primary.exchange_id, fallback.exchange_id):
            exchange = ProducerLLMExchange.objects.get(pk=ident)
            self.assertEqual(exchange.state, "purged")
            self.assertIsNone(exchange.request_snapshot)
            self.assertEqual(exchange.request_digest, digests[ident])
            with self.assertRaises(DeliveryUnresolved):
                read_response(ident)
        self.unit_stage.refresh_from_db()
        self.assertEqual(self.unit_stage.source_slots, [])
        self.assertEqual(self.unit_stage.primary_answers, [])
        self.assertEqual(self.unit_stage.fallback_answer, "")
        self.assertEqual(
            list(
                ProducerMTStageMember.objects.values_list(
                    "pk", "exchange_id", "source_slot"
                )
            ),
            members,
        )
        with self.assertRaises(DeliveryUnresolved):
            self._fallback(lambda: self.fail("pruning opened a second paid attempt"))
        self.assertEqual(ProducerMTFallbackAuthorization.objects.count(), 1)

    def test_unresolved_receipts_and_text_survive_cleanup(self):
        primary = self._credit_receipt()
        with self.assertRaises(TimeoutError):
            self._fallback(
                lambda: (_ for _ in ()).throw(TimeoutError("provider outcome unknown"))
            )
        old = timezone.now() - timedelta(days=31)
        ProducerLLMExchange.objects.filter(pk=primary.exchange_id).update(
            resolved_at=old
        )
        ProducerRun.objects.filter(pk=self.producer_run.pk).update(
            status="partial", finished=old
        )
        self.assertEqual(cleanup_delivery_bodies(), 0)
        self.unit_stage.refresh_from_db()
        self.assertEqual(
            self.unit_stage.source_slots, list(self.unit.get_source_plurals())
        )
        self.assertEqual(
            ProducerLLMExchange.objects.get(pk=primary.exchange_id).state, "received"
        )
        self.assertEqual(json.loads(read_response(primary.exchange_id))["status"], 402)
        with self.assertRaises(DeliveryUnresolved):
            self._fallback(lambda: self.fail("unknown transport was paid again"))

    def test_account_deletion_preserves_stage_and_primary_financial_witnesses(self):
        primary = self._credit_receipt()
        actor_id = self.user.pk
        authorization_id = self.authorization.pk
        self.user.delete()
        authorization = ProducerMTFallbackAuthorization.objects.get(pk=authorization_id)
        self.assertIsNone(authorization.actor_id)
        self.assertEqual(authorization.actor_id_snapshot, actor_id)
        self.assertTrue(
            ProducerMTFallbackUnit.objects.filter(pk=self.unit_stage.pk).exists()
        )
        self.assertTrue(
            ProducerMTStageMember.objects.filter(
                exchange_id=primary.exchange_id
            ).exists()
        )
        self.assertEqual(json.loads(read_response(primary.exchange_id))["status"], 402)
        with self.assertRaises(ProtectedError):
            self.producer_run.delete()

    def test_stage_receipt_without_run_fk_still_requires_terminal_authorization_run(
        self,
    ):
        self._credit_receipt()
        fallback = self._fallback(
            lambda: json.dumps({"status": 200, "body": "private answer", "headers": {}})
        )
        old = timezone.now() - timedelta(days=31)
        ProducerLLMExchange.objects.filter(pk=fallback.exchange_id).update(
            run=None, resolved_at=old
        )
        self.assertEqual(cleanup_delivery_bodies(), 0)
        exchange = ProducerLLMExchange.objects.get(pk=fallback.exchange_id)
        self.assertEqual(exchange.state, "received")
        self.assertIsNotNone(exchange.request_snapshot)
        self.assertEqual(
            json.loads(read_response(exchange.pk))["body"], "private answer"
        )

    def test_celery_coverage_stays_monotonic_across_real_stores_and_guard_skips(self):
        units = list(self.translation.unit_set.order_by("pk")[:4])
        self.assertEqual(len(units), 4)
        Unit.objects.filter(pk__in=[units[0].pk, units[1].pk]).update(
            target="", state=0
        )
        auto = AutoTranslate(
            translation=self.translation,
            user=self.user,
            mode="translate",
            q="state:empty",
        )
        auto.batch_counter = _AttemptCounter()
        auto.batch_counter.eligible = 2
        auto.progress_steps = 2
        auto.init_coverage([unit.pk for unit in units])
        metadata = []
        task = SimpleNamespace(
            request=SimpleNamespace(id="mt-stage-coverage-boundary"),
            update_state=lambda **event: metadata.append(dict(event["meta"])),
        )
        with patch("weblate.trans.autotranslate.current_task", task):
            auto.record_coverage([units[2].pk])
            auto.report_coverage()
            auto.store_results(
                {units[0].pk: {"translation": ["Prvni"], "origin": [None]}}
            )
            auto.record_coverage([units[3].pk])
            auto.report_coverage()
            auto.store_results(
                {
                    units[1].pk: {
                        "translation": ["Druhy"]
                        * (self.translation.plural.number if units[1].is_plural else 1),
                        "origin": [None],
                    }
                }
            )
        self.assertEqual(
            list(dict.fromkeys(meta["progress"] for meta in metadata)),
            [25, 50, 75, 100],
        )
        self.assertEqual({meta["coverage_total"] for meta in metadata}, {4})
        self.assertEqual(
            [meta["coverage_done"] for meta in metadata],
            sorted(meta["coverage_done"] for meta in metadata),
        )
        guard_meta = next(meta for meta in metadata if meta["coverage_done"] == 3)
        self.assertEqual((guard_meta["done"], guard_meta["total"]), (1, 2))
        self.assertEqual((metadata[-1]["done"], metadata[-1]["total"]), (2, 2))
        for unit, expected in ((units[0], "Prvni"), (units[1], "Druhy")):
            unit.refresh_from_db()
            self.assertEqual(
                {form.strip() for form in unit.get_target_plurals()}, {expected}
            )

    def test_live_legacy_stage_usage_is_recorded_once_across_fresh_engines(self):
        self._credit_receipt()
        fallback = self._fallback(
            lambda: json.dumps({"status": 200, "body": "private answer", "headers": {}})
        )
        payload = {
            "id": "charged-stage-response",
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
                "cost": "0.02",
            },
        }
        token = llm_delivery_usage.set(fallback.exchange_id)
        try:
            for _delivery in range(2):
                engine = RoutedLiteLLMTranslation({"key": "synthetic-only"})
                engine.credit_fallback_single_attempt = True
                engine.usage_run_id = self.producer_run.pk
                engine.record_llm_usage(payload, "QWEN3.7-plus", 1)
        finally:
            llm_delivery_usage.reset(token)
        usage = LLMUsageLog.objects.get(run=self.producer_run)
        self.assertEqual(usage.cost_usd, Decimal("0.02"))
        self.assertEqual((usage.prompt_tokens, usage.completion_tokens), (7, 3))
        exchange = ProducerLLMExchange.objects.get(pk=fallback.exchange_id)
        self.assertTrue(exchange.usage_recorded)

    def test_distinct_plural_primary_slots_keep_independent_receipts(self):
        plural = next(
            unit
            for unit in self.translation.unit_set.select_related("source_unit")
            if len(unit.get_source_plurals()) > 1
        )
        run = self._run()
        authorization = enroll_mt_credit_scope(
            run=run,
            translation=self.translation,
            units=[plural],
            machinery_settings=VALID_SETTINGS,
        )
        stage = enroll_unit_stages(authorization, [plural])[0]
        exchanges = []
        for index in range(2):
            slots = ((stage.pk, index),)
            snapshot, digest = request_fixture(slots, model="google/gemini-3.7-flash")
            receipt = json.dumps({"status": 200, "body": str(index), "headers": {}})
            result = dispatch_mt_primary_exchange(
                authorization.pk,
                slots,
                digest,
                lambda body=receipt: body,
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
                run_id=run.pk,
            )
            replay = dispatch_mt_primary_exchange(
                authorization.pk,
                slots,
                digest,
                lambda: self.fail("an accepted plural slot was paid twice"),
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
                run_id=run.pk,
            )
            self.assertEqual(replay.exchange_id, result.exchange_id)
            self.assertEqual(replay.receipt, receipt)
            exchanges.append(result.exchange_id)
        self.assertNotEqual(*exchanges)
        self.assertEqual(
            list(
                stage.stage_members.order_by("source_slot").values_list(
                    "source_slot", "exchange_id"
                )
            ),
            list(enumerate(exchanges)),
        )

    def test_revoked_delegation_blocks_fallback_immediately_before_transport(self):
        self._credit_receipt()
        guard = stage_send_guard(
            self.authorization,
            actor=self.user,
            slots=((self.unit_stage.pk, 0),),
        )
        with (
            patch(
                "weblate.wizard_auth.grants.run_delegation_active", return_value=False
            ),
            self.assertRaises(WizardAuthorizationRequired),
        ):
            self._fallback(
                lambda: self.fail("a revoked delegation paid for fallback"),
                before_send=guard,
            )
        self.unit_stage.refresh_from_db()
        exchange = ProducerLLMExchange.objects.get(
            pk=self.unit_stage.fallback_exchange_id
        )
        self.assertEqual(exchange.state, "reserved")

    def test_empty_target_audit_change_blocks_fallback_transport(self):
        self._credit_receipt()
        Unit.objects.filter(pk=self.unit.pk).update(state=10)
        guard = stage_send_guard(
            self.authorization,
            actor=self.user,
            slots=((self.unit_stage.pk, 0),),
        )
        with self.assertRaises(DeliveryUnresolved):
            self._fallback(
                lambda: self.fail("an audit change was ignored before payment"),
                before_send=guard,
            )
        self.unit.refresh_from_db()
        self.assertFalse(any(self.unit.get_target_plurals()))
        self.assertEqual(self.unit.state, 10)

    def test_unused_enrollment_private_text_expires_without_an_exchange(self):
        stage = enroll_unit_stages(self.authorization, [self.unit])[0]
        old = timezone.now() - timedelta(days=31)
        ProducerRun.objects.filter(pk=self.producer_run.pk).update(
            status="completed", finished=old
        )
        self.assertEqual(cleanup_delivery_bodies(), 0)
        stage.refresh_from_db()
        self.assertEqual((stage.source_slots, stage.primary_answers), ([], []))
        self.assertEqual(
            stage.identity,
            canonical_identity(
                self.unit.pk,
                self.translation.language.code,
                source_decision_revision(self.unit),
            ),
        )
        self.assertTrue(
            ProducerMTFallbackAuthorization.objects.filter(
                pk=self.authorization.pk
            ).exists()
        )
