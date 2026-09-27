"""Base check interface for all verification engines."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agent_verifier.models.session_claim import SessionClaim


class BaseCheck(ABC):
    """Abstract base class for all verification checks."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the check."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the check verifies."""
        pass

    @abstractmethod
    def run(
        self,
        repo_path: Path,
        claim: SessionClaim,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Execute the check against the repository and agent claim."""
        pass
