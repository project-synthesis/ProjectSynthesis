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

    Args:
        probe_ctx: Phase 1 grounding output. Under codebase mode the
            context carries the curated retrieval payload (relevant_files
            + explore_synthesis_excerpt + repo_full_name + known_domains).
            Under topic-only mode it carries the degenerate state
            documented in ``ProbeContext`` (``repo_full_name=None``,
            empty lists, ``topic_only=True``) — the templates handle the
            empty-context rendering at the manifest level (optional
            vars).
        provider: Active ``LLMProvider`` for the Sonnet call. Routed
            through ``call_provider_with_retry(max_retries=3)``.
        n_prompts: Target prompt count, clamped to [_MIN_PROMPTS,
            _MAX_PROMPTS] = [5, 25].
        mode:
            -   ``'codebase'`` (default) preserves the v0.4.12 contract —
                drops prompts WITHOUT backtick code identifiers (the F1
                specificity filter).
            -   ``'topic_only'`` inverts the per-prompt predicate —
                drops prompts WITH any backticks (the topic-only template
                instructs the LLM to produce prose-only prompts; a
                stray backtick is signal that the LLM ignored the
                directive).
            Both modes share the same ``_DROP_THRESHOLD=0.5`` batch-level
            envelope (> 50% dropped → ``ProbeGenerationError``) and the
            same error-message contract (both reference the word
            ``backtick`` so the test regex ``match=r"backtick"`` covers
            both directions).
        template_name: Markdown template file to hot-reload via
            ``PromptLoader``. Contract:
            -   The template MUST declare the same variable surface as
                the canonical ``probe-agent.md`` (topic, scope,
                intent_hint, n_prompts, known_domains,
                existing_clusters_brief). ``repo_full_name`` and
                ``codebase_context`` are optional in the topic-only
                manifest entry — the template body may omit them, and
                ``PromptLoader.render`` will substitute empty strings.
            -   ``manifest.json`` is the source of truth for which
                variables are required vs optional per template; startup
                ``PromptLoader.validate_all()`` rejects templates whose
                body is missing a declared-required placeholder.

    Returns:
        ``list[str]`` of length ``<= n_prompts`` (the filter may drop
        items; if it drops more than ``_DROP_THRESHOLD=0.5`` of the
        batch, ``ProbeGenerationError`` is raised instead).

    Raises:
        ProbeGenerationError: when the per-prompt filter drops more than
            50% of the generator's output (under either mode direction).
            Message always contains the substring ``"backtick"`` so
            callers can switch on the error envelope without parsing
            mode out of the exception.

    See spec §4.4 for the full pre-Foundation contract; spec §2 + §5 for
    the T2 Cycle 7 ``mode`` + ``template_name`` extensions.
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
    # Both predicates return True iff the prompt PASSES the filter.
    # Kept as two distinct functions rather than a single dispatch so
    # each direction stays grep-able and individually testable.
    predicate = _has_backtick if mode == "codebase" else _lacks_backtick

    total = len(result.prompts)
    valid = [p for p in result.prompts if predicate(p)]
    dropped = total - len(valid)
    if total and dropped / total > _DROP_THRESHOLD:
        # Mode-specific error message — both reference "backtick" so the
        # test contract ``pytest.raises(..., match=r"backtick")`` matches
        # under either direction (codebase: prompts WITHOUT backticks
        # dropped; topic_only: prompts WITH backticks dropped).
        detail = _build_drop_threshold_error_message(mode, dropped, total)
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


def _build_drop_threshold_error_message(
    mode: Literal["codebase", "topic_only"],
    dropped: int,
    total: int,
) -> str:
    """Build the ``ProbeGenerationError`` detail string for either mode.

    Centralizes the mode-specific phrasing so callers/tests can rely on a
    single source of truth. Both directions contain the substring
    ``"backtick"`` to satisfy the ``r"backtick"`` regex contract — a
    future re-phrasing of either path must preserve that substring.
    """
    threshold_pct = f"{_DROP_THRESHOLD*100:.0f}"
    if mode == "topic_only":
        return (
            f"Generator produced too many prompts containing backtick "
            f"identifiers under topic_only mode: "
            f"{dropped}/{total} dropped (>{threshold_pct}% threshold)"
        )
    return (
        f"Generator produced too many prompts without backtick "
        f"identifiers: "
        f"{dropped}/{total} dropped (>{threshold_pct}% threshold)"
    )
