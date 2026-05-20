"""synthesis_refresh_seed_agent MCP tool handler.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.29-t3.2-t3.5-design.md`` §3.3.
"""
from __future__ import annotations

from app.schemas.seed_agent_promotion import RefreshResult
from app.services.seed_agent_promoter import SeedAgentPromoter
from app.tools._shared import get_write_queue


async def handle_refresh_seed_agent(*, agent_name: str) -> RefreshResult:
    """Re-render the Examples section of `prompts/seed-agents/<agent_name>.md`."""
    write_queue = get_write_queue()
    promoter = SeedAgentPromoter(write_queue=write_queue)
    return await promoter.refresh(agent_name)
