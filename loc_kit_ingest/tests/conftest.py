# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared test fixtures for loc_kit_ingest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def kit_with_profile(tmp_path):
    """Create combined CSV kit (Temple + Terms) and matching profile."""
    temple_csv = tmp_path / "temple.csv"
    temple_csv.write_text(
        "id,Character,ru,en,Id\n"
        "id-ignore,label,label,label,label\n"
        "\n"
        "sample_key,Character: Sample, leading текст,text,42\n",
        encoding="utf-8",
    )
    terms_csv = tmp_path / "terms.csv"
    terms_csv.write_text(
        "ru,en,ja\n"
        "ru,en,ja\n"
        "Characters\n"
        "Герой,Hero,ヒーロー\n"
        "Источник описание,Target explanation,Target explanation\n"
        "Weapons\n"
        "Меч,Sword,剣\n"
        "Источник меч,Source sword,Source sword\n",
        encoding="utf-8",
    )
    profile_path = tmp_path / "kit.loc-ingest.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "components": [
                    {
                        "sheet": "temple",
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
                    },
                    {
                        "sheet": "terms",
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
                                {"section_row": 3, "first_term_row": 4, "last_description_row": 5},
                                {"section_row": 6, "first_term_row": 7, "last_description_row": 8},
                            ],
                        },
                        "key_language": "en",
                        "initial_target_languages": ["en", "ja"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return (temple_csv, terms_csv), profile_path


@pytest.fixture
def mutated_kit(kit_with_profile):
    """Factory that creates a mutated kit+profile pair for a given mutation name."""

    def _create(mutation: str):
        kits, profile_path = kit_with_profile
        temple_csv, terms_csv = kits
        profile = json.loads(profile_path.read_text(encoding="utf-8"))

        if mutation == "unknown_profile_field":
            profile["surprise"] = True
        elif mutation == "duplicate_key":
            temple_csv.write_text(
                temple_csv.read_text(encoding="utf-8")
                + "sample_key,Dup,текст,text,43\n",
                encoding="utf-8",
            )
        elif mutation == "orphan_description":
            content = terms_csv.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            lines[3] = ",,"
            terms_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif mutation == "header_mismatch":
            temple_csv.write_text(
                temple_csv.read_text(encoding="utf-8").replace(
                    "id,Character,ru,en,Id", "id,Character,XX,en,Id", 1
                ),
                encoding="utf-8",
            )
        elif mutation == "missing_sheet":
            profile["components"][0]["sheet"] = "nonexistent"
        elif mutation == "corrupt_profile":
            profile_path.write_text("{bad json", encoding="utf-8")
            return (temple_csv, terms_csv), profile_path

        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        return (temple_csv, terms_csv), profile_path

    return _create
