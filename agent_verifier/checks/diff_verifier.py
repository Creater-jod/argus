"""Diff verification engine comparing actual git diffs against agent claims."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import GitDiffSummary, parse_git_diff
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus, DiffVerificationResult


def _normalize(p: str) -> str:
    """Normalize file path for consistent comparison."""
    s = p.replace("\\", "/").strip()
    if s.startswith("./"):
        s = s[2:]
    return s.lower()


def _is_path_match(claimed: str, actual: str) -> bool:
    """Check if claimed path matches actual path with relative tolerance."""
    c = _normalize(claimed)
    a = _normalize(actual)
    if c == a:
        return True
    return bool(a.endswith("/" + c) or c.endswith("/" + a))


class DiffVerifier(BaseCheck):
    """Audits agent modification claims against ground-truth git diffs."""

    @property
    def name(self) -> str:
        return "diff_verifier"

    @property
    def description(self) -> str:
        return (
            "Compares actual git diff against agent claims to detect phantom or undeclared changes."
        )

    def run(
        self,
        repo_path: Path,
        claim: SessionClaim,
        context: dict[str, Any] | None = None,
    ) -> DiffVerificationResult:
        context = context or {}
        base_ref = context.get("base_ref")

        diff_summary: GitDiffSummary = context.get("diff_summary") or parse_git_diff(
            repo_path=repo_path,
            base_ref=base_ref,
        )

        actual_files = diff_summary.changed_file_paths
        claimed_files = claim.claimed_files

        unclaimed: list[str] = []
        for actual in actual_files:
            if not any(_is_path_match(cf, actual) for cf in claimed_files):
                unclaimed.append(actual)

        fabricated: list[str] = []
        for cf in claimed_files:
            if not any(_is_path_match(cf, actual) for actual in actual_files):
                fabricated.append(cf)

        discrepancies: list[str] = []
        if unclaimed:
            discrepancies.append(
                f"Undeclared modifications in {len(unclaimed)} file(s): {', '.join(unclaimed[:5])}"
            )
        if fabricated:
            discrepancies.append(
                f"Fabricated claims for {len(fabricated)} file(s) not actually modified: {', '.join(fabricated[:5])}"
            )

        # Status determination
        if len(unclaimed) > 0 or len(fabricated) > 1:
            status = CheckStatus.FAIL
            notes = (
                f"Diff mismatch: {len(unclaimed)} undeclared files modified, "
                f"{len(fabricated)} claimed files untouched."
            )
        elif len(fabricated) == 1:
            status = CheckStatus.WARN
            notes = f"Minor discrepancy: 1 claimed file had no detected changes ({fabricated[0]})."
        else:
            status = CheckStatus.PASS
            notes = (
                f"Diff matches claims. {len(actual_files)} file(s) verified "
                f"(+{diff_summary.total_lines_added}/-{diff_summary.total_lines_deleted} lines)."
            )

        return DiffVerificationResult(
            status=status,
            claimed_files=claimed_files,
            actual_changed_files=actual_files,
            unclaimed_changes=unclaimed,
            fabricated_claims=fabricated,
            lines_added=diff_summary.total_lines_added,
            lines_deleted=diff_summary.total_lines_deleted,
            notes=notes,
            discrepancies=discrepancies,
        )
