"""Unit tests for developer intake interview module."""

from agent_verifier.interview.intake import IntakeInterview, run_intake_interview


def test_intake_interview_markdown_spec():
    interview = IntakeInterview(
        objective="Build Stripe webhook handler with signature validation",
        allowed_scope=["src/billing/*", "tests/test_billing.py"],
        forbidden_scope=[".env", "src/auth/jwt.py"],
        acceptance_criteria=[
            "Validate Stripe signature header",
            "Handle customer.subscription.created event",
            "Return 200 OK on successful processing",
        ],
        test_expectations="pytest tests/test_billing.py",
        constraints="Must support idempotency keys.",
    )

    spec_md = interview.to_markdown_spec()
    assert "# 📋 Task Specification" in spec_md
    assert "Build Stripe webhook handler" in spec_md
    assert "`src/billing/*`" in spec_md
    assert "🛑 `.env`" in spec_md
    assert "- [ ] Validate Stripe signature header" in spec_md
    assert "pytest tests/test_billing.py" in spec_md
    assert "Must support idempotency keys." in spec_md


def test_intake_interview_session_claim():
    interview = IntakeInterview(
        objective="Refactor database connection pool",
        allowed_scope=["src/db/*"],
    )
    claim = interview.to_session_claim()
    assert "src/db/*" in claim.allowed_paths
    assert "Refactor database connection pool" in claim.summary
    assert claim.spec_text is not None


def test_run_intake_interview_simulation():
    answers = [
        "Implement rate limiting middleware",  # objective
        "src/middleware/*, tests/*",  # allowed scope
        "src/core/security.py",  # forbidden scope
        "Reject requests exceeding 60/min;Return 429 Too Many Requests",  # criteria
        "pytest tests/test_rate_limit.py",  # tests
        "Redis cluster compatibility",  # constraints
    ]
    answer_idx = 0

    def mock_prompt(_prompt_text: str) -> str:
        nonlocal answer_idx
        ans = answers[answer_idx]
        answer_idx += 1
        return ans

    result = run_intake_interview(prompt_func=mock_prompt)
    assert result.objective == "Implement rate limiting middleware"
    assert "src/middleware/*" in result.allowed_scope
    assert "src/core/security.py" in result.forbidden_scope
    assert len(result.acceptance_criteria) == 2
    assert result.test_expectations == "pytest tests/test_rate_limit.py"
