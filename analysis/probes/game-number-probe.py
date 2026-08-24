# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Report game-number firings for a live component, per language and per key.
# Read-only. Usage:
#   PYTHONPATH=weblate_customization/src python3 analysis/probes/game-number-probe.py
from __future__ import annotations

import collections
import json
import os
import urllib.request

from weblate_customization.checks import _numbers

DOMAIN = os.environ["PROBE_DOMAIN"]
TOKEN = os.environ["PROBE_TOKEN"]
COMPONENT = os.environ.get("PROBE_COMPONENT", "pirate-ships/localization-json")
SOURCE = os.environ.get("PROBE_SOURCE", "ru")
LANGUAGES = os.environ["PROBE_LANGUAGES"].split(",")


def get_file(language: str) -> dict[str, str]:
    url = f"https://{DOMAIN}/api/translations/{COMPONENT}/{language}/file/"
    request = urllib.request.Request(url, headers={"Authorization": f"Token {TOKEN}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


files = {language: get_file(language) for language in LANGUAGES}
source_file = files[SOURCE]

per_language: collections.Counter[str] = collections.Counter()
per_key: collections.Counter[str] = collections.Counter()
pairs = 0
for language, strings in files.items():
    if language == SOURCE:
        continue
    for key, target in strings.items():
        if not target:
            continue
        pairs += 1
        if _numbers(source_file.get(key, "")) - _numbers(target):
            per_language[language] += 1
            per_key[key] += 1

print(f"firings: {sum(per_language.values())} / {pairs} pairs")
print(f"per language: {dict(per_language)}")
print("per key:")
for key, count in per_key.most_common():
    print(f"  {count:2} languages  {key}")
