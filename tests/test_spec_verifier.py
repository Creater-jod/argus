"""Unit tests for SpecVerifier and LLMJudge heuristics."""

from pathlib import Path

from agent_verifier.checks.spec_verifier import SpecVerifier
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary
from agent_verifier.llm.judge import LLMJudge
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus


def test_spec_verifier_no_spec():
    verifier = SpecVerifier(judge=LLMJudge(provider="heuristic"))
    claim = SessionClaim(summary="Did some work")
    result = verifier.run(Path("."), claim)
    assert result.status == CheckStatus.PASS
    assert result.compliance_score == 1.0
    assert "No explicit specification" in result.notes


def test_spec_verifier_satisfied():
    spec = """
    Requirements:
    - [x] Implement JWT token validation in auth.py
    - [x] Add expiration check for bearer tokens
    """
    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "auth.py": FileDiff(
                path="auth.py",
                patch="def validate_jwt(token):\n    check_expiration(token)\n    return True",
            )
        },
    )
    claim = SessionClaim(
        summary="Implemented JWT token validation and expiration checking for bearer tokens in auth.py.",
        spec_text=spec,
    )
    verifier = SpecVerifier(judge=LLMJudge(provider="heuristic"))
    result = verifier.run(Path("."), claim, context={"diff_summary": diff_summary})

    assert result.status == CheckStatus.PASS
    assert result.compliance_score >= 0.85
    assert len(result.unmet_requirements) == 0


def test_spec_verifier_unmet_requirements():
    spec = """
    1. Implement rate limiting on login endpoint with Redis
    2. Add captcha verification on signup
    3. Send confirmation email after registration
    """
    # Agent only did login rate limit
    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "auth.py": FileDiff(
                path="auth.py",
                patch="def rate_limit_login(): pass",
            )
        },
    )
    claim = SessionClaim(
        summary="Added login rate limiting.",
        spec_text=spec,
    )
    verifier = SpecVerifier(judge=LLMJudge(provider="heuristic"))
    result = verifier.run(Path("."), claim, context={"diff_summary": diff_summary})

    assert result.status in (CheckStatus.WARN, CheckStatus.FAIL)
    assert len(result.unmet_requirements) >= 1
    assert result.compliance_score < 0.85
