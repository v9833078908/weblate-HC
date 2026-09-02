# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from weblate.auth.data import SELECTION_ALL
from weblate.auth.models import Group, Permission, Role
from weblate.trans.actions import ActionEvents
from weblate.trans.judge_loop import (
    _generation_lock_key,
    build_request,
    recheck_query,
)
from weblate.trans.models.change import Change
from weblate.trans.models.judge import (
    JudgeRun,
    JudgeRunUnit,
    JudgeVerdict,
    compute_context_hash,
    compute_target_hash,
    compute_target_storage_hash,
    resolve_verdict,
)
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.suggestion import Suggestion
from weblate.trans.models.unit import Unit
from weblate.trans.tests.test_views import ViewTestCase
from weblate.trans.views.basic import _judge_hand_off_blocked
from weblate.utils.state import (
    STATE_FUZZY,
    STATE_NEEDS_CHECKING,
    STATE_READONLY,
    STATE_TRANSLATED,
)
from weblate.workspaces.models import Workspace


def judge_context_hash(unit) -> str:
    request = build_request(unit)
    return compute_context_hash(
        source=request.source,
        note=request.note,
        explanation=request.explanation,
        glossary_terms=request.glossary_terms,
    )


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
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

    def test_old_readiness_card_is_absent(self) -> None:
        response = self.client.get(self.component.get_absolute_url())

        self.assertNotContains(response, "Release readiness")
        self.assertNotContains(response, "Delivery")
        self.assertNotContains(response, "Primary action")
        self.assertNotContains(response, "No blocking action")

    @override_settings(
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_preview_accepts_a_project_scope(self) -> None:
        url = reverse(
            "auto_translation_preview", kwargs={"path": self.project.get_url_path()}
        )
        response = self.client.get(
            f"{url}?mode=judge&q=state%3Aempty&auto_source=mt&threshold=80"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("matched", response.json())

    def test_post_accepts_a_project_scope_and_queues_a_run(self) -> None:
        response = self.client.post(
            reverse("auto_translation", kwargs={"path": self.project.get_url_path()}),
            {
                "mode": "judge",
                "q": "state:empty",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
            },
        )
        self.assertRedirects(response, self.project.get_absolute_url())

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_project_scope_task_runs_in_eager_mode(self) -> None:
        response = self.client.post(
            reverse("auto_translation", kwargs={"path": self.project.get_url_path()}),
            {
                "mode": "judge",
                "q": "state:empty",
                "auto_source": "mt",
                "engines": [],
                "threshold": 80,
            },
        )

        self.assertRedirects(response, self.project.get_absolute_url())

    def test_translation_estimate_uses_the_judge_default_query(self) -> None:
        translation = self.get_translation()
        unit = translation.unit_set.first()
        assert unit is not None
        unit.translate(self.user, ["Needs editing"], STATE_FUZZY)
        expected = (
            translation.unit_set.exclude(state=STATE_READONLY)
            .search("state:empty", parser="unit")
            .count()
        )

        response = self.client.get(translation.get_absolute_url())

        self.assertEqual(response.context["judge_row_count"], expected)

    @override_settings(
        JUDGE_API_KEY="sk-test",
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
        JUDGE_API_KEY="sk-test",
        JUDGE_MODEL_SEAT_1="vendor-a/model",
        JUDGE_MODEL_SEAT_2="vendor-b/model",
    )
    def test_preview_uses_observed_judge_costs(self) -> None:
        for model, cost in (("vendor-a/model", "0.01"), ("vendor-b/model", "0.02")):
            for _ in range(5):
                LLMUsageLog.objects.create(
                    model=model,
                    service="openrouter",
                    project_id_snapshot=self.component.project_id,
                    project_slug=self.component.project.slug,
                    operation=LLMUsageLog.Operation.JUDGE,
                    cost_usd=cost,
                    unit_count=1,
                )
            for project_id_snapshot, service in (
                (self.component.project_id + 1, "openrouter"),
                (self.component.project_id, "litellm"),
            ):
                for _ in range(5):
                    LLMUsageLog.objects.create(
                        model=model,
                        service=service,
                        project_id_snapshot=project_id_snapshot,
                        project_slug=self.component.project.slug,
                        operation=LLMUsageLog.Operation.JUDGE,
                        cost_usd=9,
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

    def test_form_estimates_requests_from_batches_not_strings(self) -> None:
        with override_settings(JUDGE_BATCH_SIZE=2):
            response = self.client.get(self.translation_url)
        self.assertEqual(response.status_code, 200)
        rows = response.context["judge_row_count"]
        self.assertGreater(rows, 2)
        batched = ((rows + 1) // 2) * 2 * 2
        per_string = rows * 2 * 2
        self.assertContains(response, f"plans up to {batched} LLM requests")
        self.assertNotContains(response, f"plans up to {per_string} LLM requests")

    def test_form_estimate_never_floors_a_partial_batch_to_zero(self) -> None:
        with override_settings(JUDGE_BATCH_SIZE=1000):
            response = self.client.get(self.translation_url)
        self.assertContains(response, "plans up to 4 LLM requests")

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
        self.assertContains(response, "Rejected")
        self.assertContains(response, "Will not ship")

    def test_no_verdict_means_no_card(self) -> None:
        response = self.client.get(self.get_unit().get_absolute_url())
        self.assertNotContains(response, "id_judge_card")

    def test_context_changed_is_marked(self) -> None:
        # Q3: glossary/note changed since judging -> flag it.
        unit = self.get_unit()
        self.make_reject(unit, context_hash="stale-context-hash")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "context changed")

    def test_major_card_shows_a_ships_badge(self) -> None:
        unit = self.get_unit()
        self.make_flag(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Ships with evidence")

    def test_critical_card_shows_a_will_not_ship_badge(self) -> None:
        unit = self.get_unit()
        self.make_reject(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Will not ship")
        self.assertNotContains(response, "Ships with evidence")

    def test_current_context_round_beats_newer_context_stale_evidence(self) -> None:
        unit = self.get_unit()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])
        current_context = judge_context_hash(unit)
        first = JudgeVerdict.objects.create(
            unit=unit,
            max_severity="major",
            seat=1,
            errors=[
                {
                    "category": "terminology",
                    "severity": "major",
                    "description": "current context",
                }
            ],
            judge_model="vendor/model-a",
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash=current_context,
        )
        newer = JudgeVerdict.objects.create(
            unit=unit,
            max_severity="critical",
            seat=1,
            errors=[
                {
                    "category": "terminology",
                    "severity": "critical",
                    "description": "changed context",
                }
            ],
            judge_model="vendor/model-a",
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash="changed-context",
        )
        JudgeVerdict.objects.filter(pk=newer.pk).update(
            timestamp=first.timestamp + timedelta(seconds=1)
        )

        response = self.client.get(unit.get_absolute_url())

        self.assertContains(response, "current context")
        self.assertNotContains(response, "changed context")
        self.assertContains(response, "id_judge_resolution_form")

    def test_pass_card_renders_minor_errors(self) -> None:
        unit = self.get_unit()
        self.make(
            unit,
            "minor",
            errors=[
                {
                    "span": "x",
                    "category": "fluency",
                    "severity": "minor",
                    "description": "a minor style note",
                }
            ],
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "a minor style note")

    def test_pass_card_omits_error_list_for_none(self) -> None:
        unit = self.get_unit()
        self.make(
            unit,
            "none",
            errors=[
                {
                    "span": "x",
                    "category": "fluency",
                    "severity": "none",
                    "description": "should never render",
                }
            ],
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "id_judge_card")
        self.assertNotContains(response, "should never render")


class JudgeRepairEvidenceTest(ViewTestCase):
    def make_round(self, unit, *, run_id, attempt, severity, when, tag):
        """Judge the CURRENT stored text with both seats."""
        target_hash = compute_target_hash(unit.get_target_plurals())
        errors = (
            []
            if severity == "none"
            else [
                {
                    "span": "x",
                    "category": "terminology",
                    "severity": severity,
                    "description": f"{tag} objection",
                }
            ]
        )
        rows = [
            JudgeVerdict.objects.create(
                unit=unit,
                max_severity=severity,
                seat=seat,
                run_id=run_id,
                attempt=attempt,
                errors=errors,
                judge_model=f"vendor/model-{seat}",
                target_hash=target_hash,
                context_hash="c",
            )
            for seat in (1, 2)
        ]
        JudgeVerdict.objects.filter(pk__in=[row.pk for row in rows]).update(
            timestamp=when
        )
        unit.run_checks()
        return rows

    def make_change(self, unit, *, old, target, when):
        change = Change.objects.create(
            unit=unit,
            action=ActionEvents.CHANGE,
            user=self.user,
            author=self.user,
            old=old,
            target=target,
        )
        Change.objects.filter(pk=change.pk).update(timestamp=when)
        return change

    def test_exact_match_renders_the_comparison(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Broken sentence"], STATE_TRANSLATED)
        unit = self.get_unit()
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit, run_id=run_id, attempt=0, severity="major", when=now, tag="original"
        )
        unit.translate(self.user, ["Fixed sentence"], STATE_FUZZY)
        unit = self.get_unit()
        change = unit.change_set.get(target=unit.target)
        Change.objects.filter(pk=change.pk).update(timestamp=now + timedelta(seconds=1))
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="none",
            when=now + timedelta(seconds=2),
            tag="repaired",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Repaired since the original verdict")
        self.assertContains(response, "original objection")
        self.assertContains(response, "Text before the repair:")
        self.assertContains(response, "Broken")
        # Regression: value/diff must not be swapped, or the comparison
        # shows the fixed wording as removed and the broken wording as
        # added - exactly backwards from what a producer needs to trust.
        # A plain substring assertion on "Broken" alone would pass either
        # way (the unit's own change-history section elsewhere on the same
        # page independently renders the identical Broken->Fixed diff the
        # correct way round, which would make an unscoped assertion pass
        # even if THIS block's tags were swapped) - so this locates the
        # "Text before the repair:" block specifically and only inspects
        # the few hundred bytes right after it.
        label = b"Text before the repair:"
        idx = response.content.index(label)
        evidence_html = response.content[idx : idx + len(label) + 300]
        self.assertIn(b"<del>Broken", evidence_html)
        self.assertIn(b"<ins>Fixed", evidence_html)

    def test_second_repair_shows_the_text_before_that_repair(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Broken sentence"], STATE_TRANSLATED)
        unit = self.get_unit()
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit, run_id=run_id, attempt=0, severity="major", when=now, tag="original"
        )
        unit.translate(self.user, ["First repair"], STATE_FUZZY)
        unit = self.get_unit()
        first_change = unit.change_set.get(target=unit.target)
        Change.objects.filter(pk=first_change.pk).update(
            timestamp=now + timedelta(seconds=1)
        )
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="major",
            when=now + timedelta(seconds=2),
            tag="first repair",
        )
        unit.translate(self.user, ["Final repair"], STATE_FUZZY)
        unit = self.get_unit()
        second_change = unit.change_set.get(target=unit.target)
        Change.objects.filter(pk=second_change.pk).update(
            timestamp=now + timedelta(seconds=3)
        )
        self.make_round(
            unit,
            run_id=run_id,
            attempt=2,
            severity="none",
            when=now + timedelta(seconds=4),
            tag="final repair",
        )

        response = self.client.get(unit.get_absolute_url())

        self.assertContains(response, "Text before the repair:")
        label = b"Text before the repair:"
        idx = response.content.index(label)
        evidence_html = response.content[idx : idx + len(label) + 300]
        self.assertIn(b"<ins>nal</ins> repair", evidence_html)
        self.assertNotIn(b"Broken", evidence_html)

    def test_missing_change_omits_the_comparison(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Broken sentence"], STATE_TRANSLATED)
        unit = self.get_unit()
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit, run_id=run_id, attempt=0, severity="major", when=now, tag="original"
        )
        # The repair happened without going through translate(), so no
        # Change row exists in the window at all.
        Unit.objects.filter(pk=unit.pk).update(target="Fixed sentence")
        unit = self.get_unit()
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="none",
            when=now + timedelta(seconds=2),
            tag="repaired",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Repaired since the original verdict")
        self.assertContains(response, "original objection")
        self.assertNotContains(response, "Text before the repair:")

    def test_purged_change_omits_the_comparison(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Broken sentence"], STATE_TRANSLATED)
        unit = self.get_unit()
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit, run_id=run_id, attempt=0, severity="major", when=now, tag="original"
        )
        # A Change matched "Fixed sentence" inside the window once, but a
        # later edit (outside this narrative's window) moved the current
        # text on to "Fixed sentence v2" - the earlier Change no longer
        # describes the text that is actually current.
        self.make_change(
            unit,
            old="Broken sentence",
            target="Fixed sentence",
            when=now + timedelta(seconds=1),
        )
        Unit.objects.filter(pk=unit.pk).update(target="Fixed sentence v2")
        unit = self.get_unit()
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="none",
            when=now + timedelta(seconds=2),
            tag="repaired",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Repaired since the original verdict")
        self.assertNotContains(response, "Text before the repair:")

    def test_ambiguous_changes_omit_the_comparison(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Broken sentence"], STATE_TRANSLATED)
        unit = self.get_unit()
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit, run_id=run_id, attempt=0, severity="major", when=now, tag="original"
        )
        # Two Change rows both land in the window with the repaired
        # target: neither may be trusted as THE previous text.
        self.make_change(
            unit,
            old="Broken sentence",
            target="Fixed sentence",
            when=now + timedelta(seconds=1),
        )
        self.make_change(
            unit,
            old="Broken sentence take two",
            target="Fixed sentence",
            when=now + timedelta(seconds=1, milliseconds=500),
        )
        Unit.objects.filter(pk=unit.pk).update(target="Fixed sentence")
        unit = self.get_unit()
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="none",
            when=now + timedelta(seconds=2),
            tag="repaired",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Repaired since the original verdict")
        self.assertNotContains(response, "Text before the repair:")

    def test_plural_unit_shows_every_form(self) -> None:
        unit = self.get_unit("Orangutan")
        unit.translate(
            self.user,
            ["Puvodni jedna", "Puvodni dva", "Puvodni pet"],
            STATE_TRANSLATED,
        )
        unit = self.get_unit("Orangutan")
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit, run_id=run_id, attempt=0, severity="major", when=now, tag="original"
        )
        unit.translate(
            self.user,
            ["Opraveno jedna", "Opraveno dva", "Opraveno pet"],
            STATE_FUZZY,
        )
        unit = self.get_unit("Orangutan")
        change = unit.change_set.get(target=unit.target)
        Change.objects.filter(pk=change.pk).update(timestamp=now + timedelta(seconds=1))
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="none",
            when=now + timedelta(seconds=2),
            tag="repaired",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Text before the repair:")
        self.assertContains(response, "Puvodni jedna")
        self.assertContains(response, "Puvodni dva")
        self.assertContains(response, "Puvodni pet")

    def test_unresolved_repair_still_shows_evidence(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Broken sentence"], STATE_TRANSLATED)
        unit = self.get_unit()
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit,
            run_id=run_id,
            attempt=0,
            severity="critical",
            when=now,
            tag="original",
        )
        unit.translate(self.user, ["Still broken sentence"], STATE_FUZZY)
        unit = self.get_unit()
        change = unit.change_set.get(target=unit.target)
        Change.objects.filter(pk=change.pk).update(timestamp=now + timedelta(seconds=1))
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="critical",
            when=now + timedelta(seconds=2),
            tag="repaired",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Will not ship")
        self.assertContains(response, "Text before the repair:")
        self.assertContains(response, "Broken sentence")

    def test_first_pass_renders_nothing(self) -> None:
        unit = self.get_unit()
        self.make_round(
            unit,
            run_id=uuid.uuid4(),
            attempt=0,
            severity="major",
            when=timezone.now(),
            tag="original",
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Repaired since the original verdict")
        self.assertNotContains(response, "Text before the repair:")

    def test_stale_active_round_renders_nothing(self) -> None:
        unit = self.get_unit()
        unit.translate(self.user, ["Broken sentence"], STATE_TRANSLATED)
        unit = self.get_unit()
        run_id = uuid.uuid4()
        now = timezone.now()
        self.make_round(
            unit, run_id=run_id, attempt=0, severity="major", when=now, tag="original"
        )
        unit.translate(self.user, ["Fixed sentence"], STATE_FUZZY)
        unit = self.get_unit()
        change = unit.change_set.get(target=unit.target)
        Change.objects.filter(pk=change.pk).update(timestamp=now + timedelta(seconds=1))
        self.make_round(
            unit,
            run_id=run_id,
            attempt=1,
            severity="none",
            when=now + timedelta(seconds=2),
            tag="repaired",
        )
        # A further manual edit invalidates every recorded round.
        Unit.objects.filter(pk=unit.pk).update(target="Edited again by a human")
        unit = self.get_unit()
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "previous version")
        self.assertNotContains(response, "Repaired since the original verdict")
        self.assertNotContains(response, "Text before the repair:")


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


class JudgeResolutionViewTest(ViewTestCase):
    def enable_review(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def make_verdict(self, unit, severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", judge_context_hash(unit))
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        verdict = JudgeVerdict.objects.create(
            unit=unit, max_severity=severity, **kwargs
        )
        unit.run_checks()
        return verdict

    def resolve_url(self, verdict) -> str:
        return reverse("resolve-judge-verdict", kwargs={"pk": verdict.pk})

    def test_escalating_a_critical_keeps_it_held(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_FUZZY)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        response = self.client.post(
            self.resolve_url(verdict),
            {"resolution": "escalated", "reason": "needs a second look"},
        )
        self.assertRedirects(response, unit.get_absolute_url())
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_FUZZY)
        verdict.refresh_from_db()
        self.assertEqual(verdict.resolution, "escalated")

    def test_accepting_a_held_verdict_ships_it(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_FUZZY)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        response = self.client.post(
            self.resolve_url(verdict),
            {"resolution": "accepted_as_is", "reason": "acceptable in context"},
        )
        self.assertRedirects(response, unit.get_absolute_url())
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_TRANSLATED)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Ships with override")
        self.assertNotContains(response, "Will not ship")

    def test_escalating_a_major_holds_it_for_review(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        response = self.client.post(
            self.resolve_url(verdict),
            {"resolution": "escalated", "reason": "wants a second opinion"},
        )
        self.assertRedirects(response, unit.get_absolute_url())
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_NEEDS_CHECKING)

    def test_resolve_requires_review_permission(self) -> None:
        # PermissionDenied is converted to a real 403 by Django's own
        # exception handling before it would ever reach the test client
        # as a raised exception (same pattern as ignore_check_source's
        # own permission test in test_edit.py).
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        response = self.client.post(
            self.resolve_url(verdict),
            {"resolution": "escalated", "reason": "needs a look"},
        )
        self.assertEqual(response.status_code, 403)
        verdict.refresh_from_db()
        self.assertEqual(verdict.resolution, "")

    def test_invalid_transition_shows_an_error_without_a_crash(self) -> None:
        # A fresh FLAG can be accepted as-is directly since Task 7; a
        # *terminal* accepted_as_is re-requesting escalated is still the
        # invalid transition this view must reject cleanly.
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ACCEPTED_AS_IS,
            reason="already ships",
        )
        response = self.client.post(
            self.resolve_url(verdict),
            {"resolution": "escalated", "reason": "second thoughts"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not apply")

    def test_card_offers_the_resolution_form_when_reviewer(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "id_judge_resolution_form")
        self.assertContains(response, self.resolve_url(verdict))

    def test_card_hides_the_resolution_form_without_permission(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, "critical")
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "id_judge_resolution_form")

    def test_card_shows_the_recorded_decision(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        self.client.post(
            self.resolve_url(verdict),
            {"resolution": "escalated", "reason": "flagged for a human"},
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Escalated for review")
        self.assertContains(response, "flagged for a human")

    # -- Task 8: auto-advance only after success ---------------------------

    def _next_urls(self, unit):
        page = self.client.get(unit.get_absolute_url())
        return page.context["this_unit_url"], page.context["next_unit_url"]

    def test_successful_resolution_advances_to_the_next_unit(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        this_unit_url, next_unit_url = self._next_urls(unit)
        response = self.client.post(
            self.resolve_url(verdict),
            {
                "resolution": "accepted_as_is",
                "reason": "acceptable in context",
                "next": this_unit_url,
                "success_next": next_unit_url,
            },
        )
        self.assertRedirects(response, next_unit_url, fetch_redirect_response=False)

    def test_blank_reason_stays_on_the_current_unit(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        this_unit_url, next_unit_url = self._next_urls(unit)
        response = self.client.post(
            self.resolve_url(verdict),
            {
                "resolution": "accepted_as_is",
                "reason": "   ",
                "next": this_unit_url,
                "success_next": next_unit_url,
            },
        )
        self.assertRedirects(response, this_unit_url, fetch_redirect_response=False)

    def test_stale_verdict_stays_on_the_current_unit(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        this_unit_url, next_unit_url = self._next_urls(unit)
        response = self.client.post(
            self.resolve_url(verdict),
            {
                "resolution": "accepted_as_is",
                "reason": "acceptable in context",
                "next": this_unit_url,
                "success_next": next_unit_url,
            },
        )
        # Second, stale request for the already-resolved verdict.
        response = self.client.post(
            self.resolve_url(verdict),
            {
                "resolution": "escalated",
                "reason": "changed my mind",
                "next": this_unit_url,
                "success_next": next_unit_url,
            },
        )
        self.assertRedirects(response, this_unit_url, fetch_redirect_response=False)

    def test_invalid_form_stays_on_the_current_unit(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        this_unit_url, next_unit_url = self._next_urls(unit)
        response = self.client.post(
            self.resolve_url(verdict),
            {
                "resolution": "not-a-real-choice",
                "reason": "acceptable in context",
                "next": this_unit_url,
                "success_next": next_unit_url,
            },
        )
        self.assertRedirects(response, this_unit_url, fetch_redirect_response=False)

    def test_success_next_is_sanitized_like_next(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        this_unit_url, _next_unit_url = self._next_urls(unit)
        response = self.client.post(
            self.resolve_url(verdict),
            {
                "resolution": "accepted_as_is",
                "reason": "acceptable in context",
                "next": this_unit_url,
                "success_next": "http://evil.example.com/",
            },
        )
        self.assertRedirects(response, unit.get_absolute_url())


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
)
class JudgeQueueStripViewTest(ViewTestCase):
    """The producer queue strip on component/project pages (Task 4)."""

    def enable_review(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def make_verdict(self, unit, severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault(
            "target_storage_hash", compute_target_storage_hash(unit.target)
        )
        kwargs.setdefault("context_hash", judge_context_hash(unit))
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        kwargs.setdefault("run_id", uuid.uuid4())
        verdict = JudgeVerdict.objects.create(
            unit=unit, max_severity=severity, **kwargs
        )
        unit.run_checks()
        self.refresh_stats(unit.translation)
        return verdict

    def resolve_url(self, verdict) -> str:
        return reverse("resolve-judge-verdict", kwargs={"pk": verdict.pk})

    def refresh_stats(self, translation) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            translation.invalidate_cache()

    # -- Visibility gates --------------------------------------------------

    @override_settings(JUDGE_ENABLED=False)
    def test_strip_hidden_when_configuration_incomplete(self) -> None:
        self.enable_review()
        response = self.client.get(self.component.get_absolute_url())
        self.assertNotContains(response, 'id="judge-queue-heading"')

    def test_strip_hidden_without_permission(self) -> None:
        # Default test user has no translation.auto/unit.review grant.
        response = self.client.get(self.component.get_absolute_url())
        self.assertNotContains(response, 'id="judge-queue-heading"')

    def test_strip_hidden_for_glossary(self) -> None:
        self.enable_review()
        self.component.create_glossary()
        glossary = self.project.component_set.get(is_glossary=True)
        response = self.client.get(glossary.get_absolute_url())
        self.assertNotContains(response, 'id="judge-queue-heading"')

    def test_strip_visible_when_ready(self) -> None:
        self.enable_review()
        response = self.client.get(self.component.get_absolute_url())
        self.assertContains(response, 'id="judge-queue-heading"')

    # -- Counts --------------------------------------------------------

    def test_unresolved_critical_counts_as_needs_a_human(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        self.make_verdict(unit, "critical")
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["counts"]["needs_human"], 1)
        self.assertContains(response, "needs a human")

    def test_accepted_as_is_removes_critical_from_needs_a_human(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["counts"]["needs_human"], 1)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.resolve_url(verdict),
                {"resolution": "accepted_as_is", "reason": "acceptable in context"},
            )
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["counts"]["needs_human"], 0)

    def test_same_state_resolution_invalidates_judge_stats(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        translation = unit.translation
        self.refresh_stats(translation)
        self.assertEqual(translation.stats.judge_resolved, 0)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.resolve_url(verdict),
                {"resolution": "accepted_as_is", "reason": "ships as-is"},
            )

        translation.stats.force_load()
        self.assertEqual(translation.stats.judge_resolved, 1)

    def test_escalated_major_counts_as_needs_a_human(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["counts"]["needs_human"], 0)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.resolve_url(verdict),
                {"resolution": "escalated", "reason": "wants a second opinion"},
            )
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["counts"]["needs_human"], 1)

    def test_rejudging_drops_a_stale_resolution_from_needs_a_human(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        unit.translate(self.user, ["Ahoj"], STATE_TRANSLATED)
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.resolve_url(verdict),
                {"resolution": "escalated", "reason": "wants a second opinion"},
            )
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["counts"]["needs_human"], 1)

        # A fresh round supersedes the escalated round; the new verdict is
        # unresolved and not critical, so it no longer needs a human.
        unit = self.get_unit()
        self.make_verdict(
            unit,
            "none",
            run_id=uuid.uuid4(),
            target_hash=compute_target_hash(unit.get_target_plurals()),
        )
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["counts"]["needs_human"], 0)

    def test_unparsed_attempt_counts_separately(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        self.make_verdict(unit, "none", unparsed=True)
        response = self.client.get(self.component.get_absolute_url())
        counts = response.context["judge_queue"]["counts"]
        self.assertEqual(counts["unparsed"], 1)

    def test_source_unit_is_excluded_from_counts(self) -> None:
        self.enable_review()
        source_translation = self.component.source_translation
        source_unit = source_translation.unit_set.get(
            source__startswith="Hello, world!"
        )
        self.make_verdict(source_unit, "critical", context_hash="source-context")
        response = self.client.get(self.component.get_absolute_url())
        counts = response.context["judge_queue"]["counts"]
        self.assertEqual(counts["needs_human"], 0)

    def test_every_number_matches_its_direct_query_count(self) -> None:
        self.enable_review()
        units = list(
            self.get_translation().unit_set.order_by("pk").exclude(state=STATE_READONLY)
        )
        # Unresolved critical.
        self.make_verdict(units[0], "critical")
        # Escalated major.
        escalated = self.make_verdict(units[1], "major")
        escalated.resolution = "escalated"
        escalated.save(update_fields=["resolution"])
        # Unparsed attempt.
        self.make_verdict(units[2], "none", unparsed=True)
        # Left untouched: still "not reviewed".

        response = self.client.get(self.component.get_absolute_url())
        counts = response.context["judge_queue"]["counts"]

        component_units = Unit.objects.filter(
            translation__component=self.component
        ).exclude(translation=self.component.source_translation)

        self.assertEqual(
            counts["needs_human"],
            component_units.search(
                "(judge:reject AND NOT judge:resolved) OR judge:escalated"
            ).count(),
        )
        self.assertEqual(
            counts["not_reviewed"],
            component_units.search("NOT has:judge AND NOT state:read-only").count(),
        )
        self.assertEqual(
            counts["unparsed"],
            component_units.search("judge:unparsed").count(),
        )

    # -- Project scope: controls only -----------------------------------

    def test_project_shows_controls_without_counts(self) -> None:
        self.enable_review()
        unit = self.get_unit()
        self.make_verdict(unit, "critical")
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, 'id="judge-queue-heading"')
        self.assertIsNone(response.context["judge_queue"]["counts"])
        self.assertNotContains(response, "needs a human")

    # -- Workspace scope: controls only -----------------------------------

    def attach_workspace(self) -> Workspace:
        workspace = Workspace.objects.create(name="Judge queue workspace")
        self.project.workspace = workspace
        self.project.save(update_fields=["workspace"])
        return workspace

    def test_workspace_shows_controls_without_counts(self) -> None:
        self.enable_review()
        workspace = self.attach_workspace()
        unit = self.get_unit()
        self.make_verdict(unit, "critical")
        response = self.client.get(workspace.get_absolute_url())
        self.assertContains(response, 'id="judge-queue-heading"')
        self.assertIsNone(response.context["judge_queue"]["counts"])
        self.assertNotContains(response, "needs a human")

    def test_workspace_strip_hidden_without_permission(self) -> None:
        # Default test user has no translation.auto/unit.review grant on any
        # project in the workspace.
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])
        workspace = self.attach_workspace()
        response = self.client.get(workspace.get_absolute_url())
        self.assertNotContains(response, 'id="judge-queue-heading"')

    @override_settings(JUDGE_ENABLED=False)
    def test_workspace_strip_hidden_when_configuration_incomplete(self) -> None:
        self.enable_review()
        workspace = self.attach_workspace()
        response = self.client.get(workspace.get_absolute_url())
        self.assertNotContains(response, 'id="judge-queue-heading"')

    # -- Prefilled launchers ------------------------------------------

    def test_component_binds_prefilled_judge_mode(self) -> None:
        self.enable_review()
        response = self.client.get(
            self.component.get_absolute_url(), {"mode": "judge", "q": "NOT has:judge"}
        )
        self.assertEqual(response.context["autoform"].initial.get("mode"), "judge")
        self.assertEqual(response.context["autoform"].initial.get("q"), "NOT has:judge")

    def test_project_binds_prefilled_judge_mode(self) -> None:
        self.enable_review()
        response = self.client.get(
            self.project.get_absolute_url(), {"mode": "judge", "q": "NOT has:judge"}
        )
        self.assertEqual(response.context["autoform"].initial.get("mode"), "judge")

    def test_workspace_binds_prefilled_judge_mode(self) -> None:
        self.enable_review()
        workspace = self.attach_workspace()
        response = self.client.get(
            workspace.get_absolute_url(), {"mode": "judge", "q": "NOT has:judge"}
        )
        self.assertEqual(response.context["autoform"].initial.get("mode"), "judge")
        self.assertEqual(response.context["autoform"].initial.get("q"), "NOT has:judge")

    def test_run_url_targets_the_not_reviewed_queue(self) -> None:
        self.enable_review()
        response = self.client.get(self.component.get_absolute_url())
        run_url = response.context["judge_queue"]["run_url"]
        self.assertIn("mode=judge", run_url)
        self.assertIn("q=NOT+has%3Ajudge", run_url)

    # -- Last run --------------------------------------------------------

    def test_last_run_link_hidden_without_a_run(self) -> None:
        self.enable_review()
        response = self.client.get(self.component.get_absolute_url())
        self.assertIsNone(response.context["judge_queue"]["last_run"])
        self.assertNotContains(response, "Last run")

    def test_last_run_link_shown_for_the_exact_component_scope(self) -> None:
        self.enable_review()
        run = JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["last_run"].pk, run.pk)
        self.assertContains(response, reverse("judge-run", kwargs={"pk": run.pk}))

    def test_last_run_shows_the_newest_own_launch_over_someone_elses(self) -> None:
        # Review: last_run must be an exact identity match to the requesting
        # user's own launch, not merely "newest for this scope" - a
        # concurrent launch by someone else on a shared component must
        # never surface as "my" last run after a reload. own_run's
        # ``created`` is forced strictly earlier than the other actor's
        # run via a queryset update (bypassing auto_now_add):
        # order_by("-created") has no tie-breaker, so relying on
        # sequential auto_now_add timestamps alone would make the
        # comparison this test exists to prove non-deterministic.
        self.enable_review()
        other_user = self.anotheruser
        now = timezone.now()
        own_run = JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        other_run = JudgeRun.objects.create(
            actor=other_user,
            scope_type=JudgeRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        JudgeRun.objects.filter(pk=own_run.pk).update(
            created=now - timedelta(minutes=1)
        )
        JudgeRun.objects.filter(pk=other_run.pk).update(created=now)
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(response.context["judge_queue"]["last_run"].pk, own_run.pk)

    def test_last_run_hides_a_run_launched_by_someone_else_only(self) -> None:
        self.enable_review()
        other_user = self.anotheruser
        JudgeRun.objects.create(
            actor=other_user,
            scope_type=JudgeRun.ScopeType.COMPONENT,
            scope_id=str(self.component.pk),
            scope_label=str(self.component),
            scope_path=self.component.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        response = self.client.get(self.component.get_absolute_url())
        self.assertIsNone(response.context["judge_queue"]["last_run"])

    def test_last_run_does_not_leak_across_components(self) -> None:
        self.enable_review()
        other = self.create_json_mono(name="Other component", project=self.project)
        JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.COMPONENT,
            scope_id=str(other.pk),
            scope_label=str(other),
            scope_path=other.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        response = self.client.get(self.component.get_absolute_url())
        self.assertIsNone(response.context["judge_queue"]["last_run"])

    def test_translation_page_shows_its_own_last_run(self) -> None:
        # Reload after completion has no durable path through the ephemeral
        # task message (a finished task is forgotten immediately) or the
        # component/project/workspace-only queue strip for a
        # translation-scoped launch, so the translation page carries its
        # own last-run link instead.
        self.enable_review()
        run = JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(self.translation.pk),
            scope_label=str(self.translation),
            scope_path=self.translation.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        response = self.client.get(self.translation.get_absolute_url())
        self.assertEqual(response.context["judge_last_run"].pk, run.pk)
        self.assertContains(response, reverse("judge-run", kwargs={"pk": run.pk}))

    def test_translation_page_hides_last_run_without_permission(self) -> None:
        # Default test user has no translation.auto/unit.review grant.
        JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(self.translation.pk),
            scope_label=str(self.translation),
            scope_path=self.translation.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        response = self.client.get(self.translation.get_absolute_url())
        self.assertIsNone(response.context["judge_last_run"])

    def test_translation_last_run_does_not_leak_from_another_translation(
        self,
    ) -> None:
        self.enable_review()
        other = self.component.translation_set.exclude(pk=self.translation.pk).first()
        if other is None:
            self.skipTest("fixture has only one target translation")
        JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(other.pk),
            scope_label=str(other),
            scope_path=other.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        response = self.client.get(self.translation.get_absolute_url())
        self.assertIsNone(response.context["judge_last_run"])

    def test_translation_last_run_does_not_leak_from_another_actor(self) -> None:
        self.enable_review()
        other_user = self.anotheruser
        JudgeRun.objects.create(
            actor=other_user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(self.translation.pk),
            scope_label=str(self.translation),
            scope_path=self.translation.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        response = self.client.get(self.translation.get_absolute_url())
        self.assertIsNone(response.context["judge_last_run"])

    def test_translation_last_run_shows_own_launch_over_a_newer_other_actor(
        self,
    ) -> None:
        # Same ordering discipline as the component-scope equivalent above:
        # own_run's ``created`` is forced strictly earlier than the other
        # actor's run via a queryset update (bypassing auto_now_add), since
        # order_by("-created") has no tie-breaker for equal timestamps.
        self.enable_review()
        other_user = self.anotheruser
        now = timezone.now()
        own_run = JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(self.translation.pk),
            scope_label=str(self.translation),
            scope_path=self.translation.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        other_run = JudgeRun.objects.create(
            actor=other_user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(self.translation.pk),
            scope_label=str(self.translation),
            scope_path=self.translation.get_absolute_url(),
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        JudgeRun.objects.filter(pk=own_run.pk).update(
            created=now - timedelta(minutes=1)
        )
        JudgeRun.objects.filter(pk=other_run.pk).update(created=now)
        response = self.client.get(self.translation.get_absolute_url())
        self.assertEqual(response.context["judge_last_run"].pk, own_run.pk)

    # -- Task 9: conservative hand-off readiness ---------------------------

    def judge_all_units(self, severity="none") -> None:
        """Give every non-source, non-readonly unit a fresh PASS verdict."""
        for translation in self.component.translation_set.all():
            if translation.is_source:
                continue
            for unit in translation.unit_set.exclude(state=STATE_READONLY):
                self.make_verdict(unit, severity)

    def test_hand_off_absent_with_zero_history(self) -> None:
        self.enable_review()
        response = self.client.get(self.component.get_absolute_url())
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])
        self.assertNotContains(response, "ready to hand off")

    def test_hand_off_absent_with_partial_coverage(self) -> None:
        self.enable_review()
        # Only one of several units judged: not_reviewed stays above zero.
        self.make_verdict(self.get_unit(), "none")
        response = self.client.get(self.component.get_absolute_url())
        self.assertGreater(response.context["judge_queue"]["counts"]["not_reviewed"], 0)
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])

    def test_hand_off_absent_with_unresolved_critical(self) -> None:
        self.enable_review()
        self.judge_all_units()
        self.make_verdict(self.get_unit(), "critical")
        response = self.client.get(self.component.get_absolute_url())
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])

    def test_hand_off_absent_with_escalated_major(self) -> None:
        self.enable_review()
        self.judge_all_units()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        verdict.resolution = "escalated"
        verdict.save(update_fields=["resolution"])
        self.refresh_stats(unit.translation)
        response = self.client.get(self.component.get_absolute_url())
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])

    def test_hand_off_absent_with_stale_target(self) -> None:
        self.enable_review()
        self.judge_all_units()
        unit = self.get_unit()
        unit.translate(self.user, ["Drifted away"], STATE_TRANSLATED)
        response = self.client.get(self.component.get_absolute_url())
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])

    def test_hand_off_absent_with_unparsed_attempt(self) -> None:
        self.enable_review()
        self.judge_all_units()
        self.make_verdict(self.get_unit(), "none", unparsed=True)
        response = self.client.get(self.component.get_absolute_url())
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])

    def test_hand_off_visible_when_fully_clean(self) -> None:
        self.enable_review()
        self.judge_all_units()
        response = self.client.get(self.component.get_absolute_url())
        counts = response.context["judge_queue"]["counts"]
        self.assertEqual(counts["needs_human"], 0)
        self.assertEqual(counts["not_reviewed"], 0)
        self.assertEqual(counts["unparsed"], 0)
        self.assertTrue(response.context["judge_queue"]["hand_off_ready"])
        self.assertContains(response, "ready to hand off")
        self.assertContains(response, response.context["judge_queue"]["download_url"])

    def test_candidate_presence_never_clears_critical(self) -> None:
        self.enable_review()
        self.judge_all_units()
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        Suggestion.objects.add(
            unit,
            ["A repaired translation"],
            request=self.get_request(),
            vote=False,
            raise_exception=False,
            userdetails={
                "kind": "judge-repair",
                "schema": 1,
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
                "target_hash": compute_target_hash(unit.get_target_plurals()),
                "context_hash": judge_context_hash(unit),
                "engine": "openrouter",
            },
        )
        response = self.client.get(self.component.get_absolute_url())
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])

    def test_context_drift_blocks_even_when_cache_reads_clean(self) -> None:
        self.enable_review()
        self.judge_all_units()
        unit = self.get_unit()
        # Same target hash (still current), but a context hash the unit's
        # live source/note/explanation/glossary no longer produce: the
        # cache never tracks this, so every cached count stays zero.
        JudgeVerdict.objects.filter(unit=unit).update(context_hash="0" * 64)
        response = self.client.get(self.component.get_absolute_url())
        counts = response.context["judge_queue"]["counts"]
        self.assertEqual(counts["needs_human"], 0)
        self.assertEqual(counts["not_reviewed"], 0)
        self.assertEqual(counts["unparsed"], 0)
        self.assertFalse(response.context["judge_queue"]["hand_off_ready"])

    def test_the_authoritative_check_sees_an_unjudged_unit(self) -> None:
        # The cached ``not_reviewed`` counter is the only other guard against
        # a never-judged string, and stats lag. The authoritative pass must
        # therefore detect missing coverage on its own, independently of any
        # cached count the caller gates on.
        self.enable_review()
        self.judge_all_units()
        translations = [
            translation
            for translation in self.component.translation_set.all()
            if not translation.is_source
        ]
        self.assertFalse(_judge_hand_off_blocked(translations))
        JudgeVerdict.objects.filter(unit=self.get_unit()).delete()
        self.assertTrue(_judge_hand_off_blocked(translations))

    def test_hand_off_query_count_stays_bounded(self) -> None:
        self.enable_review()
        self.judge_all_units()
        with CaptureQueriesContext(connection) as clean:
            self.client.get(self.component.get_absolute_url())
        self.assertTrue(clean.captured_queries)

        # A blocking critical short-circuits before the expensive per-unit
        # context pass ever runs: strictly fewer queries than the clean
        # scope, which must run that pass to confirm readiness.
        self.make_verdict(self.get_unit(), "critical")
        with CaptureQueriesContext(connection) as blocked:
            self.client.get(self.component.get_absolute_url())
        self.assertLess(len(blocked.captured_queries), len(clean.captured_queries))

    def test_download_url_targets_the_component(self) -> None:
        self.enable_review()
        response = self.client.get(self.component.get_absolute_url())
        self.assertEqual(
            response.context["judge_queue"]["download_url"],
            reverse("download", kwargs={"path": self.component.get_url_path()}),
        )


