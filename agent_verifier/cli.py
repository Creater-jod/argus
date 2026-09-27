"""Typer CLI interface for agent-verify."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

# Ensure UTF-8 output on Windows legacy terminals
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import agent_verifier
from agent_verifier.git.hook_installer import (
    install_pre_push_hook,
    is_hook_installed,
    uninstall_hook,
)
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import Verdict
from agent_verifier.pipeline import VerificationPipeline
from agent_verifier.report.console import render_trust_report
from agent_verifier.report.json_export import export_json, load_json_report
from agent_verifier.report.markdown import export_markdown

app = typer.Typer(
    name="agent-verify",
    help="AI Agent Verification Layer -- audits agent claims in 30 seconds.",
    add_completion=False,
)
console = Console(legacy_windows=False)


@app.command()
def verify(
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-r",
        help="Path to git repository to audit",
    ),
    summary: str | None = typer.Option(
        None,
        "--summary",
        "-s",
        help="Agent's natural language summary of changes",
    ),
    claim: Path | None = typer.Option(
        None,
        "--claim",
        "-c",
        help="Path to session_claim.json or summary markdown file",
    ),
    spec: Path | None = typer.Option(
        None,
        "--spec",
        help="Path to task specification or requirements markdown file",
    ),
    base_ref: str | None = typer.Option(
        None,
        "--base-ref",
        "-b",
        help="Git ref to diff against (e.g. 'main', 'origin/main', 'HEAD~1')",
    ),
    allowed_path: list[str] | None = typer.Option(
        None,
        "--allowed-path",
        "-a",
        help="Allowed path glob pattern(s). Can be repeated.",
    ),
    skip_tests: bool = typer.Option(
        False,
        "--skip-tests",
        help="Skip executing test suite in isolated subprocess",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save report to specified file path",
    ),
    format: str = typer.Option(
        "rich",
        "--format",
        "-f",
        help="Output format: 'rich' (terminal UI), 'json', or 'markdown'",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit with non-zero code on SUSPICIOUS as well as FAILED",
    ),
) -> None:
    """Run full verification pipeline against AI agent claims."""
    # 1. Resolve Claim
    session_claim: SessionClaim
    if claim and claim.is_file():
        session_claim = SessionClaim.from_file(claim)
    elif summary:
        spec_text = (
            spec.read_text(encoding="utf-8", errors="replace")
            if (spec and spec.is_file())
            else None
        )
        session_claim = SessionClaim.from_summary(summary, spec_text=spec_text)
    else:
        # Empty claim: diff everything in repo without prior claims
        session_claim = SessionClaim()

    if allowed_path:
        session_claim.allowed_paths.extend(allowed_path)

    # 2. Run Pipeline
    pipeline = VerificationPipeline()
    try:
        report = pipeline.run(
            repo_path=repo,
            claim=session_claim,
            base_ref=base_ref,
            spec_path=str(spec) if spec else None,
            skip_tests=skip_tests,
        )
    except Exception as e:
        console.print(f"[bold red]Verification Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # 3. Output formatting
    fmt = format.lower().strip()
    if fmt == "json":
        json_output = report.model_dump_json(indent=2)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json_output, encoding="utf-8")
            console.print(f"[green]Report saved to {output}[/green]")
        else:
            print(json_output)
    elif fmt == "markdown":
        md_output = export_markdown(report)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(md_output, encoding="utf-8")
            console.print(f"[green]Markdown report saved to {output}[/green]")
        else:
            print(md_output)
    else:
        # Default: rich console rendering
        render_trust_report(report, console=console)
        if output:
            export_json(report, output)
            console.print(f"[dim]JSON report also saved to {output}[/dim]")

    # 4. Exit Code Handling
    if report.verdict == Verdict.FAILED:
        raise typer.Exit(code=1)
    elif report.verdict == Verdict.SUSPICIOUS and strict:
        raise typer.Exit(code=2)
    raise typer.Exit(code=0)


@app.command()
def install_hook(
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-r",
        help="Git repository path where the hook will be installed",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing pre-push hook if present",
    ),
) -> None:
    """Install agent-verify pre-push git hook to automatically audit commits before push."""
    try:
        hook_path = install_pre_push_hook(repo, force=force)
        console.print(
            f"[bold green]✅ Pre-push hook successfully installed at:[/bold green] [cyan]{hook_path}[/cyan]\n"
            "Commits will now be automatically audited before every 'git push'."
        )
    except Exception as e:
        console.print(f"[bold red]❌ Failed to install hook:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def uninstall_git_hook(
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-r",
        help="Git repository path",
    ),
) -> None:
    """Uninstall the agent-verify pre-push git hook."""
    try:
        removed = uninstall_hook(repo)
        if removed:
            console.print("[green]✅ agent-verify hook uninstalled successfully.[/green]")
        else:
            console.print("[yellow]Hook was not found or not managed by agent-verify.[/yellow]")
    except Exception as e:
        console.print(f"[bold red]❌ Failed to uninstall hook:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def check_hook(
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        "-r",
        help="Git repository path",
    ),
) -> None:
    """Check if the agent-verify pre-push hook is installed."""
    installed = is_hook_installed(repo)
    if installed:
        console.print("[bold green]✅ agent-verify hook is ACTIVE.[/bold green]")
    else:
        console.print("[bold yellow]⚠️  agent-verify hook is NOT installed.[/bold yellow]")


@app.command()
def report(
    report_file: Path = typer.Argument(
        ...,
        help="Path to an existing JSON trust report",
    ),
    format: str = typer.Option(
        "rich",
        "--format",
        "-f",
        help="Output format: 'rich', 'json', or 'markdown'",
    ),
) -> None:
    """Render a previously saved Trust Report."""
    try:
        loaded = load_json_report(report_file)
    except Exception as e:
        console.print(f"[bold red]Error loading report:[/bold red] {e}")
        raise typer.Exit(code=1)

    fmt = format.lower().strip()
    if fmt == "json":
        print(loaded.model_dump_json(indent=2))
    elif fmt == "markdown":
        print(export_markdown(loaded))
    else:
        render_trust_report(loaded, console=console)


@app.command()
def version() -> None:
    """Display agent-verify version and runtime info."""
    console.print(
        f"[bold cyan]agent-verify[/bold cyan] version [bold white]{agent_verifier.__version__}[/bold white]"
    )


if __name__ == "__main__":
    app()
