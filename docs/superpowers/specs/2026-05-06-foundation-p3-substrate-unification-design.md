# Foundation P3 — Substrate Unification (v0.4.18) — Design

**Status:** Brainstorm complete (2026-05-06). Spec authoring complete; implementation plan next.
**Target release:** v0.4.18
**Predecessor phases:** Foundation P1 (v0.4.16, SHIPPED), Foundation P2 Path A (v0.4.17, SHIPPED)
**Successor phases:** Foundation P4 (v0.4.19), Probe Tier 2/3/4 (v0.4.20-22)
**Linked ROADMAP entry:** [Foundation P3 — Substrate unification](../../ROADMAP.md#foundation-p3--substrate-unification-target-v0418)

---

## 1. Context and motivation

Topic Probe Tier 1 (v0.4.12) introduced a `ProbeRun` model and a 5-phase `ProbeService` orchestrator. Batch seeding shipped earlier with no run-state model. Tiers 2-4 of Topic Probe (save-as-suite, replay, probe→seed promotion, unified UI) need both modes to share storage. Foundation P3 unifies the substrate now so T2-T4 ship natively without retroactive migration debt.

### 1.1 Verified asymmetry (against `main` @ v0.4.18-dev, 2026-05-06)

| Layer | Probe | Seed |
|---|---|---|
| Persistence | `ProbeRun` — 17 columns (`id, topic, scope, intent_hint, repo_full_name, project_id, commit_sha, started_at, completed_at, prompts_generated, prompt_results, aggregate, taxonomy_delta, final_report, status, suite_id, error`) at `models.py:570`, migration `ec86c86ba298` | **none** — no `SeedRun` model exists; never has. Only persisted artifact of a seed run is the resulting `Optimization` rows tagged `source="batch_seed"` |
| REST | `POST /api/probes` (SSE), `GET /api/probes` (paginated), `GET /api/probes/{id}` | `POST /api/seed` (sync fire-and-forget) — no list, no GET-by-id |
| MCP tool | `synthesis_probe` returns `probe_id`, supports SSE under sampling | `synthesis_seed` synchronous; `batch_id` is in-memory UUID, not persisted |
| Lifecycle | Full — status tracking, `_gc_orphan_probe_runs` startup sweep, cancellation handler under `asyncio.shield()`, `_set_probe_status` queued helper | None — `seed_batch_progress` events publish to `event_bus` and are lost on disconnect |
| Frontend | Zero code — no `probe.ts` API client, no `Probe*` components | `SeedModal.svelte` renders live progress + final summary; no history component |

P3 is therefore not "collapse two existing models" — it is **introduce row-state to the seed surface and reshape probe row-state into a generic `RunRow` substrate at the same time, without regressing any existing surface contract.**

### 1.2 Why this matters for downstream tiers

1. **T2 save-as-suite + replay** keys off `RunRow.id`. Without P3, seed has no row-state to attach a save-as-suite operation to.
2. **T3 probe→seed-agent promotion** becomes a `RunRow.mode` flip plus a metadata write. Without P3, promotion has nothing on the seed side to read from.
3. **T4 final UI consolidation** ships natively with one history surface built once on top of `RunRow`.
4. **Lifecycle parity** — `RunOrchestrator` owns the persistence helpers once and both modes inherit identical status/error/GC behavior, eliminating the helper-drift class of bugs that exists today only because seed has no row.

---

## 2. Decisions (from 2026-05-06 brainstorm)

| # | Question | Decision | Rationale |
|---|---|---|---|
| Q1 | Asymmetry handling | **A — Asymmetric collapse, one-step.** | Same end state as alternatives, materially simpler migration since seed has no rows to backfill. No transient `SeedRun` model. |
| Q2 | `RunRow` column shape | **c — Hybrid.** Shared lifecycle + promoted `topic`/`intent_hint` first-class; mode-specific fields in JSON metadata. | Query-hot fields (`topic` for T2 save-as-suite, `intent_hint` for filtering) graduate to columns; bounded mode-specific bag stays as JSON. |
| Q3 | `POST /api/seed` semantics | **Path 1 — sync POST + global SSE bus.** Sync response gains additive `run_id`; live updates flow through `/api/events` filtered by `run_id`. | Existing event bus already powers other live UI; POST stays kickoff-shaped; backward compat with all sync callers preserved. |
| Q4 | `probe_run` table fate | **a — Drop immediately** in the same Alembic migration. No SQL VIEW. | All readers are ORM-level and refactored anyway; Alembic downgrade gives rollback safety; no documented external SQL consumer. |
| Q5 | Generator protocol | **b — Awaitable generators + bus events.** `async def run(req, *, run_id) -> RunResult`; events publish directly to bus. | Avoids redundant re-publication layer; retires vestigial `AsyncIterator` shape; cleaner protocol; narrower test surface. |
| Q6 | Rollout | **Two PRs + backend-only.** PR1 = dark substrate; PR2 = wire shims atomically. Frontend = one additive line in SeedModal. | Reviewable in halves, deployable atomically; T4 (v0.4.22) builds the unified history UI. |

---

## 3. Architecture

### 3.1 Component overview

```
                    ┌─────────────────────────────────────────────┐
                    │  RunOrchestrator (NEW)                       │
                    │  - creates RunRow, sets status='running'     │
                    │  - dispatches to generator by mode           │
                    │  - awaits result, persists final state       │
                    │  - handles CancelledError + Exception        │
                    │  - all writes route through WriteQueue       │
                    └────────────────────┬────────────────────────┘
                                         │
                          ┌──────────────┴──────────────┐
                          ▼                             ▼
              ┌──────────────────────┐    ┌──────────────────────────┐
              │ TopicProbeGenerator  │    │  SeedAgentGenerator      │
              │ (refactored from     │    │  (refactored from        │
              │  ProbeService)       │    │   SeedOrchestrator +     │
              │  publishes probe_*   │    │   tools/seed.py          │
              │  events to bus       │    │   orchestration)         │
              │                      │    │  publishes seed_*        │
              │                      │    │  events to bus           │
              └──────────┬───────────┘    └────────────┬─────────────┘
                         │                             │
                         └──────────┬──────────────────┘
                                    ▼
                            ┌─────────────────┐
                            │  event_bus      │── /api/events SSE
                            │  (existing)     │   (run_id-filterable)
                            └─────────────────┘

                    ┌─────────────────────────────────────────────┐
                    │  RunRow (NEW table — replaces probe_run)    │
                    │  mode discriminator + hybrid columns        │
                    └─────────────────────────────────────────────┘
```

**Key invariant:** every `RunRow` write goes through `WriteQueue.submit()`, preserving the v0.4.13 single-writer contract and making `RunOrchestrator` the only legitimate writer.

### 3.2 Layer rules (existing, unchanged)

`routers/` → `services/` → `models/` only. Services never import from routers. `RunOrchestrator` is a service; routers depend on it. Generators are services owned by `RunOrchestrator`'s dispatch table.

---

## 4. Data model

### 4.1 `RunRow` table

```python
class RunRow(Base):
    __tablename__ = "run_row"

    # Identity / discriminator
    id: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    # Values: 'topic_probe' | 'seed_agent'
    # Future modes (e.g., 'scheduled_probe', 'replay_run') extend this enum

    # Shared lifecycle (mirrors ProbeRun's lifecycle columns byte-for-byte)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Shared correlation
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("prompt_cluster.id"), nullable=True,
    )
    repo_full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Nullable because seed-mode user-provided-prompts flow allows no repo

    # Promoted from probe-mode (Q2 hybrid — query-hot)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    intent_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    # Both NULL when mode='seed_agent'

    # Shared output payloads (probe and seed both produce these)
    prompts_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_results: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    aggregate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    taxonomy_delta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    # final_report is NULL for seed_agent mode at v0.4.18; T4 may add a seed final-report

    # Suite linkage (T2 readiness — provisioned in P3, used in T2)
    suite_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Mode-specific JSON metadata
    topic_probe_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Shape: {scope: str, commit_sha: str | None}
    seed_agent_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Shape: {project_description: str, workspace_path: str | None,
    #         agents: list[str] | None, prompt_count: int, prompts_provided: bool,
    #         batch_id: str, tier: str, estimated_cost_usd: float | None}

    __table_args__ = (
        Index("ix_run_row_mode_started", "mode", "started_at"),
        Index("ix_run_row_status_started", "status", "started_at"),
        Index("ix_run_row_project_id", "project_id"),
        Index("ix_run_row_topic", "topic"),  # T2 save-as-suite query path
    )
```

**Column derivation note:** every `ProbeRun` column maps to a `RunRow` column or to a `topic_probe_meta` JSON key. `scope` and `commit_sha` are the only fields that move from probe-side first-class to JSON metadata (low query frequency; not query-hot per Q2 analysis).

### 4.2 Alembic migration (idempotent, single migration)

```python
revision = "<new_revision_id>"
down_revision = "ec86c86ba298"  # add probe_run

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "run_row" in inspector.get_table_names():
        return  # Idempotent: already migrated

    # 1. Create run_row table
    op.create_table(
        "run_row",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("repo_full_name", sa.String(), nullable=True),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("intent_hint", sa.String(), nullable=True),
        sa.Column("prompts_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_results", sa.JSON(), nullable=True),
        sa.Column("aggregate", sa.JSON(), nullable=True),
        sa.Column("taxonomy_delta", sa.JSON(), nullable=True),
        sa.Column("final_report", sa.Text(), nullable=True),
        sa.Column("suite_id", sa.String(), nullable=True),
        sa.Column("topic_probe_meta", sa.JSON(), nullable=True),
        sa.Column("seed_agent_meta", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["prompt_cluster.id"],
            name="fk_run_row_project_id_prompt_cluster",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_row_mode_started", "run_row", ["mode", "started_at"])
    op.create_index("ix_run_row_status_started", "run_row", ["status", "started_at"])
    op.create_index("ix_run_row_project_id", "run_row", ["project_id"])
    op.create_index("ix_run_row_topic", "run_row", ["topic"])

    # 2. Backfill from probe_run
    if "probe_run" in inspector.get_table_names():
        op.execute("""
            INSERT INTO run_row (
                id, mode, status, started_at, completed_at, error,
                project_id, repo_full_name, topic, intent_hint,
                prompts_generated, prompt_results, aggregate, taxonomy_delta,
                final_report, suite_id, topic_probe_meta, seed_agent_meta
            )
            SELECT
                id, 'topic_probe', status, started_at, completed_at, error,
                project_id, repo_full_name, topic, intent_hint,
                prompts_generated, prompt_results, aggregate, taxonomy_delta,
                final_report, suite_id,
                json_object('scope', scope, 'commit_sha', commit_sha) AS topic_probe_meta,
                NULL AS seed_agent_meta
            FROM probe_run
        """)

        # 3. Drop probe_run indexes + table (decision Q4=a — no VIEW)
        op.drop_index("ix_probe_run_project_id", table_name="probe_run")
        op.drop_index("ix_probe_run_status_started", table_name="probe_run")
        op.drop_table("probe_run")

def downgrade() -> None:
    # Recreate probe_run table (mirror of ec86c86ba298 schema)
    op.create_table(
        "probe_run",
        # ... same columns as ec86c86ba298 ...
    )
    op.create_index("ix_probe_run_status_started", "probe_run", ["status", "started_at"])
    op.create_index("ix_probe_run_project_id", "probe_run", ["project_id"])

    # Reverse-backfill from run_row, extracting scope/commit_sha from JSON
    op.execute("""
        INSERT INTO probe_run (
            id, topic, scope, intent_hint, repo_full_name, project_id,
            commit_sha, started_at, completed_at, prompts_generated,
            prompt_results, aggregate, taxonomy_delta, final_report,
            status, suite_id, error
        )
        SELECT
            id, topic,
            COALESCE(json_extract(topic_probe_meta, '$.scope'), '**/*') AS scope,
            intent_hint, repo_full_name, project_id,
            json_extract(topic_probe_meta, '$.commit_sha') AS commit_sha,
            started_at, completed_at, prompts_generated,
            prompt_results, aggregate, taxonomy_delta, final_report,
            status, suite_id, error
        FROM run_row
        WHERE mode = 'topic_probe'
    """)

    op.drop_index("ix_run_row_topic", table_name="run_row")
    op.drop_index("ix_run_row_project_id", table_name="run_row")
    op.drop_index("ix_run_row_status_started", table_name="run_row")
    op.drop_index("ix_run_row_mode_started", table_name="run_row")
    op.drop_table("run_row")
```

**Backfill safety:** the `INSERT ... SELECT` is atomic at the SQL level; either all probe_run rows make it to run_row or none do. SQLite's `json_object` function works on all supported versions.

---

## 5. Service layer

### 5.1 `RunGenerator` protocol

```python
# backend/app/services/generators/base.py
from typing import Protocol, runtime_checkable

@runtime_checkable
class RunGenerator(Protocol):
    """Contract for mode-specific run execution under RunOrchestrator dispatch.

    Generators are awaitable services that:
      - execute the mode-specific run logic
      - publish progress events directly to event_bus, including run_id in payload
      - return a final RunResult dataclass

    Generators MUST NOT touch RunRow directly — RunOrchestrator owns row creation,
    status writes, and error capture. This is enforced by the WriteQueue audit hook.
    """

    async def run(self, request: RunRequest, *, run_id: str) -> RunResult:
        """Execute the run and return final result. Publish events to bus
        with run_id threaded into payload."""
        ...

@dataclass(frozen=True)
class RunRequest:
    """Mode-agnostic input shape — concrete generators cast to mode-specific request type."""
    mode: Literal["topic_probe", "seed_agent"]
    payload: dict  # Mode-specific input fields

@dataclass(frozen=True)
class RunResult:
    """Final output written to RunRow at run completion."""
    prompts_generated: int
    prompt_results: list[dict]
    aggregate: dict
    taxonomy_delta: dict
    final_report: str | None  # None for seed_agent at v0.4.18
```

### 5.2 `RunOrchestrator`

```python
# backend/app/services/run_orchestrator.py
class RunOrchestrator:
    def __init__(
        self,
        write_queue: WriteQueue,
        generators: dict[str, RunGenerator],
        # {'topic_probe': TopicProbeGenerator(...), 'seed_agent': SeedAgentGenerator(...)}
    ):
        self._write_queue = write_queue
        self._generators = generators

    async def run(self, mode: str, request: RunRequest) -> RunRow:
        """Top-level dispatch. Creates row → runs generator → persists result."""
        run_row = await self._create_row(mode, request)

        if mode not in self._generators:
            await self._mark_failed(run_row.id, error=f"unknown_mode: {mode}")
            raise ValueError(f"unknown mode: {mode}")

        generator = self._generators[mode]

        # Set the ContextVar so taxonomy events fired during run get correlated
        token = current_run_id.set(run_row.id)
        try:
            try:
                result = await generator.run(request, run_id=run_row.id)
                await self._persist_final(run_row.id, result)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        self._mark_failed(run_row.id, error="cancelled")
                    )
                raise
            except Exception as exc:
                await self._mark_failed(
                    run_row.id, error=f"{type(exc).__name__}: {exc}"
                )
                raise
        finally:
            current_run_id.reset(token)

        return await self._reload(run_row.id)

    async def _create_row(self, mode: str, request: RunRequest) -> RunRow:
        """Insert run_row(status='running') via WriteQueue."""
        run_id = str(uuid.uuid4())
        # Build mode-specific column values from request.payload
        # (full field-mapping logic in implementation)
        return await self._write_queue.submit(
            lambda db: self._do_create(db, run_id, mode, request),
            timeout=30,
            operation_label=f"run_orchestrator.create_row[{mode}]",
        )

    async def _set_run_status(
        self, run_id: str, status: str, **fields
    ) -> None:
        """Update run_row.status (and optionally completed_at, error, etc.)
        via WriteQueue. Replaces probe_service._set_probe_status."""
        await self._write_queue.submit(
            lambda db: self._do_update_status(db, run_id, status, fields),
            timeout=30,
            operation_label=f"run_orchestrator.set_status[{status}]",
        )

    async def _persist_final(self, run_id: str, result: RunResult) -> None:
        """Write final result fields + status='completed' via WriteQueue."""
        await self._write_queue.submit(
            lambda db: self._do_persist_final(db, run_id, result),
            timeout=60,
            operation_label="run_orchestrator.persist_final",
        )

    async def _mark_failed(self, run_id: str, *, error: str) -> None:
        """Write status='failed' + error + completed_at via WriteQueue."""
        await self._set_run_status(
            run_id, status="failed", error=error[:2000], completed_at=_utcnow()
        )

    async def _reload(self, run_id: str) -> RunRow:
        """Read row back through standard read path."""
        async with async_session_factory() as db:
            return await db.get(RunRow, run_id)
```

### 5.3 `current_run_id` ContextVar

```python
# backend/app/services/run_event_correlation.py (NEW location)
import contextvars
current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_run_id", default=None,
)

# backend/app/services/probe_event_correlation.py (existing — gains re-export)
from app.services.run_event_correlation import current_run_id

# Backward-compat alias for any consumer that imports current_probe_id by name.
# Slated for retirement in a follow-up cleanup cycle (see ROADMAP Exploring).
current_probe_id = current_run_id
```

### 5.4 `TopicProbeGenerator` (refactored from `ProbeService`)

Internal 5-phase orchestrator preserved (grounding → generating → running → observability → reporting). Mechanical changes:

- `yield ProbeStartedEvent(...)` calls become `event_bus.publish("probe_started", {**payload, "run_id": run_id})`
- `_set_probe_status` calls removed. The `RunRow.status` column only takes three values: `running` (set by `RunOrchestrator._create_row`), `completed` (set by `RunOrchestrator._persist_final`), `failed` (set by `RunOrchestrator._mark_failed`). Mid-run progress is observable purely through the existing per-phase events (`probe_grounding`, `probe_generating`, `probe_prompt_completed`, etc.) on the event bus — there is no intermediate row status. This simplifies the model AND keeps the WriteQueue audit-hook clean (only `RunOrchestrator` writes to `RunRow`). Today's `ProbeRun.status` column is already used this way (no intermediate statuses observed in the codebase); this just makes the invariant explicit.
- Returns `RunResult` (replacing the `ProbeRunResult` build at end of `_run_impl`); orchestrator persists it via `_persist_final`
- The `current_run_id` ContextVar is set by `RunOrchestrator.run` (outer wrapper) before invoking the generator — the generator inherits the correlation via async context propagation
- `_gc_orphan_probe_runs` retired in favor of `_gc_orphan_runs` (in `services/gc.py`)

The 9 module-level helpers from P2 Path A (`probe_common.py`, `probe_phases.py`, `probe_phase_5.py`) are reused as-is — they're already free functions that don't depend on the `ProbeService` class shape.

### 5.5 `SeedAgentGenerator` (refactored from `SeedOrchestrator` + `tools/seed.py`)

Existing internal flow preserved: `SeedOrchestrator.generate()` → `batch_pipeline.run_batch()` → `bulk_persist()` → `batch_taxonomy_assign()`. Generator wrapper delegates to these.

Mechanical changes:
- `event_bus.publish("seed_batch_progress", {...})` in `batch_orchestrator.py:240-242` gains `run_id` field in payload (one-line additive change)
- `seed_started`, `seed_explore_complete`, `seed_completed`, `seed_failed` decision events fired from `tools/seed.py:handle_seed` move into `SeedAgentGenerator.run`. The router/MCP shim retains only the dispatch call to `RunOrchestrator.run(mode='seed_agent', ...)`; all event emission is internal to the generator.
- `batch_id` in `seed_agent_meta` JSON preserves the existing in-memory UUID for backward compat with anything that grepped logs by `batch_id`

### 5.6 GC sweep

```python
# backend/app/services/gc.py
async def _gc_orphan_runs() -> None:
    """Sweep orphan run_row rows where status='running' for >RUN_ORPHAN_TTL_HOURS."""
    cutoff = datetime.utcnow() - timedelta(hours=RUN_ORPHAN_TTL_HOURS)
    async with async_session_factory() as db:
        # Mark as failed via WriteQueue
        await app_state.write_queue.submit(
            lambda write_db: write_db.execute(
                update(RunRow)
                .where(RunRow.status == "running")
                .where(RunRow.started_at < cutoff)
                .values(status="failed", error="orphaned (ttl exceeded)",
                        completed_at=_utcnow())
            ),
            timeout=60,
            operation_label="gc.orphan_runs",
        )

# Constant rename with backward-compat re-export
RUN_ORPHAN_TTL_HOURS = 1
PROBE_ORPHAN_TTL_HOURS = RUN_ORPHAN_TTL_HOURS  # legacy alias (retire in follow-up)
```

The startup-only `_gc_orphan_probe_runs()` is replaced by `_gc_orphan_runs()` in the lifespan startup sweep.

---

## 6. Routing layer

### 6.1 New: `routers/runs.py`

```python
# backend/app/routers/runs.py
@router.get("/api/runs", response_model=RunListResponse)
async def list_runs(
    mode: Literal["topic_probe", "seed_agent"] | None = Query(None),
    status: str | None = Query(None),
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    """Paginated list of all runs across both modes, ordered started_at desc."""

@router.get("/api/runs/{run_id}", response_model=RunResult)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> RunResult:
    """Full RunRow detail. 404 with 'run_not_found' on miss."""
```

Pagination envelope follows the existing pattern: `{total, count, offset, items, has_more, next_offset}`.

### 6.2 Backward-compat shim: `routers/probes.py`

Refactored to dispatch through `RunOrchestrator` + serialize from `RunRow`:

```python
@router.post("/probes")
async def post_probe(
    request: Request,
    orchestrator: RunOrchestrator = Depends(get_run_orchestrator),
):
    """SSE stream — kicks off RunOrchestrator.run in bg task; SSE response
    constructed by event_bus subscription filtered by run_id."""

    body = ProbeRunRequest(**await request.json())
    run_request = RunRequest(mode="topic_probe", payload=body.model_dump())

    # Kick off run as bg task (orchestrator handles row creation + lifecycle)
    run_task = asyncio.create_task(orchestrator.run("topic_probe", run_request))

    # SSE response: subscribe to event_bus filtered by run_id, terminate on
    # probe_completed or probe_failed.
    async def event_stream():
        async for event in event_bus.subscribe_for_run(run_task.run_id):
            yield format_sse(event.kind, event.payload)
            if event.kind in ("probe_completed", "probe_failed"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream", ...)
```

**`event_bus.subscribe_for_run(run_id)` is a NEW method** added in P3: returns an async iterator filtering the bus to events where `payload.run_id == run_id`. Includes a small ring-buffer replay (events fired in the last 500ms) to close the subscription-race window between row creation and subscription registration. Spec implementation defines the exact signature and ring-buffer size.

**Backward-compat contract:** SSE event names (`probe_started`, `probe_grounding`, `probe_generating`, `probe_prompt_completed`, `probe_completed`, `probe_failed`) and payload shapes byte-identical to today. Only the event source moves from "service iterator" to "event bus subscription."

`GET /api/probes` and `GET /api/probes/{id}` re-implemented to read from `RunRow WHERE mode='topic_probe'` and serialize through the existing `ProbeRunSummary` / `ProbeRunResult` Pydantic shapes.

### 6.3 Backward-compat shim: `routers/seed.py`

```python
@router.post("/api/seed", response_model=SeedOutput)
async def seed_taxonomy(body: SeedRequest, request: Request) -> SeedOutput:
    """Synchronous seed run. Persists RunRow at start, awaits completion,
    returns SeedOutput with additive run_id field."""

    orchestrator = request.app.state.run_orchestrator
    run_request = RunRequest(mode="seed_agent", payload=body.model_dump())

    # Awaits completion (sync semantics preserved per Q3 Path 1)
    run_row = await orchestrator.run("seed_agent", run_request)

    # Build SeedOutput from RunRow (shape preserved + additive run_id).
    # SeedOutput.status is derived: 'failed' if run_row.status='failed';
    # 'partial' if run_row.status='completed' but prompts_failed > 0;
    # 'completed' otherwise. This preserves today's tri-valued response
    # contract (SeedOutput.status was always 'completed'|'partial'|'failed')
    # while RunRow.status remains binary 'running'|'completed'|'failed'.
    failed_count = (run_row.aggregate or {}).get("prompts_failed", 0)
    if run_row.status == "failed":
        derived_status = "failed"
    elif failed_count > 0:
        derived_status = "partial"
    else:
        derived_status = "completed"

    return SeedOutput(
        status=derived_status,
        batch_id=run_row.seed_agent_meta.get("batch_id"),
        tier=run_row.seed_agent_meta.get("tier"),
        prompts_generated=run_row.prompts_generated,
        prompts_optimized=run_row.aggregate.get("prompts_optimized", 0),
        prompts_failed=run_row.aggregate.get("prompts_failed", 0),
        estimated_cost_usd=run_row.seed_agent_meta.get("estimated_cost_usd"),
        domains_touched=run_row.taxonomy_delta.get("domains_touched", []),
        clusters_created=run_row.taxonomy_delta.get("clusters_created", 0),
        summary=run_row.aggregate.get("summary", ""),
        duration_ms=int((run_row.completed_at - run_row.started_at).total_seconds() * 1000),
        run_id=run_row.id,  # NEW additive field
    )

@router.get("/api/seed", response_model=RunListResponse)
async def list_seed_runs(...):
    """NEW additive endpoint — paginated list of mode='seed_agent' runs."""

@router.get("/api/seed/{run_id}", response_model=RunResult)
async def get_seed_run(...):
    """NEW additive endpoint — full RunRow detail for a seed run."""
```

`SeedOutput` Pydantic model gains:
```python
class SeedOutput(BaseModel):
    # ... existing fields ...
    run_id: str | None = None  # additive — None for backward-compat with old test fixtures
```

### 6.4 `/api/events` enhancement

Existing endpoint, no schema change. Events that fire from generators gain a `run_id` field in their payload (purely additive — existing event-bus subscribers ignore unknown fields).

Events affected:
- `probe_started`, `probe_grounding`, `probe_generating`, `probe_prompt_completed`, `probe_completed`, `probe_failed` — already had `probe_id`; the new code uses the same field name (still typed as a string UUID; the value is `RunRow.id` now)
- `seed_batch_progress` — gains new `run_id` field
- `seed_started`, `seed_explore_complete`, `seed_completed`, `seed_failed` — gain `run_id` field

---

## 7. MCP tool layer

### 7.1 `synthesis_probe`

Dispatch through `RunOrchestrator.run(mode='topic_probe', ...)`. Response shape unchanged. The `probe_id` field in the result is now `RunRow.id` under the hood (still a string UUID, still typed identically — no consumer-visible change).

### 7.2 `synthesis_seed`

Dispatch through `RunOrchestrator.run(mode='seed_agent', ...)`. Response shape `SeedOutput` unchanged with additive `run_id` field. Behavior remains synchronous (single-call return, no streaming).

---

## 8. Backward-compat contract

Byte-for-byte preservation required for the following surfaces. Snapshot tests against fixture runs assert shape equality (modulo timestamps + UUIDs + the additive `run_id` field).

### 8.1 REST surfaces

| Surface | Preserved shape | Allowed change |
|---|---|---|
| `POST /api/probes` SSE | Event names + payload shapes for all 6 event types | None |
| `GET /api/probes` | `ProbeListResponse` envelope + items | None |
| `GET /api/probes/{id}` | `ProbeRunResult` shape | None |
| `POST /api/seed` | `SeedOutput` shape | Additive `run_id` field only |
| `GET /api/seed` | NEW endpoint | n/a |
| `GET /api/seed/{id}` | NEW endpoint | n/a |
| `GET /api/runs` | NEW endpoint | n/a |
| `GET /api/runs/{id}` | NEW endpoint | n/a |
| `GET /api/events` | Existing endpoint | Additive `run_id` field in event payloads |

### 8.2 MCP tools

| Tool | Preserved shape | Allowed change |
|---|---|---|
| `synthesis_probe` | Result schema | None |
| `synthesis_seed` | Result schema | Additive `run_id` field only |

### 8.3 Internal Python API

| Symbol | Preservation strategy |
|---|---|
| `current_probe_id` ContextVar | Re-exported as alias of `current_run_id` from `services/probe_event_correlation.py` |
| `PROBE_ORPHAN_TTL_HOURS` | Re-exported as alias of `RUN_ORPHAN_TTL_HOURS` from `services/gc.py` |
| `ProbeRun` ORM class | Removed entirely. Test fixtures and any in-tree consumers migrate to `RunRow` in PR1 (dark substrate). |

---

## 9. Test strategy

| Category | Count (est.) | Coverage |
|---|---|---|
| `RunRow` model + migration | 6 | Schema correctness, 4 indexes present, idempotency guard, backfill row-count, downgrade reversibility, JSON metadata roundtrip |
| `RunOrchestrator` lifecycle | 12 | Row create via WriteQueue, status transitions running→completed/failed, cancellation under shield, exception capture, audit-hook clean (no direct writes), unknown-mode error, ContextVar set/reset, double-cancellation idempotency |
| `RunGenerator` protocol | 4 | Both generators conform via `isinstance(g, RunGenerator)`, `RunResult` shape valid for both modes, event publishing fires from each, no direct RunRow writes |
| `TopicProbeGenerator` refactor | 10 | All 5 phases work end-to-end with new bus-publish pattern; events publish to bus with `run_id`; existing probe behavior preserved (snapshot test of full event sequence against fixture); cancellation propagates correctly |
| `SeedAgentGenerator` refactor | 8 | Generation + batch + persist + taxonomy_assign chain works; events publish with `run_id`; user-prompts mode + generated mode both covered; failure modes (generation fail, batch fail, persist fail) mark row appropriately |
| Router shim regression — probes | 10 | SSE event sequence byte-identical (snapshot test against fixture probe), `ProbeListResponse` shape, `ProbeRunResult` shape, error reasons preserved (`link_repo_first`, `probe_not_found`, `invalid_request`), pagination envelope |
| Router shim regression — seed | 6 | `SeedOutput` shape with additive `run_id`, new GET endpoints work, error semantics, sync timing (response after run completes) |
| MCP tool regression | 6 | `synthesis_probe` + `synthesis_seed` result schemas validated; dispatch routes through RunOrchestrator; error paths preserved |
| New `/api/runs` endpoints | 6 | Pagination envelope, mode filter, status filter, project_id filter, ordering, 404 on miss |
| GC sweep | 3 | `_gc_orphan_runs` detects both modes, respects TTL, marks via WriteQueue |
| Cross-process correlation | 2 | `current_run_id` threads through to taxonomy events; `current_probe_id` re-export still works |

**Total: ~73 new tests** + the existing probe + seed test suite continues to pass (modulo internal refactor — public-surface assertions byte-identical via snapshot tests).

---

## 10. Rollout

### 10.1 PR1 (cycle 1) — "Dark substrate"

**Deliverable:** new substrate exists, no live traffic hits it yet.

**Files added:**
- `backend/alembic/versions/<new>_add_run_row.py` — migration
- `backend/app/services/run_orchestrator.py` — orchestrator
- `backend/app/services/run_event_correlation.py` — `current_run_id` ContextVar
- `backend/app/services/generators/__init__.py`, `base.py` — protocol
- `backend/app/services/generators/topic_probe_generator.py` — refactored from probe_service
- `backend/app/services/generators/seed_agent_generator.py` — refactored from seed_orchestrator + tools/seed dispatch logic
- `backend/app/schemas/runs.py` — `RunRequest`, `RunResult`, `RunListResponse` Pydantic models
- All test files for categories 1–5 + 10–11 above (~36 tests)

**Files modified:**
- `backend/app/models.py` — add `RunRow`, remove `ProbeRun` ORM class entirely. All in-tree consumers (services, routers, tests, fixtures) audited and migrated to `RunRow` in this PR. No backward-compat alias.
- `backend/app/services/probe_event_correlation.py` — re-export `current_probe_id = current_run_id` (Python-level alias; out-of-tree consumers and existing import sites keep working)
- `backend/app/services/gc.py` — `_gc_orphan_runs` (replaces `_gc_orphan_probe_runs`), `RUN_ORPHAN_TTL_HOURS` constant + `PROBE_ORPHAN_TTL_HOURS` alias
- `backend/app/main.py` — lifespan startup uses `_gc_orphan_runs`, registers `RunOrchestrator` on `app.state.run_orchestrator`
- `backend/app/routers/probes.py` — reads switch to `RunRow WHERE mode='topic_probe'` (since `probe_run` table is gone). POST handler retains its existing `ProbeService` dispatch path until PR2 swaps it to `RunOrchestrator`. Net effect: zero user-visible behavior change in PR1.
- `backend/app/services/probe_service.py` — internal reads/writes refactored to `RunRow` ORM; class shape preserved for PR1 backward-compat; class deletion + dispatch refactor lands in PR2.
- Existing probe + seed tests updated to import `RunRow` instead of `ProbeRun` (mechanical sed-style refactor).

**Not changed in PR1:**
- POST `/api/probes`, POST `/api/seed`, `synthesis_probe`, `synthesis_seed` dispatch — still on legacy code paths. PR2 swaps these to `RunOrchestrator`.
- `routers/seed.py` GET endpoints don't exist yet; added in PR2.
- `routers/runs.py` — added in PR2.
- Frontend `SeedModal.svelte` — `run_id` filter additive change lands in PR2 alongside the `run_id` field arrival in `seed_batch_progress` payloads.

**PR1 deployment model:** safe to deploy alone. Behavior is byte-identical to v0.4.17; only the underlying storage table is renamed/reshaped.

### 10.2 PR2 (cycle 2) — "Live + shims"

**Deliverable:** `RunOrchestrator` is the only writer; all 4 surfaces dispatch through it.

**Files added:**
- `backend/app/routers/runs.py` — new unified endpoints

**Files modified:**
- `backend/app/routers/probes.py` — POST dispatches through `RunOrchestrator`; SSE response constructed by event_bus subscription
- `backend/app/routers/seed.py` — POST dispatches through `RunOrchestrator`; new GET endpoints added
- `backend/app/tools/probe.py` — MCP dispatch through `RunOrchestrator`
- `backend/app/tools/seed.py` — MCP dispatch through `RunOrchestrator`; orchestration logic moved to `SeedAgentGenerator` in PR1, this PR removes the orchestration body
- `backend/app/services/probe_service.py` — class deleted (logic lives in `TopicProbeGenerator` from PR1); module-level helpers from P2 Path A retained
- `backend/app/services/seed_orchestrator.py` — `SeedOrchestrator.generate()` retained as the generator's internal call; class shrinks to just the generation phase
- `backend/app/services/batch_orchestrator.py:240-242` — `seed_batch_progress` payload gains `run_id` (one-line additive)
- `frontend/src/lib/components/taxonomy/SeedModal.svelte` — one-line additive: filter `seed_batch_progress` events by `run_id` when modal is showing a specific run
- All shim regression tests (categories 6–9 above, ~32 tests)

**Atomic deployment:** PR2 merges and all 4 surfaces switch over in one shot. No flag-gated rollout needed.

### 10.3 Release: v0.4.18

After PR1 + PR2 merge, version bump from v0.4.18-dev → v0.4.18 via `./scripts/release.sh`. Release notes summarize:
- New `RunRow` substrate
- `synthesis_seed` and `POST /api/seed` gain `run_id` (additive)
- `synthesis_probe` and `POST /api/probes` shape unchanged
- New `GET /api/runs`, `GET /api/seed`, `GET /api/seed/{id}` endpoints
- ProbeRun table dropped
- Internal: `current_probe_id` aliased; `_set_probe_status` retired in favor of `_set_run_status`

---

## 11. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Cancellation shielding misalignment — moving `asyncio.shield()` from inside `_run_impl` to `RunOrchestrator.run` could let SSE response close before row marked failed | Medium | Test: simulate `asyncio.CancelledError` mid-run, assert row status reaches `failed` before SSE response closes. Verify in PR1 tests. |
| `current_probe_id` re-export coverage gap — if any taxonomy event firing site imports the ContextVar by attribute access on the old module, the rename breaks correlation | Medium | Spec implementation: grep every `current_probe_id` reference site (verified at spec time: 7 sites in `services/`, all use module-level import — re-export shim covers them). Test: assert `current_probe_id is current_run_id` after import. |
| Frontend filter race — `SeedModal` subscribes to `/api/events?run_id=X` after `POST /api/seed` returns; first event could fire before subscription registered | Low | Server-side: replay a small ring buffer of recent events on subscription. Client-side: `seed_started` event gates the modal's "running" state — if not received within 500ms, the modal shows from poll-based fallback. Test: simulated late subscription. |
| Backfill performance on large `probe_run` tables | Low | Migration uses `INSERT ... SELECT` which is single-statement and atomic in SQLite. Real-world `probe_run` rows likely <10k; sub-second backfill expected. Test: benchmark with 100k synthetic rows. |
| MCP tool `synthesis_seed` schema validation against the additive `run_id` field — old MCP clients (Claude Code, VSCode bridge) that strict-validate response schemas could reject the extra field | Low | Pydantic Output models in MCP tools use `model_config = ConfigDict(extra='ignore')` by default. Verify before PR1 ship that the SDK in use respects this. Backup: gate `run_id` field emission behind a feature flag. |

---

## 12. Out of scope

The following are intentionally NOT delivered in P3:

| Item | Where it lands |
|---|---|
| Frontend unified history UI (SeedModal-becomes-tabs) | T4 (v0.4.22) |
| Save-as-suite + replay | T2 (v0.4.20). P3 provisions `suite_id` column. |
| Probe → seed-agent promotion | T3 (v0.4.21). P3 makes the `mode` flip mechanically possible. |
| `Accept`-header content negotiation for SSE on POST `/api/seed` | Deferred — Exploring entry in ROADMAP |
| `current_probe_id` re-export retirement (drop the alias) | Deferred — Exploring entry in ROADMAP, target 2+ release cycles post-v0.4.18 |
| Foundation P4 (long-handler restructures) | v0.4.19 |
| Probe Tier 4 final UI consolidation | v0.4.22 |

---

## 13. References

- ROADMAP entry: [Foundation P3 — Substrate unification](../../ROADMAP.md#foundation-p3--substrate-unification-target-v0418)
- ROADMAP Exploring: [`Accept`-header content negotiation for SSE on POST `/api/seed`](../../ROADMAP.md)
- ROADMAP Exploring: [`current_probe_id` → `current_run_id` rename completion](../../ROADMAP.md)
- Predecessor: [v0.4.17 P2 Path A spec](../../specs/v0.4.17-probe-internals-split-2026-05-05.md)
- Predecessor: [v0.4.16 P1 cold-path spec](../../specs/v0.4.16-cold-path-chunking-2026-05-04.md)
- Predecessor: [Topic Probe Tier 1 spec](../../specs/topic-probe-2026-04-29.md)
- Migration `ec86c86ba298` (probe_run creation) — `backend/alembic/versions/ec86c86ba298_add_probe_run_table_for_topic_probe_.py`
- Backend internals reference: [backend/CLAUDE.md](../../../backend/CLAUDE.md)
