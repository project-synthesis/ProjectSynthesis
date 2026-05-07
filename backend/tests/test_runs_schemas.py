"""Tests for RunRequest, RunResult, RunListResponse Pydantic schemas + GeneratorResult dataclass."""
from __future__ import annotations

import pytest


def test_run_request_accepts_topic_probe_payload() -> None:
    from app.schemas.runs import RunRequest
    req = RunRequest(mode="topic_probe", payload={"topic": "test", "scope": "**/*"})
    assert req.mode == "topic_probe"
    assert req.payload["topic"] == "test"


def test_run_request_accepts_seed_agent_payload() -> None:
    from app.schemas.runs import RunRequest
    req = RunRequest(mode="seed_agent", payload={"project_description": "x", "prompt_count": 30})
    assert req.mode == "seed_agent"


def test_run_request_rejects_unknown_mode() -> None:
    from pydantic import ValidationError

    from app.schemas.runs import RunRequest
    with pytest.raises(ValidationError):
        RunRequest(mode="invalid_mode", payload={})


def test_run_result_accepts_4_status_values() -> None:
    from datetime import datetime

    from app.schemas.runs import RunResult
    base = dict(
        id="r1", mode="topic_probe",
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        prompts_generated=3, prompt_results=[], aggregate={},
        taxonomy_delta={}, final_report="", topic="x", intent_hint="explore",
        topic_probe_meta={}, seed_agent_meta=None, project_id=None,
        repo_full_name="x/y", suite_id=None, error=None,
    )
    for status in ("running", "completed", "failed", "partial"):
        r = RunResult(**base, status=status)
        assert r.status == status


def test_run_list_response_pagination_envelope() -> None:
    from app.schemas.runs import RunListResponse
    resp = RunListResponse(
        total=100, count=50, offset=0, items=[], has_more=True, next_offset=50,
    )
    assert resp.has_more is True
    assert resp.next_offset == 50


def test_generator_result_dataclass_terminal_status_field() -> None:
    """GeneratorResult is the service-layer dataclass, distinct from RunResult Pydantic."""
    from app.services.generators.base import GeneratorResult
    res = GeneratorResult(
        terminal_status="partial",
        prompts_generated=5,
        prompt_results=[{"id": "p1"}],
        aggregate={"prompts_optimized": 3, "prompts_failed": 2, "summary": "x"},
        taxonomy_delta={"domains_touched": ["a"], "clusters_created": 1},
        final_report=None,
    )
    assert res.terminal_status == "partial"
    assert res.aggregate["prompts_failed"] == 2


def test_generator_result_terminal_status_rejects_running() -> None:
    """GeneratorResult.terminal_status is Literal['completed', 'partial', 'failed'].

    RunOrchestrator alone owns the 'running' status; generators must never
    return 'running' as terminal."""
    import typing

    from app.services.generators.base import GeneratorResult
    # The Literal type is enforced statically; at runtime, the dataclass accepts
    # any string. This test is a compile-time-style check via type inspection.
    hints = typing.get_type_hints(GeneratorResult)
    # If using Literal: typing.get_args returns the allowed values
    args = typing.get_args(hints["terminal_status"])
    assert set(args) == {"completed", "partial", "failed"}
