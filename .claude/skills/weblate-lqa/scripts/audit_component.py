#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Weblate Component LQA Auditor & MQM-Core Scorecard Generator.

Supports both online Weblate API components and offline loc-kit files
(XLSX, CSV, TSV, PO, JSON), with full plural-form normalization. Categorizes
failing checks, identifies review candidates, and computes official MQM scores
when reviewed verdicts are supplied.

MQM scoring is always scoped to an explicitly declared `review_scope` (either
`{"coverage": "full"}` or `{"reviewed_unit_ids": [...]}`). The score's word-count
denominator is the words of exactly the units that were actually reviewed - never
silently the full component - so a partial review can never be misreported as a
component-wide grade.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
from typing import Any


def load_token(env_path: str | None = None) -> str | None:
    """Load API token securely from env vars or deploy/.env.local without printing it."""
    for var_name in ["WEBLATE_API_TOKEN", "PROD_WEBLATE_API_TOKEN"]:
        val = os.environ.get(var_name)
        if val:
            return val.strip('"\'')

    candidates = [
        env_path,
        "deploy/.env.local",
        "../deploy/.env.local",
        "../../deploy/.env.local",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        m = re.match(r"^(?:PROD_)?WEBLATE_API_TOKEN=(.+)$", line)
                        if m:
                            return m.group(1).strip("\"'")
            except Exception:
                continue
    return None


def normalize_to_string_list(val: Any) -> list[str]:
    """Normalize any source/target field (str, multistring, list, tuple) to a list of strings."""
    if val is None:
        return []
    if hasattr(val, "strings"):  # translate-toolkit multistring for PO plurals
        res = []
        for s in val.strings:
            s_str = str(s)
            if s_str and s_str not in res:
                res.append(s_str)
        return res or ([str(val)] if str(val) else [])
    if isinstance(val, (list, tuple)):
        return [str(item) for item in val if item is not None]
    if isinstance(val, str):
        return [val]
    return [str(val)]


def join_forms(forms: list[str]) -> str:
    """Format plural forms cleanly for display."""
    if not forms:
        return ""
    if len(forms) == 1:
        return forms[0]
    return " | ".join(forms)


def word_count(units: list[dict[str, Any]]) -> int:
    """Total source word count (all plural forms) across the given units."""
    return sum(
        sum(len(s.split()) for s in normalize_to_string_list(u.get("source")))
        for u in units
    )


class WeblateAuditor:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Token {token}",
            "User-Agent": "HCGameLoc-Auditor/1.0",
            "Accept": "application/json",
        }

    def api_get(self, path: str) -> dict[str, Any]:
        """Make an authenticated GET request to Weblate API."""
        url = path if path.startswith("http") else f"{self.base_url}/api/{path.lstrip('/')}"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_all_units(self, project: str, component: str, language: str) -> list[dict[str, Any]]:
        """Fetch all translation units for a component language with pagination."""
        url = f"{self.base_url}/api/translations/{project}/{component}/{language}/units/?limit=500"
        results = []
        data = self.api_get(url)
        results.extend(data.get("results", []))
        while data.get("next"):
            data = self.api_get(data["next"])
            results.extend(data.get("results", []))
        return results

    def fetch_failing_checks(self, project: str, component: str, language: str) -> dict[str, list[dict[str, Any]]]:
        """Discover which specific check IDs are currently failing, paginating through all results."""
        check_ids = [
            "same", "multiple_capital", "reused", "inconsistent", "duplicate",
            "game-markup", "game-token", "game-number", "game-length", "game-line-break",
            "cyrillic-leak", "punctuation_spacing", "end_stop", "end_colon",
            "end_question", "end_exclamation", "ellipsis", "double_space",
            "begin_space", "end_space", "max_length"
        ]
        failing_by_id: dict[str, list[dict[str, Any]]] = {}
        for cid in check_ids:
            try:
                encoded_q = urllib.parse.quote(f"check:{cid}")
                url = f"{self.base_url}/api/translations/{project}/{component}/{language}/units/?q={encoded_q}&limit=500"
                res = self.api_get(url)
                count = res.get("count", 0)
                if count > 0:
                    check_units = list(res.get("results", []))
                    while res.get("next"):
                        res = self.api_get(res["next"])
                        check_units.extend(res.get("results", []))
                    failing_by_id[cid] = check_units
            except Exception:
                continue
        return failing_by_id


