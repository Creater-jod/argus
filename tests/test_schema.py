"""Unit tests for TrustReport and SessionClaim data models."""

from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import (
    CheckStatus,
    DiffVerificationResult,
    ScopeVerificationResult,
    SpecComplianceResult,
    TestVerificationResult,
    TrustReport,
    Verdict,
)


def test_trust_report_defaults():
    report = TrustReport(repo_path=".")
    assert report.verdict == Verdict.VERIFIED
    assert report.confidence_score == 1.0
    assert report.diff_verification.status == CheckStatus.PASS
    assert report.test_verification.status == CheckStatus.PASS


def test_trust_report_verdict_computation_failure():
    report = TrustReport(repo_path=".")
    report.test_verification.exit_code = 1
    report.test_verification.status = CheckStatus.FAIL
    verdict = report.compute_verdict()
    assert verdict == Verdict.FAILED
    assert report.confidence_score <= 0.40


def test_trust_report_verdict_weakened_test_is_failure():
    report = TrustReport(repo_path=".")
    report.test_verification.weakened_assertions_detected = [
        "Deleted assertion 'assert res.status_code == 200' in test_auth.py:42"
    ]
    verdict = report.compute_verdict()
    assert verdict == Verdict.FAILED


def test_trust_report_verdict_suspicious_on_unclaimed_file():
    report = TrustReport(repo_path=".")
    report.diff_verification.unclaimed_changes = ["secret_config.py"]
    verdict = report.compute_verdict()
    assert verdict == Verdict.SUSPICIOUS
    assert report.confidence_score <= 0.75


def test_session_claim_from_summary_parsing():
    agent_output = """
    I have resolved the issue by updating `src/auth.py` and `src/models/user.py`.
    I ran pytest and all 14 tests passed successfully!
    """
    claim = SessionClaim.from_summary(agent_output)
    assert "src/auth.py" in claim.claimed_files
    assert "src/models/user.py" in claim.claimed_files
    assert claim.claimed_tests_passed == 14
    assert claim.claimed_tests_run == 14


def test_trust_report_json_roundtrip():
    report = TrustReport(
        repo_path="/path/to/repo",
        summary="Audit completed cleanly",
        diff_verification=DiffVerificationResult(
            status=CheckStatus.PASS,
            claimed_files=["app.py"],
            actual_changed_files=["app.py"],
            lines_added=15,
            lines_deleted=3,
        ),
        test_verification=TestVerificationResult(
            status=CheckStatus.PASS,
            runner="pytest",
            tests_run=5,
            tests_passed=5,
        ),
        scope_verification=ScopeVerificationResult(
            status=CheckStatus.PASS,
            blast_radius_risk_level="LOW",
        ),
        spec_compliance=SpecComplianceResult(
            status=CheckStatus.PASS,
            compliance_score=1.0,
        ),
    )
    json_str = report.model_dump_json()
    reconstructed = TrustReport.model_validate_json(json_str)
    assert reconstructed.repo_path == "/path/to/repo"
    assert reconstructed.diff_verification.lines_added == 15
    assert reconstructed.verdict == Verdict.VERIFIED
