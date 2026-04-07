"""Haiku-based label generation for taxonomy nodes.

Calls the LLM with representative member texts to generate a concise
2-4 word label for a cluster. Falls back to "Unnamed cluster" on error.

Label continuity anchor: when ``current_label`` is provided, newly generated
labels are compared via embedding cosine to prevent erratic drift during
warm-path refreshes. Labels with low cosine (< 0.5) to the current label
are rejected in favor of the existing label.
"""

from __future__ import annotations

import logging

import numpy as np
from pydantic import BaseModel, Field

from app.providers.base import LLMProvider, call_provider_with_retry
from app.services.pipeline_constants import MAX_CLUSTER_LABEL_LENGTH
from app.utils.text_cleanup import title_case_label

logger = logging.getLogger(__name__)

_FALLBACK_LABEL = "Unnamed Cluster"

# Cosine thresholds for label continuity anchor
_LABEL_DRIFT_REJECT = 0.5   # cosine < this → keep old label
_LABEL_DRIFT_WARN = 0.7     # cosine < this → warn but accept


class _LabelOutput(BaseModel):
    model_config = {"extra": "forbid"}
    label: str = Field(
        description="A concise 2-4 word label describing the common theme of these texts.",
    )


async def generate_label(
    provider: LLMProvider | None,
    member_texts: list[str],
    model: str,
    *,
    current_label: str | None = None,
) -> str:
    """Generate a label for a cluster from its member texts.

    Args:
        provider: LLM provider (Haiku). None = return fallback.
        member_texts: Representative texts from the cluster (truncated to 200 chars each).
        model: Model ID to use for generation.
        current_label: Existing cluster label. When provided, the new label
            is checked for semantic drift via embedding cosine similarity.
            Labels that drift too far (cosine < 0.5) are rejected to prevent
            erratic relabeling.

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
        new_label = result.label.strip()
        if not new_label:
            return _FALLBACK_LABEL

        new_label = title_case_label(new_label)[:MAX_CLUSTER_LABEL_LENGTH]

        # Label continuity anchor: prevent erratic drift during warm-path refreshes
        if current_label and current_label != _FALLBACK_LABEL:
            new_label = await _apply_continuity_anchor(current_label, new_label)

        return new_label
    except Exception as exc:
        logger.warning("Label generation failed (non-fatal): %s", exc)
        return _FALLBACK_LABEL


async def _apply_continuity_anchor(current_label: str, new_label: str) -> str:
    """Compare old vs new label via embedding cosine; reject if too different.

    Prevents erratic cluster relabeling when the LLM generates a semantically
    distant label on re-evaluation. Uses the local embedding model (~5ms overhead).
    """
    if current_label.lower() == new_label.lower():
        return new_label  # no change

    try:
        from app.services.embedding_service import EmbeddingService

        svc = EmbeddingService()
        old_emb = await svc.aembed_single(current_label)
        new_emb = await svc.aembed_single(new_label)

        cosine = float(np.dot(old_emb, new_emb) / (
            np.linalg.norm(old_emb) * np.linalg.norm(new_emb) + 1e-9
        ))

        if cosine < _LABEL_DRIFT_REJECT:
            logger.info(
                "Label drift rejected: '%s' → '%s' (cosine=%.3f < %.2f). Keeping old label.",
                current_label, new_label, cosine, _LABEL_DRIFT_REJECT,
            )
            return current_label

        if cosine < _LABEL_DRIFT_WARN:
            logger.info(
                "Label drift warning: '%s' → '%s' (cosine=%.3f). Accepting but flagging.",
                current_label, new_label, cosine,
            )

        return new_label
    except Exception as exc:
        logger.debug("Continuity anchor check failed (non-fatal): %s", exc)
        return new_label  # fail-open: accept new label
