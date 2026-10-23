# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Loc-kit glossary structural sampling and OpenRouter profile proposal.

This module lives in Weblate (not in the standalone ``loc_kit_ingest``
package): it owns the optional, site-wide OpenRouter profile suggestion,
the bounded deterministic structural sampler that feeds it, and the
post-processing of the model's JSON envelope. It never imports
``loc_kit_ingest`` and never performs the final parse/render/import; that
local validation is the responsibility of later orchestration (Task C4).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypedDict

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from weblate.checks.flags import Flags
from weblate.trans.models.pending import PendingUnitChange
from weblate.utils.lock import WeblateLockTimeoutError
from weblate.utils.requests import fetch_validated_url
from weblate.utils.state import STATE_TRANSLATED

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from loc_kit_ingest.model import Diagnostic, GlossaryTerm, StringUnit
    from weblate.auth.models import AuthenticatedHttpRequest, User
    from weblate.trans.models import Component, Translation, Unit
    from weblate.trans.models.loc_kit import LocKitImportDraft


# Fixed OpenRouter chat-completions endpoint. Not configurable, never derived
# from settings or user input.
OPENROUTER_API_ROOT = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_COMPLETIONS_URL = f"{OPENROUTER_API_ROOT}/chat/completions"
OPENROUTER_LOC_KIT_PROFILE_TITLE = "HCGameLoc Weblate - Loc-kit Profile Analysis"

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

# ``loc-kit-strings-update`` is synchronous. Bound its work by every
# non-empty input cell which may produce a unit, target, flag, or Explanation
# write. The largest tracked string kit has 3,960 such cells.
LOC_KIT_STRING_UPDATE_MAX_MUTATIONS = 5_000

# Background application processes the prepared table in bounded portions of
# whole rows so the component lock and any single DB transaction never span
# an entire large table. A crash between portions resumes from the durable
# ``next_row`` cursor instead of repeating already-committed rows.
LOC_KIT_STRING_UPDATE_PORTION_SIZE = 25


class SampleTooLargeError(Exception):
    """Raised when the structural sample cannot fit within ``max_bytes``."""


def load_profile_prompt() -> str:
    """
    Load the static OpenRouter profile instruction text.

    The prompt is a packaged asset; this avoids duplicating instruction text in
    Python and lets tests assert against the loaded file rather than a copy.
    """
    return (
        resources.files("weblate.trans.prompts")
        .joinpath("loc_kit_profile.txt")
        .read_text(encoding="utf-8")
    )


def _row_signature(row: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """
    Return the structural signature of a row.

    The signature is the sorted list of ``(column_index, value_length)`` pairs
    for every nonempty cell. It captures row shape (which columns are filled)
    and bounded value lengths without retaining full cell content.
    """
    signature: list[tuple[int, int]] = []
    for index, cell in enumerate(row):
        if cell is None:
            continue
        text = str(cell)
        if not text:
            continue
        signature.append((index, len(text)))
    return tuple(signature)


class _SignatureRun(TypedDict):
    """One contiguous run of rows sharing an identical structural signature."""

    signature: list[list[int]]
    first_row: int
    last_row: int
    count: int


class _Representative(TypedDict):
    """One row carried verbatim (with bounded excerpts) in the sample."""

    row: int
    cells: list[str]


def _run_length_encode_signatures(
    signatures: Sequence[tuple[tuple[int, int], ...]],
) -> list[_SignatureRun]:
    """
    Run-length encode the per-row signature sequence.

    Contiguous runs of an identical signature collapse to one entry with its
    inclusive ``first_row``/``last_row`` 0-based coordinates and ``count``.
    """
    runs: list[_SignatureRun] = []
    for index, signature in enumerate(signatures):
        if runs and runs[-1]["signature"] == [list(pair) for pair in signature]:
            runs[-1]["last_row"] = index
            runs[-1]["count"] += 1
        else:
            runs.append(
                _SignatureRun(
                    signature=[list(pair) for pair in signature],
                    first_row=index,
                    last_row=index,
                    count=1,
                )
            )
    return runs


def _looks_like_header(row: Sequence[str], max_index: int) -> bool:
    """
    Heuristic: a row whose nonempty cells are all short and distinct.

    Used to flag candidate header rows for the representative payload.
    """
    values: list[str] = []
    for cell in row[: max_index + 1]:
        if cell is None:
            continue
        text = str(cell)
        if not text:
            continue
        if len(text) > 32:
            return False
        values.append(text)
    if not values:
        return False
    return len(values) == len(set(values))


def _looks_like_section(row: Sequence[str]) -> bool:
    """
    Heuristic: a row with exactly one short, nonempty cell.

    Such rows often act as section/domain captions above record blocks.
    """
    nonempty = [str(cell) for cell in row if cell is not None and str(cell)]
    if len(nonempty) != 1:
        return False
    return len(nonempty[0]) <= 64


def _excerpt(value: str) -> str:
    """Return a UTF-8-safe bounded excerpt of a cell value."""
    if len(value) <= _CELL_EXCERPT_LIMIT:
        return value
    return value[:_CELL_EXCERPT_LIMIT] + _TRUNCATION_MARKER


def _select_representative_rows(
    *,
    signatures: list[tuple[tuple[int, int], ...]],
    signature_runs: list[_SignatureRun],
    header_candidates: list[int],
    section_candidates: list[int],
    row_count: int,
) -> list[int]:
    """
    Choose which rows carry verbatim cell excerpts, deterministically.

    Priority order: the first and last row of every contiguous signature run,
    then the first occurrence of each unique signature, then header and
    section candidates, then evenly spaced remaining rows. Duplicates are
    dropped while preserving first-seen order, so identical input always
    yields an identical list.
    """
    selected: list[int] = []
    selected_set: set[int] = set()

    def add_row(index: int) -> None:
        if 0 <= index < row_count and index not in selected_set:
            selected.append(index)
            selected_set.add(index)

    for run in signature_runs:
        add_row(run["first_row"])
        add_row(run["last_row"])

    seen: set[tuple[tuple[int, int], ...]] = set()
    for index, signature in enumerate(signatures):
        if signature not in seen:
            seen.add(signature)
            add_row(index)

    for index in header_candidates:
        add_row(index)
    for index in section_candidates:
        add_row(index)

    if row_count:
        remaining = [i for i in range(row_count) if i not in selected_set]
        if remaining:
            step = max(1, len(remaining) // 32)
            for position in range(0, len(remaining), step):
                add_row(remaining[position])

    return selected


def build_glossary_structure_sample(
    rows: Sequence[Sequence[str]],
    sheet_name: str,
    max_bytes: int,
) -> dict[str, object]:
    """
    Build a bounded, deterministic structural sample of a sheet.

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

    selected = _select_representative_rows(
        signatures=signatures,
        signature_runs=signature_runs,
        header_candidates=header_candidates,
        section_candidates=section_candidates,
        row_count=row_count,
    )

    # Greedily add representatives until the encoded sample approaches the cap.
    # We always re-encode to measure exact UTF-8 byte length.
    omitted_rows = 0
    omitted_cells = 0
    representatives: list[_Representative] = []

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
        excerpt_cells = [
            _excerpt(str(cell)) if cell is not None else "" for cell in row
        ]
        representatives.append(_Representative(row=index, cells=excerpt_cells))
        if len(encode_sample()) > max_bytes:
            # This representative does not fit; drop it and stop adding more.
            # Its signature is still fully present in signature_runs.
            representatives.pop()
            break

    # Rows without a verbatim representative are represented solely by their
    # structural signature. Tally them and their omitted cells (informational).
    represented_rows = {r["row"] for r in representatives}
    omitted_rows = row_count - len(represented_rows)
    for index, row in enumerate(rows):
        if index in represented_rows:
            continue
        omitted_cells += sum(1 for cell in row if cell is not None and str(cell))

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
    """
    Request a loc-kit glossary profile proposal from OpenRouter.

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
        prompt = load_profile_prompt()
    except Exception as error:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis prompt could not be loaded.")
        ) from error

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
    try:
        response = fetch_validated_url(
            "POST",
            OPENROUTER_CHAT_COMPLETIONS_URL,
            # Built inline so the bearer token is never bound to a frame
            # local that an error reporter could serialize.
            headers={
                "Authorization": (f"Bearer {settings.LOC_KIT_PROFILE_OPENROUTER_KEY}"),
                "Content-Type": "application/json",
                "X-OpenRouter-Title": OPENROUTER_LOC_KIT_PROFILE_TITLE,
            },
            json=payload,
            timeout=OPENROUTER_REQUEST_TIMEOUT,
            raise_for_status=False,
            # A chat-completions POST has no legitimate reason to redirect.
            follow_redirects=False,
        )
    except Exception as error:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis request failed.")
        ) from error

    if response.status_code >= 400:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis request failed (HTTP %(code)s).")
            % {"code": response.status_code}
        )

    try:
        envelope_raw = response.json()
    except Exception as error:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unreadable response.")
        ) from error

    content = _extract_message_content(envelope_raw)
    if content is None:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unreadable response.")
        )

    try:
        envelope = json.loads(content)
    except (ValueError, TypeError) as error:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unreadable response.")
        ) from error

    try:
        _validate_envelope(envelope)
    except _EnvelopeValidationError as error:
        raise _short_circuit_failure(
            _("Loc-kit profile analysis returned an unusable response.")
        ) from error

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
    """
    Validate the parsed response envelope shape per the contract.

    Requires ``status``/``profile``/``assumptions``/``reason`` to be present.
    ``profile`` must be null iff ``status == "unsupported"``, in which case
    ``reason`` must be a nonempty string.
    """
    if not isinstance(envelope, dict):
        raise _EnvelopeValidationError
    for key in ("status", "profile", "assumptions", "reason"):
        if key not in envelope:
            raise _EnvelopeValidationError
    status = envelope["status"]
    profile = envelope["profile"]
    assumptions = envelope["assumptions"]
    reason = envelope["reason"]
    if status not in {"profile", "unsupported"}:
        raise _EnvelopeValidationError
    if not isinstance(assumptions, list):
        raise _EnvelopeValidationError
    if status == "unsupported":
        if profile is not None:
            raise _EnvelopeValidationError
        if not isinstance(reason, str) or not reason:
            raise _EnvelopeValidationError
    else:
        if profile is None:
            raise _EnvelopeValidationError
        if not isinstance(profile, dict):
            raise _EnvelopeValidationError