def _extract_po_multiline(keyword: str, block: str) -> str | None:
    """Extract full string (with line concatenations) for a PO keyword."""
    m = re.search(rf'{keyword}\s+"((?:[^"\\]|\\.)*)"((?:\s*\n\s*"((?:[^"\\]|\\.)*)")*)', block)
    if not m:
        return None
    first = m.group(1)
    rest = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2)) if m.group(2) else []
    full = first + "".join(rest)
    return full.replace(r"\n", "\n").replace(r'\"', '"').replace(r"\t", "\t")


def _parse_po_fallback(file_path: Path) -> list[dict[str, Any]]:
    """Robust fallback PO parser for multiline strings and plural forms when translate-toolkit is unavailable."""
    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    blocks = re.split(r"\n\s*\n", content)
    units = []
    idx = 1

    for block in blocks:
        if not block.strip() or block.startswith("#, fuzzy"):
            continue

        ctx = _extract_po_multiline("msgctxt", block) or f"entry_{idx}"
        msgid = _extract_po_multiline("msgid", block)
        if msgid is None or (msgid == "" and idx == 1):  # skip header msgid ""
            continue

        msgid_plural = _extract_po_multiline("msgid_plural", block)
        src_list = [msgid, msgid_plural] if msgid_plural else [msgid]

        # Parse msgstr / msgstr[N]
        plural_indices = re.findall(r'msgstr\[(\d+)\]', block)
        if plural_indices:
            tgt_list = []
            for p_idx in sorted(set(map(int, plural_indices))):
                val = _extract_po_multiline(rf'msgstr\[{p_idx}\]', block)
                if val is not None:
                    tgt_list.append(val)
        else:
            msgstr = _extract_po_multiline("msgstr", block)
            tgt_list = [msgstr] if msgstr is not None else []

        units.append({
            "id": idx,
            "context": ctx,
            "source": src_list,
            "target": tgt_list,
        })
        idx += 1

    return units


