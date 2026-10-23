# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from weblate.trans.models.judge import compute_target_hash


class JudgeTargetStorageHashMigrationTest(TransactionTestCase):
    migrate_from = ("trans", "0102_component_spreadsheet_import_draft")
    migrate_to = ("trans", "0103_judge_target_storage_hash")

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.executor.migrate([cls.migrate_from])
        cls.executor = MigrationExecutor(connection)
        cls.old_apps = cls.executor.loader.project_state([cls.migrate_from]).apps

    @classmethod
    def tearDownClass(cls) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        Language = self.old_apps.get_model("lang", "Language")
        Plural = self.old_apps.get_model("lang", "Plural")
        Project = self.old_apps.get_model("trans", "Project")
        Component = self.old_apps.get_model("trans", "Component")
        Translation = self.old_apps.get_model("trans", "Translation")
        Unit = self.old_apps.get_model("trans", "Unit")
        JudgeVerdict = self.old_apps.get_model("trans", "JudgeVerdict")

        source_language = Language.objects.get(code="ru")
        target_language = Language.objects.get(code="cs")
        plural = Plural.objects.filter(language=target_language).first()
        assert plural is not None
        project = Project.objects.create(
            name="Judge migration", slug="judge-migration", web="", instructions=""
        )
        component = Component.objects.create(
            name="Judge migration",
            slug="judge-migration",
            project=project,
            repo="",
            push="",
            repoweb="",
            git_export="",
            report_source_bugs="",
            filemask="",
            screenshot_filemask="",
            template="",
            intermediate="",
            new_base="",
            file_format="po",
            source_language=source_language,
        )
        translation = Translation.objects.create(
            component=component,
            language=target_language,
            plural=plural,
            filename="cs.po",
        )
        self.singular_target = "Žluťoučký kůň"
        self.plural_target = "Одна дверь\x1e\x1eДве двери"
        singular = Unit.objects.create(
            translation=translation,
            id_hash=1,
            source="A horse",
            target=self.singular_target,
            position=1,
        )
        plural_unit = Unit.objects.create(
            translation=translation,
            id_hash=2,
            source="Doors",
            target=self.plural_target,
            position=2,
        )

        current_hash = compute_target_hash([self.singular_target])
        self.parsed_id = JudgeVerdict.objects.create(
            unit=singular,
            max_severity="major",
            model_verdict="flag",
            errors=[{"span": "kůň"}],
            back_translation="horse",
            judge_model="vendor/model",
            seat=1,
            target_hash=current_hash,
            context_hash="current-context",
            run_id=uuid.uuid4(),
        ).pk
        self.unparsed_id = JudgeVerdict.objects.create(
            unit=singular,
            max_severity="none",
            unparsed=True,
            judge_model="vendor/model",
            seat=1,
            target_hash=current_hash,
            context_hash="current-context",
            run_id=uuid.uuid4(),
        ).pk
        self.stale_id = JudgeVerdict.objects.create(
            unit=singular,
            max_severity="critical",
            model_verdict="reject",
            judge_model="vendor/model",
            seat=1,
            target_hash=compute_target_hash(["Old target"]),
            context_hash="old-context",
            run_id=uuid.uuid4(),
        ).pk
        self.plural_id = JudgeVerdict.objects.create(
            unit=plural_unit,
            max_severity="none",
            model_verdict="pass",
            judge_model="vendor/model",
            seat=1,
            target_hash=compute_target_hash(self.plural_target.split("\x1e\x1e")),
            context_hash="plural-context",
            run_id=uuid.uuid4(),
        ).pk

    def test_backfills_only_current_target_rows(self) -> None:
        self.executor.migrate([self.migrate_to])
        self.executor = MigrationExecutor(connection)
        judge_verdict_model = self.executor.loader.project_state(
            [self.migrate_to]
        ).apps.get_model("trans", "JudgeVerdict")

        parsed = judge_verdict_model.objects.get(pk=self.parsed_id)
        unparsed = judge_verdict_model.objects.get(pk=self.unparsed_id)
        stale = judge_verdict_model.objects.get(pk=self.stale_id)
        plural = judge_verdict_model.objects.get(pk=self.plural_id)

        self.assertEqual(
            parsed.target_storage_hash,
            hashlib.md5(
                self.singular_target.encode(), usedforsecurity=False
            ).hexdigest(),
        )
        self.assertEqual(unparsed.target_storage_hash, parsed.target_storage_hash)
        self.assertIsNone(stale.target_storage_hash)
        self.assertEqual(
            plural.target_storage_hash,
            hashlib.md5(self.plural_target.encode(), usedforsecurity=False).hexdigest(),
        )
        self.assertEqual(parsed.errors, [{"span": "kůň"}])
        self.assertEqual(parsed.back_translation, "horse")
        self.assertEqual(stale.max_severity, "critical")
