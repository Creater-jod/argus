"""Unit tests for Git diff parser and DiffVerifier."""

from pathlib import Path

from agent_verifier.checks.diff_verifier import DiffVerifier
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary, parse_unified_diff
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus

SAMPLE_UNIFIED_DIFF = """diff --git a/src/auth.py b/src/auth.py
index e69de29..4b825dc 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,3 +10,4 @@ def authenticate(user, password):
-    return False
+    if not user:
+        return False
+    return True
diff --git a/tests/test_auth.py b/tests/test_auth.py
new file mode 100644
index 0000000..732a39b
--- /dev/null
+++ b/tests/test_auth.py
@@ -0,0 +1,5 @@
+def test_auth():
+    assert True
"""


def test_parse_unified_diff():
    files = parse_unified_diff(SAMPLE_UNIFIED_DIFF)
    assert "src/auth.py" in files
    assert "tests/test_auth.py" in files

    auth_diff = files["src/auth.py"]
    assert auth_diff.change_type == "M"
    assert auth_diff.lines_added == 3
    assert auth_diff.lines_deleted == 1

    test_diff = files["tests/test_auth.py"]
    assert test_diff.change_type == "A"
    assert test_diff.lines_added == 2


def test_diff_verifier_exact_match():
    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "src/auth.py": FileDiff(path="src/auth.py", lines_added=5, lines_deleted=1),
            "tests/test_auth.py": FileDiff(path="tests/test_auth.py", lines_added=10),
        },
        total_lines_added=15,
        total_lines_deleted=1,
    )

    claim = SessionClaim(claimed_files=["src/auth.py", "tests/test_auth.py"])
    verifier = DiffVerifier()
    res = verifier.run(Path("."), claim, context={"diff_summary": diff_summary})

    assert res.status == CheckStatus.PASS
    assert len(res.unclaimed_changes) == 0
    assert len(res.fabricated_claims) == 0
    assert res.lines_added == 15


def test_diff_verifier_undeclared_changes():
    # Agent claims only auth.py, but billing.py was also modified in git
    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "src/auth.py": FileDiff(path="src/auth.py"),
            "src/billing.py": FileDiff(path="src/billing.py"),
        },
    )

    claim = SessionClaim(claimed_files=["src/auth.py"])
    verifier = DiffVerifier()
    res = verifier.run(Path("."), claim, context={"diff_summary": diff_summary})

    assert res.status == CheckStatus.FAIL
    assert "src/billing.py" in res.unclaimed_changes
    assert len(res.discrepancies) > 0


def test_diff_verifier_fabricated_claims():
    # Agent claims it modified db.py, but git diff has only auth.py
    diff_summary = GitDiffSummary(
        repo_path=".",
        files={
            "src/auth.py": FileDiff(path="src/auth.py"),
        },
    )

    claim = SessionClaim(claimed_files=["src/auth.py", "src/db.py", "src/config.py"])
    verifier = DiffVerifier()
    res = verifier.run(Path("."), claim, context={"diff_summary": diff_summary})

    assert res.status == CheckStatus.FAIL
    assert "src/db.py" in res.fabricated_claims
    assert "src/config.py" in res.fabricated_claims
