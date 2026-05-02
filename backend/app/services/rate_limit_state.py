"""Persistent rate-limit state store.

Provides a JSON-backed state store for LLM provider rate limits that
survives backend restarts. The store is the single source of truth for
"is the provider currently rate-limited?" — consumed by:

* ``GET /api/health`` → ``rate_limit`` field for frontend hydration
* ``rate_limit_active`` / ``rate_limit_cleared`` event-bus handlers
* ``probe_rate_limit()`` startup probe — lightweight LLM call on boot
* Frontend ``rateLimitStore`` via SSE + health poll

The state file lives at ``data/rate_limit_state.json``. Read/write is
synchronous (sub-ms for a ~200-byte JSON file) and thread-safe via
the GIL; no asyncio lock needed.

Copyright 2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import LLMProvider
    from app.services.event_bus import EventBus

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# When reset_at is unknown, treat the limit as active for this many seconds.
_DEFAULT_TTL_SECONDS = 5 * 60  # 5 minutes

_STATE_FILENAME = "rate_limit_state.json"


class RateLimitStateStore:
    """Persistent JSON-backed rate-limit state.

    Parameters
    ----------
    state_dir : Path
        Directory where the state file lives. Production default: ``data/``.
    """

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._state_file = state_dir / _STATE_FILENAME
        self._state: dict[str, dict[str, Any]] = self._load() or {}
        self._watcher_task: asyncio.Task | None = None
        self._watcher_sleep_interval: float = 5.0  # seconds between checks

    # -- Public API ---------------------------------------------------------

    def is_rate_limited(self, provider: str | None = None) -> bool:
        """Return True if the specified provider (or any provider) is currently rate-limited."""
        if provider is not None:
            return self.get_active(provider) is not None
        return any(self.get_active(p) is not None for p in self._state)

    def get_active(self, provider: str | None = None) -> dict[str, Any] | None:
        """Return the active rate-limit state for a provider, or None if clear/expired."""
        if not self._state:
            return None

        if provider is None:
            # Fallback for legacy calls: return the first active limit found
            for p in self._state:
                active = self.get_active(p)
                if active:
                    return active
            return None

        provider_state = self._state.get(provider)
        if not provider_state:
            return None

        # Check expiry
        reset_at_iso = provider_state.get("reset_at_iso")
        detected_at_iso = provider_state.get("detected_at_iso")
        now = datetime.now(timezone.utc)

        if reset_at_iso:
            try:
                reset_at = datetime.fromisoformat(reset_at_iso)
                if reset_at <= now:
                    # Expired — auto-clear
                    del self._state[provider]
                    self._persist()
                    return None
                seconds_remaining = max(
                    0, int((reset_at - now).total_seconds()),
                )
            except (ValueError, TypeError):
                seconds_remaining = None
        elif detected_at_iso:
            # No reset_at — use default TTL from detection time
            try:
                detected_at = datetime.fromisoformat(detected_at_iso)
                elapsed = (now - detected_at).total_seconds()
                if elapsed >= _DEFAULT_TTL_SECONDS:
                    del self._state[provider]
                    self._persist()
                    return None
                seconds_remaining = max(
                    0, int(_DEFAULT_TTL_SECONDS - elapsed),
                )
            except (ValueError, TypeError):
                seconds_remaining = None
        else:
            seconds_remaining = None

        return {
            **provider_state,
            "is_active": True,
            "seconds_remaining": seconds_remaining,
        }

    def record(
        self,
        provider: str,
        reset_at: datetime | None = None,
        estimated_wait_seconds: int | float | None = None,
        source: str | None = None,
    ) -> None:
        """Record a rate-limit event. Persists to disk immediately."""
        now = datetime.now(timezone.utc)

        # Compute reset_at from estimated_wait if not provided
        effective_reset_at = reset_at
        if effective_reset_at is None and estimated_wait_seconds is not None:
            effective_reset_at = now + timedelta(seconds=estimated_wait_seconds)

        self._state[provider] = {
            "provider": provider,
            "reset_at_iso": (
                effective_reset_at.isoformat()
                if effective_reset_at is not None
                else None
            ),
            "estimated_wait_seconds": (
                int(estimated_wait_seconds)
                if estimated_wait_seconds is not None
                else None
            ),
            "detected_at_iso": now.isoformat(),
            "source": source,
        }
        self._persist()
        logger.info(
            "Rate limit recorded: provider=%s reset_at=%s source=%s",
            provider,
            effective_reset_at.isoformat() if effective_reset_at else "unknown",
            source,
        )

    def clear(self, provider: str | None = None) -> None:
        """Clear the rate-limit state. Persists to disk immediately."""
        if not self._state:
            return

        if provider is not None:
            if provider in self._state:
                del self._state[provider]
            else:
                return  # Provider wasn't limited
        else:
            self._state.clear()

        self._persist()
        logger.info("Rate limit cleared: provider=%s", provider or "all")

    # -- Event-bus handlers ------------------------------------------------

    def handle_rate_limit_active(self, payload: dict[str, Any]) -> None:
        """Handle a ``rate_limit_active`` event-bus event."""
        provider = payload.get("provider", "unknown")
        reset_at_iso = payload.get("reset_at_iso")
        reset_at: datetime | None = None
        if reset_at_iso:
            try:
                reset_at = datetime.fromisoformat(reset_at_iso)
            except (ValueError, TypeError):
                pass
        self.record(
            provider=provider,
            reset_at=reset_at,
            estimated_wait_seconds=payload.get("estimated_wait_seconds"),
            source=payload.get("source", "event_bus"),
        )

    def handle_rate_limit_cleared(self, payload: dict[str, Any]) -> None:
        """Handle a ``rate_limit_cleared`` event-bus event."""
        self.clear(provider=payload.get("provider"))

    # -- Watcher pattern ----------------------------------------------------

    def start_watcher(self, event_bus: EventBus, provider: LLMProvider | None = None) -> None:
        """Start the background task that proactively clears expired rate limits."""
        if self._watcher_task is not None and not self._watcher_task.done():
            self._watcher_task.cancel()

        self._watcher_task = asyncio.create_task(self._watcher_loop(event_bus, provider))
        logger.debug("RateLimitStateStore watcher background task started.")

    async def _watcher_loop(self, event_bus: EventBus, provider: LLMProvider | None) -> None:
        """Periodically check for expired rate limits and broadcast clear events."""
        try:
            while True:
                await asyncio.sleep(self._watcher_sleep_interval)

                if not self._state:
                    continue

                now = datetime.now(timezone.utc)
                to_clear = []

                # Check each provider
                for provider_name, state in list(self._state.items()):
                    reset_at_iso = state.get("reset_at_iso")
                    detected_at_iso = state.get("detected_at_iso")

                    if reset_at_iso:
                        try:
                            reset_at = datetime.fromisoformat(reset_at_iso)
                            if reset_at <= now:
                                to_clear.append(provider_name)
                        except (ValueError, TypeError):
                            pass
                    elif detected_at_iso:
                        # Limit with unknown TTL
                        try:
                            detected_at = datetime.fromisoformat(detected_at_iso)
                            if (now - detected_at).total_seconds() >= _DEFAULT_TTL_SECONDS:
                                # We've hit the default TTL. Before clearing, probe the provider!
                                if provider and provider.name == provider_name:
                                    # We use the global probe function to avoid circular logic
                                    # but we supply our provider instance
                                    probe_result = await probe_rate_limit(provider, self)
                                    if not probe_result.get("is_rate_limited", False):
                                        to_clear.append(provider_name)
                                    else:
                                        # It's STILL rate limited. The probe updated our state automatically
                                        pass
                                else:
                                    # Can't probe, so we just clear it
                                    to_clear.append(provider_name)
                        except (ValueError, TypeError):
                            pass

                for p_name in to_clear:
                    logger.info("Watcher detected expired rate limit for provider=%s", p_name)
                    # We clear the limit locally, then publish the event so other consumers hear it
                    self.clear(p_name)
                    # event_bus.publish is sync (def, not async def) and takes
                    # two positional args (event_type: str, data: dict). The
                    # prior dict-form call against an async-mock test fixture
                    # silently passed in tests but raised TypeError in
                    # production -- watcher loop's bare ``except Exception``
                    # caught it and the watcher died. Result: only the cleaner
                    # expiry path (provider success) cleared rate-limit
                    # banners; the watcher-detected expiry path was dead.
                    event_bus.publish(
                        "rate_limit_cleared",
                        {"provider": p_name, "source": "watcher"},
                    )

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Rate limit watcher task failed: %s", exc)

    # -- Internal ----------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]] | None:
        """Load state from disk. Returns None if no file or invalid."""
        if not self._state_file.exists():
            return None
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            # Handle legacy single-provider state migration
            if "provider" in data and isinstance(data.get("provider"), str):
                return {data["provider"]: data}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Rate limit state file corrupted (%s), treating as empty: %s",
                self._state_file, exc,
            )
            return None

    def _persist(self) -> None:
        """Write current state to disk atomically."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        try:
            if not self._state:
                # Clear state — remove the file
                if self._state_file.exists():
                    self._state_file.unlink()
                return
            tmp.write_text(
                json.dumps(self._state, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._state_file)
        except OSError as exc:
            logger.warning("Failed to persist rate limit state: %s", exc)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Singleton + startup probe
# ---------------------------------------------------------------------------

_store: RateLimitStateStore | None = None


def get_rate_limit_store() -> RateLimitStateStore:
    """Return the singleton store, creating it on first call."""
    global _store
    if _store is None:
        from app.config import DATA_DIR

        _store = RateLimitStateStore(state_dir=Path(DATA_DIR))
    return _store


async def probe_rate_limit(
    provider: Any | None,
    store: RateLimitStateStore | None = None,
) -> dict[str, Any]:
    """Lightweight startup probe to check if the provider is rate-limited.

    If ``provider`` is None (no provider available), falls back to
    persisted state.

    Returns a dict with ``is_rate_limited``, ``source``, and optional
    ``error`` / ``reset_at_iso`` fields.
    """
    if store is None:
        store = get_rate_limit_store()

    if provider is None:
        # No provider available — rely on persisted state
        active = store.get_active()
        if active is not None:
            return {
                "is_rate_limited": True,
                "source": "persisted",
                "provider": active.get("provider"),
                "reset_at_iso": active.get("reset_at_iso"),
            }
        return {"is_rate_limited": False, "source": "persisted"}

    # Attempt a minimal LLM call to detect rate limits.
    # Use a trivial prompt that's fast + cheap. We need a Pydantic model
    # for complete_parsed, so use a minimal one.
    from app.providers.base import ProviderError, ProviderRateLimitError

    class _ProbeResult(BaseModel):
        ok: bool = True

    try:
        await provider.complete_parsed(
            model="claude-haiku-4-5",  # cheapest model
            system_prompt="Reply with {\"ok\": true}",
            user_message="ping",
            output_format=_ProbeResult,
            max_tokens=32,
        )
        # Success — clear any stale persisted state
        store.clear(provider=provider.name)
        return {"is_rate_limited": False, "source": "probe"}

    except ProviderRateLimitError as exc:
        store.record(
            provider=provider.name,
            reset_at=exc.reset_at,
            estimated_wait_seconds=exc.estimated_wait_seconds,
            source="startup_probe",
        )
        return {
            "is_rate_limited": True,
            "source": "probe",
            "provider": provider.name,
            "reset_at_iso": (
                exc.reset_at.isoformat() if exc.reset_at else None
            ),
            "estimated_wait_seconds": exc.estimated_wait_seconds,
        }

    except ProviderError as exc:
        # Non-rate-limit error (CLI not found, timeout, etc.)
        # Don't change the rate-limit state — this is a different problem.
        return {
            "is_rate_limited": False,
            "source": "probe",
            "error": str(exc),
        }

    except Exception as exc:
        logger.warning("Rate limit probe failed: %s", exc)
        # Fall back to persisted state
        active = store.get_active()
        return {
            "is_rate_limited": active is not None,
            "source": "persisted_fallback",
            "error": str(exc),
        }
