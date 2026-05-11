# Topic Probe Tier 2 — design

**Target release:** v0.4.22
**Status:** Design — not yet implemented
**Authors:** Claude Opus 4.7 + andre.zen799@gmail.com (brainstorming session 2026-05-11)
**Prerequisites:** Foundation P3 (v0.4.18, `RunRow` substrate) ✓ · Foundation P4 (v0.4.21, long-handler restructures + audit-hook soak) ✓
**Companions:** `docs/ROADMAP.md` "Topic Probe Tier 2" entry · `docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md` · `docs/superpowers/specs/2026-05-10-foundation-p4-long-handler-restructure-design.md`

---

## 1 Scope

Per ROADMAP lines 332-337 + 302 (audit-hook flip ride-along):

1. **save-as-suite** — fork a completed `RunRow(mode='topic_probe')` into a frozen `ValidationSuite` fixture
2. **replay** — re-run a saved suite against current code state via `RunRow(mode='replay_run')`
3. **/api/health regression alarm + StatusBar badge** — suite-level mean drops ≥ tolerance from baseline
4. **UI** — "Topic Probe" tab in `SeedModal`, live taxonomy mini-view during run, final report card with copy-as-markdown + Save-as-Suite + Replay
5. **Topic-only grounding mode** — drops Phase 1; no linked-repo requirement; non-developer verticals
6. **202 Accepted + polling** — `Prefer: respond-async` header on `POST /api/probes` decouples long runs from client timeout
7. **Audit-hook WARN→RAISE flip** — `WRITE_QUEUE_AUDIT_HOOK_RAISE` default flips `False → True` after the v0.4.21 7-day soak gate clears (≥2026-05-18)

## 2 Architecture

```
                ┌─────────────────────────────────────────────────────┐
                │  Topic Probe T2 — save / replay / regression-alert  │
                └─────────────────────────────────────────────────────┘

  RunRow (P3, existing)                ValidationSuite (NEW)              RunRow (NEW mode)
  ─────────────────────                ────────────────────                ──────────────────
  mode='topic_probe'   ◄── source ─── id                                   mode='replay_run'
  status: completed                    source_run_id  ──┐                  suite_id  ────►
  prompt_results                       prompts_snapshot │                  prompt_results
  aggregate                            baseline_scores  │                  aggregate
                                       tolerance_abs    │
                                       project_id       │
                                       label            │
                                                        │
        ┌────────── save-as-suite (POST) ────────────────┘
        │
        │  ┌────────── replay (POST) ─────────────► new RunRow
        │  │                                          │
        ▼  ▼                                          ▼
  ┌─────────────────┐                          ┌────────────────┐
  │ /api/suites/*   │                          │ batch_pipeline │  (existing primitive)
  │ /api/health/*   │                          │ run_single_    │
  │ regression alarm│                          │   prompt       │
  └─────────────────┘                          └────────────────┘
```

**Key architectural choice:** Suite-as-fixture vs Run-as-execution are conceptually distinct and get distinct tables. `RunRow.suite_id` was opened by P3 prework (`models.py:643`) specifically for this. T2 hardens it with FK + index. Alternative (suite = `RunRow.mode='validation_suite'` with `status='frozen'`) was rejected: bends the `running/completed/failed/partial` execution-event model and complicates T3 schedule integration.

**Components touched / added:**

| Component | Change |
|---|---|
| `models.py` | NEW `ValidationSuite` ORM (12 cols + 3 indexes); `RunRow.mode` gains string-convention value `'replay_run'` |
| `services/validation_suite_service.py` | NEW — `create_from_run()`, `retire()`, `get()`, `list()`, `list_replays()`, `compute_regression_alarm()` |
| `services/run_orchestrator.py` | EXTEND — dispatch path for `mode='replay_run'`; NEW `dispatch_async()` for 202 callers |
| `services/generators/replay_run_generator.py` | NEW — `RunGenerator` Protocol impl; loads suite snapshot, reruns each prompt via `batch_pipeline.run_single_prompt` |
| `services/generators/topic_probe_generator.py` | EXTEND — `grounding_mode: Literal['codebase', 'topic_only']`; topic_only skips Phase 1 |
| `routers/suites.py` | NEW — 6 endpoints |
| `routers/probes.py` | EXTEND — `grounding_mode` body field; `Prefer: respond-async` header → 202 |
| `routers/health.py` | EXTEND — `regression_alarm` block |
| `tools/save_suite.py`, `tools/replay_suite.py` | NEW MCP handlers |
| `mcp_server.py` | Register 2 new tools (15 → 17 total) |
| `schemas/validation_suite.py` | NEW — Pydantic models |
| `schemas/mcp_models.py` | EXTEND — `SaveSuiteResult`, `ReplayInitiatedResult` |
| `prompts/probe-agent-topic-only.md` | NEW template; `prompts/manifest.json` entry |
| `app/config.py` | FLIP `WRITE_QUEUE_AUDIT_HOOK_RAISE` default `False → True` |
| Alembic `<rev>_validation_suite_topic_probe_t2.py` | NEW migration |
| Frontend `components/probes/{TopicProbeForm,TopicProbeProgressView,TopicProbeReportCard,TaxonomyMiniView}.svelte` | NEW |
| Frontend `components/suites/{SuitesPanel,SuiteRow,SuiteDetailView,RegressionBadge}.svelte` | NEW |
| Frontend `components/SeedModal.svelte`, `StatusBar.svelte` | EXTEND |
| Frontend `stores/probesStore.ts`, NEW `stores/suitesStore.ts`, `lib/api.ts` | EXTEND / NEW |

## 3 Data model

### NEW table `validation_suite`

| Column | Type | Constraint | Purpose |
|---|---|---|---|
| `id` | `String` PK | `uuid4().hex` | Stable identifier |
| `source_run_id` | `String` FK → `run_row.id` `ondelete=SET NULL`, NOT NULL on insert | Provenance |
| `prompts_snapshot` | `JSON` NOT NULL | `[{raw_prompt, intent_label, original_optimization_id?}]` |
| `baseline_scores` | `JSON` NOT NULL | `{mean_overall, p5, p95, per_prompt: [{raw_prompt_idx, overall, dimensions}], task_type_distribution}` |
| `tolerance_abs` | `Float` NOT NULL, default `0.5` | Absolute point delta on 10-pt scale (ROADMAP-aligned) |
| `label` | `String(120)` NOT NULL | User-supplied; trimmed; `1..120` chars |
| `project_id` | `String` FK → `prompt_cluster.id`, nullable | ADR-005 multi-project |
| `repo_full_name` | `String` nullable | Informational `repo_drift` flag on replay |
| `created_at` | `DateTime` NOT NULL | |
| `retired_at` | `DateTime` nullable | Soft-delete; immutable suites |
| `retired_reason` | `String(500)` nullable | Audit trail |

**Indexes:**
- `ix_validation_suite_project_id`
- `ix_validation_suite_source_run_id`
- `ix_validation_suite_active` — partial `WHERE retired_at IS NULL` on `(created_at DESC)`

### CHANGE `RunRow`

