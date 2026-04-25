"""Schemas for /api/taxonomy/pattern-density.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PatternDensityRow(BaseModel):
    domain_id: str = Field(description="PromptCluster ID of the domain node.")
    domain_label: str
    cluster_count: int = Field(description="Active+mature+candidate clusters under this domain.")
    meta_pattern_count: int
    meta_pattern_avg_score: float | None = Field(
        default=None,
        description="Mean PromptCluster.avg_score across clusters with >=1 MetaPattern.",
    )
    global_pattern_count: int
    cross_cluster_injection_rate: float = Field(
        description=(
            "Ratio of in-period injected OptimizationPattern rows whose cluster "
            "belongs to this domain vs all in-period injections."
        ),
    )
    period_start: datetime
    period_end: datetime


class PatternDensityResponse(BaseModel):
    rows: list[PatternDensityRow]
    total_domains: int
    total_meta_patterns: int
    total_global_patterns: int
