#!/usr/bin/env python3
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Weblate Component LQA Auditor & MQM-Core Scorecard Generator.

Supports both online Weblate API components and offline loc-kit files
(XLSX, CSV, TSV, PO, JSON). Categorizes failing checks, identifies review
candidates, and computes official MQM scores when reviewed verdicts are supplied.
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


def load_units_from_file(file_path: str, target_lang: str | None = None) -> list[dict[str, Any]]:
    """Parse translation units from a local file (XLSX, CSV, TSV, PO, JSON)."""
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
            # Fallback path if run outside repository root
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
                        "source": src_val,
                        "target": tgt_val,
                    })
                    idx += 1

    # 2. Gettext PO files via translate.storage.pypo
    elif ext == ".po":
        from translate.storage.pypo import pofile
        po = pofile(open(p, "rb"))
        idx = 1
        for unit in po.units:
            if unit.isheader() or unit.isfuzzy():
                continue
            src = unit.source
            tgt = unit.target
            ctx = unit.getcontext() or f"po_unit_{idx}"
            if src:
                units.append({
                    "id": idx,
                    "context": ctx,
                    "source": src,
                    "target": tgt,
                })
                idx += 1

    # 3. JSON files
    elif ext == ".json":
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                for idx, item in enumerate(data, start=1):
                    units.append({
                        "id": item.get("id", idx),
                        "context": item.get("context", item.get("key", f"unit_{idx}")),
                        "source": item.get("source", item.get("src", "")),
                        "target": item.get("target", item.get("tgt", "")),
                    })
            elif isinstance(data, dict):
                idx = 1
                for k, v in data.items():
                    if isinstance(v, dict):
                        units.append({
                            "id": idx,
                            "context": k,
                            "source": v.get("source", v.get("src", "")),
                            "target": v.get("target", v.get("tgt", "")),
                        })
                    elif isinstance(v, str):
                        units.append({
                            "id": idx,
                            "context": k,
                            "source": k,
                            "target": v,
                        })
                    idx += 1
    else:
        raise ValueError(f"Unsupported file format: {ext}. Supported formats: .xlsx, .csv, .tsv, .po, .json")

    return units


def extract_candidates(units: list[dict[str, Any]], target_lang: str) -> list[dict[str, Any]]:
    """Extract candidate anomalies (heuristics) clearly labeled as hypotheses."""
    candidates = []
    for u in units:
        src = u["source"][0] if isinstance(u.get("source"), list) else u.get("source", "")
        tgt = u["target"][0] if isinstance(u.get("target"), list) else u.get("target", "")
        ctx = u.get("context", "")
        uid = u.get("id")

        if not tgt:
            continue

        # 1. Acronym leak heuristic (English acronyms in non-EN targets)
        if target_lang not in ("en", "ru"):
            m = re.search(r"\b(AT|HP|XP|DPS|LMB|RMB|Cancel|Exit|Damage|Heal)\b", tgt)
            if m:
                matched_token = m.group(1)
                candidates.append({
                    "unit_id": uid,
                    "context": ctx,
                    "source": src,
                    "target": tgt,
                    "candidate_type": "acronym_leak",
                    "matched": matched_token,
                    "note": f"Found English token '{matched_token}' in {target_lang.upper()} target.",
                })

        # 2. Cyrillic leak heuristic (in Latin/CJK targets)
        if target_lang not in ("ru", "uk", "be", "sr"):
            if re.search(r"[\u0400-\u04FF]", tgt):
                candidates.append({
                    "unit_id": uid,
                    "context": ctx,
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
                "context": ctx,
                "source": src,
                "target": tgt,
                "candidate_type": "bracket_count_mismatch",
                "note": f"Source has {src_brackets} bracket symbols, target has {tgt_brackets}.",
            })

    return candidates


def compute_mqm_score(total_words: int, verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate MQM penalty points and score based on reviewed verdicts.
    
    Raises ValueError if any verdict is unreviewed or has pending/invalid severity.
    """
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

    if total_words <= 0:
        score = 100.0
    else:
        score = max(0.0, 100.0 - ((total_penalty / total_words) * 100.0))
    score = round(score, 2)

    # Release Gate Evaluation (Critical defects are hard blockers)
    has_critical = counts["critical"] > 0
    if has_critical:
        grade = "Fail (Critical Blocker)"
        status = "Blocked (Critical defect must be resolved before release)"
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
        "total_words": total_words,
        "total_penalties": total_penalty,
        "mqm_score": score,
        "counts": counts,
        "grade": grade,
        "status": status,
        "has_critical": has_critical,
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
    md.append(f"- **Total Source Words:** {total_words}")
    total_check_units = sum(len(ulist) for ulist in failing_checks.values())
    if total_check_units > 0:
        md.append(f"- **Active Check Warnings:** {total_check_units} (across {len(failing_checks)} check types)\n")
    else:
        md.append(f"- **Active Check Warnings:** 0\n")

    # MQM Section (if verdicts were provided)
    if mqm_results and verdicts is not None:
        md.append("## 1. MQM-Core Quality Scorecard (Reviewed Verdicts)\n")
        md.append("| Metric | Value | Status |")
        md.append("|---|---|---|")
        md.append(f"| **MQM Quality Score** | **{mqm_results['mqm_score']} / 100** | **{mqm_results['grade']}** |")
        md.append(f"| **Release Gate** | {mqm_results['status']} | {'🔴 BLOCKED' if mqm_results['has_critical'] or mqm_results['mqm_score'] < 85 else '🟢 PASS'} |")
        md.append(f"| **Critical Defects (25 pt)** | {mqm_results['counts']['critical']} | {'🔴 Requires immediate fix' if mqm_results['counts']['critical'] > 0 else 'None'} |")
        md.append(f"| **Major Defects (5 pt)** | {mqm_results['counts']['major']} | Terminology/mechanic issues |")
        md.append(f"| **Minor Defects (1 pt)** | {mqm_results['counts']['minor']} | Minor polish |")
        md.append(f"| **Total Penalty Points** | {mqm_results['total_penalties']} pt | Formula: $100 - (\\text{{Penalties}}/\\text{{Words}}) \\times 100$ |\n")

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
                src = u["source"][0] if isinstance(u.get("source"), list) else u.get("source", "")
                tgt = u["target"][0] if isinstance(u.get("target"), list) else u.get("target", "")
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
    parser.add_argument("--verdicts", help="Path to reviewed verdicts JSON file for MQM scoring")
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

    # Word count
    total_words = sum(
        len((u["source"][0] if isinstance(u.get("source"), list) else u.get("source", "")).split())
        for u in units
    )

    # Extract candidate anomalies
    candidates = extract_candidates(units, args.lang)

    # Process verdicts if supplied
    verdicts_data = None
    mqm_results = None
    if args.verdicts and os.path.exists(args.verdicts):
        with open(args.verdicts, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            if isinstance(raw_data, dict) and "verdicts" in raw_data:
                verdicts_data = raw_data["verdicts"]
            elif isinstance(raw_data, list):
                verdicts_data = raw_data
            else:
                verdicts_data = []

        try:
            mqm_results = compute_mqm_score(total_words, verdicts_data)
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
        with open(args.save_verdicts_draft, "w", encoding="utf-8") as f:
            json.dump({"verdicts": draft}, f, indent=2, ensure_ascii=False)
        print(f"Saved unreviewed verdicts draft template ({len(draft)} items) to {args.save_verdicts_draft}", file=sys.stderr)

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
