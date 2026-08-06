# Copyright (C) HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from loc_kit_ingest.model import Severity
from loc_kit_ingest.parser import parse_component
from loc_kit_ingest.profile import ProfileError, load_profile
from loc_kit_ingest.reader import ReaderError, read_sheets, validate_sheet_headers
from loc_kit_ingest.writer import render_component, validate_rendered_component

if TYPE_CHECKING:
    from loc_kit_ingest.model import Diagnostic
    from loc_kit_ingest.profile import Profile


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def _format_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> str:
    lines: list[str] = []
    for d in diagnostics:
        lines.append(
            f"  [{d.severity.value}] {d.component}:{d.sheet}!row {d.row} "
            f"({d.code}): {d.message}"
        )
    return "\n".join(lines)


def _build_report(
    profile: Profile,
    parse_results: dict[str, object],
    diagnostics: tuple[Diagnostic, ...],
) -> str:
    lines: list[str] = []
    lines.append("Loc-kit ingest report")
    lines.append(f"Components: {len(profile.components)}")
    for comp in profile.components:
        result = parse_results.get(comp.component)
        if result is not None:
            lines.append(
                f"  {comp.component} ({comp.kind}): "
                f"{len(result.units)} units, "
                f"{len(result.skipped_rows)} skipped"
            )
    if diagnostics:
        lines.append("Diagnostics:")
        lines.append(_format_diagnostics(diagnostics))
    else:
        lines.append("Diagnostics: none")
    return "\n".join(lines) + "\n"


def run(
    kit_paths: list[Path],
    profile_path: Path,
    output: Path,
    zip_components: bool = False,
) -> int:
    """Run the full ingest pipeline.

    Returns 0 on success, 2 on any error.
    """
    all_diagnostics: list[Diagnostic] = []

    # 1. Validate output parent exists and output does not exist.
    if not output.parent.is_dir():
        all_diagnostics.append(
            _make_diag(
                "pipeline.missing_parent",
                f"output parent directory does not exist: {output.parent}",
            )
        )
        print(_format_diagnostics(tuple(all_diagnostics)), file=sys.stderr)
        return 2

    if output.exists():
        all_diagnostics.append(
            _make_diag(
                "pipeline.output_exists",
                f"output directory already exists: {output}",
            )
        )
        print(_format_diagnostics(tuple(all_diagnostics)), file=sys.stderr)
        return 2

    # 2. Load profile.
    try:
        profile = load_profile(profile_path)
    except ProfileError as exc:
        print(f"[{exc.diagnostic.code}] {exc.diagnostic.message}", file=sys.stderr)
        return 2

    # 3. Read all sheets from all kit files.
    all_sheets: dict[str, list[list[str]]] = {}
    for kit_path in kit_paths:
        try:
            sheets = read_sheets(Path(kit_path))
        except (ReaderError, Exception) as exc:
            all_diagnostics.append(
                _make_diag(
                    "reader.error",
                    f"cannot read {kit_path}: {exc}",
                )
            )
            print(_format_diagnostics(tuple(all_diagnostics)), file=sys.stderr)
            return 2
        all_sheets.update(sheets)

    # 4. Validate headers and parse each component.
    parse_results: dict[str, object] = {}
    for comp in profile.components:
        sheet_name = comp.sheet
        if sheet_name not in all_sheets:
            all_diagnostics.append(
                _make_diag(
                    "reader.missing_sheet",
                    f"sheet {sheet_name!r} not found in kit",
                    component=comp.component,
                    sheet=sheet_name,
                )
            )
            continue

        rows = all_sheets[sheet_name]

        # Validate headers.
        header_diagnostics = validate_sheet_headers(comp, rows)
        all_diagnostics.extend(header_diagnostics)

        # Parse component.
        result = parse_component(comp, rows)
        parse_results[comp.component] = result
        all_diagnostics.extend(result.diagnostics)

    # 5. Check for errors before staging.
    errors = [d for d in all_diagnostics if d.severity is Severity.ERROR]
    if errors:
        print(_format_diagnostics(tuple(all_diagnostics)), file=sys.stderr)
        return 2

    # 6. Create staging directory.
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent))
    )

    try:
        # 7. Render all components, then validate round-trip.
        for comp in profile.components:
            result = parse_results[comp.component]
            render_component(comp, result, staging)
            render_diagnostics = validate_rendered_component(
                comp, result, staging
            )
            all_diagnostics.extend(render_diagnostics)

        # Check render errors.
        render_errors = [d for d in all_diagnostics if d.severity is Severity.ERROR]
        if render_errors:
            print(_format_diagnostics(tuple(all_diagnostics)), file=sys.stderr)
            shutil.rmtree(staging, ignore_errors=True)
            return 2

        # 8. Create ZIP files if requested.
        if zip_components:
            for comp in profile.components:
                comp_dir = staging / comp.component
                zip_path = staging / f"{comp.component}.zip"
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for file_path in sorted(comp_dir.rglob("*")):
                        if file_path.is_file():
                            arcname = file_path.relative_to(comp_dir)
                            zf.write(file_path, arcname)

        # 9. Write report.
        report = _build_report(profile, parse_results, tuple(all_diagnostics))
        (staging / "report.txt").write_text(report, encoding="utf-8")

    except Exception as exc:
        all_diagnostics.append(
            _make_diag("pipeline.error", f"render/publish failure: {exc}")
        )
        print(_format_diagnostics(tuple(all_diagnostics)), file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 2

    # 10. Atomic publish.
    try:
        os.replace(staging, output)
    except OSError as exc:
        all_diagnostics.append(
            _make_diag("pipeline.publish_failed", f"cannot publish: {exc}")
        )
        print(_format_diagnostics(tuple(all_diagnostics)), file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 2

    # 11. Print report to stdout.
    print(report, end="")
    return 0


def _make_diag(
    code: str,
    message: str,
    *,
    component: str = "",
    sheet: str = "",
    row: int = 0,
) -> Diagnostic:
    from loc_kit_ingest.model import Diagnostic, Severity

    return Diagnostic(Severity.ERROR, code, component, sheet, row, message)
