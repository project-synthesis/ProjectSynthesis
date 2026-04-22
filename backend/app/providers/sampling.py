"""MCP Sampling Provider implementation.

Wraps the IDE's MCP sampling client to expose it as an `LLMProvider`.
This allows the primary pipeline orchestrator to run sampling jobs without
a separate pipeline implementation.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from mcp.server.fastmcp import Context
from pydantic import BaseModel

from app.providers.base import LLMProvider, TokenUsage
from app.services.sampling.primitives import sampling_request_structured

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class MCPSamplingProvider(LLMProvider):
    """Provider that uses the client's MCP sampling capability."""

    name: str = "mcp_sampling"

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.last_usage = TokenUsage()
        self.last_model = "unknown"

    async def complete_parsed(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
        cache_ttl: str | None = None,
        task_budget: int | None = None,
        compaction: bool = False,
    ) -> T:
        """Make an LLM call via the MCP sampling client and return a parsed Pydantic model.

        Note: MCP does not currently support usage reporting or effort/caching
        hints, so those are ignored and `last_usage` remains zero.
        """
        logger.debug(
            "MCPSamplingProvider calling sampling_request_structured for %s",
            output_format.__name__,
        )
        parsed, model_id = await sampling_request_structured(
            ctx=self.ctx,
            system=system_prompt,
            user=user_message,
            output_model=output_format,
            max_tokens=max_tokens,
        )
        self.last_model = model_id
        # Token usage is not returned by MCP currently
        self.last_usage = TokenUsage(0, 0, 0, 0)
        return parsed

    async def complete_parsed_streaming(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
        cache_ttl: str | None = None,
        task_budget: int | None = None,
        compaction: bool = False,
    ) -> T:
        """Streaming is not supported by MCP sampling natively. Fall back to complete_parsed."""
        return await self.complete_parsed(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            output_format=output_format,
            max_tokens=max_tokens,
            effort=effort,
            cache_ttl=cache_ttl,
            task_budget=task_budget,
            compaction=compaction,
        )
