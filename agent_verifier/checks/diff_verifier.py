"""Diff verification engine comparing actual git diffs against agent claims."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary, parse_git_diff
from agent_verifier.git.worktree_guard import find_ignored_sensitive_files
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
    if (
        ".github/workflows" in norm
        or ".gitlab-ci" in norm
        or ".circleci" in norm
        or "jenkins" in norm
    ):
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


SUSPICIOUS_UNICODE_MAP = {
    "\u200b": "ZWSP",
    "\u200c": "ZWNJ",
    "\u200d": "ZWJ",
    "\ufeff": "BOM",
    "\u2060": "WJ",
    "\u180e": "MVS",
    "\u202a": "LRE",
    "\u202b": "RLE",
    "\u202c": "PDF",
    "\u202d": "LRO",
    "\u202e": "RLO",
    "\u2066": "LRI",
    "\u2067": "RLI",
    "\u2068": "FSI",
    "\u2069": "PDI",
    "\u00ad": "SHY",
}


def _safe_render_line(line: str) -> str:
    """Render invisible or bidi characters visibly and redact secret tokens."""
    # 1. Replace suspicious Unicode characters with safe visible tokens [U+XXXX NAME]
    rendered_chars = []
    for ch in line:
        if ch in SUSPICIOUS_UNICODE_MAP:
            rendered_chars.append(f"[U+{ord(ch):04X} {SUSPICIOUS_UNICODE_MAP[ch]}]")
        elif "\U000e0001" <= ch <= "\U000e007f":
            rendered_chars.append(f"[U+{ord(ch):04X} TAG]")
        elif ord(ch) < 32 and ch not in ("\t", "\n", "\r"):
            rendered_chars.append(f"[U+{ord(ch):04X} CTRL]")
        else:
            rendered_chars.append(ch)
    rendered = "".join(rendered_chars)

    # 2. Redact common secret assignments to avoid leaking them in report
    rendered = re.sub(
        r"(?i)\b(api_key|token|secret|password|bearer|auth)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{16,})['\"]?",
        r"\1='[REDACTED_SECRET]'",
        rendered,
    )
    return rendered


def scan_diff_for_deceptions_and_security(
    file_diff: FileDiff,
) -> tuple[list[str], list[str], list[str]]:
    """Scan added lines in a diff for deceptive stubs/TODOs, security injections, and prompt injections."""
    stubs: list[str] = []
    security: list[str] = []
    prompt_injections: list[str] = []

    norm_path = file_diff.path.lower().replace("\\", "/")
    is_test = "test" in norm_path or "spec" in norm_path

    for line_no, content in file_diff.added_lines:
        stripped = content.strip()
        if not stripped:
            continue

        # 0. Detect Suspicious / Invisible / Bidi Unicode characters
        has_suspicious_unicode = any(ch in SUSPICIOUS_UNICODE_MAP for ch in stripped) or any(
            "\U000e0001" <= ch <= "\U000e007f" for ch in stripped
        )
        if has_suspicious_unicode:
            rendered = _safe_render_line(stripped)
            prompt_injections.append(
                f"{file_diff.path}:{line_no} - [UNICODE_OBFUSCATION] Invisible/Bidi Unicode characters detected: '{rendered}'"
            )

        # Normalize line by removing zero-width chars to detect obfuscated prompt injections
        normalized_text = stripped
        for zw in SUSPICIOUS_UNICODE_MAP:
            normalized_text = normalized_text.replace(zw, "")

        # Check for Prompt Injection Patterns
        pi_patterns = [
            (
                r"(?i)\bignore\s+(?:(?:all|any|previous|prior|above|other)\s+)*instructions\b",
                "Instruction override",
            ),
            (
                r"(?i)\bdisregard\s+(?:(?:all|any|previous|prior|above|other)\s+)*(?:instructions|prompt)\b",
                "Instruction override",
            ),
            (r"(?i)\bnew\s+system\s+prompt\b", "System prompt injection"),
            (r"(?i)\bsystem\s+prompt\s*:\s*", "System prompt injection"),
            (r"(?i)\bdeveloper\s+mode\s*:\s*active\b", "Developer mode jailbreak"),
            (
                r"(?i)\b(?:you\s+are\s+now|switch\s+to)\s+(?:in\s+)?maintenance\s+mode\b",
                "Maintenance mode override",
            ),
            (
                r"(?i)\balways\s+(?:return|report|output|mark\s+as)\s+(?:verified|clean|passing)\b",
                "Verdict manipulation",
            ),
            (r"<\|im_start\|>|<\|im_end\|>", "ChatML delimiter injection"),
            (r"\[INST\]|\[/INST\]", "Llama instruction delimiter injection"),
            (r"<<SYS>>|<</SYS>>", "System tag delimiter injection"),
        ]

        for pat, label in pi_patterns:
            if re.search(pat, normalized_text):
                rendered = _safe_render_line(stripped)
                prompt_injections.append(
                    f"{file_diff.path}:{line_no} - [PROMPT_INJECTION] {label}: '{rendered}'"
                )
                break

        # 1. Deceptive Stubs & Placeholders (Agent claiming functionality, but leaving stubs)
        if not is_test:
            if re.search(r"^\s*(?:#|//|/\*)\s*(?:TODO|FIXME|XXX|HACK)\b", stripped, re.IGNORECASE):
                stubs.append(f"{file_diff.path}:{line_no} - Placeholder comment: '{stripped}'")
            elif re.search(r"^\s*raise\s+NotImplementedError\b", stripped) or re.search(
                r"^\s*throw\s+new\s+Error\s*\(\s*['\"](?:TODO|Not implemented|Coming soon)",
                stripped,
                re.IGNORECASE,
            ):
                stubs.append(
                    f"{file_diff.path}:{line_no} - Unimplemented stub exception: '{stripped}'"
                )
            elif re.search(r"\b(?:todo!|unimplemented!)\(", stripped):
                stubs.append(f"{file_diff.path}:{line_no} - Unimplemented stub macro: '{stripped}'")
            elif re.search(
                r"^\s*return\s*\{\s*['\"]status['\"]\s*:\s*['\"](?:dummy|mock|fake|stub)['\"]\s*\}",
                stripped,
                re.IGNORECASE,
            ):
                stubs.append(
                    f"{file_diff.path}:{line_no} - Fake mock dictionary return: '{stripped}'"
                )

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
            security.append(
                f"{file_diff.path}:{line_no} - OS system command execution: '{stripped}'"
            )
        elif re.search(r"\bverify\s*=\s*False\b", stripped):
            security.append(
                f"{file_diff.path}:{line_no} - Disabled SSL certificate verification (verify=False): '{stripped}'"
            )
        elif re.search(r"-----BEGIN (?:RSA )?PRIVATE KEY-----", stripped):
            security.append(f"{file_diff.path}:{line_no} - Hardcoded private key in diff")

    return stubs, security, prompt_injections


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
        tolerance = context.get("max_unclaimed_files_tolerance", 2)

        diff_summary: GitDiffSummary | None = context.get("diff_summary")
        if not diff_summary:
            try:
                diff_summary = parse_git_diff(
                    repo_path=repo_path,
                    base_ref=base_ref,
                )
            except Exception as e:
                return DiffVerificationResult(
                    status=CheckStatus.FAIL,
                    notes=f"Git diff collection failed: {e}",
                    discrepancies=[f"Git diff collection failed: {e}"],
                )

        if diff_summary.error:
            return DiffVerificationResult(
                status=CheckStatus.FAIL,
                notes=f"Git diff collection failed: {diff_summary.error}",
                discrepancies=[f"Git diff collection failed: {diff_summary.error}"],
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

        # Surface ignored sensitive files in the repository
        ignored_sensitive = find_ignored_sensitive_files(repo_path)

        # Collect stubs, security flags, and prompt injections across all changed files
        all_stubs: list[str] = []
        all_security: list[str] = []
        all_prompt_injections: list[str] = []
        for fd in diff_summary.files.values():
            stubs, sec, pi = scan_diff_for_deceptions_and_security(fd)
            all_stubs.extend(stubs)
            all_security.extend(sec)
            all_prompt_injections.extend(pi)

        discrepancies: list[str] = []
        if all_security:
            for s in all_security:
                discrepancies.append(f"🚨 CRITICAL SECURITY INJECTION: {s}")
        if all_prompt_injections:
            for pi in all_prompt_injections:
                discrepancies.append(f"🚨 PROMPT INJECTION / SUSPICIOUS UNICODE: {pi}")
        if all_stubs:
            for st in all_stubs:
                discrepancies.append(f"🚨 DECEPTIVE STUB DETECTED: {st}")
        if sensitive_unclaimed:
            discrepancies.append(
                f"🚨 CRITICAL STEALTH MODIFICATION: {len(sensitive_unclaimed)} sensitive file(s) modified undeclared: {', '.join(sensitive_unclaimed)}"
            )
        if ignored_sensitive:
            ign_paths = [f"{item['path']} ({item['sha256']})" for item in ignored_sensitive]
            discrepancies.append(
                f"⚠️ Ignored sensitive file(s) present without Git baseline: {', '.join(ign_paths)}. "
                "Ignored files have no Git baseline and cannot be audited from git history."
            )
        if unclaimed:
            discrepancies.append(
                f"Undeclared modifications in {len(unclaimed)} file(s): {', '.join(unclaimed[:5])}"
            )
        if fabricated:
            discrepancies.append(
                f"Fabricated claims for {len(fabricated)} file(s) not actually modified: {', '.join(fabricated[:5])}"
            )

        # Status determination policy:
        # - Any sensitive unclaimed changes: FAIL
        # - Unclaimed changes > max_unclaimed_files_tolerance: FAIL
        # - 1..tolerance unclaimed changes (non-sensitive): WARN (never VERIFIED)
        # - Fabricated claims > 1: FAIL; fabricated claims == 1: WARN
        # - Deceptive stubs > 1: FAIL; stubs == 1: WARN
        # - Critical security injection > 0 or prompt injection > 0: FAIL
        if (
            sensitive_unclaimed
            or len(unclaimed) > tolerance
            or len(fabricated) > 1
            or len(all_security) > 0
            or len(all_prompt_injections) > 0
            or len(all_stubs) > 1
        ):
            status = CheckStatus.FAIL
            fail_notes = []
            if all_security:
                fail_notes.append(f"{len(all_security)} dangerous code injection(s)")
            if all_prompt_injections:
                fail_notes.append(
                    f"{len(all_prompt_injections)} prompt injection/Unicode pattern(s)"
                )
            if len(all_stubs) > 1:
                fail_notes.append(f"{len(all_stubs)} deceptive placeholder stub(s)")
            if sensitive_unclaimed:
                fail_notes.append(f"{len(sensitive_unclaimed)} undeclared sensitive file(s)")
            if len(unclaimed) > tolerance:
                fail_notes.append(
                    f"{len(unclaimed)} undeclared file(s) modified (tolerance: {tolerance})"
                )
            if len(fabricated) > 1:
                fail_notes.append(f"{len(fabricated)} claimed files untouched")
            notes = f"Diff mismatch: {', '.join(fail_notes)}."
        elif (
            len(unclaimed) > 0
            or len(fabricated) == 1
            or len(all_stubs) == 1
            or len(ignored_sensitive) > 0
        ):
            status = CheckStatus.WARN
            warn_parts = []
            if len(unclaimed) > 0:
                warn_parts.append(
                    f"{len(unclaimed)} undeclared file(s) modified (within tolerance {tolerance})"
                )
            if len(fabricated) == 1:
                warn_parts.append(f"1 claimed file untouched ({fabricated[0]})")
            if len(all_stubs) == 1:
                warn_parts.append(all_stubs[0])
            if ignored_sensitive:
                warn_parts.append(
                    f"{len(ignored_sensitive)} ignored sensitive file(s) present without Git baseline"
                )
            notes = f"Diff warning: {'; '.join(warn_parts)}."
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
            prompt_injection_flags=all_prompt_injections,
            ignored_sensitive_files=ignored_sensitive,
            lines_added=diff_summary.total_lines_added,
            lines_deleted=diff_summary.total_lines_deleted,
            notes=notes,
            discrepancies=discrepancies,
        )
