"""Finding 20 regression — _build_prompts_snapshot reads both prompt keys.

Pre-fix: ``_build_prompts_snapshot`` read only ``raw_prompt`` from
``RunRow.prompt_results`` rows. ``TopicProbeGenerator`` writes
``prompt_text`` (canonical, Finding 16+17), so topic-probe-sourced
suites had every snapshot row carrying ``raw_prompt=""``. The
downstream replay surface, the UI prompt display, and the regression
alarm calibration all saw an empty string instead of the real prompt.

Fix: read ``raw_prompt`` OR ``prompt_text`` (canonical wins on tie),
falling back to empty string only when both are absent.
"""
from __future__ import annotations


def test_build_prompts_snapshot_reads_raw_prompt_key() -> None:
    """ReplayRunGenerator stores prompts under ``raw_prompt`` —
    snapshot builder must preserve them.
    """
    from app.services.validation_suite_service import _build_prompts_snapshot

    out = _build_prompts_snapshot(
        [{"raw_prompt": "from-replay", "intent_label": "test"}],
    )
    assert out[0]["raw_prompt"] == "from-replay"


def test_build_prompts_snapshot_reads_prompt_text_key() -> None:
    """Finding 20 fix: TopicProbeGenerator stores prompts under
    ``prompt_text`` — snapshot builder must read them.
    """
    from app.services.validation_suite_service import _build_prompts_snapshot

    out = _build_prompts_snapshot(
        [{"prompt_text": "from-topic-probe", "intent_label": "test"}],
    )
    assert out[0]["raw_prompt"] == "from-topic-probe", (
        f"Finding 20 regression: prompt_text key not read, got "
        f"raw_prompt={out[0]['raw_prompt']!r}"
    )


def test_build_prompts_snapshot_raw_prompt_wins_when_both_present() -> None:
    """When both keys are present (a transitional row), ``raw_prompt``
    takes precedence — preserves the canonical key's authority.
    """
    from app.services.validation_suite_service import _build_prompts_snapshot

    out = _build_prompts_snapshot(
        [{"raw_prompt": "canonical", "prompt_text": "alt"}],
    )
    assert out[0]["raw_prompt"] == "canonical"


def test_build_prompts_snapshot_empty_when_both_missing() -> None:
    """When neither key is present, falls back to empty string —
    preserves the original tolerant-default semantics.
    """
    from app.services.validation_suite_service import _build_prompts_snapshot

    out = _build_prompts_snapshot([{"intent_label": "test"}])
    assert out[0]["raw_prompt"] == ""
