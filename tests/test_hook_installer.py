"""Unit tests for git hook installer."""

from pathlib import Path

from agent_verifier.git.hook_installer import (
    install_pre_push_hook,
    is_hook_installed,
    uninstall_hook,
)


def test_hook_install_and_uninstall(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True)

    # Initial state: not installed
    assert is_hook_installed(tmp_path) is False

    # Install hook
    hook_path = install_pre_push_hook(tmp_path)
    assert hook_path.exists()
    assert is_hook_installed(tmp_path) is True

    content = hook_path.read_text(encoding="utf-8")
    assert "agent-verify" in content
    assert "pre-push" in hook_path.name

    # Idempotent re-install
    hook_path_again = install_pre_push_hook(tmp_path)
    assert hook_path_again == hook_path

    # Uninstall hook
    removed = uninstall_hook(tmp_path)
    assert removed is True
    assert is_hook_installed(tmp_path) is False
    assert not hook_path.exists()
