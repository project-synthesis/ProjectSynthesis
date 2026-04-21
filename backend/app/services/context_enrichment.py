"""Unified context enrichment orchestrator for all routing tiers.

Single entry point replacing scattered context resolution sites.
Each tier calls enrich() and receives an EnrichedContext with all
resolved layers — codebase context (including workspace guidance as
fallback), strategy intelligence, applied patterns, and heuristic analysis.

Heuristic analysis runs for ALL tiers (not just passthrough) to provide
domain detection for curated retrieval cross-domain filtering.

Phase 3A split (2026-04-19): B0 relevance gate, B1/B2 divergence detection,
and strategy intelligence were extracted into dedicated modules. This file
now owns profile selection, the ``EnrichedContext`` dataclass, and the
``ContextEnrichmentService`` orchestrator. Public API is preserved via
re-exports below.

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
from app.services.divergence_detector import (
    Divergence,
    detect_divergences,
)
from app.services.heuristic_analyzer import HeuristicAnalysis, HeuristicAnalyzer
from app.services.repo_relevance import (
    compute_repo_relevance,
    extract_domain_vocab,
)
from app.services.strategy_intelligence import (
    resolve_performance_signals,
    resolve_strategy_intelligence,
)
from app.services.workspace_intelligence import WorkspaceIntelligence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enrichment profiles — match enrichment depth to use case
# ---------------------------------------------------------------------------

PROFILE_CODE_AWARE = "code_aware"
PROFILE_KNOWLEDGE_WORK = "knowledge_work"
PROFILE_COLD_START = "cold_start"

_COLD_START_THRESHOLD = 10  # optimization count below which cold-start profile activates
# I-6: if meta_patterns exist from prior seeding/import, the pattern tier is
# warm enough to unlock strategy intelligence + pattern injection even on a
# freshly-reset optimization table. Threshold is deliberately permissive —
# 5 patterns is the minimum for meaningful auto-injection.
_COLD_START_PATTERN_THRESHOLD = 5

# Task types where curated codebase retrieval (L3b) provides high value.
# Non-coding prompts (writing, creative, general) waste ~40% of the context
# window on irrelevant source files.
_CODEBASE_TASK_TYPES = frozenset({"coding", "system", "data"})

# Code-adjacent task types: the task_type itself doesn't guarantee code, but
# when the B0 repo-relevance gate has already said "this prompt is about this
# codebase" (cosine ≥ floor), curated retrieval is high-value. Covers
# "audit X", "review the Y middleware", "trace the Z pipeline" prompts that
# don't mention the narrow _CODE_ESCAPE_KEYWORDS allowlist.
_CODE_ADJACENT_TASK_TYPES = frozenset({"analysis", "system"})

# Escape-hatch keywords: even for non-coding task types, if the prompt
# mentions code-related concepts, curated retrieval is still valuable.
_CODE_ESCAPE_KEYWORDS = frozenset({
    "code", "function", "class", "api", "endpoint", "database", "schema",
    "sql", "query", "import", "module", "script", "bug", "debug",
    "refactor", "deploy", "migration", "config", "dockerfile",
})

# B0 floor value kept in sync with app.services.repo_relevance.REPO_RELEVANCE_FLOOR
# Duplicated here to keep `_should_skip_curated` a pure function with no
# import-cycle risk. Update both when the floor changes.
_CURATED_B0_FLOOR = 0.15


def select_enrichment_profile(
    task_type: str,
    repo_linked: bool,
    optimization_count: int,
    meta_pattern_count: int = 0,
) -> str:
    """Select enrichment profile based on observable state.

    Pure function — no I/O, no side effects. Determines which context layers
    to activate for this request.

    Profiles:
        code_aware     — All layers active. Coding/system/data task with repo linked.
        knowledge_work — Skip codebase context. Writing/creative/analysis/general tasks.
        cold_start     — Skip strategy intelligence + patterns. Truly cold DB.

    Signal-aware cold-start (I-6): ``cold_start`` triggers only when BOTH
    ``optimization_count < _COLD_START_THRESHOLD`` AND
    ``meta_pattern_count < _COLD_START_PATTERN_THRESHOLD``. The moment the
    pattern tier has accreted enough signal (via batch-seed, prior import,
    or >=5 optimizations' worth of extraction), strategy intelligence + auto-
    injection unlock regardless of opt_count — otherwise seed-first workflows
    never benefit from the patterns they just seeded.
    """
    if (
        optimization_count < _COLD_START_THRESHOLD
        and meta_pattern_count < _COLD_START_PATTERN_THRESHOLD
    ):
        return PROFILE_COLD_START
    if task_type in _CODEBASE_TASK_TYPES and repo_linked:
        return PROFILE_CODE_AWARE
    return PROFILE_KNOWLEDGE_WORK


# ---------------------------------------------------------------------------
# A1: Domain-signal block for enrichment_meta
# ---------------------------------------------------------------------------

# Runner-up signal margin — how close (max-score minus runner-score) a second
# candidate must be to appear as informational context alongside the winner.
# Wider margins mean "runner_up: null" more often, which is what we want on
# clear wins; narrower means we surface ambiguity the user might want to see.
_DOMAIN_SIGNAL_RUNNER_UP_MARGIN = 0.15


def _build_domain_signals_block(
    resolved_domain: str, scores: dict[str, float],
) -> dict[str, Any]:
    """Shape the ``enrichment_meta.domain_signals`` block from analyzer output.

    Returns a dict naming the winning domain (``resolved``), its score
    (``score``, rounded to 3dp), and an optional ``runner_up`` sub-dict that
    appears only when a second candidate is within
    ``_DOMAIN_SIGNAL_RUNNER_UP_MARGIN`` of the winner. Informational, never
    contradictory.

    The winner is looked up in ``scores`` by qualifier-stripped primary
    (``"backend: auth"`` → ``"backend"``), so sub-qualifiers in
    ``analysis.domain`` stay consistent with the candidate-score table.
    """
    winner = (resolved_domain or "general").split(":")[0]
    top_score = float(scores.get(winner, 0.0))
    runner_up_entry: dict[str, Any] | None = None
    runner_candidates = [(k, float(v)) for k, v in scores.items() if k != winner]
    if runner_candidates:
        best_runner = max(runner_candidates, key=lambda kv: kv[1])
        if best_runner[1] >= top_score - _DOMAIN_SIGNAL_RUNNER_UP_MARGIN:
            runner_up_entry = {
                "label": best_runner[0],
                "score": round(best_runner[1], 3),
            }
    return {
        "resolved": winner,
        "score": round(top_score, 3),
        "runner_up": runner_up_entry,
    }


def reconcile_domain_signals(
    enrichment_meta: dict[str, Any],
    effective_domain: str,
) -> dict[str, Any]:
    """Return a new enrichment_meta dict with domain_signals re-anchored to
    the pipeline's final resolved domain.

    **Why this exists.** The heuristic ``DomainSignalLoader.classify()`` caps
    winners below its 1.0 promotion threshold as ``"general"`` (safety margin
    against noisy keyword matches). The LLM + ``DomainResolver`` later
    upgrades the prompt to the specific domain (e.g. ``"backend"``). Without
    reconciliation the persisted ``enrichment_meta.domain_signals.resolved``
    stays frozen at the heuristic's pre-threshold call, contradicting
    ``optimization.domain`` in the same row — which is exactly the UI
    contradiction A1 was meant to eliminate.

    Contract:
    - New ``domain_signals.resolved`` = qualifier-stripped ``effective_domain``.
    - ``score`` is re-looked-up from the preserved ``heuristic_domain_scores``
      (0.0 when the heuristic didn't score the new winner — honest: the
      heuristic genuinely saw no evidence).
    - ``runner_up`` is recomputed against the new winner from the same scores.
    - Unknown / missing keys degrade gracefully; the helper never raises.
    - ``heuristic_domain_scores`` is preserved in the output as evidence.
    """
    out = dict(enrichment_meta)  # shallow copy — caller's dict is not mutated
    scores = out.get("heuristic_domain_scores") or {}
    # Strip any qualifier suffix ("backend: auth" → "backend") to stay
    # consistent with the winner lookup convention in _build_domain_signals_block.
    winner = (effective_domain or "general").split(":")[0]

    # No scores in meta → fall back to the existing block (if any) with the
    # resolved winner, score 0.0, no runner (nothing to rank against).
    if not scores:
        if "domain_signals" in out or effective_domain:
            out["domain_signals"] = {
                "resolved": winner,
                "score": 0.0,
                "runner_up": None,
            }
        return out

    # Scores present → rebuild the block using the same shape contract.
    out["domain_signals"] = _build_domain_signals_block(winner, scores)
    return out


@dataclass(frozen=True)
class EnrichedContext:
    """All resolved context layers for an optimization request."""

    raw_prompt: str
    codebase_context: str | None = None
    strategy_intelligence: str | None = None
    applied_patterns: str | None = None
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
    def divergence_alerts(self) -> str | None:
        """Render divergences as alert text with intent classification instructions.

        Returns the full instruction block that tells the optimizer LLM to
        classify intent as OVERSIGHT, DELIBERATE CHANGE, UPGRADE, or STANDALONE.
        Returns None when no divergences were detected.
        """
        divergences = self.enrichment_meta.get("divergences")
        if not divergences:
            return None
        conflict_lines = []
        for d in divergences:
            conflict_lines.append(
                f'The prompt mentions "{d["prompt_tech"]}" but the linked codebase '
                f'uses "{d["codebase_tech"]}" ({d["category"]}).'
            )
        conflicts = "\n".join(conflict_lines)
        return (
            f"TECHNOLOGY DIVERGENCE DETECTED\n\n{conflicts}\n\n"
            "Before optimizing, determine the user's intent:\n\n"
            "1. OVERSIGHT — The user casually names the technology without "
            "explicitly asking to switch stacks. The prompt uses generic "
            "patterns that exist in both technologies.\n"
            "   → Optimize for the codebase's actual stack. Note the "
            "correction in changes summary.\n\n"
            "2. DELIBERATE CHANGE — The user explicitly asks to replace, "
            "rewrite, migrate, or switch technologies. Even if the change "
            "seems architecturally questionable, respect the stated intent.\n"
            "   → Optimize for the requested technology change. Include a "
            "brief advisory noting what the migration entails given the "
            "current codebase, but do NOT override the user's request.\n\n"
            "3. UPGRADE — The user wants the mentioned technology because "
            "the prompt references features EXCLUSIVE to it that the current "
            "stack cannot provide.\n"
            "   → Optimize for a migration path with codebase-aware "
            "considerations.\n\n"
            "4. STANDALONE — The prompt is unrelated to the linked codebase.\n"
            "   → Optimize as-is. Ignore codebase context.\n\n"
            "CRITICAL: Never silently override an explicit user instruction "
            "like 'replace X with Y' or 'rewrite in Z'. If the user asks "
            "for a technology change, that is DELIBERATE CHANGE — optimize "
            "for what they asked, with advisory context.\n\n"
            "DEFAULT: If the prompt just names the technology without "
            "explicitly requesting a change, treat as OVERSIGHT.\n\n"
            "State your determination and why in your changes summary "
            "under '## Stack Divergence'."
        )

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

    @staticmethod
    def _should_skip_curated(
        task_type: str,
        raw_prompt: str,
        repo_relevance_score: float | None = None,
    ) -> tuple[bool, str | None]:
        """Determine whether curated codebase retrieval should be skipped.

        Decision chain:

        1. **Strong-coding task types** (``coding``/``system``/``data``) always keep
           curated on.
        2. **B0 cosine escape** — for code-adjacent task types (``analysis``/
           ``system``) with a ``repo_relevance_score`` at or above
           :data:`_CURATED_B0_FLOOR`, the semantic gate has already ruled
           the prompt is about this codebase; keep curated on even without
           an allowlist keyword hit.
        3. **Keyword allowlist escape** — fall back to :data:`_CODE_ESCAPE_KEYWORDS`
           for any other task type.
        4. Otherwise skip with a descriptive reason string.

        Returns ``(skip, reason)``. ``reason`` is ``None`` when the gate lets
        curated through.
        """
        if task_type in _CODEBASE_TASK_TYPES:
            return False, None

        # B0 cosine escape — semantic gate beats the 19-word allowlist for
        # analysis/system prompts about this codebase ("audit the middleware",
        # "review the routing pipeline", etc).
        if (
            task_type in _CODE_ADJACENT_TASK_TYPES
            and repo_relevance_score is not None
            and repo_relevance_score >= _CURATED_B0_FLOOR
        ):
            return False, None

        # Escape hatch: check for code-related keywords in the prompt
        prompt_lower = raw_prompt.lower()
        for kw in _CODE_ESCAPE_KEYWORDS:
            if kw in prompt_lower:
                return False, None
        return True, f"task_type={task_type}, no code keywords detected"

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
        project_id: str | None = None,
    ) -> EnrichedContext:
        """Resolve all context layers for the given tier.

        Enrichment profile (code_aware / knowledge_work / cold_start) is auto-selected
        based on task_type, repo link, and optimization count. The profile gates which
        layers are activated — cold_start skips strategy intelligence and patterns;
        knowledge_work skips codebase context.

        ``preferences_snapshot``, when provided, gates optional layers:
        - ``enable_strategy_intelligence``: if ``False``, skip strategy intelligence.

        Content capping is applied inline: ``codebase_context`` at
        ``MAX_CODEBASE_CONTEXT_CHARS``; ``strategy_intelligence`` at
        ``MAX_STRATEGY_INTELLIGENCE_CHARS``.
        """
        import time as _time
        _t_enrich_start = _time.monotonic()
        prefs = preferences_snapshot or {}

        # 1. Analysis — heuristic (zero-LLM) for all tiers.
        #    Passthrough uses this as the primary analysis. Internal/sampling
        #    tiers use it only for domain detection in curated retrieval
        #    (the LLM analyze phase runs later with richer classification).
        analysis: HeuristicAnalysis | None = None
        task_type: str | None = None
        _enable_llm_fallback = prefs.get("enable_llm_classification_fallback", True)
        try:
            analysis = await self._heuristic_analyzer.analyze(
                raw_prompt, db, enable_llm_fallback=_enable_llm_fallback,
            )
            task_type = analysis.task_type
        except Exception:
            logger.debug("Heuristic analysis failed during enrichment", exc_info=True)
            analysis = HeuristicAnalysis(
                task_type="general", domain="general",
                intent_label="general optimization", confidence=0.0,
            )
            task_type = "general"

        # 1a. Track disambiguation, LLM fallback, and domain signals for observability
        if analysis and analysis.disambiguation_applied:
            _disambiguation_info = {
                "original_task_type": analysis.disambiguation_from,
                "corrected_to": analysis.task_type,
            }
        else:
            _disambiguation_info = None
        _llm_fallback = analysis.llm_fallback_applied if analysis else False

        # 1b. Enrichment profile selection — determines which layers to activate.
        #     Pure function of observable state: task_type, repo link, history depth.
        opt_count = 0
        meta_pattern_count = 0
        try:
            from sqlalchemy import func
            from sqlalchemy import select as _sel_count

            from app.models import MetaPattern, Optimization
            _count_q = await db.execute(
                _sel_count(func.count()).select_from(Optimization)
            )
            opt_count = _count_q.scalar() or 0
            # I-6: meta-pattern count unlocks cold-start when seed-first
            # workflows produce patterns before any optimizations land.
            _pat_q = await db.execute(
                _sel_count(func.count()).select_from(MetaPattern)
            )
            meta_pattern_count = _pat_q.scalar() or 0
        except Exception:
            logger.debug("Optimization/MetaPattern count query failed, defaulting to 0")

        profile = select_enrichment_profile(
            task_type or "general",
            repo_full_name is not None,
            opt_count,
            meta_pattern_count=meta_pattern_count,
        )
        enrichment_meta_dict: dict[str, Any] = {"enrichment_profile": profile}
        if _disambiguation_info:
            enrichment_meta_dict["heuristic_disambiguation"] = _disambiguation_info
        if analysis and analysis.domain_scores:
            # A1: expose the resolved winner instead of shipping the raw
            # candidate-score table. The old `{label: score}` shape let the UI
            # render a runner-up candidate ("fullstack": 0.3) even when the
            # primary domain was something else ("backend" at 0.88) —
            # contradicting the classification.
            enrichment_meta_dict["domain_signals"] = _build_domain_signals_block(
                analysis.domain, analysis.domain_scores,
            )
            # A1 follow-up: preserve the raw heuristic score table as evidence
            # so the pipeline can reconcile domain_signals against the final
            # LLM/resolver-assigned domain (see ``reconcile_domain_signals``).
            # The block above freezes the *heuristic's* pre-threshold call,
            # which below-1.0 scores demote to "general" even when a specific
            # domain clearly dominated — this table lets us rebuild the winner
            # from the actual score distribution after resolution.
            enrichment_meta_dict["heuristic_domain_scores"] = dict(
                analysis.domain_scores,
            )
        if _llm_fallback:
            enrichment_meta_dict["llm_classification_fallback"] = True
        if analysis:
            enrichment_meta_dict["task_type_signal_source"] = analysis.task_type_signal_source
            if analysis.task_type_scores:
                enrichment_meta_dict["task_type_scores"] = analysis.task_type_scores
        skipped_layers: list[str] = []

        # 2. Codebase context — resolved in _resolve_codebase_context_layer.
        skip_codebase = profile == PROFILE_KNOWLEDGE_WORK
        if skip_codebase:
            skipped_layers.append("codebase_context")
        (
            codebase_context,
            repo_branch,
        ) = await self._resolve_codebase_context_layer(
            raw_prompt=raw_prompt,
            repo_full_name=repo_full_name,
            repo_branch=repo_branch,
            skip_codebase=skip_codebase,
            mcp_ctx=mcp_ctx,
            workspace_path=workspace_path,
            task_type=task_type,
            analysis=analysis,
            db=db,
            enrichment_meta_dict=enrichment_meta_dict,
        )

        # 3. Divergence detection — compare prompt tech vs codebase stack.
        #    When profile=knowledge_work skips codebase context but a repo IS linked,
        #    still fetch the synthesis (cheap cached lookup) for divergence detection.
        #    The synthesis tells us the tech stack even when we don't inject it into the LLM.
        _divergence_source = codebase_context
        if not _divergence_source and repo_full_name and skip_codebase:
            # Lightweight synthesis-only fetch for divergence detection
            try:
                if not repo_branch:
                    repo_branch = "main"
                _synth_for_div = await self._get_explore_synthesis(
                    repo_full_name, repo_branch, db,
                )
                if _synth_for_div:
                    _divergence_source = _synth_for_div
                    logger.debug(
                        "divergence_detection: using synthesis for skipped-codebase profile (repo=%s)",
                        repo_full_name,
                    )
            except Exception:
                logger.debug("Synthesis fetch for divergence detection failed", exc_info=True)

        if _divergence_source:
            _div_source_type = "codebase" if codebase_context else "synthesis_fallback"
            divergences = detect_divergences(raw_prompt, _divergence_source)
            if divergences:
                enrichment_meta_dict["divergences"] = [
                    {"prompt_tech": d.prompt_tech, "codebase_tech": d.codebase_tech,
                     "category": d.category, "severity": d.severity}
                    for d in divergences
                ]
                # Store source type so UI can show whether full codebase or synthesis was used
                enrichment_meta_dict["divergence_source"] = _div_source_type
                _div_summary = "; ".join(
                    f"{d.prompt_tech}≠{d.codebase_tech}({d.category},{d.severity})"
                    for d in divergences
                )
                logger.info(
                    "divergence_detected: count=%d source=%s details=[%s]",
                    len(divergences), _div_source_type, _div_summary,
                )

        # 4. Strategy intelligence — unified layer merging performance signals
        #    and user adaptation feedback into a single strategy advisory.
        #    Gated by preference + profile (cold-start skips — no history yet).
        strategy_intel: str | None = None
        effective_task_type = task_type or "general"
        skip_si = profile == PROFILE_COLD_START
        if skip_si:
            skipped_layers.append("strategy_intelligence")
        else:
            enable_si = prefs.get("enable_strategy_intelligence", True)
            if enable_si:
                strategy_intel, si_fallback = await resolve_strategy_intelligence(
                    db, effective_task_type,
                    analysis.domain if analysis else "general",
                )
                if strategy_intel:
                    enrichment_meta_dict["strategy_intelligence_detail"] = (
                        strategy_intel[:500] if len(strategy_intel) > 500
                        else strategy_intel
                    )
                if si_fallback:
                    enrichment_meta_dict["strategy_intelligence_fallback"] = True

        # E1: Track strategy intelligence hit rate
        try:
            from app.services.classification_agreement import get_classification_agreement
            get_classification_agreement().record_strategy_intel(
                had_intel=strategy_intel is not None,
            )
        except Exception:
            pass

        # 5. Applied patterns — profile-gated (cold-start skips — no clusters yet).
        #    Internal/sampling tiers skip enrichment-level patterns because their
        #    pipelines call auto_inject_patterns() directly with provenance recording.
        patterns: str | None = None
        # UI1: always emit injection_stats so the Inspector's CONTEXT INJECTION
        # section renders a consistent shape. Deferred tiers get zero-count
        # stats that pipeline.py/sampling_pipeline.py overwrite with real values
        # after auto-injection completes.
        _injected_count = 0
        _injected_cluster_count = 0
        if profile == PROFILE_COLD_START:
            skipped_layers.append("applied_patterns")
        elif tier in ("internal", "sampling"):
            skipped_layers.append("applied_patterns")
            enrichment_meta_dict["patterns_deferred_to_pipeline"] = True
        else:
            patterns, _pattern_details = await self._resolve_patterns(
                raw_prompt, applied_pattern_ids, db,
                project_id=project_id,
            )
            if _pattern_details:
                enrichment_meta_dict["applied_pattern_texts"] = _pattern_details
                # Injected entries = everything non-"explicit". Dedup by
                # ``cluster_id`` (always unique) — labels can legitimately be
                # empty for new/untitled clusters, which would silently collapse
                # distinct clusters into one.
                _injected = [d for d in _pattern_details if d.get("source") != "explicit"]
                _injected_count = len(_injected)
                _injected_cluster_count = len({
                    d.get("cluster_id") for d in _injected
                    if d.get("cluster_id")
                })
        enrichment_meta_dict["injection_stats"] = {
            "patterns_injected": _injected_count,
            "injection_clusters": _injected_cluster_count,
            "has_explicit_patterns": bool(applied_pattern_ids),
        }

        # 6. Content capping and injection hardening
        codebase_context = self._cap_codebase_context(codebase_context)
        strategy_intel = self._cap_strategy_intelligence(strategy_intel)

        # 6b. Enrichment metadata — track truncation and profile
        combined_chars = len(codebase_context) if codebase_context else 0
        enrichment_meta_dict["combined_context_chars"] = combined_chars
        enrichment_meta_dict["was_truncated"] = (
            combined_chars >= settings.MAX_CODEBASE_CONTEXT_CHARS
        )
        if skipped_layers:
            enrichment_meta_dict["profile_skipped_layers"] = skipped_layers

        # 7. Context sources audit (frozen via MappingProxyType)
        sources = MappingProxyType({
            "codebase_context": codebase_context is not None,
            "strategy_intelligence": strategy_intel is not None,
            "applied_patterns": patterns is not None,
            "heuristic_analysis": analysis is not None,
        })

        # 8. Log enrichment summary
        _enrich_ms = (_time.monotonic() - _t_enrich_start) * 1000

        # Compute assembled context size for observability
        _total_context = sum(
            len(s) for s in [codebase_context, strategy_intel, patterns]
            if s
        )

        if repo_full_name:
            _cr = enrichment_meta_dict.get("curated_retrieval", {})
            logger.info(
                "enrichment: tier=%s profile=%s repo=%s explore=%s curated=%s "
                "curated_files=%d curated_top=%.3f context_chars=%d "
                "strategy_intel=%s total_assembled=%dK elapsed=%.0fms",
                tier, profile, repo_full_name,
                "yes" if enrichment_meta_dict.get("explore_synthesis", {}).get("present") else "no",
                _cr.get("status", "n/a"),
                _cr.get("files_included", 0),
                _cr.get("top_relevance_score", 0.0),
                enrichment_meta_dict.get("combined_context_chars", 0),
                "yes" if strategy_intel else "none",
                _total_context // 1000,
                _enrich_ms,
            )
        else:
            logger.info(
                "enrichment: tier=%s profile=%s no_repo strategy_intel=%s "
                "total_assembled=%dK elapsed=%.0fms",
                tier, profile, "yes" if strategy_intel else "none",
                _total_context // 1000, _enrich_ms,
            )

        return EnrichedContext(
            raw_prompt=raw_prompt,
            codebase_context=codebase_context,
            strategy_intelligence=strategy_intel,
            applied_patterns=patterns,
            analysis=analysis,
            context_sources=sources,
            enrichment_meta=MappingProxyType(enrichment_meta_dict) if enrichment_meta_dict else MappingProxyType({}),
        )

    async def _resolve_codebase_context_layer(
        self,
        raw_prompt: str,
        repo_full_name: str | None,
        repo_branch: str | None,
        skip_codebase: bool,
        mcp_ctx: Any | None,
        workspace_path: str | None,
        task_type: str | None,
        analysis: HeuristicAnalysis | None,
        db: AsyncSession,
        enrichment_meta_dict: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Resolve the codebase-context layer (synthesis + curated + workspace fallback).

        Unified layer combining three sources:
            (a) Cached Haiku synthesis (architectural overview)
            (b) Per-prompt curated retrieval (task-gated)
            (c) Workspace guidance (fallback when synthesis is absent)

        Workspace guidance is a strict subset of synthesis when a repo is
        linked — it detects tech stack from manifests, which synthesis
        already covers. It only has unique value when IDE is connected
        without a repo (MCP roots or filesystem path).

        Mutates ``enrichment_meta_dict`` with all resolved metadata keys.
        Returns ``(codebase_context, resolved_repo_branch)``.
        """
        codebase_context: str | None = None

        if repo_full_name and not skip_codebase:
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

            # 2a. Cached explore synthesis (architectural context) — always fetched
            explore_synthesis = await self._get_explore_synthesis(
                repo_full_name, branch, db,
            )
            enrichment_meta_dict["explore_synthesis"] = {
                "present": explore_synthesis is not None,
                "char_count": len(explore_synthesis) if explore_synthesis else 0,
            }

            # 2a-gate. Repo relevance gate
            explore_synthesis, _repo_relevance_skipped = await self._apply_repo_relevance_gate(
                raw_prompt=raw_prompt,
                explore_synthesis=explore_synthesis,
                repo_full_name=repo_full_name,
                branch=branch,
                db=db,
                enrichment_meta_dict=enrichment_meta_dict,
            )

            # 2b. Workspace guidance as fallback when synthesis is absent
            if not explore_synthesis and not _repo_relevance_skipped:
                ws_fallback = await self._resolve_workspace_guidance(mcp_ctx, workspace_path)
                if ws_fallback:
                    explore_synthesis = ws_fallback
                    enrichment_meta_dict["workspace_as_fallback"] = True

            # 2c. Per-prompt curated index retrieval — task-gated
            # Pass the B0 repo relevance cosine when available so the gate
            # can upgrade analysis/system prompts to "code-adjacent" status.
            _relevance_for_gate: float | None = None
            if not _repo_relevance_skipped:
                _raw_rel = enrichment_meta_dict.get("repo_relevance_score")
                if isinstance(_raw_rel, (int, float)):
                    _relevance_for_gate = float(_raw_rel)
            skip_curated, skip_reason = self._should_skip_curated(
                task_type or "general", raw_prompt,
                repo_relevance_score=_relevance_for_gate,
            )
            if _repo_relevance_skipped:
                skip_curated = True
                skip_reason = "repo_relevance_gate"
            if skip_curated:
                curated_text = None
                _skip_status = (
                    "skipped_repo_relevance" if _repo_relevance_skipped
                    else "skipped_task_type"
                )
                enrichment_meta_dict["curated_retrieval"] = {
                    "status": _skip_status,
                    "files_included": 0,
                    "reason": skip_reason,
                }
                logger.info(
                    "Curated retrieval skipped: %s (repo=%s)",
                    skip_reason, repo_full_name,
                )
            else:
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

        elif not skip_codebase:
            # No repo linked — workspace guidance is the only codebase context source
            ws_guidance = await self._resolve_workspace_guidance(mcp_ctx, workspace_path)
            if ws_guidance:
                codebase_context = ws_guidance
                enrichment_meta_dict["workspace_as_fallback"] = True

        return codebase_context, repo_branch

    async def _apply_repo_relevance_gate(
        self,
        raw_prompt: str,
        explore_synthesis: str | None,
        repo_full_name: str,
        branch: str,
        db: AsyncSession,
        enrichment_meta_dict: dict[str, Any],
    ) -> tuple[str | None, bool]:
        """Apply the B0 repo-relevance gate to the explore synthesis.

        When synthesis exists and the prompt is unrelated to the linked repo,
        the gate clears ``explore_synthesis`` (returns ``None``) so downstream
        stages treat this as a no-context request. The gate never fires without
        a synthesis to anchor against.

        Returns ``(explore_synthesis_after_gate, skipped_flag)``.
        Mutates ``enrichment_meta_dict`` with relevance diagnostics.
        """
        if not explore_synthesis:
            return explore_synthesis, False

        try:
            # Fetch indexed file paths to enrich the relevance anchor with
            # component-level signal.  Hard-cap at 500 rows so very large
            # repos don't blow past memory or tokenizer limits — vocab
            # extraction dedupes downstream, and ``compute_repo_relevance()``
            # stride-samples the anchor subset to stay inside MiniLM's 512-
            # token window.
            repo_file_paths: list[str] = []
            try:
                from sqlalchemy import select as _sel_paths

                from app.models import RepoFileIndex
                _paths_q = await db.execute(
                    _sel_paths(RepoFileIndex.file_path)
                    .where(
                        RepoFileIndex.repo_full_name == repo_full_name,
                        RepoFileIndex.branch == branch,
                        RepoFileIndex.embedding.isnot(None),
                    )
                    .order_by(RepoFileIndex.file_path)
                    .limit(500)
                )
                repo_file_paths = [r[0] for r in _paths_q.all()]
            except Exception:
                logger.debug(
                    "repo_relevance_gate: file path fetch failed, "
                    "proceeding without path enrichment",
                    exc_info=True,
                )

            relevance, relevance_info = await compute_repo_relevance(
                raw_prompt, explore_synthesis, self._embedding_service,
                repo_full_name=repo_full_name,
                file_paths=repo_file_paths or None,
            )
            enrichment_meta_dict["repo_relevance_anchor_paths"] = len(repo_file_paths)
            enrichment_meta_dict["repo_relevance_score"] = round(relevance, 3)
            enrichment_meta_dict["repo_relevance_info"] = relevance_info

            if relevance_info["decision"] == "skip":
                logger.info(
                    "repo_relevance_gate: cosine=%.3f reason=%s, "
                    "skipping codebase context (repo=%s)",
                    relevance, relevance_info["reason"], repo_full_name,
                )
                enrichment_meta_dict["repo_relevance_skipped"] = True
                return None, True
        except Exception:
            logger.debug(
                "repo_relevance_gate: failed, proceeding without gate",
                exc_info=True,
            )
            enrichment_meta_dict["repo_relevance_error"] = True

        return explore_synthesis, False

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
                    # Source-type balance diagnostics
                    "budget_skip_count": result.budget_skip_count,
                    "code_files": result.code_files_included,
                    "doc_files": result.doc_files_included,
                    "doc_deferred": result.doc_deferred_count,
                }
                return result.context_text, meta
            return None, {"status": "empty", "files_included": 0}
        except Exception as exc:
            logger.warning("Curated index retrieval failed for %s@%s: %s", repo_full_name, branch, exc)
            return None, {"status": "error", "files_included": 0, "error": str(exc)[:300]}

    async def _resolve_patterns(
        self,
        raw_prompt: str,
        applied_pattern_ids: list[str] | None,
        db: AsyncSession,
        project_id: str | None = None,
    ) -> tuple[str | None, list[dict] | None]:
        """Resolve applied meta-patterns via full auto-injection + explicit IDs.

        Uses the same ``auto_inject_patterns()`` pipeline as internal/sampling
        tiers — composite fusion, cross-cluster injection, GlobalPattern boost —
        so passthrough and refine tiers get identical pattern quality.
        No provenance recording (``optimization_id`` not available at enrichment).

        Returns:
            (formatted_text, pattern_details) — pattern_details is a list of
            dicts with ``text``, ``source``, ``similarity`` for UI attribution.
        """
        try:
            pattern_details: list[dict] = []

            # 1. Resolve explicit pattern IDs (user-selected patterns)
            explicit_text: str | None = None
            if applied_pattern_ids:
                from sqlalchemy import select

                from app.models import MetaPattern
                result = await db.execute(
                    select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
                )
                patterns = result.scalars().all()
                if patterns:
                    explicit_text = (
                        "The following proven patterns from past optimizations "
                        "should be applied where relevant:\n"
                        + "\n".join(f"- {p.pattern_text}" for p in patterns)
                    )
                    for p in patterns:
                        pattern_details.append({
                            "text": p.pattern_text,
                            "source": "explicit",
                            "source_count": p.source_count,
                        })

            # 2. Auto-inject via full taxonomy pipeline (composite fusion,
            #    cross-cluster, GlobalPattern 1.3x boost — no provenance recording)
            auto_injected: list = []
            if self._taxonomy_engine:
                try:
                    import uuid as _uuid

                    from app.services.pattern_injection import (
                        auto_inject_patterns,
                    )
                    auto_injected, _ = await auto_inject_patterns(
                        raw_prompt=raw_prompt,
                        taxonomy_engine=self._taxonomy_engine,
                        db=db,
                        trace_id=str(_uuid.uuid4()),
                        project_id=project_id,
                    )
                    for ip in auto_injected:
                        pattern_details.append({
                            "text": ip.pattern_text,
                            "source": ip.source or "cluster",
                            "cluster_label": ip.cluster_label or "",
                            "cluster_id": ip.cluster_id or "",
                            "similarity": round(ip.similarity, 3) if ip.similarity else None,
                        })
                except Exception:
                    logger.debug("Auto-inject patterns failed in enrichment", exc_info=True)

            # 3. Merge explicit + auto-injected via shared formatter
            from app.services.pattern_injection import format_injected_patterns
            formatted = format_injected_patterns(auto_injected, explicit_text)
            return formatted, pattern_details if pattern_details else None
        except Exception:
            logger.debug("Pattern resolution failed", exc_info=True)
        return None, None

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
    def _cap_strategy_intelligence(text: str | None) -> str | None:
        """Cap strategy intelligence to configured maximum."""
        if text is None:
            return None
        capped = text[: settings.MAX_STRATEGY_INTELLIGENCE_CHARS]
        if len(capped) < len(text):
            logger.info(
                "Truncated strategy_intelligence from %d to %d chars",
                len(text), settings.MAX_STRATEGY_INTELLIGENCE_CHARS,
            )
        return capped


__all__ = [
    # Profile selection
    "PROFILE_CODE_AWARE",
    "PROFILE_KNOWLEDGE_WORK",
    "PROFILE_COLD_START",
    "select_enrichment_profile",
    # Dataclass
    "EnrichedContext",
    # Orchestrator
    "ContextEnrichmentService",
    # Re-exports — B0 repo relevance
    "compute_repo_relevance",
    "extract_domain_vocab",
    # Re-exports — B1/B2 divergence detection
    "Divergence",
    "detect_divergences",
    # Re-exports — strategy intelligence
    "resolve_performance_signals",
    "resolve_strategy_intelligence",
]
