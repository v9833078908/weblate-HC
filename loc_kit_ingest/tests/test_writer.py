# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import pytest
from translate.storage.pypo import pofile
from translate.storage.tbx import tbxfile

from loc_kit_ingest.model import GlossaryTerm, StringUnit
from loc_kit_ingest.profile import load_profile
from loc_kit_ingest.writer import render_component, validate_rendered_component

# ---------------------------------------------------------------------------
# Fixtures: build in-memory parsed components
# ---------------------------------------------------------------------------


@pytest.fixture
def po_component(tmp_path):
    """A ComponentProfile + ParseResult-like units for a PO component."""
    profile_path = Path(__file__).parent / "fixtures" / "temple.loc-ingest.json"
    profile = load_profile(profile_path)
    comp = profile.components[0]
    # We return (component, units) - the writer receives a ParseResult
    from loc_kit_ingest.model import ParseResult

    units = (
        StringUnit(
            key="sample_key",
            values={
                "ru": " leading [shake]текст[/shake]\r\n ",
                "en": "<color=#E3BA59>Text &#13;{value:cond:1}</color>",
            },
            comments=("Character: Sample",),
            references=("42",),
            row=4,
        ),
    )
    result = ParseResult(
        component=comp.component,
        kind="po",
        units=units,
        diagnostics=(),
        skipped_rows=(),
    )
    return comp, result


@pytest.fixture
def tbx_component(tmp_path):
    """A ComponentProfile + units for a TBX component."""
    profile_path = Path(__file__).parent / "fixtures" / "terms.loc-ingest.json"
    profile = load_profile(profile_path)
    comp = profile.components[0]
    from loc_kit_ingest.model import ParseResult

    units = (
        GlossaryTerm(
            context='["Characters","Герой"]',
            values={"ru": "Герой", "en": "Hero", "ja": "ヒーロー"},
            source_explanation="Источник описание",
            target_explanations={"en": "Target explanation", "ja": ""},
            section="Characters",
            term_row=4,
            note_rows=(5,),
        ),
        GlossaryTerm(
            context='["Weapons","Меч"]',
            values={"ru": "Меч", "en": "Sword", "ja": "剣"},
            source_explanation="Источник меч",
            target_explanations={"en": "Source sword", "ja": ""},
            section="Weapons",
            term_row=9,
            note_rows=(10,),
        ),
    )
    result = ParseResult(
        component=comp.component,
        kind="tbx",
        units=units,
        diagnostics=(),
        skipped_rows=(),
    )
    return comp, result


# ---------------------------------------------------------------------------
# PO rendering
# ---------------------------------------------------------------------------


def test_po_roundtrip_preserves_exact_text_and_metadata(tmp_path, po_component):
    comp, result = po_component
    paths = render_component(comp, result, tmp_path)
    source = pofile.parsestring(paths["ru"].read_bytes())
    unit = next(item for item in source.units if item.getid() == "sample_key")
    assert unit.target == " leading [shake]текст[/shake]\r\n "
    assert unit.getnotes("developer") == "Character: Sample"
    assert unit.getlocations() == ["42"]
    assert validate_rendered_component(comp, result, tmp_path) == ()


def test_po_writes_one_file_per_language(tmp_path, po_component):
    comp, result = po_component
    paths = render_component(comp, result, tmp_path)
    assert set(paths) == {"ru", "en"}
    en_store = pofile.parsestring(paths["en"].read_bytes())
    unit = next(item for item in en_store.units if item.getid() == "sample_key")
    assert unit.target == "<color=#E3BA59>Text &#13;{value:cond:1}</color>"


def test_po_source_file_has_developer_comments_and_references(tmp_path, po_component):
    comp, result = po_component
    paths = render_component(comp, result, tmp_path)
    source = pofile.parsestring(paths[comp.source_lang].read_bytes())
    unit = next(item for item in source.units if item.getid() == "sample_key")
    assert unit.getnotes("developer") == "Character: Sample"
    assert "42" in unit.getlocations()


# ---------------------------------------------------------------------------
# TBX rendering
# ---------------------------------------------------------------------------


def test_tbx_has_two_languages_and_both_explanations(tmp_path, tbx_component):
    comp, result = tbx_component
    paths = render_component(comp, result, tmp_path)
    assert set(paths) == {"en", "ja"}
    xml = paths["en"].read_text(encoding="utf-8")
    assert 'xml:lang="ru"' in xml and 'xml:lang="en"' in xml
    assert "Источник описание" in xml
    assert "Target explanation" in xml


def test_tbx_no_source_language_file(tmp_path, tbx_component):
    comp, result = tbx_component
    paths = render_component(comp, result, tmp_path)
    # source_lang is "ru" - must not have ru.tbx
    assert comp.source_lang not in paths


def test_tbx_no_file_for_unconfigured_target(tmp_path, tbx_component):
    comp, result = tbx_component
    paths = render_component(comp, result, tmp_path)
    # ja is in initial_target_languages, but check no arbitrary codes
    for key in paths:
        assert key in comp.initial_target_languages


def test_tbx_uses_profile_xml_lang_tags(tmp_path, tbx_component):
    """TBX files use xml_lang from profile, not Weblate code."""
    comp, result = tbx_component
    paths = render_component(comp, result, tmp_path)
    en_xml = paths["en"].read_text(encoding="utf-8")
    # en lang column has xml_lang="en"
    en_col = next(l for l in comp.languages if l.code == "en")
    assert f'xml:lang="{en_col.xml_lang}"' in en_xml


def test_tbx_parse_back_validates(tmp_path, tbx_component):
    comp, result = tbx_component
    render_component(comp, result, tmp_path)
    assert validate_rendered_component(comp, result, tmp_path) == ()


def test_tbx_parse_back_checks_explanations(tmp_path, tbx_component):
    comp, result = tbx_component
    paths = render_component(comp, result, tmp_path)
    parsed = tbxfile.parsestring(paths["en"].read_bytes())
    for unit in parsed.units:
        assert unit.getid() in ('["Characters","Герой"]', '["Weapons","Меч"]')
        assert unit.source  # source term exists
        assert unit.target  # target term exists
        # definition note = source explanation
        assert unit.getnotes("definition")
