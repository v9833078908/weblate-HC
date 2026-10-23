#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Measure the ~30.5 s reset rate per route, holding the judge payload fixed.

The A/B probe showed the reset is not caused by `response_format` or by a
missing `max_tokens`: the same arm both succeeds and resets. This probe repeats
one arm - today's judge payload - to estimate how often a route resets, and
what a single retry would recover.
"""

from __future__ import annotations

# The A/B probe is loaded by path: its filename is not an importable module name.
import importlib.util
import sys
import time
from pathlib import Path

import requests

_spec = importlib.util.spec_from_file_location(
    "_ab", Path(__file__).with_name("litellm-strict-schema-ab.py")
)
_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ab)


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    models = sys.argv[2:] or ["deepseek-v4-pro", "qwen3.8-max"]
    headers = {
        "Authorization": f"Bearer {_ab.key()}",
        "Content-Type": "application/json",
    }
    for model in models:
        payload = dict(_ab.arms(model)[0][1])
        oks: list[float] = []
        resets: list[float] = []
        others: list[str] = []
        for _ in range(repeats):
            started = time.monotonic()
            try:
                r = requests.post(
                    f"{_ab.BASE}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=180.0,
                )
            except requests.RequestException as exc:
                elapsed = time.monotonic() - started
                resets.append(elapsed)
                if elapsed < 25:
                    others.append(f"{type(exc).__name__}@{elapsed:.0f}s")
            else:
                elapsed = time.monotonic() - started
                if r.status_code == 200:
                    oks.append(elapsed)
                else:
                    others.append(f"{r.status_code}@{elapsed:.0f}s")
        n = len(oks) + len(resets) + len(others)
        print(f"\n{model}: {n} requests")
        print(f"  ok    {len(oks):2}  {sorted(round(v, 1) for v in oks)}")
        print(f"  reset {len(resets):2}  {sorted(round(v, 1) for v in resets)}")
        if others:
            print(f"  other     {others}")
        if n:
            p = len(resets) / n
            print(
                f"  reset rate {p:.0%}; after one retry {p * p:.0%}; after two {p**3:.0%}"
            )


if __name__ == "__main__":
    main()
