"""Interactive intake interview to grill developers/agents on what they are building."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agent_verifier.models.session_claim import SessionClaim


class IntakeInterview(BaseModel):
    """Structured capture of developer intent, boundaries, and acceptance criteria."""

    objective: str = Field(..., description="Full description of what is being built")
    allowed_scope: list[str] = Field(
        default_factory=list, description="Permitted directory or file paths"
    )
    forbidden_scope: list[str] = Field(
        default_factory=list, description="Off-limits directories, schemas, secrets"
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list, description="Specific criteria that must pass"
    )
    test_expectations: str = Field(
        default="", description="Required test commands and passing expectations"
    )
    expected_symbols: list[str] = Field(
        default_factory=list,
        description="Key function, method, or class names that must be present in code",
    )
    constraints: str = Field(
        default="", description="Architecture, performance, or backwards-compatibility rules"
    )

    def to_markdown_spec(self) -> str:
        """Convert interview results into a formal task specification markdown document."""
        lines = [
            "# 📋 Task Specification",
            "",
            "> Formally captured via `agent-verify interview` interactive intake.",
            "",
            "## 🎯 Primary Objective",
            "",
            self.objective.strip(),
            "",
            "## 📦 Allowed Scope Boundaries",
            "",
        ]
        if self.allowed_scope:
            for s in self.allowed_scope:
                lines.append(f"- `{s}`")
        else:
            lines.append("- *(Entire repository scope permitted)*")
        lines.append("")

        if self.forbidden_scope:
            lines.extend(
                [
                    "## 🚫 Off-Limits / Forbidden Scope",
                    "",
                ]
            )
            for f in self.forbidden_scope:
                lines.append(f"- 🛑 `{f}` (Do NOT modify)")
            lines.append("")

        lines.extend(
            [
                "## ✅ Acceptance Criteria",
                "",
            ]
        )
        if self.acceptance_criteria:
            for ac in self.acceptance_criteria:
                lines.append(f"- [ ] {ac}")
        else:
            lines.append("- [ ] Fulfill all requirements specified in primary objective.")
        lines.append("")

        if self.test_expectations:
            lines.extend(
                [
                    "## 🧪 Testing & Verification Requirements",
                    "",
                    self.test_expectations.strip(),
                    "",
                ]
            )

        if self.expected_symbols:
            lines.extend(
                [
                    "## 🧩 Required Code Symbols",
                    "",
                ]
            )
            for sym in self.expected_symbols:
                lines.append(f"- `def {sym}` or `class {sym}`")
            lines.append("")

        if self.constraints:
            lines.extend(
                [
                    "## ⚠️ Constraints & Edge Cases",
                    "",
                    self.constraints.strip(),
                    "",
                ]
            )

        return "\n".join(lines)

    def to_session_claim(self) -> SessionClaim:
        """Create baseline SessionClaim from interview parameters."""
        return SessionClaim(
            allowed_paths=self.allowed_scope,
            forbidden_paths=self.forbidden_scope,
            spec_text=self.to_markdown_spec(),
            summary=self.objective,
        )


def run_intake_interview(
    console: Console | None = None,
    prompt_func: Callable[[str], str] | None = None,
) -> IntakeInterview:
    """Conduct an interactive terminal interview to capture full requirements."""
    con = console or Console()

    con.print(
        Panel(
            "[bold white]agent-verify Intake Interview (Grill-Me)[/bold white]\n"
            "[dim]Answer the following questions to establish verified scope and acceptance boundaries.[/dim]",
            border_style="cyan",
        )
    )

    def ask(prompt_text: str, default: str = "") -> str:
        if prompt_func:
            return prompt_func(prompt_text)
        return Prompt.ask(
            f"[bold cyan]? {prompt_text}[/bold cyan]", default=default, console=con
        ).strip()

    # 1. Objective
    con.print("\n[bold]1. Objective & Requirements[/bold]")
    objective = ask("What feature, bug fix, or refactor are you building? (Full details)")
    while not objective:
        con.print(
            "[yellow]Objective cannot be empty. Please provide details of what you are building.[/yellow]"
        )
        objective = ask("What feature, bug fix, or refactor are you building? (Full details)")

    # 2. Allowed Scope
    con.print("\n[bold]2. Scope Boundaries[/bold]")
    scope_raw = ask(
        "Which files or directories are allowed to be modified? (comma-separated, e.g. 'src/auth/*, tests/*')",
        default="",
    )
    allowed_scope = [s.strip() for s in scope_raw.split(",") if s.strip()]

    # 3. Forbidden Scope
    forbidden_raw = ask(
        "Are any files or directories strictly OFF-LIMITS? (e.g. '.env, db/migrations/*, billing/*')",
        default="",
    )
    forbidden_scope = [f.strip() for f in forbidden_raw.split(",") if f.strip()]

    # 4. Acceptance Criteria
    con.print("\n[bold]3. Acceptance Criteria & Test Expectations[/bold]")
    criteria_raw = ask(
        "List key acceptance criteria (separate with semicolons ';'):",
        default="",
    )
    acceptance_criteria = [c.strip() for c in criteria_raw.split(";") if c.strip()]

    # 5. Required Code Symbols
    symbols_raw = ask(
        "What specific functions, classes, or endpoints must be implemented? (comma-separated, e.g. 'validate_jwt, check_expiration')",
        default="",
    )
    expected_symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]

    # 6. Testing expectations
    test_expectations = ask(
        "What tests must be executed? (e.g. 'pytest -q', 'npm test')",
        default="pytest",
    )

    # 7. Constraints
    con.print("\n[bold]4. Constraints & Edge Cases[/bold]")
    constraints = ask(
        "Any performance, backwards-compatibility, or security edge cases to respect?",
        default="No breaking changes to public APIs.",
    )

    interview = IntakeInterview(
        objective=objective,
        allowed_scope=allowed_scope,
        forbidden_scope=forbidden_scope,
        acceptance_criteria=acceptance_criteria,
        expected_symbols=expected_symbols,
        test_expectations=test_expectations,
        constraints=constraints,
    )

    con.print("\n[bold green]✅ Interview complete. Requirements established.[/bold green]\n")
    return interview
