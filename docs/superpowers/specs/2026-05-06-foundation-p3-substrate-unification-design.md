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
| Q5 | Generator protocol | **b — Awaitable generators + bus events.** `async def run(req, *, run_id) -> GeneratorResult`; events publish directly to bus. | Avoids redundant re-publication layer; retires vestigial `AsyncIterator` shape; cleaner protocol; narrower test surface. |
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
    """Idempotent — matched-state guard so partial-completion (run_row present
    AND probe_run also present) aborts with operator-readable error rather than
    silently proceeding. See "Idempotency guard correctness" notes below the
    code block for the rationale."""
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "run_row" in tables and "probe_run" not in tables:
        return  # Already fully migrated — idempotent no-op

    if "run_row" in tables and "probe_run" in tables:
        raise RuntimeError(
            "run_row table exists but probe_run also still exists — "
            "partial migration detected. Manual cleanup required before retry."
        )

    # Normal upgrade path: probe_run exists, run_row does not.

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

    # Reverse-backfill from run_row, extracting scope/commit_sha from JSON.
    # NOT NULL safety: original probe_run had repo_full_name/scope/intent_hint
    # NOT NULL with server defaults. RunRow makes them nullable to accommodate
    # seed mode. COALESCE every NOT-NULL-on-the-other-side column to its original
    # default so the reverse-backfill never fails NOT NULL on edge-case rows.
    # (Seed-mode rows are filtered out by the WHERE clause; this defends only
    # against probe-mode rows that happened to have nulls in nullable RunRow cols.)
    op.execute("""
        INSERT INTO probe_run (
            id, topic, scope, intent_hint, repo_full_name, project_id,
            commit_sha, started_at, completed_at, prompts_generated,
            prompt_results, aggregate, taxonomy_delta, final_report,
            status, suite_id, error
        )
        SELECT
            id,
            COALESCE(topic, '') AS topic,
            COALESCE(json_extract(topic_probe_meta, '$.scope'), '**/*') AS scope,
            COALESCE(intent_hint, 'explore') AS intent_hint,
            COALESCE(repo_full_name, '') AS repo_full_name,
            project_id,
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

**Migration atomicity (SQLite-specific):** The upgrade has 9 ops (1 × `create_table` + 4 × `create_index` + 1 × `INSERT...SELECT` + 2 × `drop_index` + 1 × `drop_table`). SQLite supports DDL inside transactions, but Alembic's default in this project (verified against `backend/alembic/env.py` 2026-05-06: `transaction_per_migration` is NOT set) determines whether all migrations run inside one transaction. **Spec implementation requirement:** add `transaction_per_migration=True` to `context.configure(...)` calls in `env.py` as part of PR1. With this flag, all 9 ops above commit atomically — partial completion impossible. Without it, a failed `INSERT...SELECT` after `create_table` would leave both tables present.

**Idempotency guard correctness rationale:** the simple guard `if "run_row" in inspector.get_table_names(): return` (used by other migrations in this codebase) does NOT handle the partial-completion case where both tables exist after a failed prior upgrade. The matched-state guard shown inline at the top of `upgrade()` above handles three cases: (1) fresh upgrade — both tables in expected state, proceed; (2) already migrated — `run_row` exists, `probe_run` gone, no-op; (3) partial completion — both tables exist, abort with operator-readable error so manual inspection can establish whether the backfill ran. There is no automatic recovery for case (3) because the partial state could indicate either "backfill ran but drop_table failed" or "create_table ran but backfill never started" — different states require different operator actions.

**Backfill safety:** the `INSERT...SELECT` is one statement, atomic in SQLite. SQLite's `json_object` and `json_extract` functions are available in all SQLite versions ≥ 3.38.0 (project requires Python 3.12 + bundled sqlite3, which is ≥ 3.40 on all supported platforms — safe).

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
      - return a final GeneratorResult dataclass

    Generators MUST NOT touch RunRow directly — RunOrchestrator owns row creation,
    status writes, and error capture. This is enforced by the WriteQueue audit hook.
    """

    async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult:
        """Execute the run and return final result. Publish events to bus
        with run_id threaded into payload."""
        ...

@dataclass(frozen=True)
class RunRequest:
    """Mode-agnostic input shape — concrete generators cast to mode-specific request type."""
    mode: Literal["topic_probe", "seed_agent"]
    payload: dict  # Mode-specific input fields

@dataclass(frozen=True)
class GeneratorResult:
    """Final output a generator returns to RunOrchestrator. Service-layer dataclass.

    Distinct from `RunResult` Pydantic model in `schemas/runs.py` (used as the
    REST + MCP response model on `/api/runs/{run_id}`). The two were originally
    both named `RunResult` — split here to avoid the namespace collision flagged
    in spec V2 review."""
    terminal_status: Literal["completed", "partial", "failed"]
    # Generator classifies its own terminal state. RunOrchestrator persists this
    # value verbatim into RunRow.status (preserves today's 4-value status set
    # including 'partial'). If the generator raises instead of returning,
    # RunOrchestrator sets status='failed' itself.
    prompts_generated: int
    prompt_results: list[dict]
    aggregate: dict
    taxonomy_delta: dict
    final_report: str | None  # None for seed_agent at v0.4.18

# Required key shapes for backward compat with existing serializers:
#   aggregate (probe mode):
#     # ProbeRunResult.aggregate (ProbeAggregate schema) — preserved unchanged
#     # See backend/app/schemas/probes.py for full shape including
#     # mean_overall, scoring_formula_version, etc.
#   aggregate (seed mode):
#     # Required keys for SeedOutput shim derivation in section 6.3:
#     prompts_optimized: int      # count of completed prompts
#     prompts_failed: int         # count of failed prompts
#     summary: str                # short human-readable run summary
#   taxonomy_delta (seed mode):
#     # Required keys for SeedOutput shim:
#     domains_touched: list[str]  # from batch_taxonomy_assign return
#     clusters_created: int       # from batch_taxonomy_assign return
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

    async def run(
        self,
        mode: str,
        request: RunRequest,
        *,
        run_id: str | None = None,
    ) -> RunRow:
        """Top-level dispatch. Creates row → runs generator → persists result.

        run_id: optional caller-supplied id. When None, minted internally.
        Race-sensitive callers (e.g., probes router constructing SSE response)
        pre-mint and supply it to register subscriptions BEFORE dispatch.
        """
        if run_id is None:
            run_id = str(uuid.uuid4())

        if mode not in self._generators:
            # Cannot mark failed — row not yet created.
            raise ValueError(f"unknown mode: {mode}")

        run_row = await self._create_row(mode, request, run_id=run_id)
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

    async def _create_row(
        self, mode: str, request: RunRequest, *, run_id: str,
    ) -> RunRow:
        """Insert run_row(status='running') via WriteQueue. run_id is
        externally supplied (callers that need race-free SSE subscriptions
        pre-mint it). All work_fn lambdas passed to WriteQueue.submit MUST
        commit before returning per the queue contract (write_queue.py:228)."""
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
        via WriteQueue. Replaces probe_service._set_probe_status. The
        wrapped work_fn MUST commit before returning."""
        await self._write_queue.submit(
            lambda db: self._do_update_status(db, run_id, status, fields),
            timeout=30,
            operation_label=f"run_orchestrator.set_status[{status}]",
        )

    async def _persist_final(self, run_id: str, result: GeneratorResult) -> None:
        """Write final result fields + status=result.terminal_status (one of
        'completed' | 'partial' | 'failed') via WriteQueue. Generator-classified
        status preserves today's 4-value contract on RunRow.status. Wrapped
        work_fn MUST commit before returning."""
        await self._write_queue.submit(
            lambda db: self._do_persist_final(db, run_id, result),
            timeout=60,
            operation_label="run_orchestrator.persist_final",
        )

    async def _mark_failed(self, run_id: str, *, error: str) -> None:
        """Write status='failed' + error + completed_at via WriteQueue.
        Used only for orchestrator-caught exceptions (incl. cancellation);
        generator-returned terminal_status='failed' goes through _persist_final."""
        await self._set_run_status(
            run_id, status="failed", error=error[:2000], completed_at=_utcnow()
        )

    async def _reload(self, run_id: str) -> RunRow:
        """Read row back through standard read path."""
        async with async_session_factory() as db:
            return await db.get(RunRow, run_id)
```

### 5.3 `current_run_id` ContextVar — rebound at canonical home

The canonical home of `current_probe_id` today is **`backend/app/services/probe_common.py:33-35`** (set during the v0.4.17 P2 split). `probe_service.py:65` re-imports directly from `probe_common`; `probe_event_correlation.py:8` re-imports transitively via `probe_service`. Both resolve to the same `ContextVar` object. The existing test `backend/tests/test_probe_service_module_split_v0_4_17.py:27` asserts object identity (`legacy.current_probe_id is common.current_probe_id`), which P3 must preserve.

P3 rebinds the canonical declaration in `probe_common.py` and exposes `current_run_id` as the new canonical name. Both names point to the **same `ContextVar` object** (no rebind that breaks identity).

```python
# backend/app/services/probe_common.py (canonical home — UPDATED in PR1)
from contextvars import ContextVar

# The canonical ContextVar for run-correlation. Renamed from the original
# current_probe_id but kept as an additional alias for backward compat.
# Both names are the SAME ContextVar object — preserves the
# `legacy.current_probe_id is common.current_probe_id` identity test.
current_run_id: ContextVar[str | None] = ContextVar(
    "current_run_id", default=None,
)

# Backward-compat alias — same object, different name.
# Existing call sites (`from probe_common import current_probe_id`,
# `from probe_service import current_probe_id`,
# `from probe_event_correlation import current_probe_id`) all keep working.
# Slated for retirement in a follow-up cleanup cycle (see ROADMAP Exploring).
current_probe_id = current_run_id

__all__ = [..., "current_run_id", "current_probe_id"]
```

**Existing import chain (verified 2026-05-06):**
- `probe_service.py:65` re-imports `current_probe_id` from `probe_common` (`from app.services.probe_common import current_probe_id`).
- `probe_event_correlation.py:8` re-imports transitively via `probe_service` (`from app.services.probe_service import current_probe_id`), NOT from `probe_common` directly.
- The chain `probe_event_correlation → probe_service → probe_common` resolves transitively to the same `ContextVar` object. The rebind in section 5.3 (which adds `current_run_id = ContextVar(...)` and `current_probe_id = current_run_id` in `probe_common.py`) preserves object identity through this chain — every `current_probe_id` import in any of the three modules resolves to the same object as `current_run_id`. PR1 needs **zero changes** to `probe_event_correlation.py` or `probe_service.py` for the ContextVar; only `probe_common.py` is touched.

**Verified line references (2026-05-06 grep):** 11 lines mentioning `current_probe_id` across 3 files in `backend/app/services/`:
- `probe_common.py:33,34,124` — declaration line + `name=` argument line + `__all__` entry
- `probe_service.py:5,65,374,445,1519` — module docstring mention + re-import + comment + `set(token)` + `reset(token)`
- `probe_event_correlation.py:8,10,20` — re-import + `__all__` entry + use site in `inject_probe_id`

Plus 1 test (`tests/test_probe_service_module_split_v0_4_17.py:27`) asserting object identity. The rebind preserves identity by reusing the same `ContextVar` object under both names.

### 5.4 `TopicProbeGenerator` (refactored from `ProbeService`)

Internal 5-phase orchestrator preserved (grounding → generating → running → observability → reporting). Mechanical changes:

- `yield ProbeStartedEvent(...)` calls become `event_bus.publish("probe_started", {**payload, "run_id": run_id})`
- `_set_probe_status` calls removed. The `RunRow.status` column takes **four values: `running`, `completed`, `failed`, `partial`** — preserving today's `ProbeRun.status` semantics byte-for-byte (verified against `backend/app/services/probe_service.py:1338-1342` where `final_status` resolves to one of the three terminal values; `ProbeRunResult.status` Literal in `backend/app/schemas/probes.py:174` confirms `Literal["completed", "failed", "partial", "running"]`). Set as follows:
  - `running` — set by `RunOrchestrator._create_row` at start
  - `completed` | `partial` | `failed` — set by `RunOrchestrator._persist_final` based on the `GeneratorResult.terminal_status` field returned by the generator. Generators classify their own terminal state: `TopicProbeGenerator` returns `partial` when 1+ prompts succeeded but 1+ failed (mirrors `probe_service.py:1342`); `SeedAgentGenerator` returns `partial` on the same condition (mirrors `tools/seed.py:362-364`).
  - `failed` — also set by `RunOrchestrator._mark_failed` when the generator raises (uncaught exception or `asyncio.CancelledError`)

Mid-run progress is observable through per-phase events on the event bus — no intermediate row status changes within a run. WriteQueue audit-hook stays clean (only `RunOrchestrator` writes to `RunRow`).
- Returns `GeneratorResult` (replacing the `ProbeRunResult` build at end of `_run_impl`); orchestrator persists it via `_persist_final`
- The `current_run_id` ContextVar is set by `RunOrchestrator.run` (outer wrapper) before invoking the generator — the generator inherits the correlation via async context propagation
- `_gc_orphan_probe_runs` retired in favor of `_gc_orphan_runs` (in `services/gc.py`)

The 9 module-level helpers from P2 Path A (`probe_common.py`, `probe_phases.py`, `probe_phase_5.py`) are reused as-is — they're already free functions that don't depend on the `ProbeService` class shape.

### 5.5 `SeedAgentGenerator` (refactored from `SeedOrchestrator` + `tools/seed.py`)

Existing internal flow preserved: `SeedOrchestrator.generate()` → `batch_pipeline.run_batch()` → `bulk_persist()` → `batch_taxonomy_assign()`. Generator wrapper delegates to these.

Mechanical changes:
- `event_bus.publish("seed_batch_progress", {...})` in `batch_orchestrator.py:240-258` gains `run_id` field in payload (one-line additive change). This is the **only** seed event published to `event_bus` today; the four `seed_*` decision events listed below go to a different channel — see section 6.4.
- `seed_started`, `seed_explore_complete`, `seed_completed`, `seed_failed` are **taxonomy decision events** (written via `taxonomy_event_logger.log_decision(path="hot", op="seed", decision=..., context={...})` to JSONL files + the in-memory ring buffer — NOT to the SSE bus). Today fired from `tools/seed.py:handle_seed`; in P3 they move into `SeedAgentGenerator.run`. They gain `run_id` inside their `context` dict (additive); the router/MCP shim retains only the dispatch call to `RunOrchestrator.run(mode='seed_agent', ...)`; all event emission is internal to the generator.
- `batch_id` in `seed_agent_meta` JSON preserves the existing in-memory UUID for backward compat with anything that grepped logs by `batch_id`.

**`GeneratorResult` keys for seed mode:**
- `terminal_status`: classified as `"completed"` when all prompts succeeded; `"partial"` when 1+ prompts succeeded AND 1+ failed (mirrors `tools/seed.py:362-364`); `"failed"` when ALL prompts failed OR any unrecoverable phase failure (generation, persist).
- `prompts_generated`: int (count from `gen_result.prompts` length, or `len(prompts)` for user-provided)
- `prompt_results`: list of dicts conforming to today's per-prompt result shape (existing `run_batch` return)
- `aggregate`: `{"prompts_optimized": int, "prompts_failed": int, "summary": str}` — required keys for `SeedOutput` shim derivation in section 6.3
- `taxonomy_delta`: `{"domains_touched": list[str], "clusters_created": int}` — sourced from `batch_taxonomy_assign`'s return dict
- `final_report`: `None` (seed mode does not produce a final report at v0.4.18; T4 may add one)

**Early-failure path semantics (preserves today's HTTP 200 with `status="failed"`):** today's `tools/seed.py:193-204` returns `SeedOutput(status="failed", summary="Requires project_description with a provider, or user-provided prompts.")` synchronously when input validation fails (no `project_description` AND no `prompts` AND no provider). Under P3, this validation moves into `SeedAgentGenerator.run` which RETURNS (does not raise) `GeneratorResult(terminal_status="failed", prompts_generated=0, prompt_results=[], aggregate={"prompts_optimized": 0, "prompts_failed": 0, "summary": "Requires project_description with a provider, or user-provided prompts."}, taxonomy_delta={"domains_touched": [], "clusters_created": 0}, final_report=None)`. `RunOrchestrator._persist_final` writes `status='failed'` from `terminal_status`. The router shim returns `SeedOutput` with HTTP 200 (mirrors today). Generator-raised exceptions (vs. returned-failed-result) only fire on UNEXPECTED failures (e.g., LLM errors, DB failures); those flow through `RunOrchestrator._mark_failed` and the router still returns 200 with `status='failed'` because the orchestrator's exception path does not propagate to the response — the shim catches `RunOrchestrator.run`'s exceptions and serializes the failed `RunRow` (which now exists, was created at start) into `SeedOutput`. **Implementation requirement:** the seed shim wraps `RunOrchestrator.run(...)` in try/except so any uncaught exception is caught, the failed `RunRow` is reloaded, and `SeedOutput` is returned with HTTP 200.

### 5.6 GC sweep

```python
# backend/app/services/gc.py
# IMPORTANT: signature preserves the existing _gc_orphan_probe_runs(db) shape
# so it composes inside run_startup_gc's batched _do_sweep work_fn (today at
# gc.py:72-87) without restructuring the sweep boundary. Outer caller
# (run_startup_gc) commits at the end of the sweep, matching the existing
# _gc_orphan_probe_runs convention (gc.py:309-311 — "this helper does NOT
# commit internally").

async def _gc_orphan_runs(db: AsyncSession) -> int:
    """Sweep orphan run_row rows where status='running' for >RUN_ORPHAN_TTL_HOURS.
    Returns count of marked-failed rows. Caller (run_startup_gc / run_recurring_gc)
    is responsible for committing — same convention as the existing
    _gc_orphan_probe_runs helper."""
    cutoff = datetime.utcnow() - timedelta(hours=RUN_ORPHAN_TTL_HOURS)
    result = await db.execute(
        update(RunRow)
        .where(RunRow.status == "running")
        .where(RunRow.started_at < cutoff)
        .values(
            status="failed",
            error="orphaned (ttl exceeded)",
            completed_at=_utcnow(),
        )
    )
    return result.rowcount or 0

# Integration with existing sweep boundary in run_startup_gc:
#   async def _do_sweep(write_db: AsyncSession) -> int:
#       total = 0
#       total += await _gc_orphan_runs(write_db)  # NEW — replaces _gc_orphan_probe_runs
#       # ... other sweep helpers unchanged ...
#       return total
#
# The outer write_queue.submit at gc.py:86 commits the entire batched sweep
# in one transaction. _gc_orphan_runs participates in that batch identically
# to how _gc_orphan_probe_runs did.

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

**Path-param naming convention:** new endpoints (`/api/runs/{run_id}`, `/api/seed/{run_id}`) use `run_id` as the canonical name. Existing `/api/probes/{probe_id}` retains its current path-param name (`probe_id`) byte-for-byte to preserve the established contract — internally the value is `RunRow.id`, but the path-param name stays as documented. This intentional inconsistency at the API surface preserves backward compat; future major version (v0.5+) can unify if desired.

### 6.2 Backward-compat shim: `routers/probes.py`

Refactored to dispatch through `RunOrchestrator` + serialize from `RunRow`. **Caller mints `run_id` first** so the SSE subscription registers BEFORE the orchestrator kicks off — eliminates the subscription-creation race entirely.

```python
@router.post("/probes")
async def post_probe(
    request: Request,
    orchestrator: RunOrchestrator = Depends(get_run_orchestrator),
):
    """SSE stream. Caller mints run_id, registers SSE subscription, then
    dispatches RunOrchestrator.run with the pre-allocated run_id. No race
    between row creation and event subscription."""

    body = ProbeRunRequest(**await request.json())
    run_id = str(uuid.uuid4())  # Caller-side allocation — passed into orchestrator
    run_request = RunRequest(mode="topic_probe", payload=body.model_dump())

    # Subscribe FIRST so the buffer captures any events the orchestrator
    # publishes during _create_row + generator start.
    subscription = event_bus.subscribe_for_run(run_id)

    # Kick off run as bg task with pre-allocated run_id
    run_task = asyncio.create_task(
        orchestrator.run("topic_probe", run_request, run_id=run_id)
    )

    async def event_stream():
        try:
            async for event in subscription:
                yield format_sse(event.kind, event.payload)
                # Termination on terminal events only. Rate-limit events
                # (`ProbeRateLimitedEvent` from yield path; `rate_limit_active`
                # from event_bus.publish) are informational, not terminal.
                if event.kind in ("probe_completed", "probe_failed"):
                    break
        finally:
            await subscription.aclose()
            # If the client disconnects, run_task may still be running;
            # orchestrator handles its own cancellation/cleanup via
            # asyncio.shield in _mark_failed.

    return StreamingResponse(event_stream(), media_type="text/event-stream", ...)
```

**API change to `RunOrchestrator.run`:** signature gains a `run_id: str` keyword parameter. `RunOrchestrator._create_row` accepts the externally-supplied id instead of minting its own. This makes the SSE subscription race-free at the cost of pushing UUID generation into the caller. For callers that don't pre-mint (e.g., MCP `synthesis_probe` if it doesn't need to register a subscription early), `run_id` defaults to `str(uuid.uuid4())` minted internally. Update section 5.2 signature accordingly:

```python
async def run(
    self,
    mode: str,
    request: RunRequest,
    *,
    run_id: str | None = None,  # caller-side allocation supported for race-free SSE
) -> RunRow:
    if run_id is None:
        run_id = str(uuid.uuid4())
    run_row = await self._create_row(mode, request, run_id=run_id)
    # ... rest unchanged
```

**`event_bus.subscribe_for_run(run_id)` is a NEW method** added in P3:
- Returns an async iterator filtering bus events to those where `payload.get("run_id") == run_id`
- Includes a small ring-buffer replay (events fired in the last 500ms before subscription) as a defense-in-depth for any caller that subscribes after dispatch (e.g., reconnecting clients). The 500ms buffer is local to this method, not the global event_bus replay buffer (which is keyed by global seq, see `event_bus.py:_replay_buffer`).
- Filter scope: ONLY events that explicitly carry `run_id` in payload pass through. `taxonomy_changed`, `optimization_created`, `optimization_deleted`, `index_phase_changed` etc. — which fire on the bus during a probe run but DON'T carry `run_id` — are excluded from the per-run SSE stream.
- Lifetime: the iterator yields until the subscription is `aclose()`d by the consumer. No timeout enforced at the bus level; consumers responsible for their own loop termination.

Implementation must add unit tests covering: (a) events with matching `run_id` are yielded; (b) events without `run_id` are filtered out; (c) events from other runs are filtered out; (d) ring-buffer replay catches recent events.

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

    # SeedOutput.status maps directly from RunRow.status (same 4 values:
    # 'completed' | 'partial' | 'failed' | 'running'). With Q3 Path 1's
    # synchronous semantics, 'running' is never observed at this point —
    # RunOrchestrator.run() only returns after the generator reaches a
    # terminal state. The generator (SeedAgentGenerator) is responsible
    # for classifying terminal_status='partial' when prompts_failed > 0.
    # See section 5.5 for the classification rules.
    # None-guard every JSON-column accessor — partially-populated failed
    # runs may have aggregate / seed_agent_meta / taxonomy_delta as NULL.
    aggregate = run_row.aggregate or {}
    seed_meta = run_row.seed_agent_meta or {}
    taxonomy_delta = run_row.taxonomy_delta or {}

    completed_at = run_row.completed_at or run_row.started_at
    duration_ms = int((completed_at - run_row.started_at).total_seconds() * 1000)
    return SeedOutput(
        status=run_row.status,  # type: ignore[arg-type]  # 4-valued
        batch_id=seed_meta.get("batch_id"),
        tier=seed_meta.get("tier"),
        prompts_generated=run_row.prompts_generated,
        prompts_optimized=aggregate.get("prompts_optimized", 0),
        prompts_failed=aggregate.get("prompts_failed", 0),
        estimated_cost_usd=seed_meta.get("estimated_cost_usd"),
        domains_touched=taxonomy_delta.get("domains_touched", []),
        clusters_created=taxonomy_delta.get("clusters_created", 0),
        summary=aggregate.get("summary", ""),
        duration_ms=duration_ms,  # None-guarded above
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

### 6.4 `/api/events` enhancement + dual-channel event taxonomy

Existing endpoint, no schema change. There are **two event channels** in this codebase that look superficially similar but have different consumers; P3 must thread `run_id` into both. The spec previously bundled them — they're separated below for clarity.

**Channel 1: `event_bus` (SSE-published via `/api/events`).** This is what frontend subscribers consume. The bus-subscription SSE shim in `routers/probes.py` (section 6.2) reads from this channel.

Probe events on this channel today (verified in `backend/app/services/probe_service.py`):

| Event name | Source | `run_id` thread |
|---|---|---|
| `probe_started` | `ProbeStartedEvent` yielded → mapped by `_event_name_for` | gains `run_id` |
| `probe_grounding` | `ProbeGroundingEvent` yielded | gains `run_id` |
| `probe_generating` | `ProbeGeneratingEvent` yielded | gains `run_id` |
| `probe_prompt_completed` | `ProbeProgressEvent` yielded → mapped to this name | gains `run_id` |
| `probe_completed` | `ProbeCompletedEvent` yielded | gains `run_id` |
| `probe_failed` | `ProbeFailedEvent` yielded (or synthetic emit on uncaught error in router) | gains `run_id` |
| `ProbeRateLimitedEvent` | `ProbeRateLimitedEvent` yielded (no explicit `_event_name_for` mapping → falls through to class name) | gains `run_id` |
| `rate_limit_active` | `event_bus.publish("rate_limit_active", ...)` at `probe_service.py:1006` (parallel to the yielded ProbeRateLimitedEvent) | gains `run_id` |

**Backward-compat:** the SSE event names + payload shapes for all 8 events above must remain byte-identical. Snapshot tests in section 9 cover all 8.

Seed events on this channel today:

| Event name | Source | `run_id` thread |
|---|---|---|
| `seed_batch_progress` | `event_bus.publish("seed_batch_progress", ...)` at `batch_orchestrator.py:240-258` | gains `run_id` |

**Channel 2: `taxonomy_event_logger` (JSONL + ring buffer, NOT SSE-published).** This is the structured decision log. Read by Observatory `ActivityPanel` via `taxonomy_activity` SSE bridge — but that's a separate SSE channel from `/api/events` (events on the ring buffer are bridged through `taxonomy_activity` events, not surfaced raw).

Seed decision events on this channel:
- `seed_started`, `seed_explore_complete`, `seed_completed`, `seed_failed` — `taxonomy_event_logger.log_decision(path="hot", op="seed", decision=..., context={...})` from today's `tools/seed.py`. Under P3 these move into `SeedAgentGenerator.run`. They gain `run_id` inside their `context` dict (additive). The `taxonomy_activity` SSE bridge already passes through `context` unchanged, so subscribers see the new field without any schema change.

Probe decision events on this channel (existing): `probe_started`, `probe_grounding`, `probe_generating`, `probe_prompt_completed`, `probe_taxonomy_change`, `probe_completed`, `probe_failed` — these are SEPARATE from the bus events with the same names. They're correlated via the `current_probe_id` (now `current_run_id`) ContextVar that `inject_probe_id` reads. The ContextVar rebind in section 5.3 keeps these working without code change.

**Spec implementation note:** the bus-subscription SSE shim in section 6.2 filters strictly to `event_bus` events with `payload.run_id == run_id`. It does NOT consume taxonomy_event_logger decisions. `taxonomy_changed`, `optimization_created`, etc. that fire on `event_bus` during a run but DON'T carry `run_id` are filtered out by the missing-key check.

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
| `POST /api/probes` SSE | Event names + payload shapes for all 8 event types: `probe_started`, `probe_grounding`, `probe_generating`, `probe_prompt_completed`, `probe_completed`, `probe_failed`, `ProbeRateLimitedEvent`, `rate_limit_active` (see section 6.4 Channel 1) | Additive `run_id` field in payloads |
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
| `current_probe_id` ContextVar | Same `ContextVar` object exposed under both names from canonical home `services/probe_common.py`. Object-identity invariant preserved for the existing test `tests/test_probe_service_module_split_v0_4_17.py:27`. See section 5.3. |
| `PROBE_ORPHAN_TTL_HOURS` | Re-exported as alias of `RUN_ORPHAN_TTL_HOURS` from `services/gc.py` |
| `ProbeRun` ORM class | Removed in **PR2** (not PR1). PR1 retains `ProbeRun` as a thin SQLAlchemy view-model alias of `RunRow` filtered to `mode='topic_probe'` so the legacy `probe_service.py` + `routers/probes.py` paths continue working with no surgical changes. PR2 deletes the alias when `probe_service.py` itself is retired and its dispatch is replaced by `TopicProbeGenerator` invocations. **Reconciliation with section 10.1:** PR1's "ProbeRun ORM class — Removed entirely" claim is replaced by "ProbeRun aliased to RunRow filter; class identity removed in PR2". |

---

## 9. Test strategy

| Category | Count (est.) | Coverage |
|---|---|---|
| `RunRow` model + migration | 8 | Schema correctness, 4 indexes present, idempotency guard (matched-state), partial-completion abort, backfill row-count, downgrade reversibility (incl. NOT NULL re-COALESCE for `intent_hint`/`repo_full_name`/`topic`), JSON metadata roundtrip, all 4 status values (`running`/`completed`/`partial`/`failed`) writable + readable |
| `RunOrchestrator` lifecycle | 14 | Row create via WriteQueue (caller-supplied `run_id` AND internally-minted `run_id` paths), status transitions `running` → `completed`/`partial`/`failed` (each path via `_persist_final` from `GeneratorResult.terminal_status`), cancellation under shield, exception capture (uncaught raises route through `_mark_failed`), audit-hook clean (no direct writes), unknown-mode error before row created, ContextVar set/reset, double-cancellation idempotency, `_persist_final` writes `terminal_status` from `GeneratorResult` not hardcoded, `WriteQueue.submit` lambdas commit before returning |
| `RunGenerator` protocol | 4 | Both generators conform via `isinstance(g, RunGenerator)`, `GeneratorResult` shape valid for both modes (incl. `terminal_status` field), event publishing fires from each with `run_id` in payload, no direct RunRow writes (audit-hook assertion) |
| `TopicProbeGenerator` refactor | 12 | All 5 phases work end-to-end with new bus-publish pattern; all 8 SSE event types preserved (`probe_started`, `probe_grounding`, `probe_generating`, `probe_prompt_completed`, `probe_completed`, `probe_failed`, `ProbeRateLimitedEvent`, `rate_limit_active`) including byte-identical payload snapshots; existing probe behavior preserved (full event sequence snapshot against a fixture); cancellation propagates correctly; partial-mode classification (`prompts_failed > 0` → `terminal_status='partial'`) preserved; `current_run_id` ContextVar inherited correctly into spawned tasks |
| `SeedAgentGenerator` refactor | 10 | Generation + batch + persist + taxonomy_assign chain works; bus events publish with `run_id`; taxonomy decision events (`seed_started`/`seed_explore_complete`/`seed_completed`/`seed_failed`) publish with `run_id` in `context` dict; user-prompts mode + generated mode both covered; failure modes (generation fail, batch fail, persist fail) mark row appropriately; partial-mode classification (`prompts_failed > 0`); EARLY-FAILURE path (no `project_description` + no `prompts` + no `provider`) returns `GeneratorResult(terminal_status='failed', ...)` instead of raising |
| Router shim regression — probes | 12 | All 8 SSE event types byte-identical via snapshot, `ProbeListResponse` shape, `ProbeRunResult` shape (incl. all 4 status values), error reasons preserved (`link_repo_first`, `probe_not_found`, `invalid_request`), pagination envelope, **race-free SSE subscription** (subscription registered before generator start; race test asserts events fire after subscribe), `event_bus.subscribe_for_run` filter correctness (other-run events excluded; `taxonomy_changed`/`optimization_created` without `run_id` excluded) |
| Router shim regression — seed | 8 | `SeedOutput` shape with additive `run_id`, all 4 status values returnable in `SeedOutput.status`, new GET endpoints work, error semantics, sync timing, EARLY-FAILURE path returns HTTP 200 with `status='failed'` (no exception leaked), `duration_ms` None-safe when `completed_at` is None, all required `aggregate`/`taxonomy_delta` keys populated by generator |
| MCP tool regression | 6 | `synthesis_probe` + `synthesis_seed` result schemas validated against MCP SDK validator; dispatch routes through RunOrchestrator; error paths preserved; additive `run_id` field accepted by MCP client (verify against actual MCP SDK behavior, not Pydantic input-side `extra='ignore'`) |
| New `/api/runs` endpoints | 6 | Pagination envelope, mode filter, status filter (all 4 values), project_id filter, ordering, 404 on miss |
| GC sweep | 4 | `_gc_orphan_runs(db) -> int` detects both modes, respects TTL, returns rowcount (no internal commit — participates in `run_startup_gc._do_sweep` batched commit, matching existing `_gc_orphan_probe_runs` convention), lifespan startup invokes the new helper inside the same `_do_sweep` work_fn |
| Cross-process correlation | 4 | `current_run_id` threads through to taxonomy events; `current_probe_id is current_run_id` object-identity preserved (verify the existing `tests/test_probe_service_module_split_v0_4_17.py` still passes); ContextVar inheritance through `asyncio.create_task` confirmed (children of `RunOrchestrator.run` see correct `run_id`); ContextVar `reset()` does NOT propagate to in-flight children (documented behavior) |
| ProbeRun ORM removal — refactor coverage | 6 | All 51 `ProbeRun` references in `backend/tests/` updated to `RunRow` (8 affected test files: `test_probe_run_model`, `test_probe_router`, `test_probe_service`, `test_probe_service_queue_routing`, `test_probe_service_module_split_v0_4_17`, etc.); reads asserting `.scope` / `.commit_sha` (now in JSON) updated to JSON-extract; reads asserting `_set_probe_status` updated to `_set_run_status` |

**Total: ~94 new/updated tests** (revised up from prior ~73 estimate after spec review V1 noted ProbeRun removal touches 51 reference sites in tests + the additive coverage required for 4-status invariant, missing event types, race-free SSE subscription, ContextVar inheritance, and early-failure paths). Existing probe + seed test suite continues to pass (modulo refactor of asserting-on-removed-symbols tests; surface-level snapshot tests byte-identical).

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
- `backend/app/models.py` — add `RunRow`. **`ProbeRun` retained as a backward-compat alias for PR1.** Two implementation options, with their tradeoffs:
  - **(a) SQLAlchemy single-table-inheritance subclass** with polymorphic discriminator on `mode`. `ProbeRun` defined as `class ProbeRun(RunRow): __mapper_args__ = {"polymorphic_identity": "topic_probe"}`. `select(ProbeRun)` automatically filters to `WHERE mode='topic_probe'`. **Recommended** for correctness — guarantees that any legacy code path doing `select(ProbeRun)` cannot accidentally read seed-mode rows.
  - **(b) Plain Python alias** `ProbeRun = RunRow`. Simpler, but `select(ProbeRun)` returns ALL `run_row` rows including seed-mode. **In PR1 specifically** this is safe because PR1 does NOT add any `mode='seed_agent'` rows — the seed router/MCP path doesn't dispatch through `RunOrchestrator` until PR2, so no seed_agent rows can exist when option (b) is in effect. PR2 deletes the alias entirely before any seed_agent rows are written.

  **Implementation picks (b)** with a custom `__init__` that extracts legacy `scope` / `commit_sha` kwargs into `topic_probe_meta` JSON. Reasons: (1) PR1 has zero `seed_agent` rows since the seed dispatch doesn't go through `RunOrchestrator` until PR2 — so the lack of select-time filter is safe transient state; (2) option (a) STI requires the parent `RunRow` to either set a `polymorphic_identity` (introducing a fictitious `mode` value that never appears at runtime) or remain abstract (which conflicts with `RunRow` being directly queryable for the unified `/api/runs` endpoint); (3) option (b) with the legacy-kwarg-extracting `__init__` keeps `probe_service.py` untouched in PR1 (existing `ProbeRun(scope=..., commit_sha=...)` calls keep working). PR2 deletes the alias before any `seed_agent` row exists.
- `backend/app/services/probe_common.py` — rebind: `current_probe_id` and `current_run_id` are both names for the same `ContextVar` object (declared in `probe_common.py:33-35`, the canonical home). Object-identity test in `tests/test_probe_service_module_split_v0_4_17.py` continues to pass.
- `backend/app/services/probe_event_correlation.py` — already re-imports `current_probe_id` transitively via `probe_service` (line 8: `from app.services.probe_service import current_probe_id`). The chain `probe_event_correlation → probe_service → probe_common` resolves to the same `ContextVar` object after the section-5.3 rebind; no change needed in PR1.
- `backend/app/services/gc.py` — `_gc_orphan_runs(db: AsyncSession) -> int` (replaces `_gc_orphan_probe_runs` with the **same shape** as today's helper at gc.py:259 — `(db) -> int`, no internal commit, caller commits at sweep boundary). `RUN_ORPHAN_TTL_HOURS` constant + `PROBE_ORPHAN_TTL_HOURS` backward-compat alias.
- `backend/app/main.py` — lifespan startup composes `_gc_orphan_runs` inside the existing `_do_sweep` work_fn at `gc.py:72-87` (no signature change at the call site; just swap the helper name). Also registers `RunOrchestrator` on `app.state.run_orchestrator`.
- `backend/app/routers/probes.py` — **no changes in PR1**. Reads on `ProbeRun` ORM continue to work via the alias.
- `backend/app/services/probe_service.py` — **no changes in PR1**. Class shape + dispatch path preserved. Refactored into `TopicProbeGenerator` in PR2 alongside the alias removal.
- Existing tests: any test importing `ProbeRun` continues to work via the alias. **No mechanical sed in PR1.** PR2 audit + rewrite all 51 reference sites when the alias is removed.

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
- `backend/app/models.py` — **remove `ProbeRun` alias** (declared in PR1). All in-tree consumers must reference `RunRow` directly after this PR.
- `backend/app/routers/probes.py` — POST dispatches through `RunOrchestrator` (caller-mints-`run_id` race-free pattern from section 6.2); SSE response constructed via `event_bus.subscribe_for_run`. Reads serialize from `RunRow WHERE mode='topic_probe'`.
- `backend/app/routers/seed.py` — POST dispatches through `RunOrchestrator`; new GET endpoints added; early-failure path preserves HTTP 200 with `status='failed'`.
- `backend/app/tools/probe.py` — MCP dispatch through `RunOrchestrator`.
- `backend/app/tools/seed.py` — MCP dispatch through `RunOrchestrator`; orchestration logic and 4 `seed_*` taxonomy decision events moved to `SeedAgentGenerator` in PR1; this PR removes the orchestration body and the in-tool decision events.
- `backend/app/services/probe_service.py` — class deleted (logic lives in `TopicProbeGenerator` added in PR1); module-level helpers from P2 Path A retained at `probe_common.py`/`probe_phases.py`/`probe_phase_5.py`.
- `backend/app/services/seed_orchestrator.py` — `SeedOrchestrator.generate()` retained as the generator's internal call; class shrinks to just the generation phase.
- `backend/app/services/batch_orchestrator.py:240-258` — `seed_batch_progress` payload gains `run_id` (one-line additive).
- `backend/app/services/event_bus.py` — add `subscribe_for_run(run_id)` method (filtered iterator + 500ms ring buffer; section 6.2).
- `frontend/src/lib/components/taxonomy/SeedModal.svelte` — one-line additive: filter `seed_batch_progress` events by `run_id` when modal is showing a specific run.
- 51 ProbeRun reference sites in `backend/tests/` rewritten to use `RunRow` (8 affected test files; mechanical for symbol renames, surgical for `.scope`/`.commit_sha` access patterns now in JSON).
- All shim regression tests (categories 6–9 of section 9, ~36 tests).

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
| Cancellation shielding misalignment — moving `asyncio.shield()` from inside `_run_impl` to `RunOrchestrator.run` could let SSE response close before row marked failed | Medium | Test: simulate `asyncio.CancelledError` mid-run, assert row status reaches `failed` before SSE response closes. Verify in PR2 tests (where the shim refactor lands). |
| `current_probe_id` ContextVar object identity preservation — the existing test `tests/test_probe_service_module_split_v0_4_17.py:27` asserts `legacy.current_probe_id is common.current_probe_id` (object identity). Section 5.3's rebind preserves identity by aliasing the SAME object under both names from `probe_common.py` (canonical home). | Medium | Spec implementation: rebind ONLY in `probe_common.py`. Do NOT reassign `current_probe_id` in `probe_event_correlation.py` or `probe_service.py` (that would break identity). Test: re-run the existing object-identity test, plus add an explicit assertion that `from probe_common import current_run_id, current_probe_id; assert current_run_id is current_probe_id`. |
| Frontend filter race — historical `SeedModal` subscribed to `/api/events` after `POST /api/seed` returns; first event could fire before subscription registered. **Probe path mitigated by section 6.2's caller-mints-`run_id` pattern** (subscribe BEFORE dispatch). Seed path: POST is synchronous so events fire before POST returns; modal subscribes after the run completes — no live updates needed during the call. T4's history UI subscribes after row creation visible via `/api/runs/{id}` poll. | Low | Server-side: `event_bus.subscribe_for_run` includes a 500ms ring-buffer replay (section 6.2). Test: simulated late subscription receives recent events from buffer. |
| Backfill performance on large `probe_run` tables — production `probe_run` retention is unbounded today (no TTL on completed/failed rows; only `_gc_orphan_probe_runs` for stuck-running rows >1h). Tables can grow without explicit cleanup. | Low | Migration `INSERT...SELECT` is single-statement and atomic in SQLite. Benchmark: 100k synthetic rows in spec implementation tests. **Document the unbounded-retention behavior** in spec implementation; add a follow-up Exploring entry for retention TTL on completed runs if benchmark shows degradation. |
| MCP tool result schema validation — additive `run_id` field on `SeedOutput` (and its presence in `synthesis_probe`'s response) could be rejected by strict MCP client validators. **Pydantic's `extra='ignore'` is for INPUT validation; output models always emit declared fields.** Risk is on the MCP client side: which validator does Claude Code / VSCode bridge use, and does it accept extra fields in tool result schemas? | Low | Spec implementation: verify the MCP SDK behavior empirically against Claude Code + VSCode bridge before PR2 ships. The MCP `structured_output=True` schema is generated from the Pydantic model; if the SDK strict-validates the schema declaration (not just input parsing), the additive field is the schema declaration itself. Add a test that calls both tools through the actual MCP transport and asserts no validation errors. |
| `current_run_id` ContextVar inheritance through `asyncio.create_task` — `RunOrchestrator.run` sets the ContextVar before invoking the generator. Generators that spawn inner tasks (e.g., `probe_service.py` lines 839, 850, 904) inherit the ContextVar value at task-creation time. When `RunOrchestrator.run`'s `finally` block calls `current_run_id.reset(token)`, in-flight inner tasks STILL see the old value (their context is a snapshot). This is correct Python behavior but not obvious. | Documentational | No code mitigation needed — Python's `contextvars` module documents this snapshot semantics. Test: explicit assertion that a child task spawned during `RunOrchestrator.run` still sees `current_run_id` correctly even after parent's `finally` block runs. Document this property in the `RunOrchestrator` docstring. |
| Migration partial-completion — if `INSERT...SELECT` fails after `create_table` succeeds, both tables exist. Default Alembic transactional model on SQLite depends on `transaction_per_migration` flag. | Medium | Spec implementation: verify `transaction_per_migration=True` in `backend/alembic/env.py`; set it explicitly in PR1 if not present. Idempotency guard uses matched-state check (`run_row exists ⊕ probe_run exists`) — partial state aborts with operator-readable error rather than silently proceeding. See section 4.2. |

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
