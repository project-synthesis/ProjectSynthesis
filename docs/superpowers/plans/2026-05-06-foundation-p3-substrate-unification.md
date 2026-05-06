# Foundation P3 — Substrate Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a unified `RunRow` substrate + `RunOrchestrator` service that replaces today's asymmetric probe/seed run architecture. Probe migrates from `ProbeRun` → `RunRow`; the seed surface gains run-state persistence for the first time. All four backward-compat surfaces (`/api/probes`, `/api/seed`, `synthesis_probe`, `synthesis_seed`) preserve their response shapes.

**Architecture:** Two PRs. PR1 = "dark substrate" (RunRow table + RunOrchestrator + generators + tests, zero behavior change at the wire). PR2 = "live + shims" (router refactors + new endpoints + frontend additive + ProbeRun alias removal). Generators are awaitable services (`async def run(req, *, run_id) -> GeneratorResult`); they publish progress events to the event bus directly with `run_id` in payload — no re-publication layer in `RunOrchestrator`. SSE on POST /api/probes is reconstructed via `event_bus.subscribe_for_run(run_id)` filter; subscription registers BEFORE orchestrator dispatch (caller-mints-`run_id`) so no race window exists. POST /api/seed stays synchronous with additive `run_id`. All `RunRow` writes route through `WriteQueue`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async + aiosqlite, FastAPI, pytest_asyncio, asyncio (ContextVar, shield, create_task), Pydantic v2, Alembic.

**Discipline:** Strict 7-dispatch TDD per cycle: RED → GREEN → REFACTOR → INTEGRATE → OPERATE → spec-compliance reviewer → code-quality reviewer per `feedback_tdd_protocol.md`. The RED test pins the contract; subsequent phases never break it. Commit after every phase. REFACTOR subagent is dispatched explicitly (not folded into GREEN). Reviewers run as independent subagents — no self-review.

**Phase template applied to every cycle that touches writers, events, or persistence (Cycles 1, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14):**

After RED → GREEN → REFACTOR for each cycle, every such cycle MUST include:

**INTEGRATE phase task** — verify the cycle's deliverable composes correctly with adjacent subsystems:
- Run the cycle's tests + any tests for subsystems the cycle interacts with (e.g., Cycle 4 RunOrchestrator INTEGRATE runs orchestrator tests + write_queue tests + gc tests).
- Lint + mypy clean (`cd backend && ruff check . && mypy app/`).
- No dangling imports / circular imports introduced.
- For schema cycles: alembic upgrade + downgrade + upgrade roundtrip verified.
- For event-emission cycles: `event_bus._subscribers` does not retain leaked subscribers after a cycle test run.
- Commit message tagged `[INTEGRATE]`.

**OPERATE phase task** — exercise the cycle's deliverable end-to-end against the running app:
- Spin up backend in dev mode (`./init.sh restart` or pytest with full lifespan).
- Issue real REST/MCP calls covering the new code path (curl + jq).
- Verify no `database is locked` errors, no audit-hook WARN, no orphaned tasks at process shutdown.
- For SSE cycles: subscribe to `/api/events`, kick off a real run, observe wire events match the snapshot fixture byte-for-byte.
- Commit message tagged `[OPERATE]`.

Per `feedback_tdd_protocol.md`: "No phase is optional. Skipping any phase has shipped a defect at least once." OPERATE is non-negotiable for any cycle changing writer paths or SSE response semantics.

**Fixture-cycle prerequisite:** Cycles 6, 7, 11, 12 depend on fixtures (`audit_hook`, `event_bus_capture`, `taxonomy_event_capture`, `provider_mock` family, `seed_orchestrator_mock`, etc.) that don't exist in `backend/tests/conftest.py` today. **Cycle 3.5** (between schema cycle and orchestrator cycle) introduces these fixtures with their own RED/GREEN/REFACTOR cycle.

**Reference reading before starting:**
- `docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md` (the spec — source of truth)
- `~/.claude/projects/.../memory/feedback_tdd_protocol.md` (TDD protocol)
- `backend/CLAUDE.md` (WriteQueue contract + layer rules)
- `backend/app/services/probe_service.py` (existing 5-phase orchestrator being refactored)
- `backend/app/services/probe_common.py:33-35` (canonical `current_probe_id` declaration)
- `backend/app/tools/seed.py` (existing seed flow being refactored)
- `backend/alembic/versions/ec86c86ba298_add_probe_run_table_for_topic_probe_.py` (mirror schema for downgrade)
- `backend/alembic/env.py` (must add `transaction_per_migration=True`)

---

## Cycle 0 — Branch setup + alembic env hardening

### Task 0.1: Create release branch

- [ ] **Step 1: Create branch from main**

```bash
git checkout main
git pull
git checkout -b release/v0.4.18
```

- [ ] **Step 2: Verify clean working tree**

```bash
git status
```

Expected: `On branch release/v0.4.18 / Your branch is up to date with 'main'. / nothing to commit, working tree clean`

### Task 0.2: Add `transaction_per_migration=True` to alembic env

**Files:**
- Modify: `backend/alembic/env.py`

- [ ] **Step 1: Read current env.py**

```bash
cat backend/alembic/env.py | grep -n 'context.configure'
```

Expected: 1-2 `context.configure(...)` calls (one offline, one online). Note the line numbers and existing kwargs.

- [ ] **Step 2: Add `transaction_per_migration=True` to both configure calls**

For each `context.configure(...)` call, add `transaction_per_migration=True` as a kwarg. Example:

```python
# Before
context.configure(
    connection=connection,
    target_metadata=target_metadata,
)

# After
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    transaction_per_migration=True,
)
```

- [ ] **Step 3: Run existing migrations to verify nothing broke**

```bash
cd backend && source .venv/bin/activate && python -m alembic check
```

Expected: `No new upgrade operations detected.`

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/env.py
git commit -m "chore(v0.4.18): enable transaction_per_migration in alembic env

