"""Unified context enrichment service for all routing tiers.

Single entry point replacing 5 scattered context resolution sites.
Each tier calls enrich() and receives an EnrichedContext with all
resolved layers — workspace guidance, codebase context, adaptation,
applied patterns, performance signals, and heuristic analysis.

Heuristic analysis runs for ALL tiers (not just passthrough) to provide
domain detection for curated retrieval cross-domain filtering.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.heuristic_analyzer import HeuristicAnalysis, HeuristicAnalyzer
from app.services.workspace_intelligence import WorkspaceIntelligence

logger = logging.getLogger(__name__)


async def resolve_performance_signals(
    db: AsyncSession,
    task_type: str,
    domain: str,
) -> str | None:
    """Resolve performance signals: strategy perf by domain, anti-patterns, domain keywords.

    Standalone function — callable from both the enrichment service (instance method)
    and the sampling pipeline (no instance needed). Cheap signals (~150 tokens) from
    the Optimization table, no LLM calls.
    """
    try:
        from sqlalchemy import func, select

        from app.models import Optimization

        lines: list[str] = []

        # 1. Strategy performance by domain+task_type (top 3)
        perf_q = await db.execute(
            select(
                Optimization.strategy_used,
                func.avg(Optimization.overall_score).label("avg_score"),
                func.count().label("n"),
            ).where(
                Optimization.task_type == task_type,
                Optimization.domain == domain,
                Optimization.overall_score.isnot(None),
                Optimization.strategy_used.isnot(None),
            ).group_by(Optimization.strategy_used)
            .having(func.count() >= 3)
            .order_by(func.avg(Optimization.overall_score).desc())
            .limit(3)
        )
        top_strategies = perf_q.all()
        if top_strategies:
            strat_parts = [
                f"{r.strategy_used} ({r.avg_score:.1f}, n={r.n})"
                for r in top_strategies
            ]
            lines.append(
                f"Top strategies for {domain}+{task_type}: "
                + ", ".join(strat_parts)
            )

        # 2. Anti-patterns: strategies whose OVERALL average is below 5.5
        #    for this task_type+domain combo
        anti_q = await db.execute(
            select(
                Optimization.strategy_used,
                func.avg(Optimization.overall_score).label("avg_score"),
                func.count().label("n"),
            ).where(
                Optimization.task_type == task_type,
                Optimization.domain == domain,
                Optimization.overall_score.isnot(None),
                Optimization.strategy_used.isnot(None),
            ).group_by(Optimization.strategy_used)
            .having(func.count() >= 3, func.avg(Optimization.overall_score) < 5.5)
            .order_by(func.avg(Optimization.overall_score).asc())
            .limit(2)
        )
        anti_patterns = anti_q.all()
        if anti_patterns:
            for r in anti_patterns:
                lines.append(
                    f"Avoid: {r.strategy_used} averaged {r.avg_score:.1f} "
                    f"for {domain}+{task_type} (n={r.n})"
                )

        # 3. Domain keywords from DomainSignalLoader singleton
        try:
            from app.services.domain_signal_loader import get_signal_loader
            loader = get_signal_loader()
            if loader:
                domain_signals = loader.signals.get(domain, [])
                if domain_signals:
                    keywords = [kw for kw, _weight in domain_signals[:8]]
                    lines.append(
                        f"Domain vocabulary: {', '.join(keywords)}"
                    )
        except Exception:
            pass  # DomainSignalLoader may not be initialized

        return "\n".join(lines) if lines else None
    except Exception:
        logger.debug("Performance signals resolution failed", exc_info=True)
        return None


@dataclass(frozen=True)
class EnrichedContext:
    """All resolved context layers for an optimization request."""

    raw_prompt: str
    workspace_guidance: str | None = None
    codebase_context: str | None = None
    adaptation_state: str | None = None
    applied_patterns: str | None = None
    performance_signals: str | None = None
    analysis: HeuristicAnalysis | None = None
    context_sources: MappingProxyType[str, bool] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    enrichment_meta: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
    )

    # -- Convenience accessors (avoid repeated null-guard boilerplate) --

    @property
    def task_type(self) -> str:
        """Task type from heuristic analysis, defaulting to 'general'."""
        return self.analysis.task_type if self.analysis else "general"

    @property
    def domain_value(self) -> str:
        """Domain from heuristic analysis, defaulting to 'general'."""
        return self.analysis.domain if self.analysis else "general"

    @property
    def intent_label(self) -> str:
        """Intent label from heuristic analysis, defaulting to 'general'."""
        return self.analysis.intent_label if self.analysis else "general"

    @property
    def analysis_summary(self) -> str | None:
        """Formatted analysis summary for template injection."""
        return self.analysis.format_summary() if self.analysis else None

    @property
    def context_sources_dict(self) -> dict[str, Any]:
        """Plain dict copy of context_sources + enrichment metadata for JSON/DB serialization."""
        result: dict[str, Any] = dict(self.context_sources)
        if self.enrichment_meta:
            result["enrichment_meta"] = dict(self.enrichment_meta)
        return result


class ContextEnrichmentService:
    """Unified enrichment orchestrator for all routing tiers."""

    def __init__(
        self,
        prompts_dir: Path,      # noqa: ARG002 — reserved for future template use
        data_dir: Path,         # noqa: ARG002 — reserved for future preferences use
        workspace_intel: WorkspaceIntelligence,
        embedding_service: Any,          # EmbeddingService
        heuristic_analyzer: HeuristicAnalyzer,
        github_client: Any,              # GitHubClient
        taxonomy_engine: Any | None = None,
    ) -> None:
        self._workspace_intel = workspace_intel
        self._embedding_service = embedding_service
        self._heuristic_analyzer = heuristic_analyzer
        self._github_client = github_client
        self._taxonomy_engine = taxonomy_engine

    async def enrich(
        self,
        raw_prompt: str,
        tier: str,
        db: AsyncSession,
        workspace_path: str | None = None,
        mcp_ctx: Any | None = None,
        repo_full_name: str | None = None,
        repo_branch: str | None = None,
        applied_pattern_ids: list[str] | None = None,
        preferences_snapshot: dict | None = None,
    ) -> EnrichedContext:
        """Resolve all context layers for the given tier.

        ``preferences_snapshot``, when provided, gates optional layers:
        - ``enable_adaptation``: if ``False``, skip adaptation state resolution.

        Content capping is applied inline: ``codebase_context`` is capped at
        ``MAX_CODEBASE_CONTEXT_CHARS`` and wrapped in ``<untrusted-context>``;
        ``adaptation_state`` is capped at ``MAX_ADAPTATION_CHARS``.
        """
        import time as _time
        _t_enrich_start = _time.monotonic()
        prefs = preferences_snapshot or {}

        # 1. Workspace guidance — ALL tiers, same path
        guidance = await self._resolve_workspace_guidance(mcp_ctx, workspace_path)

        # 2. Analysis — heuristic (zero-LLM) for all tiers.
        #    Passthrough uses this as the primary analysis. Internal/sampling
        #    tiers use it only for domain detection in curated retrieval
        #    (the LLM analyze phase runs later with richer classification).
        analysis: HeuristicAnalysis | None = None
        task_type: str | None = None
        try:
            analysis = await self._heuristic_analyzer.analyze(raw_prompt, db)
            task_type = analysis.task_type
        except Exception:
            logger.debug("Heuristic analysis failed during enrichment", exc_info=True)
            analysis = HeuristicAnalysis(
                task_type="general", domain="general",
                intent_label="general optimization", confidence=0.0,
            )
            task_type = "general"

        # 3. Codebase context — available for ALL tiers when repo is linked.
        #    Two layers: (a) cached Haiku synthesis (architectural overview, computed
        #    once on link/reindex), (b) per-prompt curated retrieval (file-specific
        #    outlines ranked by semantic similarity to the prompt).
        codebase_context: str | None = None
        enrichment_meta_dict: dict[str, Any] = {}
        if repo_full_name:
            # Resolve branch from LinkedRepo if not explicitly provided
            if not repo_branch:
                try:
                    from sqlalchemy import select as _sel_br

                    from app.models import LinkedRepo
                    _lr_q = await db.execute(
                        _sel_br(LinkedRepo).where(
                            LinkedRepo.full_name == repo_full_name,
                        ).limit(1)
                    )
                    _lr = _lr_q.scalar_one_or_none()
                    repo_branch = (_lr.branch or _lr.default_branch) if _lr else "main"
                except Exception:
                    repo_branch = "main"
            branch = repo_branch

            enrichment_meta_dict["repo_full_name"] = repo_full_name
            enrichment_meta_dict["repo_branch"] = branch

            # 3a. Cached explore synthesis (architectural context)
            explore_synthesis = await self._get_explore_synthesis(
                repo_full_name, branch, db,
            )
            enrichment_meta_dict["explore_synthesis"] = {
                "present": explore_synthesis is not None,
                "char_count": len(explore_synthesis) if explore_synthesis else 0,
            }

            # 3b. Per-prompt curated index retrieval
            curated_text, curated_meta = await self._query_index_context(
                repo_full_name, branch, raw_prompt,
                task_type, analysis.domain if analysis else None, db,
            )
            enrichment_meta_dict["curated_retrieval"] = curated_meta

            # Combine: synthesis first (overview), then curated (prompt-specific)
            if explore_synthesis and curated_text:
                codebase_context = f"{explore_synthesis}\n\n---\n\n{curated_text}"
            elif explore_synthesis:
                codebase_context = explore_synthesis
            elif curated_text:
                codebase_context = curated_text

        # 4. Adaptation state — ALL tiers, task_type-aware
        #    Skipped when preferences explicitly disable adaptation.
        adaptation: str | None = None
        effective_task_type = task_type or "general"
        if prefs.get("enable_adaptation", True):
            adaptation = await self._resolve_adaptation(db, effective_task_type)

        # 5. Applied patterns — ALL tiers
        patterns = await self._resolve_patterns(
            raw_prompt, applied_pattern_ids, db,
        )

        # 5b. Performance signals — strategy perf, domain keywords, anti-patterns
        perf_signals = await self._resolve_performance_signals(
            db, effective_task_type,
            analysis.domain if analysis else "general",
        )

        # 6. Content capping and injection hardening
        codebase_context = self._cap_codebase_context(codebase_context)
        adaptation = self._cap_adaptation_state(adaptation)

        # 6b. Enrichment metadata — track truncation
        if enrichment_meta_dict:
            combined_chars = len(codebase_context) if codebase_context else 0
            enrichment_meta_dict["combined_context_chars"] = combined_chars
            enrichment_meta_dict["was_truncated"] = (
                combined_chars >= settings.MAX_CODEBASE_CONTEXT_CHARS
            )

        # 7. Context sources audit (frozen via MappingProxyType)
        sources = MappingProxyType({
            "workspace_guidance": guidance is not None,
            "codebase_context": codebase_context is not None,
            "adaptation": adaptation is not None,
            "applied_patterns": patterns is not None,
            "heuristic_analysis": analysis is not None,
            "performance_signals": perf_signals is not None,
        })

        # 8. Log enrichment summary
        _enrich_ms = (_time.monotonic() - _t_enrich_start) * 1000

        # Compute assembled context size for observability
        _total_context = sum(
            len(s) for s in [guidance, codebase_context, adaptation, patterns, perf_signals]
            if s
        )

        if repo_full_name:
            _cr = enrichment_meta_dict.get("curated_retrieval", {})
            logger.info(
                "enrichment: tier=%s repo=%s explore=%s curated=%s "
                "curated_files=%d curated_top=%.3f context_chars=%d "
                "signals=%s total_assembled=%dK elapsed=%.0fms",
                tier, repo_full_name,
                "yes" if enrichment_meta_dict.get("explore_synthesis", {}).get("present") else "no",
                _cr.get("status", "n/a"),
                _cr.get("files_included", 0),
                _cr.get("top_relevance_score", 0.0),
                enrichment_meta_dict.get("combined_context_chars", 0),
                "perf" if perf_signals else "none",
                _total_context // 1000,
                _enrich_ms,
            )
        else:
            logger.info(
                "enrichment: tier=%s no_repo signals=%s "
                "total_assembled=%dK elapsed=%.0fms",
                tier, "perf" if perf_signals else "none",
                _total_context // 1000, _enrich_ms,
            )

        return EnrichedContext(
            raw_prompt=raw_prompt,
            workspace_guidance=guidance,
            codebase_context=codebase_context,
            adaptation_state=adaptation,
            applied_patterns=patterns,
            performance_signals=perf_signals,
            analysis=analysis,
            context_sources=sources,
            enrichment_meta=MappingProxyType(enrichment_meta_dict) if enrichment_meta_dict else MappingProxyType({}),
        )

    async def _resolve_workspace_guidance(
        self, mcp_ctx: Any | None, workspace_path: str | None,
    ) -> str | None:
        """Resolve workspace guidance via MCP roots or filesystem path."""
        roots: list[Path] = []

        if mcp_ctx:
            try:
                roots_result = await mcp_ctx.session.list_roots()
                for root in roots_result.roots:
                    uri = str(root.uri)
                    if uri.startswith("file://"):
                        roots.append(Path(uri.removeprefix("file://")))
                if roots:
                    logger.debug("Resolved %d workspace roots via MCP", len(roots))
            except Exception:
                logger.debug("MCP roots/list unavailable")

        if not roots and workspace_path:
            wp = Path(workspace_path)
            if wp.is_dir():
                roots = [wp]

        if not roots:
            return None

        try:
            return self._workspace_intel.analyze(roots)
        except Exception:
            logger.debug("Workspace guidance resolution failed", exc_info=True)
            return None

    async def _get_explore_synthesis(
        self,
        repo_full_name: str,
        branch: str,
        db: AsyncSession,
    ) -> str | None:
        """Load cached Haiku architectural synthesis from RepoIndexMeta."""
        try:
            from sqlalchemy import select

            from app.models import RepoIndexMeta
            meta_q = await db.execute(
                select(RepoIndexMeta.explore_synthesis).where(
                    RepoIndexMeta.repo_full_name == repo_full_name,
                    RepoIndexMeta.branch == branch,
                    RepoIndexMeta.explore_synthesis.isnot(None),
                )
            )
            return meta_q.scalar()
        except Exception:
            logger.debug("Explore synthesis lookup failed", exc_info=True)
            return None

    async def _query_index_context(
        self,
        repo_full_name: str,
        branch: str,
        raw_prompt: str,
        task_type: str | None,
        domain: str | None,
        db: AsyncSession,
    ) -> tuple[str | None, dict]:
        """Query pre-built index for curated codebase context.

        Returns ``(context_text, retrieval_metadata)`` tuple.
        Metadata is always populated (with ``status`` field) for observability.
        """
        try:
            from app.services.repo_index_service import RepoIndexService
            svc = RepoIndexService(db, self._github_client, self._embedding_service)
            result = await svc.query_curated_context(
                repo_full_name, branch, raw_prompt,
                task_type=task_type, domain=domain,
            )
            if result:
                meta = {
                    "status": "ready",
                    "files_included": result.files_included,
                    "total_files_indexed": result.total_files_indexed,
                    "top_relevance_score": round(result.top_relevance_score, 3),
                    "index_freshness": result.index_freshness,
                    "files": result.selected_files,
                    # Retrieval diagnostics
                    "stop_reason": result.stop_reason,
                    "budget_used_pct": round(
                        result.budget_used_chars / max(result.budget_max_chars, 1) * 100, 1,
                    ),
                    "budget_used_chars": result.budget_used_chars,
                    "budget_max_chars": result.budget_max_chars,
                    "diversity_excluded": result.diversity_excluded_count,
                    "near_misses": result.near_misses,
                }
                return result.context_text, meta
            return None, {"status": "empty", "files_included": 0}
        except Exception as exc:
            logger.warning("Curated index retrieval failed for %s@%s: %s", repo_full_name, branch, exc)
            return None, {"status": "error", "files_included": 0, "error": str(exc)[:300]}

    async def _resolve_adaptation(
        self, db: AsyncSession, task_type: str,
    ) -> str | None:
        """Resolve adaptation state for the given task type."""
        try:
            from app.services.adaptation_tracker import AdaptationTracker
            tracker = AdaptationTracker(db)
            return await tracker.render_adaptation_state(task_type)
        except Exception:
            logger.debug("Adaptation state unavailable", exc_info=True)
        return None

    async def _resolve_patterns(
        self,
        raw_prompt: str,
        applied_pattern_ids: list[str] | None,
        db: AsyncSession,
    ) -> str | None:
        """Resolve applied meta-patterns via taxonomy engine or explicit IDs."""
        try:
            if applied_pattern_ids:
                from sqlalchemy import select

                from app.models import MetaPattern
                result = await db.execute(
                    select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
                )
                patterns = result.scalars().all()
                if patterns:
                    return "\n".join(f"- {p.pattern_text}" for p in patterns)

            # Auto-inject from taxonomy engine via match_prompt()
            if self._taxonomy_engine and self._embedding_service:
                try:
                    from app.services.taxonomy.matching import match_prompt
                    match = await match_prompt(
                        raw_prompt, db, self._embedding_service,
                    )
                    if match and (match.meta_patterns or match.cross_cluster_patterns):
                        lines = [
                            f"- {p.pattern_text}"
                            for p in (match.meta_patterns or [])[:3]
                            if p.pattern_text
                        ]
                        # Include cross-cluster universal patterns
                        for cp in (match.cross_cluster_patterns or [])[:3]:
                            if cp.pattern_text:
                                lines.append(f"- {cp.pattern_text} (cross-cluster)")
                        return "\n".join(lines) if lines else None
                except Exception:
                    logger.debug("Taxonomy pattern search failed", exc_info=True)
        except Exception:
            logger.debug("Pattern resolution failed", exc_info=True)
        return None

    async def _resolve_performance_signals(
        self,
        db: AsyncSession,
        task_type: str,
        domain: str,
    ) -> str | None:
        """Delegate to module-level function (thin wrapper for instance method API)."""
        return await resolve_performance_signals(db, task_type, domain)

    # -- Content capping helpers --

    @staticmethod
    def _cap_codebase_context(text: str | None) -> str | None:
        """Cap codebase context and wrap in <untrusted-context>."""
        if text is None:
            return None
        capped = text[: settings.MAX_CODEBASE_CONTEXT_CHARS]
        if len(capped) < len(text):
            logger.info(
                "Truncated codebase_context from %d to %d chars",
                len(text), settings.MAX_CODEBASE_CONTEXT_CHARS,
            )
        return (
            '<untrusted-context source="curated-index">\n'
            f"{capped}\n"
            "</untrusted-context>"
        )

    @staticmethod
    def _cap_adaptation_state(text: str | None) -> str | None:
        """Cap adaptation state to configured maximum."""
        if text is None:
            return None
        capped = text[: settings.MAX_ADAPTATION_CHARS]
        if len(capped) < len(text):
            logger.info(
                "Truncated adaptation_state from %d to %d chars",
                len(text), settings.MAX_ADAPTATION_CHARS,
            )
        return capped
