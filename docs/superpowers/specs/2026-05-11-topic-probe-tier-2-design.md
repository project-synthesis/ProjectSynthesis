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
| `models.py` | NEW `ValidationSuite` ORM (11 cols + 3 indexes); `RunRow.mode` gains string-convention value `'replay_run'`; `RunRow.__table_args__` gains `Index("ix_run_row_suite_id", "suite_id", started_at.desc())` to keep `alembic check` clean per `bdd8e96cf489` precedent; **`RunRow.suite_id` column declaration extended** from `mapped_column(String, nullable=True)` (current at `models.py:643`) to `mapped_column(String, ForeignKey("validation_suite.id", ondelete="SET NULL"), nullable=True)` so the ORM-side FK declaration matches the migration's `fk_run_row_suite_id` constraint — without this, `alembic check` flags FK drift (same CI gate as the Index drift). Mirrors the FK declaration pattern at `a2f6d8e31b09` precedent (lines 136, 290, 493, 507 of models.py for cascade-FK columns) |
| `schemas/runs.py` | EXTEND — extend `Literal["topic_probe", "seed_agent"]` to `Literal["topic_probe", "seed_agent", "replay_run"]` on `RunRequest.mode`, `RunSummary.mode`, `RunResult.mode` (3 sites in schema file + `routers/runs.py::list_runs::mode` Query param = **4 sites total**) |
| `schemas/probes.py` | EXTEND — relax `ProbeContext.repo_full_name: str` → `str \| None = None`; add `commit_sha: str \| None = None`; add `topic_only: bool = False`. NOTE: existing fields `intent_hint: Literal["audit", "refactor", "explore", "regression-test"] = "explore"` (line 19) and `scope: str = "**/*"` (line 18) are NOT relaxed — defaults handle the topic-only path (the `model_config={"extra": "forbid"}` is preserved). |
| `schemas/validation_suite.py` | NEW — Pydantic models (request/response + nested payload types `PromptSnapshotItem`, `PerPromptScore`, `BaselineScoresPayload`) |
| `schemas/mcp_models.py` | EXTEND — `SaveSuiteOutput`, `ReplayInitiatedOutput` |
| `services/validation_suite_service.py` | NEW — `create_from_run()`, `retire()`, `get()`, `list()`, `list_replays()`, `compute_regression_alarm()` |
| `services/run_orchestrator.py` | EXTEND `_persist_final()` signature to take `mode: str` so the operation_label can be mode-keyed (`replay_run_persist` for replay, existing `run_orchestrator.persist_final` otherwise); update the single existing caller at `:86` to thread `mode` through. EXTEND `_extract_probe_meta()` (`:142`) to include `grounding_mode: request.payload.get('grounding_mode', 'codebase')` in the returned dict so `RunRow.topic_probe_meta.grounding_mode` is persisted + queryable. **`_extract_probe_meta()` continues to gate on `mode == "topic_probe"`** — `replay_run` rows have `RunRow.topic_probe_meta = NULL` (matches the existing `_extract_seed_meta()` mode-gating pattern at `run_orchestrator.py:151-163` (full function — gating check at `:152-153`, payload dict at `:154-163`)). EXTEND `_create_row()` body to **mode-gate** the `suite_id` assignment: `suite_id=(request.payload.get("suite_id") if mode == "replay_run" else None)`. This mirrors the existing mode-gated extraction pattern of `_extract_probe_meta`/`_extract_seed_meta` and enforces the §3 Key Invariant 4 (`RunRow.suite_id IS NOT NULL iff mode='replay_run'`) at the write site — a buggy caller that accidentally threads `suite_id` into a non-replay payload cannot break the invariant. Today's body at `:119-133` enumerates which columns it sets; the mode-gated `suite_id` is added to that enumeration. EXTEND lifespan registration to add `'replay_run'` generator (dispatch is generator-dict-based via `self._generators[mode]` — body unchanged). NEW private helper `_run_generator_and_persist(mode, request, run_id)` extracted from `run()` body and shared with the new `_run_to_completion` wrapper. NEW `dispatch_async()` for 202 callers (awaits initial `INSERT` only, spawns shielded background task with explicit `current_run_id` ContextVar set inside the spawned task). REFACTOR `run()` to a thin wrapper that calls `dispatch_async()` then awaits a completion event — preserves the "blocks until terminal" contract for SSE callers + tests while sharing the dispatch body. |
| `services/generators/replay_run_generator.py` | NEW — `RunGenerator` Protocol impl; consumes `RunRequest(mode='replay_run', payload={"suite_id": ..., "project_id": ..., "repo_full_name": ...})`. `__init__` takes the union of collaborators needed by `batch_pipeline.run_single_prompt`: `(provider, prompt_loader, embedding_service, session_factory, taxonomy_engine, domain_resolver, context_service, write_queue)` — this is a **NEW richer collaborator graph** (not a literal mirror of `SeedAgentGenerator.__init__` which is `(seed_orchestrator, write_queue)` only, nor of `TopicProbeGenerator.__init__` which is `(provider, repo_index_query, taxonomy_engine, *, context_service, embedding_service, session_factory, write_queue)`). Replay is structurally novel: it iterates prompts directly through `run_single_prompt` without going through a batch orchestrator or a probe-grounding phase, so its constructor takes the run_single_prompt collaborator union. Pre-fetches `historical_stats` once via `OptimizationService(read_db).get_score_distribution(exclude_scoring_modes=["heuristic"])` and threads to per-prompt children, matching `batch_orchestrator.run_batch:113-126` pattern. Reruns each prompt via `batch_pipeline.run_single_prompt`; routing tier = `internal` (deterministic regression detection — no sampling-driven nondeterminism) |
| `services/generators/topic_probe_generator.py` | EXTEND — read `grounding_mode: Literal['codebase', 'topic_only']` from `request.payload`; topic_only skips Phase 1. **No concurrency refactor in T2** — `PROBE_PROMPT_CONCURRENCY` is forward-declared per §5 concurrency note, not consumed in this release. |
| `services/generators/_constants.py` | NEW — module-level constants for the generators subpackage: `PROBE_PROMPT_CONCURRENCY: int = 5` (forward-declared value chosen to align with the batch-seeding `API=5` budget noted in root `CLAUDE.md` line 70 — different surface, but same single-user dev-tool latency profile). **NOT consumed in T2.** Reserved for T3+ parallelization with proper exception handling (see §5 concurrency note). |
| `services/generators/_aggregate.py` | NEW — shared aggregate-builder helper `compute_run_aggregate(prompt_results: list[dict]) -> dict` (extracted from the existing `TopicProbeGenerator._build_aggregate` instance method at `topic_probe_generator.py:406-444`). **Output keys mirror the canonical aggregate verbatim** to preserve any downstream consumers of `RunRow.aggregate`: `mean_overall`, `p5_overall`, `p50_overall`, `p95_overall`, `completed_count`, `failed_count`, `f5_flag_fires`, `scoring_formula_version`. **NEW additive keys** introduced by T2 — split by who sets them:
- Emitted **by the helper itself** (added to every call's output): `task_type_distribution: dict[str, int]` (new contract extension; T1 `_build_aggregate` did not compute this).
- Added **post-call by `ReplayRunGenerator.run()` only**, after invoking the helper: `replay_warnings: list[str]`, `replay_n_completed: int`, `replay_n_failed: int`, `replay_suite_id: str`. The helper itself NEVER emits these — they're appended to the helper's returned dict in the generator body (per §5 — the `aggregate = compute_run_aggregate(prompt_results)` call followed by `aggregate["replay_warnings"] = warnings` / `aggregate["replay_suite_id"] = suite_id` / `aggregate["replay_n_completed"] = n_completed` / `aggregate["replay_n_failed"] = n_failed` assignments). Non-replay callers (`TopicProbeGenerator`) get a clean canonical aggregate.

The `BaselineScoresPayload` Pydantic model in §4 uses the same `p5_overall`/`p95_overall` key naming to match the helper output. Both `TopicProbeGenerator` and `ReplayRunGenerator` import + call this helper; `_build_aggregate` instance method is refactored to a thin delegate that calls `compute_run_aggregate` (no caller-visible change since output keys are preserved). |
| `services/probe_generation.py` | EXTEND — add `mode: Literal['codebase', 'topic_only'] = 'codebase'` + `template_name: str = 'probe-agent.md'` kwargs to `generate_probe_prompts()`. `mode='topic_only'` selects the **inverted per-prompt predicate** (`_lacks_backtick` defined as `<5%` backtick density per prompt, i.e. drop prompts WITH backticks); the **batch drop threshold stays at `>50%`** (`_DROP_THRESHOLD=0.5`) per the existing F1 contract — the two thresholds operate at different levels and remain distinct. Defaults preserve backward-compat with all current callers. |
| `routers/suites.py` | NEW — 6 endpoints |
| `routers/probes.py` | EXTEND — `grounding_mode` body field on `ProbeRequest`; `Prefer: respond-async` header → 202 |
| `routers/runs.py` | EXTEND — `list_runs` `mode: Literal[...]` Query extended with `replay_run` |
| `routers/health.py` | EXTEND — `regression_alarm` block |
| `tools/save_suite.py`, `tools/replay_suite.py` | NEW MCP handlers |
| `mcp_server.py` | Register 2 new tools (15 → 17 total) |
| `prompts/probe-agent-topic-only.md` | NEW template; `prompts/manifest.json` entry |
| `app/main.py` | EXTEND lifespan — register `replay_run_generator` in the `RunOrchestrator(generators={...})` dict at `main.py:1191-1196` (canonical generator-dict-based dispatch) |
| `app/config.py` | FLIP `WRITE_QUEUE_AUDIT_HOOK_RAISE` default `False → True` |
| Alembic `<rev>_validation_suite_topic_probe_t2.py` | NEW migration (single forward-only, follows `bdd8e96cf489` precedent) |
| Frontend `components/probes/{TopicProbeForm,TopicProbeProgressView,TopicProbeReportCard,TaxonomyMiniView}.svelte` | NEW |
| Frontend `components/suites/{SuitesPanel,SuiteRow,SuiteDetailView,RegressionBadge}.svelte` | NEW |
| Frontend `components/taxonomy/SeedModal.svelte`, `components/layout/StatusBar.svelte` | EXTEND — note: `SeedModal.svelte` lives in `components/taxonomy/` (3D-scope directory) but is a 2D modal; T2 narrows the cycle-14 banned-vocab audit to `frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/` to avoid false positives on the 3D canon vocabulary used elsewhere in `taxonomy/` (per `.claude/skills/brand-guidelines/SKILL.md` line 81 / line 214) |
| Frontend `frontend/src/lib/stores/probesStore.ts` (EXTEND), NEW `frontend/src/lib/stores/suitesStore.ts`, NEW `frontend/src/lib/api/suites.ts` (per-domain module matching the existing `runs.ts`/`seed.ts`/`clusters.ts`/... convention under `lib/api/`; the project does NOT have a monolithic `api.ts` — verified by `ls frontend/src/lib/api/`) | EXTEND / NEW |
| Frontend `frontend/src/lib/utils/copy-feedback.svelte.ts` | REUSE existing `useCopyFlash()` primitive (canonical green `#22ff88` `copy-flash`; duration governed by the shipped `--duration-copy-flash` token in `app.css`, currently 1500ms — `component-patterns.md` line 219 records the original 600ms target, to be doc-sync'd to the shipped value at T2 release) — no new copy-feedback timing |
| `.claude/skills/brand-guidelines/SKILL.md` | EXTEND — (a) update canonical 2D-UI directory list at lines 79, 214, 375 from `{layout,editor,refinement,shared,landing}` to `{layout,editor,refinement,shared,landing,probes,suites}` so the brand canon and the cycle-14 audit scope agree; (b) update StatusBar height at line 140 from `22px` to `20px` per shipped `StatusBar.svelte:251` reality (parallels the `copy-flash` ship-vs-canon reconciliation) |
| `.claude/skills/brand-guidelines/references/component-patterns.md` | EXTEND — update `copy-flash` row (line 219) from `600ms` to the shipped `1500ms` per `--duration-copy-flash` token reality (doc-sync follow-up) |

## 3 Data model

### NEW table `validation_suite`

| Column | Type | Constraint | Purpose |
|---|---|---|---|
| `id` | `String` PK | `uuid4().hex` | Stable identifier |
| `source_run_id` | `String` FK → `run_row.id` `ondelete=SET NULL`, **nullable=True** (becomes NULL when source run is later deleted; suite remains intact for replay-history audit. NOT NULL would contradict SET NULL — picked nullability to honor the SET NULL semantics) | Provenance |
| `prompts_snapshot` | `JSON` NOT NULL | `[{raw_prompt, intent_label, original_optimization_id?}]` |
| `baseline_scores` | `JSON` NOT NULL | `{mean_overall, p5_overall, p50_overall, p95_overall, per_prompt: [{raw_prompt_idx, overall, dimensions}], task_type_distribution}` — key names match the canonical `compute_run_aggregate` output (see §2 `_aggregate.py` row) verbatim to preserve downstream-consumer compatibility with existing `RunRow.aggregate` readers |
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

- **Add FK** `fk_run_row_suite_id` on `suite_id` → `validation_suite.id` `ondelete=SET NULL` (via `batch_alter_table` for SQLite — table rebuild)
- **Add index** `ix_run_row_suite_id` on `(suite_id, started_at DESC)` — backs regression-alarm query (descending by `started_at` to serve "latest replay per suite" pattern)
- **Add `Index(...)` to `RunRow.__table_args__`** mirroring the migration: `Index("ix_run_row_suite_id", "suite_id", started_at.desc())` — required for `alembic check` clean exit per `bdd8e96cf489_consolidate_lifespan_ddl_into_alembic.py` precedent. Without this, the migration adds an index that the ORM doesn't declare and `alembic check` (CI gate) flags drift.
- `mode` string-convention value `'replay_run'` added at the DB level (no DDL — column is `String`); update class docstring. **Pydantic surface extensions (4 sites)**: `schemas/runs.py::RunRequest.mode` + `RunSummary.mode` + `RunResult.mode` Literal extends from `["topic_probe", "seed_agent"]` to `["topic_probe", "seed_agent", "replay_run"]`; `routers/runs.py::list_runs(mode: Literal[...] | None = Query(None))` query param adds the same value. Public API consequence: `GET /api/runs?mode=replay_run` becomes a documented filter.

### Key invariants

1. **Suites are immutable** post-create. Only `retired_at`/`retired_reason` mutable, via dedicated endpoint.
2. **Position correspondence** — `baseline_scores.per_prompt[i]` ↔ `prompts_snapshot[i]` ↔ replay's `RunRow.prompt_results[i]`. Position frozen at save time.
3. **Retired suites cannot be replayed** — 409 `suite_retired`.
4. **`RunRow.suite_id IS NOT NULL` iff `mode='replay_run'`** — service-layer invariant; enforced at write time by `RunOrchestrator`.
5. **Only `topic_probe` runs forkable** — `replay_run` and `seed_agent` runs cannot be saved as suites (raises `not_a_probe_run` 400). **T3 future-compat note:** if `RunRow.mode` is ever mutated post-create (e.g., the ROADMAP-T3 probe→seed promotion flow described as "a `RunRow.mode` flip"), the `not_a_probe_run` guard captures **runtime state**, not historical eligibility. T2 makes no commitment that a row stays save-as-suite-eligible across mode mutations; T3 must explicitly decide whether to (a) preserve `topic_probe` mode and add a separate `promoted_to_seed_agent_at` timestamp, or (b) accept that promoted rows lose suite-forkability.

### Migration `<rev>_validation_suite_topic_probe_t2.py`

```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "validation_suite" not in inspector.get_table_names():
        op.create_table(
            "validation_suite",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("source_run_id", sa.String(), nullable=True),  # SET NULL semantics require nullable; suite stays intact when source run deleted
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
            [sa.text("created_at DESC")],  # DESC matches default list ordering (reverse-chrono)
            postgresql_where=sa.text("retired_at IS NULL"),
            sqlite_where=sa.text("retired_at IS NULL"),
        )

    # SQLite cannot ALTER a constraint in place — batch_alter_table with
    # recreate="always" triggers the canonical table-rebuild workaround.
    # Precedent: alembic/versions/a2f6d8e31b09_cascade_optimization_fks.py:76,97
    # which documents this requirement in its module docstring (lines 16-18).
    # Without recreate="always", the FK addition silently no-ops on SQLite.
    with op.batch_alter_table("run_row", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_run_row_suite_id", "validation_suite",
            ["suite_id"], ["id"], ondelete="SET NULL",
        )
    # DESC on started_at: serves the "latest replay per suite_id" regression-alarm query
    op.create_index("ix_run_row_suite_id", "run_row", ["suite_id", sa.text("started_at DESC")])


def downgrade() -> None:
    # Step 1: null out run_row.suite_id BEFORE the table is dropped, so no
    # dangling-reference state persists if the migration is later re-upgraded.
    op.execute("UPDATE run_row SET suite_id = NULL WHERE suite_id IS NOT NULL")
    op.drop_index("ix_run_row_suite_id", table_name="run_row")
    # recreate="always" is also required on downgrade for SQLite to actually
    # drop the FK constraint via table rebuild.
    with op.batch_alter_table("run_row", recreate="always") as batch:
        batch.drop_constraint("fk_run_row_suite_id", type_="foreignkey")
    op.drop_index("ix_validation_suite_active", table_name="validation_suite")
    op.drop_index("ix_validation_suite_source_run_id", table_name="validation_suite")
    op.drop_index("ix_validation_suite_project_id", table_name="validation_suite")
    op.drop_table("validation_suite")
    # NOTE: existing run_row rows with mode='replay_run' preserved as harmless strings
    # (no longer joinable to a suite, but consistent with the spec's "harmless on
    # downgrade" contract — the row metadata is intact, only the FK relationship is gone).
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
    source_run_id: Mapped[str | None] = mapped_column(
        # SET NULL semantics require nullable=True; suite stays intact when
        # source run row is later deleted. NOT NULL would conflict with SET NULL
        # at delete time — pick nullable to honor the cascade contract.
        String, ForeignKey("run_row.id", ondelete="SET NULL"), nullable=True,
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
            "ix_validation_suite_active", text("created_at DESC"),
            sqlite_where=text("retired_at IS NULL"),
            postgresql_where=text("retired_at IS NULL"),
        ),
    )
```

### RunRow declaration update

In addition to the FK + migration, the existing `RunRow.__table_args__` tuple must be extended to declare the new index. Use the column-object `.desc()` precedent established at `models.py:125, 250, 350` (every existing DESC index in `models.py` uses `column.desc()`, never `text("...DESC")`):

```python
# Inside RunRow.__table_args__ (existing tuple — extend, do not replace).
# started_at is the column reference declared earlier in the class body;
# it's accessible at __table_args__ scope.
Index("ix_run_row_suite_id", "suite_id", started_at.desc()),
```

The migration's `op.create_index("ix_run_row_suite_id", "run_row", ["suite_id", sa.text("started_at DESC")])` is functionally equivalent in the produced SQL — they generate the same `CREATE INDEX ... (suite_id, started_at DESC)` — so the ORM declaration and the migration agree on what the DB ends up looking like. Without this matching ORM declaration, `alembic check` fails CI per the post-`bdd8e96cf489` schema-drift invariants.

**Parallel — `RunRow.suite_id` column declaration update.** The current declaration at `models.py:643` is `suite_id: Mapped[str | None] = mapped_column(String, nullable=True)` (P3 prework left the column without a `ForeignKey()` declaration in anticipation of T2). T2 must extend the declaration to:

```python
suite_id: Mapped[str | None] = mapped_column(
    String, ForeignKey("validation_suite.id", ondelete="SET NULL"), nullable=True,
)
```

so the ORM-side declaration matches the migration's `fk_run_row_suite_id` constraint. The `a2f6d8e31b09` precedent (the only FK-modifying migration in tree) updated both migration AND ORM declarations together — `models.py:136, 290, 493, 507` show the cascade-FK pattern. Without this ORM update, the named FK constraint exists in the DB but not in the ORM, which `alembic check` flags as drift (same CI gate as the Index discussion above).

## 4 REST + MCP surface

### NEW router `app/routers/suites.py`

| Method + Path | Body / Query | Status | Rate limit | Returns |
|---|---|---|---|---|
| `POST /api/probes/{run_id}/save-as-suite` | `{label: str[1..120], tolerance_abs?: float = 0.5}` | 201 | 20/min | `ValidationSuiteOut` |
| `GET /api/suites` | `?project_id=&repo_full_name=&include_retired=false&limit=20&offset=0` | 200 | — | `ValidationSuiteListResponse` (concrete Pydantic envelope; see §4 schemas block — same shape as `RunListResponse`: `{total, count, offset, items: list[ValidationSuiteListItem], has_more, next_offset}`) |
| `GET /api/suites/{suite_id}` | — | 200 / 404 | — | `ValidationSuiteOut` |
| `GET /api/suites/{suite_id}/replays` | `?limit=20&offset=0` | 200 | — | `RunListResponse` (canonical — `items: list[RunSummary]` filtered to `mode='replay_run' AND suite_id={suite_id}`) |
| `POST /api/suites/{suite_id}/replay` | — | 202 | 5/min | `ReplayRunOut` |
| `POST /api/suites/{suite_id}/retire` | `{reason: str[1..500]}` | 200 | 30/min | `ValidationSuiteOut` |

**ROADMAP path divergence note:** ROADMAP line 333 describes replay as `POST /api/probes/{id}/replay`. T2 relocates to `POST /api/suites/{suite_id}/replay` since the **suite** is the regression fixture being replayed; the source probe is only suite provenance. Spec design supersedes ROADMAP wording on path placement; the ROADMAP entry is updated at v0.4.22 release time to reflect.

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
| `synthesis_save_suite` | `run_id, label[1..120], tolerance_abs?: float = 0.5` | `SaveSuiteOutput{suite_id, source_run_id, label, baseline_mean, tolerance_abs, prompts_count, created_at}` |
| `synthesis_replay_suite` | `suite_id` | `ReplayInitiatedOutput{run_id, suite_id, mode: 'replay_run', poll_url, started_at}` |

**MCP naming convention**: `*Output` suffix matches 15 of 16 existing `schemas/mcp_models.py` classes (OptimizeOutput, AnalyzeOutput, PrepareOutput, SaveResultOutput, FeedbackOutput, RefineOutput, HistoryOutput, MatchOutput, StrategiesOutput, DeleteOptimizationOutput, OptimizationDetailOutput, HealthOutput, etc.). The single outlier `ExplainResult` is legacy; T2 conforms to the dominant `*Output` convention.

Listings deliberately REST-only (matches `synthesis_history` precedent — no `synthesis_list_runs` either).

### Error envelope (`{code, message}` per existing convention)

| Code | Status | Trigger |
|---|---|---|
| `run_not_found` | 404 | save-as-suite on missing run |
| `run_not_completed` | 409 | save on `status != 'completed'` |
| `not_a_probe_run` | 400 | save on `mode != 'topic_probe'` |
| `run_missing_aggregate` | 409 | save on completed run whose `aggregate` is missing or lacks `mean_overall` (legacy / corrupted run rows) |
| `invalid_label` | 400 | empty / >120 / non-string |
| `invalid_tolerance` | 400 | outside `[0.1, 5.0]` |
| `suite_not_found` | 404 | get/replay/retire missing suite |
| `suite_retired` | 409 | replay on retired suite |
| `topic_only_unavailable` | 400 | Reserved for future kill-switch via `TOPIC_ONLY_MODE_ENABLED` env var (T2 ships with this default `True`; setting `False` causes `POST /api/probes` with `grounding_mode='topic_only'` to return this error). Defensive — provisioned in T2 so a future operator-flip doesn't need a schema migration. **NOT in T2 IN-scope per §14** — kill-switch implementation deferred; only the error code envelope is reserved. |
| `link_repo_first` | 400 | (existing) `grounding_mode='codebase'` without linked repo |

`suite_repo_drift` is **informational, not an error** — returned as `ReplayRunOut.warnings: ['repo_drift']` (200 OK) when suite vs current linked repo names differ.

### Pydantic schemas (`schemas/validation_suite.py`)

```python
# --- Nested payload types (consumed by ValidationSuiteOut) ---

class PromptSnapshotItem(BaseModel):
    """One entry of validation_suite.prompts_snapshot — frozen prompt input."""
    raw_prompt: str
    intent_label: str
    original_optimization_id: str | None = None

class PerPromptScore(BaseModel):
    """One entry of baseline_scores.per_prompt — frozen scoring snapshot."""
    raw_prompt_idx: int
    overall: float
    dimensions: dict[str, float]

class BaselineScoresPayload(BaseModel):
    """Shape of validation_suite.baseline_scores JSON column.

    Key names match the canonical compute_run_aggregate output verbatim —
    p5_overall/p50_overall/p95_overall (not p5/p50/p95) to preserve
    downstream-consumer compatibility with existing RunRow.aggregate readers.
    """
    mean_overall: float
    p5_overall: float
    p50_overall: float
    p95_overall: float
    per_prompt: list[PerPromptScore]
    task_type_distribution: dict[str, int]

# --- Request / Response types ---

class SaveSuiteRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    tolerance_abs: float = Field(0.5, ge=0.1, le=5.0)

class RetireSuiteRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)

class ValidationSuiteOut(BaseModel):
    # model_config=ConfigDict(from_attributes=True) so model_validate() can
    # introspect ORM instance attributes (used at the create/get/retire call
    # sites where the service hands a ValidationSuite ORM row to the schema).
    # Matches the existing pattern in schemas/templates.py:16.
    model_config = ConfigDict(from_attributes=True)

    id: str
    # source_run_id nullable matches column nullability — becomes None
    # if/when the source run is later deleted (ondelete=SET NULL fires).
    # ValidationSuiteOut.model_validate() over a post-delete-cascade orphan
    # suite would crash with a non-nullable str field.
    source_run_id: str | None
    label: str; tolerance_abs: float
    project_id: str | None; repo_full_name: str | None
    created_at: datetime; retired_at: datetime | None; retired_reason: str | None
    prompts_snapshot: list[PromptSnapshotItem]
    baseline_scores: BaselineScoresPayload

class ValidationSuiteListItem(BaseModel):
    # Same minus JSON payloads — for fast listing
    id: str
    source_run_id: str | None  # nullable per column reality (see ValidationSuiteOut)
    label: str; tolerance_abs: float
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


class ValidationSuiteListResponse(BaseModel):
    """Pagination envelope for GET /api/suites — mirrors RunListResponse shape
    per the project's canonical pagination convention (schemas/runs.py:55-63).
    """
    total: int
    count: int
    offset: int
    items: list[ValidationSuiteListItem]
    has_more: bool
    next_offset: int | None
```

Update `GET /api/suites` (§4 REST table) returns `ValidationSuiteListResponse` directly — supersedes the prior "`RunListResponse`-style envelope" wording.

### Write-queue operation labels (new)

- `validation_suite_create` — `ValidationSuiteService.create_from_run()` terminal write
- `validation_suite_retire` — `ValidationSuiteService.retire()` terminal write. Labelled ONLY on first-retire (state-transition) submissions; idempotent re-retires short-circuit before `WriteQueue.submit()` per §5 `retire()` body's early-return clause (the `if suite.retired_at is not None: return ValidationSuiteOut.model_validate(suite)` guard that precedes any write_queue.submit call), so no operation_label is recorded on no-op re-retries. This matches the §9 `validation_suite_retired` event-emission contract ("ONLY when state actually transitioned").
- `replay_run_persist` — `RunOrchestrator._persist_final()` terminal write **when `mode='replay_run'`**. The orchestrator's persist path is otherwise unchanged from Foundation P3 — it is the same `_persist_final()` body, but the operation_label is mode-keyed: `run_orchestrator.persist_final` for `topic_probe`/`seed_agent` (existing), `replay_run_persist` for `replay_run` (new). The mode-keying is the ONLY behavioral diff in the orchestrator, surfaced for JSONL trace filterability.

## 5 Services + generators + topic-only mode

### NEW `app/services/validation_suite_service.py`

Stateless class. Detached-ORM-safe per Foundation P4 contract: read in short sessions, hand frozen snapshots to write-queue callbacks, no DB session held over any I/O.

```python
@dataclass(frozen=True)
class SuiteSnapshotInputs:
    """Pure data crossing the write-queue boundary."""
    suite_id: str               # uuid4().hex, minted upfront
    source_run_id: str | None   # nullable mirrors the column; at create
                                # time always populated, but field type
                                # matches the DB column for consistency
    label: str
    tolerance_abs: float
    project_id: str | None
    repo_full_name: str | None
    prompts_snapshot: list[dict]
    baseline_scores: dict
    created_at: datetime


@dataclass(frozen=True)
class SuiteRetireInputs:
    """Pure data for retire write — emit_event_after_commit is True iff first retire."""
    suite_id: str
    retired_at: datetime
    retired_reason: str
    emit_event_after_commit: bool


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
        # EVENT TRIGGER: validation_suite_created — emitted AFTER write_queue.submit
        # completes successfully. Not emitted on submit() exceptions (failed write).
        event_bus.publish("validation_suite_created", {
            "suite_id": snapshot.suite_id,
            "source_run_id": snapshot.source_run_id,
            "label": snapshot.label,
            "tolerance_abs": snapshot.tolerance_abs,
            "prompts_count": len(snapshot.prompts_snapshot),
            "baseline_mean": snapshot.baseline_scores["mean_overall"],
            "project_id": snapshot.project_id,
        })
        # Build the response from the snapshot via Pydantic's default validator —
        # no custom from_snapshot() classmethod required. The snapshot dataclass
        # has the same field names as ValidationSuiteOut (minus retired_at/reason
        # which default to None on fresh creates).
        return ValidationSuiteOut.model_validate({
            "id": snapshot.suite_id,
            "source_run_id": snapshot.source_run_id,
            "label": snapshot.label,
            "tolerance_abs": snapshot.tolerance_abs,
            "project_id": snapshot.project_id,
            "repo_full_name": snapshot.repo_full_name,
            "created_at": snapshot.created_at,
            "retired_at": None,
            "retired_reason": None,
            "prompts_snapshot": snapshot.prompts_snapshot,
            "baseline_scores": snapshot.baseline_scores,
        })

    async def retire(
        self, suite_id: str, *, reason: str,
        db: AsyncSession, write_queue: WriteQueue,
    ) -> ValidationSuiteOut:
        """Idempotent — re-retire of already-retired suite is a no-op success.

        Returns refreshed ValidationSuiteOut either way. The
        ``validation_suite_retired`` event fires ONLY when state actually
        transitions (first retire), not on no-op re-retires — matches the
        in-memory state-transition semantics of regression_alarm_transition.
        """
        suite = await db.scalar(
            select(ValidationSuite).where(ValidationSuite.id == suite_id)
        )
        if suite is None:
            raise ValueError("suite_not_found")
        if suite.retired_at is not None:
            # Idempotent return — no write, no event
            return ValidationSuiteOut.model_validate(suite)

        inputs = SuiteRetireInputs(
            suite_id=suite_id,
            retired_at=_utcnow(),
            retired_reason=reason.strip(),
            emit_event_after_commit=True,
        )
        await write_queue.submit(
            partial(_persist_suite_retire, inputs=inputs),
            timeout=10,
            operation_label="validation_suite_retire",
        )
        # EVENT TRIGGER: validation_suite_retired — emitted AFTER write_queue.submit
        # completes successfully, ONLY when state transitioned (first retire).
        event_bus.publish("validation_suite_retired", {
            "suite_id": suite_id,
            "reason": inputs.retired_reason,
        })
        return ValidationSuiteOut.model_validate(suite)  # refreshed by writer

    async def compute_regression_alarm(self, *, db) -> RegressionAlarmBlock:
        """30s TTLCache.

        EVENT TRIGGER: regression_alarm_transition — emitted by this method
        after running the alarm query and comparing each suite's current state
        (nominal | firing) against the prior cached state held in
        ``self._prior_alarm_states: dict[suite_id, Literal['nominal','firing','none']]``.
        Only suites whose state CHANGES (none→firing, firing→nominal, etc.)
        trigger the event. The prior-state map is service-instance-local
        (in-memory only — survives across calls within a single process,
        resets on process restart, which is acceptable for status-display
        observability).
        """
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

Conforms to the canonical `RunGenerator` Protocol (`backend/app/services/generators/base.py:24-33`): `async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult`. `RunRequest.payload` is a plain `dict` (per `app/schemas/runs.py:14`) — fields are read out of the payload exactly like `TopicProbeGenerator.run` and `SeedAgentGenerator.run` do today. **No new frozen-dataclass request type** — would break the Protocol's `runtime_checkable` and require parallel `_extract_*_meta` helpers in the orchestrator.

```python
class ReplayRunGenerator:
    """Re-runs a frozen suite through the full pipeline.

    Per-prompt: batch_pipeline.run_single_prompt with full enrichment + scoring.
    Emits probe_prompt_completed + probe_completed events for SSE-consumer
    parity. Skips probe_grounding / probe_generating (no Phase 1+2).
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        prompt_loader: PromptLoader,
        embedding_service: EmbeddingService,
        session_factory: async_sessionmaker,
        taxonomy_engine: TaxonomyEngine,
        domain_resolver: DomainResolver,
        context_service: ContextEnrichmentService,
        write_queue: WriteQueue,
    ) -> None:
        """Collaborator graph = the union needed by batch_pipeline.run_single_prompt.

        NOT a literal mirror of SeedAgentGenerator.__init__ (which is
        (seed_orchestrator, write_queue) only — seed runs delegate to a
        seed_orchestrator that owns its own collaborator wiring internally).
        Replay iterates run_single_prompt directly, so its constructor takes
        the run_single_prompt collaborator union explicitly.

        Per Foundation P3: generators MUST NOT touch RunRow — RunOrchestrator
        owns row writes. write_queue is held only for future per-prompt
        observability writes (e.g. T3 progress checkpoints); T2's body does
        not write to RunRow from inside the generator.
        """
        self._provider = provider
        self._prompt_loader = prompt_loader
        self._embedding_service = embedding_service
        self._session_factory = session_factory
        self._taxonomy_engine = taxonomy_engine
        self._domain_resolver = domain_resolver
        self._context_service = context_service
        self._write_queue = write_queue

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        # Read fields out of request.payload — matches TopicProbeGenerator /
        # SeedAgentGenerator canonical pattern (both read from request.payload
        # rather than a typed-dataclass request).
        suite_id: str = request.payload["suite_id"]
        project_id: str | None = request.payload.get("project_id")
        repo_full_name: str | None = request.payload.get("repo_full_name")

        # 1. Load suite snapshot (short read session, then session closes)
        suite_snapshot = await self._load_suite_snapshot(suite_id)
        if suite_snapshot.retired_at is not None:
            raise ValueError("suite_retired")

        # 2. Repo drift check (informational only)
        warnings: list[str] = []
        if (suite_snapshot.repo_full_name and repo_full_name
            and suite_snapshot.repo_full_name != repo_full_name):
            warnings.append("repo_drift")
            event_bus.publish("probe_warning", {
                "run_id": run_id, "code": "repo_drift",
                "suite_repo": suite_snapshot.repo_full_name,
                "current_repo": repo_full_name,
            })

        # 3. Pre-fetch historical_stats ONCE — threaded to all per-prompt
        # children. Mirrors batch_orchestrator.run_batch:113-126 (the actual
        # prefetch pattern: OptimizationService(read_db).get_score_distribution
        # with exclude_scoring_modes=["heuristic"], single DB hit per batch).
        async with self._session_factory() as read_db:
            historical_stats = await OptimizationService(read_db).get_score_distribution(
                exclude_scoring_modes=["heuristic"],
            )

        # 4. Per-prompt execution — SEQUENTIAL loop with per-iteration
        # try/except (matches TopicProbeGenerator Phase 3 canonical pattern
        # at topic_probe_generator.py:185-215). asyncio.TaskGroup was
        # rejected: a single child's non-rate-limit exception propagates
        # BaseExceptionGroup that aborts siblings, leaving inconsistent
        # state with partial assignments — terminal-status classification
        # cannot distinguish 'partial=N completed=M' from 'TaskGroup-aborted'.
        # Sequential preserves the per-prompt outcome envelope.
        prompt_results: list[dict] = []
        for idx, item in enumerate(suite_snapshot.prompts_snapshot):
            try:
                # batch_pipeline.run_single_prompt returns
                # services.batch_pipeline.PendingOptimization (NOT an
                # OptimizationResult). PendingOptimization is a flat dataclass
                # with id + score_clarity/specificity/structure/faithfulness/
                # conciseness + overall_score (the final blended score).
                # See batch_pipeline.py:97-156.
                pending: PendingOptimization = await batch_pipeline.run_single_prompt(
                    raw_prompt=item["raw_prompt"],
                    tier="internal",  # Deterministic regression detection;
                                       # sampling tier introduces caller-side
                                       # nondeterminism unsuitable for replay.
                    provider=self._provider,
                    prompt_loader=self._prompt_loader,
                    embedding_service=self._embedding_service,
                    session_factory=self._session_factory,
                    taxonomy_engine=self._taxonomy_engine,
                    domain_resolver=self._domain_resolver,
                    context_service=self._context_service,
                    historical_stats=historical_stats,
                    project_id=project_id,
                    repo_full_name=repo_full_name,
                )
                overall = pending.overall_score or 0.0
                baseline_overall = (
                    suite_snapshot.baseline_scores["per_prompt"][idx]["overall"]
                )
                prompt_results.append({
                    "raw_prompt": item["raw_prompt"],
                    "raw_prompt_idx": idx,
                    # NOTE: trace_id (not id). Replay does NOT call bulk_persist
                    # (see §5 GeneratorResult — taxonomy_delta={}, no Optimization
                    # row is INSERTed for replay results), so pending.id would be
                    # an orphan uuid pointing at no DB row. trace_id is the canonical
                    # correlation id on PendingOptimization (set at batch_pipeline.py:101,217)
                    # — opaque, NOT a FK to Optimization. Field name disambiguates the
                    # public RunResult.prompt_results contract.
                    "trace_id": pending.trace_id,
                    "overall": overall,
                    "dimensions": {
                        "clarity": pending.score_clarity,
                        "specificity": pending.score_specificity,
                        "structure": pending.score_structure,
                        "faithfulness": pending.score_faithfulness,
                        "conciseness": pending.score_conciseness,
                    },
                    "intent_label": item["intent_label"],
                    "task_type": pending.task_type,
                    "baseline_overall": baseline_overall,
                    "delta": overall - baseline_overall,
                    "status": "completed",
                })
                event_bus.publish("probe_prompt_completed", {
                    "run_id": run_id, "idx": idx,
                    "total": len(suite_snapshot.prompts_snapshot),
                    "overall": overall, "delta": overall - baseline_overall,
                })
            except Exception as e:
                prompt_results.append({
                    "raw_prompt": item["raw_prompt"],
                    "raw_prompt_idx": idx,
                    "status": "failed",
                    "error": str(e)[:500],
                })
                logger.warning("replay prompt %d/%d failed: %s",
                               idx, len(suite_snapshot.prompts_snapshot), e)

        # 5. Aggregate + classify terminal status
        n_completed = sum(1 for r in prompt_results if r["status"] == "completed")
        n_failed = len(prompt_results) - n_completed
        if n_completed == len(prompt_results):
            terminal_status: Literal["completed", "partial", "failed"] = "completed"
        elif n_completed == 0:
            terminal_status = "failed"
        else:
            terminal_status = "partial"

        aggregate = compute_run_aggregate(prompt_results)  # from services/generators/_aggregate.py (NEW, see §2)
        # Replay-specific telemetry rides in aggregate.* (additive — no schema
        # change needed). Suite linkage already lives in RunRow.suite_id;
        # downstream consumers (regression_alarm query) read RunRow.aggregate
        # to compute mean_overall, and join on suite_id for baseline lookup.
        aggregate["replay_warnings"] = warnings
        aggregate["replay_suite_id"] = suite_id
        aggregate["replay_n_completed"] = n_completed
        aggregate["replay_n_failed"] = n_failed

        # 6. Return canonical GeneratorResult — orchestrator owns terminal row write
        return GeneratorResult(
            terminal_status=terminal_status,
            prompts_generated=len(prompt_results),
            prompt_results=prompt_results,
            aggregate=aggregate,
            taxonomy_delta={},          # Replay produces no taxonomy delta —
                                         # the suite's prompts already exist in
                                         # taxonomy from the source probe.
            # final_report: stub non-None string (one short markdown line —
            # the suite-detail UI surfaces the baseline diff, not narrative).
            # RunResult.final_report is `str` (non-nullable per schemas/runs.py:49);
            # passing None would fail Pydantic validation on subsequent
            # GET /api/runs/{id}. Stub is intentionally minimal — the UI's
            # SuiteDetailView reads aggregate + suite_id, not final_report.
            final_report=(
                f"# Replay — suite_id={suite_id}\n"
                f"See SuiteDetailView for the baseline-vs-latest diff."
            ),
        )
```

**Why reuse the full pipeline?** Regression detection must catch *pipeline-level* drift, not just LLM-output drift. The suite is the input contract; the pipeline (classification + enrichment + scoring) is what's being regression-tested.

**Why `tier='internal'`?** Sampling tier introduces caller-side nondeterminism (MCP IDE selects its own model freely). Replay is a regression test — must compare like-for-like against the saved baseline, which was scored under whatever tier the source probe ran. The source probe's tier is whatever `routing.resolve_route()` resolved at the time (typically `internal`); replays pin `internal` so today's classifier+enrichment+scoring infrastructure is exercised end-to-end without sampling injecting variance.

**Concurrency note:** the spec ships replay as **sequential per-prompt** (try/except per iteration, matching `TopicProbeGenerator` Phase 3). The `PROBE_PROMPT_CONCURRENCY` constant introduced in `services/generators/_constants.py` is reserved for a future parallelization (T3+) that addresses the TaskGroup exception-propagation problem properly. For T2, the constant exists so probe + replay share a single source of truth — but neither uses a `Semaphore(PROBE_PROMPT_CONCURRENCY)` semaphore in this release.

**Event reuse (decision):** replay emits ONLY `probe_prompt_completed` + `probe_completed` — same event types as topic probe. **`probe_replay_completed` is deliberately NOT introduced.** SSE consumers don't learn a new event family; SuitesPanel/RegressionBadge filter on `RunRow.mode == 'replay_run'` from the polled state, not on event type. New event introduced by T2: `probe_warning(code='repo_drift', ...)` only.

**Collaborator-graph rationale:** the constructor takes the union needed by `batch_pipeline.run_single_prompt`. This is a **new richer graph** specific to replay's structural shape — not a literal mirror of `SeedAgentGenerator.__init__(seed_orchestrator, write_queue)` (which delegates collaborator wiring to its `seed_orchestrator`) nor of `TopicProbeGenerator.__init__(provider, repo_index_query, taxonomy_engine, *, context_service, embedding_service, session_factory, write_queue)` (which uses `repo_index_query` for Phase 1 grounding — irrelevant to replay). `batch_pipeline.run_single_prompt` is the canonical primitive (A2-compliant — no re-implementation); full enrichment context flows in (A6-compliant — no scoring without enrichment). Pre-fetched `historical_stats` mirrors `batch_orchestrator.run_batch:113-126` (single DB hit threaded to all per-prompt children — `OptimizationService(read_db).get_score_distribution(exclude_scoring_modes=["heuristic"])`).

### EXTEND `app/services/generators/topic_probe_generator.py`

`TopicProbeGenerator.run()` already takes the canonical `RunRequest` and reads fields out of `request.payload` (per `topic_probe_generator.py:94-109`). T2 adds the `grounding_mode` payload key — **no new dataclass type**; payload is just `dict`.

```python
async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult:
    # Read fields from request.payload — canonical pattern.
    topic: str = request.payload["topic"]
    n_prompts: int = request.payload.get("n_prompts", 12)
    intent_hint: str | None = request.payload.get("intent_hint")
    # scope: ProbeContext.scope is `str = "**/*"` per schemas/probes.py:18;
    # the canonical extraction pattern at topic_probe_generator.py:106 is
    # `str(payload.get("scope") or "**/*")` — keep that exact form.
    scope: str = str(request.payload.get("scope") or "**/*")
    project_id: str | None = request.payload.get("project_id")
    repo_full_name: str | None = request.payload.get("repo_full_name")
    grounding_mode: Literal["codebase", "topic_only"] = (
        request.payload.get("grounding_mode", "codebase")
    )

    # Phase 1 branches on grounding_mode.
    if grounding_mode == "codebase":
        probe_context = await self._phase_grounding(
            topic=topic, scope=scope, repo_full_name=repo_full_name,
            run_id=run_id,
        )
    else:
        # intent_hint is Literal["audit","refactor","explore","regression-test"]
        # with default "explore" — passing None would fail Pydantic validation.
        # Coerce via fallback to default for the topic-only path.
        valid_intents = ("audit", "refactor", "explore", "regression-test")
        safe_intent_hint = intent_hint if intent_hint in valid_intents else "explore"
        # scope: str = "**/*" default — preserved unchanged for topic_only path
        # (the wildcard scope is informational; topic_only ignores it during
        # Phase 2 generation per the topic-only template's lack of scope refs).
        probe_context = ProbeContext(
            topic=topic,
            intent_hint=safe_intent_hint,
            scope=scope or "**/*",
            relevant_files=[],
            explore_synthesis_excerpt=None,
            known_domains=[],
            repo_full_name=None,             # ProbeContext schema relaxed in §2
            commit_sha=None,                  # ProbeContext schema extended
            topic_only=True,                  # ProbeContext schema extended
        )
        # No probe_grounding event emitted in topic_only path

    # Phase 2 — template selection by grounding mode
    template_name = (
        "probe-agent.md" if grounding_mode == "codebase"
        else "probe-agent-topic-only.md"
    )
    prompts = await probe_generation.generate_probe_prompts(
        probe_ctx=probe_context,
        provider=self._provider,
        n_prompts=n_prompts,
        mode=grounding_mode,           # NEW kwarg — see §2 probe_generation extension
        template_name=template_name,   # NEW kwarg
    )
    ...
```

**`ProbeContext` schema extensions (per §2):**
- `repo_full_name: str | None = None` (was `str`, required)
- NEW `commit_sha: str | None = None`
- NEW `topic_only: bool = False`

Downstream phases consume `.relevant_files`, `.explore_synthesis_excerpt`, `.known_domains` etc. — empty list / `None` is a valid degenerate state for the topic-only path.

**`probe_generation.generate_probe_prompts` signature extensions (per §2):**
- NEW `mode: Literal['codebase', 'topic_only'] = 'codebase'` (default preserves backward-compat)
- NEW `template_name: str = 'probe-agent.md'`
- Internal logic: `mode='topic_only'` switches the filter from `_has_backtick` (drop prompts WITHOUT backticks) to `_lacks_backtick` (drop prompts WITH backticks), inverting the F1 filter direction; threshold remains `>50%` drop = error.

### NEW prompt template `prompts/probe-agent-topic-only.md`

Two-template strategy (not in-template conditionals) — matches `optimize.md`/`refine.md` precedent. Hot-reloaded via `PromptLoader`. Manifest entry. Lifespan validates both at startup.

Topic-only template instructs the LLM to generate prompts WITHOUT code references. F1 backtick-density validator inverts for topic-only mode: `<5% backticks` floor (topic-only prompts SHOULD NOT cite code). `probe_generation.generate_probe_prompts(mode=...)` swaps the validator.

### EXTEND `app/services/run_orchestrator.py`

`RunOrchestrator` already uses generator-dict-based dispatch (`run_orchestrator.py:54, 70-78`): `self._generators: dict[str, RunGenerator]` is set at lifespan-time and `run()` resolves via `self._generators[mode]`. **There is no if-elif chain in the orchestrator body** — only the lifespan registration changes.

```python
# app/main.py lifespan (existing dict — EXTEND, do not replace):
app.state.run_orchestrator = RunOrchestrator(
    write_queue=app.state.write_queue,
    generators={
        "topic_probe": topic_probe_gen,
        "seed_agent": seed_agent_gen,
        "replay_run": replay_run_gen,         # NEW
    },
)
```

`RunRow.suite_id` is set on the initial INSERT for `mode='replay_run'` — orchestrator's `_create_row()` reads `request.payload.get("suite_id")` and populates the column when present. Existing `current_run_id` ContextVar lifecycle, `asyncio.shield()` cancellation handling, and 4-status terminal transitions all apply unchanged.

**Persistence:** `RunOrchestrator._persist_final()` is extended to **mode-key** the operation label. The current signature at `run_orchestrator.py:186` is `_persist_final(self, run_id: str, result: GeneratorResult)` — `mode` is NOT in scope. T2 extends the signature to take `mode` as a new parameter, and the single existing caller at `:86` (`await self._persist_final(run_id, result)`) is updated to thread `mode` through. The new caller path inside `_run_to_completion` likewise threads `mode`.

```python
# NEW signature (mode parameter added — not a single-line change; existing caller updated):
async def _persist_final(
    self, run_id: str, mode: str, result: GeneratorResult,
) -> None:
    ...
    operation_label = (
        "replay_run_persist" if mode == "replay_run"
        else "run_orchestrator.persist_final"
    )
    await self._write_queue.submit(
        partial(_persist_callback, ...),
        operation_label=operation_label,
        ...
    )
```

The persist body is otherwise unchanged — same `WriteQueue.submit()` call, same SQL, same shielded cancellation; only the operation_label is selected by mode.

### NEW `dispatch_async()` for 202+polling callers

`dispatch_async()` is a new public entry point that awaits ONLY the initial `INSERT(status='running')` via `WriteQueue.submit(_create_row, ...)`, then spawns the long-running generator+terminal-persist as a shielded background task. The existing SSE entry path is refactored to internally call `dispatch_async()` then subscribe — eliminating two-codepath divergence.

**ContextVar lifecycle change.** The existing `run()` method holds the `current_run_id` token across the entire run body (`token = current_run_id.set(run_id)` at entry, `current_run_id.reset(token)` at exit). For `dispatch_async()`, the spawned `asyncio.create_task()` MUST set its own token at task entry — Python's `contextvars.copy_context()` inheritance copies the parent's CONTEXT at task-spawn time, but the parent then resets/changes its own context before the child reads. To keep replay/topic-probe events correctly stamped with `run_id`, the spawned task explicitly does:

```python
async def dispatch_async(self, *, mode: str, request: RunRequest, run_id: str) -> None:
    # 1. Synchronous-from-caller's-perspective: INSERT initial row + commit
    await self._create_row(mode=mode, request=request, run_id=run_id)
    # ↑ This blocks until WriteQueue.submit() commits, so the caller's poll
    #   on /api/probes/{run_id} immediately after dispatch_async returns
    #   is guaranteed to find the row.

    # 2. Spawn the long task — runs detached from caller's request lifecycle
    asyncio.create_task(self._run_to_completion(
        mode=mode, request=request, run_id=run_id,
    ))

async def _run_to_completion(self, *, mode, request, run_id) -> None:
    """Body that does generator.run() + _persist_final() with shielded cleanup.
    Sets current_run_id ContextVar inside the spawned task — parent's token
    binding is not inherited deterministically across copy_context().

    Cleanup paths mirror the existing run() body at run_orchestrator.py:87-102:
    every _mark_failed call is wrapped in contextlib.suppress(Exception) so a
    cleanup-path failure (e.g., WriteQueue at capacity) does NOT shadow the
    original cancellation or generator exception. _mark_failed already
    truncates error to 2000 chars internally (`_mark_failed` body at :223);
    callers pass the unmangled error string.
    """
    token = current_run_id.set(run_id)
    try:
        await self._run_generator_and_persist(mode, request, run_id)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.shield(self._mark_failed(run_id, error="cancelled"))
        raise
    except Exception as exc:
        with contextlib.suppress(Exception):
            await self._mark_failed(run_id, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        current_run_id.reset(token)
```

**Existing `run()` refactor:** `run()` becomes a thin wrapper that calls `dispatch_async()` then `await`s an `asyncio.Event` set by the spawned task at terminal status — preserves the "blocks until terminal" contract for SSE callers + tests, while sharing the dispatch_async body. (Alternative: keep `run()` as a separate code path; the spec prefers the unified body to avoid two-codepath divergence that the reviewer flagged.)

**Cancellation contract diff vs existing:**

| Existing `run()` | New `dispatch_async()` + spawned task |
|---|---|
| Caller's task owns the run end-to-end | Spawned task owns generator + terminal persist; caller's task only owns initial INSERT |
| Caller cancellation cancels the run | Caller cancellation does NOT cancel the spawned task (shielded) |
| `current_run_id` set on caller's task | `current_run_id` set inside spawned task |
| `_mark_failed` wrapped in `asyncio.shield` | Same — shielded write survives any cancellation path |

The shielded `_mark_failed` invariant means: even if the spawned task is cancelled externally (process shutdown, etc.), the row reaches a terminal state. Orphan rows (status='running' past 1h TTL) are reconciled by the existing `_gc_orphan_runs` lifespan sweep.

## 6 UI surface

**Chromatic encoding** (axiom 4: color = data; anchor wording matches brand-canon `SKILL.md` lines 232-241):

| Surface | Color | Anchor |
|---|---|---|
| Topic Probe identity (tab active, mini-view ring, form focus) | **neon-teal `#00d4aa`** | Secondary success, extraction |
| Save-as-Suite (Hero button) | **neon-purple `#a855f7`** | Processed, elevated |
| Replay (Medium button) | **neon-blue `#4d8eff`** | Information, analysis (diagnostic replay) |
| Regression alarm firing | **neon-red `#ff3366`** | Danger, destruction |
| Regression alarm nominal | **neon-green `#22ff88`** | Health, context, success |

Brand gradient (`cyan → purple`) deliberately NOT used on Save-as-Suite — solid purple preserves color-as-data axiom. Gradient stays reserved for hero brand moments per "max 2 per viewport" budget.

### Density pins (IDE layout table)

| Surface | Height | Padding | Gap | Type |
|---|---|---|---|---|
| SeedModal tab bar | h-7 | px-2 | gap-1 | text-[11px] |
| Tab cell (TOPIC PROBE) | — (inherits bar height h-7) | px-1.5 | gap-0.5 | text-[11px] uppercase 700 `font-display` |
| Form rows (input fields) | h-5 (canon "Select/input fields | 20px" per `SKILL.md` line 139) | px-1 | gap-1.5 | text-[11px] |
| Form toolbar (action buttons grouped above/below form, if present) | h-7 (canon "Toolbar bars") | px-1.5 | gap-1.5 | text-[11px] |
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
| StatusBar regression badge | full bar height (no explicit `h-*`) | `px-1.5 py-0` | — | text-[10px] `font-mono`, edge-to-edge inside the bar — matches `TierBadge`/`UpdateBadge`/`ProviderBadge` precedent. **Bar height reconciliation**: `SKILL.md` line 140 canon is 22px; shipped `StatusBar.svelte:251` ships 20px. Per `feedback_visual_feel_over_spec.md` ("update spec to match what shipped, not the other way around"), the shipped 20px is authoritative for layout. The badge fills the bar regardless. Doc-sync to `SKILL.md` line 140 from 22px → 20px is a §11 pre-release item, paralleling the `copy-flash` 600ms → 1500ms reconciliation. |

**Banned (asserted at cycle 14 audit, scope narrowed to 2D-UI directories ONLY):**
- CSS: `p-2`+, `gap-2`+, `h-7` on buttons, `px-2.5`+ on tab cells **or buttons** (per `SKILL.md` line 149 — `px-2` is the canon ceiling for both), `border: 2px`, any `box-shadow` with spread/blur, any `text-shadow`/`filter`/`radial-gradient` to transparent on UI elements
- Vocab: standalone-word `glow`/`halo`/`bloom`/`radiance`/`breathing`/`dust`/`pulse`/`flash` (canonical 3D-feature names per `references/3d-visualization.md` "Canon Vocabulary") — banned in `frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/` only.
- **Hyphenated canonical identifiers are PERMITTED in all directories**: `copy-flash` (canonical 2D animation in `component-patterns.md` line 219), `forge-spark`, `scale-in`, `slide-in-right`, `slide-up-in`, `dialog-in`, `useCopyFlash` (camelCase function name), `flash-emissive` (3D), etc. Audit grep uses word-boundary form `(?<![-\w])(glow|halo|bloom|radiance|breathing|dust|pulse|flash)(?![-\w])` (or equivalent `\b...\b` with hyphen-exclusion) to avoid false positives on these compounds.
- The `frontend/src/lib/components/taxonomy/` directory is **3D-scope** (3D-visualization canon directory per `SKILL.md` line 81 / line 214) and is **explicitly excluded** from this sweep — `taxonomy/SeedModal.svelte` lives there for legacy reasons but is a 2D modal. T2 narrows the audit grep `--include-dir` to the 7 directories above; if any new 2D component is introduced under `taxonomy/`, it must be re-pathed before merge.

### Contour-intensity tier mapping

| Button | Tier | Resting | Hover | Active |
|---|---|---|---|---|
| Save-Suite | **Hero** | `1px solid neon-purple` + 8% fill | 14% fill + `translateY(-1px)` | `translateY(0)`, border mutes |
| Replay | **Medium** | `1px solid border-subtle` | `1px solid neon-blue` + 12% fill | `inset 0 0 0 1px rgba(77, 142, 255, 0.4)` (no `translateY` — Medium tier; lift is Hero-only per `component-patterns.md` Recipe E) |
| Copy-md icon | **Small** | `1px solid border-subtle` + `bg-input` | `1px solid neon-cyan` + 8% fill | `inset 0 0 0 1px rgba(0, 229, 255, 0.4)` |
| Run | **Hero** (cyan) | `1px solid neon-cyan` + 8% fill | 14% fill + `translateY(-1px)` | `translateY(0)` |
| Tab (active) | **Micro** | transparent bottom border | dim teal bottom border | full teal bottom border |
| Suite row | **Recipe A** (border + bg-tint) — NOT Large tier; h-5 data rows are too compact for the inset-contour visual weight reserved for ~80px+ card surfaces | `1px solid border-subtle` `bg-card` | `1px solid border-accent` `bg-bg-hover/40` (matches HistoryEntry/ProjectItem sidebar-row precedent) | no `translateY` (active-state lift is reserved for Hero buttons per `component-patterns.md` Recipe E; meaningless on 20px row) |

Multi-property transitions in a single declaration with **uniform duration** per axiom 5 ("multi-property transitions fire simultaneously as one atomic event", `SKILL.md` line 59 + 335). Hover-in:

```css
transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
            border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
            transform 200ms cubic-bezier(0.16, 1, 0.3, 1),
            color 200ms cubic-bezier(0.16, 1, 0.3, 1);
```

Tighter active-press feedback (Hero buttons only) uses a separate single-duration transition rule at `150ms` on `:active`, mirroring the canonical Recipe E pattern in `component-patterns.md` — keeps the atomic-event rule intact within each state declaration.

Exit easing: `cubic-bezier(0.4, 0, 1, 1)`.

### 5-state inheritance (canonical, applies to every interactive component)

The contour-intensity tier table above defines **Resting / Hover / Active** per button. **Focus** and **Disabled** are constant across all tiers per the canonical 5-state machine (`SKILL.md` line 343-358):

- **Focus** (additive overlay): `outline: 1px solid rgba(0, 229, 255, 0.3); outline-offset: 2px;` on `:focus-visible` — overlays any other state
- **Disabled**: `opacity: 0.4; cursor: not-allowed;`; all transitions cancelled (snap-disable, no fade); no hover/active state change
- **Pressed/loading semantics for Save-Suite, Replay, Run**: when an action is in flight (network request pending), the button enters disabled state; the parent component owns the loading flag

This applies uniformly to: Run probe button, Save-Suite, Replay, Copy-md icon, tab cells, segmented controls, retire confirmation buttons, and all suite-row actions.

### Forge motion personality assignments

| Action | Stage | Animation (per `component-patterns.md` canonical definitions) |
|---|---|---|
| Run probe (click) — progress view mounts | Optimize | `scale-in` 300ms `cubic-bezier(0.16, 1, 0.3, 1)` applies to the `TopicProbeProgressView` panel as it enters the layout, NOT to the button itself (buttons get the standard 200ms hover transition only) |
| Save-as-Suite (click → toast) | Validate | `forge-spark` one-shot on button (yellow flash + scale 1.2→1.0 + rotation per the canonical `@keyframes forge-spark` definition — do not re-derive the rotation value at spec time) → toast `slide-in-right` 300ms |
| Replay (click) | Strategy | `slide-in-right` on new replay row (decisive lateral snap) |
| Emergent taxonomy node | Validate | `forge-spark` on the new node |
| Regression nominal → firing | Validate | `forge-spark` on StatusBar badge once, then static red |
| Copy-as-markdown | Validate | `copy-flash` — **green `#22ff88` tint** (color is the load-bearing anchor: success/health per `SKILL.md` line 233). Duration governed by the shipped `--duration-copy-flash` token in `app.css:110` (currently 1500ms; `component-patterns.md` line 219 records the original 600ms target — the implementation has drifted and is the authoritative source per `feedback_visual_feel_over_spec.md` "Update spec to match what shipped, not the other way around"). T2 ships **REUSE** of the existing `useCopyFlash()` primitive at `frontend/src/lib/utils/copy-feedback.svelte.ts` — do NOT re-derive timings; the CSS token is the single source of truth. Follow-up doc sync: `component-patterns.md` line 219 to be updated to the shipped 1500ms value at T2 release time. |
| Report card mount | Optimize | `dialog-in` 300ms (canon: scale `0.95→1` + opacity 0→1, centered, per `component-patterns.md` line 213) |
| Per-prompt progress strip cell fill | Analyze | `slide-up-in` per cell as events arrive (200ms entrance) |

All respect `prefers-reduced-motion → 0.01ms`.

### Voice

| Surface | Copy |
|---|---|
| Save button | `SAVE SUITE` |
| Replay button | `REPLAY` |
| Badge nominal | `12 ok` (title/lower-case, matches shipped `statusLabel()` pattern in `SeedModal.svelte:222-228` — "the voice is technical/precise rather than shouting all-caps" per the existing run-status convention) |
| Badge firing | `2 alarm` (same — lower-case noun; the chromatic color [red] carries the urgency signal, not the case) |
| Save toast | `Suite saved: <label>` |
| Empty SuitesPanel | `No suites. Save a probe to create one.` |
| Topic-only hint | `Prompts without code grounding.` |
| Regression tooltip | `2 suites · mean below tolerance` |
| Replay-complete (alarm) | `<label> · −0.64 vs baseline` |
| Replay-complete (nominal) | `<label> · within tolerance` |

**Casing conventions across surfaces** (clarifies which surface gets which case to avoid future drift):
- **Button labels** (Save-Suite, Replay, Run): UPPERCASE via `font-display` + `text-transform: uppercase` per the canonical action-button pattern (`SKILL.md` line 303 + shipped `SEED TAXONOMY` modal title in `SeedModal.svelte:239`)
- **Section / sub-section headings**: UPPERCASE via Syne + `letter-spacing: 0.1em` (per `SKILL.md` line 290-291)
- **Toast messages, empty states, tooltips, status-condition badges**: Title- or lower-case (per shipped `statusLabel()` pattern + standard prose conventions)
- **Numerics with units**: no space, no case (`8.2/10`, `42%`, `−0.64`)
- Anchor: `SKILL.md` line 303 + shipped `SeedModal.svelte:222-228` `statusLabel()` for the badge precedent.

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
| `frontend/src/lib/components/taxonomy/SeedModal.svelte` | **Third tab** "TOPIC PROBE" added — current SeedModal has 2 tabs (`'generate'` and `'provide'`, verified at lines 38, 255-263); T2 adds `'topic_probe'` as the third. SeedModal lives in `taxonomy/` directory but is a 2D modal — see §2 path-mismatch note for the cycle-14 banned-vocab audit scope narrowing. |
| `frontend/src/lib/components/layout/StatusBar.svelte` | Mount RegressionBadge |
| Left-nav (Navigator, real path varies — verify under `frontend/src/lib/components/layout/`) | "SUITES" entry → routes to SuitesPanel |

### NEW + EXTENDED stores

| Path | Purpose |
|---|---|
| `frontend/src/lib/stores/suitesStore.ts` (NEW) | Suites list + selected + regression alarm cache (polled from /api/health, refreshed on `taxonomy_changed` SSE) |
| `frontend/src/lib/stores/probesStore.ts` (EXTEND) | 202+polling abstraction — `runProbe()` returns an async iterable; source-agnostic for components. Threshold: `n_prompts > 10` → auto-attach `Prefer: respond-async` |

### `frontend/src/lib/api/suites.ts` (NEW per-domain module)

The project's frontend API client is split into **per-domain modules** under `frontend/src/lib/api/` (verified live: `client.ts`, `clusters.ts`, `domains.ts`, `observatory.ts`, `optimizations.ts`, `readiness.ts`, `runs.ts`, `seed.ts`). T2 introduces a new `suites.ts` module following this canonical layout — NOT a monolithic `api.ts` (which doesn't exist in the project):

```typescript
// frontend/src/lib/api/suites.ts (NEW)
// Imports the canonical apiFetch helper — matches the existing pattern in
// frontend/src/lib/api/seed.ts:2 + runs.ts (verified). `client.ts` exports
// apiFetch / tryFetch / streamSSE, NOT a `client` object.
import { apiFetch } from './client';

export function saveSuite(runId: string, label: string, toleranceAbs?: number): Promise<ValidationSuiteOut>
export function replaySuite(suiteId: string): Promise<ReplayRunOut>
export function getSuites(filters?: {projectId?, repoFullName?, includeRetired?}): Promise<ValidationSuiteListResponse>
export function getSuite(suiteId: string): Promise<ValidationSuiteOut>
export function getSuiteReplays(suiteId: string): Promise<RunListResponse>  // items: RunSummary[]
export function retireSuite(suiteId: string, reason: string): Promise<ValidationSuiteOut>
```

`ValidationSuiteListResponse` is the Pydantic envelope defined in §4 (mirrors the `RunListResponse` shape: `{total, count, offset, items, has_more, next_offset}`). `RunListResponse` (no type parameter) is the existing concrete envelope `{... items: RunSummary[] ...}` used by `GET /api/runs` + `GET /api/seed` only. (`GET /api/probes` uses the parallel `ProbeListResponse` envelope keyed off `ProbeRunSummary` items — distinct shape, NOT reused by suites; verified at `routers/probes.py:258` + `schemas/probes.py:190,194` (`ProbeListResponse.items: list[ProbeRunSummary]`).) Frontend consumers import from `$lib/api/suites` (matching the `$lib/api/runs` precedent).

### Accessibility

- `:focus-visible` outline on every interactive element (`1px rgba(0, 229, 255, 0.3)` offset 2px)
- `prefers-reduced-motion` → 0.01ms on all keyframes
- StatusBar regression badge: `aria-live="polite"` `role="status"`
- Save-Suite / Replay buttons: `aria-label` matches visible text
- Color paired with text (`● 2 alarm` not just `●` — note lower-case noun matches the canonical badge copy at the §6 voice table, anchored to the shipped `statusLabel()` pattern in `SeedModal.svelte:222-228`)

## 7 Audit-hook WARN→RAISE flip

**One-line change in `app/config.py`** — the actual surface is a Pydantic-Settings `Field(default=...)` per the project's existing pattern (verified at `app/config.py:248-251`):

```python
WRITE_QUEUE_AUDIT_HOOK_RAISE: bool = Field(
    default=True,   # was False — flip per v0.4.22 post-soak gate
    description="If True (CI/prod), audit hook RAISES WriteOnReadEngineError on read-engine writes outside the allow-list; otherwise WARN. Kill-switch: set WRITE_QUEUE_AUDIT_HOOK_RAISE=false to revert.",
)
```

Existing logic at `database.py:395-397` already routes to `raise WriteOnReadEngineError(...)` when True. No behavioral code changes — only the `Field(default=...)` value flips.

**Soak gate (mandatory pre-flip):**

```bash
# Run on or after 2026-05-18 (≥7 days post-v0.4.21 ship date 2026-05-11)
grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100
# Expect: zero new WARN sources beyond known/explained ones
```

If any new WARN source appears, flip blocks and the unexpected write path gets restructured before T2 ships.

**Sequencing:** flip is the **last commit** in the T2 PR train, after all other cycles green. Release tag v0.4.22 includes flip. Flip cycle merges no earlier than 2026-05-18.

**Kill switch:** operators can set `WRITE_QUEUE_AUDIT_HOOK_RAISE=false` env var. Documented in `backend/CLAUDE.md` write-queue contract section.

**3 new RED tests in Cycle 15:**

1. **`test_audit_hook_default_is_raise`** — locks the flipped default (`WRITE_QUEUE_AUDIT_HOOK_RAISE` defaults `True` at process start).
2. **`test_audit_hook_emits_zero_warn_under_full_t2_pipeline`** — extended integration test (decorated `@pytest.mark.integration`). Exercises every T2 write path under `WRITE_QUEUE_AUDIT_HOOK_RAISE=True` (save-as-suite, replay, topic-only probe, retire, regression-alarm computation) and asserts zero `read-engine audit:` WARN lines.
3. **`test_audit_hook_kill_switch_env_var_reverts_to_warn`** — exercises the kill-switch contract documented in §7: setting `WRITE_QUEUE_AUDIT_HOOK_RAISE=false` via env var override reverts the audit hook to WARN-only behavior (matching pre-v0.4.22 v0.4.21 semantics). Required because the kill-switch is a documented operator escape hatch — without an explicit test, a future refactor could silently break it.

**Existing P4 test stays unchanged** (regression-guard only, not a new RED test for T2): `test_audit_hook_emits_zero_warn_under_full_pipeline` covers analyze / optimize / score / refine / save_result handlers (the P4 surface that gated the audit-hook flip precondition). Must continue PASSing at v0.4.22 ship per `backend/CLAUDE.md` Foundation P4 entry — verified as a side-effect of the same `WRITE_QUEUE_AUDIT_HOOK_RAISE=True` flip, no test code change required.

All three new RED tests run under `pytest -m integration`. The 3-count is consistent with the §10 cycle-15 row "3" entry.

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

`RunOrchestrator.dispatch_async()` shape + cancellation contract diff are detailed in §5 "NEW `dispatch_async()` for 202+polling callers" — including the explicit `current_run_id` ContextVar transfer into the spawned task and the `_run_to_completion` wrapper that holds the `asyncio.shield()` invariant.

**Race-safety:** initial `RunRow(status='running')` is committed before route returns. Client's first poll always finds the row.

**Task lifetime:** HTTP request returning 202 does NOT cancel the spawned task. Existing GC sweep `_gc_orphan_runs` recovers any task killed by process restart (orphan rows with `status='running'` past 1h TTL).

**Replay** `POST /api/suites/{id}/replay`: always 202 — replays are full pipeline runs with no <30s case.

**Polling cost:** 1 indexed PK SELECT on `run_row` per poll (~1-2ms). 5s cadence × ~10min worst-case = 120 polls/probe. Negligible for single-user dev tool.

**Frontend:** `probesStore.runProbe()` auto-attaches `Prefer: respond-async` when `n_prompts > 10`. Below threshold → SSE (no behavioral change for typical 5-10 prompt probes).

## 9 Observability extensions

**4 new events** introduced by T2 (down from 5 — `probe_replay_completed` deliberately dropped; see §5 ReplayRunGenerator decision rationale):

| Event | Emitted by | Trigger timing | Payload |
|---|---|---|---|
| `validation_suite_created` | `ValidationSuiteService.create_from_run()` | AFTER `WriteQueue.submit(...)` completes successfully. NOT emitted on submit failure. Not idempotent — each successful create emits once. | `suite_id, source_run_id, label, tolerance_abs, prompts_count, baseline_mean, project_id` |
| `validation_suite_retired` | `ValidationSuiteService.retire()` | AFTER `WriteQueue.submit(...)` completes, **only when state actually transitioned** (idempotent re-retire of already-retired suite = no event) | `suite_id, reason` |
| `probe_warning` | `ReplayRunGenerator.run()` (also reserved for future generator warnings) | During the run, before per-prompt loop, when `repo_drift` detected. Currently single code `Literal['repo_drift']`. | `run_id, code, suite_repo, current_repo` |
| `regression_alarm_transition` | `ValidationSuiteService.compute_regression_alarm()` | After each alarm-query run, **only when ≥1 suite's alarm state transitioned** (nominal↔firing, none→firing, none→nominal). Prior-state map `self._prior_alarm_states: dict[suite_id, Literal['nominal','firing','none']]` lives in-memory on the service instance (resets on process restart — acceptable for status-display observability). | `suite_id, label, previous_state, new_state, baseline_mean, latest_mean, delta_abs` |

**Replay completion events:** replay runs reuse the EXISTING `probe_prompt_completed` (per-prompt) and `probe_completed` (terminal) event types — same payload shapes as topic probe. SSE consumers do not need to learn a new event family. SuitesPanel + RegressionBadge filter polled `RunRow.mode == 'replay_run'` state to surface replay-specific UI; they do not subscribe to a replay-specific event type. **Total `probe_*` event count stays at 8 (7 existing + `probe_warning`).**

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
 │       │         │            │            │                  ZERO inconsistencies on BOTH?
 │       │         │            │            │                  ├ no → re-dispatch failing phase
 │       │         │            │            │                  │       (if fix is behavior-changing,
 │       │         │            │            │                  │        cascade through INTEGRATE +
 │       │         │            │            │                  │        OPERATE + BOTH validators
 │       │         │            │            │                  │        fresh — never proceed-with-notes)
 │       │         │            │            │                  └ yes → cycle complete, advance
 │       │         │            │            │
 │       │         │            │            └ dynamic verification under live load
 │       │         │            └ canonical-pattern parity (static review)
 │       │         └ local code quality (lint, types, edges, docstrings)
 │       └ minimal implementation
 └ failing test documenting the contract
```

**Re-dispatch cascade rule** (per peer-plan v0.4.16-p1a precedent line 65): when a validator returns findings, route back to the relevant phase (typically REFACTOR or GREEN). **If the fix is behavior-affecting, re-run INTEGRATE + OPERATE + BOTH validators with fresh dispatches.** Static-only fixes (e.g., docstring polish caught by code-quality validator) can re-run only the failing phase + both validators. APPROVED-WITH-MINOR is treated identically to FINDINGS PRESENT — the cycle does not advance until both validators return APPROVED-ZERO-INCONSISTENCIES.

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
| 1 | Migration + `ValidationSuite` ORM + `RunRow.__table_args__` `ix_run_row_suite_id` declaration | **7** (pure-schema invariants enumerated: (1) `inspector.has_table('validation_suite')`, (2) 11-column existence assertion, (3) `validation_suite.source_run_id` FK reflection (nullable + ondelete=SET NULL), (4) 3-index existence reflection on `validation_suite` (`ix_validation_suite_project_id`, `_source_run_id`, `_active`), (5) partial-index `WHERE retired_at IS NULL` predicate reflection on `ix_validation_suite_active`, (6) `ix_run_row_suite_id` existence + descending-order reflection, (7) `RunRow.suite_id` FK constraint reflection. **All 7 tests are themselves consumer tests for the ORM + migration code** — they assert reflected DB state via SQLAlchemy `inspect()`, which satisfies "Scaffolding ≠ TDD" without cross-cycle hand-off.) | A2 (migration follows `bdd8e96cf489_consolidate_lifespan_ddl_into_alembic.py` precedent — single forward-only migration; `alembic check` exits clean post-upgrade; `inspector.has_table()` idempotency guard matches consolidation style; ORM `__table_args__` index declarations match migration `op.create_index` calls so `alembic check` reports zero drift; SQLite FK addition uses `batch_alter_table("run_row", recreate="always")` per `a2f6d8e31b09:76,97` precedent) · A5 (test fixtures construct real `ValidationSuite(...)` instances, no `MagicMock`) | O2 (migration completes cleanly under WriteQueue active; `alembic upgrade head` + `downgrade -1` round-trip on fresh DB; `tests/test_main_lifespan_no_ddl.py` continues to pass) |
| 2 | `ValidationSuiteService.create_from_run` + `SuiteSnapshotInputs` | 11 | A4 (`SuiteSnapshotInputs` populates ALL columns; column-by-column diff against `ValidationSuite` ORM) · A5 (real `RunRow` rows in fixtures, not mocked) | O1 (`SELECT * FROM validation_suite WHERE id=:s` after create — confirm row exists with all columns populated; **never trust the function's return value as evidence persistence happened** per O1 canon) |
| 3 | `.retire`, `.get`, `.list`, `.list_replays` + `SuiteRetireInputs` + `validation_suite_retire` operation label | 9 | A4 (retire updates only `retired_at`/`retired_reason` — no other column mutated; column-by-column post-update diff) · A2 (use `WriteQueue.submit()` — do not write directly) | O1 (re-retire is idempotent and persists no-op; `validation_suite_retired` event NOT emitted on re-retire — assert via event-bus subscription) |
| 4 | `.compute_regression_alarm` + 30s TTL cache + `regression_alarm_transition` event | 7 | A3 (Python-side filter matches canonical alarm-evaluation logic; cite the spec's SQL + Python filter as the canonical reference) · A1 (alarm-query result columns ALL consumed — none silently dropped) | O2 (alarm query under concurrent `replay_run` writes — no inconsistent snapshots; verify with concurrent writer harness) · O6 (cross-process replay writes from MCP do not corrupt alarm state) |
| **B — REST + Generators (5-9)** | | | | |
| 5 | `routers/suites.py` (6 endpoints, rate limits, error envelopes) | 15 | A2 (use `WriteQueue.submit()` indirectly via `ValidationSuiteService` — do NOT re-implement persistence in the router) · A4 (response payloads populate all `ValidationSuiteOut` / `ReplayRunOut` / `RunListResponse` fields — no NULL leakage from JOIN queries) | O5 (client disconnects mid-replay-dispatch — no orphan rows; test with `curl --max-time 0.1` against `POST /api/suites/{id}/replay`) · O8 (replay endpoint INSERT-then-spawn — cancellation between INSERT commit and task spawn must be reconciled by `_gc_orphan_runs` within TTL) |
| 6 | `ReplayRunGenerator` + lifespan registration extension | 11 | A2 (use `batch_pipeline.run_single_prompt` — NOT a hand-rolled inner pipeline) · A3 (diff `ReplayRunGenerator.run()` return shape against `TopicProbeGenerator.run()` and `SeedAgentGenerator.run()`; any `GeneratorResult` field the canonical generators populate that replay leaves default must have documented justification — `taxonomy_delta={}` is justified inline in §5 (replay produces no taxonomy delta) and `final_report` is set to a non-None minimal markdown stub per §5 lines 793-797 since `RunResult.final_report: str` is non-nullable at `schemas/runs.py:49`) · A5 (replay tests construct real `ValidationSuite(...)` rows and real `SuiteSnapshotInputs` instances; no MagicMock for the snapshot dataclasses) · A6 (every prompt receives full enrichment context — codebase + strategy intelligence + applied patterns — same as `batch_orchestrator.run_batch` canonical pattern) · A1 (read every field of `run_single_prompt`'s **`PendingOptimization`** — not just `overall_score`; the 5 score dimensions, `id`, `task_type` are each used by the replay result-handler per §5) | O3 (per-prompt persistence routes through queue in short batches — replay's 30-min worst-case duration must NOT hold any single write transaction >5s; verify warm-engine debounce + hot-path commits do not starve under sequential-replay load) · O4 (warm-engine debounce fires mid-replay — no contention) · O7 (replay's worst-case 30-min duration — every timeout in path: LLM provider, HTTP client, async_wait_for — must be ≥ that plus buffer) · O8 (cancel mid-replay — `RunRow.status` reaches terminal `failed` via `asyncio.shield()` write; test by killing the spawned task at every per-prompt boundary) |
| 7 | Topic-only mode — `TopicProbeGenerator` branch + 2nd template + `probe_generation` signature extension + `ProbeContext` schema extension | 9 | A2 (don't re-implement Phase 2 generation — call existing `probe_generation.generate_probe_prompts` with new `mode`/`template_name` kwargs) · A3 (consume same return shape across both modes; downstream phases handle empty `relevant_files`/`None` `explore_synthesis_excerpt` as valid degenerate state) | O1 (`RunRow.topic_probe_meta.grounding_mode='topic_only'` persisted and queryable via `GET /api/runs/{id}`) |
| 8 | 202+polling on `POST /api/probes` + `dispatch_async` + `current_run_id` ContextVar transfer | 7 | A2 (`dispatch_async` reuses existing `RunOrchestrator._create_row` + `_persist_final` lifecycle; SSE entry path refactors to internally call `dispatch_async` — single-codepath body shared between SSE + 202) | O1 (poll endpoint reflects committed row immediately after 202 returns — INSERT awaited before response; verify with race-stress test: dispatch_async + immediate GET, 1000 iterations) · O5 (curl `--max-time` shorter than initial INSERT — server-side state remains consistent under client disconnect mid-INSERT) · O7 (inventory every timeout in `dispatch_async` path: initial-INSERT WriteQueue timeout, spawned-task max duration, `_gc_orphan_runs` TTL; longest must ≥ worst-case probe duration plus buffer) · O8 (spawned task survives 202 caller's connection close via `asyncio.shield`; verify by closing connection mid-spawn and checking `RunRow` reaches terminal status) |
| 9 | `/api/health` regression_alarm block | 5 | A4 (`RegressionAlarmEntry` populates all required fields from joined query — no NULL leakage; column-by-column diff against the join-SQL result columns in spec §5) | O2 (alarm query coexists with active replays — no read-write contention; verify with 30 concurrent `/api/health` polls during an active replay) |
| **C — MCP (10)** | | | | |
| 10 | `synthesis_save_suite` + `synthesis_replay_suite` | 9 | A2 (MCP handlers call `ValidationSuiteService` methods — don't re-implement persistence) · A4 (`SaveSuiteOutput` + `ReplayInitiatedOutput` populate all fields per `schemas/mcp_models.py` extensions) · A5 (MCP test fixtures use real `RunRow` + `ValidationSuite` rows, not mocked) | O6 (cross-process MCP→backend bridge for `replay_run` writes works under load — verify with concurrent MCP `synthesis_replay_suite` from 3 clients) · O5 (MCP client disconnects after `synthesis_save_suite` returns — write already committed; verify by inspecting DB after disconnect) |
| **D — Frontend (11-13)** | | | | |
| 11 | Topic Probe tab + form + report card (Vitest + Playwright) | 18 | A2 (use existing per-domain modules under `frontend/src/lib/api/` — `runs.ts`/`seed.ts`/`suites.ts` (NEW) — do not fetch directly; the project has no monolithic `api.ts`, verified by `ls frontend/src/lib/api/`; confirm `suites.ts` is imported by SuiteRow/SuiteDetailView/RegressionBadge consumers, not duplicated inline) · A3 (consume canonical `RunListResponse` / `RunSummary` / `RunResult` shapes from `schemas/runs.py` — every field rendered or intentionally hidden; no fictional `RunOut`) | O1 (browser smoke: SeedModal renders the Topic Probe tab, form submits, report card displays with all fields populated from `RunResult`) |
| 12 | SuitesPanel + SuiteRow + SuiteDetailView + RegressionBadge + SuitesStore | 14 | A3 (suite list rows render every field of `ValidationSuiteListItem`; `ReplayRunOut.warnings` displayed when present; SuiteDetailView consumes full `ValidationSuiteOut` including `baseline_scores.per_prompt`) | O1 (browser smoke: SuitesPanel renders for empty / nominal / firing states; RegressionBadge transitions correctly on SSE-emitted `regression_alarm_transition` events) |
| 13 | 202+polling abstraction in probesStore | 7 | A2 (single async-iterable contract — SSE + poll paths emit identical event shapes; consumers must not branch on source) | O5 (browser tab closes mid-probe — store cleans up poll interval, no leaked timers) · O7 (long probe runs >10min — poll cadence persists, no client-side timeout) |
| **E — Quality (14, gate-only)** | | | | |
| 14 | Brand audit + a11y audit (axe-core) — **GATE CYCLE, not a 7-dispatch protocol cycle.** No RED→GREEN code change. Runs Validator 1 (brand-canon compliance — banned-vocab + banned-CSS grep against the canonical 7-directory scope `frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/`; `taxonomy/` directory explicitly excluded per 3D-scope canon, see §6 banned-list + §13.8 + pre-release checklist item 4) + Validator 2 (axe-core a11y) only. Any violations found here loop back to the cycle where the offending code lives (typically 11-13). | n/a | n/a | n/a |
| **F — Flip + Release (15-16)** | | | | |
| 15 | Audit-hook RAISE flip + new regression test + extended integration test | 3 | A2 (no behavioral logic change — only the default in `app/config.py` flips `False → True`) | **All O1-O8** — the extended integration test `test_audit_hook_emits_zero_warn_under_full_t2_pipeline` exercises every T2 write path under `WRITE_QUEUE_AUDIT_HOOK_RAISE=True` (save-as-suite, replay, topic-only probe, retire, regression-alarm computation) and must emit zero `read-engine audit:` lines. Existing P4 test `test_audit_hook_emits_zero_warn_under_full_pipeline` must continue PASSing. |
| 16 | E2E validation workflow + soak grep verification — **GATE CYCLE, not a 7-dispatch protocol cycle.** No new RED tests; runs Validator 1 (round-trip success) + Validator 2 (soak-log grep) only. | n/a | n/a | n/a |

**~132 new RED tests** (sum: 7+11+9+7+15+11+9+7+5+9+18+14+7+3 = 132) + extended integration + E2E smoke. Backend ≥90% coverage, frontend ≥80%.

**Pydantic schemas + operation-label cycle assignment** (per "Scaffolding ≠ TDD" hard invariant):

| Schema / label / helper | Lands in cycle (GREEN step) |
|---|---|
| `SaveSuiteRequest`, `ValidationSuiteOut`, `SuiteSnapshotInputs`, nested `PromptSnapshotItem`/`PerPromptScore`/`BaselineScoresPayload`, `validation_suite_create` op label | Cycle 2 (consumed by `create_from_run`) |
| `RetireSuiteRequest`, `SuiteRetireInputs`, `validation_suite_retire` op label | Cycle 3 (consumed by `retire`) |
| `ValidationSuiteListItem`, `ValidationSuiteListResponse` | Cycle 3 (consumed by `list`) |
| `schemas/runs.py::RunRequest.mode` + `RunSummary.mode` + `RunResult.mode` Literal extensions to add `'replay_run'` (3 sites) + `routers/runs.py::list_runs::mode` Query Literal extension (4th site — public-API surface change) | Cycle 5 (consumed by `POST /api/suites/{id}/replay` router which dispatches `mode='replay_run'`; the Pydantic Literal extensions are scaffolding without these consumer endpoints, so they land here) |
| `ReplayRunOut` | Cycle 5 (consumed by `POST /api/suites/{id}/replay` router) |
| `replay_run_persist` op label | Cycle 6 (consumed by `RunOrchestrator._persist_final()` mode-keyed label selection) |
| `services/generators/_aggregate.py::compute_run_aggregate()` shared helper | Cycle 6 (consumed by `ReplayRunGenerator.run()` aggregation step; `TopicProbeGenerator._build_aggregate` refactored to thin delegate in the same cycle to share the helper) |
| `schemas/probes.py::ProbeContext` schema extensions (`repo_full_name: str \| None`, `commit_sha`, `topic_only`) | Cycle 7 (consumed by `TopicProbeGenerator` topic-only branch; the schema relaxation is scaffolding without the branch consuming it) |
| `RegressionAlarmEntry`, `RegressionAlarmBlock` | Cycle 9 (consumed by `/api/health` block) |
| `SaveSuiteOutput`, `ReplayInitiatedOutput` (`schemas/mcp_models.py` extensions) | Cycle 10 (consumed by MCP handlers) |
| `services/generators/_constants.py::PROBE_PROMPT_CONCURRENCY` | **CANON DEVIATION FOOTNOTE** — this constant is explicitly *not consumed in T2* (forward-declared per §5 concurrency note). The canon's "Scaffolding ≠ TDD" rule would prefer the constant be deferred to T3+ alongside its first consumer. T2 ships it now to lock the single-source-of-truth path so probe + replay won't accidentally pick different values when parallelization lands. **Intentional documented deviation** — NO new RED test is added for the constant (would itself violate the canon's "pure constant without a failing consumer test" prohibition). The constant lands as a 1-line module-level definition in Cycle 6 GREEN step alongside `services/generators/_aggregate.py` (both house "shared generator-subpackage scaffolding for replay's structural extraction"). Reviewer should accept this as an explicit, documented exception rather than a scaffolding violation. |

Most pure types ride in the GREEN step of their first consumer cycle per the canon's anti-scaffolding rule. The single explicit exception is `PROBE_PROMPT_CONCURRENCY` — forward-declared without a T2 consumer, with rationale documented above.

### Hard invariants (non-negotiable per `feedback_tdd_protocol.md`)

- The RED test still passes after every subsequent phase
- No new features / no scope creep beyond cycle's RED+GREEN scope
- Reviewers receive polished code at the post-OPERATE handoff — they focus on strategic concerns (architecture fit, cross-cycle coherence), not mechanical items the prior phases should have caught
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

**Single feature branch** `feature/probe-tier-2`. **Rebase-merge** per `feedback_pr_merge_strategy.md`. Estimated **~137-172 commits**:
- 14 protocol cycles × 7 dispatches = 98 implementer+validator commits (cycles 1-13, 15)
- 2 gate cycles × 2 validators = 4 commits (cycles 14, 16)
- + ~5-10 process commits (spec, plan, CHANGELOG, release-script bump)
- + ~30-60 iteration commits when validators return APPROVED-WITH-MINOR / FINDINGS PRESENT and behavior-affecting fixes cascade through INTEGRATE+OPERATE+both validators

= 98 + 4 = **102 baseline** + (5-10 process) + (30-60 iteration) = **35-70 above baseline** = **137-172 total**. Foundation P3 (95 commits, 5 spec review rounds) and P4 (~50 commits, 1 plan review round) provide reasonable bracketing.

### Pre-release checklist (gated)

1. `grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100` — zero unexpected sources in last 7 days
2. All 16 cycles GREEN — `pytest --cov=app -v` ≥90% backend coverage
3. `npm run test:coverage` ≥80% frontend coverage
4. Brand audit pass — no `box-shadow` with spread/blur, no `text-shadow`, no `filter: drop-shadow`, no banned-vocab standalone-word hits (word-boundary regex `(?<![-\w])(glow|halo|bloom|radiance|breathing|dust|pulse|flash)(?![-\w])` — permits hyphenated canon like `copy-flash`/`forge-spark`/`useCopyFlash`) in `frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/`
5. A11y audit pass — axe-core clean on all new components
6. `alembic check` clean
7. `init.sh restart` smoke test — all services green
8. E2E validation: probe → save-as-suite → replay → regression detection round-trip
9. Backward-compat smoke: `POST /api/probes` without `Prefer:` header — SSE response unchanged
10. CHANGELOG entry written in canon voice (per `feedback_changelog_voice.md`)
11. Stale-doc fix: `backend/CLAUDE.md` T2/T3/T4 minor numbers harmonized with ROADMAP; `docs/ROADMAP.md` line 333 (`POST /api/probes/{id}/replay`) updated to reflect the relocated endpoint `POST /api/suites/{suite_id}/replay`; `.claude/skills/brand-guidelines/SKILL.md` lines 79/214/375 (line 395 is a prose scope statement without an enumerated directory list — exclude from substitution) extended from 5-directory to 7-directory scope; `.claude/skills/brand-guidelines/SKILL.md` line 140 StatusBar height updated 22px → 20px per shipped reality; `.claude/skills/brand-guidelines/references/component-patterns.md` line 219 `copy-flash` updated from 600ms to shipped 1500ms. **Cycle assignment for these doc-sync changes**: land in the same PR as Cycle 12 (frontend SuitesPanel/RegressionBadge) since the canon updates live adjacent to the new consumers — keeps doc + impl in lock-step.

### CHANGELOG preview (canon voice)

The example below is a CHANGELOG fragment; the leading `##` and `###` markers are markdown literal text inside a code fence and do NOT participate in this spec's heading outline. The spec's own section numbering remains 1-14 sequential.

```text
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
    - New events (4): validation_suite_created, validation_suite_retired, probe_warning, regression_alarm_transition. Replay terminal events reuse existing probe_completed event type.
    - New operation labels (3): validation_suite_create, validation_suite_retire, replay_run_persist (mode-keyed via RunOrchestrator._persist_final).

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
| Positional `baseline_scores.per_prompt[i]` drift on replay | L | M | T2 ships replay as **sequential per-iteration** (`for idx, item in enumerate(...)` with `prompt_results.append(...)` per §5) — `raw_prompt_idx=idx` tagging preserves positional correspondence by construction. Integration test asserts parity across N=10 prompts sequentially (no concurrency in T2; `PROBE_PROMPT_CONCURRENCY` is forward-declared for T3+ parallelization per §5 concurrency note). |
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
| Warm-path / taxonomy queries unexpectedly filter by `RunRow.mode` | L | L | Verified by grep across `services/taxonomy/*.py`: zero `RunRow.mode == 'X'` filters exist; replay_run rows have zero interaction with taxonomy cluster lifecycle (replay populates `Optimization` rows the same way as a normal pipeline run). Documented for future-architecture reference. |

## 13 Success criteria

T2 ships when ALL true:

1. **Schema** — `validation_suite` table + 3 indexes (`ix_validation_suite_project_id`, `ix_validation_suite_source_run_id`, `ix_validation_suite_active` partial-on `retired_at IS NULL DESC on created_at`) + `ix_run_row_suite_id` on `(suite_id, started_at DESC)` + `fk_run_row_suite_id` FK all exist on a freshly migrated DB; ORM `__table_args__` declarations match the migration (zero `alembic check` drift); `alembic upgrade head` + `downgrade -1` round-trip cleanly on fresh test DB
2. **Cycle completion** — All **14 protocol cycles** (1-13, 15) complete the full 7-dispatch pipeline (RED → GREEN → REFACTOR → INTEGRATE → OPERATE → Validator 1 spec-compliance → Validator 2 code-quality) with **APPROVED-ZERO-INCONSISTENCIES** on both validators. **Cycles 14 (brand/a11y audit) and 16 (E2E smoke + soak grep) are gate-only checkpoints** — Validator 1 + Validator 2 only, no RED→GREEN code change. **No protocol phase is skipped** per `feedback_tdd_protocol.md` hard invariants. ~132 new RED tests all GREEN.
3. **Coverage** — Backend ≥90%, frontend ≥80%
4. **E2E round-trip** — Manual: probe → save-as-suite → manually degrade scoring weights → replay → alarm fires red on `/api/health` → revert weights → replay → alarm clears green
5. **Audit-hook flip** — `WRITE_QUEUE_AUDIT_HOOK_RAISE` defaults `True`; **both** `test_audit_hook_emits_zero_warn_under_full_pipeline` (P4 test) AND `test_audit_hook_emits_zero_warn_under_full_t2_pipeline` (T2 test) PASS; 7-day soak grep clean
6. **Backward compat** — `POST /api/probes` without `Prefer:` header returns SSE response identical to v0.4.21; T1 probes work end-to-end with zero behavior change; existing seed agent flows unchanged; `GET /api/runs?mode=...` accepts the new `replay_run` Literal value (Pydantic schemas extended without breaking existing `topic_probe`/`seed_agent` callers)
7. **MCP** — `synthesis_save_suite` + `synthesis_replay_suite` structured_output verified; error envelopes for `run_not_completed`, `run_missing_aggregate`, `suite_retired`
8. **Brand audit** — Cycle 14 grep returns zero hits for `box-shadow.*[1-9]px [1-9]`, `text-shadow`, `filter: drop-shadow`, and **standalone-word-only** `(?<![-\w])(glow|halo|bloom|radiance|breathing|dust|pulse|flash)(?![-\w])` in `frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/` (`taxonomy/` directory explicitly excluded per 3D-scope canon; hyphenated canonical identifiers like `copy-flash`, `forge-spark`, `useCopyFlash` are permitted via word-boundary regex)
9. **A11y audit** — axe-core clean on all new components
10. **Observability** — All **4 new events** (`validation_suite_created`, `validation_suite_retired`, `probe_warning`, `regression_alarm_transition`) emit via JSONL + ring buffer + SSE; visible in `ActivityPanel` filtered to `path=validation_suite` or `path=regression_alarm`. Replay terminal events surface via the reused `probe_completed` event type.
11. **Health endpoint** — `regression_alarm` block valid shape under all conditions; 30s cache respected
12. **Docs hygiene** — CHANGELOG entry under `## v0.4.22`; `backend/CLAUDE.md` T2/T3/T4 harmonized; project root `CLAUDE.md` shows v0.4.22 SHIPPED; `docs/SHIPPED.md` gains v0.4.22 entry; `docs/ROADMAP.md` line 333 updated to reflect `POST /api/suites/{suite_id}/replay` path placement

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

- Final UI consolidation: SeedModal collapses from **3 tabs** (post-T2: `generate` / `provide` / `topic_probe`) to **1 unified tab with two modes** (template-driven / topic-driven) sharing the form scaffold
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