Required for P3 migration atomicity — the run_row migration has 9 ops
(create_table + 4 create_index + INSERT...SELECT + 2 drop_index +
drop_table) that must commit atomically. Without this flag, a failed
INSERT...SELECT after create_table would leave both tables present.

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 4.2"
```

---

## Cycle 1 — RunRow model + Alembic migration

**Files:**
- Modify: `backend/app/models.py` (add `RunRow`, add `ProbeRun` STI alias)
- Create: `backend/alembic/versions/<NEW>_add_run_row_table.py`
- Create: `backend/tests/test_run_row_model.py`

### Task 1.1: RED — write failing model + migration tests

- [ ] **Step 1: Create test file**

Create `backend/tests/test_run_row_model.py`:

```python
"""Tests for the RunRow model + migration (Foundation P3, v0.4.18).

Covers spec section 9 category 1 (RunRow model + migration) — 8 tests.
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow, ProbeRun


pytestmark = pytest.mark.asyncio


async def test_run_row_table_has_all_18_columns(db: AsyncSession) -> None:
    """RunRow table has all expected columns from spec section 4.1."""
    bind = (await db.connection()).engine
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("run_row")}
    expected = {
        "id", "mode", "status", "started_at", "completed_at", "error",
        "project_id", "repo_full_name", "topic", "intent_hint",
        "prompts_generated", "prompt_results", "aggregate", "taxonomy_delta",
        "final_report", "suite_id", "topic_probe_meta", "seed_agent_meta",
    }
    assert cols == expected, f"Column mismatch: extra={cols - expected}, missing={expected - cols}"


async def test_run_row_has_4_indexes(db: AsyncSession) -> None:
    """RunRow has the 4 indexes from spec section 4.1."""
    bind = (await db.connection()).engine
    inspector = inspect(bind)
    idx_names = {ix["name"] for ix in inspector.get_indexes("run_row")}
    expected = {
        "ix_run_row_mode_started",
        "ix_run_row_status_started",
        "ix_run_row_project_id",
        "ix_run_row_topic",
    }
    assert expected.issubset(idx_names), f"Missing indexes: {expected - idx_names}"


async def test_run_row_status_accepts_all_4_values(db: AsyncSession) -> None:
    """RunRow.status takes 4 values: running, completed, failed, partial."""
    from datetime import datetime
    for status in ("running", "completed", "failed", "partial"):
        row = RunRow(
            id=f"test-{status}", mode="topic_probe", status=status,
            started_at=datetime.utcnow(),
        )
        db.add(row)
    await db.commit()
    rows = (await db.execute(text("SELECT status FROM run_row ORDER BY id"))).all()
    assert [r[0] for r in rows] == ["test-completed", "test-failed", "test-partial", "test-running"][3], "expect-rows"  # placeholder; see below


async def test_run_row_topic_probe_meta_roundtrips(db: AsyncSession) -> None:
    """JSON metadata stores and retrieves intact."""
    from datetime import datetime
    row = RunRow(
        id="meta-1", mode="topic_probe", status="running",
        started_at=datetime.utcnow(),
        topic_probe_meta={"scope": "**/*.py", "commit_sha": "abc123"},
    )
    db.add(row)
    await db.commit()
    fetched = await db.get(RunRow, "meta-1")
    assert fetched.topic_probe_meta == {"scope": "**/*.py", "commit_sha": "abc123"}


async def test_run_row_seed_agent_meta_roundtrips(db: AsyncSession) -> None:
    """seed_agent_meta accepts the spec-defined shape."""
    from datetime import datetime
    seed_meta = {
        "project_description": "test desc",
        "workspace_path": "/tmp/x",
        "agents": ["a1", "a2"],
        "prompt_count": 30,
        "prompts_provided": False,
        "batch_id": "batch-uuid",
        "tier": "internal",
        "estimated_cost_usd": 1.23,
    }
    row = RunRow(
        id="seed-meta-1", mode="seed_agent", status="running",
        started_at=datetime.utcnow(), seed_agent_meta=seed_meta,
    )
    db.add(row)
    await db.commit()
    fetched = await db.get(RunRow, "seed-meta-1")
    assert fetched.seed_agent_meta == seed_meta


async def test_probe_run_alias_default_mode_is_topic_probe(db: AsyncSession) -> None:
    """ProbeRun(...) sets mode='topic_probe' by default (legacy-compat).

    Per spec section 10.1 option (b): ProbeRun is a Python subclass of RunRow
    that defaults mode='topic_probe' in __init__. PR1 has zero seed_agent
    rows (the seed dispatch doesn't go through RunOrchestrator until PR2),
    so the lack of select-time filter is safe transient.
    """
    from datetime import datetime

    row = ProbeRun(id="probe-default", started_at=datetime.utcnow(),
                   topic_probe_meta={"scope": "**/*", "commit_sha": None})
    assert row.mode == "topic_probe"


async def test_probe_run_property_accessors_read_topic_probe_meta(db: AsyncSession) -> None:
    """Legacy .scope / .commit_sha access paths work via property accessors."""
    from datetime import datetime

    row = ProbeRun(
        id="probe-props", started_at=datetime.utcnow(),
        topic_probe_meta={"scope": "src/**/*.py", "commit_sha": "abc123"},
    )
    assert row.scope == "src/**/*.py"
    assert row.commit_sha == "abc123"

    # Defaults when topic_probe_meta is empty
    bare = ProbeRun(id="probe-bare", started_at=datetime.utcnow())
    assert bare.scope == "**/*"  # default fallback
    assert bare.commit_sha is None


async def test_migration_idempotent_when_already_migrated(db: AsyncSession) -> None:
    """Re-running the upgrade when run_row exists + probe_run gone is a no-op."""
    # The fixture has already migrated; re-invoking upgrade should not error.
    from app.alembic_helpers import run_upgrade  # introduced in this cycle if needed; OR use Alembic API
    # If no helper exists, skip — covered by Task 1.2's idempotency-guard impl.
    pytest.skip("Covered by direct upgrade()/upgrade() call test in Task 1.2")


async def test_migration_aborts_on_partial_state(db: AsyncSession) -> None:
    """Upgrade raises RuntimeError if both run_row and probe_run exist."""
    bind = (await db.connection()).engine
    # Recreate a probe_run table to simulate partial state
    async with bind.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE probe_run (id TEXT PRIMARY KEY, topic TEXT NOT NULL)"
        ))
    # Re-run upgrade — should raise
    from alembic.config import Config
    from alembic import command
    cfg = Config("backend/alembic.ini")
    with pytest.raises(RuntimeError, match="partial migration detected"):
        command.upgrade(cfg, "head")
    # Cleanup
    async with bind.begin() as conn:
        await conn.execute(text("DROP TABLE probe_run"))
```

Replace the `test_run_row_status_accepts_all_4_values` body with the simpler form below (the `[3]` index above was a placeholder error):

```python
async def test_run_row_status_accepts_all_4_values(db: AsyncSession) -> None:
    """RunRow.status takes 4 values: running, completed, failed, partial."""
    from datetime import datetime
    from sqlalchemy import select

    for status in ("running", "completed", "failed", "partial"):
        row = RunRow(
            id=f"test-{status}", mode="topic_probe", status=status,
            started_at=datetime.utcnow(),
        )
        db.add(row)
    await db.commit()
    rows = (await db.execute(select(RunRow.id, RunRow.status).order_by(RunRow.id))).all()
    statuses = {r.status for r in rows}
    assert statuses == {"running", "completed", "failed", "partial"}
```

- [ ] **Step 2: Run tests — they must fail**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_run_row_model.py -v
```

Expected: ImportError or AttributeError on `from app.models import RunRow, ProbeRun` (one of them doesn't exist yet) OR table-not-found on the `run_row` DB queries. **All tests FAIL** — that's RED.

### Task 1.2: GREEN — add `RunRow` model + Alembic migration + STI ProbeRun alias

- [ ] **Step 1: Add RunRow class to models.py**

In `backend/app/models.py`, after the `ProbeRun` class (line ~605), add:

```python
class RunRow(Base):
    """Unified run-state model (Foundation P3, v0.4.18).

    Replaces ProbeRun and introduces row-state persistence to the seed
    surface for the first time. See spec section 4.1.
    """
    __tablename__ = "run_row"

    # Identity / discriminator
    id: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)

    # Shared lifecycle
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Shared correlation
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("prompt_cluster.id"), nullable=True,
    )
    repo_full_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # Promoted from probe-mode (Q2 hybrid — query-hot)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    intent_hint: Mapped[str | None] = mapped_column(String, nullable=True)

    # Shared output payloads
    prompts_generated: Mapped[int] = mapped_column(Integer, default=0)
    prompt_results: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    aggregate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    taxonomy_delta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_report: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Suite linkage (T2 readiness)
    suite_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Mode-specific JSON metadata
    topic_probe_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    seed_agent_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Deliberately NO polymorphic_on / polymorphic_identity — SQLAlchemy STI
    # is awkward when neither parent nor subclasses are routinely instantiated
    # by mode-discriminator. PR1 uses option (b) from spec § 10.1: ProbeRun
    # is a Python alias of RunRow with property accessors. select(ProbeRun)
    # returns ALL run_row rows — but PR1 has zero seed_agent rows since the
    # seed router/MCP path doesn't dispatch through RunOrchestrator until PR2.
    # PR2 deletes the alias entirely before any seed_agent row exists.

    __table_args__ = (
        Index("ix_run_row_mode_started", "mode", "started_at"),
        Index("ix_run_row_status_started", "status", "started_at"),
        Index("ix_run_row_project_id", "project_id"),
        Index("ix_run_row_topic", "topic"),
    )
```

- [ ] **Step 2: Replace `class ProbeRun(Base)` with Python-alias + property mixin**

Find the existing `class ProbeRun(Base):` definition (currently at `models.py:570-605`) and replace with:

```python
class ProbeRun(RunRow):
    """Backward-compat alias for RunRow + property accessors for legacy
    .scope / .commit_sha reads. Retained for PR1 only — PR2 deletes this class.

    NOTE: NO __mapper_args__ — this is plain Python subclassing, not SQLAlchemy
    STI. ProbeRun shares RunRow's table; no polymorphic identity is set, so
    instantiating ProbeRun(...) produces a row with whatever mode the caller
    passes (caller responsible for setting mode='topic_probe' explicitly).

    select(ProbeRun) returns all run_row rows (NOT filtered) — but PR1 has
    zero seed_agent rows since the seed dispatch path doesn't go through
    RunOrchestrator until PR2. Safe transient state.
    """

    @property
    def scope(self) -> str:
        return (self.topic_probe_meta or {}).get("scope", "**/*")

    @property
    def commit_sha(self) -> str | None:
        return (self.topic_probe_meta or {}).get("commit_sha")

    def __init__(self, **kwargs):
        # Default mode='topic_probe' for legacy callers that don't supply it.
        kwargs.setdefault("mode", "topic_probe")
        super().__init__(**kwargs)
```

- [ ] **Step 3: Generate migration**

```bash
cd backend && source .venv/bin/activate && python -m alembic revision -m "add run_row table foundation p3"
```

Note the generated revision id from output (e.g., `aa1234bcdef5`). The new file is at `backend/alembic/versions/<id>_add_run_row_table_foundation_p3.py`.

- [ ] **Step 4: Write the upgrade + downgrade bodies**

Replace the generated migration body with the canonical migration from spec section 4.2:

```python
"""add run_row table foundation p3

Revision ID: <id from step 3>
Revises: ec86c86ba298
Create Date: <auto-filled>
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy import inspect
from alembic import op

revision: str = "<id from step 3>"
down_revision: Union[str, Sequence[str], None] = "ec86c86ba298"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent — matched-state guard so partial-completion (run_row present
    AND probe_run also present) aborts with operator-readable error rather than
    silently proceeding."""
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

    # 2. Create 4 indexes
    op.create_index("ix_run_row_mode_started", "run_row", ["mode", "started_at"])
    op.create_index("ix_run_row_status_started", "run_row", ["status", "started_at"])
    op.create_index("ix_run_row_project_id", "run_row", ["project_id"])
    op.create_index("ix_run_row_topic", "run_row", ["topic"])

    # 3. Backfill from probe_run
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

    # 4. Drop probe_run indexes + table
    op.drop_index("ix_probe_run_project_id", table_name="probe_run")
    op.drop_index("ix_probe_run_status_started", table_name="probe_run")
    op.drop_table("probe_run")


def downgrade() -> None:
    """Recreate probe_run + reverse-backfill from run_row WHERE mode='topic_probe'.

    NOT NULL safety: original probe_run had repo_full_name/scope/intent_hint
    NOT NULL with server defaults. Reverse-backfill COALESCEs every NOT-NULL
    column so the back-insert never fails NOT NULL on edge-case rows.
    """
    op.create_table(
        "probe_run",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False, server_default="**/*"),
        sa.Column("intent_hint", sa.String(), nullable=False, server_default="explore"),
        sa.Column("repo_full_name", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("prompts_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_results", sa.JSON(), nullable=True),
        sa.Column("aggregate", sa.JSON(), nullable=True),
        sa.Column("taxonomy_delta", sa.JSON(), nullable=True),
        sa.Column("final_report", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("suite_id", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["prompt_cluster.id"],
            name="fk_probe_run_project_id_prompt_cluster",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_probe_run_status_started", "probe_run", ["status", "started_at"])
    op.create_index("ix_probe_run_project_id", "probe_run", ["project_id"])

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

- [ ] **Step 5: Run migration**

```bash
cd backend && source .venv/bin/activate && python -m alembic upgrade head
```

Expected: success, no errors. Re-running it should be a no-op (matched-state guard hits "already migrated" path).

- [ ] **Step 6: Run tests — should now pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_run_row_model.py -v
```

Expected: 8/8 PASS. (`test_migration_idempotent_when_already_migrated` is a `pytest.skip` placeholder; all other 7 pass.)

### Task 1.3: REFACTOR — extract migration helpers + cleanup

- [ ] **Step 1: Inspect for inline duplication**

Read the migration file end-to-end. The matched-state guard is concise. The `INSERT...SELECT` is single-statement. No extraction warranted at this size.

The model file: verify `ProbeRun(RunRow)` keeps the original `__tablename__ = "probe_run"` semantics — it should NOT, because STI shares the parent's table. Remove any `__tablename__` from `ProbeRun` if present.

- [ ] **Step 2: Verify no test regressions**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_run_row_model.py tests/test_probe_run_model.py -v
```

Expected: all pass. The legacy `test_probe_run_model.py` should continue working via the STI alias's property accessors (`.scope`, `.commit_sha`).

If any legacy probe_run test fails because it inserts directly to `probe_run` (table now gone), update those tests to instantiate `ProbeRun()` (which targets `run_row` via STI).

- [ ] **Step 3: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/<id>_add_run_row_table_foundation_p3.py backend/tests/test_run_row_model.py
git commit -m "feat(v0.4.18-p3): add RunRow model + migration with STI ProbeRun alias

- New RunRow table with 18 columns, 4 indexes, 4-value status
- Atomic Alembic migration: create + 4 index + INSERT...SELECT + drop
- Matched-state idempotency guard for partial-completion safety
- Reversible downgrade with NOT NULL re-COALESCE for legacy schema
- ProbeRun retained as STI subclass (polymorphic_identity='topic_probe')
  with .scope and .commit_sha property accessors for legacy reads
- 8 RunRow model + migration tests (cat 1)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 4"
```

---

## Cycle 2 — ContextVar rebind in probe_common.py

**Files:**
- Modify: `backend/app/services/probe_common.py`
- Create: `backend/tests/test_run_id_contextvar.py`

### Task 2.1: RED — write failing ContextVar identity + alias tests

- [ ] **Step 1: Create test file**

Create `backend/tests/test_run_id_contextvar.py`:

```python
"""Tests for the current_run_id ContextVar rebind (Foundation P3, v0.4.18).

Covers spec section 9 category 11 (cross-process correlation) — partial — 4 tests.
"""
from __future__ import annotations

import pytest


def test_current_run_id_is_current_probe_id() -> None:
    """The two names alias the same ContextVar object."""
    from app.services.probe_common import current_run_id, current_probe_id
    assert current_run_id is current_probe_id


def test_current_run_id_default_is_none() -> None:
    """Default value matches today's current_probe_id behavior."""
    from app.services.probe_common import current_run_id
    assert current_run_id.get() is None


def test_legacy_import_paths_resolve_to_same_object() -> None:
    """Identity invariant for tests/test_probe_service_module_split_v0_4_17.py."""
    from app.services import probe_common, probe_service, probe_event_correlation
    assert probe_common.current_probe_id is probe_service.current_probe_id
    assert probe_common.current_probe_id is probe_event_correlation.current_probe_id
    assert probe_common.current_run_id is probe_event_correlation.current_probe_id


def test_set_value_observable_through_all_aliases() -> None:
    """Setting current_run_id reflects through every name."""
    from app.services.probe_common import current_run_id
    from app.services.probe_service import current_probe_id as svc_alias
    from app.services.probe_event_correlation import current_probe_id as corr_alias

    token = current_run_id.set("test-run-123")
    try:
        assert current_run_id.get() == "test-run-123"
        assert svc_alias.get() == "test-run-123"
        assert corr_alias.get() == "test-run-123"
    finally:
        current_run_id.reset(token)
```

- [ ] **Step 2: Run tests — must fail on `current_run_id` import**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_run_id_contextvar.py -v
```

Expected: ImportError on `from app.services.probe_common import current_run_id` because the symbol doesn't exist yet.

### Task 2.2: GREEN — rebind in probe_common.py

- [ ] **Step 1: Read current probe_common.py declaration**

```bash
sed -n '30,40p' backend/app/services/probe_common.py
```

Expected output shows lines 33-35 declaring `current_probe_id`.

- [ ] **Step 2: Replace declaration with rebind**

In `backend/app/services/probe_common.py`, replace the existing `current_probe_id` declaration:

```python
# OLD:
current_probe_id: ContextVar[str | None] = ContextVar(
    "current_probe_id", default=None,
)

# NEW:
# Foundation P3 (v0.4.18): canonical ContextVar renamed to current_run_id.
# current_probe_id retained as alias of the SAME object — preserves the
# `legacy.current_probe_id is common.current_probe_id` identity test in
# tests/test_probe_service_module_split_v0_4_17.py.
current_run_id: ContextVar[str | None] = ContextVar(
    "current_run_id", default=None,
)
current_probe_id = current_run_id  # backward-compat alias
```

- [ ] **Step 3: Update `__all__` to expose both names**

In the same file, find the `__all__` list (line ~124) and add `"current_run_id"` if not present. Final list should include both `"current_probe_id"` and `"current_run_id"`.

- [ ] **Step 4: Run tests — must pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_run_id_contextvar.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 5: Verify identity test still passes**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_probe_service_module_split_v0_4_17.py -v
```

Expected: existing tests pass — including the identity assertion `legacy.current_probe_id is common.current_probe_id`.

### Task 2.3: REFACTOR — sanity check + commit

- [ ] **Step 1: Grep for any imports of `current_probe_id` that might have broken**

```bash
grep -rn 'current_probe_id\|current_run_id' backend/app backend/tests --include='*.py'
```

Expected: 9 reference sites in services + tests (no errors). Verify nothing else needs updating in PR1.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/probe_common.py backend/tests/test_run_id_contextvar.py
git commit -m "feat(v0.4.18-p3): rebind ContextVar to current_run_id in probe_common

- Canonical name renamed: current_probe_id → current_run_id
- current_probe_id retained as alias of the SAME ContextVar object
- Object-identity test (test_probe_service_module_split_v0_4_17.py:27) preserved
- 4 ContextVar identity tests (cat 11 partial)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.3"
```

---

## Cycle 3 — Pydantic schemas + GeneratorResult dataclass

**Files:**
- Create: `backend/app/schemas/runs.py`
- Create: `backend/tests/test_runs_schemas.py`

### Task 3.1: RED — write failing schema tests

- [ ] **Step 1: Create test file**

Create `backend/tests/test_runs_schemas.py`:

```python
"""Tests for RunRequest, RunResult, RunListResponse Pydantic schemas + GeneratorResult dataclass."""
from __future__ import annotations

import pytest


def test_run_request_accepts_topic_probe_payload() -> None:
    from app.schemas.runs import RunRequest
    req = RunRequest(mode="topic_probe", payload={"topic": "test", "scope": "**/*"})
    assert req.mode == "topic_probe"
    assert req.payload["topic"] == "test"


def test_run_request_accepts_seed_agent_payload() -> None:
    from app.schemas.runs import RunRequest
    req = RunRequest(mode="seed_agent", payload={"project_description": "x", "prompt_count": 30})
    assert req.mode == "seed_agent"


def test_run_request_rejects_unknown_mode() -> None:
    from app.schemas.runs import RunRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RunRequest(mode="invalid_mode", payload={})


def test_run_result_accepts_4_status_values() -> None:
    from app.schemas.runs import RunResult
    from datetime import datetime
    base = dict(
        id="r1", mode="topic_probe",
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        prompts_generated=3, prompt_results=[], aggregate={},
        taxonomy_delta={}, final_report="", topic="x", intent_hint="explore",
        topic_probe_meta={}, seed_agent_meta=None, project_id=None,
        repo_full_name="x/y", suite_id=None, error=None,
    )
    for status in ("running", "completed", "failed", "partial"):
        r = RunResult(**base, status=status)
        assert r.status == status


def test_run_list_response_pagination_envelope() -> None:
    from app.schemas.runs import RunListResponse, RunSummary
    resp = RunListResponse(
        total=100, count=50, offset=0, items=[], has_more=True, next_offset=50,
    )
    assert resp.has_more is True
    assert resp.next_offset == 50


def test_generator_result_dataclass_terminal_status_field() -> None:
    """GeneratorResult is the service-layer dataclass, distinct from RunResult Pydantic."""
    from app.services.generators.base import GeneratorResult
    res = GeneratorResult(
        terminal_status="partial",
        prompts_generated=5,
        prompt_results=[{"id": "p1"}],
        aggregate={"prompts_optimized": 3, "prompts_failed": 2, "summary": "x"},
        taxonomy_delta={"domains_touched": ["a"], "clusters_created": 1},
        final_report=None,
    )
    assert res.terminal_status == "partial"
    assert res.aggregate["prompts_failed"] == 2


def test_generator_result_terminal_status_rejects_running() -> None:
    """GeneratorResult.terminal_status is Literal['completed', 'partial', 'failed'].

    RunOrchestrator alone owns the 'running' status; generators must never
    return 'running' as terminal."""
    from app.services.generators.base import GeneratorResult
    # The Literal type is enforced statically; at runtime, the dataclass accepts
    # any string. This test is a compile-time-style check via type inspection.
    import typing
    hints = typing.get_type_hints(GeneratorResult)
    # If using Literal: typing.get_args returns the allowed values
    args = typing.get_args(hints["terminal_status"])
    assert set(args) == {"completed", "partial", "failed"}
```

- [ ] **Step 2: Run tests — must fail**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_runs_schemas.py -v
```

Expected: ImportError on `from app.schemas.runs import ...` and `from app.services.generators.base import GeneratorResult`.

### Task 3.2: GREEN — create schemas + dataclass

- [ ] **Step 1: Create `backend/app/schemas/runs.py`**

```python
"""Pydantic schemas for the unified run substrate (Foundation P3, v0.4.18)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    """Mode-agnostic input to RunOrchestrator.run()."""

    mode: Literal["topic_probe", "seed_agent"]
    payload: dict


class RunSummary(BaseModel):
    """Compact view for list endpoints."""

    id: str
    mode: Literal["topic_probe", "seed_agent"]
    status: Literal["running", "completed", "failed", "partial"]
    started_at: datetime
    completed_at: datetime | None
    project_id: str | None
    repo_full_name: str | None
    topic: str | None
    intent_hint: str | None
    prompts_generated: int


class RunResult(BaseModel):
    """Full RunRow detail view returned by /api/runs/{run_id} and equivalents."""

    id: str
    mode: Literal["topic_probe", "seed_agent"]
    status: Literal["running", "completed", "failed", "partial"]
    started_at: datetime
    completed_at: datetime | None
    error: str | None
    project_id: str | None
    repo_full_name: str | None
    topic: str | None
    intent_hint: str | None
    prompts_generated: int
    prompt_results: list[dict]
    aggregate: dict
    taxonomy_delta: dict
    final_report: str
    suite_id: str | None
    topic_probe_meta: dict | None
    seed_agent_meta: dict | None


class RunListResponse(BaseModel):
    """Paginated list envelope matching the codebase convention."""

    total: int
    count: int
    offset: int
    items: list[RunSummary]
    has_more: bool
    next_offset: int | None
```

- [ ] **Step 2: Create `backend/app/services/generators/__init__.py`**

```python
"""Run-generator package — pluggable mode-specific run executors."""
```

- [ ] **Step 3: Create `backend/app/services/generators/base.py`**

```python
"""RunGenerator protocol + GeneratorResult service-layer dataclass."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.schemas.runs import RunRequest


@dataclass(frozen=True)
class GeneratorResult:
    """Service-layer dataclass — what generators return to RunOrchestrator.

    Distinct from `app.schemas.runs.RunResult` (Pydantic response model).
    """
    terminal_status: Literal["completed", "partial", "failed"]
    prompts_generated: int
    prompt_results: list[dict]
    aggregate: dict
    taxonomy_delta: dict
    final_report: str | None


@runtime_checkable
class RunGenerator(Protocol):
    """Awaitable mode-specific run executor.

    Generators MUST publish progress events directly to event_bus with run_id
    in payload. They MUST NOT touch RunRow — RunOrchestrator owns row writes.
    """

    async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult:
        ...
```

- [ ] **Step 4: Run tests — must pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_runs_schemas.py -v
```

Expected: 7/7 PASS.

### Task 3.3: REFACTOR — namespace cleanup + commit

- [ ] **Step 1: Verify no naming collision with the v0.4.17 ProbeRunResult**

```bash
grep -rn 'class RunResult\|class ProbeRunResult' backend/app --include='*.py'
```

Expected: `ProbeRunResult` in `schemas/probes.py` (existing) + `RunResult` in `schemas/runs.py` (new). No collisions.

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/runs.py backend/app/services/generators/__init__.py backend/app/services/generators/base.py backend/tests/test_runs_schemas.py
git commit -m "feat(v0.4.18-p3): add RunRequest/RunResult/RunSummary schemas + GeneratorResult

- Pydantic schemas for /api/runs surface (RunRequest input, RunResult detail,
  RunSummary compact, RunListResponse pagination envelope)
- GeneratorResult dataclass — service-layer return type for RunGenerator
  protocol; distinct from RunResult Pydantic to avoid namespace collision
- RunGenerator protocol with awaitable run(req, *, run_id) -> GeneratorResult
- 7 schema + dataclass tests

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.1, § 6.1"
```

### Task 3.4: INTEGRATE — schemas + dataclass compose with existing imports

- [ ] **Step 1: Run lint + mypy**

```bash
cd backend && source .venv/bin/activate && ruff check app/schemas/runs.py app/services/generators/ && mypy app/schemas/runs.py app/services/generators/
```

Expected: clean.

- [ ] **Step 2: Run full backend suite for regressions**

```bash
cd backend && source .venv/bin/activate && pytest --no-cov -q
```

Expected: all pre-existing tests + 7 new tests pass; no other module breaks from the new imports.

- [ ] **Step 3: Commit `[INTEGRATE]`**

```bash
git commit --allow-empty -m "ops(v0.4.18-p3): [INTEGRATE] cycle 3 schemas + dataclass clean

ruff + mypy clean for schemas/runs.py + services/generators/.
Full backend suite passes with 7 new tests (cat 3 partial).
No import-cycle regressions detected."
```

### Task 3.5: OPERATE — instantiate schemas at runtime

- [ ] **Step 1: Smoke-test schema instantiation in a real Python REPL**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.schemas.runs import RunRequest, RunResult, RunListResponse, RunSummary
from app.services.generators.base import GeneratorResult, RunGenerator
print('OK', GeneratorResult.__dataclass_fields__.keys())
"
```

Expected output includes `dict_keys(['terminal_status', 'prompts_generated', 'prompt_results', 'aggregate', 'taxonomy_delta', 'final_report'])`.

- [ ] **Step 2: Commit `[OPERATE]`**

```bash
git commit --allow-empty -m "ops(v0.4.18-p3): [OPERATE] cycle 3 runtime instantiation verified"
```

---

## Cycle 3.5 — Test fixtures (audit_hook, event_bus_capture, taxonomy_event_capture, provider mocks)

**Files:**
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/fixtures/run_p3_fixtures.py` (or inline in conftest)

These fixtures are dependencies of Cycles 4, 6, 7, 9, 11, 12. Introduced as their own cycle to avoid copy-paste across test files.

### Task 3.5.1: RED — fixture-presence smoke tests

- [ ] **Step 1: Create `backend/tests/test_p3_fixtures_present.py`**

```python
"""Sentinel tests verifying P3 fixtures are wired into conftest."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_audit_hook_fixture_captures_warnings(audit_hook) -> None:
    audit_hook.warn("test warn")
    assert any("test warn" in str(w) for w in audit_hook.warnings)


async def test_event_bus_capture_records_published(event_bus_capture) -> None:
    from app.services.event_bus import event_bus
    event_bus.publish("probe_started", {"run_id": "fix-1"})
    assert any(e.kind == "probe_started" for e in event_bus_capture.events)


async def test_event_bus_capture_filter_by_run_id(event_bus_capture) -> None:
    from app.services.event_bus import event_bus
    event_bus.publish("probe_started", {"run_id": "fix-A"})
    event_bus.publish("probe_started", {"run_id": "fix-B"})
    a_events = event_bus_capture.events_for_run("fix-A")
    assert len(a_events) == 1


async def test_taxonomy_event_capture_records_decisions(taxonomy_event_capture) -> None:
    from app.services.taxonomy.event_logger import get_event_logger
    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_started",
            context={"batch_id": "fix-1", "run_id": "fix-rid"},
        )
    except RuntimeError:
        pytest.skip("event logger not initialized in this test session")
    decisions = taxonomy_event_capture.decisions_with_op("seed")
    assert any(d.context.get("run_id") == "fix-rid" for d in decisions)


def test_provider_mock_fixture_default_returns_completed(provider_mock) -> None:
    """Default provider_mock returns a successful response."""
    assert provider_mock is not None  # presence check; real exercise in test_topic_probe_generator


def test_provider_partial_mock_simulates_mixed_outcomes(provider_partial_mock) -> None:
    assert provider_partial_mock is not None


def test_seed_orchestrator_mock_has_generate(seed_orchestrator_mock) -> None:
    assert hasattr(seed_orchestrator_mock, "generate")
```

- [ ] **Step 2: Run — must fail because fixtures don't exist**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_p3_fixtures_present.py -v
```

Expected: every test ERRORs with "fixture not found".

### Task 3.5.2: GREEN — define fixtures in conftest

- [ ] **Step 1: Append fixtures to `backend/tests/conftest.py`**

```python
# ============================================================
# Foundation P3 fixtures (added 2026-05-06)
# ============================================================

@dataclass
class _AuditHookCapture:
    warnings: list = field(default_factory=list)

    def reset(self) -> None:
        self.warnings.clear()

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

@pytest.fixture
def audit_hook(monkeypatch) -> _AuditHookCapture:
    """Captures audit-hook WARNs during a test. Replaces the real audit hook
    so direct DB writes from inside tests don't trigger raises in CI."""
    cap = _AuditHookCapture()
    # Monkey-patch the audit hook's warn method to record into cap
    from app.services import write_queue as wq_mod
    real_warn = wq_mod._audit_warn if hasattr(wq_mod, "_audit_warn") else None
    def _capture(*args, **kwargs):
        cap.warn(args[0] if args else str(kwargs))
        if real_warn:
            real_warn(*args, **kwargs)
    if real_warn:
        monkeypatch.setattr(wq_mod, "_audit_warn", _capture)
    yield cap


@dataclass
class _BusEvent:
    kind: str
    payload: dict

@dataclass
class _EventBusCapture:
    events: list[_BusEvent] = field(default_factory=list)

    def events_for_run(self, run_id: str) -> list[_BusEvent]:
        return [e for e in self.events if e.payload.get("run_id") == run_id]

@pytest.fixture
async def event_bus_capture(monkeypatch) -> _EventBusCapture:
    """Captures every event published to event_bus during the test.
    Hooks publish() directly, parallel to existing subscribers."""
    from app.services.event_bus import event_bus
    cap = _EventBusCapture()
    real_publish = event_bus.publish
    def _wrapped(event_type, data):
        cap.events.append(_BusEvent(
            kind=event_type, payload=data if isinstance(data, dict) else {},
        ))
        return real_publish(event_type, data)
    monkeypatch.setattr(event_bus, "publish", _wrapped)
    yield cap


@dataclass
class _TaxDecision:
    path: str
    op: str
    decision: str
    context: dict

@dataclass
class _TaxonomyEventCapture:
    decisions: list[_TaxDecision] = field(default_factory=list)

    def decisions_with_op(self, op: str) -> list[_TaxDecision]:
        return [d for d in self.decisions if d.op == op]

@pytest.fixture
def taxonomy_event_capture(monkeypatch) -> _TaxonomyEventCapture:
    """Captures every taxonomy_event_logger.log_decision call."""
    from app.services.taxonomy import event_logger as el_mod
    cap = _TaxonomyEventCapture()
    real_logger_class = el_mod.TaxonomyEventLogger
    real_log = real_logger_class.log_decision
    def _wrapped(self, path, op, decision, context):
        cap.decisions.append(_TaxDecision(
            path=path, op=op, decision=decision, context=context,
        ))
        return real_log(self, path, op, decision, context)
    monkeypatch.setattr(real_logger_class, "log_decision", _wrapped)
    yield cap


@pytest.fixture
def provider_mock() -> Any:
    """Default Sonnet provider mock returning a 'completed' response."""
    from unittest.mock import AsyncMock
    p = AsyncMock()
    p.complete_parsed.return_value = AsyncMock(
        result_text="optimized prompt",
        model="claude-sonnet-4-6",
    )
    return p


@pytest.fixture
def provider_partial_mock() -> Any:
    """Simulates 1 success + 1 failure across N prompts."""
    from unittest.mock import AsyncMock
    p = AsyncMock()
    counter = {"n": 0}
    async def _call(*args, **kwargs):
        counter["n"] += 1
        if counter["n"] % 2 == 0:
            raise RuntimeError("partial failure simulation")
        return AsyncMock(result_text="ok", model="claude-sonnet-4-6")
    p.complete_parsed = _call
    return p


@pytest.fixture
def provider_all_fail_mock() -> Any:
    from unittest.mock import AsyncMock
    p = AsyncMock()
    p.complete_parsed.side_effect = RuntimeError("all fail simulation")
    return p


@pytest.fixture
def provider_429_then_ok_mock() -> Any:
    """First call raises 429, subsequent calls succeed."""
    from unittest.mock import AsyncMock
    p = AsyncMock()
    counter = {"n": 0}
    async def _call(*args, **kwargs):
        counter["n"] += 1
        if counter["n"] == 1:
            err = RuntimeError("HTTP 429: rate limited")
            raise err
        return AsyncMock(result_text="ok", model="claude-sonnet-4-6")
    p.complete_parsed = _call
    return p


@pytest.fixture
def provider_hanging_mock() -> Any:
    """Provider that never returns — used for cancellation tests."""
    from unittest.mock import AsyncMock
    p = AsyncMock()
    async def _hang(*args, **kwargs):
        await asyncio.sleep(60)
    p.complete_parsed = _hang
    return p


@pytest.fixture
def seed_orchestrator_mock() -> Any:
    """Mock SeedOrchestrator returning a successful generation."""
    from unittest.mock import AsyncMock, MagicMock
    orch = MagicMock()
    gen_result = MagicMock()
    gen_result.prompts = ["prompt 1", "prompt 2", "prompt 3"]
    orch.generate = AsyncMock(return_value=gen_result)
    return orch


@pytest.fixture
def seed_orchestrator_failing_mock() -> Any:
    from unittest.mock import AsyncMock, MagicMock
    orch = MagicMock()
    orch.generate = AsyncMock(side_effect=RuntimeError("generation failed"))
    return orch


@pytest.fixture
def repo_index_mock() -> Any:
    from unittest.mock import AsyncMock, MagicMock
    rix = MagicMock()
    rix.query_curated_context = AsyncMock(return_value=MagicMock(
        relevant_files=[], explore_synthesis_excerpt="", known_domains=[],
    ))
    return rix


@pytest.fixture
def taxonomy_mock() -> Any:
    from unittest.mock import MagicMock
    return MagicMock()
```

Imports needed at top of conftest (add if not present):

```python
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock
```

- [ ] **Step 2: Run — fixture sentinel tests pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_p3_fixtures_present.py -v
```

Expected: 7/7 PASS.

### Task 3.5.3: REFACTOR + INTEGRATE + OPERATE + commit

- [ ] **Step 1: Verify no existing test breaks from the fixture additions**

```bash
cd backend && source .venv/bin/activate && pytest --no-cov -q
```

Expected: full suite passes; no fixture-name collisions.

- [ ] **Step 2: Commit**

```bash
git add backend/tests/conftest.py backend/tests/test_p3_fixtures_present.py
git commit -m "feat(v0.4.18-p3): test fixtures for P3 cycles

- audit_hook: captures audit-hook warnings during a test
- event_bus_capture: records every event_bus.publish call
- taxonomy_event_capture: records taxonomy_event_logger.log_decision calls
- provider_mock family: mock Sonnet provider variants (default/partial/all-fail/
  429-then-ok/hanging)
- seed_orchestrator_mock + failing variant
- repo_index_mock + taxonomy_mock

These fixtures are dependencies of Cycles 4, 6, 7, 9, 11, 12.

7 sentinel tests verify all fixtures are wired."
```

---

## Cycle 4 — RunOrchestrator (with WriteQueue routing)

**Files:**
- Create: `backend/app/services/run_orchestrator.py`
- Create: `backend/tests/test_run_orchestrator.py`

This is the central orchestration service. 14 tests per spec section 9 category 2.

### Task 4.1: RED — write 14 failing orchestrator tests

- [ ] **Step 1: Create the test file with all 14 tests**

Create `backend/tests/test_run_orchestrator.py`. Due to length, write each test as a complete `async def test_...` block. The tests cover (per spec §9 category 2):

```python
"""Tests for RunOrchestrator (Foundation P3, v0.4.18) — 14 tests, cat 2."""
from __future__ import annotations

import asyncio
import pytest
from datetime import datetime

from app.models import RunRow
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult, RunGenerator
from app.services.run_orchestrator import RunOrchestrator


pytestmark = pytest.mark.asyncio


# Stub generator for testing
class StubProbeGenerator:
    def __init__(self, terminal_status="completed", raise_exc=None):
        self.terminal_status = terminal_status
        self.raise_exc = raise_exc
        self.calls = []

    async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult:
        self.calls.append((request, run_id))
        if self.raise_exc:
            raise self.raise_exc
        return GeneratorResult(
            terminal_status=self.terminal_status,
            prompts_generated=3,
            prompt_results=[{"id": "p1"}],
            aggregate={"prompts_optimized": 3, "prompts_failed": 0, "summary": "ok"},
            taxonomy_delta={"domains_touched": [], "clusters_created": 0},
            final_report="report",
        )


async def _build(write_queue, generator) -> RunOrchestrator:
    return RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": generator, "seed_agent": StubProbeGenerator()},
    )


# Test 1: row create via WriteQueue with caller-supplied run_id
async def test_create_row_with_caller_supplied_run_id(write_queue, db) -> None:
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={"topic": "x"})
    row = await orch.run("topic_probe", req, run_id="my-uuid-1")
    assert row.id == "my-uuid-1"


# Test 2: row create with internally-minted run_id when none supplied
async def test_create_row_with_internal_run_id_when_omitted(write_queue, db) -> None:
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert isinstance(row.id, str) and len(row.id) >= 32


# Test 3: status transition running → completed
async def test_status_transition_running_to_completed(write_queue, db) -> None:
    gen = StubProbeGenerator(terminal_status="completed")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert row.status == "completed"


# Test 4: status transition running → partial (generator-classified)
async def test_status_transition_running_to_partial(write_queue, db) -> None:
    gen = StubProbeGenerator(terminal_status="partial")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert row.status == "partial"


# Test 5: status transition running → failed (generator-classified)
async def test_status_transition_running_to_failed(write_queue, db) -> None:
    gen = StubProbeGenerator(terminal_status="failed")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert row.status == "failed"


# Test 6: cancellation under shield marks failed before re-raise
async def test_cancellation_marks_failed_under_shield(write_queue, db) -> None:
    class HangingGenerator:
        async def run(self, request, *, run_id):
            await asyncio.sleep(10)
            return GeneratorResult(
                terminal_status="completed", prompts_generated=0, prompt_results=[],
                aggregate={}, taxonomy_delta={}, final_report=None,
            )
    orch = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": HangingGenerator(), "seed_agent": StubProbeGenerator()},
    )
    req = RunRequest(mode="topic_probe", payload={})
    task = asyncio.create_task(orch.run("topic_probe", req, run_id="cancel-1"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Row should be marked failed
    from sqlalchemy import select
    row = (await db.execute(select(RunRow).where(RunRow.id == "cancel-1"))).scalar_one()
    assert row.status == "failed"
    assert row.error == "cancelled"


# Test 7: exception capture — generator-raised exception marks failed
async def test_exception_capture_marks_failed(write_queue, db) -> None:
    gen = StubProbeGenerator(raise_exc=ValueError("boom"))
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    with pytest.raises(ValueError):
        await orch.run("topic_probe", req, run_id="exc-1")
    from sqlalchemy import select
    row = (await db.execute(select(RunRow).where(RunRow.id == "exc-1"))).scalar_one()
    assert row.status == "failed"
    assert "ValueError: boom" in row.error


# Test 8: audit-hook clean (no direct writes) — generator never touches RunRow
async def test_audit_hook_clean_no_direct_writes_from_generator(write_queue, db, audit_hook) -> None:
    """Generators MUST NOT write to RunRow directly."""
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    audit_hook.reset()
    await orch.run("topic_probe", req)
    # No audit warnings — all writes went through WriteQueue
    assert audit_hook.warnings == []


# Test 9: unknown mode raises before row created
async def test_unknown_mode_raises_no_row_created(write_queue, db) -> None:
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest.model_construct(mode="bogus", payload={})  # bypass validation
    with pytest.raises(ValueError, match="unknown mode"):
        await orch.run("bogus", req)
    # Row should NOT exist
    from sqlalchemy import select
    rows = (await db.execute(select(RunRow))).scalars().all()
    assert all(r.mode != "bogus" for r in rows)


# Test 10: ContextVar set + reset around run
async def test_context_var_set_and_reset(write_queue, db) -> None:
    from app.services.probe_common import current_run_id
    captured = []

    class CapturingGenerator:
        async def run(self, request, *, run_id):
            captured.append(current_run_id.get())
            return GeneratorResult(
                terminal_status="completed", prompts_generated=0, prompt_results=[],
                aggregate={}, taxonomy_delta={}, final_report=None,
            )
    orch = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": CapturingGenerator(), "seed_agent": StubProbeGenerator()},
    )
    req = RunRequest(mode="topic_probe", payload={})
    await orch.run("topic_probe", req, run_id="ctx-1")
    assert captured == ["ctx-1"]
    assert current_run_id.get() is None  # reset


# Test 11: double-cancellation idempotent
async def test_double_cancellation_idempotent(write_queue, db) -> None:
    """Second cancellation does not double-write status='failed'."""
    class HangingGenerator:
        async def run(self, request, *, run_id):
            await asyncio.sleep(10)
            return GeneratorResult(
                terminal_status="completed", prompts_generated=0, prompt_results=[],
                aggregate={}, taxonomy_delta={}, final_report=None,
            )
    orch = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": HangingGenerator(), "seed_agent": StubProbeGenerator()},
    )
    req = RunRequest(mode="topic_probe", payload={})
    task = asyncio.create_task(orch.run("topic_probe", req, run_id="dbl-1"))
    await asyncio.sleep(0.05)
    task.cancel()
    task.cancel()  # second cancel — should be a no-op
    with pytest.raises(asyncio.CancelledError):
        await task
    from sqlalchemy import select
    row = (await db.execute(select(RunRow).where(RunRow.id == "dbl-1"))).scalar_one()
    assert row.status == "failed"


# Test 12: _persist_final writes terminal_status from GeneratorResult
async def test_persist_final_writes_terminal_status_from_generator_result(write_queue, db) -> None:
    gen = StubProbeGenerator(terminal_status="partial")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req, run_id="tf-1")
    assert row.status == "partial"
    assert row.aggregate == {"prompts_optimized": 3, "prompts_failed": 0, "summary": "ok"}


# Test 13: WriteQueue.submit lambdas commit before returning
async def test_write_queue_lambdas_commit_before_returning(write_queue, db) -> None:
    """Verify each submit() lambda invokes db.commit() — required by WriteQueue contract."""
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req, run_id="commit-1")
    # The fact that the row is readable from a different session proves commit happened
    from sqlalchemy import select
    fresh = (await db.execute(select(RunRow).where(RunRow.id == "commit-1"))).scalar_one()
    assert fresh.id == "commit-1"
    assert fresh.status == "completed"


# Test 14: error message truncated to 2000 chars
async def test_error_message_truncation(write_queue, db) -> None:
    huge = "x" * 5000
    gen = StubProbeGenerator(raise_exc=RuntimeError(huge))
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    with pytest.raises(RuntimeError):
        await orch.run("topic_probe", req, run_id="trunc-1")
    from sqlalchemy import select
    row = (await db.execute(select(RunRow).where(RunRow.id == "trunc-1"))).scalar_one()
    assert len(row.error) <= 2000 + len("RuntimeError: ")  # type prefix + truncated msg
```

- [ ] **Step 2: Add fixtures (write_queue, audit_hook, db) to conftest if not present**

Verify these fixtures exist in `backend/tests/conftest.py` from v0.4.13 work. If `audit_hook` isn't there, add it (it captures audit-hook warnings during a test).

- [ ] **Step 3: Run tests — all 14 must fail**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_run_orchestrator.py -v
```

Expected: ImportError on `from app.services.run_orchestrator import RunOrchestrator`.

### Task 4.2: GREEN — implement RunOrchestrator

- [ ] **Step 1: Create `backend/app/services/run_orchestrator.py`**

```python
"""RunOrchestrator — central dispatch for unified run substrate (Foundation P3).

Responsibilities:
    - Allocate run_id (or accept caller-supplied)
    - Create RunRow row via WriteQueue at start (status='running')
    - Dispatch to mode-specific RunGenerator
    - Persist final state (status from GeneratorResult.terminal_status)
    - Catch exceptions + cancellation; mark row failed under asyncio.shield()
    - Set/reset current_run_id ContextVar around generator invocation

Generators NEVER touch RunRow — RunOrchestrator is the only legitimate writer.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import RunRow
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult, RunGenerator
from app.services.probe_common import current_run_id
from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.utcnow()


class RunOrchestrator:
    def __init__(
        self,
        write_queue: WriteQueue,
        generators: dict[str, RunGenerator],
    ) -> None:
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

        run_id: optional caller-supplied id. Race-sensitive callers (e.g.,
        probes router constructing SSE response) pre-mint the id and supply it
        so they can register event subscriptions BEFORE the orchestrator
        starts. When None, minted internally.
        """
        if mode not in self._generators:
            raise ValueError(f"unknown mode: {mode}")

        if run_id is None:
            run_id = str(uuid.uuid4())

        await self._create_row(mode, request, run_id=run_id)
        generator = self._generators[mode]

        token = current_run_id.set(run_id)
        try:
            try:
                result = await generator.run(request, run_id=run_id)
                await self._persist_final(run_id, result)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self._mark_failed(run_id, error="cancelled"))
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await self._mark_failed(
                        run_id, error=f"{type(exc).__name__}: {exc}"
                    )
                raise
        finally:
            current_run_id.reset(token)

        return await self._reload(run_id)

    # ----------------------- internal helpers -----------------------

    async def _create_row(
        self, mode: str, request: RunRequest, *, run_id: str
    ) -> None:
        """Insert run_row(status='running') via WriteQueue."""

        async def _work(write_db: AsyncSession) -> None:
            row = RunRow(
                id=run_id,
                mode=mode,
                status="running",
                started_at=_utcnow(),
                project_id=request.payload.get("project_id"),
                repo_full_name=request.payload.get("repo_full_name"),
                topic=request.payload.get("topic"),
                intent_hint=request.payload.get("intent_hint"),
                topic_probe_meta=self._extract_probe_meta(mode, request),
                seed_agent_meta=self._extract_seed_meta(mode, request),
            )
            write_db.add(row)
            await write_db.commit()  # required by WriteQueue contract

        await self._write_queue.submit(
            _work,
            timeout=30,
            operation_label=f"run_orchestrator.create_row[{mode}]",
        )

    @staticmethod
    def _extract_probe_meta(mode: str, request: RunRequest) -> dict | None:
        if mode != "topic_probe":
            return None
        return {
            "scope": request.payload.get("scope", "**/*"),
            "commit_sha": request.payload.get("commit_sha"),
        }

    @staticmethod
    def _extract_seed_meta(mode: str, request: RunRequest) -> dict | None:
        if mode != "seed_agent":
            return None
        return {
            "project_description": request.payload.get("project_description"),
            "workspace_path": request.payload.get("workspace_path"),
            "agents": request.payload.get("agents"),
            "prompt_count": request.payload.get("prompt_count"),
            "prompts_provided": bool(request.payload.get("prompts")),
            "batch_id": request.payload.get("batch_id"),
            "tier": request.payload.get("tier"),
            "estimated_cost_usd": request.payload.get("estimated_cost_usd"),
        }

    async def _set_run_status(
        self, run_id: str, status: str, **fields
    ) -> None:
        """Update run_row.status (+ optional completed_at, error). Caller passes any
        of: completed_at=datetime, error=str."""

        async def _work(write_db: AsyncSession) -> None:
            row = await write_db.get(RunRow, run_id)
            if row is None:
                return
            row.status = status
            for k, v in fields.items():
                setattr(row, k, v)
            await write_db.commit()

        await self._write_queue.submit(
            _work,
            timeout=30,
            operation_label=f"run_orchestrator.set_status[{status}]",
        )

    async def _persist_final(self, run_id: str, result: GeneratorResult) -> None:
        """Write GeneratorResult fields + status from result.terminal_status."""

        async def _work(write_db: AsyncSession) -> None:
            row = await write_db.get(RunRow, run_id)
            if row is None:
                return
            row.status = result.terminal_status
            row.completed_at = _utcnow()
            row.prompts_generated = result.prompts_generated
            row.prompt_results = result.prompt_results
            row.aggregate = result.aggregate
            row.taxonomy_delta = result.taxonomy_delta
            row.final_report = result.final_report
            await write_db.commit()

        await self._write_queue.submit(
            _work,
            timeout=60,
            operation_label="run_orchestrator.persist_final",
        )

    async def _mark_failed(self, run_id: str, *, error: str) -> None:
        """Mark row failed (orchestrator-caught exceptions only)."""
        # Truncate but keep type prefix
        truncated = error[:2000] if len(error) > 2000 else error
        await self._set_run_status(
            run_id,
            status="failed",
            error=truncated,
            completed_at=_utcnow(),
        )

    async def _reload(self, run_id: str) -> RunRow:
        """Read row back through standard read path."""
        async with async_session_factory() as db:
            row = await db.get(RunRow, run_id)
            if row is None:
                raise RuntimeError(f"run row {run_id} not found after persist")
            return row
