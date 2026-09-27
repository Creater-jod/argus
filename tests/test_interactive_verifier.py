"""Unit tests for interactive discrepancy questioning engine."""

from agent_verifier.interview.interactive_verifier import InteractiveVerifierSession
from agent_verifier.models.trust_report import (
    CheckStatus,
    DiffVerificationResult,
    ScopeVerificationResult,
    SpecComplianceResult,
    TestVerificationResult,
    TrustReport,
    Verdict,
)


def make_discrepant_report() -> TrustReport:
    report = TrustReport(
        repo_path=".",
        verdict=Verdict.FAILED,
        diff_verification=DiffVerificationResult(
            status=CheckStatus.FAIL,
            claimed_files=["src/auth.py"],
            actual_changed_files=["src/auth.py", "src/billing.py"],
            unclaimed_changes=["src/billing.py"],
        ),
        test_verification=TestVerificationResult(
            status=CheckStatus.FAIL,
            weakened_assertions_detected=[
                "test_auth.py:40 - Deleted assertion: 'assert token is not None'"
            ],
        ),
        scope_verification=ScopeVerificationResult(
            status=CheckStatus.FAIL,
            out_of_scope_files=["src/billing.py"],
        ),
        spec_compliance=SpecComplianceResult(
            status=CheckStatus.PASS,
        ),
    )
    return report


def test_interactive_verifier_approves_discrepancies():
    report = make_discrepant_report()

    # User confirms all questions: yes
    def mock_confirm(_q: str) -> bool:
        return True

    session = InteractiveVerifierSession(confirm_func=mock_confirm)
    updated = session.review_discrepancies(report)

    assert len(updated.user_clarifications) == 3
    assert all(c.authorized for c in updated.user_clarifications)

    # Undeclared file should be approved and removed from pending unclaimed list
    assert len(updated.diff_verification.unclaimed_changes) == 0
    assert len(updated.scope_verification.out_of_scope_files) == 0
    assert updated.verdict == Verdict.VERIFIED


def test_interactive_verifier_rejects_discrepancies():
    report = make_discrepant_report()

    # User rejects questions: no
    def mock_confirm(_q: str) -> bool:
        return False

    session = InteractiveVerifierSession(confirm_func=mock_confirm)
    updated = session.review_discrepancies(report)

    assert len(updated.user_clarifications) == 3
    assert all(not c.authorized for c in updated.user_clarifications)

    # Discrepancies should remain active and verdict should remain FAILED
    assert "src/billing.py" in updated.diff_verification.unclaimed_changes
    assert updated.verdict == Verdict.FAILED
