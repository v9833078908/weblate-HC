# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Offline verification of the judge A/B measurement runner.

Covers the verification list of task 1 in
``docs/product/plans/2026-09-17-judge-batching-and-latency-experiments.md``:
a fake HTTP provider behind the real payload builder, reply parser and
scheduler; corrupted, duplicated, partial and reordered segment IDs; a
timeout; an auth failure; a crash between POST and persistence with a
resume; unchanged approved states; no MT, no candidates, no fallback; and
attempt attribution without PK watermarks.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import pathlib
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import httpx2
from django.conf import settings
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils.translation import activate

from weblate.trans import judge
from weblate.trans.models.judge import JudgeRequestAttempt, JudgeVerdict
from weblate.trans.models.llm_usage import LLMUsageLog
from weblate.trans.models.suggestion import Suggestion
from weblate.trans.tests.utils import RepoTestMixin, create_test_user
from weblate.utils.state import STATE_APPROVED, STATE_TRANSLATED
from weblate.utils.tests import http_mock

PROBE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "analysis"
    / "probes"
    / "judge-cost-latency-ab.py"
)
_spec = importlib.util.spec_from_file_location("judge_cost_latency_ab", PROBE_PATH)
if _spec is None or _spec.loader is None:
    msg = "cannot load the measurement runner probe"
    raise RuntimeError(msg)
probe = importlib.util.module_from_spec(_spec)
# Dataclass processing resolves the module's namespace through sys.modules.
sys.modules[_spec.name] = probe
_spec.loader.exec_module(probe)

# The mock transport intercepts after outbound URL validation, which resolves
# the hostname for real, so the fake endpoint must use a resolvable host the
# same way the existing judge client tests do.
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
SEAT_1_MODEL = "judge-ab-deepseek-test"
SEAT_2_MODEL = "judge-ab-qwen-test"
BOUNDARY_RE = re.compile(
    r"<untrusted_translation_data_[0-9a-f]+>\n(.*)\n</untrusted", re.DOTALL
)
PRICES = {
    SEAT_1_MODEL: {
        "input_cost_per_token": 6.19962e-07,
        "cache_read_input_token_cost": 7.89815e-08,
        "output_cost_per_token": 1.239924e-06,
    },
    SEAT_2_MODEL: {
        "input_cost_per_token": 2e-06,
        "cache_read_input_token_cost": 2.5e-07,
        "output_cost_per_token": 6e-06,
    },
}
BUDGET = {
    "max_unique_units": 4,
    "max_http_attempts_per_slot": 50,
    "max_wall_clock_minutes_per_slot": 10,
    "money_cap_usd": "5",
    "worst_case_completion_tokens": 512,
    "provider_side_limit": False,
    "acknowledge_residual_risk": True,
}


class TempDirMixin:
    def temp_dir(self) -> pathlib.Path:
        # Python 3.14 TemporaryDirectory.__enter__ returns the path string.
        context = self.enterContext(tempfile.TemporaryDirectory())
        return pathlib.Path(context if isinstance(context, str) else context.name)


def _segment_reply(identifier: int, severity: str = "none") -> dict:
    errors = (
        []
        if severity == "none"
        else [
            {
                "span": "x",
                "category": "terminology",
                "severity": severity,
                "description": "d",
            }
        ]
    )
    verdict = (
        "reject"
        if severity == "critical"
        else ("flag" if severity == "major" else "pass")
    )
    return {
        "id": identifier,
        "verdict": verdict,
        "errors": errors,
        "back_translation": f"bt {identifier}",
    }


