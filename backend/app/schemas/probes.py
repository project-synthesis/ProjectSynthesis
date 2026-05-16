"""Pydantic schemas for Topic Probe Tier 1 (v0.5.0).

See docs/specs/topic-probe-2026-04-29.md §4.2 for full design.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ProbeContext(BaseModel):
    """Phase 1 grounding output, fed into Phase 2 generator.

    T2 Cycle 7 schema relaxation (spec §2 + §5 topic-only branch):
      - ``repo_full_name`` relaxed from required ``str`` to ``str | None`` so
        the topic-only path can build a valid context without a linked repo
        (Phase 1 grounding is skipped entirely under ``grounding_mode='topic_only'``).
      - NEW ``commit_sha: str | None = None`` — provenance field for future
        replay-against-snapshot semantics. Populated by the codebase-mode
        grounding phase when a repo is linked; ``None`` for topic-only.
      - NEW ``topic_only: bool = False`` — explicit branch marker for
        downstream consumers (Phase 2 generation + reporting). The branch
        is otherwise discoverable from ``repo_full_name is None`` plus an
        empty ``relevant_files`` list, but an explicit flag keeps the
        contract grep-able and prevents accidental codebase-path execution
        on a degenerate codebase context.

    ``model_config = {"extra": "forbid"}`` is preserved so any typo at the
    call site still fails Pydantic validation immediately.
    """
    model_config = {"extra": "forbid"}

    topic: str
    scope: str = "**/*"
    intent_hint: Literal["audit", "refactor", "explore", "regression-test"] = "explore"
    repo_full_name: str | None = Field(
        default=None,
        description=(
            "Linked GitHub repo (``owner/name``). Required-in-practice for "
            "``grounding_mode='codebase'`` (the router's ``link_repo_first`` "
            "gate enforces it before the generator runs). ``None`` ONLY under "
            "``grounding_mode='topic_only'``, where the generator hard-codes "
            "this field to ``None`` regardless of what the caller passed — "
            "the topic-only path has no codebase to ground against, so a "
            "stale repo reference is silently dropped."
        ),
    )
    commit_sha: str | None = Field(
        default=None,
        description=(
            "Git commit SHA of the codebase snapshot captured by Phase 1 "
            "grounding. Populated by the codebase-mode path when the linked "
            "repo's HEAD SHA is resolvable; ``None`` under topic-only (no "
            "codebase to snapshot) or when the SHA lookup failed silently. "
            "Provenance field for future replay-against-snapshot semantics "
            "(T3+); T2 stores it on ``RunRow.topic_probe_meta.commit_sha`` "
            "but does not yet consume it on replay."
        ),
    )
    topic_only: bool = Field(
        default=False,
        description=(
            "Explicit branch marker — ``True`` iff "
            "``grounding_mode='topic_only'`` was selected on the request. "
            "Mirror of ``repo_full_name is None and not relevant_files`` but "
            "the explicit flag keeps the contract grep-able and prevents "
            "accidental codebase-path execution on a degenerate codebase "
            "context where retrieval returned zero files. Phase 2 reads "
            "this to select the topic-only template + inverted F1 filter."
        ),
    )
    project_id: str | None = None
    project_name: str | None = None
    dominant_stack: list[str] = Field(default_factory=list)
    relevant_files: list[str] = Field(
        default_factory=list,
        description="Top-K paths from RepoIndexQuery.query_curated_context",
    )
    explore_synthesis_excerpt: str | None = None  # cached synthesis, capped at PROBE_CODEBASE_MAX_CHARS
    known_domains: list[str] = Field(default_factory=list)
    existing_clusters_brief: list[dict[str, str]] = Field(default_factory=list)


class ProbeError(Exception):
    """Probe-specific exception with reason code.

    Reason codes (mapped to HTTP/MCP error responses):
    - 'link_repo_first': no repo_full_name provided
    - 'topic_not_found_in_repo': RepoIndexQuery returned 0 relevant files
    - 'generation_failed': probe_generation raised after retries
    """
    def __init__(self, reason: str, *, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


class ProbeRunRequest(BaseModel):
    """REST/MCP input — what the user provides.

    T2 Cycle 7 (spec §5): ``grounding_mode`` selects between codebase-grounded
    generation (the v0.4.12 contract) and topic-only generation (new). The
    default ``"codebase"`` is critical — the router calls ``body.model_dump()``
    to forward the RESOLVED default into ``RunRequest.payload`` so downstream
    consumers see the canonical value (not ``None``).
    """
    model_config = {"extra": "forbid"}
    topic: str = Field(min_length=3, max_length=500)
    scope: str | None = None
    intent_hint: Literal["audit", "refactor", "explore", "regression-test"] | None = None
    n_prompts: int | None = Field(default=None, ge=5, le=25)
    repo_full_name: str | None = None  # if None, server may resolve from session
    grounding_mode: Literal["codebase", "topic_only"] = "codebase"


class ProbePromptResult(BaseModel):
    """Per-prompt outcome captured by Phase 3 + Phase 5 reporting.

    Finding 17 backward-compat: ``prompt_text`` accepts both ``prompt_text``
    (current canonical key produced by ``TopicProbeGenerator._run_one_prompt``)
    and ``raw_prompt`` (legacy key produced by pre-T2-Cycle-6 development
    fixtures and the seed pipeline). ``populate_by_name=True`` lets external
    callers continue to construct with ``prompt_text=`` keyword AND
    JSON payloads to use either key on deserialization. Without this
    flex, ``GET /api/probes/{id}`` returns HTTP 500 on legacy rows whose
    ``prompt_results`` JSON column predates the canonical key.
    """

    model_config = ConfigDict(populate_by_name=True)

    prompt_idx: int
    prompt_text: str = Field(
        ...,
        validation_alias=AliasChoices("prompt_text", "raw_prompt"),
    )  # truncated to 1000 chars
    optimization_id: str | None = None
    overall_score: float | None = None
    intent_label: str | None = None
    cluster_id_at_persist: str | None = None
    cluster_label_at_persist: str | None = None
    domain: str | None = None
    duration_ms: int | None = None
    status: Literal["completed", "failed", "timeout"] = "completed"


class ProbeAggregate(BaseModel):
    mean_overall: float | None = None
    p5_overall: float | None = None
    p50_overall: float | None = None
    p95_overall: float | None = None
    completed_count: int = 0
    failed_count: int = 0
    f5_flag_fires: int = 0
    scoring_formula_version: int  # captured at probe time


class ProbeTaxonomyDelta(BaseModel):
    domains_created: list[str] = Field(default_factory=list)
    sub_domains_created: list[str] = Field(default_factory=list)
    clusters_created: list[dict[str, str]] = Field(default_factory=list)
    clusters_split: list[dict[str, str]] = Field(default_factory=list)
    proposal_rejected_min_source_clusters: int = 0


# --- Event payloads ---

class ProbeStartedEvent(BaseModel):
    probe_id: str
    topic: str
    scope: str
    intent_hint: str
    n_prompts: int
    repo_full_name: str


class ProbeGroundingEvent(BaseModel):
    probe_id: str
    retrieved_files_count: int
    has_explore_synthesis: bool
    dominant_stack: list[str] = Field(default_factory=list)


class ProbeGeneratingEvent(BaseModel):
    probe_id: str
    prompts_generated: int
    generator_duration_ms: int
    generator_model: str


class ProbeProgressEvent(BaseModel):
    """SSE event during Phase 3 — one per prompt completion."""
    probe_id: str
    current: int
    total: int
    optimization_id: str
    intent_label: str | None = None
    overall_score: float | None = None


class ProbeCompletedEvent(BaseModel):
    probe_id: str
    status: Literal["completed", "partial", "failed"]
    mean_overall: float | None = None
    prompts_generated: int
    taxonomy_delta_summary: dict[str, int] = Field(default_factory=dict)


class ProbeFailedEvent(BaseModel):
    probe_id: str
    phase: Literal["grounding", "generating", "running", "reporting"]
    error_class: str
    error_message_truncated: str  # max 200 chars


class ProbeRateLimitedEvent(BaseModel):
    """Emitted when the probe's LLM provider returns a rate limit.

    Surfaces structured ``reset_at`` so the UI can render a precise
    "retry after X" countdown instead of a generic "request failed"
    message. The probe's terminal status will be ``partial`` (some
    prompts persisted before the limit hit) or ``failed`` (none did);
    the row's ``error`` field is prefixed with ``rate_limited:`` so
    list endpoints can filter / badge accordingly.
    """
    probe_id: str
    provider: str  # "claude_cli" | "anthropic_api" | etc
    reset_at_iso: str | None  # absolute UTC ISO-8601 string; None when unknown
    estimated_wait_seconds: int | None  # for clients that prefer relative
    completed_count: int
    aborted_count: int  # prompts not started because the limit hit mid-batch
    total: int


class ProbeRunResult(BaseModel):
    """Final response after all 5 phases complete."""
    id: str
    topic: str
    scope: str
    intent_hint: str
    repo_full_name: str
    project_id: str | None = None
    commit_sha: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    prompts_generated: int
    prompt_results: list[ProbePromptResult] = Field(default_factory=list)
    aggregate: ProbeAggregate
    taxonomy_delta: ProbeTaxonomyDelta = Field(default_factory=ProbeTaxonomyDelta)
    final_report: str = ""
    status: Literal["completed", "failed", "partial", "running"] = "running"
    suite_id: str | None = None


class ProbeRunSummary(BaseModel):
    """Compact view for /api/probes list (excludes JSON blobs)."""
    id: str
    topic: str
    repo_full_name: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    prompts_generated: int
    mean_overall: float | None = None  # extracted from aggregate JSON


class ProbeListResponse(BaseModel):
    total: int
    count: int
    offset: int
    items: list[ProbeRunSummary] = Field(default_factory=list)
    has_more: bool
    next_offset: int | None = None
