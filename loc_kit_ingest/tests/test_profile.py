# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

import json

import pytest

from loc_kit_ingest.profile import ProfileError, load_profile


# ---------------------------------------------------------------------------
# Valid profile builder
# ---------------------------------------------------------------------------

VALID_PO = {
    "sheet": "Temple",
    "component": "Temple",
    "kind": "po",
    "source_lang": "ru",
    "header_row": 1,
    "first_data_row": 3,
    "languages": [
        {"code": "ru", "xml_lang": "ru", "column": 3, "header": "ru"},
        {"code": "en", "xml_lang": "en", "column": 4, "header": "en"},
    ],
    "key": {"column": 1, "header": "id"},
    "comments": [{"column": 2, "name": "Character", "header": "Character"}],
    "references": [{"column": 5, "name": "Id", "header": "Id"}],
    "grammar": {
        "type": "keyed",
        "skip_rows": [2],
        "allow_blank_rows": True,
    },
}

VALID_TBX = {
    "sheet": "Terms",
    "component": "Terms",
    "kind": "tbx",
    "source_lang": "ru",
    "header_row": 1,
    "languages": [
        {"code": "ru", "xml_lang": "ru", "column": 1, "header": "ru"},
        {"code": "en", "xml_lang": "en", "column": 2, "header": "en"},
        {"code": "ja", "xml_lang": "ja", "column": 3, "header": "ja"},
    ],
    "grammar": {
        "type": "term-description-pairs",
        "skip_rows": [2],
        "regions": [
            {"section_row": 3, "first_term_row": 4, "last_description_row": 7},
            {"section_row": 8, "first_term_row": 9, "last_description_row": 10},
        ],
    },
    "key_language": "en",
    "initial_target_languages": ["en", "ja"],
}

VALID_PROFILE = {"schema_version": 1, "components": [VALID_PO, VALID_TBX]}


@pytest.fixture
def valid_profile():
    import copy

    return copy.deepcopy(VALID_PROFILE)


def _write(tmp_path, obj, name="kit.loc-ingest.json"):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Root-level validation
# ---------------------------------------------------------------------------


def test_profile_requires_the_only_supported_schema_version(tmp_path):
    path = _write(tmp_path, {"schema_version": 2, "components": []})
    with pytest.raises(ProfileError, match="schema_version"):
        load_profile(path)


def test_missing_profile_raises(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        load_profile(tmp_path / "nonexistent.json")


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "kit.loc-ingest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="json"):
        load_profile(path)


def test_non_object_root_raises(tmp_path):
    path = _write(tmp_path, [1, 2, 3])
    with pytest.raises(ProfileError, match="object"):
        load_profile(path)


def test_missing_components_raises(tmp_path):
    path = _write(tmp_path, {"schema_version": 1})
    with pytest.raises(ProfileError, match="components"):
        load_profile(path)


def test_empty_components_raises(tmp_path):
    path = _write(tmp_path, {"schema_version": 1, "components": []})
    with pytest.raises(ProfileError, match="components"):
        load_profile(path)


def test_root_rejects_unknown_fields(valid_profile, tmp_path):
    valid_profile["surprise"] = True
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="unknown field"):
        load_profile(path)


# ---------------------------------------------------------------------------
# Component-level validation
# ---------------------------------------------------------------------------


def test_missing_source_lang_raises(valid_profile, tmp_path):
    del valid_profile["components"][0]["source_lang"]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="source_lang"):
        load_profile(path)


def test_source_lang_absent_from_languages_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["source_lang"] = "fr"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="source_lang"):
        load_profile(path)


def test_unknown_kind_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["kind"] = "yaml"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="kind"):
        load_profile(path)


def test_duplicate_components_raise(valid_profile, tmp_path):
    valid_profile["components"][0]["component"] = "Dup"
    valid_profile["components"][1]["component"] = "Dup"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="duplicate"):
        load_profile(path)


def test_casefold_duplicate_components_raise(valid_profile, tmp_path):
    valid_profile["components"][0]["component"] = "Test"
    valid_profile["components"][1]["component"] = "test"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="duplicate"):
        load_profile(path)


def test_unsafe_component_name_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["component"] = "../escape"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="component"):
        load_profile(path)


