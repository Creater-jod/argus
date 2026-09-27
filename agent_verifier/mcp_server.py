"""Model Context Protocol (MCP) server exposing agent verification tools.

Allows AI coding assistants (Claude Desktop, Cursor, Antigravity, VS Code Copilot)
to autonomously run ground-truth audits on workspace diffs and test executions.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_verifier.git.hook_installer import install_pre_push_hook
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.pipeline import VerificationPipeline
from agent_verifier.report.json_export import load_json_report
from agent_verifier.report.markdown import export_markdown

# Support both MCP SDK 2.x and 1.x
try:
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("agent-verify")
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("agent-verify")
    except Exception:
        mcp = None


if mcp is not None:

    @mcp.tool()
    def verify_agent_claim(
        repo_path: str = ".",
        summary: str | None = None,
        claim_file: str | None = None,
        spec_file: str | None = None,
        base_ref: str | None = None,
        allowed_paths: list[str] | None = None,
        skip_tests: bool = False,
    ) -> str:
        """Audit AI agent coding claims against ground truth git diffs, test suite, and scope.

        Returns a 30-second Trust Report in JSON with VERDICT (VERIFIED, SUSPICIOUS, FAILED).

        Args:
            repo_path: Absolute or relative path to the git repository.
            summary: Natural language explanation or summary of changes produced by agent.
            claim_file: Path to a JSON or markdown claim file.
            spec_file: Path to the task specification or requirements markdown file.
            base_ref: Git branch or commit to diff against (e.g. 'main', 'HEAD~1').
            allowed_paths: Optional list of directory prefixes or path globs permitted for this task.
            skip_tests: Whether to bypass test suite execution.
        """
        path = Path(repo_path).resolve()
        spec_text = None
        if spec_file:
            sp = Path(spec_file)
            if sp.is_file():
                spec_text = sp.read_text(encoding="utf-8", errors="replace")

        if claim_file and Path(claim_file).is_file():
            claim = SessionClaim.from_file(claim_file)
        elif summary:
            claim = SessionClaim.from_summary(summary, spec_text=spec_text)
        else:
            claim = SessionClaim(spec_text=spec_text)

        if allowed_paths:
            claim.allowed_paths.extend(allowed_paths)

        pipeline = VerificationPipeline()
        report = pipeline.run(
            repo_path=path,
            claim=claim,
            base_ref=base_ref,
            spec_path=spec_file,
            skip_tests=skip_tests,
        )

        return report.model_dump_json(indent=2)

    @mcp.tool()
    def get_trust_report(report_path: str, format: str = "markdown") -> str:
        """Load and display an existing Trust Report in Markdown or JSON format.

        Args:
            report_path: Path to the saved trust report JSON file.
            format: Output format ('markdown' or 'json').
        """
        loaded = load_json_report(report_path)
        if format.lower() == "json":
            return loaded.model_dump_json(indent=2)
        return export_markdown(loaded)

    @mcp.tool()
    def install_git_hook(repo_path: str = ".", force: bool = False) -> str:
        """Install pre-push git hook in the target repository to block unverified commits.

        Args:
            repo_path: Path to the repository root.
            force: Overwrite existing pre-push hook if present.
        """
        path = Path(repo_path).resolve()
        hook_path = install_pre_push_hook(path, force=force)
        return json.dumps(
            {
                "status": "success",
                "hook_path": str(hook_path),
                "message": "agent-verify pre-push hook active. Git push will now auto-verify agent changes.",
            }
        )


def main() -> None:
    """Run MCP server over stdio transport."""
    if mcp is None:
        raise RuntimeError(
            "MCP library is not installed. Install with 'pip install agent-verify[mcp]'"
        )

    # Run stdio server
    mcp.run()


if __name__ == "__main__":
    main()
