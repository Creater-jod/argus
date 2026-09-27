"""Configuration and environment management for agent-verify."""

import os

from pydantic import BaseModel, Field


class VerifierConfig(BaseModel):
    """Global configuration settings for agent-verify."""

    provider: str = Field(default_factory=lambda: os.getenv("AGENT_VERIFY_PROVIDER", "heuristic"))
    gemini_api_key: str | None = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    openai_api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    anthropic_api_key: str | None = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    model_name: str | None = Field(default_factory=lambda: os.getenv("AGENT_VERIFY_MODEL"))
    test_timeout_seconds: int = Field(
        default=30, description="Maximum seconds to allow test runner subprocess to execute"
    )
    max_unclaimed_files_tolerance: int = Field(
        default=2, description="Maximum acceptable unclaimed modified files before failing"
    )
    min_compliance_score: float = Field(
        default=0.85, description="Minimum compliance score before flagging SUSPICIOUS"
    )


# Singleton default config
default_config = VerifierConfig()