def register_fake_provider(behaviors: dict[str, str]) -> object:
    """Fake chat-completions provider; the real client builds and parses."""

    def callback(request):
        payload = json.loads(request.content)
        model = payload["model"]
        content = payload["messages"][1]["content"]
        segments = json.loads(BOUNDARY_RE.search(content).group(1))["segments"]
        behavior = behaviors.get(model, "ok")
        if behavior == "ok":
            body = [_segment_reply(segment["id"]) for segment in segments]
        elif behavior == "flag-major":
            body = [_segment_reply(segment["id"], "major") for segment in segments]
        elif behavior == "duplicate-id":
            body = [_segment_reply(0) for _ in segments]
        elif behavior == "partial":
            body = [_segment_reply(segment["id"]) for segment in segments[:-1]]
        elif behavior == "swap-ids":
            ids = [segment["id"] for segment in segments][::-1]
            body = [_segment_reply(identifier) for identifier in ids]
        else:
            msg = f"unknown behavior {behavior}"
            raise AssertionError(msg)
        return httpx2.Response(
            200,
            json={
                "id": "resp-1",
                "choices": [
                    {
                        "message": {"content": json.dumps({"segments": body})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 40,
                    "prompt_tokens_details": {"cached_tokens": 10},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            },
        )

    return http_mock.register_callback("POST", CHAT_URL, callback)


def write_experiment(
    tmp: pathlib.Path,
    records: list[dict],
    *,
    arms: dict | None = None,
    repeats: int = 1,
    budget: dict | None = None,
) -> pathlib.Path:
    arms = (
        arms
        if arms is not None
        else {"A0": {"title": "control", "overrides": {"seat_2_batch_size": 1}}}
    )
    corpus = tmp / "corpus.json"
    probe._write_json(corpus, {"labels_origin": "test", "records": records})
    probe._write_json(
        tmp / "corpus.split.json",
        {
            "seed": 1,
            "held_out_fraction": 0.0,
            "assignment": {row["record_id"]: "dev" for row in records},
            "excluded": [],
        },
    )
    groups = sorted({f"{row['language']}/{row['component_slug']}" for row in records})
    slots = []
    for repeat in range(1, repeats + 1):
        for group in groups:
            for arm_id in arms:
                slots.append(
                    {
                        "id": f"s{len(slots) + 1:03d}",
                        "arm": arm_id,
                        "repeat": repeat,
                        "group": group,
                    }
                )
    manifest = {
        "schema": probe.SCHEMA,
        "plan": probe.PLAN,
        "experiment_id": "selftest",
        "artifact_dir": str(tmp),
        "registered_at": "2026-09-17T00:00:00+00:00",
        "source_commit": "test",
        "corpus": {
            "path": str(corpus),
            "sha256": probe._sha256_file(corpus),
            "records": len(records),
            "languages": sorted({row["language"] for row in records}),
            "split_seed": 1,
            "held_out_fraction": 0.0,
        },
        "seat_models": {"seat_1": SEAT_1_MODEL, "seat_2": SEAT_2_MODEL},
        "seat_1_batch_size": 2,
        "arms": arms,
        "repeats": repeats,
        "schedule": {"slots": slots},
        "est_tokens": {
            "prefix_prompt_tokens": 10,
            "per_string_prompt_tokens": 5,
            "per_string_completion_tokens": 5,
        },
        "caveats": {"observed_cache_share": 0.5},
        "prices": PRICES,
        "prices_source": "test snapshot",
        "budget": budget if budget is not None else dict(BUDGET),
        "gates": {
            "control_arm": "A0",
            "major_recall_drop_pp": 2,
            "false_flag_growth_pp": 3,
            "terminal_unparsed_growth_pp": 1,
        },
        "frozen_profiles": None,
        "excluded_records": [],
    }
    manifest_path = tmp / "manifest.json"
    probe._write_json(manifest_path, manifest)
    return manifest_path


def records_from_units(units) -> list[dict]:
    return [
        {
            "record_id": f"r{index:03d}",
            "family": f"fam-{index}",
            "stratum": "ui",
            "language": unit.translation.language.code,
            "source_language": "en",
            "source": unit.source,
            "target": probe.join_plural(unit.get_target_plurals()),
            "label": "pass",
            "severity": "",
            "project_slug": unit.translation.component.project.slug,
            "component_slug": unit.translation.component.slug,
            "unit_id": unit.id,
        }
        for index, unit in enumerate(units)
    ]


def execute_args(
    manifest_path: pathlib.Path, slot: str, **kwargs
) -> argparse.Namespace:
    return argparse.Namespace(
        manifest=str(manifest_path),
        slot=slot,
        resume=kwargs.get("resume", False),
        allow_out_of_order=kwargs.get("allow_out_of_order", False),
    )


def journal_of(manifest_path: pathlib.Path) -> dict:
    directory = pathlib.Path(json.loads(manifest_path.read_text())["artifact_dir"])
    events = [
        json.loads(line)
        for line in (directory / "journal.jsonl").read_text().splitlines()
        if line.strip()
    ]
    states: dict[str, list[str]] = {"completed": [], "interrupted": []}
    for event in events:
        if event["event"] == "slot_end" and event["slot"] not in states["completed"]:
            states["completed"].append(event["slot"])
        if event["event"] == "slot_interrupted":
            states["interrupted"].append(event["slot"])
    return {"events": events, "states": states}


def sample_record(index: int) -> dict:
    return {
        "record_id": f"r{index:03d}",
        "family": f"fam-{index}",
        "stratum": "ui",
        "language": "cs",
        "source_language": "en",
        "source": f"source {index}",
        "target": f"target {index}",
        "label": "pass",
        "severity": "",
        "project_slug": "test",
        "component_slug": "test",
        "unit_id": index + 1,
    }


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-runner-test",
    JUDGE_BASE_URL="https://openrouter.ai/api/v1",
    JUDGE_MODEL_SEAT_1=SEAT_1_MODEL,
    JUDGE_MODEL_SEAT_2=SEAT_2_MODEL,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
    JUDGE_RETRY_BUDGET_RATIO=0,
    JUDGE_REQUEST_SLEEP=0,
    JUDGE_DEFERRAL_ENABLED=False,
)
class RunnerManifestTest(TempDirMixin, SimpleTestCase):
    """Manifest, budget, split and dry-run validation, all without a database."""

    def test_budget_must_be_numeric_and_acknowledged(self) -> None:
        records = [sample_record(index) for index in range(4)]
        incomplete = dict(BUDGET, money_cap_usd=None)
        manifest_path = write_experiment(self.temp_dir(), records, budget=incomplete)
        manifest = probe.load_manifest(manifest_path, require_budget=False)
        with self.assertRaises(probe.ManifestError):
            probe.parse_budget(manifest)
        unacknowledged = dict(BUDGET, acknowledge_residual_risk=False)
        path2 = write_experiment(self.temp_dir(), records, budget=unacknowledged)
        with self.assertRaises(probe.ManifestError):
            probe.load_manifest(path2, require_budget=True)

    def test_dry_run_prints_scope_and_never_touches_the_database(self) -> None:
        records = [sample_record(index) for index in range(4)]
        manifest_path = write_experiment(
            self.temp_dir(), records, arms=json.loads(json.dumps(probe.DEFAULT_ARMS))
        )
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            result = probe.cmd_dry_run(argparse.Namespace(manifest=str(manifest_path)))
        self.assertEqual(result, 0)
        text = output.getvalue()
        # 4 units: seat 1 at width 2 -> 2 calls, seat 2 at width 1 -> 4 calls.
        self.assertIn("unique dev units: 4", text)
        self.assertIn("pair calls 6/repeat", text)
        self.assertIn("widths 2/1", text)

    def test_split_keeps_families_and_strata_together(self) -> None:
        records = []
        for index in range(20):
            for variant in ("clean", "mut"):
                records.append(
                    {
                        "record_id": f"{variant}-{index}",
                        "family": f"fam-{index}",
                        "stratum": "terminology" if index % 2 else "ui",
                        "language": "cs",
                        "source_language": "en",
                        "source": f"source {index}",
                        "target": f"target {index} {variant}",
                        "label": "pass" if variant == "clean" else "defect",
                        "severity": "major",
                        "project_slug": "test",
                        "component_slug": "test",
                        "unit_id": index * 10 + len(variant),
                    }
                )
        parsed = [probe.CorpusRecord.from_json(row) for row in records]
        first = probe.build_split(parsed, 20260917, 0.5)
        second = probe.build_split(parsed, 20260917, 0.5)
        self.assertEqual(first, second, "same seed must reproduce the split")
        for family in {row["family"] for row in records}:
            parts = {
                first[row["record_id"]] for row in records if row["family"] == family
            }
            self.assertEqual(len(parts), 1, "a family must not straddle the split")
        self.assertEqual(set(first.values()), {"dev", "heldout"})

    def test_excluded_records_are_explicit_not_silent(self) -> None:
        base = sample_record(0)
        self.assertEqual(
            probe._classify_record({**base, "target": "  "})[1], "empty-target"
        )
        self.assertEqual(
            probe._classify_record({**base, "label": "maybe"})[1], "missing-label"
        )
        self.assertEqual(
            probe._classify_record({**base, "label": "defect"})[1], "bad-severity"
        )
        clean, reason = probe._classify_record(base)
        self.assertIsNone(reason)
        self.assertEqual(clean.record_id, base["record_id"])


@override_settings(
    JUDGE_ENABLED=True,
    JUDGE_API_KEY="sk-runner-test",
    JUDGE_BASE_URL="https://openrouter.ai/api/v1",
    JUDGE_MODEL_SEAT_1=SEAT_1_MODEL,
    JUDGE_MODEL_SEAT_2=SEAT_2_MODEL,
    JUDGE_BATCH_SIZE=2,
    JUDGE_MAX_UNPARSED_RETRY_ROUNDS=0,
    JUDGE_RETRY_BUDGET_RATIO=0,
    JUDGE_REQUEST_SLEEP=0,
    JUDGE_DEFERRAL_ENABLED=False,
)
class RunnerEndToEndTest(TempDirMixin, RepoTestMixin, TransactionTestCase):
    """Fake-provider runs of the real run_judge_batch through the runner."""

    def setUp(self) -> None:
        self.clone_test_repos()
        super().setUp()
        activate("en")
        component = self.create_component()
        component.create_path()
        self.project = component.project
        self.translation = component.translation_set.get(language_code="cs")
        self.user = create_test_user()
        # The fixture ships most cs targets empty; the measurement scope only
        # takes translated, non-empty strings, so translate every unit.
        for index, unit in enumerate(self.translation.unit_set.order_by("pk")):
            unit.translate(self.user, [f"cel test {index}"], STATE_TRANSLATED)
        self.units = list(self.translation.unit_set.order_by("pk"))
        self.tmp = self.temp_dir()

    def run_execute(self, manifest_path, slot, **kwargs):
        return probe.cmd_execute(execute_args(manifest_path, slot, **kwargs))

    @http_mock.activate
    def test_execute_runs_both_seats_and_writes_the_ledger(self) -> None:
        register_fake_provider({SEAT_1_MODEL: "ok", SEAT_2_MODEL: "ok"})
        manifest_path = write_experiment(self.tmp, records_from_units(self.units))
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)

        journal = journal_of(manifest_path)
        self.assertIn("s001", journal["states"]["completed"])
        block_start = next(
            event for event in journal["events"] if event["event"] == "block_start"
        )
        run_id = block_start["run_id"]
        verdicts = list(JudgeVerdict.objects.filter(run_id=run_id))
        self.assertEqual(len(verdicts), 2 * len(self.units))
        self.assertTrue(all(not row.unparsed for row in verdicts))
        attempts = list(JudgeRequestAttempt.objects.all())
        # 4 units: seat 1 judges two width-2 batches, seat 2 four width-1.
        self.assertEqual(len(attempts), 6)
        seat_1 = [row for row in attempts if row.seat == 1]
        seat_2 = [row for row in attempts if row.seat == 2]
        self.assertEqual(len(seat_1), 2)
        self.assertEqual(len(seat_2), 4)
        self.assertTrue(all(row.batch_size == 2 for row in seat_1))
        self.assertTrue(all(row.batch_size == 1 for row in seat_2))
        self.assertEqual(LLMUsageLog.objects.filter(operation="judge").count(), 6)
        self.assertEqual(LLMUsageLog.objects.filter(operation="translation").count(), 0)
        self.assertEqual(Suggestion.objects.count(), 0)
        # Attribution is by the block's own identity, never a PK watermark:
        # every attributed attempt digest belongs to the precomputed block set.
        expected = block_start["expected_batch_digests"]
        for row in attempts:
            self.assertIn(row.batch_digest, expected[str(row.seat)])
        # No unit text or state moved.
        for unit in self.units:
            unit.refresh_from_db()
            self.assertGreaterEqual(unit.state, STATE_TRANSLATED)

    @http_mock.activate
    def test_width_five_makes_one_seat_two_post(self) -> None:
        register_fake_provider({SEAT_1_MODEL: "ok", SEAT_2_MODEL: "ok"})
        manifest_path = write_experiment(
            self.tmp,
            records_from_units(self.units),
            arms={"A0": {"title": "wide", "overrides": {"seat_2_batch_size": 5}}},
        )
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        attempts = list(JudgeRequestAttempt.objects.all())
        self.assertEqual(len(attempts), 3)
        seat_2 = [row for row in attempts if row.seat == 2]
        self.assertEqual(len(seat_2), 1)
        self.assertEqual(seat_2[0].batch_size, 4)

    @http_mock.activate
    def test_adaptive_state_does_not_leak_between_slots(self) -> None:
        # JudgeAdaptiveState persists per (endpoint, model, seat) in the QA
        # database: a width-1 slot leaves budget 1 behind, and without a
        # per-slot reset the next arm's requested width collapses to it.
        register_fake_provider({SEAT_1_MODEL: "ok", SEAT_2_MODEL: "ok"})
        manifest_path = write_experiment(
            self.tmp,
            records_from_units(self.units),
            arms={
                "A0": {"title": "control", "overrides": {"seat_2_batch_size": 1}},
                "A5": {"title": "wide", "overrides": {"seat_2_batch_size": 5}},
            },
        )
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        self.assertEqual(self.run_execute(manifest_path, "s002"), 0)
        attempts = list(JudgeRequestAttempt.objects.all().order_by("pk"))
        # s001: seat 1 two width-2 batches + seat 2 four width-1 batches.
        # s002 with a fresh adaptive state: seat 1 two + seat 2 one width-4.
        self.assertEqual(len(attempts), 9, [a.batch_size for a in attempts])
        wide = [row for row in attempts if row.seat == 2 and row.batch_size > 1]
        self.assertEqual(len(wide), 1)
        self.assertEqual(wide[0].batch_size, 4)

    @http_mock.activate
    def test_parser_failures_stay_in_the_denominator(self) -> None:
        register_fake_provider({SEAT_1_MODEL: "duplicate-id", SEAT_2_MODEL: "partial"})
        manifest_path = write_experiment(self.tmp, records_from_units(self.units))
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        attempts = list(JudgeRequestAttempt.objects.all())
        self.assertEqual(len(attempts), 6, "no retries without a retry budget")
        self.assertFalse(any(row.parsed for row in attempts))
        kinds = {row.failure_kind for row in attempts}
        self.assertIn("invalid-segment", kinds)
        block = json.loads((self.tmp / "results" / "block-s001.json").read_text())
        self.assertEqual(len(block["verdicts"]), 2 * len(self.units))
        self.assertTrue(all(row["unparsed"] for row in block["verdicts"]))

    @http_mock.activate
    def test_timeout_counts_as_a_failure_not_a_verdict(self) -> None:
        http_mock.register_exception("POST", CHAT_URL, httpx2.ReadTimeout("boom"))
        manifest_path = write_experiment(self.tmp, records_from_units(self.units))
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        attempts = list(JudgeRequestAttempt.objects.all())
        # Seat 1 starts at width 2; the first deadline halves the adaptive
        # budget, so its last two units go out as two width-1 batches. Seat 2
        # never rises above width 1: 3 + 4 attempts in total, each a real
        # failed POST the budget must pay for.
        self.assertEqual(len(attempts), 7)
        self.assertTrue(
            all(row.failure_kind == "deadline" for row in attempts),
            [row.failure_kind for row in attempts],
        )
        verdicts = list(JudgeVerdict.objects.all())
        self.assertTrue(all(row.unparsed for row in verdicts))

    @http_mock.activate
    def test_swapped_ids_parse_but_are_recorded_for_adjudication(self) -> None:
        # A permutation of in-range IDs is accepted by the parser; the plan
        # answers this with back-translation checks, and the runner must
        # record the round faithfully rather than crash or hide it.
        register_fake_provider({SEAT_1_MODEL: "swap-ids", SEAT_2_MODEL: "ok"})
        manifest_path = write_experiment(self.tmp, records_from_units(self.units))
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        attempts = list(JudgeRequestAttempt.objects.all())
        self.assertEqual(len(attempts), 6)
        self.assertTrue(all(row.parsed for row in attempts))

    @http_mock.activate
    def test_auth_failure_raises_and_keeps_paid_attempts(self) -> None:
        http_mock.register("POST", CHAT_URL, status_code=401, json={})
        manifest_path = write_experiment(self.tmp, records_from_units(self.units))
        with self.assertRaises(judge.JudgeError):
            self.run_execute(manifest_path, "s001")
        attempts = list(JudgeRequestAttempt.objects.all())
        # One doomed POST per seat; the config error must not retry or hide.
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(row.failure_kind == "http-auth" for row in attempts))
        journal = journal_of(manifest_path)
        self.assertIn("s001", journal["states"]["interrupted"])

    @http_mock.activate
    def test_guard_reserves_before_sending(self) -> None:
        register_fake_provider({SEAT_1_MODEL: "ok", SEAT_2_MODEL: "ok"})
        tiny_budget = dict(BUDGET, money_cap_usd="0.000000001")
        manifest_path = write_experiment(
            self.tmp, records_from_units(self.units), budget=tiny_budget
        )
        self.assertEqual(self.run_execute(manifest_path, "s001"), 3)
        attempts = list(JudgeRequestAttempt.objects.all())
        self.assertEqual(
            attempts, [], "a tripped guard must refuse the POST before sending"
        )
        journal = journal_of(manifest_path)
        self.assertIn("s001", journal["states"]["interrupted"])
        interrupted = next(
            event for event in journal["events"] if event["event"] == "slot_interrupted"
        )
        self.assertEqual(interrupted["posts_sent"], 0)

    @http_mock.activate
    def test_crash_between_post_and_save_then_resume(self) -> None:
        route = register_fake_provider({SEAT_1_MODEL: "ok", SEAT_2_MODEL: "ok"})
        manifest_path = write_experiment(self.tmp, records_from_units(self.units))
        with (
            mock.patch(
                "weblate.trans.judge_loop._write_verdict",
                side_effect=RuntimeError("crash between POST and save"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.run_execute(manifest_path, "s001")
        journal = journal_of(manifest_path)
        self.assertIn("s001", journal["states"]["interrupted"])
        interrupted = next(
            event for event in journal["events"] if event["event"] == "slot_interrupted"
        )
        self.assertGreaterEqual(interrupted["posts_sent"], 1)
        interrupted_file = self.tmp / "results" / "interrupted-s001.json"
        self.assertTrue(interrupted_file.exists())
        paid_first_run = len(json.loads(interrupted_file.read_text())["attempts"])

        # Resume is a new paid decision; it re-runs the interrupted block and
        # must not resend anything that already completed.
        self.assertEqual(self.run_execute(manifest_path, "s001", resume=True), 0)
        attempts = list(JudgeRequestAttempt.objects.all())
        self.assertEqual(len(attempts), paid_first_run + 6)
        verdicts = list(JudgeVerdict.objects.all())
        self.assertEqual(len(verdicts), 2 * len(self.units))

        # A completed slot is never repeated, with or without --resume.
        calls_before = len(route.calls)
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        self.assertEqual(len(route.calls), calls_before)
        with self.assertRaises(probe.RunnerError):
            self.run_execute(manifest_path, "s001", resume=True)

    @http_mock.activate
    def test_approved_state_survives_the_measurement(self) -> None:
        self.project.translation_review = True
        self.project.save(update_fields=["translation_review"])
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        approved = self.units[0]
        approved.translate(self.user, approved.get_target_plurals(), STATE_APPROVED)
        register_fake_provider({SEAT_1_MODEL: "ok", SEAT_2_MODEL: "ok"})
        manifest_path = write_experiment(self.tmp, records_from_units(self.units))
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        approved.refresh_from_db()
        self.assertEqual(approved.state, STATE_APPROVED)

    @http_mock.activate
    def test_quality_metrics_and_gates_offline(self) -> None:
        behaviors = {SEAT_1_MODEL: "ok", SEAT_2_MODEL: "ok"}
        register_fake_provider(behaviors)
        records = records_from_units(self.units)
        for index, record in enumerate(records):
            record["label"] = "defect" if index % 2 else "pass"
            record["severity"] = "major" if index % 2 else ""
        manifest_path = write_experiment(
            self.tmp,
            records,
            arms={
                "A0": {"title": "control", "overrides": {"seat_2_batch_size": 1}},
                "A1": {"title": "flag all", "overrides": {"seat_2_batch_size": 1}},
            },
        )
        # Control: both seats pass everything.
        self.assertEqual(self.run_execute(manifest_path, "s001"), 0)
        # Candidate arm flags everything: recall up, false flags up.
        behaviors[SEAT_1_MODEL] = behaviors[SEAT_2_MODEL] = "flag-major"
        self.assertEqual(self.run_execute(manifest_path, "s002"), 0)

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(
                probe.cmd_summarize(argparse.Namespace(manifest=str(manifest_path))),
                0,
            )
        summary = json.loads((self.tmp / "summary.json").read_text())
        control = summary["arms"]["A0"]
        flag_all = summary["arms"]["A1"]
        self.assertEqual(control["quality"]["major_misses"], 2)
        self.assertEqual(control["quality"]["false_flags"], 0)
        self.assertEqual(flag_all["quality"]["major_misses"], 0)
        self.assertEqual(flag_all["quality"]["false_flags"], 2)
        self.assertEqual(flag_all["failure_kinds"], {})
        gates = flag_all["gates"]
        self.assertEqual(gates["critical_misses_delta"], 0)
        self.assertEqual(gates["verdict"], "fail")


class ApplyArmTest(SimpleTestCase):
    def test_apply_arm_sets_only_the_allowed_seat_setting(self) -> None:
        with mock.patch.object(settings, "JUDGE_BATCH_SIZE_SEAT_2", "inherit"):
            probe.apply_arm({"overrides": {"seat_2_batch_size": 5}})
            self.assertEqual(settings.JUDGE_BATCH_SIZE_SEAT_2, 5)

    def test_validate_manifest_rejects_unknown_overrides(self) -> None:
        with self.assertRaises(probe.ManifestError):
            probe.validate_manifest(
                {
                    "schema": probe.SCHEMA,
                    "experiment_id": "x",
                    "corpus": {"path": "p", "sha256": "h"},
                    "seat_models": {"seat_1": "a", "seat_2": "b"},
                    "seat_1_batch_size": 2,
                    "arms": {"A0": {"overrides": {"seat_1_batch_size": 9}}},
                    "repeats": 1,
                    "schedule": {
                        "slots": [
                            {
                                "id": "s001",
                                "arm": "A0",
                                "repeat": 1,
                                "group": "g",
                            }
                        ]
                    },
                    "prices": {"m": dict.fromkeys(probe.PRICE_KEYS, 1)},
                    "prices_source": "s",
                    "gates": {"control_arm": "A0"},
                },
                require_budget=False,
            )
