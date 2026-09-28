"""Scope boundary and call-graph blast radius verification engine.

Enforces that modifications stay within allowed file/directory boundaries
and quantifies the downstream impact via AST-based call-graph analysis.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import GitDiffSummary, parse_git_diff
from agent_verifier.graph.call_graph import compute_repo_blast_radius
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus, RiskLevel, ScopeVerificationResult

logger = logging.getLogger("agent_verify.checks.scope")


def _is_path_allowed(file_path: str, allowed_patterns: list[str]) -> bool:
    """Check if a file path matches any allowed pattern or directory prefix.

    Uses normalized, path-component-aware matching.
    Substring matching is strictly prohibited to avoid path prefix vulnerabilities
    (e.g., 'src' must not match 'attacker_src/file.py').
    """
    if not allowed_patterns:
        return True

    norm_file = file_path.replace("\\", "/").strip().strip("'\"")
    if norm_file.startswith("./"):
        norm_file = norm_file[2:]
    norm_file = norm_file.lstrip("/")
    file_parts = [p.lower() for p in norm_file.split("/") if p]
    if not file_parts:
        return True
    norm_file_str = "/".join(file_parts)
    filename = file_parts[-1]

    for pattern in allowed_patterns:
        norm_pat = pattern.replace("\\", "/").strip().strip("'\"")
        if norm_pat.startswith("./"):
            norm_pat = norm_pat[2:]
        norm_pat = norm_pat.lstrip("/")
        has_trailing_slash = norm_pat.endswith("/")
        clean_pat = norm_pat.rstrip("/")
        pat_parts = [p.lower() for p in clean_pat.split("/") if p]
        if not pat_parts:
            continue
        clean_pat_str = "/".join(pat_parts)

        # 1. Exact path match
        if norm_file_str == clean_pat_str:
            return True

        # 2. Glob match on full normalized path or filename
        if fnmatch.fnmatch(norm_file_str, clean_pat_str):
            return True
        if fnmatch.fnmatch(filename, clean_pat_str):
            return True
        try:
            if Path(norm_file_str).match(clean_pat_str):
                return True
        except Exception:
            pass

        # 3. Path-component-aware directory prefix match
        # A pattern without wildcards or with trailing slash matches files inside that directory
        has_wildcards = any(c in clean_pat_str for c in ("*", "?", "["))
        if (
            (has_trailing_slash or not has_wildcards)
            and len(file_parts) > len(pat_parts)
            and file_parts[: len(pat_parts)] == pat_parts
        ):
            return True

        # 4. Component-by-component glob match for directory hierarchies (e.g. src/*/models)
        if (
            len(file_parts) >= len(pat_parts)
            and (len(file_parts) == len(pat_parts) or has_trailing_slash or not has_wildcards)
            and all(
                fnmatch.fnmatch(f_p, p_p) for f_p, p_p in zip(file_parts, pat_parts, strict=False)
            )
        ):
            return True

    return False


class ScopeVerifier(BaseCheck):
    """Verifies that modifications strictly adhere to allowed scope boundaries."""

    @property
    def name(self) -> str:
        return "scope_verifier"

    @property
    def description(self) -> str:
        return "Audits allowed file paths and calculates call-graph blast radius."

    def run(
        self,
        repo_path: Path,
        claim: SessionClaim,
        context: dict[str, Any] | None = None,
    ) -> ScopeVerificationResult:
        context = context or {}
        diff_summary: GitDiffSummary | None = context.get("diff_summary")
        if not diff_summary:
            try:
                diff_summary = parse_git_diff(repo_path, base_ref=context.get("base_ref"))
            except Exception as e:
                return ScopeVerificationResult(
                    status=CheckStatus.FAIL,
                    allowed_boundaries=claim.allowed_paths,
                    blast_radius_risk_level=RiskLevel.UNKNOWN,
                    notes=f"Git diff collection failed during scope check: {e}",
                )

        if diff_summary.error:
            return ScopeVerificationResult(
                status=CheckStatus.FAIL,
                allowed_boundaries=claim.allowed_paths,
                blast_radius_risk_level=RiskLevel.UNKNOWN,
                notes=f"Git diff collection failed: {diff_summary.error}",
            )

        changed_files = diff_summary.changed_file_paths
        allowed_paths = claim.allowed_paths
        forbidden_paths = claim.forbidden_paths

        # 1. Check strictly forbidden boundaries
        forbidden_violations: list[str] = []
        if forbidden_paths:
            for f in changed_files:
                if _is_path_allowed(f, forbidden_paths):
                    forbidden_violations.append(f)

        # 2. Check allowed boundaries
        out_of_scope_files: list[str] = []
        if allowed_paths:
            for f in changed_files:
                if not _is_path_allowed(f, allowed_paths):
                    out_of_scope_files.append(f)

        # Compute blast radius
        skip_graph = context.get("skip_graph_analysis", False)
        if not skip_graph and changed_files:
            try:
                blast = compute_repo_blast_radius(
                    repo_path=repo_path,
                    changed_files=changed_files,
                    allowed_paths=allowed_paths,
                )
                risk_level = blast.risk_level
                impacted_count = blast.impacted_symbols_count
                out_of_scope_symbols = blast.out_of_scope_impacts
                graph_notes = blast.notes
            except Exception as e:
                logger.warning("Call graph analysis failed: %s", e)
                risk_level = RiskLevel.UNKNOWN
                impacted_count = 0
                out_of_scope_symbols = []
                graph_notes = (
                    f"⚠️ UNVERIFIED: Call-graph blast radius analysis failed ({e}). "
                    "Downstream blast radius risk cannot be determined."
                )
        elif skip_graph and changed_files:
            risk_level = RiskLevel.UNKNOWN
            impacted_count = 0
            out_of_scope_symbols = []
            graph_notes = "⚠️ Call graph blast radius analysis was SKIPPED by request. Blast risk is UNKNOWN and unverified."
        else:
            risk_level = RiskLevel.LOW
            impacted_count = 0
            out_of_scope_symbols = []
            graph_notes = "No files modified."

        # Determine status
        status = CheckStatus.PASS
        notes_parts = []

        if forbidden_violations:
            notes_parts.append(
                f"🚨 FORBIDDEN SCOPE BREACH: {len(forbidden_violations)} file(s) modified in strictly forbidden areas: "
                f"{', '.join(forbidden_violations[:3])}."
            )
            status = CheckStatus.FAIL
        elif out_of_scope_files:
            notes_parts.append(
                f"🚨 SCOPE VIOLATION: {len(out_of_scope_files)} file(s) modified outside allowed scope: "
                f"{', '.join(out_of_scope_files[:3])}."
            )
            status = CheckStatus.FAIL
        elif out_of_scope_symbols:
            notes_parts.append(
                f"⚠️ Downstream impact touches {len(out_of_scope_symbols)} out-of-scope symbols."
            )
            status = CheckStatus.WARN

        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            notes_parts.append(f"High blast radius risk ({risk_level.value}).")
            if status != CheckStatus.FAIL:
                status = CheckStatus.WARN
        elif risk_level == RiskLevel.UNKNOWN and changed_files:
            if skip_graph:
                notes_parts.append("Blast radius analysis was SKIPPED; scope risk is UNVERIFIED.")
            else:
                notes_parts.append("Blast radius analysis failed; downstream risk is UNVERIFIED.")
                if status != CheckStatus.FAIL:
                    status = CheckStatus.WARN

        if not notes_parts:
            notes_parts.append(
                f"Scope boundaries verified cleanly. Blast radius: {risk_level.value} "
                f"({impacted_count} impacted symbol(s))."
            )

        notes = " ".join(notes_parts) + (" " + graph_notes if graph_notes else "")

        return ScopeVerificationResult(
            status=status,
            allowed_boundaries=allowed_paths,
            forbidden_scope_violations=forbidden_violations,
            out_of_scope_files=out_of_scope_files,
            out_of_scope_callers_or_callees=out_of_scope_symbols,
            blast_radius_risk_level=risk_level,
            impacted_symbols_count=impacted_count,
            notes=notes.strip(),
        )
