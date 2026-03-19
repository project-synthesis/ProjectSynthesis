# Intelligent Routing Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize all pipeline routing decisions (sampling vs passthrough vs CLI vs API) into a single `RoutingService` with pure resolver function, thin manager, and reactive frontend.

**Architecture:** Pure `resolve_route()` function makes deterministic tier decisions from immutable `RoutingState` + `RoutingContext`. A `RoutingManager` wraps the resolver with state lifecycle, SSE event broadcasting, persistence, and disconnect detection. Frontend becomes purely reactive — no routing decisions, only display.

**Tech Stack:** Python 3.12, FastAPI, asyncio, Pydantic (frozen dataclasses), pytest (parametrize), SvelteKit 2 (Svelte 5 runes)

**Spec:** `docs/superpowers/specs/2026-03-19-intelligent-routing-service-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `backend/app/services/routing.py` | `RoutingState`, `RoutingContext`, `RoutingDecision` dataclasses, `resolve_route()` pure function, `RoutingManager` class |
| `backend/tests/test_routing.py` | Unit tests for resolver + integration tests for manager |

### Modified Files — Backend
| File | Lines | Change |
|------|-------|--------|
| `backend/app/main.py` | 31-38 | Create `RoutingManager`, replace `app.state.provider` |
| `backend/app/mcp_server.py` | 143-167, 210-509, 544-750, 848-920 | Replace routing chain + middleware integration |
| `backend/app/routers/optimize.py` | 73-116, 196-238 | Unified routing + inline passthrough |
| `backend/app/routers/refinement.py` | 53-58 | Add routing integration |
| `backend/app/routers/health.py` | 62-132 | Read from RoutingManager |
| `backend/app/routers/providers.py` | 66-72, 77-84 | set_provider on key set/delete |
| `backend/app/services/preferences.py` | 43-59 | Remove `auto_passthrough` |

### Modified Files — Frontend
| File | Change |
|------|--------|
| `frontend/src/lib/stores/forge.svelte.ts` | Remove passthrough branch, handle `routing` + `passthrough` SSE events |
| `frontend/src/lib/stores/preferences.svelte.ts` | Remove `auto_passthrough` |
| `frontend/src/routes/app/+page.svelte` | Remove routing logic, simplify polling |
| `frontend/src/lib/api/client.ts` | Mark `preparePassthrough` deprecated |

---

## Task 1: Data Model + Pure Resolver + Tests

**Files:**
- Create: `backend/app/services/routing.py`
- Create: `backend/tests/test_routing.py`

- [ ] **Step 1: Write resolver unit tests (RED)**

Create `backend/tests/test_routing.py`:

```python
"""Tests for the intelligent routing service — pure resolver + manager."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.services.routing import (
    RoutingContext,
    RoutingDecision,
    RoutingState,
    resolve_route,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _state(
    *,
    provider_name: str | None = None,
    sampling_capable: bool | None = None,
    mcp_connected: bool = False,
) -> RoutingState:
    provider = MagicMock(name=provider_name) if provider_name else None
    return RoutingState(
        provider=provider,
        provider_name=provider_name,
        sampling_capable=sampling_capable,
        mcp_connected=mcp_connected,
        last_capability_update=datetime.now(timezone.utc) if sampling_capable else None,
        last_activity=datetime.now(timezone.utc) if mcp_connected else None,
    )


def _ctx(
    *,
    caller: str = "rest",
    force_passthrough: bool = False,
    force_sampling: bool = False,
) -> RoutingContext:
    return RoutingContext(
        caller=caller,
        preferences={
            "pipeline": {
                "force_passthrough": force_passthrough,
                "force_sampling": force_sampling,
            }
        },
    )


# ── Tier 1: Force passthrough ────────────────────────────────────────


class TestForcePassthrough:
    def test_with_provider(self) -> None:
        decision = resolve_route(_state(provider_name="claude_cli"), _ctx(force_passthrough=True))
        assert decision.tier == "passthrough"
        assert decision.provider is None
        assert "Force passthrough" in decision.reason

    def test_without_provider(self) -> None:
        decision = resolve_route(_state(), _ctx(force_passthrough=True))
        assert decision.tier == "passthrough"

    def test_with_sampling(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=True, mcp_connected=True),
            _ctx(caller="mcp", force_passthrough=True),
        )
        assert decision.tier == "passthrough"


# ── Tier 2: Force sampling ───────────────────────────────────────────


class TestForceSampling:
    def test_mcp_caller_with_sampling(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=True, mcp_connected=True),
            _ctx(caller="mcp", force_sampling=True),
        )
        assert decision.tier == "sampling"
        assert decision.provider is None
        assert decision.degraded_from is None

    def test_rest_caller_degrades_to_internal(self) -> None:
        decision = resolve_route(
            _state(provider_name="claude_cli", sampling_capable=True, mcp_connected=True),
            _ctx(caller="rest", force_sampling=True),
        )
        assert decision.tier == "internal"
        assert decision.degraded_from == "sampling"

    def test_rest_caller_degrades_to_passthrough(self) -> None:
        decision = resolve_route(
            _state(),
            _ctx(caller="rest", force_sampling=True),
        )
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "sampling"

    def test_mcp_caller_not_connected_degrades(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=True, mcp_connected=False),
            _ctx(caller="mcp", force_sampling=True),
        )
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "sampling"

    def test_sampling_none_degrades(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=None, mcp_connected=True),
            _ctx(caller="mcp", force_sampling=True),
        )
        assert decision.degraded_from == "sampling"


# ── Tier 3: Internal provider ────────────────────────────────────────


class TestInternalProvider:
    def test_cli_provider(self) -> None:
        decision = resolve_route(_state(provider_name="claude_cli"), _ctx())
        assert decision.tier == "internal"
        assert decision.provider_name == "claude_cli"

    def test_api_provider(self) -> None:
        decision = resolve_route(_state(provider_name="anthropic_api"), _ctx())
        assert decision.tier == "internal"
        assert decision.provider_name == "anthropic_api"

    def test_provider_preferred_over_sampling(self) -> None:
        decision = resolve_route(
            _state(provider_name="claude_cli", sampling_capable=True, mcp_connected=True),
            _ctx(caller="mcp"),
        )
        assert decision.tier == "internal"


# ── Tier 4: Automatic sampling fallback ──────────────────────────────


class TestAutoSampling:
    def test_mcp_no_provider(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=True, mcp_connected=True),
            _ctx(caller="mcp"),
        )
        assert decision.tier == "sampling"
        assert decision.degraded_from == "internal"

    def test_rest_never_reaches_sampling(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=True, mcp_connected=True),
            _ctx(caller="rest"),
        )
        assert decision.tier == "passthrough"


# ── Tier 5: Passthrough fallback ─────────────────────────────────────


class TestPassthroughFallback:
    def test_nothing_available(self) -> None:
        decision = resolve_route(_state(), _ctx())
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"

    def test_sampling_not_connected(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=True, mcp_connected=False),
            _ctx(caller="mcp"),
        )
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"

    def test_sampling_none_unknown(self) -> None:
        decision = resolve_route(
            _state(sampling_capable=None, mcp_connected=True),
            _ctx(caller="mcp"),
        )
        assert decision.tier == "passthrough"


# ── Decision immutability ────────────────────────────────────────────


class TestDecisionProperties:
    def test_decision_is_frozen(self) -> None:
        decision = resolve_route(_state(provider_name="claude_cli"), _ctx())
        with pytest.raises(AttributeError):
            decision.tier = "passthrough"  # type: ignore[misc]

    def test_state_is_frozen(self) -> None:
        state = _state(provider_name="claude_cli")
        with pytest.raises(AttributeError):
            state.mcp_connected = True  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_routing.py -v 2>&1 | head -30`
Expected: `ModuleNotFoundError: No module named 'app.services.routing'`

- [ ] **Step 3: Implement data model + resolver (GREEN)**

Create `backend/app/services/routing.py`:

```python
"""Intelligent routing service — pure resolver + state manager.

