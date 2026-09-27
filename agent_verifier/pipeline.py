"""Verification pipeline orchestrating all checks to produce a TrustReport."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent_verifier.checks.diff_verifier import DiffVerifier
from agent_verifier.checks.scope_verifier import ScopeVerifier
from agent_verifier.checks.spec_verifier import SpecVerifier
from agent_verifier.checks.test_verifier import TestVerifier
from agent_verifier.config import VerifierConfig, default_config
from agent_verifier.git.diff_parser import parse_git_diff
from agent_verifier.llm.judge import LLMJudge
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import TrustReport


class VerificationPipeline:
    """Coordinates execution of all agent verification checks."""

    def __init__(
        self,
        config: VerifierConfig | None = None,
        judge: LLMJudge | None = None,
    ):
        self.config = config or default_config
        self.judge = judge or LLMJudge(
            provider=self.config.provider,
            model_name=self.config.model_name,
        )
        self.diff_verifier = DiffVerifier()
        self.test_verifier = TestVerifier()
        self.scope_verifier = ScopeVerifier()
        self.spec_verifier = SpecVerifier(judge=self.judge)

    def run(
        self,
        repo_path: Path | str,
        claim: SessionClaim | None = None,
        base_ref: str | None = None,
        spec_path: str | Path | None = None,
        skip_tests: bool = False,
        skip_graph: bool = False,
    ) -> TrustReport:
        """Run all verification checks on target repository and produce a TrustReport."""
        start_time = time.monotonic()
        path = Path(repo_path).resolve()
        claim = claim or SessionClaim()

        # Extract git diff summary
        diff_summary = parse_git_diff(path, base_ref=base_ref)

        context: dict[str, Any] = {
            "diff_summary": diff_summary,
            "base_ref": base_ref,
            "spec_path": spec_path,
            "skip_test_execution": skip_tests,
            "skip_graph_analysis": skip_graph,
            "test_timeout": self.config.test_timeout_seconds,
        }

        # Initialize TrustReport
        report = TrustReport(
            repo_path=str(path),
            target_ref=base_ref or "working-tree",
        )

        # 1. Diff Verification
        report.diff_verification = self.diff_verifier.run(
            repo_path=path,
            claim=claim,
            context=context,
        )

        # 2. Test Verification & Anti-Gaming Scan
        report.test_verification = self.test_verifier.run(
            repo_path=path,
            claim=claim,
            context=context,
        )

        # 3. Scope & Blast Radius Verification
        report.scope_verification = self.scope_verifier.run(
            repo_path=path,
            claim=claim,
            context=context,
        )

        # 4. Spec Compliance Verification
        report.spec_compliance = self.spec_verifier.run(
            repo_path=path,
            claim=claim,
            context=context,
        )

        # Aggregate Verdict & Confidence Score
        report.compute_verdict()
        report.duration_seconds = round(time.monotonic() - start_time, 2)

        # High-level summary
        verdict_str = report.verdict.value
        report.summary = (
            f"Verification completed in {report.duration_seconds}s with verdict {verdict_str} "
            f"(confidence {int(report.confidence_score * 100)}%). "
            f"{len(report.diff_verification.actual_changed_files)} file(s) changed, "
            f"{report.test_verification.tests_passed} test(s) passed."
        )

        return report
