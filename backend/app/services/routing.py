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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from app.providers.base import LLMProvider
    from app.services.event_bus import EventBus
    from app.services.mcp_session_file import MCPSessionFile

# Type alias for the cross-process notification callback.
# Signature: (event_type: str, payload: dict) -> None
# The callback is responsible for scheduling async work if needed.
CrossProcessNotify = Callable[[str, Any], None]


class RoutingStatePayload(TypedDict):
    """Shape of ``routing_state_changed`` event payloads.

    Used in ``_broadcast_state_change`` and ``sync_from_event`` to ensure
    consistent structure across SSE events and cross-process messages.
    """

    trigger: str
    provider: str | None
    sampling_capable: bool | None
    mcp_connected: bool
    available_tiers: list[str]


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model — all frozen (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingState:
    """Snapshot of the server's capability state at the time of a request.

    Note: ``provider`` and ``provider_name`` are set at startup via
    ``set_provider()`` and never persisted to ``mcp_session.json``.
    They live only in memory and are re-detected on each restart.

    Attributes:
        provider: The detected LLM provider instance, or None.
        provider_name: Human-readable provider name (e.g. "claude_cli").
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
    rate_limited: bool = False
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
    providers_by_phase: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------


def _can_sample(state: RoutingState, ctx: RoutingContext) -> bool:
    """Whether sampling is available for the current request."""
    return (
        ctx.caller == "mcp"
        and state.sampling_capable is True
        and state.mcp_connected
    )


def _can_internal(state: RoutingState) -> bool:
    """Whether an internal provider is available and not rate-limited."""
    return state.provider is not None and not state.rate_limited


# Tier chain: each entry is (tier, condition, reason).
# The resolver walks the chain top-to-bottom; first match wins.
# Degradation chains are explicit: when a forced tier fails, the
# resolver walks a fallback chain (sampling → internal → passthrough).
_FALLBACK_CHAIN: list[Literal["sampling", "internal", "passthrough"]] = [
    "sampling",
    "internal",
    "passthrough",
]


def _resolve_with_fallback(
    requested: Literal["sampling", "internal", "passthrough"],
    state: RoutingState,
    ctx: RoutingContext,
    reason_prefix: str,
    *,
    degraded_from_label: str | None = None,
) -> RoutingDecision:
    """Walk the fallback chain starting from *requested* tier.

    Returns the first tier whose preconditions are met, tracking
    ``degraded_from`` when the resolved tier differs from *requested*.

    Args:
        degraded_from_label: Override for ``degraded_from`` when the
            resolved tier differs from *requested*. Defaults to
            *requested* itself.  Use this when the semantic origin of
            the fallback differs from the starting tier (e.g., auto
            tier 4-5 degrades from "internal", not "sampling").
    """
    checkers: dict[str, tuple[bool, str]] = {
        "sampling": (
            _can_sample(state, ctx),
            f"{reason_prefix}, MCP client supports sampling",
        ),
        "internal": (
            _can_internal(state),
            f"{reason_prefix}, using internal provider",
        ),
        "passthrough": (
            True,  # always available
            f"{reason_prefix}, no provider or sampling available",
        ),
    }

    label = degraded_from_label or requested
    # When an explicit label overrides the starting tier, every result
    # is considered degraded (e.g., auto tiers 4-5 are always "degraded
    # from internal" because internal is the natural tier).
    always_degraded = degraded_from_label is not None and degraded_from_label != requested
    start_idx = _FALLBACK_CHAIN.index(requested)
    for tier in _FALLBACK_CHAIN[start_idx:]:
        available, reason = checkers[tier]
        if available:
            providers_by_phase = None
            if tier == "sampling":
                if _can_internal(state):
                    providers_by_phase = {
                        "analyze": "internal",
                        "score": "internal",
                        "suggest": "internal",
                        "optimize": "sampling",
                    }
                else:
                    providers_by_phase = {
                        "analyze": "sampling",
                        "score": "sampling",
                        "suggest": "sampling",
                        "optimize": "sampling",
                    }
            _has_internal = tier == "internal" or (tier == "sampling" and _can_internal(state))
            return RoutingDecision(
                tier=tier,
                provider=state.provider if _has_internal else None,
                provider_name=(
                    state.provider_name if _has_internal
                    else ("mcp_sampling" if tier == "sampling" else None)
                ),
                reason=reason,
                degraded_from=label if (tier != requested or always_degraded) else None,
                providers_by_phase=providers_by_phase,
            )

    # Unreachable — passthrough is always True
    return RoutingDecision(tier="passthrough", reason="fallback")  # pragma: no cover


def resolve_route(state: RoutingState, ctx: RoutingContext) -> RoutingDecision:
    """Determine the execution tier for a pipeline request.

    This is a **pure function** — no I/O, no logging, no side effects.
    All inputs are frozen dataclasses; the output is a frozen dataclass.

    Priority chain (highest to lowest):
      1. ``force_passthrough`` — unconditional passthrough
      2. ``force_sampling`` — sampling with fallback to internal → passthrough
      3. Internal provider available
      4. Auto-sampling (MCP caller + sampling capable, when no internal provider)
      5. Passthrough fallback
    """
    pipeline = ctx.preferences.get("pipeline", {})
    force_passthrough = bool(pipeline.get("force_passthrough"))
    force_sampling = bool(pipeline.get("force_sampling"))

    # Tier 1: force_passthrough always wins (or rate limit active)
    if force_passthrough or state.rate_limited:
        return RoutingDecision(
            tier="passthrough",
            reason="force_passthrough enabled" if force_passthrough else "rate limit active",
        )

    # Tier 2: force_sampling — walk fallback chain from sampling
    if force_sampling:
        return _resolve_with_fallback(
            "sampling", state, ctx, "force_sampling"
        )

    # Tier 3: internal provider — preferred over auto-sampling
    if _can_internal(state):
        return RoutingDecision(
            tier="internal",
            provider=state.provider,
            provider_name=state.provider_name,
            reason="auto, using internal provider",
        )

    # Tier 4: auto-sampling (MCP only, when no internal provider)
    if _can_sample(state, ctx):
        return RoutingDecision(
            tier="sampling",
            provider_name="mcp_sampling",
            reason="auto, MCP client supports sampling",
            degraded_from="internal",
            providers_by_phase={
                "analyze": "sampling",
                "score": "sampling",
                "suggest": "sampling",
                "optimize": "sampling",
            },
        )

    # Tier 5: passthrough fallback (internal was unavailable)
    return RoutingDecision(
        tier="passthrough",
        reason="auto, no provider or sampling available",
        degraded_from="internal",
    )


# ---------------------------------------------------------------------------
# RoutingManager — thin orchestration wrapper
# ---------------------------------------------------------------------------

# Issue-2 debounce window for MCP disconnect broadcasts.  Claude Code and
# similar clients open/close SSE sessions per tool call (every 30-110s on
# observed traffic).  Without debounce, every cycle emits a connect+
# disconnect pair to the frontend, producing visible flicker in the
# status indicator.  3 seconds is long enough to absorb the typical
# tool-call gap (1-2 seconds between close and re-open) without
# noticeably delaying the surfacing of a real, sustained disconnect.
DISCONNECT_DEBOUNCE_SECONDS = 3.0


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
        cross_process_notify: CrossProcessNotify | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._data_dir = data_dir
        self._is_mcp_process = is_mcp_process
        self._cross_process_notify = cross_process_notify
        self._session_file: MCPSessionFile | None = None
        self._disconnect_task: asyncio.Task[None] | None = None

        # Issue-2 debounce: Claude Code (and similar MCP clients) opens
        # and closes SSE sessions per tool call.  Each disconnect was
        # immediately broadcast as ``mcp_disconnect`` to the frontend,
        # producing visible status-indicator flicker every 30-110s
        # (one cycle per Claude Code tool call).  We now defer the
        # disconnect broadcast for ``DISCONNECT_DEBOUNCE_SECONDS`` so
        # a quick re-initialize cancels the pending broadcast.  Only
        # disconnects that PERSIST past the debounce window propagate
        # to event_bus + cross-process publish.
        self._pending_disconnect_task: asyncio.Task[None] | None = None
        self._disconnect_at: datetime | None = None
        # Issue-3 companion to debounce: when the disconnect mutation
        # ran but the BROADCAST was suppressed by a re-initialize
        # within the window, we also need to suppress the matching
        # ``mcp_initialize`` broadcast (otherwise we'd still emit one
        # publish per tool-call cycle — half of the original 994/cycle
        # volume).  We snapshot the pre-disconnect ``sampling_capable``
        # at disconnect time; ``on_mcp_initialize`` consults the
        # snapshot to decide whether the re-init is restoring the same
        # capability state (no externally-visible change → suppress).
        self._pre_disconnect_sampling: bool | None = None

        # Initialize from persistence or defaults
        self._state = self._recover_state()

    @property
    def state(self) -> RoutingState:
        return self._state

    @property
    def available_tiers(self) -> list[str]:
        """Which tiers are currently reachable (for frontend display)."""
        tiers: list[str] = []
        if self._state.provider is not None and not self._state.rate_limited:
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

    def sync_rate_limit(self, is_active: bool) -> None:
        """Called by event consumer when rate limits activate or clear."""
        if self._state.rate_limited == is_active:
            return
        self._state = replace(self._state, rate_limited=is_active)
        logger.info(
            "routing.rate_limit_sync active=%s available_tiers=%s",
            is_active, ",".join(self.available_tiers),
        )
        self._broadcast_state_change("rate_limit_state_changed")

    def on_mcp_initialize(self, sampling_capable: bool) -> None:
        """Called by ASGI middleware when MCP ``initialize`` is intercepted.

        Always trusts the incoming value — no optimistic buffering.  Since
        ``on_mcp_disconnect()`` clears ``sampling_capable`` to ``None`` when
        the last SSE stream closes, stale True values cannot persist across
        client restarts.

        Issue-2 fix: cancels any pending deferred-disconnect broadcast so
        a brief disconnect→reconnect cycle (typical Claude Code per-tool-
        call pattern) doesn't surface as a visible state-flicker pair to
        the frontend.

        Issue-3 fix: if the cancelled disconnect's pre-mutation
        ``sampling_capable`` snapshot equals the incoming value, the
        re-initialize is a no-op from the externally-visible state's
        perspective, so we suppress the initialize broadcast as well.
        Halves the per-tool-call publish volume — the disconnect side
        is already silenced by Issue-2, this silences the matching
        initialize.
        """
        # Cancel a pending deferred disconnect broadcast (debounce hit).
        debounce_hit = False
        pre_disconnect_sampling: bool | None = None
        if self._pending_disconnect_task is not None:
            self._pending_disconnect_task.cancel()
            self._pending_disconnect_task = None
            self._disconnect_at = None
            debounce_hit = True
            pre_disconnect_sampling = self._pre_disconnect_sampling
            self._pre_disconnect_sampling = None
            logger.debug(
                "routing.disconnect_debounce: pending disconnect cancelled by re-initialize",
            )

        now = datetime.now(timezone.utc)
        old_sampling = self._state.sampling_capable
        was_connected = self._state.mcp_connected
        self._update_state(
            sampling_capable=sampling_capable,
            mcp_connected=True,
            last_capability_update=now,
            last_activity=now,
        )
        self._persist()
        # Issue-3: when the debounce just absorbed a disconnect, compare
        # against the snapshot taken BEFORE the disconnect mutation
        # rather than the post-mutation in-memory state (which is
        # ``None`` / ``False`` by definition because the disconnect
        # mutation already ran).  This catches the Claude-Code-per-
        # tool-call pattern where the externally visible state never
        # changed across the disconnect/reconnect pair.
        capability_unchanged_across_debounce = (
            debounce_hit
            and pre_disconnect_sampling == sampling_capable
        )
        if capability_unchanged_across_debounce:
            logger.debug(
                "routing.mcp_initialize sampling_capable=%s "
                "(suppressed — debounce absorbed disconnect, "
                "capability unchanged)",
                sampling_capable,
            )
            return

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

    def on_mcp_disconnect(self) -> None:
        """Called when the last MCP SSE stream closes — all clients disconnected.

        Clears both ``mcp_connected`` and ``sampling_capable``.  This is a
        hard signal: no SSE streams remain, so no client can serve sampling
        requests.  The next ``on_mcp_initialize()`` will set fresh values
        from the reconnecting client's actual capabilities.

        Issue-2 fix: state mutation is immediate (state always reflects
        reality), but the BROADCAST is deferred by
        ``DISCONNECT_DEBOUNCE_SECONDS``.  If ``on_mcp_initialize`` fires
        within the debounce window the deferred broadcast is cancelled,
        suppressing the connect→disconnect→connect flicker observed when
        Claude Code (and similar) opens fresh SSE sessions per tool call.
        Sustained disconnects still propagate after the window elapses.
        """
        if not self._state.mcp_connected and self._state.sampling_capable is None:
            return  # Already fully disconnected — avoid duplicate events
        # Issue-3: snapshot capability BEFORE the disconnect mutation so
        # ``on_mcp_initialize`` can detect "capability unchanged across
        # debounce" and suppress the matching initialize broadcast.
        # We coerce to ``bool`` (or ``None``) — Claude's middleware
        # sends a bool, so this matches the comparison shape exactly.
        self._pre_disconnect_sampling = self._state.sampling_capable
        self._update_state(mcp_connected=False, sampling_capable=None)
        # Delete session file on disconnect — do NOT _persist(), which
        # writes a fresh last_activity that the reconnect detector reads
        # as evidence of a new connection, creating infinite loops.
        if self._session_file:
            self._session_file.delete()

        # Cancel any in-flight deferred disconnect (defensive — shouldn't
        # normally happen since each disconnect is paired with an
        # initialize, but if two disconnects arrive back-to-back we
        # collapse them to a single deferred broadcast).
        if self._pending_disconnect_task is not None:
            self._pending_disconnect_task.cancel()
            self._pending_disconnect_task = None

        # Schedule the deferred broadcast.  Falls back to immediate
        # broadcast if no event loop is available (e.g. sync test paths).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.info("routing.disconnect trigger=sse_closed (immediate — no event loop)")
            self._broadcast_state_change("mcp_disconnect")
            return

        self._disconnect_at = datetime.now(timezone.utc)
        self._pending_disconnect_task = loop.create_task(
            self._deferred_disconnect_broadcast(),
        )
        logger.info(
            "routing.disconnect_deferred trigger=sse_closed debounce=%.1fs",
            DISCONNECT_DEBOUNCE_SECONDS,
        )

    async def _deferred_disconnect_broadcast(self) -> None:
        """Wait for the debounce window then commit the disconnect broadcast.

        Cancellation by ``on_mcp_initialize`` suppresses the broadcast —
        the state itself was already updated at disconnect time, so a
        re-initialize within the window cleanly transitions the state
        without ever surfacing the disconnect to subscribers.
        """
        try:
            await asyncio.sleep(DISCONNECT_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            # Quietly: this is the happy path (re-init within debounce).
            self._pending_disconnect_task = None
            self._disconnect_at = None
            return

        # Commit the disconnect broadcast.  State is already consistent —
        # ``mcp_connected`` was set to False at disconnect time and no
        # initialize has flipped it back, so the broadcast is honest.
        logger.info(
            "routing.disconnect_committed trigger=sse_closed "
            "after debounce=%.1fs (no re-initialize within window)",
            DISCONNECT_DEBOUNCE_SECONDS,
        )
        self._pending_disconnect_task = None
        self._disconnect_at = None
        # The disconnect was honest — clear the snapshot so a later
        # genuine reconnect (different capability) is not silenced.
        self._pre_disconnect_sampling = None
        self._broadcast_state_change("mcp_disconnect")

    def on_sampling_disconnect(self) -> None:
        """Called when the last sampling SSE closes but non-sampling clients remain.

        Only clears ``sampling_capable`` — keeps ``mcp_connected=True`` since
        non-sampling clients (e.g. Claude Code) are still actively connected.
        This prevents incorrectly degrading the general MCP connection state
        when only the sampling bridge disconnects.
        """
        if self._state.sampling_capable is None:
            return  # Already cleared — avoid duplicate events
        self._update_state(sampling_capable=None)
        self._persist()
        logger.info("routing.sampling_disconnect trigger=last_sampling_sse_closed")
        self._broadcast_state_change("sampling_disconnect")

    def on_session_invalidated(self) -> None:
        """Called when a stale MCP session is detected (400/404 from transport).

        Clears both ``sampling_capable`` and ``mcp_connected`` since the
        session no longer exists.  Idempotent — multiple 400/404 responses
        in quick succession won't generate duplicate events.

        Issue-2/3 follow-up: cancels any in-flight deferred-disconnect task
        and clears the ``_pre_disconnect_sampling`` snapshot so a 400/404
        landing inside a debounce window does not produce double events
        (deferred disconnect + session_invalidated) for one logical
        disconnect.  The session-invalidated broadcast supersedes the
        pending one.
        """
        # Idempotency early-return: skip duplicate work UNLESS a deferred
        # disconnect is in flight.  In that case the state already reads
        # "fully disconnected" (set by ``on_mcp_disconnect``), but the
        # broadcast hasn't fired yet — we still need to cancel the
        # pending task and emit ``session_invalidated`` instead.
        if (
            self._state.sampling_capable is None
            and not self._state.mcp_connected
            and self._pending_disconnect_task is None
        ):
            return  # Already invalidated — avoid duplicate events
        old_sampling = self._state.sampling_capable
        old_connected = self._state.mcp_connected
        self._update_state(sampling_capable=None, mcp_connected=False)
        if self._pending_disconnect_task is not None:
            self._pending_disconnect_task.cancel()
            self._pending_disconnect_task = None
            self._disconnect_at = None
            self._pre_disconnect_sampling = None
        # Delete session file — same rationale as on_mcp_disconnect.
        if self._session_file:
            self._session_file.delete()
        logger.info(
            "routing.session_invalidated old_sampling=%s old_connected=%s",
            old_sampling, old_connected,
        )
        self._broadcast_state_change("session_invalidated")

    def sync_from_event(self, data: RoutingStatePayload | dict) -> None:
        """Update state from a cross-process ``routing_state_changed`` event.

        Used by the FastAPI backend to keep its own RoutingManager in sync
        with the MCP server's state without reading ``mcp_session.json``.
        Only updates MCP-related fields (never touches provider).

        Uses a sentinel to distinguish "key missing" from ``None`` — the
        ``sampling_capable`` field is legitimately ``None`` after session
        invalidation, and we must sync that.

        Notes:
            - ``mcp_connected=None`` is coerced to ``False`` (clients always
              send an explicit bool or omit the field).
            - ``last_activity`` is only set when ``mcp_connected=True``
              (connecting), not when disconnecting — this ensures the
              disconnect checker can detect subsequent staleness.
        """
        _missing = object()
        mcp_connected = data.get("mcp_connected", _missing)
        sampling_capable = data.get("sampling_capable", _missing)
        if mcp_connected is _missing and sampling_capable is _missing:
            return  # Nothing to sync

        fields: dict[str, Any] = {}
        if mcp_connected is not _missing:
            if mcp_connected is None:
                logger.debug("sync_from_event: coercing mcp_connected=None to False")
            fields["mcp_connected"] = bool(mcp_connected) if mcp_connected is not None else False
        if sampling_capable is not _missing:
            fields["sampling_capable"] = sampling_capable  # May be None, True, or False
        if mcp_connected and mcp_connected is not _missing:
            fields["last_activity"] = datetime.now(timezone.utc)

        old_connected = self._state.mcp_connected
        old_sampling = self._state.sampling_capable
        self._update_state(**fields)

        # Only broadcast if something actually changed
        new_connected = self._state.mcp_connected
        new_sampling = self._state.sampling_capable
        if old_connected != new_connected or old_sampling != new_sampling:
            # Local broadcast only — do NOT fire cross_process_notify
            # (this IS the receiving end of a cross-process notification)
            payload: RoutingStatePayload = {
                "trigger": data.get("trigger", "sync"),
                "provider": self._state.provider_name,
                "sampling_capable": self._state.sampling_capable,
                "mcp_connected": self._state.mcp_connected,
                "available_tiers": self.available_tiers,
            }
            self._event_bus.publish("routing_state_changed", payload)

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
        """Check every 60s for MCP state changes.

        Two modes:
        1. **Connected** — detect staleness and disconnect if activity gap
           exceeds threshold.
        2. **Disconnected** — poll ``mcp_session.json`` for reconnection.
           This is a fallback safety net — the primary reconnection path is
           real-time via the cross-process HTTP event from the MCP server's
           ``on_mcp_initialize()`` → ``_broadcast_state_change()`` →
           ``POST /api/events/_publish`` → ``sync_from_event()``.
           The polling here catches edge cases where the HTTP notification
           is lost (e.g., backend restarted between MCP init and HTTP POST).

        **Cross-process awareness:** The MCP server writes ``last_activity``
        to ``mcp_session.json`` on every POST, but only broadcasts
        cross-process events on state *changes* (connect/disconnect), not on
        routine activity.
        """
        from app.config import MCP_ACTIVITY_STALENESS_SECONDS

        # Grace period: skip reconnection detection for the first 2
        # polling cycles (~60s) to avoid false reconnect_detected from
        # stale session files left by the previous MCP server process.
        _skip_remaining = 2

        while True:
            try:
                await asyncio.sleep(30)

                if _skip_remaining > 0:
                    _skip_remaining -= 1
                    # Still run disconnect detection (for connected state),
                    # just skip reconnection detection (for disconnected state).
                    # Fall through to the disconnect check below.
                    pass

                # ── Reconnection detection (when disconnected) ────────
                if not self._state.mcp_connected and self._session_file and _skip_remaining <= 0:
                    data = self._session_file.read()
                    if data and not self._session_file.is_activity_stale(data):
                        sampling = data.get("sampling_capable", False)
                        try:
                            fresh_activity = datetime.fromisoformat(
                                data["last_activity"],
                            )
                        except (KeyError, ValueError, TypeError):
                            fresh_activity = None
                        if fresh_activity:
                            logger.info(
                                "routing.reconnect_detected "
                                "sampling_capable=%s "
                                "session_file_fresh=True",
                                sampling,
                            )
                            self._update_state(
                                mcp_connected=True,
                                sampling_capable=sampling if sampling else None,
                                last_activity=fresh_activity,
                            )
                            self._broadcast_state_change("reconnect_detected")
                    continue

                # ── Disconnect detection (when connected) ─────────────
                # Use a shorter window (60s) than the startup recovery
                # staleness (300s). The bridge health check runs every 10s,
                # so 60s = 6 missed heartbeats = definitive disconnect.
                _dc_staleness = 60.0

                if self._state.mcp_connected and self._state.last_activity:
                    elapsed = (
                        datetime.now(timezone.utc) - self._state.last_activity
                    ).total_seconds()
                    if elapsed > _dc_staleness:
                        # Before disconnecting, check the session file —
                        # the MCP server may have fresh activity we missed.
                        if self._session_file:
                            data = self._session_file.read()
                            file_fresh = False
                            if data:
                                try:
                                    file_activity = datetime.fromisoformat(
                                        data.get("last_activity", ""),
                                    )
                                    file_elapsed = (
                                        datetime.now(timezone.utc) - file_activity
                                    ).total_seconds()
                                    file_fresh = file_elapsed < _dc_staleness
                                except (ValueError, TypeError):
                                    pass
                            if file_fresh and data:
                                try:
                                    fresh_activity = datetime.fromisoformat(
                                        data["last_activity"],
                                    )
                                except (KeyError, ValueError, TypeError):
                                    fresh_activity = None
                                if fresh_activity:
                                    # Demoted to DEBUG (was INFO).  This logs
                                    # every 30s when in-memory state is stale
                                    # but the MCP session file shows the
                                    # client is still alive — a no-op save,
                                    # not a real state change.  Live-run
                                    # forensics: cycle 11 produced 32 of
                                    # these in 30 minutes, drowning the
                                    # actually-meaningful log signal.
                                    logger.debug(
                                        "routing.disconnect_averted "
                                        "in_memory_stale=%.0fs "
                                        "session_file_fresh=True",
                                        elapsed,
                                    )
                                    self._update_state(
                                        last_activity=fresh_activity,
                                        mcp_connected=True,
                                    )
                                    continue
                        else:
                            logger.debug(
                                "routing.disconnect_check no session file — "
                                "using in-memory staleness only",
                            )
                        logger.info(
                            "routing.disconnect activity_stale=%.0fs threshold=%ds",
                            elapsed, MCP_ACTIVITY_STALENESS_SECONDS,
                        )
                        self._update_state(
                            mcp_connected=False,
                            sampling_capable=None,
                        )
                        # Delete session file on disconnect instead of
                        # persisting.  _persist() writes a fresh last_activity
                        # timestamp, which the reconnect detector then reads
                        # as evidence of a new session — creating an infinite
                        # disconnect → reconnect → disconnect cycle.
                        if self._session_file:
                            self._session_file.delete()
                        self._broadcast_state_change("disconnect")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Disconnect checker iteration failed", exc_info=True)

    # ── Internal helpers ──────────────────────────────────────────────

    def _update_state(self, **fields: Any) -> None:
        """Replace specific fields in the current state.

        Thread-safety note: all callers (``set_provider``, ``on_mcp_initialize``,
        ``on_mcp_activity``, ``on_mcp_disconnect``, ``on_sampling_disconnect``,
        ``on_session_invalidated``, ``sync_from_event``, ``_disconnect_loop``)
        are synchronous between
        their read of ``self._state`` and this write — no ``await`` between
        read and replace.  This is safe under asyncio's cooperative scheduling.
        Do not add ``await`` calls between state reads and this method.
        """
        self._state = replace(self._state, **fields)

    def _persist(self) -> None:
        """Write-through to ``mcp_session.json`` for restart recovery.

        Structurally gated: only executes when ``is_mcp_process=True``
        (the MCP server is the sole writer per spec).  On the FastAPI
        backend (``is_mcp_process=False``), this is a silent no-op —
        the backend relies on cross-process events and session file
        reads instead of writing its own state.
        """
        if self._is_mcp_process and self._session_file:
            try:
                self._session_file.write_session(
                    sampling_capable=self._state.sampling_capable is True,
                )
            except Exception:
                logger.warning("Failed to persist routing state to mcp_session.json", exc_info=True)

    def _broadcast_state_change(self, event: str) -> None:
        """Push routing_state_changed to local event bus and cross-process."""
        payload: RoutingStatePayload = {
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

        # Cross-process notification (MCP → backend event bus → frontend SSE).
        # Fire-and-forget: the callback schedules an async task internally.
        if self._cross_process_notify:
            try:
                self._cross_process_notify("routing_state_changed", payload)
            except Exception:
                logger.debug(
                    "Cross-process notification failed for event=%s", event,
                    exc_info=True,
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

        try:
            # Issue-4 fix: trust a fresh session file when the FastAPI
            # backend restarts (e.g. uvicorn auto-reload) while the MCP
            # server process is still up.  Without this, the backend
            # sat with sampling_capable=None for ~60s — the disconnect-
            # checker grace window — even though the MCP server already
            # had an authenticated client.  Sampling tier 4 silently
            # degraded to passthrough during that window for any MCP
            # tool call landing in the gap.
            #
            # We only trust the file when BOTH freshness signals agree:
            #   1. ``is_capability_fresh`` — written_at within 30 min
            #      (so we're not picking up a day-old crash artifact)
            #   2. ``not detect_disconnect`` — sse_streams > 0 (or, on
            #      legacy files without sse_streams, last_activity
            #      within MCP_ACTIVITY_STALENESS_SECONDS)
            #
            # If either signal disagrees, we keep the conservative
            # behaviour and wait for a live initialize handshake.
            file_sampling = data.get("sampling_capable", False)
            file_connected = not self._session_file.detect_disconnect(data)
            file_capability_fresh = self._session_file.is_capability_fresh(data)

            last_activity = None
            if "last_activity" in data:
                try:
                    last_activity = datetime.fromisoformat(data["last_activity"])
                except (ValueError, TypeError):
                    pass

            if file_connected and file_capability_fresh:
                logger.info(
                    "routing.recovery file_sampling=%s file_connected=True "
                    "capability_fresh=True last_activity=%s "
                    "(trusting file — MCP server appears live)",
                    file_sampling, last_activity,
                )
                return RoutingState(
                    provider=None,  # Provider set separately via set_provider()
                    provider_name=None,
                    sampling_capable=bool(file_sampling) if file_sampling else None,
                    mcp_connected=True,
                    last_capability_update=last_activity,
                    last_activity=last_activity,
                )

            logger.info(
                "routing.recovery file_sampling=%s file_connected=%s "
                "capability_fresh=%s last_activity=%s "
                "(ignoring — waiting for live initialize handshake)",
                file_sampling, file_connected, file_capability_fresh, last_activity,
            )
            return RoutingState(
                provider=None,  # Provider set separately via set_provider()
                provider_name=None,
                sampling_capable=None,   # Always unknown until live handshake
                mcp_connected=False,     # Always disconnected until live SSE
                last_capability_update=None,
                last_activity=last_activity,
            )
        except Exception:
            logger.warning(
                "routing.recovery corrupt session data — starting with defaults",
                exc_info=True,
            )
            return _defaults
