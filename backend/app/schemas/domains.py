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


class DissolveEmptyResult(BaseModel):
    """v0.4.11 P1: response model for POST /api/domains/{id}/dissolve-empty.

    Mirrors the operator-friendly envelope used by the rebuild surface
    (always-200 + ``dissolved`` flag + reason on failure). The router
    converts ``reason`` codes ``not_empty`` and ``too_young`` to 409
    HTTPExceptions so curl operators see canonical REST semantics; the
    pure engine method always returns this dataclass-style object.
    """

    dissolved: bool = Field(
        description="True if dissolution actually occurred this call.",
    )
    domain_id: str = Field(
        description="The domain id that was targeted.",
    )
    domain_label: str | None = Field(
        default=None,
        description=(
            "The domain's label at time of dissolution "
            "(None if already dissolved)."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Reason if dissolved=False: 'already_dissolved' | 'not_empty' | "
            "'too_young' | None on success."
        ),
    )
    age_hours: float = Field(
        description="Domain age in hours at time of call.",
    )
