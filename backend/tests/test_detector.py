"""Tests for provider detection timeout behavior (N1)."""
import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.detector import (
    ProviderNotAvailableError,
    _detect_provider_inner,
    detect_provider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_without_claudecode() -> dict:
    """Build os.environ copy with CLAUDECODE removed so CLI probe runs."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


@pytest.fixture(autouse=True)
def _disable_testing_mode():
    """Ensure detect_provider() runs real detection logic, not MockProvider.

    The integration test conftest sets TESTING=true in os.environ which
    persists for the whole process.  The pydantic settings instance caches
    this value so we must patch it on the settings object directly.
    """
    from app.config import settings
    with patch.object(settings, "TESTING", False):
        yield


def _mock_proc(communicate_sleep: float = 30.0) -> MagicMock:
    """Return an AsyncMock subprocess that hangs on communicate() by default."""
    proc = MagicMock()
    proc.returncode = None

    async def _slow_communicate():
        await asyncio.sleep(communicate_sleep)
        return b"", b""

    proc.communicate = _slow_communicate
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# N1.1 — per-probe 5s timeout fires before the 10s outer cap
# ---------------------------------------------------------------------------

async def test_detect_provider_total_timeout_under_10s():
    """detect_provider() raises within 10 seconds when CLI probe hangs.

    The 5s per-probe timeout fires first; total detection stays well under 10s.
    """
    _mock_proc(communicate_sleep=30.0)

    async def slow_create(*args, **kwargs):
        # Simulate a subprocess that takes forever to start
        await asyncio.sleep(30)

    with patch.dict(os.environ, _env_without_claudecode(), clear=True):
        with patch("asyncio.create_subprocess_exec", side_effect=slow_create):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                from app.config import settings

                with patch.object(settings, "ANTHROPIC_API_KEY", None):
                    start = time.monotonic()
                    with pytest.raises(ProviderNotAvailableError):
                        await detect_provider()
                    elapsed = time.monotonic() - start

    assert elapsed < 10.5, f"Detection took {elapsed:.1f}s — must finish within 10s total"


# ---------------------------------------------------------------------------
# N1.2 — per-probe timeout covers communicate(), not just subprocess creation
# ---------------------------------------------------------------------------

async def test_detect_provider_probe_timeout_covers_communicate():
    """Per-probe timeout bounds _probe_claude_version (creation + communicate).

    A subprocess that starts instantly but hangs on communicate() is still
    caught by the 5s inner timeout, not just the 10s outer cap.
    """
    proc = _mock_proc(communicate_sleep=30.0)

    async def fast_create(*args, **kwargs):
        return proc  # starts immediately; communicate() hangs

    with patch.dict(os.environ, _env_without_claudecode(), clear=True):
        with patch("asyncio.create_subprocess_exec", side_effect=fast_create):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                from app.config import settings

                with patch.object(settings, "ANTHROPIC_API_KEY", None):
                    start = time.monotonic()
                    with pytest.raises(ProviderNotAvailableError):
                        await detect_provider()
                    elapsed = time.monotonic() - start

    # Should finish in ~5s (per-probe), well before the 10s outer cap
    assert elapsed < 7.0, (
        f"Detection took {elapsed:.1f}s — per-probe timeout should fire at ~5s"
    )


# ---------------------------------------------------------------------------
# N1.3 — outer 10s timeout wiring
# ---------------------------------------------------------------------------

async def test_detect_provider_outer_timeout_wires_correctly():
    """detect_provider() uses asyncio.wait_for(timeout=10.0) as outer cap.

    Verified by patching _detect_provider_inner to hang and shortening the
    effective outer timeout to 0.2s, which should still raise ProviderNotAvailableError.
    """
    cancelled = asyncio.Event()

    async def _hanging_inner():
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    original_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro, timeout):
        # Collapse the outer 10s cap to 0.2s; pass inner timeouts through
        effective = 0.2 if timeout == 10.0 else timeout
        return await original_wait_for(coro, effective)

    with patch("app.providers.detector._detect_provider_inner", side_effect=_hanging_inner):
        with patch("app.providers.detector.asyncio.wait_for", side_effect=_fast_wait_for):
            start = time.monotonic()
            with pytest.raises(ProviderNotAvailableError):
                await detect_provider()
            elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"Outer timeout wiring broken; took {elapsed:.1f}s"
    assert cancelled.is_set(), "_detect_provider_inner was not cancelled by timeout"


# ---------------------------------------------------------------------------
# N1.4 — CLAUDECODE env skips CLI probe
# ---------------------------------------------------------------------------

async def test_detect_provider_skips_cli_when_claudecode_env_set():
    """When CLAUDECODE is set, CLI probe is skipped without touching subprocess."""
    with patch.dict(os.environ, {"CLAUDECODE": "1"}):
        from app.config import settings

        with patch.object(settings, "ANTHROPIC_API_KEY", None):
            # No subprocess patch needed — CLI probe must be bypassed entirely
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                with pytest.raises(ProviderNotAvailableError):
                    await detect_provider()
                mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# N1.5 — timeout warning is logged
# ---------------------------------------------------------------------------

async def test_detect_provider_probe_timeout_logs_warning(caplog):
    """CLI probe timeout emits a WARNING to the detector logger."""
    import logging

    async def slow_create(*args, **kwargs):
        await asyncio.sleep(30)

    with patch.dict(os.environ, _env_without_claudecode(), clear=True):
        with patch("asyncio.create_subprocess_exec", side_effect=slow_create):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                from app.config import settings

                with patch.object(settings, "ANTHROPIC_API_KEY", None):
                    with caplog.at_level(logging.WARNING, logger="app.providers.detector"):
                        with pytest.raises(ProviderNotAvailableError):
                            await detect_provider()

    assert any(
        "timed" in r.message.lower() or "10-second" in r.message.lower()
        for r in caplog.records
    ), f"Expected timeout warning; got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# P1 — ~/.claude/ credential directory check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_provider_skips_cli_when_no_claude_dir(monkeypatch):
    """CLI detected on PATH but ~/.claude/ missing → skip to API probe."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch(
            "app.providers.detector._probe_claude_version",
            new=AsyncMock(return_value=(b"claude 1.0.0", 0)),
        ),
        patch("pathlib.Path.is_dir", return_value=False),  # no ~/.claude/
    ):
        with patch.dict(os.environ, _env_without_claudecode(), clear=True):
            from app.config import settings
            with patch.object(settings, "ANTHROPIC_API_KEY", None):
                with pytest.raises(ProviderNotAvailableError):
                    await detect_provider()


@pytest.mark.asyncio
async def test_detect_provider_uses_cli_when_claude_dir_exists(monkeypatch):
    """CLI detected on PATH AND ~/.claude/ exists → use ClaudeCLIProvider."""
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch(
            "app.providers.detector._probe_claude_version",
            new=AsyncMock(return_value=(b"claude 1.0.0", 0)),
        ),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("app.providers.claude_cli.ClaudeCLIProvider.__init__", return_value=None),
    ):
        with patch.dict(os.environ, _env_without_claudecode(), clear=True):
            provider = await detect_provider()
            assert provider.name == "claude_cli"


# ---------------------------------------------------------------------------
# N2 — AnthropicAPIProvider detection path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_provider_uses_anthropic_api_when_cli_unavailable():
    """When claude CLI is absent and ANTHROPIC_API_KEY is set, use AnthropicAPIProvider."""
    from app.providers.anthropic_api import AnthropicAPIProvider

    with (
        patch("shutil.which", return_value=None),  # no claude binary
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}),
    ):
        from app.config import settings
        with patch.object(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key"):
            with patch.dict(os.environ, _env_without_claudecode(), clear=False):
                provider = await _detect_provider_inner()

    assert isinstance(provider, AnthropicAPIProvider)
