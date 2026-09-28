"""Test Suite Verification & Anti-Gaming Engine.

Executes tests independently in sandboxed containers (or via explicit host opt-in)
and inspects git diffs for deceptive test mutations (deleted assertions, skips,
swallowed errors, tautologies).
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_verifier.checks.base import BaseCheck
from agent_verifier.git.diff_parser import FileDiff, GitDiffSummary
from agent_verifier.git.worktree_guard import compare_worktree_states, snapshot_worktree_state
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus, TestVerificationResult


def _redact_test_output(text: str) -> str:
    """Redact secret tokens, passwords, canaries, and credentials from test runner output."""
    if not text:
        return text
    redacted = re.sub(
        r"(\b[\w\-]*(?:api[_-]?key|token|secret|password|bearer|auth|canary)[\w\-]*\s*[:=]\s*['\"]?)[A-Za-z0-9_\-\.]{8,}(['\"]?)",
        r"\1[REDACTED_SECRET]\2",
        text,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b",
        "[REDACTED_GITHUB_TOKEN]",
        redacted,
    )
    redacted = re.sub(
        r"\bAKIA[0-9A-Z]{16}\b",
        "[REDACTED_AWS_KEY]",
        redacted,
    )
    return redacted


def detect_test_runner(repo_path: Path) -> tuple[str, list[str]]:
    """Automatically detect appropriate test runner and command for repository.

    Supports pytest without requiring 'uv' when Argus is installed with pip.
    """
    # Python
    if (
        (repo_path / "pyproject.toml").exists()
        or (repo_path / "pytest.ini").exists()
        or (repo_path / "tests").is_dir()
        or (repo_path / "setup.py").exists()
        or (repo_path / "requirements.txt").exists()
    ):
        # Prefer uv only if uv is actually installed and repo has uv.lock
        if shutil.which("uv") and (repo_path / "uv.lock").exists():
            return "pytest", ["uv", "run", "pytest", "-q"]
        # Use pytest in current python environment or PATH
        if shutil.which("pytest"):
            return "pytest", ["pytest", "-q"]
        return "pytest", [sys.executable, "-m", "pytest", "-q"]

    # Node / JavaScript / TypeScript
    if (repo_path / "package.json").exists():
        return "npm", ["npm", "test"]

    # Rust
    if (repo_path / "Cargo.toml").exists():
        return "cargo", ["cargo", "test"]

    # Go
    if (repo_path / "go.mod").exists():
        return "go", ["go", "test", "./..."]

    if shutil.which("pytest"):
        return "pytest", ["pytest", "-q"]
    return "pytest", [sys.executable, "-m", "pytest", "-q"]


def parse_pytest_output(output: str) -> tuple[int, int, int, int]:
    """Extract (run, passed, failed, skipped) counts from pytest output.

    Requires recognizable pytest session markers to prevent fake echo evidence.
    """
    passed = 0
    failed = 0
    skipped = 0

    lower = output.lower()
    has_pytest_marker = (
        "test session starts" in lower
        or "collected " in lower
        or "pytest" in lower
        or "rootdir:" in lower
        or re.search(r"=\s+\d+\s+passed.*in\s+[\d.]+s", lower)
    )
    if not has_pytest_marker:
        return 0, 0, 0, 0

    if "collected 0 items" in lower or "no tests ran" in lower:
        return 0, 0, 0, 0

    pass_match = re.search(r"(\d+)\s+passed", output)
    if pass_match:
        passed = int(pass_match.group(1))

    fail_match = re.search(r"(\d+)\s+failed", output)
    if fail_match:
        failed = int(fail_match.group(1))

    err_match = re.search(r"(\d+)\s+error(?:s)?\b", output)
    if err_match:
        failed += int(err_match.group(1))

    skip_match = re.search(r"(\d+)\s+skipped", output)
    if skip_match:
        skipped = int(skip_match.group(1))

    total = passed + failed + skipped
    return total, passed, failed, skipped


def parse_cargo_output(output: str) -> tuple[int, int, int, int]:
    """Extract (total, passed, failed, skipped) from cargo test output."""
    m = re.search(
        r"test result:\s+(?:ok|FAILED)\.\s+(\d+)\s+passed;\s+(\d+)\s+failed;\s+(\d+)\s+ignored",
        output,
        re.IGNORECASE,
    )
    if m:
        passed = int(m.group(1))
        failed = int(m.group(2))
        skipped = int(m.group(3))
        return passed + failed + skipped, passed, failed, skipped
    return 0, 0, 0, 0


def parse_npm_output(output: str) -> tuple[int, int, int, int]:
    """Extract (total, passed, failed, skipped) from npm/jest/vitest output.

    Requires recognizable test runner evidence. Rejects fake echo commands,
    shell-chained echo scripts, and unverified outputs.
    """
    if not output or "no_tests_found" in output.lower() or "no tests found" in output.lower():
        return 0, 0, 0, 0

    # Detect if npm test was an echo command or shell chain printing fake numbers
    # e.g.: "> echo '5 passed'", "> node evil.js && echo '10 passed'"
    if re.search(r">\s*(?:.*&&)?\s*echo\s+['\"].*pass", output, re.IGNORECASE) or re.search(
        r">\s*echo\s+.*pass", output, re.IGNORECASE
    ):
        return 0, 0, 0, 0

    # Require recognizable framework test-runner evidence (Jest, Vitest, Mocha, Node test runner, TAP)
    # A script merely printing 'Tests: 999 passed' without runner context is rejected as unverified
    has_runner_signature = bool(
        re.search(r"Test Suites:\s+", output)
        or re.search(r"Test Files\s+", output)
        or re.search(r"PASS\s+[\w\-./\\]+", output)
        or re.search(r"FAIL\s+[\w\-./\\]+", output)
        or re.search(r"\d+\s+passing\s+\(\d+m?s\)", output)
        or re.search(r"[✓✔]\s+[\w\-./\\]+", output)
        or "TAP version" in output
        or re.search(r"#\s*(?:tests|pass|Subtest:)\b", output)
    )
    if not has_runner_signature:
        return 0, 0, 0, 0

    passed = 0
    failed = 0
    skipped = 0

    # In Jest/Vitest, the summary line begins with 'Tests:'
    # Matching on this line specifically avoids collisions with 'Test Suites: 1 passed'
    tests_line_match = re.search(r"Tests:\s+([^\n]+)", output, re.IGNORECASE)
    if tests_line_match:
        line = tests_line_match.group(1)
        p_m = re.search(r"(\d+)\s+passed", line, re.IGNORECASE)
        f_m = re.search(r"(\d+)\s+failed", line, re.IGNORECASE)
        s_m = re.search(r"(\d+)\s+(?:skipped|pending|todo)", line, re.IGNORECASE)
        tot_m = re.search(r"(\d+)\s+total", line, re.IGNORECASE)
        passed = int(p_m.group(1)) if p_m else 0
        failed = int(f_m.group(1)) if f_m else 0
        skipped = int(s_m.group(1)) if s_m else 0
        total = int(tot_m.group(1)) if tot_m else (passed + failed + skipped)
        return total, passed, failed, skipped

    matched = False
    m_pass = re.search(r"(\d+)\s+passed", output, re.IGNORECASE)
    if m_pass:
        passed = int(m_pass.group(1))
        matched = True

    m_fail = re.search(r"(\d+)\s+failed", output, re.IGNORECASE)
    if m_fail:
        failed = int(m_fail.group(1))
        matched = True

    m_skip = re.search(r"(\d+)\s+(?:skipped|pending|todo)", output, re.IGNORECASE)
    if m_skip:
        skipped = int(m_skip.group(1))
        matched = True

    if not matched:
        m_pass = re.search(r"(\d+)\s+passing", output, re.IGNORECASE)
        m_fail = re.search(r"(\d+)\s+failing", output, re.IGNORECASE)
        m_skip = re.search(r"(\d+)\s+pending", output, re.IGNORECASE)
        if m_pass or m_fail or m_skip:
            passed = int(m_pass.group(1)) if m_pass else 0
            failed = int(m_fail.group(1)) if m_fail else 0
            skipped = int(s_m.group(1)) if s_m else 0
            matched = True

    if matched:
        return passed + failed + skipped, passed, failed, skipped
    return 0, 0, 0, 0


def parse_go_output(output: str) -> tuple[int, int, int, int]:
    """Extract (total, passed, failed, skipped) from go test output."""
    passed = len(re.findall(r"^--- PASS:", output, re.MULTILINE))
    failed = len(re.findall(r"^--- FAIL:", output, re.MULTILINE))
    skipped = len(re.findall(r"^--- SKIP:", output, re.MULTILINE))
    total = passed + failed + skipped
    return total, passed, failed, skipped


def run_in_docker_sandbox(
    repo_path: Path,
    cmd: list[str],
    timeout: int = 30,
) -> tuple[int, str]:
    """Execute tests inside an isolated ephemeral Docker container.

    Security guarantees:
    - Networking disabled (--network none)
    - Resource limits (--memory 512m --cpus 1.0 --pids-limit 100)
    - Isolated temporary copy (host repo is not mutated)
    - No inherited secrets or host environment variables
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise RuntimeError("Docker is not installed or not in PATH")

    with tempfile.TemporaryDirectory(prefix="argus_sandbox_") as tmp_dir:
        tmp_repo = Path(tmp_dir) / "repo"
        shutil.copytree(
            repo_path,
            tmp_repo,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache"
            ),
        )

        docker_cmd = [
            docker_bin,
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "512m",
            "--cpus",
            "1.0",
            "--pids-limit",
            "100",
            "-v",
            f"{tmp_repo.resolve()}:/workspace:ro",
            "-w",
            "/workspace",
            "python:3.12-slim",
        ] + cmd

        proc = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout + "\n" + proc.stderr


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

    # 1. Inspect deleted lines for assertions (Python and JS/TS)
    for line_no, content in file_diff.deleted_lines:
        stripped = content.strip()
        # Deleted assert statement or JS/TS expect/assert
        if (
            stripped.startswith("assert ")
            or stripped.startswith("assert(")
            or "self.assert" in stripped
            or stripped.startswith("expect(")
            or stripped.startswith("expect.")
            or stripped.startswith("assert.")
            or re.search(r"\bexpect\s*\(", stripped)
            or re.search(
                r"\bassert\.(?:strictEqual|equal|deepEqual|isTrue|isFalse|ok|notOk)\b", stripped
            )
            or re.search(r"\bt\.(?:is|true|false|assert)\(", stripped)
            or re.search(r"\bshould\.(?:exist|be|have)\b", stripped)
        ):
            findings.append(f"{file_diff.path}:{line_no} - Deleted assertion: '{stripped}'")

    # 2. Inspect added lines for gaming patterns
    for line_no, content in file_diff.added_lines:
        stripped = content.strip()

        # Commented-out assertions added (Python or JS/TS)
        if re.match(r"^#\s*(?:assert\s|self\.assert)", stripped) or re.match(
            r"^//\s*(?:expect\(|assert\b|assert\.)", stripped
        ):
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
        return "Executes test suite (sandboxed container or opted-in host process) and scans diffs for anti-gaming mutations."

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

        # 2. Test Execution
        runner_name = "pytest"
        exit_code = 0
        tests_run = 0
        tests_passed = 0
        tests_failed = 0
        tests_skipped = 0
        output_snippet = ""
        notes_parts: list[str] = []
        execution_mode = "simulated" if skip_execution else "host_unsandboxed"
        is_unverified = False

        claim_discrepancies: list[str] = []
        worktree_mutations: list[str] = []

        if not skip_execution:
            before_worktree = snapshot_worktree_state(repo_path)
            runner_name, cmd = detect_test_runner(repo_path)
            timeout = context.get("test_timeout", 30)
            allow_host_exec = context.get("allow_host_execution", True)
            sandbox_mode = context.get("sandbox_mode", "auto")

            docker_available = bool(shutil.which("docker"))
            use_docker = (sandbox_mode == "docker") or (
                sandbox_mode == "auto" and docker_available and not allow_host_exec
            )

            output = ""
            ran = False

            if use_docker:
                try:
                    exit_code, output = run_in_docker_sandbox(repo_path, cmd, timeout=timeout)
                    execution_mode = "sandboxed_docker"
                    notes_parts.append(
                        "🔒 Executed in sandboxed Docker container (network disabled, resource limits, no inherited secrets)."
                    )
                    ran = True
                except Exception as e:
                    if not allow_host_exec:
                        exit_code = 1
                        execution_mode = "blocked_no_sandbox"
                        is_unverified = True
                        output = f"Docker sandboxing failed: {e}. Host execution is disabled."
                        notes_parts.append(
                            f"🛑 Test execution blocked: Docker sandbox failed ({e}) and host execution not opted into."
                        )

            if not ran and execution_mode != "blocked_no_sandbox":
                if allow_host_exec:
                    execution_mode = "host_unsandboxed"
                    notes_parts.append(
                        "⚠️ Executed on HOST without container isolation (--allow-host-exec). "
                        "Test code executed with host user privileges. Environment variable filtering alone is NOT an OS sandbox."
                    )
                    # Sanitize host environment to avoid leaking secrets
                    safe_env = {
                        k: v
                        for k, v in os.environ.items()
                        if k
                        in (
                            "PATH",
                            "SYSTEMROOT",
                            "WINDIR",
                            "TEMP",
                            "TMP",
                            "HOMEPATH",
                            "USERPROFILE",
                            "VIRTUAL_ENV",
                            "LANG",
                            "LC_ALL",
                        )
                        and not any(
                            s in k.lower()
                            for s in ("secret", "token", "key", "cred", "auth", "pass", "canary")
                        )
                    }
                    safe_env["PYTHONUNBUFFERED"] = "1"
                    try:
                        proc = subprocess.run(
                            cmd,
                            cwd=str(repo_path),
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            env=safe_env,
                        )
                        exit_code = proc.returncode
                        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                        ran = True
                    except subprocess.TimeoutExpired:
                        exit_code = 124
                        output = f"Test execution timed out after {timeout} seconds."
                    except Exception as e:
                        exit_code = 1
                        output = f"Failed to execute test runner {cmd}: {e}"
                else:
                    exit_code = 0
                    execution_mode = "blocked_no_sandbox"
                    is_unverified = True
                    output = "Docker sandbox is unavailable and host execution was not opted into."
                    notes_parts.append(
                        "🛑 Test execution skipped: container sandbox unavailable and host execution not opted into. Pass --allow-host-exec to permit running tests on host."
                    )

            if ran:
                # Compare worktree state before and after test execution to detect mutations
                after_worktree = snapshot_worktree_state(repo_path)
                mutations = compare_worktree_states(before_worktree, after_worktree)
                if mutations:
                    worktree_mutations.extend(mutations)
                    claim_discrepancies.extend(mutations)
                    notes_parts.append(f"🚨 TEST-TIME WORKTREE MUTATION: {'; '.join(mutations)}")

                raw_snippet = output[-1000:] if len(output) > 1000 else output
                output_snippet = _redact_test_output(raw_snippet)
                is_understood = False
                if runner_name == "pytest":
                    tests_run, tests_passed, tests_failed, tests_skipped = parse_pytest_output(
                        output
                    )
                    is_understood = (
                        tests_run > 0
                        or "no tests ran" in output.lower()
                        or "collected 0 items" in output.lower()
                        or "0 passed" in output.lower()
                    )
                elif runner_name == "cargo":
                    tests_run, tests_passed, tests_failed, tests_skipped = parse_cargo_output(
                        output
                    )
                    is_understood = tests_run > 0 or ("test result:" in output)
                elif runner_name == "npm":
                    tests_run, tests_passed, tests_failed, tests_skipped = parse_npm_output(output)
                    is_understood = tests_run > 0
                elif runner_name == "go":
                    tests_run, tests_passed, tests_failed, tests_skipped = parse_go_output(output)
                    is_understood = tests_run > 0

                if not is_understood:
                    is_unverified = True
                    tests_run = 0
                    tests_passed = 0
                    tests_failed = 1 if exit_code != 0 else 0
                    notes_parts.append(
                        f"⚠️ UNVERIFIED: Output from runner '{runner_name}' could not be understood. "
                        "Cannot verify test counts or passes from command output."
                    )
                elif tests_run == 0:
                    notes_parts.append(
                        "⚠️ Zero tests were executed (no tests found or collected). "
                        "Test results cannot be verified."
                    )

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
            is_unverified = True
            tests_run = claim.claimed_tests_run or 0
            tests_passed = claim.claimed_tests_passed or 0
            if claim.claimed_tests_run or claim.claimed_tests_passed:
                notes_parts.append(
                    f"⚠️ Test suite execution skipped by request. Claimed test passes ({tests_passed}/{tests_run}) unverified."
                )
            else:
                notes_parts.append("⚠️ Test execution skipped by request. Test results unverified.")

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
        elif is_unverified:
            notes_parts.append("⚠️ Test verification status: UNVERIFIED.")
        elif tests_run == 0:
            notes_parts.append("⚠️ Test verification status: NO TESTS EXECUTED.")
        elif tests_skipped > 0:
            notes_parts.append(
                f"⚠️ Tests executed with skips ({tests_passed} passed, {tests_skipped} skipped)."
            )
        else:
            notes_parts.append(f"✅ Tests passed ({tests_passed} passed).")

        status = CheckStatus.PASS
        if weakened_assertions or exit_code != 0 or len(worktree_mutations) > 0:
            status = CheckStatus.FAIL
        elif (
            trivially_passing
            or tests_skipped > 0
            or len(claim_discrepancies) > 0
            or skip_execution
            or is_unverified
            or tests_run == 0
            or execution_mode == "blocked_no_sandbox"
        ):
            status = CheckStatus.WARN

        return TestVerificationResult(
            status=status,
            runner=runner_name,
            execution_mode=execution_mode,
            is_unverified=is_unverified,
            exit_code=exit_code,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            tests_skipped=tests_skipped,
            weakened_assertions_detected=weakened_assertions,
            trivially_passing_tests_flagged=trivially_passing,
            claim_discrepancies=claim_discrepancies,
            worktree_mutations=worktree_mutations,
            output_snippet=output_snippet.strip() or None,
            notes=" ".join(notes_parts),
        )
