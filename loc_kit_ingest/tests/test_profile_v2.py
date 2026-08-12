# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Profile v2 (``record-map``) schema tests.

v2 adds one generic TBX grammar and nothing else. A v1 document keeps its
exact v1 interpretation, and neither version's validation is loosened to
accommodate the other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loc_kit_ingest.profile import (
    IgnoredColumn,
    NoteField,
    PairsGrammar,
    ProfileError,
    RecordMapGrammar,
    SectionField,
    load_profile,
    parse_profile,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _one_row_document(**overrides):
    """A valid v2 one-row glossary with a per-record domain column."""
    document = {
        "schema_version": 2,
        "components": [
            {
                "sheet": "Glossary",
                "component": "Glossary",
                "kind": "tbx",
                "source_lang": "ru",
                "header_row": 1,
                "languages": [
                    {"code": "ru", "xml_lang": "ru", "column": 2, "header": "ru"},
                    {"code": "en", "xml_lang": "en", "column": 3, "header": "en"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "first_record_row": 2,
                            "last_record_row": 7,
                            "record_stride": 1,
                        }
                    ],
                    "term_row_offset": 0,
                    "section_field": {"column": 1, "header": "domain", "row_offset": 0},
                    "notes": [
                        {
                            "scope": "source",
                            "column": 4,
                            "header": "note_ru",
                            "row_offset": 0,
                        },
                        {
                            "scope": "target",
                            "language": "en",
                            "column": 5,
                            "header": "note_en",
                            "row_offset": 0,
                        },
                    ],
                },
                "initial_target_languages": ["en"],
            }
        ],
    }
    component = document["components"][0]
    for key, value in overrides.items():
        if key == "grammar":
            component["grammar"].update(value)
        else:
            component[key] = value
    return document


def _stride_two_document(**grammar_overrides):
    """A valid v2 stride-two glossary using region caption cells."""
    document = {
        "schema_version": 2,
        "components": [
            {
                "sheet": "Terms",
                "component": "Terms",
                "kind": "tbx",
                "source_lang": "ru",
                "header_row": 1,
                "languages": [
                    {"code": "ru", "xml_lang": "ru", "column": 1, "header": "ru"},
                    {"code": "en", "xml_lang": "en", "column": 2, "header": "en"},
                ],
                "grammar": {
                    "type": "record-map",
                    "skip_rows": [],
                    "regions": [
                        {
                            "section_row": 2,
                            "section_column": 1,
                            "first_record_row": 3,
                            "last_record_row": 6,
                            "record_stride": 2,
                        }
                    ],
                    "term_row_offset": 0,
                    "notes": [
                        {
                            "scope": "source",
                            "column": 1,
                            "header": "ru",
                            "row_offset": 1,
                        },
                        {
                            "scope": "target",
                            "language": "en",
                            "column": 2,
                            "header": "en",
                            "row_offset": 1,
                        },
                    ],
                },
                "initial_target_languages": ["en"],
            }
        ],
    }
    document["components"][0]["grammar"].update(grammar_overrides)
    return document


# --------------------------------------------------------------------------- #
# Valid documents
# --------------------------------------------------------------------------- #


def test_one_row_record_map_parses():
    profile = parse_profile(_one_row_document())
    component = profile.components[0]

    assert profile.schema_version == 2
    assert isinstance(component.grammar, RecordMapGrammar)
    grammar = component.grammar
    assert grammar.term_row_offset == 0
    assert grammar.section_field == SectionField(
        column=0, header="domain", row_offset=0
    )
    assert grammar.regions[0].record_stride == 1
    assert grammar.regions[0].first_record_row == 1  # 0-based
    assert grammar.regions[0].section_row is None
    assert grammar.notes == (
        NoteField(
            scope="source", column=3, header="note_ru", row_offset=0, language=None
        ),
        NoteField(
            scope="target", column=4, header="note_en", row_offset=0, language="en"
        ),
    )
    # v2 has no key_language.
    assert component.key_language is None
    assert component.initial_target_languages == ("en",)


def test_stride_two_record_map_with_caption_cells_parses():
    profile = parse_profile(_stride_two_document())
    grammar = profile.components[0].grammar

    assert isinstance(grammar, RecordMapGrammar)
    assert grammar.section_field is None
    region = grammar.regions[0]
    assert region.record_stride == 2
    assert region.section_row == 1  # 0-based
    assert region.section_column == 0
    # Notes live on the second row of each record.
    assert {note.row_offset for note in grammar.notes} == {1}


def test_v1_profile_still_loads_with_its_v1_interpretation():
    profile = load_profile(FIXTURES / "terms.loc-ingest.json")
    component = profile.components[0]

    assert profile.schema_version == 1
    assert isinstance(component.grammar, PairsGrammar)
    assert component.key_language == "en"
    assert component.source_lang == "ru"
    assert component.initial_target_languages == ("en", "ja")
    assert [lang.code for lang in component.languages] == ["ru", "en", "ja"]
    assert component.grammar.skip_rows == (1,)
    assert len(component.grammar.regions) == 2


