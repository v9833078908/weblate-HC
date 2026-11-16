# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Financial and transport contracts of the MT credit-fallback stage."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from shutil import rmtree
from tempfile import mkdtemp
from typing import ClassVar

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from weblate.trans.llm_delivery import (
    DeliveryUnresolved,
    record_response,
    reserve_exchange,
)
from weblate.trans.models.judge import ProducerRun
from weblate.trans.models.llm_delivery import ProducerLLMExchange, ProducerLLMWork
from weblate.trans.models.mt_fallback import (
    ProducerMTFallbackAuthorization,
    ProducerMTFallbackUnit,
    ProducerMTStageMember,
    canonical_identity,
)
from weblate.trans.mt_fallback import (
    FALLBACK_POLICY,
    authorize_mt_credit_fallback,
    close_mt_credit_fallback,
    dispatch_mt_fallback_exchange,
    dispatch_mt_primary_exchange,
    eligible_fallback_slots,
    enroll_mt_credit_scope,
    enroll_unit_stages,
    fallback_profile_configured,
    mark_slot_disposition,
    primary_saw_confirmed_credit_refusal,
    recover_mt_stage_answers,
    reserve_mt_fallback_exchange,
    resolve_fallback_profile,
    save_fallback_answer,
    semantic_key_for,
    source_decision_revision,
    validate_request_snapshot,
)
from weblate.trans.tests.test_views import ViewTestCase

LITELLM_KEY = "test-key"

VALID_SETTINGS = {
    "openrouter": {
        "key": "or-key",
        "routing": {"*": "google/gemini-3.7-flash"},
        "mt_fallback": {"engine": "litellm", "routing": {"*": "QWEN3.7-plus"}},
    },
    "litellm": {"key": LITELLM_KEY, "routing": {"*": "QWEN3.7-plus"}},
}


