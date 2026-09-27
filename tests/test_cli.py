"""Integration tests for Typer CLI commands."""

from pathlib import Path

import git
from typer.testing import CliRunner

from agent_verifier.cli import app
from agent_verifier.models.trust_report import TrustReport
from agent_verifier.report.json_export import export_json

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Argus" in result.stdout
    assert "0.1.0" in result.stdout


def test_cli_check_hook():
    result = runner.invoke(app, ["check-hook", "--repo", "."])
    assert result.exit_code == 0
    assert "hook" in result.stdout.lower()


def test_cli_verify_json_output(tmp_path: Path):
    # Create clean empty git repo
    git.Repo.init(tmp_path)
    result = runner.invoke(
        app,
        ["verify", "--repo", str(tmp_path), "--skip-tests", "--format", "json"],
    )
    assert result.exit_code == 0
    assert "verdict" in result.stdout.lower()
    assert "diff_verification" in result.stdout.lower()


def test_cli_report_command(tmp_path: Path):
    dummy_report = TrustReport(repo_path="/fake/repo", summary="Audit ok")
    report_file = tmp_path / "test_report.json"
    export_json(dummy_report, report_file)

    result = runner.invoke(app, ["report", str(report_file), "--format", "json"])
    assert result.exit_code == 0
    assert "/fake/repo" in result.stdout