# --------------------------------------------------------------------------- #
# Closed schema and cross-version isolation
# --------------------------------------------------------------------------- #


def test_unknown_v2_grammar_field_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["flags"] = [{"column": 6}]
    with pytest.raises(ProfileError, match="profile.unknown_field"):
        parse_profile(document)


def test_unknown_v2_component_field_fails():
    document = _one_row_document(nonsense=1)
    with pytest.raises(ProfileError, match="profile.unknown_field"):
        parse_profile(document)


def test_v2_rejects_the_v1_key_language_field():
    document = _one_row_document(key_language="en")
    with pytest.raises(ProfileError, match="profile.unknown_field"):
        parse_profile(document)


def test_v2_rejects_the_v1_pairs_grammar():
    document = _one_row_document()
    document["components"][0]["grammar"] = {
        "type": "term-description-pairs",
        "skip_rows": [],
        "regions": [{"section_row": 2, "first_term_row": 3, "last_description_row": 4}],
    }
    with pytest.raises(ProfileError, match="profile.grammar_mismatch"):
        parse_profile(document)


def test_v1_rejects_the_v2_record_map_grammar():
    # v1 demands key_language, which v2 does not have, so a v2 document can
    # never be reinterpreted as v1.
    document = _one_row_document()
    document["schema_version"] = 1
    with pytest.raises(ProfileError, match="profile.missing"):
        parse_profile(document)


def test_unsupported_schema_version_fails():
    document = _one_row_document()
    document["schema_version"] = 3
    with pytest.raises(ProfileError, match="profile.schema_version"):
        parse_profile(document)


# --------------------------------------------------------------------------- #
# Structural validation
# --------------------------------------------------------------------------- #


def test_both_section_styles_at_once_fails():
    document = _one_row_document()
    grammar = document["components"][0]["grammar"]
    # Leave room for a caption row above the block.
    grammar["regions"][0].update(
        {
            "first_record_row": 3,
            "last_record_row": 8,
            "section_row": 2,
            "section_column": 1,
        }
    )
    with pytest.raises(ProfileError, match="profile.section_conflict"):
        parse_profile(document)


def test_half_declared_section_cell_fails():
    document = _stride_two_document()
    del document["components"][0]["grammar"]["regions"][0]["section_column"]
    with pytest.raises(ProfileError, match="profile.incomplete_section_cell"):
        parse_profile(document)


def test_term_row_offset_outside_stride_fails():
    document = _one_row_document(grammar={"term_row_offset": 1})
    with pytest.raises(ProfileError, match="profile.offset_out_of_range"):
        parse_profile(document)


def test_skip_row_inside_a_region_fails():
    # Otherwise the record walk imports the row while the skip list claims it
    # was left out: the same row becomes both a term and a reported skip.
    document = _one_row_document()
    document["components"][0]["grammar"]["skip_rows"] = [4]
    with pytest.raises(ProfileError, match="profile.skip_inside_region"):
        parse_profile(document)


def test_skip_row_on_a_region_caption_fails():
    document = _stride_two_document()
    document["components"][0]["grammar"]["skip_rows"] = [2]
    with pytest.raises(ProfileError, match="profile.skip_inside_region"):
        parse_profile(document)


def test_skip_row_outside_every_region_is_accepted():
    document = _one_row_document()
    document["components"][0]["grammar"]["skip_rows"] = [8]
    grammar = parse_profile(document).components[0].grammar
    assert grammar.skip_rows == (7,)


def test_note_row_offset_outside_stride_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["notes"][0]["row_offset"] = 3
    with pytest.raises(ProfileError, match="profile.offset_out_of_range"):
        parse_profile(document)


def test_section_field_row_offset_outside_stride_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["section_field"]["row_offset"] = 2
    with pytest.raises(ProfileError, match="profile.offset_out_of_range"):
        parse_profile(document)


def test_record_range_not_divisible_by_stride_fails():
    document = _stride_two_document()
    document["components"][0]["grammar"]["regions"][0]["last_record_row"] = 7
    with pytest.raises(ProfileError, match="profile.record_span_not_divisible"):
        parse_profile(document)


def test_zero_stride_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["regions"][0]["record_stride"] = 0
    with pytest.raises(ProfileError, match="profile.invalid_index"):
        parse_profile(document)


def test_overlapping_regions_fail():
    document = _one_row_document()
    document["components"][0]["grammar"]["regions"] = [
        {"first_record_row": 2, "last_record_row": 7, "record_stride": 1},
        {"first_record_row": 5, "last_record_row": 9, "record_stride": 1},
    ]
    with pytest.raises(ProfileError, match="profile.region_overlap"):
        parse_profile(document)