def test_duplicate_sheets_raise(valid_profile, tmp_path):
    valid_profile["components"][0]["sheet"] = "Same"
    valid_profile["components"][1]["sheet"] = "Same"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="sheet"):
        load_profile(path)


# ---------------------------------------------------------------------------
# Language validation
# ---------------------------------------------------------------------------


def test_duplicate_language_code_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["languages"][1]["code"] = "ru"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="language"):
        load_profile(path)


def test_duplicate_language_column_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["languages"][1]["column"] = 3
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="column"):
        load_profile(path)


def test_duplicate_language_after_casefold_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["languages"][1]["code"] = "RU"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="language"):
        load_profile(path)


def test_bad_xml_lang_tag_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["languages"][0]["xml_lang"] = "1x"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="xml_lang"):
        load_profile(path)


def test_invalid_1based_index_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["languages"][0]["column"] = 0
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="column"):
        load_profile(path)


def test_bcp47_tags_with_script_subtags(valid_profile, tmp_path):
    """pt_PT, zh_Hans, zh_Hant Weblate codes map to explicit BCP-47 xml_lang."""
    valid_profile["components"][0]["languages"] = [
        {"code": "ru", "xml_lang": "ru", "column": 3, "header": "ru"},
        {"code": "zh_Hans", "xml_lang": "zh-Hans", "column": 4, "header": "zh_Hans"},
    ]
    valid_profile["components"][0]["source_lang"] = "ru"
    path = _write(tmp_path, valid_profile)
    profile = load_profile(path)
    assert profile.components[0].languages[1].xml_lang == "zh-Hans"


# ---------------------------------------------------------------------------
# PO-specific validation
# ---------------------------------------------------------------------------


def test_po_requires_key(valid_profile, tmp_path):
    del valid_profile["components"][0]["key"]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="key"):
        load_profile(path)


def test_po_requires_first_data_row(valid_profile, tmp_path):
    del valid_profile["components"][0]["first_data_row"]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="first_data_row"):
        load_profile(path)


def test_po_rejects_tbx_fields(valid_profile, tmp_path):
    valid_profile["components"][0]["key_language"] = "en"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="key_language"):
        load_profile(path)


def test_po_rejects_pair_grammar(valid_profile, tmp_path):
    valid_profile["components"][0]["grammar"] = {
        "type": "term-description-pairs",
        "regions": [
            {"section_row": 3, "first_term_row": 4, "last_description_row": 7},
        ],
    }
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="grammar"):
        load_profile(path)


def test_po_optional_arrays_default_empty(valid_profile, tmp_path):
    del valid_profile["components"][0]["comments"]
    del valid_profile["components"][0]["references"]
    path = _write(tmp_path, valid_profile)
    profile = load_profile(path)
    comp = profile.components[0]
    assert comp.comments == ()
    assert comp.references == ()


def test_po_grammar_defaults(valid_profile, tmp_path):
    del valid_profile["components"][0]["grammar"]["skip_rows"]
    del valid_profile["components"][0]["grammar"]["allow_blank_rows"]
    path = _write(tmp_path, valid_profile)
    profile = load_profile(path)
    grammar = profile.components[0].grammar
    assert grammar.skip_rows == ()
    assert grammar.allow_blank_rows is False


# ---------------------------------------------------------------------------
# TBX-specific validation
# ---------------------------------------------------------------------------


def test_tbx_requires_key_language(valid_profile, tmp_path):
    del valid_profile["components"][1]["key_language"]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="key_language"):
        load_profile(path)


def test_tbx_requires_initial_target_languages(valid_profile, tmp_path):
    del valid_profile["components"][1]["initial_target_languages"]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="initial_target_languages"):
        load_profile(path)


def test_tbx_empty_initial_target_languages_raises(valid_profile, tmp_path):
    valid_profile["components"][1]["initial_target_languages"] = []
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="initial_target_languages"):
        load_profile(path)


def test_tbx_initial_target_languages_excluding_source(valid_profile, tmp_path):
    valid_profile["components"][1]["initial_target_languages"] = ["en", "ru"]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="source"):
        load_profile(path)


def test_tbx_rejects_po_fields(valid_profile, tmp_path):
    valid_profile["components"][1]["key"] = {"column": 1, "header": "id"}
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="key"):
        load_profile(path)


