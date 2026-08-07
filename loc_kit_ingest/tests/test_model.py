# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from loc_kit_ingest.model import (
    Diagnostic,
    GlossaryTerm,
    Severity,
    SkippedRow,
    StringUnit,
)


def test_string_unit_keeps_text_and_metadata_verbatim():
    unit = StringUnit(
        key="sample_key",
        values={"ru": " leading\tтекст\r\n", "en": " leading\ttext\r\n"},
        comments=("Character: Sample",),
        references=("42",),
        row=3,
    )
    assert unit.values["ru"] == " leading\tтекст\r\n"
    assert unit.comments == ("Character: Sample",)


def test_glossary_term_separates_source_and_target_explanations():
    term = GlossaryTerm(
        context='["Characters","Герой"]',
        values={"ru": "Герой", "en": "Hero"},
        source_explanation="Описание",
        target_explanations={"en": "Description"},
        section="Characters",
        term_row=4,
        note_rows=(5,),
    )
    assert term.source_explanation == "Описание"
    assert term.target_explanations == {"en": "Description"}


def test_diagnostic_and_skip_are_typed():
    diagnostic = Diagnostic(Severity.ERROR, "profile.unknown_field", "UI", "Sheet", 2, "bad")
    skipped = SkippedRow("Temple", "Temple", 9, "blank")
    assert diagnostic.severity is Severity.ERROR
    assert skipped.reason == "blank"