def test_caption_row_inside_previous_region_fails():
    document = _stride_two_document()
    document["components"][0]["grammar"]["regions"] = [
        {
            "section_row": 2,
            "section_column": 1,
            "first_record_row": 3,
            "last_record_row": 8,
            "record_stride": 2,
        },
        {
            "section_row": 5,
            "section_column": 1,
            "first_record_row": 9,
            "last_record_row": 10,
            "record_stride": 2,
        },
    ]
    with pytest.raises(ProfileError, match="profile.region_overlap"):
        parse_profile(document)


def test_duplicate_field_location_fails():
    # A source note reading the very cell the ru term occupies.
    document = _one_row_document()
    document["components"][0]["grammar"]["notes"][0]["column"] = 2
    with pytest.raises(ProfileError, match="profile.duplicate_field_location"):
        parse_profile(document)


def test_section_field_column_colliding_with_a_language_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["section_field"]["column"] = 3
    with pytest.raises(ProfileError, match="profile.section_field_column_collision"):
        parse_profile(document)


# --------------------------------------------------------------------------- #
# Language and note semantics
# --------------------------------------------------------------------------- #


def test_target_note_naming_a_non_target_language_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["notes"][1]["language"] = "ru"
    with pytest.raises(ProfileError, match="profile.unknown_note_language"):
        parse_profile(document)


def test_source_note_declaring_a_language_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["notes"][0]["language"] = "ru"
    with pytest.raises(ProfileError, match="profile.unexpected_note_language"):
        parse_profile(document)


def test_invalid_note_scope_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["notes"][0]["scope"] = "flag"
    with pytest.raises(ProfileError, match="profile.invalid_note_scope"):
        parse_profile(document)


def test_omitted_target_languages_fail():
    document = _one_row_document(initial_target_languages=[])
    with pytest.raises(ProfileError, match="profile.empty_target_languages"):
        parse_profile(document)


def test_source_language_in_targets_fails():
    document = _one_row_document(initial_target_languages=["ru", "en"])
    with pytest.raises(ProfileError, match="profile.source_in_targets"):
        parse_profile(document)


def test_unnamed_note_and_section_columns_parse():
    """A description column whose header cell is empty is still addressable."""
    document = _one_row_document()
    grammar_document = document["components"][0]["grammar"]
    grammar_document["notes"][0]["header"] = ""
    grammar_document["section_field"]["header"] = ""

    component = parse_profile(document).components[0]

    assert isinstance(component.grammar, RecordMapGrammar)
    assert component.grammar.notes[0] == NoteField(
        scope="source", column=3, header="", row_offset=0, language=None
    )
    assert component.grammar.section_field == SectionField(
        column=0, header="", row_offset=0
    )


def test_non_string_note_header_fails():
    document = _one_row_document()
    document["components"][0]["grammar"]["notes"][0]["header"] = 7
    with pytest.raises(ProfileError, match="profile.invalid_header"):
        parse_profile(document)


def test_round_trips_through_json():
    """A v2 document survives serialization, as the UI download/upload does."""
    document = _one_row_document()
    reparsed = parse_profile(json.loads(json.dumps(document)))
    assert reparsed == parse_profile(document)


def test_ignored_columns_parse_to_zero_based_records():
    document = _one_row_document()
    document["components"][0]["grammar"]["ignored_columns"] = [
        {"column": 6, "header": "id"},
        {"column": 7, "header": "build"},
    ]
    grammar = parse_profile(document).components[0].grammar
    assert grammar.ignored_columns == (
        IgnoredColumn(column=5, header="id"),
        IgnoredColumn(column=6, header="build"),
    )


def test_ignored_columns_default_to_empty():
    grammar = parse_profile(_one_row_document()).components[0].grammar
    assert grammar.ignored_columns == ()
    assert grammar.allow_empty_targets is False


@pytest.mark.parametrize(
    ("column", "what"),
    [(2, "language ru"), (4, "source note"), (1, "section field")],
)
def test_ignored_column_colliding_with_a_declared_field_is_rejected(column, what):
    document = _one_row_document()
    document["components"][0]["grammar"]["ignored_columns"] = [
        {"column": column, "header": "x"}
    ]
    with pytest.raises(ProfileError, match="profile.ignored_column_collision"):
        parse_profile(document)


def test_ignored_column_needs_a_positive_integer_column():
    document = _one_row_document()
    document["components"][0]["grammar"]["ignored_columns"] = [
        {"column": 0, "header": "id"}
    ]
    with pytest.raises(ProfileError, match="profile.invalid_index"):
        parse_profile(document)


def test_allow_empty_targets_parses_as_bool():
    document = _one_row_document()
    document["components"][0]["grammar"]["allow_empty_targets"] = True
    assert parse_profile(document).components[0].grammar.allow_empty_targets is True


def test_allow_empty_targets_rejects_a_non_bool():
    document = _one_row_document()
    document["components"][0]["grammar"]["allow_empty_targets"] = "yes"
    with pytest.raises(ProfileError, match="profile.invalid_value"):
        parse_profile(document)
