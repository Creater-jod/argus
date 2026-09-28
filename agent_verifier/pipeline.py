"""Verification pipeline orchestrating all checks to produce a TrustReport.

The pipeline is the central coordinator — it runs each verification check
independently, catches per-check failures to produce degraded (but still useful)
reports, and aggregates results into a final deterministic verdict.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from agent_verifier.checks.diff_verifier import DiffVerifier
from agent_verifier.checks.scope_verifier import ScopeVerifier
from agent_verifier.checks.spec_verifier import SpecVerifier
from agent_verifier.checks.test_verifier import TestVerifier
from agent_verifier.config import VerifierConfig, default_config
from agent_verifier.git.diff_parser import GitDiffSummary, parse_git_diff
from agent_verifier.llm.judge import LLMJudge
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import (
    CheckStatus,
    DiffVerificationResult,
    ScopeVerificationResult,
    SpecComplianceResult,
    TestVerificationResult,
    TrustReport,
    VerdictThresholds,
)

logger = logging.getLogger("agent_verify.pipeline")


class VerificationPipeline:
    """Coordinates execution of all agent verification checks.

    Each check runs in isolation — if one check raises an unexpected error,
    the pipeline marks that pillar as FAIL with an error note and continues
    running the remaining checks to produce a partial but actionable report.
    """

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
        interactive: bool = False,
        confirm_func: Any = None,
    ) -> TrustReport:
        """Run all verification checks on target repository and produce a TrustReport.

        Args:
            repo_path: Path to the git repository to audit.
            claim: Agent's self-reported session claim. Uses empty claim if None.
            base_ref: Git ref to diff against (e.g. 'main', 'HEAD~1').
            spec_path: Path to task specification markdown file.
            skip_tests: Bypass test suite execution.
            skip_graph: Skip call-graph blast-radius analysis.
            interactive: Prompt the user to confirm/reject discrepancies.
            confirm_func: Custom confirmation function for non-TTY usage.

        Returns:
            A fully populated TrustReport with verdict and confidence score.
        """
        start_time = time.monotonic()
        path = Path(repo_path).resolve()
        claim = claim or SessionClaim()

        logger.info("Starting verification for %s (base_ref=%s)", path, base_ref)

        # Extract git diff summary (fail closed on invalid base_ref or git errors)
        try:
            diff_summary = parse_git_diff(path, base_ref=base_ref)
        except Exception as e:
            logger.error("Git diff extraction failed: %s", e)
            diff_summary = GitDiffSummary(
                repo_path=str(path),
                base_ref=base_ref,
                error=str(e),
            )

        context: dict[str, Any] = {
            "diff_summary": diff_summary,
            "base_ref": base_ref,
            "spec_path": spec_path,
            "skip_test_execution": skip_tests,
            "skip_graph_analysis": skip_graph,
            "test_timeout": self.config.test_timeout_seconds,
            "max_unclaimed_files_tolerance": self.config.max_unclaimed_files_tolerance,
            "allow_host_execution": self.config.allow_host_execution,
            "sandbox_mode": self.config.sandbox_mode,
        }

        # Initialize TrustReport
        report = TrustReport(
            repo_path=str(path),
            target_ref=base_ref or "working-tree",
        )

        # 1. Diff Verification
        report.diff_verification = self._run_check(
            "diff",
            lambda: self.diff_verifier.run(repo_path=path, claim=claim, context=context),
            DiffVerificationResult,
        )

        # 2. Test Verification & Anti-Gaming Scan
        report.test_verification = self._run_check(
            "test",
            lambda: self.test_verifier.run(repo_path=path, claim=claim, context=context),
            TestVerificationResult,
        )

        # Post-test state recheck: if tests mutated tracked, untracked, or ignored files,
        # re-evaluate diff verification against final worktree state and fail
        if report.test_verification.worktree_mutations:
            try:
                final_diff_summary = parse_git_diff(repo_path=path, base_ref=base_ref)
                final_context = dict(context)
                final_context["diff_summary"] = final_diff_summary
                final_diff = self.diff_verifier.run(
                    repo_path=path, claim=claim, context=final_context
                )
                final_diff.status = CheckStatus.FAIL
                for m in report.test_verification.worktree_mutations:
                    if m not in final_diff.discrepancies:
                        final_diff.discrepancies.append(f"🚨 TEST-TIME WORKTREE MUTATION: {m}")
                report.diff_verification = final_diff
            except Exception as e:
                logger.warning("Failed re-running diff check after test-time mutation: %s", e)
                report.diff_verification.status = CheckStatus.FAIL

        # 3. Scope & Blast Radius Verification
        report.scope_verification = self._run_check(
            "scope",
            lambda: self.scope_verifier.run(repo_path=path, claim=claim, context=context),
            ScopeVerificationResult,
        )

        # 4. Spec Compliance Verification
        report.spec_compliance = self._run_check(
            "spec",
            lambda: self.spec_verifier.run(repo_path=path, claim=claim, context=context),
            SpecComplianceResult,
        )

        # Aggregate Verdict & Confidence Score using configured thresholds
        thresholds = VerdictThresholds(
            max_unclaimed_files_fail=self.config.max_unclaimed_files_tolerance,
            min_compliance_score_suspicious=self.config.min_compliance_score,
        )
        report.compute_verdict(thresholds=thresholds)

        # Optional Interactive Discrepancy Questioning
        if interactive:
            from agent_verifier.interview.interactive_verifier import InteractiveVerifierSession

            session = InteractiveVerifierSession(confirm_func=confirm_func)
            report = session.review_discrepancies(report)

        report.duration_seconds = round(time.monotonic() - start_time, 2)

        # High-level summary
        verdict_str = report.verdict.value
        report.summary = (
            f"Verification completed in {report.duration_seconds}s with verdict {verdict_str} "
            f"(confidence {int(report.confidence_score * 100)}%). "
            f"{len(report.diff_verification.actual_changed_files)} file(s) changed, "
            f"{report.test_verification.tests_passed} test(s) passed."
        )

        logger.info(
            "Verification complete: %s (confidence %d%%) in %.2fs",
            verdict_str,
            int(report.confidence_score * 100),
            report.duration_seconds,
        )

        return report

    @staticmethod
    def _run_check(name: str, func, default_cls):
        """Run a single verification check with error isolation.

        If the check raises, returns a degraded result with FAIL status and
        an error note — the pipeline continues running remaining checks.
        """
        try:
            result = func()
            logger.debug("Check '%s' completed: status=%s", name, result.status.value)
            return result
        except Exception as e:
            logger.error("Check '%s' raised an error: %s", name, e, exc_info=True)
            return default_cls(
                status=CheckStatus.FAIL,
                notes=f"Internal error during {name} verification: {e}",
            )
