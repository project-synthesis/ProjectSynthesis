"""Tests for prompts/probe-agent.md (Topic Probe Tier 1, v0.5.0).

Verifies template rendering, manifest validation, hot-reload semantics,
and backtick-density of generator output. RED phase for cycle 1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import PROMPTS_DIR
from app.services.prompt_loader import PromptLoader


_PROBE_VARS: dict[str, str | None] = {
    "topic": "embedding cache invalidation in EmbeddingIndex",
    "scope": "**/*",
    "intent_hint": "audit",
    "n_prompts": "12",  # str-cast at call site (PromptLoader.render takes dict[str, str | None])
    "repo_full_name": "owner/repo",
    "codebase_context": "(synthesis excerpt) ... `_id_to_label` ... `tombstones` ...",
    "known_domains": "backend, frontend, data, embeddings, general",
    "existing_clusters_brief": "Embedding Index Audits, Async Event Coordination",
}


class TestProbeAgentTemplate:
    """AC-C1-1 through AC-C1-5 — see docs/specs/topic-probe-2026-04-29.md §8 Cycle 1."""

    def test_renders_with_all_variables(self):
        """AC-C1-1: All 8 declared variables substituted; missing var raises ValueError."""
        loader = PromptLoader(PROMPTS_DIR)
        body = loader.render("probe-agent.md", _PROBE_VARS)
        for var_name in _PROBE_VARS:
            assert f"{{{{{var_name}}}}}" not in body, (
                f"Template still contains unsubstituted var '{var_name}'"
            )

    def test_missing_variable_raises_valueerror(self):
        """AC-C1-1: Missing required variable raises ValueError (not PromptLoaderError)."""
        loader = PromptLoader(PROMPTS_DIR)
        partial = {k: v for k, v in _PROBE_VARS.items() if k != "topic"}
        with pytest.raises(ValueError, match=r"topic"):
            loader.render("probe-agent.md", partial)

    def test_manifest_declares_all_variables(self):
        """AC-C1-2: prompts/manifest.json declares all 8 vars."""
        manifest_path = PROMPTS_DIR / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        entry = manifest.get("probe-agent.md")
        assert entry is not None, "probe-agent.md not declared in manifest.json"
        declared = set(entry["variables"])
        expected = set(_PROBE_VARS.keys())
        assert declared == expected, (
            f"Manifest variable mismatch: missing={expected - declared}, extra={declared - expected}"
        )

    def test_template_directives_present(self):
        """AC-C1-4: Body contains generator directives that lead to JSON output schema."""
        loader = PromptLoader(PROMPTS_DIR)
        body = loader.render("probe-agent.md", _PROBE_VARS)
        # Generator must instruct LLM to produce JSON-parseable output
        assert "JSON" in body or "json" in body
        # Must reference the schema field name `prompts`
        assert "prompts" in body
        # Must enforce backtick-density (matches F1 specificity heuristic)
        assert "backtick" in body.lower() or "`" in body

    def test_template_intent_axis_diversity_directive(self):
        """AC-C1-4 (extension): body instructs diversity along explore/audit/refactor."""
        loader = PromptLoader(PROMPTS_DIR)
        body = loader.render("probe-agent.md", _PROBE_VARS)
        body_lower = body.lower()
        for keyword in ("explore", "audit", "refactor"):
            assert keyword in body_lower, (
                f"Body should mention '{keyword}' for intent-axis diversity guidance"
            )
