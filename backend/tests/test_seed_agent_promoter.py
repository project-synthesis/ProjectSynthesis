"""T3.2 + T3.5 seed-agent promoter unit tests.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.29-t3.2-t3.5-design.md`` §6.1.

14 tests pinning the eligibility-gate matrix, atomic-write contract,
top-K ranking, and refresh-algorithm correctness.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.config import AUTO_PROMOTE_THRESHOLD, AUTO_PROMOTE_TOP_K
from app.models import RunRow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_prompts_dir(tmp_path: Path) -> Path:
    """Sandbox PROMPTS_DIR — every test gets a fresh, clean directory."""
    d = tmp_path / "prompts"
    (d / "seed-agents").mkdir(parents=True)
    return d


@pytest.fixture
def promoter(tmp_prompts_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Construct SeedAgentPromoter pointing at the sandbox."""
    from app.services.seed_agent_promoter import SeedAgentPromoter

    # Real WriteQueue is overkill for unit tests; use the test stand-in
    # pattern from conftest.app_client (a serialized work executor).
    class _TestWriteQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            return await work(_FakeSession())

    class _FakeSession:
        """Minimal AsyncSession stand-in — supports get + commit only."""
        def __init__(self) -> None:
            self._rows: dict[str, RunRow] = {}

        async def get(self, model_cls, pk):
            return self._rows.get(pk)

        async def commit(self):
            pass

    queue = _TestWriteQueue()  # type: ignore[abstract]
    return SeedAgentPromoter(write_queue=queue, prompts_dir=tmp_prompts_dir)


def _make_row(
    run_id: str = "rr-test",
    *,
    mode: str = "topic_probe",
    status: str = "completed",
    aggregate_mean: float = 8.0,
    suggested_agent_name: str | None = "test-agent",
    promoted_at: str | None = None,
    prompt_results: list[dict] | None = None,
    topic: str = "test topic",
) -> RunRow:
    """Build a RunRow fixture for promoter testing — bypasses DB."""
    row = RunRow(
        id=run_id,
        mode=mode,
        status=status,
        prompts_generated=len(prompt_results or []),
        prompt_results=prompt_results or [
            {"raw_prompt": "Prompt A", "overall_score": 8.5},
            {"raw_prompt": "Prompt B", "overall_score": 8.2},
            {"raw_prompt": "Prompt C", "overall_score": 8.0},
            {"raw_prompt": "Prompt D", "overall_score": 6.0},
        ],
        aggregate={"mean_overall": aggregate_mean},
        topic=topic,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        topic_probe_meta={
            "scope": "**/*",
            "commit_sha": None,
            "grounding_mode": "codebase",
            "suggested_agent_name": suggested_agent_name,
            **({"promoted_at": promoted_at, "promoted_to": "old"} if promoted_at else {}),
        },
    )
    return row


# Tests 1-7 mock the DB read by monkey-patching async_session_factory to
# return a fake session whose .get(RunRow, id) returns the prebuilt row.
# Tests 8-12 also verify the file written (via tmp_prompts_dir).


async def _patch_db_with_row(monkeypatch: pytest.MonkeyPatch, row: RunRow | None) -> None:
    """Monkey-patch async_session_factory so promoter sees `row` for any get()."""
    import app.database as _db

    class _Ctx:
        async def __aenter__(self):
            return _Sess()

        async def __aexit__(self, *a):
            return False

    class _Sess:
        async def get(self, model_cls, pk):
            return row

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    monkeypatch.setattr(_db, "async_session_factory", lambda: _Ctx())


# ---------------------------------------------------------------------------
# Test 1: skipped_reason='run_not_found' when run_id is unknown
# ---------------------------------------------------------------------------


