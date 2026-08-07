# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Loc-kit glossary structural sampling and OpenRouter profile proposal.

This module lives in Weblate (not in the standalone ``loc_kit_ingest``
package): it owns the optional, site-wide OpenRouter profile suggestion,
the bounded deterministic structural sampler that feeds it, and the
post-processing of the model's JSON envelope. It never imports
``loc_kit_ingest`` and never performs the final parse/render/import; that
local validation is the responsibility of later orchestration (Task C4).
"""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.translation import gettext as _
from weblate.utils.requests import fetch_validated_url

if TYPE_CHECKING:
    from collections.abc import Sequence

# Fixed OpenRouter chat-completions endpoint. Not configurable, never derived
# from settings or user input.
OPENROUTER_API_ROOT = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_COMPLETIONS_URL = f"{OPENROUTER_API_ROOT}/chat/completions"

# Fixed request timeout matching the LLM machinery expectations (seconds).
OPENROUTER_REQUEST_TIMEOUT = 120

# Sentinel returned by :func:`build_glossary_structure_sample` when the complete
# signature payload cannot fit in ``max_bytes`` even after maximal truncation.
# The plan forbids a lossy fallback here: a signature payload that cannot fit
# is this diagnostic, not a sample with dropped signatures.
SAMPLE_TOO_LARGE = "loc_kit.sample_too_large"

# UTF-8 truncation marker appended to truncated cell excerpts.
_TRUNCATION_MARKER = "…[truncated]"

# Bounded excerpt length per cell value (UTF-8 characters). Only cell values
# are ever truncated; row signatures are never dropped.
_CELL_EXCERPT_LIMIT = 80


class SampleTooLargeError(Exception):
    """Raised when the structural sample cannot fit within ``max_bytes``."""


def _load_profile_prompt() -> str:
    """Load the static OpenRouter profile instruction text.

    The prompt is a packaged asset; this avoids duplicating instruction text in
    Python and lets tests assert against the loaded file rather than a copy.
    """
    return (
        resources.files("weblate.trans.prompts")
        .joinpath("loc_kit_profile.txt")
        .read_text(encoding="utf-8")
    )


def _row_signature(row: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """Return the structural signature of a row.

    The signature is the sorted list of ``(column_index, value_length)`` pairs
    for every nonempty cell. It captures row shape (which columns are filled)
    and bounded value lengths without retaining full cell content.
    """
    signature: list[tuple[int, int]] = []
    for index, cell in enumerate(row):
        if cell is None:
            continue
        text = str(cell)
        if text == "":
            continue
        signature.append((index, len(text)))
    return tuple(signature)


def _run_length_encode_signatures(
    signatures: Sequence[tuple[tuple[int, int], ...]],
) -> list[dict[str, object]]:
    """Run-length encode the per-row signature sequence.

    Contiguous runs of an identical signature collapse to one entry with its
    inclusive ``first_row``/``last_row`` 0-based coordinates and ``count``.
    """
    runs: list[dict[str, object]] = []
    for index, signature in enumerate(signatures):
        if runs and runs[-1]["signature"] == list(signature):
            runs[-1]["last_row"] = index
            runs[-1]["count"] = int(runs[-1]["count"]) + 1  # type: ignore[arg-type]
        else:
            runs.append(
                {
                    "signature": list(signature),
                    "first_row": index,
                    "last_row": index,
                    "count": 1,
                }
            )
    return runs


def _looks_like_header(row: Sequence[str], max_index: int) -> bool:
    """Heuristic: a row whose nonempty cells are all short and distinct.

    Used to flag candidate header rows for the representative payload.
    """
    values: list[str] = []
    for cell in row[: max_index + 1]:
        if cell is None:
            continue
        text = str(cell)
        if text == "":
            continue
        if len(text) > 32:
            return False
        values.append(text)
    if not values:
        return False
    return len(values) == len(set(values))


def _looks_like_section(row: Sequence[str]) -> bool:
    """Heuristic: a row with exactly one short, nonempty cell.

    Such rows often act as section/domain captions above record blocks.
    """
    nonempty = [str(cell) for cell in row if cell is not None and str(cell) != ""]
    if len(nonempty) != 1:
        return False
    return len(nonempty[0]) <= 64


def _excerpt(value: str) -> str:
    """Return a UTF-8-safe bounded excerpt of a cell value."""
    if len(value) <= _CELL_EXCERPT_LIMIT:
        return value
    return value[:_CELL_EXCERPT_LIMIT] + _TRUNCATION_MARKER


def build_glossary_structure_sample(
    rows: Sequence[Sequence[str]],
    sheet_name: str,
    max_bytes: int,
) -> dict[str, object]:
    """Build a bounded, deterministic structural sample of a sheet.

    The sample contains:

    * sheet metadata (name, row count, column count, header candidates);
    * a run-length encoding of every row signature (column indexes, cell count,
      bounded value lengths) so the full row shape is always recoverable;
    * deterministic representatives (first/last row of each signature run, every
      unique signature, candidate header rows, section-like rows, and evenly
      spaced remaining rows) with UTF-8-safe truncated cell excerpts; and
    * aggregate counts of omitted rows and omitted cells.

    Only cell *values* may be truncated. If the complete signature encoding
    cannot fit in ``max_bytes`` even after every representative value is
    maximally truncated, raise :class:`SampleTooLargeError` rather than emit a
    lossy sample.

    The function is deterministic: identical input always produces a
    byte-identical serialized sample.
    """
    if max_bytes <= 0:
        msg = SAMPLE_TOO_LARGE
        raise SampleTooLargeError(msg)

    row_count = len(rows)
    column_count = max((len(row) for row in rows), default=0)

    signatures = [_row_signature(row) for row in rows]
    signature_runs = _run_length_encode_signatures(signatures)

    # The signature payload is mandatory and must never be dropped. Build it
    # first and verify it alone fits; if it cannot, the sample is impossible.
    metadata = {
        "sheet": sheet_name,
        "row_count": row_count,
        "column_count": column_count,
    }
    header_candidates = [
        index
        for index, row in enumerate(rows)
        if _looks_like_header(row, column_count - 1 if column_count else 0)
    ]

    section_candidates = [
        index for index, row in enumerate(rows) if _looks_like_section(row)
    ]

    signature_payload = {
        "metadata": metadata,
        "header_candidates": header_candidates,
        "section_candidates": section_candidates,
        "signature_runs": signature_runs,
    }

    # Encode the mandatory payload. If it cannot fit even with no
    # representatives, the caller must offer manual profile upload.
    mandatory_encoded = json.dumps(
        signature_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(mandatory_encoded.encode("utf-8")) > max_bytes:
        msg = SAMPLE_TOO_LARGE
        raise SampleTooLargeError(msg)

    # Deterministic representative selection, in priority order. Duplicates are
    # removed while preserving first-seen order.
    unique_signatures: list[tuple[tuple[int, int], ...]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for signature in signatures:
        if signature not in seen:
            seen.add(signature)
            unique_signatures.append(signature)

    selected: list[int] = []
    selected_set: set[int] = set()

    def add_row(index: int) -> None:
        if 0 <= index < row_count and index not in selected_set:
            selected.append(index)
            selected_set.add(index)

    # First and last row of each contiguous signature run.
    for run in signature_runs:
        add_row(int(run["first_row"]))  # type: ignore[arg-type]
        add_row(int(run["last_row"]))  # type: ignore[arg-type]
    # Every unique signature's first occurrence.
    for signature in unique_signatures:
        for index, row_signature in enumerate(signatures):
            if row_signature == signature:
                add_row(index)
                break
    # Candidate header rows.
    for index in header_candidates:
        add_row(index)
    # Section-like rows.
    for index in section_candidates:
        add_row(index)
    # Evenly spaced remaining rows while capacity allows.
    if row_count:
        remaining = [i for i in range(row_count) if i not in selected_set]
        if remaining:
            # Spread a modest number of probes across the remaining range.
            probe_count = min(len(remaining), 50)
            for step in range(probe_count):
                position = int(step * len(remaining) / probe_count)
                add_row(remaining[position])

    # Greedily add representatives until the encoded sample approaches the cap.
    # We always re-encode to measure exact UTF-8 byte length.
    omitted_rows = 0
    omitted_cells = 0
    representatives: list[dict[str, object]] = []

    def encode_sample() -> bytes:
        payload = {
            **signature_payload,
            "representatives": representatives,
            "omitted_rows": omitted_rows,
            "omitted_cells": omitted_cells,
            "truncation_marker": _TRUNCATION_MARKER,
        }
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    for index in selected:
        row = rows[index]
        excerpt_cells = [_excerpt(str(cell)) if cell is not None else "" for cell in row]
        candidate = {
            "row": index,
            "cells": excerpt_cells,
        }
        representatives.append(candidate)
        if len(encode_sample()) > max_bytes:
            # This representative does not fit; drop it and stop adding more.
            # Its signature is still fully present in signature_runs.
            representatives.pop()
            break

    # Rows without a verbatim representative are represented solely by their
    # structural signature. Tally them and their omitted cells (informational).
    represented_rows = {int(r["row"]) for r in representatives}  # type: ignore[arg-type]
    omitted_rows = row_count - len(represented_rows)
    for index, row in enumerate(rows):
        if index in represented_rows:
            continue
        omitted_cells += sum(
            1 for cell in row if cell is not None and str(cell) != ""
        )

    encoded = encode_sample()
    if len(encoded) > max_bytes:
        # Should be unreachable because the mandatory payload fit and we add
        # representatives incrementally; guard regardless.
        msg = SAMPLE_TOO_LARGE
        raise SampleTooLargeError(msg)

    # Return the decoded dict so callers can re-serialize deterministically.
    return json.loads(encoded.decode("utf-8"))


def _openrouter_response_schema() -> dict[str, object]:
    """Return the strict JSON Schema for the OpenRouter response envelope."""
    return {
        "type": "object",
        "additional_properties": False,
        "required": ["status", "profile", "assumptions", "reason"],
        "properties": {
            "status": {"type": "string", "enum": ["profile", "unsupported"]},
            "profile": {
                "oneOf": [
                    {
                        "type": "object",
                        "additional_properties": False,
                        "required": ["schema_version", "components"],
                        "properties": {
                            "schema_version": {"type": "integer", "const": 2},
                            "components": {"type": "array"},
                        },
                    },
                    {"type": "null"},
                ]
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reason": {"type": ["string", "null"]},
        },
    }


class ProfileProposalError(Exception):
    """Recoverable failure producing a loc-kit profile proposal."""


def _short_circuit_failure(reason: str) -> ProfileProposalError:
    return ProfileProposalError(reason)


def request_profile_proposal(sample: dict[str, object]) -> dict[str, object]:
    """Request a loc-kit glossary profile proposal from OpenRouter.

    Refuses without any network call when the feature is disabled or the
    site-wide key/model is absent. On success returns the validated response
    envelope dict. On any failure (disabled, misconfigured, network error,
    timeout, malformed JSON, bad envelope shape, HTTP error status) raises
    :class:`ProfileProposalError` with a concise, generic message that never
    contains the API key, the raw sample, or the raw model response.
    """
    if not settings.LOC_KIT_PROFILE_ANALYSIS_ENABLED:
        raise _short_circuit_failure(
            _("Loc-kit glossary profile analysis is disabled.")
        )
    if not settings.LOC_KIT_PROFILE_OPENROUTER_KEY:
        raise _short_circuit_failure(
            _("Loc-kit glossary profile analysis is not configured.")
        )
    if not settings.LOC_KIT_PROFILE_OPENROUTER_MODEL:
        raise _short_circuit_failure(
            _("Loc-kit glossary profile analysis is not configured.")
        )

    try:
        prompt = _load_profile_prompt()
    except Exception:  # noqa: BLE001 - packaged asset load failure
        raise _short_circuit_failure(
            _("Loc-kit profile analysis prompt could not be loaded.")
        )

    payload = {
        "model": settings.LOC_KIT_PROFILE_OPENROUTER_MODEL,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "loc_kit_profile_envelope",
                "strict": True,
                "schema": _openrouter_response_schema(),
            },
        },
        "provider": {
            "require_parameters": True,
        },
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(sample, ensure_ascii=False)},
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.LOC_KIT_PROFILE_OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = fetch_validated_url(
            "POST",
            OPENROUTER_CHAT_COMPLETIONS_URL,
            headers=headers,
            json=payload,
            timeout=OPENROUTER_REQUEST_TIMEOUT,
            raise_for_status=False,
        )
    except Exception:  # noqa: BLE001 - any outbound failure is recoverable
        raise _short_circuit_failure(
            _("Loc-kit profile analysis request failed.")
        )

    if response.status_code >= 400:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis request failed (HTTP %(code)s).")
            % {"code": response.status_code}
        )

    try:
        envelope_raw = response.json()
    except Exception:  # noqa: BLE001 - malformed JSON is recoverable
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unreadable response.")
        )

    content = _extract_message_content(envelope_raw)
    if content is None:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unreadable response.")
        )

    try:
        envelope = json.loads(content)
    except (ValueError, TypeError):
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unreadable response.")
        )

    try:
        _validate_envelope(envelope)
    except _EnvelopeValidationError:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unusable response.")
        )

    return envelope


class _EnvelopeValidationError(Exception):
    """Internal signal that the response envelope failed validation."""


def _extract_message_content(envelope_raw: object) -> str | None:
    """Return ``choices[0].message.content`` as a string, else None."""
    if not isinstance(envelope_raw, dict):
        return None
    choices = envelope_raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    return content


def _validate_envelope(envelope: object) -> None:
    """Validate the parsed response envelope shape per the contract.

    Requires ``status``/``profile``/``assumptions``/``reason`` to be present.
    ``profile`` must be null iff ``status == "unsupported"``, in which case
    ``reason`` must be a nonempty string.
    """
    if not isinstance(envelope, dict):
        raise _EnvelopeValidationError
    for field in ("status", "profile", "assumptions", "reason"):
        if field not in envelope:
            raise _EnvelopeValidationError
    status = envelope["status"]
    profile = envelope["profile"]
    assumptions = envelope["assumptions"]
    reason = envelope["reason"]
    if status not in ("profile", "unsupported"):
        raise _EnvelopeValidationError
    if not isinstance(assumptions, list):
        raise _EnvelopeValidationError
    if status == "unsupported":
        if profile is not None:
            raise _EnvelopeValidationError
        if not isinstance(reason, str) or reason == "":
            raise _EnvelopeValidationError
    else:
        if profile is None:
            raise _EnvelopeValidationError
        if not isinstance(profile, dict):
            raise _EnvelopeValidationError


__all__ = [
    "OPENROUTER_API_ROOT",
    "OPENROUTER_CHAT_COMPLETIONS_URL",
    "OPENROUTER_REQUEST_TIMEOUT",
    "SAMPLE_TOO_LARGE",
    "ProfileProposalError",
    "SampleTooLargeError",
    "build_glossary_structure_sample",
    "request_profile_proposal",
]
