# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Language-code recognition for the codes game kits actually use."""

from __future__ import annotations

import pytest

from loc_kit_ingest.langcode import language_code


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("ch-s", "zh_Hans"),
        ("ch", "zh_Hans"),
        ("cn", "zh_Hans"),
        ("ch-t", "zh_Hant"),
        ("zh-TC", "zh_Hant"),
        ("jp", "ja"),
        ("kr", "ko"),
        ("Japanese(jp)", "ja"),
    ],
)
def test_game_kit_aliases_resolve_to_weblate_codes(header, expected) -> None:
    assert language_code(header) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # A code that is a language on its own keeps its meaning: "pt means
        # pt-BR" is a per-project decision for Weblate's language aliases.
        ("pt", "pt"),
        ("pt-br", "pt_BR"),
        ("id", "id"),
        ("ja", "ja"),
        ("ko", "ko"),
        ("en", "en"),
    ],
)
def test_real_codes_are_not_rewritten(header, expected) -> None:
    assert language_code(header) == expected


@pytest.mark.parametrize("header", ["note", "Character limit", "chn", "", "42"])
def test_non_language_headers_stay_unrecognised(header) -> None:
    assert language_code(header) is None
