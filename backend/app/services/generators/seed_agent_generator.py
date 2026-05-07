"""SeedAgentGenerator — refactored from SeedOrchestrator + tools/seed.py orchestration.

Wraps the existing seed flow: ``SeedOrchestrator.generate()`` → ``run_batch()`` →
``bulk_persist()`` → ``batch_taxonomy_assign()``. Publishes ``seed_batch_progress``
to ``event_bus`` with ``run_id`` (threaded via ``current_run_id`` ContextVar);
emits ``seed_started`` / ``seed_explore_complete`` / ``seed_completed`` /
``seed_failed`` taxonomy decision events with ``run_id`` in the ``context`` dict.

Translation contract from ``tools/seed.py:handle_seed`` (lines ~25-407):
-   ``payload.get('provider')`` → already extracted as local var
-   ``self._write_queue`` → injected via ``__init__``
-   Transient WriteQueue construction in handle_seed lines 285-306 REMOVED
    (always receives real WriteQueue from RunOrchestrator).
-   Taxonomy decision events fire via ``_log_decision`` helper threading
    ``run_id`` into ``context`` dict (Channel 2 per spec § 6.4).
-   EARLY-FAILURE path returns ``GeneratorResult(terminal_status='failed', ...)``
    rather than raising (preserves today's HTTP 200 with ``status='failed'``).

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md
       § 5.5 + § 6.4
Plan:  docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md
       Cycle 7
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult

logger = logging.getLogger(__name__)


class SeedAgentGenerator:
    """Seed agent execution generator — conforms to RunGenerator protocol.

    Internal flow (mirrors today's ``handle_seed`` body):
      1. Validate inputs → EARLY-FAILURE returns ``terminal_status='failed'``
      2. Generate prompts via ``SeedOrchestrator.generate()`` (or skip if
         ``payload['prompts']`` is supplied)
      3. ``run_batch()`` parallel optimization with N concurrency
      4. ``bulk_persist()`` quality-gated DB write via the injected WriteQueue
      5. ``batch_taxonomy_assign()`` cluster assignment (non-fatal on failure)
      6. Classify ``terminal_status``: completed | partial | failed
      7. Return ``GeneratorResult`` — RunOrchestrator owns RunRow writes.
    """

    def __init__(
        self,
        seed_orchestrator: Any,
        write_queue: Any,
    ) -> None:
        """Construct the generator.

        Args:
            seed_orchestrator: ``SeedOrchestrator`` instance providing
                ``async generate()``. May be ``None`` in the EARLY-FAILURE
                path (no project_description + no prompts + no provider).
            write_queue: ``WriteQueue`` injected by the RunOrchestrator.
                Routes ``bulk_persist`` + ``batch_taxonomy_assign`` writes
                through the canonical writer engine.
        """
        self._seed_orchestrator = seed_orchestrator
        self._write_queue = write_queue

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        """Execute the seed flow. Publish progress + decision events.

        Returns ``GeneratorResult`` with classified ``terminal_status``:
          - ``completed``: all prompts succeeded
          - ``partial``: 1+ succeeded AND 1+ failed (mirrors handle_seed:362-364)
          - ``failed``: 0 succeeded (gen failure, batch failure, all-failed)
        """
        payload = request.payload
        project_description = payload.get("project_description")
        repo_full_name = payload.get("repo_full_name")
        prompt_count = payload.get("prompt_count", 30)
        agents = payload.get("agents")
        prompts = payload.get("prompts")
        provider = payload.get("provider")
        context_service = payload.get("context_service")
        codebase_context = payload.get("codebase_context")
        tier = payload.get("tier", "passthrough")

        batch_id = payload.get("batch_id") or str(uuid.uuid4())
        t0 = time.monotonic()

        # ---- Decision event: seed_started (handle_seed:94-109) ----
        self._log_decision("seed_started", run_id, {
            "batch_id": batch_id,
            "tier": tier,
            "project_description": (project_description or "")[:200],
            "prompt_count_target": prompt_count if not prompts else len(prompts),
            "has_user_prompts": prompts is not None,
        })

        # ---- EARLY-FAILURE PATH (handle_seed:193-204) ----
        # Preserves today's HTTP 200 with status='failed' semantics: we
        # return a GeneratorResult rather than raising. Spec § 5.5.
        if not prompts and (not project_description or not provider):
            summary = (
                "Requires project_description with a provider, "
                "or user-provided prompts."
            )
            self._log_decision("seed_failed", run_id, {
                "batch_id": batch_id,
                "phase": "input_validation",
                "summary": summary,
            })
            return GeneratorResult(
                terminal_status="failed",
                prompts_generated=0,
                prompt_results=[],
                aggregate={
                    "prompts_optimized": 0,
                    "prompts_failed": 0,
                    "summary": summary,
                },
                taxonomy_delta={"domains_touched": [], "clusters_created": 0},
                final_report=None,
            )

        # ---- Phase 2: Generate prompts (handle_seed:115-191) ----
        if prompts:
            generated_prompts = list(prompts)
        else:
            try:
                gen_result = await self._seed_orchestrator.generate(
                    project_description=project_description,
                    batch_id=batch_id,
                    workspace_profile=None,  # explore TODO; reuse existing logic
                    codebase_context=codebase_context,
                    agent_names=agents,
                    prompt_count=prompt_count,
                )
                generated_prompts = list(gen_result.prompts)
            except Exception as exc:
                logger.error("Seed generation failed: %s", exc, exc_info=True)
                self._log_decision("seed_failed", run_id, {
                    "batch_id": batch_id,
                    "phase": "generate",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:200],
                    "prompts_completed_before_failure": 0,
                })
                return GeneratorResult(
                    terminal_status="failed",
                    prompts_generated=0,
                    prompt_results=[],
                    aggregate={
                        "prompts_optimized": 0,
                        "prompts_failed": 0,
                        "summary": f"Generation failed: {exc}",
                    },
                    taxonomy_delta={"domains_touched": [], "clusters_created": 0},
                    final_report=None,
                )

        self._log_decision("seed_explore_complete", run_id, {
            "batch_id": batch_id,
            "prompts_count": len(generated_prompts),
        })

        # ---- Phase 3: Run batch + persist + taxonomy assign ----
        # Mirror handle_seed:206-360. Function-local imports so the test
        # harness can monkey-patch at the module level.
        from app.config import PROMPTS_DIR
        from app.database import async_session_factory
        from app.services.batch_pipeline import (
            batch_taxonomy_assign,
            bulk_persist,
            run_batch,
        )
        from app.services.embedding_service import EmbeddingService
        from app.services.prompt_loader import PromptLoader

        # Resolve service singletons for enrichment parity (handle_seed:222-236)
        _taxonomy_engine = None
        try:
            from app.services.taxonomy import get_engine
            _taxonomy_engine = get_engine()
        except Exception:
            logger.debug("Taxonomy engine unavailable for seed enrichment")

        _domain_resolver = None
        try:
            from app.services.domain_resolver import get_domain_resolver
            _domain_resolver = get_domain_resolver()
        except Exception:
            logger.debug("Domain resolver unavailable for seed enrichment")

        max_parallel = self._compute_max_parallel(tier=tier, provider=provider)

        try:
            results = await run_batch(
                prompts=generated_prompts,
                provider=provider,  # type: ignore[arg-type]
                prompt_loader=PromptLoader(PROMPTS_DIR),
                embedding_service=EmbeddingService(),
                max_parallel=max_parallel,
                codebase_context=codebase_context if not prompts else None,
                repo_full_name=repo_full_name,
                batch_id=batch_id,
                session_factory=async_session_factory,
                taxonomy_engine=_taxonomy_engine,
                domain_resolver=_domain_resolver,
                tier=tier,
                context_service=context_service,
            )
        except Exception as exc:
            logger.error("Seed batch execution failed: %s", exc, exc_info=True)
            self._log_decision("seed_failed", run_id, {
                "batch_id": batch_id,
                "phase": "optimize",
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:200],
                "prompts_completed_before_failure": 0,
            })
            return GeneratorResult(
                terminal_status="failed",
                prompts_generated=len(generated_prompts),
                prompt_results=[],
                aggregate={
                    "prompts_optimized": 0,
                    "prompts_failed": len(generated_prompts),
                    "summary": f"Batch execution failed: {exc}",
                },
                taxonomy_delta={"domains_touched": [], "clusters_created": 0},
                final_report=None,
            )

        # ---- Bulk persist (handle_seed:308-337) ----
        # The transient WriteQueue construction (handle_seed:285-306) is
        # REMOVED here — generator always receives a real WriteQueue from
        # RunOrchestrator (DI in lifespan).
        try:
            await bulk_persist(results, self._write_queue, batch_id)
        except Exception as exc:
            logger.error("Seed persist failed: %s", exc, exc_info=True)
            completed = sum(1 for r in results if r.status == "completed")
            self._log_decision("seed_failed", run_id, {
                "batch_id": batch_id,
                "phase": "persist",
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:200],
                "prompts_completed_before_failure": completed,
            })
            return GeneratorResult(
                terminal_status="partial",  # some succeeded but persist crashed
                prompts_generated=len(generated_prompts),
                prompt_results=_serialize_results(results),
                aggregate={
                    "prompts_optimized": completed,
                    "prompts_failed": len(results) - completed,
                    "summary": (
                        f"Optimized {completed} prompts but persist failed: "
                        f"{exc}"
                    ),
                },
                taxonomy_delta={"domains_touched": [], "clusters_created": 0},
                final_report=None,
            )

        # ---- Taxonomy integration (handle_seed:339-357) ----
        try:
            taxonomy_result = await batch_taxonomy_assign(
                results, self._write_queue, batch_id,
            )
        except Exception as exc:
            logger.warning(
                "Taxonomy integration failed (non-fatal): %s", exc,
            )
            taxonomy_result = {
                "clusters_assigned": 0,
                "clusters_created": 0,
                "domains_touched": [],
            }

        # ---- Final classification (handle_seed:359-393) ----
        completed = sum(1 for r in results if r.status == "completed")
        failed = sum(1 for r in results if r.status == "failed")
        duration_ms = int((time.monotonic() - t0) * 1000)

        if completed > 0 and failed == 0:
            terminal: str = "completed"
        elif completed == 0:
            terminal = "failed"
        else:
            terminal = "partial"

        clusters_created = taxonomy_result.get("clusters_created", 0)
        domains_touched = taxonomy_result.get("domains_touched", [])
        summary = (
            f"{completed} prompts optimized"
            f"{f', {failed} failed' if failed else ''}"
            f". {clusters_created} clusters created"
            f", domains: {', '.join(domains_touched)}"
        )

        # Decision event: seed_completed or seed_failed
        decision_name = "seed_completed" if terminal != "failed" else "seed_failed"
        self._log_decision(decision_name, run_id, {
            "batch_id": batch_id,
            "terminal_status": terminal,
            "prompts_optimized": completed,
            "prompts_failed": failed,
            "clusters_created": clusters_created,
            "domains_touched": domains_touched,
            "total_duration_ms": duration_ms,
            "tier": tier,
        })

        return GeneratorResult(
            terminal_status=terminal,  # type: ignore[arg-type]
            prompts_generated=len(generated_prompts),
            prompt_results=_serialize_results(results),
            aggregate={
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "summary": summary,
            },
            taxonomy_delta={
                "domains_touched": domains_touched,
                "clusters_created": clusters_created,
            },
            final_report=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_max_parallel(tier: str, provider: Any) -> int:
        """Mirrors handle_seed:206-213 max_parallel logic.

        - internal + claude_cli  → 10 (CLI subprocess parallelism)
        - internal + API         → 5 (rate-limit cap)
        - sampling               → 2 (MCP roundtrip cost)
        - passthrough / default  → 1
        """
        if tier == "internal" and provider is not None:
            return 10 if getattr(provider, "name", "") == "claude_cli" else 5
        if tier == "sampling":
            return 2
        return 1

    @staticmethod
    def _log_decision(decision: str, run_id: str, context: dict) -> None:
        """Emit a taxonomy decision event with ``run_id`` in context (Channel 2).

        Wraps log_decision in try/except RuntimeError so test environments
        without an initialized event_logger don't crash the generator.
        """
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="hot",
                op="seed",
                decision=decision,
                context={**context, "run_id": run_id},
            )
        except RuntimeError:
            pass


def _serialize_results(results: list) -> list[dict]:
    """Serialize PendingOptimization-list to list[dict] for GeneratorResult.

    Uses ``__dict__`` mirroring the plan template. Filters out non-serializable
    attributes lazily — embedding bytes are dropped so the generator result
    stays JSON-friendly for downstream RunRow.prompt_results writes.
    """
    out: list[dict] = []
    for r in results:
        try:
            d = dict(r.__dict__)
        except Exception:
            continue
        # Drop non-JSON-friendly bytes columns
        for k in ("embedding", "optimized_embedding", "transformation_embedding"):
            if k in d:
                d[k] = None
        out.append(d)
    return out


__all__ = ["SeedAgentGenerator"]
