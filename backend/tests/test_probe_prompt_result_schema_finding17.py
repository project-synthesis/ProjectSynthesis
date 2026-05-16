"""Finding 17 regression — ProbePromptResult schema backward compat.

Spec/finding: pre-fix ``GET /api/probes/{id}`` returned HTTP 500
``ValidationError`` for legacy probe rows whose ``prompt_results`` JSON
column contained ``raw_prompt`` keys instead of ``prompt_text``. The fix
adds ``validation_alias=AliasChoices("prompt_text", "raw_prompt")`` to
``ProbePromptResult.prompt_text`` so both keys deserialize cleanly.

Plan: post-v0.4.22 soak-gate Day 4/5 (2026-05-16) — surfaced when a
legacy row at ``id=24af8625e1dc...`` (created during T2 development as
a save-as-suite test fixture) couldn't be retrieved via the canonical
GET endpoint.
"""
from __future__ import annotations

import pytest


def test_probe_prompt_result_accepts_canonical_prompt_text_key() -> None:
    """The canonical key ``prompt_text`` must continue to work."""
    from app.schemas.probes import ProbePromptResult

    pr = ProbePromptResult(prompt_idx=0, prompt_text="hello")
    assert pr.prompt_text == "hello"
    assert pr.prompt_idx == 0


def test_probe_prompt_result_accepts_legacy_raw_prompt_key() -> None:
    """Finding 17 fix: legacy ``raw_prompt`` key must deserialize as
    ``prompt_text`` via ``AliasChoices``.

    Pre-fix this raised ``ValidationError: prompt_text Field required``
    and the wrapping ``GET /api/probes/{id}`` returned HTTP 500.
    """
    from app.schemas.probes import ProbePromptResult

    pr = ProbePromptResult.model_validate(
        {"prompt_idx": 1, "raw_prompt": "legacy"},
    )
    assert pr.prompt_text == "legacy"
    assert pr.prompt_idx == 1


def test_probe_prompt_result_canonical_key_wins_when_both_present() -> None:
    """Defense-in-depth: when BOTH keys are present (a transitional row),
    Pydantic ``AliasChoices`` resolves in declaration order — the
    canonical ``prompt_text`` takes precedence.
    """
    from app.schemas.probes import ProbePromptResult

    pr = ProbePromptResult.model_validate(
        {
            "prompt_idx": 2,
            "prompt_text": "canonical",
            "raw_prompt": "legacy",
        },
    )
    assert pr.prompt_text == "canonical"


def test_probe_prompt_result_missing_both_keys_raises() -> None:
    """When NEITHER key is present, Pydantic raises ``ValidationError``
    with a clear ``prompt_text`` missing-field signal — the fix did NOT
    weaken the field's required-ness.
    """
    from pydantic import ValidationError

    from app.schemas.probes import ProbePromptResult

    with pytest.raises(ValidationError) as exc_info:
        ProbePromptResult.model_validate({"prompt_idx": 3})
    assert "prompt_text" in str(exc_info.value)
