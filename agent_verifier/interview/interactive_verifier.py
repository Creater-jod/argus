"""Interactive verification session questioning users on detected discrepancies."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from agent_verifier.models.trust_report import (
    CheckStatus,
    TrustReport,
    UserClarification,
    Verdict,
)


class InteractiveVerifierSession:
    """Interactively interviews the user about any discrepancies found during verification."""

    def __init__(
        self,
        console: Console | None = None,
        confirm_func: Callable[[str], bool] | None = None,
        prompt_func: Callable[[str], str] | None = None,
    ):
        self.con = console or Console()
        self.confirm_func = confirm_func
        self.prompt_func = prompt_func

    def _ask_confirm(self, question: str, default: bool = False) -> bool:
        if self.confirm_func:
            return self.confirm_func(question)
        return Confirm.ask(
            f"[bold yellow]? {question}[/bold yellow]", default=default, console=self.con
        )

    def review_discrepancies(self, report: TrustReport) -> TrustReport:
        """Walk through all detected discrepancies and question the user for confirmation."""
        # Only prompt if discrepancies or non-PASS status exist
        has_issues = (
            report.verdict != Verdict.VERIFIED
            or bool(report.diff_verification.unclaimed_changes)
            or bool(report.test_verification.weakened_assertions_detected)
            or bool(report.scope_verification.out_of_scope_files)
            or bool(report.spec_compliance.unrequested_drift)
        )

        if not has_issues:
            return report

        self.con.print(
            Panel(
                "[bold white]agent-verify Interactive Discrepancy Review[/bold white]\n"
                "[dim]Reviewing detected anomalies. Answer each question to confirm or reject deviations.[/dim]",
                border_style="yellow",
            )
        )

        clarifications: list[UserClarification] = []
        approved_unclaimed: list[str] = []
        approved_scope_exceptions: list[str] = []

        # 1. Question on Sensitive Undeclared Files
        for sensitive_file in list(report.diff_verification.sensitive_unclaimed_changes):
            q = f"🚨 CRITICAL SECURITY ALERT: Sensitive file '{sensitive_file}' was modified without declaration. Did you intentionally authorize this change?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="sensitive_undeclared_file",
                    target=sensitive_file,
                    question=q,
                    user_response="Authorized sensitive modification"
                    if approved
                    else "Rejected as unauthorized stealth modification",
                    authorized=approved,
                )
            )
            if approved:
                approved_unclaimed.append(sensitive_file)

        # 2. Question on Undeclared Files
        for unclaimed_file in list(report.diff_verification.unclaimed_changes):
            if unclaimed_file in report.diff_verification.sensitive_unclaimed_changes:
                continue
            q = f"File '{unclaimed_file}' was modified in git but never declared by the agent. Did you intend to modify this file?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="undeclared_file",
                    target=unclaimed_file,
                    question=q,
                    user_response="Approved"
                    if approved
                    else "Rejected as unauthorized modification",
                    authorized=approved,
                )
            )
            if approved:
                approved_unclaimed.append(unclaimed_file)

        # 3. Question on Test Claim Discrepancies
        for disc in list(report.test_verification.claim_discrepancies):
            q = f"🚨 Test claim mismatch: {disc}. Was this discrepancy intentional or acceptable?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="test_claim_discrepancy",
                    target=disc,
                    question=q,
                    user_response="Authorized discrepancy"
                    if approved
                    else "Rejected as deceptive test reporting",
                    authorized=approved,
                )
            )

        # 4. Question on Weakened Assertions
        for flag in list(report.test_verification.weakened_assertions_detected):
            q = f"Anti-gaming alert: {flag}. Was this assertion deliberately modified or deleted with your approval?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="weakened_assertion",
                    target=flag.split(" - ")[0] if " - " in flag else flag,
                    question=q,
                    user_response="Authorized test change"
                    if approved
                    else "Rejected as test gaming",
                    authorized=approved,
                )
            )

        # 5. Question on Forbidden Scope Breaches
        for f_file in list(report.scope_verification.forbidden_scope_violations):
            q = f"🚨 FORBIDDEN SCOPE BREACH: '{f_file}' was modified inside strictly off-limits areas. Authorize emergency scope override?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="forbidden_scope_breach",
                    target=f_file,
                    question=q,
                    user_response="Emergency override authorized"
                    if approved
                    else "Rejected as forbidden scope breach",
                    authorized=approved,
                )
            )
            if approved:
                approved_scope_exceptions.append(f_file)

        # 6. Question on Out-of-Scope Files
        for oos_file in list(report.scope_verification.out_of_scope_files):
            if oos_file in report.scope_verification.forbidden_scope_violations:
                continue
            q = f"Scope violation: '{oos_file}' is outside approved boundaries. Approve this scope exception?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="scope_violation",
                    target=oos_file,
                    question=q,
                    user_response="Scope exception approved"
                    if approved
                    else "Rejected as scope breach",
                    authorized=approved,
                )
            )
            if approved:
                approved_scope_exceptions.append(oos_file)

        # 7. Question on Hallucinated Requirement Claims
        for hal in list(report.spec_compliance.hallucinated_claims):
            q = f"🚨 Hallucinated claim: Agent claimed '{hal}' in summary, but zero code changes exist in git diff. Accept agent's word?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="hallucinated_claim",
                    target=hal,
                    question=q,
                    user_response="Accepted without code diff evidence"
                    if approved
                    else "Rejected as unverified claim",
                    authorized=approved,
                )
            )

        # 8. Question on Spec Drift
        for drift_item in list(report.spec_compliance.unrequested_drift):
            q = f"Spec drift: '{drift_item}'. Was this unrequested feature intentional and approved?"
            approved = self._ask_confirm(q, default=False)
            clarifications.append(
                UserClarification(
                    topic="spec_drift",
                    target=drift_item,
                    question=q,
                    user_response="Approved feature addition"
                    if approved
                    else "Rejected as unrequested drift",
                    authorized=approved,
                )
            )

        # Attach clarifications to report
        report.user_clarifications.extend(clarifications)

        # Re-evaluate statuses if user explicitly approved discrepancies
        if approved_unclaimed:
            remaining_unclaimed = [
                f for f in report.diff_verification.unclaimed_changes if f not in approved_unclaimed
            ]
            report.diff_verification.unclaimed_changes = remaining_unclaimed
            if not remaining_unclaimed and len(report.diff_verification.fabricated_claims) == 0:
                report.diff_verification.status = CheckStatus.PASS
                report.diff_verification.notes += " (All undeclared files authorized by user)"

        if approved_scope_exceptions:
            remaining_scope = [
                f
                for f in report.scope_verification.out_of_scope_files
                if f not in approved_scope_exceptions
            ]
            report.scope_verification.out_of_scope_files = remaining_scope
            if not remaining_scope:
                report.scope_verification.status = CheckStatus.PASS
                report.scope_verification.notes += " (Scope exceptions authorized by user)"

        # Check if all clarifications were authorized
        all_authorized = bool(clarifications) and all(c.authorized for c in clarifications)
        if all_authorized and report.test_verification.exit_code == 0:
            report.verdict = Verdict.VERIFIED
            report.confidence_score = 0.90
            report.summary += (
                " (All detected anomalies were reviewed and explicitly authorized by user)."
            )
        else:
            report.compute_verdict()

        self.con.print(
            f"\n[bold cyan]Review complete:[/bold cyan] {len(clarifications)} item(s) audited interactively. "
            f"Verdict: [bold]{report.verdict.value}[/bold]\n"
        )
        return report


def conduct_interactive_verification(
    report: TrustReport,
    console: Console | None = None,
    confirm_func: Callable[[str], bool] | None = None,
) -> TrustReport:
    """Convenience entry point for interactive discrepancy review."""
    session = InteractiveVerifierSession(console=console, confirm_func=confirm_func)
    return session.review_discrepancies(report)