# --------------------------------------------------------------------------- #
# Local candidate validation (never trusts the model)
# --------------------------------------------------------------------------- #

# How many terms the preview shows. The preview is a human sanity check, not a
# data browser.
PREVIEW_TERM_LIMIT = 10

# How many warnings a draft may store. Warnings are emitted per row and per
# language, so a wide sheet with one systematic defect - say, every description
# cell exported with a trailing newline - would otherwise write an unbounded
# blob into the draft row and render all of it into the preview page.
PREVIEW_WARNING_LIMIT = 50

# Only the record-map schema may be created through this UI flow.
GLOSSARY_SCHEMA_VERSION = 2


class GlossaryProfileError(Exception):
    """
    A candidate profile did not survive local validation.

    Always recoverable: the UI shows the message and offers a manual profile
    upload. It never leads to a component.
    """

    def __init__(self, message: str, *, details: Sequence[str] = ()) -> None:
        self.message = message
        self.details = tuple(details)
        super().__init__(message)


@dataclass(frozen=True)
class GlossaryTermPreview:
    """One bounded row of the preview table."""

    section: str
    source: str
    targets: dict[str, str]
    source_explanation: str
    target_explanations: dict[str, str]
    source_flags: tuple[str, ...]


@dataclass(frozen=True)
class GlossaryPreview:
    """
    The result of a fully validated candidate profile.

    ``files`` holds the rendered TBX bytes. They belong to the draft
    lifecycle: nothing is written to a repository until the operator
    confirms creation.
    """

    sheet: str
    component: str
    source_language: str
    target_languages: tuple[str, ...]
    term_count: int
    note_count: int
    warnings: tuple[str, ...]
    terms: tuple[GlossaryTermPreview, ...]
    # The full validated set, uncapped. Never serialized into
    # draft.preview_json and never rendered by the UI: the preview table
    # is a bounded sanity sample, while an append apply must not lose rows
    # past PREVIEW_TERM_LIMIT.
    all_terms: tuple[GlossaryTerm, ...]
    profile_json: str
    files: dict[str, bytes]


def profile_document_from_envelope(envelope: dict[str, object]) -> dict[str, object]:
    """
    Extract the candidate profile from a validated response envelope.

    ``status: "unsupported"`` is a normal, expected answer: the model could
    not read the layout. It is surfaced verbatim to the operator and never
    treated as a profile.
    """
    status = envelope.get("status")
    if status == "unsupported":
        reason = envelope.get("reason")
        raise GlossaryProfileError(
            _("The analyzer could not map this sheet: %s") % reason
        )
    if status != "profile":
        raise GlossaryProfileError(_("The analyzer returned an unusable answer."))
    profile = envelope.get("profile")
    if not isinstance(profile, dict):
        raise GlossaryProfileError(_("The analyzer returned an unusable answer."))
    return profile


def _canonical_profile_json(document: dict[str, object]) -> str:
    """Serialize a profile for download, preserving Unicode."""
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def _format_diagnostics(diagnostics: Sequence[Diagnostic]) -> list[str]:
    return [
        _("Row %(row)d: %(message)s")
        % {"row": diagnostic.row, "message": diagnostic.message}
        for diagnostic in diagnostics
    ]


def cap_preview_warnings(warnings: Sequence[str]) -> list[str]:
    """
    Bound what one sheet can write into a draft's stored preview.

    Warnings are per row and per language, so row count times language count
    is attacker-controlled through the uploaded file. Errors and sample terms
    are already capped; warnings are the remaining unbounded path into the
    draft row and the preview page.
    """
    if len(warnings) <= PREVIEW_WARNING_LIMIT:
        return list(warnings)
    hidden = len(warnings) - PREVIEW_WARNING_LIMIT
    return [
        *warnings[:PREVIEW_WARNING_LIMIT],
        ngettext("+%d more warning", "+%d more warnings", hidden) % hidden,
    ]


