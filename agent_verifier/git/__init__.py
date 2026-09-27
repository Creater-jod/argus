"""Git repository integration and hook installation modules."""

from agent_verifier.git.diff_parser import (
    FileDiff,
    GitDiffSummary,
    parse_git_diff,
    parse_unified_diff,
)
from agent_verifier.git.hook_installer import (
    install_pre_push_hook,
    is_hook_installed,
    uninstall_hook,
)

__all__ = [
    "FileDiff",
    "GitDiffSummary",
    "parse_git_diff",
    "parse_unified_diff",
    "install_pre_push_hook",
    "uninstall_hook",
    "is_hook_installed",
]
