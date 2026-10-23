# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import io
from xml.etree import ElementTree as ET

from app.worker import render_seed
from translate.storage.xliff import Xliff1File


def test_seed_preserves_distinct_keys_with_identical_source_and_omits_blank_targets():
    source = 'Привет "игрок"\n<color=#FF0000>{0}</color>$\r\nNext\rLast'
    component = {
        "units": [
            {"key": "dialogue/one", "values": {"ru": source, "en": "Hello\r\none\r"}},
            {"key": "dialogue/two", "values": {"ru": source, "en": "Hello two"}},
            {"key": "///leading", "values": {"ru": source, "en": "Leading"}},
            {"key": "a///b", "values": {"ru": source, "en": "Inner"}},
            {"key": "blank", "values": {"ru": "Play", "en": ""}},
        ]
    }
    payload = render_seed(component, "ru", "en")
    root = ET.fromstring(payload)
    ns = {"x": "urn:oasis:names:tc:xliff:document:1.2"}
    assert root.find("x:file", ns).attrib["target-language"] == "en"
    store = Xliff1File(io.BytesIO(payload))
    assert [
        (unit.xmlelement.get("resname"), str(unit.source), str(unit.target))
        for unit in store.units
        if unit.istranslatable()
    ] == [
        ("dialogue/one", source, "Hello\r\none\r"),
        ("dialogue/two", source, "Hello two"),
        ("///leading", source, "Leading"),
        ("a///b", source, "Inner"),
    ]