def validate_glossary_profile(
    *,
    profile_document: dict[str, object],
    rows: Sequence[Sequence[str]],
    sheet_name: str,
    component_name: str,
) -> GlossaryPreview:
    """
    Validate a candidate profile against the real sheet, locally.

    This is the publication gate. Whether the candidate came from OpenRouter
    or from an operator-uploaded correction, it goes through exactly the same
    deterministic pipeline: profile schema, exact header match, full-sheet
    parse, TBX render, and parse-back equality. Only a run with zero error
    diagnostics produces a preview; anything else raises.

    The candidate's own component name is discarded and replaced with
    ``component_name``, which the server generates. A model or a corrected
    upload never names a component.
    """
    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.model import GlossaryTerm, Severity

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.parser import parse_component

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.profile import ProfileError, parse_profile

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.reader import validate_sheet_headers

    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.writer import render_component, validate_rendered_component

    if not isinstance(profile_document, dict):
        raise GlossaryProfileError(_("The profile must be a JSON object."))

    if profile_document.get("schema_version") != GLOSSARY_SCHEMA_VERSION:
        raise GlossaryProfileError(
            _("A glossary import needs a schema_version %d profile.")
            % GLOSSARY_SCHEMA_VERSION
        )

    components = profile_document.get("components")
    if not isinstance(components, list) or len(components) != 1:
        raise GlossaryProfileError(
            _("One selected sheet creates exactly one glossary component.")
        )

    candidate = components[0]
    if not isinstance(candidate, dict):
        raise GlossaryProfileError(_("The profile component must be a JSON object."))
    if candidate.get("kind") != "tbx":
        raise GlossaryProfileError(_("A glossary component must be of kind 'tbx'."))
    if candidate.get("sheet") != sheet_name:
        raise GlossaryProfileError(
            _("The profile describes sheet %(profile)s, but %(selected)s is selected.")
            % {"profile": candidate.get("sheet"), "selected": sheet_name}
        )

    # The server owns the component name.
    document = {
        **profile_document,
        "components": [{**candidate, "component": component_name}],
    }

    try:
        profile = parse_profile(document)
    except ProfileError as error:
        raise GlossaryProfileError(_("The profile is not valid: %s") % error) from error

    component = profile.components[0]
    sheet_rows = [list(row) for row in rows]

    diagnostics = list(validate_sheet_headers(component, sheet_rows))
    result = parse_component(component, sheet_rows)
    diagnostics.extend(result.diagnostics)

    errors = [d for d in diagnostics if d.severity is Severity.ERROR]
    if errors:
        raise GlossaryProfileError(
            _("The sheet does not match the profile."),
            details=_format_diagnostics(errors[:PREVIEW_TERM_LIMIT]),
        )

    # Render and parse back before the operator may confirm anything.
    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir)
        render_component(component, result, staging)
        render_errors = [
            d
            for d in validate_rendered_component(component, result, staging)
            if d.severity is Severity.ERROR
        ]
        if render_errors:
            raise GlossaryProfileError(
                _("The generated glossary did not survive parse-back."),
                details=_format_diagnostics(render_errors[:PREVIEW_TERM_LIMIT]),
            )
        tbx_dir = staging / component.component / "tbx"
        files = {path.name: path.read_bytes() for path in sorted(tbx_dir.glob("*.tbx"))}

    # parse_component returns a ParsedUnit protocol; a tbx component always
    # yields GlossaryTerm, so narrow once for both the tally and the preview.
    glossary_terms = [u for u in result.units if isinstance(u, GlossaryTerm)]
    note_count = sum(
        bool(unit.source_explanation)
        + sum(1 for value in unit.target_explanations.values() if value)
        for unit in glossary_terms
    )
    terms = tuple(
        GlossaryTermPreview(
            section=unit.section,
            source=unit.values.get(component.source_lang, ""),
            targets={
                code: unit.values.get(code, "")
                for code in component.initial_target_languages
            },
            source_explanation=unit.source_explanation,
            target_explanations=dict(unit.target_explanations),
            source_flags=unit.source_flags,
        )
        for unit in glossary_terms[:PREVIEW_TERM_LIMIT]
    )

    return GlossaryPreview(
        sheet=component.sheet,
        component=component.component,
        source_language=component.source_lang,
        target_languages=tuple(component.initial_target_languages),
        term_count=len(result.units),
        note_count=note_count,
        warnings=tuple(
            f"{d.code}: {d.message}"
            for d in diagnostics
            if d.severity is not Severity.ERROR
        ),
        terms=terms,
        all_terms=tuple(glossary_terms),
        profile_json=_canonical_profile_json(document),
        files=files,
    )


# --------------------------------------------------------------------------- #
# DB-only Explanation application for ordinary string components
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KitExplanationApplyResult:
    """Outcomes for one idempotent source-explanation application."""

    set_count: int = 0
    unchanged_count: int = 0
    blank_count: int = 0
    missing_key_count: int = 0
    would_overwrite_count: int = 0
    already_in_note_count: int = 0
    # An Explanation that changed after the preview the operator confirmed:
    # an overwrite would silently destroy that newer edit.
    baseline_changed_count: int = 0
    # Never set by ``apply_kit_explanations`` itself: a caller that gates the
    # whole operation on a permission check (rather than failing hard) fills
    # this in for the rows it chose not to even attempt.
    unavailable_count: int = 0
    # A row whose key exists but whose incoming source-language value
    # disagrees with the stored source. Never set by an apply that skips
    # the gate itself (e.g. a hard-denied caller building its own result).
    source_changed_count: int = 0


def _check_explanation_apply_eligibility(component: Component) -> None:
    """Reject formats where this operation could write context to a file."""
    if component.is_glossary:
        raise ValidationError(
            _("Glossary components cannot import string explanations.")
        )
    if component.locked:
        raise ValidationError(_("Locked components cannot import string explanations."))
    if component.file_format_cls.supports_explanation:
        raise ValidationError(
            _("This component format stores explanations in translation files.")
        )


def _classify_kit_explanations(
    *,
    source_units: Mapping[str, Unit],
    units: Sequence[StringUnit],
    overwrite: bool,
    source_lang: str,
    baseline: Mapping[str, str] | None = None,
) -> tuple[list[tuple[Unit, str]], KitExplanationApplyResult]:
    """
    Classify each incoming explanation cell without mutating anything.

    Returns the source units to actually write (paired with their new
    explanation) plus the full outcome breakdown; a preview caller uses only
    the counters, ``apply_kit_explanations`` writes the first element.

    A row whose key already exists but whose source-language cell disagrees
    with the stored source is never eligible: the context may now name a
    different term, so its Explanation is not auto-applied. Only a row that
    actually supplies the source-language column is compared - ``source_lang
    in incoming.values`` - so a caller with no source evidence at all (the
    single-shot explanation apply run right after a kit-derived component's
    translations first load, which only ever tracks key -> explanation and
    never populates ``values``) is unaffected; an explicit blank cell for an
    existing key still counts as a real disagreement. This is decided here,
    against ``source_unit.source`` from the caller's own ``source_units``
    lookup, so an apply that locks and re-fetches immediately before writing
    (``apply_kit_explanations``) re-evaluates it under that fresh state - a
    stale preview snapshot can never keep a row eligible past a source that
    changed after the preview ran.
    """
    counters = {
        "set_count": 0,
        "unchanged_count": 0,
        "blank_count": 0,
        "missing_key_count": 0,
        "would_overwrite_count": 0,
        "already_in_note_count": 0,
        "source_changed_count": 0,
        "baseline_changed_count": 0,
    }
    to_apply: list[tuple[Unit, str]] = []
    for incoming in units:
        explanation = incoming.explanation.strip()
        if not explanation:
            counters["blank_count"] += 1
            continue
        source_unit = source_units.get(incoming.key)
        if source_unit is None:
            counters["missing_key_count"] += 1
            continue
        if source_lang in incoming.values:
            new_source = incoming.values[source_lang]
            if new_source != source_unit.source:
                counters["source_changed_count"] += 1
                continue
        if source_unit.explanation == explanation:
            counters["unchanged_count"] += 1
            continue
        if source_unit.explanation and not overwrite:
            counters["would_overwrite_count"] += 1
            continue
        if (
            overwrite
            and baseline is not None
            and source_unit.explanation != baseline.get(incoming.key, "")
        ):
            # The stored value is no longer what the operator saw and
            # confirmed overwriting, so the newer edit wins.
            counters["baseline_changed_count"] += 1
            continue
        if source_unit.note.strip() == explanation:
            counters["already_in_note_count"] += 1
        to_apply.append((source_unit, explanation))
        counters["set_count"] += 1
    return to_apply, KitExplanationApplyResult(**counters)


