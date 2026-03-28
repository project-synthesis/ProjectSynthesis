"""Pydantic models for the domains API."""

from pydantic import BaseModel


class DomainInfo(BaseModel):
    """Domain node summary for GET /api/domains."""

    id: str
    label: str
    color_hex: str
    member_count: int = 0
    avg_score: float | None = None
    source: str = "seed"  # seed | discovered | manual