def request_fixture(slots, *, codec="routed-slots-v4", model="QWEN3.7-plus"):
    """Build the same secret-free wire and occurrence mapping as transport."""
    snapshot = {
        "method": "post",
        "url": "https://proxy/v1/chat/completions",
        "payload": {"model": model},
        "codec": codec,
        "slots": [
            {"unit_stage_id": str(stage_id), "source_slot": slot}
            for stage_id, slot in slots
        ],
    }
    wire = {key: snapshot[key] for key in ("url", "method", "payload")}
    digest = hashlib.sha256(
        json.dumps(wire, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    return snapshot, digest


def settings_with(mt_fallback: object) -> dict:
    """Build a settings map carrying one fallback mapping under test."""
    return {
        "openrouter": {"key": "or-key", "mt_fallback": mt_fallback},
        "litellm": {"key": LITELLM_KEY},
    }


class FallbackProfileContractTest(SimpleTestCase):
    """The fixed pilot route is the only accepted configuration."""

    def test_exact_route_resolves(self) -> None:
        profile = resolve_fallback_profile(VALID_SETTINGS)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.engine, "litellm")
        self.assertEqual(profile.model, "QWEN3.7-plus")
        self.assertEqual(profile.profile, FALLBACK_POLICY)
        self.assertTrue(profile.configuration_digest)

    def test_absent_mapping_is_off(self) -> None:
        self.assertFalse(fallback_profile_configured({"openrouter": {"key": "or-key"}}))

    def test_empty_mapping_is_off(self) -> None:
        self.assertFalse(fallback_profile_configured(settings_with({})))

    def test_other_engine_is_off(self) -> None:
        self.assertIsNone(
            resolve_fallback_profile(
                settings_with({"engine": "openrouter", "routing": {"*": "m"}})
            )
        )

    def test_other_model_is_off(self) -> None:
        self.assertIsNone(
            resolve_fallback_profile(
                settings_with({"engine": "litellm", "routing": {"*": "other"}})
            )
        )

    def test_extra_keys_are_off(self) -> None:
        self.assertIsNone(
            resolve_fallback_profile(
                settings_with(
                    {
                        "engine": "litellm",
                        "routing": {"*": "QWEN3.7-plus"},
                        "credentials": "secret",
                    }
                )
            )
        )

    def test_language_specific_routing_is_off(self) -> None:
        self.assertIsNone(
            resolve_fallback_profile(
                settings_with({"engine": "litellm", "routing": {"ja": "QWEN3.7-plus"}})
            )
        )

    def test_malformed_historical_json_is_off(self) -> None:
        self.assertIsNone(resolve_fallback_profile(settings_with("not-a-dict")))
        self.assertIsNone(resolve_fallback_profile(settings_with(["litellm"])))
        self.assertIsNone(
            resolve_fallback_profile(
                settings_with({"engine": "litellm", "routing": "not-a-dict"})
            )
        )

    def test_missing_fallback_key_is_off(self) -> None:
        self.assertIsNone(
            resolve_fallback_profile(
                {
                    "openrouter": {
                        "key": "or-key",
                        "mt_fallback": {
                            "engine": "litellm",
                            "routing": {"*": "QWEN3.7-plus"},
                        },
                    },
                    "litellm": {"routing": {"*": "QWEN3.7-plus"}},
                }
            )
        )

    def test_non_openrouter_primary_is_off(self) -> None:
        self.assertIsNone(
            resolve_fallback_profile(VALID_SETTINGS, primary_engine="litellm")
        )


class CreditRefusalEligibilityTest(SimpleTestCase):
    """Only a confirmed credit refusal authorizes; nothing else does."""

    def test_credit_refusal_is_eligible(self) -> None:
        self.assertTrue(primary_saw_confirmed_credit_refusal(["insufficient-credit"]))

    def test_quota_is_not_eligible(self) -> None:
        self.assertFalse(primary_saw_confirmed_credit_refusal(["quota-exhausted"]))

    def test_auth_is_not_eligible(self) -> None:
        self.assertFalse(
            primary_saw_confirmed_credit_refusal(
                ["authentication", "provider-unavailable"]
            )
        )

    def test_timeout_and_none_are_not_eligible(self) -> None:
        self.assertFalse(primary_saw_confirmed_credit_refusal([None, None]))

    def test_late_credit_after_generic_still_eligible(self) -> None:
        # A generic provider error does not mask a later confirmed credit stop.
        self.assertTrue(
            primary_saw_confirmed_credit_refusal(
                ["provider-unavailable", "insufficient-credit"]
            )
        )


class MTFallbackStageSchemaTest(ViewTestCase):
    """Real database constraints of the stage journal."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.project = self.translation.component.project
        self.units = list(
            self.translation.unit_set.filter(state=0).select_related("source_unit")[:3]
        )
        self.assertGreater(len(self.units), 0, "need empty units for the stage tests")
        self.run = self._persisted_run()

    def _persisted_run(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.judge import ProducerRun

        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )

    def _enroll(self) -> ProducerMTFallbackAuthorization:
        return enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
            machinery_settings=VALID_SETTINGS,
        )

    def test_enrollment_freezes_scope_digest(self) -> None:
        authorization = self._enroll()
        self.assertEqual(authorization.state, "observing")
        self.assertEqual(authorization.scope_size, len(self.units))
        self.assertEqual(len(authorization.scope_digest), 64)
        self.assertEqual(authorization.fallback_engine, "litellm")
        self.assertEqual(authorization.fallback_model, "QWEN3.7-plus")
        self.assertEqual(authorization.actor_id, self.user.pk)

    def test_repeated_enrollment_of_same_scope_reuses_row(self) -> None:
        first = self._enroll()
        second = self._enroll()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ProducerMTFallbackAuthorization.objects.count(), 1)

    def test_changed_scope_under_same_authorization_is_refused(self) -> None:
        self._enroll()
        with self.assertRaises(ValueError):
            enroll_mt_credit_scope(
                run=self.run,
                translation=self.translation,
                units=self.units[:-1],
                machinery_settings=VALID_SETTINGS,
            )

    def test_semantic_key_is_the_canonical_primary_identity(self) -> None:
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.judge import compute_decision_revision

        authorization = self._enroll()
        rows = enroll_unit_stages(authorization, self.units)
        self.assertEqual(len(rows), len(self.units))
        self.assertEqual(len({row.semantic_key for row in rows}), len(rows))
        for row in rows:
            expected = canonical_identity(
                row.unit_id_snapshot,
                row.language_code,
                compute_decision_revision(row.unit.source_unit),
            )
            self.assertEqual(row.identity, expected)
            self.assertEqual(row.semantic_key, semantic_key_for(expected))

    def test_replacement_enrollment_cannot_open_second_slot(self) -> None:
        """One immutable financial slot per canonical unit of work, forever."""
        authorization = self._enroll()
        enroll_unit_stages(authorization, self.units)
        again = enroll_unit_stages(authorization, self.units)
        self.assertEqual(len(again), len(self.units))
        self.assertEqual(ProducerMTFallbackUnit.objects.count(), len(self.units))

    def test_second_operation_for_same_unit_is_refused(self) -> None:
        """Another run's authorization cannot claim the same canonical work."""
        authorization = self._enroll()
        enroll_unit_stages(authorization, self.units)
        other_run = self._persisted_run()
        other = enroll_mt_credit_scope(
            run=other_run,
            translation=self.translation,
            units=self.units,
            machinery_settings=VALID_SETTINGS,
        )
        with self.assertRaises(ValueError):
            enroll_unit_stages(other, self.units)

    def test_stage_member_slot_and_position_are_unique(self) -> None:
        authorization = self._enroll()
        unit_stage = enroll_unit_stages(authorization, self.units)[0]
        exchange = ProducerLLMExchange.objects.create(
            request_digest="a" * 64,
            request_codec="routed-slots-v4",
            owner="owner",
            lease_until=timezone.now() + timedelta(seconds=900),
            mt_authorization=authorization,
            mt_stage="fallback",
        )
        ProducerMTStageMember.objects.create(
            unit_stage=unit_stage,
            exchange=exchange,
            stage="fallback",
            source_slot=0,
            position=0,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProducerMTStageMember.objects.create(
                unit_stage=unit_stage,
                exchange=exchange,
                stage="fallback",
                source_slot=0,
                position=1,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProducerMTStageMember.objects.create(
                unit_stage=unit_stage,
                exchange=exchange,
                stage="fallback",
                source_slot=1,
                position=0,
            )

    def test_plural_unit_freezes_every_source_slot(self) -> None:
        plural = next(
            (
                unit
                for unit in self.translation.unit_set.all().order_by("pk")
                if len(unit.get_source_plurals()) > 1
            ),
            None,
        )
        if plural is None:
            self.skipTest("fixture has no plural unit")
        authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=[plural],
            machinery_settings=VALID_SETTINGS,
        )
        unit_stage = enroll_unit_stages(authorization, [plural])[0]
        self.assertEqual(unit_stage.source_slots, list(plural.get_source_plurals()))
        self.assertEqual(
            len(unit_stage.primary_dispositions), len(plural.get_source_plurals())
        )


class MTFallbackDispatchContractTest(ViewTestCase):
    """Reservation, snapshot-before-send, replay and no-second-charge."""

    def setUp(self) -> None:
        super().setUp()
        self.directory = mkdtemp()
        self.addCleanup(rmtree, self.directory)
        self.override = override_settings(DATA_DIR=self.directory)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.component = self.translation.component
        self.run = self._persisted_run()
        self.units = list(
            self.translation.unit_set.filter(state=0).select_related("source_unit")[:2]
        )
        self.assertGreater(len(self.units), 0, "need empty units for dispatch tests")
        self.authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
            machinery_settings=VALID_SETTINGS,
        )
        self.unit_stage = enroll_unit_stages(self.authorization, self.units)[0]
        mark_slot_disposition(self.unit_stage, 0, "credit_refused")
        self.slots = ((self.unit_stage.pk, 0),)

    def _persisted_run(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.judge import ProducerRun

        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )

    def _reserve(self, *, model="QWEN3.7-plus", codec="routed-slots-v4", **kwargs):
        snapshot, digest = request_fixture(self.slots, codec=codec, model=model)
        return reserve_mt_fallback_exchange(
            self.authorization.pk,
            self.slots,
            digest,
            request_snapshot=snapshot,
            request_codec=codec,
            **kwargs,
        )

    def test_reservation_commits_snapshot_before_send(self) -> None:
        snapshot, digest = request_fixture(self.slots)
        reference = reserve_mt_fallback_exchange(
            self.authorization.pk,
            self.slots,
            digest,
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        exchange = ProducerLLMExchange.objects.get(pk=reference.id)
        self.assertEqual(exchange.state, "reserved")
        self.assertEqual(exchange.mt_stage, "fallback")
        self.assertEqual(exchange.request_codec, "routed-slots-v4")
        # The secret-free wire snapshot is durable before any transport.
        self.assertEqual(exchange.request_snapshot, snapshot)
        self.assertEqual(exchange.request_snapshot["method"], "post")
        self.assertEqual(exchange.request_snapshot["payload"]["model"], "QWEN3.7-plus")
        self.assertNotIn("key", exchange.request_snapshot)
        self.assertNotIn("headers", exchange.request_snapshot)

    def test_exact_replay_sends_nothing(self) -> None:
        calls = []
        snapshot, digest = request_fixture(self.slots)
        result = dispatch_mt_fallback_exchange(
            self.authorization.pk,
            self.slots,
            digest,
            lambda: calls.append(1) or '{"answer":1}',
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        self.assertTrue(result.receipt)
        again = dispatch_mt_fallback_exchange(
            self.authorization.pk,
            self.slots,
            digest,
            lambda: calls.append(2) or '{"answer":1}',
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        self.assertEqual(calls, [1])
        self.assertEqual(result.receipt, again.receipt)
        self.assertEqual(result.exchange_id, again.exchange_id)

    def test_changed_digest_never_opens_second_exchange(self) -> None:
        self._reserve()
        with self.assertRaises(DeliveryUnresolved):
            self._reserve(model="changed-model")
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)

    def test_changed_codec_never_opens_second_exchange(self) -> None:
        self._reserve()
        with self.assertRaises(DeliveryUnresolved):
            self._reserve(codec="routed-slots-v3")
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)

    def test_changed_snapshot_mapping_never_reuses_reserved_exchange(self) -> None:
        self._reserve()
        snapshot, digest = request_fixture(self.slots)
        snapshot["slots"] = []
        with self.assertRaises(DeliveryUnresolved):
            reserve_mt_fallback_exchange(
                self.authorization.pk,
                self.slots,
                digest,
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
            )
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)

    def test_unknown_slot_cannot_be_reserved(self) -> None:
        mark_slot_disposition(self.unit_stage, 0, "unknown")
        with self.assertRaises(DeliveryUnresolved):
            self._reserve()
        self.assertFalse(ProducerLLMExchange.objects.exists())

    def test_snapshot_digest_mismatch_cannot_be_reserved(self) -> None:
        snapshot, _digest = request_fixture(self.slots)
        with self.assertRaises(DeliveryUnresolved):
            reserve_mt_fallback_exchange(
                self.authorization.pk,
                self.slots,
                "a" * 64,
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
            )
        self.assertFalse(ProducerLLMExchange.objects.exists())

    def test_timeout_leaves_unknown_and_blocks_retry(self) -> None:
        charged = []
        snapshot, digest = request_fixture(self.slots)

        def lost():
            charged.append("paid")
            msg = "response lost after acceptance"
            raise TimeoutError(msg)

        with self.assertRaises(TimeoutError):
            dispatch_mt_fallback_exchange(
                self.authorization.pk,
                self.slots,
                digest,
                lost,
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
            )
        exchange = ProducerLLMExchange.objects.get()
        self.assertEqual(exchange.state, "unknown")
        self.unit_stage.refresh_from_db()
        self.assertEqual(self.unit_stage.fallback_state, "unknown")
        # An unknown paid result is a permanent fence: the same request can
        # neither be resent nor re-keyed into a second charge.
        with self.assertRaises(DeliveryUnresolved):
            dispatch_mt_fallback_exchange(
                self.authorization.pk,
                self.slots,
                digest,
                lost,
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
            )
        with self.assertRaises(DeliveryUnresolved):
            self._reserve(model="changed-model")
        self.assertEqual(charged, ["paid"])

    def test_one_exchange_covers_all_slots_of_one_unit(self) -> None:
        plural = next(
            (
                unit
                for unit in sorted(self.units, key=lambda item: item.pk)
                if len(unit.get_source_plurals()) > 1
            ),
            None,
        )
        if plural is None:
            self.skipTest("fixture has no plural unit")
        # The plural unit is already enrolled under the test's own scope; use
        # its frozen slot rather than opening a second enrollment.
        unit_stage = min(
            (
                row
                for row in enroll_unit_stages(self.authorization, self.units)
                if row.unit_id_snapshot == plural.pk
            ),
            key=lambda row: str(row.pk),
        )
        slot_count = len(unit_stage.source_slots)
        for index in range(slot_count):
            mark_slot_disposition(unit_stage, index, "credit_refused")
        slots = tuple((unit_stage.pk, index) for index in range(slot_count))
        snapshot, digest = request_fixture(slots)
        reference = reserve_mt_fallback_exchange(
            self.authorization.pk,
            slots,
            digest,
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        members = ProducerMTStageMember.objects.filter(exchange_id=reference.id)
        self.assertEqual(members.count(), slot_count)
        self.assertEqual(
            sorted(members.values_list("source_slot", flat=True)),
            list(range(slot_count)),
        )
        # One exchange for the whole unit, never one request per slot.
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)

    def test_cross_unit_request_is_refused(self) -> None:
        other = enroll_unit_stages(self.authorization, self.units)[1]
        slots = ((self.unit_stage.pk, 0), (other.pk, 0))
        snapshot, digest = request_fixture(slots)
        with self.assertRaises(ValueError):
            reserve_mt_fallback_exchange(
                self.authorization.pk,
                slots,
                digest,
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
            )

    def test_never_sent_tail_claims_the_real_fallback_exchange(self) -> None:
        self.unit_stage.primary_work = None
        self.unit_stage.save(update_fields=["primary_work"])
        reference = self._reserve()
        work = ProducerLLMWork.objects.get(identity=self.unit_stage.identity)
        self.assertEqual(work.exchange_id, reference.id)

    def test_refused_primary_claim_keeps_its_primary_exchange(self) -> None:
        primary = ProducerLLMExchange.objects.create(
            request_digest="c" * 64,
            request_codec="routed-slots-v4",
            owner="primary-owner",
            lease_until=timezone.now() + timedelta(seconds=900),
            mt_authorization=self.authorization,
            mt_stage="primary",
        )
        ProducerLLMWork.objects.create(
            semantic_key=self.unit_stage.semantic_key,
            identity=self.unit_stage.identity,
            position=0,
            exchange=primary,
        )
        self.unit_stage.primary_work = ProducerLLMWork.objects.get(
            identity=self.unit_stage.identity
        )
        self.unit_stage.save(update_fields=["primary_work"])
        reference = self._reserve()
        work = ProducerLLMWork.objects.get(identity=self.unit_stage.identity)
        # The canonical claim is untouched by the fallback reservation.
        self.assertEqual(work.exchange_id, primary.pk)
        self.assertNotEqual(work.exchange_id, reference.id)

    def test_replay_without_reservation_is_refused(self) -> None:
        with self.assertRaises(DeliveryUnresolved):
            self._reserve(replay_only=True)


class MTFallbackEligibilityContractTest(ViewTestCase):
    """Receipt-driven eligibility, not batch-wide guessing."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.run = self._persisted_run()
        self.units = list(
            self.translation.unit_set.filter(state=0).select_related("source_unit")[:1]
        )
        self.assertGreater(len(self.units), 0, "need empty units for eligibility tests")
        self.authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
            machinery_settings=VALID_SETTINGS,
        )
        self.unit_stage = enroll_unit_stages(self.authorization, self.units)[0]
        # Give the slot row a second position so mixed-stage tests are real.
        self.unit_stage.source_slots = [*self.unit_stage.source_slots, "second slot"]
        self.unit_stage.primary_dispositions = [
            "unknown",
            "unknown",
        ]
        self.unit_stage.primary_answers = ["", ""]
        self.unit_stage.save(
            update_fields=[
                "source_slots",
                "primary_dispositions",
                "primary_answers",
            ]
        )

    def _persisted_run(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.judge import ProducerRun

        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )

    def _set_dispositions(self, dispositions: list[str]) -> None:
        self.unit_stage.primary_dispositions = dispositions
        self.unit_stage.primary_answers = ["" for _ in dispositions]
        self.unit_stage.save(update_fields=["primary_dispositions", "primary_answers"])

    def test_never_sent_slot_is_eligible(self) -> None:
        self._set_dispositions(["never_sent_due_credit", "never_sent_due_credit"])
        self.assertEqual(len(eligible_fallback_slots(self.authorization.pk)), 2)

    def test_credit_refused_slot_is_the_eligible_remainder(self) -> None:
        # A slot whose own primary request was refused for credit is exactly
        # the eligible remainder of that unit: the primary paid and was
        # refused, so the fallback carries the same unit of work once.
        self._set_dispositions(["credit_refused", "credit_refused"])
        self.assertEqual(len(eligible_fallback_slots(self.authorization.pk)), 2)

    def test_successful_primary_slot_is_not_repurchased(self) -> None:
        self._set_dispositions(["primary_usable", "never_sent_due_credit"])
        slots = eligible_fallback_slots(self.authorization.pk)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0][1], 1)

    def test_invalid_partial_and_unknown_slots_are_not_repurchased(self) -> None:
        for disposition in ("primary_invalid", "primary_failed", "unknown"):
            self._set_dispositions([disposition, disposition])
            self.assertEqual(eligible_fallback_slots(self.authorization.pk), [])

    def test_mixed_stage_unit_keeps_only_unresolved_slot(self) -> None:
        self._set_dispositions(["primary_usable", "never_sent_due_credit"])
        mark_slot_disposition(self.unit_stage, 0, "primary_usable", answer="first form")
        slots = eligible_fallback_slots(self.authorization.pk)
        self.assertEqual([index for _row, index, _text in slots], [1])


class MTFallbackRecoveryContractTest(ViewTestCase):
    """Recovery reads persisted evidence and never sends."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.run = self._persisted_run()
        self.units = list(
            self.translation.unit_set.filter(state=0).select_related("source_unit")[:1]
        )
        self.assertGreater(len(self.units), 0, "need empty units for recovery tests")
        self.authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
            machinery_settings=VALID_SETTINGS,
        )
        self.unit_stage = enroll_unit_stages(self.authorization, self.units)[0]

    def _persisted_run(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.judge import ProducerRun

        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )

    def test_recovery_returns_persisted_primary_answer(self) -> None:
        mark_slot_disposition(
            self.unit_stage, 0, "primary_usable", answer="captured prefix"
        )
        result = recover_mt_stage_answers(self.authorization.pk)
        self.assertEqual(len(result.answers), 1)
        self.assertEqual(result.answers[0].text, "captured prefix")
        self.assertEqual(result.answers[0].stage, "primary")
        self.assertTrue(result.answers[0].usable)

    def test_recovery_returns_persisted_fallback_answer(self) -> None:
        save_fallback_answer(
            self.authorization.pk, {self.unit_stage.identity: ["fallback answer"]}
        )
        result = recover_mt_stage_answers(self.authorization.pk)
        self.assertEqual(result.fallback_state, "received")
        self.assertEqual(result.answers[0].stage, "fallback")
        self.assertEqual(result.answers[0].text, "fallback answer")

    def test_recovery_reports_credit_refusal_as_unusable(self) -> None:
        mark_slot_disposition(self.unit_stage, 0, "credit_refused")
        result = recover_mt_stage_answers(self.authorization.pk)
        self.assertFalse(result.answers[0].usable)
        self.assertEqual(result.answers[0].text, "")

    def test_recovery_never_invents_an_answer(self) -> None:
        mark_slot_disposition(self.unit_stage, 0, "unknown")
        result = recover_mt_stage_answers(self.authorization.pk)
        self.assertEqual(result.answers, [])


