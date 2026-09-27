"""Spec compliance and unrequested drift verification engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import GitDiffSummary, parse_git_diff
from agent_verifier.llm.judge import LLMJudge
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus, SpecComplianceResult


class SpecVerifier(BaseCheck):
    """Audits agent implementation against task specification and detects spec drift."""

    def __init__(self, judge: LLMJudge | None = None):
        self.judge = judge or LLMJudge()

    @property
    def name(self) -> str:
        return "spec_verifier"

    @property
    def description(self) -> str:
        return "Audits whether task requirements were satisfied and flags unrequested changes or scope drift."

    def run(
        self,
        repo_path: Path,
        claim: SessionClaim,
        context: dict[str, Any] | None = None,
    ) -> SpecComplianceResult:
        context = context or {}
        diff_summary: GitDiffSummary | None = context.get("diff_summary")
        if not diff_summary:
            diff_summary = parse_git_diff(repo_path)

        spec_text = claim.spec_text or ""
        spec_path = context.get("spec_path")
        if not spec_text and spec_path:
            p = Path(spec_path)
            if p.exists():
                spec_text = p.read_text(encoding="utf-8", errors="replace")
        elif not spec_text:
            # Check default locations
            for cand in ("spec.md", "task_spec.md", "SPECIFICATION.md", "REQUIREMENTS.md"):
                cp = repo_path / cand
                if cp.exists():
                    spec_text = cp.read_text(encoding="utf-8", errors="replace")
                    break

        if not spec_text.strip():
            return SpecComplianceResult(
                status=CheckStatus.PASS,
                compliance_score=1.0,
                notes="No explicit specification file or text provided. Spec check skipped.",
            )

        # Concatenate diff patches for analysis
        combined_diff_chunks = []
        for file_diff in diff_summary.files.values():
            combined_diff_chunks.append(f"File: {file_diff.path}\n{file_diff.patch}")
        all_diff_text = "\n\n".join(combined_diff_chunks)

        eval_res = self.judge.evaluate_spec_compliance(
            spec_text=spec_text,
            agent_summary=claim.summary,
            diff_text=all_diff_text,
        )

        score = float(eval_res.get("compliance_score", 1.0))
        unmet = eval_res.get("unmet_requirements", [])
        drift = eval_res.get("unrequested_drift", [])
        reasoning = eval_res.get("reasoning", "")

        status = CheckStatus.PASS
        if score < 0.60 or len(unmet) >= 2:
            status = CheckStatus.FAIL
        elif score < 0.85 or len(drift) > 0 or len(unmet) > 0:
            status = CheckStatus.WARN

        notes = (
            f"Compliance score: {int(score * 100)}%. "
            f"Unmet requirements: {len(unmet)}. Unrequested drift: {len(drift)}. {reasoning}"
        )

        return SpecComplianceResult(
            status=status,
            unmet_requirements=unmet,
            unrequested_drift=drift,
            compliance_score=score,
            notes=notes.strip(),
        )
