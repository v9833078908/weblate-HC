# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later


import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from weblate.trans.models.judge import (
    JudgeVerdict,
    compute_target_hash,
    compute_target_storage_hash,
)
from weblate.trans.tests.test_views import ViewTestCase
from weblate.utils.state import (
    STATE_APPROVED,
    STATE_EMPTY,
    STATE_FUZZY,
    STATE_READONLY,
    STATE_TRANSLATED,
)
from weblate.utils.stats import (
    STATS_PARENTS_STATE_TTL,
    STATS_PREFETCH_CHUNK_SIZE,
    BaseStats,
    TranslationStats,
    _get_parents_state,
    _parents_state_key,
    begin_parents_update,
    fail_parents_update,
    finish_parents_update,
    prefetch_stats,
    run_parents_update,
    schedule_parents_update,
)


class StubStats(BaseStats):
    def __init__(self, identifier: int) -> None:
        super().__init__(None)
        self.identifier = identifier

    @property
    def cache_key(self) -> str:
        return f"stub-stats-{self.identifier}"

    def _calculate_basic(self) -> None:
        raise AssertionError


class StubObject:
    def __init__(self, identifier: int) -> None:
        self.identifier = identifier
        self.stats = StubStats(identifier)


class StatsPrefetchTest(SimpleTestCase):
    def test_aggregate_outdated_stats(self) -> None:
        stats = StubStats(1)
        stats.set_data({"all": 1})

        for key in (
            "unapproved",
            "unapproved_chars",
            "unapproved_words",
            "recent_changes",
            "monthly_changes",
            "total_changes",
            "stats_timestamp",
        ):
            with self.subTest(key=key):
                self.assertEqual(stats.aggregate_get(key), 0)

    def test_prefetch_is_bounded_and_preserves_order(self) -> None:
        count = 2 * STATS_PREFETCH_CHUNK_SIZE + 1
        objects = [StubObject(identifier) for identifier in range(count)]

        with patch("weblate.utils.stats.cache.get_many", return_value={}) as get_many:
            result = prefetch_stats(item for item in objects)

        self.assertEqual(
            [item.identifier for item in result],
            list(range(count)),
        )
        self.assertEqual(
            [len(call.args[0]) for call in get_many.call_args_list],
            [STATS_PREFETCH_CHUNK_SIZE, STATS_PREFETCH_CHUNK_SIZE, 1],
        )
        self.assertTrue(all(item.stats.is_loaded for item in objects))

    def test_update_dependencies_are_deduplicated_in_order(self) -> None:
        root = StubStats(0)
        dependencies: list[BaseStats] = [
            StubStats(1),
            StubStats(2),
            StubStats(1),
            StubStats(3),
        ]
        root._collected_update_objects = dependencies  # ruff: ignore[private-member-access]
        for stats in dependencies:
            stats.set_data({})

        self.assertEqual(
            [stats.cache_key for stats in root._iterate_update_objects()],  # ruff: ignore[private-member-access]
            ["stub-stats-1", "stub-stats-2", "stub-stats-3"],
        )

    def test_snapshot_buckets_cover_all_delta_keys(self) -> None:
        covered: set[str] = set()
        for state in (
            STATE_EMPTY,
            STATE_FUZZY,
            STATE_TRANSLATED,
            STATE_APPROVED,
            STATE_READONLY,
        ):
            for flags in range(32):
                covered.update(
                    TranslationStats.snapshot_to_bucket(
                        {
                            "state": state,
                            "num_words": 2,
                            "num_chars": 3,
                            "active_checks_count": flags & 1,
                            "dismissed_checks_count": flags & 2,
                            "suggestion_count": flags & 4,
                            "label_count": flags & 8,
                            "comment_count": flags & 16,
                        }
                    )
                )

        self.assertEqual(covered, TranslationStats.UNIT_DELTA_KEYS)


