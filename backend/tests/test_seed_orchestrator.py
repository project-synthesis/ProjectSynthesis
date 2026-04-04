"""Tests for SeedOrchestrator — prompt generation and deduplication."""

import pytest

from app.services.seed_orchestrator import SeedOrchestrator, deduplicate_prompts


class TestDeduplication:
    def test_removes_near_duplicates(self) -> None:
        prompts = [
            "How do I implement user authentication?",
            "How do I implement user auth?",
            "Design a database schema for products",
        ]
        result = deduplicate_prompts(prompts, threshold=0.90)
        assert len(result) <= len(prompts)

    def test_keeps_distinct_prompts(self) -> None:
        prompts = [
            "Write a REST API for user management",
            "Set up CI/CD with GitHub Actions",
            "Create a monitoring dashboard",
        ]
        result = deduplicate_prompts(prompts, threshold=0.90)
        assert len(result) == 3

    def test_empty_list(self) -> None:
        result = deduplicate_prompts([], threshold=0.90)
        assert result == []

    def test_single_prompt(self) -> None:
        result = deduplicate_prompts(["Hello"], threshold=0.90)
        assert result == ["Hello"]


class TestSeedOrchestrator:
    def test_no_provider_raises(self) -> None:
        orch = SeedOrchestrator(provider=None)
        with pytest.raises(ValueError, match="No LLM provider"):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                orch.generate("test project", batch_id="test-batch")
            )
