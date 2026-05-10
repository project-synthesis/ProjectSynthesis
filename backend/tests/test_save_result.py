"""Cycle 1 RED tests for tools/save_result.py + scoring_service signature changes.

Foundation P4 — Long-Handler Restructure Cycle 1.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

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


# --- Test 1: AST regression — no LLM/scoring await in long-lived session ---

def test_save_result_handler_does_not_open_long_lived_session():
    """AST-based assertion (Cycle 1 RED test 1).

    Any `async with async_session_factory()` block inside `handle_save_result`
    must NOT contain an `await` of `score_passthrough`, `*.analyze*`, or
    `*.analyze_no_session`. The new P4 invariant: zero LLM-in-session at
    this handler.
    """
    src = _read_source("backend/app/tools/save_result.py")
    tree = ast.parse(src)

    # Find handle_save_result function
    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_save_result"
    ]
    assert funcs, "handle_save_result not found in tools/save_result.py"
    func = funcs[0]

    # Find AsyncWith blocks whose context expression is async_session_factory()
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

    forbidden_names = {"score_passthrough"}
    forbidden_attrs = {"analyze", "analyze_no_session"}

    violations: list[str] = []
    for block in async_with_blocks:
        for awaited in ast.walk(block):
            if isinstance(awaited, ast.Await) and isinstance(awaited.value, ast.Call):
                func_node = awaited.value.func
                if isinstance(func_node, ast.Name) and func_node.id in forbidden_names:
                    violations.append(
                        f"forbidden await of name '{func_node.id}' "
                        f"inside async_session_factory block at line {awaited.lineno}"
                    )
                if isinstance(func_node, ast.Attribute) and func_node.attr in forbidden_attrs:
                    violations.append(
                        f"forbidden await of attribute '*.{func_node.attr}' "
                        f"inside async_session_factory block at line {awaited.lineno}"
                    )

    assert not violations, "LLM-in-session violations found:\n" + "\n".join(violations)


# --- Test 2: persistence routes through WriteQueue.submit() ---

@pytest.mark.asyncio
async def test_save_result_persistence_routes_through_write_queue(
    async_session_factory_override, monkeypatch,
):
    """Cycle 1 RED test 2: handle_save_result invokes WriteQueue.submit() exactly
    once with operation_label='save_result_persist'.

    Takes `async_session_factory_override` so the read step inside
    handle_save_result uses the in-memory test DB (NOT production)."""
    from app.tools import save_result as save_result_module

    submit_calls: list[dict] = []

    async def fake_submit(work, *, timeout=None, operation_label=None):
        # Run the work_fn with a mock writer session to capture the return
        submit_calls.append({"work": work, "operation_label": operation_label})
        from unittest.mock import AsyncMock as _AsyncMock
        mock_db = _AsyncMock()
        mock_db.execute = _AsyncMock()
        # Return an empty payload — the test only asserts the submit call
        return {"id": "test-opt-id", "trace_id": "test-trace"}

    class FakeQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            return await fake_submit(work, timeout=timeout, operation_label=operation_label)

    monkeypatch.setattr(save_result_module, "get_write_queue", lambda: FakeQueue())
    monkeypatch.setattr(
        "app.tools._shared.get_write_queue",
        lambda: FakeQueue(),
    )

    # Patch event bus + provider + domain resolver
    monkeypatch.setattr(save_result_module, "notify_event_bus", AsyncMock())

    # The handler also calls auto_resolve_repo etc. — mock minimally
    monkeypatch.setattr(
        "app.tools.save_result.resolve_repo_project",
        AsyncMock(return_value=(None, None)),
    )

    # Mock the routing + provider lookup so analyze_no_session has no LLM provider
    monkeypatch.setattr(
        save_result_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": None})()})(),
    )

    # Invoke the handler
    await save_result_module.handle_save_result(
        trace_id="test-trace",
        optimized_prompt="some optimized prompt text",
        changes_summary=None,
        task_type="general",
        strategy_used=None,
        scores=None,
        model="external",
        codebase_context=None,
        ctx=None,
    )

    assert len(submit_calls) == 1, (
        f"Expected exactly 1 submit call, got {len(submit_calls)}"
    )
    assert submit_calls[0]["operation_label"] == "save_result_persist"


# --- Test 3: score_passthrough drops db param, gains historical_stats ---

@pytest.mark.asyncio
async def test_score_passthrough_drops_db_param():
    """Cycle 1 RED test 3: score_passthrough succeeds without db= when
    historical_stats= is provided; calling with db= raises TypeError."""
    from app.services.scoring_service import score_passthrough

    # New signature: no db, requires historical_stats
    result = await score_passthrough(
        raw_prompt="test raw",
        optimized_prompt="optimized test text",
        external_scores=None,
        historical_stats=None,
        scoring_enabled=True,
    )
    assert result is not None
    assert hasattr(result, "optimized_scores")

    # Old signature with db= must raise TypeError
    with pytest.raises(TypeError, match="unexpected keyword"):
        await score_passthrough(
            raw_prompt="test raw",
            optimized_prompt="optimized test text",
            external_scores=None,
            db=None,  # type: ignore[call-arg]
            scoring_enabled=True,
        )


# --- Test 4: routers/optimize.py:716 call site uses new signature ---

def test_score_passthrough_routers_optimize_call_site_updated():
    """Cycle 1 RED test 4: routers/optimize.py contains a score_passthrough(...)
    call with historical_stats= kwarg AND no db= kwarg."""
    src = _read_source("backend/app/routers/optimize.py")
    tree = ast.parse(src)

    found_call = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "score_passthrough"
        ):
            kwarg_names = {kw.arg for kw in node.keywords}
            assert "historical_stats" in kwarg_names, (
                f"score_passthrough call at line {node.lineno} missing historical_stats kwarg"
            )
            assert "db" not in kwarg_names, (
                f"score_passthrough call at line {node.lineno} still passes db= "
                f"(should be dropped post-Cycle-1)"
            )
            found_call = True

    assert found_call, "No score_passthrough call found in routers/optimize.py"


# --- Test 5: analyze_no_session emits telemetry via queue with custom label ---

@pytest.mark.asyncio
async def test_heuristic_analyzer_analyze_no_session_emits_telemetry_via_queue(monkeypatch):
    """Cycle 1 RED test 5: analyze_no_session forces A4 fallback (gates patched HIGH)
    and emits telemetry with operation_label='task_type_telemetry_no_session'."""
    from app.services import heuristic_analyzer

    # Force A4 fallback. Patch the rebound local names in heuristic_analyzer
    # (NOT task_type_classifier.LLM_*_GATE — imports are at module load via
    # aliased imports, so patching the source doesn't update the local name).
    # Direction: HIGH (1.0) so `task_confidence < gate` evaluates True. Margin
    # gate also high so the margin check passes.
    monkeypatch.setattr(
        heuristic_analyzer, "_LLM_CLASSIFICATION_CONFIDENCE_GATE", 1.0,
    )
    monkeypatch.setattr(
        heuristic_analyzer, "_LLM_CLASSIFICATION_MARGIN_GATE", 1.0,
    )

    # Deterministic classifier mock: forces confidence=0.1 and margin=0.05
    # (under both 1.0 gates), bypassing dependency on signal-vocabulary state.
    # Returns task_type="general" so Layer 1b disambiguation does fire its
    # technical-noun check, but with our `fixture_prompt` having NO technical
    # verb+noun pair, `check_technical_disambiguation` returns False and we
    # fall through to Layer 1c A4. F10 round-2 fix: was vocabulary-dependent.
    def _mock_classify(prompt_lower, first_sentence, signals):
        return ("general", 0.1, {"general": 0.1, "coding": 0.05})

    monkeypatch.setattr(
        heuristic_analyzer, "classify_task_type", _mock_classify,
    )

    # Fixture prompt: deliberately ambiguous, short, no technical verb+noun pair
    # (so Layer 1b disambiguation doesn't fire and bypass A4 entirely).
    fixture_prompt = "task xyz"

    # Mock Haiku provider. `LLMProvider.complete_parsed` returns T (parsed
    # output) directly — NOT a (result, usage) tuple. Per provider contract
    # at backend/app/providers/base.py:146-157. Use a real Pydantic instance
    # so future `isinstance(result, BaseModel)` validation in
    # call_provider_with_retry stays compatible. F5 round-2 fix.
    from pydantic import BaseModel

    class _MockClassificationResult(BaseModel):
        task_type: str
        domain: str

    mock_provider = AsyncMock()
    mock_provider.complete_parsed = AsyncMock(
        return_value=_MockClassificationResult(
            task_type="general", domain="general",
        ),
    )
    mock_provider.name = "mock"

    # Patch the queue submit (AsyncMock — the submit is awaited inside the
    # spawned task at task_type_classifier.py:882)
    submit_calls: list[dict] = []

    async def fake_submit(work, *, timeout=None, operation_label=None):
        submit_calls.append({"operation_label": operation_label})
        # Run the work_fn briefly so internal db.add/commit get exercised
        # against a mock session
        mock_db = AsyncMock()
        mock_db.add = lambda x: None
        mock_db.commit = AsyncMock()
        try:
            await work(mock_db)
        except Exception:
            pass

    class FakeQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            return await fake_submit(work, timeout=timeout, operation_label=operation_label)

    monkeypatch.setattr(
        "app.dependencies.write_queue.get_process_write_queue",
        lambda: FakeQueue(),
    )

    analyzer = heuristic_analyzer.HeuristicAnalyzer()
    result = await analyzer.analyze_no_session(
        fixture_prompt, provider=mock_provider,
    )

    # Drain ALL pending tasks — `classify_with_llm` schedules telemetry via
    # `asyncio.create_task(_submit_and_log())`, which is fire-and-forget.
    # Two `await asyncio.sleep(0)` calls were insufficient on slow CI:
    # the task may not start running until the next loop iteration. Round-3
    # F3 fix — explicit drain is the correct barrier.
    pending = [
        t for t in asyncio.all_tasks() if t is not asyncio.current_task()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Assertions
    assert result is not None
    assert result.task_type == "general"
    assert result.recommended_strategy == "auto"  # contract: no strategy resolution
    assert len(submit_calls) == 1, (
        f"Expected exactly 1 queue submit (A4 telemetry), got {len(submit_calls)}: "
        f"{submit_calls}"
    )
    assert submit_calls[0]["operation_label"] == "task_type_telemetry_no_session"


# --- Test 6: integration — handler creates new Optimization when no pending ---

@pytest.mark.asyncio
async def test_save_result_creates_new_optimization_when_no_pending(
    async_session_factory_override, monkeypatch,
):
    """Cycle 1 RED test 6: full integration with real session factory.
    Invoke handle_save_result for an unknown trace_id; assert a new
    Optimization row is persisted with status='completed'."""
    import uuid

    from sqlalchemy import select

    from app.models import Optimization
    from app.tools import save_result as save_result_module

    # The fixture `async_session_factory_override` (defined in conftest.py)
    # provides a temp-file SQLite session factory wired to both the read
    # engine and the writer engine via the WriteQueue. Installed by Task 1.5
    # so this test reaches assertion logic at RED time (not collection-error).

    monkeypatch.setattr(
        save_result_module, "notify_event_bus", AsyncMock(),
    )
    monkeypatch.setattr(
        save_result_module, "resolve_repo_project",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        save_result_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": None})()})(),
    )

    trace_id = str(uuid.uuid4())

    result = await save_result_module.handle_save_result(
        trace_id=trace_id,
        optimized_prompt="test optimized prompt that is long enough",
        changes_summary="test changes",
        task_type="general",
        strategy_used="auto",
        scores=None,
        model="external",
        codebase_context=None,
        ctx=None,
    )

    assert result is not None
    assert result.optimization_id

    # SELECT the row directly to verify persistence
    async with async_session_factory_override() as db:
        rows = await db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        opt = rows.scalar_one_or_none()
        assert opt is not None
        assert opt.status == "completed"
        assert opt.task_type == "general"
        assert opt.strategy_used == "auto"


# --- Test 7: integration — handler mutates pending Optimization in place ---

@pytest.mark.asyncio
async def test_save_result_mutates_pending_optimization_when_present(
    async_session_factory_override, monkeypatch,
):
    """Cycle 1 RED test 7: pre-insert a pending Optimization with status='pending';
    invoke handle_save_result for that trace_id; assert the row is updated in
    place (same id, status flips to 'completed')."""
    import uuid

    from sqlalchemy import select

    from app.models import Optimization
    from app.tools import save_result as save_result_module

    monkeypatch.setattr(
        save_result_module, "notify_event_bus", AsyncMock(),
    )
    monkeypatch.setattr(
        save_result_module, "resolve_repo_project",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        save_result_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": None})()})(),
    )

    # Pre-insert a pending row
    trace_id = str(uuid.uuid4())
    pending_id = str(uuid.uuid4())
    async with async_session_factory_override() as db:
        pending = Optimization(
            id=pending_id,
            raw_prompt="original raw prompt",
            status="pending",
            trace_id=trace_id,
            strategy_used="cot",
            provider="mcp_passthrough",
            routing_tier="passthrough",
            task_type="general",
        )
        db.add(pending)
        await db.commit()

    # Invoke the handler
    result = await save_result_module.handle_save_result(
        trace_id=trace_id,
        optimized_prompt="newly optimized prompt text",
        changes_summary="restructured",
        task_type="general",
        strategy_used="cot",
        scores=None,
        model="external",
        codebase_context=None,
        ctx=None,
    )

    assert result.optimization_id == pending_id  # same id (mutate in place)

    async with async_session_factory_override() as db:
        rows = await db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        opts = rows.scalars().all()
        assert len(opts) == 1, f"Expected 1 row, got {len(opts)} (no duplicates)"
        opt = opts[0]
        assert opt.id == pending_id
        assert opt.status == "completed"
        assert opt.optimized_prompt == "newly optimized prompt text"
        assert opt.changes_summary == "restructured"


# --- Test 8: optimization_created event fires with full payload ---

@pytest.mark.asyncio
async def test_save_result_emits_optimization_created_event_with_full_payload(
    async_session_factory_override, monkeypatch,
):
    """Cycle 1 RED test 8: handle_save_result fires `optimization_created` SSE
    event with all expected payload keys, sourced from the persist callback's
    return dict (NOT from a post-callback ORM access)."""
    import uuid

    from app.tools import save_result as save_result_module

    event_calls: list[dict] = []

    async def fake_notify(event_type, payload):
        event_calls.append({"event_type": event_type, "payload": payload})

    monkeypatch.setattr(
        save_result_module, "notify_event_bus", fake_notify,
    )
    monkeypatch.setattr(
        save_result_module, "resolve_repo_project",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        save_result_module, "get_routing",
        lambda: type("R", (), {"state": type("S", (), {"provider": None})()})(),
    )

    trace_id = str(uuid.uuid4())

    await save_result_module.handle_save_result(
        trace_id=trace_id,
        optimized_prompt="test optimized prompt long enough text",
        changes_summary=None,
        task_type="general",
        strategy_used="auto",
        scores=None,
        model="external",
        codebase_context=None,
        ctx=None,
    )

    # Find the optimization_created event
    created_events = [e for e in event_calls if e["event_type"] == "optimization_created"]
    assert len(created_events) == 1, (
        f"Expected exactly 1 optimization_created event, got {len(created_events)}"
    )

    payload = created_events[0]["payload"]
    required_keys = {
        "id", "trace_id", "task_type", "intent_label", "domain", "domain_raw",
        "strategy_used", "overall_score", "provider", "status",
    }
    missing = required_keys - set(payload.keys())
    assert not missing, f"optimization_created event missing keys: {missing}"
    assert payload["trace_id"] == trace_id
    assert payload["status"] == "completed"


# --- Test 9: AST regression — no post-session ORM access ---

def test_save_result_no_post_session_orm_access():
    """Cycle 1 RED test 9: AST-based detached-ORM contract.

    Post-`async with async_session_factory()` block, the handler must NOT
    access `ast.Attribute` on `Name(id in ('opt', 'pending', 'persisted_opt'))`.
    Exempt: persist callback functions (their `_persist_save_result` body
    has its own writer session).

    Rationale for exempting names inside `_persist_*` callbacks: the persist
    callback re-queries the row in a fresh writer-engine session, so its
    local `opt`/`persisted_opt` names are bound to a NEW ORM instance —
    not the read-session's detached `pending`. Reading attributes from
    that fresh-session instance is safe; the test must not flag it.
    Parent-map walk via `id(node)` is sound here: a single AST tree
    enumeration does not reuse object ids across sibling nodes."""
    src = _read_source("backend/app/tools/save_result.py")
    tree = ast.parse(src)

    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_save_result"
    ]
    assert funcs, "handle_save_result not found"
    func = funcs[0]

    # Find the async-with block's exit line
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
        pytest.skip("No async_session_factory block — restructure complete; nothing to check")

    block_exit_line = max(
        getattr(n, "end_lineno", 0) for block in async_with_blocks
        for n in ast.walk(block)
    )

    forbidden_names = {"opt", "pending", "persisted_opt"}
    violations: list[str] = []

    # Walk the function body looking for forbidden Attribute accesses past the block
    # exit line, EXCLUDING any inside a nested FunctionDef whose name starts with `_persist`
    def _is_inside_persist_callback(node: ast.AST, parent_map: dict) -> bool:
        cur = parent_map.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and cur.name.startswith("_persist"):
                return True
            cur = parent_map.get(id(cur))
        return False

    # Build parent map
    parent_map: dict[int, ast.AST] = {}
    for parent in ast.walk(func):
        for child in ast.iter_child_nodes(parent):
            parent_map[id(child)] = parent

    for node in ast.walk(func):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in forbidden_names
            and node.lineno > block_exit_line
            and not _is_inside_persist_callback(node, parent_map)
        ):
            violations.append(
                f"detached-ORM access '{node.value.id}.{node.attr}' "
                f"at line {node.lineno} (post-block exit line {block_exit_line})"
            )

    assert not violations, (
        "Detached-ORM access violations:\n" + "\n".join(violations)
    )
