# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.test import SimpleTestCase

from weblate.trans.models.judge import compute_context_hash, compute_target_hash


class JudgeHashTest(SimpleTestCase):
    def test_target_hash_is_stable(self) -> None:
        self.assertEqual(
            compute_target_hash(["La porte est bloquée"]),
            compute_target_hash(["La porte est bloquée"]),
        )

    def test_target_hash_tracks_every_plural_form(self) -> None:
        self.assertNotEqual(
            compute_target_hash(["une porte", "deux portes"]),
            compute_target_hash(["une porte", "trois portes"]),
        )

    def test_target_hash_separator_cannot_be_forged(self) -> None:
        # Naive "\n".join() would collide these two different plural sets.
        self.assertNotEqual(
            compute_target_hash(["a\nb"]),
            compute_target_hash(["a", "b"]),
        )

    def test_context_hash_reacts_to_glossary_and_note(self) -> None:
        base = compute_context_hash(source="Door", note="", glossary_terms=[])
        self.assertNotEqual(
            base, compute_context_hash(source="Door", note="hall", glossary_terms=[])
        )
        self.assertNotEqual(
            base,
            compute_context_hash(
                source="Door", note="", glossary_terms=[("Door", "Porte")]
            ),
        )

    def test_context_hash_ignores_glossary_order(self) -> None:
        self.assertEqual(
            compute_context_hash(
                source="Door", note="", glossary_terms=[("a", "b"), ("c", "d")]
            ),
            compute_context_hash(
                source="Door", note="", glossary_terms=[("c", "d"), ("a", "b")]
            ),
        )
