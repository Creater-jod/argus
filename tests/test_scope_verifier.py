"""Unit tests for ScopeVerifier engine."""

from pathlib import Path

from agent_verifier.checks.scope_verifier import ScopeVerifier, _is_path_allowed
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus


def test_is_path_allowed_matching():
    # Empty patterns allow all
    assert _is_path_allowed("src/auth.py", []) is True

    # Exact and glob matching
    assert _is_path_allowed("src/auth.py", ["src/auth.py"]) is True
    assert _is_path_allowed("src/auth.py", ["src/*"]) is True
    assert _is_path_allowed("src/sub/auth.py", ["src/*"]) is True
    assert _is_path_allowed("tests/test_auth.py", ["src/*"]) is False
    assert _is_path_allowed("billing/stripe.py", ["src/*", "billing/*"]) is True


def test_scope_verifier_within_scope():
    verifier = ScopeVerifier()
    claim = SessionClaim(allowed_paths=["src/*"])

    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "src/auth.py": FileDiff(path="src/auth.py"),
            "src/models.py": FileDiff(path="src/models.py"),
        },
    )

    res = verifier.run(
        Path("."),
        claim,
        context={"diff_summary": diff_summary, "skip_graph_analysis": True},
    )

    assert res.status == CheckStatus.PASS
    assert len(res.out_of_scope_files) == 0


def test_scope_verifier_violation():
    verifier = ScopeVerifier()
    claim = SessionClaim(allowed_paths=["src/auth/*"])

    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "src/auth/jwt.py": FileDiff(path="src/auth/jwt.py"),
            "src/billing/payment.py": FileDiff(path="src/billing/payment.py"),
        },
    )

    res = verifier.run(
        Path("."),
        claim,
        context={"diff_summary": diff_summary, "skip_graph_analysis": True},
    )

    assert res.status == CheckStatus.FAIL
    assert "src/billing/payment.py" in res.out_of_scope_files
