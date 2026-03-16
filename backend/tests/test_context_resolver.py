"""Tests for ContextResolver (Task 1 — Phase 1b)."""

import pytest

from app.config import settings
from app.services.context_resolver import ContextResolver
from app.schemas.pipeline_contracts import ResolvedContext


_VALID_PROMPT = "Write a Python function that parses JSON."


class TestContextResolver:
    # ------------------------------------------------------------------
    # 1. Minimal resolve — raw prompt only, all optional None
    # ------------------------------------------------------------------

    def test_resolve_minimal(self):
        result = ContextResolver.resolve(raw_prompt=_VALID_PROMPT)
        assert isinstance(result, ResolvedContext)
        assert result.raw_prompt == _VALID_PROMPT
        assert result.strategy_override is None
        assert result.codebase_guidance is None
        assert result.codebase_context is None
        assert result.adaptation_state is None

    # ------------------------------------------------------------------
    # 2. Prompt too short
    # ------------------------------------------------------------------

    def test_prompt_too_short_rejected(self):
        with pytest.raises(ValueError, match="too short"):
            ContextResolver.resolve(raw_prompt="hi")

    # ------------------------------------------------------------------
    # 3. Prompt too long
    # ------------------------------------------------------------------

    def test_prompt_too_long_rejected(self):
        over_limit = "x" * (settings.MAX_RAW_PROMPT_CHARS + 1)
        with pytest.raises(ValueError, match="exceeds maximum"):
            ContextResolver.resolve(raw_prompt=over_limit)

    # ------------------------------------------------------------------
    # 4. Guidance truncated at cap
    # ------------------------------------------------------------------

    def test_guidance_truncated_at_cap(self):
        long_guidance = "g" * 25000
        result = ContextResolver.resolve(
            raw_prompt=_VALID_PROMPT,
            codebase_guidance=long_guidance,
        )
        # The resolved guidance is wrapped; the raw content inside should be
        # at most MAX_GUIDANCE_CHARS chars before wrapping was applied.
        assert result.codebase_guidance is not None
        # Wrapping adds delimiters; check the inner content length indirectly
        # by verifying the raw content section is capped.
        inner = long_guidance[: settings.MAX_GUIDANCE_CHARS]
        assert inner in result.codebase_guidance
        # The original excess must NOT appear
        assert "g" * (settings.MAX_GUIDANCE_CHARS + 1) not in result.codebase_guidance

    # ------------------------------------------------------------------
    # 5. Codebase context truncated at cap
    # ------------------------------------------------------------------

    def test_codebase_context_truncated_at_cap(self):
        long_context = "c" * 110000
        result = ContextResolver.resolve(
            raw_prompt=_VALID_PROMPT,
            codebase_context=long_context,
        )
        assert result.codebase_context is not None
        inner = long_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]
        assert inner in result.codebase_context
        assert "c" * (settings.MAX_CODEBASE_CONTEXT_CHARS + 1) not in result.codebase_context

    # ------------------------------------------------------------------
    # 6. Adaptation state truncated at cap
    # ------------------------------------------------------------------

    def test_adaptation_truncated_at_cap(self):
        long_adaptation = "a" * 6000
        result = ContextResolver.resolve(
            raw_prompt=_VALID_PROMPT,
            adaptation_state=long_adaptation,
        )
        assert result.adaptation_state is not None
        assert len(result.adaptation_state) == settings.MAX_ADAPTATION_CHARS

    # ------------------------------------------------------------------
    # 7. Untrusted-context wrapping
    # ------------------------------------------------------------------

    def test_untrusted_context_wrapping(self):
        result = ContextResolver.resolve(
            raw_prompt=_VALID_PROMPT,
            codebase_guidance="Be concise.",
            codebase_context="File tree here.",
        )
        assert result.codebase_guidance is not None
        assert '<untrusted-context source="codebase-guidance">' in result.codebase_guidance
        assert "</untrusted-context>" in result.codebase_guidance

        assert result.codebase_context is not None
        assert '<untrusted-context source="github-explore">' in result.codebase_context
        assert "</untrusted-context>" in result.codebase_context

        # adaptation_state must NOT be wrapped
        result2 = ContextResolver.resolve(
            raw_prompt=_VALID_PROMPT,
            adaptation_state="some state",
        )
        assert result2.adaptation_state is not None
        assert "<untrusted-context" not in result2.adaptation_state

    # ------------------------------------------------------------------
    # 8. context_sources tracking
    # ------------------------------------------------------------------

    def test_context_sources_tracking(self):
        # All three present
        result = ContextResolver.resolve(
            raw_prompt=_VALID_PROMPT,
            codebase_guidance="guidance",
            codebase_context="context",
            adaptation_state="state",
        )
        assert result.context_sources["codebase_guidance"] is True
        assert result.context_sources["codebase_context"] is True
        assert result.context_sources["adaptation"] is True

        # None present
        result2 = ContextResolver.resolve(raw_prompt=_VALID_PROMPT)
        assert result2.context_sources["codebase_guidance"] is False
        assert result2.context_sources["codebase_context"] is False
        assert result2.context_sources["adaptation"] is False

    # ------------------------------------------------------------------
    # 9. trace_id generated
    # ------------------------------------------------------------------

    def test_trace_id_generated(self):
        result = ContextResolver.resolve(raw_prompt=_VALID_PROMPT)
        assert isinstance(result.trace_id, str)
        assert len(result.trace_id) > 0

        # Each call gets a unique trace_id
        result2 = ContextResolver.resolve(raw_prompt=_VALID_PROMPT)
        assert result.trace_id != result2.trace_id


class TestWorkspaceScanning:
    def test_resolve_with_workspace_path(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Project rules\nUse pytest.")
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function that sorts a list",
            workspace_path=str(tmp_path),
        )
        assert ctx.codebase_guidance is not None
        assert "Project rules" in ctx.codebase_guidance
        assert ctx.context_sources["codebase_guidance"] is True

    def test_explicit_guidance_takes_precedence(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("From workspace")
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function that sorts a list",
            codebase_guidance="Explicit guidance",
            workspace_path=str(tmp_path),
        )
        assert "Explicit guidance" in ctx.codebase_guidance
        # Workspace content should NOT be present (explicit wins)
        assert "From workspace" not in ctx.codebase_guidance

    def test_workspace_no_guidance_files(self, tmp_path):
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function that sorts a list",
            workspace_path=str(tmp_path),
        )
        assert ctx.codebase_guidance is None

    def test_scanned_guidance_not_double_wrapped(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Test content")
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function that sorts a list",
            workspace_path=str(tmp_path),
        )
        # Should have exactly one layer of untrusted-context wrapping
        count = ctx.codebase_guidance.count("<untrusted-context")
        assert count == 1  # from scanner, not double-wrapped by resolver