- **Add FK** `fk_run_row_suite_id` on `suite_id` → `validation_suite.id` `ondelete=SET NULL` (via `batch_alter_table` for SQLite)
- **Add index** `ix_run_row_suite_id` on `(suite_id, started_at DESC)` — backs regression-alarm query
- `mode` string-convention value `'replay_run'` added (no DDL — `mode` is `String`); update class docstring

### Key invariants

1. **Suites are immutable** post-create. Only `retired_at`/`retired_reason` mutable, via dedicated endpoint.
2. **Position correspondence** — `baseline_scores.per_prompt[i]` ↔ `prompts_snapshot[i]` ↔ replay's `RunRow.prompt_results[i]`. Position frozen at save time.
3. **Retired suites cannot be replayed** — 409 `suite_retired`.
4. **`RunRow.suite_id IS NOT NULL` iff `mode='replay_run'`** — service-layer invariant; enforced at write time by `RunOrchestrator`.
5. **Only `topic_probe` runs forkable** — `replay_run` and `seed_agent` runs cannot be saved as suites (raises `not_a_probe_run` 400).

### Migration `<rev>_validation_suite_topic_probe_t2.py`

```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "validation_suite" not in inspector.get_table_names():
        op.create_table(
            "validation_suite",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("source_run_id", sa.String(), nullable=False),
            sa.Column("prompts_snapshot", sa.JSON(), nullable=False),
            sa.Column("baseline_scores", sa.JSON(), nullable=False),
            sa.Column("tolerance_abs", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("repo_full_name", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.Column("retired_reason", sa.String(500), nullable=True),
            sa.ForeignKeyConstraint(["source_run_id"], ["run_row.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["project_id"], ["prompt_cluster.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_validation_suite_project_id", "validation_suite", ["project_id"])
        op.create_index("ix_validation_suite_source_run_id", "validation_suite", ["source_run_id"])
        op.create_index(
            "ix_validation_suite_active",
            "validation_suite",
            ["created_at"],
            postgresql_where=sa.text("retired_at IS NULL"),
            sqlite_where=sa.text("retired_at IS NULL"),
        )

    with op.batch_alter_table("run_row") as batch:
        batch.create_foreign_key(
            "fk_run_row_suite_id", "validation_suite",
            ["suite_id"], ["id"], ondelete="SET NULL",
        )
    op.create_index("ix_run_row_suite_id", "run_row", ["suite_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_run_row_suite_id", table_name="run_row")
    with op.batch_alter_table("run_row") as batch:
        batch.drop_constraint("fk_run_row_suite_id", type_="foreignkey")
    op.drop_index("ix_validation_suite_active", table_name="validation_suite")
    op.drop_index("ix_validation_suite_source_run_id", table_name="validation_suite")
    op.drop_index("ix_validation_suite_project_id", table_name="validation_suite")
    op.drop_table("validation_suite")
    # NOTE: existing run_row rows with mode='replay_run' preserved as harmless strings.
```

### ORM declaration

```python
class ValidationSuite(Base):
    """Frozen prompt fixture forked from a completed topic_probe run.

    Immutable after creation — replays write new ``RunRow(mode='replay_run',
    suite_id=...)`` rows; regression alarm joins on ``suite_id`` to compare
    each replay's aggregate against ``baseline_scores``.

    Tier 2 (v0.4.22) — see docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md.
    """
    __tablename__ = "validation_suite"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("run_row.id", ondelete="SET NULL"), nullable=False,
    )
    prompts_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    baseline_scores: Mapped[dict] = mapped_column(JSON, nullable=False)
    tolerance_abs: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5",
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("prompt_cluster.id", ondelete="SET NULL"), nullable=True,
    )
    repo_full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_validation_suite_project_id", "project_id"),
        Index("ix_validation_suite_source_run_id", "source_run_id"),
        Index(
            "ix_validation_suite_active", "created_at",
            sqlite_where=text("retired_at IS NULL"),
            postgresql_where=text("retired_at IS NULL"),
        ),
    )
```

## 4 REST + MCP surface

### NEW router `app/routers/suites.py`

| Method + Path | Body / Query | Status | Rate limit | Returns |
|---|---|---|---|---|
| `POST /api/probes/{run_id}/save-as-suite` | `{label: str[1..120], tolerance_abs?: float = 0.5}` | 201 | 20/min | `ValidationSuiteOut` |
| `GET /api/suites` | `?project_id=&repo_full_name=&include_retired=false&limit=20&offset=0` | 200 | — | `Paginated[ValidationSuiteListItem]` |
| `GET /api/suites/{suite_id}` | — | 200 / 404 | — | `ValidationSuiteOut` |
| `GET /api/suites/{suite_id}/replays` | `?limit=20&offset=0` | 200 | — | `Paginated[RunOut]` |
| `POST /api/suites/{suite_id}/replay` | — | 202 | 5/min | `ReplayRunOut` |
| `POST /api/suites/{suite_id}/retire` | `{reason: str[1..500]}` | 200 | 30/min | `ValidationSuiteOut` |

### EXTEND `app/routers/probes.py`

`POST /api/probes` gains:

1. **`grounding_mode: Literal['codebase', 'topic_only'] = 'codebase'`** — `topic_only` skips Phase 1 grounding + bypasses `link_repo_first` precondition.
2. **`Prefer: respond-async` header (RFC 7240)** — explicit opt-in to 202+polling path.

```
HTTP/1.1 202 Accepted
Location: /api/probes/{run_id}
Retry-After: 5
{"run_id": "...", "status": "running", "poll_url": "/api/probes/..."}
```

**Default behavior preserved** when header absent — SSE response unchanged. Auto-flip based on `n_prompts > 10` deliberately rejected (would silently break SSE contract).

Replay endpoint **always 202** — replays are full pipeline runs with no <30s case.

### EXTEND `app/routers/health.py`

```json
"regression_alarm": {
  "suites_total": 12,
  "suites_in_alarm": 2,
  "latest_alarms": [
    {
      "suite_id": "ab12…",
      "label": "embedding-cache-invalidation",
      "baseline_mean": 7.85,
      "latest_mean": 7.21,
      "delta_abs": -0.64,
      "tolerance_abs": 0.5,
      "latest_replay_id": "cd34…",
      "latest_replay_at": "2026-05-12T14:30:00Z"
    }
  ]
}
```

30s `TTLCache` (matches `taxonomy/sub_domain_readiness.py` pattern). Suites without any completed replays excluded — no alarm fires until ≥1 replay exists.

### NEW MCP tools (15 → 17)

| Tool | Args | Output |
|---|---|---|
| `synthesis_save_suite` | `run_id, label[1..120], tolerance_abs?: float = 0.5` | `SaveSuiteResult{suite_id, source_run_id, label, baseline_mean, tolerance_abs, prompts_count, created_at}` |
| `synthesis_replay_suite` | `suite_id` | `ReplayInitiatedResult{run_id, suite_id, mode: 'replay_run', poll_url, started_at}` |

Listings deliberately REST-only (matches `synthesis_history` precedent — no `synthesis_list_runs` either).

### Error envelope (`{code, message}` per existing convention)

