"""AI Agent Verification Layer (`agent-verify`).

Audits AI coding agent claims (diffs, test execution, scope boundaries, and spec alignment)
to generate a rapid 30-second Trust Report.
"""

from agent_verifier.checks import (
    BaseCheck,
    DiffVerifier,
    ScopeVerifier,
    SpecVerifier,
    TestVerifier,
)
from agent_verifier.git import (
    FileDiff,
    GitDiffSummary,
    install_pre_push_hook,
    is_hook_installed,
    parse_git_diff,
    uninstall_hook,
)
from agent_verifier.interview import (
    IntakeInterview,
    InteractiveVerifierSession,
    conduct_interactive_verification,
    run_intake_interview,
)
from agent_verifier.models import (
    CheckStatus,
    DiffVerificationResult,
    RiskLevel,
    ScopeVerificationResult,
    SessionClaim,
    SpecComplianceResult,
    TestVerificationResult,
    TrustReport,
    UserClarification,
    Verdict,
    VerdictThresholds,
)
from agent_verifier.pipeline import VerificationPipeline
from agent_verifier.report import (
    export_json,
    export_markdown,
    load_json_report,
    render_trust_report,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "VerificationPipeline",
    "SessionClaim",
    "TrustReport",
    "Verdict",
    "VerdictThresholds",
    "CheckStatus",
    "RiskLevel",
    "DiffVerificationResult",
    "ScopeVerificationResult",
    "SpecComplianceResult",
    "TestVerificationResult",
    "UserClarification",
    "IntakeInterview",
    "run_intake_interview",
    "InteractiveVerifierSession",
    "conduct_interactive_verification",
    "DiffVerifier",
    "TestVerifier",
    "ScopeVerifier",
    "SpecVerifier",
    "BaseCheck",
    "FileDiff",
    "GitDiffSummary",
    "parse_git_diff",
    "install_pre_push_hook",
    "uninstall_hook",
    "is_hook_installed",
    "render_trust_report",
    "export_json",
    "export_markdown",
    "load_json_report",
]
