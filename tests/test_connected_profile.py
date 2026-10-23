# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from app.services import profiles


def test_language_merge_does_not_erase_previous_language():
    assert hasattr(profiles, "merge_remote_profile"), "safe profile merge is missing"
    old = {
        "persona": "Original",
        "style": "Style",
        "language_instructions": {"de": "German", "fr": "French"},
        "key": "secret",
    }
    result = profiles.merge_remote_profile(
        old,
        {"persona": "New", "style": "New style", "language_instructions": {"en": "English"}},
        ["en"],
    )
    assert result["language_instructions"] == {"de": "German", "fr": "French", "en": "English"}
    assert "key" not in result
    assert old["persona"] == "Original"
