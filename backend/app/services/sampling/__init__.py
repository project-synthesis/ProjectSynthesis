"""Sampling pipeline package — split from the monolithic ``sampling_pipeline.py``.

Submodules:

* ``primitives`` — low-level MCP sampling request helpers (plain + structured
  tool-calling variants, text/JSON extraction, fallback parsers).
* ``persistence`` — DB-facing helpers used by the pipelines (applied pattern
  resolution + usage increment, intent drift check, historical stats fetch,
  applied pattern provenance tracking).
* ``analyze`` — standalone ``run_sampling_analyze`` entry point
  (analyze + baseline score, no optimize phase).

The primary orchestrator ``run_sampling_pipeline`` stays in
``app.services.sampling_pipeline`` to keep the public import path stable
across the wider codebase and tests.

Copyright 2025-2026 Project Synthesis contributors.
"""

from app.services.sampling.analyze import run_sampling_analyze
from app.services.sampling.persistence import (
    check_intent_drift,
    fetch_historical_stats,
    increment_pattern_usage,
    resolve_applied_pattern_text,
    track_applied_patterns,
)
from app.services.sampling.primitives import (
    SAMPLING_TIMEOUT_SECONDS,
    build_analysis_from_text,
    extract_json_block,
    extract_text,
    extract_tool_use,
    parse_text_response,
    pydantic_to_mcp_tool,
    sampling_request_plain,
    sampling_request_structured,
)

__all__ = [
    "SAMPLING_TIMEOUT_SECONDS",
    "build_analysis_from_text",
    "check_intent_drift",
    "extract_json_block",
    "extract_text",
    "extract_tool_use",
    "fetch_historical_stats",
    "increment_pattern_usage",
    "parse_text_response",
    "pydantic_to_mcp_tool",
    "resolve_applied_pattern_text",
    "run_sampling_analyze",
    "sampling_request_plain",
    "sampling_request_structured",
    "track_applied_patterns",
]
