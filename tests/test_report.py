"""Unit tests for TrustReport rendering and export."""

from io import StringIO
from pathlib import Path

from rich.console import Console

from agent_verifier.models.trust_report import (
    CheckStatus,
    DiffVerificationResult,
    ScopeVerificationResult,
    SpecComplianceResult,
    TestVerificationResult,
    TrustReport,
    Verdict,
)
from agent_verifier.report.console import render_trust_report
from agent_verifier.report.json_export import export_json, load_json_report
from agent_verifier.report.markdown import export_markdown


def make_dummy_report(verdict: Verdict = Verdict.VERIFIED) -> TrustReport:
    report = TrustReport(
        repo_path="/dummy/repo",
        target_ref="main",
        verdict=verdict,
        confidence_score=0.95,
        summary="All tests passed and scope respected.",
        duration_seconds=1.25,
        diff_verification=DiffVerificationResult(
            status=CheckStatus.PASS,
            claimed_files=["src/app.py"],
            actual_changed_files=["src/app.py"],
            lines_added=10,
            lines_deleted=2,
        ),
        test_verification=TestVerificationResult(
            status=CheckStatus.PASS,
            runner="pytest",
            tests_run=5,
            tests_passed=5,
        ),
        scope_verification=ScopeVerificationResult(
            status=CheckStatus.PASS,
            blast_radius_risk_level="LOW",
        ),
        spec_compliance=SpecComplianceResult(
            status=CheckStatus.PASS,
            compliance_score=1.0,
        ),
    )
    return report


def test_console_render():
    report = make_dummy_report(Verdict.VERIFIED)
    buf = StringIO()
    console = Console(file=buf, color_system=None)
    render_trust_report(report, console=console)
    output = buf.getvalue()

    assert "Argus" in output
    assert "Trust Report" in output
    assert "VERIFIED" in output
    assert "Diff Alignment" in output
    assert "Test & Anti-Gaming" in output


def test_json_export_and_load(tmp_path: Path):
    report = make_dummy_report(Verdict.SUSPICIOUS)
    out_file = tmp_path / "report.json"

    export_json(report, out_file)
    assert out_file.exists()

    loaded = load_json_report(out_file)
    assert loaded.verdict == Verdict.SUSPICIOUS
    assert loaded.target_ref == "main"
    assert loaded.diff_verification.lines_added == 10


def test_markdown_export():
    report = make_dummy_report(Verdict.VERIFIED)
    md = export_markdown(report)

    assert "Argus (`argus-verify`) Trust Report" in md
    assert "VERIFIED" in md
    assert "| **1. Diff Alignment** |" in md
    assert "| **2. Test & Anti-Gaming** |" in md
    assert "| **3. Scope & Blast Radius** |" in md
    assert "| **4. Spec Compliance** |" in md
