"""Haiku-based label generation for taxonomy nodes.

Calls the LLM with representative member texts to generate a concise
2-4 word label for a cluster. Falls back to "Unnamed cluster" on error.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.providers.base import LLMProvider, call_provider_with_retry

logger = logging.getLogger(__name__)

_FALLBACK_LABEL = "Unnamed cluster"


class _LabelOutput(BaseModel):
    model_config = {"extra": "forbid"}
    label: str = Field(
        description="A concise 2-4 word label describing the common theme of these texts.",
    )


async def generate_label(
    provider: LLMProvider | None,
    member_texts: list[str],
    model: str,
) -> str:
    """Generate a label for a cluster from its member texts.

    Args:
        provider: LLM provider (Haiku). None = return fallback.
        member_texts: Representative texts from the cluster (truncated to 200 chars each).
        model: Model ID to use for generation.

    Returns:
        A short label string (2-4 words).
    """
    if not provider:
        return _FALLBACK_LABEL

    truncated = [t[:200] for t in member_texts[:10]]
    sample_block = "\n".join(f"- {t}" for t in truncated)

    try:
        result = await call_provider_with_retry(
            provider,
            model=model,
            system_prompt=(
                "You are a taxonomy labeler. Given a list of text samples that "
                "belong to the same cluster, generate a concise 2-4 word label "
                "that captures their common theme. Be specific — 'API Architecture' "
                "is better than 'Backend'."
            ),
            user_message=f"Cluster samples:\n{sample_block}",
            output_format=_LabelOutput,
        )
        label = result.label.strip()
        if label:
            return label
        return _FALLBACK_LABEL
    except Exception as exc:
        logger.warning("Label generation failed (non-fatal): %s", exc)
        return _FALLBACK_LABEL
