# backend/tests/integration/test_mcp_api.py
"""Integration tests for the MCP streamable HTTP ASGI app.

The MCP server is tested directly as an ASGI app (not via the FastAPI mount)
because the FastAPI mount strips the /mcp prefix, causing path mismatches.

Architecture notes:
- Each test uses a fresh MCP server instance (function scope) to avoid session
  state leakage between tests in the stateful HTTP transport.
- The FastMCP session_manager is run in a background asyncio task to avoid
  anyio task-group cross-coroutine issues in pytest-asyncio auto mode.
- base_url uses the server's configured host:port (127.0.0.1:8001) to satisfy
  FastMCP's Host header security validation.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ── Module-level session patch ────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def patch_sessions(engine):
    """Patch async_session in optimize router and database module for all tests."""
    import app.database as db_module
    import app.routers.optimize as opt_module

    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch.object(db_module, "async_session", TestSession),
        patch.object(opt_module, "async_session", TestSession),
    ):
        yield


# ── Per-test MCP client (function scope) ─────────────────────────────────

@pytest.fixture
async def mcp_client(engine):
    """Fresh MCP server + AsyncClient for each test.

    Uses function scope so each test gets an isolated session manager without
    cross-contamination from previous test sessions.
    """
    import app.mcp_server as mcp_module
    from app.mcp_server import create_mcp_server
    from tests.integration.conftest import MockProvider

    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_provider = MockProvider()
    mcp = create_mcp_server(provider=mock_provider)
    mcp_app = mcp.streamable_http_app()

    stop_event = asyncio.Event()
    session_started = asyncio.Event()
    session_error: list[BaseException] = []

    async def _run():
        try:
            async with mcp.session_manager.run():
                session_started.set()
                await stop_event.wait()
        except Exception as exc:
            session_error.append(exc)
            session_started.set()

    bg_task = asyncio.create_task(_run())
    await asyncio.wait_for(session_started.wait(), timeout=10.0)
    if session_error:
        bg_task.cancel()
        raise RuntimeError(f"session_manager failed to start: {session_error[0]}")

    with patch.object(mcp_module, "async_session", TestSession):
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            # Must match MCP_HOST:MCP_PORT for Host header validation
            base_url="http://127.0.0.1:8001",
            timeout=15.0,
        ) as c:
            yield c

    stop_event.set()
    try:
        await asyncio.wait_for(bg_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        bg_task.cancel()


# ── MCP protocol helpers ──────────────────────────────────────────────────

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "integration-test", "version": "1"},
    },
    "id": 1,
}


async def _init_session(mcp_client: AsyncClient) -> str:
    """Initialize an MCP session and return the session ID."""
    resp = await mcp_client.post("/mcp", headers=MCP_HEADERS, json=INIT_PAYLOAD)
    assert resp.status_code == 200, f"MCP init failed: {resp.status_code} {resp.text}"
    session_id = resp.headers.get("mcp-session-id")
    assert session_id, "No Mcp-Session-Id header returned"
    return session_id


async def _call_tool(
    mcp_client: AsyncClient, session_id: str, method: str, params: dict
) -> dict:
    """Send an MCP JSON-RPC request and parse the response."""
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 2}
    resp = await mcp_client.post(
        "/mcp",
        headers={**MCP_HEADERS, "Mcp-Session-Id": session_id},
        json=payload,
    )
    assert resp.status_code == 200
    text = resp.text
    if text.startswith("data:"):
        data_line = next(line for line in text.splitlines() if line.startswith("data:"))
        return json.loads(data_line[5:].strip())
    return resp.json()


# ── Tests ─────────────────────────────────────────────────────────────────

async def test_mcp_initialize_returns_session_id(mcp_client: AsyncClient):
    session_id = await _init_session(mcp_client)
    assert len(session_id) > 0


async def test_mcp_tools_list_returns_18_tools(mcp_client: AsyncClient):
    session_id = await _init_session(mcp_client)
    result = await _call_tool(mcp_client, session_id, "tools/list", {})
    tools = result.get("result", {}).get("tools", [])
    assert len(tools) == 18, f"Expected 18 tools, got {len(tools)}: {[t['name'] for t in tools]}"
    tool_names = [t["name"] for t in tools]
    for expected in (
        "optimize", "get_optimization", "list_optimizations", "delete_optimization",
        "batch_delete_optimizations", "list_trash", "restore_optimization",
        "submit_feedback", "get_branches", "get_adaptation_state",
    ):
        assert expected in tool_names, f"Tool '{expected}' not found in: {tool_names}"


async def test_mcp_tools_have_required_annotations(mcp_client: AsyncClient):
    session_id = await _init_session(mcp_client)
    result = await _call_tool(mcp_client, session_id, "tools/list", {})
    tools = result.get("result", {}).get("tools", [])
    required = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    for tool in tools:
        annotations = set((tool.get("annotations") or {}).keys())
        missing = required - annotations
        assert not missing, f"Tool '{tool['name']}' missing annotations: {missing}"


async def test_mcp_list_optimizations_returns_envelope(mcp_client: AsyncClient):
    session_id = await _init_session(mcp_client)
    result = await _call_tool(mcp_client, session_id, "tools/call", {
        "name": "list_optimizations", "arguments": {"limit": 5}
    })
    content = result.get("result", {}).get("content", [{}])
    text = content[0].get("text", "{}") if content else "{}"
    body = json.loads(text)
    assert "total" in body
    assert "items" in body


async def test_mcp_delete_and_restore_round_trip(
    mcp_client: AsyncClient,
    client: AsyncClient,
    auth_headers: dict,
):
    """Create an optimization via REST, delete via MCP, list_trash, then restore."""
    from tests.integration.test_optimize_api import _stream_optimize
    opt_id, _ = await _stream_optimize(client, auth_headers, "MCP round-trip test prompt.")
    assert opt_id, "Failed to create optimization for MCP round-trip test"

    session_id = await _init_session(mcp_client)

    # Delete via MCP
    del_result = await _call_tool(mcp_client, session_id, "tools/call", {
        "name": "delete_optimization", "arguments": {"optimization_id": opt_id}
    })
    del_text = del_result.get("result", {}).get("content", [{}])[0].get("text", "{}")
    assert json.loads(del_text).get("deleted") is True, f"Delete failed: {del_text}"

    # Verify in trash via MCP
    trash_result = await _call_tool(mcp_client, session_id, "tools/call", {
        "name": "list_trash", "arguments": {}
    })
    trash_text = trash_result.get("result", {}).get("content", [{}])[0].get("text", "{}")
    trash_ids = [item["id"] for item in json.loads(trash_text).get("items", [])]
    assert opt_id in trash_ids, f"{opt_id} not found in trash: {trash_ids}"

    # Restore via MCP
    restore_result = await _call_tool(mcp_client, session_id, "tools/call", {
        "name": "restore_optimization", "arguments": {"optimization_id": opt_id}
    })
    restore_text = restore_result.get("result", {}).get("content", [{}])[0].get("text", "{}")
    assert json.loads(restore_text).get("restored") is True, f"Restore failed: {restore_text}"
