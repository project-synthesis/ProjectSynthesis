"""SeedOrchestrator — explore-driven prompt generation for taxonomy seeding.

Chains: parallel agent dispatch → prompt deduplication.
Does NOT run the optimization pipeline — that's Phase 2 (batch_pipeline.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.config import DATA_DIR, PROMPTS_DIR, settings
from app.providers.base import LLMProvider, call_provider_with_retry
from app.services.agent_loader import AgentLoader, SeedAgent
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader
from app.services.trace_logger import TraceLogger

logger = logging.getLogger(__name__)


def _get_trace_logger() -> TraceLogger | None:
    """Best-effort TraceLogger for seed dispatch observability.

    Returns None if the traces directory cannot be created. Trace failure
    must never break seed dispatch.
    """
    try:
        return TraceLogger(DATA_DIR / "traces")
    except OSError:
        logger.debug("Could not create traces directory; seed traces disabled")
        return None


def _resolve_agent_model(agent: SeedAgent) -> str:
    """Resolve the model ID for a seed agent.

    Agents may opt into a specific tier via the ``model:`` frontmatter key
    (``sonnet|opus|haiku``). Default is Haiku — structured output of short
    creative prompts is Haiku's strength, and parallel dispatch (10 CLI /
    5 API) amortizes any per-call quality gap. Opting an agent into Sonnet
    is useful for diversity A/B tests without flipping the global default.
    """
    key = (getattr(agent, "model", None) or "").strip().lower()
    if key == "sonnet":
        return settings.MODEL_SONNET
    if key == "opus":
        return settings.MODEL_OPUS
    if key == "haiku":
        return settings.MODEL_HAIKU
    return settings.MODEL_HAIKU


@dataclass
class GenerationResult:
    """Result of the orchestrator's generate phase."""
    batch_id: str
    prompts: list[str]
    prompts_before_dedup: int
    agents_used: list[str]
    per_agent: list[dict[str, Any]]
    explore_context: str | None
    workspace_profile: str | None
    duration_ms: int


def deduplicate_prompts(
    prompts: list[str],
    threshold: float = 0.90,
) -> list[str]:
    """Remove near-duplicate prompts using embedding cosine similarity."""
    if len(prompts) <= 1:
        return prompts
    try:
        svc = EmbeddingService()
        embeddings = [svc.embed_single(p) for p in prompts]
        mat = np.stack(embeddings)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        mat_norm = mat / norms

        keep: list[int] = []
        for i in range(len(prompts)):
            is_dup = False
            for j in keep:
                sim = float(np.dot(mat_norm[i], mat_norm[j]))
                if sim > threshold:
                    is_dup = True
                    break
            if not is_dup:
                keep.append(i)
        return [prompts[i] for i in keep]
    except Exception as exc:
        logger.warning("Deduplication failed (returning all): %s", exc)
        return prompts