def count_judge_stale_after_explanations(
    *, component: Component, units: Sequence[StringUnit], overwrite: bool
) -> int:
    """
    Count units whose current-context judge verdict this apply would stale.

    A verdict is current when its stored ``context_hash`` still equals the
    hash of the unit's live context (``compute_context_hash`` over source,
    note, explanation and glossary prompt entries) - the same notion the
    judge loop uses to detect drift. Changing a source unit's explanation
    changes that hash, so every target unit whose verdict was judged against
    the old explanation needs a re-run (and a re-bill) afterwards. This
    counts only units the apply would actually touch, so an unchanged or
    skipped explanation row never reports a stale verdict.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.glossary.models import get_matched_glossary_prompt_entries

    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models.judge import compute_context_hash, compute_target_hash

    _check_explanation_apply_eligibility(component)
    keys = {unit.key for unit in units if unit.explanation.strip()}
    if not keys:
        return 0
    source_units = {
        unit.context: unit
        for unit in component.source_translation.unit_set.filter(context__in=keys)
    }
    to_apply, _result = _classify_kit_explanations(
        source_units=source_units,
        units=units,
        overwrite=overwrite,
        source_lang=component.source_language.code,
    )
    if not to_apply:
        return 0
    stale = 0
    for source_unit, _explanation in to_apply:
        for target_unit in source_unit.unit_set.exclude(pk=source_unit.pk):
            current_hash = compute_context_hash(
                source=target_unit.source,
                note=target_unit.source_unit.note,
                explanation=target_unit.source_unit.explanation,
                glossary_terms=get_matched_glossary_prompt_entries(target_unit),
            )
            if target_unit.judge_verdicts.filter(
                unparsed=False,
                target_hash=compute_target_hash(target_unit.get_target_plurals()),
                context_hash=current_hash,
            ).exists():
                stale += 1
    return stale


def classify_kit_explanations(
    *,
    component: Component,
    units: Sequence[StringUnit],
    overwrite: bool,
) -> KitExplanationApplyResult:
    """
    Read-only preview of what ``apply_kit_explanations`` would do.

    Never mutates; does not require or check ``source.edit`` itself, since
    the caller decides how to represent an unavailable permission (a hard
    eligibility problem still raises, exactly like the apply path).
    """
    _check_explanation_apply_eligibility(component)
    keys = {unit.key for unit in units if unit.explanation.strip()}
    source_units = {
        unit.context: unit
        for unit in component.source_translation.unit_set.filter(context__in=keys)
    }
    _to_apply, counters = _classify_kit_explanations(
        source_units=source_units,
        units=units,
        overwrite=overwrite,
        source_lang=component.source_language.code,
    )
    return counters


def _apply_kit_explanations_locked(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    overwrite: bool,
    baseline: Mapping[str, str] | None = None,
) -> KitExplanationApplyResult:
    """
    Lock-free core of :func:`apply_kit_explanations`.

    The caller owns the ``repository -> component -> draft`` lock order and
    the enclosing transaction; this primitive never takes a lock itself so
    the atomic portion coordinator can run it under the same component lock
    as the string append.
    """
    _check_explanation_apply_eligibility(component)
    if not user.has_perm("source.edit", component.source_translation):
        raise PermissionDenied
    keys = {unit.key for unit in units if unit.explanation.strip()}
    source_units = {
        unit.context: unit
        for unit in component.source_translation.unit_set.select_for_update()
        .filter(context__in=keys)
        .order_by("pk")
    }
    to_apply, result = _classify_kit_explanations(
        source_units=source_units,
        units=units,
        overwrite=overwrite,
        source_lang=component.source_language.code,
        baseline=baseline,
    )
    for source_unit, explanation in to_apply:
        source_unit.update_explanation(explanation, user)
    return result


def apply_kit_explanations(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    overwrite: bool,
    baseline: Mapping[str, str] | None = None,
) -> KitExplanationApplyResult:
    """
    Apply parsed kit explanation cells to matching source units by context.

    This service deliberately writes only through ``Unit.update_explanation``;
    source text, target text, state, flags, labels and component files remain
    untouched. The caller owns parsing and can reuse these counters for a
    mutation-free preview.
    """
    _check_explanation_apply_eligibility(component)
    if not user.has_perm("source.edit", component.source_translation):
        raise PermissionDenied
    with transaction.atomic(), component.locked_for_update() as locked_component:
        return _apply_kit_explanations_locked(
            user=user,
            component=locked_component,
            units=units,
            overwrite=overwrite,
            baseline=baseline,
        )


def load_prepared_string_units(
    draft: LocKitImportDraft,
) -> tuple[tuple[StringUnit, ...], dict[str, str]]:
    """
    Read and checksum-verify a draft's private canonical packet.

    Only a background task holding the draft's current fencing task id may
    call this: the packet's full row content is never exposed to an HTTP
    status/preview request.
    """
    if not draft.prepared_payload:
        msg = _("The prepared loc-kit data is unavailable.")
        raise ValidationError(msg)
    with draft.prepared_payload.open("rb") as payload:
        encoded = payload.read()
    if hashlib.sha256(encoded).hexdigest() != draft.payload_checksum:
        msg = _("The prepared loc-kit data is corrupted.")
        raise ValidationError(msg)
    try:
        packet = json.loads(encoded)
        rows = packet["rows"]
        baseline = packet.get("baseline") or {}
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        msg = _("The prepared loc-kit data is invalid.")
        raise ValidationError(msg) from error
    units = string_units_from_json(json.dumps(rows, ensure_ascii=False))
    return units, {str(key): str(value) for key, value in baseline.items()}


def string_unit_to_json(unit: StringUnit) -> dict[str, object]:
    """Return a versioned-payload-safe representation of one loc-kit row."""
    return {
        "key": unit.key,
        "values": dict(unit.values),
        "comments": list(unit.comments),
        "references": list(unit.references),
        "row": unit.row,
        "explanation": unit.explanation,
        "flags": unit.flags,
    }


def string_units_from_json(payload: str) -> tuple[StringUnit, ...]:
    """Restore loc-kit rows from private canonical JSON."""
    # ruff: ignore[import-outside-top-level]
    from loc_kit_ingest.model import StringUnit as _StringUnit

    return tuple(
        _StringUnit(
            key=row["key"],
            values=dict(row["values"]),
            comments=tuple(row["comments"]),
            references=tuple(row["references"]),
            row=row["row"],
            explanation=row.get("explanation", ""),
            flags=row.get("flags", ""),
        )
        for row in json.loads(payload)
    )


# --------------------------------------------------------------------------- #
# Update an existing string component from a loc-kit table
# --------------------------------------------------------------------------- #


def validate_loc_kit_string_update_size(units: Sequence[StringUnit]) -> None:
    """
    Reject a table too large for the synchronous strings-update transaction.

    Count all non-empty translation, flag, and Explanation cells rather than
    only rows: one key can write one source and many target units. This stays
    conservative when another confirm changes key existence between preview
    and apply, and the same guard can protect both the HTTP entry point and
    direct service callers.
    """
    mutation_count = sum(
        bool(value.strip())
        for unit in units
        for value in (*unit.values.values(), unit.flags, unit.explanation)
    )
    if mutation_count > LOC_KIT_STRING_UPDATE_MAX_MUTATIONS:
        raise ValidationError(
            _(
                "This table has %(count)d non-empty cells, exceeding the "
                "synchronous update limit of %(limit)d. Split it into "
                "smaller tables."
            )
            % {
                "count": mutation_count,
                "limit": LOC_KIT_STRING_UPDATE_MAX_MUTATIONS,
            }
        )


def _check_string_update_eligibility(component: Component) -> None:
    """Reject components this flow cannot touch, regardless of permission."""
    if component.is_glossary:
        raise ValidationError(
            _("Glossary components use the glossary update flow instead.")
        )
    if component.locked:
        raise ValidationError(_("Locked components cannot be updated from a table."))
    if not component.has_template():
        raise ValidationError(
            _("Only monolingual components can be updated from a loc-kit table.")
        )


def existing_string_keys(component: Component) -> set[str]:
    """Return every source ``Unit.context`` the component already has."""
    return set(component.source_translation.unit_set.values_list("context", flat=True))


@dataclass(frozen=True)
class ChangedSourceRow:
    """One existing key whose table row disagrees with the stored source."""

    key: str
    old_source: str
    new_source: str


def find_changed_sources(
    *, component: Component, units: Sequence[StringUnit]
) -> tuple[ChangedSourceRow, ...]:
    """
    Report existing keys whose source-language cell disagrees with Weblate.

    Read-only: the table never rewrites an existing key's source (see
    ``append_translation_strings``), so this only surfaces the discrepancy -
    key, the stored value, and the table's value - as its own preview
    section, distinct from the new/existing counts. Compares every existing
    key the table also names, including a row whose source cell is blank:
    a table that clears a previously non-empty source disagrees with
    Weblate exactly as much as one that supplies a different value, and a
    blank cell is never distinguished from an absent column (the real
    parser always populates every configured language column per row, so
    an existing key's cell is either present-and-blank or present-and-set,
    never simply missing from ``values``).
    """
    source_lang = component.source_language.code
    existing_sources = dict(
        component.source_translation.unit_set.values_list("context", "source")
    )
    changed: list[ChangedSourceRow] = []
    for unit in units:
        old_source = existing_sources.get(unit.key)
        if old_source is None or source_lang not in unit.values:
            continue
        new_source = unit.values[source_lang]
        if new_source != old_source:
            changed.append(
                ChangedSourceRow(
                    key=unit.key, old_source=old_source, new_source=new_source
                )
            )
    return tuple(changed)


@dataclass(frozen=True)
class StringsAppendResult:
    """Outcomes of adding brand-new loc-kit rows to a string component."""

    added: int = 0
    existing: int = 0
    language_added: Mapping[str, int] = field(default_factory=dict)
    created_languages: tuple[str, ...] = ()
    unavailable_languages: tuple[str, ...] = ()
    pending_change_ids: tuple[int, ...] = ()


def _resolve_append_language(
    *, user: User, component: Component, code: str
) -> Translation | None:
    """Create a missing target language for the append, or return None."""
    if not user.has_perm("translation.add", component.project):
        return None
    languages = component.get_all_available_languages()
    if not user.has_perm("translation.add_more", component):
        languages = languages.filter_for_add(component.project)
    language = languages.filter(code=code).first()
    if language is None:
        return None
    return component.add_new_language(language, None)


def _tag_cascade_pending_changes(
    cascade_pending_qs,
    *,
    pending_owner: str,
    new_row_flags: Mapping[str, str],
    source_translation_id: int,
) -> list[int]:
    """
    Tag draft ownership on every cascade pending change of a fresh key.

    Returns every tagged primary key. For the source unit, also snapshot an
    ``extra_flags`` value when one is present, so the runtime pending writer
    can later carry it into the backing file.
    """
    if not pending_owner:
        return list(cascade_pending_qs.values_list("pk", flat=True))
    pending_ids: list[int] = []
    for pending_change in cascade_pending_qs:
        pending_change.metadata["loc_kit_draft_id"] = pending_owner
        if (
            new_row_flags
            and pending_change.unit.translation_id == source_translation_id
            and pending_change.unit.context in new_row_flags
        ):
            pending_change.metadata["extra_flags"] = new_row_flags[
                pending_change.unit.context
            ]
        pending_change.save(update_fields=["metadata"])
        pending_ids.append(pending_change.pk)
    return pending_ids


def _append_translation_strings_locked(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    pending_owner: str = "",
) -> StringsAppendResult:
    """
    Lock-free core of :func:`append_translation_strings`.

    The caller owns the ``repository -> component -> draft`` lock order and
    the enclosing transaction; this primitive never takes a lock itself, so
    the atomic portion coordinator can run it under the same component lock
    as the explanation application. The full documented contract lives on
    :func:`append_translation_strings`.

    When ``pending_owner`` is set and the component's format can persist
    flags, the input ``extra_flags`` of each brand-new row are snapshotted
    into ``metadata["extra_flags"]`` of exactly the source pending change
    that adds that key, so the runtime pending writer can carry them into
    the backing file before serialization. The snapshot comes from the
    immutable packet row, never from a possibly later user edit, and an
    existing key's flags are never touched.
    """
    _check_string_update_eligibility(component)
    existing_keys = existing_string_keys(component)
    new_units = [unit for unit in units if unit.key not in existing_keys]
    existing_count = len(units) - len(new_units)
    if not new_units:
        return StringsAppendResult(existing=existing_count)

    snapshot_flags = component.file_format_cls.supports_flags
    new_row_flags = (
        {unit.key: unit.flags for unit in new_units if unit.flags.strip()}
        if snapshot_flags
        else {}
    )

    source_lang = component.source_language.code
    requested_codes = {
        code
        for unit in new_units
        for code, value in unit.values.items()
        if value.strip()
    } - {source_lang}
    translations_by_code = {
        translation.language.code: translation
        for translation in component.translation_set.select_related("language")
        if translation.language_id != component.source_language_id
    }

    created_languages: list[str] = []
    unavailable_languages: list[str] = []
    for code in sorted(requested_codes - set(translations_by_code)):
        translation = _resolve_append_language(
            user=user, component=component, code=code
        )
        if translation is None:
            unavailable_languages.append(code)
        else:
            translations_by_code[code] = translation
            created_languages.append(code)

    source_translation = component.source_translation
    language_added: dict[str, int] = {}
    row_keys: list[str] = []
    touched_translations: dict[int, Translation] = {}
    target_pending: list[PendingUnitChange] = []
    for unit in new_units:
        note = "; ".join(comment for comment in unit.comments if comment)
        location = ",".join(ref for ref in unit.references if ref)
        source_unit = source_translation.add_unit(
            None,
            unit.key,
            unit.values.get(source_lang, ""),
            [],
            is_batch_update=True,
            note=note,
            location=location,
            author=user,
        )
        if source_unit is None:
            continue
        row_keys.append(unit.key)
        for code, value in unit.values.items():
            if code == source_lang or not value.strip():
                continue
            translation = translations_by_code.get(code)
            if translation is None:
                continue
            target_unit = translation.unit_set.filter(context=unit.key).first()
            if target_unit is None:
                continue
            # For a monolingual format, ``pending = is_source`` in
            # ``_add_unit_locked``: the component-wide ``add_unit`` cascade
            # above creates this target unit's DB row but no pending
            # change at all (only the source unit gets one). This
            # ``translate`` call is therefore the first-ever pending
            # change for a unit that has never been written to its own
            # target file, so ``update_units`` must route it through
            # ``find_or_add_pending_store_unit`` - flip the ``add_unit``
            # flag ``Unit.translate`` does not know to set, after it has
            # done its normal state/check/change-history bookkeeping.
            target_unit.is_batch_update = True
            target_unit.translate(
                user, value, STATE_TRANSLATED, author=user, propagate=False
            )
            if target_unit.pending_unit_change is not None:
                target_unit.pending_unit_change.add_unit = True
                target_pending.append(target_unit.pending_unit_change)
            touched_translations[translation.pk] = translation
            language_added[code] = language_added.get(code, 0) + 1
        # Flags land on the source unit only after every target is
        # written: a read-only flag would otherwise block the target
        # writes above.
        if unit.flags.strip():
            flags = Flags(unit.flags)
            source_unit.update_extra_flags(flags.format(), user)

    if pending_owner:
        for pending_change in target_pending:
            pending_change.metadata["loc_kit_draft_id"] = pending_owner
    source_translation.store_update_changes()
    for translation in touched_translations.values():
        translation.store_update_changes()

    pending_ids = [
        pending_change.pk
        for pending_change in target_pending
        if pending_change.pk is not None
    ]
    if row_keys:
        # The source unit's own ``add_unit=True`` pending change, plus
        # any language the row's values did not populate: every such
        # unit ``add_unit`` created belongs to this row's atomic
        # finalizing commit, not only the ones this call also
        # translated above.
        cascade_pending_qs = (
            PendingUnitChange.objects.filter(
                unit__translation__component=component,
                unit__context__in=row_keys,
                add_unit=True,
            )
            .exclude(pk__in=[pc.pk for pc in target_pending if pc.pk is not None])
            .select_related("unit")
        )
        pending_ids.extend(
            _tag_cascade_pending_changes(
                cascade_pending_qs,
                pending_owner=pending_owner,
                new_row_flags=new_row_flags,
                source_translation_id=source_translation.pk,
            )
        )
    return StringsAppendResult(
        added=len(new_units),
        existing=existing_count,
        language_added=language_added,
        created_languages=tuple(created_languages),
        unavailable_languages=tuple(unavailable_languages),
        pending_change_ids=tuple(pending_ids),
    )


def append_translation_strings(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    pending_owner: str = "",
) -> StringsAppendResult:
    """
    Add loc-kit rows whose key does not exist yet, to every reachable language.

    An existing key is never touched here: source, targets, and flags of a
    key already present in the component stay exactly as they are, no matter
    what the table carries for it. A target language absent from the
    component is created only when at least one new row needs it and the
    caller holds ``translation.add``; otherwise it is reported unavailable
    and every other language still receives its rows.

    Every write goes through ``Unit.add_unit``/``Unit.translate`` with
    ``is_batch_update=True`` and a single ``store_update_changes()`` flush
    per touched translation, exactly like every other Weblate batch writer:
    a raw ``Unit.save()`` on a target creates no ``PendingUnitChange`` and
    the translation would silently never reach the backing file. When
    ``pending_owner`` is set (the calling draft's stable token), every
    ``PendingUnitChange`` this call creates is tagged with it in
    ``metadata["loc_kit_draft_id"]`` and its primary key is returned in
    ``pending_change_ids``, so a caller can later commit exactly this set
    with ``Component.commit_pending_subset`` instead of every pending
    change on the component.

    Runs under ``component.locked_for_update()``, exactly like
    ``apply_kit_explanations`` and ``append_glossary_terms``: two concurrent
    calls for the same component - most notably a double-submitted confirm
    of the same draft - serialize on the component lock, and the second one
    re-derives ``existing_keys`` from the freshly locked component, so it
    sees the first call's additions as already-existing keys instead of
    adding them again.
    """
    _check_string_update_eligibility(component)
    with transaction.atomic(), component.locked_for_update() as locked_component:
        return _append_translation_strings_locked(
            user=user,
            component=locked_component,
            units=units,
            pending_owner=pending_owner,
        )


@dataclass(frozen=True)
class StringsUpdateResult:
    """Combined outcome of one loc-kit strings-update confirm."""

    strings: StringsAppendResult
    explanations: KitExplanationApplyResult

    @property
    def pending_change_ids(self) -> tuple[int, ...]:
        return self.strings.pending_change_ids


def _apply_loc_kit_string_update_locked(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    overwrite_explanations: bool,
    pending_owner: str = "",
    explanation_baseline: Mapping[str, str] | None = None,
) -> StringsUpdateResult:
    """
    Lock-free core of :func:`apply_loc_kit_string_update`.

    The caller owns the ``repository -> component -> draft`` lock order and
    the enclosing transaction. Permission-axis independence is decided up
    front inside this single transaction: a user with only upload/add
    rights still gets new strings added with Explanations unavailable, and
    a user with only ``source.edit`` still gets Explanations set with new
    strings unavailable. An operational exception on either axis rolls the
    whole portion back - no new unit, owned pending row or cursor advance
    survives without its paired outcome.
    """
    _check_string_update_eligibility(component)
    can_add_strings = user.has_perm("upload.perform", component) and user.has_perm(
        "unit.add", component.source_translation
    )
    if can_add_strings:
        strings_result = _append_translation_strings_locked(
            user=user, component=component, units=units, pending_owner=pending_owner
        )
    else:
        existing_keys = existing_string_keys(component)
        strings_result = StringsAppendResult(
            existing=sum(1 for unit in units if unit.key in existing_keys)
        )

    if user.has_perm("source.edit", component.source_translation):
        explanation_result = _apply_kit_explanations_locked(
            user=user,
            component=component,
            units=units,
            overwrite=overwrite_explanations,
            baseline=explanation_baseline,
        )
    else:
        explanation_result = KitExplanationApplyResult(
            unavailable_count=sum(1 for unit in units if unit.explanation.strip())
        )
    return StringsUpdateResult(strings=strings_result, explanations=explanation_result)


def apply_loc_kit_string_update(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    overwrite_explanations: bool,
    pending_owner: str = "",
    explanation_baseline: Mapping[str, str] | None = None,
) -> StringsUpdateResult:
    """
    Add new loc-kit rows and set Explanations on one existing component.

    The two mutations are independent successes, exactly as their preview
    reports them: a user with only upload/add rights still gets new strings
    added even though Explanation stays unavailable, and a user with only
    ``source.edit`` still gets Explanations set even though new strings stay
    unavailable. ``upload.perform`` alone never authorizes an Explanation
    change. Both axes run inside ONE transaction under the component lock,
    so an operational exception on either axis rolls back the whole call -
    the previous split into two transactions could commit an append whose
    Explanation later failed, forcing a lossy replay.
    """
    _check_string_update_eligibility(component)
    validate_loc_kit_string_update_size(units)
    with transaction.atomic(), component.locked_for_update() as locked_component:
        return _apply_loc_kit_string_update_locked(
            user=user,
            component=locked_component,
            units=units,
            overwrite_explanations=overwrite_explanations,
            pending_owner=pending_owner,
            explanation_baseline=explanation_baseline,
        )


def apply_loc_kit_portion(
    *,
    user: User,
    component: Component,
    units: Sequence[StringUnit],
    overwrite_explanations: bool,
    pending_owner: str,
    explanation_baseline: Mapping[str, str] | None,
    draft_id: int,
    task_id,
    expected_cursor: int,
    total_rows: int,
) -> StringsUpdateResult | None:
    """
    Apply one atomic row portion under ``repository -> component -> draft`` locks.

    This is the durable write boundary a single Celery delivery may cross
    exactly once: the component repository lock, the component row and the
    draft row are locked in that order inside one transaction, the fencing
    token (``state == APPLYING``, ``apply_task_id == task_id``) and the
    cursor are re-checked before ANY unit mutation, and the adds,
    Explanations, owned pending tagging, cursor, counters and heartbeat are
    all committed together. A stale or replaced task observes a mismatched
    token or cursor and returns ``None`` without an externally visible
    mutation; two duplicate deliveries of the same UUID therefore commit at
    most one portion. An operational exception on any axis rolls the whole
    portion back and propagates, leaving the draft retryable.

    The immutable packet data (``units``, ``explanation_baseline``) is
    loaded by the caller once per delivery, never re-read from the draft
    inside the lock.
    """
    # ruff: ignore[import-outside-top-level]
    from weblate.trans.models import LocKitImportDraft

    _check_string_update_eligibility(component)
    validate_loc_kit_string_update_size(units)
    with component.locked_for_update() as locked_component:
        _check_string_update_eligibility(locked_component)
        draft = LocKitImportDraft.objects.select_for_update(of=("self",)).get(
            pk=draft_id
        )
        if (
            draft.kind != LocKitImportDraft.Kind.STRING
            or draft.state != LocKitImportDraft.State.APPLYING
            or draft.apply_task_id != task_id
            or draft.next_row != expected_cursor
        ):
            # Fence check inside the lock: a replaced UUID or an advanced
            # cursor means another generation owns this portion. Return
            # without touching anything.
            return None

        result = _apply_loc_kit_string_update_locked(
            user=user,
            component=locked_component,
            units=units,
            overwrite_explanations=overwrite_explanations,
            pending_owner=pending_owner,
            explanation_baseline=explanation_baseline,
        )

        saved_progress = draft.progress if isinstance(draft.progress, dict) else {}
        totals = {
            key: int(saved_progress.get(key, 0))
            for key in (
                "added",
                "existing",
                "explanations_set",
                "explanations_unchanged",
                "explanations_would_overwrite",
                "explanations_unavailable",
                "explanations_source_changed",
                "explanations_baseline_changed",
            )
        }
        totals["added"] += result.strings.added
        totals["existing"] += result.strings.existing
        totals["explanations_set"] += result.explanations.set_count
        totals["explanations_unchanged"] += result.explanations.unchanged_count
        totals["explanations_would_overwrite"] += (
            result.explanations.would_overwrite_count
        )
        totals["explanations_unavailable"] += result.explanations.unavailable_count
        totals["explanations_source_changed"] += (
            result.explanations.source_changed_count
        )
        totals["explanations_baseline_changed"] += (
            result.explanations.baseline_changed_count
        )
        created_languages: set[str] = set(saved_progress.get("created_languages", ()))
        created_languages.update(result.strings.created_languages)
        unavailable_languages: set[str] = set(
            saved_progress.get("unavailable_languages", ())
        )
        unavailable_languages.update(result.strings.unavailable_languages)

        draft.next_row = expected_cursor + len(units)
        draft.pending_change_ids = sorted(
            set(draft.pending_change_ids) | set(result.pending_change_ids)
        )
        draft.progress = {
            "phase": "applying",
            "processed_rows": draft.next_row,
            "total_rows": total_rows,
            **totals,
            "created_languages": sorted(created_languages),
            "unavailable_languages": sorted(unavailable_languages),
        }
        draft.last_activity_at = timezone.now()
        draft.expires_at = draft.last_activity_at + timedelta(hours=1)
        draft.save(
            update_fields=[
                "next_row",
                "pending_change_ids",
                "progress",
                "last_activity_at",
                "expires_at",
            ]
        )
        return result


# --------------------------------------------------------------------------- #
# Append-only application of a validated preview to an existing glossary
# --------------------------------------------------------------------------- #

# How many conflicts a collision error carries into the UI. The operator
# resolves them one by one; the whole table never lands in an exception.
COLLISION_REPORT_LIMIT = 10


@dataclass(frozen=True)
class GlossaryLanguageAppendResult:
    """Independent per-language counters for one append run."""

    added: int = 0
    existing: int = 0
    blank: int = 0
    absent: bool = False
    unavailable: str = ""
    unavailable_count: int = 0


@dataclass(frozen=True)
class GlossaryAppendResult:
    languages: Mapping[str, GlossaryLanguageAppendResult]
    added_terms: int


class GlossaryAppendCollisionError(Exception):
    """A source is already known under another glossary context."""

    def __init__(
        self, message: str, *, conflicts: Sequence[tuple[str, str, str]]
    ) -> None:
        super().__init__(message)
        # (source, existing context, incoming context), capped for the UI.
        self.conflicts = tuple(conflicts[:COLLISION_REPORT_LIMIT])


def _term_source(term: GlossaryTerm, source_language: str) -> str:
    return term.values.get(source_language, "")


def _classify_incoming_terms(
    preview: GlossaryPreview,
    existing_keys: set[tuple[str, str]],
):
    """
    Split the validated terms against the glossary's current identity set.

    Identity is ``(context, source)``. A matching identity is the old term:
    its targets, notes and flags must stay untouched. The same source under
    a different context is a conflict, never a silent second entry.
    """
    existing_contexts: dict[str, str] = {}
    for context, source in existing_keys:
        existing_contexts.setdefault(source, context)
    existing_sources = set(existing_contexts)

    new_terms = []
    incoming_sources: set[str] = set()
    collisions = []
    for term in preview.all_terms:
        source = _term_source(term, preview.source_language)
        key = (term.context, source)
        if key in existing_keys:
            continue
        if source in existing_sources or source in incoming_sources:
            collisions.append((source, existing_contexts.get(source, ""), term.context))
            continue
        incoming_sources.add(source)
        new_terms.append(term)
    return new_terms, collisions


def _resolve_missing_language(
    request: AuthenticatedHttpRequest, component: Component, code: str
):
    """
    Create an absent target language, or explain why it stays unavailable.

    The allowed language is picked exactly like the standard Weblate form:
    ``translation.add_more`` lifts the project's addable-language filter.
    """
    user = request.user
    if not user.has_perm("translation.add", component.project):
        return _(
            "Adding a language requires the “Add language for translation” permission."
        )
    if not user.has_perm("glossary.add", component.project):
        return _(
            "Adding glossary entries requires the “Add glossary entry” permission."
        )
    languages = component.get_all_available_languages()
    if not user.has_perm("translation.add_more", component):
        languages = languages.filter_for_add(component.project)
    language = languages.filter(code=code).first()
    if language is None:
        return _("The language cannot be added in this project.")
    if not component.can_add_new_language(user):
        return str(component.new_lang_error_message)
    return component.add_new_language(language, request) or str(
        component.new_lang_error_message
    )


def _raise_collision(collisions: Sequence[tuple[str, str, str]]) -> NoReturn:
    """Abort an apply because a source collides with an existing context."""
    raise GlossaryAppendCollisionError(
        _(
            "Some source terms already exist under a different section. "
            "Resolve the conflict before appending."
        ),
        conflicts=collisions,
    )


def _raise_missing_target_unit(source: str) -> NoReturn:
    """Abort content application because add_unit unexpectedly returned None."""
    msg = f"Could not add glossary term {source!r}"
    raise ValueError(msg)


# Two-phase locked apply (resolve languages, then write content) with
# per-language partial success and content-failure compensation is
# irreducibly this shaped; splitting it for these metrics would move state
# across function boundaries without making the transaction easier to read.
# ruff: ignore[complex-structure, too-many-statements, too-many-locals]
def append_glossary_terms(
    request: AuthenticatedHttpRequest, component: Component, preview: GlossaryPreview
) -> GlossaryAppendResult:
    """
    Append brand-new terms from a validated preview to an existing glossary.

    Existing terms are never changed: their targets, explanations and flags
    stay untouched even when the table carries other values for them. Blank
    cells, absent language columns and unavailable languages are partial
    skips reported per language, not failures.
    """
    # ruff: ignore[import-outside-top-level]
    from django.db import transaction

    # ruff: ignore[import-outside-top-level]
    from weblate.checks.flags import Flags

    # ruff: ignore[import-outside-top-level]
    from weblate.utils.errors import report_error

    user = request.user
    target_codes = list(preview.target_languages)

    # Preflight under the standard lock order: identity set, collisions and
    # language resolution all run before any unit is written.
    with component.locked_for_update() as locked_component:
        existing_keys = set(
            locked_component.source_translation.unit_set.values_list(
                "context", "source"
            )
        )
        new_terms, collisions = _classify_incoming_terms(preview, existing_keys)
        if collisions:
            _raise_collision(collisions)

        translations_by_code = {
            translation.language.code: translation
            for translation in locked_component.translation_set.select_related(
                "language"
            )
            if translation.language_id != locked_component.source_language_id
        }

        resolved_codes: set[str] = set()
        unavailable_reasons: dict[str, str] = {}
        created_by_this_apply: list[Translation] = []

        # New terms decide which languages matter at all: a column whose
        # cells are blank for every new term never creates a translation.
        new_term_data: dict[str, int] = {
            code: sum(1 for term in new_terms if term.values.get(code, "").strip())
            for code in target_codes
        }

        for code in target_codes:
            translation = translations_by_code.get(code)
            if translation is not None:
                if user.has_perm("unit.add", translation):
                    resolved_codes.add(code)
                else:
                    unavailable_reasons[code] = _(
                        "You do not have permission to add strings to this language."
                    )
            elif new_term_data[code]:
                try:
                    outcome = _resolve_missing_language(request, locked_component, code)
                except WeblateLockTimeoutError:
                    # A lock timeout is retryable for the whole operation;
                    # it must reach the view without consuming the draft.
                    raise
                except Exception as error:
                    # A VCS error on one language must not abort the whole
                    # append; it stays unavailable and is reported while the
                    # other languages continue.
                    report_error(
                        "Glossary append could not create a language",
                        level="error",
                        project=component.project,
                        exception=error,
                    )
                    unavailable_reasons[code] = _(
                        "The language file could not be created."
                    )
                    continue
                if isinstance(outcome, str):
                    unavailable_reasons[code] = outcome
                # The file and its VCS commit exist now; re-check the
                # write permission defensively before any content.
                elif user.has_perm("unit.add", outcome):
                    resolved_codes.add(code)
                    created_by_this_apply.append(outcome)
                else:
                    unavailable_reasons[code] = _(
                        "You do not have permission to add strings to this language."
                    )
                    try:
                        outcome.remove(user)
                    except Exception:
                        report_error(
                            "Could not remove a glossary language created "
                            "for an append that lost its permission",
                            level="error",
                            project=component.project,
                        )

    counters = {
        code: {"added": 0, "existing": 0, "blank": 0, "unavailable": 0}
        for code in target_codes
    }
    added_source_terms = 0
    content_failed = False

    # ruff: ignore[too-many-statements-in-try-clause]
    try:
        # Second lock round: a concurrent change between the phases must not
        # produce duplicates, so the identity set is rebuilt from the fresh
        # component and every addition runs in one database transaction.
        with component.locked_for_update() as fresh_component:
            existing_keys = set(
                fresh_component.source_translation.unit_set.values_list(
                    "context", "source"
                )
            )
            new_terms, collisions = _classify_incoming_terms(preview, existing_keys)
            if collisions:
                _raise_collision(collisions)
            # Fresh translation objects: the preflight instances belong to
            # the previous lock round and must never write content.
            fresh_translations = {
                translation.language.code: translation
                for translation in fresh_component.translation_set.select_related(
                    "language"
                )
                if translation.language_id != fresh_component.source_language_id
            }
            resolved = {
                code: fresh_translations[code]
                for code in resolved_codes
                if code in fresh_translations
            }
            # ruff: ignore[too-many-statements-in-try-clause]
            try:
                with transaction.atomic():
                    for term in new_terms:
                        source = _term_source(term, preview.source_language)
                        source_unit = None
                        first_addition = True
                        for code in target_codes:
                            value = term.values.get(code, "").strip()
                            if not value:
                                counters[code]["blank"] += 1
                                continue
                            translation = resolved.get(code)
                            if translation is None:
                                counters[code]["unavailable"] += 1
                                continue
                            target_unit = translation.add_unit(
                                request,
                                term.context,
                                source,
                                value,
                                explanation=term.target_explanations.get(code, ""),
                                # The second language of one term must reuse
                                # the source unit the first one created.
                                # Without skip_existing, add_unit's merge
                                # path would rewrite the source explanation
                                # with this language's target explanation.
                                skip_existing=not first_addition,
                            )
                            if target_unit is None:
                                _raise_missing_target_unit(source)
                            if "exact" in term.source_flags:
                                flags = Flags(target_unit.extra_flags)
                                flags.merge("exact")
                                target_unit.update_extra_flags(flags.format(), user)
                            counters[code]["added"] += 1
                            first_addition = False
                            if source_unit is None:
                                source_unit = target_unit.source_unit
                        if source_unit is not None:
                            source_unit.update_explanation(
                                term.source_explanation, user
                            )
                            flags = Flags(source_unit.extra_flags)
                            flags.merge("terminology")
                            flags.merge(
                                flag for flag in term.source_flags if flag != "exact"
                            )
                            source_unit.update_extra_flags(flags.format(), user)
                            added_source_terms += 1
                    for term in preview.all_terms:
                        source = _term_source(term, preview.source_language)
                        if (term.context, source) in existing_keys:
                            # The logical term is old: nothing is filled,
                            # cleared or re-flagged in any language.
                            for code in target_codes:
                                counters[code]["existing"] += 1
            except Exception:
                content_failed = True
                raise
            if added_source_terms:
                transaction.on_commit(fresh_component.schedule_sync_terminology)
    except Exception:
        if content_failed and created_by_this_apply:
            # Compensate the language files this call created; they are
            # empty because the content transaction rolled back.
            for translation in created_by_this_apply:
                try:
                    translation.remove(user)
                except Exception:
                    report_error(
                        "Could not compensate a glossary language created "
                        "by a failed loc-kit append",
                        level="error",
                        project=component.project,
                    )
        raise

    languages: dict[str, GlossaryLanguageAppendResult] = {
        code: GlossaryLanguageAppendResult(
            added=counters[code]["added"],
            existing=counters[code]["existing"],
            blank=counters[code]["blank"],
            unavailable=unavailable_reasons.get(code, ""),
            unavailable_count=counters[code]["unavailable"],
        )
        for code in target_codes
    }
    # Glossary languages the table knows nothing about: absent column, not
    # a failure.
    for translation in component.translation_set.select_related("language"):
        if translation.language_id == component.source_language_id:
            continue
        code = translation.language.code
        if code not in languages:
            languages[code] = GlossaryLanguageAppendResult(absent=True)
    return GlossaryAppendResult(languages=languages, added_terms=added_source_terms)


__all__ = [
    "GLOSSARY_SCHEMA_VERSION",
    "LOC_KIT_STRING_UPDATE_MAX_MUTATIONS",
    "LOC_KIT_STRING_UPDATE_PORTION_SIZE",
    "OPENROUTER_API_ROOT",
    "OPENROUTER_CHAT_COMPLETIONS_URL",
    "OPENROUTER_REQUEST_TIMEOUT",
    "PREVIEW_TERM_LIMIT",
    "PREVIEW_WARNING_LIMIT",
    "SAMPLE_TOO_LARGE",
    "ChangedSourceRow",
    "GlossaryAppendCollisionError",
    "GlossaryAppendResult",
    "GlossaryLanguageAppendResult",
    "GlossaryPreview",
    "GlossaryProfileError",
    "GlossaryTermPreview",
    "KitExplanationApplyResult",
    "ProfileProposalError",
    "SampleTooLargeError",
    "StringsAppendResult",
    "StringsUpdateResult",
    "append_glossary_terms",
    "append_translation_strings",
    "apply_kit_explanations",
    "apply_loc_kit_string_update",
    "build_glossary_structure_sample",
    "cap_preview_warnings",
    "classify_kit_explanations",
    "existing_string_keys",
    "find_changed_sources",
    "load_prepared_string_units",
    "load_profile_prompt",
    "profile_document_from_envelope",
    "request_profile_proposal",
    "validate_glossary_profile",
    "validate_loc_kit_string_update_size",
]
