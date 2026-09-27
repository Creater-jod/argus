"""Unit tests for TestVerifier anti-gaming detection and runner parsing."""

from pathlib import Path

from agent_verifier.checks.test_verifier import (
    TestVerifier,
    parse_pytest_output,
    scan_for_anti_gaming_diffs,
    scan_python_test_ast,
)
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus


def test_parse_pytest_output():
    output = "===== 12 passed, 2 skipped, 1 failed in 0.35s ====="
    total, passed, failed, skipped = parse_pytest_output(output)
    assert total == 15
    assert passed == 12
    assert failed == 1
    assert skipped == 2


def test_anti_gaming_deleted_assertion():
    diff = FileDiff(
        path="tests/test_auth.py",
        deleted_lines=[
            (42, "    assert response.status_code == 200"),
            (43, "    assert response.json()['token'] is not None"),
        ],
    )
    findings = scan_for_anti_gaming_diffs(diff)
    assert len(findings) == 2
    assert "Deleted assertion" in findings[0]
    assert "status_code == 200" in findings[0]


def test_anti_gaming_commented_assertion():
    diff = FileDiff(
        path="tests/test_payment.py",
        added_lines=[
            (15, "    # assert charge.successful is True"),
        ],
    )
    findings = scan_for_anti_gaming_diffs(diff)
    assert len(findings) == 1
    assert "Commented out assertion" in findings[0]


def test_anti_gaming_skip_marker():
    diff = FileDiff(
        path="tests/test_api.py",
        added_lines=[
            (10, "@pytest.mark.skip(reason='failing temporarily')"),
            (11, "def test_flaky_endpoint():"),
        ],
    )
    findings = scan_for_anti_gaming_diffs(diff)
    assert len(findings) == 1
    assert "Added test skip marker" in findings[0]


def test_anti_gaming_tautological_assertion():
    diff = FileDiff(
        path="tests/test_core.py",
        added_lines=[
            (25, "    assert True"),
        ],
    )
    findings = scan_for_anti_gaming_diffs(diff)
    assert len(findings) == 1
    assert "Tautological assertion" in findings[0]


def test_anti_gaming_swallowed_exception():
    diff = FileDiff(
        path="tests/test_db.py",
        added_lines=[
            (30, "    try:"),
            (31, "        db.connect()"),
            (32, "    except Exception: pass"),
        ],
    )
    findings = scan_for_anti_gaming_diffs(diff)
    assert len(findings) == 1
    assert "Swallowed exception" in findings[0]


def test_anti_gaming_ast_empty_test(tmp_path: Path):
    test_file = tmp_path / "test_dummy.py"
    test_file.write_text(
        """
def test_nothing():
    pass

def test_no_assertions():
    x = 1 + 1
    print(x)
""",
        encoding="utf-8",
    )
    flags = scan_python_test_ast(test_file)
    assert len(flags) == 2
    assert any("Empty test function 'test_nothing'" in f for f in flags)
    assert any("Test 'test_no_assertions' contains zero assertions" in f for f in flags)


def test_test_verifier_execution_simulation():
    verifier = TestVerifier()
    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "tests/test_broken.py": FileDiff(
                path="tests/test_broken.py",
                deleted_lines=[(10, "    assert user.is_active is True")],
            )
        },
    )
    claim = SessionClaim(claimed_tests_run=5, claimed_tests_passed=5)
    result = verifier.run(
        repo_path=Path("."),
        claim=claim,
        context={"diff_summary": diff_summary, "skip_test_execution": True},
    )

    assert result.status == CheckStatus.FAIL
    assert len(result.weakened_assertions_detected) == 1
    assert "ANTI-GAMING ALERT" in result.notes
