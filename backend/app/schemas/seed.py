# backend/app/schemas/seed.py
"""Request/response schemas for batch seeding."""

from pydantic import BaseModel, Field


class SeedRequest(BaseModel):
    """POST /api/seed request body."""

    project_description: str = Field(
        ..., min_length=20,
        description="Project description for prompt generation.",
    )
    workspace_path: str | None = Field(
        None, description="Local workspace path for context extraction.",
    )
    repo_full_name: str | None = Field(
        None, description="GitHub repo (owner/repo) for explore phase.",
    )
    prompt_count: int = Field(
        30, ge=5, le=100,
        description="Target total prompts across all agents.",
    )
    agents: list[str] | None = Field(
        None, description="Specific agent names. None = all enabled.",
    )
    prompts: list[str] | None = Field(
        None, description="User-provided prompts (bypasses generation).",
    )


class SeedOutput(BaseModel):
    """Response from synthesis_seed tool and POST /api/seed.

    NOTE: actual_cost_usd is intentionally omitted — cost tracking is
    estimation-only (estimated_cost_usd). No per-call billing data is
    available from the provider without additional instrumentation.
    """

    status: str  # completed | partial | failed
    batch_id: str
    tier: str  # internal | sampling | passthrough
    prompts_generated: int
    prompts_optimized: int
    prompts_failed: int
    estimated_cost_usd: float | None = None
    domains_touched: list[str] = []
    clusters_created: int = 0
    summary: str = ""
    duration_ms: int = 0
