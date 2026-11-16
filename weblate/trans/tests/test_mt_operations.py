# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx2
from django.conf import settings
from django.db.models.deletion import ProtectedError
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from weblate.api.producer.mt import MTReceiptSerializer, operation_result
from weblate.auth.models import User
from weblate.machinery.base import (
    MachineTranslationError,
    MachineTranslationServiceError,
)
from weblate.machinery.llm import PartialLLMReplyError
from weblate.machinery.models import MACHINERY
from weblate.trans.models import Unit
from weblate.trans.models.judge import ProducerRun
from weblate.trans.models.llm_delivery import ProducerLLMExchange, ProducerLLMWork
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.mt_fallback import (
    ProducerMTFallbackAuthorization,
    ProducerMTFallbackUnit,
    ProducerMTStageMember,
)
from weblate.trans.models.producer_mt import (
    ProducerMTOperation,
    ProducerMTUnitAttempt,
)
from weblate.trans.mt_operations import (
    build_engine,
    process_mt_operation,
    reconcile_mt_operation,
    recover_expired_mt_operations,
)
from weblate.trans.tasks import (
    _producer_run_dispatch_kwargs,
    auto_translate,
    producer_mt_batch,
    publish_producer_run_dispatch,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.util import join_plural
from weblate.wizard_auth.grants import WizardAuthorizationRequired
from weblate.wizard_auth.models import DelegationFamily


def revision(unit):
    return hashlib.sha256(
        json.dumps(
            {
                "id": unit.pk,
                "source": unit.get_source_plurals(),
                "context": unit.context,
                "target": unit.get_target_plurals(),
                "state": int(unit.state),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


@override_settings(
    WEBLATE_MACHINERY=(
        *settings.WEBLATE_MACHINERY,
        "weblate_customization.machinery.RoutedLLMTranslation",
    )
)
class ProducerMTTest(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.project.machinery_settings = {"openrouter": {"key": "test-fixture-only"}}
        self.project.save(update_fields=["machinery_settings"])
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.unit = self.get_unit()
        Unit.objects.filter(pk=self.unit.pk).update(target="", state=0)
        self.unit.refresh_from_db()
        self.url = f"/api/producer/projects/{self.project.slug}/mt/batches/"
        self.key = str(uuid4())
        self.payload = {
            "component": self.component.slug,
            "language": "cs",
            "engine": "openrouter",
            "threshold": 80,
            "units": [{"unit_id": self.unit.pk, "revision": revision(self.unit)}],
        }

    def submit(self, payload=None, key=None):
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            return self.api.post(
                self.url,
                payload or self.payload,
                format="json",
                HTTP_IDEMPOTENCY_KEY=key or self.key,
            )

    def test_lost_accept_response_reuses_run(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202)
        second = self.submit()
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.data["run_id"], second.data["run_id"])
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")

    def test_same_key_different_payload_is_conflict(self):
        self.assertEqual(self.submit().status_code, 202)
        payload = {**self.payload, "threshold": 90}
        self.assertEqual(self.submit(payload).status_code, 409)

    def test_requires_key(self):
        self.assertEqual(
            self.api.post(self.url, self.payload, format="json").status_code, 400
        )

    def test_nonempty_target_is_not_admitted(self):
        Unit.objects.filter(pk=self.unit.pk).update(target="Ready", state=20)
        response = self.submit()
        self.assertEqual(response.status_code, 409)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Ready")

    def test_stale_revision_is_not_admitted(self):
        self.payload["units"][0]["revision"] = "0" * 64
        self.assertEqual(self.submit().status_code, 409)

    def test_actor_bound_observation(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202)
        self.api.force_authenticate(None)
        response = self.api.get(self.url + self.key + "/")
        self.assertIn(response.status_code, {401, 403})

    def test_more_than_twenty_units_are_rejected(self):
        self.payload["units"] *= 21
        self.assertEqual(self.submit().status_code, 400)

    def test_capability_is_visible(self):
        response = self.api.get("/api/producer/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["capabilities"].get("mt_async_batches"), 1)

    def operation(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202)
        return ProducerMTOperation.objects.get(run_id=response.data["run_id"])

    def engine(self, *, cached=False, mutate=False):
        unit_id = self.unit.pk
        calls = []

        class Engine:
            def _prepare_translation_download(self, source, target, sources, threshold):
                return SimpleNamespace(
                    output=[
                        ([{"text": "Cached", "quality": 90}] if cached else [])
                        for _ in sources
                    ]
                )

            def download_multiple_translations(
                self, source, target, sources, user=None, threshold=80
            ):
                calls.extend(u.pk for _, u in sources)
                if mutate:
                    Unit.objects.filter(pk=unit_id).update(target="Human", state=20)
                return {
                    text: [{"text": "Translated", "quality": 90}] for text, _ in sources
                }

            def _translate_sources(
                self, source, target, sources, user=None, threshold=80
            ):
                result = self.download_multiple_translations(
                    source, target, sources, user, threshold
                )
                return [result[text] for text, _ in sources]

        return Engine(), calls

    def test_duplicate_delivery_does_not_call_provider_twice(self):
        operation = self.operation()
        engine, paid = self.engine()
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        operation.refresh_from_db()
        self.assertEqual(self.unit.target, "Translated\n")
        self.assertEqual(operation.state, "completed")
        self.assertEqual(paid, [self.unit.pk])

    def delegated_operation(self):
        operation = self.operation()
        family = DelegationFamily.objects.create(
            user=self.user,
            client_id="synthetic",
            expires=timezone.now() + timedelta(hours=24),
        )
        run = operation.run
        run.execution_options = {
            **run.execution_options,
            "_wizard_delegation": str(family.pk),
        }
        run.save(update_fields=["execution_options"])
        return operation, family

    @override_settings(WIZARD_SSO_ENABLED=True)
    def test_delegation_pause_before_dispatch_releases_only_unsent_after_explicit_reauthorization(
        self,
    ):
        operation, family = self.delegated_operation()
        family.revoked = True
        family.save(update_fields=["revoked"])
        engine, paid = self.engine()
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            result = process_mt_operation(operation.run_id)
        self.assertEqual(result["state"], "auth_required")
        self.assertEqual(operation.units.get().state, "pending")
        self.assertEqual(paid, [])
        renewed = DelegationFamily.objects.create(
            user=self.user,
            client_id="synthetic",
            expires=timezone.now() + timedelta(hours=24),
        )
        groups = reconcile_mt_operation(operation.pk, reauthorize_family=renewed)
        self.assertEqual(groups["never_sent"], [self.unit.pk])
        self.assertEqual(operation.units.get().state, "released")
        self.assertEqual(str(operation.key), self.key)
        self.assertEqual(operation.actor_id, self.user.pk)
        response = self.submit(key=str(uuid4()))
        self.assertEqual(response.status_code, 202)
        remaining = ProducerMTOperation.objects.get(run_id=response.data["run_id"])
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            self.assertEqual(
                process_mt_operation(remaining.run_id)["state"], "completed"
            )
        self.assertEqual(paid, [self.unit.pk])

    @override_settings(WIZARD_SSO_ENABLED=True)
    def test_delegation_refusal_at_transport_guard_is_not_a_paid_unknown(self):
        operation, family = self.delegated_operation()
        engine = build_engine(operation)

        def refuse(*args, **kwargs):
            DelegationFamily.objects.filter(pk=family.pk).update(revoked=True)
            msg = "No transport was admitted"
            raise WizardAuthorizationRequired(msg)

        with (
            patch.object(
                engine, "download_pending_translations", side_effect=refuse
            ) as no_transport,
            patch("weblate.trans.mt_operations.build_engine", return_value=engine),
        ):
            result = process_mt_operation(operation.run_id)
        no_transport.assert_called_once()
        self.assertEqual(result["state"], "auth_required")
        self.assertEqual(operation.units.get().state, "pending")

    @override_settings(WIZARD_SSO_ENABLED=True)
    def test_reply_after_logout_is_saved_and_applied_only_after_same_actor_reauthorization(
        self,
    ):
        operation, family = self.delegated_operation()
        engine, paid = self.engine()
        translate = engine._translate_sources  # ruff: ignore[private-member-access]

        def reply(*args, **kwargs):
            values = translate(*args, **kwargs)
            DelegationFamily.objects.filter(pk=family.pk).update(revoked=True)
            return values

        engine._translate_sources = reply  # ruff: ignore[private-member-access]
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(operation.run_id)
        self.assertEqual(operation.units.get().state, "received")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        renewed = DelegationFamily.objects.create(
            user=self.user,
            client_id="synthetic",
            expires=timezone.now() + timedelta(hours=24),
        )
        groups = reconcile_mt_operation(operation.pk, reauthorize_family=renewed)
        self.assertEqual(groups["ready"], [self.unit.pk])
        self.assertEqual(paid, [self.unit.pk])

    @override_settings(WIZARD_SSO_ENABLED=True)
    def test_logout_between_chunks_keeps_reply_and_unsent_tail_separate(self):
        other = self.unit.translation.unit_set.exclude(pk=self.unit.pk).first()
        Unit.objects.filter(pk=other.pk).update(
            target="", state=0, source=self.unit.source
        )
        other.refresh_from_db()
        self.payload["units"].append({"unit_id": other.pk, "revision": revision(other)})
        operation, family = self.delegated_operation()
        engine, paid = self.engine()
        engine.batch_size = 1
        translate = engine._translate_sources  # ruff: ignore[private-member-access]

        def reply(*args, **kwargs):
            values = translate(*args, **kwargs)
            DelegationFamily.objects.filter(pk=family.pk).update(revoked=True)
            return values

        engine._translate_sources = reply  # ruff: ignore[private-member-access]
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            self.assertEqual(
                process_mt_operation(operation.run_id)["state"], "auth_required"
            )
        self.assertEqual(len(paid), 1)
        self.assertCountEqual(
            operation.units.values_list("state", flat=True), ["received", "pending"]
        )

    @override_settings(WIZARD_SSO_ENABLED=True)
    def test_reauthorization_cannot_adopt_legacy_or_another_actor_and_preserves_unknown(
        self,
    ):
        operation, family = self.delegated_operation()
        other = User.objects.create_user(
            "other-delegated-actor", "other@example.org", "synthetic-long-password"
        )
        foreign = DelegationFamily.objects.create(
            user=other,
            client_id="synthetic",
            expires=timezone.now() + timedelta(hours=24),
        )
        with self.assertRaises(PermissionDenied):
            reconcile_mt_operation(operation.pk, reauthorize_family=foreign)
        run = operation.run
        run.execution_options = {}
        run.save(update_fields=["execution_options"])
        with self.assertRaises(PermissionDenied):
            reconcile_mt_operation(operation.pk, reauthorize_family=family)
        run.execution_options = {"_wizard_delegation": str(family.pk)}
        run.save(update_fields=["execution_options"])
        operation.state = "provider_unknown"
        operation.save(update_fields=["state"])
        operation.units.update(state="provider_unknown")
        groups = reconcile_mt_operation(operation.pk, reauthorize_family=family)
        self.assertEqual(groups["paid_unknown"], [self.unit.pk])
        self.assertEqual(groups["never_sent"], [])

    def test_cached_answer_is_applied_without_provider_call(self):
        operation = self.operation()
        operation.units.update(state="requesting")
        engine, paid = self.engine(cached=True)
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Cached\n")
        self.assertEqual(paid, [])

    def test_concurrent_target_change_is_not_overwritten(self):
        operation = self.operation()
        engine, paid = self.engine(mutate=True)
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Human")
        self.assertEqual(operation.units.get().state, "skipped_nonempty")
        self.assertEqual(paid, [self.unit.pk])

    def test_unknown_provider_result_cannot_be_resubmitted_with_new_key(self):
        operation = self.operation()
        operation.units.update(state="requesting")
        engine, paid = self.engine()
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        operation.refresh_from_db()
        self.assertEqual(operation.state, "provider_unknown")
        self.assertEqual(self.submit(key=str(uuid4())).status_code, 409)
        self.assertEqual(paid, [])

    def test_cancellation_before_dispatch_does_not_call_provider(self):
        operation = self.operation()
        run = operation.run
        run.status = "cancel_requested"
        run.save(update_fields=["status"])
        engine, paid = self.engine()
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(run.pk))
        self.unit.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(paid, [])
        self.assertEqual(self.unit.target, "")
        self.assertEqual(run.status, "cancelled")

    def test_component_locked_during_provider_call_is_not_written(self):
        operation = self.operation()
        engine, _paid = self.engine()
        original = engine.download_multiple_translations

        def lock(*args, **kwargs):
            type(self.component).objects.filter(pk=self.component.pk).update(
                locked=True
            )
            return original(*args, **kwargs)

        engine.download_multiple_translations = lock
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        self.assertEqual(operation.units.get().state, "failed")

    def test_unknown_overwrite_option_is_rejected(self):
        self.assertEqual(
            self.submit({**self.payload, "overwrite_existing": True}).status_code, 400
        )

    def test_deleting_actor_preserves_paid_intent(self):
        operation = self.operation()
        operation.units.update(state="requesting")
        self.user.delete()
        self.assertTrue(
            ProducerMTUnitAttempt.objects.filter(
                unit=self.unit, state="requesting"
            ).exists()
        )
        operation.refresh_from_db()
        self.assertIsNone(operation.actor_id)

    def test_real_engine_populates_cache_before_apply(self):
        operation = self.operation()
        engine = build_engine(operation)
        sources = [(self.unit.get_source_plurals()[0], self.unit)]
        with (
            patch.object(
                engine,
                "download_pending_translations",
                return_value={
                    sources[0][0]: [{"text": "Cached response", "quality": 90}]
                },
            ) as outbound,
            patch("weblate.trans.mt_operations.build_engine", return_value=engine),
            patch(
                "weblate.trans.mt_operations.AutoTranslate.update",
                side_effect=RuntimeError("synthetic crash before apply"),
            ),
            self.assertRaises(RuntimeError),
        ):
            process_mt_operation(str(operation.run_id))
        self.assertEqual(outbound.call_count, 1)
        cached = engine._prepare_translation_download("en", "cs", sources, 80)  # ruff: ignore[private-member-access]
        self.assertTrue(cached.output[0])
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")

    def test_plural_unit_is_rejected_without_singular_truncation(self):
        Unit.objects.filter(pk=self.unit.pk).update(source=join_plural(["One", "Many"]))
        self.unit.refresh_from_db()
        self.payload["units"][0]["revision"] = revision(self.unit)
        self.assertEqual(self.submit().status_code, 409)

    def test_active_delivery_fences_reentrant_worker(self):
        operation = self.operation()
        engine, paid = self.engine()
        original = engine.download_multiple_translations

        def nested(*args, **kwargs):
            self.assertEqual(
                process_mt_operation(str(operation.run_id))["state"], "running"
            )
            return original(*args, **kwargs)

        engine.download_multiple_translations = nested
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.assertEqual(paid, [self.unit.pk])

    def test_restart_applies_durable_answer_without_provider(self):
        operation = self.operation()
        operation.units.update(state="received", answer="Saved")
        engine, paid = self.engine()
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Saved\n")
        self.assertEqual(paid, [])

    def test_different_key_cannot_bypass_pending_reservation(self):
        self.operation()
        self.assertEqual(self.submit(key=str(uuid4())).status_code, 409)

    def test_dispatch_uses_existing_async_outbox(self):
        operation = self.operation()
        with patch.object(producer_mt_batch, "apply_async") as publish:
            self.assertTrue(publish_producer_run_dispatch(run_id=operation.run_id))
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(
            publish.call_args.kwargs["kwargs"]["run_id"], str(operation.run_id)
        )
        self.assertEqual(set(publish.call_args.kwargs["kwargs"]), {"run_id"})

    def test_wrong_translation_is_rejected(self):
        self.payload["language"] = "en"
        self.assertEqual(self.submit().status_code, 403)

    def test_receipt_polling_does_not_exhaust_paid_admission_budget(self):
        self.operation()
        for _ in range(60):
            self.assertEqual(self.api.get(self.url + self.key + "/").status_code, 200)

    def test_real_engine_timeout_is_one_provider_attempt(self):
        engine = build_engine(self.operation())
        engine.max_retry_delay = 0
        with (
            patch(
                "weblate.machinery.base.fetch_url",
                side_effect=httpx2.ReadTimeout("synthetic timeout"),
            ) as direct,
            patch(
                "weblate.machinery.base.fetch_validated_url",
                side_effect=httpx2.ReadTimeout("synthetic timeout"),
            ) as validated,
            self.assertRaises(httpx2.ReadTimeout),
        ):
            engine.request(
                "POST", "https://openrouter.ai/api/v1/chat/completions", json={}
            )
        self.assertEqual(direct.call_count + validated.call_count, 1)

    def test_real_engine_malformed_reply_is_not_split_and_repaid(self):
        engine = build_engine(self.operation())
        with (
            patch.object(
                engine,
                "_fetch_llm_batch",
                side_effect=MachineTranslationError("synthetic malformed reply"),
            ) as outbound,
            self.assertRaises(MachineTranslationError),
        ):
            engine.download_multiple_translations(
                "en", "cs", [("One", None), ("Two", None)], threshold=80
            )
        self.assertEqual(outbound.call_count, 1)

    def test_cancelled_by_drain_before_delivery_does_not_start(self):
        operation = self.operation()
        type(operation.run).objects.filter(pk=operation.run_id).update(
            status="cancelled"
        )
        engine, paid = self.engine()
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.assertEqual(paid, [])
        operation.run.refresh_from_db()
        self.assertEqual(operation.run.status, "cancelled")

    def test_cancel_during_response_preserves_answer_without_apply(self):
        operation = self.operation()
        engine, paid = self.engine()
        original = engine.download_multiple_translations

        def cancel(*args, **kwargs):
            type(operation.run).objects.filter(pk=operation.run_id).update(
                status="cancel_requested"
            )
            return original(*args, **kwargs)

        engine.download_multiple_translations = cancel
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        operation.run.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        self.assertEqual(operation.units.get().answer, "Translated")
        self.assertEqual(operation.run.status, "cancelled")
        self.assertEqual(paid, [self.unit.pk])

    def test_cleared_lease_is_safe_for_overlapping_recovery(self):
        operation = self.operation()
        type(operation).objects.filter(pk=operation.pk).update(
            state="running", lease_until=timezone.now()
        )
        queryset = type(operation).objects
        original = queryset.select_for_update

        def concurrently_cleared(*args, **kwargs):
            queryset.filter(pk=operation.pk).update(lease_until=None)
            return original(*args, **kwargs)

        with patch.object(
            queryset, "select_for_update", side_effect=concurrently_cleared
        ):
            recover_expired_mt_operations()

    def test_owner_with_cleared_lease_can_resume_without_overwriting(self):
        operation = self.operation()
        type(operation).objects.filter(pk=operation.pk).update(
            state="running", lease_owner="expired-owner", lease_until=None
        )
        engine, paid = self.engine()
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            result = process_mt_operation(str(operation.run_id))
        self.assertEqual(result["state"], "completed")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Translated\n")
        self.assertEqual(paid, [self.unit.pk])

    def test_failed_unpublished_dispatch_is_visible_and_same_key_can_retry(self):
        operation = self.operation()
        with patch.object(
            producer_mt_batch,
            "apply_async",
            side_effect=RuntimeError("synthetic broker unavailable"),
        ):
            for _ in range(5):
                publish_producer_run_dispatch(run_id=operation.run_id)
        receipt = self.api.get(self.url + self.key + "/")
        self.assertEqual(receipt.data["state"], "failed")
        self.assertTrue(receipt.data["retryable"])
        retry = self.submit()
        self.assertEqual(retry.status_code, 202)
        self.assertEqual(retry.data["run_id"], str(operation.run_id))
        self.assertEqual(retry.data["state"], "accepted")

    def test_mt_dispatch_cannot_execute_as_legacy_auto_translate(self):
        operation = self.operation()
        with (
            patch.object(auto_translate, "apply_async") as legacy,
            patch.object(producer_mt_batch, "apply_async"),
        ):
            publish_producer_run_dispatch(run_id=operation.run_id)
        self.assertEqual(legacy.call_count, 0)

    def test_legacy_outbox_cannot_republish_mt_receipt_as_ordinary_translation(self):
        operation = self.operation()
        # This helper intentionally retains the exact rollback baseline behavior.
        self.assertIsNone(_producer_run_dispatch_kwargs(operation.run))

    def test_real_engine_partial_reply_keeps_prefix_without_paid_tail_rescue(self):
        engine = build_engine(self.operation())
        prefix = {"One": [{"text": "Eins", "quality": 90}]}
        with patch.object(
            engine, "_fetch_llm_batch", side_effect=PartialLLMReplyError(prefix, 1)
        ) as outbound:
            result = engine.download_multiple_translations(
                "en", "cs", [("One", None), ("Two", None)], threshold=80
            )
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(result["One"], prefix["One"])
        self.assertEqual(result["Two"], [])

    def test_real_engine_cached_prefix_survives_later_internal_batch_failure(self):
        other = self.unit.translation.unit_set.exclude(pk=self.unit.pk).first()
        Unit.objects.filter(pk=other.pk).update(
            source=self.unit.source, target="", state=0, context="other-cache-identity"
        )
        other.refresh_from_db()
        self.payload["units"].append({"unit_id": other.pk, "revision": revision(other)})
        operation = self.operation()
        engine = build_engine(operation)
        calls = []

        def provider(source, target, sources, occurrences):
            calls.extend(u.pk for _, u in sources)
            if len(calls) > 1:
                msg = "synthetic later batch failure"
                raise MachineTranslationError(msg)
            return {
                text: [{"text": "Saved prefix", "quality": 90}] for text, _ in sources
            }

        with (
            patch.object(engine, "_fetch_llm_batch", side_effect=provider),
            patch("weblate.trans.mt_operations.build_engine", return_value=engine),
        ):
            process_mt_operation(str(operation.run_id))
        applied = list(
            operation.units.filter(state="applied").values_list("unit_id", flat=True)
        )
        self.assertEqual(len(applied), 1)
        self.assertEqual(Unit.objects.get(pk=applied[0]).target, "Saved prefix\n")
        self.assertEqual(operation.units.filter(state="provider_unknown").count(), 1)
        self.assertEqual(len(calls), 2)

    def test_reupgrade_republishes_only_pending_mt_envelope(self):
        operation = self.operation()
        run = operation.run
        run.dispatch_published_at = timezone.now() - timedelta(seconds=601)
        run.save(update_fields=["dispatch_published_at"])
        recover_expired_mt_operations()
        run.refresh_from_db()
        self.assertIsNone(run.dispatch_published_at)
        with (
            patch.object(auto_translate, "apply_async") as legacy,
            patch.object(producer_mt_batch, "apply_async") as durable,
        ):
            publish_producer_run_dispatch(run_id=run.pk)
        self.assertEqual(legacy.call_count, 0)
        self.assertEqual(durable.call_count, 1)

    def test_real_engine_usage_stays_linked_to_receipt_run(self):
        operation = self.operation()
        self.assertEqual(build_engine(operation).usage_run_id, str(operation.run_id))

    def test_engine_batch_size_limits_each_authorized_outbound(self):
        other = self.unit.translation.unit_set.exclude(pk=self.unit.pk).first()
        Unit.objects.filter(pk=other.pk).update(
            target="", state=0, source=self.unit.source
        )
        other.refresh_from_db()
        self.payload["units"].append({"unit_id": other.pk, "revision": revision(other)})
        operation = self.operation()
        engine, _paid = self.engine()
        engine.batch_size = 1
        sizes = []
        original = engine._translate_sources  # ruff: ignore[private-member-access]

        def translate(*args, **kwargs):
            sizes.append(len(args[2]))
            return original(*args, **kwargs)

        engine._translate_sources = translate  # ruff: ignore[private-member-access]
        with patch("weblate.trans.mt_operations.build_engine", return_value=engine):
            process_mt_operation(str(operation.run_id))
        self.assertEqual(sizes, [1, 1])

    def test_unrequested_tail_stays_pending_after_unknown_provider_result(self):
        other = self.unit.translation.unit_set.exclude(pk=self.unit.pk).first()
        Unit.objects.filter(pk=other.pk).update(
            target="", state=0, source=self.unit.source
        )
        other.refresh_from_db()
        self.payload["units"].append({"unit_id": other.pk, "revision": revision(other)})
        operation = self.operation()
        engine, _paid = self.engine()
        engine.batch_size = 1
        with (
            patch.object(
                engine,
                "_translate_sources",
                side_effect=RuntimeError("synthetic lost response"),
            ) as outbound,
            patch("weblate.trans.mt_operations.build_engine", return_value=engine),
        ):
            process_mt_operation(str(operation.run_id))
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(operation.units.filter(state="provider_unknown").count(), 1)
        self.assertEqual(operation.units.filter(state="pending").count(), 1)


class _CreditRefusingEngine:
    """Engine that refuses with insufficient credit on every call."""

    name = "OpenRouter"
    batch_size = 20
    batch_concurrency = 1
    usage_run_id: str | None = None

    def __init__(
        self, reason_code: str = "insufficient-credit", engine_id: str = "openrouter"
    ) -> None:
        self.reason_code = reason_code
        self.engine_id = engine_id
        self.calls: list[list[int]] = []

    def get_identifier(self) -> str:
        return self.engine_id

    def _refuse(self, sources):
        self.calls.extend(u.pk for _, u in sources)
        msg = "insufficient credit"
        raise MachineTranslationServiceError(
            msg,
            reason_code=self.reason_code,
            safe_message="The credit balance is spent.",
        )

    def _translate_sources(self, source, target, sources, user=None, threshold=80):
        self._refuse(sources)

    def _translate_sources_plan(self, source, target, sources, user=None, threshold=80):
        self._refuse(sources)

    def translate_mt_fallback_slots(
        self, source, target, ordered_slots, user=None, threshold=80
    ):
        self._refuse([(text, unit) for _stage, _slot, text, unit in ordered_slots])

    def _prepare_translation_download(self, source, target, sources, threshold):
        return SimpleNamespace(output=[[] for _ in sources])


class _SuccessfulFallbackEngine:
    """Fallback engine that returns translations."""

    name = "LiteLLM"
    batch_size = 20
    batch_concurrency = 1
    usage_run_id: str | None = None

    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    @classmethod
    def get_identifier(cls) -> str:
        return "litellm"

    def _translate_sources(self, source, target, sources, user=None, threshold=80):
        self.calls.extend(u.pk for _, u in sources)
        return [[{"text": "Fallback translated", "quality": 90}] for _ in sources]

    def _translate_sources_plan(self, source, target, sources, user=None, threshold=80):
        self.calls.extend(u.pk for _, u in sources)
        return SimpleNamespace(
            output=[[{"text": "Fallback translated", "quality": 90}] for _ in sources]
        )

    def translate_mt_fallback_slots(
        self, source, target, ordered_slots, user=None, threshold=80
    ):
        self.calls.extend(unit.pk for _stage, _slot, _text, unit in ordered_slots)
        return [[{"text": "Fallback translated", "quality": 90}] for _ in ordered_slots]

    def _prepare_translation_download(self, source, target, sources, threshold):
        return SimpleNamespace(output=[[] for _ in sources])


class ProducerMTFallbackTest(ViewTestCase):
    """Durable MT orchestrates a fallback stage after confirmed credit refusal."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.project.machinery_settings = {
            "openrouter": {
                "key": "test-fixture-only",
                "mt_fallback": {
                    "engine": "litellm",
                    "routing": {"*": "QWEN3.7-plus"},
                },
            },
            "litellm": {"key": "ll-key", "routing": {"*": "QWEN3.7-plus"}},
        }
        self.project.save(update_fields=["machinery_settings"])
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.unit = self.get_unit()
        Unit.objects.filter(pk=self.unit.pk).update(target="", state=0)
        self.unit.refresh_from_db()
        self.url = f"/api/producer/projects/{self.project.slug}/mt/batches/"
        self.key = str(uuid4())
        self.payload = {
            "component": self.component.slug,
            "language": "cs",
            "engine": "openrouter",
            "threshold": 80,
            "units": [{"unit_id": self.unit.pk, "revision": revision(self.unit)}],
        }

    def submit(self, payload=None, key=None):
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            return self.api.post(
                self.url,
                payload or self.payload,
                format="json",
                HTTP_IDEMPOTENCY_KEY=key or self.key,
            )

    def operation(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202)
        return ProducerMTOperation.objects.get(run_id=response.data["run_id"])

    def test_credit_exception_without_receipt_never_authorizes_fallback(self):
        operation = self.operation()
        primary = _CreditRefusingEngine("insufficient-credit")
        fallback = _SuccessfulFallbackEngine()
        machinery = {
            "openrouter": lambda _cfg: primary,
            "litellm": lambda _cfg: fallback,
        }
        with (
            patch("weblate.trans.mt_operations.MACHINERY", machinery),
            patch("weblate.machinery.models.MACHINERY", machinery),
        ):
            result = process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        self.assertEqual(result["state"], "provider_unknown")
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 0)

    def test_quota_refusal_does_not_trigger_fallback(self):
        operation = self.operation()
        primary = _CreditRefusingEngine("quota-exhausted")
        fallback = _SuccessfulFallbackEngine()
        machinery = {
            "openrouter": lambda _cfg: primary,
            "litellm": lambda _cfg: fallback,
        }
        with (
            patch("weblate.trans.mt_operations.MACHINERY", machinery),
            patch("weblate.machinery.models.MACHINERY", machinery),
        ):
            result = process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        self.assertNotEqual(result["state"], "completed")
        self.assertEqual(len(fallback.calls), 0)

    def test_unjournaled_refusal_cannot_start_fallback(self):
        operation = self.operation()
        primary = _CreditRefusingEngine("insufficient-credit")
        fallback = _CreditRefusingEngine("insufficient-credit", engine_id="litellm")
        machinery = {
            "openrouter": lambda _cfg: primary,
            "litellm": lambda _cfg: fallback,
        }
        with (
            patch("weblate.trans.mt_operations.MACHINERY", machinery),
            patch("weblate.machinery.models.MACHINERY", machinery),
        ):
            result = process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        self.assertNotEqual(result["state"], "completed")
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 0)

    def test_no_fallback_mapping_configured(self):
        # Remove mt_fallback mapping.
        self.project.machinery_settings = {
            "openrouter": {"key": "test-fixture-only"},
        }
        self.project.save(update_fields=["machinery_settings"])
        operation = self.operation()
        primary = _CreditRefusingEngine("insufficient-credit")
        machinery = {"openrouter": lambda _cfg: primary}
        with (
            patch("weblate.trans.mt_operations.MACHINERY", machinery),
            patch("weblate.machinery.models.MACHINERY", machinery),
        ):
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")

    def test_redelivery_of_unknown_work_does_not_send(self):
        """An unknown primary without a receipt cannot authorize fallback."""
        operation = self.operation()
        primary = _CreditRefusingEngine("insufficient-credit")
        fallback = _SuccessfulFallbackEngine()
        machinery = {
            "openrouter": lambda _cfg: primary,
            "litellm": lambda _cfg: fallback,
        }
        with (
            patch("weblate.trans.mt_operations.MACHINERY", machinery),
            patch("weblate.machinery.models.MACHINERY", machinery),
        ):
            process_mt_operation(str(operation.run_id))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        authorizations = ProducerMTFallbackAuthorization.objects.filter(
            run_id=operation.run_id
        )
        self.assertEqual(authorizations.count(), 1)
        authorization = authorizations.first()
        self.assertIsNotNone(authorization)
        assert authorization is not None
        # The fake fallback engine does not travel the public dispatch path, so
        # no fallback exchange is materialized for it; the public
        # ProducerMTFallbackPublicTest covers the real exchange binding.
        self.assertFalse(
            ProducerMTFallbackUnit.objects.filter(
                authorization_id=authorization.pk,
                fallback_exchange_id__isnull=False,
            ).exists()
        )

        # Redelivery of a terminal unknown operation must not dispatch again.

        primary2 = _CreditRefusingEngine("insufficient-credit")
        fallback2 = _SuccessfulFallbackEngine()
        machinery2 = {
            "openrouter": lambda _cfg: primary2,
            "litellm": lambda _cfg: fallback2,
        }
        with (
            patch("weblate.trans.mt_operations.MACHINERY", machinery2),
            patch("weblate.machinery.models.MACHINERY", machinery2),
        ):
            process_mt_operation(str(operation.run_id))
        # Still exactly one authorization and one slot per unit of work;
        # a redelivery never opens a second fallback charge.
        self.assertEqual(
            ProducerMTFallbackAuthorization.objects.filter(
                run_id=operation.run_id
            ).count(),
            1,
        )
        authorization = ProducerMTFallbackAuthorization.objects.get(
            run_id=operation.run_id
        )
        slots = ProducerMTFallbackUnit.objects.filter(authorization_id=authorization.pk)
        self.assertEqual(slots.count(), 1)

    def test_operation_result_omits_unjournaled_fallback(self):
        """An unjournaled exception must not invent fallback provenance."""
        operation = self.operation()
        primary = _CreditRefusingEngine("insufficient-credit")
        fallback = _SuccessfulFallbackEngine()
        machinery = {
            "openrouter": lambda _cfg: primary,
            "litellm": lambda _cfg: fallback,
        }
        with (
            patch("weblate.trans.mt_operations.MACHINERY", machinery),
            patch("weblate.machinery.models.MACHINERY", machinery),
        ):
            process_mt_operation(str(operation.run_id))
        operation.refresh_from_db()
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "")
        result = operation_result(operation)
        self.assertNotIn("fallback", result)
        with self.assertRaises(ProtectedError):
            operation.delete()
        # Primary-only operations omit the fallback key entirely.
        primary_op = ProducerMTOperation.objects.create(
            actor=self.user,
            project=self.project,
            translation=self.translation,
            run=ProducerRun.objects.create(
                actor=self.user,
                scope_type=ProducerRun.ScopeType.COMPONENT,
                scope_id=str(self.component.pk),
                scope_label=str(self.component),
                scope_path=self.component.get_absolute_url(),
                requested_mode="translate",
                cap=1,
            ),
            key=uuid4(),
            request_hash="a" * 64,
            payload={"engine": "openrouter", "threshold": 80},
        )
        primary_result = operation_result(primary_op)
        self.assertNotIn("fallback", primary_result)


def _chat_completion(text: str):
    """Build a successful chat completion payload for the mocked transport."""
    return httpx2.Response(
        200,
        json={
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 2,
                "total_tokens": 11,
            },
        },
    )


def _credit_refusal() -> httpx2.Response:
    """Build the captured primary credit refusal response (HTTP 402)."""
    return httpx2.Response(
        402,
        json={
            "error": {
                "message": "The credit balance is spent.",
                "type": "insufficient_credits",
                "code": "insufficient_credits",
            }
        },
    )


def _fallback_chat_completion(method: str, url: str, **kwargs) -> httpx2.Response:
    """Answer every fallback slot with a usable, occurrence-aligned reply."""
    payload = kwargs.get("json") or {}
    request = httpx2.Request(method, url, json=payload)
    inputs = json.loads(json.loads(request.content)["messages"][-1]["content"])[
        "strings"
    ]
    content = json.dumps(
        {
            "translations": {
                item["id"]: {"text": ["Fallback translated"], "placeholders": {}}
                for item in inputs
            }
        }
    )
    return httpx2.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 2,
                "total_tokens": 11,
            },
        },
        request=request,
    )


@override_settings(
    WEBLATE_MACHINERY=(
        *settings.WEBLATE_MACHINERY,
        "weblate_customization.machinery.RoutedLLMTranslation",
        "weblate_customization.machinery.RoutedLiteLLMTranslation",
    )
)
class ProducerMTFallbackPublicTest(ViewTestCase):
    """
    Exercise the durable fallback stage through the real public machinery HTTP path.

    A captured primary credit refusal authorizes exactly one fallback
    transport whose usage binds the returned fallback exchange.
    """

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.project.machinery_settings = {
            "openrouter": {
                "key": "or-key",
                "routing": {"*": "google/gemini-3.7-flash"},
                "mt_fallback": {
                    "engine": "litellm",
                    "routing": {"*": "QWEN3.7-plus"},
                },
            },
            "litellm": {
                "key": "ll-key",
                "routing": {"*": "QWEN3.7-plus"},
            },
        }
        self.project.save(update_fields=["machinery_settings"])
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.unit = self.get_unit()
        Unit.objects.filter(pk=self.unit.pk).update(target="", state=0)
        self.unit.refresh_from_db()
        self.url = f"/api/producer/projects/{self.project.slug}/mt/batches/"
        self.key = str(uuid4())
        self.payload = {
            "component": self.component.slug,
            "language": "cs",
            "engine": "openrouter",
            "threshold": 80,
            "units": [{"unit_id": self.unit.pk, "revision": revision(self.unit)}],
        }

    def submit(self):
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            response = self.api.post(
                self.url, self.payload, format="json", HTTP_IDEMPOTENCY_KEY=self.key
            )
        self.assertEqual(response.status_code, 202)
        return ProducerMTOperation.objects.get(run_id=response.data["run_id"])

    def submit_pair(self):
        # Ordered and source-shaped: the pair must be two single-form units of
        # the same translation, picked deterministically so the stored target
        # does not depend on the database's row order.
        other = next(
            (
                unit
                for unit in self.unit.translation.unit_set.exclude(
                    pk=self.unit.pk
                ).order_by("pk")
                if len(unit.get_source_plurals()) == 1
            ),
            None,
        )
        self.assertIsNotNone(other)
        Unit.objects.filter(pk=other.pk).update(target="", state=0)
        other.refresh_from_db()
        payload = {
            **self.payload,
            "units": [
                {"unit_id": self.unit.pk, "revision": revision(self.unit)},
                {"unit_id": other.pk, "revision": revision(other)},
            ],
        }
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            response = self.api.post(
                self.url, payload, format="json", HTTP_IDEMPOTENCY_KEY=self.key
            )
        self.assertEqual(response.status_code, 202, response.data)
        return ProducerMTOperation.objects.get(run_id=response.data["run_id"]), other

    @staticmethod
    def primary_reply(method: str, url: str, **kwargs) -> httpx2.Response:
        payload = kwargs.get("json") or {}
        request = httpx2.Request(method, url, json=payload)
        inputs = json.loads(json.loads(request.content)["messages"][-1]["content"])[
            "strings"
        ]
        content = json.dumps(
            {
                "translations": {
                    item["id"]: {"text": ["Primary translated"], "placeholders": {}}
                    for item in inputs
                }
            }
        )
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 2},
            },
            request=request,
        )

    def test_primary_reservation_binds_full_scope_before_transport(self):
        operation, other = self.submit_pair()
        observed = []

        def respond(method, url, **kwargs):
            authorization = ProducerMTFallbackAuthorization.objects.get(
                operation=operation
            )
            stages = list(authorization.fallback_units.order_by("unit_id_snapshot"))
            self.assertEqual(
                {stage.unit_id_snapshot for stage in stages},
                {self.unit.pk, other.pk},
            )
            members = list(
                ProducerMTStageMember.objects.filter(
                    unit_stage__authorization=authorization, stage="primary"
                ).select_related("exchange", "unit_stage")
            )
            self.assertTrue(members)
            for member in members:
                exchange = member.exchange
                self.assertEqual(exchange.state, "sending")
                self.assertEqual(exchange.mt_authorization_id, authorization.pk)
                self.assertTrue(exchange.request_snapshot["parser"])
                self.assertEqual(
                    member.unit_stage.primary_work.exchange_id, exchange.pk
                )
            observed.append(True)
            return self.primary_reply(method, url, **kwargs)

        with patch("weblate.utils.requests._request", side_effect=respond):
            process_mt_operation(str(operation.run_id))
        self.assertTrue(observed)
        exchanges = ProducerLLMExchange.objects.filter(
            mt_authorization__operation=operation, mt_stage="primary"
        )
        self.assertTrue(exchanges.exists())
        self.assertFalse(exchanges.filter(usage_recorded=False).exists())
        self.assertEqual(
            LLMUsageLog.objects.filter(
                run_id=operation.run_id, service="openrouter"
            ).count(),
            exchanges.count(),
        )

    def test_primary_prefix_is_not_repurchased_by_fallback(self):
        operation, other = self.submit_pair()
        primary_url = "https://openrouter.ai/api/v1/chat/completions"
        primary_slots: list[list[str]] = []
        fallback_slots: list[list[str]] = []

        def respond(method: str, url: str, **kwargs) -> httpx2.Response:
            payload = kwargs.get("json") or {}
            ids = [
                item["id"]
                for item in json.loads(payload["messages"][-1]["content"])["strings"]
            ]
            if url == primary_url:
                primary_slots.append(ids)
                if len(primary_slots) == 1:
                    return self.primary_reply(method, url, **kwargs)
                response = _credit_refusal()
                response.request = httpx2.Request(method, url)
                return response
            fallback_slots.append(ids)
            return _fallback_chat_completion(method, url, **kwargs)

        with (
            patch("weblate.utils.requests._request", side_effect=respond),
            patch(
                "weblate_customization.machinery.RoutedLLMTranslation.batch_size",
                1,
            ),
        ):
            result = process_mt_operation(str(operation.run_id))

        self.unit.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.unit.target, "Primary translated\n")
        self.assertEqual(other.target, "Fallback translated\n")
        self.assertEqual(len(primary_slots), 2)
        self.assertEqual(len(fallback_slots), 1)
        self.assertNotEqual(fallback_slots[0], primary_slots[0])
        self.assertEqual(result["state"], "completed")

    def _fallback_url(self) -> str:
        return "https://hcbifrost.herocraft.com/litellm/v1/chat/completions"

    def test_public_credit_refusal_binds_usage_to_fallback_exchange(self):
        """
        Assert one fallback charge with usage and digest on the fallback exchange.

        Real engines travel the public durable path: the primary refusal is
        captured, exactly one fallback transport is authorized, usage binds
        the returned fallback exchange and the frozen digest is the actual
        request digest.
        """
        MACHINERY.clear_cache()
        self.addCleanup(MACHINERY.clear_cache)
        operation = self.submit()
        primary_url = "https://openrouter.ai/api/v1/chat/completions"
        fallback_url = self._fallback_url()
        sent: list[str] = []

        def respond(method: str, url: str, **kwargs) -> httpx2.Response:
            sent.append(url)
            if url == primary_url:
                response = _credit_refusal()
            else:
                response = _fallback_chat_completion(method, url, **kwargs)
            response.request = httpx2.Request(method, url)
            return response

        with patch(
            "weblate.utils.requests._request",
            side_effect=respond,
        ):
            result = process_mt_operation(str(operation.run_id))

        self.unit.refresh_from_db()
        self.assertEqual(self.unit.target, "Fallback translated\n")
        self.assertEqual(result["state"], "completed")
        # Exactly one primary (refused) and one fallback (answered) transport.
        self.assertEqual(sent.count(primary_url), 1)
        self.assertEqual(sent.count(fallback_url), 1)

        authorization = ProducerMTFallbackAuthorization.objects.get(
            run_id=operation.run_id
        )
        fallback_slot = ProducerMTFallbackUnit.objects.get(
            authorization_id=authorization.pk
        )
        primary_member = ProducerMTStageMember.objects.get(
            unit_stage=fallback_slot, stage="primary"
        )
        self.assertEqual(
            fallback_slot.primary_work.exchange_id, primary_member.exchange_id
        )
        self.assertEqual(primary_member.exchange.mt_stage, "primary")
        self.assertEqual(fallback_slot.primary_dispositions, ["credit_refused"])
        self.assertEqual(
            primary_member.exchange.request_snapshot["slots"],
            [{"unit_stage_id": str(fallback_slot.pk), "source_slot": 0}],
        )
        fallback_exchange = ProducerLLMExchange.objects.get(
            pk=fallback_slot.fallback_exchange_id
        )
        self.assertEqual(fallback_exchange.state, "received")
        self.assertEqual(fallback_exchange.mt_stage, "fallback")
        # The frozen digest is the actual fallback wire request digest, not
        # the engine configuration digest.
        self.assertNotEqual(
            fallback_exchange.request_digest,
            authorization.fallback_configuration_digest,
        )
        self.assertEqual(fallback_exchange.request_codec, "routed-slots-v4")
        # The secret-free wire snapshot is durable on the exchange itself.
        self.assertIsNotNone(fallback_exchange.request_snapshot)
        self.assertTrue(fallback_exchange.usage_recorded)
        # Usage is attributed to the fallback exchange, not the primary.
        receipt = operation_result(operation)
        summary = receipt["fallback"]
        self.assertEqual(summary["engine"], "litellm")
        self.assertEqual(summary["model"], "QWEN3.7-plus")
        self.assertEqual(summary["requested"], 1)
        self.assertEqual(summary["usable"], 1)
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["unit_ids"], [self.unit.pk])
        self.assertEqual(MTReceiptSerializer(receipt).data["fallback"], summary)
        operation.run.refresh_from_db()
        self.assertEqual(operation.run.summary["fallback_unit_ids"], [self.unit.pk])
        usage = LLMUsageLog.objects.get(run_id=operation.run_id, service="litellm")
        self.assertIsNotNone(usage)
        # Canonical primary claims keep pointing at their primary exchange;
        # the fallback exchange is reachable through the audited slot.
        primary_identities = set(
            ProducerLLMWork.objects.filter(
                identity__startswith="mt:", exchange__mt_stage="primary"
            ).values_list("exchange_id", flat=True)
        )
        self.assertNotIn(fallback_exchange.pk, primary_identities)

    def test_credit_refusal_and_never_sent_tail_both_fallback(self):
        """Each enrolled unit keeps its own credit eligibility evidence."""
        other = next(
            (
                unit
                for unit in self.unit.translation.unit_set.exclude(
                    pk=self.unit.pk
                ).order_by("pk")
                if len(unit.get_source_plurals()) == 1
            ),
            None,
        )
        self.assertIsNotNone(other)
        Unit.objects.filter(pk=other.pk).update(target="", state=0)
        other.refresh_from_db()
        # Use a separate operation for the second unit to keep the scope exact.
        payload = {
            **self.payload,
            "units": [
                {"unit_id": self.unit.pk, "revision": revision(self.unit)},
                {"unit_id": other.pk, "revision": revision(other)},
            ],
        }
        key = str(uuid4())
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            response = self.api.post(
                self.url, payload, format="json", HTTP_IDEMPOTENCY_KEY=key
            )
        self.assertEqual(response.status_code, 202, response.data)
        operation = ProducerMTOperation.objects.get(key=key)
        primary_url = "https://openrouter.ai/api/v1/chat/completions"
        sent: list[str] = []
        primary_calls = 0

        def respond(method: str, url: str, **kwargs) -> httpx2.Response:
            nonlocal primary_calls
            sent.append(url)
            if url == primary_url:
                primary_calls += 1
                response = (
                    _credit_refusal()
                    if primary_calls == 1
                    else _fallback_chat_completion(method, url, **kwargs)
                )
            else:
                response = _fallback_chat_completion(method, url, **kwargs)
            response.request = httpx2.Request(method, url)
            return response

        # Force one unit per durable outbound chunk; the first is refused.
        with (
            patch("weblate.utils.requests._request", side_effect=respond),
            patch(
                "weblate_customization.machinery.RoutedLLMTranslation.batch_size",
                1,
            ),
        ):
            process_mt_operation(str(operation.run_id))

        states = dict(operation.units.values_list("unit_id", "state"))
        self.assertEqual(states, {self.unit.pk: "applied", other.pk: "applied"})
        authorization = ProducerMTFallbackAuthorization.objects.get(
            run_id=operation.run_id
        )
        slots = list(authorization.fallback_units.all())
        self.assertEqual(
            {slot.unit_id_snapshot for slot in slots}, {self.unit.pk, other.pk}
        )
        self.assertEqual(
            {slot.eligibility_reason for slot in slots},
            {"credit_refused", "never_sent_due_credit"},
        )
        self.assertTrue(all(slot.fallback_exchange_id for slot in slots))


@override_settings(
    WEBLATE_MACHINERY=(
        *settings.WEBLATE_MACHINERY,
        "weblate_customization.machinery.RoutedLLMTranslation",
        "weblate_customization.machinery.RoutedLiteLLMTranslation",
    )
)
class ProducerMTFallbackDurableStageTest(ViewTestCase):
    """
    The durable public path falls back only on its own captured credit refusal.

    A unit whose own internal request was refused for credit is resolved by
    exactly one authorized fallback transport; a peer whose request succeeded,
    was generic, partial or unknown is never repurchased and never resolved.
    """

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.project.machinery_settings = {
            "openrouter": {
                "key": "or-key",
                "routing": {"*": "google/gemini-3.7-flash"},
                "mt_fallback": {
                    "engine": "litellm",
                    "routing": {"*": "QWEN3.7-plus"},
                },
            },
            "litellm": {"key": "ll-key", "routing": {"*": "QWEN3.7-plus"}},
        }
        self.project.save(update_fields=["machinery_settings"])
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.unit = self.get_unit()
        Unit.objects.filter(pk=self.unit.pk).update(target="", state=0)
        self.unit.refresh_from_db()
        # A second single-form unit in the same translation, so one chunk can
        # hold both and the classifier is exercised per unit.
        self.other = next(
            (
                unit
                for unit in self.unit.translation.unit_set.exclude(
                    pk=self.unit.pk
                ).order_by("pk")
                if len(unit.get_source_plurals()) == 1
            ),
            None,
        )
        self.assertIsNotNone(self.other)
        Unit.objects.filter(pk=self.other.pk).update(target="", state=0)
        self.other.refresh_from_db()
        self.url = f"/api/producer/projects/{self.project.slug}/mt/batches/"
        self.key = str(uuid4())
        self.payload = {
            "component": self.component.slug,
            "language": "cs",
            "engine": "openrouter",
            "threshold": 80,
            "units": [
                {"unit_id": self.unit.pk, "revision": revision(self.unit)},
                {"unit_id": self.other.pk, "revision": revision(self.other)},
            ],
        }
        self.primary_url = "https://openrouter.ai/api/v1/chat/completions"
        self.fallback_url = (
            "https://hcbifrost.herocraft.com/litellm/v1/chat/completions"
        )

    def submit(self):
        with patch("weblate.trans.tasks.publish_producer_run_dispatch"):
            response = self.api.post(
                self.url, self.payload, format="json", HTTP_IDEMPOTENCY_KEY=self.key
            )
        self.assertEqual(response.status_code, 202, response.data)
        return ProducerMTOperation.objects.get(run_id=response.data["run_id"])

    def test_generic_refusal_is_not_repurchased_as_credit(self) -> None:
        """
        A 500 on one unit is not a credit refusal for it or for its peer.

        The run resolves nothing through the fallback: the first unit's own
        request failed generically, so its slot is not eligible, and the
        second unit - which was never asked because the chunk failed - is not
        eligible either.
        """
        operation = self.submit()
        sent: list[str] = []

        def respond(method: str, url: str, **kwargs) -> httpx2.Response:
            sent.append(url)
            response = httpx2.Response(500, json={"error": "boom"})
            response.request = httpx2.Request(method, url)
            return response

        with (
            patch("weblate.utils.requests._request", side_effect=respond),
            patch(
                "weblate_customization.machinery.RoutedLLMTranslation.batch_size",
                1,
            ),
        ):
            process_mt_operation(str(operation.run_id))

        self.assertNotIn(self.fallback_url, sent)
        states = dict(operation.units.values_list("unit_id", "state"))
        # Enrollment observes both units without authorizing a generic failure.
        self.assertNotIn("applied", states.values())
        for unit in (self.unit, self.other):
            unit.refresh_from_db()
            self.assertEqual(unit.target, "")

    def test_credit_refusal_resolves_the_never_sent_tail(self) -> None:
        """A stopped, never-sent peer is eligible without a fabricated 402."""
        operation = self.submit()
        primary_calls = 0

        def respond(method: str, url: str, **kwargs) -> httpx2.Response:
            nonlocal primary_calls
            if url == self.primary_url:
                primary_calls += 1
                if primary_calls == 1:
                    response = _credit_refusal()
                else:
                    response = _fallback_chat_completion(method, url, **kwargs)
            else:
                response = _fallback_chat_completion(method, url, **kwargs)
            response.request = httpx2.Request(method, url)
            return response

        with (
            patch("weblate.utils.requests._request", side_effect=respond),
            patch(
                "weblate_customization.machinery.RoutedLLMTranslation.batch_size",
                1,
            ),
        ):
            process_mt_operation(str(operation.run_id))

        states = dict(operation.units.values_list("unit_id", "state"))
        self.assertEqual(states[self.other.pk], "applied")
        self.assertEqual(states[self.unit.pk], "applied")
        authorization = ProducerMTFallbackAuthorization.objects.get(
            run_id=operation.run_id
        )
        slots = list(authorization.fallback_units.all())
        self.assertEqual(
            {slot.unit_id_snapshot for slot in slots},
            {self.unit.pk, self.other.pk},
        )
        self.assertEqual(
            {slot.eligibility_reason for slot in slots},
            {"credit_refused", "never_sent_due_credit"},
        )
        for slot in slots:
            self.assertTrue(slot.fallback_answer)
            self.assertIsNotNone(slot.fallback_exchange_id)
        usage = LLMUsageLog.objects.filter(run_id=operation.run_id, service="litellm")
        self.assertEqual(usage.count(), 2)
