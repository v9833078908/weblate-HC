# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later


import uuid
from unittest.mock import patch

from django.test import SimpleTestCase

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
    STATS_PREFETCH_CHUNK_SIZE,
    BaseStats,
    TranslationStats,
    prefetch_stats,
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


class JudgeStatsTest(ViewTestCase):
    def add_verdict(self, unit, severity: str, *, unparsed: bool = False, stale=False):
        return JudgeVerdict.objects.create(
            unit=unit,
            max_severity=severity,
            unparsed=unparsed,
            judge_model="vendor/model",
            seat=1,
            target_hash=compute_target_hash(unit.get_target_plurals()),
            target_storage_hash=(
                "old-target" if stale else compute_target_storage_hash(unit.target)
            ),
            context_hash="context",
            run_id=uuid.uuid4(),
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
        units = list(self.translation.unit_set.order_by("pk")[:6])
        while len(units) < 6:
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
        self.refresh_stats()

        stats = self.translation.stats
        self.assertEqual(stats.judge_total, stats.all - stats.readonly)
        self.assertEqual(stats.judge_evaluated, 3)
        self.assertEqual(stats.judge_pass, 1)
        self.assertEqual(stats.judge_flag, 1)
        self.assertEqual(stats.judge_reject, 1)
        self.assertEqual(stats.judge_pass + stats.judge_flag + stats.judge_reject, 3)
        self.assertEqual(stats.judge_stale, 1)
        self.assertEqual(stats.judge_unparsed, 1)

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
