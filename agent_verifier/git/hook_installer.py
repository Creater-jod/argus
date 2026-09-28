"""Git hook installation and management for agent-verify."""

from __future__ import annotations

import stat
from pathlib import Path

HOOK_SCRIPT_TEMPLATE = """#!/bin/sh
# Argus (agent-verify) pre-push hook
# Audits agent session claims before code is pushed to remote repositories.

echo "👁️  [Argus] Running pre-push verification check..."

# Run Argus verification in strict mode (rejects both FAILED and SUSPICIOUS)
if command -v argus >/dev/null 2>&1; then
    argus verify --repo "$(git rev-parse --show-toplevel)" --format rich --strict
else
    agent-verify verify --repo "$(git rev-parse --show-toplevel)" --format rich --strict
fi
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ [Argus] Pre-push verification FAILED or flagged SUSPICIOUS changes."
    echo "   Push aborted. Review the findings above or bypass with --no-verify if intentional."
    exit 1
fi

echo "✅ [Argus] Verification passed. Proceeding with push."
exit 0
"""


def get_git_hooks_dir(repo_path: Path | str) -> Path:
    """Resolve the git hooks directory for a given repository."""
    path = Path(repo_path).resolve()
    git_dir = path / ".git"
    if not git_dir.exists():
        raise ValueError(f"Target directory is not a git repository: {path}")

    # Check if .git is a file (e.g. worktree)
    if git_dir.is_file():
        content = git_dir.read_text(encoding="utf-8").strip()
        if content.startswith("gitdir:"):
            real_git_dir = Path(content.replace("gitdir:", "").strip())
            return real_git_dir / "hooks"

    return git_dir / "hooks"


def install_pre_push_hook(repo_path: Path | str, force: bool = False) -> Path:
    """Install the agent-verify pre-push hook script into .git/hooks/pre-push."""
    hooks_dir = get_git_hooks_dir(repo_path)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_file = hooks_dir / "pre-push"
    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8", errors="replace")
        is_argus = ("agent-verify" in existing) or ("argus" in existing)
        if is_argus and not force:
            return hook_file  # Already installed
        if not is_argus:
            # Back up existing hook, but refuse to overwrite an existing backup
            backup = hooks_dir / "pre-push.backup"
            if backup.exists():
                if not force:
                    raise FileExistsError(
                        f"Pre-push hook backup already exists at {backup}. Refusing to overwrite backup or replace existing hook."
                    )
                # When force=True, proceed with installing the hook while strictly preserving the existing backup
            else:
                backup.write_text(existing, encoding="utf-8")

    hook_file.write_text(HOOK_SCRIPT_TEMPLATE, encoding="utf-8")

    # Make executable on Unix-like platforms
    try:
        current_mode = hook_file.stat().st_mode
        hook_file.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

    return hook_file


def uninstall_hook(repo_path: Path | str, hook_name: str = "pre-push") -> bool:
    """Remove an installed agent-verify hook."""
    hooks_dir = get_git_hooks_dir(repo_path)
    hook_file = hooks_dir / hook_name
    if not hook_file.exists():
        return False

    content = hook_file.read_text(encoding="utf-8", errors="replace")
    if "agent-verify" in content or "argus" in content:
        hook_file.unlink()
        backup = hooks_dir / f"{hook_name}.backup"
        if backup.exists():
            backup.rename(hook_file)
        return True
    return False


def is_hook_installed(repo_path: Path | str, hook_name: str = "pre-push") -> bool:
    """Check if the agent-verify hook is currently installed."""
    try:
        hooks_dir = get_git_hooks_dir(repo_path)
        hook_file = hooks_dir / hook_name
        if not hook_file.exists():
            return False
        content = hook_file.read_text(encoding="utf-8", errors="replace")
        return ("agent-verify" in content) or ("argus" in content)
    except Exception:
        return False
