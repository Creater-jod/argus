"""Pydantic schemas for the Trust Report and verification sub-results."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


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
    out_of_scope_files: list[str] = Field(
        default_factory=list, description="Changed files located outside allowed boundaries"
    )
    out_of_scope_callers_or_callees: list[str] = Field(
        default_factory=list,
        description="Functions/methods outside scope boundary directly touched or impacted",
    )
    blast_radius_risk_level: str = Field(
        default="LOW", description="Risk tier: LOW, MEDIUM, HIGH, CRITICAL"
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
    unrequested_drift: list[str] = Field(
        default_factory=list,
        description="Code changes or features implemented that were never requested",
    )
    compliance_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score between 0.0 and 1.0"
    )
    notes: str = Field(default="", description="Spec compliance assessment notes")


class TrustReport(BaseModel):
    """Comprehensive 30-second Trust Report aggregating all verification checks."""

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

    duration_seconds: float = Field(
        default=0.0, description="Total verification duration in seconds"
    )

    def compute_verdict(self) -> Verdict:
        """Compute the aggregate verdict based on sub-check statuses."""
        # Hard fail conditions
        if (
            self.diff_verification.status == CheckStatus.FAIL
            or self.test_verification.status == CheckStatus.FAIL
            or self.scope_verification.status == CheckStatus.FAIL
            or self.spec_compliance.status == CheckStatus.FAIL
            or self.test_verification.exit_code != 0
            or len(self.test_verification.weakened_assertions_detected) > 0
            or len(self.diff_verification.unclaimed_changes) > 2
        ):
            self.verdict = Verdict.FAILED
            self.confidence_score = min(self.confidence_score, 0.40)
            return self.verdict

        # Suspicious conditions
        if (
            self.diff_verification.status == CheckStatus.WARN
            or self.test_verification.status == CheckStatus.WARN
            or self.scope_verification.status == CheckStatus.WARN
            or self.spec_compliance.status == CheckStatus.WARN
            or len(self.diff_verification.unclaimed_changes) > 0
            or len(self.diff_verification.fabricated_claims) > 0
            or len(self.spec_compliance.unrequested_drift) > 0
            or self.scope_verification.blast_radius_risk_level in ("MEDIUM", "HIGH")
            or self.spec_compliance.compliance_score < 0.85
        ):
            self.verdict = Verdict.SUSPICIOUS
            self.confidence_score = min(self.confidence_score, 0.75)
            return self.verdict

        self.verdict = Verdict.VERIFIED
        return self.verdict