| Code | Status | Trigger |
|---|---|---|
| `run_not_found` | 404 | save-as-suite on missing run |
| `run_not_completed` | 409 | save on `status != 'completed'` |
| `not_a_probe_run` | 400 | save on `mode != 'topic_probe'` |
| `invalid_label` | 400 | empty / >120 / non-string |
| `invalid_tolerance` | 400 | outside `[0.1, 5.0]` |
| `suite_not_found` | 404 | get/replay/retire missing suite |
| `suite_retired` | 409 | replay on retired suite |
| `topic_only_unavailable` | 400 | (defensive — reserved for kill-switch) |
| `link_repo_first` | 400 | (existing) `grounding_mode='codebase'` without linked repo |

`suite_repo_drift` is **informational, not an error** — returned as `ReplayRunOut.warnings: ['repo_drift']` (200 OK) when suite vs current linked repo names differ.

### Pydantic schemas (`schemas/validation_suite.py`)

```python
class SaveSuiteRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    tolerance_abs: float = Field(0.5, ge=0.1, le=5.0)

class RetireSuiteRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)

class ValidationSuiteOut(BaseModel):
    id: str; source_run_id: str; label: str; tolerance_abs: float
    project_id: str | None; repo_full_name: str | None
    created_at: datetime; retired_at: datetime | None; retired_reason: str | None
    prompts_snapshot: list[PromptSnapshotItem]
    baseline_scores: BaselineScoresPayload

class ValidationSuiteListItem(BaseModel):
    # Same minus JSON payloads — for fast listing
    id: str; source_run_id: str; label: str; tolerance_abs: float
    project_id: str | None; repo_full_name: str | None
    created_at: datetime; retired_at: datetime | None
    prompts_count: int; baseline_mean: float

class ReplayRunOut(BaseModel):
    run_id: str; suite_id: str
    mode: Literal['replay_run']
    status: Literal['running']
    started_at: datetime; poll_url: str
    warnings: list[Literal['repo_drift']] = []

class RegressionAlarmEntry(BaseModel):
    suite_id: str; label: str
    baseline_mean: float; latest_mean: float; delta_abs: float
    tolerance_abs: float
    latest_replay_id: str; latest_replay_at: datetime

class RegressionAlarmBlock(BaseModel):
    suites_total: int
    suites_in_alarm: int
    latest_alarms: list[RegressionAlarmEntry]
```

### Write-queue operation labels (new)

- `validation_suite_create`
- `validation_suite_retire`
- `replay_run_persist`

## 5 Services + generators + topic-only mode

### NEW `app/services/validation_suite_service.py`

Stateless class. Detached-ORM-safe per Foundation P4 contract: read in short sessions, hand frozen snapshots to write-queue callbacks, no DB session held over any I/O.

```python
@dataclass(frozen=True)
class SuiteSnapshotInputs:
    """Pure data crossing the write-queue boundary."""
    suite_id: str               # uuid4().hex, minted upfront
    source_run_id: str
    label: str
    tolerance_abs: float
    project_id: str | None
    repo_full_name: str | None
    prompts_snapshot: list[dict]
    baseline_scores: dict
    created_at: datetime


class ValidationSuiteService:
    async def create_from_run(
        self, run_id: str, *, label: str, tolerance_abs: float,
        db: AsyncSession, write_queue: WriteQueue,
    ) -> ValidationSuiteOut:
        # Read phase — validate + build snapshot
        run = await db.scalar(select(RunRow).where(RunRow.id == run_id))
        if run is None: raise ValueError("run_not_found")
        if run.status != "completed": raise ValueError("run_not_completed")
        if run.mode != "topic_probe": raise ValueError("not_a_probe_run")
        if not run.aggregate or "mean_overall" not in (run.aggregate or {}):
            raise ValueError("run_missing_aggregate")

        snapshot = self._build_snapshot_inputs(run, label, tolerance_abs)
        # No session held below this line.

        await write_queue.submit(
            partial(_persist_suite_create, snapshot=snapshot),
            timeout=30,
            operation_label="validation_suite_create",
        )
        return ValidationSuiteOut.from_snapshot(snapshot)

    async def retire(self, suite_id, *, reason, db, write_queue) -> ValidationSuiteOut:
        """Idempotent — re-retire is a no-op success."""
        ...

    async def compute_regression_alarm(self, *, db) -> RegressionAlarmBlock:
        """30s TTLCache."""
        ...

    # get(), list(), list_replays() — read-only thin wrappers
```

**Regression-alarm SQL** (cached 30s):

```sql
SELECT s.id, s.label, s.tolerance_abs, s.baseline_scores,
       r.id AS replay_id, r.aggregate AS replay_aggregate, r.started_at
FROM validation_suite s
JOIN (
    SELECT suite_id, MAX(started_at) AS max_started
    FROM run_row
    WHERE mode = 'replay_run' AND status = 'completed' AND suite_id IS NOT NULL
    GROUP BY suite_id
) lr ON lr.suite_id = s.id
JOIN run_row r ON r.suite_id = s.id AND r.started_at = lr.max_started
WHERE s.retired_at IS NULL;
```

Python-side: filter where `replay_aggregate['mean_overall'] < baseline_scores['mean_overall'] - tolerance_abs`. `ix_run_row_suite_id` keeps this O(active_suites).

### NEW `app/services/generators/replay_run_generator.py`

```python
@dataclass(frozen=True)
class ReplayRunRequest:
    suite_id: str
    project_id: str | None
    repo_full_name: str | None


class ReplayRunGenerator:
    """Re-runs a frozen suite through the full pipeline.

    Per-prompt: batch_pipeline.run_single_prompt with full enrichment + scoring.
    Emits probe_prompt_completed + probe_completed events for SSE-consumer
    parity. Skips probe_grounding / probe_generating (no Phase 1+2).
    """

    async def run(self, req, *, run_id) -> GeneratorResult:
        # 1. Load suite snapshot
        suite_snapshot = await self._load_suite_snapshot(req.suite_id)
        if suite_snapshot.retired_at is not None:
            raise ValueError("suite_retired")

        # 2. Repo drift check (informational only)
        warnings = []
        if (suite_snapshot.repo_full_name and req.repo_full_name
            and suite_snapshot.repo_full_name != req.repo_full_name):
            warnings.append("repo_drift")
            _emit_event("probe_warning", run_id=run_id, code="repo_drift", ...)

        # 3. Per-prompt execution (positional-index preserving)
        N = len(suite_snapshot.prompts_snapshot)
        prompt_results = [None] * N  # pre-sized for index parity
        sem = asyncio.Semaphore(PROBE_PROMPT_CONCURRENCY)
        async with asyncio.TaskGroup() as tg:
            for idx, item in enumerate(suite_snapshot.prompts_snapshot):
                tg.create_task(self._execute_one(
                    sem, run_id, idx, item, suite_snapshot, out=prompt_results,
                ))

        # 4. Aggregate
        aggregate = _compute_aggregate(prompt_results)

        # 5. Return — orchestrator persists terminal row
        return GeneratorResult(
            prompt_results=prompt_results,
            aggregate=aggregate,
            taxonomy_delta=None,
            final_report=None,
            warnings=warnings,
            mode_meta={"suite_id": req.suite_id, "warnings": warnings},
        )
```

