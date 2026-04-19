"""Tests for app.providers.capabilities — pure model capability helpers.

Locks the effort/thinking/label matrix per Anthropic docs:
  - Haiku: NO effort support (CLI skips --effort entirely). No thinking.
  - Sonnet 4.5: low/medium/high (errors on max).
  - Sonnet 4.6: low/medium/high/max.
  - Opus 4.5 / 4.6: low/medium/high/max.
  - Opus 4.7: low/medium/high/xhigh/max (+ display:summarized thinking default).
"""

from __future__ import annotations

import pytest

from app.providers.capabilities import (
    effort_support,
    model_label,
    model_tier,
    model_version,
    supports_thinking,
    tier_display_name,
)


class TestEffortSupport:
    def test_opus_4_7_full_matrix(self):
        assert effort_support("claude-opus-4-7") == ["low", "medium", "high", "xhigh", "max"]

    def test_opus_4_6_has_max_but_no_xhigh(self):
        assert effort_support("claude-opus-4-6") == ["low", "medium", "high", "max"]

    def test_opus_4_5_has_max_but_no_xhigh(self):
        assert effort_support("claude-opus-4-5") == ["low", "medium", "high", "max"]

    def test_sonnet_4_6_has_max_but_no_xhigh(self):
        assert effort_support("claude-sonnet-4-6") == ["low", "medium", "high", "max"]

    def test_sonnet_4_5_no_max_no_xhigh(self):
        assert effort_support("claude-sonnet-4-5") == ["low", "medium", "high"]

    def test_haiku_4_5_empty(self):
        """Haiku: no effort support at all. CLI skips --effort entirely."""
        assert effort_support("claude-haiku-4-5") == []

    def test_haiku_case_insensitive(self):
        assert effort_support("Claude-Haiku-4-5") == []

    def test_unknown_model_conservative_default(self):
        """Unknown models default to low/medium/high (no max, no xhigh)."""
        assert effort_support("claude-future-99-9") == ["low", "medium", "high"]


class TestSupportsThinking:
    def test_haiku_no_thinking(self):
        assert supports_thinking("claude-haiku-4-5") is False

    def test_opus_4_7_supports_thinking(self):
        assert supports_thinking("claude-opus-4-7") is True

    def test_opus_4_6_supports_thinking(self):
        assert supports_thinking("claude-opus-4-6") is True

    def test_sonnet_4_6_supports_thinking(self):
        assert supports_thinking("claude-sonnet-4-6") is True

    def test_case_insensitive(self):
        assert supports_thinking("Claude-Haiku-4-5") is False


class TestModelLabel:
    def test_opus_4_7_label(self):
        assert model_label("claude-opus-4-7") == "Opus 4.7"

    def test_sonnet_4_6_label(self):
        assert model_label("claude-sonnet-4-6") == "Sonnet 4.6"

    def test_haiku_4_5_label(self):
        assert model_label("claude-haiku-4-5") == "Haiku 4.5"

    def test_opus_without_claude_prefix(self):
        assert model_label("opus-4-7") == "Opus 4.7"

    def test_unknown_tier_titlecased(self):
        assert model_label("claude-future-99-9") == "Future 99.9"


class TestModelVersion:
    def test_opus_4_7(self):
        assert model_version("claude-opus-4-7") == "4.7"

    def test_sonnet_4_6(self):
        assert model_version("claude-sonnet-4-6") == "4.6"

    def test_haiku_4_5(self):
        assert model_version("claude-haiku-4-5") == "4.5"

    def test_no_version_returns_empty(self):
        assert model_version("claude-opus") == ""


class TestModelTier:
    def test_opus(self):
        assert model_tier("claude-opus-4-7") == "opus"

    def test_sonnet(self):
        assert model_tier("claude-sonnet-4-6") == "sonnet"

    def test_haiku(self):
        assert model_tier("claude-haiku-4-5") == "haiku"

    def test_tier_alias_input(self):
        """Short tier strings ('opus', 'sonnet', 'haiku') resolve to themselves."""
        assert model_tier("opus") == "opus"
        assert model_tier("sonnet") == "sonnet"
        assert model_tier("haiku") == "haiku"

    def test_unknown_returns_none(self):
        assert model_tier("gpt-4") is None


class TestTierDisplayName:
    def test_opus(self):
        assert tier_display_name("opus") == "Opus"

    def test_sonnet(self):
        assert tier_display_name("sonnet") == "Sonnet"

    def test_haiku(self):
        assert tier_display_name("haiku") == "Haiku"


class TestConsistencyWithProviderGates:
    """Lock the invariant: providers must degrade xhigh→high for non-Opus-4.7.

    `effort_support` is the single source of truth — `xhigh` appears ONLY in
    Opus 4.7's list. If this test fails, either effort_support or the provider
    xhigh gate has drifted.
    """

    @pytest.mark.parametrize("model", [
        "claude-opus-4-6", "claude-opus-4-5",
        "claude-sonnet-4-6", "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ])
    def test_xhigh_not_in_support_list(self, model: str):
        assert "xhigh" not in effort_support(model)

    def test_xhigh_only_in_opus_4_7(self):
        assert "xhigh" in effort_support("claude-opus-4-7")
