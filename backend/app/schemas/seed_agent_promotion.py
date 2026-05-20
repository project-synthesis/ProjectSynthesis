"""T3.2 + T3.5 seed-agent promotion result schemas.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.29-t3.2-t3.5-design.md`` §3.5.
"""
from __future__ import annotations

from pydantic import BaseModel


class PromotionResult(BaseModel):
    """Result of ``SeedAgentPromoter.maybe_promote``.

    Either ``written=True`` (file landed, source row stamped) or
    ``written=False`` with a ``skipped_reason`` enumerating why.
    """

    run_id: str
    written: bool
    agent_name: str | None = None
    path: str | None = None
    examples_count: int = 0
    skipped_reason: str | None = None
    # skipped_reason ∈ {
    #   run_not_found, wrong_mode, not_completed,
    #   below_threshold, no_agent_name, invalid_slug,
    #   already_promoted, write_failed,
    # }


class RefreshResult(BaseModel):
    """Result of ``SeedAgentPromoter.refresh`` (and the MCP tool)."""

    agent_name: str
    refreshed: bool
    examples_count: int = 0
    source_run_id: str | None = None
    skipped_reason: str | None = None
    # skipped_reason ∈ {file_not_found, no_source_run, source_run_deleted, write_failed}
