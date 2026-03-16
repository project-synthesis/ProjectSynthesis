# Phase 1a: Provider Layer + Pipeline Core — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the core optimization pipeline so that `POST /api/optimize` returns a real optimized prompt via CLI or API provider.

**Architecture:** Python orchestrator making independent LLM calls per phase (analyze → optimize → score). Three-tier provider abstraction (CLI/API auto-detected). Filesystem-based prompt templates with `{{variable}}` substitution. Async generator pipeline yielding SSE events.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy async, anthropic SDK, Pydantic v2

**Spec:** `docs/superpowers/specs/2026-03-15-project-synthesis-redesign.md` (Sections 1, 12, 14)

**Phase 0 Handoff:** `docs/superpowers/plans/handoffs/handoff-phase-0.json` (all_passed: true)

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `backend/app/database.py` | Async session factory + `get_db` FastAPI dependency |
| `backend/app/schemas/pipeline_contracts.py` | All Pydantic contracts (Section 12) |
| `backend/app/providers/base.py` | Abstract `LLMProvider` with `complete_parsed()` |
| `backend/app/providers/anthropic_api.py` | Direct API provider with prompt caching |
| `backend/app/providers/claude_cli.py` | CLI subprocess provider |
| `backend/app/providers/detector.py` | Auto-detection (CLI → API fallback) |
| `backend/app/services/prompt_loader.py` | Template loading + variable substitution |
| `backend/app/services/strategy_loader.py` | Strategy file discovery |
| `backend/app/services/pipeline.py` | Pipeline orchestrator (async generator → SSE events) |
| `backend/app/routers/optimize.py` | `POST /api/optimize` (SSE), `GET /api/optimize/{id}` |
| `backend/app/routers/health.py` | `GET /api/health` |
| `backend/tests/test_contracts.py` | Contract validation tests |
| `backend/tests/test_providers.py` | Provider tests (mocked) |
| `backend/tests/test_prompt_loader.py` | Template loading tests |
| `backend/tests/test_strategy_loader.py` | Strategy discovery tests |
| `backend/tests/test_pipeline.py` | Full pipeline flow tests |
| `backend/tests/test_prompt_caching.py` | Cache control verification |

### Modify

| File | Changes |
|------|---------|
| `backend/app/main.py` | Add routers, provider init in lifespan, db session factory |
| `backend/tests/conftest.py` | Add mock provider fixture, `app_client` fixture |
| `prompts/manifest.json` | Finalize variable specs |
| `prompts/agent-guidance.md` | Real content |
| `prompts/analyze.md` | Real content |
| `prompts/optimize.md` | Real content |
| `prompts/scoring.md` | Real content (anchored rubric + calibration) |
| `prompts/adaptation.md` | Real content |
| `prompts/README.md` | Update with full variable reference |
| `prompts/strategies/*.md` | Real content (6 files) |

---

## Chunk 1: Contracts, Database, and Provider Layer

### Task 1: Pipeline Contracts

**Files:**
- Create: `backend/app/schemas/pipeline_contracts.py`
- Create: `backend/tests/test_contracts.py`

- [ ] **Step 1: Write contract tests**

```python
# backend/tests/test_contracts.py
"""Tests for pipeline Pydantic contracts."""

import pytest
from pydantic import ValidationError

from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    PipelineEvent,
    PipelineResult,
    ResolvedContext,
    ScoreResult,
)


class TestDimensionScores:
    def test_valid_scores(self):
        s = DimensionScores(clarity=7.0, specificity=8.0, structure=6.5, faithfulness=9.0, conciseness=5.5)
        assert s.clarity == 7.0

    def test_score_below_range(self):
        with pytest.raises(ValidationError, match="outside 1.0-10.0"):
            DimensionScores(clarity=0.5, specificity=5.0, structure=5.0, faithfulness=5.0, conciseness=5.0)

    def test_score_above_range(self):
        with pytest.raises(ValidationError, match="outside 1.0-10.0"):
            DimensionScores(clarity=10.5, specificity=5.0, structure=5.0, faithfulness=5.0, conciseness=5.0)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            DimensionScores(clarity=5.0, specificity=5.0, structure=5.0, faithfulness=5.0, conciseness=5.0, extra=1)

    def test_overall_score(self):
        s = DimensionScores(clarity=6.0, specificity=8.0, structure=7.0, faithfulness=9.0, conciseness=5.0)
        assert s.overall == 7.0  # mean of 5 dimensions


class TestAnalysisResult:
    def test_valid(self):
        r = AnalysisResult(
            task_type="coding",
            weaknesses=["vague"],
            strengths=["concise"],
            selected_strategy="chain-of-thought",
            strategy_rationale="good for coding",
            confidence=0.85,
        )
        assert r.task_type == "coding"

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            AnalysisResult(
                task_type="coding", weaknesses=[], strengths=[],
                selected_strategy="auto", strategy_rationale="", confidence=1.5,
            )


class TestOptimizationResult:
    def test_valid(self):
        r = OptimizationResult(
            optimized_prompt="Better prompt here",
            changes_summary="Added specificity",
            strategy_used="chain-of-thought",
        )
        assert r.optimized_prompt == "Better prompt here"

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            OptimizationResult(
                optimized_prompt="x", changes_summary="y", strategy_used="z", rogue_field="bad",
            )


class TestScoreResult:
    def test_valid(self):
        scores = DimensionScores(clarity=7.0, specificity=8.0, structure=6.0, faithfulness=9.0, conciseness=5.0)
        r = ScoreResult(prompt_a_scores=scores, prompt_b_scores=scores)
        assert r.prompt_a_scores.clarity == 7.0


class TestResolvedContext:
    def test_minimal(self):
        ctx = ResolvedContext(raw_prompt="test", trace_id="abc-123")
        assert ctx.codebase_guidance is None
        assert ctx.context_sources == {}


class TestPipelineResult:
    def test_valid(self):
        scores = DimensionScores(clarity=7.0, specificity=8.0, structure=6.0, faithfulness=9.0, conciseness=5.0)
        r = PipelineResult(
            id="opt-1", trace_id="trace-1", raw_prompt="test",
            optimized_prompt="better test", task_type="coding",
            strategy_used="chain-of-thought", changes_summary="improved",
            optimized_scores=scores, original_scores=scores,
            score_deltas={"clarity": 0.0}, overall_score=7.0,
            provider="anthropic_api", model_used="claude-sonnet-4-6",
            scoring_mode="independent", duration_ms=1500, status="completed",
            context_sources={"codebase": False},
        )
        assert r.status == "completed"

    def test_accepts_extra_fields(self):
        """PipelineResult does NOT use extra='forbid' — assembled by orchestrator."""
        scores = DimensionScores(clarity=5.0, specificity=5.0, structure=5.0, faithfulness=5.0, conciseness=5.0)
        r = PipelineResult(
            id="x", trace_id="y", raw_prompt="z", optimized_prompt="w",
            task_type="general", strategy_used="auto", changes_summary="none",
            optimized_scores=scores, original_scores=scores,
            score_deltas={}, overall_score=5.0, provider="mock",
            model_used="test", scoring_mode="independent", duration_ms=0,
            status="completed", context_sources={}, extra_field="ok",
        )
        assert r.status == "completed"


class TestPipelineEvent:
    def test_valid(self):
        e = PipelineEvent(event="status", data={"stage": "analyzing", "state": "running"})
        assert e.event == "status"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_contracts.py -v`
Expected: FAIL (import errors — module does not exist)

- [ ] **Step 3: Implement contracts**

```python
# backend/app/schemas/pipeline_contracts.py
"""Phase handoff contracts — Pydantic models for all pipeline boundaries.

LLM output models use extra='forbid' for guaranteed schema compliance via output_config.format.
Orchestrator-side models (ResolvedContext, PipelineResult) do NOT use extra='forbid'.
See spec Section 12 for full contract definitions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResolvedContext(BaseModel):
    """Context assembled by the orchestrator before pipeline execution."""

    raw_prompt: str
    strategy_override: str | None = None
    codebase_guidance: str | None = None
    codebase_context: str | None = None
    adaptation_state: str | None = None
    context_sources: dict[str, bool] = Field(default_factory=dict)
    trace_id: str


# --- LLM output contracts (used with output_format) ---


class AnalysisResult(BaseModel):
    """Output from the analyzer subagent."""

    model_config = ConfigDict(extra="forbid")

    task_type: str
    weaknesses: list[str]
    strengths: list[str]
    selected_strategy: str
    strategy_rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class OptimizationResult(BaseModel):
    """Output from the optimizer subagent."""

    model_config = ConfigDict(extra="forbid")

    optimized_prompt: str
    changes_summary: str
    strategy_used: str


class DimensionScores(BaseModel):
    """5-dimension scoring for a single prompt."""

    model_config = ConfigDict(extra="forbid")

    clarity: float
    specificity: float
    structure: float
    faithfulness: float
    conciseness: float

    @model_validator(mode="after")
    def scores_in_range(self) -> DimensionScores:
        for field_name in self.model_fields:
            val = getattr(self, field_name)
            if not 1.0 <= val <= 10.0:
                raise ValueError(f"{field_name} score {val} outside 1.0-10.0 range")
        return self

    @property
    def overall(self) -> float:
        """Mean of all 5 dimensions."""
        vals = [getattr(self, f) for f in self.model_fields]
        return round(sum(vals) / len(vals), 2)


class ScoreResult(BaseModel):
    """Output from the scorer subagent — uses neutral A/B naming."""

    model_config = ConfigDict(extra="forbid")

    prompt_a_scores: DimensionScores
    prompt_b_scores: DimensionScores


# --- Orchestrator-side input contracts ---


class AnalyzerInput(BaseModel):
    """Orchestrator-assembled input for the analyzer."""

    model_config = ConfigDict(extra="forbid")

    raw_prompt: str
    strategy_override: str | None = None
    available_strategies: list[str]


class OptimizerInput(BaseModel):
    """Orchestrator-assembled input for the optimizer."""

    model_config = ConfigDict(extra="forbid")

    raw_prompt: str
    analysis: AnalysisResult
    analysis_summary: str
    strategy_instructions: str
    codebase_guidance: str | None = None
    codebase_context: str | None = None
    adaptation_state: str | None = None


class ScorerInput(BaseModel):
    """Orchestrator-assembled input for the scorer."""

    model_config = ConfigDict(extra="forbid")

    prompt_a: str
    prompt_b: str
    presentation_order: str  # "original_first" or "optimized_first" — logged, NOT sent to scorer


# --- SSE + final result ---


class PipelineEvent(BaseModel):
    """SSE event emitted during pipeline execution."""

    event: str
    data: dict[str, Any]


class PipelineResult(BaseModel):
    """Final pipeline output — persisted to optimizations table.

    No extra='forbid' — assembled by orchestrator, not used as LLM output_format.
    """

    id: str
    trace_id: str
    raw_prompt: str
    optimized_prompt: str
    task_type: str
    strategy_used: str
    changes_summary: str
    optimized_scores: DimensionScores
    original_scores: DimensionScores
    score_deltas: dict[str, float]
    overall_score: float
    provider: str
    model_used: str
    scoring_mode: str  # "independent" or "self_rated"
    duration_ms: int
    status: str  # "completed" / "failed" / "interrupted"
    context_sources: dict[str, bool]
    tokens_total: int = 0
    tokens_by_phase: dict[str, int] = Field(default_factory=dict)
    repo_full_name: str | None = None
    codebase_context_snapshot: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_contracts.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/schemas/pipeline_contracts.py tests/test_contracts.py
git commit -m "feat: add pipeline Pydantic contracts (Section 12)"
```

