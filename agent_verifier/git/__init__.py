"""Git repository integration and hook installation modules."""

from agent_verifier.git.diff_parser import (
    FileDiff,
    GitDiffError,
    GitDiffSummary,
    parse_git_diff,
    parse_unified_diff,
)
from agent_verifier.git.hook_installer import (
    install_pre_push_hook,
    is_hook_installed,
    uninstall_hook,
)
from agent_verifier.git.worktree_guard import (
    compare_worktree_states,
    find_ignored_sensitive_files,
    snapshot_worktree_state,
)

__all__ = [
    "FileDiff",
    "GitDiffError",
    "GitDiffSummary",
    "parse_git_diff",
    "parse_unified_diff",
    "install_pre_push_hook",
    "uninstall_hook",
    "is_hook_installed",
    "find_ignored_sensitive_files",
    "snapshot_worktree_state",
    "compare_worktree_states",
]