**Why reuse the full pipeline?** Regression detection must catch *pipeline-level* drift, not just LLM-output drift. The suite is the input contract; the pipeline (classification + enrichment + scoring) is what's being regression-tested.

**Concurrency:** module-level `PROBE_PROMPT_CONCURRENCY` semaphore shared between probe + replay.

**Event reuse:** replay emits `probe_prompt_completed` + `probe_completed` — same types as topic probe. SSE consumers don't learn new event family. New event: `probe_warning(code='repo_drift', ...)`.

### EXTEND `app/services/generators/topic_probe_generator.py`

```python
@dataclass(frozen=True)
class TopicProbeRequest:
    topic: str
    n_prompts: int
    intent_hint: str | None
    scope: list[str] | None
    project_id: str | None
    repo_full_name: str | None
    grounding_mode: Literal["codebase", "topic_only"] = "codebase"   # NEW


async def run(self, req, *, run_id):
    if req.grounding_mode == "codebase":
        probe_context = await self._phase_grounding(req, run_id)
    else:
        probe_context = ProbeContext(
            topic=req.topic, intent_hint=req.intent_hint,
            relevant_files=[], explore_synthesis_excerpt=None,
            known_domains=[], repo_full_name=None, commit_sha=None,
            topic_only=True,
        )
        # No probe_grounding event emitted

    template_name = (
        "probe-agent.md" if req.grounding_mode == "codebase"
        else "probe-agent-topic-only.md"
    )
    prompts = await probe_generation.generate_probe_prompts(
        template_name=template_name, context=probe_context, mode=req.grounding_mode,
        ...
    )
    ...
```

`ProbeContext` adds `topic_only: bool = False`. Downstream phases consume `.relevant_files` etc. — empty list is a valid degenerate state.

### NEW prompt template `prompts/probe-agent-topic-only.md`

Two-template strategy (not in-template conditionals) — matches `optimize.md`/`refine.md` precedent. Hot-reloaded via `PromptLoader`. Manifest entry. Lifespan validates both at startup.

Topic-only template instructs the LLM to generate prompts WITHOUT code references. F1 backtick-density validator inverts for topic-only mode: `<5% backticks` floor (topic-only prompts SHOULD NOT cite code). `probe_generation.generate_probe_prompts(mode=...)` swaps the validator.

### EXTEND `app/services/run_orchestrator.py`

```python
if mode == "topic_probe":
    generator = TopicProbeGenerator(provider=self._provider, ...)
    request = TopicProbeRequest.from_payload(payload)
elif mode == "seed_agent":
    generator = SeedAgentGenerator(...)
    request = SeedAgentRequest.from_payload(payload)
elif mode == "replay_run":                              # NEW
    generator = ReplayRunGenerator(provider=self._provider, ...)
    request = ReplayRunRequest(
        suite_id=payload["suite_id"],
        project_id=payload.get("project_id"),
        repo_full_name=payload.get("repo_full_name"),
    )
else:
    raise ValueError(f"unknown run mode: {mode}")
```

`RunRow.suite_id` set on the initial INSERT for `replay_run`. Existing `current_run_id` ContextVar, `asyncio.shield()`, 4-status transitions all apply unchanged. Persistence routes through `WriteQueue.submit(_persist_run_terminal, operation_label='replay_run_persist')`.

**NEW `dispatch_async()`** for 202+polling: awaits ONLY the initial `INSERT(status='running')` via `WriteQueue.submit()`, then `asyncio.create_task(...)` for the long-running generator + terminal persist. The existing SSE entry path is restructured to internally call `dispatch_async()` + subscribe — eliminating two-codepath divergence.

## 6 UI surface

**Chromatic encoding** (axiom 4: color = data):

| Surface | Color | Anchor |
|---|---|---|
| Topic Probe identity (tab active, mini-view ring, form focus) | **neon-teal `#00d4aa`** | Extraction / focused exploration |
| Save-as-Suite (Hero button) | **neon-purple `#a855f7`** | Processed, elevated artifact |
| Replay (Medium button) | **neon-blue `#4d8eff`** | Diagnostic / analytical |
| Regression alarm firing | **neon-red `#ff3366`** | Danger |
| Regression alarm nominal | **neon-green `#22ff88`** | Health |

Brand gradient (`cyan → purple`) deliberately NOT used on Save-as-Suite — solid purple preserves color-as-data axiom. Gradient stays reserved for hero brand moments.

### Density pins (IDE layout table)

| Surface | Height | Padding | Gap | Type |
|---|---|---|---|---|
| SeedModal tab bar | h-7 | px-2 | gap-1 | text-[11px] |
| Tab cell (TOPIC PROBE) | h-7 | px-1.5 | gap-0.5 | text-[11px] uppercase 700 `font-display` |
| Form rows | h-7 (toolbar) | px-1.5 | gap-1.5 | text-[11px] |
| Run / Save-Suite / Replay buttons | **h-5** | px-2 | — | text-[10px] mono, line-height 18px |
| Copy-md icon button | 20×20 | — | — | 16px icon, centered |
| Suite list rows | h-5 | px-1 | — | text-[10px], labels `text-dim` |
| SuitesPanel container | — | p-1.5 | space-y-1.5 | — |
| SuiteDetailView card interior | — | p-1 | — | — |
| Section divider (`▌ TAXONOMY DELTA`) | — | — | — | text-[10px] uppercase 700 `font-display` letter-spacing 0.1em |
| Page heading (`TOPIC PROBE COMPLETED`) | — | — | — | text-[11px] uppercase 700 `font-display` letter-spacing 0.1em |
| Score values | — | — | — | text-[10px] `font-mono` tabular |
| TaxonomyMiniView row | h-4 | px-1 | — | text-[10px] `font-mono` for counts |
| `NEW` chip | h-3.5 | px-1 | — | text-[9px] `font-mono` 500, 1px neon-teal contour |
| StatusBar regression badge | h-4 | px-1.5 | — | text-[10px] `font-mono` |

**Banned (asserted at cycle 14 audit):** `p-2`+, `gap-2`+, `h-7` on buttons, `px-2.5`+ on tab cells, `border: 2px`, any `box-shadow` with spread/blur, any `text-shadow`/`filter`/`radial-gradient` to transparent on UI elements, banned vocab (`glow`/`halo`/`bloom`/`radiance`) in 2D-UI directories.

### Contour-intensity tier mapping

| Button | Tier | Resting | Hover | Active |
|---|---|---|---|---|
| Save-Suite | **Hero** | `1px solid neon-purple` + 8% fill | 14% fill + `translateY(-1px)` | `translateY(0)`, border mutes |
| Replay | **Medium** | `1px solid border-subtle` | `1px solid neon-blue` + 12% fill | `inset 0 0 0 1px rgba(77, 142, 255, 0.4)`, `translateY(0)` |
| Copy-md icon | **Small** | `1px solid border-subtle` + `bg-input` | `1px solid neon-cyan` + 8% fill | `inset 0 0 0 1px rgba(0, 229, 255, 0.4)` |
| Run | **Hero** (cyan) | `1px solid neon-cyan` + 8% fill | 14% fill + `translateY(-1px)` | `translateY(0)` |
| Tab (active) | **Micro** | transparent bottom border | dim teal bottom border | full teal bottom border |
| Suite row | **Large** | `1px solid border-subtle` `bg-card` | `inset 0 0 0 1px rgba(0, 212, 170, 0.4)` `bg-hover` | `translateY(0)` |

