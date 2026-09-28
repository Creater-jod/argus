"""Worktree state tracking, mutation detection, and ignored sensitive file auditing.

Provides tools to snapshot repository state before untrusted test execution,
detect test-time worktree pollution/tampering, and surface ignored sensitive files
without leaking their secret contents.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import git

logger = logging.getLogger("agent_verify.worktree")

# Patterns for sensitive files that often appear in .gitignore
SENSITIVE_IGNORED_PATTERNS = [
    ".env",
    ".env.*",
    "*.env",
    "*credential*",
    "*secret*",
    "*token*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
]


def _hash_file(file_path: Path) -> str:
    """Compute SHA-256 hash of a file's content without exposing its contents.

    Explicitly handles symlinks and read/permission errors.
    """
    h = hashlib.sha256()
    try:
        if file_path.is_symlink():
            target = file_path.readlink()
            h.update(f"symlink:{target}".encode("utf-8", errors="replace"))
            if file_path.exists() and file_path.is_file():
                with open(file_path, "rb") as f:
                    while chunk := f.read(65536):
                        h.update(chunk)
            return h.hexdigest()

        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except (PermissionError, OSError) as e:
        logger.warning("Could not read file %s for hashing: %s", file_path, e)
        return f"unreadable:{type(e).__name__}"
    except Exception as e:
        logger.warning("Unexpected error hashing %s: %s", file_path, e)
        return f"error:{type(e).__name__}"


def find_ignored_sensitive_files(repo_path: Path | str) -> list[dict[str, str]]:
    """Identify relevant ignored credential-like files in the repository.

    Identifies files matching sensitive patterns that are ignored by git.
    Does NOT copy or print file contents; only records relative path and SHA-256 hash.
    Explains that ignored files have no Git baseline.
    """
    repo_p = Path(repo_path).resolve()
    try:
        repo = git.Repo(repo_p)
    except Exception:
        return []

    results: list[dict[str, str]] = []
    # Directories to ignore (dependencies, package caches, virtual environments)
    ignored_dirs = {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        "site-packages",
        "dist",
        "build",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }

    # Collect candidates by globbing sensitive patterns
    seen_paths: set[Path] = set()
    candidate_files: list[Path] = []

    for pattern in SENSITIVE_IGNORED_PATTERNS:
        try:
            for p in repo_p.rglob(pattern):
                if (
                    p.is_file()
                    and not any(d in p.parts for d in ignored_dirs)
                    and p not in seen_paths
                ):
                    seen_paths.add(p)
                    candidate_files.append(p)
        except Exception:
            continue

    if not candidate_files:
        return results

    # Determine which candidate files are ignored by git
    for cand in candidate_files:
        try:
            rel_str = cand.relative_to(repo_p).as_posix()
            # Check if git ignores this file
            ignored = repo.ignored(str(cand))
            if ignored:
                file_hash = _hash_file(cand)
                results.append(
                    {
                        "path": rel_str,
                        "sha256": file_hash[:16] + "..." if len(file_hash) > 16 else file_hash,
                        "full_sha256": file_hash,
                        "note": "Ignored by Git (no Git baseline; cannot be verified against commit history)",
                    }
                )
        except Exception as e:
            logger.debug("Failed checking ignore status for %s: %s", cand, e)

    return results


def snapshot_worktree_state(repo_path: Path | str) -> dict[str, Any]:
    """Capture a snapshot of tracked, untracked, and relevant ignored files."""
    repo_p = Path(repo_path).resolve()
    try:
        repo = git.Repo(repo_p)
        # 1. Tracked file porcelain diff / status
        tracked_status = repo.git.status("--porcelain", "-uno")
        # 2. Untracked files set
        untracked = set(repo.untracked_files)
    except Exception as e:
        logger.warning("Failed capturing git worktree snapshot: %s", e)
        tracked_status = ""
        untracked = set()

    # 3. Ignored sensitive files and their hashes
    ignored_sensitive = {}
    for item in find_ignored_sensitive_files(repo_p):
        ignored_sensitive[item["path"]] = item.get("full_sha256", "")

    return {
        "tracked_status": tracked_status,
        "untracked_files": untracked,
        "ignored_sensitive": ignored_sensitive,
    }


def compare_worktree_states(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    """Compare repository state before and after test execution.

    Detects any test-time mutations to tracked files, newly created untracked files,
    or altered ignored sensitive files.
    """
    mutations: list[str] = []

    # 1. Tracked changes
    if before.get("tracked_status") != after.get("tracked_status"):
        mutations.append("Tracked files were modified or staged during test execution")

    # 2. Untracked file changes
    before_untracked: set[str] = before.get("untracked_files", set())
    after_untracked: set[str] = after.get("untracked_files", set())
    new_untracked = sorted(list(after_untracked - before_untracked))
    if new_untracked:
        mutations.append(
            f"Test execution created {len(new_untracked)} new untracked file(s): {', '.join(new_untracked[:5])}"
        )

    # 3. Ignored sensitive files
    before_ignored: dict[str, str] = before.get("ignored_sensitive", {})
    after_ignored: dict[str, str] = after.get("ignored_sensitive", {})

    for path, b_hash in before_ignored.items():
        if path not in after_ignored:
            mutations.append(f"Ignored sensitive file was deleted during test execution: {path}")
        elif b_hash != after_ignored[path]:
            mutations.append(
                f"Ignored sensitive file was modified during test execution: {path} (hash changed)"
            )

    for path in after_ignored:
        if path not in before_ignored:
            mutations.append(f"Ignored sensitive file was created during test execution: {path}")

    return mutations
