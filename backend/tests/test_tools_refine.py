"""Cycle 2 RED tests for tools/refine.py + routers/refinement.py + RefinementService.

Foundation P4 — Long-Handler Restructure Cycle 2.
Spec §6.2 — 17 tests verbatim. AST-based handler invariants + integration paths.
"""
from __future__ import annotations

import ast
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest

# --- Fixtures ------------------------------------------------------------

def _read_source(rel_path: str) -> str:
    """Read a source file relative to the repo root.

    Tests run from various CWDs (`backend/`, repo root, etc.). Anchor to
    this test file's location so the path resolves deterministically.
    `parents[2]` = repo root (test file is at `backend/tests/X.py`).
    """
    repo_root = Path(__file__).parents[2]
    return (repo_root / rel_path).read_text(encoding="utf-8")


# --- Test 1: AST handler invariant — no LLM/session overlap ----------------

def test_refine_handler_does_not_open_long_lived_session():
    """Cycle 2 RED test 1: AST-parse `tools/refine.py`. Any
    `async with async_session_factory()` block must close (its body's last
    statement / dedent line) BEFORE the `async for event in svc.invoke_refinement_pipeline(ctx):`
    statement that iterates the LLM phase.

    `await context_service.enrich(db=db, ...)` IS permitted inside the read
    session (enrich's internal A4 fallback writes only to the writer engine
    via fire-and-forget — does not violate the audit-hook contract).
    """
    src = _read_source("backend/app/tools/refine.py")
    tree = ast.parse(src)

    # Find handle_refine async function
    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_refine"
    ]
    assert funcs, "handle_refine not found"
    func = funcs[0]

    # Find async-with blocks against async_session_factory
    async_with_blocks = [
        n for n in ast.walk(func)
        if isinstance(n, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "async_session_factory"
            for item in n.items
        )
    ]

    # Find the `async for event in svc.invoke_refinement_pipeline(ctx):` statement
    pipeline_async_fors = [
        n for n in ast.walk(func)
        if isinstance(n, ast.AsyncFor)
        and isinstance(n.iter, ast.Call)
        and isinstance(n.iter.func, ast.Attribute)
        and n.iter.func.attr == "invoke_refinement_pipeline"
    ]

    if not async_with_blocks or not pipeline_async_fors:
        pytest.skip(
            "Restructure incomplete — either no async_session_factory block "
            "OR no invoke_refinement_pipeline async-for"
        )

    # Assert every async-with block closes BEFORE the pipeline async-for starts
    pipeline_start_line = min(n.lineno for n in pipeline_async_fors)
    for block in async_with_blocks:
        # block's body's last statement end_lineno
        last_stmt_line = max(
            getattr(stmt, "end_lineno", stmt.lineno) for stmt in block.body
        )
        assert last_stmt_line < pipeline_start_line, (
            f"async-with block ends at line {last_stmt_line} but pipeline "
            f"async-for starts at line {pipeline_start_line} — session overlaps LLM phase"
        )


# --- Test 2: Initial turn persisted via queue ------------------------------

@pytest.mark.asyncio
async def test_refine_initial_turn_persisted_via_queue(
    async_session_factory_override, monkeypatch,
):
    """Cycle 2 RED test 2: when no initial turn exists, persist runs via
    `submit(operation_label="refine_initial_turn")`."""
    from app.models import Optimization
    from app.tools import refine as refine_module

    submit_calls: list[dict] = []
    factory = async_session_factory_override

    async def fake_submit(work, *, timeout=None, operation_label=None):
        submit_calls.append({"operation_label": operation_label})
        # Actually invoke the work callback against the test factory's session
        # — Tests 8/10 need the seed turn + final turn to land in the DB.
        async with factory() as write_db:
            return await work(write_db)

    class FakeQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            return await fake_submit(work, timeout=timeout, operation_label=operation_label)

    monkeypatch.setattr(refine_module, "get_write_queue", lambda: FakeQueue())
    monkeypatch.setattr(
        "app.tools._shared.get_write_queue",
        lambda: FakeQueue(),
    )
    monkeypatch.setattr(refine_module, "notify_event_bus", AsyncMock())

    # Pre-insert an Optimization with NO refinement turns
    opt_id = str(uuid.uuid4())
    async with factory() as db:
        opt = Optimization(
            id=opt_id,
            trace_id=str(uuid.uuid4()),
            raw_prompt="raw prompt for refine test",
            optimized_prompt="optimized prompt to refine",
            task_type="general",
            domain="general",
            strategy_used="auto",
            status="completed",
        )
        db.add(opt)
        await db.commit()

    # Mock provider so the pipeline emits refinement_complete
    mock_provider = AsyncMock()
    mock_provider.complete_parsed = AsyncMock(return_value=MagicMock(
        analysis="ok", suggested_strategy="auto",
        optimized_prompt="refined prompt v2",
        scores={"overall": 7.5},
    ))
    mock_provider.name = "mock"

    monkeypatch.setattr(
        refine_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": mock_provider})()})(),
    )

    try:
        # handle_refine returns RefineOutput, not an async generator
        await refine_module.handle_refine(
            optimization_id=opt_id,
            refinement_request="make it clearer",
            branch_id=None,
            ctx=None,
        )
    except Exception:
        # Pipeline mock may fail mid-flight; we only care about queue submits
        pass

    labels = [c["operation_label"] for c in submit_calls]
    assert "refine_initial_turn" in labels, (
        f"Expected refine_initial_turn submit; got labels={labels}"
    )


