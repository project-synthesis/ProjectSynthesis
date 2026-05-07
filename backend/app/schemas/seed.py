# backend/app/schemas/seed.py
"""Request/response schemas for batch seeding."""

from pydantic import BaseModel, Field


class SeedRequest(BaseModel):
    """POST /api/seed request body.

    Foundation P3 cycle 12 (v0.4.18) relaxed ``project_description`` to
    optional + dropped ``min_length`` so that the early-failure path
    (no project_description + no prompts + no provider) flows through
    ``RunOrchestrator`` → ``SeedAgentGenerator.run`` and surfaces as
    HTTP 200 with ``SeedOutput(status='failed', summary='Requires
    project_description...')`` — preserving today's contract per spec § 6.3.
    Pydantic 422s for an empty body would break that contract.
    """

    project_description: str | None = Field(
        None,
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

    Foundation P3 cycle 12 (v0.4.18) added the additive ``run_id`` field
    populated from the underlying ``RunRow.id`` so callers can correlate
    the synchronous response with cross-channel SSE / GET-by-id reads.
    Defaults to ``None`` for backward-compat with old test fixtures.
    """

    status: str  # running | completed | partial | failed
    batch_id: str | None = None
    tier: str | None = None  # internal | sampling | passthrough
    prompts_generated: int = 0
    prompts_optimized: int = 0
    prompts_failed: int = 0
    estimated_cost_usd: float | None = None
    domains_touched: list[str] = []
    clusters_created: int = 0
    summary: str = ""
    duration_ms: int = 0
    run_id: str | None = None  # Foundation P3 cycle 12 additive field
