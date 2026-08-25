# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: file-ignore[private-member-access]

from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
REPLAY_PATH = ROOT / "analysis/probes/game-number-replay.py"


def replay_module():
    spec = importlib.util.spec_from_file_location("game_number_replay", REPLAY_PATH)
    if spec is None or spec.loader is None:
        msg = f"Cannot load {REPLAY_PATH}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GameNumberReplayTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.replay = replay_module()

    def test_tsv_loader_accepts_the_declared_manifest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "units.tsv"
            path.write_text(
                "context\tru\ten\nkey\tИсходник\tTarget\n", encoding="utf-8"
            )
            rows = self.replay._read_tsv(path, ("context", "ru", "en"), 1)

        self.assertEqual(rows, [{"context": "key", "ru": "Исходник", "en": "Target"}])

    def test_tsv_loader_rejects_an_incomplete_manifest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "units.tsv"
            path.write_text("context\tru\nkey\tИсходник\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                self.replay._read_tsv(path, ("context", "ru", "en"), 1)

    def test_tsv_loader_rejects_a_blank_target(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "units.tsv"
            path.write_text("context\tru\ten\nkey\tИсходник\t\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                self.replay._read_tsv(path, ("context", "ru", "en"), 1)

    def test_jsonl_loader_rejects_an_empty_corpus(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "units.jsonl"
            path.write_text("", encoding="utf-8")

            with self.assertRaises(ValueError):
                self.replay._read_jsonl(path, ("context", "source", "target"), 1)
