"""Diff verification engine comparing actual git diffs against agent claims."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary, parse_git_diff
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


def scan_diff_for_deceptions_and_security(
    file_diff: FileDiff,
) -> tuple[list[str], list[str]]:
    """Scan added lines in a diff for deceptive stubs/TODOs and dangerous security injections."""
    stubs: list[str] = []
    security: list[str] = []

    norm_path = file_diff.path.lower().replace("\\", "/")
    is_test = "test" in norm_path or "spec" in norm_path

    for line_no, content in file_diff.added_lines:
        stripped = content.strip()
        if not stripped:
            continue

        # 1. Deceptive Stubs & Placeholders (Agent claiming functionality, but leaving stubs)
        if not is_test:
            if re.search(r"^\s*(?:#|//|/\*)\s*(?:TODO|FIXME|XXX|HACK)\b", stripped, re.IGNORECASE):
                stubs.append(f"{file_diff.path}:{line_no} - Placeholder comment: '{stripped}'")
            elif re.search(r"^\s*raise\s+NotImplementedError\b", stripped) or re.search(
                r"^\s*throw\s+new\s+Error\s*\(\s*['\"](?:TODO|Not implemented|Coming soon)",
                stripped,
                re.IGNORECASE,
            ):
                stubs.append(f"{file_diff.path}:{line_no} - Unimplemented stub exception: '{stripped}'")
            elif re.search(r"\b(?:todo!|unimplemented!)\(", stripped):
                stubs.append(f"{file_diff.path}:{line_no} - Unimplemented stub macro: '{stripped}'")
            elif re.search(
                r"^\s*return\s*\{\s*['\"]status['\"]\s*:\s*['\"](?:dummy|mock|fake|stub)['\"]\s*\}",
                stripped,
                re.IGNORECASE,
            ):
                stubs.append(f"{file_diff.path}:{line_no} - Fake mock dictionary return: '{stripped}'")

        # 2. Dangerous Security Injections & Smuggled Operations
        if re.search(r"\b(?:eval|exec)\s*\(", stripped):
            security.append(f"{file_diff.path}:{line_no} - Dynamic code execution: '{stripped}'")
        elif re.search(r"\b__import__\s*\(", stripped):
            security.append(f"{file_diff.path}:{line_no} - Obfuscated module import: '{stripped}'")
        elif re.search(
            r"\bsubprocess\.(?:Popen|run|call|check_output)\s*\(.*shell\s*=\s*True", stripped
        ):
            security.append(
                f"{file_diff.path}:{line_no} - Subprocess shell execution (shell=True): '{stripped}'"
            )
        elif re.search(r"\bos\.system\s*\(", stripped):
            security.append(f"{file_diff.path}:{line_no} - OS system command execution: '{stripped}'")
        elif re.search(r"\bverify\s*=\s*False\b", stripped):
            security.append(
                f"{file_diff.path}:{line_no} - Disabled SSL certificate verification (verify=False): '{stripped}'"
            )
        elif re.search(r"-----BEGIN (?:RSA )?PRIVATE KEY-----", stripped):
            security.append(f"{file_diff.path}:{line_no} - Hardcoded private key in diff")

    return stubs, security


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

        # Collect stubs and security flags across all changed files
        all_stubs: list[str] = []
        all_security: list[str] = []
        for fd in diff_summary.files.values():
            stubs, sec = scan_diff_for_deceptions_and_security(fd)
            all_stubs.extend(stubs)
            all_security.extend(sec)

        discrepancies: list[str] = []
        if all_security:
            for s in all_security:
                discrepancies.append(f"🚨 CRITICAL SECURITY INJECTION: {s}")
        if all_stubs:
            for st in all_stubs:
                discrepancies.append(f"🚨 DECEPTIVE STUB DETECTED: {st}")
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
        if (
            sensitive_unclaimed
            or len(unclaimed) > 0
            or len(fabricated) > 1
            or len(all_security) > 0
            or len(all_stubs) > 1
        ):
            status = CheckStatus.FAIL
            fail_notes = []
            if all_security:
                fail_notes.append(f"{len(all_security)} dangerous code injection(s)")
            if len(all_stubs) > 1:
                fail_notes.append(f"{len(all_stubs)} deceptive placeholder stub(s)")
            if sensitive_unclaimed:
                fail_notes.append(f"{len(sensitive_unclaimed)} undeclared sensitive file(s)")
            if unclaimed:
                fail_notes.append(f"{len(unclaimed)} undeclared files modified")
            if len(fabricated) > 1:
                fail_notes.append(f"{len(fabricated)} claimed files untouched")
            notes = f"Diff mismatch: {', '.join(fail_notes)}."
        elif len(fabricated) == 1 or len(all_stubs) == 1:
            status = CheckStatus.WARN
            notes = (
                f"Minor discrepancy: {all_stubs[0] if all_stubs else f'1 claimed file had no detected changes ({fabricated[0]})'}."
            )
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
            deceptive_stubs=all_stubs,
            security_flags=all_security,
            lines_added=diff_summary.total_lines_added,
            lines_deleted=diff_summary.total_lines_deleted,
            notes=notes,
            discrepancies=discrepancies,
        )