---

### Task 2: Database Session Factory

**Files:**
- Create: `backend/app/database.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create database module**

```python
# backend/app/database.py
"""Async database session factory and FastAPI dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session, auto-closes on exit."""
    async with async_session_factory() as session:
        yield session
```

- [ ] **Step 2: Update main.py to import routers and init provider in lifespan**

Replace `backend/app/main.py` with:

```python
"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app._version import __version__
from app.config import settings, DATA_DIR

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    # Startup
    settings.SECRET_KEY = settings.resolve_secret_key()

    # Enable WAL mode for SQLite
    db_path = DATA_DIR / "synthesis.db"
    if db_path.exists():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")

    # Detect LLM provider
    from app.providers.detector import detect_provider

    provider = detect_provider()
    app.state.provider = provider
    logger.info("Provider detected: %s", provider.name if provider else "none")

    yield
    # Shutdown


app = FastAPI(
    title="Project Synthesis",
    version=__version__,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5199"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
from app.routers.health import router as health_router  # noqa: E402
from app.routers.optimize import router as optimize_router  # noqa: E402

app.include_router(health_router)
app.include_router(optimize_router)

# ASGI app for uvicorn
asgi_app = app
```

- [ ] **Step 3: Commit**

```bash
cd backend && git add app/database.py app/main.py
git commit -m "feat: add async session factory and wire main.py lifespan"
```

---

### Task 3: Provider Layer

**Files:**
- Create: `backend/app/providers/base.py`
- Create: `backend/app/providers/anthropic_api.py`
- Create: `backend/app/providers/claude_cli.py`
- Create: `backend/app/providers/detector.py`
- Create: `backend/tests/test_providers.py`

- [ ] **Step 1: Write provider tests**

```python
# backend/tests/test_providers.py
"""Tests for the provider layer."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import AnalysisResult


# --- Base provider ---

class TestThinkingConfig:
    def test_opus_adaptive(self):
        assert LLMProvider.thinking_config("claude-opus-4-6") == {"type": "adaptive"}

    def test_sonnet_adaptive(self):
        assert LLMProvider.thinking_config("claude-sonnet-4-6") == {"type": "adaptive"}

    def test_haiku_disabled(self):
        assert LLMProvider.thinking_config("claude-haiku-4-5") == {"type": "disabled"}

    def test_haiku_case_insensitive(self):
        assert LLMProvider.thinking_config("claude-Haiku-4-5") == {"type": "disabled"}


# --- API provider ---

class TestAnthropicAPIProvider:
    @pytest.fixture
    def mock_client(self):
        with patch("app.providers.anthropic_api.AsyncAnthropic") as MockClass:
            client = AsyncMock()
            MockClass.return_value = client
            yield client

    async def test_complete_parsed_calls_api(self, mock_client):
        from app.providers.anthropic_api import AnthropicAPIProvider

        mock_response = MagicMock()
        mock_response.parsed_output = AnalysisResult(
            task_type="coding", weaknesses=["vague"], strengths=["concise"],
            selected_strategy="chain-of-thought", strategy_rationale="helps", confidence=0.9,
        )
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_client.messages.parse = AsyncMock(return_value=mock_response)

        provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
        provider.client = mock_client

        result = await provider.complete_parsed(
            model="claude-sonnet-4-6",
            system_prompt="You are an analyst.",
            user_message="Analyze this prompt",
            output_format=AnalysisResult,
            effort="medium",
        )
        assert isinstance(result, AnalysisResult)
        assert result.task_type == "coding"

        # Verify API call args
        call_kwargs = mock_client.messages.parse.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-6"
        assert call_kwargs.kwargs["thinking"] == {"type": "adaptive"}
        # System prompt has cache_control
        system = call_kwargs.kwargs["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    async def test_haiku_no_effort(self, mock_client):
        from app.providers.anthropic_api import AnthropicAPIProvider

        mock_response = MagicMock()
        mock_response.parsed_output = AnalysisResult(
            task_type="writing", weaknesses=[], strengths=[],
            selected_strategy="auto", strategy_rationale="", confidence=0.5,
        )
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 30
        mock_client.messages.parse = AsyncMock(return_value=mock_response)

        provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
        provider.client = mock_client

        await provider.complete_parsed(
            model="claude-haiku-4-5",
            system_prompt="system",
            user_message="msg",
            output_format=AnalysisResult,
            effort="medium",  # should be ignored for Haiku
        )
        call_kwargs = mock_client.messages.parse.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "disabled"}
        # No effort in output_config for Haiku
        assert "output_config" not in call_kwargs or "effort" not in call_kwargs.get("output_config", {})


# --- CLI provider ---

class TestClaudeCLIProvider:
    async def test_complete_parsed_subprocess(self):
        from app.providers.claude_cli import ClaudeCLIProvider

        expected = AnalysisResult(
            task_type="coding", weaknesses=["vague"], strengths=["clear"],
            selected_strategy="chain-of-thought", strategy_rationale="good", confidence=0.8,
        )
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(expected.model_dump_json().encode(), b"")
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            provider = ClaudeCLIProvider()
            result = await provider.complete_parsed(
                model="claude-sonnet-4-6",
                system_prompt="system",
                user_message="test",
                output_format=AnalysisResult,
            )
            assert isinstance(result, AnalysisResult)
            assert result.task_type == "coding"
            # Verify claude CLI called with correct args
            cmd_args = mock_exec.call_args[0]
            assert cmd_args[0] == "claude"
            assert "-p" in cmd_args

    async def test_cli_failure_raises(self):
        from app.providers.claude_cli import ClaudeCLIProvider

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            provider = ClaudeCLIProvider()
            with pytest.raises(RuntimeError, match="CLI call failed"):
                await provider.complete_parsed(
                    model="claude-sonnet-4-6",
                    system_prompt="sys",
                    user_message="msg",
                    output_format=AnalysisResult,
                )


# --- Detector ---

class TestDetector:
    def test_detects_cli_first(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            from app.providers.detector import detect_provider
            provider = detect_provider()
            assert provider.name == "claude_cli"

    def test_falls_back_to_api(self):
        with patch("shutil.which", return_value=None):
            with patch("app.providers.detector._settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "sk-test-key"
                from app.providers.detector import detect_provider
                provider = detect_provider()
                assert provider.name == "anthropic_api"

    def test_returns_none_when_nothing_available(self):
        with patch("shutil.which", return_value=None):
            with patch("app.providers.detector._settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = ""
                from app.providers.detector import detect_provider
                provider = detect_provider()
                assert provider is None
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_providers.py -v`
Expected: FAIL (import errors)

- [ ] **Step 3: Implement base provider**

```python
# backend/app/providers/base.py
"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    """Base class for all LLM providers."""

    name: str

    @abstractmethod
    async def complete_parsed(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
    ) -> T:
        """Make an LLM call and return a parsed Pydantic model.

        Args:
            model: Model ID (e.g. "claude-opus-4-6").
            system_prompt: System prompt text (cached on API path).
            user_message: User message text.
            output_format: Pydantic model class for structured output.
            max_tokens: Maximum output tokens.
            effort: Thinking effort level ("low"/"medium"/"high"). Ignored for Haiku.
        """
        ...

    @staticmethod
    def thinking_config(model: str) -> dict:
        """Return thinking configuration based on model.

        Opus/Sonnet: adaptive thinking. Haiku: disabled.
        """
        if "haiku" in model.lower():
            return {"type": "disabled"}
        return {"type": "adaptive"}
```

- [ ] **Step 4: Implement API provider**

```python
# backend/app/providers/anthropic_api.py
"""Anthropic API provider — direct API calls with structured output and prompt caching."""

import logging
from typing import TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class AnthropicAPIProvider(LLMProvider):
    """Direct Anthropic API provider with prompt caching and structured output."""

    name = "anthropic_api"

    def __init__(self, api_key: str) -> None:
        self.client = AsyncAnthropic(api_key=api_key)

    async def complete_parsed(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
    ) -> T:
        thinking = self.thinking_config(model)

        # System prompt with cache_control for prompt caching (90% cost savings)
        system = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "thinking": thinking,
            "messages": [{"role": "user", "content": user_message}],
        }

        # Effort only for non-Haiku models (Haiku doesn't support it)
        if effort and "haiku" not in model.lower():
            kwargs["output_config"] = {"effort": effort}

        response = await self.client.messages.parse(
            output_format=output_format,
            **kwargs,
        )

        logger.info(
            "API call: model=%s, input_tokens=%d, output_tokens=%d",
            model,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        return response.parsed_output
```

- [ ] **Step 5: Implement CLI provider**

```python
# backend/app/providers/claude_cli.py
"""Claude CLI subprocess provider for Max subscribers."""

import asyncio
import json
import logging
from typing import TypeVar

from pydantic import BaseModel

from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ClaudeCLIProvider(LLMProvider):
    """Claude CLI subprocess provider — zero cost for Max subscribers."""

    name = "claude_cli"

    async def complete_parsed(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
    ) -> T:
        # Include JSON schema in system prompt for CLI path
        # (CLI doesn't support output_config.format natively)
        schema = json.dumps(output_format.model_json_schema(), indent=2)
        full_system = (
            f"{system_prompt}\n\n"
            f"IMPORTANT: You MUST return your response as a single JSON object "
            f"matching this exact schema:\n```json\n{schema}\n```\n"
            f"Return ONLY valid JSON. No markdown fencing, no commentary."
        )

        cmd = [
            "claude",
            "-p", user_message,
            "--model", model,
            "--system-prompt", full_system,
            "--output-format", "json",
            "--max-tokens", str(max_tokens),
        ]

        logger.info("CLI call: model=%s", model)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error("CLI call failed (rc=%d): %s", proc.returncode, error_msg)
            raise RuntimeError(f"CLI call failed: {error_msg}")

        raw = stdout.decode().strip()
        logger.debug("CLI response: %s", raw[:200])

        return output_format.model_validate_json(raw)
```

- [ ] **Step 6: Implement detector**

```python
# backend/app/providers/detector.py
"""Auto-detect the best available LLM provider (CLI → API)."""

import logging
import shutil

from app.config import settings as _settings

logger = logging.getLogger(__name__)


def detect_provider():
    """Detect available provider. Returns LLMProvider instance or None.

    Priority: Claude CLI (free for Max) → Anthropic API (paid).
    """
    # 1. Claude CLI
    if shutil.which("claude"):
        from app.providers.claude_cli import ClaudeCLIProvider

        logger.info("Detected Claude CLI provider")
        return ClaudeCLIProvider()

    # 2. Anthropic API key
    if _settings.ANTHROPIC_API_KEY:
        from app.providers.anthropic_api import AnthropicAPIProvider

        logger.info("Detected Anthropic API provider")
        return AnthropicAPIProvider(api_key=_settings.ANTHROPIC_API_KEY)

    logger.warning("No LLM provider available — optimization will fail")
    return None
```

- [ ] **Step 7: Run tests — verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_providers.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
cd backend && git add app/providers/ tests/test_providers.py
git commit -m "feat: implement provider layer (base, API, CLI, detector)"
```

---

## Chunk 2: Template Services and Real Prompt Content

### Task 4: Prompt Loader Service

**Files:**
- Create: `backend/app/services/prompt_loader.py`
- Create: `backend/tests/test_prompt_loader.py`

- [ ] **Step 1: Write prompt loader tests**

```python
# backend/tests/test_prompt_loader.py
"""Tests for prompt template loading and variable substitution."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from app.services.prompt_loader import PromptLoader


@pytest.fixture
def tmp_prompts(tmp_path):
    """Create a temporary prompts directory with test templates."""
    # Template with variables
    (tmp_path / "test.md").write_text(
        "<user-prompt>\n{{raw_prompt}}\n</user-prompt>\n\n"
        "<context>\n{{codebase_context}}\n</context>\n\n"
        "## Instructions\nDo the thing."
    )

    # Static template (no variables)
    (tmp_path / "static.md").write_text("You are a helpful assistant.")

    # Manifest
    manifest = {
        "test.md": {"required": ["raw_prompt"], "optional": ["codebase_context"]},
        "static.md": {"required": [], "optional": []},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    return tmp_path


class TestPromptLoader:
    def test_load_static(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        result = loader.load("static.md")
        assert result == "You are a helpful assistant."

    def test_render_with_variables(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        result = loader.render("test.md", {"raw_prompt": "Write a function", "codebase_context": "file.py: def foo():"})
        assert "Write a function" in result
        assert "file.py: def foo():" in result

    def test_optional_var_removed_with_empty_tags(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        result = loader.render("test.md", {"raw_prompt": "test"})
        # Empty <context> tags should be removed
        assert "<context>" not in result
        assert "test" in result

    def test_missing_required_var_raises(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        with pytest.raises(ValueError, match="Required variable.*raw_prompt"):
            loader.render("test.md", {})

    def test_unknown_template_raises(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        with pytest.raises(FileNotFoundError):
            loader.load("nonexistent.md")

    def test_hot_reload(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        # First load
        result1 = loader.load("static.md")
        # Modify file
        (tmp_prompts / "static.md").write_text("Updated content")
        # Should get new content (no cache)
        result2 = loader.load("static.md")
        assert result2 == "Updated content"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt_loader.py -v`
Expected: FAIL (import errors)

- [ ] **Step 3: Implement prompt loader**

```python
# backend/app/services/prompt_loader.py
"""Template loading + variable substitution.

Templates are Markdown files with {{variable}} placeholders.
Variables with no value are omitted, including surrounding XML tags.
Templates are read from disk on each call (hot-reload, no restart needed).
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class PromptLoader:
    """Loads and renders prompt templates from the prompts directory."""

    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir
        self._manifest: dict | None = None

    @property
    def manifest(self) -> dict:
        """Load manifest.json (cached, reloaded on each access for hot-reload)."""
        manifest_path = self.prompts_dir / "manifest.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text())
        return {}

    def load(self, name: str) -> str:
        """Load a template file as raw text (no substitution)."""
        path = self.prompts_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {path}")
        return path.read_text()

    def render(self, name: str, variables: dict[str, str | None] | None = None) -> str:
        """Load template and substitute variables.

        - Required variables (per manifest) must be present and non-empty.
        - Optional variables with None/empty value → empty string.
        - Empty XML tag pairs are removed after substitution.
        """
        variables = variables or {}
        template = self.load(name)

        # Validate required variables
        spec = self.manifest.get(name, {})
        for required in spec.get("required", []):
            if not variables.get(required):
                raise ValueError(
                    f"Required variable '{required}' missing or empty for template '{name}'"
                )

        # Substitute variables
        for var_name, value in variables.items():
            placeholder = "{{" + var_name + "}}"
            template = template.replace(placeholder, value or "")

        # Remove any remaining unsubstituted optional placeholders
        template = re.sub(r"\{\{[a-z_]+\}\}", "", template)

        # Remove empty XML tags (tags with only whitespace content)
        template = re.sub(r"<([\w-]+)>\s*</\1>", "", template, flags=re.DOTALL)

        # Clean up excessive blank lines
        template = re.sub(r"\n{3,}", "\n\n", template)

        return template.strip()
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt_loader.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/prompt_loader.py tests/test_prompt_loader.py
git commit -m "feat: implement prompt template loader with variable substitution"
```

---

### Task 5: Strategy Loader Service

**Files:**
- Create: `backend/app/services/strategy_loader.py`
- Create: `backend/tests/test_strategy_loader.py`

- [ ] **Step 1: Write strategy loader tests**

```python
# backend/tests/test_strategy_loader.py
"""Tests for strategy file discovery and loading."""

import pytest
from pathlib import Path

from app.services.strategy_loader import StrategyLoader


@pytest.fixture
def tmp_strategies(tmp_path):
    """Create a temporary strategies directory with test files."""
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (strat_dir / "chain-of-thought.md").write_text("# Chain of Thought\nThink step by step.")
    (strat_dir / "few-shot.md").write_text("# Few-Shot\nProvide examples.")
    (strat_dir / "auto.md").write_text("# Auto\nSelect the best approach.")
    return strat_dir


class TestStrategyLoader:
    def test_list_strategies(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        strategies = loader.list_strategies()
        assert "chain-of-thought" in strategies
        assert "few-shot" in strategies
        assert "auto" in strategies

    def test_load_strategy(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        content = loader.load("chain-of-thought")
        assert "Think step by step" in content

    def test_load_unknown_strategy_raises(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        with pytest.raises(FileNotFoundError, match="Strategy not found"):
            loader.load("nonexistent")

    def test_format_available_strategies(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        formatted = loader.format_available()
        assert "chain-of-thought" in formatted
        assert "few-shot" in formatted

    def test_empty_directory(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        loader = StrategyLoader(strat_dir)
        assert loader.list_strategies() == []
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_strategy_loader.py -v`
Expected: FAIL (import errors)

- [ ] **Step 3: Implement strategy loader**

```python
# backend/app/services/strategy_loader.py
"""Strategy file discovery and loading.

Strategy files are static Markdown in prompts/strategies/.
Their full text is injected as {{strategy_instructions}} in optimize.md / refine.md.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class StrategyLoader:
    """Discovers and loads strategy files from the strategies directory."""

    def __init__(self, strategies_dir: Path) -> None:
        self.strategies_dir = strategies_dir

    def list_strategies(self) -> list[str]:
        """Return sorted list of available strategy names (without .md extension)."""
        if not self.strategies_dir.exists():
            return []
        return sorted(p.stem for p in self.strategies_dir.glob("*.md"))

    def load(self, name: str) -> str:
        """Load a strategy file by name (without .md extension)."""
        path = self.strategies_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Strategy not found: {path}")
        return path.read_text()

    def format_available(self) -> str:
        """Format available strategies as a bullet list for the analyzer prompt."""
        strategies = self.list_strategies()
        if not strategies:
            return "No strategies available."
        return "\n".join(f"- {s}" for s in strategies)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_strategy_loader.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/strategy_loader.py tests/test_strategy_loader.py
git commit -m "feat: implement strategy loader service"
```

---

### Task 6: Real Prompt Template Content

**Files:**
- Modify: `prompts/agent-guidance.md`
- Modify: `prompts/analyze.md`
- Modify: `prompts/optimize.md`
- Modify: `prompts/scoring.md`
- Modify: `prompts/adaptation.md`
- Modify: `prompts/strategies/chain-of-thought.md`
- Modify: `prompts/strategies/few-shot.md`
- Modify: `prompts/strategies/role-playing.md`
- Modify: `prompts/strategies/structured-output.md`
- Modify: `prompts/strategies/meta-prompting.md`
- Modify: `prompts/strategies/auto.md`
- Modify: `prompts/manifest.json`

- [ ] **Step 1: Write agent-guidance.md**

This is the orchestrator system prompt — static, no variables. Replace the placeholder with:

```markdown
You are Project Synthesis, an expert prompt optimization system.

Your role is to analyze, rewrite, and score prompts to make them more effective for AI language models. You operate as a pipeline of specialized subagents, each with isolated context windows:

1. **Analyzer** — Classifies the prompt type, identifies weaknesses, and selects the best optimization strategy.
2. **Optimizer** — Rewrites the prompt using the selected strategy while preserving the original intent.
3. **Scorer** — Independently evaluates both the original and optimized prompts on 5 quality dimensions.

## Principles

- **Preserve intent.** The optimized prompt must accomplish the same goal as the original.
- **Be concrete.** Replace vague language with specific instructions, constraints, and examples.
- **Stay concise.** Remove filler, redundancy, and unnecessary elaboration. Shorter is better when clarity is maintained.
- **Use structure.** Add formatting (headers, lists, XML tags) when it improves parseability.
- **Score honestly.** Use the full 1-10 range. Mediocre prompts get mediocre scores.
```

- [ ] **Step 2: Write analyze.md**

```markdown
<user-prompt>
{{raw_prompt}}
</user-prompt>

<available-strategies>
{{available_strategies}}
</available-strategies>

## Instructions

You are an expert prompt analyst. Classify the user's prompt and identify its strengths and weaknesses.

Analyze the prompt above and determine:

1. **Task type** — What kind of task is this prompt for? Choose one: coding, writing, analysis, creative, data, system, general.
2. **Weaknesses** — List specific, actionable problems. Be concrete: "no output format specified" not "could be improved."
3. **Strengths** — What does this prompt already do well? Even weak prompts have strengths.
4. **Strategy** — Select the single best strategy from the available list above. If unsure, select "auto."
5. **Rationale** — Explain in 1-2 sentences why this strategy fits.
6. **Confidence** — How confident are you? 0.0 = pure guess, 1.0 = certain. Below 0.7 triggers automatic fallback to "auto" strategy.

Think thoroughly about the prompt's intent and context before classifying. Consider who would write this prompt and what outcome they expect.
```

- [ ] **Step 3: Write optimize.md**

```markdown
<user-prompt>
{{raw_prompt}}
</user-prompt>

<analysis>
{{analysis_summary}}
</analysis>

<codebase-context>
{{codebase_guidance}}
{{codebase_context}}
</codebase-context>

<adaptation>
{{adaptation_state}}
</adaptation>

<strategy>
{{strategy_instructions}}
</strategy>

## Instructions

You are an expert prompt engineer. Rewrite the user's prompt using the strategy and analysis above.

**Guidelines:**
- **Preserve intent completely.** The optimized prompt must accomplish the exact same goal.
- **Target the weaknesses** identified in the analysis. Each weakness should be addressed.
- **Apply the strategy** — use its techniques to improve the prompt's effectiveness.
- **Be concise.** Remove filler words, redundant phrases, and unnecessary elaboration. Every word must earn its place.
- **Add structure** where it helps: headers, numbered steps, XML tags, output format specifications.
- **Include constraints** the original prompt implies but doesn't state (language, format, error handling, edge cases).
- **Use specific language.** Replace "handle errors" with "raise ValueError with descriptive message on invalid input."

If the original prompt references a codebase (see context above), incorporate relevant code patterns, naming conventions, and architecture details into the optimized prompt.

Summarize the changes you made and why.
```

- [ ] **Step 4: Write scoring.md**

This is the big template — static system prompt with anchored rubric and calibration examples.

```markdown
You are an independent prompt quality evaluator. You will receive two prompts labeled "Prompt A" and "Prompt B" in random order. You do not know which is the original and which is the optimized version. Evaluate each independently.

<rubric>
  <dimension name="clarity">
    <description>How unambiguous is the prompt? Could two competent practitioners interpret it identically?</description>
    <score value="1-2">Unintelligible or deeply ambiguous. Reader cannot determine the task.</score>
    <score value="3-4">Intent is guessable but vague. Multiple valid interpretations exist.</score>
    <score value="5-6">Intent is clear but execution details are missing. Reader knows WHAT but not HOW.</score>
    <score value="7-8">Clear intent with most execution details specified. Minor ambiguities remain.</score>
    <score value="9-10">Unambiguous. A competent practitioner would produce identical output.</score>
    <calibration-example score="3">write some code to handle user data</calibration-example>
    <calibration-example score="7">write a Python function that validates email addresses using RFC 5322 regex, returns bool</calibration-example>
    <calibration-example score="9">write a Python function validate_email(addr: str) -> bool that uses RFC 5322 regex, returns False on invalid format, raises ValueError if addr is None, includes docstring with usage examples</calibration-example>
  </dimension>

  <dimension name="specificity">
    <description>How many constraints, requirements, and details does the prompt provide?</description>
    <score value="1-2">No constraints. Completely open-ended.</score>
    <score value="3-4">One or two vague constraints. Most details left to interpretation.</score>
    <score value="5-6">Several constraints present but key details missing (format, edge cases, scope).</score>
    <score value="7-8">Well-constrained with format, scope, and most edge cases specified.</score>
    <score value="9-10">Exhaustively specified. Language, format, error handling, examples, and edge cases all present.</score>
    <calibration-example score="2">make a website</calibration-example>
    <calibration-example score="6">build a REST API for user management with CRUD operations</calibration-example>
    <calibration-example score="9">build a FastAPI REST API for user management: POST /users (create, validate email format), GET /users/{id} (404 on missing), PUT /users/{id} (partial update), DELETE /users/{id} (soft delete). Use Pydantic v2 models, return JSON with consistent error envelope {error, detail, status_code}.</calibration-example>
  </dimension>

  <dimension name="structure">
    <description>How well-organized is the prompt? Does formatting aid comprehension?</description>
    <score value="1-2">Wall of text. No formatting, no separation of concerns.</score>
    <score value="3-4">Minimal formatting. Some paragraph breaks but no clear sections.</score>
    <score value="5-6">Basic structure present (paragraphs or simple lists) but could be clearer.</score>
    <score value="7-8">Well-structured with headers, lists, or XML tags. Clear separation of context and instructions.</score>
    <score value="9-10">Excellent structure. Data-first layout, tagged sections, output format specified, examples properly delineated.</score>
    <calibration-example score="3">I need you to write a function that takes a list and sorts it and also filters out duplicates and returns the result as a new list and it should handle empty lists too</calibration-example>
    <calibration-example score="8">## Task\nWrite a sort-and-deduplicate function.\n\n## Requirements\n- Input: list of comparable items\n- Output: new sorted list with duplicates removed\n- Handle empty lists (return [])\n\n## Output format\nPython function with type hints and docstring.</calibration-example>
  </dimension>

  <dimension name="faithfulness">
    <description>Does the prompt preserve its core intent? (For original prompts, this is a baseline — score 5.0 by default since a prompt cannot be unfaithful to itself.)</description>
    <score value="1-2">Intent completely lost or contradicted.</score>
    <score value="3-4">Core intent present but significant aspects altered or omitted.</score>
    <score value="5-6">Intent preserved but some nuance lost or added.</score>
    <score value="7-8">Intent fully preserved with minor additions that don't change the goal.</score>
    <score value="9-10">Perfect intent preservation. Every aspect of the original goal is maintained.</score>
    <calibration-example score="3">Original asked for a REST API; rewrite focuses on a CLI tool instead</calibration-example>
    <calibration-example score="7">Original asked to "validate emails"; rewrite validates emails plus adds input sanitization (minor scope addition, core intent intact)</calibration-example>
    <calibration-example score="9">Original asked for a sort function; rewrite asks for the same sort function with added type hints and edge case handling (no intent change)</calibration-example>
  </dimension>

  <dimension name="conciseness">
    <description>Is every word necessary? Score strictly — filler, redundancy, and over-elaboration reduce this score.</description>
    <score value="1-2">Extremely verbose. Most content is filler or repetition.</score>
    <score value="3-4">Noticeably wordy. Several unnecessary sentences or phrases.</score>
    <score value="5-6">Acceptable length but contains some filler or redundancy.</score>
    <score value="7-8">Tight writing. Almost every word contributes.</score>
    <score value="9-10">Maximally concise. Cannot remove a word without losing information.</score>
    <calibration-example score="3">I would like you to please write me a function, if you could, that would take in a list of numbers and then go through each number and add them all up together to get the total sum of all the numbers in the list</calibration-example>
    <calibration-example score="8">Write a function sum_list(numbers: list[float]) -> float that returns the sum.</calibration-example>
  </dimension>
</rubric>

<examples>
  <example>
    <prompt-a>write some code to handle user data</prompt-a>
    <prompt-b>Write a Python function validate_user(data: dict) -> bool that checks: (1) 'email' field exists and matches RFC 5322, (2) 'age' is int between 0-150, (3) 'name' is non-empty string. Return False on any failure. Raise TypeError if data is not a dict.</prompt-b>
    <scores>{"prompt_a": {"clarity": 3, "specificity": 2, "structure": 2, "faithfulness": 5, "conciseness": 8}, "prompt_b": {"clarity": 8, "specificity": 9, "structure": 7, "faithfulness": 8, "conciseness": 7}}</scores>
    <reasoning>Prompt A is vague ("some code", "handle", "user data" — all undefined). Its only strength is brevity. Prompt B specifies language, function signature, validation rules, return type, and error handling.</reasoning>
  </example>
</examples>

## Evaluation Instructions

You will receive two prompts labeled "Prompt A" and "Prompt B."

1. Read both prompts completely before scoring.
2. For each prompt, find specific phrases that support your assessment. Place them in <quotes> tags.
3. Score each prompt independently on all 5 dimensions using the rubric above.
4. Use the full 1-10 range. If both prompts are mediocre, use scores in the 3-5 range. Reserve 7+ for genuinely strong prompts. A score of 9-10 should be rare.
5. Longer is NOT better. A 3-sentence prompt that perfectly communicates intent scores higher on clarity than a 3-paragraph prompt with unnecessary context.
6. Score conciseness strictly — any filler, redundancy, or elaboration reduces the conciseness score below 5.

Before finalizing your scores, verify:
- Did you use the full 1-10 range across both prompts?
- Are your scores consistent with the calibration examples above?
- Would a different evaluator reach similar scores for these prompts?
```

- [ ] **Step 5: Write adaptation.md**

```markdown
<task-type-affinities>
{{task_type_affinities}}
</task-type-affinities>

## Adaptation Context

The JSON above shows strategy performance data from previous optimizations with user feedback. Each entry maps a task type to strategy approval rates.

When selecting or applying a strategy, consider this data:
- Strategies with high approval rates (> 0.7) for the current task type should be preferred.
- Strategies with low approval rates (< 0.3) should be avoided unless the prompt specifically demands them.
- If no data exists for the current task type, rely on your own judgment.

This data reflects real user preferences — weigh it alongside your analysis.
```

- [ ] **Step 6: Write strategy templates**

**`prompts/strategies/chain-of-thought.md`:**
```markdown
# Chain of Thought Strategy

Guide the AI through explicit reasoning steps before producing output.

## Techniques
- Add "Think step by step" or "Let's work through this" instructions
- Break complex tasks into numbered sub-problems
- Request intermediate reasoning before the final answer
- Add "Before answering, consider..." prefixes for evaluation tasks
- Use "First... Then... Finally..." sequential structure

## When to Use
- Complex reasoning or multi-step problems
- Mathematical or logical tasks
- Debugging and troubleshooting prompts
- Decision-making with multiple criteria

## When to Avoid
- Simple factual lookups or one-step tasks
- Creative writing (can make output feel mechanical)
- Tasks where speed matters more than accuracy
```

**`prompts/strategies/few-shot.md`:**
```markdown
# Few-Shot Strategy

Provide concrete input/output examples to demonstrate the expected behavior.

## Techniques
- Add 2-3 examples showing input → expected output format
- Include edge cases in examples (empty input, error cases)
- Use consistent formatting between examples and the actual task
- Label examples clearly: "Example 1:", "Example 2:"
- Show both positive and negative examples when relevant

## When to Use
- Format-sensitive tasks (specific output structure required)
- Classification or categorization tasks
- Tasks where "show, don't tell" is more effective
- When the expected behavior is hard to describe but easy to demonstrate

## When to Avoid
- Tasks where examples might constrain creativity
- When the output format is already obvious
- Very simple tasks (examples add unnecessary length)
```

**`prompts/strategies/role-playing.md`:**
```markdown
# Role-Playing Strategy

Assign a specific expert persona to focus the AI's knowledge and tone.

## Techniques
- Define expertise: "You are a senior backend engineer with 10 years of Python experience"
- Set context: "You are reviewing code for a production deployment"
- Specify tone: "Respond as a technical mentor explaining to a junior developer"
- Add constraints the role implies: "As a security auditor, flag any potential vulnerabilities"

## When to Use
- Domain-specific tasks requiring specialized knowledge
- Tasks where tone and perspective matter (teaching, reviewing, consulting)
- When you want to focus the AI on a specific body of knowledge
- Technical writing that needs a particular voice

## When to Avoid
- Objective analysis tasks (persona can introduce bias)
- Simple tasks that don't benefit from a persona
- When the role might conflict with accuracy
```

**`prompts/strategies/structured-output.md`:**
```markdown
# Structured Output Strategy

Define explicit output format, constraints, and validation rules.

## Techniques
- Specify output format: JSON schema, Markdown template, specific sections
- Add field-level constraints: types, ranges, required vs optional
- Include output examples showing the exact expected structure
- Define error format alongside success format
- Use XML tags or headers to delineate output sections

## When to Use
- API response specifications
- Data transformation tasks
- Code generation with specific signatures and types
- Any task where the output format matters as much as the content

## When to Avoid
- Open-ended creative tasks
- Exploratory analysis where format isn't predetermined
- Tasks where natural language response is more appropriate
```

**`prompts/strategies/meta-prompting.md`:**
```markdown
# Meta-Prompting Strategy

Optimize the prompt by making its structure and intent more explicit to the AI.

## Techniques
- Add explicit task framing: "Your task is to..." followed by clear objectives
- Separate context from instructions (data at top, instructions at bottom)
- Add self-check instructions: "Before responding, verify that..."
- Include negative constraints: "Do NOT include...", "Avoid..."
- Specify the audience: "Write for developers who are familiar with..."
- Add quality criteria the response should meet

## When to Use
- General-purpose improvement when no specific strategy fits
- Prompts that are unclear about what they want
- Tasks requiring precision in following instructions
- Complex prompts that need organizational improvement

## When to Avoid
- Prompts that are already well-structured (would add unnecessary meta-instructions)
- Very short, clear tasks (meta-instructions would overwhelm the actual task)
```

**`prompts/strategies/auto.md`:**
```markdown
# Auto Strategy

Analyze the prompt yourself and select the best optimization approach.

You have full discretion to combine techniques from any strategy:
- Chain of thought (reasoning steps)
- Few-shot (examples)
- Role-playing (expert persona)
- Structured output (format specification)
- Meta-prompting (structural improvement)

Choose the approach that best addresses the prompt's weaknesses. You may combine multiple techniques if appropriate. Focus on the highest-impact improvements first.

If the prompt is already strong, make minimal changes — do not over-optimize.
```

- [ ] **Step 7: Update manifest.json**

```json
{
  "agent-guidance.md": {"required": [], "optional": []},
  "analyze.md": {"required": ["raw_prompt", "available_strategies"], "optional": []},
  "optimize.md": {"required": ["raw_prompt", "strategy_instructions", "analysis_summary"], "optional": ["codebase_guidance", "codebase_context", "adaptation_state"]},
  "scoring.md": {"required": [], "optional": []},
  "explore.md": {"required": ["raw_prompt", "file_contents", "file_paths"], "optional": []},
  "adaptation.md": {"required": ["task_type_affinities"], "optional": []},
  "refine.md": {"required": ["current_prompt", "refinement_request", "original_prompt", "strategy_instructions"], "optional": ["codebase_guidance", "codebase_context", "adaptation_state"]},
  "suggest.md": {"required": ["optimized_prompt", "scores", "weaknesses", "strategy_used"], "optional": []},
  "passthrough.md": {"required": ["raw_prompt", "scoring_rubric_excerpt"], "optional": ["strategy_instructions", "codebase_guidance", "codebase_context", "adaptation_state"]}
}
```

- [ ] **Step 8: Commit**

```bash
git add prompts/
git commit -m "feat: write real prompt templates for all Phase 1a files"
```

---

## Chunk 3: Pipeline Orchestrator, Routers, and Integration

### Task 7: Pipeline Orchestrator

**Files:**
- Create: `backend/app/services/pipeline.py`
- Create: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Write pipeline tests**

```python
# backend/tests/test_pipeline.py
"""Tests for the pipeline orchestrator."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    PipelineEvent,
    PipelineResult,
    ScoreResult,
)
from app.services.pipeline import PipelineOrchestrator


def _make_analysis(**overrides):
    defaults = dict(
        task_type="coding", weaknesses=["vague"], strengths=["concise"],
        selected_strategy="chain-of-thought", strategy_rationale="good for coding",
        confidence=0.9,
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def _make_optimization(**overrides):
    defaults = dict(
        optimized_prompt="Write a Python function that sorts a list in ascending order.",
        changes_summary="Added specificity: language, operation, order.",
        strategy_used="chain-of-thought",
    )
    defaults.update(overrides)
    return OptimizationResult(**defaults)


def _make_scores(a_clarity=4.0, b_clarity=8.0):
    return ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=a_clarity, specificity=3.0, structure=5.0, faithfulness=5.0, conciseness=6.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=b_clarity, specificity=8.0, structure=7.0, faithfulness=9.0, conciseness=7.0,
        ),
    )


@pytest.fixture
def mock_provider():
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"
    return provider


@pytest.fixture
def orchestrator(tmp_path):
    """Create orchestrator with minimal prompts dir."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    strategies = prompts / "strategies"
    strategies.mkdir()

    # Minimal templates
    (prompts / "agent-guidance.md").write_text("System prompt.")
    (prompts / "analyze.md").write_text("{{raw_prompt}}\n{{available_strategies}}")
    (prompts / "optimize.md").write_text(
        "{{raw_prompt}}\n{{analysis_summary}}\n{{strategy_instructions}}\n"
        "<codebase-context>\n{{codebase_guidance}}\n{{codebase_context}}\n</codebase-context>\n"
        "<adaptation>\n{{adaptation_state}}\n</adaptation>"
    )
    (prompts / "scoring.md").write_text("Score these prompts.")
    (prompts / "manifest.json").write_text(
        '{"analyze.md": {"required": ["raw_prompt", "available_strategies"], "optional": []},'
        '"optimize.md": {"required": ["raw_prompt", "strategy_instructions", "analysis_summary"], '
        '"optional": ["codebase_guidance", "codebase_context", "adaptation_state"]},'
        '"scoring.md": {"required": [], "optional": []}}'
    )
    (strategies / "chain-of-thought.md").write_text("Think step by step.")
    (strategies / "auto.md").write_text("Auto-select.")

    return PipelineOrchestrator(prompts_dir=prompts)


class TestPipelineOrchestrator:
    async def test_full_flow_emits_correct_events(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(),
            _make_optimization(),
            _make_scores(),
        ]

        events = []
        async for event in orchestrator.run(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider,
            db=db_session,
        ):
            events.append(event)

        event_names = [e.event for e in events]
        assert "optimization_start" in event_names
        assert "optimization_complete" in event_names
        # Correct order
        start_idx = event_names.index("optimization_start")
        complete_idx = event_names.index("optimization_complete")
        assert start_idx < complete_idx
        # Provider called 3 times (analyze, optimize, score)
        assert mock_provider.complete_parsed.call_count == 3

    async def test_scorer_gets_neutral_ab_labels(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(),
            _make_optimization(),
            _make_scores(),
        ]

        async for _ in orchestrator.run(
            raw_prompt="test prompt",
            provider=mock_provider,
            db=db_session,
        ):
            pass

        # Third call is the scorer
        scorer_call = mock_provider.complete_parsed.call_args_list[2]
        user_msg = scorer_call.kwargs.get("user_message", scorer_call.args[2] if len(scorer_call.args) > 2 else "")
        # Must contain neutral labels
        assert "Prompt A" in user_msg
        assert "Prompt B" in user_msg
        # Must NOT contain bias-inducing labels
        assert "original" not in user_msg.lower()
        assert "optimized" not in user_msg.lower()

    async def test_low_confidence_overrides_to_auto(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(confidence=0.4, selected_strategy="few-shot"),
            _make_optimization(strategy_used="auto"),
            _make_scores(),
        ]

        events = []
        async for event in orchestrator.run(
            raw_prompt="test prompt",
            provider=mock_provider,
            db=db_session,
        ):
            events.append(event)

        # Optimizer should have been called with auto strategy
        optimizer_call = mock_provider.complete_parsed.call_args_list[1]
        user_msg = optimizer_call.kwargs.get("user_message", optimizer_call.args[2] if len(optimizer_call.args) > 2 else "")
        assert "Auto-select" in user_msg  # auto.md content

    async def test_error_event_on_provider_failure(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = RuntimeError("LLM unavailable")

        events = []
        async for event in orchestrator.run(
            raw_prompt="test",
            provider=mock_provider,
            db=db_session,
        ):
            events.append(event)

        event_names = [e.event for e in events]
        assert "error" in event_names

    async def test_score_deltas_computed_correctly(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(),
            _make_optimization(),
            _make_scores(a_clarity=4.0, b_clarity=8.0),
        ]

        # Seed random for deterministic A/B assignment
        with patch("app.services.pipeline.random.choice", return_value=True):  # original_first
            events = []
            async for event in orchestrator.run(
                raw_prompt="test",
                provider=mock_provider,
                db=db_session,
            ):
                events.append(event)

        # Find score_card event for delta verification
        score_card = next(e for e in events if e.event == "score_card")
        # With original_first=True: prompt_a=original, prompt_b=optimized
        # So original_clarity=4.0, optimized_clarity=8.0, delta=+4.0
        assert score_card.data["deltas"]["clarity"] == 4.0

    async def test_strategy_override(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(selected_strategy="chain-of-thought"),
            _make_optimization(),
            _make_scores(),
        ]

        async for _ in orchestrator.run(
            raw_prompt="test",
            provider=mock_provider,
            db=db_session,
            strategy_override="chain-of-thought",
        ):
            pass

        # Analyzer should still have been called (for weaknesses/task_type)
        assert mock_provider.complete_parsed.call_count == 3
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_pipeline.py -v`
Expected: FAIL (import errors)

- [ ] **Step 3: Implement pipeline orchestrator**

```python
# backend/app/services/pipeline.py
"""Pipeline orchestrator — analyzer → optimizer → scorer.

The orchestrator is Python code making independent LLM calls per phase.
Each call gets a fresh context window. Subagents cannot see each other's reasoning.
Yields PipelineEvent objects for SSE streaming.
"""

import asyncio
import logging
import random
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization
from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    PipelineEvent,
    PipelineResult,
    ScoreResult,
)
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader

logger = logging.getLogger(__name__)

# Confidence threshold — below this, override strategy to "auto"
CONFIDENCE_GATE = 0.7

# Retry config — retry once with 2s backoff on LLM failure
RETRY_DELAY_SECONDS = 2
MAX_RETRIES = 1

# Keywords for semantic consistency check
_CODING_KEYWORDS = {"function", "class", "api", "code", "program", "script", "endpoint", "database", "module", "import"}


async def _retry_llm_call(provider, **kwargs):
    """Call provider.complete_parsed with retry-once-then-fail."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await provider.complete_parsed(**kwargs)
        except Exception:
            if attempt < MAX_RETRIES:
                logger.warning("LLM call failed (attempt %d), retrying in %ds", attempt + 1, RETRY_DELAY_SECONDS)
                await asyncio.sleep(RETRY_DELAY_SECONDS)
            else:
                raise


class PipelineOrchestrator:
    """Orchestrates the optimization pipeline."""

    def __init__(self, prompts_dir: Path) -> None:
        self.prompt_loader = PromptLoader(prompts_dir)
        self.strategy_loader = StrategyLoader(prompts_dir / "strategies")

    async def run(
        self,
        raw_prompt: str,
        provider: LLMProvider,
        db: AsyncSession,
        strategy_override: str | None = None,
        codebase_guidance: str | None = None,
        codebase_context: str | None = None,
        adaptation_state: str | None = None,
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Run the full pipeline. Yields SSE events."""
        trace_id = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        start_time = time.monotonic()

        yield PipelineEvent(event="optimization_start", data={"trace_id": trace_id})

        try:
            # --- Phase 1: Analyze ---
            yield PipelineEvent(event="status", data={"stage": "analyzing", "state": "running"})

            available_strategies = self.strategy_loader.format_available()
            analyze_prompt = self.prompt_loader.render("analyze.md", {
                "raw_prompt": raw_prompt,
                "available_strategies": available_strategies,
            })

            analysis = await _retry_llm_call(
                provider,
                model="claude-sonnet-4-6",
                system_prompt=self.prompt_loader.load("agent-guidance.md"),
                user_message=analyze_prompt,
                output_format=AnalysisResult,
                effort="medium",
            )

            # Semantic consistency check — reduce confidence if task_type doesn't match prompt keywords
            prompt_lower = raw_prompt.lower()
            if analysis.task_type == "coding" and not any(kw in prompt_lower for kw in _CODING_KEYWORDS):
                logger.warning(
                    "Semantic mismatch: task_type='coding' but no coding keywords found in prompt (trace_id=%s)",
                    trace_id,
                )
                analysis.confidence = max(0.0, analysis.confidence - 0.2)

            # Confidence gate
            strategy_name = strategy_override or analysis.selected_strategy
            if analysis.confidence < CONFIDENCE_GATE and not strategy_override:
                logger.info(
                    "Low confidence (%.2f) — overriding strategy '%s' → 'auto'",
                    analysis.confidence, analysis.selected_strategy,
                )
                strategy_name = "auto"

            yield PipelineEvent(event="status", data={"stage": "analyzing", "state": "complete"})

            # --- Phase 2: Optimize ---
            yield PipelineEvent(event="status", data={"stage": "optimizing", "state": "running"})

            strategy_content = self.strategy_loader.load(strategy_name)
            analysis_summary = (
                f"Task type: {analysis.task_type}\n"
                f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
                f"Strengths: {', '.join(analysis.strengths)}\n"
                f"Strategy rationale: {analysis.strategy_rationale}"
            )

            optimize_prompt = self.prompt_loader.render("optimize.md", {
                "raw_prompt": raw_prompt,
                "analysis_summary": analysis_summary,
                "strategy_instructions": strategy_content,
                "codebase_guidance": codebase_guidance,
                "codebase_context": codebase_context,
                "adaptation_state": adaptation_state,
            })

            # Dynamic max_tokens: at least 16384, or 2x estimated prompt tokens
            estimated_tokens = len(raw_prompt) // 4  # rough estimate
            max_tokens = max(16384, estimated_tokens * 2)

            optimization = await _retry_llm_call(
                provider,
                model="claude-opus-4-6",
                system_prompt=self.prompt_loader.load("agent-guidance.md"),
                user_message=optimize_prompt,
                output_format=OptimizationResult,
                max_tokens=max_tokens,
                effort="high",
            )

            yield PipelineEvent(
                event="prompt_preview",
                data={
                    "prompt": optimization.optimized_prompt,
                    "changes": optimization.changes_summary,
                },
            )
            yield PipelineEvent(event="status", data={"stage": "optimizing", "state": "complete"})

            # --- Phase 3: Score ---
            yield PipelineEvent(event="status", data={"stage": "scoring", "state": "running"})

            # Randomize A/B assignment for bias mitigation
            original_first = random.choice([True, False])
            if original_first:
                prompt_a, prompt_b = raw_prompt, optimization.optimized_prompt
                presentation_order = "original_first"
            else:
                prompt_a, prompt_b = optimization.optimized_prompt, raw_prompt
                presentation_order = "optimized_first"

            # Log presentation order for bias analysis (spec Section 1)
            logger.info("Scorer presentation_order=%s trace_id=%s", presentation_order, trace_id)

            scorer_message = (
                f"## Prompt A\n\n{prompt_a}\n\n"
                f"## Prompt B\n\n{prompt_b}"
            )

            score_result = await _retry_llm_call(
                provider,
                model="claude-sonnet-4-6",
                system_prompt=self.prompt_loader.load("scoring.md"),
                user_message=scorer_message,
                output_format=ScoreResult,
                effort="medium",
            )

            # Map A/B back to original/optimized
            if presentation_order == "original_first":
                original_scores = score_result.prompt_a_scores
                optimized_scores = score_result.prompt_b_scores
            else:
                original_scores = score_result.prompt_b_scores
                optimized_scores = score_result.prompt_a_scores

            # Compute deltas
            score_deltas = {}
            for dim in DimensionScores.model_fields:
                orig_val = getattr(original_scores, dim)
                opt_val = getattr(optimized_scores, dim)
                score_deltas[dim] = round(opt_val - orig_val, 2)

            yield PipelineEvent(
                event="score_card",
                data={
                    "scores": optimized_scores.model_dump(),
                    "original_scores": original_scores.model_dump(),
                    "deltas": score_deltas,
                },
            )

            # --- Persist ---
            duration_ms = int((time.monotonic() - start_time) * 1000)

            result = PipelineResult(
                id=opt_id,
                trace_id=trace_id,
                raw_prompt=raw_prompt,
                optimized_prompt=optimization.optimized_prompt,
                task_type=analysis.task_type,
                strategy_used=strategy_name,
                changes_summary=optimization.changes_summary,
                optimized_scores=optimized_scores,
                original_scores=original_scores,
                score_deltas=score_deltas,
                overall_score=optimized_scores.overall,
                provider=provider.name,
                model_used="claude-opus-4-6",
                scoring_mode="independent",
                duration_ms=duration_ms,
                status="completed",
                context_sources={
                    "codebase_guidance": codebase_guidance is not None,
                    "codebase_context": codebase_context is not None,
                    "adaptation": adaptation_state is not None,
                },
            )

            # Save to database
            opt_row = Optimization(
                id=result.id,
                raw_prompt=result.raw_prompt,
                optimized_prompt=result.optimized_prompt,
                task_type=result.task_type,
                strategy_used=result.strategy_used,
                changes_summary=result.changes_summary,
                score_clarity=optimized_scores.clarity,
                score_specificity=optimized_scores.specificity,
                score_structure=optimized_scores.structure,
                score_faithfulness=optimized_scores.faithfulness,
                score_conciseness=optimized_scores.conciseness,
                overall_score=result.overall_score,
                provider=result.provider,
                model_used=result.model_used,
                scoring_mode=result.scoring_mode,
                duration_ms=result.duration_ms,
                status=result.status,
                trace_id=result.trace_id,
                context_sources=result.context_sources,
                original_scores=original_scores.model_dump(),
                score_deltas=result.score_deltas,
            )
            db.add(opt_row)
            await db.commit()

            yield PipelineEvent(event="optimization_complete", data=result.model_dump(mode="json"))

        except Exception as exc:
            logger.exception("Pipeline error at trace_id=%s", trace_id)
            duration_ms = int((time.monotonic() - start_time) * 1000)

            # Rollback any dirty session state before persisting failure
            await db.rollback()

            # Persist failed optimization
            opt_row = Optimization(
                id=opt_id,
                raw_prompt=raw_prompt,
                status="failed",
                trace_id=trace_id,
                provider=provider.name,
                duration_ms=duration_ms,
            )
            db.add(opt_row)
            await db.commit()

            yield PipelineEvent(
                event="error",
                data={"stage": "pipeline", "message": str(exc), "trace_id": trace_id},
            )
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_pipeline.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/pipeline.py tests/test_pipeline.py
git commit -m "feat: implement pipeline orchestrator with A/B scorer and SSE events"
```

---

### Task 8: Routers (Health + Optimize)

**Files:**
- Create: `backend/app/routers/health.py`
- Create: `backend/app/routers/optimize.py`
- Modify: `backend/tests/conftest.py` — add `app_client` fixture
- Create: `backend/tests/test_routers.py`

- [ ] **Step 1: Update conftest.py with app_client fixture**

Add these fixtures to `backend/tests/conftest.py`:

```python
# Add to existing conftest.py — keep existing db_session fixture

from unittest.mock import AsyncMock
from httpx import ASGITransport, AsyncClient
import pytest_asyncio

from app.providers.base import LLMProvider


@pytest_asyncio.fixture
async def mock_provider():
    """Mock LLM provider for testing."""
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"
    return provider


@pytest_asyncio.fixture
async def app_client(mock_provider, db_session):
    """Async HTTP client with mocked provider and in-memory DB."""
    from app.main import app
    from app.database import get_db

    # Override provider
    app.state.provider = mock_provider

    # Override DB dependency
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
```

- [ ] **Step 2: Write router tests**

```python
# backend/tests/test_routers.py
"""Tests for API routers."""

import json

import pytest

from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
)


class TestHealthRouter:
    async def test_health_check(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert "version" in data
        assert "provider" in data

    async def test_health_no_provider(self, app_client):
        app_client._transport.app.state.provider = None  # type: ignore
        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["provider"] is None


class TestOptimizeRouter:
    async def test_optimize_sse_stream(self, app_client, mock_provider):
        mock_provider.complete_parsed.side_effect = [
            AnalysisResult(
                task_type="coding", weaknesses=["vague"], strengths=["concise"],
                selected_strategy="chain-of-thought", strategy_rationale="good",
                confidence=0.9,
            ),
            OptimizationResult(
                optimized_prompt="Better prompt",
                changes_summary="Added specificity",
                strategy_used="chain-of-thought",
            ),
            ScoreResult(
                prompt_a_scores=DimensionScores(
                    clarity=4.0, specificity=3.0, structure=5.0,
                    faithfulness=5.0, conciseness=6.0,
                ),
                prompt_b_scores=DimensionScores(
                    clarity=8.0, specificity=8.0, structure=7.0,
                    faithfulness=9.0, conciseness=7.0,
                ),
            ),
        ]

        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": "Write a function that sorts a list"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # Parse SSE events
        events = []
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        # Should have at least optimization_start and optimization_complete
        event_types = [e.get("event") or e.get("type", "") for e in events]
        assert len(events) >= 2

    async def test_optimize_missing_prompt(self, app_client):
        resp = await app_client.post("/api/optimize", json={})
        assert resp.status_code == 422

    async def test_optimize_empty_prompt(self, app_client):
        resp = await app_client.post("/api/optimize", json={"prompt": ""})
        assert resp.status_code == 422

    async def test_get_optimization_by_trace_id(self, app_client, mock_provider, db_session):
        """Test GET /api/optimize/{trace_id} for SSE reconnection support."""
        from app.models import Optimization

        # Insert a completed optimization
        opt = Optimization(
            id="test-opt-1",
            raw_prompt="test",
            optimized_prompt="better test",
            task_type="coding",
            strategy_used="chain-of-thought",
            overall_score=7.5,
            status="completed",
            trace_id="trace-reconnect-1",
            provider="mock",
        )
        db_session.add(opt)
        await db_session.commit()

        resp = await app_client.get("/api/optimize/trace-reconnect-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["optimized_prompt"] == "better test"
        assert data["trace_id"] == "trace-reconnect-1"

    async def test_get_optimization_not_found(self, app_client):
        resp = await app_client.get("/api/optimize/nonexistent-trace")
        assert resp.status_code == 404

    async def test_no_provider_returns_503(self, app_client):
        app_client._transport.app.state.provider = None  # type: ignore
        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": "test prompt"},
        )
        assert resp.status_code == 503
```

- [ ] **Step 3: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_routers.py -v`
Expected: FAIL (import errors)

- [ ] **Step 4: Implement health router**

```python
# backend/app/routers/health.py
"""Health check endpoint."""

from fastapi import APIRouter, Request

from app._version import __version__

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Liveness check with provider and version info."""
    provider = getattr(request.app.state, "provider", None)
    return {
        "status": "healthy" if provider else "degraded",
        "version": __version__,
        "provider": provider.name if provider else None,
    }
```

- [ ] **Step 5: Implement optimize router**

```python
# backend/app/routers/optimize.py
"""Optimization endpoints — POST /api/optimize (SSE) and GET /api/optimize/{id}."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR
from app.database import get_db
from app.models import Optimization
from app.services.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizeRequest(BaseModel):
    """Request body for POST /api/optimize."""

    prompt: str = Field(..., min_length=1, description="The raw prompt to optimize")
    strategy: str | None = Field(None, description="Strategy override (e.g. 'chain-of-thought')")


def _format_sse(event_type: str, data: dict) -> str:
    """Format a server-sent event."""
    payload = json.dumps({"event": event_type, **data})
    return f"data: {payload}\n\n"


@router.post("/optimize")
async def optimize(
    body: OptimizeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Run the optimization pipeline, streaming SSE events."""
    provider = getattr(request.app.state, "provider", None)
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="No LLM provider available. Configure ANTHROPIC_API_KEY or install Claude CLI.",
        )

    orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

    async def event_stream():
        async for event in orchestrator.run(
            raw_prompt=body.prompt,
            provider=provider,
            db=db,
            strategy_override=body.strategy,
        ):
            yield _format_sse(event.event, event.data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/optimize/{trace_id}")
async def get_optimization(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a completed optimization by trace_id (SSE reconnection support).

    The frontend stores trace_id from the optimization_start SSE event.
    On connection drop, it polls this endpoint to retrieve the final result.
    """
    result = await db.execute(
        select(Optimization).where(Optimization.trace_id == trace_id)
    )
    opt = result.scalar_one_or_none()
    if not opt:
        raise HTTPException(status_code=404, detail="Optimization not found")

    return {
        "id": opt.id,
        "trace_id": opt.trace_id,
        "raw_prompt": opt.raw_prompt,
        "optimized_prompt": opt.optimized_prompt,
        "task_type": opt.task_type,
        "strategy_used": opt.strategy_used,
        "changes_summary": opt.changes_summary,
        "scores": {
            "clarity": opt.score_clarity,
            "specificity": opt.score_specificity,
            "structure": opt.score_structure,
            "faithfulness": opt.score_faithfulness,
            "conciseness": opt.score_conciseness,
        },
        "original_scores": opt.original_scores,
        "score_deltas": opt.score_deltas,
        "overall_score": opt.overall_score,
        "provider": opt.provider,
        "model_used": opt.model_used,
        "scoring_mode": opt.scoring_mode,
        "duration_ms": opt.duration_ms,
        "status": opt.status,
        "context_sources": opt.context_sources,
        "created_at": opt.created_at.isoformat() if opt.created_at else None,
    }
```

- [ ] **Step 6: Run tests — verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_routers.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/routers/ tests/conftest.py tests/test_routers.py app/database.py
git commit -m "feat: implement health and optimize routers with SSE streaming"
```

---

### Task 9: Prompt Caching Tests + Handoff

**Files:**
- Create: `backend/tests/test_prompt_caching.py`
- Create: `docs/superpowers/plans/handoffs/handoff-phase-1a.json`

- [ ] **Step 1: Write prompt caching test**

```python
# backend/tests/test_prompt_caching.py
"""Tests verifying prompt caching on the API provider path."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.pipeline_contracts import AnalysisResult


class TestPromptCaching:
    async def test_api_provider_sets_cache_control(self):
        """Verify system prompt includes cache_control for prompt caching."""
        with patch("app.providers.anthropic_api.AsyncAnthropic") as MockClass:
            client = AsyncMock()
            MockClass.return_value = client

            mock_response = MagicMock()
            mock_response.parsed_output = AnalysisResult(
                task_type="coding", weaknesses=[], strengths=[],
                selected_strategy="auto", strategy_rationale="", confidence=0.5,
            )
            mock_response.usage.input_tokens = 100
            mock_response.usage.output_tokens = 50
            client.messages.parse = AsyncMock(return_value=mock_response)

            from app.providers.anthropic_api import AnthropicAPIProvider

            provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
            provider.client = client

            await provider.complete_parsed(
                model="claude-sonnet-4-6",
                system_prompt="You are an expert.",
                user_message="Analyze this.",
                output_format=AnalysisResult,
            )

            call_kwargs = client.messages.parse.call_args.kwargs
            system = call_kwargs["system"]
            assert isinstance(system, list)
            assert len(system) == 1
            assert system[0]["cache_control"] == {"type": "ephemeral"}

    async def test_cache_control_present_across_models(self):
        """All model calls should include cache_control on system prompt."""
        with patch("app.providers.anthropic_api.AsyncAnthropic") as MockClass:
            client = AsyncMock()
            MockClass.return_value = client

            mock_response = MagicMock()
            mock_response.parsed_output = AnalysisResult(
                task_type="writing", weaknesses=[], strengths=[],
                selected_strategy="auto", strategy_rationale="", confidence=0.5,
            )
            mock_response.usage.input_tokens = 50
            mock_response.usage.output_tokens = 30
            client.messages.parse = AsyncMock(return_value=mock_response)

            from app.providers.anthropic_api import AnthropicAPIProvider

            provider = AnthropicAPIProvider.__new__(AnthropicAPIProvider)
            provider.client = client

            for model in ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]:
                await provider.complete_parsed(
                    model=model,
                    system_prompt="System prompt here.",
                    user_message="Message.",
                    output_format=AnalysisResult,
                )

            # All 3 calls should have cache_control
            assert client.messages.parse.call_count == 3
            for call in client.messages.parse.call_args_list:
                system = call.kwargs["system"]
                assert system[0]["cache_control"] == {"type": "ephemeral"}
```

- [ ] **Step 2: Run test — verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_prompt_caching.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite with coverage**

Run: `cd backend && source .venv/bin/activate && pytest --cov=app --cov-report=term-missing -v`
Expected: All tests PASS, coverage reported

- [ ] **Step 4: Commit**

```bash
cd backend && git add tests/test_prompt_caching.py
git commit -m "test: add prompt caching verification tests"
```

- [ ] **Step 5: Write handoff artifact**

Generate `docs/superpowers/plans/handoffs/handoff-phase-1a.json` with actual test counts and coverage from Step 3. The handoff should follow this structure:

```json
{
  "phase": "1a",
  "status": "completed",
  "timestamp": "<current ISO timestamp>",
  "summary": "Core pipeline operational. Providers (CLI/API), prompt loader, strategy loader, pipeline orchestrator, and routers implemented. POST /api/optimize returns real optimized prompts via SSE. All prompt templates populated with real content.",
  "files_created": [
    "backend/app/database.py",
    "backend/app/schemas/pipeline_contracts.py",
    "backend/app/providers/base.py",
    "backend/app/providers/anthropic_api.py",
    "backend/app/providers/claude_cli.py",
    "backend/app/providers/detector.py",
    "backend/app/services/prompt_loader.py",
    "backend/app/services/strategy_loader.py",
    "backend/app/services/pipeline.py",
    "backend/app/routers/health.py",
    "backend/app/routers/optimize.py",
    "backend/tests/test_contracts.py",
    "backend/tests/test_providers.py",
    "backend/tests/test_prompt_loader.py",
    "backend/tests/test_strategy_loader.py",
    "backend/tests/test_pipeline.py",
    "backend/tests/test_prompt_caching.py",
    "backend/tests/test_routers.py"
  ],
  "files_modified": [
    "backend/app/main.py",
    "backend/tests/conftest.py",
    "prompts/agent-guidance.md",
    "prompts/analyze.md",
    "prompts/optimize.md",
    "prompts/scoring.md",
    "prompts/adaptation.md",
    "prompts/manifest.json",
    "prompts/strategies/chain-of-thought.md",
    "prompts/strategies/few-shot.md",
    "prompts/strategies/role-playing.md",
    "prompts/strategies/structured-output.md",
    "prompts/strategies/meta-prompting.md",
    "prompts/strategies/auto.md"
  ],
  "entry_conditions_met": [
    "Phase 0 handoff exists with all_passed: true",
    "Project skeleton intact",
    "Database has 10 tables",
    "pytest discovers test directory"
  ],
  "exit_conditions": {
    "all_passed": "<true/false based on actual results>",
    "tests_total": "<actual count>",
    "tests_passed": "<actual count>",
    "coverage_percent": "<actual percentage>",
    "verification_commands": [
      {"cmd": "cd backend && pytest --cov=app -v", "result": "<actual>"},
      {"cmd": "curl POST /api/optimize", "result": "<actual>"}
    ]
  },
  "warnings": [],
  "next_phase_context": {
    "critical_interfaces": [
      "PipelineOrchestrator(prompts_dir).run(raw_prompt, provider, db, ...) → AsyncGenerator[PipelineEvent]",
      "provider.complete_parsed(model, system_prompt, user_message, output_format, effort) → T",
      "PromptLoader(prompts_dir).render(name, variables) → str",
      "StrategyLoader(strategies_dir).load(name) → str"
    ],
    "env_vars_required": [
      "ANTHROPIC_API_KEY (optional — CLI auto-detected first)"
    ],
    "known_limitations": [
      "codebase_guidance and codebase_context always None until Phase 2",
      "adaptation_state always None until Phase 1b",
      "No rate limiting yet — Phase 1b adds RateLimit dependency",
      "No trace logging yet — Phase 1b adds trace_logger",
      "GET /api/optimize/{id} returns by optimization ID, not trace_id"
    ],
    "alembic_revision": "42eace9b572e"
  }
}
```

- [ ] **Step 6: Commit handoff**

```bash
git add docs/superpowers/plans/handoffs/handoff-phase-1a.json
git commit -m "docs: write Phase 1a handoff artifact"
```

---

## Exit Conditions Checklist

Map of Phase 1a exit conditions (from orchestration protocol) to tasks:

| # | Exit Condition | Task |
|---|---------------|------|
| 1 | All 4 providers implemented | Task 3 |
| 2 | `complete_parsed()` uses adaptive thinking with correct effort | Task 3 |
| 3 | Haiku calls use thinking disabled | Task 3 |
| 4 | All subagent outputs use `output_format` with Pydantic models | Task 7 |
| 5 | System prompts use `cache_control` on API path | Task 3, 9 |
| 6 | `prompt_loader.py` implemented | Task 4 |
| 7 | `strategy_loader.py` implemented | Task 5 |
| 8 | `pipeline.py` implemented (analyzer → optimizer → scorer) | Task 7 |
| 9 | All contracts use `extra="forbid"` | Task 1 |
| 10 | Scorer receives neutral "Prompt A"/"Prompt B" | Task 7 |
| 11 | `POST /api/optimize` returns real optimized prompt | Task 8 |
| 12 | `GET /api/optimize/{id}` returns full result | Task 8 |
| 13 | SSE events in correct order | Task 7, 8 |
| 14 | All prompt templates have real content | Task 6 |
| 15 | `handoff-phase-1a.json` written | Task 9 |
