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

from app.config import PROMPTS_DIR, settings
from app.providers.base import LLMProvider, call_provider_with_retry
from app.services.agent_loader import AgentLoader, SeedAgent
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


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

        keep = []
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
            variables = {
                "project_description": project_description,
                "workspace_profile": workspace_profile or "Not available",
                "codebase_context": codebase_context or "Not available",
                "prompts_per_run": str(scaled_count),
                "task_types": ", ".join(agent.task_types),
                "phase_context": ", ".join(agent.phase_context),
            }
            user_message = self._prompt_loader.render("seed.md", variables)

            try:
                from pydantic import BaseModel, Field

                class PromptList(BaseModel):
                    prompts: list[str] = Field(description="List of generated prompt strings")

                result = await call_provider_with_retry(
                    self._provider,
                    model=settings.MODEL_HAIKU,
                    system_prompt=agent.body,
                    user_message=user_message,
                    output_format=PromptList,
                )
                duration = int((time.monotonic() - agent_t0) * 1000)
                return agent.name, result.prompts[:scaled_count], duration
            except asyncio.TimeoutError:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' timed out", agent.name)
                return agent.name, [], duration
            except json.JSONDecodeError:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' returned unparseable JSON", agent.name)
                return agent.name, [], duration
            except Exception as exc:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' failed: %s", agent.name, exc)
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

        # Log agents complete event
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_agents_complete",
                context={
                    "batch_id": batch_id,
                    "prompts_generated": before_dedup,
                    "per_agent": per_agent,
                    "duplicates_to_remove": "pending",
                },
            )
        except RuntimeError:
            pass

        all_prompts = deduplicate_prompts(all_prompts)
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
