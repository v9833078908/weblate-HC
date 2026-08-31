# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Reduce a rendered judge-run report page on stdin to flat monitor records."""

from __future__ import annotations

import html
import re
import sys

OUTCOMES = (
    "Matched",
    "Checked",
    "Cached",
    "Пропущено",
    "Repaired",
    "Rolled back",
    "Minor noted",
    "Major not fixed",
    "Critical held",
    "Unparsed",
    "Stale conflict",
    "Accepted as is",
    "Escalated",
    "Deferred",
)
FIELDS = ("Статус", "Started", "Finished")


def main() -> int:
    page = sys.stdin.read()
    if "Judge run" not in page:
        print("RUN_FETCH_ERR no-report-page")
        return 1
    text = re.sub(r"(?s)<(script|style).*?</\1>", "", page)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", text))
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    joined = "\n".join(lines)

    seen_status = False
    for index, line in enumerate(lines):
        if line in FIELDS and index + 1 < len(lines):
            key = "RUN_STATUS" if line == "Статус" else f"RUN_{line.upper()}"
            seen_status |= line == "Статус"
            print(key, lines[index + 1])
    if not seen_status:
        print("RUN_FETCH_ERR no-status-field")

    for name in OUTCOMES:
        match = re.search(rf"{re.escape(name)}: (\d+)", joined)
        if match:
            print("OUT_" + re.sub(r"\s", "_", name), match.group(1))

    failure = re.search(r"(?m)^(?:This run failed|Failure).*$", joined)
    if failure:
        print("RUN_FAILURE", failure.group(0)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
