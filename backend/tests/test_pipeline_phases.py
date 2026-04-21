"""Tests for ``pipeline_phases`` pure helpers.

Covers observability guards: the ``optimize_inject`` log line must
report ``strategy_intel=0`` when the incoming value is None, empty, or
whitespace-only — matching the upstream enrichment log's
``strategy_intel=none`` semantics. Prevents the log from counting
rendered wrapper tags (e.g. ``<strategy-intelligence></strategy-intelligence>``)
as real payload.
"""

from __future__ import annotations

import logging
import re

import pytest

from app.schemas.pipeline_contracts import AnalysisResult
from app.services.pipeline_phases import build_optimize_context
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader


def _make_analysis() -> AnalysisResult:
    return AnalysisResult(
        task_type="coding",
        weaknesses=["vague"],
        strengths=["concise"],
        selected_strategy="chain-of-thought",
        strategy_rationale="good for coding",
        confidence=0.9,
    )


@pytest.fixture
def loaders(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    strategies = prompts / "strategies"
    strategies.mkdir()
    # Template embeds the wrapper tags so render output ALWAYS includes them —
    # this mimics the real ``optimize.md`` where wrapper presence does not
    # imply content presence.
    (prompts / "optimize.md").write_text(
        "{{raw_prompt}}\n{{analysis_summary}}\n{{strategy_instructions}}\n"
        "<codebase-context>\n{{codebase_context}}\n</codebase-context>\n"
        "<strategy-intelligence>\n{{strategy_intelligence}}\n</strategy-intelligence>\n"
        "{{applied_patterns}}\n{{few_shot_examples}}\n{{divergence_alerts}}\n"
    )
    (prompts / "manifest.json").write_text(
        '{"optimize.md": {"required": ["raw_prompt", "strategy_instructions", '
        '"analysis_summary"], "optional": ["codebase_context", '
        '"strategy_intelligence", "applied_patterns", "few_shot_examples", '
        '"divergence_alerts"]}}'
    )
    (strategies / "chain-of-thought.md").write_text("Think step by step.")
    prompt_loader = PromptLoader(prompts_dir=prompts)
    strategy_loader = StrategyLoader(strategies_dir=strategies)
    return prompt_loader, strategy_loader


def _extract_strategy_intel_chars(log_records) -> int:
    """Parse ``strategy_intel=N`` from the optimize_inject log record."""
    for rec in log_records:
        msg = rec.getMessage()
        if "optimize_inject" in msg:
            match = re.search(r"strategy_intel=(\d+)", msg)
            if match:
                return int(match.group(1))
    raise AssertionError("optimize_inject log not emitted")


@pytest.mark.asyncio
class TestOptimizeInjectStrategyIntelLog:
    """I-7: ``strategy_intel=`` count must ignore wrapper-only / empty values."""

    async def _invoke(self, loaders, db_session, caplog, strategy_intelligence):
        prompt_loader, strategy_loader = loaders
        caplog.set_level(logging.INFO, logger="app.services.pipeline_phases")
        await build_optimize_context(
            raw_prompt="Write a sort function.",
            analysis=_make_analysis(),
            effective_strategy="chain-of-thought",
            effective_domain="general",
            prompt_loader=prompt_loader,
            strategy_loader=strategy_loader,
            db=db_session,
            applied_pattern_ids=None,
            auto_injected_patterns=[],
            codebase_context=None,
            strategy_intelligence=strategy_intelligence,
            divergence_alerts=None,
            prompt_embedding=None,
            trace_id="trace-i7",
        )
        return _extract_strategy_intel_chars(caplog.records)

    async def test_none_reports_zero(self, loaders, db_session, caplog):
        chars = await self._invoke(loaders, db_session, caplog, None)
        assert chars == 0, "None strategy_intelligence must log strategy_intel=0"

    async def test_empty_string_reports_zero(self, loaders, db_session, caplog):
        chars = await self._invoke(loaders, db_session, caplog, "")
        assert chars == 0

    async def test_whitespace_only_reports_zero(self, loaders, db_session, caplog):
        # A whitespace-only value would otherwise render and contribute the
        # wrapper-tag envelope to ``len(optimize_msg)`` — the per-field
        # count must still be 0 because there is no real content.
        chars = await self._invoke(loaders, db_session, caplog, "   \n  \n")
        assert chars == 0

    async def test_real_content_reports_length(self, loaders, db_session, caplog):
        payload = "Top strategies: chain-of-thought (8.2 avg)"
        chars = await self._invoke(loaders, db_session, caplog, payload)
        assert chars == len(payload)
