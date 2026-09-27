# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-09-27

### Added
- **Diff Verification Engine**: Parses unified git diffs and untracked files, detecting undeclared edits and phantom claims.
- **Test Verification & Anti-Gaming Engine**:
  - Independent test suite execution in isolated subprocesses.
  - Test runner auto-detection for pytest, npm, cargo, and go.
  - AST and diff mutation scanning for deleted assertions, commented-out assertions, `@pytest.mark.skip` abuse, tautological assertions, and swallowed exceptions (`except: pass`).
- **Scope Verification & Blast Radius Engine**:
  - Path glob and directory boundary enforcement (`allowed_paths`).
  - Python AST symbol and invocation extractor using the standard library `ast`.
  - NetworkX directed call-graph construction and blast radius risk categorization (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`).
- **Spec Compliance Engine**:
  - Multiprovider LLM Judge support (Gemini, OpenAI, Anthropic) for specification compliance.
  - Deterministic heuristic keyword and drift fallback without API keys.
- **Reporting System**:
  - 30-second Rich terminal console UI with color-coded verdict banners.
  - Formatted JSON persistence and roundtrip loading.
  - GitHub-flavored Markdown report exporter.
- **CLI Interface**:
  - `agent-verify verify` with flexible options (`--repo`, `--summary`, `--claim`, `--spec`, `--allowed-path`, `--format`, `--output`).
  - `agent-verify install-hook` to install pre-push git hooks.
  - `agent-verify uninstall-git-hook` and `agent-verify check-hook`.
  - `agent-verify report` to display saved JSON reports.
  - `agent-verify version`.
- **Model Context Protocol (MCP) Server**:
  - stdio server with tools `verify_agent_claim`, `get_trust_report`, and `install_git_hook` for Claude Desktop, Cursor, Antigravity, and Copilot.
- **Pre-Push Git Hook**:
  - Fast local verification before code leaves developer machines.
