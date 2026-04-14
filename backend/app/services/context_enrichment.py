"""Unified context enrichment service for all routing tiers.

Single entry point replacing scattered context resolution sites.
Each tier calls enrich() and receives an EnrichedContext with all
resolved layers — codebase context (including workspace guidance as
fallback), strategy intelligence, applied patterns, and heuristic analysis.

Heuristic analysis runs for ALL tiers (not just passthrough) to provide
domain detection for curated retrieval cross-domain filtering.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.heuristic_analyzer import HeuristicAnalysis, HeuristicAnalyzer
from app.services.workspace_intelligence import WorkspaceIntelligence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enrichment profiles — match enrichment depth to use case
# ---------------------------------------------------------------------------

PROFILE_CODE_AWARE = "code_aware"
PROFILE_KNOWLEDGE_WORK = "knowledge_work"
PROFILE_COLD_START = "cold_start"

_COLD_START_THRESHOLD = 10  # optimization count below which cold-start profile activates

# Task types where curated codebase retrieval (L3b) provides high value.
# Non-coding prompts (writing, creative, general) waste ~40% of the context
# window on irrelevant source files.
_CODEBASE_TASK_TYPES = frozenset({"coding", "system", "data"})

# Escape-hatch keywords: even for non-coding task types, if the prompt
# mentions code-related concepts, curated retrieval is still valuable.
_CODE_ESCAPE_KEYWORDS = frozenset({
    "code", "function", "class", "api", "endpoint", "database", "schema",
    "sql", "query", "import", "module", "script", "bug", "debug",
    "refactor", "deploy", "migration", "config", "dockerfile",
})


def select_enrichment_profile(
    task_type: str,
    repo_linked: bool,
    optimization_count: int,
) -> str:
    """Select enrichment profile based on observable state.

    Pure function — no I/O, no side effects. Determines which context layers
    to activate for this request.

    Profiles:
        code_aware     — All layers active. Coding/system/data task with repo linked.
        knowledge_work — Skip codebase context. Writing/creative/analysis/general tasks.
        cold_start     — Skip strategy intelligence + patterns. < 10 optimizations.
    """
    if optimization_count < _COLD_START_THRESHOLD:
        return PROFILE_COLD_START
    if task_type in _CODEBASE_TASK_TYPES and repo_linked:
        return PROFILE_CODE_AWARE
    return PROFILE_KNOWLEDGE_WORK


# ---------------------------------------------------------------------------
# B0: Prompt-repo relevance gate
# ---------------------------------------------------------------------------

_GENERIC_TERMS = frozenset({
    # Architecture / structure
    "service", "services", "model", "models", "controller", "handler",
    "module", "modules", "interface", "component", "components",
    "factory", "provider", "middleware", "wrapper", "manager",
    "backend", "frontend", "system", "systems", "application",
    "project", "projects", "framework", "library", "package",
    # CRUD / data
    "create", "read", "update", "delete", "query", "filter",
    "request", "response", "result", "results", "payload",
    "field", "fields", "column", "columns", "table", "tables",
    "record", "records", "entry", "entries", "item", "items",
    "database", "migration", "migrations",
    # Files / config
    "file", "files", "directory", "path", "config", "configuration",
    "setting", "settings", "option", "options", "parameter",
    # Code constructs
    "function", "method", "class", "instance", "object", "variable",
    "value", "values", "return", "import", "export", "async", "await",
    "callback", "promise", "decorator", "annotation",
    # HTTP / API
    "endpoint", "route", "router", "server", "client", "port", "host",
    "header", "headers", "body", "status", "error", "errors",
    "json", "yaml", "html", "text", "string", "number", "boolean",
    # Common actions
    "init", "start", "stop", "setup", "build", "test", "tests",
    "check", "validate", "parse", "format", "convert", "process",
    "load", "save", "send", "fetch", "push", "pull",
    # Generic nouns
    "name", "title", "description", "content", "type", "types",
    "state", "data", "info", "meta", "context", "source",
    "default", "optional", "required", "enabled", "disabled",
    "base", "core", "utils", "helpers", "common", "shared",
    "user", "users", "admin", "role", "session", "token",
    "list", "page", "pagination", "offset", "limit", "total",
    "schema", "schemas", "validator", "validators",
    "level", "mode", "version", "index", "count",
    "event", "events", "action", "actions", "task", "tasks",
    "null", "none", "true", "false", "undefined",
    "logging", "logger", "debug", "warning",
    # Software lifecycle
    "testing", "deploy", "deployment", "release", "staging",
    "template", "templates", "phase", "only", "first", "never",
    "tracking", "active", "calls", "from", "with", "turn",
})


def extract_domain_vocab(synthesis: str) -> frozenset[str]:
    """Extract domain-specific vocabulary from explore synthesis.

    Tokenizes the synthesis text, keeps words with frequency >= 3, and
    filters out generic programming terms (:data:`_GENERIC_TERMS`) and
    tech-stack aliases (:data:`_TECH_VOCABULARY`).  Returns a frozenset
    of domain-specific terms that characterize the linked repo's domain.
    """
    if not synthesis:
        return frozenset()
    from collections import Counter

    words = re.findall(r"\b[a-z][a-z_]{3,}\b", synthesis.lower())
    freq = Counter(words)
    tech_aliases: set[str] = set()
    for techs in _TECH_VOCABULARY.values():
        for aliases in techs.values():
            tech_aliases.update(aliases)
    return frozenset(
        w for w, c in freq.items()
        if c >= 3 and w not in _GENERIC_TERMS and w not in tech_aliases
    )


async def compute_repo_relevance(
    raw_prompt: str,
    explore_synthesis: str,
    embedding_service: Any,
) -> float:
    """Semantic relevance between a prompt and the linked repo's architecture.

    Computes cosine similarity between the prompt embedding and the explore
    synthesis embedding.  Returns a float in [0.0, 1.0] — higher means the
    prompt is more likely *about* the linked project rather than merely sharing
    the same tech stack.

    Used by :func:`ContextEnrichmentService.enrich` to gate codebase context
    injection.  When the score falls below ``REPO_RELEVANCE_GATE`` the pipeline
    skips synthesis + curated retrieval, preventing unrelated projects from
    inheriting the linked repo's internal patterns.
    """
    import numpy as np

    prompt_vec = await embedding_service.aembed_single(raw_prompt)
    synth_vec = await embedding_service.aembed_single(explore_synthesis)
    return float(
        np.dot(prompt_vec, synth_vec)
        / (np.linalg.norm(prompt_vec) * np.linalg.norm(synth_vec) + 1e-9)
    )


# ---------------------------------------------------------------------------
# B1: Prompt-context divergence detection
# ---------------------------------------------------------------------------

_TECH_VOCABULARY: dict[str, dict[str, set[str]]] = {
    "database": {
        "postgresql": {"postgresql", "postgres", "psycopg", "asyncpg", "pg_"},
        "mysql": {"mysql", "mariadb", "pymysql", "mysqlclient"},
        "sqlite": {"sqlite", "aiosqlite", "sqlite3"},
        "mongodb": {"mongodb", "pymongo", "motor", "mongosh"},
        "redis": {"redis"},
    },
    "framework": {
        "fastapi": {"fastapi"},
        "django": {"django"},
        "flask": {"flask"},
        "express": {"express", "expressjs"},
        "nextjs": {"nextjs", "next.js"},
        "rails": {"rails", "ruby on rails"},
        "spring": {"spring", "springframework", "spring boot"},
    },
    "language": {
        "python": {"python", "pyproject", "setuptools", ".py"},
        "javascript": {"javascript", "node_modules"},
        "typescript": {"typescript", "tsconfig"},
        "java": {"java", "maven", "gradle"},
        "go": {"golang", "go.mod", "go.sum"},
        "rust": {"rust", "cargo.toml", "rustc"},
        "ruby": {"ruby", "gemfile", "bundler"},
    },
}

# Pairs within the same category that are NOT conflicts
_COMPAT_PAIRS = frozenset({
    ("typescript", "javascript"),  # TS is a superset of JS
    ("javascript", "typescript"),
})

# Technologies that are always additive (no conflict even if different category tech exists)
_ADDITIVE_TECHS = frozenset({
    "redis", "celery", "rabbitmq", "docker", "kubernetes", "nginx",
    "terraform", "prometheus", "grafana", "elasticsearch",
})

_MIGRATION_KEYWORDS = frozenset({
    "migrate", "migration", "upgrade", "switch to", "replace with",
    "move to", "transition to", "port to", "convert to",
})


@dataclass(frozen=True)
class Divergence:
    """A detected tech stack conflict between prompt and codebase context."""

    prompt_tech: str
    codebase_tech: str
    category: str
    severity: str  # "conflict" | "migration"


def _extract_techs(text: str) -> dict[str, set[str]]:
    """Extract technology mentions from text, grouped by category.

    Uses word-boundary-aware matching to avoid false positives
    (e.g., "flask" in "flasks", "go" in "going").
    Multi-word aliases and aliases containing dots/punctuation use
    substring matching (same pattern as _TASK_TYPE_SIGNALS).

    Returns {category: {tech_name, ...}} for each tech found.
    """
    if not text:
        return {}
    text_lower = text.lower()
    found: dict[str, set[str]] = {}
    for category, techs in _TECH_VOCABULARY.items():
        for tech_name, aliases in techs.items():
            for alias in aliases:
                # Multi-word or dotted aliases: substring match
                if " " in alias or "." in alias:
                    matched = alias in text_lower
                else:
                    # Single-word aliases: word boundary match
                    matched = bool(re.search(r"\b" + re.escape(alias) + r"\b", text_lower))
                if matched:
                    found.setdefault(category, set()).add(tech_name)
                    break  # one alias match is enough per tech
    return found


def detect_divergences(
    raw_prompt: str,
    codebase_context: str | None,
) -> list[Divergence]:
    """Compare tech mentions in prompt vs codebase context.

    Returns a list of Divergence objects for any conflicts detected.
    Only runs when codebase_context is available (repo linked + synthesis/curated).
    """
    if not codebase_context:
        return []

    prompt_techs = _extract_techs(raw_prompt)
    codebase_techs = _extract_techs(codebase_context)

    if not prompt_techs or not codebase_techs:
        return []

    # Check for migration keywords in the prompt (NOT codebase — Alembic migrations are noise).
    # Multi-word patterns like "replace...with" are checked with a word-window scan
    # since the user may write "Replace our X layer with Y" (words between "replace" and "with").
    prompt_lower = raw_prompt.lower()
    prompt_words = prompt_lower.split()
    has_migration = any(kw in prompt_lower for kw in _MIGRATION_KEYWORDS)
    _migration_match: str | None = None
    if has_migration:
        _migration_match = next((kw for kw in _MIGRATION_KEYWORDS if kw in prompt_lower), None)
    else:
        # Window-based check for "replace...with" and "rewrite...in" patterns
        for i, w in enumerate(prompt_words):
            if w == "replace":
                if "with" in prompt_words[i + 1 : i + 8]:
                    has_migration = True
                    _migration_match = "replace...with (window)"
                    break
            elif w == "rewrite":
                if "in" in prompt_words[i + 1 : i + 6]:
                    has_migration = True
                    _migration_match = "rewrite...in (window)"
                    break

    logger.debug(
        "divergence_scan: prompt_techs=%s codebase_techs=%s migration=%s match=%s",
        prompt_techs, codebase_techs, has_migration, _migration_match,
    )

    divergences: list[Divergence] = []
    for category, prompt_set in prompt_techs.items():
        codebase_set = codebase_techs.get(category, set())
        if not codebase_set:
            continue  # no codebase tech in this category — can't conflict

        for p_tech in prompt_set:
            # Skip additive technologies
            if p_tech in _ADDITIVE_TECHS:
                continue
            # Skip if the tech IS in the codebase (no conflict)
            if p_tech in codebase_set:
                continue
            # Skip compatible pairs (TS/JS)
            compatible = any(
                (p_tech, c_tech) in _COMPAT_PAIRS for c_tech in codebase_set
            )
            if not compatible:
                # Genuine divergence — determine severity
                severity = "migration" if has_migration else "conflict"
                c_tech = next(iter(codebase_set))
                divergences.append(Divergence(
                    prompt_tech=p_tech,
                    codebase_tech=c_tech,
                    category=category,
                    severity=severity,
                ))

    return divergences


async def resolve_performance_signals(
    db: AsyncSession,
    task_type: str,
    domain: str,
) -> tuple[str | None, bool]:
    """Resolve performance signals: strategy perf by domain, anti-patterns, domain keywords.

    Standalone function — callable from both the enrichment service (instance method)
    and the sampling pipeline (no instance needed). Cheap signals (~150 tokens) from
    the Optimization table, no LLM calls.

    Returns:
        Tuple of (formatted signals text or None, fallback_used flag).
        When the exact domain+task_type query is empty, falls back to
        task_type-only across all domains (C1 domain-relaxed fallback).
    """
    try:
        from sqlalchemy import func, select

        from app.models import Optimization

        lines: list[str] = []

        # 1. Strategy performance by domain+task_type (top 3)
        _strategy_base = select(
            Optimization.strategy_used,
            func.avg(Optimization.overall_score).label("avg_score"),
            func.count().label("n"),
        ).where(
            Optimization.task_type == task_type,
            Optimization.overall_score.isnot(None),
            Optimization.strategy_used.isnot(None),
        )

        # Exact match first (domain + task_type)
        perf_q = await db.execute(
            _strategy_base.where(Optimization.domain == domain)
            .group_by(Optimization.strategy_used)
            .having(func.count() >= 3)
            .order_by(func.avg(Optimization.overall_score).desc())
            .limit(3)
        )
        top_strategies = perf_q.all()
        strategy_fallback = False

        # C1: Domain-relaxed fallback when exact match returns nothing
        if not top_strategies:
            fallback_q = await db.execute(
                _strategy_base
                .group_by(Optimization.strategy_used)
                .having(func.count() >= 3)
                .order_by(func.avg(Optimization.overall_score).desc())
                .limit(3)
            )
            top_strategies = fallback_q.all()
            if top_strategies:
                strategy_fallback = True
                logger.info(
                    "strategy_intelligence: exact=%s+%s empty, fallback to %s-only (%d strategies)",
                    task_type, domain, task_type, len(top_strategies),
                )

        if top_strategies:
            strat_parts = [
                f"{r.strategy_used} ({r.avg_score:.1f}, n={r.n})"
                for r in top_strategies
            ]
            scope = f"{task_type} (across all domains)" if strategy_fallback else f"{domain}+{task_type}"
            lines.append(f"Top strategies for {scope}: " + ", ".join(strat_parts))

        # 2. Anti-patterns: strategies whose average is below 5.5
        anti_q = await db.execute(
            _strategy_base.where(Optimization.domain == domain)
            .group_by(Optimization.strategy_used)
            .having(func.count() >= 3, func.avg(Optimization.overall_score) < 5.5)
            .order_by(func.avg(Optimization.overall_score).asc())
            .limit(2)
        )
        anti_patterns = anti_q.all()
        anti_fallback = False

        # C1: Anti-pattern fallback (independent of strategy fallback)
        if not anti_patterns:
            anti_fb_q = await db.execute(
                _strategy_base
                .group_by(Optimization.strategy_used)
                .having(func.count() >= 3, func.avg(Optimization.overall_score) < 5.5)
                .order_by(func.avg(Optimization.overall_score).asc())
                .limit(2)
            )
            anti_patterns = anti_fb_q.all()
            if anti_patterns:
                anti_fallback = True

        if anti_patterns:
            scope = f"{task_type} (across all domains)" if anti_fallback else f"{domain}+{task_type}"
            for r in anti_patterns:
                lines.append(
                    f"Avoid: {r.strategy_used} averaged {r.avg_score:.1f} "
                    f"for {scope} (n={r.n})"
                )

        # Unified fallback flag — either strategy or anti-pattern needed the fallback
        fallback_used = strategy_fallback or anti_fallback

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

        return ("\n".join(lines) if lines else None, fallback_used)
    except Exception:
        logger.debug("Performance signals resolution failed", exc_info=True)
        return None, False


async def resolve_strategy_intelligence(
    db: AsyncSession,
    task_type: str,
    domain: str,
) -> tuple[str | None, bool]:
    """Unified strategy intelligence — merges performance signals + user adaptation feedback.

    Combines domain+task-type strategy performance data with user approval ratings
    into a single strategy advisory.

    Standalone function — callable from the enrichment service, sampling pipeline,
    batch pipeline, and refinement fallback paths.

    Returns:
        Tuple of (formatted strategy intelligence string or None, fallback_used flag).
        The fallback flag indicates whether the domain-relaxed fallback (C1) was used
        for performance signals.
    """
    sections: list[str] = []
    fallback_used = False

    # 1. Score-based strategy rankings + anti-patterns + domain keywords
    perf, fallback_used = await resolve_performance_signals(db, task_type, domain)
    if perf:
        sections.append(perf)

    # 2. Feedback-based affinities from AdaptationTracker
    try:
        from app.services.adaptation_tracker import AdaptationTracker

        tracker = AdaptationTracker(db)
        affinities = await tracker.get_affinities(task_type)

        if affinities:
            aff_lines: list[str] = []
            for strategy, data in sorted(
                affinities.items(),
                key=lambda x: x[1]["approval_rate"],
                reverse=True,
            ):
                total = data["thumbs_up"] + data["thumbs_down"]
                rate = data["approval_rate"]
                aff_lines.append(
                    f"  {strategy}: {rate:.0%} approval ({total} feedbacks)"
                )
            sections.append("User feedback:\n" + "\n".join(aff_lines))

        # 3. Blocked strategies (approval < 0.3 with 5+ feedbacks)
        blocked = await tracker.get_blocked_strategies(task_type)
        if blocked:
            sections.append(
                "Blocked strategies (low approval): "
                + ", ".join(sorted(blocked))
            )
    except Exception:
        logger.debug("Adaptation data resolution failed", exc_info=True)

    return ("\n\n".join(sections) if sections else None, fallback_used)


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
    def _should_skip_curated(task_type: str, raw_prompt: str) -> tuple[bool, str | None]:
        """Determine whether curated codebase retrieval should be skipped.

        Returns (True, reason) when the task type is non-coding AND the prompt
        contains no code-related escape keywords.  Returns (False, None) otherwise.
        """
        if task_type in _CODEBASE_TASK_TYPES:
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
    ) -> EnrichedContext:
        """Resolve all context layers for the given tier.

        Enrichment profile (code_aware / knowledge_work / cold_start) is auto-selected
        based on task_type, repo link, and optimization count. The profile gates which
        layers are activated — cold_start skips strategy intelligence and patterns;
        knowledge_work skips codebase context.

        ``preferences_snapshot``, when provided, gates optional layers:
        - ``enable_strategy_intelligence``: if ``False``, skip strategy intelligence.
          Falls back to ``enable_adaptation`` for backward compat.

        Content capping is applied inline: ``codebase_context`` at
        ``MAX_CODEBASE_CONTEXT_CHARS``; ``strategy_intelligence`` at ``MAX_ADAPTATION_CHARS``.
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
        try:
            from sqlalchemy import func
            from sqlalchemy import select as _sel_count

            from app.models import Optimization
            _count_q = await db.execute(
                _sel_count(func.count()).select_from(Optimization)
            )
            opt_count = _count_q.scalar() or 0
        except Exception:
            logger.debug("Optimization count query failed, defaulting to 0")

        profile = select_enrichment_profile(
            task_type or "general",
            repo_full_name is not None,
            opt_count,
        )
        enrichment_meta_dict: dict[str, Any] = {"enrichment_profile": profile}
        if _disambiguation_info:
            enrichment_meta_dict["heuristic_disambiguation"] = _disambiguation_info
        if analysis and analysis.domain_scores:
            enrichment_meta_dict["domain_signals"] = analysis.domain_scores
        if _llm_fallback:
            enrichment_meta_dict["llm_classification_fallback"] = True
        if analysis:
            enrichment_meta_dict["task_type_signal_source"] = analysis.task_type_signal_source
            if analysis.task_type_scores:
                enrichment_meta_dict["task_type_scores"] = analysis.task_type_scores
        skipped_layers: list[str] = []

        # 2. Codebase context — unified layer combining three sources:
        #    (a) Cached Haiku synthesis (architectural overview)
        #    (b) Per-prompt curated retrieval (task-gated)
        #    (c) Workspace guidance (fallback when synthesis is absent)
        #
        #    Workspace guidance is a strict subset of synthesis when a repo is
        #    linked — it detects tech stack from manifests, which synthesis
        #    already covers. It only has unique value when IDE is connected
        #    without a repo (MCP roots or filesystem path).
        codebase_context: str | None = None
        skip_codebase = profile == PROFILE_KNOWLEDGE_WORK
        if skip_codebase:
            skipped_layers.append("codebase_context")
        if repo_full_name and not skip_codebase:
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

            # 2a. Cached explore synthesis (architectural context) — always fetched
            explore_synthesis = await self._get_explore_synthesis(
                repo_full_name, branch, db,
            )
            enrichment_meta_dict["explore_synthesis"] = {
                "present": explore_synthesis is not None,
                "char_count": len(explore_synthesis) if explore_synthesis else 0,
            }

            # 2a-gate. Repo relevance gate — skip codebase context when the
            # prompt is semantically unrelated to the linked repo (same tech
            # stack but different project).  Only fires when synthesis exists
            # (can't compute relevance without it).
            _repo_relevance_skipped = False
            if explore_synthesis:
                from app.services.pipeline_constants import REPO_RELEVANCE_GATE

                try:
                    relevance = await compute_repo_relevance(
                        raw_prompt, explore_synthesis, self._embedding_service,
                    )
                    enrichment_meta_dict["repo_relevance_score"] = round(relevance, 3)

                    if relevance < REPO_RELEVANCE_GATE:
                        logger.info(
                            "repo_relevance_gate: score=%.3f < %.2f, "
                            "skipping codebase context (repo=%s)",
                            relevance, REPO_RELEVANCE_GATE, repo_full_name,
                        )
                        enrichment_meta_dict["repo_relevance_skipped"] = True
                        explore_synthesis = None
                        _repo_relevance_skipped = True
                except Exception:
                    logger.debug(
                        "repo_relevance_gate: embedding failed, proceeding without gate",
                        exc_info=True,
                    )
                    enrichment_meta_dict["repo_relevance_error"] = True

            # 2b. Workspace guidance as fallback when synthesis is absent
            if not explore_synthesis and not _repo_relevance_skipped:
                ws_fallback = await self._resolve_workspace_guidance(mcp_ctx, workspace_path)
                if ws_fallback:
                    explore_synthesis = ws_fallback
                    enrichment_meta_dict["workspace_as_fallback"] = True

            # 2c. Per-prompt curated index retrieval — task-gated
            skip_curated, skip_reason = self._should_skip_curated(
                task_type or "general", raw_prompt,
            )
            # Also skip curated if repo relevance gate fired
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
            enable_si = prefs.get(
                "enable_strategy_intelligence",
                prefs.get("enable_adaptation", True),
            )
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
        if profile == PROFILE_COLD_START:
            skipped_layers.append("applied_patterns")
        elif tier in ("internal", "sampling"):
            skipped_layers.append("applied_patterns")
            enrichment_meta_dict["patterns_deferred_to_pipeline"] = True
        else:
            patterns, _pattern_details = await self._resolve_patterns(
                raw_prompt, applied_pattern_ids, db,
            )
            if _pattern_details:
                enrichment_meta_dict["applied_pattern_texts"] = _pattern_details

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
            auto_injected = []
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
                    )
                    for ip in auto_injected:
                        pattern_details.append({
                            "text": ip.pattern_text,
                            "source": ip.source or "cluster",
                            "cluster_label": ip.cluster_label or "",
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
        capped = text[: settings.MAX_ADAPTATION_CHARS]
        if len(capped) < len(text):
            logger.info(
                "Truncated strategy_intelligence from %d to %d chars",
                len(text), settings.MAX_ADAPTATION_CHARS,
            )
        return capped
