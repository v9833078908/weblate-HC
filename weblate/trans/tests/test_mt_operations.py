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
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from weblate.auth.models import User
from weblate.machinery.base import MachineTranslationError
from weblate.machinery.llm import PartialLLMReplyError
from weblate.trans.models import Unit
from weblate.trans.models.producer_mt import ProducerMTOperation, ProducerMTUnitAttempt
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
