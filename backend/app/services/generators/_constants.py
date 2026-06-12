"""Shared constants for run generators.

Named in the T2 spec and deliberately deferred ("Scaffolding != TDD" canon)
to land together with its first consumer — the v0.4.36 concurrent replay
executor (spec:
docs/superpowers/specs/2026-06-12-v0.4.36-replay-parallelism-and-debt-retirement-design.md §3.1).

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from app.services.batch_orchestrator import BATCH_CONCURRENCY_BY_TIER

# Per-tier per-prompt concurrency for generator fan-out (replay today; future
# generators reuse). Single source of truth: the batch-seed caps
# (internal=10 / api=5 / sampling=2) proven by seed batches in production.
# MappingProxyType over a copy: immutable view, decoupled from accidental
# mutation of the orchestrator's table.
PROBE_PROMPT_CONCURRENCY: Final[Mapping[str, int]] = MappingProxyType(dict(BATCH_CONCURRENCY_BY_TIER))

# Fallback when a generator passes an unknown tier label.
DEFAULT_PROMPT_CONCURRENCY: Final[int] = 5

# v0.4.37 §3.1 — replay output capture caps. DB p99 of optimized_prompt
# ≈ 5.7K chars (max observed 7.1K) — 20K is comfortable headroom. Observed
# max changes_summary is 5,492 chars — a 4K cap would truncate real data
# today, hence 8K.
REPLAY_OUTPUT_SNAPSHOT_MAX_CHARS: Final[int] = 20_000
REPLAY_CHANGES_SUMMARY_MAX_CHARS: Final[int] = 8_000

__all__ = [
    "DEFAULT_PROMPT_CONCURRENCY",
    "PROBE_PROMPT_CONCURRENCY",
    "REPLAY_CHANGES_SUMMARY_MAX_CHARS",
    "REPLAY_OUTPUT_SNAPSHOT_MAX_CHARS",
]
