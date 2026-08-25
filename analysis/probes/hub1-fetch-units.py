# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fetch heart-abyss/hub-1 unit rows in the shape detect_misalignment.py expects.

Read-only. Reuses WeblateAuditor and load_token from the weblate-lqa skill so the
endpoint, pagination and token source stay identical to the sanctioned audit path.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parents[2]
AUDIT = REPO / ".omp/skills/weblate-lqa/scripts/audit_component.py"
LANGUAGES = ["de", "en", "es", "fr", "it", "ja", "ko", "zh_Hans", "zh_Hant"]


def load_audit_module() -> Any:
    """Import the skill's audit script by path; it is not an installed package."""
    spec = importlib.util.spec_from_file_location("audit_component", AUDIT)
    if spec is None or spec.loader is None:
        msg = f"cannot load {AUDIT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://l10n.herocraft.com")
    parser.add_argument("--project", default="heart-abyss")
    parser.add_argument("--component", default="hub-1")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--lang", action="append", help="repeatable; defaults to all nine")
    args = parser.parse_args(argv)

    audit = load_audit_module()
    token = audit.load_token()
    if not token:
        print("Error: no API token in env or deploy/.env.local", file=sys.stderr)
        return 1

    languages = args.lang or LANGUAGES
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    auditor = audit.WeblateAuditor(args.url, token)

    for lang in languages:
        units = auditor.fetch_all_units(args.project, args.component, lang)
        path = out / f"{args.project}__{args.component}__{lang}.json"
        path.write_text(
            json.dumps(units, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        translated = sum(1 for u in units if u.get("target") and u["target"][0])
        print(f"{lang}: {len(units)} units, {translated} translated -> {path.name}")

    scope = [
        {
            "project": args.project,
            "component": args.component,
            "languages": list(languages),
        }
    ]
    (out / "scope.json").write_text(
        json.dumps(scope, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"scope.json: {len(languages)} languages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
