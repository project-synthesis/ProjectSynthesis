"""Topic Probe agentic generator primitive (Tier 1, v0.5.0).

Pure function: ProbeContext + provider -> list[str] of code-grounded prompts.

Honors:
- PromptLoader.render() for hot-reload (str-cast int values)
- call_provider_with_retry(max_retries=3) - explicit, since default is 1
- Inline Pydantic PromptList(BaseModel) - mirrors seed_orchestrator pattern
- Sonnet model for long-context codebase awareness
- Backtick-density filter - drops prompts without >=1 backtick-wrapped
  identifier (matches F1 specificity heuristic). >50% drop raises.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.config import PROMPTS_DIR, settings
from app.providers.base import call_provider_with_retry
from app.schemas.probes import ProbeContext  # re-exported below for convenience
from app.services.prompt_loader import PromptLoader

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

__all__ = [
    "ProbeContext",
    "ProbeGenerationError",
    "generate_probe_prompts",
]


class ProbeGenerationError(Exception):
    """Raised when probe-agent generation fails persistently."""


_MIN_PROMPTS = 5
_MAX_PROMPTS = 25
_BACKTICK_RX = re.compile(r"`[^`]+`")
_DROP_THRESHOLD = 0.5  # > 50% dropped -> error


def _clamp_n(n: int) -> int:
    return max(_MIN_PROMPTS, min(_MAX_PROMPTS, n))


def _has_backtick(prompt: str) -> bool:
    return bool(_BACKTICK_RX.search(prompt))


async def generate_probe_prompts(
    probe_ctx: ProbeContext,
    *,
    provider: "LLMProvider",
    n_prompts: int = 12,
) -> list[str]:
    """Single Sonnet call to probe-agent.md -> list of code-grounded prompts.

    See spec §4.4 for full contract.
    """
    n_prompts = _clamp_n(n_prompts)

    # Inline Pydantic schema mirrors seed_orchestrator.py pattern.
    class PromptList(BaseModel):
        prompts: list[str] = Field(description="Generated probe prompts")

    loader = PromptLoader(PROMPTS_DIR)
    variables: dict[str, str | None] = {
        "topic": probe_ctx.topic,
        "scope": probe_ctx.scope,
        "intent_hint": probe_ctx.intent_hint,
        "n_prompts": str(n_prompts),  # str-cast required (PromptLoader.render dict[str, str | None])
        "repo_full_name": probe_ctx.repo_full_name,
        "codebase_context": (
            (probe_ctx.explore_synthesis_excerpt or "") + "\n\n" +
            "\n".join(f"- {f}" for f in probe_ctx.relevant_files)
        )[: settings.PROBE_CODEBASE_MAX_CHARS],
        "known_domains": ", ".join(probe_ctx.known_domains) or "(none yet)",
        "existing_clusters_brief": ", ".join(
            f"{c['label']}" for c in probe_ctx.existing_clusters_brief
        ) or "(none yet)",
    }
    user_message = loader.render("probe-agent.md", variables)

    result: PromptList = await call_provider_with_retry(
        provider,
        model=settings.MODEL_SONNET,
        system_prompt="",  # entire body is the user_message rendering
        user_message=user_message,
        output_format=PromptList,
        max_retries=3,
    )

    # Backtick-density filter - drops prompts without >=1 backtick identifier.
    valid = [p for p in result.prompts if _has_backtick(p)]
    dropped = len(result.prompts) - len(valid)
    if result.prompts and dropped / max(1, len(result.prompts)) > _DROP_THRESHOLD:
        raise ProbeGenerationError(
            f"Generator produced too many prompts without backtick identifiers: "
            f"{dropped}/{len(result.prompts)} dropped (>{_DROP_THRESHOLD*100:.0f}% threshold)"
        )

    # Clamp to requested n_prompts (after filter - generator may overproduce).
    return valid[:n_prompts]