@override_settings(JUDGE_ENABLED=True, JUDGE_API_KEY="sk-test")
class JudgeRunReportViewTest(ViewTestCase):
    """The durable judge run report page (Task 2)."""

    def enable_review(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def create_run(self, scope=None, *, status=JudgeRun.Status.COMPLETED) -> JudgeRun:
        scope = scope or self.component
        return JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.COMPONENT,
            scope_id=str(scope.pk),
            scope_label=str(scope),
            scope_path=scope.get_absolute_url(),
            requested_query="state:empty",
            requested_mode="judge",
            cap=1000,
            status=status,
            started=timezone.now() - timedelta(minutes=5),
            finished=(
                timezone.now()
                if status in {JudgeRun.Status.COMPLETED, JudgeRun.Status.FAILED}
                else None
            ),
        )

    def add_row(
        self,
        run: JudgeRun,
        unit=None,
        *,
        unit_id_snapshot: int | None = None,
        outcome=JudgeRunUnit.Outcome.PASSED,
        **overrides,
    ) -> JudgeRunUnit:
        if unit is not None:
            unit_id_snapshot = unit.id
            overrides.setdefault("translation_id", unit.translation_id)
            overrides.setdefault("component_id", unit.translation.component_id)
            overrides.setdefault("project_id", unit.translation.component.project_id)
            overrides.setdefault("input_target", unit.get_target_plurals())
            overrides.setdefault(
                "input_target_hash", compute_target_hash(unit.get_target_plurals())
            )
            overrides.setdefault("context_hash", judge_context_hash(unit))
        else:
            assert unit_id_snapshot is not None
            overrides.setdefault("translation_id", self.translation.pk)
            overrides.setdefault("component_id", self.component.pk)
            overrides.setdefault("project_id", self.project.pk)
            overrides.setdefault("input_target", [])
            overrides.setdefault("input_target_hash", compute_target_hash([]))
            overrides.setdefault("context_hash", "x")
        return JudgeRunUnit.objects.create(
            run=run,
            unit=unit,
            unit_id_snapshot=unit_id_snapshot,
            outcome=outcome,
            before_target=overrides.get("input_target", []),
            after_target=overrides.get("input_target", []),
            **overrides,
        )

    def report_url(self, run: JudgeRun) -> str:
        return reverse("judge-run", kwargs={"pk": run.pk})

    def make_verdict(self, unit, *, resolution: str = "") -> JudgeVerdict:
        return JudgeVerdict.objects.create(
            unit=unit,
            max_severity="major",
            model_verdict=JudgeVerdict.Verdict.FLAG,
            judge_model="vendor/model-a",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            context_hash=judge_context_hash(unit),
            resolution=resolution,
        )

    # -- Counts --------------------------------------------------------

    def test_every_outcome_count_equals_its_report_local_row_count(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        escalated_verdict = self.make_verdict(
            unit, resolution=JudgeVerdict.Resolution.ESCALATED
        )
        self.add_row(
            run,
            unit,
            outcome=JudgeRunUnit.Outcome.MAJOR,
            verdict=escalated_verdict,
            repair_status=JudgeRunUnit.RepairStatus.ROLLED_BACK,
        )
        self.add_row(
            run,
            unit_id_snapshot=900001,
            outcome=JudgeRunUnit.Outcome.MINOR,
            cached=True,
        )
        self.add_row(
            run,
            unit_id_snapshot=900002,
            outcome=JudgeRunUnit.Outcome.SKIPPED,
            skip_reason=JudgeRunUnit.SkipReason.CAP,
        )
        self.add_row(
            run,
            unit_id_snapshot=900003,
            outcome=JudgeRunUnit.Outcome.PASSED,
            repair_status=JudgeRunUnit.RepairStatus.APPLIED,
        )

        response = self.client.get(self.report_url(run))
        self.assertEqual(response.status_code, 200)
        stats = {key: count for key, _label, count in response.context["stats"]}
        for key, expected in stats.items():
            filtered = self.client.get(self.report_url(run), {"outcome": key})
            self.assertEqual(
                filtered.context["page_obj"].paginator.count,
                expected,
                f"outcome={key}",
            )
        self.assertEqual(stats["matched"], 4)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["checked"], 3)
        self.assertEqual(stats["cached"], 1)
        self.assertEqual(stats["major"], 1)
        self.assertEqual(stats["minor"], 1)
        self.assertEqual(stats["rolled-back"], 1)
        self.assertEqual(stats["repaired"], 1)
        self.assertEqual(stats["escalated"], 1)
        self.assertEqual(stats["accepted-as-is"], 0)

    def test_cached_evidence_appears_once_and_is_labeled_cached(self) -> None:
        self.enable_review()
        run = self.create_run()
        unit = self.get_unit()
        self.add_row(run, unit, outcome=JudgeRunUnit.Outcome.PASSED, cached=True)
        response = self.client.get(self.report_url(run), {"outcome": "cached"})
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "cached")

    def test_unit_in_older_and_newer_run_appears_only_in_requested_report(
        self,
    ) -> None:
        self.enable_review()
        unit = self.get_unit()
        older = self.create_run()
        newer = self.create_run()
        self.add_row(older, unit, outcome=JudgeRunUnit.Outcome.MAJOR)
        self.add_row(newer, unit, outcome=JudgeRunUnit.Outcome.PASSED)

        older_response = self.client.get(self.report_url(older))
        [older_row] = list(older_response.context["page_obj"])
        self.assertEqual(older_row.outcome, JudgeRunUnit.Outcome.MAJOR)

        newer_response = self.client.get(self.report_url(newer))
        [newer_row] = list(newer_response.context["page_obj"])
        self.assertEqual(newer_row.outcome, JudgeRunUnit.Outcome.PASSED)

    def test_current_and_stale_and_deleted_units_render_safely(self) -> None:
        self.enable_review()
        run = self.create_run()
        current_unit = self.get_unit()
        self.add_row(run, current_unit, outcome=JudgeRunUnit.Outcome.PASSED)

        stale_unit = self.get_unit(source="Thank you for using Weblate.")
        self.add_row(
            run,
            stale_unit,
            outcome=JudgeRunUnit.Outcome.PASSED,
            input_target=["a stored snapshot no longer on the unit"],
        )

        gone = self.add_row(
            run, unit_id_snapshot=900010, outcome=JudgeRunUnit.Outcome.MAJOR
        )
        response = self.client.get(self.report_url(run))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "900010")
        self.assertContains(response, "current text changed since this run")
        self.assertIsNone(JudgeRunUnit.objects.get(pk=gone.pk).unit)

    def test_empty_queued_running_completed_failed_runs_render_explicit_states(
        self,
    ) -> None:
        self.enable_review()
        for status in JudgeRun.Status.values:
            with self.subTest(status=status):
                run = self.create_run(status=status)
                response = self.client.get(self.report_url(run))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, run.get_status_display())
                if status in {JudgeRun.Status.QUEUED, JudgeRun.Status.RUNNING}:
                    self.assertContains(response, "still in progress")
                if status == JudgeRun.Status.FAILED:
                    self.assertContains(response, "failed")
                self.assertContains(response, "No strings in this outcome.")

    def test_unauthorized_private_scope_returns_404_with_no_count_leakage(
        self,
    ) -> None:
        # Default test user has no translation.auto/unit.review grant.
        run = self.create_run()
        self.add_row(run, self.get_unit(), outcome=JudgeRunUnit.Outcome.CRITICAL)
        response = self.client.get(self.report_url(run))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "Critical held", status_code=404)
        self.assertNotContains(response, run.scope_label, status_code=404)

    def test_missing_scope_returns_404(self) -> None:
        self.enable_review()
        run = self.create_run()
        component_pk = self.component.pk
        run2 = JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.COMPONENT,
            scope_id="999999999",
            scope_label="gone",
            scope_path="/",
            requested_mode="judge",
            cap=10,
            status=JudgeRun.Status.COMPLETED,
        )
        self.assertNotEqual(run2.scope_id, str(component_pk))
        response = self.client.get(self.report_url(run2))
        self.assertEqual(response.status_code, 404)
        # A real, currently accessible run still renders normally.
        response = self.client.get(self.report_url(run))
        self.assertEqual(response.status_code, 200)

    def test_pagination_and_outcome_filter_remain_bounded_on_large_fixtures(
        self,
    ) -> None:
        self.enable_review()
        run = self.create_run()
        for offset in range(60):
            self.add_row(
                run,
                unit_id_snapshot=910000 + offset,
                outcome=JudgeRunUnit.Outcome.MINOR,
            )
        first_page = self.client.get(self.report_url(run), {"outcome": "minor"})
        self.assertEqual(len(first_page.context["page_obj"]), 50)
        self.assertEqual(first_page.context["page_obj"].paginator.count, 60)
        second_page = self.client.get(
            self.report_url(run), {"outcome": "minor", "page": 2}
        )
        self.assertEqual(len(second_page.context["page_obj"]), 10)

    def test_unknown_outcome_filter_is_rejected(self) -> None:
        self.enable_review()
        run = self.create_run()
        response = self.client.get(self.report_url(run), {"outcome": "made-up"})
        self.assertEqual(response.status_code, 404)

    def test_no_query_path_emits_a_cost_figure(self) -> None:
        self.enable_review()
        run = self.create_run()
        self.add_row(run, self.get_unit(), outcome=JudgeRunUnit.Outcome.MAJOR)
        for key in (
            "",
            "matched",
            "major",
            "cached",
            "repaired",
            "escalated",
        ):
            response = self.client.get(
                self.report_url(run), {"outcome": key} if key else {}
            )
            content = response.content.decode().lower()
            self.assertNotIn("cost", content)
            self.assertNotIn("$", content)

    def test_report_page_query_count_does_not_grow_with_row_count(self) -> None:
        self.enable_review()
        run = self.create_run()
        for offset in range(5):
            self.add_row(
                run,
                unit_id_snapshot=920000 + offset,
                outcome=JudgeRunUnit.Outcome.MINOR,
            )
        run2 = self.create_run()
        for offset in range(45):
            self.add_row(
                run2,
                unit_id_snapshot=930000 + offset,
                outcome=JudgeRunUnit.Outcome.MINOR,
            )
        # Warm permission/content-type caches identically before either
        # capture: an uncached first request is not comparable to a second.
        self.client.get(self.report_url(run))
        self.client.get(self.report_url(run2))

        with CaptureQueriesContext(connection) as small:
            self.client.get(self.report_url(run))
        with CaptureQueriesContext(connection) as large:
            self.client.get(self.report_url(run2))

        self.assertEqual(len(small), len(large))


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
class JudgeProducerTriageViewTest(ViewTestCase):
    """Producer one-unit re-check and candidate-generation endpoints."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        # unit.review is gated on review being enabled for the language:
        # without it every denial comes from the feature switch, not the
        # permission the tests are about.
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def grant(self, codenames) -> None:
        # Two custom roles mirror the built-in split the form gate relies
        # on: review on the unit, automatic translation on the language.
        # Drop superuser: otherwise the denial tests would pass through
        # the backstop rather than the role being probed.
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        role = Role.objects.create(name="Producer triage")
        for codename in codenames:
            role.permissions.add(Permission.objects.get(codename=codename))
        group = Group.objects.create(name="Producer triagers")
        group.roles.add(role)
        self.user.groups.add(group)
        self.user.clear_permissions_cache()

    def make_verdict(self, unit, severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault("context_hash", judge_context_hash(unit))
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        verdict = JudgeVerdict.objects.create(
            unit=unit, max_severity=severity, **kwargs
        )
        unit.run_checks()
        return verdict

    # -- permissions ------------------------------------------------------

    def test_recheck_denied_without_permissions(self) -> None:
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        unit = self.get_unit()
        response = self.client.post(
            reverse("judge-recheck", kwargs={"pk": unit.pk}), follow=True
        )
        self.assertEqual(response.status_code, 403)

    def test_recheck_denied_with_review_only(self) -> None:
        self.grant(["unit.review"])
        unit = self.get_unit()
        response = self.client.post(
            reverse("judge-recheck", kwargs={"pk": unit.pk}), follow=True
        )
        self.assertEqual(response.status_code, 403)

    def test_generate_denied_with_auto_only(self) -> None:
        self.grant(["translation.auto"])
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        response = self.client.post(
            reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 403)

    # -- re-check queueing -------------------------------------------------

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_two_rapid_posts_queue_one_run(self) -> None:
        unit = self.get_unit()
        url = reverse("judge-recheck", kwargs={"pk": unit.pk})
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-1"),
            ) as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            first = self.client.post(url, follow=True)
            second = self.client.post(url, follow=True)
        runs = JudgeRun.objects.filter(requested_mode="recheck")
        self.assertEqual(runs.count(), 1)
        run = runs.get()
        self.assertEqual(run.status, JudgeRun.Status.QUEUED)
        self.assertEqual(run.requested_query, recheck_query(unit.pk))
        self.assertEqual(run.cap, 1)
        self.assertEqual(run.task_id, "task-1")
        self.assertEqual(delay.call_count, 1)
        self.assertContains(first, "re-check has been queued", status_code=200)
        self.assertContains(second, "already running", status_code=200)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_recheck_after_a_finished_run_queues_a_new_one(self) -> None:
        unit = self.get_unit()
        run = self._completed_recheck(unit)
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-2"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(
                reverse("judge-recheck", kwargs={"pk": unit.pk}), follow=True
            )
        self.assertEqual(JudgeRun.objects.filter(requested_mode="recheck").count(), 2)
        self.assertNotEqual(
            JudgeRun.objects.exclude(pk=run.pk).get().status, JudgeRun.Status.COMPLETED
        )

    def _completed_recheck(self, unit) -> JudgeRun:
        translation = unit.translation
        return JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(translation.pk),
            scope_label=str(translation),
            scope_path=translation.get_absolute_url(),
            requested_query=recheck_query(unit.pk),
            requested_mode="recheck",
            cap=1,
            status=JudgeRun.Status.COMPLETED,
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_broker_failure_marks_the_run_failed(self) -> None:
        unit = self.get_unit()
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                side_effect=RuntimeError("broker down"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("judge-recheck", kwargs={"pk": unit.pk}), follow=True
            )
        run = JudgeRun.objects.get(requested_mode="recheck")
        self.assertEqual(run.status, JudgeRun.Status.FAILED)
        self.assertTrue(run.failure)
        self.assertContains(response, "re-check has been queued", status_code=200)

    def test_recheck_get_is_rejected(self) -> None:
        unit = self.get_unit()
        response = self.client.get(reverse("judge-recheck", kwargs={"pk": unit.pk}))
        self.assertEqual(response.status_code, 405)

    # -- unit-page badge context ------------------------------------------

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_unit_page_reports_a_pending_recheck(self) -> None:
        unit = self.get_unit()
        self._completed_recheck(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertIsNone(response.context["judge_recheck_pending"])
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-3"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(reverse("judge-recheck", kwargs={"pk": unit.pk}))
        response = self.client.get(unit.get_absolute_url())
        run = response.context["judge_recheck_pending"]
        self.assertIsNotNone(run)
        self.assertEqual(run.requested_mode, "recheck")

    def test_unit_page_exposes_the_active_candidate(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        response = self.client.get(unit.get_absolute_url())
        self.assertIsNone(response.context["judge_candidate"])
        Suggestion.objects.add(
            unit,
            ["better text"],
            request=self.get_request(),
            vote=False,
            user=self.user,
            raise_exception=False,
            userdetails={
                "kind": "judge-repair",
                "schema": 1,
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
                "target_hash": compute_target_hash(unit.get_target_plurals()),
                "context_hash": judge_context_hash(unit),
                "engine": "openrouter",
            },
        )
        unit = self.get_unit()
        response = self.client.get(unit.get_absolute_url())
        candidate = response.context["judge_candidate"]
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.target.strip(), "better text")
        self.assertFalse(response.context["judge_generation_pending"])

    def test_candidate_disappears_with_a_stale_target_hash(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        Suggestion.objects.add(
            unit,
            ["better text"],
            request=self.get_request(),
            vote=False,
            user=self.user,
            raise_exception=False,
            userdetails={
                "kind": "judge-repair",
                "schema": 1,
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
                "target_hash": "0" * 64,
                "context_hash": judge_context_hash(unit),
                "engine": "openrouter",
            },
        )
        response = self.client.get(self.get_unit().get_absolute_url())
        self.assertIsNone(response.context["judge_candidate"])

    # -- candidate generation ---------------------------------------------

    def test_generate_pass_verdict_is_refused(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "none")
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate"
        ) as generate:
            response = self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                follow=True,
            )
        generate.assert_not_called()
        self.assertContains(response, "no longer expects a candidate", status_code=200)

    def test_generate_resolved_verdict_is_refused(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        verdict.resolution = JudgeVerdict.Resolution.ESCALATED
        verdict.save(update_fields=["resolution"])
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate"
        ) as generate:
            response = self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                follow=True,
            )
        generate.assert_not_called()
        self.assertContains(response, "no longer expects a candidate", status_code=200)

    def test_generate_pending_lock_reports_busy(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        cache.add(_generation_lock_key(unit.pk, verdict.pk), "1", timeout=60)
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate"
        ) as generate:
            response = self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                follow=True,
            )
        generate.assert_not_called()
        self.assertContains(response, "already being generated", status_code=200)

    def test_generate_eager_success_reports_stored(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate",
            return_value="generated",
        ) as generate:
            response = self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                follow=True,
            )
        generate.assert_called_once_with(
            unit_id=unit.pk, verdict_id=verdict.pk, user_id=self.user.pk, replace=False
        )
        self.assertContains(response, "candidate has been stored", status_code=200)

    def test_generate_eager_failure_keeps_previous_candidate(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate",
            return_value="failed",
        ):
            response = self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                follow=True,
            )
        self.assertContains(response, "previous candidate remains", status_code=200)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_generate_non_eager_dispatches_with_kwargs(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate.delay"
        ) as delay:
            response = self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                {"replace": "1"},
                follow=True,
            )
        delay.assert_called_once_with(
            unit_id=unit.pk, verdict_id=verdict.pk, user_id=self.user.pk, replace=True
        )
        self.assertContains(response, "generation has been queued", status_code=200)

    def test_generate_replace_bypasses_the_pending_gate(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        cache.add(_generation_lock_key(unit.pk, verdict.pk), "1", timeout=60)
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate",
            return_value="generated",
        ) as generate:
            self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                {"replace": "1"},
            )
        self.assertTrue(generate.call_args.kwargs["replace"])

    # -- candidate acceptance ----------------------------------------------

    def make_candidate(self, unit, verdict, target="Better translation"):
        suggestion, _result = Suggestion.objects.add(
            unit,
            [target],
            request=self.get_request(),
            vote=False,
            raise_exception=False,
            userdetails={
                "kind": "judge-repair",
                "schema": 1,
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
                "target_hash": compute_target_hash(unit.get_target_plurals()),
                "context_hash": judge_context_hash(unit),
                "engine": "openrouter",
            },
        )
        return suggestion

    def test_accept_denied_without_permissions(self) -> None:
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        response = self.client.post(
            reverse("judge-accept-candidate", kwargs={"pk": candidate.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())

    def test_accept_denied_with_review_only(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        self.grant(["unit.review"])
        response = self.client.post(
            reverse("judge-accept-candidate", kwargs={"pk": candidate.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_accept_denied_with_auto_only(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        self.grant(["translation.auto"])
        response = self.client.post(
            reverse("judge-accept-candidate", kwargs={"pk": candidate.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_accept_missing_suggestion_is_404(self) -> None:
        response = self.client.post(
            reverse("judge-accept-candidate", kwargs={"pk": 999999}),
            follow=True,
        )
        self.assertEqual(response.status_code, 404)

    def test_accept_get_is_rejected(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        response = self.client.get(
            reverse("judge-accept-candidate", kwargs={"pk": candidate.pk})
        )
        self.assertEqual(response.status_code, 405)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_accept_success_holds_fuzzy_queues_recheck_and_redirects(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-1"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("judge-accept-candidate", kwargs={"pk": candidate.pk}),
                follow=True,
            )
        self.assertContains(response, "suggested fix has been applied", status_code=200)
        refreshed = self.get_unit()
        self.assertEqual(refreshed.state, STATE_FUZZY)
        self.assertFalse(Suggestion.objects.filter(pk=candidate.pk).exists())
        self.assertEqual(JudgeRun.objects.filter(requested_mode="recheck").count(), 1)

    def test_accept_stale_target_shows_error_and_keeps_candidate(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        unit.translate(self.user, ["Drifted away"], STATE_TRANSLATED)
        response = self.client.post(
            reverse("judge-accept-candidate", kwargs={"pk": candidate.pk}),
            follow=True,
        )
        self.assertContains(
            response, "no longer matches the current text", status_code=200
        )
        self.assertTrue(Suggestion.objects.filter(pk=candidate.pk).exists())
        self.assertEqual(self.get_unit().target, "Drifted away\n")

    # -- Task 8: auto-advance only after success ---------------------------

    def _next_urls(self, unit):
        page = self.client.get(unit.get_absolute_url())
        return page.context["this_unit_url"], page.context["next_unit_url"]

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_successful_accept_advances_to_the_next_unit(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        this_unit_url, next_unit_url = self._next_urls(unit)
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-1"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("judge-accept-candidate", kwargs={"pk": candidate.pk}),
                {"next": this_unit_url, "success_next": next_unit_url},
            )
        self.assertRedirects(response, next_unit_url, fetch_redirect_response=False)

    def test_failed_accept_stays_on_the_current_unit(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        candidate = self.make_candidate(unit, verdict)
        this_unit_url, next_unit_url = self._next_urls(unit)
        unit.translate(self.user, ["Drifted away"], STATE_TRANSLATED)
        response = self.client.post(
            reverse("judge-accept-candidate", kwargs={"pk": candidate.pk}),
            {"next": this_unit_url, "success_next": next_unit_url},
        )
        self.assertRedirects(response, this_unit_url, fetch_redirect_response=False)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_generate_never_advances_even_on_success(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        this_unit_url, next_unit_url = self._next_urls(unit)
        with mock.patch(
            "weblate.trans.views.edit.generate_judge_candidate",
            return_value="generated",
        ):
            response = self.client.post(
                reverse("judge-generate-candidate", kwargs={"pk": verdict.pk}),
                # A generate form never submits success_next, but even a
                # crafted request carrying one must not advance: queued
                # work is not a completed decision.
                {"next": this_unit_url, "success_next": next_unit_url},
            )
        self.assertRedirects(response, this_unit_url, fetch_redirect_response=False)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_recheck_never_advances_even_when_queued(self) -> None:
        unit = self.get_unit()
        this_unit_url, next_unit_url = self._next_urls(unit)
        with (
            mock.patch(
                "weblate.trans.tasks.auto_translate.delay",
                return_value=SimpleNamespace(id="task-1"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("judge-recheck", kwargs={"pk": unit.pk}),
                {"next": this_unit_url, "success_next": next_unit_url},
            )
        self.assertRedirects(response, this_unit_url, fetch_redirect_response=False)


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-test",
    JUDGE_MODEL_SEAT_1="vendor-a/model",
    JUDGE_MODEL_SEAT_2="vendor-b/model",
    JUDGE_MAX_REPAIR_ATTEMPTS=1,
)
class JudgeVerdictCardRenderTest(ViewTestCase):
    """Task 5: the embedded triage states the verdict card renders."""

    def setUp(self) -> None:
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.component.project.translation_review = True
        self.component.project.save(update_fields=["translation_review"])

    def grant_only(self, codenames) -> None:
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])
        # Clear default/baseline group membership too: otherwise a
        # basic-tier permission such as machinery.view leaks in from the
        # user's normal "Users" group and this helper can no longer prove
        # a permission's absence, only its presence.
        self.user.groups.clear()
        role = Role.objects.create(name="Card render permissions")
        for codename in codenames:
            role.permissions.add(Permission.objects.get(codename=codename))
        # project_selection/language_selection default to SELECTION_MANUAL
        # (applies to nothing until projects/languages are added
        # explicitly); SELECTION_ALL makes the grant apply everywhere,
        # which is what a scopeless test role needs.
        group = Group.objects.create(
            name="Card render testers",
            project_selection=SELECTION_ALL,
            language_selection=SELECTION_ALL,
        )
        group.roles.add(role)
        self.user.groups.add(group)
        self.user.clear_permissions_cache()

    def make_verdict(self, unit, severity, **kwargs):
        kwargs.setdefault("target_hash", compute_target_hash(unit.get_target_plurals()))
        kwargs.setdefault(
            "target_storage_hash", compute_target_storage_hash(unit.target)
        )
        kwargs.setdefault("context_hash", judge_context_hash(unit))
        kwargs.setdefault("judge_model", "vendor/model-a")
        kwargs.setdefault("seat", 1)
        verdict = JudgeVerdict.objects.create(
            unit=unit, max_severity=severity, **kwargs
        )
        unit.run_checks()
        return verdict

    def make_candidate(self, unit, verdict, target="Better translation"):
        suggestion, _result = Suggestion.objects.add(
            unit,
            [target],
            request=self.get_request(),
            vote=False,
            raise_exception=False,
            userdetails={
                "kind": "judge-repair",
                "schema": 1,
                "judge_verdict_id": verdict.pk,
                "judge_run_id": str(verdict.run_id),
                "target_hash": compute_target_hash(unit.get_target_plurals()),
                "context_hash": judge_context_hash(unit),
                "engine": "openrouter",
            },
        )
        return suggestion

    def _completed_recheck(self, unit) -> JudgeRun:
        translation = unit.translation
        return JudgeRun.objects.create(
            actor=self.user,
            scope_type=JudgeRun.ScopeType.TRANSLATION,
            scope_id=str(translation.pk),
            scope_label=str(translation),
            scope_path=translation.get_absolute_url(),
            requested_query=recheck_query(unit.pk),
            requested_mode="recheck",
            cap=1,
            status=JudgeRun.Status.QUEUED,
        )

    def test_stale_shows_only_recheck(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, "critical")
        unit.translate(self.user, ["Manually edited away"], STATE_TRANSLATED)
        response = self.client.get(self.get_unit().get_absolute_url())
        self.assertContains(response, "Re-check this string")
        self.assertNotContains(response, "Use suggested fix")
        self.assertNotContains(response, "Generate suggested fix")
        self.assertNotContains(response, "Record decision")

    def test_queued_recheck_shows_status_not_button(self) -> None:
        unit = self.get_unit()
        # A recheck-pending state needs the card itself to render, which
        # needs at least one existing verdict/round.
        self.make_verdict(unit, "critical")
        self._completed_recheck(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Re-checking this string")
        self.assertNotContains(response, "Re-check this string<")

    def test_a_pending_recheck_suppresses_every_candidate_action(self) -> None:
        # The re-check decides what the card may offer next; applying or
        # regenerating a candidate while it is in flight races that answer.
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        self.make_candidate(unit, verdict)
        self._completed_recheck(unit)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Re-checking this string")
        self.assertNotContains(response, "Use suggested fix")
        self.assertNotContains(response, "Generate another")
        self.assertNotContains(response, "Generate suggested fix")

    def test_a_pending_generation_replaces_the_generate_another_button(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        self.make_candidate(unit, verdict)
        cache.set(_generation_lock_key(unit.pk, verdict.pk), "1", 60)
        self.addCleanup(cache.delete, _generation_lock_key(unit.pk, verdict.pk))
        response = self.client.get(unit.get_absolute_url())
        # The preview stays usable, but a second paid generation must not be
        # offered while one is already running.
        self.assertContains(response, "Use suggested fix")
        self.assertContains(response, "Generating a suggested fix")
        self.assertNotContains(response, "Generate another")

    def test_current_candidate_shows_diff_provenance_and_actions(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        self.make_candidate(unit, verdict, target="A much better translation")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Suggested fix")
        self.assertContains(response, "A much better translation")
        self.assertContains(response, "Use suggested fix")
        self.assertContains(response, "Generate another")
        # normal editor access is untouched
        self.assertContains(response, 'name="target_0"')

    def test_no_candidate_shows_generate_and_resolution(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, "critical")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Generate suggested fix")
        self.assertContains(response, "Record decision")
        # "Automatic suggestions" alone also matches an unrelated keyboard
        # shortcuts help entry always present on the page.
        self.assertContains(response, "Computer-aided translation suggestions")
        self.assertNotContains(response, "Use suggested fix")

    def test_automatic_suggestions_fallback_needs_machinery_permission(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, "critical")
        self.grant_only(["unit.review", "translation.auto"])
        self.assertFalse(self.user.has_perm("machinery.view", unit.translation))
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Generate suggested fix")
        # "Automatic suggestions" alone also matches an unrelated keyboard
        # shortcuts help entry always present on the page; the card's
        # fallback link is uniquely identified by its title text.
        self.assertNotContains(response, "Computer-aided translation suggestions")

    def test_generation_pending_hides_generate_button(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        cache.add(_generation_lock_key(unit.pk, verdict.pk), "1", timeout=60)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Generating a suggested fix")
        self.assertNotContains(response, "Generate suggested fix<")

    def test_resolved_verdict_never_shows_candidate_controls(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "critical")
        self.make_candidate(unit, verdict)
        resolve_verdict(
            unit=unit,
            expected_verdict_id=verdict.pk,
            actor=self.user,
            resolution=JudgeVerdict.Resolution.ESCALATED,
            reason="needs a human look",
        )
        response = self.client.get(self.get_unit().get_absolute_url())
        self.assertNotContains(response, "Use suggested fix")
        self.assertNotContains(response, "Generate suggested fix")
        self.assertContains(response, "Escalated for review")

    def test_minor_never_shows_candidate_controls(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, "minor")
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Use suggested fix")
        self.assertNotContains(response, "Generate suggested fix")

    def test_flag_renders_same_candidate_controls_as_reject(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)
        self.make_candidate(unit, verdict)
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Ships with evidence")
        self.assertContains(response, "Use suggested fix")
        self.assertContains(response, "Generate another")

    def test_active_max_length_hides_candidate_and_generate(self) -> None:
        unit = self.get_unit()
        unit.extra_flags = "max-length:5"
        unit.save(update_fields=["extra_flags"], same_content=True)
        unit.translate(self.user, ["a much longer raw target"], STATE_TRANSLATED)
        unit = self.get_unit()
        unit.run_checks()
        self.assertTrue(unit.has_check("max-length"))
        self.make_verdict(unit, "critical")
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Use suggested fix")
        self.assertNotContains(response, "Generate suggested fix")
        # Re-check and the resolution form are unrelated to the mutating
        # path and remain available.
        self.assertContains(response, "Re-check this string")

    # -- Task 6: compressed two-seat evidence ------------------------------

    def test_reject_evidence_is_collapsed_into_one_details_element(self) -> None:
        unit = self.get_unit()
        self.make_verdict(
            unit,
            "critical",
            errors=[
                {
                    "category": "terminology",
                    "severity": "critical",
                    "description": "Wrong term used for the key item.",
                }
            ],
            back_translation="This is the reverse-translated text.",
        )
        response = self.client.get(unit.get_absolute_url())
        content = response.content.decode()
        self.assertEqual(content.count("<details"), 1)
        self.assertIn("<summary>", content)
        self.assertContains(response, "Wrong term used for the key item.")
        self.assertContains(response, "vendor/model-a")
        self.assertContains(response, "This is the reverse-translated text.")
        self.assertContains(response, "Back-translation:")
        # Severity/ship-hold badges stay outside (before) the details.
        self.assertLess(content.index("Will not ship"), content.index("<details"))

    def test_flag_evidence_also_collapses_into_one_details_element(self) -> None:
        unit = self.get_unit()
        self.make_verdict(
            unit,
            "major",
            errors=[
                {
                    "category": "tone",
                    "severity": "major",
                    "description": "Overly formal register.",
                }
            ],
        )
        response = self.client.get(unit.get_absolute_url())
        content = response.content.decode()
        self.assertEqual(content.count("<details"), 1)
        self.assertContains(response, "Overly formal register.")
        self.assertLess(content.index("Ships with evidence"), content.index("<details"))

    def test_minor_pass_evidence_also_collapses_into_one_details_element(
        self,
    ) -> None:
        unit = self.get_unit()
        self.make_verdict(
            unit,
            "minor",
            errors=[
                {
                    "category": "style",
                    "severity": "minor",
                    "description": "Slightly awkward phrasing.",
                }
            ],
        )
        response = self.client.get(unit.get_absolute_url())
        content = response.content.decode()
        self.assertEqual(content.count("<details"), 1)
        self.assertContains(response, "Slightly awkward phrasing.")

    def test_repair_evidence_also_collapses_into_a_details_element(self) -> None:
        unit = self.get_unit()
        run_id = uuid.uuid4()
        # attempt 0: the originally judged (pre-repair) text and its errors.
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="critical",
            seat=1,
            judge_model="vendor/model-a",
            run_id=run_id,
            attempt=0,
            target_hash="0" * 64,
            target_storage_hash="x" * 32,
            context_hash="0" * 64,
            errors=[
                {
                    "category": "markup",
                    "severity": "critical",
                    "description": "Broken tag in the translation.",
                }
            ],
        )
        # attempt 1: the current, repaired text - matches the unit as it
        # stands now, so it becomes the active/current verdict.
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="minor",
            seat=1,
            judge_model="vendor/model-a",
            run_id=run_id,
            attempt=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            target_storage_hash=compute_target_storage_hash(unit.target),
            context_hash=judge_context_hash(unit),
        )
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Repaired since the original verdict")
        self.assertContains(response, "Broken tag in the translation.")

    # -- Task 7: naming remaining outcomes ---------------------------------

    def test_fresh_flag_offers_keep_as_is_and_escalate_directly(self) -> None:
        unit = self.get_unit()
        verdict = self.make_verdict(unit, "major")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Record decision")
        form = response.context["judge_resolution_form"]
        choices = {value for value, _label in form.fields["resolution"].choices}
        self.assertEqual(
            choices,
            {JudgeVerdict.Resolution.ESCALATED, JudgeVerdict.Resolution.ACCEPTED_AS_IS},
        )
        self.assertEqual(verdict.verdict, JudgeVerdict.Verdict.FLAG)

    def test_minor_shows_ai_variants_fallback_but_no_resolution_form(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, "minor")
        response = self.client.get(unit.get_absolute_url())
        self.assertContains(response, "Computer-aided translation suggestions")
        self.assertNotContains(response, "Record decision")
        self.assertNotContains(response, "Use suggested fix")
        self.assertNotContains(response, "Generate suggested fix")

    def test_minor_ai_variants_fallback_needs_machinery_permission(self) -> None:
        unit = self.get_unit()
        self.make_verdict(unit, "minor")
        self.grant_only(["unit.review", "translation.auto"])
        response = self.client.get(unit.get_absolute_url())
        self.assertNotContains(response, "Computer-aided translation suggestions")
