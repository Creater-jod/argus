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


def _is_sensitive_file(path: str) -> bool:
    """Identify if a file path belongs to security, CI/CD, credential, dependency, or infra domains."""
    norm = path.replace("\\", "/").lower()
    if ".github/workflows" in norm or ".gitlab-ci" in norm or ".circleci" in norm or "jenkins" in norm:
        return True
    if norm.endswith(".env") or "/.env" in norm or ".env." in norm:
        return True
    if any(k in norm for k in ("secret", "credential", "id_rsa", "token", ".pem", ".key")):
        return True
    filename = Path(norm).name
    if filename in (
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "requirements.txt",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    ):
        return True
    return bool("migrations/" in norm or "alembic/" in norm)


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

        sensitive_unclaimed = [f for f in unclaimed if _is_sensitive_file(f)]

        discrepancies: list[str] = []
        if sensitive_unclaimed:
            discrepancies.append(
                f"🚨 CRITICAL STEALTH MODIFICATION: {len(sensitive_unclaimed)} sensitive file(s) modified undeclared: {', '.join(sensitive_unclaimed)}"
            )
        if unclaimed:
            discrepancies.append(
                f"Undeclared modifications in {len(unclaimed)} file(s): {', '.join(unclaimed[:5])}"
            )
        if fabricated:
            discrepancies.append(
                f"Fabricated claims for {len(fabricated)} file(s) not actually modified: {', '.join(fabricated[:5])}"
            )

        # Status determination
        if sensitive_unclaimed or len(unclaimed) > 0 or len(fabricated) > 1:
            status = CheckStatus.FAIL
            fail_notes = []
            if sensitive_unclaimed:
                fail_notes.append(f"{len(sensitive_unclaimed)} undeclared sensitive file(s)")
            if unclaimed:
                fail_notes.append(f"{len(unclaimed)} undeclared files modified")
            if len(fabricated) > 1:
                fail_notes.append(f"{len(fabricated)} claimed files untouched")
            notes = f"Diff mismatch: {', '.join(fail_notes)}."
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
            sensitive_unclaimed_changes=sensitive_unclaimed,
            fabricated_claims=fabricated,
            lines_added=diff_summary.total_lines_added,
            lines_deleted=diff_summary.total_lines_deleted,
            notes=notes,
            discrepancies=discrepancies,
        )
