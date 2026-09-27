"""Pydantic schemas for the Trust Report and verification sub-results.

Defines the data contracts for all verification outputs:
- Per-check results (diff, test, scope, spec)
- Aggregate TrustReport with configurable verdict thresholds
- UserClarification audit trail records
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger("agent_verify.models")


class Verdict(str, Enum):
    """Overall verdict of the AI agent verification."""

    VERIFIED = "VERIFIED"
    SUSPICIOUS = "SUSPICIOUS"
    FAILED = "FAILED"


class CheckStatus(str, Enum):
    """Execution status of an individual verification check."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class RiskLevel(str, Enum):
    """Blast radius risk classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DiffVerificationResult(BaseModel):
    """Result of diff verification comparing git reality against agent claims."""

    status: CheckStatus = CheckStatus.PASS
    claimed_files: list[str] = Field(
        default_factory=list, description="Files the agent claimed to modify"
    )
    actual_changed_files: list[str] = Field(
        default_factory=list, description="Files actually changed in git"
    )
    unclaimed_changes: list[str] = Field(
        default_factory=list, description="Files changed in git that the agent never mentioned"
    )
    sensitive_unclaimed_changes: list[str] = Field(
        default_factory=list,
        description="High-risk or sensitive files (secrets, CI, configs) modified without declaration",
    )
    fabricated_claims: list[str] = Field(
        default_factory=list,
        description="Files claimed by the agent as modified but untouched in git",
    )
    lines_added: int = Field(default=0, description="Total lines added")
    lines_deleted: int = Field(default=0, description="Total lines deleted")
    notes: str = Field(default="", description="Detailed commentary or judge notes")
    discrepancies: list[str] = Field(default_factory=list, description="Key discrepancies flagged")


class TestVerificationResult(BaseModel):
    """Result of independent test execution and anti-gaming assertion scans."""

    __test__ = False
    status: CheckStatus = CheckStatus.PASS
    runner: str = Field(default="pytest", description="Test runner used (e.g. pytest, npm, custom)")
    exit_code: int = Field(default=0, description="Process exit code of test runner")
    tests_run: int = Field(default=0, description="Number of tests executed")
    tests_passed: int = Field(default=0, description="Number of passing tests")
    tests_failed: int = Field(default=0, description="Number of failing tests")
    tests_skipped: int = Field(default=0, description="Number of skipped tests")
    weakened_assertions_detected: list[str] = Field(
        default_factory=list,
        description="List of detected assertion deletions, exception swallows, or weakened test bodies",
    )
    trivially_passing_tests_flagged: list[str] = Field(
        default_factory=list,
        description="Newly added or modified tests that pass without valid assertions (tautological)",
    )
    claim_discrepancies: list[str] = Field(
        default_factory=list,
        description="Discrepancies between agent claimed test statistics and independent execution",
    )
    output_snippet: str | None = Field(
        default=None, description="Truncated stdout/stderr of test run"
    )
    notes: str = Field(default="", description="Summary of test run observations")


class ScopeVerificationResult(BaseModel):
    """Result of path boundary checks and call-graph blast radius analysis."""

    status: CheckStatus = CheckStatus.PASS
    allowed_boundaries: list[str] = Field(
        default_factory=list, description="Permitted directory or file glob patterns"
    )
    forbidden_scope_violations: list[str] = Field(
        default_factory=list,
        description="Files modified that violate strictly forbidden scope boundaries",
    )
    out_of_scope_files: list[str] = Field(
        default_factory=list, description="Changed files located outside allowed boundaries"
    )
    out_of_scope_callers_or_callees: list[str] = Field(
        default_factory=list,
        description="Functions/methods outside scope boundary directly touched or impacted",
    )
    blast_radius_risk_level: RiskLevel = Field(
        default=RiskLevel.LOW, description="Risk tier: LOW, MEDIUM, HIGH, CRITICAL"
    )
    impacted_symbols_count: int = Field(
        default=0, description="Total distinct symbols impacted along call paths"
    )
    notes: str = Field(default="", description="Boundary violation notes")


class SpecComplianceResult(BaseModel):
    """Result of task spec compliance audit and drift detection."""

    status: CheckStatus = CheckStatus.PASS
    unmet_requirements: list[str] = Field(
        default_factory=list, description="Explicit spec requirements that were not fulfilled"
    )
    hallucinated_claims: list[str] = Field(
        default_factory=list,
        description="Requirements claimed by agent in summary but completely absent in code diff",
    )
    unrequested_drift: list[str] = Field(
        default_factory=list,
        description="Code changes or features implemented that were never requested",
    )
    compliance_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score between 0.0 and 1.0"
    )
    notes: str = Field(default="", description="Spec compliance assessment notes")


class UserClarification(BaseModel):
    """Records an interactive question asked to the user and their response."""

    topic: str = Field(
        ...,
        description="Category: undeclared_file, weakened_assertion, scope_violation, spec_drift",
    )
    target: str = Field(..., description="Target symbol, line, or file path")
    question: str = Field(..., description="Question posed to the user")
    user_response: str = Field(..., description="User's interactive response or answer")
    authorized: bool = Field(
        default=False, description="Whether deviation was explicitly authorized"
    )


class VerdictThresholds(BaseModel):
    """Configurable thresholds controlling verdict sensitivity.

    These values determine when the aggregate verdict flips from
    VERIFIED → SUSPICIOUS → FAILED. Tighter thresholds catch more
    discrepancies but may produce more false positives.
    """

    max_unclaimed_files_fail: int = Field(
        default=2,
        ge=0,
        description="Unclaimed file count above which verdict is FAILED",
    )
    min_compliance_score_suspicious: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Compliance score below which verdict is SUSPICIOUS",
    )
    suspicious_risk_levels: list[RiskLevel] = Field(
        default_factory=lambda: [RiskLevel.MEDIUM, RiskLevel.HIGH],
        description="Blast-radius risk levels that trigger SUSPICIOUS",
    )
    failed_confidence_cap: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Max confidence score when verdict is FAILED",
    )
    suspicious_confidence_cap: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Max confidence score when verdict is SUSPICIOUS",
    )


class TrustReport(BaseModel):
    """Comprehensive 30-second Trust Report aggregating all verification checks.

    The report merges results from four independent verification pillars
    (diff alignment, test anti-gaming, scope blast-radius, and spec compliance)
    into a single deterministic verdict with a confidence score.
    """

    version: str = "1.0"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    repo_path: str = Field(default="", description="Target repository path")
    target_ref: str = Field(default="working-tree", description="Git ref or working tree checked")
    verdict: Verdict = Field(default=Verdict.VERIFIED, description="Overall verdict")
    confidence_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Calculated confidence score (0.0 - 1.0)"
    )
    summary: str = Field(default="", description="High-level human-readable verdict summary")

    diff_verification: DiffVerificationResult = Field(default_factory=DiffVerificationResult)
    test_verification: TestVerificationResult = Field(default_factory=TestVerificationResult)
    scope_verification: ScopeVerificationResult = Field(default_factory=ScopeVerificationResult)
    spec_compliance: SpecComplianceResult = Field(default_factory=SpecComplianceResult)

    user_clarifications: list[UserClarification] = Field(
        default_factory=list,
        description="Interactive clarifications and user approvals obtained during audit",
    )

    duration_seconds: float = Field(
        default=0.0, description="Total verification duration in seconds"
    )

    def compute_verdict(
        self, thresholds: VerdictThresholds | None = None
    ) -> Verdict:
        """Compute the aggregate verdict based on sub-check statuses.

        Args:
            thresholds: Optional custom thresholds. Uses sensible defaults if None.

        Returns:
            The computed Verdict enum value.
        """
        t = thresholds or VerdictThresholds()

        # Collect per-pillar statuses for clarity
        statuses = [
            self.diff_verification.status,
            self.test_verification.status,
            self.scope_verification.status,
            self.spec_compliance.status,
        ]

        # Hard fail conditions
        fail_reasons: list[str] = []
        if CheckStatus.FAIL in statuses:
            fail_reasons.append("One or more verification pillars returned FAIL")
        if self.test_verification.exit_code != 0:
            fail_reasons.append(f"Test runner exited with code {self.test_verification.exit_code}")
        if len(self.test_verification.weakened_assertions_detected) > 0:
            fail_reasons.append(
                f"{len(self.test_verification.weakened_assertions_detected)} weakened assertion(s)"
            )
        if len(self.scope_verification.forbidden_scope_violations) > 0:
            fail_reasons.append(
                f"{len(self.scope_verification.forbidden_scope_violations)} forbidden scope violation(s)"
            )
        if len(self.diff_verification.sensitive_unclaimed_changes) > 0:
            fail_reasons.append(
                f"{len(self.diff_verification.sensitive_unclaimed_changes)} undeclared sensitive file modification(s)"
            )
        if len(self.diff_verification.unclaimed_changes) > t.max_unclaimed_files_fail:
            fail_reasons.append(
                f"{len(self.diff_verification.unclaimed_changes)} undeclared file(s) "
                f"exceed threshold of {t.max_unclaimed_files_fail}"
            )
        if (
            len(self.test_verification.claim_discrepancies) > 0
            and self.test_verification.tests_failed > 0
        ):
            fail_reasons.append("Agent claimed tests passed, but tests actually failed")

        if fail_reasons:
            self.verdict = Verdict.FAILED
            self.confidence_score = min(self.confidence_score, t.failed_confidence_cap)
            logger.info(
                "Verdict FAILED: %s",
                "; ".join(fail_reasons),
            )
            return self.verdict

        # Suspicious conditions
        warn_reasons: list[str] = []
        if CheckStatus.WARN in statuses:
            warn_reasons.append("One or more verification pillars returned WARN")
        if len(self.diff_verification.unclaimed_changes) > 0:
            warn_reasons.append(
                f"{len(self.diff_verification.unclaimed_changes)} undeclared file(s)"
            )
        if len(self.diff_verification.fabricated_claims) > 0:
            warn_reasons.append(
                f"{len(self.diff_verification.fabricated_claims)} phantom claim(s)"
            )
        if len(self.test_verification.claim_discrepancies) > 0:
            warn_reasons.append(
                f"{len(self.test_verification.claim_discrepancies)} test claim discrepancy item(s)"
            )
        if len(self.spec_compliance.hallucinated_claims) > 0:
            warn_reasons.append(
                f"{len(self.spec_compliance.hallucinated_claims)} hallucinated requirement claim(s)"
            )
        if len(self.spec_compliance.unrequested_drift) > 0:
            warn_reasons.append(
                f"{len(self.spec_compliance.unrequested_drift)} spec drift item(s)"
            )
        if self.scope_verification.blast_radius_risk_level in t.suspicious_risk_levels:
            warn_reasons.append(
                f"Blast radius risk: {self.scope_verification.blast_radius_risk_level.value}"
            )
        if self.spec_compliance.compliance_score < t.min_compliance_score_suspicious:
            warn_reasons.append(
                f"Compliance score {self.spec_compliance.compliance_score:.0%} "
                f"below threshold {t.min_compliance_score_suspicious:.0%}"
            )

        if warn_reasons:
            self.verdict = Verdict.SUSPICIOUS
            self.confidence_score = min(self.confidence_score, t.suspicious_confidence_cap)
            logger.info(
                "Verdict SUSPICIOUS: %s",
                "; ".join(warn_reasons),
            )
            return self.verdict

        self.verdict = Verdict.VERIFIED
        logger.info("Verdict VERIFIED — all checks clean")
        return self.verdict
