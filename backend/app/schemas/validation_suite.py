"""Pydantic schemas for ValidationSuite (Topic Probe Tier 2, v0.4.22).

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §4.

Key-name invariant: ``BaselineScoresPayload`` uses ``p5_overall`` /
``p50_overall`` / ``p95_overall`` to match the canonical ``RunRow.aggregate``
output verbatim (`compute_run_aggregate`). Downstream consumers that read
``RunRow.aggregate`` directly stay compatible without a key-shape adapter.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Nested payload types (consumed by ValidationSuiteOut)
# ---------------------------------------------------------------------------


class PromptSnapshotItem(BaseModel):
    """One entry of ``validation_suite.prompts_snapshot`` — frozen prompt input.

    Position-correspondence invariant (§3 key invariant 2): ``prompts_snapshot[i]``
    aligns with ``baseline_scores.per_prompt[i]`` and the source
    ``RunRow.prompt_results[i]``. Frozen at save time — never mutated.

    ``intent_label`` is ``str | None`` (NOT plain ``str`` as spec §4 line 358
    originally specified — spec updated 2026-05-11 to match this shape).
    The topic_probe generator's per-row builder writes ``intent_label: None``
    (`services/generators/topic_probe_generator.py:397`) since T1 does not
    classify intent yet — a non-nullable ``str`` would crash
    ``model_validate()`` on every real probe run.
    """

    raw_prompt: str
    intent_label: str | None = None
    original_optimization_id: str | None = None
    # v0.4.37 §3.2 — full-length baseline optimized output (≤20,000 chars),
    # captured from the source Optimization row at save time so provenance
    # survives row deletion. None for pre-v0.4.37 suites and for sources
    # without output. Additive JSON — no migration.
    baseline_optimized_prompt: str | None = None


class PerPromptScore(BaseModel):
    """One entry of ``baseline_scores.per_prompt`` — frozen scoring snapshot."""

    raw_prompt_idx: int
    overall: float
    dimensions: dict[str, float]


class BaselineScoresPayload(BaseModel):
    """Shape of ``validation_suite.baseline_scores`` JSON column.

    Key names match the canonical ``compute_run_aggregate`` output verbatim —
    ``p5_overall`` / ``p50_overall`` / ``p95_overall`` (NOT ``p5``/``p50``/``p95``)
    to preserve downstream-consumer compatibility with existing
    ``RunRow.aggregate`` readers.
    """

    mean_overall: float
    p5_overall: float
    p50_overall: float
    p95_overall: float
    per_prompt: list[PerPromptScore]
    task_type_distribution: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Request / response types
# ---------------------------------------------------------------------------


class SaveSuiteRequest(BaseModel):
    """POST /api/probes/{run_id}/save-as-suite body."""

    label: str = Field(..., min_length=1, max_length=120)
    tolerance_abs: float = Field(0.5, ge=0.1, le=5.0)


class RetireSuiteRequest(BaseModel):
    """POST /api/suites/{id}/retire body."""

    reason: str = Field(..., min_length=1, max_length=500)


class ValidationSuiteOut(BaseModel):
    """Full ValidationSuite read response — backs GET /api/suites/{id} +
    POST /api/probes/{run_id}/save-as-suite.

    ``from_attributes=True`` lets ``model_validate()`` accept either an ORM
    instance or a plain dict (mirrors `schemas/templates.py:TemplateRead`).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    # source_run_id nullable matches column nullability — becomes None when the
    # source run is later deleted (ondelete=SET NULL fires).
    source_run_id: str | None
    label: str
    tolerance_abs: float
    project_id: str | None
    repo_full_name: str | None
    created_at: datetime
    retired_at: datetime | None = None
    retired_reason: str | None = None
    is_release_gate: bool = False  # T3.1 spec §3.5
    prompts_snapshot: list[PromptSnapshotItem]
    baseline_scores: BaselineScoresPayload


class ValidationSuiteListItem(BaseModel):
    """Abbreviated view for paginated listings — backs GET /api/suites items."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source_run_id: str | None
    label: str
    tolerance_abs: float
    project_id: str | None
    repo_full_name: str | None
    created_at: datetime
    retired_at: datetime | None = None
    prompts_count: int
    baseline_mean: float


class ValidationSuiteListResponse(BaseModel):
    """Pagination envelope for GET /api/suites — mirrors ``RunListResponse``."""

    total: int
    count: int
    offset: int
    items: list[ValidationSuiteListItem]
    has_more: bool
    next_offset: int | None


class ReplayRunOut(BaseModel):
    """Returned by POST /api/suites/{id}/replay at 202-Accepted time.

    Constructed BEFORE the replay generator runs — repo-drift detection lives
    inside ``ReplayRunGenerator.run()`` and is written to
    ``RunRow.aggregate['replay_warnings']`` post-completion, NOT this response.
    """

    run_id: str
    suite_id: str
    mode: Literal["replay_run"] = "replay_run"
    status: Literal["running"] = "running"
    started_at: datetime
    poll_url: str


# ---------------------------------------------------------------------------
# Regression-alarm types (T2 Cycle 4 fleshes out compute_regression_alarm —
# the schema names exist here so route + service code can import them now)
# ---------------------------------------------------------------------------


class RegressionAlarmEntry(BaseModel):
    """One alarm row — a suite whose latest replay regressed beyond tolerance."""

    suite_id: str
    label: str
    baseline_mean: float
    latest_mean: float
    delta_abs: float
    tolerance_abs: float
    latest_replay_id: str
    latest_replay_at: datetime


class RegressionAlarmBlock(BaseModel):
    """Aggregated alarm payload — backs GET /api/health alarm block."""

    suites_total: int
    suites_in_alarm: int
    latest_alarms: list[RegressionAlarmEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Release-gate types (T3.1 — spec §3.5)
# ---------------------------------------------------------------------------


class ReleaseGateRequest(BaseModel):
    """``POST /api/suites/{id}/release-gate`` body schema.

    Spec: ``docs/superpowers/specs/2026-05-19-t3.1-release-gate-design.md`` §3.5.
    """

    enabled: bool


class ReleaseGatedSuiteOut(BaseModel):
    """``GET /api/suites/release-gates`` entry shape.

    ``alarm_state`` derives from the suite's appearance in
    ``RegressionAlarmBlock.latest_alarms``: ``'firing'`` if present (latest
    replay regressed beyond tolerance), else ``'nominal'``. Flagged suites
    with no completed replays surface as ``'nominal'`` (they pass the gate —
    the gate only blocks on active regressions).

    Spec: ``docs/superpowers/specs/2026-05-19-t3.1-release-gate-design.md`` §3.5.
    """

    suite_id: str
    label: str
    alarm_state: Literal["firing", "nominal"]
    baseline_mean: float | None
    latest_mean: float | None
    delta_abs: float | None
    latest_replay_at: datetime | None
