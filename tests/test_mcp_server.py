"""Unit tests for MCP Server tools."""

import json
from pathlib import Path

from agent_verifier.mcp_server import (
    get_trust_report,
    install_git_hook,
    mcp,
    verify_agent_claim,
)
from agent_verifier.models.trust_report import TrustReport
from agent_verifier.report.json_export import export_json


def test_mcp_server_initialization():
    assert mcp is not None
    assert mcp.name == "agent-verify"


def test_mcp_verify_agent_claim():
    json_result = verify_agent_claim(
        repo_path=".",
        summary="Initial audit verification",
        skip_tests=True,
    )
    data = json.loads(json_result)
    assert "verdict" in data
    assert "confidence_score" in data
    assert "diff_verification" in data


def test_mcp_get_trust_report(tmp_path: Path):
    dummy = TrustReport(repo_path="/mcp/test", summary="MCP passed")
    path = tmp_path / "mcp_report.json"
    export_json(dummy, path)

    res_json = get_trust_report(str(path), format="json")
    parsed = json.loads(res_json)
    assert parsed["repo_path"] == "/mcp/test"

    res_md = get_trust_report(str(path), format="markdown")
    assert "Trust Report" in res_md
    assert "VERIFIED" in res_md


def test_mcp_install_git_hook(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True)

    result_str = install_git_hook(repo_path=str(tmp_path))
    res = json.loads(result_str)
    assert res["status"] == "success"
    assert "pre-push" in res["hook_path"]
