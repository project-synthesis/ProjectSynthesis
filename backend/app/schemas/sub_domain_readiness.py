"""Pydantic schemas for domain + sub-domain readiness endpoints.

Exposes the live state of the sub-domain emergence cascade (three-source
qualifier matching) alongside top-level domain stability guards. One report
per domain node covers both projections; `compute_domain_readiness()` in
`services/taxonomy/sub_domain_readiness.py` produces these models.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

QualifierSource = Literal["domain_raw", "intent_label", "tf_idf"]
EmergenceTier = Literal["ready", "warming", "inert"]
StabilityTier = Literal["healthy", "guarded", "critical"]
EmergenceBlocker = Literal[
    "no_candidates",
    "below_threshold",
    "insufficient_members",
    "single_cluster",
    "none",
]


class QualifierCandidate(BaseModel):
    """One qualifier that could become a sub-domain if it clears the threshold."""

    qualifier: str
    count: int = Field(ge=0, description="Members matching this qualifier")
    consistency: float = Field(
        ge=0.0, le=1.0, description="count / total_opts — share of domain members"
    )
    dominant_source: QualifierSource = Field(
        description="Which cascade source contributed the most to this qualifier"
    )
    source_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Per-source hit counts: keys are 'domain_raw'|'intent_label'|'tf_idf'",
    )
    cluster_breadth: int = Field(
        ge=0, description="Distinct clusters contributing — a sub-domain needs >= 2"
    )


class SubDomainEmergenceReport(BaseModel):
    """Readiness-to-promote: how close a new sub-domain is to emerging."""

    threshold: float = Field(ge=0.0, le=1.0)
    threshold_formula: str = Field(
        description="Human-readable formula with substituted values"
    )
    min_member_count: int = Field(ge=1, description="SUB_DOMAIN_QUALIFIER_MIN_MEMBERS")
    total_opts: int = Field(ge=0, description="Total optimizations scanned")
    top_candidate: QualifierCandidate | None = None
    gap_to_threshold: float | None = Field(
        default=None,
        description="threshold - top_candidate.consistency; negative = ready",
    )
    ready: bool = Field(description="Top candidate meets threshold + MIN_MEMBERS + breadth")
    blocked_reason: EmergenceBlocker | None = None
    runner_ups: list[QualifierCandidate] = Field(
        default_factory=list,
        description="Up to 5 runners-up ordered by consistency desc",
    )
    tier: EmergenceTier


class DomainStabilityGuards(BaseModel):
    """Outcome of each dissolution guard for a domain."""

    general_protected: bool = Field(
        description="Domain label is 'general' — dissolution permanently blocked"
    )
    has_sub_domain_anchor: bool = Field(
        description="Domain has at least one child sub-domain — bottom-up block"
    )
    age_eligible: bool = Field(
        description="Domain is old enough to be dissolved (age >= threshold)"
    )
    above_member_ceiling: bool = Field(
        description="Member count > ceiling — too large to dissolve on consistency alone"
    )
    consistency_above_floor: bool = Field(
        description="Source-1 consistency >= dissolution floor"
    )


class DomainStabilityReport(BaseModel):
    """Readiness-against-dissolution for a top-level domain."""

    consistency: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of member opts whose domain_raw primary matches this label",
    )
    dissolution_floor: float = Field(ge=0.0, le=1.0)
    hysteresis_creation_threshold: float = Field(
        ge=0.0, le=1.0,
        description="Creation threshold — gap vs floor is the hysteresis band",
    )
    age_hours: float = Field(ge=0.0)
    min_age_hours: int = Field(ge=0)
    member_count: int = Field(ge=0)
    member_ceiling: int = Field(ge=0)
    sub_domain_count: int = Field(ge=0)
    total_opts: int = Field(ge=0)
    guards: DomainStabilityGuards
    tier: StabilityTier
    dissolution_risk: float = Field(
        ge=0.0,
        le=1.0,
        description="Composite [0,1] — 1.0 means all guards failing + likely dissolution",
    )
    would_dissolve: bool = Field(
        description="All guards currently failing — next warm cycle would dissolve"
    )


class DomainReadinessReport(BaseModel):
    """Unified readiness snapshot for a domain node.

    Composes stability (vs dissolution) + emergence (of new sub-domain).
    """

    domain_id: str
    domain_label: str
    member_count: int = Field(ge=0)
    stability: DomainStabilityReport
    emergence: SubDomainEmergenceReport
    computed_at: datetime


class ReadinessSnapshot(BaseModel):
    """One persisted observation of a domain's readiness state."""

    ts: datetime = Field(description="UTC observation time")
    domain_id: str
    domain_label: str
    consistency: float = Field(ge=0.0, le=1.0)
    dissolution_risk: float = Field(ge=0.0, le=1.0)
    stability_tier: StabilityTier
    emergence_tier: EmergenceTier
    top_candidate_gap: float | None = Field(
        default=None,
        description="threshold - top_candidate.consistency; None when no candidates",
    )
    member_count: int = Field(ge=0)
    total_opts: int = Field(ge=0)

    @field_validator("ts")
    @classmethod
    def _coerce_ts_to_utc(cls, value: datetime) -> datetime:
        """Normalize ``ts`` to UTC.

        Naive datetimes are assumed-UTC (matches the engine's ``_utcnow()``
        convention in ``_constants.py``); aware datetimes are converted.
        Ensures downstream consumers (daily JSONL rotation, time-bucket
        aggregation) never drift across timezones.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ReadinessHistoryPoint(BaseModel):
    """One time-series point — either a raw snapshot or a bucket mean."""

    ts: datetime
    consistency: float = Field(ge=0.0, le=1.0)
    dissolution_risk: float = Field(ge=0.0, le=1.0)
    top_candidate_gap: float | None = None
    stability_tier: StabilityTier
    emergence_tier: EmergenceTier
    is_bucket_mean: bool = Field(
        default=False,
        description="True when this point is an hourly bucket aggregate",
    )


class ReadinessHistoryResponse(BaseModel):
    """Time-series response for a single domain."""

    domain_id: str
    domain_label: str
    window: Literal["24h", "7d", "30d"]
    bucketed: bool = Field(description="True when hourly bucket means were applied")
    points: list[ReadinessHistoryPoint]