async def test_maybe_promote_skipped_run_not_found(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _patch_db_with_row(monkeypatch, None)
    result = await promoter.maybe_promote("rr-nonexistent")
    assert result.written is False
    assert result.skipped_reason == "run_not_found"


# ---------------------------------------------------------------------------
# Test 2: skipped_reason='wrong_mode' when not topic_probe
# ---------------------------------------------------------------------------


async def test_maybe_promote_skipped_wrong_mode(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(mode="replay_run")
    await _patch_db_with_row(monkeypatch, row)
    result = await promoter.maybe_promote("rr-test")
    assert result.written is False
    assert result.skipped_reason == "wrong_mode"


# ---------------------------------------------------------------------------
# Test 3: skipped_reason='not_completed' when status != completed
# ---------------------------------------------------------------------------


async def test_maybe_promote_skipped_not_completed(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(status="failed")
    await _patch_db_with_row(monkeypatch, row)
    result = await promoter.maybe_promote("rr-test")
    assert result.written is False
    assert result.skipped_reason == "not_completed"


# ---------------------------------------------------------------------------
# Test 4: skipped_reason='below_threshold' when mean_overall < threshold
# ---------------------------------------------------------------------------


async def test_maybe_promote_skipped_below_threshold(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(aggregate_mean=AUTO_PROMOTE_THRESHOLD - 0.1)
    await _patch_db_with_row(monkeypatch, row)
    result = await promoter.maybe_promote("rr-test")
    assert result.written is False
    assert result.skipped_reason == "below_threshold"


# ---------------------------------------------------------------------------
# Test 5: skipped_reason='no_agent_name' when suggested_agent_name missing
# ---------------------------------------------------------------------------


async def test_maybe_promote_skipped_no_agent_name(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(suggested_agent_name=None)
    await _patch_db_with_row(monkeypatch, row)
    result = await promoter.maybe_promote("rr-test")
    assert result.written is False
    assert result.skipped_reason == "no_agent_name"


# ---------------------------------------------------------------------------
# Test 6: skipped_reason='invalid_slug' for slugs failing regex; some valid pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected_reason",
    [
        # Invalid (rejected with invalid_slug)
        ("React-Testing", "invalid_slug"),            # uppercase
        ("react testing", "invalid_slug"),            # space
        ("-react", "invalid_slug"),                   # leading dash
        ("react-", "invalid_slug"),                   # trailing dash
        ("c++", "invalid_slug"),                      # special chars
        ("a" * 61, "invalid_slug"),                   # > 60 chars
        # Valid (no_agent_name check passes; would proceed to write)
        ("react-testing", None),
        ("react--testing", None),                     # consecutive dashes ALLOWED
        ("a", None),                                  # single char
    ],
)
async def test_maybe_promote_slug_validation(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    expected_reason: str | None,
) -> None:
    row = _make_row(suggested_agent_name=name)
    await _patch_db_with_row(monkeypatch, row)
    result = await promoter.maybe_promote("rr-test")
    if expected_reason == "invalid_slug":
        assert result.written is False
        assert result.skipped_reason == "invalid_slug"
    else:
        # Valid slug — should proceed past slug-validation gate.
        # We don't assert written=True here because the file-write path
        # is exercised by Test 8; only verify NOT short-circuited on slug.
        assert result.skipped_reason != "invalid_slug"


# ---------------------------------------------------------------------------
# Test 7: skipped_reason='already_promoted' when topic_probe_meta.promoted_at set
# ---------------------------------------------------------------------------


async def test_maybe_promote_skipped_already_promoted(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(promoted_at="2026-05-18T10:00:00+00:00")
    await _patch_db_with_row(monkeypatch, row)
    result = await promoter.maybe_promote("rr-test")
    assert result.written is False
    assert result.skipped_reason == "already_promoted"


# ---------------------------------------------------------------------------
# Test 8: happy path — frontmatter shape matches spec §3.4
# ---------------------------------------------------------------------------


async def test_maybe_promote_happy_path_writes_correct_frontmatter(
    promoter: Any,
    tmp_prompts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(suggested_agent_name="happy-agent", topic="happy topic")
    await _patch_db_with_row(monkeypatch, row)

    result = await promoter.maybe_promote("rr-test")
    assert result.written is True
    assert result.agent_name == "happy-agent"
    assert result.examples_count == AUTO_PROMOTE_TOP_K

    target = tmp_prompts_dir / "seed-agents" / "happy-agent.md"
    assert target.exists()
    content = target.read_text()
    assert "name: happy-agent" in content
    assert "description: Auto-promoted from probe rr-test on topic 'happy topic'" in content
    assert "promoted_from_run_id: rr-test" in content
    # promoted_at and last_refreshed_at must be ISO 8601 UTC
    assert re.search(r"promoted_at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", content)
    assert re.search(r"last_refreshed_at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", content)
    assert "enabled: true" in content


# ---------------------------------------------------------------------------
# Test 9: happy path — Examples section ranked by overall_score desc
# ---------------------------------------------------------------------------


async def test_maybe_promote_examples_ranked_desc(
    promoter: Any,
    tmp_prompts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(
        suggested_agent_name="ranked-agent",
        prompt_results=[
            # below-threshold not relevant at K-rank time
            {"raw_prompt": "Mid prompt", "overall_score": 7.5},
            {"raw_prompt": "Top prompt", "overall_score": 9.0},
            # K=3 so this drops
            {"raw_prompt": "Worst prompt", "overall_score": 6.0},
            {"raw_prompt": "Second prompt", "overall_score": 8.5},
        ],
    )
    await _patch_db_with_row(monkeypatch, row)
    await promoter.maybe_promote("rr-test")

    content = (tmp_prompts_dir / "seed-agents" / "ranked-agent.md").read_text()
    # Top 3 ranked desc: 9.0, 8.5, 7.5
    top_idx = content.index("Top prompt")
    second_idx = content.index("Second prompt")
    mid_idx = content.index("Mid prompt")
    assert top_idx < second_idx < mid_idx
    # Worst prompt (6.0) must NOT appear — only top-K
    assert "Worst prompt" not in content


# ---------------------------------------------------------------------------
# Test 10: happy path — fewer than K qualifying prompts → renders with actual N
# ---------------------------------------------------------------------------


async def test_maybe_promote_fewer_than_k_prompts(
    promoter: Any,
    tmp_prompts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _make_row(
        suggested_agent_name="small-agent",
        prompt_results=[
            {"raw_prompt": "Only prompt", "overall_score": 8.0},
        ],
    )
    await _patch_db_with_row(monkeypatch, row)
    result = await promoter.maybe_promote("rr-test")
    assert result.written is True
    assert result.examples_count == 1

    content = (tmp_prompts_dir / "seed-agents" / "small-agent.md").read_text()
    assert "## Examples (top 1 from probe rr-test" in content
    assert "Only prompt" in content


# ---------------------------------------------------------------------------
# Test 11: happy path stamps topic_probe_meta.promoted_at + promoted_to
# ---------------------------------------------------------------------------


async def test_maybe_promote_stamps_source_row(
    promoter: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _stamp_promoted is called with NEW dict + attribute reassignment.

    We check via the test WriteQueue: when promoter calls submit(_work, ...),
    the _work callback mutates a row's topic_probe_meta. We capture that.
    """
    stamped: dict[str, Any] = {}

    row = _make_row(suggested_agent_name="stamped-agent")

    class _Sess:
        async def get(self, model_cls, pk):
            return row

        async def commit(self):
            stamped["meta"] = row.topic_probe_meta

        async def refresh(self, obj):
            pass

    class _Ctx:
        async def __aenter__(self):
            return _Sess()

        async def __aexit__(self, *a):
            return False

    import app.database as _db
    monkeypatch.setattr(_db, "async_session_factory", lambda: _Ctx())

    # Override the promoter's WriteQueue to drive the _work callback through _Sess
    captured_work: list = []

    class _CapturingQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            captured_work.append((work, operation_label))
            return await work(_Sess())

    promoter._write_queue = _CapturingQueue()  # type: ignore[assignment]

    await promoter.maybe_promote("rr-test")

    # Verify _stamp_promoted was submitted under the correct op label
    assert any(label == "seed_agent_promoter.stamp_promoted" for _, label in captured_work)
    # Verify topic_probe_meta now contains promoted_at + promoted_to
    assert "promoted_at" in (stamped.get("meta") or {})
    assert stamped["meta"]["promoted_to"] == "stamped-agent"


# ---------------------------------------------------------------------------
# Test 12: atomic write — tempfile is the only intermediate state
# ---------------------------------------------------------------------------


async def test_maybe_promote_atomic_write_no_partial_file(
    promoter: Any,
    tmp_prompts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asserts the tempfile-then-rename pattern lands a complete file.

    Direct test: pre-write a partial file at the target path, then verify
    the promoter REPLACES it (via os.replace) rather than appending. The
    final content must match the fully-rendered output, not the prior
    partial.
    """
    row = _make_row(suggested_agent_name="atomic-agent")
    await _patch_db_with_row(monkeypatch, row)

    target = tmp_prompts_dir / "seed-agents" / "atomic-agent.md"
    target.write_text("PARTIAL CONTENT", encoding="utf-8")

    await promoter.maybe_promote("rr-test")
    final = target.read_text()
    assert "PARTIAL" not in final
    assert "name: atomic-agent" in final


# ---------------------------------------------------------------------------
# Test 13: refresh happy path — re-renders Examples section + last_refreshed_at
# ---------------------------------------------------------------------------


async def test_refresh_happy_path(
    promoter: Any,
    tmp_prompts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end refresh: pre-promote a file, then refresh it; verify the
    Examples section is rewritten and last_refreshed_at advances.
    """
    # First promotion
    row = _make_row(suggested_agent_name="refresh-agent")
    await _patch_db_with_row(monkeypatch, row)
    await promoter.maybe_promote("rr-test")

    target = tmp_prompts_dir / "seed-agents" / "refresh-agent.md"
    first_content = target.read_text()
    first_refreshed_match = re.search(r"last_refreshed_at: ([^\n]+)", first_content)
    assert first_refreshed_match is not None
    first_ts = first_refreshed_match.group(1)

    # Mutate the source row's prompts to simulate a later refresh window
    row.prompt_results = [
        {"raw_prompt": "Brand new top prompt", "overall_score": 9.9},
        {"raw_prompt": "Brand new mid prompt", "overall_score": 8.5},
    ]
    # The fake session returns the SAME row instance — so refresh sees mutations.

    # Wait ~1s to ensure the refreshed timestamp differs from promoted_at
    import asyncio
    await asyncio.sleep(1.1)

    result = await promoter.refresh("refresh-agent")
    assert result.refreshed is True
    assert result.examples_count == 2
    assert result.source_run_id == "rr-test"

    second_content = target.read_text()
    second_refreshed_match = re.search(r"last_refreshed_at: ([^\n]+)", second_content)
    assert second_refreshed_match is not None
    second_ts = second_refreshed_match.group(1)
    assert second_ts != first_ts
    # New examples present
    assert "Brand new top prompt" in second_content
    # Body content outside the Examples section preserved
    assert "You are a prompt generation agent for" in second_content


# ---------------------------------------------------------------------------
# Test 14: refresh error cases — file_not_found, no_source_run, source_run_deleted
# ---------------------------------------------------------------------------


async def test_refresh_error_paths(
    promoter: Any,
    tmp_prompts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 14a: file_not_found
    result = await promoter.refresh("does-not-exist-anywhere")
    assert result.refreshed is False
    assert result.skipped_reason == "file_not_found"

    # 14b: no_source_run — file exists but frontmatter missing promoted_from_run_id
    target = tmp_prompts_dir / "seed-agents" / "orphan-agent.md"
    target.write_text(
        "---\nname: orphan-agent\nenabled: true\n---\n\nbody without provenance.\n",
        encoding="utf-8",
    )
    result = await promoter.refresh("orphan-agent")
    assert result.refreshed is False
    assert result.skipped_reason == "no_source_run"

    # 14c: source_run_deleted — frontmatter has promoted_from_run_id but DB returns None
    target = tmp_prompts_dir / "seed-agents" / "deleted-source-agent.md"
    target.write_text(
        "---\nname: deleted-source-agent\npromoted_from_run_id: rr-gone\nenabled: true\n---\n\nbody.\n",
        encoding="utf-8",
    )
    await _patch_db_with_row(monkeypatch, None)
    result = await promoter.refresh("deleted-source-agent")
    assert result.refreshed is False
    assert result.skipped_reason == "source_run_deleted"
    assert result.source_run_id == "rr-gone"