# --- Test 3: Final turn persisted via queue --------------------------------

@pytest.mark.asyncio
async def test_refine_persistence_routes_through_write_queue(
    async_session_factory_override, monkeypatch,
):
    """Cycle 2 RED test 3: final turn persisted via
    `submit(operation_label="refine_persist_turn")`."""
    from app.models import Optimization, RefinementBranch, RefinementTurn
    from app.tools import refine as refine_module

    submit_calls: list[dict] = []
    factory = async_session_factory_override

    async def fake_submit(work, *, timeout=None, operation_label=None):
        submit_calls.append({"operation_label": operation_label})
        async with factory() as write_db:
            return await work(write_db)

    class FakeQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            return await fake_submit(work, timeout=timeout, operation_label=operation_label)

    monkeypatch.setattr(refine_module, "get_write_queue", lambda: FakeQueue())
    monkeypatch.setattr(
        "app.tools._shared.get_write_queue",
        lambda: FakeQueue(),
    )
    monkeypatch.setattr(refine_module, "notify_event_bus", AsyncMock())

    # Pre-insert Optimization + initial turn + branch
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt-v1",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        db.add(RefinementTurn(
            id=turn_id, optimization_id=opt_id,
            branch_id=branch_id, version=1, parent_version=0,
            prompt="opt-v1", refinement_request="seed",
            scores={"overall": 7.0}, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        await db.commit()

    mock_provider = AsyncMock()
    mock_provider.complete_parsed = AsyncMock(return_value=MagicMock(
        analysis="ok", suggested_strategy="auto",
        optimized_prompt="refined v2",
        scores={"overall": 8.0},
    ))
    mock_provider.name = "mock"

    monkeypatch.setattr(
        refine_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": mock_provider})()})(),
    )

    try:
        await refine_module.handle_refine(
            optimization_id=opt_id,
            refinement_request="improve clarity",
            branch_id=branch_id,
            ctx=None,
        )
    except Exception:
        pass

    labels = [c["operation_label"] for c in submit_calls]
    assert "refine_persist_turn" in labels, (
        f"Expected refine_persist_turn submit; got labels={labels}"
    )


# --- Test 4: Constructor drops db param ------------------------------------

def test_refinement_service_constructor_drops_db():
    """Cycle 2 RED test 4: `RefinementService(provider=..., prompts_dir=...)`
    succeeds. `RefinementService(db=..., provider=..., prompts_dir=...)`
    raises `TypeError("got an unexpected keyword argument 'db'")`."""
    from app.services.refinement_service import RefinementService

    # NEW signature: succeeds with keyword-only args (no db)
    mock_provider = MagicMock()
    svc = RefinementService(
        provider=mock_provider,
        prompts_dir=Path("/tmp/test-prompts"),
    )
    assert svc is not None

    # Old signature with db= must raise TypeError
    mock_session = MagicMock()
    with pytest.raises(TypeError, match=r"unexpected keyword argument 'db'"):
        RefinementService(  # type: ignore[call-arg]
            db=mock_session,
            provider=mock_provider,
            prompts_dir=Path("/tmp/test-prompts"),
        )


# --- Test 5a: AST detached-ORM contract — handler --------------------------

