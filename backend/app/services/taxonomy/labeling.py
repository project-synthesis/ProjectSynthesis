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
import math
from dataclasses import dataclass, field

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

# Cosine thresholds for qualifier vocabulary similarity matrix rendering
_VOCAB_SIM_HIGH = 0.7  # "very similar" threshold in vocab prompt
_VOCAB_SIM_LOW = 0.3   # "distinct" threshold in vocab prompt


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


# ---------------------------------------------------------------------------
# Dynamic qualifier vocabulary generation
# ---------------------------------------------------------------------------


@dataclass
class ClusterVocabContext:
    """Enriched per-cluster context for vocabulary generation."""

    label: str
    member_count: int
    intent_labels: list[str] = field(default_factory=list)
    qualifier_distribution: dict[str, int] = field(default_factory=dict)


class _QualifierGroup(BaseModel):
    """A single qualifier group with its name and keywords."""

    model_config = {"extra": "forbid"}
    name: str = Field(description="Short qualifier name (1-2 words, lowercase, e.g. 'growth', 'onboarding').")
    keywords: list[str] = Field(description="5-10 lowercase keywords that signal this specialization.")


class _QualifierVocabulary(BaseModel):
    """Generated qualifier vocabulary for a domain."""

    model_config = {"extra": "forbid"}
    groups: list[_QualifierGroup] = Field(description="3-6 qualifier groups covering the domain's specializations.")


async def generate_qualifier_vocabulary(
    provider: LLMProvider | None,
    domain_label: str,
    cluster_contexts: list[ClusterVocabContext],
    similarity_matrix: list[list[float | None]] | None,
    model: str,
) -> dict[str, list[str]]:
    """Generate a qualifier vocabulary from a domain's cluster structure.

    Calls Haiku to analyze enriched per-cluster context (label, member count,
    member intent labels, existing qualifier distribution) alongside a pairwise
    centroid similarity matrix, and produces keyword groups that capture the
    domain's specializations. Returns a dictionary mapping qualifier names to
    keyword lists (e.g., ``{"growth": ["metrics", "kpi", ...]}``) stored in
    domain node ``cluster_metadata["generated_qualifiers"]``.

    Args:
        provider: LLM provider (Haiku).  None = return empty dict.
        domain_label: The domain name (e.g., "saas").
        cluster_contexts: Per-cluster enriched context (label, member_count,
            intent_labels, qualifier_distribution). Order must match
            ``similarity_matrix`` rows/columns.
        similarity_matrix: Optional NxN pairwise cosine matrix of cluster
            centroids. None = no geometric context rendered.
        model: Model ID to use.

    Returns:
        Qualifier vocabulary dict, e.g. ``{"growth": ["metrics", "kpi", ...], ...}``.
        Empty dict on failure.
    """
    if not provider or len(cluster_contexts) < 2:
        return {}

    lines = []
    for i, ctx in enumerate(cluster_contexts):
        parts = [f'- C{i+1}: "{ctx.label}" ({ctx.member_count} members)']
        if ctx.intent_labels:
            parts.append(f"  Intents: {', '.join(ctx.intent_labels[:10])}")
        if ctx.qualifier_distribution:
            dist = ', '.join(
                f'{q}({c})' for q, c in sorted(
                    ctx.qualifier_distribution.items(), key=lambda x: -x[1]
                )[:5]
            )
            parts.append(f"  Existing qualifiers: {dist}")
        lines.append('\n'.join(parts))
    cluster_block = '\n'.join(lines)

    # Render similarity matrix if available (>= 2 clusters)
    matrix_block = ""
    if similarity_matrix and len(similarity_matrix) >= 2:
        matrix_lines = ["Cluster similarity (cosine):"]
        for i in range(len(similarity_matrix)):
            for j in range(i + 1, len(similarity_matrix)):
                cell = similarity_matrix[i][j]
                if cell is None:
                    # Unknown geometry: one side lacked a centroid. Skip to
                    # avoid misleading Haiku into treating unknown as distinct.
                    continue
                try:
                    sim = float(cell)
                    if math.isnan(sim) or math.isinf(sim):
                        continue
                except (TypeError, ValueError, IndexError):
                    continue
                hint = " (very similar)" if sim > _VOCAB_SIM_HIGH else " (distinct)" if sim < _VOCAB_SIM_LOW else ""
                matrix_lines.append(f"  C{i+1}↔C{j+1}: {sim:.2f}{hint}")
        matrix_block = '\n'.join(matrix_lines)

    try:
        result = await call_provider_with_retry(
            provider,
            model=model,
            system_prompt=(
                "You are a taxonomy analyst. Given clusters within a domain with their "
                "member intents, existing qualifier signals, and pairwise embedding similarity, "
                "identify 3-6 thematic specializations. For each, produce a short name "
                "(1-2 lowercase words) and 5-10 lowercase keywords that signal that "
                "specialization in a user's prompt. Keywords should DISCRIMINATE between "
                "groups — choose words that appear in one specialization but not others. "
                f"Use the similarity matrix to guide grouping: clusters with cosine > {_VOCAB_SIM_HIGH} "
                "should typically belong to the same group. "
                "Do not include the domain name itself as a keyword."
            ),
            user_message=(
                f"Domain: {domain_label}\n\n"
                f"Clusters:\n{cluster_block}\n\n"
                + (f"{matrix_block}\n\n" if matrix_block else "")
                + "Generate qualifier groups for this domain's specializations."
            ),
            output_format=_QualifierVocabulary,
        )

        vocab: dict[str, list[str]] = {}
        for group in result.groups:
            name = group.name.strip().lower().replace(" ", "-")[:20]
            keywords = [kw.strip().lower() for kw in group.keywords if kw.strip()]
            if name and len(keywords) >= 3:
                vocab[name] = keywords

        if vocab:
            logger.info(
                "Generated qualifier vocabulary for '%s': %d groups (%s)",
                domain_label,
                len(vocab),
                ", ".join(f"{k}({len(v)}kw)" for k, v in vocab.items()),
            )
        return vocab
    except Exception as exc:
        logger.warning(
            "Qualifier vocabulary generation failed for '%s' (non-fatal): %s",
            domain_label, exc,
        )
        return {}
