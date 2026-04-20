"""Pydantic models for the domains API."""

from pydantic import BaseModel


class DomainInfo(BaseModel):
    """Domain node summary for GET /api/domains.

    When the endpoint is called with ``project_id``, ``member_count`` and
    ``avg_score`` reflect only the optimizations owned by that project, and
    ``project_member_count`` equals ``member_count`` for clarity. Without a
    project filter, they reflect the global member pool.
    """

    id: str
    label: str
    color_hex: str
    member_count: int = 0
    avg_score: float | None = None
    source: str = "seed"  # seed | discovered | manual
    parent_id: str | None = None
    project_member_count: int | None = None
