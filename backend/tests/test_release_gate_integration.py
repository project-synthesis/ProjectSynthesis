"""T3.1 release-gate integration tests.

Spec: ``docs/superpowers/specs/2026-05-19-t3.1-release-gate-design.md`` §6.2.

Test 9: end-to-end firing-suite flow.
Test 10: subprocess release.sh --dry-run against a live uvicorn-backed
backend; SKIPs gracefully if the backend fixture can't start within 30s.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.main import app
from app.models import RunRow, ValidationSuite


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("anyio_backend")]


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Backend-running fixture — session-scoped uvicorn on a free port
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_backend() -> Generator[str, None, None]:
    """Start uvicorn on a free port in a daemon thread; yield the URL.

    SKIPs the dependent test if startup exceeds 30 seconds. Releases socket
    cleanly on teardown.
    """
    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for readiness (up to 30s)
    base_url = f"http://127.0.0.1:{port}"
    start = time.monotonic()
    while time.monotonic() - start < 30:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        server.should_exit = True
        pytest.skip("backend startup timeout (>30s) — manual verification per spec §5.3")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Test 9 — end-to-end firing-suite flow
# ---------------------------------------------------------------------------


async def test_end_to_end_firing_suite_surfaces_in_release_gates(
    db_session: Any,
) -> None:
    """Acceptance #5, #6 — create suite → flag → seed firing replay → query gates.

    Uses TestClient against the real DB session (per-test) to walk the full
    HTTP path without needing the uvicorn fixture.
    """
    client = TestClient(app)

    suite = ValidationSuite(
        id="s-integration-firing",
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores={
            "mean_overall": 8.0, "p5_overall": 5.0, "p50_overall": 8.0,
            "p95_overall": 9.5, "per_prompt": [], "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="integration-firing-suite",
        created_at=datetime.now(UTC),
    )
    db_session.add(suite)
    await db_session.commit()

    # Step 1: flag as gate
    resp = client.post(
        f"/api/suites/{suite.id}/release-gate",
        json={"enabled": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_release_gate"] is True

    # Step 2: seed firing replay (latest_mean=6.0 < baseline 8.0 - tolerance 0.5 = 7.5)
    firing_replay = RunRow(
        id="rr-integration-firing",
        mode="replay_run",
        status="completed",
        suite_id=suite.id,
        prompt_results=[],
        prompts_generated=0,
        aggregate={"mean_overall": 6.0, "p5_overall": 5.0, "p50_overall": 6.0, "p95_overall": 7.0},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db_session.add(firing_replay)
    await db_session.commit()

    # Step 3: query gates endpoint
    resp = client.get("/api/suites/release-gates")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    assert items[0]["suite_id"] == suite.id
    assert items[0]["alarm_state"] == "firing"
    assert items[0]["delta_abs"] == pytest.approx(-2.0)


# ---------------------------------------------------------------------------
# Test 10 — subprocess release.sh --dry-run against live backend
# ---------------------------------------------------------------------------


def test_release_sh_dry_run_blocks_on_firing(
    live_backend: str,
    db_session: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance #7, #8, #14, #15 — release.sh exits 1 on firing; --skip-release-gates exits 0.

    Patches release.sh's hardcoded ``localhost:8000`` to the live_backend URL
    via env var; falls back to manual verification (acceptance #15) if the
    fixture can't start.
    """
    # NOTE: release.sh hardcodes http://localhost:8000. For this test we run a
    # tiny shim script that copies release.sh into a tmp dir and patches the
    # URL via sed, then invokes it from the worktree root.
    import tempfile

    release_sh = REPO_ROOT / "scripts" / "release.sh"
    if not release_sh.exists():
        pytest.skip(f"scripts/release.sh not found at {release_sh}")

    with tempfile.TemporaryDirectory() as tmp:
        shim = Path(tmp) / "release_shim.sh"
        original = release_sh.read_text()
        patched = original.replace(
            "http://localhost:8000/api/suites/release-gates",
            f"{live_backend}/api/suites/release-gates",
        )
        shim.write_text(patched)
        shim.chmod(0o755)

        # Seed 1 firing suite
        firing_suite = ValidationSuite(
            id="s-subproc-firing",
            source_run_id=None,
            prompts_snapshot=[],
            baseline_scores={
                "mean_overall": 8.0, "p5_overall": 5.0, "p50_overall": 8.0,
                "p95_overall": 9.5, "per_prompt": [], "task_type_distribution": {},
            },
            tolerance_abs=0.5,
            label="subproc-firing",
            is_release_gate=True,
            created_at=datetime.now(UTC),
        )
        firing_replay = RunRow(
            id="rr-subproc-firing",
            mode="replay_run",
            status="completed",
            suite_id="s-subproc-firing",
            prompt_results=[],
        prompts_generated=0,
            aggregate={"mean_overall": 6.0, "p5_overall": 5.0, "p50_overall": 6.0, "p95_overall": 7.0},
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        db_session.add_all([firing_suite, firing_replay])
        # Note: db_session is async; commit is awaited in async tests. This
        # sync test uses asyncio.run to commit.
        import asyncio
        asyncio.run(db_session.commit())

        # Run shim with --dry-run; expect exit 1
        result = subprocess.run(
            ["bash", str(shim), "--dry-run"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 1, (
            f"Expected exit 1 (firing suite); got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "subproc-firing" in (result.stdout + result.stderr), (
            f"Expected failing-suite label in output.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Run shim with --dry-run --skip-release-gates; expect exit 0
        result_skip = subprocess.run(
            ["bash", str(shim), "--dry-run", "--skip-release-gates"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result_skip.returncode == 0, (
            f"Expected exit 0 with --skip-release-gates; got {result_skip.returncode}.\n"
            f"stdout: {result_skip.stdout}\nstderr: {result_skip.stderr}"
        )
        assert "SKIPPED" in (result_skip.stdout + result_skip.stderr), (
            "Expected SKIPPED banner in output"
        )