def test_tbx_key_language_must_be_in_languages(valid_profile, tmp_path):
    valid_profile["components"][1]["key_language"] = "fr"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="key_language"):
        load_profile(path)


def test_tbx_rejects_keyed_grammar(valid_profile, tmp_path):
    valid_profile["components"][1]["grammar"] = {
        "type": "keyed",
    }
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="grammar"):
        load_profile(path)


# ---------------------------------------------------------------------------
# Pair region validation
# ---------------------------------------------------------------------------


def test_pair_region_odd_length_raises(valid_profile, tmp_path):
    valid_profile["components"][1]["grammar"]["regions"] = [
        {"section_row": 3, "first_term_row": 4, "last_description_row": 6},
    ]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="pair"):
        load_profile(path)


def test_pair_region_overlap_raises(valid_profile, tmp_path):
    valid_profile["components"][1]["grammar"]["regions"] = [
        {"section_row": 3, "first_term_row": 4, "last_description_row": 9},
        {"section_row": 8, "first_term_row": 9, "last_description_row": 10},
    ]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="overlap"):
        load_profile(path)


def test_pair_region_section_at_or_before_header_raises(valid_profile, tmp_path):
    valid_profile["components"][1]["grammar"]["regions"] = [
        {"section_row": 1, "first_term_row": 4, "last_description_row": 7},
    ]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="header"):
        load_profile(path)


def test_pair_region_section_outside_range_raises(valid_profile, tmp_path):
    valid_profile["components"][1]["grammar"]["regions"] = [
        {"section_row": 8, "first_term_row": 4, "last_description_row": 7},
    ]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="section"):
        load_profile(path)


def test_pair_region_first_term_after_last_desc_raises(valid_profile, tmp_path):
    valid_profile["components"][1]["grammar"]["regions"] = [
        {"section_row": 3, "first_term_row": 7, "last_description_row": 4},
    ]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="term"):
        load_profile(path)


def test_pair_region_gap_between_regions(valid_profile, tmp_path):
    """A section row must not land inside the previous region's range."""
    valid_profile["components"][1]["grammar"]["regions"] = [
        {"section_row": 3, "first_term_row": 4, "last_description_row": 7},
        {"section_row": 6, "first_term_row": 8, "last_description_row": 11},
    ]
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="overlap"):
        load_profile(path)


# ---------------------------------------------------------------------------
# Successful load
# ---------------------------------------------------------------------------


def test_valid_profile_loads_successfully(valid_profile, tmp_path):
    path = _write(tmp_path, valid_profile)
    profile = load_profile(path)
    assert profile.schema_version == 1
    assert len(profile.components) == 2
    po = profile.components[0]
    assert po.kind == "po"
    assert po.component == "Temple"
    assert po.source_lang == "ru"
    assert len(po.languages) == 2
    assert po.languages[0].code == "ru"
    assert po.languages[1].code == "en"
    # 0-based conversion: header_row 1 -> 0, first_data_row 3 -> 2, column 3 -> 2
    assert po.header_row == 0
    assert po.first_data_row == 2
    assert po.languages[0].column == 2
    tbx = profile.components[1]
    assert tbx.kind == "tbx"
    assert tbx.key_language == "en"
    assert tbx.initial_target_languages == ("en", "ja")
    assert len(tbx.grammar.regions) == 2


def test_unknown_field_in_component_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["extra"] = "bad"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="unknown field"):
        load_profile(path)


def test_unknown_field_in_language_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["languages"][0]["extra"] = "bad"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="unknown field"):
        load_profile(path)


def test_unknown_field_in_grammar_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["grammar"]["extra"] = "bad"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="unknown field"):
        load_profile(path)


def test_unknown_field_in_key_raises(valid_profile, tmp_path):
    valid_profile["components"][0]["key"]["extra"] = "bad"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="unknown field"):
        load_profile(path)


def test_unknown_field_in_region_raises(valid_profile, tmp_path):
    valid_profile["components"][1]["grammar"]["regions"][0]["extra"] = "bad"
    path = _write(tmp_path, valid_profile)
    with pytest.raises(ProfileError, match="unknown field"):
        load_profile(path)
