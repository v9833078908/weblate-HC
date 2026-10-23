# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest
from app.services.source_fallback import is_content_empty, resolve_row_source


@pytest.mark.parametrize(
    "values,declared,expected",
    [
        ({"de": "Hallo", "ru": "Привет", "en": "Hello"}, "de", ("de", "Hallo", "declared")),
        ({"en": "", "ru": "Привет"}, "en", ("ru", "Привет", "fallback")),
        ({"ru": "", "en": "Hello"}, "ru", ("en", "Hello", "fallback")),
        ({"ru": "Привет", "en": "Hello"}, "de", ("ru", "Привет", "fallback")),
        ({"en": " \t", "ru": "  Привет  "}, "en", ("ru", "  Привет  ", "fallback")),
        ({"es": "Hola"}, "en", None),
        ({"en": "=literal"}, "en", ("en", "=literal", "declared")),
    ],
)
def test_row_source_policy(values, declared, expected):
    original = dict(values)
    assert resolve_row_source(values, declared) == expected
    assert values == original


def test_formula_is_not_source():
    assert resolve_row_source(
        {"en": "=A1", "ru": "Текст"}, "en", formula_languages=frozenset({"en"})
    ) == ("ru", "Текст", "fallback")


def test_unknown_service_column_not_silently_empty():
    assert not is_content_empty(["", "", "service"], set())
    assert is_content_empty(["", "", "service"], {2})
    assert not is_content_empty(["key", "", "service"], {2})