def load_units_from_file(file_path: str, target_lang: str | None = None) -> list[dict[str, Any]]:
    """Parse translation units from a local file (XLSX, CSV, TSV, PO, JSON) with plural support."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Local file not found: {file_path}")

    ext = p.suffix.lower()
    units: list[dict[str, Any]] = []

    # 1. Tabular loc-kits (XLSX, CSV, TSV) via repository loc_kit_ingest.reader
    if ext in (".xlsx", ".csv", ".tsv"):
        try:
            from loc_kit_ingest.reader import read_sheets
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
            from loc_kit_ingest.reader import read_sheets

        sheets = read_sheets(p)
        idx = 1
        for sheet_name, rows in sheets.items():
            if not rows or len(rows) < 2:
                continue
            header = [str(c).strip().lower() for c in rows[0]]

            # Locate key/context column
            ctx_idx = next((i for i, col in enumerate(header) if col in ("context", "key", "id", "string_id", "name")), 0)

            # Locate source column
            src_idx = next((i for i, col in enumerate(header) if col in ("source", "src", "ru", "en", "original")), 1 if len(header) > 1 else 0)

            # Locate target column
            tgt_idx = None
            if target_lang:
                tgt_idx = next((i for i, col in enumerate(header) if col == target_lang.lower()), None)
            if tgt_idx is None:
                tgt_idx = next((i for i, col in enumerate(header) if col in ("target", "tgt", "translation", "de", "fr", "es", "ja", "zh") and i != src_idx), 2 if len(header) > 2 else src_idx)

            for row_idx, row in enumerate(rows[1:], start=2):
                ctx_val = row[ctx_idx].strip() if ctx_idx < len(row) else f"{sheet_name}_r{row_idx}"
                src_val = row[src_idx].strip() if src_idx < len(row) else ""
                tgt_val = row[tgt_idx].strip() if tgt_idx < len(row) else ""
                if src_val or tgt_val:
                    units.append({
                        "id": idx,
                        "context": ctx_val,
                        "source": normalize_to_string_list(src_val),
                        "target": normalize_to_string_list(tgt_val),
                    })
                    idx += 1

    # 2. Gettext PO files (translate.storage.pypo with fallback parser)
    elif ext == ".po":
        try:
            from translate.storage.pypo import pofile
            po = pofile(open(p, "rb"))
            idx = 1
            for unit in po.units:
                if unit.isheader() or unit.isfuzzy():
                    continue
                src_list = normalize_to_string_list(unit.source)
                tgt_list = normalize_to_string_list(unit.target)
                ctx = unit.getcontext() or f"po_unit_{idx}"
                if src_list:
                    units.append({
                        "id": idx,
                        "context": ctx,
                        "source": src_list,
                        "target": tgt_list,
                    })
                    idx += 1
        except ImportError:
            units = _parse_po_fallback(p)

    # 3. JSON files
    elif ext == ".json":
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                for idx, item in enumerate(data, start=1):
                    units.append({
                        "id": item.get("id", idx),
                        "context": item.get("context", item.get("key", f"unit_{idx}")),
                        "source": normalize_to_string_list(item.get("source", item.get("src", ""))),
                        "target": normalize_to_string_list(item.get("target", item.get("tgt", ""))),
                    })
            elif isinstance(data, dict):
                idx = 1
                for k, v in data.items():
                    if isinstance(v, dict):
                        units.append({
                            "id": idx,
                            "context": k,
                            "source": normalize_to_string_list(v.get("source", v.get("src", ""))),
                            "target": normalize_to_string_list(v.get("target", v.get("tgt", ""))),
                        })
                    elif isinstance(v, (str, list)):
                        units.append({
                            "id": idx,
                            "context": k,
                            "source": [k],
                            "target": normalize_to_string_list(v),
                        })
                    idx += 1
    else:
        raise ValueError(f"Unsupported file format: {ext}. Supported formats: .xlsx, .csv, .tsv, .po, .json")

    return units


def extract_candidates(units: list[dict[str, Any]], target_lang: str) -> list[dict[str, Any]]:
    """Extract candidate anomalies (heuristics) across all plural forms."""
    candidates = []
    for u in units:
        src_list = normalize_to_string_list(u.get("source"))
        tgt_list = normalize_to_string_list(u.get("target"))
        ctx = u.get("context", "")
        uid = u.get("id")

        for form_idx, tgt in enumerate(tgt_list):
            if not tgt:
                continue
            src = src_list[form_idx] if form_idx < len(src_list) else (src_list[0] if src_list else "")

            # 1. Acronym leak heuristic (English acronyms in non-EN targets)
            if target_lang not in ("en", "ru"):
                m = re.search(r"\b(AT|HP|XP|DPS|LMB|RMB|Cancel|Exit|Damage|Heal)\b", tgt)
                if m:
                    matched_token = m.group(1)
                    candidates.append({
                        "unit_id": uid,
                        "context": f"{ctx}[plural_{form_idx}]" if len(tgt_list) > 1 else ctx,
                        "source": src,
                        "target": tgt,
                        "candidate_type": "acronym_leak",
                        "matched": matched_token,
                        "note": f"Found English token '{matched_token}' in {target_lang.upper()} target (form {form_idx+1}/{len(tgt_list)}).",
                    })

            # 2. Cyrillic leak heuristic (in Latin/CJK targets)
            if target_lang not in ("ru", "uk", "be", "sr"):
                if re.search(r"[\u0400-\u04FF]", tgt):
                    candidates.append({
                        "unit_id": uid,
                        "context": f"{ctx}[plural_{form_idx}]" if len(tgt_list) > 1 else ctx,
                        "source": src,
                        "target": tgt,
                        "candidate_type": "cyrillic_leak",
                        "note": f"Cyrillic character detected in {target_lang.upper()} target.",
                    })

            # 3. Placeholder / bracket mismatch heuristic
            src_brackets = src.count("[") + src.count("]")
            tgt_brackets = tgt.count("[") + tgt.count("]")
            if src_brackets != tgt_brackets:
                candidates.append({
                    "unit_id": uid,
                    "context": f"{ctx}[plural_{form_idx}]" if len(tgt_list) > 1 else ctx,
                    "source": src,
                    "target": tgt,
                    "candidate_type": "bracket_count_mismatch",
                    "note": f"Source has {src_brackets} bracket symbols, target has {tgt_brackets}.",
                })

    return candidates


def resolve_review_scope(
    units: list[dict[str, Any]],
    review_scope: dict[str, Any] | None,
    verdicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Resolve an explicit review_scope declaration to the exact set of units the MQM
    denominator must be computed over. Never falls back to "all units" silently -
    a partial review scored against the full component's word count is exactly the
    bug this function exists to prevent (a partial-sample numerator divided by a
    full-population denominator silently assumes the unreviewed rest is defect-free).
    """
    if not review_scope or not isinstance(review_scope, dict):
        raise ValueError(
            "verdicts file is missing a top-level 'review_scope' object. Every MQM score "
            "must declare exactly what was reviewed: either {\"coverage\": \"full\"} (every "
            "unit in the component was checked) or {\"reviewed_unit_ids\": [...]} (the exact "
            "list of unit IDs actually reviewed, including units with no defect). Without this, "
            "the score cannot be trusted - a partial sample must never be silently divided by "
            "the full component's word count."
        )

    all_ids = {u["id"] for u in units}
    coverage = review_scope.get("coverage")

    if coverage == "full":
        scoped_units = units
    elif "reviewed_unit_ids" in review_scope:
        reviewed_ids = review_scope["reviewed_unit_ids"]
        if not isinstance(reviewed_ids, list) or not reviewed_ids:
            raise ValueError(
                "review_scope.reviewed_unit_ids must be a non-empty list of unit IDs that were "
                "actually reviewed (not just units that ended up with a defect). An empty or "
                "missing list means no scope was declared - fill it in before scoring."
            )
        unknown = [uid for uid in reviewed_ids if uid not in all_ids]
        if unknown:
            raise ValueError(
                f"review_scope.reviewed_unit_ids contains {len(unknown)} unit ID(s) not present "
                f"in this component/file: {unknown[:10]}{'...' if len(unknown) > 10 else ''}"
            )
        reviewed_id_set = set(reviewed_ids)
        scoped_units = [u for u in units if u["id"] in reviewed_id_set]
    else:
        raise ValueError(
            "review_scope must set either \"coverage\": \"full\" or a non-empty "
            "\"reviewed_unit_ids\" list. Got neither."
        )

    scoped_ids = {u["id"] for u in scoped_units}
    scoped_by_id = {u["id"]: u for u in scoped_units}
    out_of_scope = [v for v in verdicts if v.get("unit_id") not in scoped_ids]
    if out_of_scope:
        bad_ids = [v.get("unit_id") for v in out_of_scope]
        raise ValueError(
            f"{len(out_of_scope)} verdict(s) reference unit ID(s) outside the declared "
            f"review_scope: {bad_ids[:10]}{'...' if len(bad_ids) > 10 else ''}. A defect cannot "
            "be scored in a unit that was not declared as reviewed - add those IDs to "
            "reviewed_unit_ids or remove the mismatched verdicts."
        )

    # Catch unit_id transcription errors: a verdict's declared context must match
    # the actual context of the unit at that ID. Hand-authoring verdicts by typing
    # IDs from memory (instead of copying them from tool output) can silently attach
    # a real defect description to the wrong, innocent unit - this check makes that
    # class of mistake fail loudly instead of corrupting the wrong string on "fix".
    mismatched = []
    for v in verdicts:
        actual_ctx = scoped_by_id.get(v.get("unit_id"), {}).get("context", "")
        declared_ctx = str(v.get("context", "")).split("[plural_")[0]  # strip plural-form suffix
        if declared_ctx and actual_ctx and declared_ctx != actual_ctx:
            mismatched.append((v.get("unit_id"), declared_ctx, actual_ctx))
    if mismatched:
        detail = "; ".join(f"unit {uid}: verdict says '{d}', actual context is '{a}'" for uid, d, a in mismatched[:5])
        raise ValueError(
            f"{len(mismatched)} verdict(s) have a context that does not match the actual "
            f"unit at that ID - likely a unit_id transcription error: {detail}"
            f"{'; ...' if len(mismatched) > 5 else ''}. Re-copy the unit_id directly from "
            "tool output rather than typing it from memory."
        )

    return {
        "mode": "full" if coverage == "full" else "sample",
        "reviewed_unit_count": len(scoped_units),
        "total_unit_count": len(units),
        "reviewed_word_count": word_count(scoped_units),
        "total_word_count": word_count(units),
    }