Multi-property transitions in a single declaration:

```css
transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
            border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
            transform 150ms cubic-bezier(0.16, 1, 0.3, 1),
            color 200ms cubic-bezier(0.16, 1, 0.3, 1);
```

Exit easing: `cubic-bezier(0.4, 0, 1, 1)`.

### Forge motion personality assignments

| Action | Stage | Animation |
|---|---|---|
| Run probe (click) | Optimize | `scale-in` 300ms |
| Save-as-Suite (click → toast) | Validate | `forge-spark` one-shot on button → toast `slide-in-right` |
| Replay (click) | Strategy | `slide-in-right` on new replay row |
| Emergent taxonomy node | Validate | `forge-spark` on the new node |
| Regression nominal → firing | Validate | `forge-spark` on StatusBar badge once, then static red |
| Copy-as-markdown | Validate | `copy-flash` 150ms cyan tint + check icon swap |
| Report card mount | Optimize | `dialog-in` 300ms (scale 0.96→1 + opacity 0→1) |
| Per-prompt progress strip cell fill | Analyze | `slide-up-in` per cell as events arrive |

All respect `prefers-reduced-motion → 0.01ms`.

### Voice

| Surface | Copy |
|---|---|
| Save button | `SAVE SUITE` |
| Replay button | `REPLAY` |
| Badge nominal | `12 OK` |
| Badge firing | `2 ALARM` |
| Save toast | `Suite saved: <label>` |
| Empty SuitesPanel | `No suites. Save a probe to create one.` |
| Topic-only hint | `Prompts without code grounding.` |
| Regression tooltip | `2 suites · mean below tolerance` |
| Replay-complete (alarm) | `<label> · −0.64 vs baseline` |
| Replay-complete (nominal) | `<label> · within tolerance` |

### NEW components

| Path | Purpose |
|---|---|
| `frontend/src/lib/components/probes/TopicProbeForm.svelte` | Form — topic textarea + grounding-mode segmented control + scope file picker + N slider + intent dropdown |
| `frontend/src/lib/components/probes/TopicProbeProgressView.svelte` | Active-run view (source-agnostic SSE/poll) |
| `frontend/src/lib/components/probes/TopicProbeReportCard.svelte` | Final report + actions |
| `frontend/src/lib/components/probes/TaxonomyMiniView.svelte` | ~200px emergent-taxonomy tree |
| `frontend/src/lib/components/suites/SuitesPanel.svelte` | Left-nav panel |
| `frontend/src/lib/components/suites/SuiteRow.svelte` | Row (h-5) with status dot + delta |
| `frontend/src/lib/components/suites/SuiteDetailView.svelte` | Detail + replay history + per-prompt baseline-vs-latest table |
| `frontend/src/lib/components/suites/RegressionBadge.svelte` | StatusBar badge mount |

### EXTENDED components

| Path | Change |
|---|---|
| `frontend/src/lib/components/SeedModal.svelte` | Sixth tab "TOPIC PROBE" |
| `frontend/src/lib/components/StatusBar.svelte` | Mount RegressionBadge |
| Left-nav (Navigator) | "SUITES" entry → SuitesPanel |

### NEW + EXTENDED stores

| Path | Purpose |
|---|---|
| `frontend/src/lib/stores/suitesStore.ts` (NEW) | Suites list + selected + regression alarm cache (polled from /api/health, refreshed on `taxonomy_changed` SSE) |
| `frontend/src/lib/stores/probesStore.ts` (EXTEND) | 202+polling abstraction — `runProbe()` returns an async iterable; source-agnostic for components. Threshold: `n_prompts > 10` → auto-attach `Prefer: respond-async` |

### `frontend/src/lib/api.ts` additions

```typescript
saveSuite(runId: string, label: string, toleranceAbs?: number): Promise<ValidationSuiteOut>
replaySuite(suiteId: string): Promise<ReplayRunOut>
getSuites(filters?: {projectId?, repoFullName?, includeRetired?}): Promise<Paginated<ValidationSuiteListItem>>
getSuite(suiteId: string): Promise<ValidationSuiteOut>
getSuiteReplays(suiteId: string): Promise<Paginated<RunOut>>
retireSuite(suiteId: string, reason: string): Promise<ValidationSuiteOut>
```

### Accessibility

- `:focus-visible` outline on every interactive element (`1px rgba(0, 229, 255, 0.3)` offset 2px)
- `prefers-reduced-motion` → 0.01ms on all keyframes
- StatusBar regression badge: `aria-live="polite"` `role="status"`
- Save-Suite / Replay buttons: `aria-label` matches visible text
- Color paired with text (`● 2 ALARM` not just `●`)

## 7 Audit-hook WARN→RAISE flip

**One-line change in `app/config.py`:**

```python
WRITE_QUEUE_AUDIT_HOOK_RAISE: bool = True   # was False
```

Existing logic at `database.py:395-397` already routes to `raise WriteOnReadEngineError(...)` when True. No behavioral code changes — only the default flips.

**Soak gate (mandatory pre-flip):**

```bash
# Run on or after 2026-05-18 (≥7 days post-v0.4.21 ship date 2026-05-11)
grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100
# Expect: zero new WARN sources beyond known/explained ones
```

If any new WARN source appears, flip blocks and the unexpected write path gets restructured before T2 ships.

**Sequencing:** flip is the **last commit** in the T2 PR train, after all other cycles green. Release tag v0.4.22 includes flip. Flip cycle merges no earlier than 2026-05-18.

**Kill switch:** operators can set `WRITE_QUEUE_AUDIT_HOOK_RAISE=false` env var. Documented in `backend/CLAUDE.md` write-queue contract section.

**New regression test** `test_audit_hook_default_is_raise` locks the flipped default.

**Extended integration test** `test_audit_hook_emits_zero_warn_under_full_t2_pipeline` covers: save-as-suite, replay, topic-only probe, retire — all run with zero `read-engine audit:` WARN.

## 8 202 Accepted + polling architecture

**Server side** in `routers/probes.py`:

```python
@router.post("/api/probes")
async def post_probe(
    request: ProbeRequest,
    prefer: str | None = Header(None),
    ...
):
    run_id = uuid4().hex
    poll_url = f"/api/probes/{run_id}"

    if prefer == "respond-async":
        await run_orchestrator.dispatch_async(
            mode="topic_probe", request=request, run_id=run_id,
        )
        return Response(
            status_code=202,
            headers={"Location": poll_url, "Retry-After": "5"},
            content=json.dumps({
                "run_id": run_id, "status": "running", "poll_url": poll_url,
            }),
            media_type="application/json",
        )

    # Existing SSE path (default — preserved verbatim)
    return await _sse_probe_response(request, run_id=run_id, ...)
```

`RunOrchestrator.dispatch_async()` awaits ONLY the initial `WriteQueue.submit(_insert_running_row, ...)` to land, then `asyncio.create_task(...)` for the long-running generator + terminal persist (existing `asyncio.shield()` contract).