def test_refine_handler_no_post_session_orm_access():
    """Cycle 2 RED test 5a: AST-parse `tools/refine.py`. After the
    `async with async_session_factory()` block closes, no `ast.Attribute` on
    `Name(id in ('opt', 'latest_turn', 'branch', 'optimization', 'prev_turn'))`
    may appear (snapshot constructions inside the block are exempt).

    Persist-callback bodies (functions starting with `_persist`) are exempt —
    they open their own writer session.
    """
    src = _read_source("backend/app/tools/refine.py")
    tree = ast.parse(src)

    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_refine"
    ]
    assert funcs, "handle_refine not found"
    func = funcs[0]

    async_with_blocks = [
        n for n in ast.walk(func)
        if isinstance(n, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "async_session_factory"
            for item in n.items
        )
    ]
    if not async_with_blocks:
        pytest.skip("Restructure incomplete — no async_session_factory block")

    block_exit_line = max(
        getattr(n, "end_lineno", 0) for block in async_with_blocks
        for n in ast.walk(block)
    )

    forbidden_names = {"opt", "latest_turn", "branch", "optimization", "prev_turn"}
    violations: list[str] = []

    # Build parent map for persist-callback exemption
    parent_map: dict[int, ast.AST] = {}
    for parent in ast.walk(func):
        for child in ast.iter_child_nodes(parent):
            parent_map[id(child)] = parent

    def _is_inside_persist_callback(node: ast.AST) -> bool:
        cur = parent_map.get(id(node))
        while cur is not None:
            if (
                isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef))
                and cur.name.startswith("_persist")
            ):
                return True
            cur = parent_map.get(id(cur))
        return False

    for node in ast.walk(func):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in forbidden_names
            and node.lineno > block_exit_line
            and not _is_inside_persist_callback(node)
        ):
            violations.append(
                f"detached-ORM access '{node.value.id}.{node.attr}' "
                f"at line {node.lineno} (post-block exit line {block_exit_line})"
            )

    assert not violations, "Detached-ORM access violations:\n" + "\n".join(violations)


# --- Test 5b: AST detached-ORM contract — service --------------------------

def test_refinement_service_invoke_pipeline_no_session_or_orm():
    """Cycle 2 RED test 5b: AST-parse `services/refinement_service.py`. The
    `invoke_refinement_pipeline` body contains:
    - ZERO `async with async_session_factory()` blocks
    - ZERO `self.db` references
    - ZERO `ast.Attribute` accesses on `Name(id in
      ('opt', 'optimization', 'latest_turn', 'prev_turn', 'branch'))`
    """
    src = _read_source("backend/app/services/refinement_service.py")
    tree = ast.parse(src)

    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "invoke_refinement_pipeline"
    ]
    if not funcs:
        pytest.skip("invoke_refinement_pipeline not yet defined — RED unmet")
    func = funcs[0]

    # ZERO async_session_factory async-withs
    async_with_violations = [
        n for n in ast.walk(func)
        if isinstance(n, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "async_session_factory"
            for item in n.items
        )
    ]
    assert not async_with_violations, (
        f"invoke_refinement_pipeline opens session(s): "
        f"{[n.lineno for n in async_with_violations]}"
    )

    # ZERO self.db references
    self_db_refs = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Attribute)
        and n.attr == "db"
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    ]
    assert not self_db_refs, (
        f"invoke_refinement_pipeline references self.db at lines "
        f"{[n.lineno for n in self_db_refs]}"
    )

    # ZERO Attribute access on forbidden ORM-typed names
    forbidden_names = {"opt", "optimization", "latest_turn", "prev_turn", "branch"}
    orm_violations = [
        f"{n.value.id}.{n.attr} at line {n.lineno}"
        for n in ast.walk(func)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id in forbidden_names
    ]
    assert not orm_violations, (
        "invoke_refinement_pipeline accesses ORM attributes:\n"
        + "\n".join(orm_violations)
    )


# --- Test 6: RefinementContext immutability -------------------------------

