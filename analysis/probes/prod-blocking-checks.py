#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Read-only inventory of failing checks per production project, by check id.

Answers one question the UI cannot: how many strings fail a *blocking* check,
once the advisory ones (repeat-drift) and the opt-in length proxy are taken
out. The 2026-09-11 Anvil Saga audit found a critical escaped_newline that was
invisible exactly because 556 advisory hits and 414 game-length hits sat on
top of it.

Runs against the current production code, so it derives the check ids from
this checkout rather than from the /checks/ endpoint (not deployed yet).
Writes nothing.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

BASE = "https://l10n.herocraft.com"
# Advisory or opt-in by the 2026-09-11 decision: reported, not a defect.
ADVISORY = {"repeat-drift"}
OPT_IN_NOISE = {"game-length"}
TIMEOUT = 60


def token() -> str:
    env = pathlib.Path(__file__).resolve().parents[2] / ".env.local"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("PROD_WEBLATE_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    msg = "PROD_WEBLATE_API_TOKEN not found in .env.local"
    raise SystemExit(msg)


def get(url: str, key: str) -> dict:
    request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        url, headers={"Authorization": f"Token {key}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # ruff: ignore[suspicious-url-open-usage]
        return json.loads(response.read().decode("utf-8"))


def check_ids() -> list[str]:
    """
    Check ids of this checkout.

    Two shapes, because missing the second one silently hides a whole check:
    the literal `check_id = "end_stop"` and the indirection
    `check_id = REPEAT_DRIFT_CHECK_ID` with `REPEAT_DRIFT_CHECK_ID =
    "repeat-drift"` next to it. The first version of this probe only read the
    literal form and reported zero repeat-drift hits on a corpus that has
    6911 of them.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    found: set[str] = set()
    patterns = (r'check_id\s*=\s*"([^"]+)"', r'_CHECK_ID\s*=\s*"([^"]+)"')
    for directory in ("weblate/checks", "weblate_customization/src"):
        for path in (root / directory).rglob("*.py"):
            if "/tests" in str(path):
                continue
            text = path.read_text()
            for pattern in patterns:
                found.update(re.findall(pattern, text))
    return sorted(found)


def count(query: str, key: str) -> int:
    url = f"{BASE}/api/units/?q={urllib.parse.quote(query)}&limit=1"
    return get(url, key).get("count", 0)


def main() -> None:
    key = os.environ.get("PROD_WEBLATE_API_TOKEN") or token()
    projects = [
        p["slug"] for p in get(f"{BASE}/api/projects/?limit=100", key)["results"]
    ]
    ids = check_ids()
    print(f"{len(projects)} projects, {len(ids)} check ids probed\n")

    grand: dict[str, int] = {}
    for slug in projects:
        total = count(f"project:{slug} AND has:check", key)
        rows: dict[str, int] = {}
        for cid in ids:
            hits = count(f"project:{slug} AND check:{cid}", key)
            if hits:
                rows[cid] = hits
                grand[cid] = grand.get(cid, 0) + hits
        blocking = {
            cid: n for cid, n in rows.items() if cid not in ADVISORY | OPT_IN_NOISE
        }
        print(f"## {slug}: has:check {total}")
        for cid, n in sorted(rows.items(), key=lambda kv: -kv[1]):
            tier = (
                " [advisory]"
                if cid in ADVISORY
                else " [opt-in]"
                if cid in OPT_IN_NOISE
                else ""
            )
            print(f"   {n:7d}  {cid}{tier}")
        print(f"   -> blocking check hits: {sum(blocking.values())}\n")
        sys.stdout.flush()

    print("## instance total (check hits, not units)")
    for cid, n in sorted(grand.items(), key=lambda kv: -kv[1]):
        print(f"   {n:7d}  {cid}")


if __name__ == "__main__":
    main()
