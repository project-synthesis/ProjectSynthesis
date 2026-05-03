"""Topic Probe 5-phase orchestrator (Tier 1, v0.5.0).

5 phases: grounding -> generating -> running -> observability -> reporting.

The current_probe_id ContextVar is declared HERE (not in
probe_event_correlation.py) per the C4<->C7 dependency resolution.
C7 imports it from this module to feed event_logger.log_decision.

See docs/specs/topic-probe-2026-04-29.md sec 4.5 for full design.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import statistics
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Optimization, OptimizationPattern, ProbeRun, PromptCluster
from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    SCORING_FORMULA_VERSION,
    DimensionScores,
)
from app.schemas.probes import (
    ProbeAggregate,
    ProbeCompletedEvent,
    ProbeContext,
    ProbeError,
    ProbeFailedEvent,
    ProbeGeneratingEvent,
    ProbeGroundingEvent,
    ProbeProgressEvent,
    ProbePromptResult,
    ProbeRateLimitedEvent,
    ProbeRunRequest,
    ProbeRunResult,
    ProbeStartedEvent,
    ProbeTaxonomyDelta,
)
from app.services.batch_orchestrator import BATCH_CONCURRENCY_BY_TIER
from app.services.event_bus import event_bus
from app.services.probe_generation import generate_probe_prompts
from app.services.taxonomy.event_logger import get_event_logger

logger = logging.getLogger(__name__)

# C4<->C7 dependency resolution -- declare ContextVar where it is SET (here).
# C7's probe_event_correlation.py re-exports + adds inject_probe_id helper.
current_probe_id: ContextVar[str | None] = ContextVar(
    "current_probe_id", default=None,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _apply_scope_filter(files: list[str], scope: str) -> list[str]:
    """Post-retrieval glob filter.

    ``RepoIndexQuery.query_curated_context`` has no scope parameter, so the
    probe applies the filter here at the boundary.
    """
    if scope == "**/*" or not scope:
        return files
    return [f for f in files if fnmatch.fnmatch(f, scope)]


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _resolve_followups(
    delta: ProbeTaxonomyDelta,
    agg: ProbeAggregate,
) -> list[str]:
    """Deterministic recommended-followups rule set per AC-C4-5.

    4 rules: single-cluster + no sub-domain emergence; F5 false-premise fires;
    P0a rejected candidate domains; mean below 1-sigma floor.
    """
    out: list[str] = []
    # Rule 1: single-cluster + no sub-domain emergence
    if not delta.sub_domains_created:
        single_member = [
            c for c in delta.clusters_created
            if str(c.get("member_count", "0")) == "1"
        ]
        if single_member:
            label = single_member[0].get("label", "(unnamed)")
            out.append(
                f"Cluster '{label}' has only 1 member; ~3 more topic-related "
                f"prompts could promote a sub-domain (per v0.4.11 P0a "
                f">=2-cluster floor)."
            )
    # Rule 2: false-premise flag fired
    if agg.f5_flag_fires > 0:
        out.append(
            f"{agg.f5_flag_fires} prompts triggered the "
            f"`possible_false_premise` flag -- review topic framing for "
            f"accuracy."
        )
    # Rule 3: P0a rejected candidate domains
    if delta.proposal_rejected_min_source_clusters > 0:
        out.append(
            f"{delta.proposal_rejected_min_source_clusters} candidate "
            f"domain(s) were rejected (insufficient cluster evidence); "
            f"re-probe similar topics to accumulate sibling clusters."
        )
    # Rule 4: mean below 1-sigma floor
    if agg.mean_overall is not None and agg.mean_overall < 6.9:
        out.append(
            "Mean below 1-sigma floor -- review codebase grounding quality "
            "(`relevant_files` count) and topic specificity."
        )
    return out


def _render_final_report(
    request: ProbeRunRequest,
    probe_id: str,
    started_at: datetime,
    completed_at: datetime,
    prompt_results: list[ProbePromptResult],
    agg: ProbeAggregate,
    delta: ProbeTaxonomyDelta,
    commit_sha: str | None,
    rate_limit_meta: dict | None = None,
) -> str:
    """Render the 5-section final report markdown per AC-C4-5.

    When ``rate_limit_meta`` is supplied (one or more prompts hit a provider
    rate limit during phase 3), an extra "Rate-limited" section is rendered
    near the top with the provider name + reset time so the user sees what
    happened without scrolling through the score distribution. Format
    matches the structured ``ProbeRateLimitedEvent`` SSE payload so the
    final report and live event are coherent.
    """
    top_3 = sorted(
        (r for r in prompt_results if r.overall_score is not None),
        key=lambda r: (-(r.overall_score or 0.0), r.prompt_idx),
    )[:3]

    lines: list[str] = []
    lines.append(f"# Topic Probe Run Report -- `{probe_id}`")
    lines.append("")
    lines.append(f"**Topic:** {request.topic}")
    lines.append(f"**Scope:** {request.scope or '**/*'}")
    lines.append(f"**Intent hint:** {request.intent_hint or 'explore'}")
    lines.append("")

    if rate_limit_meta is not None:
        _prov = rate_limit_meta.get("provider") or "?"
        _reset = rate_limit_meta.get("reset_at_iso") or "?"
        _wait = rate_limit_meta.get("estimated_wait_seconds")
        lines.append("## Rate-limited")
        lines.append(
            f"This probe was rate-limited by **{_prov}** mid-batch. "
            f"Provider limit resets at **{_reset}** UTC"
            + (f" (~{_wait}s from when the limit hit)" if _wait else "")
            + "."
        )
        lines.append(
            "Re-run the same probe topic after that time to fill in the "
            "remaining prompts. Already-completed prompts (below) are "
            "persisted and visible in `/api/optimizations`."
        )
        lines.append("")

    lines.append("## Top 3 Prompts (by overall score)")
    if not top_3:
        lines.append("_(no completed prompts)_")
    else:
        for i, r in enumerate(top_3, 1):
            lines.append(
                f"{i}. **score {r.overall_score:.2f}** -- {r.prompt_text}"
            )
    lines.append("")

    lines.append("## Score Distribution")
    lines.append(f"- mean: {agg.mean_overall}")
    lines.append(
        f"- p5 / p50 / p95: {agg.p5_overall} / {agg.p50_overall} / "
        f"{agg.p95_overall}"
    )
    lines.append(f"- completed: {agg.completed_count}")
    lines.append(f"- failed: {agg.failed_count}")
    lines.append("")

    lines.append("## Taxonomy Delta")
    lines.append(
        f"- domains_created: {delta.domains_created or '_none_'}"
    )
    lines.append(
        f"- sub_domains_created: {delta.sub_domains_created or '_none_'}"
    )
    if delta.clusters_created:
        lines.append("- clusters_created:")
        for c in delta.clusters_created:
            cid = str(c.get("id", "?"))[:8]
            lines.append(f"  - `{cid}` -- {c.get('label', '?')}")
    if delta.clusters_split:
        lines.append("- clusters_split:")
        for c in delta.clusters_split:
            lines.append(f"  - `{str(c.get('id', '?'))[:8]}` -> split")
    lines.append(
        f"- proposal_rejected_min_source_clusters: "
        f"{delta.proposal_rejected_min_source_clusters}"
    )
    lines.append("")

    lines.append("## Recommended Follow-ups")
    followups = _resolve_followups(delta, agg)
    if not followups:
        lines.append(
            "_No structural follow-ups detected -- probe ran cleanly._"
        )
    else:
        for f in followups:
            lines.append(f"- {f}")
    lines.append("")

    lines.append("## Run Metadata")
    lines.append(f"- commit_sha: `{commit_sha or 'unknown'}`")
    lines.append(f"- started_at: {started_at.isoformat()}")
    lines.append(f"- completed_at: {completed_at.isoformat()}")
    lines.append(
        f"- prompts_generated: "
        f"{agg.completed_count + agg.failed_count}"
    )
    lines.append(
        f"- scoring_formula_version: {agg.scoring_formula_version}"
    )
    return "\n".join(lines)


async def _commit_with_retry(
    db: AsyncSession,
    *,
    max_attempts: int = 5,
    probe_id: str = "",
) -> None:
    """Commit with exponential backoff on SQLite "database is locked".

    The canonical batch path has just committed N Optimization INSERTs +
    OptimizationPattern joins + cluster updates immediately before. The
    warm-path engine runs in the same process and may hold writers
    concurrently. Under SQLite WAL the final ProbeRun UPDATE can hit
    transient lock contention even with busy_timeout=30s. Retrying with
    backoff (0.5s, 1s, 2s, 4s, 8s -- max ~15s) catches the window
    without losing the terminal-state write.

    Raises the underlying error after ``max_attempts`` so the
    orchestrator's top-level except handler still marks the row failed.
    """
    import sqlalchemy.exc as _sa_exc

    delay = 0.5
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            await db.commit()
            if attempt > 0:
                logger.info(
                    "probe %s commit succeeded on attempt %d",
                    probe_id, attempt + 1,
                )
            return
        except _sa_exc.OperationalError as exc:
            last_exc = exc
            if "database is locked" not in str(exc):
                raise
            logger.warning(
                "probe %s commit hit lock (attempt %d/%d); backing off %.1fs",
                probe_id, attempt + 1, max_attempts, delay,
            )
            try:
                await db.rollback()
            except Exception:
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)
    if last_exc is not None:
        raise last_exc


def _stub_dimension_scores() -> DimensionScores:
    """Per-prompt deterministic baseline scores.

    Tier 1 ProbeService synthesizes per-prompt results in-memory rather than
    calling the full pipeline (which has heavy provider/loader dependencies
    not present in unit tests). The dimension values are intentionally
    asymmetric so analysis-vs-default weight differences surface in the
    aggregate (AC-C4-6).

    Default-weights overall: 6.80; analysis-weights overall: 7.30.
    """
    return DimensionScores(
        clarity=9.0,
        specificity=9.0,
        structure=8.0,
        faithfulness=4.0,
        conciseness=4.0,
    )


def _resolve_curated_files(curated: Any) -> list[str]:
    """Return file paths from a ``CuratedCodebaseContext``-shaped object.

    Production shape: ``selected_files: list[dict]`` with ``path`` keys
    (see ``services/repo_index_query.py``). Returns ``[]`` on absent or
    falsy input.
    """
    if curated is None:
        return []
    selected = getattr(curated, "selected_files", None) or []
    out: list[str] = []
    for d in selected:
        if isinstance(d, dict):
            path = d.get("path") or d.get("file_path")
            if path:
                out.append(str(path))
    return out


def _resolve_curated_synthesis(curated: Any) -> str | None:
    """Return the cached explore-synthesis excerpt for the probe.

    The probe-specific ``explore_synthesis_excerpt`` attribute is preferred
    (set by Tier 2 grounding when the cached synthesis is layered on top of
    curated retrieval). Falls back to ``context_text`` from the production
    ``CuratedCodebaseContext`` shape.
    """
    if curated is None:
        return None
    for attr in ("explore_synthesis_excerpt", "context_text"):
        v = getattr(curated, attr, None)
        if v:
            return str(v)
    return None


def _resolve_dominant_stack(curated: Any) -> list[str]:
    """Return dominant tech stack as a list of stable string tokens.

    Tier 2 grounding will source this from ``WorkspaceIntelligence`` and
    layer it onto the curated-context object before passing it here.
    """
    if curated is None:
        return []
    stack = getattr(curated, "dominant_stack", None)
    if isinstance(stack, list):
        return [str(s) for s in stack]
    return []


# ---------------------------------------------------------------------------
# ProbeService
# ---------------------------------------------------------------------------


class ProbeService:
    """Stateless 5-phase orchestrator.

    Phases:
      1. Grounding   -- curated retrieval + project resolution + ProbeContext
      2. Generating  -- topic -> N code-grounded prompts via probe-agent
      3. Running     -- per-prompt enrichment + scoring (concurrent)
      4. Observability -- folded into Phase 3 (events fire inline)
      5. Reporting   -- aggregate + taxonomy delta + final markdown report

    Persists a ``ProbeRun`` row at start (status=running) and updates it at
    the end (status=completed|partial|failed). Yields SSE events through the
    full lifecycle.
    """

    def __init__(
        self,
        db: AsyncSession,
        provider: LLMProvider,
        repo_query: Any,
        context_service: Any,
        event_bus: Any,
        embedding_service: Any | None = None,
        session_factory: Any | None = None,
        write_queue: Any | None = None,
    ) -> None:
        self.db = db
        self.provider = provider
        self.repo_query = repo_query
        self.context_service = context_service
        self.event_bus = event_bus
        # Optional: when provided, _persist_and_assign reuses this singleton
        # instead of constructing a new EmbeddingService per prompt. Lazy
        # import inside _persist_and_assign keeps tests free of the ML
        # stack when they don't exercise persistence.
        self.embedding_service = embedding_service
        # ``session_factory`` is required for safe concurrent persistence:
        # the per-prompt as_completed loop runs up to N=10 _execute_one
        # tasks in parallel (BATCH_CONCURRENCY_BY_TIER['internal']) and
        # SQLAlchemy AsyncSession isn't safe under concurrent flush/commit.
        # Each _persist_and_assign opens a fresh session via this factory.
        # When None, falls back to a serializing asyncio.Lock around the
        # primary self.db -- safe but slower.
        self.session_factory = session_factory
        # v0.4.13 cycle 7c: optional ``WriteQueue`` for routing status
        # transitions + terminal writes through the single-writer queue
        # worker. When set, ``_set_probe_status`` + ``_mark_cancelled`` +
        # ``_mark_failed_with_error`` submit their callbacks to the queue
        # instead of committing on ``self.db`` directly. Queue-less
        # callers continue to use the v0.4.12 path. Cycle 9 wires the
        # singleton from app.state; until then tests + MCP supply it
        # explicitly.
        self.write_queue = write_queue
        self._persist_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Cycle 7c: probe row mutation helpers
    # ------------------------------------------------------------------

    async def _set_probe_status(
        self,
        probe_id: str,
        status: str,
        *,
        error: str | None = None,
        completed_at: Any | None = None,
    ) -> None:
        """Update the ProbeRun row's status (+ optional error/completed_at).

        v0.4.13 cycle 7c: when ``self.write_queue`` is set, the update
        runs inside a submit() callback labelled
        ``probe_status_transition``. Without the queue, the update goes
        directly through ``self.db`` (legacy v0.4.12 path). Either way
        the call is a no-op if the row has already moved past
        ``running`` -- callers can invoke this idempotently from
        cancellation / partial-success branches without re-overwriting
        a terminal-state write.
        """
        async def _do_update(write_db: AsyncSession) -> None:
            row = await write_db.get(ProbeRun, probe_id)
            if row is None:
                return
            row.status = status  # type: ignore[assignment]
            if error is not None:
                row.error = error
            if completed_at is not None:
                row.completed_at = completed_at
            await write_db.commit()

        if self.write_queue is not None:
            await self.write_queue.submit(
                _do_update, operation_label="probe_status_transition",
            )
            return
        # Legacy: update through self.db directly. Mirrors the inline
        # ``row.status = ...; await self.db.commit()`` pattern from
        # v0.4.12 -- callers that pre-built a row can skip this helper
        # if they want to keep their reference fresh.
        row = await self.db.get(ProbeRun, probe_id)
        if row is None:
            return
        row.status = status  # type: ignore[assignment]
        if error is not None:
            row.error = error
        if completed_at is not None:
            row.completed_at = completed_at
        await self.db.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_result(self, probe_id: str) -> ProbeRunResult:
        """Read a ProbeRun row by id and return ``ProbeRunResult``.

        Used by the C6 MCP tool. Cycle 4 ships a forward-compatible stub --
        C6 layers the tool over this.
        """
        row = await self.db.get(ProbeRun, probe_id)
        if row is None:
            raise ProbeError(
                "probe_not_found", message=f"probe_id={probe_id} not found",
            )
        prompt_results = [
            ProbePromptResult(**r) for r in (row.prompt_results or [])
        ]
        agg_dict = row.aggregate or {
            "scoring_formula_version": SCORING_FORMULA_VERSION,
        }
        agg = ProbeAggregate(**agg_dict)
        delta_dict = row.taxonomy_delta or {}
        delta = ProbeTaxonomyDelta(**delta_dict)
        return ProbeRunResult(
            id=row.id,
            topic=row.topic,
            scope=row.scope,
            intent_hint=row.intent_hint,
            repo_full_name=row.repo_full_name,
            project_id=row.project_id,
            commit_sha=row.commit_sha,
            started_at=row.started_at,
            completed_at=row.completed_at,
            prompts_generated=row.prompts_generated or 0,
            prompt_results=prompt_results,
            aggregate=agg,
            taxonomy_delta=delta,
            final_report=row.final_report or "",
            status=row.status,  # type: ignore[arg-type]
            suite_id=row.suite_id,
        )

    async def run(
        self,
        request: ProbeRunRequest,
        *,
        probe_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Execute 5 phases, yielding events. Persists ProbeRun row.

        Sets ``current_probe_id`` ContextVar for the duration; C7's
        event_logger integration injects probe_id into taxonomy event
        contexts when the var is set.
        """
        async for ev in self._run_impl(request, probe_id=probe_id):
            yield ev

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------

    async def _run_impl(
        self,
        request: ProbeRunRequest,
        *,
        probe_id: str | None,
    ) -> AsyncIterator[Any]:
        probe_id = probe_id or str(uuid4())
        started_at = datetime.now(timezone.utc)
        scope = request.scope or "**/*"
        intent_hint = request.intent_hint or "explore"
        n_prompts = request.n_prompts or 12

        # Pre-flight: persist running row.
        row = ProbeRun(
            id=probe_id,
            topic=request.topic,
            scope=scope,
            intent_hint=intent_hint,
            repo_full_name=request.repo_full_name or "",
            project_id=None,
            started_at=started_at,
            status="running",
        )
        self.db.add(row)
        await self.db.commit()

        # ContextVar token for the entire run.
        token = current_probe_id.set(probe_id)
        try:
            # ----------------------------------------------------------
            # Phase 1: Grounding
            # ----------------------------------------------------------
            try:
                get_event_logger().log_decision(
                    path="probe",
                    op="probe_started",
                    decision="probe_started",
                    context={
                        "topic": request.topic,
                        "scope": scope,
                        "intent_hint": intent_hint,
                        "n_prompts": n_prompts,
                        "repo_full_name": request.repo_full_name or "",
                    },
                )
            except RuntimeError:
                pass

            yield ProbeStartedEvent(
                probe_id=probe_id,
                topic=request.topic,
                scope=scope,
                intent_hint=intent_hint,
                n_prompts=n_prompts,
                repo_full_name=request.repo_full_name or "",
            )

            if not request.repo_full_name:
                row.status = "failed"
                row.error = "link_repo_first"
                row.completed_at = datetime.now(timezone.utc)
                await self.db.commit()
                try:
                    get_event_logger().log_decision(
                        path="probe",
                        op="probe_failed",
                        decision="probe_failed",
                        context={
                            "phase": "grounding",
                            "error_class": "ProbeError",
                            "error_message_truncated": "link_repo_first",
                        },
                    )
                except RuntimeError:
                    pass
                yield ProbeFailedEvent(
                    probe_id=probe_id,
                    phase="grounding",
                    error_class="ProbeError",
                    error_message_truncated="link_repo_first",
                )
                raise ProbeError("link_repo_first")

            # Resolve project_id. ``resolve_project_id`` already returns the
            # legacy fallback when no LinkedRepo row exists (and ``None`` if
            # legacy isn't provisioned), so a try/except here would only
            # mask genuine DB faults. Let those propagate.
            from app.services.project_service import resolve_project_id
            row.project_id = await resolve_project_id(
                self.db, repo_full_name=request.repo_full_name,
            )

            # Curated retrieval -- the topic itself is the query.
            curated = None
            try:
                curated = await self.repo_query.query_curated_context(
                    repo_full_name=request.repo_full_name,
                    branch="main",
                    query=request.topic,
                    max_chars=settings.PROBE_CODEBASE_MAX_CHARS,
                )
            except Exception as exc:
                logger.warning(
                    "probe %s: query_curated_context raised (%s) -- "
                    "continuing with empty grounding",
                    probe_id, exc,
                )

            relevant_files = _apply_scope_filter(
                _resolve_curated_files(curated), scope,
            )
            explore_excerpt = _resolve_curated_synthesis(curated)
            dominant_stack = _resolve_dominant_stack(curated)

            # Resolve known domains from existing taxonomy.
            try:
                domains_q = await self.db.execute(
                    select(PromptCluster.label).where(
                        PromptCluster.state == "domain",
                    )
                )
                known_domains = [
                    r[0] for r in domains_q.all() if r[0]
                ]
            except Exception:
                known_domains = []
            domains_pre: set[str] = set(known_domains)

            ctx = ProbeContext(
                topic=request.topic,
                scope=scope,
                intent_hint=intent_hint,
                repo_full_name=request.repo_full_name,
                project_id=row.project_id,
                project_name=(
                    request.repo_full_name.split("/")[-1]
                    if "/" in request.repo_full_name else None
                ),
                dominant_stack=dominant_stack,
                relevant_files=relevant_files,
                explore_synthesis_excerpt=explore_excerpt,
                known_domains=known_domains,
                existing_clusters_brief=[],
            )

            try:
                get_event_logger().log_decision(
                    path="probe",
                    op="probe_grounding",
                    decision="probe_grounding",
                    context={
                        "retrieved_files_count": len(relevant_files),
                        "has_explore_synthesis":
                            explore_excerpt is not None,
                        "dominant_stack": list(ctx.dominant_stack),
                    },
                )
            except RuntimeError:
                pass

            yield ProbeGroundingEvent(
                probe_id=probe_id,
                retrieved_files_count=len(relevant_files),
                has_explore_synthesis=explore_excerpt is not None,
                dominant_stack=ctx.dominant_stack,
            )

            # ----------------------------------------------------------
            # Phase 2: Generating
            # ----------------------------------------------------------
            gen_t0 = datetime.now(timezone.utc)
            try:
                prompts = await self._generate_prompts(ctx, n_prompts)
            except Exception as e:
                row.status = "failed"
                row.error = (
                    f"generation_failed: {type(e).__name__}: {e}"
                )
                row.completed_at = datetime.now(timezone.utc)
                await self.db.commit()
                try:
                    get_event_logger().log_decision(
                        path="probe",
                        op="probe_failed",
                        decision="probe_failed",
                        context={
                            "phase": "generating",
                            "error_class": type(e).__name__,
                            "error_message_truncated": _truncate(str(e), 200),
                        },
                    )
                except RuntimeError:
                    pass
                yield ProbeFailedEvent(
                    probe_id=probe_id,
                    phase="generating",
                    error_class=type(e).__name__,
                    error_message_truncated=_truncate(str(e), 200),
                )
                raise ProbeError(
                    "generation_failed", message=str(e),
                ) from e

            gen_duration = int(
                (datetime.now(timezone.utc) - gen_t0).total_seconds() * 1000
            )
            try:
                get_event_logger().log_decision(
                    path="probe",
                    op="probe_generating",
                    decision="probe_generating",
                    duration_ms=gen_duration,
                    context={
                        "prompts_generated": len(prompts),
                        "generator_model": settings.MODEL_SONNET,
                    },
                )
            except RuntimeError:
                pass

            yield ProbeGeneratingEvent(
                probe_id=probe_id,
                prompts_generated=len(prompts),
                generator_duration_ms=gen_duration,
                generator_model=settings.MODEL_SONNET,
            )

            # ----------------------------------------------------------
            # Phase 3: Running -- canonical batch_pipeline path
            # ----------------------------------------------------------
            # Per v0.4.12 spec: "Peer of seed agents -- same execution
            # primitive (batch_pipeline)". This delegates to the same
            # run_batch + bulk_persist + batch_taxonomy_assign chain that
            # ``synthesis_seed`` uses, so probe-generated rows get the
            # FULL pipeline (analyze + optimize + LLM-blended scoring +
            # auto_inject_patterns + multi-embedding + few-shot retrieval)
            # rather than the stub heuristic-only path that produced
            # barren rows in the original wiring.
            #
            # The probe retains its own orchestrator-level concerns:
            #   * Phase 1 grounding -> ProbeContext with relevant_files +
            #     explore_synthesis_excerpt + dominant_stack
            #   * Phase 2 generating -> agentic prompt synthesis
            #   * Phase 3 running -> canonical batch (this block)
            #   * Phase 5 reporting -> aggregate + taxonomy delta + report
            tier = "internal"
            from app.config import PROMPTS_DIR
            from app.database import async_session_factory as _sf
            from app.services.batch_orchestrator import run_batch
            from app.services.batch_persistence import (
                batch_taxonomy_assign,
                bulk_persist,
            )
            from app.services.domain_resolver import get_domain_resolver
            from app.services.embedding_service import EmbeddingService
            from app.services.prompt_loader import PromptLoader
            from app.services.taxonomy import get_engine

            # Both run_batch (enrichment reads) and the persist primitives
            # (bulk_persist + batch_taxonomy_assign) use the production
            # session factory now. SQLite write contention is handled by
            # ``app.database.db_writer_lock`` -- the canonical batch
            # primitives take the lock around their write blocks, so
            # concurrent probe/seed/warm-engine writers serialize
            # gracefully rather than racing for the SQLite WAL writer
            # slot.
            session_factory = self.session_factory or _sf
            try:
                tax_engine = get_engine()
            except Exception:
                tax_engine = None
            try:
                domain_resolver = get_domain_resolver()
            except Exception:
                domain_resolver = None
            emb_service = self.embedding_service or EmbeddingService()
            prompt_loader = PromptLoader(PROMPTS_DIR)

            # Phase-3 progress: emit a probe_prompt_completed event +
            # probe_progress SSE event each time a prompt finishes.
            # Closure over ``probe_id`` + the orchestrator-level counter.
            phase3_counter = {"n": 0}
            phase3_progress: list[Any] = []

            # Per-prompt streaming persistence (v0.4.12 P0b).
            #
            # Pre-fix the probe ran ``run_batch`` to completion, then
            # called ``bulk_persist(all_5_pendings)`` as ONE 5-row
            # transaction at the end. That batched commit is the
            # largest single SQLite write in the system and the most
            # likely to lose the WAL writer-slot race against
            # concurrent backend warm-path maintenance. Live diagnosis
            # (probes v22-v24, all confirmed catastrophic via the
            # verify-after-persist gate from commit ae379bf6): warm
            # path's ``Warm path maintenance-only:
            # snapshot=error-no-snapshot`` and ``Bulk persist
            # attempt 1/5 failed`` consistently appeared in tandem.
            #
            # Per-prompt streaming makes each transaction a single
            # INSERT + COMMIT (~tens of ms hold time on the WAL slot,
            # vs hundreds of ms for the 5-row variant). Each prompt is
            # idempotent via bulk_persist's ``existing_ids`` check, so
            # the existing 5x exponential backoff inside bulk_persist
            # still wraps the per-row attempt -- but the smaller window
            # is far more likely to slip between concurrent writers.
            #
            # Bonus: the user's UX complaint (probe rows appearing all
            # at once instead of as they complete) is resolved as a
            # structural side effect. Each successful per-prompt persist
            # fires its own ``optimization_created`` event-bus event
            # (see batch_persistence.py:280), and the frontend's
            # ``+page.svelte`` already routes those through HistoryPanel
            # for surgical row insertion.
            persist_tasks: list[Any] = []
            persisted_ids: set[str] = set()
            persist_errors: list[Exception] = []

            # v0.4.12 P1 — early-abort for sustained writer contention.
            #
            # When the FIRST per-prompt persist task exhausts its retry
            # budget (5 attempts × exponential backoff = 75s of trying),
            # the SQLite writer-slot contention is sustained -- the
            # remaining peer prompts' persist tasks will also fail.
            # Letting them complete their full LLM pipeline (optimize
            # Opus 4.7 + score Sonnet 4.6) costs ~3-5 minutes of wall
            # time + meaningful tokens per prompt that we KNOW won't
            # land in the DB.  Pre-fix all 5 LLM pipelines ran to
            # completion regardless of persistence outcome -- worst-case
            # 12-20 minutes of Opus 4.7 audit-class calls wasted on a
            # probe that the verify-gate would mark catastrophic anyway.
            #
            # The abort_event gates the run_batch task: when set, the
            # watchdog cancels ``run_batch_task`` which propagates
            # CancelledError through asyncio.gather to all in-flight
            # _attempt() coroutines (each holding an LLM call). Already-
            # spawned persist_tasks continue (their bulk_persist calls
            # are idempotent + retried; we don't waste the work that
            # was about to land if contention happens to clear).
            abort_event = asyncio.Event()

            async def _persist_one(p: Any) -> None:
                """Single-prompt persistence task spawned per completion.

                Reuses bulk_persist's idempotency + retry path but on a
                1-element list. Three outcomes:

                * Success (``n > 0``): record the id in ``persisted_ids``
                  so the post-run verify gate sees it.
                * Intentional rejection (``n == 0`` with no exception):
                  bulk_persist's quality gate (``overall_score < 5.0``)
                  or ID-shape gate (non-uuid id, test-fixture indicator)
                  rejected the row at insert-time. NOT a persistence
                  failure -- the row was correctly NOT durable. Tag the
                  pending so the verify-gate excludes it from
                  ``expected_completed`` and doesn't misclassify a
                  legitimate quality outcome as catastrophic
                  contention. (v0.4.12 review C1.)
                * Exception (e.g. SQLite "database is locked"): real
                  persistence failure. Capture for the gate's
                  partial/catastrophic classification AND signal
                  ``abort_event`` so peer LLM calls can be cancelled.
                """
                try:
                    n = await bulk_persist(
                        [p], session_factory, batch_id=probe_id,
                    )
                    if n > 0:
                        persisted_ids.add(p.id)
                    else:
                        # Quality-gate or ID-shape gate dropped the row.
                        # Not a persistence failure -- the row was never
                        # going to be durable, by design.
                        setattr(p, "_persist_intentionally_rejected", True)
                        logger.info(
                            "Per-prompt persist intentionally rejected "
                            "%s (score=%s) -- bulk_persist quality/id gate",
                            p.id[:8] if p.id else "<no-id>",
                            p.overall_score,
                        )
                except Exception as exc:
                    persist_errors.append(exc)
                    setattr(p, "_persist_dropped", True)
                    logger.warning(
                        "Per-prompt persist failed for %s: %s",
                        p.id[:8] if p.id else "<no-id>",
                        exc,
                        exc_info=True,
                    )
                    # Signal early-abort. Idempotent at the consumer
                    # (asyncio.Event.set is safe to call multiple times).
                    abort_event.set()

            def _on_progress(idx: int, total: int, pending: Any) -> None:
                phase3_counter["n"] += 1
                # Stash for SSE yield outside the callback (we can't
                # ``yield`` from here -- it's a sync callback inside
                # asyncio.gather). The orchestrator drains this list
                # after run_batch returns.
                phase3_progress.append((idx, pending))
                try:
                    get_event_logger().log_decision(
                        path="probe",
                        op="probe_prompt_completed",
                        decision="probe_prompt_completed",
                        optimization_id=pending.id,
                        context={
                            "prompt_idx": idx,
                            "current": phase3_counter["n"],
                            "total": total,
                            "intent_label": pending.intent_label,
                            "overall_score": pending.overall_score,
                            "status": pending.status,
                        },
                    )
                except RuntimeError:
                    pass

                # Spawn the per-prompt persistence task immediately so
                # the row lands in the DB while peer prompts are still
                # in their LLM phase. Skipped for failed/rate-limited
                # rows (their status != "completed" so bulk_persist
                # would skip them anyway -- avoid the wasted round trip).
                if pending.status == "completed":
                    persist_tasks.append(
                        asyncio.create_task(_persist_one(pending))
                    )

            # Wrap run_batch in a Task so the abort_event watchdog can
            # cancel it on sustained writer-slot contention. CancelledError
            # propagates through asyncio.gather to every in-flight
            # _attempt() coroutine, releasing the LLM-call coroutines and
            # closing the provider stream early. Already-completed pendings
            # are preserved via the on_progress callback (which appends to
            # phase3_progress as each prompt finishes scoring), so the
            # abort fallback recovers the work already done.
            run_batch_task = asyncio.create_task(
                run_batch(
                    prompts=prompts,
                    provider=self.provider,
                    prompt_loader=prompt_loader,
                    embedding_service=emb_service,
                    max_parallel=BATCH_CONCURRENCY_BY_TIER[tier],
                    codebase_context=ctx.explore_synthesis_excerpt,
                    repo_full_name=ctx.repo_full_name,
                    batch_id=probe_id,
                    on_progress=_on_progress,
                    session_factory=session_factory,
                    taxonomy_engine=tax_engine,
                    domain_resolver=domain_resolver,
                    tier=tier,
                    context_service=self.context_service,
                )
            )

            async def _abort_watcher() -> None:
                """Cancel run_batch_task when ``abort_event`` fires.

                Fires once at most -- ``abort_event`` is the event-driven
                signal from ``_persist_one``'s catch block. Watcher exits
                cleanly when run_batch finishes normally (parent task
                cancellation in the finally clause below).
                """
                await abort_event.wait()
                if not run_batch_task.done():
                    n_completed = len(phase3_progress)
                    n_total = len(prompts)
                    logger.warning(
                        "probe %s aborting run_batch -- per-prompt persist "
                        "failed catastrophic (writer-slot contention). "
                        "Cancelling %d in-flight LLM calls; preserving %d "
                        "already-scored pendings.",
                        probe_id, n_total - n_completed, n_completed,
                    )
                    try:
                        get_event_logger().log_decision(
                            path="probe",
                            op="probe_early_abort",
                            decision="probe_early_abort",
                            context={
                                "probe_id": probe_id,
                                "completed_before_abort": n_completed,
                                "cancelled_in_flight": n_total - n_completed,
                                "reason": "persist_catastrophic",
                            },
                        )
                    except RuntimeError:
                        pass
                    run_batch_task.cancel()

            abort_watcher_task = asyncio.create_task(_abort_watcher())

            try:
                pendings = await run_batch_task
            except asyncio.CancelledError:
                # Early-abort path: the watchdog cancelled run_batch
                # because the first per-prompt persist exhausted its
                # retry budget. ``phase3_progress`` carries every
                # PendingOptimization that managed to finish scoring
                # before the abort fired; the rest never produced a
                # PendingOptimization (their LLM calls were cancelled
                # mid-flight). Treat the early-abort pendings as the
                # full result set -- the verify-after-persist gate
                # below will then either find some persisted (partial)
                # or none (catastrophic).
                pendings = [p for _, p in phase3_progress]
            except Exception as exc:
                logger.warning(
                    "probe %s run_batch raised (%s) -- marking failed",
                    probe_id, exc, exc_info=True,
                )
                pendings = []
            finally:
                # Watcher cleanup: if run_batch finished cleanly, the
                # abort_event never fired, so the watcher is still
                # parked on ``await abort_event.wait()``. Cancel it
                # so it doesn't outlive the run() generator. CancelledError
                # is swallowed -- it's the expected signal here.
                if not abort_watcher_task.done():
                    abort_watcher_task.cancel()
                    try:
                        await abort_watcher_task
                    except asyncio.CancelledError:
                        pass

            # Detect rate-limit signal from run_batch results.  If ANY
            # PendingOptimization came back with heuristic_flags.rate_limited,
            # emit a structured ProbeRateLimitedEvent with reset_at so the
            # UI can render a precise "retry after X" countdown. This is
            # surfaced BEFORE persistence runs so the user sees the
            # rate-limit context even if the partial batch then persists
            # cleanly.
            rate_limit_meta_first: dict | None = None
            rate_limited_aborted = 0
            rate_limited_completed = 0
            for p in pendings:
                # rate_limit_meta is the dedicated rate-limit channel.
                # heuristic_flags is reserved for the blender's
                # divergence_flags (a list, not a dict) and would crash
                # `.get()` if we mixed the two on the same field.
                flags = getattr(p, "rate_limit_meta", None) or {}
                if flags.get("rate_limited"):
                    if rate_limit_meta_first is None:
                        rate_limit_meta_first = flags
                    if flags.get("rate_limit_aborted_by_sibling"):
                        rate_limited_aborted += 1
                elif p.status == "completed":
                    rate_limited_completed += 1
            if rate_limit_meta_first is not None:
                rate_limited_completed = sum(
                    1 for p in pendings if p.status == "completed"
                )
                try:
                    get_event_logger().log_decision(
                        path="probe",
                        op="probe_rate_limited",
                        decision="probe_rate_limited",
                        context={
                            "provider": rate_limit_meta_first.get("provider"),
                            "reset_at_iso":
                                rate_limit_meta_first.get("reset_at_iso"),
                            "estimated_wait_seconds":
                                rate_limit_meta_first.get(
                                    "estimated_wait_seconds"
                                ),
                            "completed_count": rate_limited_completed,
                            "aborted_count": rate_limited_aborted,
                            "total": len(pendings),
                        },
                    )
                except RuntimeError:
                    pass
                yield ProbeRateLimitedEvent(
                    probe_id=probe_id,
                    provider=rate_limit_meta_first.get("provider")
                        or "unknown",
                    reset_at_iso=rate_limit_meta_first.get("reset_at_iso"),
                    estimated_wait_seconds=(
                        rate_limit_meta_first.get("estimated_wait_seconds")
                    ),
                    completed_count=rate_limited_completed,
                    aborted_count=rate_limited_aborted,
                    total=len(pendings),
                )

                # Global rate-limit signal: publish onto the event bus so
                # the frontend's `rateLimitStore` can render the global
                # banner + Settings card without needing to subscribe to
                # every probe SSE.  Mirrors the routing_state_changed
                # broadcast pattern.  The `rate_limit_cleared` companion
                # is published below by the canonical batch path on the
                # next successful LLM call against the same provider.
                try:
                    event_bus.publish("rate_limit_active", {
                        "provider": rate_limit_meta_first.get("provider")
                            or "unknown",
                        "reset_at_iso":
                            rate_limit_meta_first.get("reset_at_iso"),
                        "estimated_wait_seconds":
                            rate_limit_meta_first.get(
                                "estimated_wait_seconds"
                            ),
                        "source": "probe",
                        "probe_id": probe_id,
                    })
                except Exception:
                    logger.debug(
                        "rate_limit_active publish failed", exc_info=True,
                    )

            # Drain per-prompt persistence tasks. Each task was spawned
            # by ``_on_progress`` as the corresponding prompt finished
            # scoring -- so by the time ``run_batch`` returns, most are
            # already done; ``gather`` just collects the stragglers.
            # ``return_exceptions=True`` ensures one task's failure
            # doesn't cancel the others (they're independent).
            if persist_tasks:
                await asyncio.gather(*persist_tasks, return_exceptions=True)

            # Cluster assignment runs ONLY for rows that actually landed
            # (stream-persisted ids). Pre-fix this assigned clusters for
            # ghost rows that bulk_persist had silently dropped, polluting
            # the taxonomy with cluster_ids referencing non-existent
            # Optimization rows. ``persisted_pendings`` is the durable
            # subset, ordered by their position in the run_batch result
            # list so cluster centroids reflect the right embedding mix.
            persisted_pendings = [p for p in pendings if p.id in persisted_ids]
            persist_error: Exception | None = (
                persist_errors[0] if persist_errors else None
            )
            if persisted_pendings:
                try:
                    await batch_taxonomy_assign(
                        persisted_pendings,
                        session_factory,
                        batch_id=probe_id,
                    )
                except Exception as _bta_exc:
                    persist_error = persist_error or _bta_exc
                    logger.warning(
                        "probe batch_taxonomy_assign failed",
                        exc_info=True,
                    )

            # Verify-after-persist gate (v0.4.12 P0a/P0b).
            #
            # Pre-fix the probe reported status='completed' with a
            # mean_overall computed from in-memory PendingOptimization
            # objects (whose status='completed' was set during scoring)
            # even when ZERO Optimization rows landed in the DB --
            # an outright correctness defect, not a UX gap. Now we
            # query the DB for the canonical truth of what got
            # persisted before we report anything to the user.
            #
            # In the streaming-persistence world the per-prompt tasks
            # already maintain ``persisted_ids`` as a set of believed-
            # durable ids. The DB SELECT here is the source-of-truth
            # confirmation -- and a SELECT can never deadlock on the
            # writer lock (no dirty/new/deleted rows on the verify
            # session, so WriterLockedAsyncSession's gate skips
            # acquisition). Single query: capture the durable ids
            # directly, then count = len(set), then drive the same
            # three-outcome path as the bulk-persist version did.
            #
            # Outcomes:
            #   * full   -- persisted_actual == expected: proceed normally
            #   * partial -- 0 < persisted_actual < expected: drop the
            #     ghost rows from prompt_results so aggregate +
            #     taxonomy_delta only count what's durable
            #   * catastrophic -- persisted_actual == 0: raise so the
            #     top-level except handler marks the row failed with
            #     the underlying persistence exception preserved
            # ``expected_completed`` counts pendings that should have
            # produced a durable Optimization row. Excludes pendings
            # tagged ``_persist_intentionally_rejected=True`` by
            # ``_persist_one`` -- those are quality-gate / ID-shape
            # rejections that bulk_persist correctly DROPPED at insert
            # time. Without this exclusion, a probe whose 5 prompts
            # all scored below the seed quality floor (5.0) would have
            # ``expected=5, actual=0`` and the verify-gate would
            # misclassify the legitimate quality outcome as catastrophic
            # persistence contention. (v0.4.12 review C1 fix.)
            expected_completed = sum(
                1 for p in pendings
                if p.status == "completed"
                and not getattr(p, "_persist_intentionally_rejected", False)
            )
            persisted_id_set: set[str] = set()
            if expected_completed > 0:
                pending_ids = [
                    p.id for p in pendings
                    if p.status == "completed"
                    and not getattr(p, "_persist_intentionally_rejected", False)
                ]
                async with session_factory() as _verify_db:
                    _verify_q = await _verify_db.execute(
                        select(Optimization.id).where(
                            Optimization.id.in_(pending_ids),
                        )
                    )
                    persisted_id_set = {
                        row for row in _verify_q.scalars().all()
                    }
            persisted_actual = len(persisted_id_set)
            if expected_completed > 0 and persisted_actual == 0:
                # Catastrophic: nothing landed. Raise so the top-level
                # except handler in run() marks the probe row as failed
                # with structured error info + emits probe_failed.
                err_class = (
                    type(persist_error).__name__
                    if persist_error else "PersistenceVerificationFailed"
                )
                err_msg = (
                    str(persist_error)
                    if persist_error
                    else "expected to persist {n} rows; 0 landed".format(
                        n=expected_completed
                    )
                )
                raise RuntimeError(
                    f"probe persistence catastrophic: "
                    f"{err_class}: {err_msg}"
                ) from persist_error
            if persisted_actual < expected_completed:
                logger.warning(
                    "probe %s partial persistence: %d of %d rows landed",
                    probe_id, persisted_actual, expected_completed,
                )
                try:
                    get_event_logger().log_decision(
                        path="probe",
                        op="probe_partial_persistence",
                        decision="probe_partial_persistence",
                        context={
                            "probe_id": probe_id,
                            "expected": expected_completed,
                            "persisted": persisted_actual,
                            "lost": expected_completed - persisted_actual,
                            "underlying_error": (
                                f"{type(persist_error).__name__}: "
                                f"{str(persist_error)[:200]}"
                                if persist_error else None
                            ),
                        },
                    )
                except RuntimeError:
                    pass

            # Filter ghost rows out of pendings so downstream
            # prompt_results, taxonomy delta, and aggregate only see
            # what actually landed. ``persisted_id_set`` is empty on
            # the full-success path (no filter applied).
            if persisted_id_set:
                for p in pendings:
                    if (
                        p.status == "completed"
                        and p.id not in persisted_id_set
                    ):
                        p.status = "failed"
                        # Stash a flag so _pending_to_prompt_result can
                        # surface "persistence dropped" rather than a
                        # silent skip.
                        setattr(p, "_persist_dropped", True)

            # Tag persisted rows with source="probe" so downstream
            # analytics distinguish probe-originated from seed-originated
            # batch rows. Also overlay probe-specific context_sources
            # (probe_topic + probe_intent_hint), and rehydrate cluster_id
            # from the Optimization rows (batch_taxonomy_assign wrote it
            # there post-PendingOptimization construction).
            persisted_ids = [
                p.id for p in pendings if p.status == "completed"
            ]
            cluster_id_by_opt = await self._tag_probe_rows(
                session_factory, persisted_ids,
                probe_topic=request.topic,
                probe_intent_hint=intent_hint,
            )
            # Backfill cluster_id onto PendingOptimization objects so
            # _pending_to_prompt_result can include it.
            for p in pendings:
                if p.id in cluster_id_by_opt:
                    setattr(p, "cluster_id", cluster_id_by_opt[p.id])

            # Convert PendingOptimizations -> ProbePromptResults.  Use
            # the indexed prompts list (run_batch preserves order via
            # results[index] = result).  Emit ProbeProgressEvent SSE
            # for each one in arrival order (using the closure-captured
            # phase3_progress list to preserve completion semantics).
            prompt_results: list[ProbePromptResult] = []
            seen_ids: set[int] = set()
            for idx, pending in phase3_progress:
                if idx in seen_ids:
                    continue
                seen_ids.add(idx)
                ppr = self._pending_to_prompt_result(idx, pending, prompts)
                prompt_results.append(ppr)
                yield ProbeProgressEvent(
                    probe_id=probe_id,
                    current=len(prompt_results),
                    total=len(prompts),
                    optimization_id=ppr.optimization_id or "",
                    intent_label=ppr.intent_label,
                    overall_score=ppr.overall_score,
                )
            # Cover any prompts that didn't fire on_progress (run_batch
            # rate-limit retry path returns directly without callback).
            for i, p in enumerate(pendings):
                if i in seen_ids:
                    continue
                ppr = self._pending_to_prompt_result(i, p, prompts)
                prompt_results.append(ppr)
                yield ProbeProgressEvent(
                    probe_id=probe_id,
                    current=len(prompt_results),
                    total=len(prompts),
                    optimization_id=ppr.optimization_id or "",
                    intent_label=ppr.intent_label,
                    overall_score=ppr.overall_score,
                )
            prompt_results.sort(key=lambda r: r.prompt_idx)

            # ----------------------------------------------------------
            # Phase 5: Reporting (Phase 4 observability folded into 3)
            # ----------------------------------------------------------
            completed = [
                r for r in prompt_results if r.status == "completed"
            ]
            failed = [
                r for r in prompt_results if r.status != "completed"
            ]
            scores = [
                r.overall_score for r in completed
                if r.overall_score is not None
            ]

            # Resolve taxonomy delta -- diff cluster set + domain set.
            cluster_rows: list[Any] = []
            cluster_ids = [
                r.cluster_id_at_persist for r in completed
                if r.cluster_id_at_persist
            ]
            if cluster_ids:
                try:
                    cluster_q = await self.db.execute(
                        select(
                            PromptCluster.id,
                            PromptCluster.label,
                            PromptCluster.member_count,
                        ).where(PromptCluster.id.in_(cluster_ids))
                    )
                    cluster_rows = list(cluster_q.all())
                except Exception:
                    cluster_rows = []

            try:
                domains_post_q = await self.db.execute(
                    select(PromptCluster.label).where(
                        PromptCluster.state == "domain",
                    )
                )
                domains_post = {
                    r[0] for r in domains_post_q.all() if r[0]
                }
            except Exception:
                domains_post = set(domains_pre)

            delta = ProbeTaxonomyDelta(
                domains_created=sorted(domains_post - domains_pre),
                sub_domains_created=[],
                clusters_created=[
                    {
                        "id": str(cid),
                        "label": str(lab or "?"),
                        "member_count": str(mc or 0),
                    }
                    for cid, lab, mc in cluster_rows
                ],
                clusters_split=[],
                proposal_rejected_min_source_clusters=0,
            )

            # Aggregate
            agg_mean: float | None = None
            agg_p5: float | None = None
            agg_p50: float | None = None
            agg_p95: float | None = None
            if scores:
                agg_mean = round(statistics.mean(scores), 3)
                agg_p50 = round(statistics.median(scores), 3)
                if len(scores) >= 5:
                    qs = statistics.quantiles(scores, n=20)
                    agg_p5 = round(qs[0], 3)
                    agg_p95 = round(qs[-1], 3)
                else:
                    agg_p5 = round(min(scores), 3)
                    agg_p95 = round(max(scores), 3)

            agg = ProbeAggregate(
                mean_overall=agg_mean,
                p5_overall=agg_p5,
                p50_overall=agg_p50,
                p95_overall=agg_p95,
                completed_count=len(completed),
                failed_count=len(failed),
                f5_flag_fires=0,
                scoring_formula_version=SCORING_FORMULA_VERSION,
            )

            completed_at = datetime.now(timezone.utc)
            final_report = _render_final_report(
                request, probe_id, started_at, completed_at,
                prompt_results, agg, delta,
                commit_sha=None,
                rate_limit_meta=rate_limit_meta_first,
            )

            # Status state machine per AC-C4-3.
            if not failed:
                final_status = "completed"
            elif len(failed) == len(prompt_results):
                final_status = "failed"
            else:
                final_status = "partial"

            row.completed_at = completed_at
            row.prompts_generated = len(prompt_results)
            row.prompt_results = [
                r.model_dump() for r in prompt_results
            ]
            row.aggregate = agg.model_dump()
            row.taxonomy_delta = delta.model_dump()
            row.final_report = final_report
            row.status = final_status
            # Stamp rate-limit context onto ``error`` so list endpoints
            # and the UI can filter / badge probe runs that hit limits
            # without scanning the full report. Format:
            # ``rate_limited:reset_at=<ISO>:provider=<name>``
            if rate_limit_meta_first is not None:
                _reset = rate_limit_meta_first.get("reset_at_iso") or "?"
                _prov = rate_limit_meta_first.get("provider") or "?"
                row.error = (
                    f"rate_limited:reset_at={_reset}:provider={_prov}"
                )[:500]
            # Retry-on-locked: the canonical-batch persist path commits
            # 5 optimization INSERTs + cluster assigns + tag overlay
            # right before this. Under SQLite WAL with the warm-path
            # engine running concurrently in the same process, the
            # final ProbeRun UPDATE can hit "database is locked"
            # transiently. Exponential backoff catches the contention
            # window without losing the row.
            await _commit_with_retry(self.db, max_attempts=5, probe_id=probe_id)

            # Single taxonomy_changed publish per probe -- mirrors batch
            # seeding semantics. Hot-path assign_cluster() already wrote
            # member_count + centroid for each prompt; this event triggers
            # the warm-path debounced reconciliation cycle so labels +
            # patterns are refreshed for any clusters touched by the probe.
            if any(
                r.cluster_id_at_persist for r in prompt_results
                if r.status == "completed"
            ):
                try:
                    event_bus.publish("taxonomy_changed", {
                        "trigger": "probe",
                        "probe_id": probe_id,
                    })
                except Exception:
                    logger.debug(
                        "probe taxonomy_changed publish failed",
                        exc_info=True,
                    )

            try:
                get_event_logger().log_decision(
                    path="probe",
                    op="probe_completed",
                    decision="probe_completed",
                    context={
                        "status": final_status,
                        "mean_overall": agg.mean_overall,
                        "prompts_generated": len(prompt_results),
                        "taxonomy_delta_summary": {
                            "domains_created":
                                len(delta.domains_created),
                            "sub_domains_created":
                                len(delta.sub_domains_created),
                            "clusters_created":
                                len(delta.clusters_created),
                            "clusters_split":
                                len(delta.clusters_split),
                            "proposal_rejected_min_source_clusters":
                                delta.proposal_rejected_min_source_clusters,
                        },
                    },
                )
            except RuntimeError:
                pass

            yield ProbeCompletedEvent(
                probe_id=probe_id,
                status=final_status,  # type: ignore[arg-type]
                mean_overall=agg.mean_overall,
                prompts_generated=len(prompt_results),
                taxonomy_delta_summary={
                    "domains_created": len(delta.domains_created),
                    "sub_domains_created": len(delta.sub_domains_created),
                    "clusters_created": len(delta.clusters_created),
                    "clusters_split": len(delta.clusters_split),
                    "proposal_rejected_min_source_clusters":
                        delta.proposal_rejected_min_source_clusters,
                },
            )
        except asyncio.CancelledError:
            # Client disconnect mid-stream (e.g. FastAPI ClientDisconnect)
            # cancels this generator. Pre-fix the row stayed at status=
            # 'running' forever -- now we mark it failed with
            # error='cancelled' before re-raising.
            #
            # asyncio.shield: when the cancelled task awaits in this except
            # block, the await would re-raise CancelledError immediately
            # *unless* the awaitable is shielded. Without shield the row
            # write would not land. ``_mark_cancelled`` itself is wrapped
            # in shield so the outer ``except Exception`` still catches a
            # real DB failure (the GC sweep is the safety net).
            try:
                await asyncio.shield(self._mark_cancelled(row, probe_id))
            except asyncio.CancelledError:
                # Re-raised at await boundaries even with shield when the
                # task is cancelled multiple times -- treat like commit
                # failure and let GC reconcile.
                logger.warning(
                    "Probe %s cancelled twice; GC will reconcile row",
                    probe_id,
                )
            except Exception:
                logger.warning(
                    "Probe %s cancelled, row update failed; GC will reconcile",
                    probe_id, exc_info=True,
                )
            raise
        except Exception as exc:
            # Defense in depth: any uncaught exception in run() must
            # mark the row as failed before propagating, otherwise we
            # leak running rows. Per-phase try/except wrappers cover
            # the documented failure modes; this handler covers
            # unexpected raises (reporting-phase computation, DB commit
            # retry exhaustion, future regressions).
            try:
                await asyncio.shield(self._mark_failed_with_error(
                    row, probe_id,
                    phase="running",
                    error_class=type(exc).__name__,
                    error_message=str(exc),
                ))
            except Exception:
                logger.warning(
                    "Probe %s mid-run failure (%s); row update failed; "
                    "GC will reconcile",
                    probe_id, type(exc).__name__, exc_info=True,
                )
            raise
        finally:
            try:
                current_probe_id.reset(token)
            except ValueError:
                # ContextVar token was created in a different context (can
                # happen when CancelledError is raised from a consumer task).
                # The ContextVar copy in this task will be discarded with
                # the frame anyway, so this is benign.
                pass

    async def _mark_cancelled(
        self, row: ProbeRun, probe_id: str,
    ) -> None:
        """Idempotent: mark row failed with error='cancelled' if still running.

        v0.4.13 cycle 7c: when ``self.write_queue`` is set the terminal
        write goes through a submit() callback labelled
        ``probe_mark_cancelled`` so cancellation racing against a
        concurrent backend writer cannot lose the row's terminal state.
        The caller-supplied ``row`` reference is mutated for in-process
        consistency, but the queue path also re-reads the row inside
        the callback so the writer-engine session has the canonical
        copy.
        """
        completed_at = datetime.now(timezone.utc)

        if self.write_queue is not None:
            async def _do_mark(write_db: AsyncSession) -> None:
                # Re-read inside the writer session -- the caller's
                # ``row`` is bound to ``self.db``'s identity map and
                # can't be added across sessions.
                fresh = await write_db.get(ProbeRun, probe_id)
                if fresh is None:
                    return
                fresh.status = "failed"  # type: ignore[assignment]
                fresh.error = "cancelled"
                fresh.completed_at = completed_at
                await write_db.commit()

            await self.write_queue.submit(
                _do_mark, operation_label="probe_mark_cancelled",
            )
            # Keep the in-memory reference consistent for any caller
            # that inspects ``row`` after this returns.
            row.status = "failed"  # type: ignore[assignment]
            row.error = "cancelled"
            row.completed_at = completed_at
            return

        # Legacy: write through self.db (v0.4.12 path).
        row.status = "failed"  # type: ignore[assignment]
        row.error = "cancelled"
        row.completed_at = completed_at
        await self.db.commit()

    async def _mark_failed_with_error(
        self,
        row: ProbeRun,
        probe_id: str,
        *,
        phase: str,
        error_class: str,
        error_message: str,
    ) -> None:
        """Mark row failed with structured ``error`` info + emit a
        ``probe_failed`` decision event.

        Companion to ``_mark_cancelled`` for the top-level
        ``except Exception`` handler in ``run()``. Truncates the
        captured message so a runaway exception body can't blow out
        the column. Event-logger call is wrapped in
        ``try/except RuntimeError`` per the rest of this module so an
        un-initialized logger (test harness) doesn't mask the actual
        DB write.
        """
        row.status = "failed"
        row.error = (
            f"{error_class}: {_truncate(error_message, 500)} "
            f"(phase={phase})"
        )
        row.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        try:
            get_event_logger().log_decision(
                path="probe",
                op="probe_failed",
                decision="probe_failed",
                context={
                    "probe_id": probe_id,
                    "phase": phase,
                    "error_class": error_class,
                    "error_message_truncated":
                        _truncate(error_message, 200),
                },
            )
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Phase 2 helper -- prompt generation
    # ------------------------------------------------------------------

    async def _generate_prompts(
        self, ctx: ProbeContext, n_prompts: int,
    ) -> list[str]:
        """Delegate to the C3 ``generate_probe_prompts`` primitive.

        The primitive owns: prompt-template rendering, retry policy
        (``call_provider_with_retry`` with ``max_retries=3``), and the F1
        backtick-density filter (>50% drop -> ``ProbeGenerationError``).
        Keeping this thin wrapper preserves a stable orchestrator-level
        seam if/when probe generation grows additional inputs (e.g.
        per-tier model overrides).
        """
        prompts = await generate_probe_prompts(
            ctx, provider=self.provider, n_prompts=n_prompts,
        )
        if not prompts:
            raise RuntimeError(
                "probe-agent generator returned 0 prompts"
            )
        return prompts

    # ------------------------------------------------------------------
    # Phase 3 helper -- per-prompt execution
    # ------------------------------------------------------------------

    async def _execute_one(
        self,
        idx: int,
        prompt: str,
        ctx: ProbeContext,
    ) -> ProbePromptResult:
        """Execute one prompt: enrich -> score -> persist -> assign cluster.

        Persists an ``Optimization`` row and assigns it to the taxonomy via
        ``family_ops.assign_cluster()`` so probe-generated prompts become
        first-class artifacts (visible in /api/optimizations history,
        topology view, cluster matching). Tier 1 design: no optimize-phase
        LLM call -- but every other piece of pipeline infrastructure
        (real ``HeuristicScorer``, multi-embedding, real heuristic
        analysis from ``enrich()``) IS used so the persisted row is
        indistinguishable from a non-optimized historical optimization.

        Returns ``status='failed'`` (no row persisted) on any enrich /
        embed / persist / cluster-assign exception so AC-C4-3
        partial-status semantics hold.
        """
        import time as _time

        from app.schemas.pipeline_contracts import DimensionScores
        from app.services.heuristic_scorer import HeuristicScorer
        from app.services.pipeline_constants import MAX_INTENT_LABEL_LENGTH
        from app.utils.text_cleanup import title_case_label, validate_intent_label

        _t0 = _time.monotonic()
        try:
            enriched = await self.context_service.enrich(
                raw_prompt=prompt,
                tier="internal",  # probe always runs Phase 3 on the
                                  # internal provider (no sampling
                                  # round-trip on per-prompt enrich).
                db=self.db,
                repo_full_name=ctx.repo_full_name,
                project_id=ctx.project_id,
                provider=self.provider,
            )
        except Exception as exc:
            logger.warning(
                "probe prompt %d enrich raised (%s) -- marking failed",
                idx, exc, exc_info=True,
            )
            return ProbePromptResult(
                prompt_idx=idx,
                prompt_text=_truncate(prompt, 1000),
                status="failed",
            )

        # Canonical attribute name is ``analysis``, NOT ``heuristic_analysis``
        # (which is only a bool flag inside ``context_sources``). The latter
        # was the v0.4.12 INTEGRATE-phase defect: production attr-lookup
        # returned None for every prompt, defaulting task_type/domain to
        # "general" -- batch_pipeline.py:337 is the canonical reference.
        heuristic = enriched.analysis if hasattr(enriched, "analysis") else None
        # Defensive str coercion -- non-str field values (e.g. MagicMock
        # leaking from a poorly-built test fixture) get rejected so they
        # don't become DB column values.
        def _as_str_or_none(v: Any) -> str | None:
            return v if isinstance(v, str) else None

        task_type = (
            _as_str_or_none(getattr(heuristic, "task_type", None))
            or "general"
        )
        # ``domain_raw`` carries the heuristic's full output (including
        # sub-domain qualifier syntax like ``backend: taxonomy``);
        # ``effective_domain`` is the canonicalized primary label
        # registered in DomainResolver, which is what assign_cluster()
        # needs to find the correct domain node parent. Without this
        # resolution every sub-domain-namespaced prompt collapses to the
        # ``general`` domain node (the v0.4.12 cluster-parenting bug).
        domain_raw = (
            _as_str_or_none(getattr(heuristic, "domain", None)) or "general"
        )
        confidence = float(getattr(heuristic, "confidence", 0.0) or 0.0)
        effective_domain = await self._resolve_effective_domain(
            domain_raw, confidence, prompt,
        )
        # Intent label normalization mirrors the canonical persist paths
        # (pipeline_phases.py:1041-1044, batch_pipeline.py:594-597):
        # title-case → validate → length-clip. Falling back to a raw
        # prompt fragment (the prior bug) polluted ``QualifierIndex``
        # with 80-char strings instead of 3-6 word labels.
        raw_label = (
            _as_str_or_none(getattr(heuristic, "intent_label", None))
            or "general"
        )
        intent_label = validate_intent_label(
            title_case_label(raw_label), prompt,
        )[:MAX_INTENT_LABEL_LENGTH]

        # Real heuristic scoring (replaces deterministic stub).
        # Falls back to the stub on any exception so the F3.1 contract
        # test path still has a deterministic baseline. Production almost
        # never trips this -- HeuristicScorer.score_prompt is pure regex.
        try:
            heur_dict = HeuristicScorer.score_prompt(prompt)
            scores = DimensionScores(
                clarity=heur_dict["clarity"],
                specificity=heur_dict["specificity"],
                structure=heur_dict["structure"],
                faithfulness=heur_dict["faithfulness"],
                conciseness=heur_dict["conciseness"],
            )
        except Exception:
            scores = _stub_dimension_scores()
        overall = scores.compute_overall(task_type=task_type)

        # Persist + cluster-assign.  Failures here mark the prompt failed
        # rather than crashing the whole probe.
        try:
            opt_id, cluster_id, cluster_label = await self._persist_and_assign(
                prompt=prompt,
                task_type=task_type,
                domain=effective_domain,
                domain_raw=domain_raw,
                intent_label=intent_label,
                scores=scores,
                overall=overall,
                ctx=ctx,
                duration_ms=int((_time.monotonic() - _t0) * 1000),
            )
        except Exception as exc:
            logger.warning(
                "probe prompt %d persist/assign raised (%s) -- marking failed",
                idx, exc, exc_info=True,
            )
            return ProbePromptResult(
                prompt_idx=idx,
                prompt_text=_truncate(prompt, 1000),
                status="failed",
            )

        return ProbePromptResult(
            prompt_idx=idx,
            prompt_text=_truncate(prompt, 1000),
            optimization_id=opt_id,
            overall_score=overall,
            intent_label=intent_label,
            cluster_id_at_persist=cluster_id,
            cluster_label_at_persist=cluster_label,
            domain=effective_domain,
            duration_ms=int((_time.monotonic() - _t0) * 1000),
            status="completed",
        )

    def _pending_to_prompt_result(
        self,
        idx: int,
        pending: Any,
        prompts: list[str],
    ) -> ProbePromptResult:
        """Map a ``PendingOptimization`` (canonical batch primitive) to a
        ``ProbePromptResult`` for the probe SSE / row JSON contract."""
        prompt_text = (
            pending.raw_prompt if pending and pending.raw_prompt
            else (prompts[idx] if 0 <= idx < len(prompts) else "")
        )
        if pending.status == "completed":
            return ProbePromptResult(
                prompt_idx=idx,
                prompt_text=_truncate(prompt_text, 1000),
                optimization_id=pending.id,
                overall_score=pending.overall_score,
                intent_label=pending.intent_label,
                cluster_id_at_persist=getattr(pending, "cluster_id", None),
                cluster_label_at_persist=None,
                domain=pending.domain,
                duration_ms=pending.duration_ms,
                status="completed",
            )
        return ProbePromptResult(
            prompt_idx=idx,
            prompt_text=_truncate(prompt_text, 1000),
            status="failed",
        )

    async def _tag_probe_rows(
        self,
        session_factory: Any,
        opt_ids: list[str],
        *,
        probe_topic: str,
        probe_intent_hint: str,
    ) -> dict[str, str]:
        """Overlay probe-specific tags + return cluster_id mapping.

        ``bulk_persist`` writes ``context_sources={"source": "batch_seed",
        "batch_id": probe_id, ...}`` because it shares the seed batch
        primitive. Probe rows need ``source="probe"`` + the topic /
        intent_hint so downstream analytics distinguish probe-originated
        rows from seed-agent rows. Applies the overlay in a single
        UPDATE per probe -- runs after persist+taxonomy_assign so we
        don't race the canonical primitives.

        Returns ``{optimization_id: cluster_id}`` so the caller can
        backfill ``PendingOptimization.cluster_id`` (PendingOptimization
        has no cluster_id field; it's written onto ``Optimization`` only
        by batch_taxonomy_assign).
        """
        cluster_map: dict[str, str] = {}
        if not opt_ids:
            return cluster_map
        from app.models import Optimization
        try:
            async with session_factory() as db:
                for oid in opt_ids:
                    opt = await db.get(Optimization, oid)
                    if opt is None:
                        continue
                    cs = dict(opt.context_sources or {})
                    cs["source"] = "probe"
                    cs["probe_topic"] = probe_topic
                    cs["probe_intent_hint"] = probe_intent_hint
                    opt.context_sources = cs
                    if opt.cluster_id:
                        cluster_map[oid] = opt.cluster_id
                await db.commit()
        except Exception:
            logger.warning("probe row tagging failed", exc_info=True)
        return cluster_map

    async def _resolve_effective_domain(
        self,
        domain_raw: str,
        confidence: float,
        prompt: str,
    ) -> str:
        """Canonicalize the raw heuristic domain to a registered label.

        Mirrors pipeline_phases.py:376-411 (the canonical post-analyze
        domain reconciliation): runs ``_normalize_llm_domain`` BEFORE
        ``DomainResolver.resolve()`` so hyphen-style sub-domain strings
        (``"backend-observability"``) get rewritten to canonical colon
        syntax (``"backend: observability"``) when the prefix is a
        registered primary. Without this step, the resolver receives an
        unrecognized pseudo-primary and falls through to ``general`` --
        the v0.4.5 SEV-MAJOR class regression that the canonical
        sequencing fix was added to prevent.

        Falls through to ``domain_raw`` on any error so a missing
        DomainResolver singleton (cold-start tests) doesn't crash the
        probe -- ``assign_cluster()`` will create a new domain node if
        no match is found, which is acceptable degraded behaviour.
        """
        try:
            from app.services.domain_resolver import get_domain_resolver
            from app.services.pipeline_phases import _normalize_llm_domain
            resolver = get_domain_resolver()
            # Step 1: hyphen → colon normalization against the live registry.
            normalized = _normalize_llm_domain(
                domain_raw, set(resolver.domain_labels),
            )
            # Step 2: canonicalize to the primary label.
            return await resolver.resolve(normalized, confidence, prompt)
        except Exception:
            logger.debug(
                "probe domain_resolver unavailable -- using raw domain '%s'",
                domain_raw,
            )
            return domain_raw

    async def _persist_and_assign(
        self,
        *,
        prompt: str,
        task_type: str,
        domain: str,
        domain_raw: str | None = None,
        intent_label: str,
        scores: DimensionScores,
        overall: float,
        ctx: ProbeContext,
        duration_ms: int | None = None,
    ) -> tuple[str, str | None, str | None]:
        """Persist Optimization row + assign cluster + emit events.

        Returns ``(optimization_id, cluster_id, cluster_label)``. The
        ``optimization_id`` is the real PK of the persisted row.

        Tier 1 design notes:
          * No optimize-phase LLM call -- ``optimized_prompt`` is NULL,
            ``scoring_mode='probe-tier1'`` distinguishes from regular
            optimizations + batch_seed in downstream analytics.
          * ``status='completed'`` so the row passes ``/api/history``
            filters.
          * ``OptimizationPattern(relationship='source')`` lets pattern
            extraction + topology view find this row.
          * Cluster ``pattern_stale=True`` defers pattern extraction to
            the warm path (matches batch-seed semantics).
          * ``optimization_created`` event fires per prompt;
            ``taxonomy_changed`` fires once at probe completion.

        Concurrency: uses ``self.session_factory`` for a fresh per-call
        session when available (parallel-safe). Falls back to a lock
        around ``self.db`` for tests that don't supply a factory.
        """
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy import get_engine
        from app.services.taxonomy.cluster_meta import write_meta
        from app.services.taxonomy.family_ops import assign_cluster

        # Embedding (cheap -- MiniLM-L6-v2 ~5ms for one prompt).
        emb_service = (
            self.embedding_service
            if getattr(self, "embedding_service", None) is not None
            else EmbeddingService()
        )
        embedding_np = await emb_service.aembed_single(prompt)
        embedding_bytes = embedding_np.astype(np.float32).tobytes()

        # Multi-embedding parity with batch_pipeline so the persisted row
        # participates in fusion-based clustering + few-shot retrieval.
        # Tier 1 has no optimize-phase output -- mirror raw into
        # optimized_embedding (same vector) and skip transformation.
        # qualifier_embedding is derived from the intent_label (matches
        # the regular pipeline's behaviour on bare-intent rows).
        opt_embedding_bytes = embedding_bytes
        try:
            qual_vec = await emb_service.aembed_single(intent_label)
            qual_embedding_bytes = qual_vec.astype(np.float32).tobytes()
        except Exception:
            qual_embedding_bytes = None

        opt_id = str(uuid4())
        trace_id = str(uuid4())

        engine = None
        try:
            engine = get_engine()
        except Exception:
            engine = None
        emb_index = getattr(engine, "_embedding_index", None) if engine else None

        # Real heuristic baseline scores (the "deterministic anchor" the
        # regular pipeline uses for delta + improvement_score derivation).
        # Tier 1: original_scores == score_* columns since no optimize
        # phase mutates them. score_deltas == zero across the board.
        scores_dict = scores.model_dump()
        zero_deltas = {k: 0.0 for k in scores_dict}

        async def _do_persist(db: AsyncSession) -> tuple[str | None, str | None]:
            opt = Optimization(
                id=opt_id,
                trace_id=trace_id,
                raw_prompt=prompt,
                optimized_prompt=None,  # Tier 1: no optimize-phase LLM call
                task_type=task_type,
                strategy_used=None,
                score_clarity=scores.clarity,
                score_specificity=scores.specificity,
                score_structure=scores.structure,
                score_faithfulness=scores.faithfulness,
                score_conciseness=scores.conciseness,
                overall_score=overall,
                scoring_mode="probe-tier1",
                intent_label=intent_label,
                domain=domain,
                # ``domain_raw`` carries the raw heuristic output so the
                # warm path can re-derive sub-domain qualifiers without
                # losing the original signal.  Mirrors batch_pipeline:599.
                domain_raw=domain_raw or domain,
                embedding=embedding_bytes,
                optimized_embedding=opt_embedding_bytes,
                transformation_embedding=None,  # no diff in Tier 1
                qualifier_embedding=qual_embedding_bytes,
                # Heuristic-only baseline + original = score_*; deltas zero.
                heuristic_baseline_scores=scores_dict,
                original_scores=scores_dict,
                score_deltas=zero_deltas,
                improvement_score=0.0,
                repo_full_name=ctx.repo_full_name,
                project_id=ctx.project_id,
                status="completed",
                routing_tier="internal",
                provider=(
                    self.provider.name
                    if isinstance(getattr(self.provider, "name", None), str)
                    else None
                ),
                duration_ms=duration_ms,
                models_by_phase={"probe": "heuristic-only"},
                context_sources={
                    "source": "probe",
                    "probe_topic": ctx.topic,
                    "probe_intent_hint": ctx.intent_hint,
                },
            )
            db.add(opt)
            cluster = await assign_cluster(
                db=db,
                embedding=embedding_np,
                label=intent_label,
                domain=domain,
                task_type=task_type,
                overall_score=overall,
                embedding_index=emb_index,
                project_id=ctx.project_id,
            )
            opt.cluster_id = cluster.id
            db.add(OptimizationPattern(
                optimization_id=opt_id,
                cluster_id=cluster.id,
                relationship="source",
            ))
            cluster.cluster_metadata = write_meta(
                cluster.cluster_metadata, pattern_stale=True,
            )
            await db.commit()
            return cluster.id, (cluster.label or None)

        # Serialize persist+assign across concurrent prompts on the
        # orchestrator's own session. Why not session_factory? SQLite
        # is single-writer and the warm-path engine + cross-process MCP
        # server hold writers periodically; a fresh session via
        # session_factory grabs a *different* pool connection that
        # competes for the lock against ``self.db``'s connection plus
        # those background writers, tripping ``database is locked``
        # even with busy_timeout=30s. Reusing ``self.db`` collapses the
        # contention to a single pool connection. The ProbeRun row is
        # committed at the top of ``_run_impl`` so this session has no
        # in-flight transaction the writer would conflict with. Cost
        # is trivial (~50ms × N prompts) vs the LLM-bound phases.
        async with self._persist_lock:
            cluster_id, cluster_label = await _do_persist(self.db)

        # Emit events so frontend history refreshes per prompt.
        try:
            event_bus.publish("optimization_created", {
                "id": opt_id,
                "trace_id": trace_id,
                "task_type": task_type,
                "intent_label": intent_label,
                "domain": domain,
                "strategy_used": None,
                "overall_score": overall,
                "provider": None,
                "status": "completed",
                "routing_tier": "internal",
                "source": "probe",
            })
        except Exception:
            logger.debug("optimization_created publish failed", exc_info=True)

        return (opt_id, cluster_id, cluster_label)
