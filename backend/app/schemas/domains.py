"""Pydantic models for the domains API."""

from typing import Optional

from pydantic import BaseModel, Field


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


class RebuildSubDomainsRequest(BaseModel):
    """R6: operator-triggered sub-domain rebuild request body."""

    min_consistency: Optional[float] = Field(
        default=None,
        ge=0.25,
        le=1.0,
        description=(
            "Override the adaptive consistency threshold for this rebuild. "
            "Default (None) keeps the standard "
            "max(0.40, 0.60 - 0.004*N) formula. "
            "Recommended override: 0.30. Must be >= 0.25 = "
            "SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR (Pydantic enforced)."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "When True, computes which sub-domains WOULD be created without "
            "modifying state. Returns the planned list."
        ),
    )


class RebuildSubDomainsResult(BaseModel):
    """R6: response for the rebuild-sub-domains endpoint."""

    domain_id: str
    domain_label: str
    threshold_used: float
    proposed: list[str]
    created: list[str]
    skipped_existing: list[str]
    dry_run: bool
