"""Shared aggregate computation for run generators.

Spec §5 (Cycle 6, T2): ``compute_run_aggregate`` projects per-prompt
``GeneratorResult.prompt_results`` rows into the canonical 8-key block
consumed by ``RunRow.aggregate``. Extracted from
``TopicProbeGenerator._build_aggregate`` so the topic-probe and replay-run
generators share one implementation — the regression-alarm + suite-detail
view contracts both depend on byte-identical aggregate shapes across
generator implementations.

Position correspondence with the source ``prompts_snapshot`` (key
invariant 2 in spec §3) is preserved by the *caller* — this helper only
aggregates and does not reorder.

Algorithm parity with ``TopicProbeGenerator._build_aggregate``:

* ``mean_overall`` = ``round(statistics.mean(scores), 3)`` over completed scores
* ``p50_overall`` = ``round(statistics.median(scores), 3)``
* ``p5_overall`` / ``p95_overall`` = ``round(statistics.quantiles(scores, n=20)[i], 3)``
  when N≥5 completed scores; otherwise the min/max of the completed scores
* ``completed_count`` / ``failed_count`` partition on ``status == 'completed'``
* ``f5_flag_fires`` counts entries whose ``divergence_flags`` contains
  ``'possible_false_premise'`` (additive replay-side metric — topic_probe today
  returns 0 because the placeholder per-prompt path does not run scoring)
* ``scoring_formula_version`` mirrors ``schemas/pipeline_contracts.py``
* ``task_type_distribution`` is a defensive ``Counter`` over completed entries'
  ``task_type`` (defaulting to ``'unknown'`` when absent — replay's
  ``run_single_prompt`` populates this from the analysis phase, but the
  fallback keeps the contract intact for older / synthesized rows)

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from app.schemas.pipeline_contracts import SCORING_FORMULA_VERSION

# Canonical aggregate keys per spec §5. Single source of truth referenced
# by both producers (this module + TopicProbeGenerator._build_aggregate
# delegate) and downstream consumers (ValidationSuiteService baseline
# snapshot, regression alarm). The ``task_type_distribution`` 9th key is
# additive metadata that the spec lists separately because it's a dict
# value rather than a scalar.
_AGGREGATE_KEYS: tuple[str, ...] = (
    "mean_overall",
    "p5_overall",
    "p50_overall",
    "p95_overall",
    "completed_count",
    "failed_count",
    "f5_flag_fires",
    "scoring_formula_version",
)


def compute_run_aggregate(prompt_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Project per-prompt results into the canonical aggregate dict.

    Parameters
    ----------
    prompt_results : list[dict[str, Any]]
        Per-prompt rows. Each row must carry ``status`` (``'completed'`` /
        ``'failed'``); completed rows may also carry ``overall_score``
        (float), ``task_type`` (str), and ``divergence_flags`` (list[str]).
        Missing fields fall back to sensible defaults (``overall_score``
        excluded from mean computation; ``task_type`` defaults to
        ``'unknown'``; ``divergence_flags`` defaults to empty).

    Returns
    -------
    dict[str, Any]
        Aggregate dict with the 8 canonical keys plus
        ``task_type_distribution``. Numeric fields default to ``0.0`` /
        ``0`` for empty input so the dict always validates against the
        consumer schemas.
    """
    completed = [r for r in prompt_results if r.get("status") == "completed"]
    failed = [r for r in prompt_results if r.get("status") != "completed"]

    scores = [
        float(r["overall_score"])
        for r in completed
        if r.get("overall_score") is not None
    ]

    # Byte-identical with TopicProbeGenerator._build_aggregate empty
    # semantics: when zero completed scores are available the four score
    # fields are ``None`` rather than ``0.0`` so downstream consumers can
    # distinguish "no completed runs" from "ran but scored zero". The
    # ValidationSuiteService canonical builder applies ``or 0.0`` when
    # it needs a scalar, so the ``None`` does not propagate into stored
    # baseline JSON.
    agg_mean: float | None = None
    agg_p5: float | None = None
    agg_p50: float | None = None
    agg_p95: float | None = None
    if scores:
        agg_mean = round(statistics.mean(scores), 3)
        agg_p50 = round(statistics.median(scores), 3)
        if len(scores) >= 5:
            qs = statistics.quantiles(scores, n=20)
            agg_p5 = round(qs[0], 3)
            agg_p95 = round(qs[-1], 3)
        else:
            agg_p5 = round(min(scores), 3)
            agg_p95 = round(max(scores), 3)

    # F5 flag fires — count completed rows whose divergence_flags include
    # the ``'possible_false_premise'`` sentinel emitted by score_blender.
    f5_fires = sum(
        1
        for r in completed
        if "possible_false_premise" in (r.get("divergence_flags") or [])
    )

    # Defensive task_type counter — falls back to ``'unknown'`` so the
    # value is always a non-None string (the schema and frontend treat
    # the distribution as a {str: int} histogram).
    task_types: Counter[str] = Counter(
        (r.get("task_type") or "unknown") for r in completed
    )

    return {
        "mean_overall": agg_mean,
        "p5_overall": agg_p5,
        "p50_overall": agg_p50,
        "p95_overall": agg_p95,
        "completed_count": len(completed),
        "failed_count": len(failed),
        "f5_flag_fires": f5_fires,
        "scoring_formula_version": SCORING_FORMULA_VERSION,
        "task_type_distribution": dict(task_types),
    }


__all__ = ["compute_run_aggregate"]
