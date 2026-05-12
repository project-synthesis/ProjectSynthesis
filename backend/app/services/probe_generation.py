"""Topic Probe agentic generator primitive (Tier 1, v0.5.0).

Pure function: ProbeContext + provider -> list[str] of code-grounded prompts.

Honors:
- PromptLoader.render() for hot-reload (str-cast int values)
- call_provider_with_retry(max_retries=3) - explicit, since default is 1
- Inline Pydantic PromptList(BaseModel) - mirrors seed_orchestrator pattern
- Sonnet model for long-context codebase awareness
- Backtick-density filter - drops prompts without >=1 backtick-wrapped
  *code identifier* (regex aligned with F1 specificity heuristic in
  heuristic_scorer.py: `[a-zA-Z_][a-zA-Z0-9_./:-]*` — excludes spaces so
  `arbitrary phrase` does not earn structural credit). >50% drop raises.

T2 Cycle 7 (spec §2 + §5 topic-only branch):
- NEW kwarg ``mode: Literal['codebase', 'topic_only'] = 'codebase'`` —
  ``topic_only`` inverts the per-prompt predicate so prompts WITH
  backtick identifiers are dropped (the inverse of codebase mode, which
  drops prompts WITHOUT).
- NEW kwarg ``template_name: str = 'probe-agent.md'`` — selects the
  template hot-reloaded by ``PromptLoader``. Topic-only callers pass
  ``'probe-agent-topic-only.md'``.
- The batch-level ``_DROP_THRESHOLD=0.5`` (>50% dropped → error) is
  preserved across BOTH modes — only the per-prompt predicate flips
  direction.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Literal

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
# Aligned with F1 specificity heuristic (heuristic_scorer.py category 11):
# only matches backtick-wrapped *code identifiers* — letters/digits/`._-:/`,
# starting with a letter/underscore. Prose-in-backticks (`free phrase`) is
# rejected because spaces are excluded from the char class.
_BACKTICK_RX = re.compile(r"`[a-zA-Z_][a-zA-Z0-9_./:-]*`")
_DROP_THRESHOLD = 0.5  # > 50% dropped -> error


def _clamp_n(n: int) -> int:
    return max(_MIN_PROMPTS, min(_MAX_PROMPTS, n))


def _has_backtick(prompt: str) -> bool:
    """Codebase-mode predicate — prompt PASSES if it contains a backtick
    identifier (i.e. the prompt is code-grounded).
    """
    return bool(_BACKTICK_RX.search(prompt))


def _lacks_backtick(prompt: str) -> bool:
    """Topic-only-mode predicate — prompt PASSES if it contains NO backticks
    at all (the topic-only template instructs the LLM to omit code refs).

    Uses the simpler ``"`" not in prompt`` check rather than the strict
    F1 regex: topic-only callers should never produce backticks; any
    backtick at all is signal that the LLM ignored the instruction.
    """
    return "`" not in prompt


async def generate_probe_prompts(
    probe_ctx: ProbeContext,
    *,
    provider: "LLMProvider",
    n_prompts: int = 12,
    mode: Literal["codebase", "topic_only"] = "codebase",
    template_name: str = "probe-agent.md",
) -> list[str]:
    """Single Sonnet call to a probe-agent template -> list of prompts.

    T2 Cycle 7 (spec §2 + §5):
      - ``mode='codebase'`` (default) preserves the v0.4.12 contract — drops
        prompts WITHOUT backtick identifiers (the F1 specificity filter).
      - ``mode='topic_only'`` inverts the per-prompt predicate — drops
        prompts WITH backticks (the topic-only template instructs the LLM
        to produce prose-only prompts).
      - ``template_name`` selects the template hot-reloaded by
        ``PromptLoader``; topic-only callers pass ``probe-agent-topic-only.md``.

    The batch-level ``_DROP_THRESHOLD=0.5`` (>50% dropped → error) and the
    error envelope (``ProbeGenerationError("...backtick...")``) are
    identical across both modes. See spec §4.4 for full contract.
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
    user_message = loader.render(template_name, variables)

    result: PromptList = await call_provider_with_retry(
        provider,
        model=settings.MODEL_SONNET,
        system_prompt="",  # entire body is the user_message rendering
        user_message=user_message,
        output_format=PromptList,
        max_retries=3,
    )

    # Per-prompt predicate — flips direction by ``mode`` (T2 Cycle 7).
    # Both predicates return True iff the prompt PASSES the filter (kept).
    predicate = _has_backtick if mode == "codebase" else _lacks_backtick

    total = len(result.prompts)
    valid = [p for p in result.prompts if predicate(p)]
    dropped = total - len(valid)
    if total and dropped / total > _DROP_THRESHOLD:
        # Mode-specific error message — both reference "backtick" so the
        # test's ``pytest.raises(..., match=r"backtick")`` matches under
        # either direction (codebase: prompts WITHOUT backticks dropped;
        # topic_only: prompts WITH backticks dropped).
        if mode == "topic_only":
            detail = (
                f"Generator produced too many prompts containing backtick "
                f"identifiers under topic_only mode: "
                f"{dropped}/{total} dropped (>{_DROP_THRESHOLD*100:.0f}% threshold)"
            )
        else:
            detail = (
                f"Generator produced too many prompts without backtick "
                f"identifiers: "
                f"{dropped}/{total} dropped (>{_DROP_THRESHOLD*100:.0f}% threshold)"
            )
        logger.warning(
            "probe_generation: drop-threshold exceeded for topic=%r mode=%r "
            "(%d/%d dropped, >%.0f%% threshold) — raising ProbeGenerationError",
            probe_ctx.topic, mode, dropped, total, _DROP_THRESHOLD * 100,
        )
        raise ProbeGenerationError(detail)
    if dropped:
        logger.info(
            "probe_generation: filtered %d/%d prompts under mode=%r for topic=%r",
            dropped, total, mode, probe_ctx.topic,
        )

    # Clamp to requested n_prompts (after filter - generator may overproduce).
    return valid[:n_prompts]
