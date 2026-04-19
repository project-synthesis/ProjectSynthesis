"""Tests for SeedOrchestrator — prompt generation and deduplication."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent_loader import SeedAgent
from app.services.seed_orchestrator import (
    SeedOrchestrator,
    _resolve_agent_model,
    deduplicate_prompts,
)


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
        import asyncio

        orch = SeedOrchestrator(provider=None)
        with pytest.raises(ValueError, match="No LLM provider"):
            asyncio.run(orch.generate("test project", batch_id="test-batch"))


def _make_agent(name: str, model: str | None = None) -> SeedAgent:
    """Helper: build a SeedAgent fixture with sensible defaults."""
    return SeedAgent(
        name=name,
        description=f"test agent {name}",
        task_types=["coding"],
        phase_context=["build"],
        body="Generate diverse prompts.",
        prompts_per_run=3,
        enabled=True,
        model=model,
    )


class TestResolveAgentModel:
    """_resolve_agent_model() maps frontmatter strings to settings constants."""

    def test_no_override_returns_haiku(self) -> None:
        from app.config import settings

        agent = _make_agent("no-override", model=None)
        assert _resolve_agent_model(agent) == settings.MODEL_HAIKU

    def test_sonnet_override(self) -> None:
        from app.config import settings

        agent = _make_agent("diversity", model="sonnet")
        assert _resolve_agent_model(agent) == settings.MODEL_SONNET

    def test_opus_override(self) -> None:
        from app.config import settings

        agent = _make_agent("heavy", model="opus")
        assert _resolve_agent_model(agent) == settings.MODEL_OPUS

    def test_haiku_override_explicit(self) -> None:
        from app.config import settings

        agent = _make_agent("fast", model="haiku")
        assert _resolve_agent_model(agent) == settings.MODEL_HAIKU

    def test_unknown_value_falls_back_to_haiku(self) -> None:
        from app.config import settings

        agent = _make_agent("bogus", model="gpt-4")
        assert _resolve_agent_model(agent) == settings.MODEL_HAIKU


def _write_minimal_prompts_dir(tmp_path: Path) -> Path:
    """Minimal prompts dir with a seed.md template + manifest."""
    import json

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "seed.md").write_text(
        "Project: {{project_description}}\n"
        "Workspace: {{workspace_profile}}\n"
        "Codebase: {{codebase_context}}\n"
        "Generate {{prompts_per_run}} prompts for {{task_types}} during {{phase_context}}.\n"
    )
    (prompts / "manifest.json").write_text(json.dumps({
        "seed.md": {
            "required": [
                "project_description",
                "workspace_profile",
                "codebase_context",
                "prompts_per_run",
                "task_types",
                "phase_context",
            ],
            "optional": [],
        },
    }))
    return prompts


class TestAgentModelOverride:
    """The resolved model is threaded into call_provider_with_retry."""

    @pytest.mark.asyncio
    async def test_sonnet_override_threads_into_call(self, tmp_path: Path) -> None:
        from pydantic import BaseModel

        from app.config import settings
        from app.services.prompt_loader import PromptLoader

        prompts = _write_minimal_prompts_dir(tmp_path)
        agents_dir = tmp_path / "seed-agents"
        agents_dir.mkdir()

        class _PromptList(BaseModel):
            prompts: list[str] = []

        provider = AsyncMock()

        orch = SeedOrchestrator(
            provider=provider,
            agents_dir=agents_dir,
            prompt_loader=PromptLoader(prompts),
        )

        # Inject a single agent with model=sonnet directly; bypass file loader.
        orch._agent_loader.list_enabled = lambda: [_make_agent("diversity", model="sonnet")]  # type: ignore[method-assign]

        with patch(
            "app.services.seed_orchestrator.call_provider_with_retry",
            new=AsyncMock(return_value=_PromptList(prompts=["p1", "p2"])),
        ) as mock_retry:
            await orch.generate(
                project_description="Test project",
                batch_id="b1",
                prompt_count=2,
            )

        mock_retry.assert_awaited_once()
        assert mock_retry.call_args.kwargs["model"] == settings.MODEL_SONNET

    @pytest.mark.asyncio
    async def test_no_override_defaults_to_haiku(self, tmp_path: Path) -> None:
        from pydantic import BaseModel

        from app.config import settings
        from app.services.prompt_loader import PromptLoader

        prompts = _write_minimal_prompts_dir(tmp_path)
        agents_dir = tmp_path / "seed-agents"
        agents_dir.mkdir()

        class _PromptList(BaseModel):
            prompts: list[str] = []

        provider = AsyncMock()

        orch = SeedOrchestrator(
            provider=provider,
            agents_dir=agents_dir,
            prompt_loader=PromptLoader(prompts),
        )
        orch._agent_loader.list_enabled = lambda: [_make_agent("default", model=None)]  # type: ignore[method-assign]

        with patch(
            "app.services.seed_orchestrator.call_provider_with_retry",
            new=AsyncMock(return_value=_PromptList(prompts=["p1"])),
        ) as mock_retry:
            await orch.generate(
                project_description="Test project",
                batch_id="b2",
                prompt_count=2,
            )

        assert mock_retry.call_args.kwargs["model"] == settings.MODEL_HAIKU


class TestSeedTraceEmission:
    """Every agent dispatch emits one JSONL trace entry with status=ok or error."""

    @pytest.mark.asyncio
    async def test_emits_ok_trace_on_success(self, tmp_path: Path) -> None:
        from pydantic import BaseModel

        from app.providers.base import TokenUsage
        from app.services.prompt_loader import PromptLoader
        from app.services.trace_logger import TraceLogger

        prompts = _write_minimal_prompts_dir(tmp_path)
        agents_dir = tmp_path / "seed-agents"
        agents_dir.mkdir()
        traces_dir = tmp_path / "traces"

        class _PromptList(BaseModel):
            prompts: list[str] = []

        provider = AsyncMock()
        # Seed a real TokenUsage so the trace emitter can serialize it.
        # AsyncMock().last_usage defaults to a MagicMock, which json.dumps
        # rejects — use the real dataclass to mimic production shape.
        provider.last_usage = TokenUsage(
            input_tokens=500, output_tokens=120,
            cache_read_tokens=0, cache_creation_tokens=0,
        )

        orch = SeedOrchestrator(
            provider=provider,
            agents_dir=agents_dir,
            prompt_loader=PromptLoader(prompts),
        )
        orch._agent_loader.list_enabled = lambda: [_make_agent("ok-agent", model=None)]  # type: ignore[method-assign]

        with patch(
            "app.services.seed_orchestrator._get_trace_logger",
            return_value=TraceLogger(traces_dir),
        ), patch(
            "app.services.seed_orchestrator.call_provider_with_retry",
            new=AsyncMock(return_value=_PromptList(prompts=["p1", "p2"])),
        ):
            await orch.generate(
                project_description="Test project",
                batch_id="trace-ok",
                prompt_count=2,
            )

        entries = TraceLogger(traces_dir).read_trace("seed:trace-ok:ok-agent")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["phase"] == "seed_agent"
        assert entry["status"] == "ok"
        assert entry["tokens_in"] == 500
        assert entry["tokens_out"] == 120
        assert entry["result"]["batch_id"] == "trace-ok"
        assert entry["result"]["agent"] == "ok-agent"
        assert entry["result"]["prompts_generated"] == 2

    @pytest.mark.asyncio
    async def test_emits_error_trace_on_failure(self, tmp_path: Path) -> None:
        from app.providers.base import TokenUsage
        from app.services.prompt_loader import PromptLoader
        from app.services.trace_logger import TraceLogger

        prompts = _write_minimal_prompts_dir(tmp_path)
        agents_dir = tmp_path / "seed-agents"
        agents_dir.mkdir()
        traces_dir = tmp_path / "traces"

        provider = AsyncMock()
        provider.last_usage = TokenUsage()

        orch = SeedOrchestrator(
            provider=provider,
            agents_dir=agents_dir,
            prompt_loader=PromptLoader(prompts),
        )
        orch._agent_loader.list_enabled = lambda: [_make_agent("bad-agent", model=None)]  # type: ignore[method-assign]

        with patch(
            "app.services.seed_orchestrator._get_trace_logger",
            return_value=TraceLogger(traces_dir),
        ), patch(
            "app.services.seed_orchestrator.call_provider_with_retry",
            new=AsyncMock(side_effect=RuntimeError("provider down")),
        ):
            await orch.generate(
                project_description="Test project",
                batch_id="trace-err",
                prompt_count=2,
            )

        entries = TraceLogger(traces_dir).read_trace("seed:trace-err:bad-agent")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["status"] == "error"
        assert entry["result"]["prompts_generated"] == 0
        assert "provider down" in entry["result"]["error"]