class MTFallbackAuthorizationLifecycleTest(ViewTestCase):
    """The standing authorization promotes exactly once."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.run = self._persisted_run()
        self.units = list(
            self.translation.unit_set.filter(state=0).select_related("source_unit")[:1]
        )
        self.assertGreater(len(self.units), 0, "need empty units for lifecycle tests")

    def _persisted_run(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.judge import ProducerRun

        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )

    def test_authorize_promotes_once(self) -> None:
        authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
            machinery_settings=VALID_SETTINGS,
        )
        authorize_mt_credit_fallback(authorization.pk)
        again = authorize_mt_credit_fallback(authorization.pk)
        self.assertEqual(again.state, "authorized")
        self.assertIsNotNone(again.authorized_at)
        self.assertEqual(ProducerMTFallbackAuthorization.objects.count(), 1)

    def test_enrollment_without_fallback_profile_records_no_route(self) -> None:
        authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
            machinery_settings={"openrouter": {"key": "or-key"}},
        )
        # No fallback configured: the enrollment records no paid route.
        self.assertEqual(authorization.fallback_engine, "")
        self.assertEqual(authorization.state, "observing")

    def test_enrollment_without_settings_is_still_journaled(self) -> None:
        authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
        )
        self.assertEqual(authorization.state, "observing")
        self.assertEqual(authorization.scope_size, len(self.units))


class MTStageExchangePrivacyTest(ViewTestCase):
    """The private snapshot never carries credentials."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.run = self._persisted_run()
        self.units = list(
            self.translation.unit_set.filter(state=0).select_related("source_unit")[:1]
        )
        self.assertGreater(len(self.units), 0, "need empty units for privacy tests")
        self.authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=self.units,
            machinery_settings=VALID_SETTINGS,
        )
        self.unit_stage = enroll_unit_stages(self.authorization, self.units)[0]

    def _persisted_run(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.judge import ProducerRun

        return ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )

    def test_snapshot_has_no_authorization_material(self) -> None:
        mark_slot_disposition(self.unit_stage, 0, "credit_refused")
        slots = ((self.unit_stage.pk, 0),)
        snapshot, digest = request_fixture(slots)
        reserve_mt_fallback_exchange(
            self.authorization.pk,
            slots,
            digest,
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        exchange = ProducerLLMExchange.objects.get()
        serialized = str(exchange.request_snapshot)
        for secret in ("Authorization", "Bearer", LITELLM_KEY, "or-key", "api_key"):
            self.assertNotIn(secret, serialized)

    def test_stage_exchange_is_linked_to_authorization(self) -> None:
        mark_slot_disposition(self.unit_stage, 0, "credit_refused")
        slots = ((self.unit_stage.pk, 0),)
        snapshot, digest = request_fixture(slots)
        reference = reserve_mt_fallback_exchange(
            self.authorization.pk,
            slots,
            digest,
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        exchange = ProducerLLMExchange.objects.get(pk=reference.id)
        self.assertEqual(exchange.mt_authorization_id, self.authorization.pk)
        self.assertEqual(exchange.mt_stage, "fallback")
        self.unit_stage.refresh_from_db()
        self.assertEqual(self.unit_stage.fallback_exchange_id, reference.id)
        self.assertEqual(self.unit_stage.fallback_state, "reserved")


class MTStagePrimaryJournalTest(ViewTestCase):
    """A primary stage request journals its own receipt before the classifier."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.unit = self.translation.unit_set.filter(state=0).first()
        self.assertIsNotNone(self.unit)
        self.run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )
        self.authorization = enroll_mt_credit_scope(
            run=self.run,
            translation=self.translation,
            units=[self.unit],
        )
        self.unit_stage = enroll_unit_stages(self.authorization, [self.unit])[0]

    def test_primary_request_leaves_stage_member_and_receipt(self) -> None:
        snapshot, digest = request_fixture(((self.unit_stage.pk, 0),), model="m")
        result = dispatch_mt_primary_exchange(
            self.authorization.pk,
            ((self.unit_stage.pk, 0),),
            digest,
            lambda: json.dumps(
                {"status": 402, "body": '{"error": "spent"}', "headers": {}}
            ),
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        member = ProducerMTStageMember.objects.get(
            unit_stage=self.unit_stage, stage="primary"
        )
        self.assertEqual(member.source_slot, 0)
        self.assertEqual(member.position, 0)
        exchange = ProducerLLMExchange.objects.get(pk=result.exchange_id)
        self.assertEqual(exchange.mt_stage, "primary")
        self.assertEqual(exchange.state, "received")
        self.assertEqual(exchange.request_digest, digest)
        self.assertIsNotNone(exchange.request_snapshot)

    def test_exact_replay_sends_nothing(self) -> None:
        snapshot, digest = request_fixture(((self.unit_stage.pk, 0),))
        dispatch_mt_primary_exchange(
            self.authorization.pk,
            ((self.unit_stage.pk, 0),),
            digest,
            lambda: json.dumps({"status": 200, "body": "ok", "headers": {}}),
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        calls = []

        def send():
            calls.append(1)
            return json.dumps({"status": 200, "body": "ok", "headers": {}})

        dispatch_mt_primary_exchange(
            self.authorization.pk,
            ((self.unit_stage.pk, 0),),
            digest,
            send,
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        self.assertEqual(calls, [])
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)

    def test_changed_digest_never_opens_second_exchange(self) -> None:
        slots = ((self.unit_stage.pk, 0),)
        snapshot, digest = request_fixture(slots)
        changed, changed_digest = request_fixture(slots, model="changed")
        dispatch_mt_primary_exchange(
            self.authorization.pk,
            ((self.unit_stage.pk, 0),),
            digest,
            lambda: json.dumps({"status": 200, "body": "ok", "headers": {}}),
            request_snapshot=snapshot,
            request_codec="routed-slots-v4",
        )
        with self.assertRaises(DeliveryUnresolved):
            dispatch_mt_primary_exchange(
                self.authorization.pk,
                ((self.unit_stage.pk, 0),),
                changed_digest,
                lambda: json.dumps({"status": 200, "body": "ok", "headers": {}}),
                request_snapshot=changed,
                request_codec="routed-slots-v4",
            )
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)

    def test_timeout_leaves_unknown_and_blocks_resend(self) -> None:
        snapshot, digest = request_fixture(((self.unit_stage.pk, 0),))
        with self.assertRaises(TimeoutError):
            dispatch_mt_primary_exchange(
                self.authorization.pk,
                ((self.unit_stage.pk, 0),),
                digest,
                lambda: (_ for _ in ()).throw(TimeoutError()),
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
            )
        exchange = ProducerLLMExchange.objects.get()
        self.assertEqual(exchange.state, "unknown")
        calls = []

        def send():
            calls.append(1)
            return json.dumps({"status": 200, "body": "ok", "headers": {}})

        with self.assertRaises(DeliveryUnresolved):
            dispatch_mt_primary_exchange(
                self.authorization.pk,
                ((self.unit_stage.pk, 0),),
                digest,
                send,
                request_snapshot=snapshot,
                request_codec="routed-slots-v4",
            )
        self.assertEqual(calls, [])


class MTSnapshotValidationTest(SimpleTestCase):
    """A request snapshot is immutable and never carries credentials."""

    BASE: ClassVar[dict] = {
        "method": "post",
        "url": "https://proxy/v1/chat/completions",
        "payload": {"model": "QWEN3.7-plus"},
        "codec": "routed-slots-v4",
        "slots": [{"unit_stage_id": "u", "source_slot": 0}],
    }

    def test_valid_snapshot_is_accepted(self) -> None:
        validate_request_snapshot(self.BASE)

    def test_missing_key_is_refused(self) -> None:
        for key in ("method", "url", "payload", "codec", "slots"):
            snapshot = {k: v for k, v in self.BASE.items() if k != key}
            with self.assertRaises(ValueError):
                validate_request_snapshot(snapshot)

    def test_credential_key_is_refused_at_any_depth(self) -> None:
        for key in ("authorization", "api_key", "key", "token", "headers", "auth"):
            snapshot = {**self.BASE, "parser": {"nested": {key: "secret"}}}
            with self.assertRaises(ValueError):
                validate_request_snapshot(snapshot)

    def test_self_referential_snapshot_is_refused(self) -> None:
        snapshot = dict(self.BASE)
        snapshot["payload"] = {"loop": snapshot}
        with self.assertRaises(ValueError):
            validate_request_snapshot(snapshot)

    def test_oversized_snapshot_is_refused(self) -> None:
        snapshot = {**self.BASE, "payload": {"data": "x" * (2 * 1024 * 1024)}}
        with self.assertRaises(ValueError):
            validate_request_snapshot(snapshot)

    def test_unsupported_value_is_refused(self) -> None:
        snapshot = {**self.BASE, "payload": {"object": object()}}
        with self.assertRaises(ValueError):
            validate_request_snapshot(snapshot)


class MTAuthorizationStateMachineTest(ViewTestCase):
    """observing → authorized → closed is enforced, never reopened."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.unit = self.translation.unit_set.filter(state=0).first()
        self.run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )
        self.authorization = enroll_mt_credit_scope(
            run=self.run, translation=self.translation, units=[self.unit]
        )

    def test_enrollment_starts_observing(self) -> None:
        self.assertEqual(self.authorization.state, "observing")

    def test_authorize_promotes_once(self) -> None:
        first = authorize_mt_credit_fallback(self.authorization.pk)
        self.assertEqual(first.state, "authorized")
        self.assertIsNotNone(first.authorized_at)
        second = authorize_mt_credit_fallback(self.authorization.pk)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.state, "authorized")

    def test_closed_authorization_is_never_reopened(self) -> None:
        authorize_mt_credit_fallback(self.authorization.pk)
        close_mt_credit_fallback(self.authorization.pk)
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.state, "closed")
        self.assertIsNotNone(self.authorization.closed_at)
        with self.assertRaises(DeliveryUnresolved):
            authorize_mt_credit_fallback(self.authorization.pk)

    def test_closing_twice_is_a_no_op(self) -> None:
        close_mt_credit_fallback(self.authorization.pk)
        close_mt_credit_fallback(self.authorization.pk)
        self.authorization.refresh_from_db()
        self.assertEqual(self.authorization.state, "closed")