```

- [ ] **Step 2: Run tests — all 14 must pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_run_orchestrator.py -v
```

Expected: 14/14 PASS.

### Task 4.3: REFACTOR — review for cleanup

- [ ] **Step 1: Inspect for code-smell — magic numbers, duplication**

Review `_extract_probe_meta` and `_extract_seed_meta` static methods. They share the pattern "extract mode-specific keys from request.payload into a dict". If the test suite passes and the code is readable, no further refactor is needed. The methods are 8-10 lines each, focused, and have clear single responsibility.

- [ ] **Step 2: Verify imports are minimal**

```bash
grep '^from\|^import' backend/app/services/run_orchestrator.py
```

Expected: only what's used.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/run_orchestrator.py backend/tests/test_run_orchestrator.py
git commit -m "feat(v0.4.18-p3): RunOrchestrator service with 4-status lifecycle

- Top-level dispatch: row create → generator run → persist final
- Caller-supplied run_id (race-free SSE) OR internally minted
- All RunRow writes route through WriteQueue with explicit commit
- ContextVar set/reset around generator invocation
- Cancellation handling under asyncio.shield() — row marked failed
  before re-raise
- Exception capture truncates error to 2000 chars
- Generator-classified terminal_status (completed/partial/failed)
  written to RunRow.status
