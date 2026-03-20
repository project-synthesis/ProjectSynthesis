"""Centralized routing decision engine for the optimization pipeline.

Provides a pure function ``resolve_route()`` that maps system state and
request context to one of three execution tiers:

  internal   — use a locally detected LLM provider (CLI or API)
  sampling   — delegate to an MCP client via sampling/createMessage
  passthrough — return assembled prompt for external LLM processing

Priority chain (highest wins):
  1. force_passthrough  → passthrough (unconditional)
  2. force_sampling     → sampling (if eligible) or degrade
  3. internal provider  → internal
  4. auto sampling      → sampling (MCP caller only, degraded from internal)
  5. passthrough        → fallback (degraded from internal)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from app.providers.base import LLMProvider
    from app.services.event_bus import EventBus
    from app.services.mcp_session_file import MCPSessionFile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model — all frozen (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingState:
    """Snapshot of the server's capability state at the time of a request.

    Attributes:
        provider: The detected LLM provider instance, or None.
        provider_name: Human-readable provider name (e.g. "claude-cli").
        sampling_capable: Whether the MCP client supports sampling.
            ``None`` means unknown or stale — treated as ``False``.
        mcp_connected: Whether an MCP client is currently connected.
        last_capability_update: When sampling_capable was last refreshed.
        last_activity: When the MCP client last communicated.
    """

    provider: LLMProvider | None = None
    provider_name: str | None = None
    sampling_capable: bool | None = None
    mcp_connected: bool = False
    last_capability_update: datetime | None = None
    last_activity: datetime | None = None


@dataclass(frozen=True)
class RoutingContext:
    """Per-request context that influences the routing decision.

    Attributes:
        preferences: User preference snapshot (may contain
            ``force_passthrough`` and ``force_sampling`` keys).
        caller: Where the request originated — ``"rest"`` for the HTTP API,
            ``"mcp"`` for an MCP tool invocation.
    """

    preferences: dict[str, Any] = field(default_factory=dict)
    caller: Literal["rest", "mcp"] = "rest"


@dataclass(frozen=True)
class RoutingDecision:
    """The resolved execution tier and associated metadata.

    Attributes:
        tier: The selected execution tier.
        provider: The LLM provider to use (only set for ``internal`` tier).
        provider_name: Name of the provider, if any.
        reason: Human-readable explanation of why this tier was chosen.
        degraded_from: If the decision was a fallback, the tier that was
            originally requested or expected.
    """

    tier: Literal["internal", "sampling", "passthrough"]
    provider: LLMProvider | None = None
    provider_name: str | None = None
    reason: str = ""
    degraded_from: str | None = None


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------


def resolve_route(state: RoutingState, ctx: RoutingContext) -> RoutingDecision:
    """Determine the execution tier for a pipeline request.

    This is a **pure function** — no I/O, no logging, no side effects.
    All inputs are frozen dataclasses; the output is a frozen dataclass.
    """
    pipeline = ctx.preferences.get("pipeline", {})
    force_passthrough = bool(pipeline.get("force_passthrough"))
    force_sampling = bool(pipeline.get("force_sampling"))

    # Tier 1: force_passthrough always wins
    if force_passthrough:
        return RoutingDecision(
            tier="passthrough",
            reason="force_passthrough enabled",
        )

    # Tier 2: force_sampling — requires MCP caller + sampling capability
    if force_sampling:
        sampling_ok = (
            ctx.caller == "mcp"
            and state.sampling_capable is True
            and state.mcp_connected
        )
        if sampling_ok:
            return RoutingDecision(
                tier="sampling",
                reason="force_sampling enabled, MCP client supports sampling",
            )
        # Degrade: try internal, then passthrough
        if state.provider is not None:
            return RoutingDecision(
                tier="internal",
                provider=state.provider,
                provider_name=state.provider_name,
                reason="force_sampling degraded: sampling unavailable, using internal provider",
                degraded_from="sampling",
            )
        return RoutingDecision(
            tier="passthrough",
            reason="force_sampling degraded: sampling unavailable, no internal provider",
            degraded_from="sampling",
        )

    # Tier 3: internal provider available
    if state.provider is not None:
        return RoutingDecision(
            tier="internal",
            provider=state.provider,
            provider_name=state.provider_name,
            reason="internal provider available",
        )

    # Tier 4: auto sampling — MCP caller with sampling capability
    if (
        ctx.caller == "mcp"
        and state.sampling_capable is True
        and state.mcp_connected
    ):
        return RoutingDecision(
            tier="sampling",
            reason="no internal provider, MCP client supports sampling",
            degraded_from="internal",
        )

    # Tier 5: passthrough fallback
    return RoutingDecision(
        tier="passthrough",
        reason="no internal provider or sampling available",
        degraded_from="internal",
    )


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
        *,
        is_mcp_process: bool = False,
    ) -> None:
        self._event_bus = event_bus
        self._data_dir = data_dir
        self._is_mcp_process = is_mcp_process
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
            logger.info(
                "routing.provider_changed old=%s new=%s available_tiers=%s",
                old_name, new_name, ",".join(self.available_tiers),
            )
            self._broadcast_state_change("provider_changed")

    def on_mcp_initialize(self, sampling_capable: bool) -> None:
        """Called by ASGI middleware when MCP ``initialize`` is intercepted."""
        now = datetime.now(timezone.utc)
        old_sampling = self._state.sampling_capable

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
        self._update_state(
            sampling_capable=sampling_capable,
            mcp_connected=True,
            last_capability_update=now,
            last_activity=now,
        )
        self._persist()
        if old_sampling != sampling_capable or not was_connected:
            logger.info(
                "routing.mcp_initialize sampling_capable=%s→%s mcp_connected=%s→%s",
                old_sampling, sampling_capable, was_connected, True,
            )
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
            try:
                await asyncio.sleep(60)
                if self._state.mcp_connected and self._state.last_activity:
                    elapsed = (
                        datetime.now(timezone.utc) - self._state.last_activity
                    ).total_seconds()
                    if elapsed > MCP_ACTIVITY_STALENESS_SECONDS:
                        logger.info(
                            "routing.disconnect activity_stale=%.0fs threshold=%ds",
                            elapsed, MCP_ACTIVITY_STALENESS_SECONDS,
                        )
                        self._update_state(mcp_connected=False)
                        self._broadcast_state_change("disconnect")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Disconnect checker iteration failed", exc_info=True)

    # ── Internal helpers ──────────────────────────────────────────────

    def _update_state(self, **fields: Any) -> None:
        """Replace specific fields in the current state.

        Thread-safety note: all callers (``set_provider``, ``on_mcp_initialize``,
        ``on_mcp_activity``, ``_disconnect_loop``) are synchronous between
        their read of ``self._state`` and this write — no ``await`` between
        read and replace.  This is safe under asyncio's cooperative scheduling.
        Do not add ``await`` calls between state reads and this method.
        """
        self._state = replace(self._state, **fields)

    def _persist(self) -> None:
        """Write-through to ``mcp_session.json`` for restart recovery.

        Structurally gated: only executes when ``is_mcp_process=True``
        (the MCP server is the sole writer per spec).
        """
        if self._is_mcp_process and self._session_file:
            try:
                self._session_file.write_session(
                    sampling_capable=self._state.sampling_capable is True,
                )
            except Exception:
                logger.warning("Failed to persist routing state to mcp_session.json", exc_info=True)

    def _broadcast_state_change(self, event: str) -> None:
        """Push routing_state_changed to all SSE subscribers."""
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
        """Recover state from ``mcp_session.json`` on startup.

        Returns safe defaults if the file is missing, corrupt, or
        unreadable.  Never raises — constructor must always succeed.
        """
        _defaults = RoutingState(
            provider=None,
            provider_name=None,
            sampling_capable=None,
            mcp_connected=False,
            last_capability_update=None,
            last_activity=None,
        )
        try:
            from app.services.mcp_session_file import MCPSessionFile as _MCPSessionFile

            self._session_file = _MCPSessionFile(self._data_dir)
            data = self._session_file.read()
        except Exception:
            logger.debug("Failed to initialize MCPSessionFile for recovery", exc_info=True)
            return _defaults

        if data is None:
            logger.info("routing.recovery no session file — starting with defaults")
            return _defaults

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

        logger.info(
            "routing.recovery sampling_capable=%s mcp_connected=%s last_activity=%s",
            sampling, connected, last_activity,
        )
        return RoutingState(
            provider=None,  # Provider set separately via set_provider()
            provider_name=None,
            sampling_capable=sampling,
            mcp_connected=connected,
            last_capability_update=None,
            last_activity=last_activity,
        )
