# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Migration compatibility of the independently prepared producer releases."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone


class ProducerReleaseMigrationGraphTest(SimpleTestCase):
    def test_current_production_repair_history_is_in_the_upgrade_plan(self) -> None:
        loader = MigrationLoader(None)
        self.assertNotIn("trans", loader.detect_conflicts())
        leaves = loader.graph.leaf_nodes("trans")
        self.assertEqual(len(leaves), 1)
        plan = loader.graph.forwards_plan(leaves[0])
        self.assertIn(("trans", "0147_judge_application_change_restrict"), plan)
        self.assertIn(("trans", "0152_delivery_retention"), plan)
        self.assertIn(("trans", "0154_llm_exchange_request_codec"), plan)

    def test_quality_and_mt_share_one_upgrade_target(self) -> None:
        loader = MigrationLoader(None)
        self.assertNotIn("trans", loader.detect_conflicts())
        leaves = loader.graph.leaf_nodes("trans")
        self.assertEqual(len(leaves), 1)
        plan = loader.graph.forwards_plan(leaves[0])
        self.assertIn(("trans", "0147_producer_mt_operations"), plan)
        self.assertIn(("trans", "0147_producer_quality_operations"), plan)
        self.assertIn(("trans", "0148_preserve_workspace_project_history"), plan)