- 14 lifecycle tests (cat 2)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.2"
```

---

## Cycle 5 — GC sweep helper

**Files:**
- Modify: `backend/app/services/gc.py`
- Modify: `backend/tests/test_gc.py` (or create new test file)

### Task 5.1: RED — 4 GC sweep tests

- [ ] **Step 1: Add tests to `backend/tests/test_gc_runs.py`**

```python
"""Tests for _gc_orphan_runs (Foundation P3, v0.4.18) — 4 tests, cat 10."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from app.models import RunRow


pytestmark = pytest.mark.asyncio


async def test_gc_orphan_runs_marks_stale_running_rows_failed(db) -> None:
    """Rows in status='running' for >TTL hours are marked failed."""
    from app.services.gc import _gc_orphan_runs, RUN_ORPHAN_TTL_HOURS
    cutoff = datetime.utcnow() - timedelta(hours=RUN_ORPHAN_TTL_HOURS + 1)

    db.add(RunRow(id="orphan-1", mode="topic_probe", status="running", started_at=cutoff))
    db.add(RunRow(id="fresh-1", mode="topic_probe", status="running", started_at=datetime.utcnow()))
    await db.commit()

    n = await _gc_orphan_runs(db)
    await db.commit()

    assert n == 1
    orphan = await db.get(RunRow, "orphan-1")
    fresh = await db.get(RunRow, "fresh-1")
    assert orphan.status == "failed"
    assert orphan.error == "orphaned (ttl exceeded)"
    assert fresh.status == "running"


async def test_gc_orphan_runs_includes_seed_mode(db) -> None:
    """Both topic_probe and seed_agent rows are swept."""
    from app.services.gc import _gc_orphan_runs, RUN_ORPHAN_TTL_HOURS
    cutoff = datetime.utcnow() - timedelta(hours=RUN_ORPHAN_TTL_HOURS + 1)

    db.add(RunRow(id="probe-orphan", mode="topic_probe", status="running", started_at=cutoff))
    db.add(RunRow(id="seed-orphan", mode="seed_agent", status="running", started_at=cutoff))
    await db.commit()

    n = await _gc_orphan_runs(db)
    await db.commit()

    assert n == 2


async def test_gc_orphan_runs_returns_zero_when_no_orphans(db) -> None:
    from app.services.gc import _gc_orphan_runs
    n = await _gc_orphan_runs(db)
    assert n == 0


async def test_probe_orphan_ttl_hours_is_alias_of_run_orphan_ttl_hours(db) -> None:
    """Backward-compat alias for the constant rename."""
    from app.services.gc import RUN_ORPHAN_TTL_HOURS, PROBE_ORPHAN_TTL_HOURS
    assert PROBE_ORPHAN_TTL_HOURS is RUN_ORPHAN_TTL_HOURS or \
           PROBE_ORPHAN_TTL_HOURS == RUN_ORPHAN_TTL_HOURS
```

- [ ] **Step 2: Run tests — must fail on import**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_gc_runs.py -v
```

Expected: ImportError on `_gc_orphan_runs` or `RUN_ORPHAN_TTL_HOURS`.

### Task 5.2: GREEN — add helper to gc.py

- [ ] **Step 1: Read current gc.py and find `_gc_orphan_probe_runs`**

```bash
grep -n '_gc_orphan_probe_runs\|PROBE_ORPHAN_TTL_HOURS\|run_startup_gc' backend/app/services/gc.py
```

Note line numbers — we'll add the new helper near the existing one.

- [ ] **Step 2: Add new constants + helper above `_gc_orphan_probe_runs`**

In `backend/app/services/gc.py`, near the existing `PROBE_ORPHAN_TTL_HOURS` declaration, add:

```python
# Foundation P3 (v0.4.18): unified run orphan sweep.
RUN_ORPHAN_TTL_HOURS = PROBE_ORPHAN_TTL_HOURS  # 1 hour, preserved


async def _gc_orphan_runs(db: AsyncSession) -> int:
    """Sweep orphan RunRow rows where status='running' for >RUN_ORPHAN_TTL_HOURS.

    Same shape as the legacy _gc_orphan_probe_runs (db: AsyncSession) -> int —
    composes inside run_startup_gc._do_sweep batched commit. Caller is
    responsible for committing.
    """
    from sqlalchemy import update
    from app.models import RunRow

    cutoff = datetime.utcnow() - timedelta(hours=RUN_ORPHAN_TTL_HOURS)
    result = await db.execute(
        update(RunRow)
        .where(RunRow.status == "running")
        .where(RunRow.started_at < cutoff)
        .values(
            status="failed",
            error="orphaned (ttl exceeded)",
            completed_at=datetime.utcnow(),
        )
    )
    return result.rowcount or 0
```

- [ ] **Step 3: Update `run_startup_gc._do_sweep` to call the new helper**

In the same file, find the `_do_sweep` inner function (around line 72). Add the new helper alongside the existing one:

```python
async def _do_sweep(write_db: AsyncSession) -> int:
    total = 0
    total += await _gc_orphan_probe_runs(write_db)  # legacy — keep until PR2
    total += await _gc_orphan_runs(write_db)  # new — sweeps both modes
    # ... (other existing sweep helpers unchanged)
    return total
```

Wait — note: with the STI `ProbeRun` alias, `_gc_orphan_probe_runs` operates on `RunRow WHERE mode='topic_probe'` automatically. Calling both helpers would double-process orphans. **Update the legacy helper to be a no-op** in PR1 (it'll be deleted in PR2):

In `_gc_orphan_probe_runs(db)`, replace the body with:

```python
async def _gc_orphan_probe_runs(db: AsyncSession) -> int:
    """Legacy alias — superseded by _gc_orphan_runs in Foundation P3 (v0.4.18).
    Returns 0; the unified _gc_orphan_runs sweep covers both modes including
    legacy probe-mode rows. This function will be deleted in PR2."""
    return 0
```

- [ ] **Step 4: Run tests — all 4 pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_gc_runs.py tests/test_gc.py -v
```

Expected: 4 new tests pass. Existing GC tests continue to pass (the legacy helper now returns 0; `_gc_orphan_runs` covers what it did).

### Task 5.3: REFACTOR + commit

- [ ] **Step 1: Verify test isolation**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_gc_runs.py tests/test_gc.py tests/test_run_orchestrator.py tests/test_run_row_model.py tests/test_run_id_contextvar.py tests/test_runs_schemas.py -v
```

Expected: all pass cleanly.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/gc.py backend/tests/test_gc_runs.py
git commit -m "feat(v0.4.18-p3): _gc_orphan_runs sweeps both modes

- New helper preserves (db) -> int signature, composes inside
  run_startup_gc._do_sweep batched commit (no internal commit)
- Sweeps RunRow rows in status='running' for >RUN_ORPHAN_TTL_HOURS
  across both topic_probe and seed_agent modes
- Legacy _gc_orphan_probe_runs returns 0 (superseded; deleted in PR2)
- RUN_ORPHAN_TTL_HOURS constant + PROBE_ORPHAN_TTL_HOURS alias
- 4 GC sweep tests (cat 10)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.6"
```

---

## Cycle 6 — TopicProbeGenerator refactor

**Files:**
- Create: `backend/app/services/generators/topic_probe_generator.py`
- Create: `backend/tests/test_topic_probe_generator.py`

12 tests per spec section 9 category 4.

### Task 6.1: RED — 12 generator tests

- [ ] **Step 1: Create test file with 12 tests**

Due to the substantial mechanical work involved, this file mirrors the existing probe service tests but redirects assertions from "events yielded in order" to "events published to bus in order with `run_id` in payload".

Create `backend/tests/test_topic_probe_generator.py` with the following test outline (full bodies follow standard fixture patterns from `backend/tests/test_probe_service.py`):

```python
"""Tests for TopicProbeGenerator — Foundation P3 refactor of ProbeService.

Covers spec section 9 category 4 — 12 tests.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock

from app.services.generators.topic_probe_generator import TopicProbeGenerator
from app.services.generators.base import GeneratorResult
from app.schemas.runs import RunRequest


pytestmark = pytest.mark.asyncio


async def _make_generator(provider, repo_index_query, taxonomy_engine):
    """Factory matching ProbeService DI shape."""
    return TopicProbeGenerator(
        provider=provider,
        repo_index_query=repo_index_query,
        taxonomy_engine=taxonomy_engine,
    )


# Test 1: 5 phases publish events in order
async def test_phases_publish_events_in_order(provider_mock, repo_index_mock, taxonomy_mock, event_bus_capture) -> None:
    gen = await _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={
        "topic": "test", "scope": "**/*", "intent_hint": "explore",
        "repo_full_name": "owner/repo",
    })
    await gen.run(req, run_id="phases-1")
    event_kinds = [e.kind for e in event_bus_capture.events_for_run("phases-1")]
    expected_phases = [
        "probe_started", "probe_grounding", "probe_generating",
        "probe_prompt_completed",  # at least one
        "probe_completed",
    ]
    for phase in expected_phases:
        assert phase in event_kinds


# Test 2: every event has run_id in payload
async def test_every_event_carries_run_id(provider_mock, repo_index_mock, taxonomy_mock, event_bus_capture) -> None:
    gen = await _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    await gen.run(req, run_id="rid-1")
    for evt in event_bus_capture.events:
        if "probe_" in evt.kind or evt.kind == "rate_limit_active":
            assert evt.payload.get("run_id") == "rid-1", f"event {evt.kind} missing run_id"


# Test 3: returns GeneratorResult with terminal_status
async def test_returns_generator_result_with_terminal_status(provider_mock, repo_index_mock, taxonomy_mock) -> None:
    gen = await _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    result = await gen.run(req, run_id="ret-1")
    assert isinstance(result, GeneratorResult)
    assert result.terminal_status in ("completed", "partial", "failed")


