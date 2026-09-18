#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Read-only source-markup defect probe over the ``source.tag_closing_has_attribute`` rule.

The rule lives in ``loc_kit_ingest.source_markup``.

For every requested project the probe enumerates components, reads each
component's own ``source_language``, cursor-paginates the source translation
units (``?page_size=1000``, following ``next`` to the end), and runs
``source_markup_defects()`` on each distinct ``(component, context)`` source
exactly once. Output: components seen, source units examined, a Counter by
defect code, and up to three ``(project, component, context, source)``
examples per code for manual true/false-positive classification.

Read-only: no Weblate data is modified. A production run requires separate
access authorization; the local dev instance is the default target.

Usage:
    WEBLATE_API_TOKEN=... uv run python analysis/probes/source-markup-defects.py
    WEBLATE_API_TOKEN=... uv run python analysis/probes/source-markup-defects.py \
        --project anvil-saga --api https://l10n.herocraft.com/api
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from loc_kit_ingest.source_markup import (
    source_markup_defects,
)

DEFAULT_API = "http://localhost:3001/api"

TOKEN = os.environ.get("WEBLATE_API_TOKEN", "").strip()
if not TOKEN:
    sys.exit("WEBLATE_API_TOKEN is required")

EXAMPLES_PER_CODE = 3
RETRY_DELAYS: tuple[int, ...] = (2, 4, 8, 16, 32)


def get(url: str, api: str) -> dict | list:
    # Pagination `next` links come back with the http:// scheme when the
    # instance sits behind a TLS-terminating proxy; normalize to the api base
    # scheme before the host check.
    if url.startswith("http://") and api.startswith("https://"):
        url = "https://" + url[len("http://") :]
    if not url.startswith(api):
        msg = f"refusing to fetch outside {api}: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers={"Authorization": f"Token {TOKEN}"})
    # The path to production drops TLS handshakes intermittently; an HTTP
    # error (401/404) is an answer, not an outage, and must fail fast.
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            if attempt >= len(RETRY_DELAYS):
                raise
            wait = RETRY_DELAYS[attempt]
            attempt += 1
            print(f"  transient {type(error).__name__}, retrying in {wait}s")
            time.sleep(wait)


def paginate(url: str, api: str) -> list[dict]:
    rows: list[dict] = []
    while url:
        page = get(url, api)
        if isinstance(page, list):
            return page
        rows.extend(page.get("results", []))
        url = page.get("next") or ""
    return rows


def first(values: list[str] | None) -> str:
    return values[0] if values else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Project slug. Repeatable; default is every project.",
    )
    parser.add_argument(
        "--component",
        action="append",
        default=[],
        help="Component slug filter. Repeatable.",
    )
    parser.add_argument(
        "--captured-at",
        default="",
        help="Date stamp for the output artifact path",
    )
    args = parser.parse_args()

    api = args.api.rstrip("/")
    wanted_projects = args.project
    wanted_components = set(args.component)

    components = paginate(f"{api}/projects/?page_size=100", api)
    projects = wanted_projects or [project["slug"] for project in components]

    counts: Counter[str] = Counter()
    examples: dict[str, list[dict]] = {}
    per_project: dict[str, dict] = {}
    units_seen = 0

    for project in projects:
        comp_rows = paginate(f"{api}/projects/{project}/components/?page_size=100", api)
        project_units = 0
        project_candidates = 0
        for comp in comp_rows:
            slug = comp["slug"]
            if wanted_components and slug not in wanted_components:
                continue
            # Each component carries its own source language; never assume a
            # project-wide one.
            source_lang = comp["source_language"]["code"]
            comp_units = 0
            try:
                units = paginate(
                    f"{api}/translations/{project}/{slug}/{source_lang}/units/"
                    "?page_size=1000",
                    api,
                )
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    # A component without a source translation has nothing to
                    # analyse.
                    continue
                raise
            seen: set[str] = set()
            for unit in units:
                context = unit["context"]
                if context in seen:
                    continue
                # Process the source once per (component, context).
                seen.add(context)
                source = first(unit["source"])
                if not source:
                    continue
                comp_units += 1
                for defect in source_markup_defects(source):
                    counts[defect.code] += 1
                    project_candidates += 1
                    bucket = examples.setdefault(defect.code, [])
                    if len(bucket) < EXAMPLES_PER_CODE:
                        bucket.append(
                            {
                                "project": project,
                                "component": slug,
                                "context": context,
                                "source": source,
                            }
                        )
            project_units += comp_units
            per_project.setdefault(project, {})[slug] = {
                "source_language": source_lang,
                "source_units": comp_units,
                "is_glossary": comp["is_glossary"],
            }
        per_project.setdefault(project, {})["units_seen"] = project_units
        per_project[project]["candidates"] = project_candidates
        units_seen += project_units

    print(f"projects: {', '.join(projects)}")
    for project, info in per_project.items():
        comps = {
            slug: row
            for slug, row in info.items()
            if isinstance(row, dict) and "source_language" in row
        }
        print(
            f"  {project}: {len(comps)} components, "
            f"{info['units_seen']} source units, "
            f"{info['candidates']} candidates"
        )
    print(f"source units examined: {units_seen}")
    if not counts:
        print("defects by code: none")
    for code, count in counts.most_common():
        print(f"defects by code: {code} = {count}")
        for example in examples[code]:
            print(
                f"  example {example['project']}/{example['component']} "
                f"[{example['context']}]: {example['source']!r}"
            )

    stamp = args.captured_at or "latest"
    artifact = {
        "api": api,
        "captured_at": args.captured_at or "unset",
        "rule": "source.tag_closing_has_attribute",
        "source_units_seen": units_seen,
        "counts_by_code": dict(counts),
        "examples_by_code": examples,
        "per_project": per_project,
    }
    out = ROOT / f"analysis/data/source-markup-defects-{stamp}.json"
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
