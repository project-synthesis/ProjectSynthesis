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
    *,
    domain_signal_keywords: list[tuple[str, float]] | None = None,
    existing_vocab_groups: list[str] | None = None,
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
        domain_signal_keywords: Top-K corpus TF-IDF terms the cascade's
            source-3 path is currently using (``signal_keywords`` in the
            domain node's ``cluster_metadata``).  Surfaces latent themes
            that the previous vocab missed — e.g., the live backend
            domain shows ``audit`` as a TF-IDF top term not covered by
            any existing Haiku group.  Without this hint the cascade
            keeps recording ``audit`` via source 3 every cycle while
            successive vocabs ignore it.
        existing_vocab_groups: Names of the previous vocab's groups,
            so Haiku can prefer continuity (keep stable group names
            when the underlying clusters haven't drifted) and only
            introduce new groups when warranted by new TF-IDF terms or
            shifted cluster geometry.

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

    # Render TF-IDF orphan terms — keep only ones that don't appear as
    # substrings in any existing vocab group name (those are the "latent
    # themes" the cascade is recording via source 3 but no group covers).
    orphan_block = ""
    if domain_signal_keywords:
        existing_lower = [g.lower() for g in (existing_vocab_groups or [])]
        orphans: list[tuple[str, float]] = []
        for kw, weight in domain_signal_keywords:
            kw_l = (kw or "").strip().lower()
            if not kw_l or len(kw_l) < 3 or weight < 0.5:
                continue
            # Skip terms that look like noise: pure-digit fragments, single
            # tokens like "py" / "app" already contained inside an existing
            # group name, or the domain label itself.
            if kw_l == domain_label.lower():
                continue
            covered = any(kw_l in g or g in kw_l for g in existing_lower)
            if covered:
                continue
            orphans.append((kw_l, weight))
        if orphans:
            top = orphans[:8]
            orphan_lines = ", ".join(f"{kw}({w:.2f})" for kw, w in top)
            orphan_block = (
                f"Recurring corpus terms NOT covered by any existing group "
                f"(weight 0-1, normalized): {orphan_lines}"
            )

    existing_block = ""
    if existing_vocab_groups:
        existing_block = (
            f"Existing vocab groups (prefer continuity unless geometry has "
            f"shifted): {', '.join(existing_vocab_groups)}"
        )

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
                "If the user message lists 'Recurring corpus terms NOT covered by any existing "
                "group', evaluate whether they represent a real latent specialization "
                "(in which case introduce or rename a group to absorb them) or merely lexical "
                "noise (in which case ignore them). "
                "If the user message lists existing vocab groups, prefer keeping those names "
                "stable when cluster geometry hasn't materially shifted — drift in group naming "
                "across regenerations breaks downstream consistency-based emergence detection. "
                "Do not include the domain name itself as a keyword."
            ),
            user_message=(
                f"Domain: {domain_label}\n\n"
                f"Clusters:\n{cluster_block}\n\n"
                + (f"{matrix_block}\n\n" if matrix_block else "")
                + (f"{existing_block}\n\n" if existing_block else "")
                + (f"{orphan_block}\n\n" if orphan_block else "")
                + "Generate qualifier groups for this domain's specializations."
            ),
            output_format=_QualifierVocabulary,
        )

        from app.utils.text_cleanup import normalize_sub_domain_label

        vocab: dict[str, list[str]] = {}
        for group in result.groups:
            # Use the shared canonicalizer so vocab group names follow the
            # same rule as eventual sub-domain labels (no underscore/space
            # drift, word-boundary truncation, no mid-word slice). Limit
            # raised from 20 → 30 to align with engine.py:_propose_sub_domains
            # — short names like "tracing" still fit, longer ones like
            # "pattern-instrumentation" no longer truncate mid-word.
            name = normalize_sub_domain_label(group.name)
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
