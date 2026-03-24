"""Unified context enrichment service for all routing tiers.

Single entry point replacing 5 scattered context resolution sites.
Each tier calls enrich() and receives an EnrichedContext with all
resolved layers — workspace guidance, codebase context, adaptation,
applied patterns, and (for passthrough) heuristic analysis.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.heuristic_analyzer import HeuristicAnalysis, HeuristicAnalyzer
from app.services.workspace_intelligence import WorkspaceIntelligence

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context
    from app.services.embedding_service import EmbeddingService
    from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichedContext:
    """All resolved context layers for an optimization request."""

    raw_prompt: str
    workspace_guidance: str | None = None
    codebase_context: str | None = None
    adaptation_state: str | None = None
    applied_patterns: str | None = None
    analysis: HeuristicAnalysis | None = None
    context_sources: MappingProxyType[str, bool] = field(
        default_factory=lambda: MappingProxyType({}),
    )


class ContextEnrichmentService:
    """Unified enrichment orchestrator for all routing tiers."""

    def __init__(
        self,
        prompts_dir: Path,
        data_dir: Path,
        workspace_intel: WorkspaceIntelligence,
        embedding_service: Any,          # EmbeddingService
        heuristic_analyzer: HeuristicAnalyzer,
        github_client: Any,              # GitHubClient
        taxonomy_engine: Any | None = None,
    ) -> None:
        self._prompts_dir = prompts_dir
        self._data_dir = data_dir
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
        Content capping and ``<untrusted-context>`` wrapping remain the caller's
        responsibility (currently handled by ``ContextResolver``).
        """
        prefs = preferences_snapshot or {}

        # 1. Workspace guidance — ALL tiers, same path
        guidance = await self._resolve_workspace_guidance(mcp_ctx, workspace_path)

        # 2. Analysis — tier-dependent
        analysis: HeuristicAnalysis | None = None
        task_type: str | None = None
        if tier == "passthrough":
            try:
                analysis = await self._heuristic_analyzer.analyze(raw_prompt, db)
                task_type = analysis.task_type
            except Exception:
                logger.exception("Heuristic analysis failed")
                analysis = HeuristicAnalysis(
                    task_type="general", domain="general",
                    intent_label="general optimization", confidence=0.0,
                )
                task_type = "general"

        # 3. Codebase context — tier-dependent
        codebase_context: str | None = None
        if tier == "passthrough" and repo_full_name:
            branch = repo_branch or "main"
            codebase_context = await self._query_index_context(
                repo_full_name, branch, raw_prompt,
                task_type, analysis.domain if analysis else None, db,
            )

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

        # 6. Context sources audit (frozen via MappingProxyType)
        sources = MappingProxyType({
            "workspace_guidance": guidance is not None,
            "codebase_context": codebase_context is not None,
            "adaptation": adaptation is not None,
            "applied_patterns": patterns is not None,
            "heuristic_analysis": analysis is not None,
        })

        return EnrichedContext(
            raw_prompt=raw_prompt,
            workspace_guidance=guidance,
            codebase_context=codebase_context,
            adaptation_state=adaptation,
            applied_patterns=patterns,
            analysis=analysis,
            context_sources=sources,
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

        return self._workspace_intel.analyze(roots)

    async def _query_index_context(
        self,
        repo_full_name: str,
        branch: str,
        raw_prompt: str,
        task_type: str | None,
        domain: str | None,
        db: AsyncSession,
    ) -> str | None:
        """Query pre-built index for curated codebase context.

        Note: ``RepoIndexService`` is instantiated per-call because it holds a
        reference to the per-request ``AsyncSession``.  The service itself is
        lightweight (no model loading) so the allocation cost is negligible.
        """
        try:
            from app.services.repo_index_service import RepoIndexService
            svc = RepoIndexService(db, self._github_client, self._embedding_service)
            result = await svc.query_curated_context(
                repo_full_name, branch, raw_prompt,
                task_type=task_type, domain=domain,
            )
            if result:
                return result.context_text
        except Exception:
            logger.debug("Curated index retrieval failed", exc_info=True)
        return None

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
                from app.models import MetaPattern
                from sqlalchemy import select
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
                    if match and match.meta_patterns:
                        return "\n".join(
                            f"- {p.pattern_text}"
                            for p in match.meta_patterns[:3]
                            if p.pattern_text
                        )
                except Exception:
                    logger.debug("Taxonomy pattern search failed", exc_info=True)
        except Exception:
            logger.debug("Pattern resolution failed", exc_info=True)
        return None
