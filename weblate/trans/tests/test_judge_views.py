# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid

from django.test import override_settings
from django.urls import reverse

from weblate.trans.judge_loop import build_request
from weblate.trans.models.judge import (
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
)
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import STATE_TRANSLATED


def judge_context_hash(unit) -> str:
    request = build_request(unit)
    return compute_context_hash(
        source=request.source,
        note=request.note,
        glossary_terms=request.glossary_terms,
    )


@override_settings(JUDGE_ENABLED=True, JUDGE_MAX_REPAIR_ATTEMPTS=1)
class JudgeAutoTranslateViewTest(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def make_reject(self, unit):
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="critical",
            model_verdict="reject",
            judge_model="vendor/model-a",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash=judge_context_hash(unit),
        )
        unit.run_checks()

    def test_judge_mode_requires_review_permission_at_the_view(self) -> None:
        # The view must refuse the mode even when a crafted POST asks
        # for it while the form would hide it (permission gate).
        self.user.is_superuser = False
        self.user.save()
        url = reverse("auto_translation", kwargs=self.kw_translation)
        response = self.client.post(
            url,
            {
                "mode": "judge",
                "q": "state:empty",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
            },
        )
        # Refused at the permission gate: 403, no run started.
        self.assertEqual(response.status_code, 403)
        self.assertEqual(JudgeVerdict.objects.count(), 0)

    @override_settings(
        JUDGE_OPENROUTER_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_preview_reports_the_capped_execution_scope(self) -> None:
        response = self.client.get(
            reverse("auto_translation_preview", kwargs=self.kw_translation),
            {
                "mode": "judge",
                "q": "state:empty",
                "auto_source": "mt",
                "threshold": 80,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                "matched",
                "processed",
                "remaining",
                "writable",
                "judge_calls_initial",
                "judge_calls_worst_case",
                "judge_cost",
                "pretranslation_cost",
            },
        )

    @override_settings(
        JUDGE_OPENROUTER_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_preview_uses_observed_judge_costs(self) -> None:
        for model, cost in (("vendor-a/model", "0.01"), ("vendor-b/model", "0.02")):
            for _ in range(5):
                LLMUsageLog.objects.create(
                    model=model,
                    project_slug=self.component.project.slug,
                    operation=LLMUsageLog.Operation.JUDGE,
                    cost_usd=cost,
                    unit_count=1,
                )
        response = self.client.get(
            reverse("auto_translation_preview", kwargs=self.kw_translation),
            {
                "mode": "judge",
                "q": "state:empty",
                "auto_source": "mt",
                "threshold": 80,
            },
        )

        cost = response.json()["judge_cost"]
        self.assertTrue(cost["available"])
        self.assertEqual(cost["min"], "0.12")
        self.assertEqual(cost["max"], "0.24")

    def test_form_shows_how_many_strings_a_run_would_touch(self) -> None:
        response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_auto_row_count")

    def test_form_shows_the_honest_request_estimate(self) -> None:
        response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        # Worst case: strings x 2 seats x (1 + repair attempts).
        self.assertContains(response, "id_auto_request_estimate")

    def test_form_explains_judge_duration_contention(self) -> None:
        response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_auto_duration_estimate")

    def test_overwrite_warning_shown_when_judge_verdicts_exist(self) -> None:
        self.make_reject(self.get_unit())
        response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "judge-verdicts-exist")


class JudgeCheckVisibilityTest(ViewTestCase):
    def make_reject(self, unit):
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="critical",
            model_verdict="reject",
            judge_model="vendor/model-a",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash=judge_context_hash(unit),
        )
        unit.run_checks()

    def test_judge_check_is_absent_from_things_to_check(self) -> None:
        # translate.html's card uses unit.deterministic_checks for both
        # its visibility gate and its loop (task 4/10); test that surface
        # directly rather than scraping HTML across unrelated page regions
        # (e.g. the nearby-units status tooltip, out of this task's scope).
        unit = self.get_unit()
        unit.translate(self.user, ["Hello, world!\n"], STATE_TRANSLATED)  # "same"
        self.make_reject(unit)
        names = {check.name for check in unit.deterministic_checks}
        self.assertIn("same", names)
        self.assertNotIn("judge-reject", names)

    def test_judge_check_still_reaches_navigation_and_filters(self) -> None:
        unit = self.get_unit()
        self.make_reject(unit)
        self.assertIn("judge-reject", unit.all_checks_names)

    def test_card_is_hidden_when_only_a_judge_check_remains(self) -> None:
        unit = self.get_unit()
        self.make_reject(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Things to check")

    def test_a_judge_check_cannot_be_enforced(self) -> None:
        # A7: judge-reject in enforced_checks must not push state 11.
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        self.make_reject(unit)
        component = unit.translation.component
        component.enforced_checks = ["judge-reject"]
        component.save(update_fields=["enforced_checks"])
        unit.translate(self.user, ["Ahoj upraveno"], STATE_TRANSLATED)
        refreshed = self.get_unit()
        self.assertNotEqual(
            refreshed.state, 11, "enforced judge-reject must not set needs-rewriting"
        )

    def test_deterministic_check_still_renders(self) -> None:
        unit = self.get_unit()
        # Translating to the same text triggers the deterministic "same"
        # check via run_checks; it must still render in the card.
        unit.translate(self.user, ["Hello, world!\n"], STATE_TRANSLATED)
        unit.run_checks()
        unit.clear_checks_cache()
        self.assertIn("same", unit.all_checks_names)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Unchanged translation")


class JudgeVerdictCardTest(ViewTestCase):
    def make(self, unit, max_severity, *, unparsed=False, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        JudgeVerdict.objects.create(
            unit=unit, max_severity=max_severity, unparsed=unparsed, **kwargs
        )
        unit.run_checks()

    def make_reject(self, unit, **kw):
        self.make(unit, "critical", **kw)

    def make_flag(self, unit, **kw):
        self.make(unit, "major", **kw)

    def make_unparsed(self, unit, **kw):
        self.make(unit, "none", unparsed=True, **kw)

    def make_round(self, unit, *, seat1, seat2):
        run_id = uuid.uuid4()
        errors1 = (
            []
            if seat1 == "none"
            else [
                {
                    "span": "x",
                    "category": "terminology",
                    "severity": seat1,
                    "description": "seat one's objection",
                }
            ]
        )
        errors2 = (
            []
            if seat2 == "none"
            else [
                {
                    "span": "y",
                    "category": "fluency",
                    "severity": seat2,
                    "description": "seat two's objection",
                }
            ]
        )
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity=seat1,
            seat=1,
            run_id=run_id,
            errors=errors1,
            judge_model="vendor/model-a",
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash=judge_context_hash(unit),
        )
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity=seat2,
            seat=2,
            run_id=run_id,
            errors=errors2,
            judge_model="vendor/model-b",
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash=judge_context_hash(unit),
        )
        unit.run_checks()

    def test_card_shows_the_verdict_and_the_model(self) -> None:
        unit = self.get_unit()
        self.make_reject(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "vendor/model-a")

    def test_card_never_shows_a_score(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_judge_card")
        self.assertNotContains(response, "confidence")

    def test_card_shows_seat_disagreement(self) -> None:
        unit = self.get_unit()
        self.make_round(unit, seat1="critical", seat2="none")
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id_judge_card")
        self.assertContains(response, "seat one&#x27;s objection")

    def test_stale_verdict_is_marked_not_hidden(self) -> None:
        unit = self.get_unit()
        self.make_reject(unit, target_hash="stale-hash-matches-nothing")
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "previous version")

    def test_unparsed_is_not_shown_as_a_verdict(self) -> None:
        unit = self.get_unit()
        self.make_unparsed(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "rejected")

    def test_current_unparsed_round_is_marked_even_with_historical_verdict(
        self,
    ) -> None:
        unit = self.get_unit()
        context_hash = judge_context_hash(unit)
        self.make_reject(unit, context_hash=context_hash)
        self.make_unparsed(unit, context_hash=context_hash)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "latest judge answer was not parsed")
        self.assertContains(response, "historical evidence")

    def test_no_verdict_means_no_card(self) -> None:
        response = self.client.get(self.get_unit().get_absolute_url())
        self.assertNotContains(response, "id_judge_card")

    def test_context_changed_is_marked(self) -> None:
        # Q3: glossary/note changed since judging -> flag it.
        unit = self.get_unit()
        self.make_reject(unit, context_hash="stale-context-hash")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "context changed")


class JudgeBackTranslationTest(ViewTestCase):
    def make_flag(self, unit, *, back_translation, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", "c")
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="major",
            model_verdict="flag",
            back_translation=back_translation,
            **kwargs,
        )
        unit.run_checks()

    def test_back_translation_renders_next_to_the_string(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit, back_translation="The door is blocked by the DOORS")
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The door is blocked by the DOORS")

    def test_back_translation_is_labelled_as_a_reconstruction(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit, back_translation="Whatever")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Approximate reconstruction")

    def test_stale_verdict_does_not_render_its_back_translation(self) -> None:
        unit = self.get_unit()
        self.make_flag(
            unit,
            back_translation="Outdated",
            target_hash="stale-hash-matches-nothing",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Outdated")

    def test_back_translation_shows_without_secondary_languages(self) -> None:
        # Q6: the block must live outside {% if secondary %}.
        unit = self.get_unit()  # this test project has no secondary languages
        self.make_flag(unit, back_translation="Visible anyway")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Visible anyway")

    def test_empty_back_translation_renders_nothing(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit, back_translation="")
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Approximate reconstruction")
