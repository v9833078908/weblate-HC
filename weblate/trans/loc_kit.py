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

import json
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from django.conf import settings
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from weblate.utils.lock import WeblateLockTimeoutError
from weblate.utils.requests import fetch_validated_url

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from loc_kit_ingest.model import Diagnostic, GlossaryTerm

    from weblate.auth.models import AuthenticatedHttpRequest
    from weblate.trans.models import Component, Translation


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
    for field in ("status", "profile", "assumptions", "reason"):
        if field not in envelope:
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
            collisions.append(
                (source, existing_contexts.get(source, ""), term.context)
            )
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
            "Adding a language requires the “Add language for translation” "
            "permission."
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
            raise GlossaryAppendCollisionError(
                _(
                    "Some source terms already exist under a different section. "
                    "Resolve the conflict before appending."
                ),
                conflicts=collisions,
            )

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
            code: sum(
                1
                for term in new_terms
                if term.values.get(code, "").strip()
            )
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
                    outcome = _resolve_missing_language(
                        request, locked_component, code
                    )
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
                else:
                    # The file and its VCS commit exist now; re-check the
                    # write permission defensively before any content.
                    if user.has_perm("unit.add", outcome):
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
                raise GlossaryAppendCollisionError(
                    _(
                        "Some source terms already exist under a different "
                        "section. Resolve the conflict before appending."
                    ),
                    conflicts=collisions,
                )
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
                                msg = f"Could not add glossary term {source!r}"
                                raise ValueError(msg)
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
    "OPENROUTER_API_ROOT",
    "OPENROUTER_CHAT_COMPLETIONS_URL",
    "OPENROUTER_REQUEST_TIMEOUT",
    "PREVIEW_TERM_LIMIT",
    "PREVIEW_WARNING_LIMIT",
    "SAMPLE_TOO_LARGE",
    "GlossaryAppendCollisionError",
    "GlossaryAppendResult",
    "GlossaryLanguageAppendResult",
    "GlossaryPreview",
    "GlossaryProfileError",
    "GlossaryTermPreview",
    "ProfileProposalError",
    "SampleTooLargeError",
    "append_glossary_terms",
    "build_glossary_structure_sample",
    "cap_preview_warnings",
    "load_profile_prompt",
    "profile_document_from_envelope",
    "request_profile_proposal",
    "validate_glossary_profile",
]
