# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from weblate.trans import defaults as trans_defaults


class LocKitSettingsDefaultsTest(SimpleTestCase):
    """Defaults and disabled-by-default behavior for loc-kit analysis settings."""

    def test_analysis_disabled_by_default(self) -> None:
        self.assertFalse(settings.LOC_KIT_PROFILE_ANALYSIS_ENABLED)

    def test_key_defaults_empty(self) -> None:
        self.assertEqual(settings.LOC_KIT_PROFILE_OPENROUTER_KEY, "")

    def test_model_defaults_empty(self) -> None:
        self.assertEqual(settings.LOC_KIT_PROFILE_OPENROUTER_MODEL, "")

    def test_sample_max_bytes_default(self) -> None:
        self.assertEqual(settings.LOC_KIT_PROFILE_SAMPLE_MAX_BYTES, 131072)

    def test_import_draft_expiry_default(self) -> None:
        self.assertEqual(settings.LOC_KIT_IMPORT_DRAFT_EXPIRY, 3600)

    def test_ratelimit_defaults(self) -> None:
        self.assertEqual(settings.RATELIMIT_LOC_KIT_ANALYSIS_ATTEMPTS, 3)
        self.assertEqual(settings.RATELIMIT_LOC_KIT_ANALYSIS_WINDOW, 3600)

    def test_module_defaults_match_contract(self) -> None:
        # Guard the exact contract values declared in defaults.py.
        self.assertFalse(trans_defaults.DEFAULT_LOC_KIT_PROFILE_ANALYSIS_ENABLED)
        self.assertEqual(trans_defaults.DEFAULT_LOC_KIT_PROFILE_OPENROUTER_KEY, "")
        self.assertEqual(trans_defaults.DEFAULT_LOC_KIT_PROFILE_OPENROUTER_MODEL, "")
        self.assertEqual(trans_defaults.DEFAULT_LOC_KIT_PROFILE_SAMPLE_MAX_BYTES, 131072)
        self.assertEqual(trans_defaults.DEFAULT_LOC_KIT_IMPORT_DRAFT_EXPIRY, 3600)

    def test_override_settings_can_enable(self) -> None:
        with override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=True):
            self.assertTrue(settings.LOC_KIT_PROFILE_ANALYSIS_ENABLED)


class LocKitClampTest(SimpleTestCase):
    """Hard caps hold regardless of the supplied value."""

    def test_clamp_sample_max_bytes_caps_oversized(self) -> None:
        self.assertEqual(
            trans_defaults.clamp_loc_kit_sample_max_bytes(10_000_000),
            trans_defaults.LOC_KIT_PROFILE_SAMPLE_MAX_BYTES_CAP,
        )

    def test_clamp_sample_max_bytes_passes_under_cap(self) -> None:
        self.assertEqual(trans_defaults.clamp_loc_kit_sample_max_bytes(4096), 4096)

    def test_clamp_sample_max_bytes_passes_exact_cap(self) -> None:
        self.assertEqual(
            trans_defaults.clamp_loc_kit_sample_max_bytes(131072),
            131072,
        )

    def test_clamp_import_draft_expiry_caps_oversized(self) -> None:
        self.assertEqual(
            trans_defaults.clamp_loc_kit_import_draft_expiry(99_999_999),
            trans_defaults.LOC_KIT_IMPORT_DRAFT_EXPIRY_CAP,
        )

    def test_clamp_import_draft_expiry_passes_under_cap(self) -> None:
        self.assertEqual(trans_defaults.clamp_loc_kit_import_draft_expiry(600), 600)

    def test_clamp_import_draft_expiry_passes_exact_cap(self) -> None:
        self.assertEqual(trans_defaults.clamp_loc_kit_import_draft_expiry(3600), 3600)

    def test_clamp_handles_non_int_input(self) -> None:
        # Robustness: env parsing always yields an int, but the helper must
        # not crash on a non-int coercible value.
        self.assertEqual(
            trans_defaults.clamp_loc_kit_sample_max_bytes(int("200000")),  # noqa: FURB123
            131072,
        )
