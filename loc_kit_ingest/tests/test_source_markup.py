# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from loc_kit_ingest.source_markup import (
    SOURCE_TAG_CLOSING_HAS_ATTRIBUTE,
    source_markup_defects,
)

# Defect 525591 from the Anvil Saga FR LQA audit: the opening tag of the
# ru source is written as a closing tag.
DEFECT_525591 = "Заказчики приходят в </color=yellow>{0}</color> раза реже."


def test_defect_525591_matches_exactly_once():
    defects = source_markup_defects(DEFECT_525591)
    assert len(defects) == 1
    defect = defects[0]
    assert defect.code == SOURCE_TAG_CLOSING_HAS_ATTRIBUTE
    start = DEFECT_525591.index("</color=yellow>")
    assert defect.span == (start, start + len("</color=yellow>"))
    assert "</color=yellow>" in defect.message


def test_two_independent_closing_tags_give_two_non_overlapping_defects():
    text = "</color=red>{0}</size=2>{1}</color>"
    defects = source_markup_defects(text)
    assert len(defects) == 2
    assert all(defect.code == SOURCE_TAG_CLOSING_HAS_ATTRIBUTE for defect in defects)
    assert [text[start:end] for start, end in (d.span for d in defects)] == [
        "</color=red>",
        "</size=2>",
    ]
    assert defects[0].span[1] <= defects[1].span[0]


def test_empty_string_has_no_defects():
    assert source_markup_defects("") == ()


def test_correct_opening_and_closing_tags_have_no_defects():
    text = "<color=yellow>{0}</color> and <size=12>big</size>"
    assert source_markup_defects(text) == ()


def test_bare_closing_sprite_is_not_a_defect():
    text = 'pick <sprite name="fire"></sprite>'
    assert source_markup_defects(text) == ()


@pytest.mark.parametrize(
    "text",
    [
        "HP > 50",
        "<3",
        "a -> b",
        "Урон < 10",
        "2 < 3 > 1",
        "<basic>",
        "Стоимость: {0} (около 5 <)",
    ],
)
def test_ordinary_punctuation_is_not_a_candidate(text):
    assert source_markup_defects(text) == ()


def test_match_is_case_insensitive_like_tag_pattern():
    text = "see </COLOR=yellow>here</COLOR>"
    defects = source_markup_defects(text)
    assert [text[start:end] for start, end in (d.span for d in defects)] == [
        "</COLOR=yellow>"
    ]


def test_whitespace_attribute_is_caught():
    text = "come </color yellow>back</color>"
    defects = source_markup_defects(text)
    assert [text[start:end] for start, end in (d.span for d in defects)] == [
        "</color yellow>"
    ]


def test_empty_attribute_value_is_caught():
    defects = source_markup_defects("come </color=>back</color>")
    assert len(defects) == 1
    assert defects[0].code == SOURCE_TAG_CLOSING_HAS_ATTRIBUTE
