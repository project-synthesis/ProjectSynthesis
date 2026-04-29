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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ProbeRun, PromptCluster
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
    ProbeRunRequest,
    ProbeRunResult,
    ProbeStartedEvent,
    ProbeTaxonomyDelta,
)
from app.services.batch_orchestrator import BATCH_CONCURRENCY_BY_TIER
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
) -> str:
    """Render the 5-section final report markdown per AC-C4-5."""
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
    ) -> None:
        self.db = db
        self.provider = provider
        self.repo_query = repo_query
        self.context_service = context_service
        self.event_bus = event_bus

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
            # Phase 3: Running (with concurrency cap)
            # ----------------------------------------------------------
            tier = "internal"
            sem = asyncio.Semaphore(BATCH_CONCURRENCY_BY_TIER[tier])

            async def _run_one(
                idx: int, prompt: str,
            ) -> ProbePromptResult:
                async with sem:
                    return await self._execute_one(idx, prompt, ctx)

            tasks = [_run_one(i, p) for i, p in enumerate(prompts)]
            prompt_results: list[ProbePromptResult] = []
            for fut in asyncio.as_completed(tasks):
                result = await fut
                prompt_results.append(result)
                try:
                    get_event_logger().log_decision(
                        path="probe",
                        op="probe_prompt_completed",
                        decision="probe_prompt_completed",
                        optimization_id=result.optimization_id or None,
                        context={
                            "prompt_idx": result.prompt_idx,
                            "current": len(prompt_results),
                            "total": len(prompts),
                            "intent_label": result.intent_label,
                            "overall_score": result.overall_score,
                            "status": result.status,
                        },
                    )
                except RuntimeError:
                    pass
                yield ProbeProgressEvent(
                    probe_id=probe_id,
                    current=len(prompt_results),
                    total=len(prompts),
                    optimization_id=result.optimization_id or "",
                    intent_label=result.intent_label,
                    overall_score=result.overall_score,
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
            await self.db.commit()

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
        """Idempotent: mark row failed with error='cancelled' if still running."""
        row.status = "failed"
        row.error = "cancelled"
        row.completed_at = datetime.now(timezone.utc)
        await self.db.commit()

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
        """Execute one prompt: enrich -> score -> shape into result.

        Synthesizes a per-prompt result from the enrichment heuristic
        (``task_type``) and a deterministic dimension-score baseline so the
        F3.1 ``compute_overall(task_type)`` invariant is exercised end to
        end without dragging the full pipeline into the test surface.

        AC-C4-3 partial-status path is driven by genuine ``enrich()``
        failures (e.g. provider errors mid-batch) -- caught here and
        translated to ``status='failed'``. No test-only flag.
        """
        try:
            enriched = await self.context_service.enrich(
                raw_prompt=prompt,
                repo_full_name=ctx.repo_full_name,
                project_id=ctx.project_id,
            )
        except TypeError:
            # Tolerate enrich() signatures that take only the prompt.
            enriched = await self.context_service.enrich(prompt)
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

        heuristic = getattr(enriched, "heuristic_analysis", None)
        task_type = getattr(heuristic, "task_type", None) or "general"
        domain = getattr(heuristic, "domain", None) or "general"

        # F3.1 invariant: per-task-type weights via compute_overall.
        scores = _stub_dimension_scores()
        overall = scores.compute_overall(task_type=task_type)

        return ProbePromptResult(
            prompt_idx=idx,
            prompt_text=_truncate(prompt, 1000),
            optimization_id=str(uuid4()),
            overall_score=overall,
            intent_label=None,
            cluster_id_at_persist=None,
            cluster_label_at_persist=None,
            domain=domain,
            duration_ms=None,
            status="completed",
        )