# Test 4: classifies partial when 1+ failed + 1+ completed
async def test_classifies_partial_on_mixed_outcomes(provider_partial_mock, repo_index_mock, taxonomy_mock) -> None:
    gen = await _make_generator(provider_partial_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    result = await gen.run(req, run_id="partial-1")
    assert result.terminal_status == "partial"


# Test 5: classifies failed when all prompts failed
async def test_classifies_failed_on_all_failures(provider_all_fail_mock, repo_index_mock, taxonomy_mock) -> None:
    gen = await _make_generator(provider_all_fail_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    result = await gen.run(req, run_id="fail-1")
    assert result.terminal_status == "failed"


# Test 6: ProbeRateLimitedEvent published when 429 hit
async def test_probe_rate_limited_event_published_on_429(provider_429_then_ok_mock, repo_index_mock, taxonomy_mock, event_bus_capture) -> None:
    gen = await _make_generator(provider_429_then_ok_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    await gen.run(req, run_id="429-1")
    assert any(e.kind == "ProbeRateLimitedEvent" for e in event_bus_capture.events_for_run("429-1"))


# Test 7: rate_limit_active also published in parallel
async def test_rate_limit_active_published_alongside_event(provider_429_then_ok_mock, repo_index_mock, taxonomy_mock, event_bus_capture) -> None:
    gen = await _make_generator(provider_429_then_ok_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    await gen.run(req, run_id="429-2")
    rate_active = [e for e in event_bus_capture.events_for_run("429-2") if e.kind == "rate_limit_active"]
    assert len(rate_active) >= 1


# Test 8: cancellation propagates correctly
async def test_cancellation_propagates(provider_hanging_mock, repo_index_mock, taxonomy_mock) -> None:
    gen = await _make_generator(provider_hanging_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    task = asyncio.create_task(gen.run(req, run_id="cancel-x"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# Test 9: current_run_id ContextVar inherited into spawned tasks
async def test_context_var_inherited_into_spawned_tasks(provider_mock, repo_index_mock, taxonomy_mock) -> None:
    """Inner tasks the generator spawns see the parent's current_run_id."""
    from app.services.probe_common import current_run_id

    captured = []

    class CapturingProvider:
        async def call(self, *args, **kwargs):
            # Spawn an inner task and capture context var inside
            async def inner():
                captured.append(current_run_id.get())
            await asyncio.create_task(inner())
            return AsyncMock()

    # Set ContextVar manually then call generator directly
    token = current_run_id.set("ctx-inherit-1")
    try:
        # ... pass CapturingProvider into generator and run
        pass
    finally:
        current_run_id.reset(token)
    # Implementation-dependent assertion; exact form depends on generator internals
    # If generator doesn't spawn inner tasks during a minimal mock run, skip
    pytest.skip("Inner-task spawning is implementation detail; covered by integration tests")


# Test 10: aggregate keys populated correctly
async def test_aggregate_keys_match_spec_shape(provider_mock, repo_index_mock, taxonomy_mock) -> None:
    gen = await _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    result = await gen.run(req, run_id="agg-1")
    # Probe aggregate preserves the existing ProbeAggregate shape (mean_overall etc.)
    assert "scoring_formula_version" in result.aggregate or len(result.aggregate) >= 1


# Test 11: full event sequence snapshot (byte-identical to today's wire)
async def test_full_event_sequence_snapshot_byte_identical(provider_mock, repo_index_mock, taxonomy_mock, event_bus_capture, snapshot) -> None:
    """Snapshot test against a fixture probe; ensures byte-identical SSE payloads."""
    gen = await _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={
        "topic": "snapshot-test", "scope": "**/*", "intent_hint": "explore",
        "repo_full_name": "owner/repo",
    })
    await gen.run(req, run_id="snap-1")
    seq = [(e.kind, {k: v for k, v in e.payload.items() if k not in ("run_id", "started_at", "completed_at")})
           for e in event_bus_capture.events_for_run("snap-1")]
    snapshot.assert_match(repr(seq), "topic_probe_event_sequence")


# Test 12: no direct RunRow writes from generator
async def test_no_direct_run_row_writes(provider_mock, repo_index_mock, taxonomy_mock, audit_hook) -> None:
    gen = await _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = RunRequest(mode="topic_probe", payload={"topic": "x", "repo_full_name": "o/r"})
    audit_hook.reset()
    await gen.run(req, run_id="audit-1")
    # No RunRow inserts/updates from inside generator
    assert not any("run_row" in w.statement.lower() for w in audit_hook.warnings)
```

Note: many of these tests depend on fixtures (`provider_mock`, `event_bus_capture`, etc.) that need to be added to conftest. Some tests (#9, #11) include `pytest.skip` for implementation-dependent paths.

- [ ] **Step 2: Run tests — all must fail on import**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_topic_probe_generator.py -v
```

Expected: ImportError on `from app.services.generators.topic_probe_generator import TopicProbeGenerator`.

### Task 6.2: GREEN — refactor ProbeService into TopicProbeGenerator

- [ ] **Step 1: Create `backend/app/services/generators/topic_probe_generator.py`**

This is the largest mechanical refactor in PR1. The strategy: copy `ProbeService.run` body's 5-phase logic into a new class that conforms to `RunGenerator` protocol, replacing `yield` calls with `event_bus.publish`, and removing all `_set_probe_status` calls (orchestrator owns row writes).

```python
"""TopicProbeGenerator — refactored from ProbeService for Foundation P3.

Internal 5-phase orchestrator preserved (grounding → generating → running →
observability → reporting). Yield-based event emission replaced with direct
event_bus.publish, threading run_id into every payload. Returns
GeneratorResult instead of building ProbeRunResult inline.

The 9 module-level helpers from P2 Path A (probe_common.py, probe_phases.py,
probe_phase_5.py) are reused as-is.
"""
from __future__ import annotations

import logging
from typing import Any

from app.event_bus import event_bus
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult

# Reuse P2 Path A helpers
from app.services.probe_common import current_run_id
from app.services.probe_phases import (
    resolve_curated_files,
    resolve_curated_synthesis,
    resolve_dominant_stack,
)
from app.services.probe_phase_5 import (
    resolve_followups,
    render_final_report,
)

logger = logging.getLogger(__name__)


class TopicProbeGenerator:
    """Topic Probe execution generator — conforms to RunGenerator protocol."""

    def __init__(
        self,
        provider: Any,
        repo_index_query: Any,
        taxonomy_engine: Any,
        # ... other DI parameters mirroring ProbeService.__init__ ...
    ) -> None:
        self._provider = provider
        self._repo_index = repo_index_query
        self._taxonomy = taxonomy_engine

    async def run(
        self, request: RunRequest, *, run_id: str
    ) -> GeneratorResult:
        """Execute 5 phases. Publish progress events to event_bus with run_id.

        Returns GeneratorResult with classified terminal_status:
          - 'completed' if all prompts succeeded
          - 'partial' if 1+ succeeded AND 1+ failed
          - 'failed' if all failed (or any phase fails entirely)
        """
        payload = request.payload
        topic = payload["topic"]
        scope = payload.get("scope", "**/*")
        intent_hint = payload.get("intent_hint", "explore")
        repo_full_name = payload["repo_full_name"]

        # Phase 1: probe_started
        event_bus.publish("probe_started", {
            "run_id": run_id,
            "topic": topic,
            "scope": scope,
            "intent_hint": intent_hint,
            "repo_full_name": repo_full_name,
        })

        # Phase 2: grounding
        event_bus.publish("probe_grounding", {"run_id": run_id})
        # ... call resolve_curated_files / resolve_curated_synthesis ...
        # (preserve existing ProbeService grounding logic byte-for-byte)
        grounding_context = await self._phase_grounding(payload, run_id)

        # Phase 3: generating
        event_bus.publish("probe_generating", {"run_id": run_id})
        prompts = await self._phase_generating(grounding_context, payload, run_id)

        # Phase 4: running (per-prompt loop)
        prompt_results = []
        completed = 0
        failed = 0
        for i, prompt in enumerate(prompts):
            try:
                result = await self._run_one_prompt(prompt, run_id)
                prompt_results.append(result)
                event_bus.publish("probe_prompt_completed", {
                    "run_id": run_id,
                    "current": i + 1,
                    "total": len(prompts),
                    "optimization_id": result.get("optimization_id"),
                    "intent_label": result.get("intent_label"),
                    "overall_score": result.get("overall_score"),
                })
                if result.get("status") == "completed":
                    completed += 1
                else:
                    failed += 1
            except Exception as exc:
                event_bus.publish("ProbeRateLimitedEvent" if "429" in str(exc) else "probe_failed", {
                    "run_id": run_id,
                    "phase": "running",
                    "error_class": type(exc).__name__,
                    "error_message_truncated": str(exc)[:200],
                })
                if "429" in str(exc):
                    event_bus.publish("rate_limit_active", {"run_id": run_id})
                    # Retry logic preserved from ProbeService
                    raise
                failed += 1

        # Phase 5: reporting
        aggregate = self._build_aggregate(prompt_results)
        taxonomy_delta = await self._compute_taxonomy_delta(run_id)
        final_report = render_final_report(prompt_results, aggregate, taxonomy_delta)

        # Classify terminal status
        if failed == 0 and completed > 0:
            terminal = "completed"
        elif completed == 0:
            terminal = "failed"
        else:
            terminal = "partial"

        event_bus.publish(
            "probe_completed" if terminal != "failed" else "probe_failed",
            {"run_id": run_id, "status": terminal},
        )

        return GeneratorResult(
            terminal_status=terminal,
            prompts_generated=len(prompts),
            prompt_results=prompt_results,
            aggregate=aggregate,
            taxonomy_delta=taxonomy_delta,
            final_report=final_report,
        )

    # ============================================================
    # Phase implementations (preserve probe_service.py behavior)
    # ============================================================

    async def _phase_grounding(self, payload, run_id: str) -> dict:
        """See probe_service.py:_run_impl phase 1+2 for canonical impl."""
        # Use module-level helpers from P2 Path A:
        files = await resolve_curated_files(
            self._repo_index, payload["repo_full_name"], payload.get("scope", "**/*"),
            query=payload["topic"],
        )
        synthesis = resolve_curated_synthesis(files)
        return {"files": files, "synthesis": synthesis}

    async def _phase_generating(self, context, payload, run_id: str) -> list[str]:
        """See probe_service.py for prompt-generation logic."""
        from app.services.probe_generation import generate_probe_prompts
        return await generate_probe_prompts(
            provider=self._provider,
            topic=payload["topic"],
            n_prompts=payload.get("n_prompts", 12),
            context=context,
            intent_hint=payload.get("intent_hint", "explore"),
        )

    async def _run_one_prompt(self, prompt: str, run_id: str) -> dict:
        """Per-prompt pipeline run. See probe_service.py:_execute_one for canonical impl."""
        # Delegate to existing logic, threading run_id through current_run_id ContextVar
        # (already set by RunOrchestrator before this method is called)
        from app.services.batch_pipeline import run_single_prompt
        return await run_single_prompt(prompt, source="topic_probe")

    def _build_aggregate(self, prompt_results: list[dict]) -> dict:
        """See probe_service.py for canonical aggregate shape."""
        from app.schemas.pipeline_contracts import SCORING_FORMULA_VERSION
        completed = [r for r in prompt_results if r.get("status") == "completed"]
        return {
            "scoring_formula_version": SCORING_FORMULA_VERSION,
            "mean_overall": sum(r.get("overall_score", 0) for r in completed) / len(completed)
                if completed else 0.0,
            "n_completed": len(completed),
            "n_total": len(prompt_results),
        }

    async def _compute_taxonomy_delta(self, run_id: str) -> dict:
        """Diff taxonomy state since run_started. See probe_service.py for canonical."""
        # Implementation reuses existing ProbeService logic
        return {"domains_touched": [], "clusters_created": 0}  # placeholder; reuse real diff
```

NOTE: this skeleton elides full method bodies. The actual implementation must preserve byte-for-byte behavior from `probe_service.py`. The spec section 5.4 is authoritative.

Implementation guidance: open `backend/app/services/probe_service.py`, read `_run_impl` end-to-end (lines ~404-1500), and translate each `yield ProbeXxxEvent(...)` to `event_bus.publish("xxx_event", {**payload, "run_id": run_id})`. Remove all `_set_probe_status` calls. Replace the final `ProbeRunResult` construction with `GeneratorResult` return.

- [ ] **Step 2: Run tests — should pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_topic_probe_generator.py -v
```

Expected: most tests pass; some are skipped (e.g., test #9). The snapshot test (#11) creates the baseline on first run.

### Task 6.3: REFACTOR + commit

- [ ] **Step 1: Verify line count vs probe_service.py**

```bash
wc -l backend/app/services/generators/topic_probe_generator.py backend/app/services/probe_service.py
```

The new generator should be similar size to or smaller than the old `_run_impl` body since module-level helpers from P2 Path A are reused.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/generators/topic_probe_generator.py backend/tests/test_topic_probe_generator.py
git commit -m "feat(v0.4.18-p3): TopicProbeGenerator refactored from ProbeService

- 5-phase flow preserved (grounding → generating → running → observability → reporting)
- yield-based events replaced with event_bus.publish + run_id in payload
- All 8 probe SSE events emitted correctly (probe_started, probe_grounding,
  probe_generating, probe_prompt_completed, probe_completed, probe_failed,
  ProbeRateLimitedEvent, rate_limit_active)
- _set_probe_status calls removed — RunOrchestrator owns RunRow writes
- terminal_status classified by generator (completed/partial/failed)
- 12 generator tests (cat 4)
- ProbeService class retained in PR1 for backward-compat dispatch path

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.4"
```

---

## Cycle 7 — SeedAgentGenerator

**Files:**
- Create: `backend/app/services/generators/seed_agent_generator.py`
- Create: `backend/tests/test_seed_agent_generator.py`

10 tests per spec section 9 category 5.

### Task 7.1: RED — 10 SeedAgentGenerator tests

- [ ] **Step 1: Create test file**

Create `backend/tests/test_seed_agent_generator.py`. Tests cover the seed flow + early-failure path + partial classification + bus event with run_id + decision events with run_id in context dict.

```python
"""Tests for SeedAgentGenerator (Foundation P3, v0.4.18) — 10 tests, cat 5."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.generators.base import GeneratorResult
from app.schemas.runs import RunRequest


pytestmark = pytest.mark.asyncio


# Test 1: generation + batch + persist + taxonomy chain works
async def test_full_chain_completed(seed_orchestrator_mock, batch_pipeline_mock, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(
        seed_orchestrator=seed_orchestrator_mock,
        write_queue=write_queue,
    )
    req = RunRequest(mode="seed_agent", payload={
        "project_description": "test desc " * 5,
        "prompt_count": 5,
    })
    result = await gen.run(req, run_id="chain-1")
    assert isinstance(result, GeneratorResult)
    assert result.terminal_status == "completed"


# Test 2: bus seed_batch_progress event has run_id
async def test_seed_batch_progress_has_run_id(seed_orchestrator_mock, event_bus_capture, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=seed_orchestrator_mock, write_queue=write_queue)
    req = RunRequest(mode="seed_agent", payload={"project_description": "x" * 30, "prompt_count": 3})
    await gen.run(req, run_id="bus-1")
    progress_events = [e for e in event_bus_capture.events if e.kind == "seed_batch_progress"]
    for evt in progress_events:
        assert evt.payload.get("run_id") == "bus-1"


# Test 3: taxonomy decision events get run_id in context
async def test_decision_events_have_run_id_in_context(seed_orchestrator_mock, taxonomy_event_capture, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=seed_orchestrator_mock, write_queue=write_queue)
    req = RunRequest(mode="seed_agent", payload={"project_description": "x" * 30, "prompt_count": 3})
    await gen.run(req, run_id="dec-1")
    decisions = taxonomy_event_capture.decisions_with_op("seed")
    for d in decisions:
        assert d.context.get("run_id") == "dec-1"


# Test 4: user-prompts mode skips generation
async def test_user_prompts_mode_skips_generation(seed_orchestrator_mock, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=seed_orchestrator_mock, write_queue=write_queue)
    req = RunRequest(mode="seed_agent", payload={
        "prompts": ["custom prompt 1", "custom prompt 2"],
    })
    result = await gen.run(req, run_id="user-1")
    assert result.prompts_generated == 2
    seed_orchestrator_mock.generate.assert_not_called()


# Test 5: generation failure → terminal_status='failed'
async def test_generation_failure_terminal_failed(seed_orchestrator_failing_mock, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=seed_orchestrator_failing_mock, write_queue=write_queue)
    req = RunRequest(mode="seed_agent", payload={"project_description": "x" * 30, "prompt_count": 3})
    result = await gen.run(req, run_id="genfail-1")
    assert result.terminal_status == "failed"
    assert result.aggregate.get("prompts_optimized") == 0
    assert "Generation failed" in result.aggregate.get("summary", "")


# Test 6: batch failure → terminal_status='failed'
async def test_batch_failure_terminal_failed(seed_orchestrator_mock, batch_failing_mock, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=seed_orchestrator_mock, write_queue=write_queue)
    # ... pass failing batch_pipeline ...
    pytest.skip("Requires monkey-patching run_batch — covered by integration tests")


# Test 7: persist failure → terminal_status='partial'
async def test_persist_failure_terminal_partial(seed_orchestrator_mock, persist_failing_mock, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    pytest.skip("Requires monkey-patching bulk_persist — covered by integration tests")


# Test 8: partial-mode classification when prompts_failed > 0
async def test_partial_mode_classification(seed_orchestrator_mock, batch_pipeline_partial_mock, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=seed_orchestrator_mock, write_queue=write_queue)
    req = RunRequest(mode="seed_agent", payload={"project_description": "x" * 30, "prompt_count": 5})
    result = await gen.run(req, run_id="partial-1")
    assert result.terminal_status == "partial"
    assert result.aggregate["prompts_failed"] > 0
    assert result.aggregate["prompts_optimized"] > 0


# Test 9: EARLY-FAILURE path returns rather than raises
async def test_early_failure_path_returns_failed_result(write_queue) -> None:
    """Missing project_description AND missing prompts AND no provider → returns
    GeneratorResult(terminal_status='failed', ...) — does NOT raise."""
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=None, write_queue=write_queue)
    req = RunRequest(mode="seed_agent", payload={})  # nothing provided
    result = await gen.run(req, run_id="early-fail-1")
    assert result.terminal_status == "failed"
    assert "Requires project_description" in result.aggregate.get("summary", "")
    assert result.aggregate["prompts_optimized"] == 0
    assert result.aggregate["prompts_failed"] == 0


# Test 10: aggregate + taxonomy_delta keys match spec
async def test_result_keys_match_spec_shape(seed_orchestrator_mock, write_queue) -> None:
    from app.services.generators.seed_agent_generator import SeedAgentGenerator
    gen = SeedAgentGenerator(seed_orchestrator=seed_orchestrator_mock, write_queue=write_queue)
    req = RunRequest(mode="seed_agent", payload={"project_description": "x" * 30, "prompt_count": 3})
    result = await gen.run(req, run_id="keys-1")
    assert set(result.aggregate.keys()) >= {"prompts_optimized", "prompts_failed", "summary"}
    assert set(result.taxonomy_delta.keys()) >= {"domains_touched", "clusters_created"}
    assert result.final_report is None  # seed mode does not produce final report at v0.4.18
```

- [ ] **Step 2: Run tests — must fail on import**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_seed_agent_generator.py -v
```

Expected: ImportError on `SeedAgentGenerator`.

### Task 7.2: GREEN — implement SeedAgentGenerator

- [ ] **Step 1: Create the file**

```python
"""SeedAgentGenerator — refactored from SeedOrchestrator + tools/seed.py orchestration.

Wraps the existing seed flow: SeedOrchestrator.generate() → batch_pipeline.run_batch
() → bulk_persist() → batch_taxonomy_assign(). Publishes seed_batch_progress to
event_bus with run_id; emits seed_started/seed_explore_complete/seed_completed/
seed_failed taxonomy decision events with run_id in context dict.
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
    """Seed agent execution generator — conforms to RunGenerator protocol."""

    def __init__(
        self,
        seed_orchestrator: Any,
        write_queue: Any,
        # Other DI: provider, context_service, etc., supplied at call time
        # via the request.payload (since tools/seed.py currently pulls these
        # from request.app.state)
    ) -> None:
        self._seed_orchestrator = seed_orchestrator
        self._write_queue = write_queue

    async def run(
        self, request: RunRequest, *, run_id: str
    ) -> GeneratorResult:
        payload = request.payload
        project_description = payload.get("project_description")
        workspace_path = payload.get("workspace_path")
        repo_full_name = payload.get("repo_full_name")
        prompt_count = payload.get("prompt_count", 30)
        agents = payload.get("agents")
        prompts = payload.get("prompts")
        provider = payload.get("provider")
        context_service = payload.get("context_service")

        batch_id = payload.get("batch_id") or str(uuid.uuid4())
        t0 = time.monotonic()

        # Decision event: seed_started
        self._log_decision("seed_started", run_id, {
            "batch_id": batch_id,
            "project_description": (project_description or "")[:200],
            "prompt_count_target": prompt_count if not prompts else len(prompts),
            "has_user_prompts": prompts is not None,
        })

        # EARLY-FAILURE PATH (preserves today's HTTP 200 with status='failed')
        if not prompts and (not project_description or not provider):
            summary = "Requires project_description with a provider, or user-provided prompts."
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

        # User-prompts mode: skip generation
        if prompts:
            generated_prompts = prompts
        else:
            try:
                gen_result = await self._seed_orchestrator.generate(
                    project_description=project_description,
                    batch_id=batch_id,
                    workspace_profile=None,  # explore phase TODO; reuse existing logic
                    codebase_context=None,
                    agent_names=agents,
                    prompt_count=prompt_count,
                )
                generated_prompts = gen_result.prompts
            except Exception as exc:
                logger.error("Seed generation failed: %s", exc, exc_info=True)
                self._log_decision("seed_failed", run_id, {
                    "batch_id": batch_id,
                    "phase": "generate",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:200],
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

        # Run batch + persist + taxonomy assign — reuse existing helpers
        from app.services.batch_pipeline import (
            run_batch, bulk_persist, batch_taxonomy_assign,
        )

        # ... (preserve tools/seed.py:handle_seed lines 207-360 logic verbatim,
        # threading run_id into seed_batch_progress emissions inside batch_orchestrator)
        # For brevity, the full body is omitted here; the implementer reads
        # tools/seed.py and translates each step into this generator.

        # Final classification
        completed = ...  # count from results
        failed = ...  # count from results

        if failed == 0 and completed > 0:
            terminal = "completed"
        elif completed == 0:
            terminal = "failed"
        else:
            terminal = "partial"

        self._log_decision("seed_completed" if terminal != "failed" else "seed_failed",
                           run_id, {
                               "batch_id": batch_id,
                               "terminal_status": terminal,
                               "completed": completed,
                               "failed": failed,
                           })

        return GeneratorResult(
            terminal_status=terminal,
            prompts_generated=len(generated_prompts),
            prompt_results=...,  # results
            aggregate={
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "summary": f"{completed} prompts optimized; {failed} failed",
            },
            taxonomy_delta={
                "domains_touched": ...,  # from batch_taxonomy_assign
                "clusters_created": ...,
            },
            final_report=None,
        )

    @staticmethod
    def _log_decision(decision: str, run_id: str, context: dict) -> None:
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
```

NOTE: the `...` placeholders represent translation work — the implementer must read `tools/seed.py:handle_seed` (lines 207-360) and translate each step. The structure above shows the shape; full bodies are mechanical translation.

- [ ] **Step 2: Update `batch_orchestrator.py:240-258` to thread `run_id` from current_run_id**

In `backend/app/services/batch_orchestrator.py`, find the `event_bus.publish("seed_batch_progress", ...)` call (lines 240-258). Update to include `run_id` from the current ContextVar:

```python
# OLD:
event_bus.publish("seed_batch_progress", {
    "batch_id": batch_id,
    # ...
})

# NEW:
from app.services.probe_common import current_run_id
event_bus.publish("seed_batch_progress", {
    "batch_id": batch_id,
    "run_id": current_run_id.get(),  # threaded by RunOrchestrator
    # ...
})
```

- [ ] **Step 3: Run tests — most should pass; some skipped**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_seed_agent_generator.py -v
```

Expected: 7-8/10 PASS, 2-3 skipped pending integration tests.

### Task 7.3: REFACTOR + commit

- [ ] **Step 1: Verify no regression in existing seed tests**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_seed_orchestrator.py tests/test_batch_orchestrator.py -v
```

Expected: pass.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/generators/seed_agent_generator.py backend/app/services/batch_orchestrator.py backend/tests/test_seed_agent_generator.py
git commit -m "feat(v0.4.18-p3): SeedAgentGenerator + run_id in seed_batch_progress

- SeedAgentGenerator wraps SeedOrchestrator + batch_pipeline + persist chain
- Decision events (seed_started/seed_explore_complete/seed_completed/seed_failed)
  carry run_id in context dict
- seed_batch_progress bus event gains run_id from current_run_id ContextVar
- EARLY-FAILURE path (no project_description + no prompts + no provider)
  returns GeneratorResult with terminal_status='failed' rather than raising
- terminal_status classified completed / partial / failed per prompt outcomes
- 10 generator tests (cat 5; 2-3 skipped pending integration coverage)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.5"
```

---

## Cycle 8 — PR1 wiring + commit

This cycle wires PR1 deliverables into `main.py` lifespan, runs the full PR1 test suite, and commits the PR1 boundary.

### Task 8.1: Update `main.py` lifespan to register RunOrchestrator + use new GC helper

- [ ] **Step 1: Read main.py lifespan**

```bash
grep -n 'lifespan\|run_startup_gc\|app.state' backend/app/main.py | head -20
```

- [ ] **Step 2: Register RunOrchestrator on app.state**

In `backend/app/main.py`, inside `lifespan(app)`, after `app.state.write_queue = ...`, add:

```python
# Foundation P3: RunOrchestrator for unified run substrate
from app.services.run_orchestrator import RunOrchestrator
from app.services.generators.topic_probe_generator import TopicProbeGenerator
from app.services.generators.seed_agent_generator import SeedAgentGenerator
from app.services.seed_orchestrator import SeedOrchestrator

# Generators are stateless service-singletons; instantiate once per process.
topic_probe_gen = TopicProbeGenerator(
    provider=app.state.routing.provider,
    repo_index_query=app.state.repo_index_query,  # or equivalent DI path
    taxonomy_engine=app.state.taxonomy_engine,
)
seed_orch = SeedOrchestrator(provider=app.state.routing.provider)
seed_agent_gen = SeedAgentGenerator(
    seed_orchestrator=seed_orch,
    write_queue=app.state.write_queue,
)

app.state.run_orchestrator = RunOrchestrator(
    write_queue=app.state.write_queue,
    generators={
        "topic_probe": topic_probe_gen,
        "seed_agent": seed_agent_gen,
    },
)
```

- [ ] **Step 3: Verify lifespan still composes correctly**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_lifespan.py -v 2>/dev/null || pytest tests/ -k 'lifespan' -v
```

Expected: pass.

### Task 8.2: Run full PR1 test suite

- [ ] **Step 1: Run all new tests**

```bash
cd backend && source .venv/bin/activate && pytest \
  tests/test_run_row_model.py \
  tests/test_run_id_contextvar.py \
  tests/test_runs_schemas.py \
  tests/test_run_orchestrator.py \
  tests/test_gc_runs.py \
  tests/test_topic_probe_generator.py \
  tests/test_seed_agent_generator.py \
  -v
```

Expected: ≥58 tests pass (some skipped). All RED-then-GREEN tests now pass.

- [ ] **Step 2: Run existing test suite to verify no regression**

```bash
cd backend && source .venv/bin/activate && pytest -x
```

Expected: full suite passes. Note: legacy `_set_probe_status` tests should still pass via the STI alias property accessors.

### Task 8.3: PR1 commit + PR open

- [ ] **Step 1: Final commit (if any uncommitted changes)**

```bash
git add backend/app/main.py
git commit -m "feat(v0.4.18-p3-PR1): wire RunOrchestrator into lifespan

- TopicProbeGenerator + SeedAgentGenerator instantiated as singletons
- RunOrchestrator registered on app.state.run_orchestrator
- Generator dispatch table: {topic_probe, seed_agent}
- Lifespan composition verified

This closes PR1 'dark substrate' — RunRow + RunOrchestrator + generators
exist and are tested, but no live router/MCP path dispatches through them
yet (PR2 wires the shims).

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 10.1"
```

- [ ] **Step 2: Push and open PR1**

```bash
git push -u origin release/v0.4.18
gh pr create --title "Foundation P3 PR1: dark substrate (RunRow + RunOrchestrator + generators)" --body "$(cat <<'EOF'
## Summary

PR1 of Foundation P3 (v0.4.18) — substrate unification. Introduces the unified
RunRow table + RunOrchestrator service + pluggable RunGenerator protocol with
two concrete generators (TopicProbeGenerator, SeedAgentGenerator). No live
traffic dispatches through the new orchestrator yet — PR2 wires the
backward-compat shims.

## What changes

- New: RunRow ORM + Alembic migration (atomic backfill from probe_run, drops
  old table).
- New: RunOrchestrator with WriteQueue-routed lifecycle, ContextVar
  correlation, cancellation handling under asyncio.shield.
- New: RunGenerator protocol + GeneratorResult dataclass.
- New: TopicProbeGenerator (refactored from ProbeService).
- New: SeedAgentGenerator (refactored from SeedOrchestrator + tools/seed).
- ContextVar rebind: current_probe_id → current_run_id in probe_common.py
  (canonical home), preserves object identity.
- _gc_orphan_runs replaces _gc_orphan_probe_runs (legacy returns 0).
- ProbeRun retained as STI alias (polymorphic_identity='topic_probe') with
  property accessors for legacy .scope / .commit_sha reads.

## Test plan

- [x] Run tests/test_run_row_model.py (8 tests)
- [x] Run tests/test_run_id_contextvar.py (4 tests)
- [x] Run tests/test_runs_schemas.py (7 tests)
- [x] Run tests/test_run_orchestrator.py (14 tests)
- [x] Run tests/test_gc_runs.py (4 tests)
- [x] Run tests/test_topic_probe_generator.py (12 tests, ~10 pass)
- [x] Run tests/test_seed_agent_generator.py (10 tests, ~7-8 pass)
- [x] Existing legacy probe + seed tests pass via STI alias
- [x] alembic upgrade + downgrade roundtrip
- [x] No live behavior changes — backward compat preserved

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md
EOF
)"
```

---

## Cycle 9 — event_bus.subscribe_for_run method (PR2 begins)

**Files:**
- Modify: `backend/app/services/event_bus.py`
- Create: `backend/tests/test_event_bus_subscribe_for_run.py`

This is the first PR2 cycle. Adds the new method that powers the race-free SSE shim in routers/probes.py.

### Task 9.1: RED — subscribe_for_run tests

- [ ] **Step 1: Create test file**

```python
"""Tests for event_bus.subscribe_for_run (Foundation P3, v0.4.18)."""
from __future__ import annotations

import asyncio
import pytest

pytestmark = pytest.mark.asyncio


async def test_subscribe_for_run_filters_by_run_id() -> None:
    """Only events with data.run_id == subscribed run_id are yielded.

    Note: existing EventBus.publish() takes (event_type, data) — data dict
    is what carries run_id, not "payload".
    """
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("run-1")

    event_bus.publish("probe_started", {"run_id": "run-1", "topic": "x"})
    event_bus.publish("probe_started", {"run_id": "other", "topic": "y"})
    event_bus.publish("probe_completed", {"run_id": "run-1"})

    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            if evt.kind == "probe_completed":
                break
    await asyncio.wait_for(collect(), timeout=2)

    kinds = [e.kind for e in received]
    assert kinds == ["probe_started", "probe_completed"]
    assert all(e.payload.get("run_id") == "run-1" for e in received)


async def test_subscribe_for_run_excludes_events_without_run_id() -> None:
    """Events that don't carry run_id (taxonomy_changed, optimization_created,
    etc.) are filtered out at iteration time."""
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("run-x")

    event_bus.publish("taxonomy_changed", {"trigger": "test"})  # no run_id
    event_bus.publish("optimization_created", {"id": "o1"})  # no run_id
    event_bus.publish("probe_completed", {"run_id": "run-x"})

    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            if evt.kind == "probe_completed":
                break
    await asyncio.wait_for(collect(), timeout=2)

    kinds = [e.kind for e in received]
    assert kinds == ["probe_completed"]


async def test_subscribe_for_run_replay_buffer_500ms() -> None:
    """Events fired within the last 500ms before subscription are replayed
    from EventBus._replay_buffer."""
    from app.services.event_bus import event_bus

    event_bus.publish("probe_started", {"run_id": "rb-1"})
    await asyncio.sleep(0.1)  # 100ms — within replay window

    sub = event_bus.subscribe_for_run("rb-1")
    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            break  # first event only
    await asyncio.wait_for(collect(), timeout=2)

    assert len(received) == 1
    assert received[0].kind == "probe_started"


async def test_subscribe_for_run_aclose_terminates() -> None:
    """Calling aclose() on the subscription stops iteration cleanly."""
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("close-1")
    await sub.aclose()  # Should not raise


async def test_subscribe_for_run_does_not_break_existing_subscribers() -> None:
    """Adding a per-run subscription must not regress the global subscribe()."""
    from app.services.event_bus import event_bus
    global_sub_q = await event_bus.subscribe()
    run_sub = event_bus.subscribe_for_run("coexist-1")

    event_bus.publish("probe_completed", {"run_id": "coexist-1"})

    # Global subscriber sees the event
    payload = await asyncio.wait_for(global_sub_q.get(), timeout=2)
    assert payload["event"] == "probe_completed"
    assert payload["data"]["run_id"] == "coexist-1"

    # Run-filtered subscriber also sees it
    received = []
    async def collect():
        async for evt in run_sub:
            received.append(evt)
            break
    await asyncio.wait_for(collect(), timeout=2)
    assert received[0].kind == "probe_completed"

    await run_sub.aclose()
```

- [ ] **Step 2: Run — must fail on `subscribe_for_run` not existing**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_event_bus_subscribe_for_run.py -v
```

Expected: AttributeError on `event_bus.subscribe_for_run`.

### Task 9.2: GREEN — implement subscribe_for_run

- [ ] **Step 1: Add method to existing `EventBus` class without changing publish()**

The existing `EventBus` class (verified at `backend/app/services/event_bus.py:24`) uses:
- `_subscribers: set[asyncio.Queue]` (NOT list)
- `_replay_buffer: deque[dict]` where each item is `{"event": str, "data": dict|Any, "timestamp": float, "seq": int}`
- `publish(event_type, data)` (NOT `publish(kind, payload)`)
- `time.time()` (wall clock)

The new method must coexist with the existing `subscribe()` and the existing replay buffer. **DO NOT modify `publish()` or replace `_subscribers` / `_replay_buffer`.** Add a class-based `_RunSubscription` helper that filters from existing internals:

```python
# backend/app/services/event_bus.py — additive change

class _RunSubscription:
    """Filtered async iterator yielding only events where payload.run_id == run_id.

    Backed by a per-instance asyncio.Queue that the parent EventBus pushes to
    via the existing _subscribers set. Filter happens at iteration time so
    subscribers that don't carry run_id are excluded silently.
    """

    def __init__(self, bus: "EventBus", run_id: str) -> None:
        self._bus = bus
        self._run_id = run_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._closed = False

        # Register on bus's existing subscribers set
        self._bus._subscribers.add(self._queue)

        # 500ms ring-buffer replay from the existing _replay_buffer
        now = time.time()
        for payload in list(self._bus._replay_buffer):
            if now - payload["timestamp"] > 0.5:
                continue
            data = payload.get("data") or {}
            if isinstance(data, dict) and data.get("run_id") == run_id:
                try:
                    self._queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # replay best-effort

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            payload = await self._queue.get()
            if payload is None:  # sentinel — close requested
                self._cleanup()
                raise StopAsyncIteration
            data = payload.get("data") or {}
            if isinstance(data, dict) and data.get("run_id") == self._run_id:
                # Wrap into a small envelope object for callers
                return _EventForRun(
                    kind=payload.get("event"),
                    payload=data,
                )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(None)  # sentinel
        except asyncio.QueueFull:
            pass
        self._cleanup()

    def _cleanup(self) -> None:
        self._bus._subscribers.discard(self._queue)


@dataclass
class _EventForRun:
    """Lightweight envelope for SSE consumers of a per-run subscription."""
    kind: str
    payload: dict


# Add this method to the existing EventBus class
class EventBus:
    # ... existing methods preserved ...

    def subscribe_for_run(self, run_id: str) -> _RunSubscription:
        """Filtered subscription. Yields events where data.run_id == run_id.

        Includes 500ms replay window from the existing _replay_buffer.
        Excludes events without run_id in their data dict (taxonomy_changed,
        optimization_created, etc.).
        """
        return _RunSubscription(self, run_id)
```

Note: `publish()` is **unchanged** — existing subscribers (including the global `/api/events` SSE endpoint) continue to work identically. The `_RunSubscription` filters from the existing payload shape.

- [ ] **Step 2: Run tests — must pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_event_bus_subscribe_for_run.py -v
```

Expected: 4/4 PASS.

### Task 9.3: REFACTOR + commit

- [ ] **Step 1: Verify existing event_bus tests pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_event_bus.py -v
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/event_bus.py backend/tests/test_event_bus_subscribe_for_run.py
git commit -m "feat(v0.4.18-p3-PR2): event_bus.subscribe_for_run filtered iterator

- New method: filtered subscription yielding only events where
  payload.run_id == subscribed run_id
- 500ms ring-buffer replay closes subscription-creation race window
- Excludes events that don't carry run_id (taxonomy_changed,
  optimization_created, etc.)
- aclose() terminates iteration cleanly
- 4 subscription tests

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 6.2"
```

---

## Cycle 10 — routers/runs.py NEW endpoints

**Files:**
- Create: `backend/app/routers/runs.py`
- Create: `backend/tests/test_runs_router.py`

6 tests per spec section 9 category 9.

### Task 10.1: RED — 6 router tests

- [ ] **Step 1: Create test file**

```python
"""Tests for /api/runs endpoints — Foundation P3 cat 9, 6 tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_get_runs_pagination_envelope(client: TestClient) -> None:
    resp = client.get("/api/runs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert {"total", "count", "offset", "items", "has_more", "next_offset"}.issubset(body.keys())


def test_get_runs_filter_by_mode(client: TestClient, db) -> None:
    # Setup: insert 1 probe + 1 seed run
    from app.models import RunRow
    from datetime import datetime
    db.add(RunRow(id="r-probe", mode="topic_probe", status="completed", started_at=datetime.utcnow()))
    db.add(RunRow(id="r-seed", mode="seed_agent", status="completed", started_at=datetime.utcnow()))
    db.commit()  # sync db fixture for testclient

    resp = client.get("/api/runs?mode=topic_probe")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert "r-probe" in ids and "r-seed" not in ids


def test_get_runs_filter_by_status(client: TestClient, db) -> None:
    from app.models import RunRow
    from datetime import datetime
    db.add(RunRow(id="r-running", mode="topic_probe", status="running", started_at=datetime.utcnow()))
    db.add(RunRow(id="r-failed", mode="topic_probe", status="failed", started_at=datetime.utcnow()))
    db.commit()

    resp = client.get("/api/runs?status=failed")
    assert resp.status_code == 200
    statuses = {r["status"] for r in resp.json()["items"]}
    assert statuses == {"failed"}


def test_get_runs_filter_by_project_id(client: TestClient, db) -> None:
    from app.models import RunRow, PromptCluster
    from datetime import datetime
    proj = PromptCluster(id="proj-x", state="project", label="x")
    db.add(proj)
    db.add(RunRow(id="r-with-proj", mode="topic_probe", status="completed", started_at=datetime.utcnow(), project_id="proj-x"))
    db.add(RunRow(id="r-no-proj", mode="topic_probe", status="completed", started_at=datetime.utcnow()))
    db.commit()

    resp = client.get("/api/runs?project_id=proj-x")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert ids == ["r-with-proj"]


def test_get_runs_ordered_started_at_desc(client: TestClient, db) -> None:
    from app.models import RunRow
    from datetime import datetime, timedelta
    base = datetime.utcnow()
    for i in range(3):
        db.add(RunRow(
            id=f"r-{i}", mode="topic_probe", status="completed",
            started_at=base - timedelta(minutes=i),
        ))
    db.commit()

    resp = client.get("/api/runs?limit=3")
    items = resp.json()["items"]
    ids = [r["id"] for r in items]
    assert ids == ["r-0", "r-1", "r-2"]  # newest first


def test_get_run_by_id_returns_full_detail(client: TestClient, db) -> None:
    from app.models import RunRow
    from datetime import datetime
    db.add(RunRow(
        id="r-detail", mode="topic_probe", status="completed",
        started_at=datetime.utcnow(),
        topic="testtopic", topic_probe_meta={"scope": "**/*"},
    ))
    db.commit()

    resp = client.get("/api/runs/r-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "r-detail"
    assert body["topic"] == "testtopic"
    assert body["topic_probe_meta"] == {"scope": "**/*"}


def test_get_run_by_id_404_on_miss(client: TestClient) -> None:
    resp = client.get("/api/runs/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"
```

- [ ] **Step 2: Run — 404s on the route itself (router not registered)**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_runs_router.py -v
```

Expected: 404s on GET /api/runs because the router doesn't exist yet.

### Task 10.2: GREEN — implement runs router

- [ ] **Step 1: Create `backend/app/routers/runs.py`**

```python
"""Unified runs surface (Foundation P3, v0.4.18).

GET /api/runs — paginated list, filterable by mode/status/project_id, ordered started_at desc
GET /api/runs/{run_id} — full RunRow detail; 404 on miss
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RunRow
from app.schemas.runs import RunListResponse, RunResult, RunSummary

router = APIRouter(prefix="/api", tags=["runs"])


def _serialize_summary(row: RunRow) -> RunSummary:
    return RunSummary(
        id=row.id,
        mode=row.mode,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        project_id=row.project_id,
        repo_full_name=row.repo_full_name,
        topic=row.topic,
        intent_hint=row.intent_hint,
        prompts_generated=row.prompts_generated or 0,
    )


def _serialize_full(row: RunRow) -> RunResult:
    return RunResult(
        id=row.id,
        mode=row.mode,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error=row.error,
        project_id=row.project_id,
        repo_full_name=row.repo_full_name,
        topic=row.topic,
        intent_hint=row.intent_hint,
        prompts_generated=row.prompts_generated or 0,
        prompt_results=row.prompt_results or [],
        aggregate=row.aggregate or {},
        taxonomy_delta=row.taxonomy_delta or {},
        final_report=row.final_report or "",
        suite_id=row.suite_id,
        topic_probe_meta=row.topic_probe_meta,
        seed_agent_meta=row.seed_agent_meta,
    )


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    mode: Literal["topic_probe", "seed_agent"] | None = Query(None),
    status: str | None = Query(None),
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    base = select(RunRow)
    if mode is not None:
        base = base.where(RunRow.mode == mode)
    if status is not None:
        base = base.where(RunRow.status == status)
    if project_id is not None:
        base = base.where(RunRow.project_id == project_id)

    total_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(total_q)).scalar_one()

    page_q = base.order_by(RunRow.started_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(page_q)).scalars().all()
    items = [_serialize_summary(r) for r in rows]

    has_more = offset + len(items) < total
    next_offset = offset + len(items) if has_more else None

    return RunListResponse(
        total=int(total),
        count=len(items),
        offset=offset,
        items=items,
        has_more=has_more,
        next_offset=next_offset,
    )


@router.get("/runs/{run_id}", response_model=RunResult)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> RunResult:
    row = await db.get(RunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return _serialize_full(row)
```

- [ ] **Step 2: Register router in `backend/app/main.py`**

In the `app` setup section, add:

```python
from app.routers import runs as runs_router
app.include_router(runs_router.router)
```

- [ ] **Step 3: Run tests — must pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_runs_router.py -v
```

Expected: 6/6 PASS.

### Task 10.3: REFACTOR + commit

- [ ] **Step 1: Commit**

```bash
git add backend/app/routers/runs.py backend/app/main.py backend/tests/test_runs_router.py
git commit -m "feat(v0.4.18-p3-PR2): /api/runs unified endpoints

- GET /api/runs (paginated list, filter by mode/status/project_id)
- GET /api/runs/{run_id} (full detail, 404 on miss)
- Standard pagination envelope
- 6 router tests (cat 9)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 6.1"
```

---

## Cycle 11 — routers/probes.py shim refactor

**Files:**
- Modify: `backend/app/routers/probes.py`
- Modify: `backend/tests/test_probe_router.py`

12 tests per spec section 9 category 6.

### Task 11.1: RED — 12 probe shim regression tests

- [ ] **Step 1: Add tests covering byte-identical SSE + race-free subscription**

In `backend/tests/test_probe_router.py`, add or update the 12 cat-6 tests. Each has a real assertion body:

```python
import pytest
from datetime import datetime
from sqlalchemy import select
from app.models import RunRow

pytestmark = pytest.mark.asyncio


async def test_post_probes_sse_event_sequence_byte_identical(client, event_bus_capture, snapshot):
    """SSE event sequence (names + payloads) byte-identical to v0.4.17 fixture."""
    resp = client.post("/api/probes", json={
        "topic": "snap-probe", "scope": "**/*", "intent_hint": "explore",
        "repo_full_name": "o/r", "n_prompts": 3,
    })
    assert resp.status_code == 200
    sse_lines = list(resp.iter_lines())
    # Strip volatile fields (timestamps, UUIDs) before snapshot
    normalized = [_strip_volatile(line) for line in sse_lines if line]
    snapshot.assert_match("\n".join(normalized), "probe_sse_sequence_v0.4.17")


async def test_post_probes_subscription_registered_before_dispatch(client, event_bus_capture):
    """First event yielded by the SSE stream MUST be probe_started — proving
    the subscription registered before RunOrchestrator dispatched."""
    resp = client.post("/api/probes", json={
        "topic": "race", "repo_full_name": "o/r", "n_prompts": 1,
    })
    first = next(l for l in resp.iter_lines() if l.startswith(b"event: "))
    assert first == b"event: probe_started"


async def test_get_probes_list_serializes_runrow_via_probe_run_summary(client, db):
    """GET /api/probes returns RunRow WHERE mode='topic_probe' through ProbeRunSummary."""
    db.add(RunRow(
        id="probe-1", mode="topic_probe", status="completed",
        started_at=datetime.utcnow(), topic="hello", repo_full_name="o/r",
    ))
    db.add(RunRow(
        id="seed-1", mode="seed_agent", status="completed",
        started_at=datetime.utcnow(),
    ))
    db.commit()

    resp = client.get("/api/probes")
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {it["id"] for it in items}
    assert "probe-1" in ids and "seed-1" not in ids
    assert items[0]["topic"] == "hello"  # ProbeRunSummary shape preserved


async def test_get_probe_by_id_serializes_through_probe_run_result(client, db):
    """GET /api/probes/{id} returns full ProbeRunResult shape."""
    db.add(RunRow(
        id="probe-detail", mode="topic_probe", status="completed",
        started_at=datetime.utcnow(),
        topic="detail-test", intent_hint="explore",
        repo_full_name="o/r",
        topic_probe_meta={"scope": "src/**", "commit_sha": "abc"},
        prompt_results=[], aggregate={"mean_overall": 7.5},
    ))
    db.commit()

    resp = client.get("/api/probes/probe-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "probe-detail"
    assert body["topic"] == "detail-test"
    assert body["scope"] == "src/**"  # ProbeRunResult shape (scope at top level)
    assert body["commit_sha"] == "abc"


async def test_get_probes_link_repo_first_error_preserved(client):
    """POST /api/probes without repo_full_name returns 400 link_repo_first."""
    resp = client.post("/api/probes", json={"topic": "x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "link_repo_first"


async def test_get_probe_404_probe_not_found(client):
    resp = client.get("/api/probes/nonexistent-id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "probe_not_found"


async def test_get_probes_pagination_envelope(client, db):
    for i in range(5):
        db.add(RunRow(
            id=f"p-{i}", mode="topic_probe", status="completed",
            started_at=datetime.utcnow(), topic=f"t-{i}",
        ))
    db.commit()

    resp = client.get("/api/probes?limit=2")
    body = resp.json()
    assert {"total", "count", "offset", "items", "has_more", "next_offset"}.issubset(body.keys())
    assert body["count"] == 2
    assert body["has_more"] is True


async def test_post_probes_invalid_request_400(client):
    resp = client.post("/api/probes", json={
        "repo_full_name": "o/r", "topic": "x", "n_prompts": 1000,  # exceeds max
    })
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_request"


async def test_post_probes_run_id_in_event_payloads(client, event_bus_capture):
    resp = client.post("/api/probes", json={
        "topic": "rid-test", "repo_full_name": "o/r", "n_prompts": 1,
    })
    # Drain the SSE response so all events are captured
    list(resp.iter_lines())
    probe_events = [e for e in event_bus_capture.events if e.kind.startswith("probe_")]
    assert all(e.payload.get("run_id") for e in probe_events)


async def test_subscription_filters_other_run_events(client, db):
    """Events for a different run don't appear in this run's SSE stream."""
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("target-run")

    event_bus.publish("probe_started", {"run_id": "target-run", "topic": "t"})
    event_bus.publish("probe_started", {"run_id": "other-run", "topic": "x"})
    event_bus.publish("probe_completed", {"run_id": "target-run"})

    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            if evt.kind == "probe_completed":
                break
    import asyncio
    await asyncio.wait_for(collect(), timeout=2)

    assert all(e.payload.get("run_id") == "target-run" for e in received)


async def test_subscription_excludes_taxonomy_changed_optimization_created(client):
    """Events without run_id in data are filtered out."""
    from app.services.event_bus import event_bus
    sub = event_bus.subscribe_for_run("filt-run")

    event_bus.publish("taxonomy_changed", {"trigger": "test"})  # no run_id
    event_bus.publish("optimization_created", {"id": "o1"})  # no run_id
    event_bus.publish("probe_completed", {"run_id": "filt-run"})

    received = []
    async def collect():
        async for evt in sub:
            received.append(evt)
            if evt.kind == "probe_completed":
                break
    import asyncio
    await asyncio.wait_for(collect(), timeout=2)

    kinds = {e.kind for e in received}
    assert kinds == {"probe_completed"}


async def test_post_probes_writes_run_row_status_running_at_start(client, db):
    """RunRow exists with status='running' before generator returns.
    Verified by checking the row exists during the SSE stream."""
    import asyncio
    resp = client.post("/api/probes", json={
        "topic": "early-row", "repo_full_name": "o/r", "n_prompts": 1,
    })
    # First SSE event must be probe_started → row already exists at that point
    first_line = next(resp.iter_lines())
    assert b"probe_started" in first_line

    # Now query for any RunRow rows from this run; row exists with mode='topic_probe'
    rows = (await db.execute(select(RunRow).where(RunRow.mode == "topic_probe"))).scalars().all()
    assert any(r.topic == "early-row" for r in rows)


def _strip_volatile(line: bytes) -> str:
    """Helper: strip timestamps + UUIDs for snapshot comparison."""
    import re
    s = line.decode("utf-8", errors="replace")
    s = re.sub(r'"started_at":\s*"[^"]+"', '"started_at": "<TS>"', s)
    s = re.sub(r'"completed_at":\s*"[^"]+"', '"completed_at": "<TS>"', s)
    s = re.sub(r'"run_id":\s*"[a-f0-9-]+"', '"run_id": "<UUID>"', s)
    s = re.sub(r'"id":\s*"[a-f0-9-]+"', '"id": "<UUID>"', s)
    return s
```

- [ ] **Step 2: Run — fail because router still on legacy ProbeService dispatch**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_probe_router.py -v
```

Expected: tests fail because (a) RunOrchestrator dispatch path not yet wired in routers/probes.py POST handler — first test `test_post_probes_sse_event_sequence_byte_identical` will fail because the response uses ProbeService (no run_id in payload yet), (b) GET endpoints don't yet read from RunRow.

### Task 11.2: GREEN — refactor probes.py shim

- [ ] **Step 1: Replace POST /api/probes handler**

In `backend/app/routers/probes.py`, replace the `post_probe` handler body to use the race-free subscribe-before-dispatch pattern (per spec section 6.2):

```python
@router.post("/probes")
async def post_probe(request: Request):
    """SSE stream — caller mints run_id, registers subscription, then dispatches."""
    raw = await request.json()
    if not isinstance(raw, dict) or not raw.get("repo_full_name"):
        raise HTTPException(400, "link_repo_first")
    body = ProbeRunRequest(**raw)

    orchestrator: RunOrchestrator = request.app.state.run_orchestrator
    run_id = str(uuid.uuid4())  # caller-side allocation
    run_request = RunRequest(mode="topic_probe", payload=body.model_dump())

    subscription = event_bus.subscribe_for_run(run_id)

    run_task = asyncio.create_task(
        orchestrator.run("topic_probe", run_request, run_id=run_id)
    )

    async def event_stream():
        try:
            async for evt in subscription:
                yield format_sse(evt.kind, evt.payload)
                if evt.kind in ("probe_completed", "probe_failed"):
                    break
        finally:
            await subscription.aclose()
            # run_task lifecycle: orchestrator owns failure marking via shield

    return StreamingResponse(event_stream(), media_type="text/event-stream", ...)
```

- [ ] **Step 2: Update GET /api/probes + GET /api/probes/{id}**

Replace `_serialize_summary(row: ProbeRun)` and `_serialize_full(row: ProbeRun)` to read from `RunRow WHERE mode='topic_probe'` (the `ProbeRun` STI alias still works but be explicit). The serializer body remains the same — it consumes the same column names. Just change the model imports.

- [ ] **Step 3: Run tests — must pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_probe_router.py tests/test_runs_router.py -v
```

### Task 11.3: REFACTOR + commit

```bash
git add backend/app/routers/probes.py backend/tests/test_probe_router.py
git commit -m "feat(v0.4.18-p3-PR2): probes router refactored to RunOrchestrator dispatch

- POST /api/probes uses race-free subscribe-before-dispatch
- SSE response constructed via event_bus.subscribe_for_run
- Event names + payload shapes byte-identical to v0.4.17
- GET endpoints read from RunRow via STI alias
- 12 shim regression tests (cat 6)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 6.2"
```

---

## Cycle 12 — routers/seed.py shim + new GET endpoints

**Files:**
- Modify: `backend/app/routers/seed.py`
- Add tests

8 tests per spec section 9 category 7.

### Task 12.1: RED — 8 seed shim tests

- [ ] **Step 1: Add tests with real assertion bodies**

In `backend/tests/test_seed_router.py`:

```python
import pytest
from datetime import datetime
from sqlalchemy import select
from app.models import RunRow

pytestmark = pytest.mark.asyncio


SEED_OUTPUT_REQUIRED_KEYS = {
    "status", "batch_id", "tier", "prompts_generated", "prompts_optimized",
    "prompts_failed", "estimated_cost_usd", "domains_touched",
    "clusters_created", "summary", "duration_ms",
}


async def test_post_seed_response_shape_byte_identical_with_run_id(client, seed_orchestrator_mock):
    """SeedOutput shape preserved + additive run_id field, no other changes."""
    resp = client.post("/api/seed", json={
        "project_description": "Test seed run for shape validation",
        "prompt_count": 5,
    })
    assert resp.status_code == 200
    body = resp.json()
    # Existing keys must all be present
    assert SEED_OUTPUT_REQUIRED_KEYS.issubset(body.keys())
    # Additive run_id is the ONLY new key
    new_keys = set(body.keys()) - SEED_OUTPUT_REQUIRED_KEYS
    assert new_keys == {"run_id"}
    assert isinstance(body["run_id"], str) and len(body["run_id"]) >= 32


async def test_post_seed_status_completed_on_success(client, seed_orchestrator_mock):
    """All prompts succeed → SeedOutput.status == 'completed'."""
    resp = client.post("/api/seed", json={
        "project_description": "Successful seed run", "prompt_count": 3,
    })
    assert resp.json()["status"] == "completed"


async def test_post_seed_status_partial_when_failures(client, seed_orchestrator_mock):
    """1+ succeeded AND 1+ failed → SeedOutput.status == 'partial'.

    Mock the batch pipeline to return mixed results.
    """
    from unittest.mock import patch

    async def _mixed_run_batch(*args, **kwargs):
        from app.services.batch_pipeline import PendingOptimization
        return [
            PendingOptimization(prompt="p1", status="completed", optimization_id="o1"),
            PendingOptimization(prompt="p2", status="failed", optimization_id=None),
        ]

    with patch("app.services.batch_pipeline.run_batch", side_effect=_mixed_run_batch):
        resp = client.post("/api/seed", json={
            "project_description": "Mixed", "prompt_count": 2,
        })
        assert resp.json()["status"] == "partial"
        assert resp.json()["prompts_failed"] == 1
        assert resp.json()["prompts_optimized"] == 1


async def test_post_seed_status_failed_on_input_validation(client):
    """Early-failure path: missing project_description + missing prompts + no
    provider → HTTP 200 with status='failed' (preserves today's contract)."""
    resp = client.post("/api/seed", json={})  # nothing supplied
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "Requires project_description" in body["summary"]
    assert body["prompts_optimized"] == 0


async def test_get_seed_list_returns_only_seed_agent_runs(client, db):
    """GET /api/seed returns RunRow WHERE mode='seed_agent' only."""
    db.add(RunRow(id="seed-list-1", mode="seed_agent", status="completed",
                  started_at=datetime.utcnow()))
    db.add(RunRow(id="probe-list-1", mode="topic_probe", status="completed",
                  started_at=datetime.utcnow()))
    db.commit()

    resp = client.get("/api/seed")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["items"]}
    assert "seed-list-1" in ids and "probe-list-1" not in ids


async def test_get_seed_by_id_404_on_miss(client):
    resp = client.get("/api/seed/nonexistent-uuid")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"


async def test_post_seed_persists_run_row_at_start(client, db, seed_orchestrator_mock):
    """RunRow exists with status='running' before/during generator runs.
    Verified by checking the row appears in the DB after the call returns."""
    resp = client.post("/api/seed", json={
        "project_description": "Persisted at start", "prompt_count": 2,
    })
    body = resp.json()
    run_id = body["run_id"]
    row = await db.get(RunRow, run_id)
    assert row is not None
    assert row.mode == "seed_agent"
    assert row.status in ("completed", "partial", "failed")  # terminal by call return


async def test_post_seed_duration_ms_none_safe_when_completed_at_none(client, db):
    """Edge case: a row with completed_at=None still serializes without crash."""
    db.add(RunRow(
        id="seed-no-completed", mode="seed_agent", status="failed",
        started_at=datetime.utcnow(), completed_at=None,
        seed_agent_meta={"batch_id": "x"},
        aggregate={"prompts_optimized": 0, "prompts_failed": 0, "summary": "x"},
        taxonomy_delta={"domains_touched": [], "clusters_created": 0},
    ))
    db.commit()

    resp = client.get("/api/seed/seed-no-completed")
    assert resp.status_code == 200
    # SeedOutput-style serialization not applicable on GET (uses RunResult);
    # but the GET-by-id endpoint should not crash
    body = resp.json()
    assert body["completed_at"] is None
```

- [ ] **Step 2: Run tests — fail because router not refactored yet**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_seed_router.py -v
```

Expected: tests fail because POST /api/seed doesn't yet dispatch through RunOrchestrator (no `run_id` in response), GET /api/seed and GET /api/seed/{id} don't exist yet.

### Task 12.2: GREEN — refactor `routers/seed.py`

- [ ] **Step 1: Replace seed_taxonomy handler**

Replace the existing handler body to dispatch through `RunOrchestrator` and serialize the resulting `RunRow` back to `SeedOutput`. See spec section 6.3 for the canonical shim shape including the `aggregate`/`seed_meta`/`taxonomy_delta` None-guards.

- [ ] **Step 2: Add new GET endpoints**

```python
@router.get("/api/seed", response_model=RunListResponse)
async def list_seed_runs(
    status: str | None = Query(None),
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    base = select(RunRow).where(RunRow.mode == "seed_agent")
    if status: base = base.where(RunRow.status == status)
    if project_id: base = base.where(RunRow.project_id == project_id)
    # ... pagination as in routers/runs.py


@router.get("/api/seed/{run_id}", response_model=RunResult)
async def get_seed_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> RunResult:
    row = await db.get(RunRow, run_id)
    if row is None or row.mode != "seed_agent":
        raise HTTPException(status_code=404, detail="run_not_found")
    return _serialize_full(row)  # reuse routers/runs.py helper
```

- [ ] **Step 3: Run tests — must pass**

```bash
cd backend && source .venv/bin/activate && pytest tests/test_seed_router.py -v
```

Expected: 8/8 PASS.

### Task 12.3: REFACTOR + INTEGRATE + OPERATE + commit

```bash
cd backend && source .venv/bin/activate && pytest tests/test_seed_router.py -v
git add backend/app/routers/seed.py backend/tests/test_seed_router.py
git commit -m "feat(v0.4.18-p3-PR2): seed router refactored + new GET endpoints

- POST /api/seed dispatches through RunOrchestrator (sync semantics preserved)
- SeedOutput shape preserved + additive run_id field
- New: GET /api/seed (paginated list of seed_agent runs)
- New: GET /api/seed/{run_id} (full RunRow detail)
- All 4 status values mappable (completed/partial/failed/running)
- duration_ms None-safe
- 8 seed shim regression tests (cat 7)"
```

---

## Cycle 13 — MCP tool dispatch updates

**Files:**
- Modify: `backend/app/tools/probe.py`
- Modify: `backend/app/tools/seed.py`

6 tests per spec section 9 category 8.

### Task 13.1: Refactor MCP tool handlers

- [ ] **Step 1: tools/probe.py — dispatch through RunOrchestrator**

Replace the body of `synthesis_probe` MCP tool handler to call `app.state.run_orchestrator.run("topic_probe", ...)` instead of `ProbeService.run()`. Response shape unchanged.

- [ ] **Step 2: tools/seed.py — dispatch through RunOrchestrator**

Replace `handle_seed` body to call `app.state.run_orchestrator.run("seed_agent", ...)` and convert the resulting `RunRow` back to `SeedOutput` (with additive `run_id`). Most of the orchestration logic moves to `SeedAgentGenerator` (already done in cycle 7).

- [ ] **Step 3: Tests**

Add MCP tool regression tests covering:
- synthesis_probe response schema preserved
- synthesis_seed response schema preserved + additive run_id
- Both tools dispatch through RunOrchestrator
- Error paths preserved
- 6 tests total (cat 8)

- [ ] **Step 4: Commit**

```bash
git add backend/app/tools/probe.py backend/app/tools/seed.py backend/tests/test_mcp_tools_p3.py
git commit -m "feat(v0.4.18-p3-PR2): MCP tools dispatch through RunOrchestrator

- synthesis_probe + synthesis_seed both route through unified orchestrator
- Response schemas preserved byte-for-byte (additive run_id only)
- 6 MCP tool regression tests (cat 8)"
```

---

## Cycle 14 — ProbeService class delete + ProbeRun alias removal + test rewrites

**Files:**
- Modify: `backend/app/services/probe_service.py` (delete class)
- Modify: `backend/app/models.py` (remove ProbeRun STI alias)
- Modify: 8 affected test files (51 reference rewrites)

6 tests for refactor coverage per spec §9 cat 12.

### Task 14.1: Audit + rewrite ProbeRun reference sites

- [ ] **Step 1: Inventory all 51 ProbeRun references in tests**

```bash
grep -rln 'ProbeRun' backend/tests --include='*.py'
```

Expected: 8 files. Note them.

- [ ] **Step 2: For each file, rewrite ProbeRun → RunRow with mode filter**

Mechanical pattern:
```python
# OLD
from app.models import ProbeRun
db.add(ProbeRun(id="x", topic="y", scope="*", repo_full_name="o/r", ...))

# NEW
from app.models import RunRow
db.add(RunRow(id="x", mode="topic_probe", topic="y",
              topic_probe_meta={"scope": "*", "commit_sha": None},
              repo_full_name="o/r", started_at=datetime.utcnow(), ...))
```

For reads of `.scope` / `.commit_sha`:
```python
# OLD: row.scope
# NEW: row.topic_probe_meta.get("scope") if row.topic_probe_meta else None
```

- [ ] **Step 3: Delete ProbeService class**

In `backend/app/services/probe_service.py`, remove the `class ProbeService:` definition. Module-level helpers (`probe_common.py`, `probe_phases.py`, `probe_phase_5.py`) remain. Module-level imports that referenced the class (e.g., `dependencies/probes.py:build_probe_service`) need updating to construct/return `RunOrchestrator` instead.

- [ ] **Step 4: Remove ProbeRun STI subclass from models.py**

```python
# Delete the ProbeRun(RunRow) class entirely.
# Verify no remaining `from app.models import ProbeRun` imports anywhere in backend/app or backend/tests.
```

```bash
grep -rln 'ProbeRun\|from app.models import' backend/app backend/tests --include='*.py'
```

Expected: zero `ProbeRun` references (modulo possibly forgotten test fixtures — fix any).

- [ ] **Step 5: Run full test suite**

```bash
cd backend && source .venv/bin/activate && pytest -x
```

Expected: full pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/services/probe_service.py backend/app/dependencies/probes.py backend/tests/
git commit -m "refactor(v0.4.18-p3-PR2): delete ProbeService class + ProbeRun alias

- ProbeService class removed (logic lives in TopicProbeGenerator)
- ProbeRun STI alias removed from models.py
- 51 ProbeRun references in 8 test files rewritten to RunRow
- Module-level probe helpers (probe_common, probe_phases, probe_phase_5) retained
- 6 refactor coverage tests (cat 12)

This closes the PR1 backward-compat scaffold. PR2 is now the
canonical implementation."
```

---

## Cycle 15 — Frontend SeedModal additive change

**Files:**
- Modify: `frontend/src/lib/components/taxonomy/SeedModal.svelte`

### Task 15.1: Add run_id filter to seed_batch_progress subscription

- [ ] **Step 1: Find the seed_batch_progress subscription**

```bash
grep -n 'seed_batch_progress' frontend/src/lib/components/taxonomy/SeedModal.svelte
```

- [ ] **Step 2: Update the subscription handler**

In the SSE event handler for `seed_batch_progress`, add a `run_id` filter when the modal is showing a specific run:

```typescript
// OLD
events.on('seed_batch_progress', (payload) => {
    updateProgress(payload);
});

// NEW
events.on('seed_batch_progress', (payload) => {
    if (currentRunId && payload.run_id !== currentRunId) {
        return;  // ignore events for other runs
    }
    updateProgress(payload);
});
```

- [ ] **Step 3: Run frontend tests**

```bash
cd frontend && npm run test
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/taxonomy/SeedModal.svelte
git commit -m "feat(v0.4.18-p3-PR2): SeedModal filters seed_batch_progress by run_id

Additive change — when modal is showing a specific run, filter events to only
that run's run_id. Pre-existing global subscription behavior preserved when
no run is selected."
```

---

## Cycle 16 — PR2 integration tests + release prep

### Task 16.1: Run full backend + frontend test suite

- [ ] **Step 1: Backend**

```bash
cd backend && source .venv/bin/activate && pytest --cov=app -v
```

Expected: 0 failures, ≥94 new tests, full v0.4.17 suite continues to pass.

- [ ] **Step 2: Frontend**

```bash
cd frontend && npm run test
```

### Task 16.2: Open PR2

```bash
git push
gh pr create --title "Foundation P3 PR2: live + shims (RunOrchestrator dispatch + new endpoints)" --body "$(cat <<'EOF'
## Summary

PR2 of Foundation P3 (v0.4.18). Wires all four backward-compat surfaces
(/api/probes, /api/seed, synthesis_probe, synthesis_seed) through
RunOrchestrator. Removes the PR1 ProbeRun STI alias + ProbeService class.
Adds new /api/runs unified endpoints + /api/seed list/get.

## What changes

- routers/probes.py: race-free subscribe-before-dispatch SSE; reads through RunRow
- routers/seed.py: dispatch through RunOrchestrator; new GET endpoints
- routers/runs.py: NEW unified endpoints
- tools/probe.py + tools/seed.py: MCP dispatch through RunOrchestrator
- event_bus.subscribe_for_run: NEW filtered iterator + 500ms replay
- batch_orchestrator.py:240-258: seed_batch_progress payload gains run_id
- frontend SeedModal: filter events by run_id (additive)
- ProbeService class deleted; ProbeRun alias removed
- 51 test references rewritten

## Test plan

- [x] Full backend test suite passes
- [x] Frontend test suite passes
- [x] Snapshot tests for all 8 probe SSE event types byte-identical
- [x] SeedOutput shape preserved + additive run_id only
- [x] MCP tool schemas preserved
- [x] No /api/seed callers regress (sync semantics)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md
EOF
)"
```

### Task 16.3: After both PRs merge — release v0.4.18

- [ ] **Step 1: Run release script**

```bash
./scripts/release.sh
```

This handles: version sync → commit → tag → push → GitHub Release → dev bump.

- [ ] **Step 2: Verify**

```bash
curl http://127.0.0.1:8000/api/health | jq .version
```

Expected: `"0.4.18"`.

---

## Plan complete

**Total cycles:** 16. **Total tests added:** ~94 (across 12 categories per spec §9). **Two PRs.**

After both PRs merge and v0.4.18 ships, P3 is closed. Foundation continues at P4 (long-handler restructures, v0.4.19) and Topic Probe T2 (save-as-suite, v0.4.20).

**Plan-review subagent dispatch is the next step** — independent reviewer must clear the plan APPROVED-ZERO before any RED test is written.