Centralizes all pipeline routing decisions (sampling vs passthrough vs
CLI vs API) into a single service. The pure ``resolve_route()`` function
makes deterministic tier decisions from immutable state. The
``RoutingManager`` wraps it with state lifecycle, SSE events, persistence,
and disconnect detection.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from app.providers.base import LLMProvider
    from app.services.event_bus import EventBus
    from app.services.mcp_session_file import MCPSessionFile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingState:
    """Immutable snapshot of system capabilities at a point in time."""

    provider: LLMProvider | None
    provider_name: str | None
    sampling_capable: bool | None  # True/False/None (None = unknown/stale)
    mcp_connected: bool
    last_capability_update: datetime | None
    last_activity: datetime | None


@dataclass(frozen=True)
class RoutingContext:
    """Per-request context influencing the routing decision."""

    preferences: dict[str, Any]
    caller: Literal["rest", "mcp"]


@dataclass(frozen=True)
class RoutingDecision:
    """Output — which tier to use and why."""

    tier: Literal["internal", "sampling", "passthrough"]
    provider: LLMProvider | None
    provider_name: str | None
    reason: str
    degraded_from: str | None = None


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------


def resolve_route(state: RoutingState, ctx: RoutingContext) -> RoutingDecision:
    """Pure routing decision — no I/O, no state mutation, no logging.

    Five-tier priority chain:
    1. force_passthrough (user override, highest priority)
    2. force_sampling + mcp caller + sampling available
    3. Local provider available (CLI or API)
    4. No provider + mcp caller + sampling available
    5. Passthrough fallback (always reachable)
    """
    pipeline = ctx.preferences.get("pipeline", {})
    force_passthrough = pipeline.get("force_passthrough", False)
    force_sampling = pipeline.get("force_sampling", False)

    # Tier 1: Explicit passthrough override (highest priority)
    if force_passthrough:
        return RoutingDecision(
            tier="passthrough",
            provider=None,
            provider_name=None,
            reason="Force passthrough enabled by user preference",
        )

    # Tier 2: Explicit sampling override
    if force_sampling:
        can_sample = (
            ctx.caller == "mcp"
            and state.sampling_capable is True
            and state.mcp_connected
        )
        if can_sample:
            return RoutingDecision(
                tier="sampling",
                provider=None,
                provider_name=None,
                reason="Force sampling enabled — MCP client available",
            )
        # Degrade gracefully
        if state.provider:
            return RoutingDecision(
                tier="internal",
                provider=state.provider,
                provider_name=state.provider_name,
                reason="Force sampling requested but MCP unavailable — using local provider",
                degraded_from="sampling",
            )
        return RoutingDecision(
            tier="passthrough",
            provider=None,
            provider_name=None,
            reason="Force sampling requested but no MCP session or provider available",
            degraded_from="sampling",
        )

    # Tier 3: Local provider available — preferred automatic path
    if state.provider:
        return RoutingDecision(
            tier="internal",
            provider=state.provider,
            provider_name=state.provider_name,
            reason=f"Local provider: {state.provider_name}",
        )

    # Tier 4: No provider — try sampling if MCP caller with active session
    can_sample = (
        ctx.caller == "mcp"
        and state.sampling_capable is True
        and state.mcp_connected
    )
    if can_sample:
        return RoutingDecision(
            tier="sampling",
            provider=None,
            provider_name=None,
            reason="No local provider — using MCP sampling",
            degraded_from="internal",
        )

    # Tier 5: Nothing available — passthrough fallback
    return RoutingDecision(
        tier="passthrough",
        provider=None,
        provider_name=None,
        reason="No local provider, no sampling — passthrough fallback",
        degraded_from="internal",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_routing.py -v`
Expected: All tests pass (20+ cases)

- [ ] **Step 5: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add backend/app/services/routing.py backend/tests/test_routing.py
git commit -m "feat: add RoutingState, RoutingContext, RoutingDecision and resolve_route() pure function

Implements the core routing decision engine as a pure function with
frozen dataclasses. 5-tier priority chain: force passthrough > force
sampling > internal provider > auto sampling > passthrough fallback."
```

---

## Task 2: RoutingManager + Integration Tests

**Files:**
- Modify: `backend/app/services/routing.py`
- Modify: `backend/tests/test_routing.py`

- [ ] **Step 1: Write RoutingManager integration tests (RED)**

Append to `backend/tests/test_routing.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.event_bus import EventBus
from app.services.routing import RoutingManager


# ── RoutingManager ────────────────────────────────────────────────────


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def manager(tmp_path: Path, event_bus: EventBus) -> RoutingManager:
    return RoutingManager(event_bus=event_bus, data_dir=tmp_path)


class TestManagerSetProvider:
    def test_initial_state_no_provider(self, manager: RoutingManager) -> None:
        assert manager.state.provider is None
        assert manager.state.provider_name is None

    def test_set_provider(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        assert manager.state.provider is mock_provider
        assert manager.state.provider_name == "claude_cli"

    def test_set_provider_fires_event(self, manager: RoutingManager, event_bus: EventBus) -> None:
        events: list[dict] = []
        event_bus._subscribers.add(asyncio.Queue(maxsize=10))
        mock_provider = MagicMock()
        mock_provider.name = "anthropic_api"
        manager.set_provider(mock_provider)
        # Check that the event bus received a routing_state_changed event
        for q in event_bus._subscribers:
            if not q.empty():
                event = q.get_nowait()
                events.append(event)
        assert any(e["event"] == "routing_state_changed" for e in events)

    def test_clear_provider(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        manager.set_provider(None)
        assert manager.state.provider is None
        assert manager.state.provider_name is None


class TestManagerMcpInitialize:
    def test_sampling_detected(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        assert manager.state.mcp_connected is True

    def test_no_sampling(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=False)
        assert manager.state.sampling_capable is False
        assert manager.state.mcp_connected is True

    def test_optimistic_skip_no_downgrade(self, manager: RoutingManager) -> None:
        """Fresh True should not be overwritten by False within staleness window."""
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        manager.on_mcp_initialize(sampling_capable=False)
        # Should still be True (optimistic strategy)
        assert manager.state.sampling_capable is True


class TestManagerActivity:
    def test_activity_updates_timestamp(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=True)
        old_activity = manager.state.last_activity
        # Simulate time passing — update activity
        manager.on_mcp_activity()
        assert manager.state.last_activity >= old_activity  # type: ignore[operator]

    def test_reconnection_detected(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=True)
        # Simulate disconnect
        manager._update_state(mcp_connected=False)
        assert manager.state.mcp_connected is False
        # Activity triggers reconnection
        manager.on_mcp_activity()
        assert manager.state.mcp_connected is True


class TestManagerResolve:
    def test_delegates_to_resolver(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        ctx = RoutingContext(preferences={"pipeline": {}}, caller="rest")
        decision = manager.resolve(ctx)
        assert decision.tier == "internal"
        assert decision.provider_name == "claude_cli"


class TestManagerRecovery:
    def test_no_session_file(self, tmp_path: Path, event_bus: EventBus) -> None:
        """No mcp_session.json — starts with safe defaults."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is None
        assert mgr.state.mcp_connected is False

    def test_fresh_session_file(self, tmp_path: Path, event_bus: EventBus) -> None:
        """Recent mcp_session.json with sampling=True — recovers correctly."""
        import json
        from datetime import datetime, timezone
        session_file = tmp_path / "mcp_session.json"
        now = datetime.now(timezone.utc).isoformat()
        session_file.write_text(json.dumps({
            "sampling_capable": True, "written_at": now, "last_activity": now,
        }))
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is True
        assert mgr.state.mcp_connected is True

    def test_stale_session_file(self, tmp_path: Path, event_bus: EventBus) -> None:
        """Old mcp_session.json — sampling goes to None (unknown)."""
        import json
        from datetime import datetime, timedelta, timezone
        session_file = tmp_path / "mcp_session.json"
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        session_file.write_text(json.dumps({
            "sampling_capable": True, "written_at": old, "last_activity": old,
        }))
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is None  # stale → unknown
        assert mgr.state.mcp_connected is False      # activity stale


class TestManagerAvailableTiers:
    def test_only_passthrough_when_nothing(self, manager: RoutingManager) -> None:
        assert manager.available_tiers == ["passthrough"]

    def test_internal_and_passthrough(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        assert "internal" in manager.available_tiers
        assert "passthrough" in manager.available_tiers

    def test_all_tiers(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.available_tiers == ["internal", "sampling", "passthrough"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_routing.py::TestManagerSetProvider -v 2>&1 | head -15`
Expected: `AttributeError: module 'app.services.routing' has no attribute 'RoutingManager'`

- [ ] **Step 3: Implement RoutingManager (GREEN)**

Append to `backend/app/services/routing.py`:

```python
# ---------------------------------------------------------------------------
# RoutingManager — thin orchestration wrapper
# ---------------------------------------------------------------------------


class RoutingManager:
    """Manages live routing state, disconnect detection, and event broadcasting.

    Holds in-memory ``RoutingState`` as primary source of truth.
    ``mcp_session.json`` is write-through persistence for restart recovery.
    """

    def __init__(
        self,
        event_bus: EventBus,
        data_dir: Path,
    ) -> None:
        self._event_bus = event_bus
        self._data_dir = data_dir
        self._session_file: MCPSessionFile | None = None
        self._disconnect_task: asyncio.Task[None] | None = None

        # Initialize from persistence or defaults
        self._state = self._recover_state()

    @property
    def state(self) -> RoutingState:
        return self._state

    @property
    def available_tiers(self) -> list[str]:
        """Which tiers are currently reachable (for frontend display)."""
        tiers: list[str] = []
        if self._state.provider:
            tiers.append("internal")
        if self._state.sampling_capable is True and self._state.mcp_connected:
            tiers.append("sampling")
        tiers.append("passthrough")
        return tiers

    # ── State updates ─────────────────────────────────────────────────

    def set_provider(self, provider: LLMProvider | None) -> None:
        """Called at startup and on API key hot-reload/delete."""
        old_name = self._state.provider_name
        new_name = provider.name if provider else None
        self._state = replace(
            self._state,
            provider=provider,
            provider_name=new_name,
        )
        if old_name != new_name:
            self._broadcast_state_change("provider_changed")

    def on_mcp_initialize(self, sampling_capable: bool) -> None:
        """Called by ASGI middleware when MCP ``initialize`` is intercepted."""
        now = datetime.now(timezone.utc)

        # Optimistic strategy: False never overwrites a fresh True
        if not sampling_capable and self._state.sampling_capable is True:
            if self._state.last_capability_update:
                from app.config import MCP_CAPABILITY_STALENESS_MINUTES
                elapsed = (now - self._state.last_capability_update).total_seconds() / 60
                if elapsed <= MCP_CAPABILITY_STALENESS_MINUTES:
                    logger.debug("Optimistic skip: not downgrading fresh sampling_capable=True")
                    self._update_state(mcp_connected=True, last_activity=now)
                    self._persist()
                    return

        was_connected = self._state.mcp_connected
        old_sampling = self._state.sampling_capable
        self._update_state(
            sampling_capable=sampling_capable,
            mcp_connected=True,
            last_capability_update=now,
            last_activity=now,
        )
        self._persist()
        if old_sampling != sampling_capable or not was_connected:
            self._broadcast_state_change("mcp_initialize")

    def on_mcp_activity(self) -> None:
        """Called by middleware on every MCP POST (throttled to 10s by caller)."""
        now = datetime.now(timezone.utc)
        was_disconnected = not self._state.mcp_connected
        self._update_state(mcp_connected=True, last_activity=now)
        self._persist()
        if was_disconnected:
            self._broadcast_state_change("mcp_reconnect")

    # ── Public API ────────────────────────────────────────────────────

    def resolve(self, ctx: RoutingContext) -> RoutingDecision:
        """Resolve route, log decision, return."""
        start = time.monotonic_ns()
        decision = resolve_route(self._state, ctx)
        elapsed_us = (time.monotonic_ns() - start) // 1000
        logger.info(
            "routing.decision caller=%s tier=%s provider=%s reason=%r degraded_from=%s duration_us=%d",
            ctx.caller,
            decision.tier,
            decision.provider_name,
            decision.reason,
            decision.degraded_from,
            elapsed_us,
        )
        return decision

    # ── Background disconnect checker ─────────────────────────────────

    async def start_disconnect_checker(self) -> None:
        """Start the background task that detects MCP disconnections."""
        self._disconnect_task = asyncio.create_task(self._disconnect_loop())

    async def stop(self) -> None:
        """Cancel background tasks."""
        if self._disconnect_task:
            self._disconnect_task.cancel()
            try:
                await self._disconnect_task
            except asyncio.CancelledError:
                pass

    async def _disconnect_loop(self) -> None:
        """Check every 60s if MCP activity has gone stale."""
        from app.config import MCP_ACTIVITY_STALENESS_SECONDS

        while True:
            await asyncio.sleep(60)
            if self._state.mcp_connected and self._state.last_activity:
                elapsed = (
                    datetime.now(timezone.utc) - self._state.last_activity
                ).total_seconds()
                if elapsed > MCP_ACTIVITY_STALENESS_SECONDS:
                    logger.info("MCP activity stale (%.0fs) — marking disconnected", elapsed)
                    self._update_state(mcp_connected=False)
                    self._broadcast_state_change("disconnect")

    # ── Internal helpers ──────────────────────────────────────────────

    def _update_state(self, **fields: Any) -> None:
        """Replace specific fields in the current state."""
        self._state = replace(self._state, **fields)

    def _persist(self) -> None:
        """Write-through to ``mcp_session.json`` for restart recovery.

        Only called from the MCP server process (sole writer per spec).
        """
        if self._session_file:
            try:
                self._session_file.write_session(
                    sampling_capable=self._state.sampling_capable is True,
                )
            except Exception:
                logger.debug("Failed to persist routing state", exc_info=True)

    def _broadcast_state_change(self, event: str) -> None:
        """Push routing_state_changed to all SSE subscribers.

        Publishes to the local event bus. For cross-process notification
        (MCP server → FastAPI), callers should also call
        ``_notify_cross_process()`` after this method.
        """
        payload = {
            "trigger": event,
            "provider": self._state.provider_name,
            "sampling_capable": self._state.sampling_capable,
            "mcp_connected": self._state.mcp_connected,
            "available_tiers": self.available_tiers,
        }
        self._event_bus.publish("routing_state_changed", payload)
        logger.info(
            "routing.state_change event=%s sampling_capable=%s mcp_connected=%s available_tiers=%s",
            event,
            self._state.sampling_capable,
            self._state.mcp_connected,
            ",".join(self.available_tiers),
        )

    def _recover_state(self) -> RoutingState:
        """Recover state from ``mcp_session.json`` on startup."""
        from app.services.mcp_session_file import MCPSessionFile as _MCPSessionFile

        self._session_file = _MCPSessionFile(self._data_dir)
        data = self._session_file.read()

        if data is None:
            return RoutingState(
                provider=None,
                provider_name=None,
                sampling_capable=None,
                mcp_connected=False,
                last_capability_update=None,
                last_activity=None,
            )

        # Apply staleness checks
        sampling = data.get("sampling_capable", False)
        if not self._session_file.is_capability_fresh(data):
            sampling = None  # Stale → unknown

        connected = not self._session_file.detect_disconnect(data)

        last_activity = None
        if "last_activity" in data:
            try:
                last_activity = datetime.fromisoformat(data["last_activity"])
            except (ValueError, TypeError):
                pass

        return RoutingState(
            provider=None,  # Provider set separately via set_provider()
            provider_name=None,
            sampling_capable=sampling if sampling is not None else None,
            mcp_connected=connected,
            last_capability_update=None,
            last_activity=last_activity,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_routing.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add backend/app/services/routing.py backend/tests/test_routing.py
git commit -m "feat: add RoutingManager with state lifecycle, events, and disconnect detection

Thin wrapper around resolve_route() that owns in-memory state, SSE
event broadcasting, background disconnect checking, and mcp_session.json
recovery on startup."
```

---

## Task 3: Integrate into FastAPI Lifespan (`main.py`)

**Files:**
- Modify: `backend/app/main.py:31-38`

- [ ] **Step 1: Replace `app.state.provider` with `app.state.routing`**

In `backend/app/main.py`, replace lines 31-38:

```python
    # Detect LLM provider
    try:
        from app.providers.detector import detect_provider
        provider = detect_provider()
    except ImportError:
        provider = None
    app.state.provider = provider
    logger.info("Provider detected: %s", provider.name if provider else "none")
```

With:

```python
    # Initialize routing service
    from app.providers.detector import detect_provider
    from app.services.routing import RoutingManager

    routing = RoutingManager(event_bus=event_bus, data_dir=DATA_DIR)
    try:
        provider = detect_provider()
    except ImportError:
        provider = None
    routing.set_provider(provider)
    app.state.routing = routing
    logger.info(
        "Routing initialized: provider=%s available_tiers=%s",
        routing.state.provider_name or "none",
        routing.available_tiers,
    )

    # Start background disconnect checker
    await routing.start_disconnect_checker()
```

Also add cleanup in the shutdown section (after `yield`). Find the existing cleanup code after `yield` and add:

```python
    # Stop routing disconnect checker
    await routing.stop()
```

Update `PatternExtractorService` initialization (around line 63) to read from routing:

```python
    extractor = PatternExtractorService(provider=app.state.routing.state.provider)
```

- [ ] **Step 2: Update test conftest — backward-compatible fixture**

In `backend/tests/conftest.py`, update the `app_client` fixture (line 46) to set both old and new for backward compatibility during migration:

```python
@pytest_asyncio.fixture
async def app_client(mock_provider, db_session):
    from app.database import get_db
    from app.main import app
    from app.services.event_bus import EventBus
    from app.services.routing import RoutingManager

    # Create a test RoutingManager with mock provider
    test_routing = RoutingManager(event_bus=EventBus(), data_dir=tmp_path)
    test_routing.set_provider(mock_provider)
    app.state.routing = test_routing
    # Keep backward compat for any tests not yet migrated
    app.state.provider = mock_provider

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
```

Note: the fixture needs `tmp_path` — add it as a parameter to `app_client`.

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `cd backend && python -m pytest tests/test_routers.py tests/test_integration.py -v 2>&1 | tail -20`
Expected: All tests pass with the backward-compatible fixture.

- [ ] **Step 4: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add backend/app/main.py backend/tests/conftest.py
git commit -m "feat: wire RoutingManager into FastAPI lifespan

Replaces app.state.provider with app.state.routing. Initializes
routing service at startup with provider detection and disconnect
checker background task."
```

---

## Task 4: Update Backend Routers to Use RoutingManager

**Files:**
- Modify: `backend/app/routers/optimize.py:73-116`
- Modify: `backend/app/routers/health.py:62-132`
- Modify: `backend/app/routers/providers.py:52-84`
- Modify: `backend/app/routers/refinement.py:53-58`

- [ ] **Step 1: Update `routers/optimize.py` — unified routing + inline passthrough**

Replace the `optimize` function (lines 73-116) in `backend/app/routers/optimize.py`:

```python
@router.post("/optimize")
async def optimize(
    body: OptimizeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT)),
):
    from app.services.routing import RoutingContext

    routing = getattr(request.app.state, "routing", None)
    if not routing:
        raise HTTPException(status_code=503, detail="Routing service not initialized.")

    _prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = _prefs.load()

    ctx = RoutingContext(preferences=prefs_snapshot, caller="rest")
    decision = routing.resolve(ctx)

    logger.info(
        "POST /api/optimize: prompt_len=%d strategy=%s tier=%s",
        len(body.prompt), body.strategy, decision.tier,
    )

    # Scan workspace for guidance files
    guidance = None
    if body.workspace_path:
        from pathlib import Path as _Path
        from app.services.roots_scanner import RootsScanner
        scanner = RootsScanner()
        guidance = scanner.scan(_Path(body.workspace_path))

    effective_strategy = body.strategy or _prefs.get("defaults.strategy") or "auto"

    if decision.tier == "passthrough":
        # Inline passthrough — stream assembled template via SSE
        assembled, strategy_name = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=body.prompt,
            strategy_name=effective_strategy,
            codebase_guidance=guidance,
        )

        trace_id = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        pending = Optimization(
            id=opt_id, raw_prompt=body.prompt, status="pending",
            trace_id=trace_id, provider="web_passthrough",
            strategy_used=strategy_name, task_type="general",
        )
        db.add(pending)
        await db.commit()

        async def passthrough_stream():
            yield format_sse("routing", {
                "tier": decision.tier, "provider": decision.provider_name,
                "reason": decision.reason, "degraded_from": decision.degraded_from,
            })
            yield format_sse("passthrough", {
                "assembled_prompt": assembled, "strategy": strategy_name,
                "trace_id": trace_id, "optimization_id": opt_id,
            })

        return StreamingResponse(
            passthrough_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # Internal pipeline (decision.tier == "internal")
    orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

    async def event_stream():
        yield format_sse("routing", {
            "tier": decision.tier, "provider": decision.provider_name,
            "reason": decision.reason, "degraded_from": decision.degraded_from,
        })
        async for event in orchestrator.run(
            raw_prompt=body.prompt, provider=decision.provider, db=db,
            strategy_override=effective_strategy if effective_strategy != "auto" else None,
            codebase_guidance=guidance,
            applied_pattern_ids=body.applied_pattern_ids,
        ):
            yield format_sse(event.event, event.data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: Update `routers/health.py` — read from RoutingManager**

In `backend/app/routers/health.py`, replace the provider read at the top of the health function (line 65: `provider = getattr(request.app.state, "provider", None)`) AND the `MCPSessionFile` read logic (around lines 111-120):

```python
    # Provider and routing state (live in-memory)
    routing = getattr(request.app.state, "routing", None)
    if routing:
        provider = routing.state.provider
        provider_name = routing.state.provider_name
        sampling_capable = routing.state.sampling_capable
        mcp_disconnected = not routing.state.mcp_connected if routing.state.sampling_capable is not None else False
        available_tiers = routing.available_tiers
    else:
        provider = getattr(request.app.state, "provider", None)  # fallback
        provider_name = provider.name if provider else None
        sampling_capable = None
        mcp_disconnected = False
        available_tiers = ["passthrough"]
```

Add `available_tiers: list[str]` to the health response model and return value. Update any references to `provider.name` later in the function to use `provider_name`.

- [ ] **Step 3: Update `routers/providers.py` — hot-reload via routing**

In `backend/app/routers/providers.py`, update the `set_api_key` function (around line 66-72) to call `routing.set_provider()`:

```python
    # Hot-reload: update routing service
    routing = getattr(request.app.state, "routing", None)
    if routing:
        routing.set_provider(new_provider)
```

And in `delete_api_key` (around line 82), add provider clear:

```python
    # Clear provider from routing service
    routing = getattr(request.app.state, "routing", None)
    if routing:
        routing.set_provider(None)
```

- [ ] **Step 4: Update `routers/refinement.py` — add routing integration**

In `backend/app/routers/refinement.py`, replace the direct provider check (lines 53-58) with routing:

```python
    from app.services.routing import RoutingContext

    routing = getattr(request.app.state, "routing", None)
    if not routing:
        raise HTTPException(status_code=503, detail="Routing service not initialized.")

    _prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = _prefs.load()
    ctx = RoutingContext(preferences=prefs_snapshot, caller="rest")
    decision = routing.resolve(ctx)

    # Note: spec envisions passthrough refinement in the future. For now,
    # refinement still requires a provider (passthrough refinement UX not designed yet).
    if decision.tier == "passthrough":
        raise HTTPException(
            status_code=503,
            detail="Refinement requires a local provider. Configure an API key or install the Claude CLI.",
        )
    provider = decision.provider
```

- [ ] **Step 5: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: Most tests pass. Some older tests that mock `app.state.provider` may need fixup — address in next step.

- [ ] **Step 6: Fix any test fixtures that reference `app.state.provider`**

Search for `app.state.provider` in test files and update to use `app.state.routing`:

Run: `cd backend && grep -rn "app.state.provider" tests/`

For each match, update the fixture to create a mock `RoutingManager` or set `app.state.routing` with a mock that has a `.state.provider` attribute.

- [ ] **Step 7: Run tests again to confirm**

Run: `cd backend && python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add backend/app/routers/optimize.py backend/app/routers/health.py backend/app/routers/providers.py backend/app/routers/refinement.py backend/tests/
git commit -m "feat: wire all backend routers to RoutingManager

- optimize.py: unified routing with inline passthrough SSE, emits routing event
- health.py: reads live state from RoutingManager instead of mcp_session.json
- providers.py: API key set/delete calls routing.set_provider()
- refinement.py: uses routing.resolve() with 503 for passthrough tier"
```

---

## Task 5: Update MCP Server Routing

**Files:**
- Modify: `backend/app/mcp_server.py:143-167, 170-193, 210-509, 544-750, 848-920`

- [ ] **Step 1: Create RoutingManager in MCP lifespan**

In `backend/app/mcp_server.py`, update `_mcp_lifespan` (around lines 170-193) to create and store a `RoutingManager`:

```python
@asynccontextmanager
async def _mcp_lifespan(server: FastMCP):
    global _provider
    from app.services.event_bus import EventBus as _EventBus
    from app.services.routing import RoutingManager as _RoutingManager

    _mcp_event_bus = _EventBus()
    _routing_manager = _RoutingManager(event_bus=_mcp_event_bus, data_dir=DATA_DIR)

    _provider = detect_provider()
    if _provider:
        _routing_manager.set_provider(_provider)
        logger.info("MCP routing: provider=%s tiers=%s", _provider.name, _routing_manager.available_tiers)
    else:
        logger.warning("MCP routing: no provider, tiers=%s", _routing_manager.available_tiers)

    await _routing_manager.start_disconnect_checker()

    # Store on module level for tool access
    global _routing
    _routing = _routing_manager

    yield

    await _routing_manager.stop()
```

Add module-level global: `_routing: RoutingManager | None = None`

- [ ] **Step 2: Update middleware to call RoutingManager + cross-process notification**

In `_CapabilityDetectionMiddleware`, update `_inspect_initialize()` (around line 472) to call `_routing.on_mcp_initialize()` instead of writing the session file directly. Also add cross-process HTTP notification to the FastAPI backend:

```python
    if _routing:
        _routing.on_mcp_initialize(sampling_capable=has_sampling)
        # Notify FastAPI process so its RoutingManager and SSE subscribers update
        await notify_event_bus("routing_state_changed", {
            "trigger": "mcp_initialize",
            "provider": _routing.state.provider_name,
            "sampling_capable": _routing.state.sampling_capable,
            "mcp_connected": _routing.state.mcp_connected,
            "available_tiers": _routing.available_tiers,
        })
```

Update `_touch_activity()` (around line 433) to call `_routing.on_mcp_activity()`:

```python
    if _routing:
        was_disconnected = not _routing.state.mcp_connected
        _routing.on_mcp_activity()
        if was_disconnected:
            # Reconnection — notify FastAPI process
            await notify_event_bus("routing_state_changed", {
                "trigger": "mcp_reconnect",
                "provider": _routing.state.provider_name,
                "sampling_capable": _routing.state.sampling_capable,
                "mcp_connected": _routing.state.mcp_connected,
                "available_tiers": _routing.available_tiers,
            })
```

Also remove the old `mcp_session_changed` event firing code from both methods — it's replaced by `routing_state_changed`.

- [ ] **Step 3: Replace synthesis_optimize routing chain**

Replace the 200-line if/elif chain in `synthesis_optimize` (lines 593-750) with:

```python
    from app.services.routing import RoutingContext as _RoutingCtx

    ctx = _RoutingCtx(preferences=prefs, caller="mcp")
    decision = _routing.resolve(ctx) if _routing else None

    if decision is None:
        raise ValueError("Routing service not initialized")

    if decision.tier == "passthrough":
        # ... existing passthrough assembly logic (lines 594-626)
        pass

    elif decision.tier == "sampling":
        # ... existing sampling pipeline logic (lines 632-680)
        pass

    elif decision.tier == "internal":
        # ... existing internal pipeline logic (lines 721-750)
        pass
```

Preserve the existing execution code within each branch — only replace the decision logic, not the execution.

- [ ] **Step 4: Replace synthesis_analyze routing**

Same pattern for `synthesis_analyze` (lines 869-884): replace the provider check with `_routing.resolve()`.

- [ ] **Step 5: Remove `_write_mcp_session_caps()` function**

Delete the `_write_mcp_session_caps()` helper (lines 143-167) — no longer called. The middleware now calls `_routing.on_mcp_initialize()` and `_routing.on_mcp_activity()` instead.

- [ ] **Step 6: Run MCP-related tests**

Run: `cd backend && python -m pytest tests/test_mcp_tools.py tests/test_sampling_detection.py -v --tb=short`
Expected: Tests may need updates for the new routing path. Fix as needed.

- [ ] **Step 7: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add backend/app/mcp_server.py backend/tests/
git commit -m "feat: replace MCP server routing chain with RoutingManager

Replaces 200-line if/elif chain in synthesis_optimize with
routing.resolve(). Middleware calls manager.on_mcp_initialize()
and on_mcp_activity() instead of writing session file directly.
Removes _write_mcp_session_caps() helper."
```

---

## Task 6: Remove `auto_passthrough` Preference

**Files:**
- Modify: `backend/app/services/preferences.py:43-59`
- Modify: `backend/tests/test_preferences.py`

- [ ] **Step 1: Remove `auto_passthrough` from backend**

In `backend/app/services/preferences.py`:
- Remove `"auto_passthrough": False` from `DEFAULTS["pipeline"]` (line 49)
- Remove `"auto_passthrough"` from `_PIPELINE_TOGGLES` tuple (line 58)

- [ ] **Step 2: Update preferences tests**

In `backend/tests/test_preferences.py`, remove any assertions about `auto_passthrough` in defaults.

- [ ] **Step 3: Run preferences tests**

Run: `cd backend && python -m pytest tests/test_preferences.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add backend/app/services/preferences.py backend/tests/test_preferences.py
git commit -m "refactor: remove auto_passthrough preference

Backend routing service now owns MCP disconnect degradation.
No frontend preference needed — degradation is automatic and
transparent via routing_state_changed SSE events."
```

---

## Task 7: Frontend — Forge Store Refactor

**Files:**
- Modify: `frontend/src/lib/stores/forge.svelte.ts:87-101`
- Modify: `frontend/src/lib/api/client.ts:272-276`

- [ ] **Step 1: Add `routing` and `passthrough` SSE event handling in forge store**

In `frontend/src/lib/stores/forge.svelte.ts`, find the `handleEvent` method and add handlers for the new SSE events:

```typescript
  // Add new state fields (near line 30)
  routingDecision = $state<{ tier: string; provider: string | null; reason: string; degraded_from: string | null } | null>(null);
```

In the `handleEvent` method, add cases:

```typescript
    if (event.event === 'routing') {
      this.routingDecision = event.data as any;
      return;
    }
    if (event.event === 'passthrough') {
      const d = event.data as any;
      this.assembledPrompt = d.assembled_prompt;
      this.passthroughTraceId = d.trace_id;
      this.passthroughStrategy = d.strategy;
      this.status = 'passthrough';
      return;
    }
```

- [ ] **Step 2: Remove passthrough branch from `forge()` method**

Replace lines 87-101 (the passthrough branch) in `forge()`. Remove the entire `if (this.noProvider || preferencesStore.pipeline.force_passthrough)` block. The `forge()` method should always go straight to the SSE path (line 103+). The backend decides the tier.

- [ ] **Step 3: Mark `preparePassthrough` as deprecated in client.ts**

In `frontend/src/lib/api/client.ts`, add a JSDoc deprecation comment to `preparePassthrough`:

```typescript
/** @deprecated Use unified POST /api/optimize — backend routes to passthrough via SSE */
export const preparePassthrough = ...
```

Keep the function for backward compatibility but it's no longer called by forge.

- [ ] **Step 4: Remove `noProvider` field from forge store**

Remove `noProvider = $state(false)` (line 44) — no longer needed for routing decisions. If any display code still references it, replace with a check on `routingDecision?.tier === 'passthrough'`.

- [ ] **Step 5: Verify frontend builds**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -20`
Expected: No errors related to removed fields (may need to update references in other components).

- [ ] **Step 6: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add frontend/src/lib/stores/forge.svelte.ts frontend/src/lib/api/client.ts
git commit -m "feat: forge store uses unified SSE endpoint with routing events

Removes preparePassthrough branch. forge() always calls optimizeSSE().
Handles routing and passthrough SSE events from backend. Removes
noProvider state — backend owns routing decisions."
```

---

## Task 8: Frontend — Remove Routing Logic from `+page.svelte`

**Files:**
- Modify: `frontend/src/routes/app/+page.svelte:14-156`

- [ ] **Step 1: Remove disconnect/reconnect handlers and auto-passthrough logic**

In `frontend/src/routes/app/+page.svelte`:

1. **Delete** `handleMcpDisconnect()` (lines 16-22)
2. **Delete** `handleMcpReconnect()` (lines 24-29)
3. In `applyHealth()` (lines 96-132), remove:
   - The `force_sampling` guard (lines 106-109) — backend handles this via `routing_state_changed`
   - The `auto_passthrough` guard (lines 111-116)
   - The auto-switch/restore on disconnect/reconnect (lines 123-131)
   - Keep: setting `health`, clearing `backendError`, toast for sampling first detected (lines 118-121)
4. Simplify the adaptive polling to fixed 60s:
   - Remove `FAST_INTERVAL`, `FAST_WINDOW`, `fastWindowElapsed` (lines 86-89, 136-143)
   - Replace `pollInterval` derivation with a constant `POLL_INTERVAL = 60_000`
   - Simplify the interval effect to use the constant

- [ ] **Step 2: Add `routing_state_changed` SSE handler**

In the existing `connectEventStream` callback (around line 33), add a handler for the new event:

```typescript
      if (type === 'routing_state_changed') {
        const d = data as { provider: string | null; sampling_capable: boolean | null; mcp_connected: boolean; available_tiers: string[] };
        // Capture previous state BEFORE updating
        const wasDisconnected = forgeStore.mcpDisconnected;
        const prevSampling = forgeStore.samplingCapable;
        // Update state
        forgeStore.samplingCapable = d.sampling_capable;
        forgeStore.mcpDisconnected = !d.mcp_connected;
        // Toast on state changes
        if (d.mcp_connected && wasDisconnected) {
          addToast('created', 'MCP client reconnected');
        }
        if (prevSampling !== true && d.sampling_capable === true) {
          addToast('created', 'MCP client connected with sampling capability');
        }
        if (!d.mcp_connected && !wasDisconnected) {
          addToast('deleted', 'MCP client disconnected');
        }
      }
```

Replace the existing `mcp_session_changed` handler (lines 72-79) with this.

- [ ] **Step 3: Verify frontend builds**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -20`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add frontend/src/routes/app/+page.svelte
git commit -m "refactor: remove routing logic from +page.svelte

Removes handleMcpDisconnect, handleMcpReconnect, auto-passthrough
guards, adaptive polling. Replaces mcp_session_changed handler with
routing_state_changed. Fixed 60s health polling for display only."
```

---

## Task 9: Frontend — Preferences Store + Settings UI Cleanup

**Files:**
- Modify: `frontend/src/lib/stores/preferences.svelte.ts:9-30`

- [ ] **Step 1: Remove `auto_passthrough` from frontend preferences**

In `frontend/src/lib/stores/preferences.svelte.ts`:
- Remove `auto_passthrough: boolean;` from `PipelinePrefs` interface (line 15)
- Remove `auto_passthrough: false` from `DEFAULTS.pipeline` (line 28)

- [ ] **Step 2: Search and remove all remaining `auto_passthrough` references**

Run: `cd frontend && grep -rn "auto_passthrough" src/`

Remove all remaining references in components (likely in settings panels or StatusBar). Each reference should either be deleted or replaced with a read from `forgeStore.mcpDisconnected`.

- [ ] **Step 3: Verify frontend builds**

Run: `cd frontend && npx svelte-check --threshold warning 2>&1 | tail -20`
Expected: Clean

- [ ] **Step 4: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add frontend/src/
git commit -m "refactor: remove auto_passthrough from frontend preferences

Backend owns degradation now. Removes auto_passthrough from
PipelinePrefs interface, defaults, and all component references."
```

---

## Task 10: Documentation + Final Cleanup

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Update CLAUDE.md**

Add `routing.py` to the Key services section:

```markdown
- `routing.py` — intelligent routing service: `RoutingState` (immutable capabilities snapshot), `RoutingContext` (per-request), `RoutingDecision` (tier + reason), `resolve_route()` (pure 5-tier decision function), `RoutingManager` (state lifecycle, SSE events, disconnect detection, persistence recovery)
```

Update the event bus event types list: replace `mcp_session_changed` with `routing_state_changed`.

Update the "MCP capability hierarchy" decision to reference the routing service.

- [ ] **Step 2: Update CHANGELOG.md**

Add under `## Unreleased`:

```markdown
### Added
- Intelligent routing service (`services/routing.py`) — centralizes all pipeline routing decisions into a pure resolver function + thin manager
- `routing` SSE event emitted as first event in every optimize stream — frontend knows the tier before pipeline phases begin
- `routing_state_changed` SSE event — ambient notification when available tiers change (MCP connect/disconnect, provider add/remove)
- Inline passthrough in unified `POST /api/optimize` endpoint — no more 503 when no provider is configured

### Changed
- Backend owns MCP disconnect degradation — frontend no longer makes routing decisions
- Health endpoint reads live in-memory state instead of `mcp_session.json`
- MCP detection is near-instant (in-memory) instead of file-based
- Health polling simplified to fixed 60s interval (display only)
- Refinement endpoint uses routing service — future-ready for passthrough refinement

### Removed
- `auto_passthrough` preference — backend manages degradation automatically
- Frontend routing decision tree (handleMcpDisconnect, handleMcpReconnect, adaptive polling)
- `_write_mcp_session_caps()` from MCP server — replaced by RoutingManager
```

- [ ] **Step 3: Add E2E smoke test**

Add to `backend/tests/test_routing.py`:

```python
@pytest.mark.asyncio
async def test_optimize_emits_routing_event(app_client):
    """POST /api/optimize streams a routing SSE event as first event."""
    # app_client fixture has a mock provider set
    response = await app_client.post(
        "/api/optimize",
        json={"prompt": "Write a function that calculates fibonacci numbers efficiently"},
    )
    assert response.status_code == 200
    # Parse first SSE event
    lines = response.text.strip().split("\n")
    first_event = None
    for line in lines:
        if line.startswith("event: routing"):
            first_event = "routing"
            break
    assert first_event == "routing", f"Expected first SSE event to be 'routing', got: {lines[:5]}"
```

- [ ] **Step 4: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: All tests pass

Run: `cd frontend && npx svelte-check --threshold warning`
Expected: Clean

- [ ] **Step 5: Commit**

```bash
cd /home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2
git add CLAUDE.md docs/CHANGELOG.md backend/tests/test_routing.py
git commit -m "docs: update CLAUDE.md and CHANGELOG for intelligent routing service"
```

---

## Summary

| Task | Description | Est. |
|------|-------------|------|
| 1 | Data model + pure resolver + tests | 15 min |
| 2 | RoutingManager + integration tests | 20 min |
| 3 | FastAPI lifespan integration | 5 min |
| 4 | Backend routers (optimize, health, providers, refinement) | 25 min |
| 5 | MCP server routing replacement | 25 min |
| 6 | Remove `auto_passthrough` preference | 5 min |
| 7 | Frontend forge store refactor | 15 min |
| 8 | Frontend `+page.svelte` routing removal | 15 min |
| 9 | Frontend preferences cleanup | 10 min |
| 10 | Documentation + final cleanup | 10 min |
| **Total** | | **~2.5 hrs** |