class ProducerReleaseMigrationDataTest(TransactionTestCase):
    def test_codec_upgrade_keeps_paid_witnesses_unclassified_and_unchanged(self):
        before = ("trans", "0153_merge_production_repair_history")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        row = apps.get_model("trans", "ProducerLLMExchange").objects.create(
            request_digest="a" * 64,
            response_digest="b" * 64,
            response_path="synthetic.reply",
            state="unknown",
            owner="synthetic",
            lease_until=timezone.now() - timedelta(seconds=1),
            usage_recorded=True,
        )
        old = (
            apps.get_model("trans", "ProducerLLMExchange")
            .objects.values()
            .get(pk=row.pk)
        )
        target = ("trans", "0154_llm_exchange_request_codec")
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        current = executor.loader.project_state([target]).apps
        saved = (
            current.get_model("trans", "ProducerLLMExchange")
            .objects.values()
            .get(pk=row.pk)
        )
        self.assertEqual(saved.pop("request_codec"), "")
        self.assertEqual(saved, old)

    def test_attempt_link_upgrade_preserves_paid_history_without_inferred_links(self):
        before = ("trans", "0155_routed_gemini_reasoning_codec")
        target = ("trans", "0156_judge_delivery_attempt")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        run = apps.get_model("trans", "ProducerRun").objects.create(
            scope_type="component",
            scope_id="synthetic",
            scope_label="Synthetic",
            scope_path="/",
            requested_mode="judge",
            cap=1,
        )
        attempt = apps.get_model("trans", "JudgeRequestAttempt").objects.create(
            run=run,
            seat=1,
            provider="litellm",
            model="synthetic/model",
            endpoint_fingerprint="a" * 64,
            profile_fingerprint="b" * 64,
            prompt_schema_version="synthetic",
            batch_digest="c" * 64,
            batch_size=1,
            http_status=200,
            transport_succeeded=True,
            parsed=False,
            failure_kind="invalid-json",
            response_id="synthetic-paid",
        )
        exchange = apps.get_model("trans", "ProducerLLMExchange").objects.create(
            run=run,
            request_digest="d" * 64,
            response_digest="e" * 64,
            response_path="synthetic-paid.reply",
            response_id="synthetic-paid",
            request_codec="opaque-v1",
            state="received",
            owner="synthetic",
            lease_until=timezone.now() + timedelta(seconds=1),
            usage_recorded=True,
        )
        work = apps.get_model("trans", "ProducerLLMWork").objects.create(
            semantic_key="f" * 64,
            identity="synthetic-paid-work",
            position=0,
            exchange=exchange,
        )
        usage = apps.get_model("trans", "LLMUsageLog").objects.create(
            run=run,
            request_attempt=attempt,
            model="synthetic/model",
            service="litellm",
            operation="judge",
            prompt_tokens=2,
            completion_tokens=1,
            total_tokens=3,
            response_id="synthetic-paid",
            cost_usd="0.02",
        )
        witnesses = {
            name: apps.get_model("trans", name).objects.values().get(pk=row.pk)
            for name, row in (
                ("ProducerRun", run),
                ("JudgeRequestAttempt", attempt),
                ("ProducerLLMExchange", exchange),
                ("ProducerLLMWork", work),
                ("LLMUsageLog", usage),
            )
        }
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        current = executor.loader.project_state([target]).apps
        for name, expected in witnesses.items():
            with self.subTest(model=name):
                saved = (
                    current.get_model("trans", name)
                    .objects.values()
                    .get(pk=expected["id"])
                )
                if name == "JudgeRequestAttempt":
                    self.assertIs(saved.pop("response_complete"), False)
                elif name == "ProducerLLMExchange":
                    self.assertIsNone(saved.pop("judge_attempt_id"))
                self.assertEqual(saved, expected)

    def test_pre_upgrade_attempt_writer_remains_usable_after_upgrade(self) -> None:
        before = ("trans", "0155_routed_gemini_reasoning_codec")
        target = ("trans", "0156_judge_delivery_attempt")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        legacy_attempt = executor.loader.project_state([before]).apps.get_model(
            "trans", "JudgeRequestAttempt"
        )
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        written = legacy_attempt.objects.create(
            seat=1,
            provider="litellm",
            model="synthetic/model",
            endpoint_fingerprint="a" * 64,
            profile_fingerprint="b" * 64,
            prompt_schema_version="synthetic",
            batch_digest="c" * 64,
            batch_size=1,
        )
        current_attempt = executor.loader.project_state([target]).apps.get_model(
            "trans", "JudgeRequestAttempt"
        )
        self.assertIs(
            current_attempt.objects.get(pk=written.pk).response_complete, False
        )

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def seed_translation(self, apps):
        user = apps.get_model(settings.AUTH_USER_MODEL).objects.create(
            username="release-owner"
        )
        project = apps.get_model("trans", "Project").objects.create(
            name="Synthetic release", slug="synthetic-release", web="", instructions=""
        )
        language = apps.get_model("lang", "Language").objects.get(code="cs")
        plural = (
            apps.get_model("lang", "Plural").objects.filter(language=language).first()
        )
        component = apps.get_model("trans", "Component").objects.create(
            name="Synthetic release",
            slug="release",
            project=project,
            file_format="po",
            source_language=language,
        )
        translation = apps.get_model("trans", "Translation").objects.create(
            component=component, language=language, plural=plural, filename="cs.po"
        )
        unit = apps.get_model("trans", "Unit").objects.create(
            translation=translation,
            id_hash=1,
            source="Open",
            target="Öffnen",
            state=20,
            position=1,
        )
        return user, project, translation, unit

    def assert_translation_preserved(self, apps, unit_id) -> None:
        unit = apps.get_model("trans", "Unit").objects.get(pk=unit_id)
        self.assertEqual((unit.source, unit.target, unit.state), ("Open", "Öffnen", 20))

    def test_mt_upgrade_preserves_translation_and_provider_receipt(self) -> None:
        before = ("trans", "0148_preserve_workspace_project_history")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        user, project, translation, unit = self.seed_translation(apps)
        run = apps.get_model("trans", "ProducerRun").objects.create(
            actor=user,
            scope_type="translation",
            scope_id=str(translation.pk),
            scope_label="Synthetic",
            scope_path="/synthetic/",
            requested_mode="translate",
            cap=1,
            status="completed",
            scope_snapshot=[unit.pk],
        )
        key = uuid.uuid4()
        operation = apps.get_model("trans", "ProducerMTOperation").objects.create(
            actor=user,
            project=project,
            translation=translation,
            run=run,
            key=key,
            request_hash="a" * 64,
            state="completed",
            payload={"units": [unit.pk]},
        )
        attempt = apps.get_model("trans", "ProducerMTUnitAttempt").objects.create(
            operation=operation,
            unit=unit,
            revision="b" * 64,
            state="applied",
            answer="Öffnen",
            after_revision="c" * 64,
        )
        executor = MigrationExecutor(connection)
        target = ("trans", "0149_merge_producer_quality_mt")
        executor.migrate([target])
        apps = executor.loader.project_state([target]).apps
        self.assert_translation_preserved(apps, unit.pk)
        receipt = apps.get_model("trans", "ProducerMTOperation").objects.get(
            pk=operation.pk
        )
        self.assertEqual(
            (receipt.key, receipt.state, receipt.run_id), (key, "completed", run.pk)
        )
        saved = apps.get_model("trans", "ProducerMTUnitAttempt").objects.get(
            pk=attempt.pk
        )
        self.assertEqual(
            (saved.state, saved.answer, saved.revision), ("applied", "Öffnen", "b" * 64)
        )
        self.assertEqual(
            apps.get_model("trans", "ProducerQualityOperation").objects.count(), 0
        )

    def test_quality_upgrade_preserves_translation_and_quality_receipt(self) -> None:
        before = ("trans", "0147_producer_quality_operations")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        apps = executor.loader.project_state([before]).apps
        user, project, _, unit = self.seed_translation(apps)
        key = uuid.uuid4()
        operation = apps.get_model("trans", "ProducerQualityOperation").objects.create(
            actor=user,
            project=project,
            key=key,
            action="recheck",
            request_hash="d" * 64,
            state="confirmed",
            result={"checked_unit_ids": [unit.pk]},
        )
        executor = MigrationExecutor(connection)
        target = ("trans", "0149_merge_producer_quality_mt")
        executor.migrate([target])
        apps = executor.loader.project_state([target]).apps
        self.assert_translation_preserved(apps, unit.pk)
        receipt = apps.get_model("trans", "ProducerQualityOperation").objects.get(
            pk=operation.pk
        )
        self.assertEqual((receipt.key, receipt.state), (key, "confirmed"))
        self.assertEqual(receipt.result, {"checked_unit_ids": [unit.pk]})
        self.assertEqual(
            apps.get_model("trans", "ProducerMTOperation").objects.count(), 0
        )
