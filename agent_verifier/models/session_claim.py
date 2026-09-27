"""Schema and parsers for agent self-reported session claims."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field


class SessionClaim(BaseModel):
    """The claims made by an AI coding agent regarding its work."""

    claimed_files: list[str] = Field(
        default_factory=list,
        description="List of file paths the agent claims to have created or modified",
    )
    claimed_tests_run: int | None = Field(
        default=None, description="Number of tests the agent claims to have run"
    )
    claimed_tests_passed: int | None = Field(
        default=None, description="Number of tests the agent claims passed"
    )
    claimed_requirements_satisfied: list[str] = Field(
        default_factory=list,
        description="Specific requirements or tickets the agent claims to have fulfilled",
    )
    summary: str = Field(
        default="", description="Natural language summary of agent actions provided to user"
    )
    spec_text: str | None = Field(
        default=None, description="Task prompt or specification against which work was performed"
    )
    allowed_paths: list[str] = Field(
        default_factory=list, description="Approved directories or glob patterns for this task"
    )

    @classmethod
    def from_file(cls, filepath: str | Path) -> SessionClaim:
        """Load session claim from a JSON file or markdown summary."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Claim file not found: {path}")

        raw = path.read_text(encoding="utf-8")
        # Try JSON first
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return cls(**data)
        except json.JSONDecodeError:
            pass

        # Fallback: parse markdown/unstructured summary
        return cls.from_summary(raw)

    @classmethod
    def from_summary(cls, summary_text: str, spec_text: str | None = None) -> SessionClaim:
        """Heuristically extract claimed files and stats from a freeform agent summary."""
        # Find file paths mentioned like `path/to/file.ext` or ```path/file.py``` or [file](path)
        # Matches patterns like src/foo.py, test_bar.py, lib/utils.ts, etc.
        path_pattern = re.compile(
            r'(?:[\s`\'"\(]|^)([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]{1,6})(?:[\s`\'"\)]|$)'
        )
        found_paths = set()
        for line in summary_text.splitlines():
            # Check lines mentioning modify, create, update, changed, edit, file
            for match in path_pattern.finditer(line):
                cand = match.group(1).strip().replace("\\", "/")
                # Filter out obvious false positives like e.g., i.e., v1.0, 3.11.2
                if not any(
                    cand.endswith(ext)
                    for ext in [
                        ".py",
                        ".ts",
                        ".js",
                        ".jsx",
                        ".tsx",
                        ".html",
                        ".css",
                        ".json",
                        ".md",
                        ".yml",
                        ".yaml",
                        ".toml",
                        ".rs",
                        ".go",
                        ".java",
                        ".c",
                        ".cpp",
                    ]
                ):
                    continue
                # Normalize leading ./
                if cand.startswith("./"):
                    cand = cand[2:]
                found_paths.add(cand)

        # Detect test numbers if present
        tests_run = None
        tests_passed = None
        run_match = re.search(r"(\d+)\s+(?:tests?\s+passed|passed)", summary_text, re.IGNORECASE)
        if run_match:
            tests_passed = int(run_match.group(1))

        total_match = re.search(r"(?:ran|running)\s+(\d+)\s+tests?", summary_text, re.IGNORECASE)
        if total_match:
            tests_run = int(total_match.group(1))
        elif tests_passed is not None:
            tests_run = tests_passed

        return cls(
            claimed_files=sorted(list(found_paths)),
            claimed_tests_run=tests_run,
            claimed_tests_passed=tests_passed,
            summary=summary_text,
            spec_text=spec_text,
        )