**Race-safety:** initial `RunRow(status='running')` is committed before route returns. Client's first poll always finds the row.

**Task lifetime:** HTTP request returning 202 does NOT cancel the task. Existing GC sweep `_gc_orphan_runs` recovers any task killed by process restart.

**Replay** `POST /api/suites/{id}/replay`: always 202 — replays are full pipeline runs with no <30s case.

**Polling cost:** 1 indexed PK SELECT on `run_row` per poll (~1-2ms). 5s cadence × ~10min worst-case = 120 polls/probe. Negligible for single-user dev tool.

**Frontend:** `probesStore.runProbe()` auto-attaches `Prefer: respond-async` when `n_prompts > 10`. Below threshold → SSE (no behavioral change for typical 5-10 prompt probes).

## 9 Observability extensions

| Event | Path / Op | Payload additions |
|---|---|---|
| `validation_suite_created` | `path='validation_suite', op='created'` | `suite_id, source_run_id, label, tolerance_abs, prompts_count, baseline_mean, project_id` |
| `validation_suite_retired` | `path='validation_suite', op='retired'` | `suite_id, reason` |
| `probe_warning` (NEW type) | bus event during run | `run_id, code: Literal['repo_drift'], context` |
| `probe_replay_completed` (variant) | bus event | adds `suite_id, regression_detected, baseline_mean, latest_mean, delta_abs` |
| `regression_alarm_transition` | `path='regression_alarm', op='transition'` | `suite_id, label, previous_state ∈ {nominal, firing, none}, new_state, baseline_mean, latest_mean, delta_abs` — fires only on state change |

Per-evaluation alarm computation silent in JSONL (would be noisy at 30s cache cadence × N suites). State transitions durably logged.

**JSONL trace:** replay runs get `phase="replay_run"` trace. Suite creates/retires get `phase="validation_suite"`.

## 10 Testing strategy — 16 cycles, 7-dispatch TDD per cycle

### Protocol — `feedback_tdd_protocol.md` canon

> **REQUIRED SUB-SKILL:** Use `superpowers:subagent-driven-development`. Each cycle dispatches **5 fresh implementer subagents** (RED → GREEN → REFACTOR → INTEGRATE → OPERATE) followed by **2 independent validators** (spec-compliance reviewer + code-quality reviewer), iterated until **ZERO inconsistencies**. `APPROVED-WITH-MINOR` triggers re-dispatch — NOT proceed-with-notes. **Total: 7 dispatches per cycle. No phase skipping. No exceptions for "small" changes.**

```
RED ─▶ GREEN ─▶ REFACTOR ─▶ INTEGRATE ─▶ OPERATE ─▶ Validator 1 ─▶ Validator 2
 │       │         │            │            │            │              │
 │       │         │            │            │            └ spec compliance
 │       │         │            │            │                           │
 │       │         │            │            │                  ZERO inconsistencies?
 │       │         │            │            │                  ├ no → re-dispatch failing phase
 │       │         │            │            │                  └ yes → proceed
 │       │         │            │            │
 │       │         │            │            └ dynamic verification under live load
 │       │         │            └ canonical-pattern parity (static review)
 │       │         └ local code quality (lint, types, edges, docstrings)
 │       └ minimal implementation
 └ failing test documenting the contract
```

### Phase responsibility (per `feedback_tdd_protocol.md`)

| Defect class | Owning phase | Action |
|---|---|---|
| Missing test for new behavior | **RED** | Write failing test documenting contract |
| Code doesn't pass the test | **GREEN** | Minimal implementation |
| Lint / type / edge cases / error handling / DRY / docstrings | **REFACTOR** | Local code quality (ruff + mypy on touched files) |
| Service-call signature wrong / fictional kwargs (A1-A6) | **INTEGRATE** | Open target's `def`; column-by-column / kwarg-by-kwarg diff against canonical |
| User-visible end-state never queried / writer contention / cancellation / timeouts / cross-process / orphan rows (O1-O8) | **OPERATE** | Live concurrency run with full stack + DB inspection by ID |
| Cross-cycle architectural fit | **Independent code-reviewer** | Strategic concerns post-OPERATE |

### Cycle-specific INTEGRATE / OPERATE attention table

For each cycle, the most likely anti-patterns INTEGRATE + OPERATE must explicitly catch (in addition to running the full checklists):

