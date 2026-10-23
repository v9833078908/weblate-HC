# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def offline_language_catalog(monkeypatch):
    """Route tests must not contact a live translation server."""
    from app.services import languages
    from translate.lang.data import languages as toolkit_languages

    catalog = [{"code": code, "name": value[0]} for code, value in toolkit_languages.items()]
    monkeypatch.setattr(languages, "catalog_for_settings", lambda settings: catalog)
