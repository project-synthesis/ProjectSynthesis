"""TaxonomyEngine — hot, warm, and cold path orchestration for the Evolutionary
Taxonomy Engine.

Spec Section 2.3, 2.5, 2.6, 3.5, 4.2, 6.4, 7.3, 7.5, 8.5.

Responsibilities:
  - process_optimization: embed + assign cluster + extract meta-patterns (hot path)
  - run_warm_path: periodic re-clustering with lifecycle (split > emerge > merge > retire)
  - run_cold_path: full HDBSCAN + UMAP refit (the "defrag" operation)
  - map_domain / match_prompt: delegated to matching.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR, settings
from app.models import (
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PromptCluster,
    TaxonomySnapshot,
)
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader
from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES, _utcnow
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.cold_path import ColdPathResult, execute_cold_path
from app.services.taxonomy.embedding_index import EmbeddingIndex
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.family_ops import (
    assign_cluster,
    build_breadcrumb,
    extract_meta_patterns,
    merge_meta_pattern,
)
from app.services.taxonomy.matching import (
    PatternMatch,
    TaxonomyMapping,
)
from app.services.taxonomy.matching import (
    map_domain as _map_domain,
)
from app.services.taxonomy.matching import (
    match_prompt as _match_prompt,
)
from app.services.taxonomy.sparkline import compute_sparkline_data
from app.services.taxonomy.sub_domain_readiness import (
    SubDomainMatchResult,
    compute_qualifier_cascade,
    match_opt_to_sub_domain_vocab,
)
from app.services.taxonomy.warm_path import WarmPathResult, execute_warm_path
from app.utils.text_cleanup import is_low_quality_label, parse_domain, validate_intent_label

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary quality metric constants (post-generation observability)
# ---------------------------------------------------------------------------

_VOCAB_QUALITY_POOR_THRESHOLD = 0.1  # quality below this triggers WARNING log (poor)
_VOCAB_QUALITY_ACCEPTABLE_THRESHOLD = 0.3  # quality in [poor, this) triggers WARNING log (acceptable)
_VOCAB_OVERLAP_REPORT_THRESHOLD = 0.7  # include overlapping_pair in event only when pairwise > this
_VOCAB_ZERO_NORM_EPS = 1e-9  # zero-norm guard for embeddings
_VOCAB_QUALITY_SCORES_MAXLEN = 500  # rolling window for engine._vocab_quality_scores


# ---------------------------------------------------------------------------
# ADR-005: Adaptive scheduler (Phase 1 measurement + Phase 3A decisions)
# ---------------------------------------------------------------------------


@dataclass
class WarmCycleMeasurement:
    """Single warm cycle measurement for adaptive scheduling."""
    dirty_count: int
    duration_ms: int


@dataclass
class SchedulerDecision:
    """Result of adaptive scheduler mode decision."""
    mode: str  # "all_dirty" | "round_robin"
    project_id: str | None = None
    scoped_dirty_ids: set[str] | None = None
    project_budgets: dict[str, int] | None = None  # per-project cluster budgets

    @property
    def is_round_robin(self) -> bool:
        return self.mode == "round_robin"


class AdaptiveScheduler:
    """Self-tuning warm path scheduler (ADR-005).

    Phase 1: measurement only (always all-dirty mode).
    Phase 3A: per-project budget allocation when dirty count exceeds
    boundary. Each project gets a proportional share of the boundary
    with a guaranteed minimum floor (_MIN_QUOTA), ensuring all projects
    make progress every cycle.
    """

    _WINDOW_SIZE = 10
    _BOOTSTRAP_TARGET_MS = 10_000  # 10s default until enough data
    _BOOTSTRAP_BOUNDARY: int = 20  # dirty count fallback during bootstrap
    _STARVATION_LIMIT: int = 3     # max consecutive skipped cycles
    _MIN_QUOTA: int = 3            # minimum clusters per project in budget mode

    def __init__(self) -> None:
        self._window: list[WarmCycleMeasurement] = []
        self._target_cycle_ms: int = self._BOOTSTRAP_TARGET_MS
        self._skip_counts: dict[str, int] = {}
        self._last_mode: str = "all_dirty"
        self._last_project_id: str | None = None
        self._last_dirty_by_project: dict[str, set[str]] | None = None
        self._last_project_budgets: dict[str, int] | None = None

    @property
    def target_cycle_ms(self) -> int:
        return self._target_cycle_ms

    def record(self, dirty_count: int, duration_ms: int) -> None:
        """Record a warm cycle measurement and update target."""
        self._window.append(WarmCycleMeasurement(dirty_count, duration_ms))
        if len(self._window) > self._WINDOW_SIZE:
            self._window = self._window[-self._WINDOW_SIZE:]

        # Update target after bootstrap period
        if len(self._window) >= self._WINDOW_SIZE:
            durations = [m.duration_ms for m in self._window]
            quantiles = statistics.quantiles(durations, n=4)
            self._target_cycle_ms = int(quantiles[2])  # 75th percentile

    def _compute_boundary(self) -> int:
        """Dirty count at which predicted duration equals target.

        Uses simple linear regression on the rolling window.
        Returns bootstrap fallback if insufficient data.
        """
        if len(self._window) < self._WINDOW_SIZE:
            return self._BOOTSTRAP_BOUNDARY

        xs = [m.dirty_count for m in self._window]
        ys = [m.duration_ms for m in self._window]
        n = len(xs)
        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_xx = sum(x * x for x in xs)

        denom = n * sum_xx - sum_x * sum_x
        if abs(denom) < 1e-9:
            return self._BOOTSTRAP_BOUNDARY  # degenerate: all same dirty count

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        if slope <= 0:
            return 999  # duration doesn't grow with dirty count

        boundary = (self._target_cycle_ms - intercept) / slope
        result = max(1, int(boundary))

        if result == 1:
            logger.warning(
                "AdaptiveScheduler: boundary clamped to 1 "
                "(slope=%.2f intercept=%.0f target=%d)",
                slope, intercept, self._target_cycle_ms,
            )
        return result

    def _clear_all_dirty_state(self) -> None:
        """Reset scheduler state when entering all-dirty mode."""
        self._skip_counts.clear()
        self._last_project_id = None
        self._last_dirty_by_project = None
        self._last_project_budgets = None

    def decide_mode(
        self,
        dirty_ids: set[str] | None,
        dirty_by_project: dict[str, set[str]] | None = None,
    ) -> SchedulerDecision:
        """Decide scheduling mode for this warm cycle."""
        if dirty_ids is None:
            self._last_mode = "all_dirty"
            self._clear_all_dirty_state()
            return SchedulerDecision("all_dirty")

        boundary = self._compute_boundary()
        if len(dirty_ids) <= boundary:
            self._last_mode = "all_dirty"
            self._clear_all_dirty_state()
            return SchedulerDecision("all_dirty")

        if not dirty_by_project:
            self._last_mode = "all_dirty"
            self._clear_all_dirty_state()
            return SchedulerDecision("all_dirty")

        # Per-project budget allocation
        budgets, scoped = self._allocate_budgets(dirty_by_project, boundary)
        self._last_mode = "round_robin"
        self._last_project_id = None
        self._last_dirty_by_project = dirty_by_project
        self._last_project_budgets = budgets
        return SchedulerDecision("round_robin", None, scoped, budgets)

    def _allocate_budgets(
        self,
        dirty_by_project: dict[str, set[str]],
        boundary: int,
    ) -> tuple[dict[str, int], set[str]]:
        """Allocate per-project budgets proportionally within boundary.

        Each project gets a share proportional to its dirty count, with a
        guaranteed minimum floor (_MIN_QUOTA) so small projects always make
        progress. Starved projects (skip_count >= _STARVATION_LIMIT) get
        boosted quotas stolen from the largest non-starved project.

        Returns:
            (project_budgets, scoped_dirty_ids) where scoped_dirty_ids is
            the union of budget-limited subsets from each project.
        """
        total_dirty = sum(len(cids) for cids in dirty_by_project.values())
        if total_dirty == 0:
            return {}, set()

        # Step 1: Proportional raw budgets with MIN_QUOTA floor
        budgets: dict[str, int] = {}
        for pid, cids in dirty_by_project.items():
            raw = round(len(cids) / total_dirty * boundary)
            budgets[pid] = min(max(self._MIN_QUOTA, raw), len(cids))

        # Step 2: Starvation boost — starved projects steal from largest donor
        starved_pids = [
            pid for pid in dirty_by_project
            if self._skip_counts.get(pid, 0) >= self._STARVATION_LIMIT
        ]
        if starved_pids:
            non_starved = [
                pid for pid in dirty_by_project if pid not in starved_pids
            ]
            if non_starved:
                for spid in starved_pids:
                    boost = max(0, self._MIN_QUOTA - budgets[spid])
                    if boost > 0:
                        # Re-find donor each iteration (budget may have changed)
                        donor = max(non_starved, key=lambda p: budgets[p])
                        steal = min(boost, budgets[donor] - self._MIN_QUOTA)
                        if steal > 0:
                            budgets[spid] += steal
                            budgets[donor] -= steal

        # Step 3: Build scoped_dirty_ids from budget-limited subsets
        scoped: set[str] = set()
        for pid, cids in dirty_by_project.items():
            scoped.update(list(cids)[:budgets[pid]])

        # Step 4: Update starvation counters
        for pid in dirty_by_project:
            if budgets[pid] > 0:
                self._skip_counts[pid] = 0
            else:
                self._skip_counts[pid] = self._skip_counts.get(pid, 0) + 1

        # Step 5: Clean up stale entries (handles project unlinking)
        stale = [pid for pid in self._skip_counts if pid not in dirty_by_project]
        for pid in stale:
            del self._skip_counts[pid]

        # Warn if floors exceeded boundary
        total_budget = sum(budgets.values())
        if total_budget > boundary:
            logger.warning(
                "AdaptiveScheduler: per-project floors (%d) exceed boundary (%d); "
                "cycle may exceed target time",
                total_budget, boundary,
            )

        return budgets, scoped

    def snapshot(self) -> dict:
        """Return scheduler state for logging/observability."""
        counters = dict(self._skip_counts)
        return {
            "target_cycle_ms": self._target_cycle_ms,
            "window_size": len(self._window),
            "mode": self._last_mode,
            "bootstrapping": len(self._window) < self._WINDOW_SIZE,
            "boundary": self._compute_boundary(),
            "skip_counts": counters,
            "starvation_counters": counters,
            "last_project_id": self._last_project_id,
            "dirty_by_project_counts": {
                pid: len(cids)
                for pid, cids in (self._last_dirty_by_project or {}).items()
            },
            "project_budgets": dict(self._last_project_budgets) if self._last_project_budgets else None,
        }


# ---------------------------------------------------------------------------
# TaxonomyEngine
# ---------------------------------------------------------------------------


class TaxonomyEngine:
    """Orchestrates the hot path for the Evolutionary Taxonomy Engine.

    Args:
        embedding_service: EmbeddingService instance (or mock in tests).
        provider: LLM provider for Haiku calls. None disables LLM steps.
            Ignored when *provider_resolver* is set.
        provider_resolver: Callable returning the current LLM provider.
            When set, ``_provider`` resolves lazily on every access so
            hot-reloaded providers (e.g. API key change) are picked up
            automatically.  Falls back to *provider* if not given.
    """

    _STATS_CACHE_TTL: float = 30.0  # seconds — stats endpoint TTL

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        provider: LLMProvider | None = None,
        provider_resolver: Callable[[], LLMProvider | None] | None = None,
    ) -> None:
        self._embedding = embedding_service or EmbeddingService()
        self._provider_direct: LLMProvider | None = provider
        self._provider_resolver = provider_resolver
        self._prompt_loader = PromptLoader(PROMPTS_DIR)
        self._embedding_index = EmbeddingIndex(dim=384)
        # Post-generation vocabulary quality scores — observability only.
        # Populated by vocab generation pass; read by health endpoint (Task 5).
        self._vocab_quality_scores: deque[float] = deque(maxlen=_VOCAB_QUALITY_SCORES_MAXLEN)
        from app.services.taxonomy.transformation_index import TransformationIndex
        self._transformation_index = TransformationIndex(dim=384)
        from app.services.taxonomy.qualifier_index import QualifierIndex
        self._qualifier_index = QualifierIndex(dim=384)
        from app.services.taxonomy.optimized_index import OptimizedEmbeddingIndex
        self._optimized_index = OptimizedEmbeddingIndex(dim=384)
        # Lock gates concurrent hot-path writes to shared centroid state.
        self._lock: asyncio.Lock = asyncio.Lock()
        # Separate lock for warm/cold path deduplication (Spec Section 2.6).
        self._warm_path_lock: asyncio.Lock = asyncio.Lock()
        # Per-phase rejection counters (Spec Section 2.5) — used by warm_path orchestrator.
        self._phase_rejection_counters: dict[str, int] = {}
        # Warm-path age counter for adaptive epsilon tolerance.
        self._warm_path_age: int = 0
        # Set by deadlock breaker — caller should schedule cold path.
        self._cold_path_needed: bool = False
        # Last silhouette score from cold path — reused by warm path since
        # warm path lacks the full embedding matrix needed for silhouette.
        self._last_silhouette: float = 0.0
        # Stats cache — monotonic TTL, invalidated on warm/cold path completion.
        # ADR-005 B6: keyed per project_id (None = global/unscoped) so that
        # ``/api/clusters/stats?project_id=X`` views don't pollute the global
        # cache and vice versa.
        self._stats_cache: dict[str | None, dict] = {}
        self._stats_cache_time: dict[str | None, float] = {}
        # ADR-005: Dirty-set tracking for warm path optimization.
        # Hot path marks clusters as dirty when members change.
        # Warm path snapshots and clears at cycle start.
        self._dirty_set: dict[str, str | None] = {}  # cluster_id -> project_id (Phase 3A)
        # ADR-005: Adaptive scheduler — rolling window of warm cycle timings.
        self._scheduler = AdaptiveScheduler()
        # ADR-005 Phase 2A: project resolution caches
        self._cluster_project_cache: dict[str, str] = {}  # cluster_id -> project_id
        self._legacy_project_id: str | None = None  # cached Legacy project node ID
        self._last_global_pattern_check: float = 0.0  # monotonic, Phase 2B
        # Maintenance retry flag — set True when Phase 5 (discover) fails
        # with a transient error.  Causes the next idle warm cycle to run
        # maintenance phases regardless of the periodic cadence gate.
        self._maintenance_pending: bool = False
        # Date of last readiness-history prune (UTC).  Guards the daily
        # idempotent prune inside warm-path Phase 5 so it runs at most
        # once per process per UTC day.  ``None`` = never pruned.
        self._readiness_pruned_on: date | None = None
        # Injection effectiveness — cached by warm path Phase 4, read by health endpoint.
        self._injection_effectiveness: dict | None = None
        # Domain lifecycle stats — read by health endpoint
        self._domain_lifecycle_stats: dict = {
            "domains_reevaluated": 0,
            "domains_dissolved": 0,
            "seeds_remaining": 0,
            "dissolution_blocked": 0,
            "last_domain_reeval": None,
        }

    def mark_dirty(self, cluster_id: str, project_id: str | None = None) -> None:
        """Mark a cluster as needing warm-path processing."""
        self._dirty_set[cluster_id] = project_id

    def snapshot_dirty_set_with_projects(self) -> tuple[set[str], dict[str, set[str]]]:
        """Snapshot dirty set with per-project breakdown.

        Returns (all_ids, per_project_ids) where per_project_ids maps
        project_id -> set of cluster_ids. Clusters with project_id=None
        are grouped under "legacy".
        Safe under asyncio cooperative scheduling (no await between read and clear).
        """
        snapshot = dict(self._dirty_set)
        self._dirty_set.clear()
        all_ids = set(snapshot.keys())
        by_project: dict[str, set[str]] = {}
        for cid, pid in snapshot.items():
            by_project.setdefault(pid or "legacy", set()).add(cid)
        return all_ids, by_project

    def snapshot_dirty_set(self) -> set[str]:
        """Snapshot and clear. Returns cluster IDs only (no project breakdown).

        Backward-compatible wrapper — delegates to snapshot_dirty_set_with_projects.
        """
        all_ids, _ = self.snapshot_dirty_set_with_projects()
        return all_ids

    def is_first_warm_cycle(self) -> bool:
        """True if this is the first warm cycle after server restart.

        The first cycle runs a full scan to catch changes from before restart.
        """
        return self._warm_path_age == 0

    @property
    def _provider(self) -> LLMProvider | None:
        """Resolve the current LLM provider, preferring the live resolver."""
        if self._provider_resolver is not None:
            return self._provider_resolver()
        return self._provider_direct

    @property
    def embedding_index(self) -> EmbeddingIndex:
        """In-memory embedding search index for PromptCluster centroids."""
        return self._embedding_index

    @property
    def transformation_index(self):
        """In-memory transformation vector search index."""
        return self._transformation_index

    @property
    def qualifier_index(self):
        """In-memory qualifier vector search index."""
        return self._qualifier_index

    # ------------------------------------------------------------------
    # Index cache management
    # ------------------------------------------------------------------

    async def load_index_caches(self, data_dir: Path) -> None:
        """Load TransformationIndex and OptimizedEmbeddingIndex from disk cache.

        Called at startup to avoid cold-start degradation of composite fusion
        Signals 2 (transformation) and 3 (output). EmbeddingIndex has its own
        warm-load logic in main.py with staleness validation.
        """
        # TransformationIndex
        try:
            ti_loaded = await self._transformation_index.load_cache(
                data_dir / "transformation_index.pkl"
            )
            if ti_loaded:
                logger.info(
                    "TransformationIndex warm-loaded from cache: %d vectors",
                    self._transformation_index.size,
                )
            else:
                logger.info("TransformationIndex cache not available — will populate via hot path")
        except Exception as ti_exc:
            logger.warning("TransformationIndex warm-load failed (non-fatal): %s", ti_exc)

        # QualifierIndex
        try:
            qi_loaded = await self._qualifier_index.load_cache(
                data_dir / "qualifier_index.pkl"
            )
            if qi_loaded:
                logger.info(
                    "QualifierIndex warm-loaded from cache: %d vectors",
                    self._qualifier_index.size,
                )
            else:
                logger.info("QualifierIndex cache not available — will populate via hot path")
        except Exception as qi_exc:
            logger.warning("QualifierIndex warm-load failed (non-fatal): %s", qi_exc)

        # OptimizedEmbeddingIndex
        try:
            oi_loaded = await self._optimized_index.load_cache(
                data_dir / "optimized_index.pkl"
            )
            if oi_loaded:
                logger.info(
                    "OptimizedEmbeddingIndex warm-loaded from cache: %d vectors",
                    self._optimized_index.size,
                )
            else:
                logger.info("OptimizedEmbeddingIndex cache not available — will populate via hot path")
        except Exception as oi_exc:
            logger.warning("OptimizedEmbeddingIndex warm-load failed (non-fatal): %s", oi_exc)

    # ------------------------------------------------------------------
    # Public hot-path entry point
    # ------------------------------------------------------------------

    async def process_optimization(
        self,
        optimization_id: str,
        db: AsyncSession | None = None,
        repo_full_name: str | None = None,  # ADR-005 Phase 2A
        *,
        write_queue: Any = None,
    ) -> None:
        """Full extraction pipeline for a single completed optimization.

        Steps:
          1. Load optimization — skip if not 'completed'.
          2. Idempotency check via OptimizationPattern 'source' record.
          3. Embed raw_prompt.
          4. Find or create PromptCluster via _assign_cluster().
          5. Extract meta-patterns via _extract_meta_patterns().
          6. Merge meta-patterns via _merge_meta_pattern().
          7. Write OptimizationPattern join record and commit.

        Args:
            optimization_id: PK of the Optimization row to process.
            db: Async SQLAlchemy session (legacy form). Pass ``None``
                when threading ``write_queue``.
            repo_full_name: Optional repo (e.g. "owner/repo") to resolve project.
                Falls back to ``opt.repo_full_name`` when *None*.
            write_queue: Optional ``WriteQueue`` instance. When supplied,
                the entire extraction body runs inside a single ``submit()``
                callback labelled ``hot_path_assign_cluster`` so writes
                serialize against every other backend writer. Detection is
                ``write_queue is not None``; legacy callers continue to
                pass ``db=`` directly.
        """
        if write_queue is not None:
            async def _do_extract(write_db: AsyncSession) -> None:
                await self._process_optimization_impl(
                    optimization_id,
                    write_db,
                    repo_full_name=repo_full_name,
                )

            await write_queue.submit(
                _do_extract, operation_label="hot_path_assign_cluster",
            )
            return

        if db is None:
            raise TypeError(
                "process_optimization requires either write_queue= or "
                "a positional AsyncSession (legacy form).",
            )
        await self._process_optimization_impl(
            optimization_id, db, repo_full_name=repo_full_name,
        )

    async def _process_optimization_impl(
        self,
        optimization_id: str,
        db: AsyncSession,
        *,
        repo_full_name: str | None = None,
    ) -> None:
        """Internal: hot-path extraction body. See ``process_optimization``."""
        try:
            result = await db.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()

            if not opt or opt.status != "completed":
                logger.debug(
                    "Skipping taxonomy extraction for %s (status=%s)",
                    optimization_id,
                    opt.status if opt else "not_found",
                )
                return

            # ADR-005 Phase 2A: resolve project_id from repo
            from app.services.project_service import resolve_project_id
            if self._legacy_project_id is None:
                _legacy_q = await db.execute(
                    select(PromptCluster).where(
                        PromptCluster.state == "project"
                    ).limit(1)
                )
                _legacy = _legacy_q.scalar_one_or_none()
                if _legacy:
                    self._legacy_project_id = _legacy.id

            project_id = await resolve_project_id(
                db,
                repo_full_name or opt.repo_full_name,
                self._legacy_project_id,
            )
            if project_id:
                opt.project_id = project_id

            # Idempotency: skip if a 'source' OptimizationPattern already exists.
            # Use .first() to tolerate duplicate source records from
            # historical race conditions (scalar_one_or_none raises
            # MultipleResultsFound even with .limit(1) in some SQLAlchemy
            # edge cases).
            existing = await db.execute(
                select(OptimizationPattern).where(
                    OptimizationPattern.optimization_id == optimization_id,
                    OptimizationPattern.relationship == "source",
                )
            )
            if existing.scalars().first():
                logger.debug(
                    "Skipping taxonomy extraction for %s (already processed)",
                    optimization_id,
                )
                return

            # 1. Embed raw_prompt
            embedding = await self._embedding.aembed_single(opt.raw_prompt)
            opt.embedding = embedding.astype(np.float32).tobytes()

            # 1b. Embed optimized_prompt (Phase 1: multi-embedding)
            if opt.optimized_prompt:
                optimized_emb = await self._embedding.aembed_single(opt.optimized_prompt)
                opt.optimized_embedding = optimized_emb.astype(np.float32).tobytes()

                # 1c. Compute transformation vector: direction of improvement
                transform = optimized_emb - embedding  # raw_emb already computed
                t_norm = np.linalg.norm(transform)
                if t_norm > 1e-9:
                    transform = transform / t_norm
                opt.transformation_embedding = transform.astype(np.float32).tobytes()

            # 1d. Compute qualifier embedding from organic vocabulary
            qualifier_emb = None
            try:
                domain_primary_raw, domain_qualifier = parse_domain(opt.domain_raw or "")
                if domain_qualifier:
                    from app.services.domain_signal_loader import get_signal_loader
                    loader = get_signal_loader()
                    if loader:
                        qualifiers = loader.get_qualifiers(domain_primary_raw)
                        keywords = qualifiers.get(domain_qualifier)
                        if keywords:
                            cache_key = "|".join(sorted(keywords))
                            cached = loader.get_cached_qualifier_embedding(cache_key)
                            if cached is not None:
                                qualifier_emb = cached
                            else:
                                qualifier_text = " ".join(keywords)
                                qualifier_emb = await self._embedding.aembed_single(qualifier_text)
                                loader.cache_qualifier_embedding(cache_key, qualifier_emb)
                            opt.qualifier_embedding = qualifier_emb.astype(np.float32).tobytes()
                            loader._qualifier_embeddings_generated += 1
                        else:
                            loader._qualifier_embeddings_skipped += 1
                    # else: no loader — cold start, skip silently
                # else: no qualifier in domain_raw, skip silently
            except Exception as qe:
                logger.warning("Qualifier embedding failed (non-fatal): %s", qe)

            # 2. Find or create PromptCluster
            # Use the RESOLVED domain (opt.domain) for cluster assignment — this
            # maps to a known domain node label. The raw analyzer output (domain_raw)
            # is preserved on the Optimization record for warm-path discovery.
            domain_primary, _ = parse_domain(opt.domain or "general")
            async with self._lock:
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=opt.intent_label or "general",
                    domain=domain_primary,
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                    embedding_index=self._embedding_index,
                    project_id=project_id,
                )

            # Write back the definitive cluster assignment.
            # The pipeline's Phase 1.5 domain mapping sets a preliminary
            # cluster_id (often NULL); this is the canonical assignment.
            old_cluster_id = opt.cluster_id
            if old_cluster_id and old_cluster_id != cluster.id:
                old_cluster_q = await db.execute(
                    select(PromptCluster).where(PromptCluster.id == old_cluster_id)
                )
                old_cluster = old_cluster_q.scalar_one_or_none()
                if old_cluster is None:
                    logger.warning(
                        "Old cluster %s not found during reassignment of opt %s — "
                        "member_count may be inconsistent",
                        old_cluster_id, optimization_id,
                    )
                elif old_cluster.state != "archived":
                    old_cluster.member_count = max(0, (old_cluster.member_count or 1) - 1)
                    if opt.overall_score is not None and (old_cluster.scored_count or 0) > 0:
                        old_cluster.scored_count = max(0, old_cluster.scored_count - 1)
                    # Mark old cluster pattern-stale — it lost a member
                    old_cluster.cluster_metadata = write_meta(
                        old_cluster.cluster_metadata, pattern_stale=True,
                    )
                    self.mark_dirty(old_cluster.id, project_id=project_id)  # ADR-005: old cluster lost a member
                    logger.info(
                        "Decremented old cluster '%s' member_count to %d "
                        "(reassigned to '%s')",
                        old_cluster.label, old_cluster.member_count,
                        cluster.label,
                    )
            opt.cluster_id = cluster.id
            self.mark_dirty(cluster.id, project_id=project_id)  # ADR-005: new cluster gained a member

            # ADR-005 Phase 2A: update cluster->project cache + tag embedding index
            if project_id:
                self._cluster_project_cache[cluster.id] = project_id
                # Re-upsert centroid with project_id so the embedding index
                # knows which project this cluster belongs to.  assign_cluster
                # already wrote the centroid without project_id; this adds the tag.
                if cluster.centroid_embedding:
                    _centroid = np.frombuffer(cluster.centroid_embedding, dtype=np.float32)
                    await self._embedding_index.upsert(
                        cluster.id, _centroid, project_id=project_id,
                    )

            # ----- Intent label hardening (Tier 2) -----
            from app.services.pipeline_constants import MAX_INTENT_LABEL_LENGTH

            # 2a: Upgrade generic labels from cluster label
            old_label = opt.intent_label or "general"
            if is_low_quality_label(old_label):
                # Try improving from raw_prompt first
                upgraded = validate_intent_label(old_label, opt.raw_prompt)
                if upgraded != old_label:
                    opt.intent_label = upgraded[:MAX_INTENT_LABEL_LENGTH]
                else:
                    # raw_prompt didn't help — try adopting the cluster label
                    cluster_label = cluster.label or ""
                    if cluster_label and not is_low_quality_label(cluster_label):
                        opt.intent_label = cluster_label[:MAX_INTENT_LABEL_LENGTH]
                        logger.info(
                            "Upgraded generic intent_label '%s' → '%s' from cluster '%s'",
                            old_label, opt.intent_label, cluster.id,
                        )

            # 2b: Deduplicate exact-match labels within cluster
            # Previously appended a parenthetical word from the prompt (e.g.
            # "(Implement)"), but these looked like status tags and added no
            # semantic value. Duplicate labels within a cluster are acceptable —
            # the cluster provides grouping context.

            # Update stale OP records to match new cluster assignment.
            # Prevents OP↔Optimization.cluster_id mismatch after reassignment.
            if old_cluster_id and old_cluster_id != cluster.id:
                from sqlalchemy import update as sa_update
                await db.execute(
                    sa_update(OptimizationPattern)
                    .where(
                        OptimizationPattern.optimization_id == opt.id,
                        OptimizationPattern.relationship == "source",
                    )
                    .values(cluster_id=cluster.id)
                )

            # Snapshot contextual phase weights — derived from task type + cluster
            # learning, NOT global preferences.  Different task types produce
            # different profiles, breaking the bootstrap fixed point so that
            # compute_score_correlated_target() has real variance to learn from.
            if opt.phase_weights_json is None:
                try:
                    from app.services.taxonomy.fusion import resolve_contextual_weights

                    cluster_meta = read_meta(cluster.cluster_metadata)
                    opt.phase_weights_json = resolve_contextual_weights(
                        task_type=opt.task_type or "general",
                        cluster_learned_weights=cluster_meta.get("learned_phase_weights"),
                    )
                except Exception as pw_exc:
                    logger.debug("Phase weights snapshot failed for opt %s: %s", opt.id, pw_exc)

            # Update TransformationIndex with running mean of cluster transformations
            if opt.transformation_embedding:
                try:
                    transform_vec = np.frombuffer(
                        opt.transformation_embedding, dtype=np.float32
                    )
                    existing_vec = self._transformation_index.get_vector(cluster.id)
                    if existing_vec is not None:
                        # Weighted running mean: blend existing mean with new sample
                        # L2 normalization is handled by upsert()
                        member_ct = max(1, (cluster.member_count or 1) - 1)
                        blended = (existing_vec * member_ct + transform_vec) / (member_ct + 1)
                        await self._transformation_index.upsert(cluster.id, blended)
                    else:
                        await self._transformation_index.upsert(
                            cluster.id, transform_vec
                        )
                except Exception as ti_exc:
                    logger.warning(
                        "TransformationIndex upsert failed for cluster %s: %s",
                        cluster.id, ti_exc,
                    )

            # Update OptimizedEmbeddingIndex with running mean of cluster output embeddings
            if opt.optimized_embedding:
                try:
                    optimized_vec = np.frombuffer(
                        opt.optimized_embedding, dtype=np.float32
                    )
                    existing_opt = self._optimized_index.get_vector(cluster.id)
                    if existing_opt is not None:
                        member_ct = max(1, (cluster.member_count or 1) - 1)
                        blended = (existing_opt * member_ct + optimized_vec) / (member_ct + 1)
                        await self._optimized_index.upsert(cluster.id, blended)
                    else:
                        await self._optimized_index.upsert(
                            cluster.id, optimized_vec
                        )
                except Exception as oi_exc:
                    logger.warning(
                        "OptimizedEmbeddingIndex upsert failed for cluster %s: %s",
                        cluster.id, oi_exc,
                    )

            # 3d. Update QualifierIndex
            if qualifier_emb is not None:
                try:
                    await self._qualifier_index.upsert(cluster.id, qualifier_emb)
                except Exception as qi_exc:
                    logger.warning("QualifierIndex upsert failed: %s", qi_exc)

            # 3. Extract meta-patterns
            meta_texts = await extract_meta_patterns(
                opt, db, self._provider, self._prompt_loader,
            )

            # 4. Merge meta-patterns and update freshness flag
            for text in meta_texts:
                await merge_meta_pattern(db, cluster.id, text, self._embedding)
            # If extraction produced results, patterns are fresh for this member.
            # If empty (provider unavailable), mark stale so Phase 4 catches it.
            cluster.cluster_metadata = write_meta(
                cluster.cluster_metadata,
                pattern_stale=len(meta_texts) == 0,
            )

            # 5. Write join record
            join = OptimizationPattern(
                optimization_id=opt.id,
                cluster_id=cluster.id,
                relationship="source",
            )
            db.add(join)

            await db.commit()
            logger.debug(
                "Taxonomy extraction complete: opt=%s cluster='%s' meta_patterns=%d",
                optimization_id,
                cluster.label,
                len(meta_texts),
            )

            # 6. Publish taxonomy_changed event (Spec Section 6.5)
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("taxonomy_changed", {
                    "optimization_id": optimization_id,
                    "cluster_id": cluster.id,
                    "cluster_label": cluster.label,
                    "meta_patterns_added": len(meta_texts),
                })
            except Exception as evt_exc:
                logger.warning("Failed to publish taxonomy_changed: %s", evt_exc)

            try:
                get_event_logger().log_decision(
                    path="hot", op="extract", decision="complete",
                    cluster_id=cluster.id,
                    optimization_id=optimization_id,
                    context={
                        "cluster_label": cluster.label,
                        "meta_patterns_added": len(meta_texts),
                        "reassigned_from": old_cluster_id if old_cluster_id and old_cluster_id != cluster.id else None,
                    },
                )
            except RuntimeError:
                pass

        except Exception as exc:
            logger.error(
                "Taxonomy process_optimization failed for %s: %s",
                optimization_id,
                exc,
                exc_info=True,
            )
            try:
                get_event_logger().log_decision(
                    path="hot", op="error", decision="failed",
                    optimization_id=optimization_id,
                    context={
                        "source": "process_optimization",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                        "recovery": "skipped",
                        "stack_trace": traceback.format_exc()[:500],
                    },
                )
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Pattern matching — delegated to matching.py
    # ------------------------------------------------------------------

    async def match_prompt(
        self, prompt_text: str, db: AsyncSession,
    ) -> PatternMatch | None:
        """Hierarchical pattern matching for on-paste suggestion.

        Delegates to :func:`matching.match_prompt`.
        """
        return await _match_prompt(prompt_text, db, self._embedding)

    # ------------------------------------------------------------------
    # Warm path (Spec Section 2.3, 2.5, 2.6, 3.5)
    # ------------------------------------------------------------------

    async def run_warm_path(
        self,
        session_factory: Callable | None = None,
        *,
        write_queue: Any = None,
    ) -> WarmPathResult | None:
        """Periodic re-clustering with lifecycle operations.

        Checks ``_warm_path_lock`` for deduplication first — if already held
        by another coroutine, returns None (skip).  Otherwise acquires the
        lock and delegates to :func:`execute_warm_path` which runs 7
        sequential phases, each with its own database session.

        **Cycle 6 dual-typed dispatch (v0.4.13):** when ``write_queue`` is
        threaded by the orchestrator (via ``app.state.write_queue`` once
        cycle 9 wires the lifespan), every commit routes through the
        single-writer queue worker. Until then, the legacy
        ``session_factory`` path is used unchanged.

        Args:
            session_factory: Async context manager factory yielding fresh
                ``AsyncSession`` instances (legacy). Pass ``None`` when
                threading ``write_queue``.
            write_queue: ``WriteQueue`` instance from
                ``app.state.write_queue`` (canonical cycle 6+ path).
                Wired by ``main.py`` lifespan in a future cycle.

        Returns:
            WarmPathResult on success, or None if skipped due to lock.
        """
        # Lock deduplication (Spec Section 2.6).
        # In asyncio single-thread model, locked() + acquire is safe —
        # no preemption between check and context manager entry.
        if self._warm_path_lock.locked():
            logger.debug("Warm path skipped — lock already held")
            try:
                get_event_logger().log_decision(
                    path="warm", op="skip", decision="lock_held",
                    context={"reason": "warm_path_lock already held"},
                )
            except RuntimeError:
                pass
            return None

        async with self._warm_path_lock:
            try:
                return await execute_warm_path(
                    self, session_factory, write_queue=write_queue,
                )
            except Exception as exc:
                logger.error("Warm path failed: %s", exc, exc_info=True)
                # v0.4.12 P0c: advance _warm_path_age on failure so the
                # next cycle does NOT re-run is_first_warm_cycle()'s
                # full-scan path. Pre-fix the age was only incremented
                # by phase_audit() at the END of a successful cycle --
                # so a cycle that failed early (e.g. autoflush hits
                # "database is locked" during Phase 0's SELECT, session
                # ends in PendingRollbackError) left age at 0, which
                # made is_first_warm_cycle() stay True forever, which
                # forced every subsequent cycle to do the full Phase 0
                # scan that triggered the same failure -- a Groundhog
                # Day loop visible in the live log as ``Warm path
                # cycle: dirty_ids=all (first_cycle=True, ...)`` ->
                # ``Warm path failed: PendingRollbackError`` repeating
                # every cadence interval. Once age advances past 0, the
                # next cycle uses ``snapshot_dirty_set`` which empties
                # the dirty_set and skips when nothing changed -- a
                # natural recovery path.
                self._warm_path_age += 1
                # Return a minimal result so callers don't break.
                # Cycle 6: queue path threads via write_queue.submit; legacy
                # path opens a fresh session_factory session unchanged.
                async def _do_error_snapshot(db: AsyncSession) -> str:
                    snap = await self._create_warm_snapshot(
                        db, q_system=0.0, operations=[],
                        ops_attempted=0, ops_accepted=0,
                    )
                    sid = snap.id
                    await db.commit()
                    return sid

                try:
                    if write_queue is not None:
                        # v0.4.13 cycle 9 (I3): renamed to fit ``warm_phase_*``.
                        snapshot_id = await write_queue.submit(
                            _do_error_snapshot,
                            timeout=120.0,
                            operation_label="warm_phase_error_snapshot",
                        )
                    else:
                        assert session_factory is not None
                        async with session_factory() as db:
                            snapshot_id = await _do_error_snapshot(db)
                except Exception as snap_exc:
                    logger.error(
                        "Warm path error-recovery snapshot also failed: %s",
                        snap_exc, exc_info=True,
                    )
                    snapshot_id = "error-no-snapshot"
                return WarmPathResult(
                    snapshot_id=snapshot_id,
                    q_baseline=None,
                    q_final=0.0,
                    phase_results=[],
                    operations_attempted=0,
                    operations_accepted=0,
                    deadlock_breaker_used=False,
                    deadlock_breaker_phase=None,
                )

    # ------------------------------------------------------------------
    # Backfill: replay hot-path assignment for all optimizations
    # ------------------------------------------------------------------

    async def reassign_all_clusters(
        self, db: AsyncSession,
    ) -> dict:
        """Replay hot-path cluster assignment for every optimization.

        Clears all non-domain, non-archived PromptCluster records and
        re-runs ``assign_cluster()`` for every optimization that has an
        embedding.  This is the correct way to apply a new merge threshold
        to existing data — unlike ``run_cold_path()`` which only
        rearranges the cluster hierarchy without changing
        ``Optimization.cluster_id``.

        Returns:
            Dict with ``reassigned``, ``clusters_before``, ``clusters_after``.
        """
        from sqlalchemy import func as sa_func

        # Count clusters before
        before_q = await db.execute(
            select(sa_func.count()).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        clusters_before = before_q.scalar() or 0

        # Load all optimizations with embeddings
        opt_result = await db.execute(
            select(Optimization)
            .where(Optimization.embedding.isnot(None))
            .order_by(Optimization.created_at.asc())
        )
        optimizations = list(opt_result.scalars().all())
        if not optimizations:
            return {"reassigned": 0, "clusters_before": clusters_before, "clusters_after": 0}

        # Archive all non-domain active clusters — we'll rebuild from scratch
        active_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        now = _utcnow()
        for cluster in active_q.scalars().all():
            cluster.state = "archived"
            cluster.archived_at = now
            cluster.member_count = 0
            cluster.scored_count = 0
            cluster.usage_count = 0
            cluster.avg_score = None

        # Reset embedding index
        if self._embedding_index is not None:
            await self._embedding_index.reset()

        await db.flush()

        # Replay hot-path assignment for each optimization (chronological order)
        reassigned = 0
        for opt in optimizations:
            embedding = np.frombuffer(opt.embedding, dtype=np.float32)  # type: ignore[arg-type]
            domain_primary, _ = parse_domain(opt.domain or "general")

            async with self._lock:
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=opt.intent_label or "general",
                    domain=domain_primary,
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                    embedding_index=self._embedding_index,
                    project_id=getattr(opt, "project_id", None),
                )

            opt.cluster_id = cluster.id
            reassigned += 1

        await db.flush()

        # Count clusters after
        after_q = await db.execute(
            select(sa_func.count()).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        clusters_after = after_q.scalar() or 0

        # Rebuild join table, meta-patterns, and coherence
        repair = await self.repair_data_integrity(db)

        logger.info(
            "Reassign complete: %d optimizations, %d→%d clusters",
            reassigned, clusters_before, clusters_after,
        )
        return {
            "reassigned": reassigned,
            "clusters_before": clusters_before,
            "clusters_after": clusters_after,
            **repair,
        }

    # ------------------------------------------------------------------
    # Data integrity repair
    # ------------------------------------------------------------------

    async def repair_data_integrity(self, db: AsyncSession) -> dict:
        """Rebuild optimization_patterns, meta-patterns, and coherence.

        Repairs three classes of integrity issues:

        1. **Orphaned optimization_patterns** — deletes join records
           pointing to archived/missing clusters, recreates ``source``
           records for every optimization with a valid ``cluster_id``.

        2. **Orphaned meta_patterns** — deletes patterns pointing to
           archived/missing clusters, re-extracts structural patterns
           for every optimization and merges into current clusters.

        3. **Missing coherence** — computes pairwise coherence from
           member embeddings for every active cluster with 2+ members.

        Safe to call multiple times (idempotent).
        """
        from sqlalchemy import delete as sa_delete

        from app.services.taxonomy.clustering import compute_pairwise_coherence
        from app.services.taxonomy.family_ops import (
            extract_structural_patterns,
        )

        stats: dict[str, int] = {}

        # --- 1. Rebuild optimization_patterns ---
        # Delete ALL existing (they may be orphaned)
        del_op = await db.execute(sa_delete(OptimizationPattern))
        stats["join_deleted"] = del_op.rowcount  # type: ignore[attr-defined]

        # Recreate source records for every optimization with a cluster
        opts = (await db.execute(
            select(Optimization).where(
                Optimization.cluster_id.isnot(None),
            )
        )).scalars().all()

        join_created = 0
        for opt in opts:
            # Verify cluster exists and is active
            cluster_q = await db.execute(
                select(PromptCluster.id).where(
                    PromptCluster.id == opt.cluster_id,
                    PromptCluster.state.notin_(["archived"]),  # intentional: only archived, not structural
                )
            )
            if cluster_q.scalar_one_or_none():
                db.add(OptimizationPattern(
                    optimization_id=opt.id,
                    cluster_id=opt.cluster_id,
                    relationship="source",
                ))
                join_created += 1
        stats["join_created"] = join_created
        await db.flush()

        # --- 2. Rebuild meta-patterns ---
        # Delete orphaned meta-patterns (pointing to archived/missing clusters)
        del_mp = await db.execute(
            sa_delete(MetaPattern).where(
                MetaPattern.cluster_id.notin_(
                    select(PromptCluster.id).where(
                        PromptCluster.state.notin_(["archived"]),  # intentional: only archived, not structural
                    )
                )
            )
        )
        stats["meta_patterns_deleted"] = del_mp.rowcount  # type: ignore[attr-defined]

        # Re-extract structural patterns for each optimization
        patterns_created = 0
        for opt in opts:
            if not opt.raw_prompt or not opt.optimized_prompt:
                continue
            try:
                pattern_texts = extract_structural_patterns(
                    raw_prompt=opt.raw_prompt[:2000],
                    optimized_prompt=opt.optimized_prompt[:2000],
                )
                if opt.cluster_id is None:
                    continue
                for text in pattern_texts:
                    await merge_meta_pattern(
                        db, opt.cluster_id, text, self._embedding,
                    )
                    patterns_created += 1
            except Exception as exc:
                logger.debug(
                    "Pattern extraction failed for opt=%s: %s", opt.id, exc,
                )
        stats["meta_patterns_created"] = patterns_created
        await db.flush()

        # --- 3. Compute coherence ---
        active_clusters = (await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )).scalars().all()

        coherence_computed = 0
        for cluster in active_clusters:
            emb_rows = (await db.execute(
                select(Optimization.embedding).where(
                    Optimization.cluster_id == cluster.id,
                    Optimization.embedding.isnot(None),
                )
            )).scalars().all()

            if len(emb_rows) < 2:
                cluster.coherence = 1.0 if len(emb_rows) == 1 else 0.0
            else:
                embs = [
                    np.frombuffer(e, dtype=np.float32)  # type: ignore[arg-type]
                    for e in emb_rows
                ]
                cluster.coherence = compute_pairwise_coherence(embs)
            coherence_computed += 1

        stats["coherence_computed"] = coherence_computed

        # --- 4. Reconcile member_count from Optimization rows ---
        from sqlalchemy import func as _sa_func

        mc_q = await db.execute(
            select(Optimization.cluster_id, _sa_func.count().label("ct"))
            .where(Optimization.cluster_id.isnot(None))
            .group_by(Optimization.cluster_id)
        )
        mc_map: dict[str, int] = dict(mc_q.all())  # type: ignore[arg-type]
        mc_fixed = 0
        for cluster in active_clusters:
            expected = mc_map.get(cluster.id, 0)
            if cluster.member_count != expected:
                cluster.member_count = expected
                mc_fixed += 1
        stats["member_count_fixed"] = mc_fixed

        # --- 5. Reconcile orphaned project_id references ---
        # Find optimizations whose project_id references a non-existent
        # PromptCluster (state='project') and fix them via the cluster
        # ancestry chain: cluster → domain (parent_id) → project (parent_id).
        valid_project_ids = {
            c.id for c in active_clusters if c.state == "project"
        }
        # Also include domain-state nodes for the ancestry walk
        all_nodes_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.in_(["project", "domain"])
            )
        )
        all_structural = {n.id: n for n in all_nodes_q.scalars().all()}
        # Build parent lookup
        parent_map: dict[str, str | None] = {
            n.id: n.parent_id for n in all_structural.values()
        }
        # Also need domain parents for active clusters
        for cluster in active_clusters:
            if cluster.parent_id:
                parent_map.setdefault(cluster.id, cluster.parent_id)

        orphan_filter = (
            ~Optimization.project_id.in_(valid_project_ids)
            if valid_project_ids
            else Optimization.project_id.isnot(None)
        )
        orphan_q = await db.execute(
            select(Optimization).where(
                Optimization.project_id.isnot(None),
                orphan_filter,
            )
        )
        orphan_opts = orphan_q.scalars().all()
        project_fixed = 0
        for opt in orphan_opts:
            # Walk: cluster_id → parent (domain) → parent (project)
            cursor = opt.cluster_id
            resolved = None
            for _ in range(3):  # max depth
                if not cursor:
                    break
                if cursor in valid_project_ids:
                    resolved = cursor
                    break
                cursor = parent_map.get(cursor)
            if resolved and resolved != opt.project_id:
                opt.project_id = resolved
                project_fixed += 1
        stats["project_id_fixed"] = project_fixed
        await db.flush()

        logger.info(
            "Data integrity repair: join=%d created/%d deleted, "
            "meta=%d created/%d deleted, coherence=%d computed, "
            "project_id=%d fixed",
            stats["join_created"], stats["join_deleted"],
            stats["meta_patterns_created"], stats["meta_patterns_deleted"],
            stats["coherence_computed"],
            project_fixed,
        )
        return stats

    # ------------------------------------------------------------------
    # Cold path (Spec Section 2.3, 8.5)
    # ------------------------------------------------------------------

    async def run_cold_path(self, db: AsyncSession) -> ColdPathResult | None:
        """Full HDBSCAN + UMAP refit — the "defrag" operation.

        Acquires the same ``_warm_path_lock`` (cold path is a superset of
        warm path).  Uses skip-if-busy guard matching warm-path pattern —
        callers handle ``None`` as "lock held, try later".

        Returns:
            ColdPathResult on completion, or None if skipped due to lock.
        """
        if self._warm_path_lock.locked():
            logger.debug("Cold path skipped — warm/cold lock already held")
            try:
                get_event_logger().log_decision(
                    path="cold", op="skip", decision="lock_held",
                    context={"reason": "warm_path_lock already held"},
                )
            except RuntimeError:
                pass
            return None

        async with self._warm_path_lock:
            try:
                return await execute_cold_path(self, db)
            except Exception as exc:
                logger.error("Cold path failed: %s", exc, exc_info=True)
                try:
                    from app.services.taxonomy.snapshot import create_snapshot
                    snap = await create_snapshot(
                        db,
                        trigger="cold_path",
                        q_system=0.0,
                        q_coherence=0.0,
                        q_separation=0.0,
                        q_coverage=0.0,
                    )
                    snapshot_id = snap.id
                except Exception as snap_exc:
                    logger.error(
                        "Cold path error-recovery snapshot also failed: %s",
                        snap_exc, exc_info=True,
                    )
                    snapshot_id = "error-no-snapshot"
                return ColdPathResult(
                    snapshot_id=snapshot_id,
                    q_before=None,
                    q_after=0.0,
                    accepted=False,
                    nodes_created=0,
                    nodes_updated=0,
                    umap_fitted=False,
                )

    async def run_umap_projection(self, db: AsyncSession) -> int:
        """UMAP-only projection for clusters that lack 3D coordinates.

        Unlike ``run_cold_path()``, this does NOT re-cluster via HDBSCAN.
        It fits UMAP on already-positioned clusters and incrementally
        transforms the unpositioned ones. No Q-gate, no rollback risk.

        Returns:
            Number of clusters projected, or 0 if skipped (lock held).
        """
        if self._warm_path_lock.locked():
            return 0

        async with self._warm_path_lock:
            try:
                from app.services.taxonomy.cold_path import execute_umap_projection
                return await execute_umap_projection(self, db)
            except Exception as exc:
                logger.error("UMAP projection failed: %s", exc, exc_info=True)
                return 0

    # ------------------------------------------------------------------
    # Warm/cold path helpers
    # ------------------------------------------------------------------

    def _compute_q_from_nodes(
        self, nodes: list[PromptCluster], silhouette: float = 0.0
    ) -> float | None:
        """Compute Q_system from a list of PromptCluster rows.

        A5: Returns ``None`` when fewer than 2 active (non-structural) nodes
        exist — Q is undefined for single/empty taxonomies. Callers that
        need a persistable value (snapshot writers) must coerce ``None`` at
        the write boundary.
        """
        from app.services.taxonomy.quality import (
            NodeMetrics,
            QWeights,
            compute_q_system,
        )

        if not nodes:
            return None

        metrics = []
        for n in nodes:
            metrics.append(
                NodeMetrics(
                    coherence=n.coherence if n.coherence is not None else 0.0,
                    separation=n.separation if n.separation is not None else 1.0,
                    state=n.state or "active",
                    member_count=n.member_count or 0,
                )
            )

        # DBCV ramp: linear activation from 5 to 25 active nodes.
        # Only activate when silhouette has actually been computed (> 0).
        # Uninitialized silhouette (0.0) creates dead weight that
        # artificially depresses Q_system by up to 15%.
        if silhouette > 0.0:
            n_active = len(metrics)
            ramp = min(1.0, max(0.0, (n_active - 5) / 20))
        else:
            ramp = 0.0
        weights = QWeights.from_ramp(ramp)

        return compute_q_system(metrics, weights, dbcv=silhouette)

    def _compute_q_health_from_nodes(
        self, nodes: list, silhouette: float = 0.0,
    ):  # -> QHealthResult (local import)
        """Compute member-weighted Q_health from PromptCluster rows."""
        from app.services.taxonomy.quality import (
            NodeMetrics,
            QHealthResult,
            QWeights,
            compute_q_health,
        )

        if not nodes:
            return QHealthResult(
                q_health=0.0, coherence_weighted=0.0, separation_weighted=0.0,
                coverage=1.0, dbcv=0.0,
                weights={"w_c": 0.4, "w_s": 0.35, "w_v": 0.25, "w_d": 0.0},
                total_members=0, cluster_count=0,
            )

        metrics = []
        for n in nodes:
            metrics.append(
                NodeMetrics(
                    coherence=n.coherence if n.coherence is not None else 0.0,
                    separation=n.separation if n.separation is not None else 1.0,
                    state=n.state or "active",
                    member_count=n.member_count or 0,
                )
            )

        if silhouette > 0.0:
            n_active = len(metrics)
            ramp = min(1.0, max(0.0, (n_active - 5) / 20))
        else:
            ramp = 0.0
        weights = QWeights.from_ramp(ramp)

        return compute_q_health(metrics, weights, dbcv=silhouette)

    @staticmethod
    def _update_per_node_separation(nodes: list[PromptCluster]) -> None:
        """Set each node's ``separation`` to the minimum cosine distance to any sibling.

        For a single node, separation is 1.0 (no siblings to conflict with).
        Modifies nodes in-place.
        """
        if len(nodes) <= 1:
            for n in nodes:
                n.separation = 1.0
            return

        # Build centroid matrix
        valid: list[tuple[int, np.ndarray]] = []
        for i, n in enumerate(nodes):
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32).copy()  # type: ignore[arg-type]
                valid.append((i, c))
            except (ValueError, TypeError) as _sep_exc:
                logger.warning(
                    "Corrupt centroid in separation computation, cluster='%s': %s",
                    n.label, _sep_exc,
                )
                n.separation = 1.0  # default for corrupt centroid

        if len(valid) < 2:
            for n in nodes:
                n.separation = 1.0
            return

        mat = np.stack([c for _, c in valid], axis=0).astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        mat_norm = mat / norms
        sim_matrix = mat_norm @ mat_norm.T  # cosine similarity
        dist_matrix = 1.0 - sim_matrix       # cosine distance

        # Fill diagonal with inf so self-distance is ignored
        np.fill_diagonal(dist_matrix, np.inf)

        for j, (orig_idx, _) in enumerate(valid):
            min_dist = float(dist_matrix[j].min())
            # Clamp to [0, 1] for safety
            nodes[orig_idx].separation = max(0.0, min(1.0, min_dist))

    async def _try_emerge_from_families(
        self,
        db: AsyncSession,
        families: list[PromptCluster],
        batch_cluster_fn: Callable[..., Any],
        max_clusters: int | None = None,
    ) -> list[dict]:
        """Cluster unassigned families via HDBSCAN and attempt emerge for each.

        Shared by the normal emerge path and the deadlock breaker to avoid
        duplicating the embed → cluster → emerge pipeline.

        Args:
            db: Async DB session (caller-managed transaction).
            families: Unassigned PromptCluster rows (parent_id IS NULL).
            batch_cluster_fn: HDBSCAN clustering callable (typically
                ``clustering.batch_cluster``).
            max_clusters: If set, only attempt emerge for the first N
                discovered clusters (deadlock breaker uses 1).

        Returns:
            List of ``{"type": "emerge", "node_id": ...}`` operation dicts
            for each successfully emerged node.
        """
        from app.services.taxonomy.clustering import ClusterResult
        from app.services.taxonomy.lifecycle import attempt_emerge

        # Extract embeddings and IDs from families
        embeddings: list[np.ndarray] = []
        ids: list[str] = []
        for f in families:
            try:
                emb = np.frombuffer(f.centroid_embedding, dtype=np.float32)  # type: ignore[arg-type]
                embeddings.append(emb)
                ids.append(f.id)
            except (ValueError, TypeError) as _emg_exc:
                logger.warning(
                    "Corrupt embedding in emerge family, cluster='%s': %s",
                    f.label, _emg_exc,
                )
                continue

        if len(embeddings) < 3:
            return []

        cr: ClusterResult = batch_cluster_fn(embeddings)
        if cr.n_clusters == 0:
            return []

        operations: list[dict] = []
        limit = cr.n_clusters if max_clusters is None else min(max_clusters, cr.n_clusters)

        for cluster_label in range(limit):
            member_mask = cr.labels == cluster_label
            member_ids = [ids[i] for i, m in enumerate(member_mask) if m]
            member_embs = [embeddings[i] for i, m in enumerate(member_mask) if m]

            if len(member_ids) < 2:
                continue

            node = await attempt_emerge(
                db=db,
                member_cluster_ids=member_ids,
                embeddings=member_embs,
                warm_path_age=self._warm_path_age,
                provider=self._provider,
                model=settings.MODEL_HAIKU,
            )
            if node is not None:
                operations.append({"type": "emerge", "node_id": node.id})
                try:
                    get_event_logger().log_decision(
                        path="warm", op="emerge", decision="created",
                        cluster_id=node.id,
                        context={
                            "member_count": node.member_count or 0,
                            "coherence": round(node.coherence or 0, 4),
                            "domain": node.domain or "general",
                            "parent_id": node.parent_id,
                            "family_id": node.id,
                        },
                    )
                except RuntimeError:
                    pass

        return operations

    # ------------------------------------------------------------------
    # Domain discovery (ADR-004)
    # ------------------------------------------------------------------

    async def _propose_domains(self, db: AsyncSession) -> list[str]:
        """Inspect 'general' domain children and propose new domains.

        Scans active clusters parented under the "general" domain node.
        For each candidate with sufficient members and coherence, inspects
        the ``domain_raw`` field of linked optimizations.  When a single
        parsed primary domain reaches the consistency threshold and no
        domain node with that label already exists, a new domain node is
        created.

        Returns:
            List of newly created domain labels.
        """
        from collections import Counter

        from app.services.pipeline_constants import (
            DOMAIN_COUNT_CEILING,
            DOMAIN_DISCOVERY_BOOTSTRAP_DB_THRESHOLD,
            DOMAIN_DISCOVERY_CONSISTENCY,
            DOMAIN_DISCOVERY_MIN_COHERENCE,
            DOMAIN_DISCOVERY_MIN_MEMBERS,
            DOMAIN_DISCOVERY_POOL_MIN_MEMBERS,
        )
        from app.services.taxonomy._constants import (
            DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS,
        )
        # --- Step a: Check domain ceiling ---
        ceiling_q = await db.execute(
            select(func.count()).select_from(PromptCluster).where(
                PromptCluster.state == "domain",
            )
        )
        domain_count = ceiling_q.scalar() or 0

        if domain_count >= DOMAIN_COUNT_CEILING:
            logger.warning(
                "Domain ceiling reached (%d >= %d) — skipping discovery",
                domain_count, DOMAIN_COUNT_CEILING,
            )
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("domain_ceiling_reached", {
                    "domain_count": domain_count,
                    "ceiling": DOMAIN_COUNT_CEILING,
                })
            except Exception as _dc_exc:
                logger.debug("Failed to publish domain_ceiling_reached event: %s", _dc_exc)
            return []

        # --- Step b: Find canonical global "general" domain node ---
        # Hybrid taxonomy: prefer parent_id=NULL (global root); deterministic
        # ordering prevents multi-project databases from picking an arbitrary
        # per-project general via `.first()` on an unordered query.
        from app.services.taxonomy.family_ops import get_canonical_general

        general_node = await get_canonical_general(db)
        if general_node is None:
            logger.debug("No 'general' domain node — skipping discovery")
            return []

        # --- Change B: Adaptive member floor for sparse databases ---
        # Below DOMAIN_DISCOVERY_BOOTSTRAP_DB_THRESHOLD total optimizations, the
        # per-cluster member floor relaxes by 1 so a 2-prompt signal on a fresh
        # DB can promote. Above the threshold, the standard floor applies.
        # ADR-006 compliant: applies to ANY label, not just seeds.
        total_opts_q = await db.execute(
            select(func.count()).select_from(Optimization)
        )
        total_opts = int(total_opts_q.scalar() or 0)
        if total_opts < DOMAIN_DISCOVERY_BOOTSTRAP_DB_THRESHOLD:
            effective_min_members = max(2, DOMAIN_DISCOVERY_MIN_MEMBERS - 1)
        else:
            effective_min_members = DOMAIN_DISCOVERY_MIN_MEMBERS

        # --- Step c: Query eligible children ---
        # Coherence is now recomputed from actual member embeddings during
        # the reconciliation step above, so 0.0 values should be rare.
        children_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == general_node.id,
                PromptCluster.state.in_(["active", "mature"]),
                PromptCluster.member_count >= effective_min_members,
                PromptCluster.coherence >= DOMAIN_DISCOVERY_MIN_COHERENCE,
            )
        )
        candidates = list(children_q.scalars().all())
        # Note: do NOT early-return here — Change A's cross-cluster pooled pass
        # runs on clusters below the per-cluster member gate, so an empty
        # per-cluster candidate list does not preclude pooled promotion.

        # --- Step d-f: Gather existing domain labels for dedup ---
        existing_q = await db.execute(
            select(PromptCluster.label).where(
                PromptCluster.state == "domain",
            )
        )
        existing_domains: set[str] = {
            row[0] for row in existing_q.all() if row[0]
        }

        created: list[str] = []

        # v0.4.11 P0a — aggregate primaries across all qualifying clusters
        # BEFORE promotion so we can enforce the cluster-count floor.
        # Each entry in primary_to_clusters[primary] is a tuple of
        # (candidate, top_count, total) where the per-cluster consistency
        # gate has already passed.
        primary_to_clusters: dict[
            str, list[tuple[PromptCluster, int, int]]
        ] = {}

        for candidate in candidates:
            try:
                # Query domain_raw from linked optimizations
                opt_q = await db.execute(
                    select(Optimization.domain_raw).where(
                        Optimization.cluster_id == candidate.id,
                        Optimization.domain_raw.isnot(None),
                    )
                )
                domain_raws = [row[0] for row in opt_q.all() if row[0]]
                if not domain_raws:
                    continue

                # Parse primaries and count
                primaries: Counter[str] = Counter()
                for raw in domain_raws:
                    primary, _ = parse_domain(raw)
                    primaries[primary] += 1

                total = len(domain_raws)
                top_primary, top_count = primaries.most_common(1)[0]

                # Skip "general" — it's not a discovery target
                if top_primary == "general":
                    continue

                # Check consistency threshold
                if top_count / total < DOMAIN_DISCOVERY_CONSISTENCY:
                    continue

                # Skip if domain already exists
                if top_primary in existing_domains:
                    continue

                primary_to_clusters.setdefault(top_primary, []).append(
                    (candidate, top_count, total),
                )
            except Exception:
                logger.error(
                    "Domain discovery failed for cluster %s — skipping",
                    candidate.id, exc_info=True,
                )
                continue

        # v0.4.11 P0a — emit rejection events for primaries that fail
        # the cluster-count floor (forensic visibility before we filter).
        for _primary, _entries in primary_to_clusters.items():
            if len(_entries) < DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="proposal_rejected_min_source_clusters",
                        context={
                            "domain_label": _primary,
                            "source_cluster_count": len(_entries),
                            "required_min": DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS,
                            "source": "per_cluster_pass",
                        },
                    )
                except RuntimeError:
                    pass

        # Filter to only primaries with enough distinct contributing clusters.
        eligible_primaries = {
            primary: entries
            for primary, entries in primary_to_clusters.items()
            if len(entries) >= DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS
        }

        for top_primary, entries in eligible_primaries.items():
            try:
                # Re-check ceiling (may have created domains in this loop)
                if (domain_count + len(created)) >= DOMAIN_COUNT_CEILING:
                    break

                # Skip if domain already exists (another iteration may have
                # created it; defensive — eligible_primaries was filtered
                # against existing_domains during aggregation).
                if top_primary in existing_domains:
                    continue

                # Use the largest-member-count cluster as the seed (parallel
                # to the pooled pass), with deterministic tiebreak by id.
                seed_candidate, seed_top_count, seed_total = max(
                    entries,
                    key=lambda e: (e[0].member_count or 0, e[0].id),
                )

                # Create the domain node
                _domain_node, _members_reparented = await self._create_domain_node(
                    db, top_primary, existing_domains, seed_candidate,
                    general_node_id=general_node.id,
                )
                created.append(top_primary)
                existing_domains.add(top_primary)

                # Re-parent every other contributing cluster to the new
                # domain so its evidence isn't stranded under "general".
                for c, _tc, _tt in entries:
                    if c.id == seed_candidate.id:
                        continue
                    c.parent_id = _domain_node.id
                    c.domain = top_primary

                try:
                    _total_domains_q = await db.execute(
                        select(func.count()).where(PromptCluster.state == "domain")
                    )
                    _total_domains_after = int(_total_domains_q.scalar() or 0)
                except Exception:
                    _total_domains_after = len(existing_domains)
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover", decision="domain_created",
                        cluster_id=_domain_node.id,
                        context={
                            "domain_label": top_primary,
                            "seed_cluster_id": seed_candidate.id,
                            "consistency_pct": round(
                                seed_top_count / seed_total, 4,
                            ),
                            "members_reparented": _members_reparented,
                            "source_cluster_count": len(entries),
                            "total_domains_after": _total_domains_after,
                        },
                    )
                except RuntimeError:
                    pass
            except Exception:
                logger.error(
                    "Domain discovery promotion failed for primary %s — skipping",
                    top_primary, exc_info=True,
                )
                continue

        # --- Change A: Cross-cluster pooled evidence pass ---
        # Fragmented signals — 3 backend prompts split across 3 single-member
        # clusters — all fail member_count >= MIN_MEMBERS individually, but
        # their collective signal is unambiguous.  Pool by primary domain_raw
        # across ALL general-parented clusters (no per-cluster member gate);
        # each contributing cluster must still be internally consistent
        # (>= DOMAIN_DISCOVERY_CONSISTENCY), and the pooled total must cross
        # DOMAIN_DISCOVERY_POOL_MIN_MEMBERS.  Labels already promoted by the
        # per-cluster pass are skipped.
        try:
            all_children_q = await db.execute(
                select(PromptCluster).where(
                    PromptCluster.parent_id == general_node.id,
                    PromptCluster.state.in_(["active", "mature", "candidate"]),
                )
            )
            all_children = list(all_children_q.scalars().all())

            pooled: dict[str, dict] = {}
            for candidate in all_children:
                try:
                    opt_q = await db.execute(
                        select(Optimization.domain_raw).where(
                            Optimization.cluster_id == candidate.id,
                            Optimization.domain_raw.isnot(None),
                        )
                    )
                    raws = [row[0] for row in opt_q.all() if row[0]]
                    if not raws:
                        continue
                    primary_counts: Counter[str] = Counter()
                    for raw in raws:
                        primary, _ = parse_domain(raw)
                        primary_counts[primary] += 1
                    top_label, top_count = primary_counts.most_common(1)[0]
                    if top_label == "general":
                        continue
                    # Per-cluster internal consistency gate — inconsistent
                    # clusters don't contribute any members to the pool.
                    if top_count / len(raws) < DOMAIN_DISCOVERY_CONSISTENCY:
                        continue
                    entry = pooled.setdefault(
                        top_label, {"members": 0, "clusters": []},
                    )
                    entry["members"] += top_count
                    entry["clusters"].append(candidate)
                except Exception:
                    logger.debug(
                        "Pooled-pass cluster scan failed for %s — skipping",
                        getattr(candidate, "id", "?"), exc_info=True,
                    )
                    continue

            # Change B + A interaction: apply the same bootstrap relaxation
            # to the pooled floor. With <BOOTSTRAP_DB_THRESHOLD total opts
            # (e.g. 5 prompts split 3 backend + 2 frontend), a 2-member
            # pooled signal is meaningful; the dissolution floor still
            # collects premature promotions at the next maintenance cycle.
            effective_pool_min_members = (
                max(2, DOMAIN_DISCOVERY_POOL_MIN_MEMBERS - 1)
                if total_opts < DOMAIN_DISCOVERY_BOOTSTRAP_DB_THRESHOLD
                else DOMAIN_DISCOVERY_POOL_MIN_MEMBERS
            )

            for label, bucket in pooled.items():
                if label in created:
                    continue
                if label in existing_domains:
                    continue
                # v0.4.11 P0a — pooled cluster-count floor.  A single
                # cluster's pooled signal cannot promote even when the
                # member count is high; the proposal needs cross-cluster
                # corroboration to avoid the fullstack-ghost pathology.
                if len(bucket["clusters"]) < DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS:
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="proposal_rejected_min_source_clusters",
                            context={
                                "domain_label": label,
                                "source_cluster_count": len(bucket["clusters"]),
                                "required_min": DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS,
                                "pooled_members": bucket["members"],
                                "source": "pooled_pass",
                            },
                        )
                    except RuntimeError:
                        pass
                    continue
                if bucket["members"] < effective_pool_min_members:
                    continue
                # Respect ceiling (may have grown via per-cluster pass)
                if (domain_count + len(created)) >= DOMAIN_COUNT_CEILING:
                    break
                # Seed the domain node off the largest contributing cluster
                # so _create_domain_node has a meaningful seed for coloring.
                seed_cluster = max(
                    bucket["clusters"],
                    key=lambda c: c.member_count or 0,
                )
                try:
                    _domain_node, _members_reparented = (
                        await self._create_domain_node(
                            db, label, existing_domains, seed_cluster,
                            general_node_id=general_node.id,
                        )
                    )
                    created.append(label)
                    existing_domains.add(label)
                    # Re-parent every contributing cluster to the new domain
                    for c in bucket["clusters"]:
                        if c.id == seed_cluster.id:
                            continue  # already reparented by _create_domain_node
                        c.parent_id = _domain_node.id
                        c.domain = label
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="domain_created",
                            cluster_id=_domain_node.id,
                            context={
                                "domain_label": label,
                                "source": "cross_cluster_pool",
                                "pooled_members": bucket["members"],
                                "contributing_clusters": len(bucket["clusters"]),
                                "total_domains_after": (
                                    domain_count + len(created)
                                ),
                            },
                        )
                    except RuntimeError:
                        pass
                except Exception:
                    logger.error(
                        "Pooled domain promotion failed for label %s",
                        label, exc_info=True,
                    )
                    continue
        except Exception as pool_exc:
            logger.warning(
                "Cross-cluster pooled pass failed (non-fatal): %s", pool_exc,
            )

        # --- Post-discovery re-parenting sweep ---
        # Check ALL general-parented clusters against existing domain nodes.
        # Clusters created AFTER a domain was established may still be under
        # general if their hot-path assignment didn't find the domain node.
        try:
            domain_nodes_q = await db.execute(
                select(PromptCluster.id, PromptCluster.label, PromptCluster.parent_id).where(
                    PromptCluster.state == "domain",
                    PromptCluster.label != "general",
                )
            )
            _domain_rows = domain_nodes_q.all()
            domain_lookup = {row[1].lower(): row[0] for row in _domain_rows}
            # Map sub-domain labels to their parent domain label so cluster.domain
            # always uses the top-level domain (matching DomainResolver behavior).
            _domain_id_to_label = {row[0]: row[1] for row in _domain_rows}
            _sub_domain_to_parent: dict[str, str] = {}
            for row_id, label, parent_id in _domain_rows:
                if parent_id and parent_id in _domain_id_to_label:
                    _sub_domain_to_parent[label.lower()] = _domain_id_to_label[parent_id].lower()

            from collections import Counter as _Counter

            general_children_q = await db.execute(
                select(PromptCluster).where(
                    PromptCluster.parent_id == general_node.id,
                    PromptCluster.state.in_(["active", "mature"]),
                )
            )
            general_children = list(general_children_q.scalars().all())
            logger.info(
                "Re-parenting sweep: checking %d general-parented clusters against %d domains",
                len(general_children), len(domain_lookup),
            )
            sweep_reparented = 0
            for cluster in general_children:
                # Get ALL domain_raw values for this cluster's optimizations
                opt_raws_q = await db.execute(
                    select(Optimization.domain_raw).where(
                        Optimization.cluster_id == cluster.id,
                        Optimization.domain_raw.isnot(None),
                    )
                )
                all_raws = [r[0] for r in opt_raws_q.all() if r[0]]
                if not all_raws:
                    continue
                # Parse domain_raw values to extract lowercase primaries
                # before counting — raw values like "Backend: Security" must
                # become "backend" to match domain_lookup keys.
                parsed_counts: _Counter[str] = _Counter()
                for raw in all_raws:
                    primary, _ = parse_domain(raw)
                    parsed_counts[primary] += 1
                total = len(all_raws)
                # Find the top non-general parsed domain
                for top_primary, top_ct in parsed_counts.most_common():
                    if top_primary == "general":
                        continue
                    consistency = top_ct / total
                    if top_primary in domain_lookup and consistency >= DOMAIN_DISCOVERY_CONSISTENCY:
                        target_id = domain_lookup[top_primary]
                        # Sub-domain labels map to parent domain for cluster.domain
                        # (cluster.parent_id points to the sub-domain node for tree structure)
                        effective_domain = _sub_domain_to_parent.get(top_primary, top_primary)
                        logger.info(
                            "Re-parenting '%s' → '%s' (domain=%s, consistency=%.0f%%, %d/%d members)",
                            cluster.label, top_primary, effective_domain,
                            consistency * 100, top_ct, total,
                        )
                        cluster.parent_id = target_id
                        cluster.domain = effective_domain
                        sweep_reparented += 1
                    break  # only check the top non-general candidate
            if sweep_reparented:
                logger.info(
                    "Re-parenting sweep: moved %d clusters from general to their domain",
                    sweep_reparented,
                )
        except Exception as sweep_exc:
            logger.warning("Re-parenting sweep failed (non-fatal): %s", sweep_exc)

        return created

    async def _propose_sub_domains(
        self,
        db: AsyncSession,
        *,
        vocab_only: bool = False,
    ) -> list[str]:
        """Discover sub-domains from domain_raw qualifier signals.

        Scans each domain's linked optimizations for sub-qualifier signals
        (e.g., "backend: auth").  When a qualifier appears in enough
        optimizations (adaptive threshold), a sub-domain node is created.

        Three signal sources per optimization (priority cascade):
          1. ``domain_raw`` sub-qualifier via ``parse_domain()`` (primary)
          2. ``intent_label`` keyword match against organic vocabulary (fallback)
          3. ``raw_prompt`` keyword match against dynamic TF-IDF signals from
             the domain node's ``cluster_metadata.signal_keywords`` (fallback)

        Args:
            db: Async SQLAlchemy session.
            vocab_only: When True, only runs the qualifier vocabulary
                generation pass and returns without performing sub-domain
                discovery. Used by the dedicated vocab-refresh phase which
                commits the vocab in an isolated session so Haiku-generated
                qualifiers persist independently of downstream Phase 5 fate.

        Returns:
            List of newly created sub-domain labels (empty when vocab_only).

        Side effects:
            Emits ``vocab_generated_enriched`` events on each Phase 4.5
            vocabulary regeneration. Each event carries ``previous_groups``
            (sorted lowercase prior vocab keys), ``new_groups`` (sorted
            lowercase regenerated vocab keys), and ``overlap_pct`` (Jaccard
            intersection-over-union × 100, rounded to 1 decimal) for
            forensic correlation with downstream sub-domain dissolutions.
            A WARNING-level log line is emitted when ``overlap_pct < 50.0``
            on a non-bootstrap regen (audit R7).
        """
        from collections import Counter

        from app.services.pipeline_constants import DOMAIN_COUNT_CEILING
        from app.services.taxonomy._constants import (
            SUB_DOMAIN_MIN_CLUSTER_BREADTH,
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH,
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
            SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
            SUB_DOMAIN_QUALIFIER_SCALE_RATE,
        )
        from app.utils.text_cleanup import parse_domain  # used by vocab-gen block

        created: list[str] = []

        # Check domain ceiling
        domain_count_q = await db.execute(
            select(func.count()).where(PromptCluster.state == "domain")
        )
        current_domain_count = int(domain_count_q.scalar() or 0)
        if current_domain_count >= DOMAIN_COUNT_CEILING:
            return created

        # Gather existing domain labels for dedup
        existing_q = await db.execute(
            select(PromptCluster.label).where(PromptCluster.state == "domain")
        )
        existing_labels = {r[0].lower() for r in existing_q.all() if r[0]}

        # --- Vocabulary generation pass: ALL domains including "general" ---
        # Every domain gets organic qualifier vocabulary. This is decoupled
        # from sub-domain discovery (which skips "general") because vocab is
        # useful for hot-path enrichment regardless of sub-domain formation.
        all_domain_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        all_domains = list(all_domain_q.scalars().all())
        for domain_node in all_domains:
            # Get child cluster IDs for this domain (direct children only)
            child_q = await db.execute(
                select(PromptCluster.id).where(
                    PromptCluster.parent_id == domain_node.id,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )
            child_ids = [r[0] for r in child_q.all()]
            if len(child_ids) < 2:
                continue  # need ≥2 clusters for meaningful vocab

            meta = read_meta(domain_node.cluster_metadata)
            cached_vocab = meta.get("generated_qualifiers")
            cached_cluster_count = meta.get("generated_qualifiers_cluster_count", 0)
            current_cluster_count = len(child_ids)

            is_first_generation = not cached_vocab
            stale = (
                is_first_generation
                or abs(current_cluster_count - cached_cluster_count)
                > max(2, cached_cluster_count * 0.3)
            )
            if stale and self._provider:
                import time as _vocab_time

                from app.services.taxonomy.labeling import generate_qualifier_vocabulary

                _vocab_start = _vocab_time.monotonic()
                cluster_info_q = await db.execute(
                    select(
                        PromptCluster.id,
                        PromptCluster.label,
                        PromptCluster.member_count,
                        PromptCluster.centroid_embedding,
                    ).where(
                        PromptCluster.id.in_(child_ids),
                    )
                )
                cluster_rows = cluster_info_q.all()

                from app.services.taxonomy.labeling import ClusterVocabContext

                cluster_contexts: list[ClusterVocabContext]
                similarity_matrix: list[list[float | None]] | None = None
                try:
                    from collections import Counter as _Counter

                    # Query intent_labels and domain_raw for enrichment
                    opt_enrichment_q = await db.execute(
                        select(
                            Optimization.cluster_id,
                            Optimization.intent_label,
                            Optimization.domain_raw,
                        ).where(
                            Optimization.cluster_id.in_(child_ids),
                        )
                    )
                    opt_rows = opt_enrichment_q.all()

                    intents_by_cluster: dict[str, _Counter[str]] = {}
                    qualifiers_by_cluster: dict[str, _Counter[str]] = {}
                    for cid, intent, domain_raw in opt_rows:
                        if intent:
                            intents_by_cluster.setdefault(cid, _Counter())[intent.lower()] += 1
                        if domain_raw and ':' in domain_raw:
                            _, q = parse_domain(domain_raw)
                            if q:
                                q_norm = q.strip().lower()
                                if q_norm:
                                    qualifiers_by_cluster.setdefault(cid, _Counter())[q_norm] += 1

                    # Compute centroid similarity matrix. Cells for clusters
                    # lacking a centroid are left as None so downstream renderers
                    # can distinguish "unknown" from "distinct" (0.0 would mislead
                    # Haiku into treating unknown geometry as orthogonal).
                    centroid_vecs: list[np.ndarray] = []
                    centroid_indices: list[int] = []
                    try:
                        for i, (cid, label, mc, centroid_bytes) in enumerate(cluster_rows):
                            if centroid_bytes:
                                vec = np.frombuffer(centroid_bytes, dtype=np.float32)
                                norm = np.linalg.norm(vec)
                                if norm > _VOCAB_ZERO_NORM_EPS:
                                    centroid_vecs.append(vec / norm)
                                    centroid_indices.append(i)

                        if len(centroid_vecs) >= 2:
                            mat = np.vstack(centroid_vecs)
                            sim = (mat @ mat.T).tolist()
                            n = len(cluster_rows)
                            similarity_matrix = [[None] * n for _ in range(n)]
                            for si, ri in enumerate(centroid_indices):
                                for sj, rj in enumerate(centroid_indices):
                                    similarity_matrix[ri][rj] = sim[si][sj]
                        else:
                            # No or only 1 valid centroid — Haiku gets no geometric context.
                            similarity_matrix = None
                            try:
                                get_event_logger().log_decision(
                                    path="warm", op="discover",
                                    decision="vocab_enrichment_fallback",
                                    context={
                                        "domain": domain_node.label,
                                        "reason": "no_centroids",
                                        "centroid_count": len(centroid_vecs),
                                        "cluster_count": len(cluster_rows),
                                    },
                                )
                            except RuntimeError:
                                pass
                    except Exception as matrix_exc:
                        logger.warning(
                            "Vocab matrix computation failed for '%s': %s",
                            domain_node.label, matrix_exc,
                        )
                        similarity_matrix = None
                        try:
                            get_event_logger().log_decision(
                                path="warm", op="discover",
                                decision="vocab_enrichment_fallback",
                                context={
                                    "domain": domain_node.label,
                                    "reason": "matrix_failed",
                                    "error": str(matrix_exc)[:200],
                                },
                            )
                        except RuntimeError:
                            pass

                    # Build ClusterVocabContext list
                    cluster_contexts = []
                    for cid, label, mc, _centroid in cluster_rows:
                        intent_counter = intents_by_cluster.get(cid, _Counter())
                        top_intents = [intent for intent, _ in intent_counter.most_common(10)]
                        qual_counter = qualifiers_by_cluster.get(cid, _Counter())
                        qual_dist = dict(qual_counter.most_common(5))
                        cluster_contexts.append(ClusterVocabContext(
                            label=label,
                            member_count=mc or 0,
                            intent_labels=top_intents,
                            qualifier_distribution=qual_dist,
                        ))
                except Exception as enrich_exc:
                    logger.warning(
                        "Vocab enrichment failed for '%s' (falling back to labels): %s",
                        domain_node.label, enrich_exc,
                    )
                    cluster_contexts = [
                        ClusterVocabContext(label=r[1], member_count=r[2] or 0)
                        for r in cluster_rows
                    ]
                    similarity_matrix = None
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="vocab_enrichment_fallback",
                            context={
                                "domain": domain_node.label,
                                "reason": "query_failed",
                                "error": str(enrich_exc)[:200],
                            },
                        )
                    except RuntimeError:
                        pass

                # Surface domain-level TF-IDF terms + previous vocab groups
                # so Haiku can absorb latent themes the cascade is currently
                # routing through source 3 instead of any curated group.
                # Without this hint, terms like the live backend's ``audit``
                # (3 source-3 hits) stay invisible to every regeneration —
                # the cascade keeps recording them and the vocab keeps
                # ignoring them.  Continuity hint (existing group names)
                # also discourages spurious drift in group naming across
                # regenerations, since name churn breaks the consistency
                # baseline used for emergence detection.
                _domain_meta_for_hints = read_meta(domain_node.cluster_metadata)
                _signal_kw_raw = _domain_meta_for_hints.get("signal_keywords") or []
                _signal_kws_typed: list[tuple[str, float]] = []
                for _item in _signal_kw_raw:
                    try:
                        _kw, _w = _item[0], float(_item[1])
                    except (IndexError, TypeError, ValueError):
                        continue
                    if isinstance(_kw, str):
                        _signal_kws_typed.append((_kw, _w))
                _existing_vocab_dict = _domain_meta_for_hints.get(
                    "generated_qualifiers",
                ) or {}
                _existing_groups = (
                    list(_existing_vocab_dict.keys())
                    if isinstance(_existing_vocab_dict, dict) else []
                )

                try:
                    generated = await generate_qualifier_vocabulary(
                        provider=self._provider,
                        domain_label=domain_node.label,
                        cluster_contexts=cluster_contexts,
                        similarity_matrix=similarity_matrix,
                        model=settings.MODEL_HAIKU,
                        domain_signal_keywords=_signal_kws_typed,
                        existing_vocab_groups=_existing_groups,
                    )
                except Exception as gen_exc:
                    generated = {}
                    logger.warning("Vocab generation failed for '%s': %s", domain_node.label, gen_exc)

                _vocab_ms = round((_vocab_time.monotonic() - _vocab_start) * 1000, 1)

                if generated:
                    # --- Post-generation vocabulary quality metric (observability only) ---
                    _quality_score: float | None = None
                    _max_pairwise: float | None = None
                    _overlapping_pair: list[str] | None = None
                    _qm_ms: float | None = None
                    try:
                        import time as _qm_time
                        _qm_start = _qm_time.monotonic()

                        group_embeddings: dict[str, np.ndarray] = {}
                        for gname, gkws in generated.items():
                            if not gkws:
                                continue
                            emb = await self._embedding.aembed_single(" ".join(gkws))
                            emb_norm = np.linalg.norm(emb)
                            if emb_norm > _VOCAB_ZERO_NORM_EPS:
                                group_embeddings[gname] = emb / emb_norm

                        if len(group_embeddings) >= 2:
                            names = list(group_embeddings.keys())
                            vecs = np.vstack([group_embeddings[n] for n in names])
                            pairwise = vecs @ vecs.T
                            _max_pairwise = -1.0
                            for i in range(len(names)):
                                for j in range(i + 1, len(names)):
                                    if pairwise[i][j] > _max_pairwise:
                                        _max_pairwise = float(pairwise[i][j])
                                        _overlapping_pair = [names[i], names[j]]
                            _quality_score = round(1.0 - _max_pairwise, 4)

                        _qm_ms = round((_qm_time.monotonic() - _qm_start) * 1000, 1)

                        if _quality_score is not None:
                            self._vocab_quality_scores.append(_quality_score)
                            try:
                                get_event_logger().log_decision(
                                    path="warm", op="discover",
                                    decision="vocab_quality_assessed",
                                    context={
                                        "domain": domain_node.label,
                                        "quality_score": _quality_score,
                                        "max_pairwise_cosine": (
                                            round(_max_pairwise, 4)
                                            if _max_pairwise is not None
                                            else None
                                        ),
                                        "overlapping_pair": (
                                            _overlapping_pair
                                            if (
                                                _max_pairwise is not None
                                                and _max_pairwise > _VOCAB_OVERLAP_REPORT_THRESHOLD
                                            )
                                            else None
                                        ),
                                        "quality_ms": _qm_ms,
                                    },
                                )
                            except RuntimeError:
                                pass

                            if _quality_score < _VOCAB_QUALITY_POOR_THRESHOLD:
                                logger.warning(
                                    "Vocab quality poor for '%s': score=%.2f (max_pairwise=%.2f between %s)",
                                    domain_node.label, _quality_score,
                                    _max_pairwise if _max_pairwise is not None else float('nan'),
                                    _overlapping_pair,
                                )
                            elif _quality_score < _VOCAB_QUALITY_ACCEPTABLE_THRESHOLD:
                                logger.warning(
                                    "Vocab quality acceptable (overlapping groups) for '%s': "
                                    "score=%.2f (max_pairwise=%.2f between %s)",
                                    domain_node.label, _quality_score,
                                    _max_pairwise if _max_pairwise is not None else float('nan'),
                                    _overlapping_pair,
                                )
                    except Exception as qm_exc:
                        logger.warning(
                            "Vocab quality metric failed for '%s': %s",
                            domain_node.label, qm_exc,
                        )

                    # Emit enriched vocab observability event (per spec § Observability)
                    clusters_with_intents = sum(
                        1 for ctx in cluster_contexts if ctx.intent_labels
                    )
                    clusters_with_centroids = 0
                    if similarity_matrix:
                        # A cluster has a centroid iff its row has any non-None cell
                        clusters_with_centroids = sum(
                            1 for row in similarity_matrix
                            if any(v is not None for v in row)
                        )
                    n_ctx = len(cluster_contexts) or 1
                    matrix_coverage_pct = round(
                        100.0 * clusters_with_centroids / n_ctx, 1
                    )

                    # R7 (audit 2026-04-27): vocab regeneration overlap telemetry.
                    # Jaccard intersection-over-union between previous and new group names.
                    prev_set = {g.lower() for g in (_existing_groups or [])}
                    new_set = {g.lower() for g in generated.keys()}
                    if prev_set or new_set:
                        intersect = prev_set & new_set
                        union = prev_set | new_set
                        overlap_pct = round(100.0 * len(intersect) / len(union), 1) if union else 0.0
                    else:
                        overlap_pct = 0.0
                    prev_groups_sorted = sorted(prev_set)
                    new_groups_sorted = sorted(new_set)

                    # WARNING when regen has low overlap — sub-domains anchored to the
                    # previous vocab may dissolve on next Phase 5. Fires once per regen
                    # (the enclosing block runs at most once per domain per cycle).
                    if prev_set and overlap_pct < 50.0:
                        logger.warning(
                            "Vocab regen low overlap for '%s': overlap=%.1f%% "
                            "previous=%s new=%s — sub-domains anchored to the previous "
                            "vocab may dissolve on next Phase 5 (audit R7)",
                            domain_node.label, overlap_pct,
                            prev_groups_sorted, new_groups_sorted,
                        )

                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="vocab_generated_enriched",
                            context={
                                "domain": domain_node.label,
                                "groups": len(generated),
                                "quality_score": _quality_score,
                                "max_pairwise_cosine": (
                                    round(_max_pairwise, 4)
                                    if _max_pairwise is not None
                                    else None
                                ),
                                "clusters_with_intents": clusters_with_intents,
                                "clusters_with_centroids": clusters_with_centroids,
                                "matrix_coverage_pct": matrix_coverage_pct,
                                "generation_ms": _vocab_ms,
                                "quality_ms": (
                                    _qm_ms if _quality_score is not None else None
                                ),
                                "previous_groups": prev_groups_sorted,
                                "new_groups": new_groups_sorted,
                                "overlap_pct": overlap_pct,
                            },
                        )
                    except RuntimeError:
                        pass

                    domain_node.cluster_metadata = write_meta(
                        domain_node.cluster_metadata,
                        generated_qualifiers=generated,
                        generated_qualifiers_cluster_count=current_cluster_count,
                    )
                    try:
                        from app.services.domain_signal_loader import get_signal_loader
                        loader = get_signal_loader()
                        if loader:
                            loader.refresh_qualifiers(domain_node.label, generated)
                    except Exception:
                        pass
            elif cached_vocab and isinstance(cached_vocab, dict):
                # Push existing cached vocab to DomainSignalLoader
                try:
                    from app.services.domain_signal_loader import get_signal_loader
                    loader = get_signal_loader()
                    if loader:
                        loader.refresh_qualifiers(domain_node.label, cached_vocab)
                except Exception:
                    pass

        # --- Early return when caller only wants vocab generation ---
        # The dedicated vocab-refresh phase runs this in an isolated session
        # that it commits itself. Returning here keeps Haiku-generated
        # qualifiers persistent independently of downstream Phase 5 fate
        # (the sub-domain discovery pass below issues many queries that
        # can trigger autoflush on a poisoned session).
        if vocab_only:
            return []

        # --- Sub-domain discovery pass: non-general domains only ---
        domain_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label != "general",
            )
        )
        domains = list(domain_q.scalars().all())

        for domain_node in domains:
            # Count existing sub-domains (for child_ids expansion below)
            existing_sub_q = await db.execute(
                select(func.count()).where(
                    PromptCluster.parent_id == domain_node.id,
                    PromptCluster.state == "domain",
                )
            )
            existing_sub_count = existing_sub_q.scalar() or 0
            # No permanent lock — discovery continues even with existing
            # sub-domains.  The label dedup guard below prevents re-creating
            # existing sub-domains while allowing new ones to form.

            if existing_sub_count > 0:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="sub_domain_domain_reevaluated",
                        context={
                            "domain": domain_node.label,
                            "existing_sub_domain_count": existing_sub_count,
                        },
                    )
                except RuntimeError:
                    pass

            # Re-evaluate existing sub-domains for dissolution
            dissolved_this_cycle: set[str] = set()
            if existing_sub_count > 0:
                dissolved = await self._reevaluate_sub_domains(
                    db, domain_node, existing_labels,
                )
                # Block same-cycle re-creation of dissolved labels (flip-flop prevention)
                dissolved_this_cycle.update(d.lower().replace(" ", "-") for d in dissolved)
                if dissolved:
                    # Update sub-domain count after dissolution
                    existing_sub_q2 = await db.execute(
                        select(func.count()).where(
                            PromptCluster.parent_id == domain_node.id,
                            PromptCluster.state == "domain",
                        )
                    )
                    existing_sub_count = existing_sub_q2.scalar() or 0

            # --- Run shared three-source qualifier cascade ---
            # Single source of truth: the same primitive powers the readiness
            # analytics service.  Scans all optimizations under the domain
            # hierarchy (direct children + under existing sub-domains).
            cascade = await compute_qualifier_cascade(db, domain_node)
            total_opts = cascade.total_opts
            if total_opts < SUB_DOMAIN_QUALIFIER_MIN_MEMBERS:
                continue

            # Diagnostic "vocab present?" log — cascade already computed the
            # keyword set and organic-vocab presence; consume those fields
            # directly instead of re-reading metadata.
            dynamic_keywords = cascade.dynamic_keywords
            if dynamic_keywords:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="sub_domain_dynamic_vocab",
                        context={
                            "domain": domain_node.label,
                            "dynamic_keyword_count": len(dynamic_keywords),
                            "top_keywords": [kw for kw, _ in dynamic_keywords[:5]],
                            "has_organic_vocab": cascade.generated_qualifiers_present,
                        },
                    )
                except RuntimeError:
                    pass

            # Unpack cascade into engine-local tallies (preserves downstream logic).
            qualifier_counts: Counter[str] = Counter(cascade.qualifier_counts)
            qualifier_to_cluster_ids = cascade.qualifier_to_cluster_ids
            source_from_raw = cascade.source_breakdown.get("domain_raw", 0)
            source_from_intent = cascade.source_breakdown.get("intent_label", 0)
            source_from_tfidf = cascade.source_breakdown.get("tf_idf", 0)
            intent_qualifier_counts: Counter[str] = Counter({
                q: srcs.get("intent_label", 0)
                for q, srcs in cascade.per_qualifier_sources.items()
                if srcs.get("intent_label", 0) > 0
            })

            # Log signal scan (source key "tf_idf" standardized per ROADMAP
            # engine-cascade-extraction naming reconciliation — was "dynamic").
            try:
                get_event_logger().log_decision(
                    path="warm", op="discover",
                    decision="sub_domain_signal_scan",
                    context={
                        "domain": domain_node.label,
                        "total_opts": total_opts,
                        "qualifiers_found": len(qualifier_counts),
                        "source_breakdown": {
                            "domain_raw": source_from_raw,
                            "intent_label": source_from_intent,
                            "tf_idf": source_from_tfidf,
                        },
                        "qualifier_counts": dict(qualifier_counts.most_common(10)),
                        "vocab_source": "organic",
                    },
                )
            except RuntimeError:
                pass

            # Log intent_label fallback summary when it contributed signals
            if source_from_intent > 0:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="sub_domain_intent_fallback",
                        context={
                            "domain": domain_node.label,
                            "opt_count": total_opts,
                            "intent_matches": source_from_intent,
                            "qualifiers_from_intent": dict(intent_qualifier_counts),
                        },
                    )
                except RuntimeError:
                    pass

            if not qualifier_counts:
                continue

            # Adaptive threshold
            threshold = max(
                SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
                SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH
                - SUB_DOMAIN_QUALIFIER_SCALE_RATE * total_opts,
            )

            # Evaluate each qualifier
            for qualifier, count in qualifier_counts.most_common():
                consistency = count / total_opts
                passed = (
                    count >= SUB_DOMAIN_QUALIFIER_MIN_MEMBERS
                    and consistency >= threshold
                )

                # Log evaluation
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="sub_domain_qualifier_eval",
                        context={
                            "domain": domain_node.label,
                            "qualifier": qualifier,
                            "count": count,
                            "total": total_opts,
                            "consistency_pct": round(consistency * 100, 1),
                            "threshold_pct": round(threshold * 100, 1),
                            "passed": passed,
                        },
                    )
                except RuntimeError:
                    pass

                if not passed:
                    continue

                # Check label dedup + flip-flop prevention.
                # Shared canonicalizer (text_cleanup.normalize_sub_domain_label)
                # guarantees vocab-group names AND sub-domain labels follow the
                # same rule: lowercase + space/underscore→hyphen + collapse
                # multiple hyphens + word-boundary truncation at 30 chars.
                # Prevents incoherent labels like ``pattern-instrumentat``
                # (mid-word slice) when Haiku returns longer phrases.
                from app.utils.text_cleanup import normalize_sub_domain_label

                sub_label = normalize_sub_domain_label(qualifier)
                if not sub_label:
                    continue
                if sub_label in existing_labels or sub_label in dissolved_this_cycle:
                    skip_reason = (
                        "dissolved_this_cycle" if sub_label in dissolved_this_cycle
                        else "already_exists"
                    )
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="sub_domain_skipped",
                            context={
                                "qualifier": sub_label,
                                "reason": skip_reason,
                                "domain": domain_node.label,
                            },
                        )
                    except RuntimeError:
                        pass
                    continue

                # Minimum cluster breadth: a sub-domain with only 1 child
                # cluster is a 1:1 wrapper that adds hierarchy depth without
                # navigational value.  Require at least 2 distinct clusters
                # to justify the sub-domain level.
                matching_cluster_count = len(
                    qualifier_to_cluster_ids.get(qualifier, set())
                )
                if matching_cluster_count < SUB_DOMAIN_MIN_CLUSTER_BREADTH:
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="sub_domain_skipped",
                            context={
                                "qualifier": sub_label,
                                "reason": "single_cluster",
                                "domain": domain_node.label,
                                "cluster_count": matching_cluster_count,
                                "consistency_pct": round(consistency * 100, 1),
                            },
                        )
                    except RuntimeError:
                        pass
                    continue

                if current_domain_count >= DOMAIN_COUNT_CEILING:
                    break

                try:
                    # Pick the largest matching cluster as a seed for
                    # _create_domain_node so signal_keywords + centroid +
                    # signal_member_count_at_generation get populated from
                    # representative content instead of left as defaults.
                    # Without a seed the sub-domain is created with empty
                    # signal_keywords and a None centroid_embedding — both
                    # observable bugs prior to this change.
                    matching_cluster_ids = qualifier_to_cluster_ids.get(qualifier, set())
                    seed_for_keywords: PromptCluster | None = None
                    if matching_cluster_ids:
                        # Order by member_count desc for representativeness.
                        seed_q = await db.execute(
                            select(PromptCluster).where(
                                PromptCluster.id.in_(matching_cluster_ids),
                                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                            ).order_by(PromptCluster.member_count.desc())
                            .limit(1)
                        )
                        seed_for_keywords = seed_q.scalars().first()

                    sub_node, _ = await self._create_domain_node(
                        db, sub_label, existing_labels,
                        seed_cluster=seed_for_keywords,
                        parent_domain_id=domain_node.id,
                    )

                    # Reparent matching clusters
                    reparented = 0
                    for cid in matching_cluster_ids:
                        cluster = await db.get(PromptCluster, cid)
                        if cluster and cluster.state not in EXCLUDED_STRUCTURAL_STATES:
                            cluster.parent_id = sub_node.id
                            cluster.domain = domain_node.label
                            reparented += 1

                    # Position sub-domain in topology
                    await self._set_domain_umap_from_children(db, sub_node)

                    created.append(sub_label)
                    existing_labels.add(sub_label)
                    current_domain_count += 1

                    # Update resolver cache
                    try:
                        from app.services.domain_resolver import get_domain_resolver
                        get_domain_resolver().add_label(
                            sub_label, parent_label=domain_node.label,
                        )
                    except (ValueError, Exception):
                        pass

                    logger.info(
                        "Created sub-domain '%s' under '%s': %d clusters, "
                        "consistency=%.0f%% (%d/%d)",
                        sub_label, domain_node.label, reparented,
                        consistency * 100, count, total_opts,
                    )

                    try:
                        get_event_logger().log_decision(
                            path="warm", op="discover",
                            decision="sub_domain_created",
                            cluster_id=sub_node.id,
                            context={
                                "qualifier": sub_label,
                                "parent_domain": domain_node.label,
                                "member_count": count,
                                "clusters_reparented": reparented,
                                "consistency_pct": round(consistency * 100, 1),
                                "total_domains_after": current_domain_count,
                            },
                        )
                    except RuntimeError:
                        pass

                except Exception as exc:
                    logger.warning(
                        "Sub-domain creation failed for '%s' under '%s': %s",
                        sub_label, domain_node.label, exc,
                        exc_info=True,
                    )
                    continue

        return created

    async def rebuild_sub_domains(
        self,
        db: AsyncSession | None,
        domain_id: str,
        *,
        min_consistency_override: float | None = None,
        dry_run: bool = False,
        write_queue: Any = None,
    ) -> dict:
        """Operator recovery: re-run sub-domain discovery on ONE domain.

        Idempotent: existing sub-domains are not recreated. Single transaction
        semantics — partial failure rolls back the in-flight session. Always
        emits a ``sub_domain_rebuild_invoked`` event (including dry runs).
        Publishes ``taxonomy_changed`` only when ``created`` is non-empty AND
        non-dry.

        v0.4.13 cycle 7b: when ``write_queue`` is supplied, the entire body
        (including the SAVEPOINT-protected batch insert at step 7) runs
        inside a single ``submit()`` callback labelled
        ``engine_rebuild_sub_domains``. The SAVEPOINT semantics are
        unchanged -- partial failure still rolls back the in-flight inserts
        without poisoning the outer queue session. Detection is
        ``write_queue is not None``.

        Implementation note — cascade deviation:
            The R6 spec proposed reusing ``compute_qualifier_cascade`` for the
            qualifier scan, but that primitive admits a qualifier only when it
            already lives in the parent domain's vocabulary
            (``generated_qualifiers`` keys ∪ ``signal_keywords`` lemmas — see
            ``known_qualifiers`` gating in
            ``sub_domain_readiness.compute_qualifier_cascade``). This is the
            correct gate for *organic* discovery during the warm-path Phase 5
            cycle, but it is exactly wrong for the operator-recovery scenario
            this method exists to serve: post-mass-dissolution, the parent
            domain's ``generated_qualifiers`` may have been emptied or scoped
            away from the qualifiers the operator wants to re-promote, and the
            cascade would therefore return ``qualifier_counts={}`` and silently
            produce a zero-proposal recovery. We instead read each
            optimization's ``domain_raw`` directly via
            ``parse_domain``, count occurrences locally, and let the breadth +
            consistency gates do their work without a vocabulary precondition.
            User-visible semantics (threshold formula, breadth gate, idempotency,
            transactional rollback, telemetry) are unchanged.

        Args:
            db: Async SQLAlchemy session.
            domain_id: PromptCluster.id of the domain to rebuild.
            min_consistency_override: Replaces the adaptive threshold formula.
                Must be >= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR (defense-
                in-depth check; Pydantic also enforces at the router).
            dry_run: When True, returns proposals without modifying state.

        Returns:
            dict with keys: domain_id, domain_label, threshold_used,
            proposed, created, skipped_existing, dry_run.

        Raises:
            ValueError: domain_id not found OR not a domain node OR
                min_consistency_override below dissolution floor.
        """
        if write_queue is not None:
            async def _do_rebuild(write_db: AsyncSession) -> dict:
                return await self._rebuild_sub_domains_impl(
                    write_db, domain_id,
                    min_consistency_override=min_consistency_override,
                    dry_run=dry_run,
                )

            return await write_queue.submit(
                _do_rebuild, operation_label="engine_rebuild_sub_domains",
            )

        if db is None:
            raise TypeError(
                "rebuild_sub_domains requires either write_queue= or "
                "a positional AsyncSession (legacy form).",
            )
        return await self._rebuild_sub_domains_impl(
            db, domain_id,
            min_consistency_override=min_consistency_override,
            dry_run=dry_run,
        )

    async def _rebuild_sub_domains_impl(
        self,
        db: AsyncSession,
        domain_id: str,
        *,
        min_consistency_override: float | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Internal: rebuild_sub_domains body.

        See ``rebuild_sub_domains`` for the public contract -- this helper
        receives an ``AsyncSession`` directly and is invoked either by
        the legacy positional path or by the queue submit() callback.
        The single-transaction SAVEPOINT semantics are identical in both
        modes.
        """
        from collections import Counter

        from app.services.event_bus import event_bus
        from app.services.taxonomy._constants import (
            SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
            SUB_DOMAIN_MIN_CLUSTER_BREADTH,
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH,
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
            SUB_DOMAIN_QUALIFIER_SCALE_RATE,
        )
        from app.utils.text_cleanup import normalize_sub_domain_label

        # 1. Validate domain exists and is a domain node.
        domain = await db.get(PromptCluster, domain_id)
        if domain is None:
            raise ValueError(f"domain not found: {domain_id}")
        if domain.state != "domain":
            raise ValueError(
                f"cluster {domain_id} is not a domain node "
                f"(state='{domain.state}')"
            )

        # 2. Defense-in-depth floor check (Pydantic also enforces ge=0.25
        # at the router layer).
        if (
            min_consistency_override is not None
            and min_consistency_override
            < SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR
        ):
            raise ValueError(
                f"min_consistency_override={min_consistency_override} "
                f"below SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR="
                f"{SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR}"
            )

        # 3. Scan the optimization tree under the domain for raw qualifier
        # signals.  The rebuild is operator-invoked recovery — it must not
        # gate on whether the cascade's vocabulary already "knows" the
        # qualifier (which the discovery cascade does, by design).  Instead
        # parse every ``domain_raw`` directly so a fresh qualifier the
        # operator has just observed in production can become a sub-domain.
        sub_descendant_q = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.parent_id == domain.id,
                PromptCluster.state == "domain",
            )
        )
        parent_ids: list[str] = [domain.id]
        parent_ids.extend(r[0] for r in sub_descendant_q.all())

        cluster_id_q = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.parent_id.in_(parent_ids),
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        cluster_ids: list[str] = [r[0] for r in cluster_id_q.all()]

        opt_rows: list[tuple[str | None, str | None]] = []
        if cluster_ids:
            opt_q = await db.execute(
                select(
                    Optimization.domain_raw,
                    Optimization.cluster_id,
                ).where(Optimization.cluster_id.in_(cluster_ids))
            )
            opt_rows = [(row[0], row[1]) for row in opt_q.all()]

        from app.utils.text_cleanup import parse_domain as _parse_domain
        qualifier_counts: Counter[str] = Counter()
        qualifier_to_cluster_ids: dict[str, set[str]] = {}
        for domain_raw, cluster_id in opt_rows:
            if not domain_raw or cluster_id is None:
                # ``cluster_id is None`` cannot occur given the
                # ``cluster_id.in_(cluster_ids)`` filter, but guard explicitly
                # so the downstream type narrows to ``str``.
                continue
            _, q = _parse_domain(domain_raw)
            if not q:
                continue
            q_norm = q.strip().lower()
            if not q_norm:
                continue
            qualifier_counts[q_norm] += 1
            qualifier_to_cluster_ids.setdefault(q_norm, set()).add(cluster_id)
        total_opts = len(opt_rows)

        # 4. Compute threshold_used.
        if min_consistency_override is not None:
            threshold_used = min_consistency_override
        else:
            threshold_used = max(
                SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
                SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH
                - SUB_DOMAIN_QUALIFIER_SCALE_RATE * total_opts,
            )

        # 5. Identify existing sub-domain labels under this domain.
        existing_q = await db.execute(
            select(PromptCluster.label).where(
                PromptCluster.parent_id == domain.id,
                PromptCluster.state == "domain",
            )
        )
        existing_labels: set[str] = {
            (row[0] or "").lower() for row in existing_q.all() if row[0]
        }

        # 6. Walk eligible qualifiers — apply threshold + breadth gates.
        # Idempotency note: a qualifier whose label already exists as a
        # sub-domain is reported in ``skipped_existing`` whenever it would
        # have been considered (threshold met) — regardless of the breadth
        # gate.  This gives the operator a complete inventory of "already
        # covered" qualifiers, not just the strict subset the discovery
        # cascade would have promoted today.
        proposed: list[str] = []
        skipped_existing: list[str] = []
        eligible: list[tuple[str, str, set[str]]] = []  # (qualifier, sub_label, cluster_ids)
        for qualifier, count in qualifier_counts.most_common():
            if total_opts <= 0:
                continue
            consistency = count / total_opts
            if consistency < threshold_used:
                continue
            sub_label = normalize_sub_domain_label(qualifier)
            if not sub_label:
                continue
            if sub_label in existing_labels:
                if sub_label not in skipped_existing:
                    skipped_existing.append(sub_label)
                continue
            cluster_breadth = len(
                qualifier_to_cluster_ids.get(qualifier, set())
            )
            if cluster_breadth < SUB_DOMAIN_MIN_CLUSTER_BREADTH:
                continue
            proposed.append(sub_label)
            eligible.append(
                (qualifier, sub_label, qualifier_to_cluster_ids.get(qualifier, set())),
            )

        # 7. Create sub-domains (or skip in dry-run mode).
        # Wrap the batch in a SAVEPOINT so a mid-batch failure rolls back
        # any in-flight INSERTs without poisoning the outer session — the
        # caller's prior committed state (and Python references such as
        # ``domain.id``) survive untouched.
        created: list[str] = []

        # Materialize domain identity into plain Python so a SAVEPOINT
        # rollback (which expires ORM-tracked attributes) does not force a
        # refetch of ``domain.id`` / ``domain.label`` later in this method.
        domain_id_str = domain.id
        domain_label_str = domain.label

        if eligible and not dry_run:
            savepoint = await db.begin_nested()
            try:
                for qualifier, sub_label, matching_cluster_ids in eligible:
                    # Pick the largest matching cluster as a seed for keywords.
                    seed_for_keywords: PromptCluster | None = None
                    if matching_cluster_ids:
                        seed_q = await db.execute(
                            select(PromptCluster).where(
                                PromptCluster.id.in_(matching_cluster_ids),
                                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                            ).order_by(PromptCluster.member_count.desc()).limit(1)
                        )
                        seed_for_keywords = seed_q.scalars().first()

                    sub_node, _ = await self._create_domain_node(
                        db, sub_label, existing_labels,
                        seed_cluster=seed_for_keywords,
                        parent_domain_id=domain_id_str,
                    )

                    # Reparent matching clusters under the new sub-domain.
                    for cid in matching_cluster_ids:
                        cluster = await db.get(PromptCluster, cid)
                        if (
                            cluster
                            and cluster.state not in EXCLUDED_STRUCTURAL_STATES
                        ):
                            cluster.parent_id = sub_node.id
                            cluster.domain = domain_label_str

                    created.append(sub_label)
                    existing_labels.add(sub_label)
                await savepoint.commit()
            except Exception:
                # Single-transaction semantics — discard any partial inserts.
                await savepoint.rollback()
                raise

        # 8. Telemetry: always fire sub_domain_rebuild_invoked.
        try:
            get_event_logger().log_decision(
                path="warm",
                op="operator_action",
                decision="sub_domain_rebuild_invoked",
                cluster_id=domain_id_str,
                context={
                    "domain": domain_label_str,
                    "min_consistency_override": min_consistency_override,
                    "threshold_used": threshold_used,
                    "dry_run": dry_run,
                    "proposed_count": len(proposed),
                    "created_count": len(created),
                    "skipped_existing_count": len(skipped_existing),
                },
            )
        except RuntimeError:
            pass

        # 9. Publish taxonomy_changed only when created is non-empty AND
        # not dry_run.
        if created and not dry_run:
            event_bus.publish("taxonomy_changed", {
                "source": "rebuild_sub_domains",
                "domain": domain_label_str,
                "created_sub_domains": list(created),
            })

        return {
            "domain_id": domain_id_str,
            "domain_label": domain_label_str,
            "threshold_used": threshold_used,
            "proposed": list(proposed),
            "created": list(created),
            "skipped_existing": list(skipped_existing),
            "dry_run": dry_run,
        }

    async def dissolve_empty_domain(
        self,
        db: AsyncSession,
        domain_id: str,
    ) -> dict:
        """v0.4.11 P1: operator escape hatch — force-dissolve a ghost domain.

        Bypasses the standard 48h ``DOMAIN_DISSOLUTION_MIN_AGE_HOURS`` gate
        but enforces ``DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES`` (default
        30 min) so an operator can't instantly dissolve a domain the warm
        path just created during organic emergence.

        Idempotent: returns ``dissolved=False`` (with ``reason`` populated)
        when the target is already dissolved, has members, or is younger
        than the floor. Never raises for these conditions — the router
        converts ``reason`` codes into HTTP status as needed.

        Mirrors the v0.4.8 R6 ``rebuild_sub_domains`` operator pattern:
        single transaction, ``_dissolve_node`` as the existing primitive,
        ``domain_ghost_dissolved`` decision event + ``taxonomy_changed``
        SSE event on success only. Wrapped event-bus publish (existing
        pattern); raises ``ValueError`` only if the id is not found.

        Args:
            db: Async SQLAlchemy session.
            domain_id: PromptCluster.id of the domain to dissolve.

        Returns:
            dict with keys: dissolved, domain_id, domain_label, reason,
            age_hours.

        Raises:
            ValueError: domain_id not found.
        """
        from app.services.event_bus import event_bus
        from app.services.taxonomy._constants import (
            DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES,
        )

        # 1. Look up the domain. Missing rows raise ValueError so the
        # router can map to 404 (mirrors rebuild_sub_domains).
        domain = await db.get(PromptCluster, domain_id)
        if domain is None:
            raise ValueError(f"domain not found: {domain_id}")

        # 2. Idempotent: already-dissolved targets return without raising.
        # The archived state is the post-dissolution sentinel _dissolve_node
        # transitions nodes into.
        if domain.state != "domain":
            # Compute age for the response envelope even on the archived
            # path so operators can still see when the original
            # dissolution happened.
            age_hours = 0.0
            if domain.created_at is not None:
                created = domain.created_at
                if created.tzinfo is not None:
                    created = created.replace(tzinfo=None)
                age_hours = (_utcnow() - created).total_seconds() / 3600.0
            return {
                "dissolved": False,
                "domain_id": domain_id,
                "domain_label": None,
                "reason": "already_dissolved",
                "age_hours": round(age_hours, 4),
            }

        # 3. Compute current age for both the response envelope and the
        # min-age floor check.
        now = _utcnow()
        age_hours = 0.0
        if domain.created_at is not None:
            created = domain.created_at
            if created.tzinfo is not None:
                created = created.replace(tzinfo=None)
            age_hours = (now - created).total_seconds() / 3600.0

        domain_label_str = domain.label

        # 4. Empty check — only ghost domains qualify for this hatch.
        if domain.member_count and domain.member_count > 0:
            return {
                "dissolved": False,
                "domain_id": domain_id,
                "domain_label": domain_label_str,
                "reason": "not_empty",
                "age_hours": round(age_hours, 4),
            }

        # 5. Min-age floor — prevents instant dissolution of just-promoted
        # domains during organic emergence.
        min_age_hours = DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES / 60.0
        if age_hours < min_age_hours:
            return {
                "dissolved": False,
                "domain_id": domain_id,
                "domain_label": domain_label_str,
                "reason": "too_young",
                "age_hours": round(age_hours, 4),
            }

        # 6. Resolve the dissolution target — empty ghost domains have no
        # children to reparent in practice, but the existing
        # _dissolve_node primitive defensively reparents any straggler
        # rows so the ``general`` canonical node is the right target.
        from app.services.taxonomy.family_ops import get_canonical_general

        general_node = await get_canonical_general(db)
        if general_node is None:
            # Without a canonical general node we have nowhere to reparent
            # any potential stragglers safely. Treat as a transient
            # condition (matches _reevaluate_domains' early-return
            # behaviour) and report failure without raising.
            return {
                "dissolved": False,
                "domain_id": domain_id,
                "domain_label": domain_label_str,
                "reason": "no_general_node",
                "age_hours": round(age_hours, 4),
            }

        # 7. Perform the dissolution via the existing shared primitive.
        # ``_dissolve_node`` already handles index cleanup, resolver/loader
        # cache invalidation, member_count zeroing, and label release.
        existing_labels: set[str] = {domain.label.lower()}
        await self._dissolve_node(
            db, domain,
            dissolution_target_id=general_node.id,
            existing_labels=existing_labels,
            clear_signal_loader=True,
        )

        # 8. Telemetry — mirrors the rebuild surface's wrapped pattern.
        try:
            get_event_logger().log_decision(
                path="warm",
                op="operator_action",
                decision="domain_ghost_dissolved",
                cluster_id=domain_id,
                context={
                    "domain_label": domain_label_str,
                    "age_hours": round(age_hours, 4),
                    "dissolution_path": "operator_ghost_dissolve",
                },
            )
        except RuntimeError:
            pass

        # 9. Cross-process notification so other engine instances refresh.
        try:
            event_bus.publish("taxonomy_changed", {
                "source": "dissolve_empty_domain",
                "domain": domain_label_str,
                "domain_id": domain_id,
            })
        except Exception:
            # Event bus failure must not undo a successful dissolution.
            pass

        return {
            "dissolved": True,
            "domain_id": domain_id,
            "domain_label": domain_label_str,
            "reason": None,
            "age_hours": round(age_hours, 4),
        }

    async def _dissolve_node(
        self,
        db: AsyncSession,
        node: PromptCluster,
        dissolution_target_id: str,
        existing_labels: set[str],
        clear_signal_loader: bool = False,
    ) -> dict:
        """Shared dissolution logic for both domain and sub-domain nodes.

        Reparents child clusters and direct optimizations to the dissolution
        target, merges meta-patterns (UPDATE not DELETE — prompts never lost),
        archives the node, clears all 4 indices, clears resolver cache, and
        optionally clears DomainSignalLoader (domain-level only).

        Args:
            node: The domain/sub-domain node to dissolve.
            dissolution_target_id: ID of the node to reparent children to
                ("general" for domains, parent domain for sub-domains).
            existing_labels: Label set to discard from (enables re-discovery).
            clear_signal_loader: If True, also remove from DomainSignalLoader
                (domain dissolution only — sub-domains don't have loader entries).

        Returns:
            Dict with keys: clusters_reparented, meta_patterns_merged.
        """
        from sqlalchemy import update as _sa_update

        now = _utcnow()

        # --- Reparent child clusters ---
        child_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == node.id,
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        reparented = 0
        for child in child_q.scalars():
            child.parent_id = dissolution_target_id
            reparented += 1

        # --- Reparent any direct optimizations (defensive) ---
        await db.execute(
            _sa_update(Optimization)
            .where(Optimization.cluster_id == node.id)
            .values(cluster_id=dissolution_target_id)
        )

        # --- Merge meta-patterns into target (UPDATE, not DELETE) ---
        mp_result = await db.execute(
            _sa_update(MetaPattern)
            .where(MetaPattern.cluster_id == node.id)
            .values(cluster_id=dissolution_target_id)
        )
        patterns_merged = mp_result.rowcount  # type: ignore[attr-defined]

        # --- Re-anchor OptimizationPattern provenance to the parent ---
        # Each OptimizationPattern row records where a cluster's pattern was
        # injected into a specific Optimization.  Without this update the
        # provenance points at the now-archived node, stranding the row both
        # for UI attribution and for `_gc_archived_zero_member_clusters`,
        # which refuses to delete archived clusters with live OP references.
        # Update is harmless when there are no rows.
        await db.execute(
            _sa_update(OptimizationPattern)
            .where(OptimizationPattern.cluster_id == node.id)
            .values(cluster_id=dissolution_target_id)
        )

        # --- Archive the node ---
        node.state = "archived"
        node.archived_at = now
        node.member_count = 0
        node.usage_count = 0
        node.avg_score = None
        node.weighted_member_sum = 0.0
        node.scored_count = 0

        # --- Clear all 4 in-memory indices ---
        # Use public properties where available, private attr for optimized
        # (which has no @property wrapper — _optimized_index only).
        for index_name in ("embedding_index", "transformation_index", "_optimized_index", "qualifier_index"):
            try:
                idx = getattr(self, index_name, None)
                if idx:
                    await idx.remove(node.id)
            except (KeyError, ValueError, AttributeError):
                pass

        # --- Clear DomainResolver cache ---
        try:
            from app.services.domain_resolver import get_domain_resolver
            resolver = get_domain_resolver()
            if resolver:
                resolver.remove_label(node.label)
        except (ValueError, Exception):
            pass

        # --- Optionally clear DomainSignalLoader (domain dissolution only) ---
        if clear_signal_loader:
            try:
                from app.services.domain_signal_loader import get_signal_loader
                loader = get_signal_loader()
                if loader:
                    loader.remove_domain(node.label)
            except Exception:
                pass

        # --- Free label for re-discovery ---
        existing_labels.discard(node.label.lower())

        return {
            "clusters_reparented": reparented,
            "meta_patterns_merged": patterns_merged,
        }

    async def _reevaluate_domains(
        self,
        db: AsyncSession,
        existing_labels: set[str],
    ) -> list[str]:
        """Re-evaluate top-level domains and dissolve those with degraded consistency.

        Guards (all must pass for dissolution):
        1. Not "general" (permanent root)
        2. No surviving sub-domains (bottom-up anchor)
        3. Age >= DOMAIN_DISSOLUTION_MIN_AGE_HOURS
        4. Consistency < DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR (Source 1 only)
        5. member_count <= DOMAIN_DISSOLUTION_MEMBER_CEILING

        Returns list of dissolved domain labels.
        """
        from app.services.taxonomy._constants import (
            DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
            DOMAIN_DISSOLUTION_MEMBER_CEILING,
            DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
        )
        from app.utils.text_cleanup import parse_domain as _parse_domain

        dissolved: list[str] = []

        # Find the canonical global "general" domain node as dissolution target.
        from app.services.taxonomy.family_ops import get_canonical_general

        general_node = await get_canonical_general(db)
        if not general_node:
            return dissolved

        # Load all non-general top-level domains
        domain_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label != "general",
            )
        )
        domains = list(domain_q.scalars().all())

        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        now = _utcnow()
        age_cutoff = now - _td(hours=DOMAIN_DISSOLUTION_MIN_AGE_HOURS)

        for domain in domains:
            self._domain_lifecycle_stats["domains_reevaluated"] += 1

            # Guard 1: "general" already excluded by query

            # Guard 2: sub-domain anchor — bottom-up only
            sub_count_q = await db.execute(
                select(func.count()).where(
                    PromptCluster.parent_id == domain.id,
                    PromptCluster.state == "domain",
                )
            )
            sub_count = sub_count_q.scalar() or 0
            if sub_count > 0:
                self._domain_lifecycle_stats["dissolution_blocked"] += 1
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="domain_dissolution_blocked",
                        context={
                            "domain": domain.label,
                            "reason": "has_sub_domains",
                            "sub_domain_count": sub_count,
                        },
                    )
                except RuntimeError:
                    pass
                continue

            # Guard 3: age gate
            created = domain.created_at
            if created is not None:
                if isinstance(created, str):
                    try:
                        created = __import__("datetime").datetime.fromisoformat(created)
                    except (ValueError, TypeError):
                        created = None
                if created is not None and created.tzinfo is not None:
                    created = created.replace(tzinfo=None)
            if created and created > age_cutoff:
                self._domain_lifecycle_stats["dissolution_blocked"] += 1
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="domain_dissolution_blocked",
                        context={
                            "domain": domain.label,
                            "reason": "too_young",
                            "age_hours": round((now - created).total_seconds() / 3600, 1) if created else 0,
                        },
                    )
                except RuntimeError:
                    pass
                continue

            # Guard 5: member ceiling (check before consistency to avoid unnecessary DB queries)
            child_q = await db.execute(
                select(PromptCluster.id).where(
                    PromptCluster.parent_id == domain.id,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )
            child_ids = [r[0] for r in child_q.all()]
            if len(child_ids) > DOMAIN_DISSOLUTION_MEMBER_CEILING:
                self._domain_lifecycle_stats["dissolution_blocked"] += 1
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="domain_dissolution_blocked",
                        context={
                            "domain": domain.label,
                            "reason": "above_member_ceiling",
                            "member_count": len(child_ids),
                            "ceiling": DOMAIN_DISSOLUTION_MEMBER_CEILING,
                        },
                    )
                except RuntimeError:
                    pass
                continue

            # Guard 4: consistency check (Source 1 only — domain_raw primary label)
            if child_ids:
                opt_q = await db.execute(
                    select(Optimization.domain_raw).where(
                        Optimization.cluster_id.in_(child_ids),
                    )
                )
                domain_raws = [r[0] for r in opt_q.all()]
                total_opts = len(domain_raws)

                if total_opts > 0:
                    matching = 0
                    for dr in domain_raws:
                        if not dr:
                            continue
                        primary, _ = _parse_domain(dr)
                        if primary == domain.label.lower():
                            matching += 1
                    consistency = matching / total_opts
                else:
                    consistency = 0.0
            else:
                total_opts = 0
                consistency = 0.0

            # Log re-evaluation
            try:
                get_event_logger().log_decision(
                    path="warm", op="discover",
                    decision="domain_reevaluated",
                    context={
                        "domain": domain.label,
                        "consistency_pct": round(consistency * 100, 1),
                        "floor_pct": round(DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, 1),
                        "member_count": len(child_ids),
                        "member_ceiling": DOMAIN_DISSOLUTION_MEMBER_CEILING,
                        "has_sub_domains": False,
                        "source": "domain_raw",
                        "total_opts": total_opts,
                        "passed": consistency >= DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
                    },
                )
            except RuntimeError:
                pass

            if consistency >= DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR:
                continue  # healthy

            # --- Dissolve ---
            logger.info(
                "Dissolving domain '%s': consistency=%.1f%% < floor=%.1f%%, %d clusters",
                domain.label, consistency * 100,
                DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, len(child_ids),
            )
            result = await self._dissolve_node(
                db, domain, dissolution_target_id=general_node.id,
                existing_labels=existing_labels,
                clear_signal_loader=True,
            )
            dissolved.append(domain.label)
            self._domain_lifecycle_stats["domains_dissolved"] += 1

            try:
                get_event_logger().log_decision(
                    path="warm", op="discover",
                    decision="domain_dissolved",
                    cluster_id=domain.id,
                    context={
                        "domain": domain.label,
                        "consistency_pct": round(consistency * 100, 1),
                        "floor_pct": round(DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, 1),
                        "clusters_reparented": result["clusters_reparented"],
                        "meta_patterns_merged": result["meta_patterns_merged"],
                        "reason": "consistency_below_floor",
                    },
                )
            except RuntimeError:
                pass

        self._domain_lifecycle_stats["last_domain_reeval"] = _dt.now(_tz.utc).isoformat()
        return dissolved

    async def _reevaluate_sub_domains(
        self,
        db: AsyncSession,
        domain_node: PromptCluster,
        existing_labels: set[str],
    ) -> list[str]:
        """Re-evaluate existing sub-domains and dissolve those with degraded consistency.

        For each sub-domain under ``domain_node``:
        1. Skip if younger than ``SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS``,
           or if its ``cluster_metadata.generated_qualifiers`` snapshot is
           empty (R3 — empty snapshot would silently fall through to
           v0.4.6 exact-equality matching; emits
           ``sub_domain_reevaluation_skipped`` for operator visibility).
        2. Gather all optimizations under its child clusters
        3. Re-check qualifier consistency by delegating each opt to the
           shared per-opt matcher
           ``sub_domain_readiness.match_opt_to_sub_domain_vocab`` — Source 1
           (``domain_raw`` qualifier parse), Source 2 (``intent_label`` vs
           organic vocab), Source 2b (legacy parent-keyed substring scan),
           Source 3 (``raw_prompt`` × dynamic ``signal_keywords``). Vocab
           construction (``sub_vocab_groups`` / ``sub_vocab_terms`` /
           ``sub_vocab_tokens`` / ``sub_keywords_legacy`` /
           ``dynamic_keywords``) stays inline here — only the per-opt
           boolean cascade lives in the primitive (R4, audit
           ``docs/audits/sub-domain-regression-2026-04-27.md``). Matching-path
           parity with the create path is required: any asymmetry produces
           dissolve/recreate flip-flop cycles. The dissolution decision uses
           a Bayesian Beta-Binomial posterior (``shrunk_consistency``) rather
           than the raw point estimate — the prior pulls strongly at small N
           (N≤10) and fades at large N (N≥30), preventing single-member
           swings at small populations from triggering spurious dissolution
           (R1, audit ``docs/audits/sub-domain-regression-2026-04-27.md``).
        4. If shrunk consistency < ``SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR``:
           a. Reparent all child clusters to the top-level domain
           b. Merge meta-patterns from sub-domain into parent domain (UPDATE,
              not DELETE — prompts are never lost)
           c. Archive the sub-domain node (state="archived", zero metrics)
           d. Remove from in-memory indices
           e. Log ``sub_domain_dissolved`` event. Both this event and the
              earlier ``sub_domain_reevaluated`` event carry forensic detail
              for audit reconstruction (R5, audit
              ``docs/audits/sub-domain-regression-2026-04-27.md``):
              ``matching_members`` (count of opts the matcher accepted) plus
              up to ``SUB_DOMAIN_FAILURE_SAMPLES`` (=3) ``sample_match_failures``
              entries — each with ``cluster_id`` (UUID, not truncated),
              ``domain_raw``, ``intent_label`` (text fields capped at
              ``SUB_DOMAIN_FAILURE_FIELD_TRUNCATE`` =80 chars), and the
              per-opt ``reason`` returned by the matcher.

        Returns:
            List of dissolved sub-domain labels.
        """
        from app.services.taxonomy._constants import (
            SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
            SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS,
            SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER,
            SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH,
            SUB_DOMAIN_FAILURE_FIELD_TRUNCATE,
            SUB_DOMAIN_FAILURE_SAMPLES,
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH,
            SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
            SUB_DOMAIN_QUALIFIER_SCALE_RATE,
        )

        dissolved: list[str] = []

        # Load existing sub-domains under this domain
        sub_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == domain_node.id,
                PromptCluster.state == "domain",
            )
        )
        sub_domains = list(sub_q.scalars().all())
        if not sub_domains:
            return dissolved

        now = _utcnow()
        age_cutoff = now - __import__("datetime").timedelta(hours=SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS)

        for sub in sub_domains:
            # --- Age gate ---
            created = sub.created_at
            if created is not None:
                if isinstance(created, str):
                    try:
                        created = __import__("datetime").datetime.fromisoformat(created)
                    except (ValueError, TypeError):
                        created = None
                if created is not None and created.tzinfo is not None:
                    created = created.replace(tzinfo=None)
            if created and created > age_cutoff:
                continue  # too young — skip

            # --- Gather all child clusters ---
            # Re-evaluation uses a narrow, sub-qualifier-targeted matcher (not
            # the shared ``compute_qualifier_cascade`` primitive):
            #   * Source 1 accepts ANY ``domain_raw`` parse equal to
            #     ``sub_qualifier`` — no known-vocabulary gate (the discovery
            #     primitive's gate would drop valid matches in domains whose
            #     vocab has not yet been generated).
            #   * Source 2 checks only THIS sub-domain's keywords (not the
            #     best-qualifier-wins cascade) so an opt matching multiple
            #     sub-qualifiers still counts toward each.
            #   * Source 3 only counts dynamic-keyword hits that normalise to
            #     ``sub_qualifier``, ignoring hits for other qualifiers.
            # These targeted semantics are orthogonal to the cascade (which
            # answers "which qualifiers exist in this domain?"), so they stay
            # inlined here. Drift risk is minimal: the engine's *discovery*
            # path is the one that must agree with ``/readiness`` responses.
            child_q = await db.execute(
                select(PromptCluster.id).where(
                    PromptCluster.parent_id == sub.id,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )
            child_ids = [r[0] for r in child_q.all()]
            if not child_ids:
                continue  # empty sub-domain — handled by phase_archive_empty_sub_domains

            opt_q = await db.execute(
                select(
                    Optimization.domain_raw,
                    Optimization.intent_label,
                    Optimization.raw_prompt,
                    Optimization.cluster_id,
                ).where(
                    Optimization.cluster_id.in_(child_ids),
                )
            )
            opt_rows = opt_q.all()
            total_opts = len(opt_rows)
            if total_opts == 0:
                continue

            sub_qualifier = sub.label.lower()

            from app.services.domain_signal_loader import get_signal_loader as _get_loader

            _loader = _get_loader()
            domain_qualifiers = _loader.get_qualifiers(domain_node.label) if _loader else {}
            # Parent-side lookup at the sub-domain LABEL is structurally
            # empty: the parent's qualifier dict is keyed by vocab GROUP
            # names (``observability``, ``metrics``), not by the sub-domain
            # label (``embedding-health``). Kept for backwards-compat with
            # any call site that still consults it; real matching uses
            # ``sub_vocab_*`` below.
            sub_keywords_legacy = domain_qualifiers.get(sub_qualifier, [])

            # Sub-domain's OWN organic vocabulary (Haiku-generated at
            # creation time, stored in cluster_metadata.generated_qualifiers).
            # Shape: ``{group_name: [kebab-case-term, ...], ...}`` — e.g.
            # ``{"instrumentation": ["observability", "tracing", ...], ...}``
            # for the ``embedding-health`` sub-domain.
            sub_meta = read_meta(sub.cluster_metadata)
            sub_generated_qualifiers = sub_meta.get("generated_qualifiers") or {}
            # R3 (audit 2026-04-27): when generated_qualifiers is empty, the sub_vocab_*
            # sets are empty and matching falls back to v0.4.6 exact-equality behavior
            # — guaranteed 0% consistency on healthy sub-domains. Skip rather than
            # fail-open. Emit skip event for operator visibility.
            if not sub_generated_qualifiers:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="discover",
                        decision="sub_domain_reevaluation_skipped",
                        cluster_id=sub.id,
                        context={
                            "domain": domain_node.label,
                            "domain_node_id": domain_node.id,
                            "sub_domain": sub.label,
                            "reason": "empty_vocab_snapshot",
                            "total_opts": total_opts,
                        },
                    )
                except RuntimeError:
                    pass
                continue
            sub_vocab_groups: set[str] = {
                str(k).lower()
                for k in sub_generated_qualifiers.keys()
                if isinstance(k, str)
            }
            sub_vocab_terms: set[str] = {
                str(t).lower()
                for terms in sub_generated_qualifiers.values()
                if isinstance(terms, list)
                for t in terms
                if isinstance(t, str)
            }
            # Tokenized form for substring matching against intent labels
            # (``"Cache Eviction Policy Audit"`` should hit
            # ``cache-instrumentation``'s ``cache`` token).  Drop tokens
            # shorter than 4 chars to suppress noise.
            sub_vocab_tokens: set[str] = set()
            for _term in sub_vocab_terms:
                for _sub in re.split(r"[\s\-_]", _term):
                    if len(_sub) >= 4:
                        sub_vocab_tokens.add(_sub.lower())
            for _grp in sub_vocab_groups:
                if len(_grp) >= 4:
                    sub_vocab_tokens.add(_grp)

            _domain_meta = read_meta(domain_node.cluster_metadata)
            dynamic_keywords: list[tuple[str, float]] = []
            for _item in _domain_meta.get("signal_keywords", []):
                try:
                    _kw, _weight = _item[0], float(_item[1])
                    if isinstance(_kw, str) and len(_kw) >= 3 and _weight >= 0.5:
                        dynamic_keywords.append((_kw, _weight))
                except (IndexError, TypeError, ValueError):
                    continue

            matching = 0
            match_results: list[SubDomainMatchResult] = []
            for domain_raw, intent_label, raw_prompt, _cluster_id in opt_rows:
                match_result = match_opt_to_sub_domain_vocab(
                    domain_raw=domain_raw,
                    intent_label=intent_label,
                    raw_prompt=raw_prompt,
                    sub_qualifier=sub_qualifier,
                    sub_vocab_groups=sub_vocab_groups,
                    sub_vocab_terms=sub_vocab_terms,
                    sub_vocab_tokens=sub_vocab_tokens,
                    sub_keywords_legacy=sub_keywords_legacy,
                    dynamic_keywords=dynamic_keywords,
                )
                match_results.append(match_result)
                if match_result.matched:
                    matching += 1

            sample_failures: list[dict] = []
            for (domain_raw, intent_label, _raw_prompt, cluster_id), result in zip(
                opt_rows, match_results,
            ):
                if result.matched:
                    continue
                if len(sample_failures) >= SUB_DOMAIN_FAILURE_SAMPLES:
                    break
                sample_failures.append({
                    "cluster_id": str(cluster_id) if cluster_id else None,
                    "domain_raw": (
                        (domain_raw or "")[:SUB_DOMAIN_FAILURE_FIELD_TRUNCATE]
                        or None
                    ),
                    "intent_label": (
                        (intent_label or "")[:SUB_DOMAIN_FAILURE_FIELD_TRUNCATE]
                        or None
                    ),
                    "reason": result.reason,
                })

            consistency: float = matching / total_opts

            # R1 (audit 2026-04-27): Bayesian Beta-Binomial shrinkage on the
            # consistency point estimate.  K=10 prior observations centered
            # at 0.40 (= SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW, "absent
            # evidence, assume the lower bound of healthy"). Pulls strongly
            # at small N where one off-topic member can swing raw consistency
            # by 20+ percentage points; fades at N>=30 where the empirical
            # rate dominates. Locks dissolution unreachability shut at large
            # N (see test_large_n_zero_match_still_dissolves).
            k_prior: int = SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH
            alpha_prior: float = k_prior * SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER
            shrunk_consistency: float = (matching + alpha_prior) / (total_opts + k_prior)

            # Adaptive threshold (same formula as creation, for context)
            creation_threshold = max(
                SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
                SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH - SUB_DOMAIN_QUALIFIER_SCALE_RATE * total_opts,
            )

            # Log re-evaluation result
            try:
                get_event_logger().log_decision(
                    path="warm", op="discover",
                    decision="sub_domain_reevaluated",
                    context={
                        "domain": domain_node.label,
                        "sub_domain": sub.label,
                        "consistency_pct": round(consistency * 100, 1),
                        "shrunk_consistency_pct": round(shrunk_consistency * 100, 1),
                        "prior_strength": k_prior,
                        "floor_pct": round(SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, 1),
                        "threshold_pct": round(creation_threshold * 100, 1),
                        "passed": shrunk_consistency >= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR,
                        "total_opts": total_opts,
                        "matching": matching,
                        "matching_members": matching,
                        "sample_match_failures": sample_failures,
                    },
                )
            except RuntimeError:
                pass

            if shrunk_consistency >= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR:
                continue  # healthy — keep

            # --- Dissolve via shared method ---
            result = await self._dissolve_node(
                db, sub, dissolution_target_id=domain_node.id,
                existing_labels=existing_labels,
                clear_signal_loader=False,
            )
            reparented = result["clusters_reparented"]
            patterns_merged = result["meta_patterns_merged"]

            dissolved.append(sub.label)

            logger.info(
                "Dissolved sub-domain '%s' under '%s': "
                "consistency=%.1f%% (shrunk=%.1f%%) < floor=%.1f%%, "
                "%d clusters reparented, %d patterns merged",
                sub.label, domain_node.label,
                consistency * 100, shrunk_consistency * 100,
                SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100,
                reparented, patterns_merged,
            )

            try:
                get_event_logger().log_decision(
                    path="warm", op="discover",
                    decision="sub_domain_dissolved",
                    cluster_id=sub.id,
                    context={
                        "domain": domain_node.label,
                        "sub_domain": sub.label,
                        "consistency_pct": round(consistency * 100, 1),
                        "shrunk_consistency_pct": round(shrunk_consistency * 100, 1),
                        "prior_strength": k_prior,
                        "floor_pct": round(SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR * 100, 1),
                        "clusters_reparented": reparented,
                        "meta_patterns_merged": patterns_merged,
                        "reason": "qualifier_consistency_below_floor",
                        "matching_members": matching,
                        "sample_match_failures": sample_failures,
                    },
                )
            except RuntimeError:
                pass

        return dissolved

    async def _detect_domain_candidates(self, db: AsyncSession) -> None:
        """Detect near-threshold clusters that may become domains soon."""
        from app.services.pipeline_constants import (
            DOMAIN_DISCOVERY_CANDIDATE_MIN_COHERENCE,
            DOMAIN_DISCOVERY_CANDIDATE_MIN_MEMBERS,
            DOMAIN_DISCOVERY_MIN_MEMBERS,
        )
        from app.services.taxonomy.family_ops import get_canonical_general

        general = await get_canonical_general(db)
        if not general:
            return

        near_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == general.id,
                PromptCluster.state.in_(["active", "mature"]),
                PromptCluster.member_count >= DOMAIN_DISCOVERY_CANDIDATE_MIN_MEMBERS,
                PromptCluster.member_count < DOMAIN_DISCOVERY_MIN_MEMBERS,
                PromptCluster.coherence >= DOMAIN_DISCOVERY_CANDIDATE_MIN_COHERENCE,
            )
        )
        for candidate in near_q.scalars():
            logger.info(
                "Domain candidate detected: '%s' (members=%d/%d, coherence=%.2f)",
                candidate.label, candidate.member_count or 0,
                DOMAIN_DISCOVERY_MIN_MEMBERS, candidate.coherence or 0,
            )
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("domain_candidate_detected", {
                    "label": candidate.label,
                    "member_count": candidate.member_count or 0,
                    "threshold": DOMAIN_DISCOVERY_MIN_MEMBERS,
                    "coherence": candidate.coherence or 0,
                })
            except Exception as _dcd_exc:
                logger.debug("Failed to publish domain_candidate_detected event: %s", _dcd_exc)

    async def _extract_domain_keywords(
        self, db: AsyncSession, cluster: PromptCluster, top_k: int = 15,
    ) -> list:
        """Extract top TF-IDF keywords from member prompts.

        Two query modes depending on the input cluster's role:

        * **Domain node** (``state == "domain"``): aggregates ``raw_prompt``
          texts across **all active/mature descendant clusters**.  Domain
          nodes never own optimizations directly — their ``member_count``
          is the count of child clusters, not opts.  The previous
          implementation queried ``Optimization.cluster_id == domain.id``
          and consistently returned 0 rows, so every refresh persisted
          an empty ``signal_keywords`` list and the cascade's
          ``tf_idf`` source was permanently silent
          (``source_breakdown.tf_idf == 0`` across all domains in the
          live audit).

        * **Regular cluster** (any other state): pulls opts directly via
          ``cluster_id == cluster.id`` — same as the previous
          implementation, kept for the domain-creation seed-cluster
          call site (``_create_domain_node``).
        """
        from sklearn.feature_extraction.text import TfidfVectorizer

        if cluster.state == "domain":
            # Aggregate across ALL active/mature descendants — direct
            # children AND clusters parented under sub-domains (which
            # carry ``state="domain"`` themselves).  A two-pass walk
            # collects descendant cluster ids (one query per tier),
            # then a single ``raw_prompt`` query joins on those ids.
            # SQLite via aiosqlite has limited recursive-CTE ergonomics
            # in SQLAlchemy 2.0 (especially with mapped classes), so
            # the explicit two-pass walk is more portable than a
            # ``WITH RECURSIVE`` CTE — and the depth is bounded at 2
            # in production today (sub-sub-domains are not created by
            # any code path).  The walk is iterative so deeper trees
            # are still correct without recursion-limit risk.
            descendant_ids: set[str] = set()
            frontier: set[str] = {cluster.id}
            for _depth in range(8):  # paranoia: cap at 8 hops
                if not frontier:
                    break
                child_q = await db.execute(
                    select(PromptCluster.id, PromptCluster.state).where(
                        PromptCluster.parent_id.in_(frontier),
                    )
                )
                next_frontier: set[str] = set()
                for cid, cstate in child_q.all():
                    if cstate in ("active", "mature"):
                        descendant_ids.add(cid)
                    elif cstate == "domain":
                        # Sub-domain node — recurse to its children.
                        next_frontier.add(cid)
                frontier = next_frontier
            if not descendant_ids:
                return []
            result = await db.execute(
                select(Optimization.raw_prompt).where(
                    Optimization.cluster_id.in_(descendant_ids),
                )
            )
        else:
            result = await db.execute(
                select(Optimization.raw_prompt).where(
                    Optimization.cluster_id == cluster.id,
                )
            )
        texts = [row[0] for row in result if row[0]]
        if not texts:
            return []

        try:
            vectorizer = TfidfVectorizer(
                max_features=top_k, stop_words="english", ngram_range=(1, 2),
            )
            tfidf = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()
            scores = tfidf.mean(axis=0).A1
            ranked = sorted(
                zip(feature_names, scores), key=lambda x: x[1], reverse=True,
            )
            # Min-max normalize the top-K mean scores so the top keyword
            # gets weight 1.0 and the K-th gets the relative ratio.
            # Without this, raw TF-IDF means cluster in 0.05-0.20 range
            # because each per-document TF-IDF row is L2-normalized — so
            # the cascade's ``weight >= 0.5`` admit gate filters out
            # *every* keyword and the ``tf_idf`` cascade source is
            # structurally silent (live audit: 0 hits across all
            # domains).  Normalization also makes the
            # ``raw_weight >= 0.8`` 1-hit/2-hit threshold meaningful:
            # top-1 keyword fires on a single prompt mention; mid-rank
            # keywords need 2 mentions to qualify.
            top_score = float(ranked[0][1]) if ranked else 0.0
            if top_score <= 0.0:
                return []
            return [
                [kw, round(float(score) / top_score, 2)]
                for kw, score in ranked[:top_k]
            ]
        except Exception:
            logger.warning(
                "TF-IDF extraction failed for cluster %s", cluster.id,
                exc_info=True,
            )
            return []

    async def _create_domain_node(
        self,
        db: AsyncSession,
        label: str,
        existing_domains: set[str],
        seed_cluster: PromptCluster | None = None,
        general_node_id: str | None = None,
        parent_domain_id: str | None = None,
    ) -> tuple[PromptCluster, int]:
        """Create a new domain node with a maximally distant color.

        Args:
            db: Async DB session.
            label: Domain label (e.g. "marketing" or "saas-pricing").
            existing_domains: Set of existing domain labels (for color computation).
            seed_cluster: The cluster that triggered this domain discovery.
            general_node_id: ID of the "general" domain node (for top-level reparenting).
            parent_domain_id: If set, creates a sub-domain parented to this domain.

        Returns:
            The newly created PromptCluster domain node.
        """

        from app.services.taxonomy.coloring import (
            compute_max_distance_color,
            resolve_seed_palette_color,
        )

        # Gather existing domain colors for max-distance computation
        color_q = await db.execute(
            select(PromptCluster.color_hex).where(
                PromptCluster.state == "domain",
                PromptCluster.color_hex.isnot(None),
            )
        )
        existing_colors = [row[0] for row in color_q.all() if row[0]]
        if parent_domain_id:
            # Sub-domain: derive color from parent's base (same hue, darker).
            # If the parent's color_hex is NULL (cold path hasn't run yet),
            # compute a temporary parent color via max-distance then derive
            # from that — prevents fallback to an unrelated random color.
            from app.services.taxonomy.coloring import derive_sub_domain_color
            parent_color_q = await db.execute(
                select(PromptCluster.color_hex).where(PromptCluster.id == parent_domain_id)
            )
            parent_color = parent_color_q.scalar()
            if not parent_color:
                # Parent domain hasn't been assigned a color yet.
                # Compute one now and persist it so all future sub-domains
                # of this parent will be consistent.
                parent_color = compute_max_distance_color(existing_colors)
                await db.execute(
                    update(PromptCluster)
                    .where(PromptCluster.id == parent_domain_id)
                    .values(color_hex=parent_color)
                )
                logger.info(
                    "Assigned color %s to parent domain %s (was NULL)",
                    parent_color, parent_domain_id,
                )
            color_hex = derive_sub_domain_color(parent_color)
        else:
            # Top-level domain: brand-anchored seed color when the label
            # is canonical (backend/frontend/etc. — see SEED_PALETTE),
            # otherwise maximally distinct from all existing domains.
            # Preserves palette identity across dissolution/re-promotion
            # cycles so "backend is always purple" stays true.
            seed_color = resolve_seed_palette_color(label)
            if seed_color and seed_color not in existing_colors:
                color_hex = seed_color
                logger.info(
                    "Assigned seed palette color %s to domain '%s' (re-promotion)",
                    color_hex, label,
                )
            else:
                color_hex = compute_max_distance_color(existing_colors)

        # Extract TF-IDF keywords from seed cluster
        keywords: list = []
        signal_member_count = 0
        if seed_cluster is not None:
            keywords = await self._extract_domain_keywords(db, seed_cluster)
            signal_member_count = seed_cluster.member_count or 0

        label = label.lower()  # Domain labels are always lowercase
        node = PromptCluster(
            label=label,
            state="domain",
            domain=label,
            task_type="general",
            parent_id=parent_domain_id,  # None for top-level, domain ID for sub-domains
            persistence=1.0,
            color_hex=color_hex,
            centroid_embedding=seed_cluster.centroid_embedding if seed_cluster else None,
            cluster_metadata=write_meta(
                None,
                source="discovered",
                signal_keywords=keywords,
                discovered_at=datetime.now(timezone.utc).isoformat(),
                proposed_by_snapshot=None,
                signal_member_count_at_generation=signal_member_count,
            ),
        )
        db.add(node)
        await db.flush()

        logger.info(
            "Created discovered domain node: label=%s color=%s id=%s keywords=%d",
            label, color_hex, node.id, len(keywords),
        )

        # Re-parent matching clusters from "general" to the new domain
        reparented = 0
        if general_node_id:
            reparented = await self._reparent_to_domain(
                db, node, label, general_node_id,
            )
            if reparented:
                await self._backfill_optimization_domain(db, node)
                # Position the new domain node near its children so the
                # topology visualization starts from a meaningful location
                # instead of a random hash-based fallback.
                await self._set_domain_umap_from_children(db, node)

        try:
            from app.services.event_bus import event_bus
            event_bus.publish("domain_created", {
                "label": label,
                "color_hex": color_hex,
                "node_id": node.id,
                "source": "discovered",
            })
        except Exception as _dc_evt_exc:
            logger.warning("Failed to publish domain_created event for '%s': %s", label, _dc_evt_exc)

        return node, reparented

    async def _reparent_to_domain(
        self,
        db: AsyncSession,
        domain_node: PromptCluster,
        label: str,
        general_id: str,
    ) -> int:
        """Re-parent clusters from 'general' to the new domain."""
        candidates = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == general_id,
                PromptCluster.state.in_(["active", "mature", "candidate"]),
                PromptCluster.domain == "general",
            )
        )
        reparented = 0
        for cluster in candidates.scalars():
            opts = await db.execute(
                select(Optimization.domain_raw).where(
                    Optimization.cluster_id == cluster.id,
                )
            )
            raw_domains = [row[0] for row in opts if row[0]]
            if not raw_domains:
                continue
            primaries = [parse_domain(d)[0] for d in raw_domains]
            match_count = sum(1 for p in primaries if p == label)
            if match_count / len(primaries) >= 0.6:
                cluster.parent_id = domain_node.id
                cluster.domain = label
                reparented += 1

        if reparented:
            logger.info(
                "Re-parented %d clusters from 'general' to '%s'",
                reparented, label,
            )
        return reparented

    async def _backfill_optimization_domain(
        self, db: AsyncSession, domain_node: PromptCluster,
    ) -> int:
        """Update Optimization.domain for re-parented clusters."""
        from sqlalchemy import update

        result = await db.execute(
            update(Optimization)
            .where(
                Optimization.cluster_id.in_(
                    select(PromptCluster.id).where(
                        PromptCluster.parent_id == domain_node.id,
                    )
                ),
                Optimization.domain == "general",
            )
            .values(domain=domain_node.label)
        )
        if result.rowcount:  # type: ignore[attr-defined]
            logger.info(
                "Backfilled %d optimizations from 'general' to '%s'",
                result.rowcount, domain_node.label,  # type: ignore[attr-defined]
            )
        return result.rowcount  # type: ignore[attr-defined]

    async def _set_domain_umap_from_children(
        self, db: AsyncSession, domain_node: PromptCluster,
    ) -> None:
        """Set a domain node's UMAP position as the centroid of its children.

        Called after domain creation + reparenting so the topology
        visualization starts from a semantically meaningful position
        instead of a hash-based random fallback.  Also called during
        warm-path reconciliation and cold-path refit for domain nodes
        that still lack UMAP coordinates.

        Two matching strategies (tried in order):
        1. ``domain`` field — works for top-level domains where children
           have ``cluster.domain == domain_node.label``.
        2. ``parent_id`` fallback — works for sub-domains where children
           have ``cluster.domain == parent_domain`` (not the sub-domain
           label) but ``cluster.parent_id == sub_domain_node.id``.
        """
        from sqlalchemy import func as sa_func

        _umap_filter = [
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            PromptCluster.umap_x.isnot(None),
            PromptCluster.umap_y.isnot(None),
            PromptCluster.umap_z.isnot(None),
        ]

        # Strategy 1: match by domain field (top-level domains)
        row = (await db.execute(
            select(
                sa_func.avg(PromptCluster.umap_x),
                sa_func.avg(PromptCluster.umap_y),
                sa_func.avg(PromptCluster.umap_z),
                sa_func.count(),
            ).where(
                PromptCluster.domain == domain_node.label,
                *_umap_filter,
            )
        )).one_or_none()

        # Strategy 2: fall back to parent_id match (sub-domains)
        if (not row or row[0] is None) and domain_node.id:
            row = (await db.execute(
                select(
                    sa_func.avg(PromptCluster.umap_x),
                    sa_func.avg(PromptCluster.umap_y),
                    sa_func.avg(PromptCluster.umap_z),
                    sa_func.count(),
                ).where(
                    PromptCluster.parent_id == domain_node.id,
                    *_umap_filter,
                )
            )).one_or_none()

        if row and row[0] is not None:
            domain_node.umap_x = float(row[0])
            domain_node.umap_y = float(row[1])
            domain_node.umap_z = float(row[2])

            # Single-child domains: offset the domain node so it doesn't
            # sit at the exact same UMAP position as its only child.
            # Without this, the force simulation's parent-child spring and
            # UMAP anchor cancel out, rendering both nodes on top of each
            # other in the topology graph.
            # Offset of 1.0 in UMAP space = 10.0 scene units after
            # UMAP_SCALE (frontend TopologyData.ts), which is slightly
            # above the PARENT_REST_LEN (9.0) — enough for the spring
            # to find equilibrium at a visible distance.
            child_count = int(row[3])
            if child_count == 1:
                domain_node.umap_x += 1.0
                domain_node.umap_y += 0.5

            logger.info(
                "Set domain '%s' UMAP from %d children: (%.3f, %.3f, %.3f)",
                domain_node.label, child_count,
                domain_node.umap_x, domain_node.umap_y, domain_node.umap_z,
            )

    # ------------------------------------------------------------------
    # Risk detection (ADR-004 Section 8B)
    # ------------------------------------------------------------------

    async def _suggest_domain_archival(self, db: AsyncSession) -> list[str]:
        """Identify low-activity top-level domains for potential archival.

        Sub-domains are excluded — they are automatically archived by
        ``phase_archive_empty_sub_domains()`` in Phase 5.5 of the warm path.
        """
        from datetime import timedelta

        from sqlalchemy import and_, or_

        from app.services.pipeline_constants import (
            DOMAIN_ARCHIVAL_IDLE_DAYS,
            DOMAIN_ARCHIVAL_MIN_USAGE,
        )

        cutoff = _utcnow() - timedelta(days=DOMAIN_ARCHIVAL_IDLE_DAYS)

        # Pre-compute domain IDs for sub-domain detection
        domain_id_q = await db.execute(
            select(PromptCluster.id).where(PromptCluster.state == "domain")
        )
        domain_id_set = set(domain_id_q.scalars().all())

        stale = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                or_(
                    PromptCluster.member_count == 0,
                    and_(
                        or_(
                            PromptCluster.last_used_at < cutoff,
                            PromptCluster.last_used_at.is_(None),
                        ),
                        PromptCluster.usage_count < DOMAIN_ARCHIVAL_MIN_USAGE,
                    ),
                ),
            )
        )
        suggestions = []
        for domain in stale.scalars():
            # Skip sub-domains — handled by phase_archive_empty_sub_domains()
            if domain.parent_id and domain.parent_id in domain_id_set:
                continue
            suggestions.append(domain.label)
            logger.info(
                "Domain archival suggested: '%s' (members=%d, usage=%d)",
                domain.label, domain.member_count or 0, domain.usage_count or 0,
            )
        if suggestions:
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("domain_archival_suggested", {"labels": suggestions})
            except Exception as _das_exc:
                logger.debug("Failed to publish domain_archival_suggested event: %s", _das_exc)
        return suggestions

    async def _check_signal_staleness(self, db: AsyncSession) -> list[PromptCluster]:
        """Identify domains whose TF-IDF signals need regeneration."""
        from app.services.pipeline_constants import SIGNAL_REFRESH_MEMBER_RATIO

        stale = []
        domains = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        for domain in domains.scalars():
            meta = read_meta(domain.cluster_metadata)
            gen_count = meta["signal_member_count_at_generation"]
            if gen_count == 0:
                continue
            if (domain.member_count or 0) >= gen_count * SIGNAL_REFRESH_MEMBER_RATIO:
                stale.append(domain)
                logger.info(
                    "Signal staleness detected: domain '%s' generated at %d members, now has %d",
                    domain.label, gen_count, domain.member_count or 0,
                )
        return stale

    async def _refresh_domain_signals(
        self, db: AsyncSession, domain: PromptCluster,
    ) -> None:
        """Regenerate TF-IDF keywords for a domain with stale signals."""

        keywords = await self._extract_domain_keywords(db, domain)
        domain.cluster_metadata = write_meta(
            domain.cluster_metadata,
            signal_keywords=keywords,
            signal_generated_at=datetime.now(timezone.utc).isoformat(),
            signal_member_count_at_generation=domain.member_count or 0,
        )
        logger.info(
            "Signals refreshed for domain '%s': %d keywords from %d members",
            domain.label, len(keywords), domain.member_count or 0,
        )
        try:
            from app.services.event_bus import event_bus
            event_bus.publish("domain_signals_refreshed", {
                "label": domain.label,
                "keyword_count": len(keywords),
            })
        except Exception as _dsr_exc:
            logger.debug("Failed to publish domain_signals_refreshed event: %s", _dsr_exc)

    async def _monitor_general_health(self, db: AsyncSession) -> None:
        """Log diagnostic metrics for the 'general' domain."""
        from app.services.pipeline_constants import (
            DOMAIN_DISCOVERY_MIN_COHERENCE,
            DOMAIN_DISCOVERY_MIN_MEMBERS,
        )
        from app.services.taxonomy.family_ops import get_canonical_general

        general_node = await get_canonical_general(db)
        if not general_node:
            return

        child_count = await db.scalar(
            select(func.count()).where(
                PromptCluster.parent_id == general_node.id,
                PromptCluster.state.in_(["active", "mature"]),
            )
        ) or 0

        near_threshold = await db.scalar(
            select(func.count()).where(
                PromptCluster.parent_id == general_node.id,
                PromptCluster.state.in_(["active", "mature"]),
                PromptCluster.member_count >= DOMAIN_DISCOVERY_MIN_MEMBERS - 2,
                PromptCluster.coherence >= DOMAIN_DISCOVERY_MIN_COHERENCE - 0.1,
            )
        ) or 0

        opt_count = await db.scalar(
            select(func.count()).where(Optimization.domain == "general"),
        ) or 0

        logger.info(
            "General domain health: %d child clusters, %d near discovery threshold, "
            "%d total optimizations",
            child_count, near_threshold, opt_count,
        )
        if opt_count > 50 and child_count > 5 and near_threshold == 0:
            logger.warning(
                "General domain stagnation: %d optimizations across %d clusters "
                "but none near threshold. Consider lowering "
                "DOMAIN_DISCOVERY_MIN_MEMBERS (current=%d) or "
                "DOMAIN_DISCOVERY_MIN_COHERENCE (current=%.2f).",
                opt_count, child_count,
                DOMAIN_DISCOVERY_MIN_MEMBERS, DOMAIN_DISCOVERY_MIN_COHERENCE,
            )

    async def _create_warm_snapshot(
        self,
        db: AsyncSession,
        *,
        q_system: float | None,
        operations: list[dict],
        ops_attempted: int,
        ops_accepted: int,
    ) -> TaxonomySnapshot:
        """Create a warm-path snapshot with current metrics."""
        from app.services.taxonomy.snapshot import create_snapshot

        # Load non-domain/non-archived nodes for metrics (Fix #10)
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
            )
        )
        active_nodes = list(result.scalars().all())

        mean_coherence, separation = self._snapshot_metrics(active_nodes)

        # Compute member-weighted q_health for snapshot persistence
        q_health_val = None
        try:
            _health = self._compute_q_health_from_nodes(
                active_nodes, silhouette=self._last_silhouette,
            )
            q_health_val = _health.q_health
        except Exception as _qh_exc:
            logger.warning("q_health computation failed in warm snapshot: %s", _qh_exc)

        nodes_created = sum(1 for op in operations if op.get("type") == "emerge")
        nodes_merged = sum(1 for op in operations if op.get("type") == "merge")
        nodes_retired = sum(1 for op in operations if op.get("type") == "retire")
        nodes_split = sum(1 for op in operations if op.get("type") == "split")

        return await create_snapshot(
            db,
            trigger="warm_path",
            q_system=q_system,
            q_coherence=mean_coherence,
            q_separation=separation,
            q_coverage=1.0,
            q_health=q_health_val,
            operations=operations,
            nodes_created=nodes_created,
            nodes_retired=nodes_retired,
            nodes_merged=nodes_merged,
            nodes_split=nodes_split,
        )

    # ------------------------------------------------------------------
    # Domain mapping — delegated to matching.py
    # ------------------------------------------------------------------

    async def map_domain(
        self,
        domain_raw: str,
        db: AsyncSession,
        applied_pattern_ids: list[str] | None = None,
    ) -> TaxonomyMapping:
        """Map a free-text domain string to the nearest active PromptCluster.

        Delegates to :func:`matching.map_domain`.
        """
        return await _map_domain(
            domain_raw, db, self._embedding, applied_pattern_ids
        )

    # ------------------------------------------------------------------
    # Read API (Spec Section 6.3)
    # ------------------------------------------------------------------

    async def get_tree(
        self,
        db: AsyncSession,
        min_persistence: float = 0.0,
    ) -> list[dict]:
        # Return all lifecycle states including archived — the ClusterNavigator's
        # "archived" tab filters on the frontend side from this full set.
        # The topology component further filters to non-archived for rendering.
        query = select(PromptCluster).where(
            PromptCluster.state.in_(["active", "candidate", "mature", "domain", "archived", "project"])
        )
        if min_persistence > 0:
            query = query.where(PromptCluster.persistence >= min_persistence)
        result = await db.execute(query)
        nodes = result.scalars().all()

        # Precompute meta-pattern counts per cluster (single GROUP BY query)
        from app.models import MetaPattern
        pattern_count_q = await db.execute(
            select(MetaPattern.cluster_id, func.count().label("cnt"))
            .group_by(MetaPattern.cluster_id)
        )
        pattern_counts: dict[str, int] = dict(pattern_count_q.all())  # type: ignore[arg-type]

        tree = []
        for n in nodes:
            d = self._node_to_dict(n)
            d["meta_pattern_count"] = pattern_counts.get(n.id, 0)
            tree.append(d)
        return tree

    async def get_node(
        self,
        node_id: str,
        db: AsyncSession,
    ) -> dict | None:
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.id == node_id)
        )
        node = result.scalar_one_or_none()
        if not node:
            return None
        node_dict = self._node_to_dict(node)
        # Add children
        children_result = await db.execute(
            select(PromptCluster).where(PromptCluster.parent_id == node_id)
        )
        children = children_result.scalars().all()
        node_dict["children"] = [self._node_to_dict(c) for c in children]
        # Add breadcrumb
        node_dict["breadcrumb"] = await build_breadcrumb(db, node)
        return node_dict

    @staticmethod
    def _snapshot_metrics(
        active_nodes: list[PromptCluster],
    ) -> tuple[float, float]:
        """Compute mean coherence and mean separation for snapshot creation.

        Shared by warm-path and cold-path snapshot callers to avoid
        duplicated centroid-extraction and metric-computation logic.

        Returns:
            (mean_coherence, mean_separation)
        """
        from app.services.taxonomy.clustering import compute_mean_separation

        coherences = [n.coherence for n in active_nodes if n.coherence is not None]
        mean_coherence = float(np.mean(coherences)) if coherences else 0.0

        centroids: list[np.ndarray] = []
        for n in active_nodes:
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)  # type: ignore[arg-type]
                centroids.append(c)
            except (ValueError, TypeError) as _sm_exc:
                logger.warning(
                    "Corrupt centroid in snapshot metrics, cluster='%s': %s",
                    n.label, _sm_exc,
                )
                continue

        separation = compute_mean_separation(centroids) if len(centroids) >= 2 else 1.0
        return mean_coherence, separation

    def _invalidate_stats_cache(self) -> None:
        """Clear all stats cache entries after a warm or cold path mutation."""
        self._stats_cache.clear()
        self._stats_cache_time.clear()

    @staticmethod
    def _stats_scope_clause(project_id: str | None):
        """Return a SQLAlchemy WHERE clause matching the B6 tree filter.

        Mirrors ``GET /api/clusters/tree?project_id=X`` semantics: include the
        project node itself, all domain nodes (structural skeleton), and any
        cluster whose ``dominant_project_id`` matches. Returns ``None`` when
        ``project_id`` is ``None`` (global scope — no filter).
        """
        if project_id is None:
            return None
        return (
            (PromptCluster.dominant_project_id == project_id)
            | (PromptCluster.id == project_id)
            | (PromptCluster.state == "domain")
        )

    async def get_stats(
        self, db: AsyncSession, project_id: str | None = None,
    ) -> dict:
        """Return system/taxonomy health metrics.

        ADR-005 B6 — When ``project_id`` is provided, counts (active, candidate,
        mature, archived), tree depth/leaf metrics, total families and live
        ``q_health`` are scoped to the target project + structural skeleton.
        Snapshot-derived series (``q_history``, ``q_sparkline``, ``q_system``)
        remain global since ``TaxonomySnapshot`` is system-wide. The cache is
        keyed per ``project_id`` (including ``None``) so scoped and global
        callers don't pollute each other.
        """
        now = time.monotonic()
        cached = self._stats_cache.get(project_id)
        cached_at = self._stats_cache_time.get(project_id, 0.0)
        if cached is not None and (now - cached_at) < self._STATS_CACHE_TTL:
            return cached

        scope_clause = self._stats_scope_clause(project_id)

        # Node state counts via GROUP BY (avoids loading full ORM objects + blobs)
        state_stmt = select(
            PromptCluster.state, func.count(PromptCluster.id),
        ).group_by(PromptCluster.state)
        if scope_clause is not None:
            state_stmt = state_stmt.where(scope_clause)
        state_result = await db.execute(state_stmt)
        state_counts: dict[str, int] = dict(state_result.all())  # type: ignore[arg-type]
        active = state_counts.get("active", 0)
        candidate = state_counts.get("candidate", 0)
        mature = state_counts.get("mature", 0)
        archived = state_counts.get("archived", 0)

        # max_depth + leaf_count: lightweight projection (id + parent_id + state only)
        tree_stmt = select(
            PromptCluster.id, PromptCluster.parent_id, PromptCluster.state,
        )
        if scope_clause is not None:
            tree_stmt = tree_stmt.where(scope_clause)
        tree_result = await db.execute(tree_stmt)
        tree_rows = tree_result.all()
        id_to_parent: dict[str, str | None] = {r.id: r.parent_id for r in tree_rows}

        max_depth = 0
        for node_id in id_to_parent:
            depth = 0
            current_id = node_id
            visited: set[str] = {node_id}
            while True:
                pid = id_to_parent.get(current_id)
                if not pid or pid in visited:
                    break
                visited.add(pid)
                depth += 1
                current_id = pid
            if depth > max_depth:
                max_depth = depth

        active_ids = {r.id for r in tree_rows if r.state != "archived"}
        parent_ids = {r.parent_id for r in tree_rows if r.parent_id}
        leaf_count = sum(1 for nid in active_ids if nid not in parent_ids)

        # Total pattern families (leaf clusters with a parent) via scalar COUNT
        fam_stmt = select(func.count(PromptCluster.id)).where(
            PromptCluster.parent_id.isnot(None)
        )
        if scope_clause is not None:
            fam_stmt = fam_stmt.where(scope_clause)
        fam_count_result = await db.execute(fam_stmt)
        total_families = fam_count_result.scalar() or 0

        # Recent snapshots (last 30, ascending chronological)
        from app.services.taxonomy.snapshot import get_snapshot_history

        recent = await get_snapshot_history(db, limit=30)
        # recent is newest-first; reverse for chronological sparkline
        snapshots = list(reversed(recent))

        # Latest snapshot metrics (newest = recent[0])
        latest = recent[0] if recent else None
        q_system = latest.q_system if latest else None
        q_coherence = latest.q_coherence if latest else None
        q_separation = latest.q_separation if latest else None
        q_coverage = latest.q_coverage if latest else None
        q_dbcv = latest.q_dbcv if latest else None

        # A5: Q is undefined when fewer than 2 active (non-structural) nodes
        # exist in the current (live) taxonomy — even if a stale snapshot
        # persisted a coerced 0.0. Surface None so the UI chip + API consumers
        # can render "—" instead of a misleading number.
        _live_active_count = active + candidate + mature
        if _live_active_count < 2:
            q_system = None

        # Sparkline history — use q_health (member-weighted) exclusively.
        # Older snapshots that predate q_health and rejected cold path
        # snapshots (q_health=None) are skipped rather than falling back
        # to q_system, which is on a different scale (0.78 vs 0.66) and
        # creates misleading oscillations in the sparkline.
        q_values = [
            s.q_health
            for s in snapshots
            if s.q_health is not None
        ]
        sparkline = compute_sparkline_data(q_values)

        q_history = []
        for snap in snapshots:
            try:
                ops = json.loads(snap.operations) if snap.operations else []
            except (ValueError, TypeError):
                ops = []
            q_history.append(
                {
                    "timestamp": snap.created_at.isoformat() if snap.created_at else None,
                    "q_system": snap.q_system,
                    "operations": len(ops),
                }
            )

        # last_warm_path and last_cold_path timestamps (scan newest-first)
        last_warm_path: str | None = None
        last_cold_path: str | None = None
        for snap in recent:  # already newest-first from get_snapshot_history
            if last_warm_path is None and snap.trigger == "warm_path":
                last_warm_path = snap.created_at.isoformat() if snap.created_at else None
            if last_cold_path is None and snap.trigger == "cold_path":
                last_cold_path = snap.created_at.isoformat() if snap.created_at else None
            if last_warm_path is not None and last_cold_path is not None:
                break

        result = {
            "q_system": q_system,
            "q_coherence": q_coherence,
            "q_separation": q_separation,
            "q_coverage": q_coverage,
            "q_dbcv": q_dbcv,
            "total_families": total_families,
            "nodes": {
                "active": active,
                "candidate": candidate,
                "mature": mature,
                "archived": archived,
                "max_depth": max_depth,
                "leaf_count": leaf_count,
            },
            "q_history": q_history,
            # Raw values (0–1 scale) — frontend uses fixedRange=[0,1] for
            # absolute rendering. Normalized values caused tiny fluctuations
            # to look like catastrophic drops.
            "q_sparkline": sparkline.raw_values,
            "q_trend": sparkline.trend,
            "q_current": sparkline.current if sparkline.point_count > 0 else None,
            "q_min": sparkline.min if sparkline.point_count > 0 else None,
            "q_max": sparkline.max if sparkline.point_count > 0 else None,
            "q_point_count": sparkline.point_count,
            "last_warm_path": last_warm_path,
            "last_cold_path": last_cold_path,
            "warm_path_age": self._warm_path_age,
        }

        # Compute q_health live from current cluster state.
        # Pre-set None defaults so dict shape is always consistent.
        result["q_health"] = None
        result["q_health_coherence_w"] = None
        result["q_health_separation_w"] = None
        result["q_health_weights"] = None
        result["q_health_total_members"] = None
        result["q_health_cluster_count"] = None
        try:
            _health_stmt = select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
            )
            if project_id is not None:
                # Scoped q_health — in-project clusters only (drop the structural
                # domain/project nodes that the wider scope clause lets through).
                _health_stmt = _health_stmt.where(
                    PromptCluster.dominant_project_id == project_id
                )
            _health_nodes_q = await db.execute(_health_stmt)
            _health_nodes = list(_health_nodes_q.scalars().all())
            _health_result = self._compute_q_health_from_nodes(
                _health_nodes, silhouette=self._last_silhouette,
            )
            result["q_health"] = _health_result.q_health
            result["q_health_coherence_w"] = _health_result.coherence_weighted
            result["q_health_separation_w"] = _health_result.separation_weighted
            result["q_health_weights"] = _health_result.weights
            result["q_health_total_members"] = _health_result.total_members
            result["q_health_cluster_count"] = _health_result.cluster_count
        except Exception as qh_exc:
            logger.debug("q_health computation failed (non-fatal): %s", qh_exc)

        self._stats_cache[project_id] = result
        self._stats_cache_time[project_id] = time.monotonic()

        return result

    async def increment_usage(self, cluster_id: str, db: AsyncSession) -> None:
        """Increment usage on the cluster and propagate up the taxonomy tree.

        Spec Section 7.8 — usage count flows upward so that ancestor
        clusters reflect aggregate activity from their subtree.

        Uses atomic SQL UPDATE (``usage_count = usage_count + 1``) instead of
        Python field mutation to prevent lost writes when multiple optimizations
        complete concurrently and increment the same cluster.

        Args:
            cluster_id: ID of the PromptCluster whose patterns were applied.
            db: Async SQLAlchemy session.
        """
        from sqlalchemy import update as sa_update

        cluster = await db.get(PromptCluster, cluster_id)
        if not cluster:
            logger.warning("increment_usage: cluster %s not found", cluster_id)
            return

        now = _utcnow()

        # Atomic increment on the target cluster
        await db.execute(
            sa_update(PromptCluster)
            .where(PromptCluster.id == cluster_id)
            .values(
                usage_count=PromptCluster.usage_count + 1,
                last_used_at=now,
            )
        )

        # Walk up the taxonomy tree with atomic increments (cycle guard)
        parent_id = cluster.parent_id
        visited: set[str] = set()
        while parent_id:
            if parent_id in visited:
                logger.warning("increment_usage: cycle detected at node %s", parent_id)
                break
            visited.add(parent_id)
            parent = await db.get(PromptCluster, parent_id)
            if not parent:
                break
            await db.execute(
                sa_update(PromptCluster)
                .where(PromptCluster.id == parent_id)
                .values(
                    usage_count=PromptCluster.usage_count + 1,
                    last_used_at=now,
                )
            )
            parent_id = parent.parent_id

        await db.flush()

        # Refresh to get the updated value for logging
        await db.refresh(cluster)
        logger.info(
            "Usage incremented: '%s' (usage=%d, domain=%s)",
            cluster.label, cluster.usage_count, cluster.domain,
        )

    # ------------------------------------------------------------------
    # Tree integrity verification and auto-repair
    # ------------------------------------------------------------------

    async def verify_domain_tree_integrity(self, db: AsyncSession) -> list[str]:
        """Post-migration and periodic integrity check.

        Returns a list of violation descriptions. Empty = healthy.
        """
        from sqlalchemy import text

        violations = []

        # 1. Check for duplicate domain labels under the same parent
        # Multi-project: same label under different parents is valid (ADR-005)
        result = await db.execute(
            select(PromptCluster.parent_id, PromptCluster.label, func.count()).where(
                PromptCluster.state == "domain"
            ).group_by(PromptCluster.parent_id, PromptCluster.label)
        )
        for parent_id, label, count in result:
            if count > 1:
                violations.append(
                    f"Duplicate domain label: '{label}' appears {count} times "
                    f"under parent {parent_id}"
                )

        # 2. Check for orphaned clusters (parent_id points to non-existent node)
        orphans = await db.execute(text("""
            SELECT c.id, c.label, c.parent_id
            FROM prompt_cluster c
            LEFT JOIN prompt_cluster p ON c.parent_id = p.id
            WHERE c.parent_id IS NOT NULL AND p.id IS NULL
        """))
        for row in orphans:
            violations.append(
                f"Orphan cluster: '{row[1]}' (id={row[0]}) references missing parent {row[2]}"
            )

        # 3. Check domain nodes have persistence=1.0
        weak = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.persistence < 1.0,
            )
        )
        for d in weak.scalars():
            violations.append(
                f"Domain node '{d.label}' has persistence={d.persistence} (expected 1.0)"
            )

        # 4. Check for self-referencing parents
        self_refs = await db.execute(
            text("SELECT id, label FROM prompt_cluster WHERE parent_id = id")
        )
        for row in self_refs:
            violations.append(
                f"Self-referencing parent: '{row[1]}' (id={row[0]})"
            )

        # 5. Every non-domain cluster's domain field must match a domain node label
        #    Case-insensitive: analyzer may emit "Backend" while domain label is "backend"
        mismatch_result = await db.execute(text("""
            SELECT c.id, c.label, c.domain
            FROM prompt_cluster c
            WHERE c.state != 'domain'
              AND LOWER(c.domain) NOT IN (SELECT LOWER(label) FROM prompt_cluster WHERE state = 'domain')
              AND c.domain IS NOT NULL
        """))
        for row in mismatch_result:
            violations.append(
                f"Domain mismatch: cluster '{row[1]}' has domain='{row[2]}' "
                f"which is not a domain node"
            )

        # 6. Non-domain clusters must only have domain node parents
        non_domain_parent_q = await db.execute(
            select(PromptCluster.id, PromptCluster.label, PromptCluster.parent_id).where(
                PromptCluster.state != "domain",
                PromptCluster.parent_id.isnot(None),
            )
        )
        domain_ids: set[str] = set(
            (await db.execute(
                select(PromptCluster.id).where(PromptCluster.state == "domain")
            )).scalars().all()
        )
        for row in non_domain_parent_q:
            if row[2] not in domain_ids:  # row[2] = parent_id
                violations.append(
                    f"Non-domain parent: '{row[1]}' (id={row[0][:8]}) "
                    f"has parent {row[2][:8]} which is not a domain node"
                )

        # 7. Archived clusters with active usage AND actual members should not be archived
        # (clusters with usage but 0 members are ghosts — stale usage from before reassignment)
        archived_used_q = await db.execute(
            select(PromptCluster.id, PromptCluster.label, PromptCluster.usage_count).where(
                PromptCluster.state == "archived",
                PromptCluster.usage_count > 0,
                PromptCluster.member_count > 0,
            )
        )
        for row in archived_used_q:
            violations.append(
                f"Archived with usage: '{row[1]}' (id={row[0][:8]}) "
                f"has usage_count={row[2]}"
            )

        # 8. Empty sub-domains — domain nodes parented to another domain
        #    with 0 active children.  These are typically orphaned by cold
        #    path refits and will be archived by Phase 5.5.
        for did in domain_ids:
            d_node = await db.get(PromptCluster, did)
            if not d_node or d_node.state != "domain":
                continue
            if d_node.parent_id not in domain_ids:
                continue  # Not a sub-domain
            child_count_q = await db.execute(
                select(func.count()).where(
                    PromptCluster.parent_id == did,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )
            if (child_count_q.scalar() or 0) == 0:
                violations.append(
                    f"Empty sub-domain: '{d_node.label}' (id={did[:8]}) "
                    f"has 0 active children"
                )

        if violations:
            for v in violations:
                logger.error("Tree integrity violation: %s", v)
        else:
            logger.info("Domain tree integrity check passed")

        return violations

    async def _repair_tree_violations(
        self, db: AsyncSession, violations: list[str]
    ) -> int:
        """Auto-repair detected violations. Returns count of repairs.

        Emits one ``tree_integrity_repair`` taxonomy_activity event per
        repaired row so operators can see what structural damage the warm
        path has silently healed (I-8).
        """
        from sqlalchemy import text, update

        repaired = 0

        def _emit(
            violation_type: str,
            action: str,
            cluster_id: str | None,
            label: str | None = None,
            extra: dict | None = None,
        ) -> None:
            """Emit one repair event; swallow RuntimeError if logger absent."""
            ctx: dict = {
                "violation_type": violation_type,
                "action": action,
            }
            if label is not None:
                ctx["label"] = label
            if extra:
                ctx.update(extra)
            try:
                get_event_logger().log_decision(
                    path="warm",
                    op="tree_integrity_repair",
                    decision="repaired",
                    cluster_id=cluster_id,
                    context=ctx,
                )
            except RuntimeError:
                pass

        # Repair weak domain persistence — select-then-update so each repair
        # can emit its own event with the affected cluster id/label.
        weak_nodes = (await db.execute(
            select(PromptCluster.id, PromptCluster.label, PromptCluster.persistence).where(
                PromptCluster.state == "domain",
                PromptCluster.persistence < 1.0,
            )
        )).all()
        if weak_nodes:
            await db.execute(
                update(PromptCluster)
                .where(PromptCluster.state == "domain", PromptCluster.persistence < 1.0)
                .values(persistence=1.0)
            )
            logger.info("Auto-repaired %d domain nodes with weak persistence", len(weak_nodes))
            repaired += len(weak_nodes)
            for row in weak_nodes:
                _emit(
                    violation_type="weak_persistence",
                    action="persistence_set_to_1.0",
                    cluster_id=row[0],
                    label=row[1],
                    extra={"previous_persistence": row[2]},
                )

        # Repair orphaned clusters → re-parent under canonical global "general"
        from app.services.taxonomy.family_ops import get_canonical_general

        general_node = await get_canonical_general(db)
        if general_node is not None:
            orphan_rows = (await db.execute(text("""
                SELECT c.id, c.label, c.parent_id
                FROM prompt_cluster c
                WHERE c.parent_id IS NOT NULL
                  AND c.parent_id NOT IN (SELECT id FROM prompt_cluster)
                  AND c.state != 'domain'
            """))).all()
            if orphan_rows:
                await db.execute(
                    text("""
                        UPDATE prompt_cluster
                        SET parent_id = :general_id, domain = 'general'
                        WHERE parent_id IS NOT NULL
                          AND parent_id NOT IN (SELECT id FROM prompt_cluster)
                          AND state != 'domain'
                    """),
                    {"general_id": general_node.id},
                )
                logger.info("Auto-repaired %d orphaned clusters → 'general'", len(orphan_rows))
                repaired += len(orphan_rows)
                for row in orphan_rows:
                    _emit(
                        violation_type="orphan_cluster",
                        action="reparented_to_general",
                        cluster_id=row[0],
                        label=row[1],
                        extra={"missing_parent_id": row[2]},
                    )

        # Repair domain mismatches.  Default fallback is "general", but when
        # the cluster's parent_id points to a live top-level domain node, use
        # that domain's label instead.  This preserves taxonomy locality after
        # sub-domain dissolution: a child cluster of `backend → embedding-health`
        # whose sub-domain just dissolved becomes parented to `backend`, and
        # its `domain` field should track the new parent rather than collapse
        # to `general`.  Sub-domain children (state != 'domain', parent is a
        # sub-domain) walk one level up to the top-level domain — the canonical
        # storage convention is `domain = top_level_label` (sub-domain identity
        # lives in parent_id only).
        mismatch_rows = (await db.execute(text("""
            SELECT c.id, c.label, c.domain, c.parent_id
            FROM prompt_cluster c
            WHERE c.state != 'domain'
              AND LOWER(c.domain) NOT IN (SELECT LOWER(label) FROM prompt_cluster WHERE state = 'domain')
              AND c.domain IS NOT NULL
        """))).all()
        if mismatch_rows:
            # Build parent_id → top_level_domain_label map once.  Walk up to
            # the root domain so a cluster parented to a sub-domain inherits
            # the top-level label (sub-domain children carry the top-level
            # `domain` field, not the sub-domain label).
            domain_nodes = (await db.execute(text("""
                SELECT id, label, parent_id FROM prompt_cluster WHERE state = 'domain'
            """))).all()
            id_to_node = {row[0]: row for row in domain_nodes}
            top_level_label_for: dict[str, str] = {}
            for did, dlabel, dparent in domain_nodes:
                cur_id, cur_label = did, dlabel
                visited: set[str] = set()
                while id_to_node.get(cur_id) and id_to_node[cur_id][2] is not None:
                    if cur_id in visited:
                        break  # cycle defense
                    visited.add(cur_id)
                    parent_row = id_to_node.get(id_to_node[cur_id][2])
                    if not parent_row:
                        break
                    cur_id, cur_label = parent_row[0], parent_row[1]
                top_level_label_for[did] = cur_label

            repaired_to_general = 0
            repaired_to_parent = 0
            for cid, clabel, prev_domain, parent_id in mismatch_rows:
                target = top_level_label_for.get(parent_id) or "general"
                await db.execute(
                    text("UPDATE prompt_cluster SET domain = :d WHERE id = :i"),
                    {"d": target, "i": cid},
                )
                if target == "general":
                    repaired_to_general += 1
                else:
                    repaired_to_parent += 1
                _emit(
                    violation_type="domain_mismatch",
                    action=(
                        "domain_reset_to_general"
                        if target == "general"
                        else "domain_reset_to_parent"
                    ),
                    cluster_id=cid,
                    label=clabel,
                    extra={"previous_domain": prev_domain, "new_domain": target},
                )
            repaired += len(mismatch_rows)
            logger.info(
                "Auto-repaired %d domain mismatches (→parent: %d, →general: %d)",
                len(mismatch_rows), repaired_to_parent, repaired_to_general,
            )

        # Repair self-referencing parents → re-parent under matching domain node
        self_ref_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == PromptCluster.id,
            )
        )
        for node in self_ref_q.scalars():
            domain_label = (node.domain or "general").lower().split(":")[0].strip()
            domain_node = (await db.execute(
                select(PromptCluster).where(
                    PromptCluster.state == "domain",
                    func.lower(PromptCluster.label) == domain_label,
                )
            )).scalar()
            if domain_node and domain_node.id != node.id:
                node.parent_id = domain_node.id
                repaired += 1
                logger.info("Repaired self-ref parent: '%s' → domain '%s'", node.label, domain_label)
                _emit(
                    violation_type="self_reference",
                    action=f"reparented_to_domain:{domain_label}",
                    cluster_id=node.id,
                    label=node.label,
                    extra={"target_domain_id": domain_node.id},
                )

        # Repair non-domain clusters with non-domain parents
        domain_id_map: dict[str, str] = {}
        for row in (await db.execute(
            select(PromptCluster.id, PromptCluster.label).where(PromptCluster.state == "domain")
        )):
            domain_id_map[row[1].lower()] = row[0]

        non_domain_parent_q2 = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state != "domain",
                PromptCluster.parent_id.isnot(None),
                PromptCluster.parent_id.notin_(list(domain_id_map.values())),
            )
        )
        reparented = 0
        for node in non_domain_parent_q2.scalars():
            domain_label = (node.domain or "general").lower().split(":")[0].strip()
            target = domain_id_map.get(domain_label) or domain_id_map.get("general")
            if target and target != node.parent_id:
                previous_parent_id = node.parent_id
                node.parent_id = target
                reparented += 1
                _emit(
                    violation_type="non_domain_parent",
                    action=f"reparented_to_domain:{domain_label}",
                    cluster_id=node.id,
                    label=node.label,
                    extra={
                        "previous_parent_id": previous_parent_id,
                        "target_domain_id": target,
                    },
                )
        if reparented:
            repaired += reparented
            logger.info("Repaired %d non-domain parent relationships", reparented)

        # Unarchive clusters with active usage AND actual content.
        # Clusters with usage_count > 0 but member_count == 0 are ghosts —
        # their content was reassigned and the usage is stale. Only unarchive
        # when members exist to back the usage data.
        archived_used = (await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "archived",
                PromptCluster.usage_count > 0,
                PromptCluster.member_count > 0,
            )
        )).scalars().all()
        for node in archived_used:
            node.state = "active"
            node.archived_at = None
            repaired += 1
            logger.info(
                "Unarchived cluster with usage: '%s' (usage=%d, members=%d)",
                node.label, node.usage_count, node.member_count,
            )
            _emit(
                violation_type="archived_with_usage",
                action="unarchived",
                cluster_id=node.id,
                label=node.label,
                extra={
                    "usage_count": node.usage_count,
                    "member_count": node.member_count,
                },
            )

        # NOTE: do NOT commit here — the warm path handles commit/rollback
        # after the Q_system non-regression gate.  Committing prematurely
        # would bypass the quality gate rollback mechanism.
        return repaired

    @staticmethod
    def _node_to_dict(node: PromptCluster) -> dict:
        from app.services.taxonomy._constants import (
            CLUSTERING_BLEND_W_OPTIMIZED,
            CLUSTERING_BLEND_W_TRANSFORM,
        )
        from app.services.taxonomy.cluster_meta import read_meta

        meta = read_meta(node.cluster_metadata)
        out_coh = meta.get("output_coherence")

        # Compute effective blend weights for this cluster
        w_opt = CLUSTERING_BLEND_W_OPTIMIZED
        if out_coh is not None and out_coh < 0.5:
            w_opt = CLUSTERING_BLEND_W_OPTIMIZED * max(0.25, out_coh / 0.5)
        w_raw = 1.0 - w_opt - CLUSTERING_BLEND_W_TRANSFORM

        return {
            "id": node.id,
            "label": node.label,
            "parent_id": node.parent_id,
            "state": node.state,
            "domain": node.domain,
            "task_type": node.task_type,
            "member_count": node.member_count or 0,
            "coherence": node.coherence,
            "separation": node.separation,
            "stability": node.stability,
            "persistence": node.persistence,
            "color_hex": node.color_hex,
            "umap_x": node.umap_x,
            "umap_y": node.umap_y,
            "umap_z": node.umap_z,
            "usage_count": node.usage_count or 0,
            "avg_score": node.avg_score,
            "preferred_strategy": node.preferred_strategy,
            "promoted_at": node.promoted_at.isoformat() if node.promoted_at else None,
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "output_coherence": out_coh,
            "blend_w_raw": round(w_raw, 4),
            "blend_w_optimized": round(w_opt, 4),
            "blend_w_transform": CLUSTERING_BLEND_W_TRANSFORM,
            "split_failures": meta.get("split_failures", 0),
            "dominant_project_id": getattr(node, "dominant_project_id", None),
        }