class ParentsUpdateSchedulerTest(TransactionTestCase):
    def setUp(self) -> None:
        self.pk = 1
        self.task = SimpleNamespace(
            name=f"weblate.utils.tests.parents.{uuid.uuid4().hex}",
            request=SimpleNamespace(id=None),
            apply_async=Mock(),
        )

    def get_state(self):
        return _get_parents_state(self.task.name, self.pk)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_pending_and_dirty_saves_publish_one_task_each(self) -> None:
        for _ in range(100):
            schedule_parents_update(self.task, self.pk)

        self.task.apply_async.assert_called_once()
        first_token = self.task.apply_async.call_args.kwargs["task_id"]
        self.assertEqual(self.get_state(), {"phase": "pending", "token": first_token})

        self.task.request.id = first_token
        run_parents_update(
            self.task,
            self.pk,
            lambda: [schedule_parents_update(self.task, self.pk) for _ in range(100)],
        )

        self.assertEqual(self.task.apply_async.call_count, 2)
        followup = self.task.apply_async.call_args.kwargs
        self.assertNotEqual(followup["task_id"], first_token)
        self.assertEqual(followup["countdown"], 1)
        self.assertEqual(
            self.get_state(), {"phase": "pending", "token": followup["task_id"]}
        )

    def test_stale_delivery_does_not_recalculate_current_reservation(self) -> None:
        schedule_parents_update(self.task, self.pk)
        active_token = self.task.apply_async.call_args.kwargs["task_id"]
        calculate = Mock()
        self.task.request.id = "stale-task-id"

        run_parents_update(self.task, self.pk, calculate)

        calculate.assert_not_called()
        self.assertEqual(self.get_state(), {"phase": "pending", "token": active_token})
        self.task.apply_async.assert_called_once()

    def test_failed_owner_keeps_newer_reservation(self) -> None:
        cache.set(
            _parents_state_key(self.task.name, self.pk),
            {"phase": "pending", "token": "newer-task-id"},
            STATS_PARENTS_STATE_TTL,
        )

        fail_parents_update(self.task.name, self.pk, "failed-task-id")

        self.assertEqual(
            self.get_state(), {"phase": "pending", "token": "newer-task-id"}
        )

    def test_calculation_error_releases_its_own_reservation(self) -> None:
        schedule_parents_update(self.task, self.pk)
        self.task.request.id = self.task.apply_async.call_args.kwargs["task_id"]

        with self.assertRaisesRegex(RuntimeError, "stats failed"):
            run_parents_update(
                self.task,
                self.pk,
                lambda: (_ for _ in ()).throw(RuntimeError("stats failed")),
            )

        self.assertIsNone(self.get_state())

    def test_translation_and_language_parent_tasks_coalesce_independently(
        self,
    ) -> None:
        language_task = SimpleNamespace(
            name="weblate.utils.tasks.update_language_stats_parents",
            request=SimpleNamespace(id=None),
            apply_async=Mock(),
        )
        self.task.name = "weblate.utils.tasks.update_translation_stats_parents"

        for _ in range(100):
            schedule_parents_update(self.task, self.pk)
            schedule_parents_update(language_task, self.pk)

        self.task.apply_async.assert_called_once()
        language_task.apply_async.assert_called_once()

    def test_successful_calculation_releases_reservation(self) -> None:
        schedule_parents_update(self.task, self.pk)
        self.task.request.id = self.task.apply_async.call_args.kwargs["task_id"]
        calculate = Mock()

        run_parents_update(self.task, self.pk, calculate)

        calculate.assert_called_once()
        self.assertIsNone(self.get_state())

    def test_followup_publishes_only_after_calculation_returns(self) -> None:
        events: list[str] = []

        def publish(**kwargs) -> None:
            if "countdown" in kwargs:
                self.assertEqual(events, ["calculate", "returned"])

        self.task.apply_async.side_effect = publish
        schedule_parents_update(self.task, self.pk)
        self.task.request.id = self.task.apply_async.call_args.kwargs["task_id"]

        def calculate() -> None:
            events.append("calculate")
            schedule_parents_update(self.task, self.pk)
            events.append("returned")

        run_parents_update(self.task, self.pk, calculate)

        self.assertEqual(self.task.apply_async.call_count, 2)
        self.assertEqual(self.task.apply_async.call_args.kwargs["countdown"], 1)

    def test_save_after_finish_publishes_fresh_reservation(self) -> None:
        schedule_parents_update(self.task, self.pk)
        token = self.task.apply_async.call_args.kwargs["task_id"]
        self.assertTrue(begin_parents_update(self.task.name, self.pk, token))
        self.assertIsNone(finish_parents_update(self.task.name, self.pk, token))

        schedule_parents_update(self.task, self.pk)

        self.assertEqual(self.task.apply_async.call_count, 2)
        self.assertNotEqual(
            self.task.apply_async.call_args.kwargs["task_id"],
            token,
        )

    def test_publication_failure_releases_only_failed_reservation(self) -> None:
        self.task.apply_async.side_effect = OSError("broker unavailable")

        with self.assertRaisesRegex(OSError, "broker unavailable"):
            schedule_parents_update(self.task, self.pk)

        self.assertIsNone(self.get_state())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_translation_stats_save_without_parent_update_does_not_schedule(
        self,
    ) -> None:
        stats = TranslationStats(
            SimpleNamespace(
                pk=self.pk,
                cache_key=f"parents-update-{uuid.uuid4().hex}",
                is_source=False,
            )
        )

        with patch("weblate.utils.stats.schedule_parents_update") as schedule:
            stats.save(update_parents=False)

        schedule.assert_not_called()


