"""Rich terminal console renderer for TrustReport."""

from __future__ import annotations

from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent_verifier.models.trust_report import CheckStatus, TrustReport, Verdict


def _status_badge(status: CheckStatus) -> str:
    if status == CheckStatus.PASS:
        return "[bold green]PASS[/bold green]"
    elif status == CheckStatus.WARN:
        return "[bold yellow]WARN[/bold yellow]"
    elif status == CheckStatus.FAIL:
        return "[bold red]FAIL[/bold red]"
    return "[dim]SKIPPED[/dim]"


def render_trust_report(report: TrustReport, console: Console | None = None) -> None:
    """Render a 30-second color-coded Trust Report to the terminal."""
    con = console or Console()

    # 1. Verdict Header Panel
    if report.verdict == Verdict.VERIFIED:
        verdict_style = "bold green"
        verdict_icon = "🛡️  VERIFIED"
        border_style = "green"
    elif report.verdict == Verdict.SUSPICIOUS:
        verdict_style = "bold yellow"
        verdict_icon = "⚠️  SUSPICIOUS"
        border_style = "yellow"
    else:
        verdict_style = "bold red"
        verdict_icon = "❌ FAILED"
        border_style = "red"

    conf_pct = int(report.confidence_score * 100)
    verdict_text = Text()
    verdict_text.append(f"{verdict_icon}\n", style=verdict_style)
    verdict_text.append(
        f"Confidence: {conf_pct}%  •  Ref: {report.target_ref}  •  Duration: {report.duration_seconds}s\n",
        style="dim",
    )
    if report.summary:
        verdict_text.append(f"\n{report.summary}\n", style="italic")

    con.print(
        Panel(
            verdict_text,
            title="[bold white]agent-verify Trust Report[/bold white]",
            border_style=border_style,
            box=ROUNDED,
            padding=(1, 2),
        )
    )

    # 2. Main Verification Checks Table
    table = Table(
        box=ROUNDED,
        title="[bold]Verification Pillars[/bold]",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Pillar", style="bold", width=22)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Key Findings & Ground Truth Evidence")

    # Row 1: Diff Verification
    diff = report.diff_verification
    diff_details = [
        f"Files: {len(diff.actual_changed_files)} changed (+{diff.lines_added}/-{diff.lines_deleted})"
    ]
    if diff.unclaimed_changes:
        diff_details.append(f"[red]Undeclared: {len(diff.unclaimed_changes)}[/red]")
    if diff.fabricated_claims:
        diff_details.append(f"[yellow]Fabricated: {len(diff.fabricated_claims)}[/yellow]")
    if diff.notes:
        diff_details.append(f"[dim]{diff.notes}[/dim]")
    table.add_row("1. Diff Alignment", _status_badge(diff.status), " • ".join(diff_details))

    # Row 2: Test Verification
    test = report.test_verification
    test_details = [
        f"Runner: {test.runner} ({test.tests_passed} passed, {test.tests_failed} failed, {test.tests_skipped} skipped)"
    ]
    if test.weakened_assertions_detected:
        test_details.append(
            f"[red]Weakened Asserts: {len(test.weakened_assertions_detected)}[/red]"
        )
    if test.trivially_passing_tests_flagged:
        test_details.append(
            f"[yellow]Trivial Tests: {len(test.trivially_passing_tests_flagged)}[/yellow]"
        )
    if test.exit_code != 0:
        test_details.append(f"[red]Exit Code {test.exit_code}[/red]")
    table.add_row("2. Test & Anti-Gaming", _status_badge(test.status), " • ".join(test_details))

    # Row 3: Scope Verification
    scope = report.scope_verification
    scope_details = [f"Blast Risk: [bold]{scope.blast_radius_risk_level.value}[/bold]"]
    if scope.out_of_scope_files:
        scope_details.append(f"[red]{len(scope.out_of_scope_files)} out-of-scope files[/red]")
    if scope.impacted_symbols_count > 0:
        scope_details.append(f"{scope.impacted_symbols_count} symbols impacted")
    if scope.notes:
        scope_details.append(f"[dim]{scope.notes}[/dim]")
    table.add_row("3. Scope & Blast Radius", _status_badge(scope.status), " • ".join(scope_details))

    # Row 4: Spec Compliance
    spec = report.spec_compliance
    spec_score_pct = int(spec.compliance_score * 100)
    spec_details = [f"Compliance: {spec_score_pct}%"]
    if spec.unmet_requirements:
        spec_details.append(f"[red]{len(spec.unmet_requirements)} unmet[/red]")
    if spec.unrequested_drift:
        spec_details.append(f"[yellow]{len(spec.unrequested_drift)} drift items[/yellow]")
    if spec.notes:
        spec_details.append(f"[dim]{spec.notes}[/dim]")
    table.add_row("4. Spec Compliance", _status_badge(spec.status), " • ".join(spec_details))

    con.print(table)

    # 3. Discrepancies & Alerts Section
    alerts = []
    if diff.sensitive_unclaimed_changes:
        alerts.append(
            f"[bold red]🚨 CRITICAL STEALTH MODIFICATIONS:[/bold red] {', '.join(diff.sensitive_unclaimed_changes)}"
        )
    if diff.unclaimed_changes:
        alerts.append(
            f"[bold red]Undeclared Modifications:[/bold red] {', '.join(diff.unclaimed_changes)}"
        )
    if diff.fabricated_claims:
        alerts.append(
            f"[bold yellow]Phantom Claims (Untouched):[/bold yellow] {', '.join(diff.fabricated_claims)}"
        )
    for cd in test.claim_discrepancies:
        alerts.append(f"[bold red]🚨 Test Claim Discrepancy:[/bold red] {cd}")
    for wa in test.weakened_assertions_detected:
        alerts.append(f"[bold red]Anti-Gaming Flag:[/bold red] {wa}")
    for tp in test.trivially_passing_tests_flagged:
        alerts.append(f"[bold yellow]Trivial Test Flag:[/bold yellow] {tp}")
    for fb in scope.forbidden_scope_violations:
        alerts.append(f"[bold red]🚨 FORBIDDEN SCOPE BREACH:[/bold red] {fb}")
    for oos in scope.out_of_scope_files:
        if oos not in scope.forbidden_scope_violations:
            alerts.append(f"[bold red]Scope Violation:[/bold red] {oos}")
    for hal in spec.hallucinated_claims:
        alerts.append(f"[bold red]🚨 Hallucinated Feature Claim:[/bold red] {hal}")
    for unmet in spec.unmet_requirements:
        alerts.append(f"[bold red]Unmet Requirement:[/bold red] {unmet}")
    for drift in spec.unrequested_drift:
        alerts.append(f"[bold yellow]Spec Drift:[/bold yellow] {drift}")

    if alerts:
        con.print("\n[bold red]⚠️  Active Discrepancies & Flags:[/bold red]")
        for alert in alerts:
            con.print(f"  • {alert}")

    # 4. Interactive Clarifications Section (if any)
    if report.user_clarifications:
        con.print("\n[bold cyan]💬 Interactive User Clarifications & Audit Trail:[/bold cyan]")
        for c in report.user_clarifications:
            auth_badge = (
                "[bold green]AUTHORIZED[/bold green]"
                if c.authorized
                else "[bold red]REJECTED[/bold red]"
            )
            con.print(
                f"  • [{c.topic}] [bold]{c.target}[/bold]: {auth_badge} -- [italic]{c.user_response}[/italic]"
            )

    con.print()