| Cycle | Scope | ~RED tests | INTEGRATE focus | OPERATE focus |
|---|---|---|---|---|
| **A — Substrate (1-4)** | | | | |
| 1 | Migration + `ValidationSuite` ORM | 7 | A5 (test fixtures use real `ValidationSuite()` constructor, not `MagicMock`) | O2 (migration completes under WriteQueue active) |
| 2 | `ValidationSuiteService.create_from_run` + snapshot inputs | 11 | A4 (`SuiteSnapshotInputs` populates ALL columns; column-by-column diff against `ValidationSuite` ORM) · A5 | O1 (`SELECT * FROM validation_suite WHERE id=:s` after create — confirm row exists) |
| 3 | `.retire`, `.get`, `.list`, `.list_replays` | 9 | A4 (retire updates only `retired_at`/`retired_reason` — no other column mutated) | O1 (re-retire is idempotent and persists no-op) |
| 4 | `.compute_regression_alarm` + 30s TTL cache | 7 | A3 (Python-side filter matches canonical alarm-evaluation logic) | O2 (alarm query under concurrent `replay_run` writes — no inconsistent snapshots) · O6 (cross-process replay writes from MCP) |
| **B — REST + Generators (5-9)** | | | | |
| 5 | `routers/suites.py` (6 endpoints, rate limits, error envelopes) | 15 | A2 (use `WriteQueue.submit()` — do NOT re-implement persistence) · A4 (response payloads populate all `ValidationSuiteOut` fields) | O5 (client disconnects mid-replay-dispatch — no orphan rows) |
| 6 | `ReplayRunGenerator` + `RunOrchestrator.dispatch` extension | 11 | A2 (use `batch_pipeline.run_single_prompt` — NOT a hand-rolled inner pipeline) · A6 (every prompt receives full enrichment context — codebase + strategy intelligence + applied patterns) · A1 (read every field of `RunResult` — not just `overall`) | O8 (cancel mid-replay — `RunRow.status` reaches terminal `failed` via shielded write) · O4 (warm-engine debounce fires mid-replay — no contention) · O7 (replay's worst-case 30min duration — every timeout in path ≥ that plus buffer) |
| 7 | Topic-only mode — `TopicProbeGenerator` branch + 2nd template | 9 | A2 (don't re-implement Phase 2 generation — call existing `probe_generation.generate_probe_prompts`) · A3 (consume same return shape, both modes) | O1 (RunRow.topic_probe_meta.grounding_mode='topic_only' persisted and queryable) |
| 8 | 202+polling on `POST /api/probes` + `dispatch_async` | 7 | A2 (`dispatch_async` reuses existing `RunOrchestrator` lifecycle — no parallel codepath) | O8 (202 caller closes connection — task survives via `asyncio.shield()`) · O1 (poll endpoint reflects committed row immediately after 202 returns — INSERT awaited before response) · O5 (curl `--max-time` shorter than poll cadence — server-side state remains consistent) |
| 9 | `/api/health` regression_alarm block | 5 | A4 (RegressionAlarmEntry populates all required fields from joined query — no NULL leakage) | O2 (alarm query coexists with active replays — no read-write contention) |
| **C — MCP (10)** | | | | |
| 10 | `synthesis_save_suite` + `synthesis_replay_suite` | 9 | A2 (call `ValidationSuiteService` methods — don't re-implement) · A4 (`SaveSuiteResult` + `ReplayInitiatedResult` populate all fields) | O6 (cross-process MCP→backend bridge for `replay_run` writes works under load) · O5 (MCP client disconnects after `save_suite` returns — write completed and queryable) |
| **D — Frontend (11-13)** | | | | |
| 11 | Topic Probe tab + form + report card (Vitest + Playwright) | 18 | A2 (use existing `api.ts` helpers — don't fetch directly) · A3 (consume existing `RunOut` shape — every field rendered or intentionally hidden) | O1 (browser smoke: SeedModal renders, form submits, report card displays with all fields) |
| 12 | SuitesPanel + SuiteRow + SuiteDetailView + RegressionBadge + SuitesStore | 14 | A3 (suite list rows render every field; `ReplayRunOut.warnings` displayed when present) | O1 (browser smoke: SuitesPanel renders for empty / nominal / firing states; RegressionBadge transitions on SSE) |
| 13 | 202+polling abstraction in probesStore | 7 | A2 (single async-iterable contract — SSE + poll paths emit identical event shapes; no two-codepath divergence in consumers) | O5 (browser tab closes mid-probe — store cleans up poll interval) · O7 (long probe runs >10min — poll cadence persists, no client-side timeout) |
| **E — Quality (14)** | | | | |
| 14 | Brand audit + a11y audit (axe-core) | n/a | n/a | n/a (audit phase — no code) |
| **F — Flip + Release (15-16)** | | | | |
| 15 | Audit-hook RAISE flip + new regression test + extended integration test | 3 | A2 (no behavioral logic change — only the default flips) | **All O1-O8** — the extended integration test exercises every T2 write path under `WRITE_QUEUE_AUDIT_HOOK_RAISE=True` and must emit zero `read-engine audit:` lines |
| 16 | E2E validation workflow + soak grep verification | n/a (smoke) | n/a | O1 (probe → save-as-suite → replay → alarm round-trip with DB-level inspection at each stage) · O6 (full stack — backend + MCP + frontend — coexists during round-trip) |

**~131 new RED tests** + extended integration + E2E smoke. Backend ≥90% coverage, frontend ≥80%.

### Hard invariants (non-negotiable per `feedback_tdd_protocol.md`)

- The RED test still passes after every subsequent phase
- No new features / no scope creep beyond cycle's RED+GREEN scope
- Reviewers receive polished code at post-OPERATE handoff — focused on strategic concerns, not mechanical items the prior phases should have caught
- For cycles touching user-visible state: live integration evidence is part of the OPERATE completion claim. Claiming "OPERATE done, ready for review" without that evidence is incomplete
- Pure constants / pure schemas without a failing consumer test = scaffolding, NOT TDD — fold into the GREEN step of the consumer's cycle rather than committing as standalone RED work

### Validators — APPROVED-ZERO-INCONSISTENCIES bar

Both validator subagents (spec-compliance + code-quality) must return APPROVED-ZERO-INCONSISTENCIES. `APPROVED-WITH-MINOR` triggers re-dispatch of the failing phase — never proceed-with-notes per `feedback_tdd_protocol.md` precedent (foundation-p3 spec went 5 review rounds; foundation-p4 plan went 1).

## 11 Release sequencing

```
2026-05-11 (today) ─── v0.4.21 SHIPPED
                       │
                       └── T2 dev begins immediately (cycles 1-14)
                       │
2026-05-18 ────────── 7-day soak window closes
                       │
                       └── Cycles 15-16 unblocked (audit-hook flip)
                       │
2026-05-25 ─────────── Estimated v0.4.22 release window opens
2026-06-01 ─────────── Latest realistic v0.4.22 ship date
```

**Single feature branch** `feature/probe-tier-2`. **Rebase-merge** per `feedback_pr_merge_strategy.md`. Estimated **~140-170 commits** (16 cycles × 7 dispatches = ~112 implementer+validator commits + spec/plan/CHANGELOG/release commits + iteration commits when validators return inconsistencies). Foundation P3's 95-commit and P4's ~50-commit baselines provide reasonable bracketing.

### Pre-release checklist (gated)

1. `grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100` — zero unexpected sources in last 7 days
2. All 16 cycles GREEN — `pytest --cov=app -v` ≥90% backend coverage
3. `npm run test:coverage` ≥80% frontend coverage
4. Brand audit pass — no `box-shadow` with spread/blur, no `text-shadow`, no `filter: drop-shadow`, no banned vocab in `frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/`
5. A11y audit pass — axe-core clean on all new components
6. `alembic check` clean
7. `init.sh restart` smoke test — all services green
8. E2E validation: probe → save-as-suite → replay → regression detection round-trip
9. Backward-compat smoke: `POST /api/probes` without `Prefer:` header — SSE response unchanged
10. CHANGELOG entry written in canon voice (per `feedback_changelog_voice.md`)
11. Stale-doc fix: `backend/CLAUDE.md` T2/T3/T4 minor numbers harmonized with ROADMAP

### CHANGELOG preview (canon voice)

```
## v0.4.22 — 2026-MM-DD

### Added
- POST /api/probes/{id}/save-as-suite forks completed topic-probe runs into immutable ValidationSuite fixtures.
- POST /api/suites/{id}/replay re-runs frozen suites through the full pipeline; produces RunRow(mode='replay_run', suite_id=...).
- GET /api/suites + GET /api/suites/{id} + GET /api/suites/{id}/replays paginated views.
- POST /api/suites/{id}/retire soft-deletes a suite (idempotent).
- /api/health regression_alarm block surfaces suite-level mean drops below per-suite tolerance_abs (default 0.5).
- POST /api/probes accepts Prefer: respond-async header — returns 202 Accepted + Location + Retry-After; client polls GET /api/probes/{id} until terminal.
- Topic-only grounding mode (grounding_mode='topic_only') — skips Phase 1 codebase grounding; no linked-repo requirement.
- 2 new MCP tools: synthesis_save_suite, synthesis_replay_suite (15 → 17 tools).
- Topic Probe SeedModal tab + final report card with copy-as-markdown.
- SuitesPanel + SuiteDetailView for suite lifecycle management.
- StatusBar regression alarm badge — neon-green nominal, neon-red firing.
- New prompt template prompts/probe-agent-topic-only.md.
- New events: validation_suite_created, validation_suite_retired, probe_warning, probe_replay_completed, regression_alarm_transition.
- New operation labels: validation_suite_create, validation_suite_retire, replay_run_persist.

### Changed
- WRITE_QUEUE_AUDIT_HOOK_RAISE default flipped True. Writes to the read engine outside the allow-list now raise WriteOnReadEngineError. 7-day post-v0.4.21 soak window cleared. Operators can set WRITE_QUEUE_AUDIT_HOOK_RAISE=false as a kill switch.
- RunRow.suite_id gains FK to validation_suite.id (ON DELETE SET NULL) plus ix_run_row_suite_id index.
- RunRow.mode adds string-convention value 'replay_run' (no enum migration).
- backend/CLAUDE.md T2/T3/T4 minor numbers harmonized with docs/ROADMAP.md.
```

## 12 Risk register

| Risk | P | I | Mitigation |
|---|---|---|---|
| Migration partial apply on prod DB | L | H | Idempotency guard `inspector.has_table()`; `batch_alter_table` for SQLite FK; reversible downgrade; manual smoke on copy of `data/synthesis.db` |
| Replay false-positive regression (pipeline drift, not LLM drift) | M | M | User-facing tooltip explicit; 0.5 tolerance absorbs noise; T3+ per-dimension assertions if needed |
| Positional `baseline_scores.per_prompt[i]` drift on replay | L | H | Pre-sized `prompt_results = [None] * N` + index-keyed assignment; integration test asserts parity across N=10, concurrency=5 |
| Audit-hook flip surfaces unexpected WARN→RAISE site | M | H | 7-day soak gate; explicit grep verification; kill-switch env var; extended integration test |
| 202 client never polls — orphan `RunRow(running)` | M | L | `_gc_orphan_runs` lifespan sweep; SuitesPanel surfaces stuck rows |
| Polling stampede at multi-user scale | L | M | Single-user dev tool today; deferred; can add `Cache-Control: max-age=2` if needed |
| 30s alarm cache hides transitions | L | L | Transition events fire instantly via SSE; cache only affects /api/health polling |
| Topic-only regresses code-aware quality (template selection bug) | L | M | Two distinct templates + distinct validators; manifest validation at startup |
| Suite immutability bypass | L | H | No PATCH endpoint; service only has `create_from_run()` + `retire()`; integration test asserts |
| Brand drift in new components | M | L | Cycle 14 grep-based audit; banned-vocab + banned-CSS-property sweeps |
| A11y regression | L | M | Cycle 14 axe-core sweep; inherited focus pattern; `role="status"` `aria-live="polite"` on RegressionBadge |
| Unbounded suite count | L | L | User-curated artifacts; document; T3+ if pressure |
| `PROBE_CODEBASE_MAX_CHARS=40_000` insufficient on grown repo at replay time | L | L | Replay uses same enrichment budget as probes; `repo_drift` warning fires on rename |
| `synthesis_save_suite` MCP race (called before run completes) | M | L | Service raises `run_not_completed` 409; MCP retries after poll |
| Polling reads partial state | L | L | `WriteQueue.submit()` transactional; reads see committed state only |
| CHANGELOG voice drift | M | L | Pre-merge grep for first-person pronouns + conversational framing |

## 13 Success criteria

T2 ships when ALL true:

1. **Schema** — `validation_suite` + `ix_run_row_suite_id` exist; `alembic upgrade head` + `downgrade -1` round-trip cleanly on fresh test DB
2. **Cycle completion** — All 16 cycles complete the full 7-dispatch pipeline (RED → GREEN → REFACTOR → INTEGRATE → OPERATE → Validator 1 spec-compliance → Validator 2 code-quality) with **APPROVED-ZERO-INCONSISTENCIES** on both validators; ~131 new RED tests all GREEN; **no phase skipped** per `feedback_tdd_protocol.md` hard invariants
3. **Coverage** — Backend ≥90%, frontend ≥80%
4. **E2E round-trip** — Manual: probe → save-as-suite → manually degrade scoring weights → replay → alarm fires red on `/api/health` → revert weights → replay → alarm clears green
5. **Audit-hook flip** — `WRITE_QUEUE_AUDIT_HOOK_RAISE` defaults `True`; `test_audit_hook_emits_zero_warn_under_full_t2_pipeline` PASS; 7-day soak grep clean
6. **Backward compat** — `POST /api/probes` without `Prefer:` returns SSE identical to v0.4.21; T1 probes work end-to-end with zero behavior change; existing seed agent flows unchanged
7. **MCP** — `synthesis_save_suite` + `synthesis_replay_suite` structured_output verified; error envelopes for `run_not_completed`, `suite_retired`
8. **Brand audit** — Cycle 14 grep returns zero hits for `box-shadow.*[1-9]px [1-9]`, `text-shadow`, `filter: drop-shadow`, `(glow|halo|bloom|radiance)` in 2D UI directories
9. **A11y audit** — axe-core clean on all new components
10. **Observability** — All 5 new events emit via JSONL + ring buffer + SSE; visible in `ActivityPanel` filtered to `path=validation_suite` or `path=regression_alarm`
11. **Health endpoint** — `regression_alarm` block valid shape under all conditions; 30s cache respected
12. **Docs hygiene** — CHANGELOG entry under `## v0.4.22`; `backend/CLAUDE.md` T2/T3/T4 harmonized; project root `CLAUDE.md` shows v0.4.22 SHIPPED; `docs/SHIPPED.md` gains v0.4.22 entry

## 14 Scope hardness — IN T2 vs DEFERRED

**IN T2 (v0.4.22):**

- `ValidationSuite` ORM + migration
- Save-as-suite REST + MCP
- Replay REST + MCP (always 202)
- `RunRow.mode='replay_run'` + `ReplayRunGenerator`
- `/api/health` regression_alarm block
- Topic Probe SeedModal tab
- TopicProbeReportCard with copy-as-markdown
- Live TaxonomyMiniView during probe
- SuitesPanel + SuiteDetailView
- StatusBar regression badge (chromatic states)
- Topic-only grounding mode + 2nd prompt template
- 202 Accepted + polling on `POST /api/probes` (header opt-in)
- Audit-hook WARN→RAISE flip
- 2 new MCP tools

**DEFERRED to T3 (v0.4.23) — ROADMAP-locked:**

- `release.sh` CI hook: register suites as pre-release gates
- Probe → seed-agent promotion flow (`RunRow.mode` flip + write to `prompts/seed-agents/<slug>.md`)
- "Drill into cluster" auto-probe action on seed runs
- Cross-tier composition: probe-discovered prompts → seed-agent few-shot context

**DEFERRED to T4 (v0.4.24) — ROADMAP-locked:**

- Final UI consolidation: SeedModal collapses to one tab with two modes (template-driven / topic-driven)
- Single history surface backed by `RunRow` + `GET /api/runs`

**NEVER (in scope hardness; user-driven if demand surfaces):**

- ValidationSuite editing — suites are immutable
- ValidationSuite versioning / lineage chains — flat namespace
- Per-dimension or per-prompt assertions (T2 ships mean-overall-vs-tolerance only)
- `/schedule` integration for cron-driven replays — separate feature
- Auto-GC of old suites — user-curated
- Suite cloning / forking — retire-and-create-new is canonical

**EXPLICITLY EXCLUDED from save-as-suite trigger paths:**

- save-as-suite on `seed_agent` runs (only `topic_probe` forkable)
- save-as-suite on `replay_run` runs (no recursive forking)

Both enforced at service layer via `run.mode == 'topic_probe'` invariant → raises `not_a_probe_run` 400.