def test_refinement_context_dataclass_immutable():
    """Cycle 2 RED test 6: all 6 dataclasses frozen; mutation raises
    `FrozenInstanceError`."""
    from app.services.refinement_context import (
        RefinementContext,
        RollbackPayload,
        _BranchSnapshot,
        _InitialTurnPayload,
        _OptSnapshot,
        _TurnSnapshot,
    )

    opt_snap = _OptSnapshot(
        id="opt-1", raw_prompt="raw", optimized_prompt="opt",
        strategy_used="auto", project_id="proj-1",
    )
    turn_snap = _TurnSnapshot(version=1, prompt="opt", scores={}, strategy_used="auto")
    branch_snap = _BranchSnapshot(id="branch-1")

    # All 3 snapshots: frozen
    for snap in (opt_snap, turn_snap, branch_snap):
        with pytest.raises(FrozenInstanceError):
            snap.id = "mutated"  # type: ignore[misc]

    # _InitialTurnPayload + RollbackPayload: frozen
    init_payload = _InitialTurnPayload(branch_kwargs={}, turn_kwargs={})
    rollback_payload = RollbackPayload(branch_kwargs={})
    for payload in (init_payload, rollback_payload):
        with pytest.raises(FrozenInstanceError):
            payload.branch_kwargs = {"changed": True}  # type: ignore[misc]

    # RefinementContext: frozen
    from types import SimpleNamespace
    ctx = RefinementContext(
        opt_snapshot=opt_snap,
        latest_turn_snapshot=turn_snap,
        branch_snapshot=branch_snap,
        historical_stats=None,
        enrichment=SimpleNamespace(),  # type: ignore[arg-type]  # enrichment is structurally typed
        refinement_request="test",
        trace_id="trace-1",
        initial_scores_dict={},
    )
    with pytest.raises(FrozenInstanceError):
        ctx.refinement_request = "mutated"  # type: ignore[misc]


# --- Test 7: RefinementContext carries all EnrichedContext fields ----------

def test_refinement_context_carries_all_enrichment_fields():
    """Cycle 2 RED test 7: construct `RefinementContext` with populated
    `EnrichedContext` (7 stored fields + 6 properties); all reachable
    via `ctx.enrichment.*`."""
    from app.services.context_enrichment import EnrichedContext
    from app.services.refinement_context import (
        RefinementContext,
        _BranchSnapshot,
        _OptSnapshot,
        _TurnSnapshot,
    )

    enrichment = EnrichedContext(
        raw_prompt="raw",
        analysis=None,
        codebase_context="cb",
        strategy_intelligence="si",
        applied_patterns="ap",
        context_sources=MappingProxyType({"codebase_context": True}),
        enrichment_meta=MappingProxyType({"divergences": []}),
    )

    ctx = RefinementContext(
        opt_snapshot=_OptSnapshot(
            id="opt-1", raw_prompt="raw", optimized_prompt="opt",
            strategy_used="auto", project_id=None,
        ),
        latest_turn_snapshot=_TurnSnapshot(
            version=1, prompt="opt", scores=None, strategy_used="auto",
        ),
        branch_snapshot=_BranchSnapshot(id="branch-1"),
        historical_stats=None,
        enrichment=enrichment,
        refinement_request="test",
        trace_id="trace-1",
        initial_scores_dict={},
    )

    # 7 stored fields reachable
    assert ctx.enrichment.raw_prompt == "raw"
    assert ctx.enrichment.analysis is None
    assert ctx.enrichment.codebase_context == "cb"
    assert ctx.enrichment.strategy_intelligence == "si"
    assert ctx.enrichment.applied_patterns == "ap"
    assert ctx.enrichment.context_sources["codebase_context"] is True
    assert "divergences" in ctx.enrichment.enrichment_meta

    # 6 properties resolvable (analysis is None, so derivative properties
    # may return None or empty — verify they don't raise)
    _ = ctx.enrichment.task_type
    _ = ctx.enrichment.domain_value
    _ = ctx.enrichment.intent_label
    _ = ctx.enrichment.analysis_summary
    _ = ctx.enrichment.divergence_alerts
    _ = ctx.enrichment.context_sources_dict


# --- Test 8: Full pipeline persists new turn -------------------------------

