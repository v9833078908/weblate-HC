# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import re
import time
from pathlib import Path
from threading import Lock

from app.db import DomainError
from app.integrations.weblate import WeblateClient
from app.settings import Settings

_cache: dict[tuple, tuple[float, list[dict[str, str]]]] = {}
_lock = Lock()
_ttl = 60


def normalized_code(code: str) -> str:
    return code.strip().replace("-", "_").casefold()


def load_language_catalog(remote: WeblateClient) -> list[dict[str, str]]:
    try:
        rows = remote.collect("languages/")
        result = []
        seen = set()
        for row in rows:
            code, name = row.get("code"), row.get("name")
            if (
                not isinstance(code, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_@-]{0,49}", code)
                or not isinstance(name, str)
                or not name.strip()
                or len(name) > 200
                or normalized_code(code) in seen
            ):
                raise ValueError("Invalid language catalog")
            seen.add(normalized_code(code))
            result.append({"code": code, "name": name})
        if not result:
            raise ValueError("Empty language catalog")
        return result
    except (DomainError, ValueError, TypeError, AttributeError) as error:
        raise DomainError(
            "language_catalog_unavailable",
            "Не удалось получить справочник языков Weblate. Повторите попытку позднее.",
            503,
        ) from error


def catalog_for_settings(settings: Settings) -> list[dict[str, str]]:
    try:
        path = Path(settings.weblate_token_file).resolve()
        stat = path.stat()
        key = (settings.weblate_url, str(path), stat.st_mtime_ns, stat.st_size)
    except OSError as error:
        raise DomainError(
            "language_catalog_unavailable", "Справочник языков недоступен", 503
        ) from error
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < _ttl:
            return [dict(row) for row in cached[1]]
        rows = load_language_catalog(WeblateClient(settings))
        # A single configuration per process; incomplete/failed responses never replace it.
        _cache.clear()
        _cache[key] = (time.monotonic(), rows)
        return [dict(row) for row in rows]


def canonical_language(code: str, catalog: list[dict[str, str]]) -> str:
    normalized = normalized_code(code)
    match = next(
        (row["code"] for row in catalog if normalized_code(row["code"]) == normalized), None
    )
    if match is None:
        raise DomainError("language_unknown", "Язык не найден в справочнике Weblate")
    return match


def validate_language_selection(
    source: str, targets: list[str], catalog: list[dict[str, str]]
) -> tuple[str, list[str]]:
    source = canonical_language(source, catalog) if source else ""
    targets = list(dict.fromkeys(canonical_language(code, catalog) for code in targets))
    if source in targets:
        raise DomainError(
            "language_source_target", "Язык, с которого переводить, не может быть целью"
        )
    for code in [source, *targets]:
        if code and not re.fullmatch(r"[A-Za-z]{2,3}(?:_[A-Za-z0-9]{2,8})*", code):
            raise DomainError(
                "language_unsupported",
                "Этот вариант языка пока не поддерживается форматом лок-кита",
            )
    return source, targets


def validate_draft_languages(draft: dict, settings: Settings) -> None:
    manifest = draft.get("manifest")
    if not manifest:
        raise DomainError("kit_not_ready", "Сначала сохраните языки и параметры лок-кита")
    source, targets = validate_language_selection(
        manifest["source"], manifest["targets"], catalog_for_settings(settings)
    )
    if (source, targets) != (manifest["source"], manifest["targets"]):
        raise DomainError(
            "language_selection_changed", "Повторно сохраните языки с точными кодами Weblate", 409
        )