class MTStageCrossPathArbitrationTest(ViewTestCase):
    """
    The two reservation paths deny each other on the same canonical work.

    Each test drives BOTH paths for real: the durable primary reservation
    through ``reserve_exchange`` and the MT stage enrollment through
    ``enroll_unit_stages``, over the same unit and the same canonical identity.
    Whichever runs first owns the unit of work; the other is refused instead of
    both passing an absence-of-claim check.
    """

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.unit = self.translation.unit_set.filter(state=0).first()
        self.assertIsNotNone(self.unit)
        self.run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )
        self.identity = canonical_identity(
            self.unit.pk,
            self.translation.language.code,
            source_decision_revision(self.unit),
        )

    def _authorization(self) -> ProducerMTFallbackAuthorization:
        return enroll_mt_credit_scope(
            run=self.run, translation=self.translation, units=[self.unit]
        )

    def test_unknown_durable_claim_blocks_stage_enrollment(self) -> None:
        """A durable primary reservation whose reply was lost owns the work."""
        reference = reserve_exchange(
            (self.identity,),
            "1" * 64,
            run_id=self.run.pk,
            request_codec="routed-slots-v4",
        )
        self.assertTrue(reference.can_send)
        # The request was paid but its reply never arrived: the exchange is
        # unresolved, so no other route may claim that unit of work.
        ProducerLLMExchange.objects.filter(pk=reference.id).update(state="unknown")
        with self.assertRaises(ValueError):
            enroll_unit_stages(self._authorization(), [self.unit])

    def test_sending_durable_claim_blocks_stage_enrollment(self) -> None:
        """An in-flight durable request is owned by its own lease."""
        reference = reserve_exchange(
            (self.identity,),
            "2" * 64,
            run_id=self.run.pk,
            request_codec="routed-slots-v4",
        )
        ProducerLLMExchange.objects.filter(pk=reference.id).update(state="sending")
        with self.assertRaises(ValueError):
            enroll_unit_stages(self._authorization(), [self.unit])

    def test_received_durable_claim_is_adopted_by_the_stage(self) -> None:
        """A recorded primary attempt is the unit's own evidence, not a rival."""
        reference = reserve_exchange(
            (self.identity,),
            "3" * 64,
            run_id=self.run.pk,
            request_codec="routed-slots-v4",
        )
        record_response(reference.id, json.dumps({"status": 200, "body": "ok"}))
        stage = enroll_unit_stages(self._authorization(), [self.unit])[0]
        self.assertIsNotNone(stage.primary_work)
        self.assertEqual(stage.primary_work.identity, self.identity)
        self.assertEqual(stage.primary_work.exchange_id, reference.id)

    def test_live_stage_claim_blocks_a_new_durable_reservation(self) -> None:
        """The reverse direction: a stage row that owns the unit fences it."""
        authorization = self._authorization()
        enroll_unit_stages(authorization, [self.unit])
        # The stage reserved and paid for this unit of work.
        unit_stage = ProducerMTFallbackUnit.objects.get(
            authorization_id=authorization.pk
        )
        exchange = ProducerLLMExchange.objects.create(
            request_digest="4" * 64,
            request_codec="routed-slots-v4",
            owner="o",
            lease_until=timezone.now() + timedelta(hours=1),
            state="received",
        )
        unit_stage.fallback_exchange = exchange
        unit_stage.fallback_state = "received"
        unit_stage.save(update_fields=["fallback_exchange", "fallback_state"])
        with self.assertRaises(DeliveryUnresolved):
            reserve_exchange(
                (self.identity,),
                "5" * 64,
                run_id=self.run.pk,
                request_codec="routed-slots-v4",
            )

    def test_enrolled_stage_row_does_not_block_its_own_primary(self) -> None:
        """The scope's own primary atomically attaches its enrolled evidence."""
        authorization = self._authorization()
        stage = enroll_unit_stages(authorization, [self.unit])[0]
        slots = ((stage.pk, 0),)
        snapshot, digest = request_fixture(slots, model="google/gemini-3.7-flash")
        reference = reserve_exchange(
            (self.identity,),
            digest,
            run_id=self.run.pk,
            request_codec="routed-slots-v4",
            mt_primary_context=(authorization.pk, slots, snapshot),
        )
        self.assertTrue(reference.can_send)
        self.assertEqual(ProducerLLMExchange.objects.count(), 1)
        stage.refresh_from_db()
        self.assertEqual(stage.primary_work.exchange_id, reference.id)
        self.assertEqual(stage.stage_members.get().exchange_id, reference.id)
