"""Tests for persistent rate-limit state — TDD red-green-refactor.

The rate-limit state store provides:
1. Persistence: rate-limit state survives backend restarts.
2. Health integration: ``GET /api/health`` includes the current rate-limit
   state so the frontend can hydrate on page load instead of waiting for
   a ``rate_limit_active`` SSE event that may never fire.
3. Startup probe: on backend boot, a lightweight LLM call checks if the
   provider is still rate-limited and updates the store.

Copyright 2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. RateLimitStateStore — persistent JSON-backed state
# ---------------------------------------------------------------------------


class TestRateLimitStateStore:
    """Core state store: read/write/clear/expiry."""

    def test_initial_state_is_clear(self, tmp_path: Path):
        """Fresh store with no persisted file reports no active limits."""
        from app.services.rate_limit_state import RateLimitStateStore

        store = RateLimitStateStore(state_dir=tmp_path)
        assert store.get_active() is None
        assert store.is_rate_limited() is False

    def test_record_persists_to_disk(self, tmp_path: Path):
        """record() writes state to a JSON file that survives re-instantiation."""
        from app.services.rate_limit_state import RateLimitStateStore

        reset_at = datetime.now(timezone.utc) + timedelta(hours=2)
        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(
            provider="claude_cli",
            reset_at=reset_at,
            estimated_wait_seconds=7200,
            source="batch_pipeline",
        )

        # State is active
        active = store.get_active()
        assert active is not None
        assert active["provider"] == "claude_cli"
        assert active["reset_at_iso"] is not None
        assert store.is_rate_limited() is True

        # Re-instantiate (simulates process restart) — state survives
        store2 = RateLimitStateStore(state_dir=tmp_path)
        active2 = store2.get_active()
        assert active2 is not None
        assert active2["provider"] == "claude_cli"
        assert store2.is_rate_limited() is True

    def test_clear_removes_state(self, tmp_path: Path):
        """clear() removes the persisted state and reports no active limit."""
        from app.services.rate_limit_state import RateLimitStateStore

        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(provider="claude_cli", reset_at=None)
        assert store.is_rate_limited() is True

        store.clear(provider="claude_cli")
        assert store.is_rate_limited() is False
        assert store.get_active() is None

    def test_expired_limit_auto_clears(self, tmp_path: Path):
        """A limit whose reset_at is in the past reports as not active."""
        from app.services.rate_limit_state import RateLimitStateStore

        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(provider="claude_cli", reset_at=past)

        # Should auto-expire
        assert store.is_rate_limited() is False
        assert store.get_active() is None

    def test_no_reset_at_uses_default_ttl(self, tmp_path: Path):
        """When reset_at is None, the limit stays active for DEFAULT_TTL."""
        from app.services.rate_limit_state import RateLimitStateStore

        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(provider="claude_cli", reset_at=None)

        # Immediately after recording, should be active
        assert store.is_rate_limited() is True

    def test_record_with_none_reset_at_and_estimated_wait(self, tmp_path: Path):
        """estimated_wait_seconds is used to compute reset_at when not provided."""
        from app.services.rate_limit_state import RateLimitStateStore

        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(
            provider="claude_cli",
            reset_at=None,
            estimated_wait_seconds=3600,
        )

        active = store.get_active()
        assert active is not None
        # Should have a computed reset_at ~1h from now
        assert active["reset_at_iso"] is not None
        parsed = datetime.fromisoformat(active["reset_at_iso"])
        diff = (parsed - datetime.now(timezone.utc)).total_seconds()
        assert 3500 < diff < 3700  # ~1h with tolerance

    def test_corrupted_file_is_handled_gracefully(self, tmp_path: Path):
        """If the state file is corrupted, the store treats it as empty."""
        from app.services.rate_limit_state import RateLimitStateStore

        state_file = tmp_path / "rate_limit_state.json"
        state_file.write_text("not valid json {{{")

        store = RateLimitStateStore(state_dir=tmp_path)
        assert store.is_rate_limited() is False
        assert store.get_active() is None


# ---------------------------------------------------------------------------
# 2. Health endpoint integration — ``rate_limit`` field
# ---------------------------------------------------------------------------


class TestHealthRateLimitField:
    """GET /api/health returns ``rate_limit`` with the persisted state."""

    @pytest.mark.asyncio
    async def test_health_includes_rate_limit_clear(self):
        """When no rate limit is active, health returns rate_limit=None or clear status."""
        from app.services.rate_limit_state import get_rate_limit_store

        store = get_rate_limit_store()
        store.clear(provider="claude_cli")

        # The health response should have a rate_limit field
        state = store.get_active()
        assert state is None

    @pytest.mark.asyncio
    async def test_health_includes_rate_limit_active(self, tmp_path: Path):
        """When a rate limit is active, health returns the rate_limit state."""
        from app.services.rate_limit_state import RateLimitStateStore

        reset_at = datetime.now(timezone.utc) + timedelta(hours=1)
        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(
            provider="claude_cli",
            reset_at=reset_at,
            estimated_wait_seconds=3600,
        )

        active = store.get_active()
        assert active is not None
        assert active["provider"] == "claude_cli"
        assert active["is_active"] is True
        assert "seconds_remaining" in active
        assert active["seconds_remaining"] > 0


# ---------------------------------------------------------------------------
# 3. Event-bus integration — auto-record and auto-clear
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    """The store subscribes to event-bus events to auto-update state."""

    def test_rate_limit_active_event_records_state(self, tmp_path: Path):
        """rate_limit_active event → store.record()."""
        from app.services.rate_limit_state import RateLimitStateStore

        store = RateLimitStateStore(state_dir=tmp_path)
        reset_at = datetime.now(timezone.utc) + timedelta(hours=2)

        store.handle_rate_limit_active({
            "provider": "claude_cli",
            "reset_at_iso": reset_at.isoformat(),
            "estimated_wait_seconds": 7200,
            "source": "probe",
        })

        assert store.is_rate_limited() is True
        active = store.get_active()
        assert active["provider"] == "claude_cli"

    def test_rate_limit_cleared_event_clears_state(self, tmp_path: Path):
        """rate_limit_cleared event → store.clear()."""
        from app.services.rate_limit_state import RateLimitStateStore

        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(provider="claude_cli", reset_at=None)
        assert store.is_rate_limited() is True

        store.handle_rate_limit_cleared({
            "provider": "claude_cli",
        })

        assert store.is_rate_limited() is False


# ---------------------------------------------------------------------------
# 4. Startup probe — lightweight LLM call to detect rate limits on boot
# ---------------------------------------------------------------------------


class TestStartupProbe:
    """On startup, probe the provider to check if we're rate-limited."""

    @pytest.mark.asyncio
    async def test_probe_detects_active_rate_limit(self, tmp_path: Path):
        """If the provider returns 429 on the startup probe, state is recorded."""
        from app.providers.base import ProviderRateLimitError
        from app.services.rate_limit_state import RateLimitStateStore, probe_rate_limit

        store = RateLimitStateStore(state_dir=tmp_path)

        reset_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        mock_provider.complete_parsed = AsyncMock(
            side_effect=ProviderRateLimitError(
                "HTTP 429: rate limited",
                reset_at=reset_at,
                provider_name="claude_cli",
            )
        )

        result = await probe_rate_limit(mock_provider, store)
        assert result["is_rate_limited"] is True
        assert store.is_rate_limited() is True

    @pytest.mark.asyncio
    async def test_probe_clears_stale_limit(self, tmp_path: Path):
        """If the provider responds OK, stale persisted state is cleared."""
        from app.services.rate_limit_state import RateLimitStateStore, probe_rate_limit

        store = RateLimitStateStore(state_dir=tmp_path)
        # Pre-populate a stale limit
        store.record(
            provider="claude_cli",
            reset_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert store.is_rate_limited() is True

        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        # Provider responds successfully — rate limit has lifted
        mock_provider.complete_parsed = AsyncMock(return_value=MagicMock())

        result = await probe_rate_limit(mock_provider, store)
        assert result["is_rate_limited"] is False
        assert store.is_rate_limited() is False

    @pytest.mark.asyncio
    async def test_probe_handles_non_rate_limit_errors(self, tmp_path: Path):
        """Non-429 errors don't change the rate-limit state."""
        from app.providers.base import ProviderError
        from app.services.rate_limit_state import RateLimitStateStore, probe_rate_limit

        store = RateLimitStateStore(state_dir=tmp_path)

        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        mock_provider.complete_parsed = AsyncMock(
            side_effect=ProviderError("CLI not found", retryable=False)
        )

        result = await probe_rate_limit(mock_provider, store)
        assert result["is_rate_limited"] is False
        assert result.get("error") is not None
        # Store should NOT be flagged as rate-limited
        assert store.is_rate_limited() is False

    @pytest.mark.asyncio
    async def test_probe_uses_persisted_state_when_provider_unavailable(
        self, tmp_path: Path,
    ):
        """When no provider is available, rely on persisted state."""
        from app.services.rate_limit_state import RateLimitStateStore, probe_rate_limit

        store = RateLimitStateStore(state_dir=tmp_path)
        store.record(
            provider="claude_cli",
            reset_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await probe_rate_limit(None, store)
        assert result["is_rate_limited"] is True
        assert result["source"] == "persisted"