class JudgeStatsTest(ViewTestCase):
    def add_verdict(
        self,
        unit,
        severity: str,
        *,
        unparsed: bool = False,
        stale=False,
        seat: int = 1,
        run_id: uuid.UUID | None = None,
    ):
        return JudgeVerdict.objects.create(
            unit=unit,
            max_severity=severity,
            unparsed=unparsed,
            judge_model="vendor/model",
            seat=seat,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            target_storage_hash=(
                "old-target" if stale else compute_target_storage_hash(unit.target)
            ),
            context_hash="context",
            run_id=run_id or uuid.uuid4(),
        )

    def refresh_stats(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            self.translation.invalidate_cache()

    def test_translation_judge_stats_are_target_fresh(self) -> None:
        unit = self.get_unit()
        JudgeVerdict.objects.create(
            unit=unit,
            max_severity="major",
            judge_model="vendor/model",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            target_storage_hash=compute_target_storage_hash(unit.target),
            context_hash="context",
            run_id=uuid.uuid4(),
        )
        self.translation.invalidate_cache()

        self.assertEqual(self.translation.stats.judge_total, self.translation.stats.all)
        self.assertEqual(self.translation.stats.judge_evaluated, 1)
        self.assertEqual(self.translation.stats.judge_flag, 1)
        self.assertEqual(self.translation.stats.judge_pass, 0)
        self.assertEqual(self.translation.stats.judge_reject, 0)

    def test_translation_judge_stats_cover_all_statuses(self) -> None:
        units = list(self.translation.unit_set.order_by("pk")[:7])
        while len(units) < 7:
            seed = units[0]
            units.append(
                type(seed).objects.create(
                    translation=self.translation,
                    source_unit=seed.source_unit,
                    source=f"Extra {len(units)}",
                    target="",
                    context=f"extra-{len(units)}",
                    id_hash=seed.id_hash + len(units),
                    position=100 + len(units),
                    state=STATE_TRANSLATED,
                )
            )
        self.add_verdict(units[0], "none")
        self.add_verdict(units[1], "major")
        self.add_verdict(units[2], "critical")
        self.add_verdict(units[3], "major", stale=True)
        self.add_verdict(units[4], "none", unparsed=True)
        type(units[5]).objects.filter(pk=units[5].pk).update(state=STATE_READONLY)
        self.add_verdict(units[5], "critical")
        self.add_verdict(units[6], "minor")
        self.refresh_stats()

        stats = self.translation.stats
        self.assertEqual(stats.judge_total, stats.all - stats.readonly)
        self.assertEqual(stats.judge_evaluated, 4)
        self.assertEqual(stats.judge_pass, 2)
        self.assertEqual(stats.judge_minor, 1)
        self.assertEqual(stats.judge_flag, 1)
        self.assertEqual(stats.judge_reject, 1)
        self.assertEqual(
            stats.judge_pass + stats.judge_flag + stats.judge_reject,
            stats.judge_evaluated,
        )
        self.assertEqual(stats.judge_stale, 1)
        self.assertEqual(stats.judge_unparsed, 1)

    def test_translation_judge_resolution_stats(self) -> None:
        unit = self.get_unit()
        verdict = self.add_verdict(unit, "critical")
        verdict.resolution = "escalated"
        verdict.save(update_fields=["resolution"])
        self.refresh_stats()
        self.assertEqual(self.translation.stats.judge_escalated, 1)
        self.assertEqual(self.translation.stats.judge_resolved, 0)
        verdict.resolution = "accepted_as_is"
        verdict.save(update_fields=["resolution"])
        self.refresh_stats()
        self.assertEqual(self.translation.stats.judge_resolved, 1)
        self.assertEqual(self.translation.stats.judge_escalated, 0)

    def test_translation_judge_needs_human_stats(self) -> None:
        units = list(self.translation.unit_set.order_by("pk")[:5])
        while len(units) < 5:
            seed = units[0]
            units.append(
                type(seed).objects.create(
                    translation=self.translation,
                    source_unit=seed.source_unit,
                    source=f"Extra {len(units)}",
                    target="",
                    context=f"extra-{len(units)}",
                    id_hash=seed.id_hash + len(units),
                    position=100 + len(units),
                    state=STATE_TRANSLATED,
                )
            )
        # Unresolved critical: needs a human.
        self.add_verdict(units[0], "critical")
        # Accepted critical: no longer needs a human.
        accepted = self.add_verdict(units[1], "critical")
        accepted.resolution = "accepted_as_is"
        accepted.save(update_fields=["resolution"])
        # Escalated major: needs a human even though it is not critical.
        escalated_major = self.add_verdict(units[2], "major")
        escalated_major.resolution = "escalated"
        escalated_major.save(update_fields=["resolution"])
        # Escalated critical: needs a human (matches both OR clauses once).
        escalated_critical = self.add_verdict(units[3], "critical")
        escalated_critical.resolution = "escalated"
        escalated_critical.save(update_fields=["resolution"])
        # Unresolved major, never escalated: does not need a human.
        self.add_verdict(units[4], "major")
        self.refresh_stats()

        self.assertEqual(self.translation.stats.judge_needs_human, 3)

    def test_target_edit_stales_and_new_verdict_restores_coverage(self) -> None:
        unit = self.get_unit()
        self.add_verdict(unit, "major")
        self.refresh_stats()
        self.assertEqual(self.translation.stats.judge_evaluated, 1)
        type(unit).objects.filter(pk=unit.pk).update(target="changed")
        self.refresh_stats()
        self.assertEqual(self.translation.stats.judge_evaluated, 0)
        self.assertEqual(self.translation.stats.judge_stale, 1)
        unit.refresh_from_db()
        self.add_verdict(unit, "none")
        self.refresh_stats()
        self.assertEqual(self.translation.stats.judge_evaluated, 1)
        self.assertEqual(self.translation.stats.judge_pass, 1)

    def test_judge_stats_follow_the_consensus_policy(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.add_verdict(unit, "none", seat=1, run_id=run)
        self.add_verdict(unit, "critical", seat=2, run_id=run)
        self.refresh_stats()
        self.assertEqual(self.translation.stats.judge_flag, 1)
        self.assertEqual(self.translation.stats.judge_reject, 0)

    @override_settings(JUDGE_CONSENSUS_REJECT=False)
    def test_judge_stats_follow_any_critical_policy_in_rollback_mode(self) -> None:
        unit = self.get_unit()
        run = uuid.uuid4()
        self.add_verdict(unit, "none", seat=1, run_id=run)
        self.add_verdict(unit, "critical", seat=2, run_id=run)
        self.refresh_stats()
        self.assertEqual(self.translation.stats.judge_flag, 0)
        self.assertEqual(self.translation.stats.judge_reject, 1)