def compute_mqm_score(
    units: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
    review_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Calculate MQM penalty points and score based on reviewed verdicts, scoped strictly
    to the declared review_scope. Raises ValueError if review_scope is missing/invalid,
    if any verdict is unreviewed, or if any verdict references a unit outside scope.
    """
    scope = resolve_review_scope(units, review_scope, verdicts)

    weights = {"neutral": 0, "minor": 1, "major": 5, "critical": 25}
    counts = {"neutral": 0, "minor": 0, "major": 0, "critical": 0}
    total_penalty = 0

    for idx, v in enumerate(verdicts):
        if not v.get("reviewed", True) or v.get("severity") in (None, "pending", ""):
            raise ValueError(
                f"Verdict #{idx + 1} (Unit {v.get('unit_id', 'N/A')}, context '{v.get('context', '')}') "
                "is marked as pending or unreviewed. Every verdict must be explicitly reviewed with a "
                "valid severity ('neutral', 'minor', 'major', 'critical') before computing MQM scores."
            )

        sev = str(v.get("severity")).lower()
        if sev not in weights:
            raise ValueError(
                f"Verdict #{idx + 1} has invalid severity '{sev}'. "
                f"Allowed values are: {list(weights.keys())}"
            )

        counts[sev] += 1
        total_penalty += weights[sev]

    reviewed_words = scope["reviewed_word_count"]
    if reviewed_words <= 0:
        score = 100.0
    else:
        score = max(0.0, 100.0 - ((total_penalty / reviewed_words) * 100.0))
    score = round(score, 2)

    has_critical = counts["critical"] > 0
    is_full_coverage = scope["mode"] == "full"

    # Release Gate Evaluation (Critical defects are hard blockers; partial coverage
    # can never resolve to a clean "Pass" grade, no matter how high the sample score is -
    # the unreviewed remainder is unverified, not verified-clean).
    if has_critical:
        grade = "Fail (Critical Blocker)"
        status = "Blocked (Critical defect must be resolved before release)"
    elif not is_full_coverage:
        grade = "Not gradable (partial coverage)"
        status = (
            f"Blocked - audit incomplete: only {scope['reviewed_unit_count']}/{scope['total_unit_count']} "
            f"units ({scope['reviewed_unit_count'] / scope['total_unit_count'] * 100:.1f}%) reviewed. "
            "Score below is a defect-density indicator over the reviewed subset only, not a "
            "component-wide grade."
        )
    elif score >= 95.0:
        grade = "Grade A (Pass)"
        status = "Approved for Release"
    elif score >= 85.0:
        grade = "Grade B (Conditional)"
        status = "Conditional Approval (fix Major issues)"
    elif score >= 70.0:
        grade = "Grade C (Blocked)"
        status = "Blocked (Re-translation / Polish required)"
    else:
        grade = "Fail (Rejected)"
        status = "Rejected (Extensive re-translation required)"

    return {
        "coverage_mode": scope["mode"],
        "reviewed_unit_count": scope["reviewed_unit_count"],
        "total_unit_count": scope["total_unit_count"],
        "reviewed_word_count": scope["reviewed_word_count"],
        "total_word_count": scope["total_word_count"],
        "total_words": reviewed_words,  # MQM denominator actually used
        "total_penalties": total_penalty,
        "mqm_score": score,
        "counts": counts,
        "grade": grade,
        "status": status,
        "has_critical": has_critical,
        "is_full_coverage": is_full_coverage,
    }


def format_markdown_report(
    target_name: str,
    language: str,
    total_units: int,
    total_words: int,
    failing_checks: dict[str, list[dict[str, Any]]],
    candidates: list[dict[str, Any]],
    mqm_results: dict[str, Any] | None,
    verdicts: list[dict[str, Any]] | None,
) -> str:
    """Generate a clean markdown report."""
    md = []
    md.append(f"# Weblate LQA Audit: {target_name} ({language.upper()})\n")
    md.append(f"- **Target:** `{target_name}`")
    md.append(f"- **Language:** `{language}`")
    md.append(f"- **Total Units:** {total_units}")
    md.append(f"- **Total Source Words (all plural forms):** {total_words}")
    total_check_units = sum(len(ulist) for ulist in failing_checks.values())
    if total_check_units > 0:
        md.append(f"- **Active Check Warnings:** {total_check_units} (across {len(failing_checks)} check types)\n")
    else:
        md.append("- **Active Check Warnings:** 0\n")

    # MQM Section (if verdicts were provided)
    if mqm_results and verdicts is not None:
        cov_pct = mqm_results["reviewed_unit_count"] / mqm_results["total_unit_count"] * 100 if mqm_results["total_unit_count"] else 100.0
        word_cov_pct = mqm_results["reviewed_word_count"] / mqm_results["total_word_count"] * 100 if mqm_results["total_word_count"] else 100.0

        md.append("## 1. MQM-Core Quality Scorecard (Reviewed Verdicts)\n")
        md.append("### Review Coverage\n")
        md.append("| Metric | Value |")
        md.append("|---|---|")
        md.append(f"| **Coverage mode** | `{mqm_results['coverage_mode']}` ({'entire component reviewed' if mqm_results['is_full_coverage'] else 'PARTIAL - see release gate note'}) |")
        md.append(f"| **Units reviewed** | {mqm_results['reviewed_unit_count']} / {mqm_results['total_unit_count']} ({cov_pct:.1f}%) |")
        md.append(f"| **Words reviewed (MQM denominator)** | {mqm_results['reviewed_word_count']} / {mqm_results['total_word_count']} ({word_cov_pct:.1f}%) |\n")

        score_label = "MQM Quality Score (component-wide)" if mqm_results["is_full_coverage"] else "MQM Score (SAMPLE-SCOPED, non-projectable)"
        md.append("| Metric | Value | Status |")
        md.append("|---|---|---|")
        md.append(f"| **{score_label}** | **{mqm_results['mqm_score']} / 100** | **{mqm_results['grade']}** |")
        md.append(f"| **Release Gate** | {mqm_results['status']} | {'🔴 BLOCKED' if mqm_results['has_critical'] or not mqm_results['is_full_coverage'] or mqm_results['mqm_score'] < 85 else '🟢 PASS'} |")
        md.append(f"| **Critical Defects (25 pt)** | {mqm_results['counts']['critical']} | {'🔴 Requires immediate fix' if mqm_results['counts']['critical'] > 0 else 'None'} |")
        md.append(f"| **Major Defects (5 pt)** | {mqm_results['counts']['major']} | Terminology/mechanic issues |")
        md.append(f"| **Minor Defects (1 pt)** | {mqm_results['counts']['minor']} | Minor polish |")
        md.append(f"| **Total Penalty Points** | {mqm_results['total_penalties']} pt | Formula: $100 - (\\text{{Penalties}}/\\text{{Reviewed Words}}) \\times 100$ |\n")

        md.append("### Reviewed Defect Log\n")
        for v in verdicts:
            sev_icon = {"critical": "🔴", "major": "🟠", "minor": "🟡", "neutral": "⚪"}.get(str(v.get("severity", "minor")).lower(), "🟡")
            md.append(f"- {sev_icon} **[{str(v.get('severity', 'minor')).upper()}]** `{v.get('context', 'unknown')}` (Unit {v.get('unit_id', 'N/A')}):")
            md.append(f"  - **Source:** `{v.get('source', '')}`")
            md.append(f"  - **Target:** `{v.get('target', '')}`")
            md.append(f"  - **Category:** `{v.get('category', 'accuracy/mistranslation')}`")
            md.append(f"  - **Explanation:** {v.get('explanation', '')}\n")
    else:
        md.append("## 1. MQM-Core Scorecard Status\n")
        md.append("> ℹ️ **No reviewed verdicts file provided.** MQM Quality Score is reserved for human or LLM-judge reviewed verdicts. Run with `--verdicts <file.json>` to compute formal MQM metrics.\n")

    # Layer 0 Checks Section
    if failing_checks:
        md.append("## 2. Deterministic Weblate Checks Breakdown (Layer 0)\n")
        for cid, ulist in failing_checks.items():
            md.append(f"### Check: `{cid}` ({len(ulist)} strings)")
            for u in ulist:
                src = join_forms(normalize_to_string_list(u.get("source")))
                tgt = join_forms(normalize_to_string_list(u.get("target")))
                md.append(f"- Unit {u['id']} (`{u.get('context')}`): `{src}` $\\to$ `{tgt}`")
            md.append("")

    # Layer 1 Candidates Section
    md.append("## 3. Heuristic Candidates for Review (Hypotheses)\n")
    if not candidates:
        md.append("No heuristic anomalies detected.\n")
    else:
        md.append(f"Found {len(candidates)} candidate items requiring verification:\n")
        for c in candidates:
            md.append(f"- **[{c['candidate_type']}]** `{c.get('context')}` (Unit {c.get('unit_id')}):")
            md.append(f"  - SRC: `{c.get('source')}`")
            md.append(f"  - TGT: `{c.get('target')}`")
            md.append(f"  - Note: {c.get('note')}\n")

    return "\n".join(md)


def main():
    parser = argparse.ArgumentParser(description="Weblate LQA Auditor & MQM-Core Scorecard Generator")
    parser.add_argument("--url", default="https://l10n.herocraft.com", help="Weblate base URL (API mode)")
    parser.add_argument("--token", help="Weblate API token (defaults to deploy/.env.local or WEBLATE_API_TOKEN)")
    parser.add_argument("--project", help="Project slug (API mode)")
    parser.add_argument("--component", help="Component slug (API mode)")
    parser.add_argument("--file", help="Path to local translation file (XLSX, CSV, TSV, PO, JSON) for offline mode")
    parser.add_argument("--lang", default="de", help="Target language code (e.g. de, fr, es)")
    parser.add_argument("--verdicts", help="Path to reviewed verdicts JSON file (must include review_scope) for MQM scoring")
    parser.add_argument("--output-json", help="Path to save raw audit JSON data")
    parser.add_argument("--save-verdicts-draft", help="Path to save template verdicts draft JSON for annotation")

    args = parser.parse_args()

    # Determine execution mode: Local File vs Weblate API
    if args.file:
        target_name = os.path.basename(args.file)
        units = load_units_from_file(args.file, target_lang=args.lang)
        failing_checks: dict[str, list[dict[str, Any]]] = {}
    elif args.project and args.component:
        target_name = f"{args.project}/{args.component}"
        token = args.token or load_token()
        if not token:
            print("Error: Weblate API token not found in env vars or deploy/.env.local (required for API mode).", file=sys.stderr)
            sys.exit(1)
        auditor = WeblateAuditor(args.url, token)
        units = auditor.fetch_all_units(args.project, args.component, args.lang)
        failing_checks = auditor.fetch_failing_checks(args.project, args.component, args.lang)
    else:
        print("Error: Specify either --file <path> for local mode or --project <p> --component <c> for API mode.", file=sys.stderr)
        sys.exit(1)

    total_units = len(units)
    total_words = word_count(units)

    # Extract candidate anomalies
    candidates = extract_candidates(units, args.lang)

    # Process verdicts if supplied
    verdicts_data = None
    mqm_results = None
    if args.verdicts and os.path.exists(args.verdicts):
        with open(args.verdicts, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            if isinstance(raw_data, dict):
                verdicts_data = raw_data.get("verdicts", [])
                review_scope = raw_data.get("review_scope")
            elif isinstance(raw_data, list):
                verdicts_data = raw_data
                review_scope = None
            else:
                verdicts_data = []
                review_scope = None

        try:
            mqm_results = compute_mqm_score(units, verdicts_data, review_scope)
        except ValueError as err:
            print(f"Error validating verdicts: {err}", file=sys.stderr)
            sys.exit(1)

    # Save draft verdicts template if requested
    if args.save_verdicts_draft:
        draft = []
        for c in candidates:
            draft.append({
                "unit_id": c.get("unit_id"),
                "context": c.get("context"),
                "source": c.get("source"),
                "target": c.get("target"),
                "category": "pending",
                "severity": "pending",
                "explanation": f"[Candidate: {c.get('candidate_type')}] {c.get('note')}",
                "reviewed": False,
            })
        draft_payload = {
            "review_scope": {
                "_instructions": (
                    "REQUIRED before this file can be scored. Set either "
                    "{\"coverage\": \"full\"} if you reviewed literally every unit in the "
                    "component, or replace reviewed_unit_ids below with the exact list of "
                    "every unit ID you actually reviewed - not just the ones with a defect "
                    "below. An empty list will be rejected."
                ),
                "reviewed_unit_ids": [],
            },
            "verdicts": draft,
        }
        with open(args.save_verdicts_draft, "w", encoding="utf-8") as f:
            json.dump(draft_payload, f, indent=2, ensure_ascii=False)
        print(f"Saved unreviewed verdicts draft template ({len(draft)} items) to {args.save_verdicts_draft}. Fill in review_scope before scoring.", file=sys.stderr)

    # Save raw JSON if requested
    if args.output_json:
        out_payload = {
            "target": target_name,
            "language": args.lang,
            "total_units": total_units,
            "total_words": total_words,
            "failing_checks": {cid: [u["id"] for u in ulist] for cid, ulist in failing_checks.items()},
            "candidates": candidates,
            "mqm_results": mqm_results,
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(out_payload, f, indent=2, ensure_ascii=False)

    # Print markdown report
    report = format_markdown_report(
        target_name=target_name,
        language=args.lang,
        total_units=total_units,
        total_words=total_words,
        failing_checks=failing_checks,
        candidates=candidates,
        mqm_results=mqm_results,
        verdicts=verdicts_data,
    )
    print(report)


if __name__ == "__main__":
    main()
