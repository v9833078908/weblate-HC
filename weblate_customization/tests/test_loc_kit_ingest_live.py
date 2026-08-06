# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Opt-in live Routed LLM smoke test.

Verifies that the configured routed-llm service returns one real translation
through the actual Weblate machinery entrypoint. SKIPPED by default:

    LOC_INGEST_LIVE_LLM=1 ./rundev.sh test \
        weblate_customization/tests/test_loc_kit_ingest_live.py

Prerequisites: routed-llm configured in /manage/machinery/ with a live
OpenRouter key and an "en" routing entry. Makes exactly ONE LLM request.

The deterministic glossary-payload contract (source/target explanations in the
LLM glossary entry) is covered without network access by
weblate/trans/tests/test_loc_kit_ingest_contract.py.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LOC_INGEST_LIVE_LLM") != "1",
    reason="set LOC_INGEST_LIVE_LLM=1 to spend one routed LLM request",
)

SOURCE_TEXT = "Мудрец говорит мудро."


@pytest.mark.django_db(transaction=True)
def test_routed_llm_returns_one_real_translation():
    from weblate_customization.machinery import RoutedLLMTranslation

    from weblate.configuration.models import Setting, SettingCategory

    service_id = RoutedLLMTranslation.get_identifier()

    config = None
    try:
        config = Setting.objects.get(category=SettingCategory.MT, name=service_id).value
    except Setting.DoesNotExist:
        # pytest runs against a fresh test database; the service configured on
        # the dev instance lives in the real one. Read it directly.
        import psycopg

        with psycopg.connect(
            host=os.environ.get("CI_DB_HOST", "database"),
            dbname=os.environ.get("WEBLATE_LIVE_DB", "weblate"),
            user=os.environ.get("CI_DB_USER", "weblate"),
            password=os.environ.get("CI_DB_PASSWORD", "weblate"),
        ) as conn:
            row = conn.execute(
                "SELECT value FROM configuration_setting"
                " WHERE category = %s AND name = %s",
                (int(SettingCategory.MT), service_id),
            ).fetchone()
        if row is None:
            pytest.skip(f"{service_id} service not configured on the instance")
        config = row[0]

    if not config.get("key"):
        pytest.skip("OpenRouter key not configured for routed-llm")
    routing = config.get("routing", {})
    if "en" not in routing and "*" not in routing:
        pytest.skip("no routing entry (nor '*' fallback) for 'en'")
    service = RoutedLLMTranslation(configuration=config)

    # The real machinery entrypoint, with service language codes as strings —
    # the same shape validate_settings() uses.
    result = service.download_multiple_translations("ru", "en", [(SOURCE_TEXT, None)])

    translations = result[SOURCE_TEXT]
    assert translations, "routed LLM returned no candidates"
    text = translations[0]["text"]
    assert text and text != SOURCE_TEXT
