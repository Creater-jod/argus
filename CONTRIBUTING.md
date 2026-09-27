# Contributing to Argus (`argus-verify`) 👁️

Thank you for your interest in contributing to **Argus**! We welcome contributions from developers, researchers, and AI practitioners who want to help make autonomous AI coding safer, verifiable, and zero-trust.

---

## 📜 Code of Conduct

All contributors and maintainers are expected to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Please report unacceptable behavior to `jrnikiljr@gmail.com`.

---

## 🛠️ Development Setup

`argus-verify` uses [`uv`](https://github.com/astral-sh/uv) for fast, deterministic Python dependency management.

### 1. Fork & Clone

```bash
git clone https://github.com/Creater-jod/argus.git
cd argus
```

### 2. Create Virtual Environment & Install Dependencies

```bash
# Create venv and install all dependencies in editable mode
uv venv
uv sync --all-extras
```

### 3. Run the Test Suite

```bash
uv run pytest
```

---

## 🧩 Adding a New Verification Check

All verification checks implement the `BaseCheck` interface in `agent_verifier/checks/base.py`:

```python
from pathlib import Path
from typing import Any, Dict, Optional
from agent_verifier.checks.base import BaseCheck
from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import CheckStatus


class CustomVerifier(BaseCheck):
    @property
    def name(self) -> str:
        return "custom_verifier"

    @property
    def description(self) -> str:
        return "Verifies custom constraint or policy."

    def run(
        self,
        repo_path: Path,
        claim: SessionClaim,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        # Implement your audit logic
        return ...
```

Register your check in `agent_verifier/pipeline.py` and add comprehensive tests in `tests/`.

---

## 🎨 Code Quality Standards

Before submitting a Pull Request, verify that your code adheres to our formatting and linting rules:

```bash
# Run Ruff linting
uv run ruff check .

# Run Ruff formatting check
uv run ruff format --check .

# Run full test suite with coverage
uv run pytest -v
```

---

## 🔀 Pull Request Process

1. **Create a topic branch**: `git checkout -b feat/your-feature-name` or `fix/issue-description`.
2. **Follow Conventional Commits**:
   - `feat: add AST support for TypeScript files`
   - `fix: correct pytest summary regex for failed counts`
   - `docs: update MCP server integration instructions`
   - `test: add unit tests for scope verifier`
3. **Write tests**: Every new feature or bug fix must include corresponding tests in `tests/`.
4. **Submit PR**: Open a PR against `main`. Ensure CI passes and fill in the PR description template.

---

## 💡 Reporting Issues

- **Bug Reports**: Open an issue using our [Bug Report Template](.github/ISSUE_TEMPLATE/bug_report.yml).
- **Feature Requests**: Propose enhancements via [Feature Request Template](.github/ISSUE_TEMPLATE/feature_request.yml) or GitHub Discussions.
- **Security Vulnerabilities**: Please review our [Security Policy](SECURITY.md) — do **not** open public issues for security exploits.
