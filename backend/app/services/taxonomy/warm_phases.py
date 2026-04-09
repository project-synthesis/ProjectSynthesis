"""Warm-path phase implementations — individual lifecycle phases for the
Evolutionary Taxonomy Engine warm path.

Each phase function receives the engine instance via dependency injection
(``engine`` parameter typed via ``TYPE_CHECKING`` to avoid circular imports)
and a fresh ``AsyncSession``.  Phases are independently callable and load
their own data from the database.

Phase order:
  0. reconcile  — member count, coherence, score, domain node repair, zombie cleanup
  1. split_emerge — leaf splits (HDBSCAN + k-means fallback), family splits, emerge
  2. merge — global best-pair merge + same-domain label/embedding merge
  3. retire — archive idle nodes with 0 members
  4. refresh — stale label and meta-pattern re-extraction
  5. discover — domain discovery, candidate detection, risk monitoring, tree repair
  6. audit — per-node separation, Q_system, snapshot, deadlock breaker, event

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    MetaPattern,
    Optimization,
    PromptCluster,
)
from app.services.taxonomy._constants import (
    CANDIDATE_COHERENCE_FLOOR,
    DISSOLVE_COHERENCE_CEILING,
    DISSOLVE_MAX_MEMBERS,
    DISSOLVE_MIN_AGE_HOURS,
    EXCLUDED_STRUCTURAL_STATES,
    FORCED_SPLIT_COHERENCE_FLOOR,
    FORCED_SPLIT_MIN_MEMBERS,
    LABEL_COHERENCE_SPLIT_SIGNAL,
    MEGA_CLUSTER_MEMBER_FLOOR,
    MERGE_BACK_GRACE_MINUTES,
    SPLIT_COHERENCE_EXEMPT,
    SPLIT_COHERENCE_FLOOR,
    SPLIT_CONTENT_HASH_MAX_RETRIES,
    SPLIT_MIN_MEMBERS,
    _utcnow,
)
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.clustering import (
    batch_cluster,
    blend_embeddings,
    compute_pairwise_coherence,
    cosine_similarity,
)
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.family_ops import (
    _ExtractedPatterns,
    adaptive_merge_threshold,
    build_breadcrumb,
    merge_meta_pattern,
    score_to_centroid_weight,
)
from app.utils.text_cleanup import parse_domain

if TYPE_CHECKING:
    from app.services.taxonomy.engine import TaxonomyEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain-level split block recording
# ---------------------------------------------------------------------------


async def _record_domain_split_block(
    db: AsyncSession,
    cluster_domain: str,
    content_hash: str,
    label: str,
    source: str,
) -> None:
    """Record a content hash in the parent domain node's split_blocked_hashes ring buffer.

    This persists split failure identity at the domain level, surviving across
    cluster ID changes (the cross-ID identity anchor for Groundhog Day prevention).
    """
    from app.services.taxonomy._constants import (
        DOMAIN_SPLIT_HASH_MAX_ENTRIES,
        DOMAIN_SPLIT_HASH_TTL_HOURS,
    )

    if not cluster_domain:
        return

    dn_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == cluster_domain,
        )
    )
    domain_node = dn_q.scalar_one_or_none()
    if not domain_node:
        return

    meta = read_meta(domain_node.cluster_metadata)
    blocked: list[dict[str, str]] = list(meta.get("split_blocked_hashes", []))

    # Deduplicate: don't add the same hash twice
    if any(e.get("hash") == content_hash for e in blocked):
        return

    # Add new entry
    blocked.append({
        "hash": content_hash,
        "ts": _utcnow().isoformat(),
        "label": label,
    })

    # Prune expired + enforce max entries
    ttl_cutoff = (
        _utcnow() - timedelta(hours=DOMAIN_SPLIT_HASH_TTL_HOURS)
    ).isoformat()
    blocked = [e for e in blocked if e.get("ts", "") >= ttl_cutoff]
    if len(blocked) > DOMAIN_SPLIT_HASH_MAX_ENTRIES:
        blocked = blocked[-DOMAIN_SPLIT_HASH_MAX_ENTRIES:]

    domain_node.cluster_metadata = write_meta(
        domain_node.cluster_metadata,
        split_blocked_hashes=blocked,
    )

    try:
        get_event_logger().log_decision(
            path="warm", op="split", decision="domain_hash_recorded",
            context={
                "hash": content_hash,
                "domain": cluster_domain,
                "label": label,
                "source": source,
                "buffer_size": len(blocked),
            },
        )
    except RuntimeError:
        pass

    logger.info(
        "Recorded domain split block: domain='%s' hash=%s label='%s' source=%s",
        cluster_domain, content_hash, label, source,
    )


async def _detect_merge_back(
    db: AsyncSession,
    loser_merge_until: str,
    merged: "PromptCluster",
    loser_id: str,
) -> None:
    """If loser had recent merge protection, record the winner's content hash at domain level.

    Evidence of a futile split: child created by split, protection expired, child merged back.
    Called AFTER attempt_merge() succeeds with loser metadata read BEFORE the merge.
    """
    if not loser_merge_until:
        return

    try:
        _prot_until = datetime.fromisoformat(loser_merge_until)
        _now_mb = _utcnow()
        _grace = timedelta(minutes=MERGE_BACK_GRACE_MINUTES)
        if _now_mb > _prot_until + _grace:
            return  # protection expired too long ago — not a merge-back

        # Compute winner's content hash for domain block
        _winner_opts_q = await db.execute(
            select(Optimization.id)
            .where(Optimization.cluster_id == merged.id)
            .order_by(Optimization.id)
        )
        _winner_opt_ids = sorted([r[0] for r in _winner_opts_q.all()])
        _winner_hash = hashlib.sha256(
            json.dumps(_winner_opt_ids).encode()
        ).hexdigest()[:16]
        await _record_domain_split_block(
            db, merged.domain or "general",
            _winner_hash, merged.label or "?",
            source="merge_back_detected",
        )
        try:
            get_event_logger().log_decision(
                path="warm", op="merge", decision="merge_back_detected",
                cluster_id=merged.id,
                context={
                    "loser_id": loser_id,
                    "winner_id": merged.id,
                    "domain": merged.domain,
                    "hash_recorded": _winner_hash,
                },
            )
        except RuntimeError:
            pass
    except Exception as _mb_exc:
        logger.warning("Merge-back detection failed (non-fatal): %s", _mb_exc)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PhaseResult:
    """Result from a lifecycle phase that performs speculative mutations."""

    phase: str
    q_before: float
    q_after: float
    accepted: bool
    ops_attempted: int = 0
    ops_accepted: int = 0
    operations: list[dict] = field(default_factory=list)
    embedding_index_mutations: int = 0
    # Cluster IDs that had split attempts (populated by phase_split_emerge).
    # Used by warm_path to persist split_failures metadata outside the
    # speculative transaction on Q-gate rejection (prevents Groundhog Day loop).
    split_attempted_ids: list[str] = field(default_factory=list)
    # Maps cluster_id → content hash of sorted member opt_ids.
    # Persisted outside the speculative transaction so the content-hash
    # loop detector can compare future split attempts against prior ones.
    split_content_hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class ReconcileResult:
    """Result from Phase 0 — reconciliation."""

    member_counts_fixed: int = 0
    coherence_updated: int = 0
    scores_reconciled: int = 0
    zombies_archived: int = 0
    leaked_patterns_cleaned: int = 0
    outliers_ejected: int = 0


@dataclass
class RefreshResult:
    """Result from Phase 4 — stale label/pattern refresh."""

    clusters_refreshed: int = 0


@dataclass
class DiscoverResult:
    """Result from Phase 5 — domain discovery."""

    domains_created: int = 0
    candidates_detected: int = 0


@dataclass
class AuditResult:
    """Result from Phase 6 — audit, snapshot, and deadlock breaker."""

    snapshot_id: str = "no-snapshot"
    q_final: float | None = None
    deadlock_breaker_used: bool = False
    deadlock_breaker_phase: str | None = None


# ---------------------------------------------------------------------------
# Phase 0.5 helpers — Candidate lifecycle
# ---------------------------------------------------------------------------


async def _reassign_to_active(
    db: AsyncSession,
    opt_ids: list[str],
    opt_embeddings: list[np.ndarray],
    exclude_cluster_ids: set[str] | None = None,
) -> list[dict]:
    """Reassign optimizations to the nearest active/mature/template cluster.

    Domain-aware: prefers same-domain targets to prevent dissolution cascades
    where cross-domain reassignment creates more incoherent clusters that
    trigger further dissolutions.  Falls back to cross-domain only when no
    same-domain target has cosine >= ``_CROSS_DOMAIN_FALLBACK_FLOOR``.

    When reassigning cross-domain, updates ``opt.domain`` to match the
    target cluster's domain so the Optimization row stays consistent.

    Only targets non-candidate, non-archived, non-domain clusters so that
    rejected candidate members always land in a stable parent cluster.

    Args:
        db: Active database session.
        opt_ids: Optimization primary-key IDs to reassign.
        opt_embeddings: Corresponding unit-norm embeddings (same order).
        exclude_cluster_ids: Cluster IDs to exclude from targets (e.g., the
            dissolving cluster itself).

    Returns:
        List of {cluster_id, cluster_label, count} dicts summarizing
        where members were reassigned — used in candidate_rejected event.
    """
    if not opt_ids:
        return []

    # Minimum same-domain cosine before allowing cross-domain fallback.
    # Below this, the member has no viable home in its own domain and
    # cross-domain placement is the lesser evil.
    cross_domain_fallback_floor = 0.30

    # Load candidate target clusters (active/mature/template only).
    # SQL-level exclusion prevents the dissolving cluster from appearing
    # in results regardless of SQLAlchemy identity-map state.
    _target_query = select(PromptCluster).where(
        PromptCluster.state.in_(["active", "mature", "template"])
    )
    if exclude_cluster_ids:
        _target_query = _target_query.where(
            PromptCluster.id.notin_(list(exclude_cluster_ids))
        )
    targets_q = await db.execute(_target_query)
    target_clusters: list[PromptCluster] = list(targets_q.scalars().all())
    if not target_clusters:
        logger.warning("_reassign_to_active: no stable targets available")
        return []

    # Pre-decode target centroids and parse domains
    target_centroids: list[np.ndarray] = []
    valid_targets: list[PromptCluster] = []
    target_domains: list[str] = []  # parsed primary domain per target
    for tc in target_clusters:
        if tc.centroid_embedding:
            try:
                centroid = np.frombuffer(tc.centroid_embedding, dtype=np.float32).copy()
                target_centroids.append(centroid)
                valid_targets.append(tc)
                target_domains.append(parse_domain(tc.domain)[0])
            except (ValueError, TypeError) as _tc_exc:
                logger.warning(
                    "Corrupt centroid in reassignment targets, cluster='%s': %s",
                    tc.label, _tc_exc,
                )

    if not valid_targets:
        return []

    reassignment_counts: dict[str, dict] = {}

    for opt_id, emb in zip(opt_ids, opt_embeddings):
        # Load the optimization to read its domain
        try:
            opt = await db.get(Optimization, opt_id)
            if opt is None:
                continue
        except Exception as exc:
            logger.warning("_reassign_to_active: failed to load opt %s: %s", opt_id, exc)
            continue

        opt_primary_domain, _ = parse_domain(opt.domain)

        # Two-pass search: same-domain first, cross-domain fallback
        best_same: PromptCluster | None = None
        best_same_sim: float = -1.0
        best_any: PromptCluster | None = None
        best_any_sim: float = -1.0

        for tc, centroid, td in zip(valid_targets, target_centroids, target_domains):
            sim = cosine_similarity(emb, centroid)
            if sim > best_any_sim:
                best_any_sim = sim
                best_any = tc
            if td == opt_primary_domain and sim > best_same_sim:
                best_same_sim = sim
                best_same = tc

        # Prefer same-domain target.  Only fall back to cross-domain when
        # no same-domain target reaches the minimum cosine floor.
        if best_same is not None and best_same_sim >= cross_domain_fallback_floor:
            chosen = best_same
        elif best_any is not None:
            chosen = best_any
        else:
            logger.warning("_reassign_to_active: no target found for opt %s", opt_id)
            continue

        try:
            opt.cluster_id = chosen.id
            # Keep opt.domain consistent with the target cluster's domain.
            # Without this, cross-domain reassignment leaves stale domain
            # values that confuse downstream reconciliation and reporting.
            chosen_primary, _ = parse_domain(chosen.domain)
            if chosen_primary != opt_primary_domain and chosen.domain:
                opt.domain = chosen.domain
            chosen.member_count = (chosen.member_count or 0) + 1
            key = chosen.id
            if key not in reassignment_counts:
                reassignment_counts[key] = {
                    "cluster_id": chosen.id,
                    "cluster_label": chosen.label,
                    "count": 0,
                }
            reassignment_counts[key]["count"] += 1
        except Exception as exc:
            logger.warning("_reassign_to_active: failed for opt %s: %s", opt_id, exc)

    # Belt-and-suspenders: verify no assignment landed on an excluded cluster.
    # Catches edge cases where the SQL-level filter did not take effect.
    if exclude_cluster_ids:
        for key in list(reassignment_counts):
            if reassignment_counts[key]["cluster_id"] in exclude_cluster_ids:
                logger.error(
                    "_reassign_to_active: BUG — member assigned to excluded "
                    "cluster %s (label=%s). Removing from results.",
                    reassignment_counts[key]["cluster_id"],
                    reassignment_counts[key].get("cluster_label"),
                )
                del reassignment_counts[key]

    return list(reassignment_counts.values())


async def phase_evaluate_candidates(
    db: AsyncSession,
) -> dict:
    """Phase 0.5 — evaluate candidate clusters for promotion or rejection.

    For each cluster with ``state="candidate"``:
    - Zero members → archive immediately (candidate_rejected: zero_members)
    - Pairwise coherence >= CANDIDATE_COHERENCE_FLOOR → promote to "active"
    - Pairwise coherence < CANDIDATE_COHERENCE_FLOOR → reassign members to
      nearest active cluster, then archive (candidate_rejected: low_coherence)

    After evaluating all candidates, detect ``split_fully_reversed`` events:
    all candidates sharing the same ``parent_id`` were rejected.

    This phase is NOT Q-gated — it always commits.

    Returns:
        Dict with keys: promoted (int), rejected (int), splits_fully_reversed (int).
    """
    promoted = 0
    rejected = 0
    splits_fully_reversed = 0

    # Load all candidate clusters
    cands_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "candidate")
    )
    candidates: list[PromptCluster] = list(cands_q.scalars().all())

    if not candidates:
        return {"promoted": 0, "rejected": 0, "splits_fully_reversed": 0}

    # Pre-fetch member embeddings grouped by cluster_id in a single query
    cand_ids = [c.id for c in candidates]
    emb_q = await db.execute(
        select(Optimization.id, Optimization.cluster_id, Optimization.embedding)
        .where(
            Optimization.cluster_id.in_(cand_ids),
            Optimization.embedding.isnot(None),
        )
    )
    emb_by_cluster: dict[str, list[tuple[str, np.ndarray]]] = {}
    for opt_id, cid, emb_bytes in emb_q.all():
        if emb_bytes:
            try:
                emb = np.frombuffer(emb_bytes, dtype=np.float32).copy()
                emb_by_cluster.setdefault(cid, []).append((opt_id, emb))
            except (ValueError, TypeError) as _ce_exc:
                logger.warning(
                    "Corrupt embedding in candidate member loading, opt=%s: %s",
                    opt_id, _ce_exc,
                )

    # Track outcomes per parent_id for split_fully_reversed detection
    # Maps parent_id → {promoted: int, rejected: int}
    parent_outcomes: dict[str, dict[str, int]] = {}

    # Exclude ALL candidate IDs from reassignment targets — prevents
    # rejected members from being reassigned to a sibling candidate
    # that itself gets rejected later in this same evaluation loop.
    all_candidate_ids = {c.id for c in candidates}

    now = _utcnow()

    for candidate in candidates:
        members = emb_by_cluster.get(candidate.id, [])
        member_count = len(members)

        # Track outcome per parent
        if candidate.parent_id:
            if candidate.parent_id not in parent_outcomes:
                parent_outcomes[candidate.parent_id] = {"promoted": 0, "rejected": 0}

        # Compute time_as_candidate_ms for event logging.
        # Guard against naive/aware mismatch: models._utcnow() may return
        # timezone-aware datetimes while SQLAlchemy strips tzinfo on SQLite
        # round-trips. Use try/except for safety.
        time_as_candidate_ms: int | None = None
        if candidate.created_at:
            try:
                created = candidate.created_at
                # Normalise: strip tzinfo if present so subtraction works with
                # our naive `now`.
                if getattr(created, "tzinfo", None) is not None:
                    created = created.replace(tzinfo=None)
                delta = now - created
                time_as_candidate_ms = int(delta.total_seconds() * 1000)
            except (TypeError, AttributeError):
                pass

        # Case 1: zero members — archive immediately
        if member_count == 0:
            candidate.state = "archived"
            candidate.archived_at = now
            rejected += 1
            if candidate.parent_id:
                parent_outcomes[candidate.parent_id]["rejected"] += 1
                parent_outcomes[candidate.parent_id].setdefault("labels", []).append(candidate.label or "unknown")
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="candidate_rejected",
                    cluster_id=candidate.id,
                    context={
                        "cluster_label": candidate.label,
                        "reason": "zero_members",
                        "coherence": None,
                        "coherence_floor": CANDIDATE_COHERENCE_FLOOR,
                        "member_count": 0,
                        "members_reassigned_to": [],
                        "parent_id": candidate.parent_id,
                        "time_as_candidate_ms": time_as_candidate_ms,
                    },
                )
            except RuntimeError:
                pass
            continue

        # Compute pairwise coherence from member embeddings
        coherence: float | None = None
        embeddings = [emb for _, emb in members]
        try:
            if len(embeddings) >= 2:
                coherence = compute_pairwise_coherence(embeddings)
            else:
                # Single member — coherence is 1.0 by definition
                coherence = 1.0
        except Exception as coh_exc:
            logger.warning("Coherence computation failed for candidate '%s': %s", candidate.label, coh_exc)
            coherence = None

        # Case 2: coherence meets floor — promote to active
        if coherence is not None and coherence >= CANDIDATE_COHERENCE_FLOOR:
            candidate.state = "active"
            candidate.coherence = coherence
            promoted += 1
            if candidate.parent_id:
                parent_outcomes[candidate.parent_id]["promoted"] += 1
            logger.info(
                "Candidate '%s' promoted to active (coherence=%.3f)",
                candidate.label, coherence,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="candidate_promoted",
                    cluster_id=candidate.id,
                    context={
                        "cluster_label": candidate.label,
                        "coherence": round(coherence, 4),
                        "coherence_floor": CANDIDATE_COHERENCE_FLOOR,
                        "member_count": member_count,
                        "reason": "coherence_above_floor",
                        "parent_id": candidate.parent_id,
                        "time_as_candidate_ms": time_as_candidate_ms,
                    },
                )
            except RuntimeError:
                pass

        else:
            # Case 3: coherence below floor or None — reassign members and archive
            reason = "coherence_unavailable" if coherence is None else "coherence_below_floor"
            opt_ids = [oid for oid, _ in members]
            opt_embs = [emb for _, emb in members]
            reassignment_info = await _reassign_to_active(
                db, opt_ids, opt_embs, exclude_cluster_ids=all_candidate_ids,
            )

            candidate.state = "archived"
            candidate.archived_at = now
            candidate.member_count = 0
            rejected += 1
            if candidate.parent_id:
                parent_outcomes[candidate.parent_id]["rejected"] += 1
                parent_outcomes[candidate.parent_id].setdefault("labels", []).append(candidate.label or "unknown")
                parent_outcomes[candidate.parent_id].setdefault("reassigned", 0)
                parent_outcomes[candidate.parent_id]["reassigned"] += len(opt_ids)
            logger.info(
                "Candidate '%s' rejected (%s, coherence=%s < %.2f), reassigning %d members",
                candidate.label, reason,
                f"{coherence:.3f}" if coherence is not None else "None",
                CANDIDATE_COHERENCE_FLOOR, member_count,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="candidate_rejected",
                    cluster_id=candidate.id,
                    context={
                        "cluster_label": candidate.label,
                        "reason": reason,
                        "coherence": round(coherence, 4) if coherence is not None else None,
                        "coherence_floor": CANDIDATE_COHERENCE_FLOOR,
                        "member_count": member_count,
                        "members_reassigned_to": reassignment_info,
                        "parent_id": candidate.parent_id,
                        "time_as_candidate_ms": time_as_candidate_ms,
                    },
                )
            except RuntimeError:
                pass

    # Detect split_fully_reversed: all siblings from same parent were rejected
    for parent_id, outcomes in parent_outcomes.items():
        total = outcomes["promoted"] + outcomes["rejected"]
        if total > 0 and outcomes["promoted"] == 0:
            splits_fully_reversed += 1
            # Look up parent label for observability
            parent_label = "unknown"
            try:
                parent_node = await db.get(PromptCluster, parent_id)
                if parent_node:
                    parent_label = parent_node.label or "unknown"
            except Exception:
                pass
            logger.info(
                "Split fully reversed: all %d candidates from parent '%s' rejected",
                total, parent_label,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="candidate", decision="split_fully_reversed",
                    cluster_id=parent_id,
                    context={
                        "parent_id": parent_id,
                        "parent_label": parent_label,
                        "candidates_rejected": outcomes["rejected"],
                        "candidate_labels": outcomes.get("labels", []),
                        "total_members_reassigned": outcomes.get("reassigned", 0),
                    },
                )
            except RuntimeError:
                pass

    # Flush all pending ORM mutations to the DB so callers can rely on
    # consistent state after this function returns (without requiring a full
    # commit, matching the pattern used by phase_reconcile and phase_refresh).
    if promoted > 0 or rejected > 0:
        await db.flush()

    return {
        "promoted": promoted,
        "rejected": rejected,
        "splits_fully_reversed": splits_fully_reversed,
    }


# ---------------------------------------------------------------------------
# Outlier reconciliation helper (called at end of Phase 0)
# ---------------------------------------------------------------------------

# Coherence gate: only inspect clusters below this threshold
_OUTLIER_CLUSTER_COHERENCE_GATE = 0.45

# Minimum cosine to own centroid — members below this are outlier candidates
_OUTLIER_COSINE_FLOOR = 0.40

# Margin: target must be better by at least this much to justify ejection
_OUTLIER_REASSIGN_MARGIN = 0.10

# Max ejections per cluster per warm cycle (stability guard)
_OUTLIER_MAX_PER_CLUSTER = 5


async def _reconcile_outlier_members(
    db: AsyncSession,
    engine: "TaxonomyEngine",
) -> int:
    """Eject cross-domain outliers from clusters they don't belong in.

    Identifies members whose ``domain`` differs from their cluster's domain
    AND whose cosine to the cluster centroid is below ``_OUTLIER_COSINE_FLOOR``.

    For each outlier:
    1. Try to reassign to a same-domain cluster that provides a better fit.
    2. If no better same-domain cluster exists, create a singleton cluster
       in the member's correct domain (orphan liberation).

    This is the individual-member analog of dissolution — completing the
    reconciliation phase by checking member fit, not just cluster-level metrics.

    Returns:
        Number of members ejected.
    """
    # Load active clusters (source candidates: coherence < gate, 3+ members)
    active_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.in_(["active", "mature", "template"]),
        )
    )
    all_active = list(active_q.scalars().all())

    # Pre-decode centroids and domains for target search
    target_centroids: dict[str, np.ndarray] = {}
    target_domains: dict[str, str] = {}
    for c in all_active:
        if c.centroid_embedding:
            try:
                target_centroids[c.id] = np.frombuffer(
                    c.centroid_embedding, dtype=np.float32,
                ).copy()
                target_domains[c.id] = parse_domain(c.domain)[0]
            except (ValueError, TypeError) as _ot_exc:
                logger.warning(
                    "Corrupt centroid in outlier target loading, cluster='%s': %s",
                    c.label, _ot_exc,
                )

    # Only inspect clusters with enough members and low coherence
    source_candidates = [
        c for c in all_active
        if (c.member_count or 0) > 2
        and c.coherence is not None
        and c.coherence < _OUTLIER_CLUSTER_COHERENCE_GATE
    ]

    # Look up domain node IDs for parenting new singletons
    domain_node_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "domain")
    )
    domain_nodes: dict[str, PromptCluster] = {
        dn.label: dn for dn in domain_node_q.scalars().all()
    }

    total_ejected = 0

    for cluster in source_candidates:
        cluster_primary, _ = parse_domain(cluster.domain)
        cluster_centroid = target_centroids.get(cluster.id)
        if cluster_centroid is None:
            continue

        # Load members with their embeddings and domains
        member_q = await db.execute(
            select(
                Optimization.id,
                Optimization.embedding,
                Optimization.domain,
                Optimization.intent_label,
                Optimization.overall_score,
            ).where(
                Optimization.cluster_id == cluster.id,
                Optimization.embedding.isnot(None),
            )
        )
        members = member_q.all()

        ejected_this_cluster = 0
        for opt_id, emb_bytes, opt_domain, intent_label, overall_score in members:
            if ejected_this_cluster >= _OUTLIER_MAX_PER_CLUSTER:
                break

            opt_primary, _ = parse_domain(opt_domain)

            # Only eject members whose domain doesn't match the cluster
            if opt_primary == cluster_primary:
                continue

            try:
                emb = np.frombuffer(emb_bytes, dtype=np.float32).copy()
            except (ValueError, TypeError) as _oe_exc:
                logger.warning(
                    "Corrupt embedding in outlier reconciliation, opt=%s: %s",
                    opt_id, _oe_exc,
                )
                continue

            # Check cosine to current centroid
            cos_to_current = cosine_similarity(emb, cluster_centroid)
            if cos_to_current >= _OUTLIER_COSINE_FLOOR:
                # Close enough semantically — domain mismatch is borderline
                continue

            # Find best same-domain target
            best_target_id: str | None = None
            best_target_sim: float = -1.0
            for tid, tc_centroid in target_centroids.items():
                if tid == cluster.id:
                    continue
                if target_domains.get(tid) != opt_primary:
                    continue
                sim = cosine_similarity(emb, tc_centroid)
                if sim > best_target_sim:
                    best_target_sim = sim
                    best_target_id = tid

            # Decision: reassign to better cluster OR create singleton
            opt = await db.get(Optimization, opt_id)
            if opt is None:
                continue

            old_cluster_label = cluster.label
            target_label: str
            target_domain_str: str
            cos_to_target: float

            if (
                best_target_id is not None
                and best_target_sim >= cos_to_current + _OUTLIER_REASSIGN_MARGIN
            ):
                # Path A: better same-domain cluster exists → reassign
                target_cluster = next(
                    (c for c in all_active if c.id == best_target_id), None,
                )
                if target_cluster is None:
                    continue
                opt.cluster_id = best_target_id
                target_cluster.member_count = (target_cluster.member_count or 0) + 1
                target_label = target_cluster.label
                target_domain_str = target_cluster.domain or opt_domain
                cos_to_target = best_target_sim
            else:
                # Path B: no better cluster → create singleton in correct domain
                # This liberates orphans that were swept in by dissolution cascades
                parent_node = domain_nodes.get(opt_primary)
                new_singleton = PromptCluster(
                    label=intent_label or f"Singleton ({opt_primary})",
                    domain=opt_domain,
                    task_type=None,
                    parent_id=parent_node.id if parent_node else None,
                    centroid_embedding=emb.astype(np.float32).tobytes(),
                    member_count=1,
                    weighted_member_sum=score_to_centroid_weight(overall_score),
                    scored_count=1 if overall_score is not None else 0,
                    avg_score=overall_score,
                    coherence=1.0,
                    state="active",
                )
                db.add(new_singleton)
                await db.flush()  # populate ID
                opt.cluster_id = new_singleton.id
                target_label = new_singleton.label
                target_domain_str = opt_domain or "general"
                cos_to_target = 1.0  # singleton is the member itself

            cluster.member_count = max(0, (cluster.member_count or 1) - 1)
            ejected_this_cluster += 1
            total_ejected += 1

            try:
                get_event_logger().log_decision(
                    path="warm", op="reconcile", decision="outlier_ejected",
                    cluster_id=cluster.id,
                    context={
                        "opt_id": opt_id,
                        "opt_domain": opt_domain,
                        "source_cluster": old_cluster_label,
                        "source_domain": cluster.domain,
                        "target_cluster": target_label,
                        "target_domain": target_domain_str,
                        "cos_to_source": round(cos_to_current, 4),
                        "cos_to_target": round(cos_to_target, 4),
                        "created_singleton": best_target_id is None
                        or best_target_sim < cos_to_current + _OUTLIER_REASSIGN_MARGIN,
                    },
                )
            except RuntimeError:
                pass

    if total_ejected:
        await db.flush()
        logger.info(
            "Outlier reconciliation: ejected %d cross-domain members",
            total_ejected,
        )

    return total_ejected


# ---------------------------------------------------------------------------
# Phase 0 — Reconcile
# ---------------------------------------------------------------------------


async def phase_reconcile(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> ReconcileResult:
    """Reconcile member counts, coherence, scores, domain node repairs, and
    archive zombie clusters.

    Fix #10: queries nodes with ``state.notin_(EXCLUDED_STRUCTURAL_STATES)``
    instead of iterating over a stale ``active_nodes`` list.
    Fix #16: uses fresh query results from its own session.
    """
    result = ReconcileResult()

    # --- Member count + coherence reconciliation ---
    try:
        count_q = await db.execute(
            select(Optimization.cluster_id, func.count().label("ct"))
            .where(Optimization.cluster_id.isnot(None))
            .group_by(Optimization.cluster_id)
        )
        actual_counts = dict(count_q.all())

        # Batch-load all optimization embeddings + scores grouped by cluster_id.
        all_emb_q = await db.execute(
            select(
                Optimization.cluster_id,
                Optimization.embedding,
                Optimization.overall_score,
                Optimization.optimized_embedding,
            ).where(
                Optimization.cluster_id.isnot(None),
                Optimization.embedding.isnot(None),
            )
        )
        emb_by_cluster: dict[str, list[np.ndarray]] = {}
        score_by_cluster: dict[str, list[float]] = {}
        opt_emb_by_cluster: dict[str, list[np.ndarray]] = {}
        for cid, emb_bytes, opt_score, opt_emb_bytes in all_emb_q.all():
            if emb_bytes is not None:
                try:
                    emb_by_cluster.setdefault(cid, []).append(
                        np.frombuffer(emb_bytes, dtype=np.float32).copy()
                    )
                    score_by_cluster.setdefault(cid, []).append(
                        score_to_centroid_weight(opt_score)
                    )
                except (ValueError, TypeError):
                    pass
            if opt_emb_bytes is not None:
                try:
                    opt_emb_by_cluster.setdefault(cid, []).append(
                        np.frombuffer(opt_emb_bytes, dtype=np.float32).copy()
                    )
                except (ValueError, TypeError):
                    pass

        # Fix #10: query non-domain/non-archived nodes directly instead of
        # relying on a stale active_nodes list from a prior query.
        nodes_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
            )
        )
        live_nodes = list(nodes_q.scalars().all())

        for node in live_nodes:
            expected = actual_counts.get(node.id, 0)
            if node.member_count != expected:
                node.member_count = expected
                result.member_counts_fixed += 1

            # Always recompute coherence from actual member embeddings.
            if expected >= 2:
                member_embs = emb_by_cluster.get(node.id, [])
                if len(member_embs) >= 2:
                    node.coherence = compute_pairwise_coherence(member_embs)
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        coherence_member_count=expected,
                    )
                    result.coherence_updated += 1
            elif expected == 1:
                node.coherence = 1.0
            elif expected == 0:
                node.coherence = 0.0

            # Output coherence: pairwise cosine of optimized_embeddings.
            # A cluster with high raw coherence but low output coherence
            # produces divergent outputs from similar inputs — a split signal.
            opt_embs = opt_emb_by_cluster.get(node.id, [])
            if len(opt_embs) >= 2:
                output_coh = compute_pairwise_coherence(opt_embs)
                node.cluster_metadata = write_meta(
                    node.cluster_metadata, output_coherence=round(output_coh, 4),
                )
            elif len(opt_embs) == 1:
                node.cluster_metadata = write_meta(
                    node.cluster_metadata, output_coherence=1.0,
                )

        # Intent label coherence: supplementary split signal (Tier 5b)
        try:
            from app.services.taxonomy.quality import compute_intent_label_coherence

            label_q = await db.execute(
                select(
                    Optimization.cluster_id,
                    Optimization.intent_label,
                ).where(
                    Optimization.cluster_id.isnot(None),
                    Optimization.intent_label.isnot(None),
                )
            )
            labels_by_cluster: dict[str, list[str]] = {}
            for cid, il in label_q.all():
                labels_by_cluster.setdefault(cid, []).append(il)

            for node in live_nodes:
                labels = labels_by_cluster.get(node.id, [])
                if len(labels) >= 2:
                    ilc = compute_intent_label_coherence(labels)
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        intent_label_coherence=round(ilc, 4),
                    )
        except Exception as ilc_exc:
            logger.debug("Intent label coherence computation failed (non-fatal): %s", ilc_exc)

        # Reconcile avg_score and scored_count from actual member data.
        score_q = await db.execute(
            select(
                Optimization.cluster_id,
                func.avg(Optimization.overall_score),
                func.count(Optimization.overall_score),
            ).where(
                Optimization.cluster_id.isnot(None),
                Optimization.overall_score.isnot(None),
            ).group_by(Optimization.cluster_id)
        )
        score_map: dict[str, tuple[float, int]] = {
            row[0]: (round(row[1], 2), row[2])
            for row in score_q.all()
        }
        for node in live_nodes:
            avg, scored = score_map.get(node.id, (None, 0))
            if node.avg_score != avg or (node.scored_count or 0) != scored:
                node.avg_score = avg
                node.scored_count = scored
                result.scores_reconciled += 1

        # Recompute weighted_member_sum and centroid from member data.
        # The hot-path running mean can drift; this corrects from ground truth.
        # Uses score_to_centroid_weight() — the same power-law formula as the
        # hot-path assignment — so reconciliation preserves centroid semantics.
        for node in live_nodes:
            member_embs = emb_by_cluster.get(node.id, [])
            member_scores = score_by_cluster.get(node.id, [])

            # Recompute true weighted_member_sum from per-member scores
            if member_scores:
                node.weighted_member_sum = sum(member_scores)

            # Score-weighted centroid recomputation
            if len(member_embs) >= 2 and len(member_scores) == len(member_embs):
                stacked = np.stack(member_embs, axis=0)
                weights = np.array(member_scores, dtype=np.float32).reshape(-1, 1)
                recomputed = (stacked * weights).sum(axis=0) / weights.sum()
                recomputed = recomputed.astype(np.float32)
                c_norm = np.linalg.norm(recomputed)
                if c_norm > 1e-9:
                    node.centroid_embedding = (recomputed / c_norm).tobytes()
            elif len(member_embs) >= 2:
                # Fallback: no per-member scores, use unweighted mean
                stacked = np.stack(member_embs, axis=0)
                recomputed = np.mean(stacked, axis=0).astype(np.float32)
                c_norm = np.linalg.norm(recomputed)
                if c_norm > 1e-9:
                    node.centroid_embedding = (recomputed / c_norm).tobytes()

        # Reconcile domain node member_counts and parent_id links.
        domain_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        for domain_node in domain_q.scalars().all():
            # Domain nodes may be parented to another domain node (sub-domain)
            # or a project node (ADR-005 hierarchy). Only clear stale
            # parent_id references that point to non-structural parents.
            if domain_node.parent_id is not None:
                parent_is_structural = (await db.execute(
                    select(func.count()).where(
                        PromptCluster.id == domain_node.parent_id,
                        PromptCluster.state.in_(["domain", "project"]),
                    )
                )).scalar() or 0
                if parent_is_structural == 0:
                    # Stale reference — parent is not a domain or project node
                    logger.info(
                        "Clearing stale parent_id on domain '%s' (was %s)",
                        domain_node.label, domain_node.parent_id,
                    )
                    domain_node.parent_id = None
                    result.member_counts_fixed += 1

            # Count children by domain field (robust to broken parent_id)
            child_count = (await db.execute(
                select(func.count()).where(
                    PromptCluster.domain == domain_node.label,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )).scalar() or 0
            if domain_node.member_count != child_count:
                domain_node.member_count = child_count
                result.member_counts_fixed += 1

        # Reconcile domain node member_count (child cluster count by parent_id)
        domain_q2 = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        for domain_node in domain_q2.scalars():
            child_count_q = await db.execute(
                select(func.count()).where(
                    PromptCluster.parent_id == domain_node.id,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )
            actual_count = child_count_q.scalar() or 0
            if domain_node.member_count != actual_count:
                domain_node.member_count = actual_count
                result.member_counts_fixed += 1

            # Repair self-referencing parent_id links on children.
            self_ref_q = await db.execute(
                select(PromptCluster).where(
                    PromptCluster.domain == domain_node.label,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                    PromptCluster.id == PromptCluster.parent_id,
                )
            )
            for child in self_ref_q.scalars().all():
                child.parent_id = domain_node.id
                result.member_counts_fixed += 1

            # Fix domain nodes missing UMAP coordinates
            if (domain_node.umap_x is None
                    or domain_node.umap_y is None
                    or domain_node.umap_z is None):
                await engine._set_domain_umap_from_children(db, domain_node)

        if (result.member_counts_fixed
                or result.coherence_updated
                or result.scores_reconciled):
            logger.info(
                "Reconciled %d member_counts, %d coherence, %d scores",
                result.member_counts_fixed,
                result.coherence_updated,
                result.scores_reconciled,
            )
            await db.flush()
            try:
                get_event_logger().log_decision(
                    path="warm", op="reconcile", decision="repaired",
                    context={
                        "member_counts_fixed": result.member_counts_fixed,
                        "coherence_updated": result.coherence_updated,
                        "scores_reconciled": result.scores_reconciled,
                    },
                )
            except RuntimeError:
                pass
    except Exception as recon_exc:
        logger.warning("Reconciliation failed (non-fatal): %s", recon_exc)

    # --- Zombie cluster cleanup ---
    try:
        # Fix #10: re-query non-domain/non-archived nodes for zombie check
        # instead of iterating over a stale active_nodes list.
        zombie_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        zombie_candidates = list(zombie_q.scalars().all())
        zombie_ids: list[str] = []

        for node in zombie_candidates:
            if (node.member_count or 0) == 0:
                # Verify no optimizations still reference this cluster.
                actual_refs = (await db.execute(
                    select(func.count()).where(
                        Optimization.cluster_id == node.id,
                    )
                )).scalar() or 0
                if actual_refs > 0:
                    node.member_count = actual_refs
                    logger.info(
                        "Zombie guard: '%s' has %d optimization refs "
                        "-- correcting member_count, not archiving",
                        node.label, actual_refs,
                    )
                    continue

                # Clear ALL stale data.
                if node.usage_count and node.usage_count > 0:
                    logger.info(
                        "Clearing stale usage_count=%d on 0-member cluster '%s'",
                        node.usage_count, node.label,
                    )
                node.usage_count = 0
                node.avg_score = None
                node.scored_count = 0
                node.state = "archived"
                node.archived_at = _utcnow()
                result.zombies_archived += 1
                zombie_ids.append(node.id)
                await engine._embedding_index.remove(node.id)
                await engine._transformation_index.remove(node.id)
                await engine._optimized_index.remove(node.id)

        if zombie_ids:
            try:
                get_event_logger().log_decision(
                    path="warm", op="reconcile", decision="zombies_archived",
                    context={"count": len(zombie_ids), "node_ids": zombie_ids},
                )
            except RuntimeError:
                pass

        if result.zombies_archived:
            logger.info(
                "Archived %d zombie clusters (0 members)",
                result.zombies_archived,
            )
            await db.flush()
    except Exception as zombie_exc:
        logger.warning("Zombie cleanup failed (non-fatal): %s", zombie_exc)

    # --- Prune stale archived clusters ---
    try:
        # Delete archived nodes older than 24h with no optimization or pattern
        # references. Prevents dead-weight accumulation from repeated
        # split/merge/reform cycles.
        prune_cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).replace(tzinfo=None)

        stale_archived_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "archived",
                PromptCluster.member_count == 0,
                PromptCluster.archived_at.isnot(None),
                PromptCluster.archived_at < prune_cutoff,
            )
        )
        stale_archived = list(stale_archived_q.scalars().all())

        pruned = 0
        for node in stale_archived:
            # Check no optimizations reference this cluster.
            opt_ref = await db.execute(
                select(func.count()).select_from(Optimization).where(
                    Optimization.cluster_id == node.id
                )
            )
            if (opt_ref.scalar() or 0) > 0:
                continue

            # Check no meta-patterns reference this cluster.
            pat_ref = await db.execute(
                select(func.count()).select_from(MetaPattern).where(
                    MetaPattern.cluster_id == node.id
                )
            )
            if (pat_ref.scalar() or 0) > 0:
                continue

            await db.delete(node)
            pruned += 1

        if pruned:
            logger.info(
                "Pruned %d stale archived clusters (>24h old, no references)",
                pruned,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="reconcile", decision="stale_pruned",
                    context={"count": pruned},
                )
            except RuntimeError:
                pass
            await db.flush()
    except Exception as prune_exc:
        logger.warning("Stale cluster pruning failed (non-fatal): %s", prune_exc)

    # --- Leaked meta-pattern cleanup ---
    # Delete MetaPatterns belonging to archived clusters.  These accumulate
    # when dissolution archives a cluster without inline cleanup (now fixed),
    # or from historical leaks.  Capped per cycle for SQLite single-writer.
    _leaked_pattern_cap = 200  # max deletions per warm cycle
    try:
        archived_ids_sq = select(PromptCluster.id).where(
            PromptCluster.state == "archived"
        )
        leaked_q = await db.execute(
            select(MetaPattern).where(
                MetaPattern.cluster_id.in_(archived_ids_sq)
            ).limit(_leaked_pattern_cap)
        )
        leaked_patterns = list(leaked_q.scalars().all())
        if leaked_patterns:
            for mp in leaked_patterns:
                await db.delete(mp)
            await db.flush()
            result.leaked_patterns_cleaned = len(leaked_patterns)
            logger.info(
                "Cleaned %d leaked meta-patterns from archived clusters",
                len(leaked_patterns),
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="reconcile",
                    decision="leaked_patterns_cleaned",
                    context={"count": len(leaked_patterns)},
                )
            except RuntimeError:
                pass
    except Exception as leak_exc:
        logger.warning("Leaked pattern cleanup failed (non-fatal): %s", leak_exc)

    # --- Semi-orphan optimization repair ---
    # Reassign optimizations pointing to archived/missing clusters to the
    # nearest active cluster.  backfill_orphans() handles this at startup,
    # but semi-orphans accumulate between restarts from split/merge/retire.
    try:
        from sqlalchemy import or_

        active_ids_sq = select(PromptCluster.id).where(
            PromptCluster.state.in_(["active", "candidate", "mature", "template"])
        )
        semi_orphan_q = await db.execute(
            select(Optimization).where(
                Optimization.status == "completed",
                Optimization.embedding.isnot(None),
                or_(
                    Optimization.cluster_id.is_(None),
                    ~Optimization.cluster_id.in_(active_ids_sq),
                ),
            ).limit(50)  # cap per cycle to avoid blocking warm path
        )
        semi_orphans = list(semi_orphan_q.scalars().all())
        if semi_orphans:
            repaired = 0
            for orphan in semi_orphans:
                try:
                    emb = np.frombuffer(orphan.embedding, dtype=np.float32)
                    matches = engine._embedding_index.search(emb, k=1, threshold=0.25)
                    if matches:
                        new_cid, _sim = matches[0]
                        orphan.cluster_id = new_cid
                        repaired += 1
                except (ValueError, TypeError) as _so_exc:
                    logger.warning(
                        "Corrupt embedding in semi-orphan repair, opt=%s: %s",
                        orphan.id, _so_exc,
                    )
                    continue
            if repaired:
                await db.flush()
                logger.info(
                    "Repaired %d semi-orphan optimizations (of %d found)",
                    repaired, len(semi_orphans),
                )
                try:
                    get_event_logger().log_decision(
                        path="warm", op="reconcile",
                        decision="semi_orphans_repaired",
                        context={
                            "repaired": repaired,
                            "found": len(semi_orphans),
                        },
                    )
                except RuntimeError:
                    pass
    except Exception as orphan_exc:
        logger.warning("Semi-orphan repair failed (non-fatal): %s", orphan_exc)

    # --- Cross-domain outlier reconciliation ---
    # Eject members whose domain differs from their cluster's domain.
    # This cleans up the primary source of junk-drawer clusters:
    # _reassign_to_active() historically ignored domain, so dissolution
    # cascades sprayed members cross-domain.  Now that _reassign_to_active()
    # is domain-aware, this pass cleans up the existing mess.
    #
    # ORDERING: Must run BEFORE OptimizationPattern repair so that OP repair
    # sees the updated cluster_id values and can migrate/backfill correctly.
    try:
        result.outliers_ejected = await _reconcile_outlier_members(db, engine)
    except Exception as outlier_exc:
        logger.warning(
            "Outlier reconciliation failed (non-fatal): %s", outlier_exc,
        )

    # --- Stale OptimizationPattern repair ---
    # Migrate join records pointing to archived/missing clusters to the
    # optimization's current cluster_id. Previously this just DELETED stale
    # records, causing prompts to vanish from cluster detail views.
    try:

        from app.models import OptimizationPattern

        active_ids_sq2 = select(PromptCluster.id).where(
            PromptCluster.state.in_(["active", "candidate", "mature", "template", "domain"])
        )
        # Find stale OP records (pointing to dead clusters)
        stale_ops = (await db.execute(
            select(OptimizationPattern).where(
                OptimizationPattern.relationship == "source",
                ~OptimizationPattern.cluster_id.in_(active_ids_sq2),
            )
        )).scalars().all()

        if stale_ops:
            migrated = 0
            deleted = 0
            for op in stale_ops:
                # Look up the optimization's CURRENT cluster_id
                opt_row = (await db.execute(
                    select(Optimization.cluster_id).where(
                        Optimization.id == op.optimization_id,
                    )
                )).scalar_one_or_none()
                if opt_row and opt_row in {c.id for c in (await db.execute(
                    select(PromptCluster).where(
                        PromptCluster.id == opt_row,
                        PromptCluster.state.notin_(["archived"]),  # intentional: only archived, not structural
                    )
                )).scalars().all()}:
                    # Migrate to current cluster
                    op.cluster_id = opt_row
                    migrated += 1
                else:
                    # Optimization has no valid cluster — delete the orphan
                    await db.delete(op)
                    deleted += 1
            await db.flush()
            logger.info(
                "OptimizationPattern repair: %d migrated, %d deleted",
                migrated, deleted,
            )

        # Also backfill: create source records for optimizations that have
        # cluster_id but no OP record (can happen if hot-path crashed or
        # batch_taxonomy_assign partially failed).
        opts_without_op = (await db.execute(
            select(Optimization.id, Optimization.cluster_id).where(
                Optimization.status == "completed",
                Optimization.cluster_id.isnot(None),
                ~Optimization.id.in_(
                    select(OptimizationPattern.optimization_id).where(
                        OptimizationPattern.relationship == "source",
                    )
                ),
            )
        )).all()
        if opts_without_op:
            for opt_id, cluster_id in opts_without_op:
                db.add(OptimizationPattern(
                    optimization_id=opt_id,
                    cluster_id=cluster_id,
                    relationship="source",
                ))
            await db.flush()
            logger.info(
                "OptimizationPattern backfill: created %d missing source records",
                len(opts_without_op),
            )
    except Exception as stale_op_exc:
        logger.warning(
            "OptimizationPattern repair failed (non-fatal): %s",
            stale_op_exc,
        )

    return result


# ---------------------------------------------------------------------------
# Phase 1 — Split + Emerge
# ---------------------------------------------------------------------------


async def phase_split_emerge(
    engine: TaxonomyEngine,
    db: AsyncSession,
    split_protected_ids: set[str],
    dirty_ids: set[str] | None = None,  # ADR-005: None = process all
) -> PhaseResult:
    """Leaf splits (HDBSCAN + k-means fallback), family-based splits, and
    emerge from orphan families.

    Fix #7: exclude domain/archived from emerge query.
    Fix #9: increment ``ops_accepted`` for successful leaf splits.
    Fix #11: use pre-fetched ``_split_emb_cache`` for noise reassignment
        instead of per-noise-point DB queries.
    Fix #12: replace manual cosine with ``cosine_similarity()`` from
        clustering.py at noise reassignment.
    """
    ops_attempted = 0
    ops_accepted = 0
    operations_log: list[dict] = []
    split_content_hashes: dict[str, str] = {}
    embedding_index_mutations = 0
    split_attempted_ids: list[str] = []

    # Load active nodes for lifecycle operations.
    # Q_before/Q_after are computed by the orchestrator (_run_speculative_phase),
    # not here — phases focus on mutations, orchestrator handles quality gating.
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    active_nodes = list(active_q.scalars().all())

    # --- Priority 1: Split ---
    # Pre-fetch all optimization embeddings for split candidates in a
    # single batch query.
    # Include both normal split candidates (≥ SPLIT_MIN_MEMBERS) and forced
    # split candidates (≥ FORCED_SPLIT_MIN_MEMBERS with very low coherence).
    # Without this, forced split candidates have no cached embeddings and
    # coherence recomputation always yields 1.0 (dead code).
    _split_candidate_ids = [
        n.id for n in active_nodes
        if (n.member_count or 0) >= FORCED_SPLIT_MIN_MEMBERS
    ]
    # Cache: (opt_id, raw_bytes, optimized_bytes | None, transformation_bytes | None)
    _split_emb_cache: dict[str, list[tuple[str, bytes, bytes | None, bytes | None]]] = {}
    if _split_candidate_ids:
        _split_emb_q = await db.execute(
            select(
                Optimization.id,
                Optimization.cluster_id,
                Optimization.embedding,
                Optimization.optimized_embedding,
                Optimization.transformation_embedding,
            ).where(
                Optimization.cluster_id.in_(_split_candidate_ids),
                Optimization.embedding.isnot(None),
            )
        )
        for opt_id, cid, emb_bytes, opt_bytes, trans_bytes in _split_emb_q.all():
            if emb_bytes is not None:
                _split_emb_cache.setdefault(cid, []).append(
                    (opt_id, emb_bytes, opt_bytes, trans_bytes)
                )

    for node in active_nodes:
        # ADR-005: Skip clean clusters in dirty-only mode
        if dirty_ids is not None and node.id not in dirty_ids:
            continue

        member_count = node.member_count or 0
        # Normal split: ≥ SPLIT_MIN_MEMBERS
        # Forced split: ≥ FORCED_SPLIT_MIN_MEMBERS with very low coherence
        is_forced_split_candidate = (
            FORCED_SPLIT_MIN_MEMBERS <= member_count < SPLIT_MIN_MEMBERS
            and node.coherence is not None
            and node.coherence < FORCED_SPLIT_COHERENCE_FLOOR
        )
        if member_count < SPLIT_MIN_MEMBERS and not is_forced_split_candidate:
            continue

        # Recompute coherence from actual member embeddings.
        _cached_opt_rows = _split_emb_cache.get(node.id, [])
        try:
            _coh_embs = [
                np.frombuffer(row[1], dtype=np.float32).copy()
                for row in _cached_opt_rows
                if row[1] is not None
            ]
            if len(_coh_embs) >= 2:
                coherence = compute_pairwise_coherence(_coh_embs)
                node.coherence = coherence
                node.cluster_metadata = write_meta(
                    node.cluster_metadata,
                    coherence_member_count=len(_coh_embs),
                )
            else:
                coherence = 1.0
        except Exception:
            logger.debug(
                "Coherence recomputation failed for '%s', using stored value",
                node.label,
                exc_info=True,
            )
            coherence = node.coherence if node.coherence is not None else 1.0

        # Stability floor: clusters above this coherence are exempt from splitting.
        # Prevents the dead zone (0.38-0.50) where clusters oscillate between
        # split and merge indefinitely (Groundhog Day loop).
        if coherence >= SPLIT_COHERENCE_EXEMPT and not is_forced_split_candidate:
            try:
                get_event_logger().log_decision(
                    path="warm", op="split", decision="stability_floor_skip",
                    cluster_id=node.id,
                    context={
                        "coherence": round(coherence, 4),
                        "floor": SPLIT_COHERENCE_EXEMPT,
                        "label": node.label,
                    },
                )
            except RuntimeError:
                pass
            continue

        # Scale: +0.05 per doubling above 6 members
        dynamic_floor = SPLIT_COHERENCE_FLOOR + max(
            0, math.log2(max(member_count, 6) / 6)
        ) * 0.05

        # Output coherence split signal: even if raw coherence is acceptable,
        # low output coherence (similar inputs → divergent outputs) suggests
        # the cluster conflates different optimization goals and should split.
        output_coh = read_meta(node.cluster_metadata).get("output_coherence")
        if output_coh is not None and coherence >= dynamic_floor:
            if output_coh >= 0.25:
                continue  # both coherences are healthy — skip
            # Low output coherence: lower the split threshold to trigger a split
            dynamic_floor = max(dynamic_floor - 0.10, 0.20)
            logger.info(
                "Output coherence split signal for '%s': raw=%.3f, output=%.3f — lowered threshold to %.3f",
                node.label, coherence, output_coh, dynamic_floor,
            )

        # Intent label coherence split signal (Tier 5b): if labels are
        # highly incoherent, lower the split threshold further. This is
        # never the sole reason for a split — only supplements embedding/output
        # coherence signals.
        if coherence >= dynamic_floor:
            label_coh = read_meta(node.cluster_metadata).get("intent_label_coherence")
            if label_coh is not None and label_coh < LABEL_COHERENCE_SPLIT_SIGNAL:
                # Labels are highly incoherent — lower the threshold by up to 0.08
                label_adjustment = min((LABEL_COHERENCE_SPLIT_SIGNAL - label_coh) * 0.5, 0.08)
                adjusted = dynamic_floor - label_adjustment
                if coherence < adjusted:
                    logger.info(
                        "Label coherence split signal for '%s': coh=%.3f, label_coh=%.3f — "
                        "lowered threshold from %.3f to %.3f",
                        node.label, coherence, label_coh, dynamic_floor, adjusted,
                    )
                    dynamic_floor = adjusted
                else:
                    continue  # coherence still above adjusted threshold
            else:
                continue  # no label coherence signal or labels are coherent

        if coherence >= dynamic_floor:
            continue

        # Cooldown: skip if this cluster already failed to split 3+ times
        # Growth-based reset: if member_count grew 25%+ since last attempt,
        # new data may create sub-structure that wasn't there before.
        node_meta = read_meta(node.cluster_metadata)
        split_failures = node_meta["split_failures"]
        if split_failures >= 3:
            split_attempt_mc = node_meta.get("split_attempt_member_count", 0)
            if split_attempt_mc > 0 and member_count >= split_attempt_mc * 1.25:
                split_failures = 0
                node.cluster_metadata = write_meta(
                    node.cluster_metadata,
                    split_failures=0,
                    split_content_hash="",
                )
                logger.info(
                    "Split cooldown reset: '%s' grew from %d to %d members",
                    node.label, split_attempt_mc, member_count,
                )
            else:
                continue

        # Content-hash loop detection: compute hash of current member set.
        # If identical to the hash stored at last split attempt AND failures
        # exceed SPLIT_CONTENT_HASH_MAX_RETRIES, skip — splitting the same
        # population will produce the same (rejected/re-merged) result.
        _current_opt_ids = sorted([r[0] for r in _cached_opt_rows])
        _current_content_hash = hashlib.sha256(
            json.dumps(_current_opt_ids).encode()
        ).hexdigest()[:16]
        stored_content_hash = node_meta.get("split_content_hash", "")
        if (
            stored_content_hash
            and stored_content_hash == _current_content_hash
            and split_failures >= SPLIT_CONTENT_HASH_MAX_RETRIES
        ):
            logger.info(
                "Split skipped (content-hash loop): '%s' — same %d members "
                "failed %d times (hash=%s)",
                node.label, len(_current_opt_ids),
                split_failures, _current_content_hash,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="split", decision="content_hash_loop",
                    cluster_id=node.id,
                    context={
                        "cluster_label": node.label,
                        "member_count": len(_current_opt_ids),
                        "content_hash": _current_content_hash,
                        "split_failures": split_failures,
                        "max_retries": SPLIT_CONTENT_HASH_MAX_RETRIES,
                    },
                )
            except RuntimeError:
                pass
            continue

        # Domain-level hash check: if this member set was recently blocked at the
        # domain level (from a prior Groundhog Day cycle under a different cluster ID),
        # skip the split. The domain node survives across cluster ID changes.
        try:
            from app.services.taxonomy._constants import DOMAIN_SPLIT_HASH_TTL_HOURS
            _domain_node = None
            if node.domain:
                _dn_q = await db.execute(
                    select(PromptCluster).where(
                        PromptCluster.state == "domain",
                        PromptCluster.label == node.domain,
                    )
                )
                _domain_node = _dn_q.scalar_one_or_none()
            if _domain_node:
                _dn_meta = read_meta(_domain_node.cluster_metadata)
                _blocked = _dn_meta.get("split_blocked_hashes", [])
                _ttl_cutoff = (
                    _utcnow() - timedelta(hours=DOMAIN_SPLIT_HASH_TTL_HOURS)
                ).isoformat()
                # Prune expired entries (write back only if changed)
                _active = [e for e in _blocked if e.get("ts", "") >= _ttl_cutoff]
                if len(_active) != len(_blocked):
                    _domain_node.cluster_metadata = write_meta(
                        _domain_node.cluster_metadata,
                        split_blocked_hashes=_active,
                    )
                    logger.debug(
                        "Pruned %d expired split hashes from domain '%s'",
                        len(_blocked) - len(_active), _domain_node.label,
                    )
                # Check if current hash is blocked
                if any(e.get("hash") == _current_content_hash for e in _active):
                    _blocked_entry = next(
                        e for e in _active if e.get("hash") == _current_content_hash
                    )
                    logger.info(
                        "Split skipped (domain-level hash block): '%s' — hash=%s blocked since %s",
                        node.label, _current_content_hash, _blocked_entry.get("ts", "?"),
                    )
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="split", decision="domain_hash_blocked",
                            cluster_id=node.id,
                            context={
                                "hash": _current_content_hash,
                                "domain": _domain_node.label,
                                "label": node.label,
                                "blocked_since": _blocked_entry.get("ts", ""),
                            },
                        )
                    except RuntimeError:
                        pass
                    continue
        except Exception as _dh_exc:
            logger.debug("Domain hash check failed (non-fatal): %s", _dh_exc)

        ops_attempted += 1
        split_attempted_ids.append(node.id)
        split_content_hashes[node.id] = _current_content_hash
        logger.info(
            "Split candidate: '%s' (members=%d, coherence=%.3f, threshold=%.3f)",
            node.label, member_count, coherence, dynamic_floor,
        )

        # Gather ACTIVE families assigned to this node
        fam_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == node.id,
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        node_families = list(fam_q.scalars().all())

        # --- Leaf split path ---
        # Triggers for: (a) normal splits (≥ SPLIT_MIN_MEMBERS), or
        # (b) forced splits for incoherent clusters
        #     (≥ FORCED_SPLIT_MIN_MEMBERS, coherence < FORCED_SPLIT_COHERENCE_FLOOR)
        _qualifies_for_leaf_split = (
            member_count >= SPLIT_MIN_MEMBERS
            or is_forced_split_candidate
        )
        if len(node_families) < SPLIT_MIN_MEMBERS and _qualifies_for_leaf_split:
            opt_rows = _cached_opt_rows
            _min_rows = FORCED_SPLIT_MIN_MEMBERS if is_forced_split_candidate else SPLIT_MIN_MEMBERS
            if len(opt_rows) >= _min_rows:
                from app.services.taxonomy.split import split_cluster

                result = await split_cluster(node, engine, db, opt_rows)

                if not result.success:
                    # Track failed attempt for cooldown
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        split_failures=split_failures + 1,
                        split_attempt_member_count=member_count,
                        split_content_hash=_current_content_hash,
                    )
                    # Record at domain level if failures reach 3
                    if split_failures + 1 >= 3:
                        try:
                            await _record_domain_split_block(
                                db, node.domain or "general",
                                _current_content_hash, node.label or "?",
                                source="split_failures_reached_3",
                            )
                        except Exception:
                            pass
                    logger.info(
                        "Leaf split failed for '%s' (attempt %d/3)",
                        node.label, split_failures + 1,
                    )
                else:
                    # Reset failure counter and content hash on success
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        split_failures=0,
                        split_attempt_member_count=0,
                        split_content_hash="",
                    )
                    embedding_index_mutations += len(result.children) + 1

                    # Protect split children and parent from merge in same cycle
                    split_protected_ids.add(node.id)
                    for ch in result.children:
                        split_protected_ids.add(ch.id)

                    ops_accepted += result.children_created
                    operations_log.append({
                        "type": "leaf_split",
                        "parent_id": node.id,
                        "children": [c.id for c in result.children],
                    })
                    logger.info(
                        "Leaf split complete: '%s' -> %d sub-clusters",
                        node.label, result.children_created,
                    )
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="split", decision="leaf_split",
                            cluster_id=node.id,
                            context={
                                "trigger": "coherence_floor",
                                "coherence": round(coherence, 4),
                                "floor": round(dynamic_floor, 4),
                                "hdbscan_clusters": result.children_created,
                                "noise_count": result.noise_reassigned,
                                "silhouette": getattr(result, 'silhouette', None),
                                "children": [
                                    {
                                        "id": c.id,
                                        "label": c.label,
                                        "members": c.member_count or 0,
                                        "coherence": round(c.coherence or 0.0, 4),
                                    }
                                    for c in result.children
                                ],
                                "fallback": "none",
                            },
                        )
                    except RuntimeError:
                        pass

        # --- Family-based split path ---
        if len(node_families) >= SPLIT_MIN_MEMBERS:
            child_embs_fam = []
            child_blended_fam = []
            child_fam_ids_fam = []
            opt_idx = getattr(engine, "_optimized_index", None)
            trans_idx = getattr(engine, "_transformation_index", None)
            for f in node_families:
                try:
                    emb = np.frombuffer(
                        f.centroid_embedding, dtype=np.float32
                    )
                    opt_vec = opt_idx.get_vector(f.id) if opt_idx else None
                    trans_vec = trans_idx.get_vector(f.id) if trans_idx else None
                    child_embs_fam.append(emb)
                    child_blended_fam.append(blend_embeddings(
                        raw=emb,
                        optimized=opt_vec,
                        transformation=trans_vec,
                    ))
                    child_fam_ids_fam.append(f.id)
                except (ValueError, TypeError) as _fs_exc:
                    logger.warning(
                        "Corrupt embedding in family split, cluster='%s': %s",
                        f.label, _fs_exc,
                    )
                    continue

            if len(child_blended_fam) >= SPLIT_MIN_MEMBERS:
                split_clusters_fam = batch_cluster(
                    child_blended_fam, min_cluster_size=3
                )
                if split_clusters_fam.n_clusters >= 2:
                    from app.services.taxonomy.lifecycle import attempt_split

                    child_groups = []
                    for cid in range(split_clusters_fam.n_clusters):
                        mask = split_clusters_fam.labels == cid
                        group_ids = [
                            child_fam_ids_fam[i]
                            for i in range(len(child_fam_ids_fam))
                            if mask[i]
                        ]
                        group_embs = [
                            child_embs_fam[i]
                            for i in range(len(child_embs_fam))
                            if mask[i]
                        ]
                        if group_ids:
                            child_groups.append((group_ids, group_embs))

                    if len(child_groups) >= 2:
                        children = await attempt_split(
                            db=db,
                            parent_node=node,
                            child_clusters=child_groups,
                            warm_path_age=engine._warm_path_age,
                            provider=engine._provider,
                            model=settings.MODEL_HAIKU,
                        )
                        if children:
                            ops_accepted += len(children)
                            split_protected_ids.add(node.id)
                            for child in children:
                                split_protected_ids.add(child.id)
                                operations_log.append(
                                    {"type": "split", "node_id": child.id}
                                )
                                try:
                                    get_event_logger().log_decision(
                                        path="warm", op="split", decision="family_split",
                                        cluster_id=child.id,
                                        context={
                                            "split_source_id": node.id,
                                            "split_source_label": node.label,
                                            "assigned_parent_id": child.parent_id,
                                            "children_created": len(children),
                                        },
                                    )
                                except RuntimeError:
                                    pass

    # --- Priority 2: Emerge ---
    # Fix #7: exclude domain/archived from emerge query (parent_id IS NULL
    # must also exclude domain and archived nodes).
    fam_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id.is_(None),
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
        )
    )
    unassigned_families = list(fam_result.scalars().all())

    if len(unassigned_families) >= 3:
        ops_attempted += 1
        emerged = await engine._try_emerge_from_families(
            db, unassigned_families, batch_cluster,
        )
        ops_accepted += len(emerged)
        operations_log.extend(emerged)

    return PhaseResult(
        phase="split_emerge",
        q_before=0.0,  # Overwritten by orchestrator
        q_after=0.0,   # Overwritten by orchestrator
        accepted=False, # Set by orchestrator Q gate
        ops_attempted=ops_attempted,
        ops_accepted=ops_accepted,
        operations=operations_log,
        embedding_index_mutations=embedding_index_mutations,
        split_attempted_ids=split_attempted_ids,
        split_content_hashes=split_content_hashes,
    )


# ---------------------------------------------------------------------------
# Phase 2 — Merge
# ---------------------------------------------------------------------------


async def phase_merge(
    engine: TaxonomyEngine,
    db: AsyncSession,
    split_protected_ids: set[str],
    dirty_ids: set[str] | None = None,  # ADR-005: None = process all
) -> PhaseResult:
    """Global best-pair merge and same-domain label/embedding merge.

    Fix #12: replace manual cosine at label merge and embedding merge
    with ``cosine_similarity()`` from clustering.py.
    """
    ops_attempted = 0
    ops_accepted = 0
    operations_log: list[dict] = []
    embedding_index_mutations = 0

    # Load active nodes
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    active_nodes = list(active_q.scalars().all())

    # --- Global best-pair merge ---
    # Use blended centroids (raw + optimized + transformation) for the
    # pairwise similarity matrix so merge candidates reflect topic,
    # output quality, and technique direction — not just topic similarity.
    # Exclude split-protected and merge-cooled nodes from merge candidates.
    now_merge = _utcnow()
    opt_idx = getattr(engine, "_optimized_index", None)
    trans_idx = getattr(engine, "_transformation_index", None)
    if len(active_nodes) >= 2:
        centroids = []
        blended_centroids = []
        valid_nodes: list[PromptCluster] = []
        _global_sp_count = 0
        _global_mc_count = 0
        for n in active_nodes:
            if n.id in split_protected_ids:
                _global_sp_count += 1
                continue
            meta_m = read_meta(n.cluster_metadata)
            merge_until_m = meta_m.get("merge_protected_until", "")
            if merge_until_m:
                try:
                    # INVARIANT: merge_protected_until is stored as naive UTC (no tzinfo).
                    # All comparisons use _utcnow() which is also naive UTC.
                    # Do NOT compare with timezone-aware datetimes.
                    if now_merge < datetime.fromisoformat(merge_until_m):
                        _global_mc_count += 1
                        continue
                except (ValueError, TypeError):
                    pass
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                opt_vec = opt_idx.get_vector(n.id) if opt_idx else None
                trans_vec = trans_idx.get_vector(n.id) if trans_idx else None
                centroids.append(c)
                blended_centroids.append(blend_embeddings(
                    raw=c,
                    optimized=opt_vec,
                    transformation=trans_vec,
                ))
                valid_nodes.append(n)
            except (ValueError, TypeError) as _gm_exc:
                logger.warning(
                    "Corrupt centroid in global merge candidates, cluster='%s': %s",
                    n.label, _gm_exc,
                )
                continue

        if (_global_sp_count or _global_mc_count) and len(blended_centroids) >= 2:
            try:
                get_event_logger().log_decision(
                    path="warm", op="merge", decision="candidates_filtered",
                    context={"pass": "global", "split_protected": _global_sp_count, "merge_cooled": _global_mc_count},
                )
            except RuntimeError:
                pass

        if len(blended_centroids) >= 2:
            mat = np.stack(blended_centroids, axis=0).astype(np.float32)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            mat_norm = mat / norms
            sim = mat_norm @ mat_norm.T
            np.fill_diagonal(sim, -1)

            best_i, best_j = np.unravel_index(np.argmax(sim), sim.shape)
            best_score = float(sim[best_i, best_j])

            merge_node_a = valid_nodes[int(best_i)]
            merge_node_b = valid_nodes[int(best_j)]
            merge_threshold = adaptive_merge_threshold(
                max(
                    merge_node_a.member_count or 1,
                    merge_node_b.member_count or 1,
                ),
            )

            # Quality gates: block merge if either cluster is unhealthy.
            # Gate 1: Coherence floor — merging two fragmented clusters
            # creates a worse fragmented cluster.
            merge_blocked = False

            # ADR-005: Skip global merge when neither candidate is dirty
            if dirty_ids is not None and merge_node_a.id not in dirty_ids and merge_node_b.id not in dirty_ids:
                merge_blocked = True
                logger.debug(
                    "Global merge skipped: neither '%s' nor '%s' is dirty",
                    merge_node_a.label, merge_node_b.label,
                )

            if not merge_blocked and (
                (merge_node_a.coherence is not None and merge_node_a.coherence < 0.35)
                or (merge_node_b.coherence is not None and merge_node_b.coherence < 0.35)
            ):
                merge_blocked = True
                logger.debug(
                    "Merge blocked: coherence floor — '%s' (%.2f) + '%s' (%.2f)",
                    merge_node_a.label, merge_node_a.coherence or 0,
                    merge_node_b.label, merge_node_b.coherence or 0,
                )

            # Gate 2: Output coherence — block if either has divergent outputs.
            # Ease threshold only when both are high (similar outputs, safe merge).
            a_meta = read_meta(merge_node_a.cluster_metadata)
            b_meta = read_meta(merge_node_b.cluster_metadata)
            a_out_coh = a_meta.get("output_coherence")
            b_out_coh = b_meta.get("output_coherence")
            if not merge_blocked and (
                (a_out_coh is not None and a_out_coh < 0.30)
                or (b_out_coh is not None and b_out_coh < 0.30)
            ):
                merge_blocked = True
                logger.debug(
                    "Merge blocked: low output coherence — '%s' (%.2f) + '%s' (%.2f)",
                    merge_node_a.label, a_out_coh or 0,
                    merge_node_b.label, b_out_coh or 0,
                )
            elif not merge_blocked and (
                a_out_coh is not None and b_out_coh is not None
                and a_out_coh > 0.5 and b_out_coh > 0.5
            ):
                merge_threshold = max(merge_threshold - 0.03, 0.45)

            if merge_blocked:
                # Determine which gate blocked the merge for observability
                _gate = "coherence_floor"
                if (
                    (a_out_coh is not None and a_out_coh < 0.30)
                    or (b_out_coh is not None and b_out_coh < 0.30)
                ):
                    _gate = "output_floor"
                try:
                    get_event_logger().log_decision(
                        path="warm", op="merge", decision="blocked",
                        context={
                            "pair": [merge_node_a.id, merge_node_b.id],
                            "labels": [merge_node_a.label, merge_node_b.label],
                            "similarity": round(best_score, 4),
                            "threshold": round(merge_threshold, 4),
                            "gate": _gate,
                        },
                    )
                except RuntimeError:
                    pass

            if not merge_blocked and best_score >= merge_threshold:
                ops_attempted += 1
                from app.services.taxonomy.lifecycle import attempt_merge

                # Pre-read merge protection for both candidates (before attempt_merge)
                _meta_a = read_meta(merge_node_a.cluster_metadata)
                _meta_b = read_meta(merge_node_b.cluster_metadata)

                merged = await attempt_merge(
                    db=db,
                    node_a=merge_node_a,
                    node_b=merge_node_b,
                    warm_path_age=engine._warm_path_age,
                    embedding_svc=engine._embedding,
                )
                if merged:
                    ops_accepted += 1
                    operations_log.append(
                        {"type": "merge", "node_id": merged.id}
                    )
                    loser = (
                        merge_node_b
                        if merged.id == merge_node_a.id
                        else merge_node_a
                    )
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="merge", decision="merged",
                            cluster_id=merged.id,
                            context={
                                "pair": [merge_node_a.id, merge_node_b.id],
                                "labels": [merge_node_a.label, merge_node_b.label],
                                "similarity": round(best_score, 4),
                                "threshold": round(merge_threshold, 4),
                                "gate": "passed",
                                "survivor_id": merged.id,
                                "combined_members": merged.member_count or 0,
                            },
                        )
                    except RuntimeError:
                        pass
                    # Merge-back detection
                    _loser_merge_until = (
                        _meta_b if merged.id == merge_node_a.id else _meta_a
                    ).get("merge_protected_until", "")
                    await _detect_merge_back(db, _loser_merge_until, merged, loser.id)
                    # Update embedding index: upsert winner, remove loser
                    winner_centroid = np.frombuffer(
                        merged.centroid_embedding, dtype=np.float32
                    )
                    await engine._embedding_index.upsert(
                        merged.id, winner_centroid
                    )
                    await engine._embedding_index.remove(loser.id)
                    await engine._transformation_index.remove(loser.id)
                    await engine._optimized_index.remove(loser.id)
                    embedding_index_mutations += 2
                    # ADR-005: survivor needs re-evaluation
                    engine.mark_dirty(
                        merged.id,
                        project_id=engine._cluster_project_cache.get(merged.id),
                    )

    # --- Same-domain duplicate merge ---
    same_domain_merge_base = 0.65
    label_merge_sanity_base = 0.40
    try:
        from app.services.taxonomy.lifecycle import attempt_merge

        # Reload active nodes (may have changed from global merge)
        current_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
        )
        current_active = list(current_q.scalars().all())

        # Group by primary domain, excluding split-protected and merge-cooled nodes
        now = _utcnow()
        domain_groups: dict[str, list[PromptCluster]] = {}
        _domain_sp_count = 0
        _domain_mc_count = 0
        for node in current_active:
            if node.id in split_protected_ids:
                _domain_sp_count += 1
                continue
            # Merge cooldown: split children are protected for 30 minutes
            meta = read_meta(node.cluster_metadata)
            merge_until = meta.get("merge_protected_until", "")
            if merge_until:
                try:
                    # INVARIANT: merge_protected_until is stored as naive UTC (no tzinfo).
                    # All comparisons use _utcnow() which is also naive UTC.
                    # Do NOT compare with timezone-aware datetimes.
                    protected_until = datetime.fromisoformat(merge_until)
                    if now < protected_until:
                        _domain_mc_count += 1
                        continue  # still protected
                except (ValueError, TypeError):
                    pass
            primary, _ = parse_domain(node.domain or "general")
            domain_groups.setdefault(primary, []).append(node)

        if _domain_sp_count or _domain_mc_count:
            try:
                get_event_logger().log_decision(
                    path="warm", op="merge", decision="candidates_filtered",
                    context={
                        "pass": "same_domain",
                        "split_protected": _domain_sp_count,
                        "merge_cooled": _domain_mc_count,
                    },
                )
            except RuntimeError:
                pass

        domain_merges = 0
        for domain, siblings in domain_groups.items():
            if len(siblings) < 2:
                continue

            # ADR-005: Skip domain groups with no dirty nodes
            if dirty_ids is not None:
                has_dirty = any(n.id in dirty_ids for n in siblings)
                if not has_dirty:
                    continue

            # Signal A: identical labels (one merge per domain per cycle)
            label_merged = False
            label_groups: dict[str, list[PromptCluster]] = {}
            for s in siblings:
                label_groups.setdefault(s.label, []).append(s)
            for label, group in label_groups.items():
                if label_merged:
                    break
                if len(group) < 2:
                    continue
                group.sort(
                    key=lambda n: n.member_count or 0, reverse=True
                )
                survivor = group[0]
                for loser in group[1:]:
                    # Fix #12: use cosine_similarity() from clustering.py
                    # Use blended centroids for consistency with global merge.
                    try:
                        emb_a = np.frombuffer(
                            survivor.centroid_embedding, dtype=np.float32
                        )
                        emb_b = np.frombuffer(
                            loser.centroid_embedding, dtype=np.float32
                        )
                        blend_a = blend_embeddings(
                            raw=emb_a,
                            optimized=opt_idx.get_vector(survivor.id) if opt_idx else None,
                            transformation=trans_idx.get_vector(survivor.id) if trans_idx else None,
                        )
                        blend_b = blend_embeddings(
                            raw=emb_b,
                            optimized=opt_idx.get_vector(loser.id) if opt_idx else None,
                            transformation=trans_idx.get_vector(loser.id) if trans_idx else None,
                        )
                        sim = cosine_similarity(blend_a, blend_b)
                    except (ValueError, TypeError):
                        sim = 0.0
                    combined_mc = max(
                        survivor.member_count or 0,
                        loser.member_count or 0,
                    )
                    label_merge_sanity = max(
                        label_merge_sanity_base,
                        adaptive_merge_threshold(combined_mc),
                    )
                    if sim >= label_merge_sanity:
                        # Pre-read loser metadata before merge
                        _loser_merge_until_lbl = read_meta(loser.cluster_metadata).get("merge_protected_until", "")
                        merged = await attempt_merge(
                            db,
                            survivor,
                            loser,
                            engine._warm_path_age,
                            embedding_svc=engine._embedding,
                        )
                        if merged:
                            domain_merges += 1
                            logger.info(
                                "Same-domain label merge: '%s' absorbed "
                                "duplicate (sim=%.2f, domain=%s)",
                                label, sim, domain,
                            )
                            await _detect_merge_back(db, _loser_merge_until_lbl, merged, loser.id)
                            winner_centroid = np.frombuffer(
                                merged.centroid_embedding, dtype=np.float32
                            )
                            await engine._embedding_index.upsert(
                                merged.id, winner_centroid
                            )
                            await engine._embedding_index.remove(loser.id)
                            await engine._transformation_index.remove(loser.id)
                            await engine._optimized_index.remove(loser.id)
                            embedding_index_mutations += 2
                            # ADR-005: survivor needs re-evaluation
                            engine.mark_dirty(
                                merged.id,
                                project_id=engine._cluster_project_cache.get(merged.id),
                            )
                            label_merged = True
                            break  # one merge per domain per cycle

            # Signal B: high centroid similarity within domain
            remaining = [s for s in siblings if s.state not in EXCLUDED_STRUCTURAL_STATES]
            if len(remaining) >= 2:
                merged_this_domain = False
                for i in range(len(remaining)):
                    if merged_this_domain:
                        break
                    for j in range(i + 1, len(remaining)):
                        # Fix #12: use cosine_similarity() from clustering.py
                        # Use blended centroids for consistency with global merge.
                        try:
                            emb_i = np.frombuffer(
                                remaining[i].centroid_embedding,
                                dtype=np.float32,
                            )
                            emb_j = np.frombuffer(
                                remaining[j].centroid_embedding,
                                dtype=np.float32,
                            )
                            blend_i = blend_embeddings(
                                raw=emb_i,
                                optimized=opt_idx.get_vector(remaining[i].id) if opt_idx else None,
                                transformation=trans_idx.get_vector(remaining[i].id) if trans_idx else None,
                            )
                            blend_j = blend_embeddings(
                                raw=emb_j,
                                optimized=opt_idx.get_vector(remaining[j].id) if opt_idx else None,
                                transformation=trans_idx.get_vector(remaining[j].id) if trans_idx else None,
                            )
                            sim = cosine_similarity(blend_i, blend_j)
                        except (ValueError, TypeError):
                            continue
                        both_active = (
                            remaining[i].state not in EXCLUDED_STRUCTURAL_STATES
                            and remaining[j].state not in EXCLUDED_STRUCTURAL_STATES
                        )
                        combined_mc = max(
                            remaining[i].member_count or 0,
                            remaining[j].member_count or 0,
                        )
                        same_domain_threshold = max(
                            same_domain_merge_base,
                            adaptive_merge_threshold(combined_mc),
                        )
                        if sim >= same_domain_threshold and both_active:
                            ni, nj = remaining[i], remaining[j]
                            # Block same-domain merges that would create an
                            # oversized cluster. The global merge path uses
                            # adaptive_merge_threshold with size pressure for
                            # large clusters — the same-domain path should not
                            # bypass that by merging on similarity alone.
                            combined_members = (ni.member_count or 0) + (nj.member_count or 0)
                            if combined_members >= MEGA_CLUSTER_MEMBER_FLOOR:
                                logger.debug(
                                    "Same-domain merge blocked: would recreate mega-cluster "
                                    "'%s' + '%s' (%d combined, low coherence)",
                                    ni.label, nj.label, combined_members,
                                )
                                try:
                                    get_event_logger().log_decision(
                                        path="warm", op="merge", decision="blocked",
                                        context={
                                            "pair": [ni.id, nj.id],
                                            "labels": [ni.label, nj.label],
                                            "similarity": round(sim, 4),
                                            "threshold": round(same_domain_threshold, 4),
                                            "gate": "mega_cluster_prevention",
                                            "combined_members": combined_members,
                                        },
                                    )
                                except RuntimeError:
                                    pass
                                continue
                            big = (
                                ni
                                if (ni.member_count or 0) >= (nj.member_count or 0)
                                else nj
                            )
                            small = nj if big is ni else ni
                            # Pre-read loser metadata before merge
                            _loser_merge_until_emb = read_meta(small.cluster_metadata).get("merge_protected_until", "")
                            merged = await attempt_merge(
                                db,
                                big,
                                small,
                                engine._warm_path_age,
                                embedding_svc=engine._embedding,
                            )
                            if merged:
                                domain_merges += 1
                                logger.info(
                                    "Same-domain embedding merge: '%s' + '%s' "
                                    "(sim=%.2f, domain=%s)",
                                    big.label, small.label, sim, domain,
                                )
                                await _detect_merge_back(db, _loser_merge_until_emb, merged, small.id)
                                winner_centroid = np.frombuffer(
                                    merged.centroid_embedding,
                                    dtype=np.float32,
                                )
                                await engine._embedding_index.upsert(
                                    merged.id, winner_centroid
                                )
                                await engine._embedding_index.remove(small.id)
                                await engine._transformation_index.remove(small.id)
                                await engine._optimized_index.remove(small.id)
                                embedding_index_mutations += 2
                                # ADR-005: survivor needs re-evaluation
                                engine.mark_dirty(
                                    merged.id,
                                    project_id=engine._cluster_project_cache.get(merged.id),
                                )
                                merged_this_domain = True
                                break  # one merge per domain per cycle

        if domain_merges:
            ops_accepted += domain_merges
            ops_attempted += domain_merges
            logger.info(
                "Same-domain merge: %d merges completed", domain_merges
            )
            await db.flush()
    except Exception as merge_exc:
        logger.warning("Same-domain merge failed (non-fatal): %s", merge_exc)

    return PhaseResult(
        phase="merge",
        q_before=0.0,  # Overwritten by orchestrator
        q_after=0.0,   # Overwritten by orchestrator
        accepted=False, # Set by orchestrator Q gate
        ops_attempted=ops_attempted,
        ops_accepted=ops_accepted,
        operations=operations_log,
        embedding_index_mutations=embedding_index_mutations,
    )


# ---------------------------------------------------------------------------
# Phase 3 — Retire
# ---------------------------------------------------------------------------


async def phase_retire(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> PhaseResult:
    """Retire idle nodes with 0 members."""
    ops_attempted = 0
    ops_accepted = 0
    operations_log: list[dict] = []
    embedding_index_mutations = 0

    # Load active nodes
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    active_nodes = list(active_q.scalars().all())

    for node in active_nodes:
        if (node.member_count or 0) == 0:
            ops_attempted += 1
            from app.services.taxonomy.lifecycle import attempt_retire

            retire_result = await attempt_retire(
                db=db,
                node=node,
                warm_path_age=engine._warm_path_age,
            )
            if retire_result.success:
                ops_accepted += 1
                operations_log.append({"type": "retire", "node_id": node.id})
                await engine._embedding_index.remove(node.id)
                await engine._transformation_index.remove(node.id)
                await engine._optimized_index.remove(node.id)
                embedding_index_mutations += 1
                try:
                    get_event_logger().log_decision(
                        path="warm", op="retire", decision="archived",
                        cluster_id=node.id,
                        context={
                            "node_label": node.label,
                            "member_count_before": node.member_count or 0,
                            "sibling_target_id": retire_result.sibling_target_id,
                            "sibling_label": retire_result.sibling_label,
                            "families_reparented": retire_result.families_reparented,
                            "optimizations_reassigned": retire_result.optimizations_reassigned,
                        },
                    )
                except RuntimeError:
                    pass

    # --- Dissolution: small incoherent clusters with members ---
    # These clusters are too small to split (< SPLIT_MIN_MEMBERS) and too
    # incoherent to be useful. Dissolve them: reassign members to nearest
    # active cluster, then archive. Uses the same _reassign_to_active()
    # helper built for candidate rejection.
    now = _utcnow()
    for node in active_nodes:
        mc = node.member_count or 0
        if mc == 0 or mc > DISSOLVE_MAX_MEMBERS:
            continue
        if node.coherence is None or node.coherence >= DISSOLVE_COHERENCE_CEILING:
            continue
        # Age guard: don't dissolve newly created clusters (give them time to grow)
        if node.created_at:
            try:
                created = node.created_at
                # Handle timezone-aware vs naive comparison
                if hasattr(created, 'tzinfo') and created.tzinfo is not None:
                    created = created.replace(tzinfo=None)
                age_hours = (now - created).total_seconds() / 3600
                if age_hours < DISSOLVE_MIN_AGE_HOURS:
                    continue
            except (TypeError, ValueError):
                pass  # can't determine age, proceed

        # This cluster qualifies for dissolution
        ops_attempted += 1
        logger.info(
            "Dissolving incoherent cluster '%s' (members=%d, coherence=%.3f)",
            node.label, mc, node.coherence,
        )

        # Gather member embeddings for reassignment
        member_q = await db.execute(
            select(Optimization.id, Optimization.embedding)
            .where(
                Optimization.cluster_id == node.id,
                Optimization.embedding.isnot(None),
            )
        )
        member_rows = member_q.all()
        opt_ids = [r[0] for r in member_rows]
        opt_embs = []
        for _, emb_bytes in member_rows:
            try:
                opt_embs.append(np.frombuffer(emb_bytes, dtype=np.float32).copy())
            except (ValueError, TypeError) as _de_exc:
                logger.warning(
                    "Corrupt embedding in dissolution, defaulting to zeros: %s",
                    _de_exc,
                )
                opt_embs.append(np.zeros(384, dtype=np.float32))

        # Reassign members to nearest active cluster (exclude self)
        reassignment_info = await _reassign_to_active(
            db, opt_ids, opt_embs, exclude_cluster_ids={node.id},
        )

        # ADR-005: mark dissolution targets dirty for next cycle re-evaluation
        for _ra_info in reassignment_info:
            engine.mark_dirty(
                _ra_info["cluster_id"],
                project_id=engine._cluster_project_cache.get(_ra_info["cluster_id"]),
            )

        # Archive the dissolved cluster — zero ALL counters to prevent
        # phantom data. Must match the fields cleared by attempt_merge()
        # and attempt_retire() in lifecycle.py.
        node.state = "archived"
        node.archived_at = now
        node.member_count = 0
        node.weighted_member_sum = 0.0
        node.scored_count = 0
        node.avg_score = None
        node.usage_count = 0
        ops_accepted += 1
        operations_log.append({
            "type": "dissolve",
            "node_id": node.id,
            "node_label": node.label,
            "members_reassigned": len(opt_ids),
        })

        # Remove from indices
        await engine._embedding_index.remove(node.id)
        await engine._transformation_index.remove(node.id)
        await engine._optimized_index.remove(node.id)
        embedding_index_mutations += 1

        # Clean up dissolved cluster's MetaPatterns inline — don't defer
        # to Phase 0 backfill. Pattern: matches split.py and lifecycle.py.
        try:
            _dissolved_mp_q = await db.execute(
                select(MetaPattern).where(MetaPattern.cluster_id == node.id)
            )
            _dissolved_mps = list(_dissolved_mp_q.scalars().all())
            for _dmp in _dissolved_mps:
                await db.delete(_dmp)
            if _dissolved_mps:
                logger.info(
                    "Dissolution: deleted %d meta-patterns from '%s'",
                    len(_dissolved_mps), node.label,
                )
        except Exception as _dmp_exc:
            logger.warning("Dissolution meta-pattern cleanup failed (non-fatal): %s", _dmp_exc)

        try:
            get_event_logger().log_decision(
                path="warm", op="retire", decision="dissolved",
                cluster_id=node.id,
                context={
                    "cluster_label": node.label,
                    "coherence": round(node.coherence, 4) if node.coherence is not None else None,
                    "member_count": len(opt_ids),
                    "reason": "incoherent_small_cluster",
                    "coherence_ceiling": DISSOLVE_COHERENCE_CEILING,
                    "max_members": DISSOLVE_MAX_MEMBERS,
                    "members_reassigned_to": reassignment_info,
                },
            )
        except RuntimeError:
            pass

    return PhaseResult(
        phase="retire",
        q_before=0.0,  # Overwritten by orchestrator
        q_after=0.0,   # Overwritten by orchestrator
        accepted=False, # Set by orchestrator Q gate
        ops_attempted=ops_attempted,
        ops_accepted=ops_accepted,
        operations=operations_log,
        embedding_index_mutations=embedding_index_mutations,
    )


# ---------------------------------------------------------------------------
# Phase 4 — Refresh
# ---------------------------------------------------------------------------


async def phase_refresh(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> RefreshResult:
    """Stale label and meta-pattern refresh.

    Fix #15: extract new patterns FIRST, only delete old ones if extraction
    succeeds.
    """
    result = RefreshResult()

    refresh_min_members = 1     # extract patterns from ANY non-empty cluster
    # Lowered from 3: with diverse seed batches, 74% of clusters end up as
    # singletons. Even a single optimization demonstrates a concrete
    # transformation technique worth capturing as a meta-pattern.
    refresh_sample_size = 8     # representative sample for re-extraction
    refresh_cooldown_minutes = 10  # min time between re-extractions
    refresh_min_delta = 3       # min member change to override cooldown

    try:
        from app.services.taxonomy.labeling import generate_label

        # Load active non-domain nodes
        nodes_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
        )
        active_nodes = list(nodes_q.scalars().all())

        # --- Phase A: Collect stale cluster data (sequential DB queries) ---
        now = _utcnow()
        stale_clusters: list[tuple[PromptCluster, list[str], list]] = []  # (node, member_texts, sample_opts)
        for node in active_nodes:
            if node.state == "domain":
                continue
            if (node.member_count or 0) < refresh_min_members:
                continue  # Empty cluster — nothing to extract
            meta = read_meta(node.cluster_metadata)

            # Event-driven: only refresh clusters marked stale by mutation events
            if not meta.get("pattern_stale", True):
                continue  # patterns are fresh

            # Cooldown: don't re-extract if recently refreshed AND small change
            last_refresh = meta.get("label_refreshed_at", "")
            last_pmc = meta.get("pattern_member_count", 0)
            if last_refresh:
                try:
                    refresh_time = datetime.fromisoformat(last_refresh)
                    age_minutes = (now - refresh_time).total_seconds() / 60
                    member_delta = abs((node.member_count or 0) - last_pmc)
                    if age_minutes < refresh_cooldown_minutes and member_delta < refresh_min_delta:
                        continue  # too soon, too little change
                except (ValueError, TypeError):
                    pass  # malformed timestamp, proceed with refresh

            # Gather representative sample of recent members
            sample_q = await db.execute(
                select(Optimization)
                .where(Optimization.cluster_id == node.id)
                .order_by(Optimization.created_at.desc())
                .limit(refresh_sample_size)
            )
            sample_opts = list(sample_q.scalars().all())
            if not sample_opts:
                continue  # Empty cluster (member_count out of sync)

            member_texts = [
                o.intent_label or (o.raw_prompt or "")[:200]
                for o in sample_opts
            ]
            stale_clusters.append((node, member_texts, sample_opts))

        if not stale_clusters:
            # Nothing to refresh — skip flush and event
            pass
        else:
            # --- Phase B: Parallel label generation (LLM calls, no DB) ---
            label_tasks = [
                generate_label(
                    provider=engine._provider,
                    member_texts=sc[1],
                    model=settings.MODEL_HAIKU,
                    current_label=sc[0].label,  # continuity anchor
                )
                for sc in stale_clusters
            ]
            labels = await asyncio.gather(*label_tasks, return_exceptions=True)

            # --- Phase C: Parallel pattern extraction across clusters ---
            # LLM calls are the bottleneck (~8-30s each). Parallelizing across
            # clusters gives massive speedup. IMPORTANT: extract_meta_patterns()
            # does DB reads (taxonomy context lookup). We pre-compute taxonomy
            # context strings here (sequential, safe) and pass them to the
            # parallel LLM calls to avoid concurrent DB session access.

            # Pre-compute taxonomy context per cluster (sequential DB reads)
            cluster_taxonomy_ctx: dict[str, str] = {}
            for sc_node, _, sc_opts in stale_clusters:
                ctx_str = ""
                try:
                    breadcrumb = await build_breadcrumb(db, sc_node)
                    ctx_str = (
                        f'This prompt belongs to the "{sc_node.label}" pattern cluster '
                        f"({' > '.join(breadcrumb)}).\n"
                    )
                except Exception as _bc_exc:
                    logger.warning(
                        "Breadcrumb build failed for cluster '%s' (pattern refresh): %s",
                        sc_node.label, _bc_exc,
                    )
                cluster_taxonomy_ctx[sc_node.id] = ctx_str

            # Bound concurrency to avoid Haiku rate-limit pressure.
            # 10 matches batch_pipeline.py's CLI concurrency limit.
            _extraction_sem = asyncio.Semaphore(10)

            async def _extract_patterns_for_cluster(
                cluster_node: PromptCluster,
                opts: list,
                tax_ctx: str,
            ) -> list[str]:
                """Extract patterns using LLM only — no DB access."""
                texts: list[str] = []
                for opt in opts[:5]:
                    try:
                        # Direct LLM call instead of extract_meta_patterns() to
                        # avoid shared DB session in parallel coroutines.
                        template = engine._prompt_loader.render(
                            "extract_patterns.md",
                            {
                                "raw_prompt": (opt.raw_prompt or "")[:2000],
                                "optimized_prompt": (opt.optimized_prompt or "")[:2000],
                                "intent_label": opt.intent_label or "general",
                                "domain_raw": opt.domain_raw or opt.domain or "general",
                                "strategy_used": opt.strategy_used or "auto",
                                "taxonomy_context": tax_ctx,
                            },
                        )
                        from app.providers.base import call_provider_with_retry
                        async with _extraction_sem:
                            response = await call_provider_with_retry(
                                engine._provider,
                                model=settings.MODEL_HAIKU,
                                system_prompt="You are a prompt engineering analyst. Extract reusable meta-patterns.",
                                user_message=template,
                                output_format=_ExtractedPatterns,
                            )
                        patterns = [str(p) for p in response.patterns if isinstance(p, str)][:5]
                        texts.extend(patterns)
                    except Exception as _pe:
                        logger.warning(
                            "Pattern extraction failed for opt %s: %s",
                            opt.id, _pe,
                        )
                return texts

            # Fire all pattern extractions in parallel (LLM only, no DB)
            extraction_tasks = [
                _extract_patterns_for_cluster(
                    sc[0], sc[2], cluster_taxonomy_ctx.get(sc[0].id, ""),
                )
                for sc in stale_clusters
            ]
            all_patterns = await asyncio.gather(
                *extraction_tasks, return_exceptions=True,
            )

            # --- Phase D: Apply labels + patterns (sequential DB writes) ---
            # Each cluster is wrapped in its own try/except so that a single
            # failure (e.g. SQLite write contention from the hot path) does
            # not abort the entire refresh pass — remaining clusters still
            # get processed.
            for i, (node, member_texts, sample_opts) in enumerate(stale_clusters):
                try:
                    new_label = labels[i]
                    if isinstance(new_label, BaseException):
                        logger.warning(
                            "Label generation failed for cluster %s: %s",
                            node.id, new_label,
                        )
                        new_label = None
                    if new_label and new_label != "Unnamed Cluster":
                        node.label = new_label

                    # Apply extracted patterns
                    new_pattern_texts = all_patterns[i]
                    if isinstance(new_pattern_texts, BaseException):
                        logger.warning(
                            "Pattern extraction failed for cluster %s: %s",
                            node.id, new_pattern_texts,
                        )
                        new_pattern_texts = []

                    if new_pattern_texts:
                        old_patterns = await db.execute(
                            select(MetaPattern).where(
                                MetaPattern.cluster_id == node.id
                            )
                        )
                        for old_mp in old_patterns.scalars():
                            await db.delete(old_mp)

                        for text in new_pattern_texts:
                            await merge_meta_pattern(
                                db, node.id, text, engine._embedding,
                            )

                    # Track extraction state
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        pattern_member_count=node.member_count,
                        pattern_stale=False,
                        label_refreshed_at=_utcnow().isoformat(),
                    )
                    result.clusters_refreshed += 1
                    logger.info(
                        "Refreshed label+patterns for '%s' (members=%d)",
                        node.label, node.member_count,
                    )
                except Exception as per_cluster_exc:
                    logger.warning(
                        "Refresh failed for cluster '%s' (id=%s): %s — "
                        "skipping, will retry next warm cycle",
                        node.label, node.id, per_cluster_exc,
                    )
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="refresh",
                            decision="per_cluster_refresh_failed",
                            cluster_id=node.id,
                            context={
                                "cluster_label": node.label,
                                "error_type": type(per_cluster_exc).__name__,
                                "error_message": str(per_cluster_exc)[:300],
                            },
                        )
                    except RuntimeError:
                        pass

        if result.clusters_refreshed:
            await db.flush()
            logger.info(
                "Refreshed label+patterns for %d clusters",
                result.clusters_refreshed,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="refresh", decision="patterns_refreshed",
                    context={"count": result.clusters_refreshed},
                )
            except RuntimeError:
                pass
    except Exception as refresh_exc:
        logger.warning(
            "Stale label/pattern refresh failed (non-fatal): %s",
            refresh_exc,
        )

    # Decay phase weights toward defaults (prevents overfitting)
    try:
        from app.services.preferences import PreferencesService
        from app.services.taxonomy.fusion import PhaseWeights, decay_toward_defaults

        prefs_svc = PreferencesService()
        prefs = prefs_svc.load()
        phase_weights = prefs.get("phase_weights", {})
        decayed = False
        for phase_name in ["analysis", "optimization", "pattern_injection", "scoring"]:
            if phase_name in phase_weights:
                current = PhaseWeights.from_dict(phase_weights[phase_name])
                updated = decay_toward_defaults(current, phase_name)
                if updated.to_dict() != phase_weights[phase_name]:
                    phase_weights[phase_name] = updated.to_dict()
                    decayed = True
        if decayed:
            prefs_svc.patch({"phase_weights": phase_weights})
    except Exception as decay_exc:
        logger.warning("Phase weight decay failed (non-fatal): %s", decay_exc)

    # Score-correlated phase weight adaptation
    # Queries recent scored optimizations, computes score-weighted optimal
    # profile from above-median results, adapts current weights toward it.
    # Runs AFTER decay so that adaptation (alpha=0.05) dominates over
    # decay (rate=0.01) when there is strong quality signal.
    #
    # Two levels: (1) global adaptation updates preferences as a cross-task
    # regularizer, (2) per-cluster adaptation stores learned_phase_weights on
    # each cluster so future members inherit a proven profile.
    try:
        from app.services.taxonomy.fusion import (
            SCORE_ADAPTATION_LOOKBACK,
            SCORE_ADAPTATION_MIN_SAMPLES,
            adapt_weights,
            compute_score_correlated_target,
        )

        scored_q = await db.execute(
            select(
                Optimization.overall_score,
                Optimization.phase_weights_json,
                Optimization.cluster_id,
                Optimization.improvement_score,  # wider variance (std≈0.53 vs 0.27)
            ).where(
                Optimization.overall_score.isnot(None),
                Optimization.phase_weights_json.isnot(None),
                Optimization.status == "completed",
            ).order_by(
                Optimization.created_at.desc(),
            ).limit(SCORE_ADAPTATION_LOOKBACK)
        )
        scored_rows = scored_q.all()

        # --- Global adaptation (existing) ---
        if len(scored_rows) >= SCORE_ADAPTATION_MIN_SAMPLES:
            # Prefer improvement_score (wider variance: std≈0.53 vs 0.27 for
            # overall_score) so score-correlated adaptation has more signal to
            # work with. Fall back to overall_score when improvement_score is
            # absent (e.g. passthrough-only optimizations).
            scored_profiles = [
                (float(row[3] if row[3] is not None else row[0]), row[1])
                for row in scored_rows
            ]
            target_profiles = compute_score_correlated_target(scored_profiles)

            if target_profiles:
                prefs_svc_sc = PreferencesService()
                prefs_sc = prefs_svc_sc.load()
                phase_weights_sc = prefs_sc.get("phase_weights", {})
                adapted = False

                for phase_name_sc, target_pw in target_profiles.items():
                    current_dict_sc = phase_weights_sc.get(phase_name_sc, {})
                    current_pw_sc = PhaseWeights.from_dict(current_dict_sc)
                    updated_pw_sc = adapt_weights(current_pw_sc, target_pw)
                    new_dict = updated_pw_sc.to_dict()
                    if new_dict != phase_weights_sc.get(phase_name_sc):
                        phase_weights_sc[phase_name_sc] = new_dict
                        adapted = True

                if adapted:
                    prefs_svc_sc.patch({"phase_weights": phase_weights_sc})
                    logger.info(
                        "Score-correlated adaptation applied from %d scored optimizations",
                        len(scored_rows),
                    )

        # --- Per-cluster adaptation (new) ---
        # Group scored profiles by cluster, compute per-cluster target,
        # and store as learned_phase_weights in cluster_metadata.
        # This closes the learning loop: cluster members snapshot contextual
        # weights -> warm path discovers which profiles correlate with high
        # scores for THAT cluster -> cluster stores learned weights -> new
        # members inherit the cluster's proven profile.
        cluster_groups: dict[str, list[tuple[float, dict]]] = {}
        for row in scored_rows:
            cid = row[2]
            if cid:
                # row[3] = improvement_score (wider variance), row[0] = overall_score
                score = float(row[3] if row[3] is not None else row[0])
                cluster_groups.setdefault(cid, []).append((score, row[1]))

        clusters_adapted = 0
        for cid, members in cluster_groups.items():
            if len(members) < SCORE_ADAPTATION_MIN_SAMPLES:
                continue
            cluster_target = compute_score_correlated_target(members)
            if not cluster_target:
                continue
            cluster_q = await db.execute(
                select(PromptCluster).where(PromptCluster.id == cid)
            )
            cluster_node = cluster_q.scalar_one_or_none()
            if cluster_node:
                cluster_node.cluster_metadata = write_meta(
                    cluster_node.cluster_metadata,
                    learned_phase_weights={
                        phase: pw.to_dict() for phase, pw in cluster_target.items()
                    },
                )
                clusters_adapted += 1

        if clusters_adapted:
            await db.flush()
            logger.info(
                "Per-cluster weight adaptation applied to %d clusters",
                clusters_adapted,
            )
    except Exception as sc_exc:
        logger.warning("Score-correlated adaptation failed (non-fatal): %s", sc_exc)

    # --- Cross-cluster global_source_count computation ---
    # For each MetaPattern, count how many DISTINCT clusters contain a
    # semantically similar pattern (cosine >= 0.82). This enables
    # cross-cluster injection: patterns with high global_source_count
    # are universal techniques that benefit all prompts.
    try:
        all_patterns_q = await db.execute(
            select(MetaPattern)
            .join(PromptCluster, MetaPattern.cluster_id == PromptCluster.id)
            .where(
                MetaPattern.embedding.isnot(None),
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        all_patterns = list(all_patterns_q.scalars().all())

        if len(all_patterns) >= 2:
            # Build embedding matrix + cluster_id mapping
            pattern_embs: list[np.ndarray] = []
            pattern_cluster_ids: list[str] = []
            valid_patterns: list[MetaPattern] = []
            for mp in all_patterns:
                try:
                    emb = np.frombuffer(mp.embedding, dtype=np.float32).copy()
                    if emb.shape[0] == 384:
                        pattern_embs.append(emb)
                        pattern_cluster_ids.append(mp.cluster_id)
                        valid_patterns.append(mp)
                except (ValueError, TypeError) as _gsc_exc:
                    logger.warning(
                        "Corrupt pattern embedding in global_source_count, pattern=%s: %s",
                        mp.id, _gsc_exc,
                    )
                    continue

            if len(pattern_embs) >= 2:
                # Pairwise cosine similarity matrix
                mat = np.stack(pattern_embs, axis=0).astype(np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1.0, norms)
                mat_norm = mat / norms
                sim_matrix = mat_norm @ mat_norm.T

                from app.services.pipeline_constants import (
                    CROSS_CLUSTER_SIMILARITY_THRESHOLD,
                )

                for i, mp in enumerate(valid_patterns):
                    similar_mask = sim_matrix[i] >= CROSS_CLUSTER_SIMILARITY_THRESHOLD
                    similar_cluster_ids = {
                        pattern_cluster_ids[j]
                        for j in range(len(valid_patterns))
                        if similar_mask[j]
                    }
                    mp.global_source_count = len(similar_cluster_ids)

                await db.flush()
                logger.info(
                    "Computed global_source_count for %d meta-patterns",
                    len(valid_patterns),
                )
        elif len(all_patterns) == 1:
            all_patterns[0].global_source_count = 1
            await db.flush()
    except Exception as gsc_exc:
        logger.warning(
            "Global source count computation failed (non-fatal): %s", gsc_exc
        )

    return result


# ---------------------------------------------------------------------------
# Phase 5 — Discover
# ---------------------------------------------------------------------------


async def phase_discover(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> DiscoverResult:
    """Domain discovery, candidate detection, risk monitoring, and tree
    integrity repair.

    Orchestrates calls to the engine's domain management methods.
    """
    result = DiscoverResult()

    # --- Domain discovery (ADR-004) ---
    new_domains = await engine._propose_domains(db)
    if new_domains:
        result.domains_created = len(new_domains)
        logger.info(
            "Warm path discovered %d new domains: %s",
            len(new_domains), new_domains,
        )
        try:
            get_event_logger().log_decision(
                path="warm", op="discover", decision="domains_created",
                context={
                    "count": len(new_domains),
                    "domains": new_domains[:10],
                },
            )
        except RuntimeError:
            pass

    # --- Sub-domain discovery (intra-domain HDBSCAN) ---
    try:
        new_sub_domains = await engine._propose_sub_domains(db)
        if new_sub_domains:
            result.domains_created += len(new_sub_domains)
            logger.info(
                "Warm path discovered %d sub-domains: %s",
                len(new_sub_domains), new_sub_domains,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="discover", decision="sub_domains_created",
                    context={
                        "count": len(new_sub_domains),
                        "sub_domains": new_sub_domains[:10],
                    },
                )
            except RuntimeError:
                pass
    except Exception as sub_exc:
        logger.warning(
            "Sub-domain discovery failed (non-fatal): %s", sub_exc
        )

    # --- Candidate domain detection (near-threshold clusters) ---
    try:
        await engine._detect_domain_candidates(db)
    except Exception as cand_exc:
        logger.warning(
            "Candidate detection failed (non-fatal): %s", cand_exc
        )

    # --- Risk monitoring (ADR-004 Section 8B) ---
    try:
        await engine._monitor_general_health(db)
        stale_domains = await engine._check_signal_staleness(db)
        for stale_domain in stale_domains:
            await engine._refresh_domain_signals(db, stale_domain)
        await engine._suggest_domain_archival(db)
    except Exception as risk_exc:
        logger.warning(
            "Risk monitoring failed (non-fatal): %s", risk_exc
        )

    # --- Tree integrity check + auto-repair (ADR-004 Risk 5) ---
    try:
        violations = await engine.verify_domain_tree_integrity(db)
        if violations:
            repaired = await engine._repair_tree_violations(db, violations)
            logger.warning(
                "Tree integrity: %d violations, %d repaired",
                len(violations), repaired,
            )
    except Exception as integrity_exc:
        logger.warning(
            "Tree integrity check failed (non-fatal): %s", integrity_exc
        )

    return result


# ---------------------------------------------------------------------------
# Phase 6 — Audit
# ---------------------------------------------------------------------------


async def phase_audit(
    engine: TaxonomyEngine,
    db: AsyncSession,
    phase_results: list[PhaseResult],
    q_baseline: float | None,
) -> AuditResult:
    """Compute per-node separation, final Q_system, create snapshot, publish events.

    Fix #13: always increment ``engine._warm_path_age`` unconditionally.

    Note: Quality gating and deadlock breaking are handled per-phase by the
    orchestrator (warm_path.py). This function only computes the final metrics,
    creates the audit snapshot, and publishes events.
    """
    from app.services.taxonomy.snapshot import get_latest_snapshot

    result = AuditResult()

    # Gather aggregated stats from all phase results
    total_ops_attempted = sum(pr.ops_attempted for pr in phase_results)
    total_ops_accepted = sum(pr.ops_accepted for pr in phase_results)
    all_operations: list[dict] = []
    for pr in phase_results:
        all_operations.extend(pr.operations)

    # Compute per-node separation and Q_final
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    active_after = list(active_q.scalars().all())
    engine._update_per_node_separation(active_after)
    # Reuse last cold-path silhouette score. Warm path lacks the full member
    # embedding matrix needed for silhouette_score (centroids alone produce
    # unique labels which sklearn rejects). The cold path computes the valid
    # silhouette from HDBSCAN's blended embeddings and stores it on the engine.
    q_after = engine._compute_q_from_nodes(
        active_after, silhouette=engine._last_silhouette
    )
    result.q_final = q_after

    # Invalidate stats cache
    engine._invalidate_stats_cache()

    # Fix #13: always increment _warm_path_age unconditionally.
    engine._warm_path_age += 1

    # Create snapshot (skip on idle cycles with no changes)
    if total_ops_accepted > 0 or total_ops_attempted > 0 or engine._warm_path_age == 1:
        snap = await engine._create_warm_snapshot(
            db,
            q_system=q_after,
            operations=all_operations,
            ops_attempted=total_ops_attempted,
            ops_accepted=total_ops_accepted,
        )
        result.snapshot_id = snap.id
    else:
        latest = await get_latest_snapshot(db)
        result.snapshot_id = latest.id if latest else "no-snapshot"

    # Compute member-weighted q_health
    _q_health_val = None
    try:
        _q_health_result = engine._compute_q_health_from_nodes(
            active_after, silhouette=engine._last_silhouette,
        )
        _q_health_val = _q_health_result.q_health
    except Exception as _qh_exc:
        logger.warning("q_health computation failed in warm audit: %s", _qh_exc)

    # Log structured audit summary for observability
    try:
        get_event_logger().log_decision(
            path="warm", op="audit", decision="q_computed",
            context={
                "q_system": round(q_after, 4) if q_after else None,
                "q_baseline": round(q_baseline, 4) if q_baseline else None,
                "q_health": round(_q_health_val, 4) if _q_health_val is not None else None,
                "ops_attempted": total_ops_attempted,
                "ops_accepted": total_ops_accepted,
                "active_clusters": len(active_after),
                "warm_path_age": engine._warm_path_age,
                "snapshot_id": result.snapshot_id,
            },
        )
    except RuntimeError:
        pass

    # Publish taxonomy_changed when a snapshot was created
    snapshot_created = result.snapshot_id != "no-snapshot" and (
        total_ops_attempted > 0
        or result.deadlock_breaker_used
        or engine._warm_path_age == 1
    )
    if snapshot_created:
        try:
            from app.services.event_bus import event_bus

            event_bus.publish("taxonomy_changed", {
                "trigger": "warm_path",
                "operations_accepted": total_ops_accepted,
                "q_system": q_after,
            })
        except Exception as evt_exc:
            logger.warning(
                "Failed to publish taxonomy_changed (warm): %s", evt_exc
            )

    return result