class SeedOrchestrator:
    """Orchestrates parallel agent dispatch + deduplication for batch seeding."""

    def __init__(
        self,
        provider: LLMProvider | None,
        agents_dir: Path | None = None,
        prompt_loader: PromptLoader | None = None,
    ) -> None:
        self._provider = provider
        self._agent_loader = AgentLoader(agents_dir or (PROMPTS_DIR / "seed-agents"))
        self._prompt_loader = prompt_loader or PromptLoader(PROMPTS_DIR)

    async def generate(
        self,
        project_description: str,
        batch_id: str,
        workspace_profile: str | None = None,
        codebase_context: str | None = None,
        agent_names: list[str] | None = None,
        prompt_count: int = 30,
    ) -> GenerationResult:
        """Run parallel agent dispatch + deduplicate. Returns prompt list."""
        t0 = time.monotonic()

        if agent_names:
            agents = [a for a in self._agent_loader.list_enabled() if a.name in agent_names]
        else:
            agents = self._agent_loader.list_enabled()

        if not agents:
            raise ValueError("No enabled seed agents found")
        if not self._provider:
            raise ValueError("No LLM provider available for prompt generation")

        # Scale prompts_per_run to hit target count
        total_default = sum(a.prompts_per_run for a in agents)
        scale = prompt_count / total_default if total_default > 0 else 1.0

        async def _run_agent(agent: SeedAgent) -> tuple[str, list[str], int]:
            agent_t0 = time.monotonic()
            scaled_count = max(2, int(agent.prompts_per_run * scale))
            resolved_model = _resolve_agent_model(agent)
            variables = {
                "project_description": project_description,
                "workspace_profile": workspace_profile or "Not available",
                "codebase_context": codebase_context or "Not available",
                "prompts_per_run": str(scaled_count),
                "task_types": ", ".join(agent.task_types),
                "phase_context": ", ".join(agent.phase_context),
            }
            user_message = self._prompt_loader.render("seed.md", variables)  # type: ignore[arg-type]

            def _emit_trace(
                status: str,
                prompts_generated: int,
                error: str | None = None,
            ) -> None:
                """Emit a per-agent JSONL trace entry (best-effort)."""
                tl = _get_trace_logger()
                if tl is None or self._provider is None:
                    return
                try:
                    usage = getattr(self._provider, "last_usage", None)
                    tokens_in = getattr(usage, "input_tokens", 0) or 0
                    tokens_out = getattr(usage, "output_tokens", 0) or 0
                    result: dict[str, object] = {
                        "agent": agent.name,
                        "batch_id": batch_id,
                        "prompts_generated": prompts_generated,
                        "scaled_count": scaled_count,
                    }
                    if error is not None:
                        result["error"] = error
                    tl.log_phase(
                        trace_id=f"seed:{batch_id}:{agent.name}",
                        phase="seed_agent",
                        duration_ms=int((time.monotonic() - agent_t0) * 1000),
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        model=resolved_model,
                        provider=type(self._provider).__name__,
                        result=result,
                        status=status,
                    )
                except Exception:
                    logger.debug("seed trace emit failed for %s", agent.name, exc_info=True)

            try:
                from pydantic import BaseModel, Field

                class PromptList(BaseModel):
                    prompts: list[str] = Field(description="List of generated prompt strings")

                if self._provider is None:
                    raise RuntimeError("No provider available for seed generation")
                result = await call_provider_with_retry(
                    self._provider,
                    model=resolved_model,
                    system_prompt=agent.body,
                    user_message=user_message,
                    output_format=PromptList,
                )
                duration = int((time.monotonic() - agent_t0) * 1000)
                prompts_returned = result.prompts[:scaled_count]
                _emit_trace(status="ok", prompts_generated=len(prompts_returned))
                return agent.name, prompts_returned, duration
            except asyncio.TimeoutError:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' timed out", agent.name)
                _emit_trace(status="error", prompts_generated=0, error="TimeoutError")
                return agent.name, [], duration
            except json.JSONDecodeError:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' returned unparseable JSON", agent.name)
                _emit_trace(status="error", prompts_generated=0, error="JSONDecodeError")
                return agent.name, [], duration
            except Exception as exc:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' failed: %s", agent.name, exc)
                _emit_trace(
                    status="error",
                    prompts_generated=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return agent.name, [], duration

        results = await asyncio.gather(
            *[_run_agent(a) for a in agents],
            return_exceptions=True,
        )

        all_prompts: list[str] = []
        per_agent: list[dict[str, Any]] = []
        agents_used: list[str] = []

        for r in results:
            if isinstance(r, BaseException):
                logger.warning("Agent dispatch error: %s", r)
                continue
            name, prompts, duration = r
            per_agent.append({"name": name, "count": len(prompts), "duration_ms": duration})
            if prompts:
                agents_used.append(name)
                all_prompts.extend(prompts)

        before_dedup = len(all_prompts)

        # Deduplicate BEFORE logging so event has actual counts
        all_prompts = deduplicate_prompts(all_prompts)

        # Log agents complete event with real dedup counts
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_agents_complete",
                context={
                    "batch_id": batch_id,
                    "prompts_generated": before_dedup,
                    "prompts_after_dedup": len(all_prompts),
                    "per_agent": per_agent,
                    "duplicates_removed": before_dedup - len(all_prompts),
                },
            )
        except RuntimeError:
            pass
        duration_ms = int((time.monotonic() - t0) * 1000)

        return GenerationResult(
            batch_id=batch_id,
            prompts=all_prompts,
            prompts_before_dedup=before_dedup,
            agents_used=agents_used,
            per_agent=per_agent,
            explore_context=codebase_context,
            workspace_profile=workspace_profile,
            duration_ms=duration_ms,
        )
