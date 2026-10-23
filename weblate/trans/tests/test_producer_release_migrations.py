# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Migration compatibility of the independently prepared producer releases."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TransactionTestCase


class ProducerReleaseMigrationGraphTest(SimpleTestCase):
    def test_current_production_repair_history_is_in_the_upgrade_plan(self) -> None:
        loader = MigrationLoader(None)
        self.assertNotIn("trans", loader.detect_conflicts())
        leaves = loader.graph.leaf_nodes("trans")
        self.assertEqual(len(leaves), 1)
        plan = loader.graph.forwards_plan(leaves[0])
        self.assertIn(("trans", "0147_judge_application_change_restrict"), plan)
        self.assertIn(("trans", "0152_delivery_retention"), plan)

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
