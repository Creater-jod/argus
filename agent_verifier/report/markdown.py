"""Markdown export formatter for TrustReport."""

from __future__ import annotations

from agent_verifier.models.trust_report import CheckStatus, TrustReport, Verdict


def _status_md(status: CheckStatus) -> str:
    if status == CheckStatus.PASS:
        return "✅ PASS"
    elif status == CheckStatus.WARN:
        return "⚠️ WARN"
    elif status == CheckStatus.FAIL:
        return "❌ FAIL"
    return "⏭️ SKIPPED"


def export_markdown(report: TrustReport) -> str:
    """Generate a GitHub-compatible Markdown summary of the TrustReport."""
    if report.verdict == Verdict.VERIFIED:
        verdict_badge = "🛡️ **VERIFIED**"
    elif report.verdict == Verdict.SUSPICIOUS:
        verdict_badge = "⚠️ **SUSPICIOUS**"
    else:
        verdict_badge = "❌ **FAILED**"

    conf_pct = int(report.confidence_score * 100)
    lines = [
        "# `agent-verify` Trust Report",
        "",
        f"**Verdict**: {verdict_badge} (Confidence: {conf_pct}%)  ",
        f"**Target**: `{report.target_ref}` | **Duration**: {report.duration_seconds}s | **Timestamp**: `{report.timestamp}`",
        "",
    ]

    if report.summary:
        lines.extend([f"> {report.summary}", ""])

    lines.extend(
        [
            "## 📊 Verification Summary",
            "",
            "| Verification Pillar | Status | Summary Findings |",
            "|:---|:---:|:---|",
        ]
    )

    diff = report.diff_verification
    diff_summary = (
        f"{len(diff.actual_changed_files)} changed (+{diff.lines_added}/-{diff.lines_deleted})"
    )
    if diff.unclaimed_changes:
        diff_summary += f", 🚨 {len(diff.unclaimed_changes)} undeclared"
    if diff.fabricated_claims:
        diff_summary += f", ⚠️ {len(diff.fabricated_claims)} phantom"
    lines.append(f"| **1. Diff Alignment** | {_status_md(diff.status)} | {diff_summary} |")

    test = report.test_verification
    test_summary = f"{test.runner}: {test.tests_passed} passed, {test.tests_failed} failed, {test.tests_skipped} skipped"
    if test.weakened_assertions_detected:
        test_summary += f", 🚨 {len(test.weakened_assertions_detected)} weakened assertions"
    lines.append(f"| **2. Test & Anti-Gaming** | {_status_md(test.status)} | {test_summary} |")

    scope = report.scope_verification
    scope_summary = f"Risk: **{scope.blast_radius_risk_level}** ({scope.impacted_symbols_count} symbols impacted)"
    if scope.out_of_scope_files:
        scope_summary += f", 🚨 {len(scope.out_of_scope_files)} out-of-scope files"
    lines.append(f"| **3. Scope & Blast Radius** | {_status_md(scope.status)} | {scope_summary} |")

    spec = report.spec_compliance
    spec_summary = f"Score: {int(spec.compliance_score * 100)}%"
    if spec.unmet_requirements:
        spec_summary += f", 🚨 {len(spec.unmet_requirements)} unmet"
    if spec.unrequested_drift:
        spec_summary += f", ⚠️ {len(spec.unrequested_drift)} drift"
    lines.append(f"| **4. Spec Compliance** | {_status_md(spec.status)} | {spec_summary} |")
    lines.append("")

    # Detailed discrepancies section
    discrepancies = []
    if diff.unclaimed_changes:
        discrepancies.append(
            f"- **Undeclared Modifications**: `{', '.join(diff.unclaimed_changes)}`"
        )
    if diff.fabricated_claims:
        discrepancies.append(
            f"- **Phantom Claims (Unmodified)**: `{', '.join(diff.fabricated_claims)}`"
        )
    for wa in test.weakened_assertions_detected:
        discrepancies.append(f"- **Weakened Assertion / Anti-Gaming Flag**: `{wa}`")
    for tp in test.trivially_passing_tests_flagged:
        discrepancies.append(f"- **Trivial Test Flag**: `{tp}`")
    for oos in scope.out_of_scope_files:
        discrepancies.append(f"- **Scope Violation**: `{oos}`")
    for unmet in spec.unmet_requirements:
        discrepancies.append(f"- **Unmet Spec Requirement**: {unmet}")
    for drift in spec.unrequested_drift:
        discrepancies.append(f"- **Unrequested Spec Drift**: {drift}")

    if discrepancies:
        lines.extend(
            [
                "## 🚨 Discrepancies & Audit Findings",
                "",
                *discrepancies,
                "",
            ]
        )

    return "\n".join(lines)
