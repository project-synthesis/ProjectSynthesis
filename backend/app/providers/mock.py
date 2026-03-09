# backend/app/providers/mock.py
"""Deterministic mock LLM provider.

Used automatically when TESTING=True (set in pytest integration tests and
Playwright E2E webServer). Never use in production.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from app.providers.base import AgenticResult, LLMProvider, ToolDefinition

logger = logging.getLogger(__name__)


class MockProvider(LLMProvider):
    """Deterministic provider that returns canned responses without hitting any LLM.

    Returns a superset of fields so every pipeline stage can extract what it needs.
    Used in integration tests (via conftest) and E2E tests (via TESTING=True env var).
    """

    @property
    def name(self) -> str:
        return "mock"

    async def complete(self, system: str, user: str, model: str) -> str:
        return "Mock optimized prompt: always respond in bullet points."

    async def stream(self, system: str, user: str, model: str) -> AsyncGenerator[str, None]:
        yield "Mock optimized prompt: always respond in bullet points."

    async def complete_json(
        self,
        system: str,
        user: str,
        model: str,
        schema: dict | None = None,
    ) -> dict:
        return {
            "task_type": "instruction",
            "complexity": "simple",
            "framework": "CRISPE",
            "rationale": "CRISPE works well for instruction prompts.",
            "alternative_frameworks": ["CO-STAR"],
            "optimized_prompt": "Mock optimized prompt: always respond in bullet points.",
            "changes_made": ["Added role context", "Clarified output format"],
            "overall_score": 8.0,
            "clarity_score": 8.0,
            "specificity_score": 8.0,
            "effectiveness_score": 8.0,
            "is_improvement": True,
            "feedback": "Good structure. Consider adding examples.",
            "strengths": ["Clear intent"],
            "weaknesses": ["No examples"],
        }

    async def complete_agentic(
        self,
        system: str,
        user: str,
        model: str,
        tools: list[ToolDefinition],
        max_turns: int = 20,
        on_tool_call: Any = None,
        on_agent_text: Any = None,
        output_schema: dict | None = None,
    ) -> AgenticResult:
        # Explore stage only runs when repo_full_name is provided.
        # Tests don't send a repo so this is never called in normal test runs.
        raise NotImplementedError("MockProvider.complete_agentic should not be called in tests")