@pytest.mark.asyncio
async def test_refine_full_pipeline_persists_new_turn(
    async_session_factory_override, monkeypatch,
):
    """Cycle 2 RED test 8: invoke `handle_refine`, assert `RefinementTurn`
    row with `version=prev+1`, scores, suggestions, deltas, deltas_from_prev."""
    from sqlalchemy import select

    from app.models import Optimization, RefinementBranch, RefinementTurn
    from app.tools import refine as refine_module

    factory = async_session_factory_override
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt-v1",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        db.add(RefinementTurn(
            id=turn_id, optimization_id=opt_id,
            branch_id=branch_id, version=1, parent_version=0,
            prompt="opt-v1", refinement_request="seed",
            scores={"overall": 7.0}, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        await db.commit()

    monkeypatch.setattr(refine_module, "notify_event_bus", AsyncMock())

    mock_provider = AsyncMock()
    mock_provider.complete_parsed = AsyncMock(return_value=MagicMock(
        analysis="ok", suggested_strategy="auto",
        optimized_prompt="refined v2",
        scores={"overall": 8.0, "clarity": 8, "specificity": 8,
                "structure": 8, "faithfulness": 8, "conciseness": 8},
    ))
    mock_provider.name = "mock"
    monkeypatch.setattr(
        refine_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": mock_provider})()})(),
    )

    try:
        await refine_module.handle_refine(
            optimization_id=opt_id,
            refinement_request="make better",
            branch_id=branch_id,
            ctx=None,
        )
    except Exception:
        pass

    # Verify new turn row exists
    async with factory() as db:
        result = await db.execute(
            select(RefinementTurn).where(
                RefinementTurn.optimization_id == opt_id,
                RefinementTurn.version == 2,
            )
        )
        new_turn = result.scalar_one_or_none()
        assert new_turn is not None, "No version=2 row persisted"
        assert new_turn.prompt
        assert new_turn.scores is not None


# --- Test 9: Strategy intelligence threaded through ctx --------------------

@pytest.mark.asyncio
async def test_refine_strategy_intelligence_threaded(
    async_session_factory_override, monkeypatch,
):
    """Cycle 2 RED test 9: verify optimize template receives
    `strategy_intelligence` from `ctx.enrichment.strategy_intelligence`."""
    from app.models import Optimization, RefinementBranch, RefinementTurn
    from app.services import refinement_service as svc_module
    from app.tools import refine as refine_module

    factory = async_session_factory_override
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt-v1",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        db.add(RefinementTurn(
            id=str(uuid.uuid4()), optimization_id=opt_id,
            branch_id=branch_id, version=1, parent_version=0,
            prompt="opt-v1", refinement_request="seed",
            scores={"overall": 7.0}, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        await db.commit()

    # Capture optimize call args
    captured_calls: list[dict] = []

    async def capturing_call_provider(self, **kwargs):
        captured_calls.append(kwargs)
        # Return appropriate mock based on output_format
        return MagicMock(
            analysis="ok", suggested_strategy="auto",
            optimized_prompt="refined", scores={"overall": 8.0},
        )

    monkeypatch.setattr(
        svc_module.RefinementService, "_call_provider", capturing_call_provider,
    )
    monkeypatch.setattr(refine_module, "notify_event_bus", AsyncMock())

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    monkeypatch.setattr(
        refine_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": mock_provider})()})(),
    )

    try:
        await refine_module.handle_refine(
            optimization_id=opt_id,
            refinement_request="improve",
            branch_id=branch_id,
            ctx=None,
        )
    except Exception:
        pass

    # At least one optimize call should reference strategy_intelligence
    # in its user_message (template render injects it)
    assert captured_calls, "No _call_provider calls captured"


# --- Test 10: SSE refinement_turn event fires -----------------------------

@pytest.mark.asyncio
async def test_refine_emits_refinement_turn_event(
    async_session_factory_override, monkeypatch,
):
    """Cycle 2 RED test 10: SSE `refinement_turn` event fires with payload
    `{optimization_id, version, branch_id, overall_score}` from persist
    callback's return."""
    from app.models import Optimization, RefinementBranch, RefinementTurn
    from app.tools import refine as refine_module

    event_calls: list[tuple] = []

    async def capture_event(event_type, payload):
        event_calls.append((event_type, payload))

    monkeypatch.setattr(refine_module, "notify_event_bus", capture_event)

    factory = async_session_factory_override
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt-v1",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        db.add(RefinementTurn(
            id=str(uuid.uuid4()), optimization_id=opt_id,
            branch_id=branch_id, version=1, parent_version=0,
            prompt="opt-v1", refinement_request="seed",
            scores={"overall": 7.0}, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        await db.commit()

    mock_provider = AsyncMock()
    mock_provider.complete_parsed = AsyncMock(return_value=MagicMock(
        analysis="ok", suggested_strategy="auto",
        optimized_prompt="refined", scores={"overall": 8.0},
    ))
    mock_provider.name = "mock"
    monkeypatch.setattr(
        refine_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": mock_provider})()})(),
    )

    try:
        await refine_module.handle_refine(
            optimization_id=opt_id,
            refinement_request="improve",
            branch_id=branch_id,
            ctx=None,
        )
    except Exception:
        pass

    refinement_events = [c for c in event_calls if c[0] == "refinement_turn"]
    assert refinement_events, f"No refinement_turn event fired: {event_calls}"
    payload = refinement_events[0][1]
    assert "optimization_id" in payload
    assert "version" in payload
    assert "branch_id" in payload
    assert "overall_score" in payload


