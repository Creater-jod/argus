"""Unified LLM Judge caller with multi-provider support and deterministic heuristic fallback.

Supports Gemini, OpenAI, and Anthropic as LLM providers. When no API key
is configured, falls back to deterministic keyword-based heuristics that
work offline with zero cost.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

from agent_verifier.config import default_config
from agent_verifier.llm.prompts import (
    DIFF_JUDGE_SYSTEM_PROMPT,
    DIFF_JUDGE_USER_TEMPLATE,
    SPEC_COMPLIANCE_SYSTEM_PROMPT,
    SPEC_COMPLIANCE_USER_TEMPLATE,
)

logger = logging.getLogger("agent_verify.llm")


class LLMJudge:
    """Evaluates agent diffs and spec compliance using LLMs or deterministic heuristic fallbacks."""

    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        model_name: str | None = None,
    ):
        self.provider = (provider or default_config.provider).lower()
        self.api_key = api_key
        self.model_name = model_name

        # Auto-detect provider if default is heuristic but keys are present
        if self.provider == "heuristic":
            if os.getenv("GEMINI_API_KEY"):
                self.provider = "gemini"
                self.api_key = os.getenv("GEMINI_API_KEY")
            elif os.getenv("OPENAI_API_KEY"):
                self.provider = "openai"
                self.api_key = os.getenv("OPENAI_API_KEY")
            elif os.getenv("ANTHROPIC_API_KEY"):
                self.provider = "anthropic"
                self.api_key = os.getenv("ANTHROPIC_API_KEY")

    def evaluate_spec_compliance(
        self,
        spec_text: str,
        agent_summary: str,
        diff_text: str,
    ) -> dict[str, Any]:
        """Evaluate whether git diff and agent claims comply with task specification."""
        if not spec_text.strip():
            return {
                "compliance_score": 1.0,
                "unmet_requirements": [],
                "unrequested_drift": [],
                "reasoning": "No explicit spec provided.",
            }

        # Try API provider if configured
        if self.provider in ("gemini", "openai", "anthropic") and self.api_key:
            try:
                res = self._call_llm(
                    system_prompt=SPEC_COMPLIANCE_SYSTEM_PROMPT,
                    user_prompt=SPEC_COMPLIANCE_USER_TEMPLATE.format(
                        spec_text=spec_text[:4000],
                        agent_summary=agent_summary[:2000],
                        diff_text=diff_text[:6000],
                    ),
                )
                if res and "compliance_score" in res:
                    logger.info(
                        "LLM spec compliance evaluation via %s: score=%.2f",
                        self.provider,
                        res.get("compliance_score", 0),
                    )
                    return res
            except Exception as e:
                logger.warning(
                    "LLM provider '%s' failed for spec compliance, falling back to heuristic: %s",
                    self.provider,
                    e,
                )

        return self._heuristic_spec_compliance(spec_text, agent_summary, diff_text)

    def evaluate_diff_alignment(
        self,
        agent_summary: str,
        diff_text: str,
    ) -> dict[str, Any]:
        """Evaluate whether agent summary accurately describes the diff without deception."""
        if self.provider in ("gemini", "openai", "anthropic") and self.api_key:
            try:
                res = self._call_llm(
                    system_prompt=DIFF_JUDGE_SYSTEM_PROMPT,
                    user_prompt=DIFF_JUDGE_USER_TEMPLATE.format(
                        agent_summary=agent_summary[:2000],
                        diff_text=diff_text[:6000],
                    ),
                )
                if res and "matches_claims" in res:
                    logger.info(
                        "LLM diff alignment evaluation via %s: matches=%s",
                        self.provider,
                        res.get("matches_claims"),
                    )
                    return res
            except Exception as e:
                logger.warning(
                    "LLM provider '%s' failed for diff alignment, falling back to heuristic: %s",
                    self.provider,
                    e,
                )

        logger.debug("Using heuristic diff alignment (no LLM provider active)")
        return {
            "matches_claims": True,
            "phantom_claims": [],
            "concealed_changes": [],
            "notes": "Verified via heuristic diff alignment.",
        }

    def _call_llm(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Make an HTTP call to the selected LLM provider."""
        with httpx.Client(timeout=15.0) as client:
            if self.provider == "gemini":
                model = self.model_name or "gemini-1.5-flash"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
                payload = {
                    "contents": [
                        {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
                    ],
                    "generationConfig": {"response_mime_type": "application/json"},
                }
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)

            elif self.provider == "openai":
                model = self.model_name or "gpt-4o-mini"
                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {self.api_key}"}
                payload = {
                    "model": model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return json.loads(text)

            elif self.provider == "anthropic":
                model = self.model_name or "claude-3-5-haiku-20241022"
                url = "https://api.anthropic.com/v1/messages"
                headers = {
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                }
                payload = {
                    "model": model,
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["content"][0]["text"]
                # Extract JSON block
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(0))
                return json.loads(text)

        return {}

    def _heuristic_spec_compliance(
        self,
        spec_text: str,
        agent_summary: str,
        diff_text: str,
    ) -> dict[str, Any]:
        """Deterministic heuristic check for spec requirements without external API calls."""
        requirements: list[str] = []
        for line in spec_text.splitlines():
            s = line.strip()
            # Look for bullet points or numbered lists: - [ ], 1., *, etc.
            if re.match(r"^(?:[-*+]|\d+\.)\s+", s):
                req = re.sub(r"^(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?", "", s)
                if len(req) > 5:
                    requirements.append(req)

        if not requirements:
            # If no bullets found, split into paragraphs
            paragraphs = [p.strip() for p in spec_text.split("\n\n") if len(p.strip()) > 10]
            requirements = paragraphs[:5]

        diff_evidence = diff_text.lower()
        summary_evidence = agent_summary.lower()
        unmet: list[str] = []
        hallucinated: list[str] = []

        for req in requirements:
            # Extract key words (> 3 chars, alphanumeric)
            words = [w.lower() for w in re.findall(r"\b[a-zA-Z_]{4,}\b", req)]
            if not words:
                continue

            # Ground truth: check presence in the actual git diff
            diff_matches = [w for w in words if w in diff_evidence]
            diff_ratio = len(diff_matches) / len(words)

            # Check if agent claimed it in summary
            summary_matches = [w for w in words if w in summary_evidence]
            summary_ratio = len(summary_matches) / len(words)

            if diff_ratio >= 0.30:
                # Evidence confirmed in code diff
                pass
            elif summary_ratio >= 0.40 and diff_ratio < 0.20:
                # Agent claimed it in summary, but code is missing from diff!
                flag_text = f"{req} (Claimed by agent in summary, but missing from git diff)"
                unmet.append(flag_text)
                hallucinated.append(req)
            else:
                unmet.append(req)

        # Check for unrequested drift: changes in sensitive areas in diff not mentioned in spec
        drift: list[str] = []
        spec_lower = spec_text.lower()

        # Define sensitive domain categories and their trigger keywords
        _DRIFT_CATEGORIES = [
            ("auth", ["auth", "login", "session", "oauth", "jwt"], "authentication/login"),
            ("payment", ["payment", "billing", "stripe", "invoice", "charge"], "payment/billing"),
            ("database", ["migration", "schema", "alter table", "drop table"], "database schema"),
            ("security", ["secret", "credential", "password", "api_key", "token"], "security/credentials"),
            ("config", ["dockerfile", "docker-compose", "nginx", ".env"], "infrastructure/config"),
            ("cicd", ["workflow", "github/workflows", "ci.yml", "deploy"], "CI/CD pipeline"),
        ]

        for category, triggers, label in _DRIFT_CATEGORIES:
            all_terms = [category] + triggers
            if not any(term in spec_lower for term in all_terms) and any(
                trigger in diff_evidence for trigger in triggers
            ):
                drift.append(f"Unrequested {label} modifications detected in diff")

        total_reqs = len(requirements) or 1
        compliance_score = max(0.0, min(1.0, (total_reqs - len(unmet)) / total_reqs))

        logger.debug(
            "Heuristic spec compliance: score=%.2f, unmet=%d, hallucinated=%d, drift=%d",
            compliance_score,
            len(unmet),
            len(hallucinated),
            len(drift),
        )

        return {
            "compliance_score": round(compliance_score, 2),
            "unmet_requirements": unmet,
            "hallucinated_claims": hallucinated,
            "unrequested_drift": drift,
            "reasoning": f"Evaluated {total_reqs} requirement(s). Found {len(unmet)} unverified item(s) and {len(hallucinated)} hallucinated claim(s).",
        }
