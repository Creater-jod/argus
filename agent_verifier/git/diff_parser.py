"""Git diff parsing and file change extraction."""

from __future__ import annotations

import re
from pathlib import Path

import git
from pydantic import BaseModel, Field


class FileDiff(BaseModel):
    """Structured representation of changes in a single file."""

    path: str
    old_path: str | None = None
    change_type: str = (
        "M"  # 'M' (modified), 'A' (added), 'D' (deleted), 'R' (renamed), 'U' (untracked)
    )
    lines_added: int = 0
    lines_deleted: int = 0
    added_lines: list[tuple[int, str]] = Field(default_factory=list)  # (line_number, content)
    deleted_lines: list[tuple[int, str]] = Field(default_factory=list)  # (line_number, content)
    patch: str = ""


class GitDiffSummary(BaseModel):
    """Aggregated summary of changes across a git repository."""

    repo_path: str
    base_ref: str | None = None
    files: dict[str, FileDiff] = Field(default_factory=dict)
    untracked_files: list[str] = Field(default_factory=list)
    total_lines_added: int = 0
    total_lines_deleted: int = 0

    @property
    def changed_file_paths(self) -> list[str]:
        """Return list of all modified, added, and untracked file paths."""
        return sorted(list(self.files.keys()))


def _normalize_path(p: str | Path) -> str:
    """Normalize file path to use forward slashes and no leading ./."""
    s = str(p).replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    return s


def parse_unified_diff(diff_text: str) -> dict[str, FileDiff]:
    """Parse raw git unified diff text into FileDiff objects."""
    files: dict[str, FileDiff] = {}
    if not diff_text.strip():
        return files

    chunks = re.split(r"(?m)^diff --git ", diff_text)
    for chunk in chunks:
        if not chunk.strip():
            continue

        lines = chunk.splitlines()
        first_line = lines[0]
        # Example: a/src/auth.py b/src/auth.py
        match = re.match(r"a/(.+?)\s+b/(.+)", first_line)
        if not match:
            continue

        old_file = _normalize_path(match.group(1))
        new_file = _normalize_path(match.group(2))
        file_path = new_file

        change_type = "M"
        if "new file mode" in chunk:
            change_type = "A"
        elif "deleted file mode" in chunk:
            change_type = "D"
            file_path = old_file
        elif "rename from" in chunk:
            change_type = "R"

        added_lines: list[tuple[int, str]] = []
        deleted_lines: list[tuple[int, str]] = []
        lines_added = 0
        lines_deleted = 0

        curr_new_line = 1
        curr_old_line = 1

        for line in lines:
            if line.startswith("@@"):
                # Parse hunk header: @@ -old_start,old_len +new_start,new_len @@
                hunk_match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if hunk_match:
                    curr_old_line = int(hunk_match.group(1))
                    curr_new_line = int(hunk_match.group(2))
                continue

            if line.startswith("+") and not line.startswith("+++"):
                lines_added += 1
                added_lines.append((curr_new_line, line[1:]))
                curr_new_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                lines_deleted += 1
                deleted_lines.append((curr_old_line, line[1:]))
                curr_old_line += 1
            elif not line.startswith("\\"):
                curr_new_line += 1
                curr_old_line += 1

        files[file_path] = FileDiff(
            path=file_path,
            old_path=old_file if change_type == "R" else None,
            change_type=change_type,
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            added_lines=added_lines,
            deleted_lines=deleted_lines,
            patch=chunk,
        )

    return files


def parse_git_diff(
    repo_path: str | Path,
    base_ref: str | None = None,
    include_untracked: bool = True,
) -> GitDiffSummary:
    """Extract and parse git diff for a repository.

    If base_ref is None, diffs working tree against HEAD (or staging/unstaged).
    Also collects untracked files to detect undeclared new files.
    """
    path = Path(repo_path).resolve()
    try:
        repo = git.Repo(path)
    except (git.InvalidGitRepositoryError, git.NoSuchPathError) as e:
        raise ValueError(f"Not a valid git repository: {path}") from e

    files: dict[str, FileDiff] = {}
    untracked_paths: list[str] = []

    # 1. Inspect diff
    try:
        # Check if repo has any commits
        has_commits = False
        try:
            _ = repo.head.commit
            has_commits = True
        except ValueError:
            has_commits = False

        if has_commits:
            if base_ref:
                raw_diff = repo.git.diff(base_ref)
            else:
                # Working tree diff (unstaged + staged against HEAD)
                raw_diff = repo.git.diff("HEAD")
                # Also check staged changes if HEAD is not committed yet
                staged_diff = repo.git.diff("--cached")
                if staged_diff and staged_diff != raw_diff:
                    # Combine non-redundantly or take HEAD diff
                    raw_diff = repo.git.diff("HEAD")

            parsed = parse_unified_diff(raw_diff)
            files.update(parsed)
        else:
            # New repo without initial commit: check staged files
            staged_diff = repo.git.diff("--cached")
            if staged_diff:
                files.update(parse_unified_diff(staged_diff))

    except git.GitCommandError:
        # Subprocess fallback or empty diff
        pass

    # 2. Inspect untracked files
    if include_untracked:
        for untracked in repo.untracked_files:
            norm = _normalize_path(untracked)
            untracked_paths.append(norm)
            if norm not in files:
                file_full_path = path / untracked
                added_lines: list[tuple[int, str]] = []
                lines_added = 0
                if file_full_path.is_file():
                    try:
                        content = file_full_path.read_text(encoding="utf-8", errors="replace")
                        for idx, line in enumerate(content.splitlines(), start=1):
                            added_lines.append((idx, line))
                        lines_added = len(added_lines)
                    except Exception:
                        pass

                files[norm] = FileDiff(
                    path=norm,
                    change_type="U",
                    lines_added=lines_added,
                    lines_deleted=0,
                    added_lines=added_lines,
                    patch=f"Untracked new file: {norm}",
                )

    total_added = sum(f.lines_added for f in files.values())
    total_deleted = sum(f.lines_deleted for f in files.values())

    return GitDiffSummary(
        repo_path=str(path),
        base_ref=base_ref,
        files=files,
        untracked_files=untracked_paths,
        total_lines_added=total_added,
        total_lines_deleted=total_deleted,
    )