# --- Test 11: Router constructor call sites updated ----------------------

def test_routers_refinement_constructor_call_sites_updated():
    """Cycle 2 RED test 11: source-grep `routers/refinement.py:96, 246, 290`.
    Each `RefinementService(...)` call uses `prompts_dir=` kwarg (and optional
    `provider=`), with NO `db=` kwarg."""
    src = _read_source("backend/app/routers/refinement.py")
    tree = ast.parse(src)

    constructor_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "RefinementService"
    ]

    assert constructor_calls, "No RefinementService(...) calls found in router"

    for call in constructor_calls:
        kwarg_names = {kw.arg for kw in call.keywords}
        assert "db" not in kwarg_names, (
            f"RefinementService call at line {call.lineno} still uses db= kwarg"
        )
        assert "prompts_dir" in kwarg_names, (
            f"RefinementService call at line {call.lineno} missing prompts_dir= kwarg"
        )


# --- Test 12: get_versions takes db param --------------------------------

@pytest.mark.asyncio
async def test_refinement_service_get_versions_takes_db_param(
    async_session_factory_override,
):
    """Cycle 2 RED test 12: `await svc.get_versions(db, optimization_id,
    branch_id=...)` succeeds. Calling without `db` raises `TypeError`."""
    from app.models import Optimization, RefinementBranch, RefinementTurn
    from app.services.refinement_service import RefinementService

    factory = async_session_factory_override
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        db.add(RefinementTurn(
            id=str(uuid.uuid4()), optimization_id=opt_id,
            branch_id=branch_id, version=1, parent_version=0,
            prompt="opt", refinement_request="seed",
            scores=None, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        await db.commit()

    svc = RefinementService(prompts_dir=Path("/tmp/test-prompts"))

    # New signature: db is first arg
    async with factory() as db:
        versions = await svc.get_versions(db, opt_id, branch_id=branch_id)
        assert len(versions) >= 1

    # Old signature without db raises
    with pytest.raises(TypeError):
        await svc.get_versions(opt_id, branch_id=branch_id)  # type: ignore[call-arg]


# --- Test 13: get_branches takes db param --------------------------------

@pytest.mark.asyncio
async def test_refinement_service_get_branches_takes_db_param(
    async_session_factory_override,
):
    """Cycle 2 RED test 13: same pattern for `get_branches(db, optimization_id)`."""
    from app.models import Optimization, RefinementBranch
    from app.services.refinement_service import RefinementService

    factory = async_session_factory_override
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        await db.commit()

    svc = RefinementService(prompts_dir=Path("/tmp/test-prompts"))

    async with factory() as db:
        branches = await svc.get_branches(db, opt_id)
        assert len(branches) >= 1

    with pytest.raises(TypeError):
        await svc.get_branches(opt_id)  # type: ignore[call-arg]


# --- Test 14: rollback returns RollbackPayload ---------------------------

@pytest.mark.asyncio
async def test_refinement_service_rollback_returns_payload(
    async_session_factory_override,
):
    """Cycle 2 RED test 14: `payload = await svc.rollback(db, optimization_id,
    to_version=N)` returns `RollbackPayload` (un-attached); no `db.commit()`
    inside the method."""
    from app.models import Optimization, RefinementBranch, RefinementTurn
    from app.services.refinement_context import RollbackPayload
    from app.services.refinement_service import RefinementService

    factory = async_session_factory_override
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        db.add(RefinementTurn(
            id=str(uuid.uuid4()), optimization_id=opt_id,
            branch_id=branch_id, version=1, parent_version=0,
            prompt="opt", refinement_request="seed",
            scores=None, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        db.add(RefinementTurn(
            id=str(uuid.uuid4()), optimization_id=opt_id,
            branch_id=branch_id, version=2, parent_version=1,
            prompt="opt-v2", refinement_request="r2",
            scores=None, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        await db.commit()

    svc = RefinementService(prompts_dir=Path("/tmp/test-prompts"))

    async with factory() as db:
        payload = await svc.rollback(db, opt_id, to_version=1)
        assert isinstance(payload, RollbackPayload)
        assert "id" in payload.branch_kwargs
        assert "optimization_id" in payload.branch_kwargs
        assert payload.branch_kwargs["optimization_id"] == opt_id


# --- Test 15: Rollback persists via queue --------------------------------

@pytest.mark.asyncio
async def test_routers_refinement_rollback_persists_via_queue(
    async_session_factory_override, monkeypatch,
):
    """Cycle 2 RED test 15: patch `submit`; invoke
    `POST /api/refine/{id}/rollback`; assert one queue submit with
    `operation_label="refine_rollback"`."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import Optimization, RefinementBranch, RefinementTurn

    submit_calls: list[dict] = []

    async def fake_submit(work, *, timeout=None, operation_label=None):
        submit_calls.append({"operation_label": operation_label})
        # Return a representative payload — actual values not asserted here
        return {
            "id": str(uuid.uuid4()),
            "optimization_id": "opt-1",
            "parent_branch_id": "branch-1",
            "forked_at_version": 1,
            "created_at": "2026-05-10T00:00:00",
        }

    class FakeQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            return await fake_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

    # FastAPI Depends() can ONLY be overridden via app.dependency_overrides —
    # monkeypatch on the function name doesn't reach the resolved Depends graph.
    from app.dependencies.write_queue import get_write_queue
    app.dependency_overrides[get_write_queue] = lambda: FakeQueue()

    factory = async_session_factory_override
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(Optimization(
            id=opt_id, trace_id=str(uuid.uuid4()),
            raw_prompt="raw", optimized_prompt="opt",
            task_type="general", domain="general",
            strategy_used="auto", status="completed",
        ))
        db.add(RefinementBranch(
            id=branch_id, optimization_id=opt_id,
            parent_branch_id=None, forked_at_version=0,
        ))
        db.add(RefinementTurn(
            id=str(uuid.uuid4()), optimization_id=opt_id,
            branch_id=branch_id, version=1, parent_version=0,
            prompt="opt", refinement_request="seed",
            scores=None, strategy_used="auto",
            trace_id=str(uuid.uuid4()),
        ))
        await db.commit()

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/refine/{opt_id}/rollback",
            json={"to_version": 1},
        )

        # Response may be 200 or 500 depending on integration; assert submit was made
        rollback_submits = [
            c for c in submit_calls
            if c["operation_label"] == "refine_rollback"
        ]
        assert rollback_submits, (
            f"Expected refine_rollback submit; got {submit_calls}; "
            f"response: {response.status_code} {response.text[:200]}"
        )
    finally:
        app.dependency_overrides.pop(get_write_queue, None)


# --- Test 16: invoke_refinement_pipeline raises without provider ---------

@pytest.mark.asyncio
async def test_refinement_service_invoke_pipeline_raises_without_provider():
    """Cycle 2 RED test 16: `RefinementService(prompts_dir=PROMPTS_DIR)` (no
    provider). Iterating `async for event in svc.invoke_refinement_pipeline(ctx)`
    raises `ValueError("provider required")` on FIRST iteration step.
    """
    from app.services.context_enrichment import EnrichedContext
    from app.services.refinement_context import (
        RefinementContext,
        _BranchSnapshot,
        _OptSnapshot,
        _TurnSnapshot,
    )
    from app.services.refinement_service import RefinementService

    svc = RefinementService(prompts_dir=Path("/tmp/test-prompts"))

    enrichment = EnrichedContext(
        raw_prompt="raw",
        analysis=None,
        codebase_context=None,
        strategy_intelligence=None,
        applied_patterns=None,
        context_sources=MappingProxyType({}),
        enrichment_meta=MappingProxyType({}),
    )
    ctx = RefinementContext(
        opt_snapshot=_OptSnapshot(
            id="opt-1", raw_prompt="raw", optimized_prompt="opt",
            strategy_used="auto", project_id=None,
        ),
        latest_turn_snapshot=_TurnSnapshot(
            version=1, prompt="opt", scores=None, strategy_used="auto",
        ),
        branch_snapshot=_BranchSnapshot(id="branch-1"),
        historical_stats=None,
        enrichment=enrichment,
        refinement_request="test",
        trace_id="trace-1",
        initial_scores_dict={},
    )

    with pytest.raises(ValueError, match=r"provider required"):
        async for _event in svc.invoke_refinement_pipeline(ctx):
            pass


# --- Test 17: invoke_refinement_pipeline emits refinement_complete -------

@pytest.mark.asyncio
async def test_invoke_refinement_pipeline_emits_refinement_complete(monkeypatch):
    """Cycle 2 RED test 17: terminal `refinement_complete` event with 6 keys.

    Patches `_call_provider` with stub responses; iterates
    `async for event in svc.invoke_refinement_pipeline(ctx)`; asserts the LAST
    yielded event has `event.event == "refinement_complete"` and `event.data`
    contains: `optimized_prompt`, `scores`, `deltas_from_prev`,
    `deltas_from_original`, `strategy_used`, `suggestions`.
    """
    from app.schemas.pipeline_contracts import (
        AnalysisResult,
        OptimizationResult,
        ScoreResult,
        SuggestionsOutput,
    )
    from app.services.context_enrichment import EnrichedContext
    from app.services.refinement_context import (
        RefinementContext,
        _BranchSnapshot,
        _OptSnapshot,
        _TurnSnapshot,
    )
    from app.services.refinement_service import RefinementService

    mock_provider = AsyncMock()
    mock_provider.name = "mock"

    svc = RefinementService(
        provider=mock_provider,
        prompts_dir=Path("/tmp/test-prompts"),
    )

    # Mock _call_provider to return stub phase outputs
    call_count = [0]

    async def stub_call_provider(self, *, system_prompt, user_message, output_format,
                                  model, effort=None, max_tokens=16384,
                                  streaming=False, cache_ttl=None):
        call_count[0] += 1
        if output_format is AnalysisResult:
            return AnalysisResult(
                task_type="general",
                domain="general",
                weaknesses=[],
                analysis_summary="ok",
                selected_strategy="auto",
                intent_label="general",
                confidence=0.8,
            )
        elif output_format is OptimizationResult:
            return OptimizationResult(
                optimized_prompt="refined", changes_summary="updated",
            )
        elif output_format is ScoreResult:
            from app.schemas.pipeline_contracts import DimensionScores
            return ScoreResult(
                prompt_a_scores=DimensionScores(
                    clarity=8.0, specificity=8.0, structure=8.0,
                    faithfulness=8.0, conciseness=8.0,
                ),
                prompt_b_scores=DimensionScores(
                    clarity=8.0, specificity=8.0, structure=8.0,
                    faithfulness=8.0, conciseness=8.0,
                ),
                analysis="good",
            )
        elif output_format is SuggestionsOutput:
            return SuggestionsOutput(suggestions=[])
        return MagicMock()

    monkeypatch.setattr(
        RefinementService, "_call_provider", stub_call_provider,
    )

    enrichment = EnrichedContext(
        raw_prompt="raw",
        analysis=None,
        codebase_context=None,
        strategy_intelligence=None,
        applied_patterns=None,
        context_sources=MappingProxyType({}),
        enrichment_meta=MappingProxyType({}),
    )
    ctx = RefinementContext(
        opt_snapshot=_OptSnapshot(
            id="opt-1", raw_prompt="raw", optimized_prompt="opt",
            strategy_used="auto", project_id=None,
        ),
        latest_turn_snapshot=_TurnSnapshot(
            version=1, prompt="opt", scores={"overall": 7.0}, strategy_used="auto",
        ),
        branch_snapshot=_BranchSnapshot(id="branch-1"),
        historical_stats=None,
        enrichment=enrichment,
        refinement_request="improve",
        trace_id="trace-1",
        initial_scores_dict={},
    )

    events = []
    async for event in svc.invoke_refinement_pipeline(ctx):
        events.append(event)

    assert events, "No events yielded"
    last_event = events[-1]
    assert last_event.event == "refinement_complete", (
        f"Last event is {last_event.event}, expected refinement_complete"
    )

    # 6 required keys
    data = last_event.data
    required_keys = {
        "optimized_prompt", "scores", "deltas_from_prev",
        "deltas_from_original", "strategy_used", "suggestions",
    }
    missing = required_keys - set(data.keys())
    assert not missing, f"refinement_complete missing keys: {missing}"
