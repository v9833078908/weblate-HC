# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery reads captured receipts without rebuilding provider requests."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from weblate.trans.llm_delivery import record_response
from weblate.trans.models.judge import ProducerRun
from weblate.trans.models.llm_delivery import ProducerLLMExchange
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.mt_fallback import ProducerMTStageMember
from weblate.trans.mt_fallback import enroll_mt_credit_scope, enroll_unit_stages
from weblate.trans.mt_fallback_replay import replay_mt_stage_receipts
from weblate.trans.tests.test_mt_fallback import VALID_SETTINGS
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.util import split_plural


class StageReceiptReplayTest(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.translation = self.get_translation()
        self.unit = (
            self.translation.unit_set.filter(state=0)
            .select_related("source_unit")
            .first()
        )
        self.producer_run = ProducerRun.objects.create(
            actor=self.user,
            scope_type=ProducerRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="translate",
            cap=1,
        )
        self.authorization = enroll_mt_credit_scope(
            run=self.producer_run,
            translation=self.translation,
            units=[self.unit],
            machinery_settings=VALID_SETTINGS,
        )
        self.row = enroll_unit_stages(self.authorization, [self.unit])[0]
        self.row.source_slots = ["Hello", "Hello"]
        self.row.primary_dispositions = ["unknown", "unknown"]
        self.row.primary_answers = ["", ""]
        self.row.save(
            update_fields=["source_slots", "primary_dispositions", "primary_answers"]
        )

    def capture(
        self, stage, indexes, translations, *, parser_override=None, reply_override=None
    ):
        ids = [f"s{index}" for index in indexes]
        wire = {
            "method": "post",
            "url": "https://example.invalid/chat",
            "payload": {"model": "frozen-model"},
        }
        snapshot = {
            **wire,
            "codec": "routed-slots-v4",
            "slots": [
                {"unit_stage_id": str(self.row.pk), "source_slot": index}
                for index in indexes
            ],
            "parser": {
                "codec": "routed-slots-v4",
                "sources": ["Hello"] * len(indexes),
                "cleaned": ["Hello"] * len(indexes),
                "source_occurrences": list(range(len(indexes))),
                "string_ids": ids,
                "replacements": [{} for _ in indexes],
                "name": "OpenRouter",
            },
        }
        if parser_override is not None:
            snapshot["parser"].update(parser_override)
        digest = hashlib.sha256(
            json.dumps(wire, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        exchange = ProducerLLMExchange.objects.create(
            request_digest=digest,
            request_codec="routed-slots-v4",
            state="sending",
            owner="test",
            lease_until=timezone.now() + timedelta(minutes=1),
            run=self.producer_run,
            mt_authorization=self.authorization,
            mt_stage=stage,
            request_snapshot=snapshot,
        )
        for position, index in enumerate(indexes):
            ProducerMTStageMember.objects.create(
                unit_stage=self.row,
                exchange=exchange,
                stage=stage,
                source_slot=index,
                position=position,
            )
        reply = {
            "translations": {
                ident: {"text": [answer], "placeholders": {}}
                for ident, answer in zip(ids, translations, strict=True)
            }
        }
        if reply_override is not None:
            reply = reply_override
        body = {
            "choices": [
                {"message": {"content": json.dumps(reply)}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 9, "completion_tokens": 2},
        }
        record_response(
            exchange.pk,
            json.dumps({"status": 200, "headers": {}, "body": json.dumps(body)}),
        )
        return exchange

    def test_primary_and_fallback_replay_once_without_configuration(self):
        self.capture("primary", [0], ["First"])
        fallback = self.capture("fallback", [1], ["Second"])
        self.row.fallback_exchange = fallback
        self.row.fallback_state = "received"
        self.row.primary_dispositions = ["unknown", "credit_refused"]
        self.row.save(
            update_fields=[
                "fallback_exchange",
                "fallback_state",
                "primary_dispositions",
            ]
        )
        project = self.translation.component.project
        project.machinery_settings = {}
        project.save(update_fields=["machinery_settings"])
        with patch(
            "httpx2.Client.request", side_effect=AssertionError("network forbidden")
        ):
            replay_mt_stage_receipts(self.authorization.pk)
            replay_mt_stage_receipts(self.authorization.pk)
        self.row.refresh_from_db()
        self.assertEqual(self.row.primary_answers, ["First", ""])
        self.assertEqual(split_plural(self.row.fallback_answer), ["First", "Second"])
        self.assertEqual(LLMUsageLog.objects.filter(run=self.producer_run).count(), 2)

    def test_unattributed_skip_does_not_become_credit_eligible(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.autotranslate import AutoTranslate

        # ruff: ignore[import-outside-top-level]
        from weblate.trans.machinery import MachineryBatchOutcome

        auto = AutoTranslate(
            translation=self.translation,
            user=self.user,
            q="",
            mode="translate",
            unit_ids=[self.unit.pk],
            producer_run=self.producer_run,
        )
        # ruff: ignore[private-member-access]
        auto._record_primary_dispositions(
            self.authorization,
            [self.unit],
            [
                MachineryBatchOutcome(
                    status="skipped",
                    service="OpenRouter",
                    unit_ids=(self.unit.pk,),
                    skip_cause="terminal-refusal",
                )
            ],
        )
        self.row.refresh_from_db()
        self.assertEqual(self.row.primary_dispositions, ["unknown", "unknown"])

    def test_damaged_snapshot_or_receipt_does_not_publish(self):
        exchange = self.capture("primary", [0, 1], ["First", "Second"])
        exchange.request_snapshot["payload"]["model"] = "changed"
        exchange.save(update_fields=["request_snapshot"])
        replay_mt_stage_receipts(self.authorization.pk)
        self.row.refresh_from_db()
        self.assertEqual(self.row.primary_answers, ["", ""])
        exchange.refresh_from_db()
        self.assertEqual(exchange.state, "received")
        self.assertFalse(exchange.usage_recorded)

    def test_missing_and_corrupt_body_remain_fenced(self):
        # ruff: ignore[import-outside-top-level]
        from pathlib import Path

        # ruff: ignore[import-outside-top-level]
        from django.conf import settings

        exchange = self.capture("primary", [0, 1], ["First", "Second"])
        path = Path(settings.DATA_DIR) / "private-llm-delivery" / f"{exchange.pk}.reply"
        path.write_text("corrupt", encoding="utf-8")
        replay_mt_stage_receipts(self.authorization.pk)
        path.unlink()
        replay_mt_stage_receipts(self.authorization.pk)
        self.row.refresh_from_db()
        exchange.refresh_from_db()
        self.assertEqual(self.row.primary_answers, ["", ""])
        self.assertEqual(exchange.state, "received")

    def test_legacy_redelivery_applies_before_loading_engines(self):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.autotranslate import AutoTranslate

        self.row.source_slots = ["Hello"]
        self.row.primary_dispositions = ["unknown"]
        self.row.primary_answers = [""]
        self.row.save(
            update_fields=["source_slots", "primary_dispositions", "primary_answers"]
        )
        self.capture("primary", [0], ["Recovered"])
        auto = AutoTranslate(
            translation=self.translation,
            user=self.user,
            q="",
            mode="translate",
            unit_ids=[self.unit.pk],
            producer_run=self.producer_run,
        )
        with patch.object(
            type(self.translation.component.project),
            "get_machinery_settings",
            side_effect=AssertionError("live configuration read"),
        ):
            auto.fetch_mt(["openrouter"], 80)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.get_target_plurals(), ["Recovered\n"])

    @override_settings(
        WEBLATE_MACHINERY=(
            *settings.WEBLATE_MACHINERY,
            "weblate_customization.machinery.RoutedLLMTranslation",
            "weblate_customization.machinery.RoutedLiteLLMTranslation",
        )
    )
    def test_credit_crash_redelivery_sends_fallback_once(self):
        # ruff: ignore[import-outside-top-level]
        import httpx2

        # ruff: ignore[import-outside-top-level]
        from weblate.trans.autotranslate import AutoTranslate

        # ruff: ignore[import-outside-top-level]
        from weblate.trans.mt_fallback import mt_fallback_run_summary

        # ruff: ignore[import-outside-top-level]
        from weblate.trans.tests.test_autotranslate import (
            LITELLM_FALLBACK_URL,
            OPENROUTER_PRIMARY_URL,
            _attach,
            _routed_reply,
            _routed_strings,
        )

        self.row.delete()
        self.authorization.delete()
        self.translation.component.project.machinery_settings = VALID_SETTINGS
        self.translation.component.project.save(update_fields=["machinery_settings"])
        sent = []

        def respond(method, url, **kwargs):
            sent.append(url)
            if url == OPENROUTER_PRIMARY_URL:
                return _attach(
                    httpx2.Response(
                        402, json={"error": {"message": "The credit balance is spent."}}
                    ),
                    method,
                    url,
                )
            inputs = _routed_strings(kwargs["json"])
            return _attach(
                _routed_reply(inputs, [["Recovered"] for _ in inputs]), method, url
            )

        def auto():
            return AutoTranslate(
                translation=self.translation,
                user=self.user,
                q="state:empty",
                mode="translate",
                unit_ids=[self.unit.pk],
                producer_run=self.producer_run,
            )

        with patch("weblate.utils.requests._request", side_effect=respond):
            with (
                patch.object(
                    AutoTranslate,
                    "_fetch_mt_fallback",
                    side_effect=RuntimeError("worker lost"),
                ),
                self.assertRaisesRegex(RuntimeError, "worker lost"),
            ):
                auto().process_mt(["openrouter"], 80)
            resumed = auto()
            resumed.process_mt(["openrouter"], 80)
            auto().process_mt(["openrouter"], 80)

        self.unit.refresh_from_db()
        self.assertEqual(self.unit.get_target_plurals(), ["Recovered\n"])
        self.assertIsNone(resumed.failure_message)
        self.assertEqual(sent, [OPENROUTER_PRIMARY_URL, LITELLM_FALLBACK_URL])
        summary = mt_fallback_run_summary(self.producer_run)
        self.assertEqual(summary["applied"], 1)

    def test_frozen_protected_markup_and_equal_plural_slots(self):
        source = "Hello <b>"
        self.row.source_slots = [source, source]
        self.row.save(update_fields=["source_slots"])
        spec = [
            "@@PH6@@",
            {
                "start": 6,
                "end": 9,
                "text": "<b>",
                "kind": "markup",
                "group": None,
                "role": None,
                "translatable": False,
                "forbidden_text": [],
            },
        ]
        reply = {
            "translations": {
                "s0": {"text": ["First ", ""], "placeholders": {"p0": {"position": 1}}},
                "s1": {
                    "text": ["Second ", ""],
                    "placeholders": {"p0": {"position": 1}},
                },
            }
        }
        self.capture(
            "primary",
            [0, 1],
            ["", ""],
            parser_override={
                "sources": [source, source],
                "cleaned": ["Hello @@PH6@@"] * 2,
                "replacements": [{"@@PH6@@": "<b>"}] * 2,
                "placeholder_specs": [[spec], [spec]],
            },
            reply_override=reply,
        )
        with patch(
            "httpx2.Client.request", side_effect=AssertionError("network forbidden")
        ):
            replay_mt_stage_receipts(self.authorization.pk)
            replay_mt_stage_receipts(self.authorization.pk)
        self.row.refresh_from_db()
        self.assertEqual(self.row.primary_answers, ["First <b>", "Second <b>"])
        self.assertEqual(LLMUsageLog.objects.filter(run=self.producer_run).count(), 1)

    def test_receipt_rename_before_database_update_is_adopted(self):
        exchange = self.capture("primary", [0, 1], ["First", "Second"])
        ProducerLLMExchange.objects.filter(pk=exchange.pk).update(
            state="unknown", response_digest="", response_path=""
        )
        replay_mt_stage_receipts(self.authorization.pk)
        exchange.refresh_from_db()
        self.row.refresh_from_db()
        self.assertEqual(exchange.state, "received")
        self.assertTrue(exchange.response_digest)
        self.assertEqual(self.row.primary_answers, ["First", "Second"])
