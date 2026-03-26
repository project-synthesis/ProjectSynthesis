"""Async MCP client for proxying tool calls to the local MCP server.

Used by the REST endpoint to forward sampling-tier requests through the MCP
server process, which owns the sampling session with the connected IDE bridge.

The MCP server uses Streamable HTTP transport.  Each proxy call opens a
short-lived session: initialize → tools/call → parse result → close.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import MCP_PORT

logger = logging.getLogger(__name__)

_MCP_URL = f"http://127.0.0.1:{MCP_PORT}/mcp"
_PROXY_TIMEOUT = 300.0  # 5 min total — sampling runs 4 phases × up to 120s each


def _parse_sse_data(raw: str) -> dict | None:
    """Extract the first ``data:`` line from an SSE response body."""
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                continue
    return None


async def call_mcp_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout: float = _PROXY_TIMEOUT,
) -> dict[str, Any]:
    """Call an MCP tool on the local MCP server via Streamable HTTP.

    Opens a short-lived MCP session, invokes the named tool, and returns
    the structured result dict.  Raises ``RuntimeError`` on failure.

    Args:
        tool_name: MCP tool name (e.g. ``synthesis_optimize``).
        arguments: Tool arguments dict.
        timeout: Total timeout in seconds for the entire proxy call.

    Returns:
        The tool result as a dict (parsed from the ``content[0].text`` JSON).
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        # Step 1: Initialize MCP session
        init_resp = await client.post(
            _MCP_URL,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "synthesis-rest-proxy", "version": "1.0.0"},
                },
            },
        )
        init_resp.raise_for_status()

        session_id = init_resp.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError("MCP server did not return a session ID")

        init_data = _parse_sse_data(init_resp.text)
        if not init_data or "result" not in init_data:
            raise RuntimeError(f"MCP initialize failed: {init_resp.text[:200]}")

        logger.info(
            "mcp_proxy: session=%s server=%s",
            session_id[:12],
            init_data["result"].get("serverInfo", {}).get("name", "?"),
        )

        # Step 2: Send initialized notification (required by MCP protocol)
        session_headers = {**headers, "Mcp-Session-Id": session_id}
        notify_resp = await client.post(
            _MCP_URL,
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        # Notification may return 202 Accepted or 200
        if notify_resp.status_code not in (200, 202):
            logger.warning("mcp_proxy: initialized notification got %d", notify_resp.status_code)

        # Step 3: Call the tool
        logger.info("mcp_proxy: calling %s with %d args", tool_name, len(arguments))
        tool_resp = await client.post(
            _MCP_URL,
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            },
        )
        tool_resp.raise_for_status()

        tool_data = _parse_sse_data(tool_resp.text)
        if not tool_data:
            raise RuntimeError(f"MCP tool call returned no parseable data: {tool_resp.text[:200]}")

        if "error" in tool_data:
            error = tool_data["error"]
            raise RuntimeError(
                f"MCP tool error: {error.get('message', str(error))}"
            )

        result = tool_data.get("result", {})

        # Extract text content from MCP tool result format
        content = result.get("content", [])
        if content and isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    try:
                        return json.loads(block["text"])
                    except (json.JSONDecodeError, KeyError):
                        return {"raw_text": block.get("text", "")}

        return result
