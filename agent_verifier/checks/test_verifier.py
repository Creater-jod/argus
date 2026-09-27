"""Test Suite Verification & Anti-Gaming Engine.

Executes tests independently in isolated subprocesses and inspects git diffs
for deceptive test mutations (deleted assertions, skips, swallowed errors, tautologies).
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus, TestVerificationResult


def detect_test_runner(repo_path: Path) -> tuple[str, list[str]]:
    """Automatically detect appropriate test runner and command for repository."""
    # Python
    if (
        (repo_path / "pyproject.toml").exists()
        or (repo_path / "pytest.ini").exists()
        or (repo_path / "tests").is_dir()
    ):
        return "pytest", ["uv", "run", "pytest", "-q"]

    # Node / JavaScript / TypeScript
    if (repo_path / "package.json").exists():
        return "npm", ["npm", "test", "--", "--bail", "false"]

    # Rust
    if (repo_path / "Cargo.toml").exists():
        return "cargo", ["cargo", "test"]

    # Go
    if (repo_path / "go.mod").exists():
        return "go", ["go", "test", "./..."]

    return "pytest", ["pytest", "-q"]


def parse_pytest_output(output: str) -> tuple[int, int, int, int]:
    """Extract (run, passed, failed, skipped) counts from pytest output."""
    # Typical pytest summary line: "14 passed, 1 skipped, 2 failed in 0.45s"
    passed = 0
    failed = 0
    skipped = 0

    pass_match = re.search(r"(\d+)\s+passed", output)
    if pass_match:
        passed = int(pass_match.group(1))

    fail_match = re.search(r"(\d+)\s+failed", output)
    if fail_match:
        failed = int(fail_match.group(1))

    skip_match = re.search(r"(\d+)\s+skipped", output)
    if skip_match:
        skipped = int(skip_match.group(1))

    total = passed + failed + skipped
    return total, passed, failed, skipped


def is_test_file(path_str: str) -> bool:
    """Determine if a path represents a test file."""
    p = path_str.lower().replace("\\", "/")
    filename = Path(p).name
    return (
        "test" in p
        or filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename.endswith(".test.ts")
        or filename.endswith(".spec.ts")
        or filename.endswith(".test.js")
        or filename.endswith(".spec.js")
    )


def scan_for_anti_gaming_diffs(file_diff: FileDiff) -> list[str]:
    """Scan a test file diff for deletion or weakening of assertions, skips, or swallowed errors."""
    findings: list[str] = []

    # 1. Inspect deleted lines for assertions
    for line_no, content in file_diff.deleted_lines:
        stripped = content.strip()
        # Deleted assert statement
        if (
            stripped.startswith("assert ")
            or stripped.startswith("assert(")
            or "self.assert" in stripped
        ):
            findings.append(f"{file_diff.path}:{line_no} - Deleted assertion: '{stripped}'")

    # 2. Inspect added lines for gaming patterns
    for line_no, content in file_diff.added_lines:
        stripped = content.strip()

        # Commented-out assertions added
        if re.match(r"^#\s*(?:assert\s|self\.assert)", stripped):
            findings.append(f"{file_diff.path}:{line_no} - Commented out assertion: '{stripped}'")

        # Skip decorators or skip calls added
        if (
            "@pytest.mark.skip" in stripped
            or "@unittest.skip" in stripped
            or re.search(r"\b(?:it|test|describe)\.skip\b", stripped)
            or re.search(r"\b(?:xit|xdescribe)\b", stripped)
        ):
            findings.append(f"{file_diff.path}:{line_no} - Added test skip marker: '{stripped}'")

        # Mark xfail added (silencing real failures)
        if "@pytest.mark.xfail" in stripped or "@unittest.expectedFailure" in stripped:
            findings.append(
                f"{file_diff.path}:{line_no} - Added test failure suppression (xfail): '{stripped}'"
            )

        # Tautological assertions: assert True, assert 1 == 1, self.assertTrue(True)
        if re.search(r"\bassert\s+(?:True|1\s*==\s*1|0\s*==\s*0)\b", stripped) or re.search(
            r"self\.assertTrue\(\s*True\s*\)", stripped
        ):
            findings.append(
                f"{file_diff.path}:{line_no} - Tautological assertion added: '{stripped}'"
            )

        # Diluted/weakened assertions: assert ... or True
        if re.search(r"\bassert\s+.*(?:\bor\s+True|\bor\s+1\b)", stripped) or re.search(
            r"\bassert\s+True\s+in\b", stripped
        ):
            findings.append(
                f"{file_diff.path}:{line_no} - Diluted/weakened assertion: '{stripped}'"
            )

        # Swallowed exceptions in tests: except ...: pass
        if re.search(r"except(?:\s+\w+)?:\s*pass", stripped):
            findings.append(
                f"{file_diff.path}:{line_no} - Swallowed exception (except: pass) in test: '{stripped}'"
            )

    return findings


def scan_python_test_ast(file_path: Path) -> list[str]:
    """Parse Python test file AST to detect empty tests or tests without any assertions."""
    flags: list[str] = []
    if not file_path.is_file() or not file_path.name.endswith(".py"):
        return flags

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
    except Exception:
        return flags

    for node in ast.walk(tree):
        if (  # noqa: SIM102
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (node.name.startswith("test_") or node.name.endswith("_test"))
        ):
            # Check if body is just `pass` or `...` or empty
            if (  # noqa: SIM102
                len(node.body) == 1
                and isinstance(node.body[0], (ast.Pass, ast.Expr))
                and (
                    isinstance(node.body[0], ast.Pass)
                    or (
                        isinstance(node.body[0].value, ast.Constant)
                        and node.body[0].value.value is ...
                    )
                )
            ):
                flags.append(
                    f"{file_path.name}:{node.lineno} - Empty test function '{node.name}' with no assertions"
                )
                continue

            # Check if test has at least one assert or assertion method call
            has_assertion = False
            for child in ast.walk(node):
                if isinstance(child, ast.Assert):
                    has_assertion = True
                    break
                elif isinstance(child, ast.Call):
                    # check calls like self.assertEqual, pytest.raises, etc.
                    func_name = ""
                    if isinstance(child.func, ast.Attribute):
                        func_name = child.func.attr
                    elif isinstance(child.func, ast.Name):
                        func_name = child.func.id
                    if "assert" in func_name.lower() or func_name in (
                        "raises",
                        "check",
                        "verify",
                        "expect",
                    ):
                        has_assertion = True
                        break

            if not has_assertion:
                flags.append(
                    f"{file_path.name}:{node.lineno} - Test '{node.name}' contains zero assertions"
                )

    return flags


class TestVerifier(BaseCheck):
    """Executes tests independently and detects deceptive anti-gaming modifications."""

    @property
    def name(self) -> str:
        return "test_verifier"

    @property
    def description(self) -> str:
        return "Runs test suite in isolation and scans diffs for deleted assertions or test skips."

    def run(
        self,
        repo_path: Path,
        claim: SessionClaim,
        context: dict[str, Any] | None = None,
    ) -> TestVerificationResult:
        context = context or {}
        diff_summary: GitDiffSummary | None = context.get("diff_summary")
        skip_execution: bool = context.get("skip_test_execution", False)

        weakened_assertions: list[str] = []
        trivially_passing: list[str] = []

        # 1. Anti-gaming check on test file diffs
        if diff_summary:
            for file_path, file_diff in diff_summary.files.items():
                if is_test_file(file_path):
                    anti_gaming = scan_for_anti_gaming_diffs(file_diff)
                    weakened_assertions.extend(anti_gaming)

                    # AST check for new/modified python test files
                    full_p = repo_path / file_path
                    if full_p.exists():
                        ast_flags = scan_python_test_ast(full_p)
                        trivially_passing.extend(ast_flags)

        # 2. Independent Test Execution (Subprocess)
        runner_name = "pytest"
        exit_code = 0
        tests_run = 0
        tests_passed = 0
        tests_failed = 0
        tests_skipped = 0
        output_snippet = ""
        notes_parts: list[str] = []

        if not skip_execution:
            runner_name, cmd = detect_test_runner(repo_path)
            timeout = context.get("test_timeout", 30)
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(repo_path),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
                exit_code = proc.returncode
                output = proc.stdout + "\n" + proc.stderr
                output_snippet = output[-1000:] if len(output) > 1000 else output

                if runner_name == "pytest":
                    tests_run, tests_passed, tests_failed, tests_skipped = parse_pytest_output(
                        output
                    )
                else:
                    tests_run = 1 if exit_code == 0 else 0
                    tests_passed = 1 if exit_code == 0 else 0
                    tests_failed = 1 if exit_code != 0 else 0

            except subprocess.TimeoutExpired:
                exit_code = 124
                output_snippet = f"Test execution timed out after {timeout} seconds."
            except Exception as e:
                exit_code = 1
                output_snippet = f"Failed to execute test runner {cmd}: {e}"
        claim_discrepancies: list[str] = []
        if not skip_execution:
            if claim.claimed_tests_run is not None and claim.claimed_tests_run != tests_run:
                claim_discrepancies.append(
                    f"Agent claimed {claim.claimed_tests_run} tests run, but actual execution ran {tests_run} tests."
                )
            if (
                claim.claimed_tests_passed is not None
                and claim.claimed_tests_passed != tests_passed
            ):
                claim_discrepancies.append(
                    f"Agent claimed {claim.claimed_tests_passed} tests passed, but actual execution recorded {tests_passed} passed and {tests_failed} failed."
                )
        else:
            # When test execution is simulated or skipped in context
            tests_run = claim.claimed_tests_run or 0
            tests_passed = claim.claimed_tests_passed or 0
            if claim.claimed_tests_run or claim.claimed_tests_passed:
                notes_parts.append(
                    f"⚠️ Test suite execution skipped by request. Claimed test passes ({tests_passed}/{tests_run}) unverified."
                )

        # Status determination
        if weakened_assertions:
            notes_parts.append(
                f"🚨 ANTI-GAMING ALERT: {len(weakened_assertions)} weakened assertion(s) or skips detected!"
            )
        if trivially_passing:
            notes_parts.append(
                f"⚠️ {len(trivially_passing)} assertion-less or trivial test(s) flagged."
            )
        if claim_discrepancies:
            for cd in claim_discrepancies:
                notes_parts.append(f"🚨 TEST CLAIM DISCREPANCY: {cd}")
        if exit_code != 0:
            notes_parts.append(f"❌ Test runner failed with exit code {exit_code}.")
        else:
            notes_parts.append(f"✅ Tests passed ({tests_passed} passed, {tests_skipped} skipped).")

        status = CheckStatus.PASS
        if weakened_assertions or exit_code != 0:
            status = CheckStatus.FAIL
        elif (
            trivially_passing or tests_skipped > 0 or len(claim_discrepancies) > 0 or skip_execution
        ):
            status = CheckStatus.WARN

        return TestVerificationResult(
            status=status,
            runner=runner_name,
            exit_code=exit_code,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            tests_skipped=tests_skipped,
            weakened_assertions_detected=weakened_assertions,
            trivially_passing_tests_flagged=trivially_passing,
            claim_discrepancies=claim_discrepancies,
            output_snippet=output_snippet.strip() or None,
            notes=" ".join(notes_parts),
        )
