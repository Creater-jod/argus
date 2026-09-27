"""Scope boundary and call-graph blast radius verification engine."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import GitDiffSummary, parse_git_diff
from agent_verifier.graph.call_graph import compute_repo_blast_radius
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus, ScopeVerificationResult


def _is_path_allowed(file_path: str, allowed_patterns: list[str]) -> bool:
    """Check if a file path matches any allowed pattern or directory prefix."""
    if not allowed_patterns:
        return True

    norm_file = file_path.replace("\\", "/").strip().lower()
    if norm_file.startswith("./"):
        norm_file = norm_file[2:]

    for pattern in allowed_patterns:
        norm_pat = pattern.replace("\\", "/").strip().lower()
        if norm_pat.startswith("./"):
            norm_pat = norm_pat[2:]

        # Direct match or glob match
        if fnmatch.fnmatch(norm_file, norm_pat):
            return True
        # Directory prefix match (e.g. "src/" or "agent_verifier/")
        if (
            norm_pat.endswith("/")
            and norm_file.startswith(norm_pat)
            or not norm_pat.endswith("/")
            and norm_file.startswith(norm_pat + "/")
        ):
            return True
        if norm_pat in norm_file:
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
            diff_summary = parse_git_diff(repo_path, base_ref=context.get("base_ref"))

        changed_files = diff_summary.changed_file_paths
        allowed_paths = claim.allowed_paths

        out_of_scope_files: list[str] = []
        if allowed_paths:
            for f in changed_files:
                if not _is_path_allowed(f, allowed_paths):
                    out_of_scope_files.append(f)

        # Compute blast radius
        skip_graph = context.get("skip_graph_analysis", False)
        if not skip_graph and changed_files:
            blast = compute_repo_blast_radius(
                repo_path=repo_path,
                changed_files=changed_files,
                allowed_paths=allowed_paths,
            )
            risk_level = blast.risk_level
            impacted_count = blast.impacted_symbols_count
            out_of_scope_symbols = blast.out_of_scope_impacts
            graph_notes = blast.notes
        else:
            risk_level = "LOW"
            impacted_count = 0
            out_of_scope_symbols = []
            graph_notes = "Call graph analysis skipped or no files modified."

        # Determine status
        status = CheckStatus.PASS
        notes_parts = []

        if out_of_scope_files:
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

        if risk_level in ("HIGH", "CRITICAL"):
            notes_parts.append(f"High blast radius risk ({risk_level}).")
            if status != CheckStatus.FAIL:
                status = CheckStatus.WARN

        if not notes_parts:
            notes_parts.append(
                f"Scope boundaries verified cleanly. Blast radius: {risk_level} "
                f"({impacted_count} impacted symbol(s))."
            )

        notes = " ".join(notes_parts) + (" " + graph_notes if graph_notes else "")

        return ScopeVerificationResult(
            status=status,
            allowed_boundaries=allowed_paths,
            out_of_scope_files=out_of_scope_files,
            out_of_scope_callers_or_callees=out_of_scope_symbols,
            blast_radius_risk_level=risk_level,
            impacted_symbols_count=impacted_count,
            notes=notes.strip(),
        )
