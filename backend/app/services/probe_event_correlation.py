"""ContextVar-based probe_id correlation for taxonomy events (Tier 1, v0.4.12).

`current_probe_id` is the canonical ContextVar, declared in
`app/services/probe_service.py` (the module that SETS it). This file
re-exports it for ergonomic imports and adds `inject_probe_id(context)`
helper used by `event_logger.log_decision`.
"""
from app.services.probe_service import current_probe_id  # noqa: F401

__all__ = ["current_probe_id", "inject_probe_id"]


def inject_probe_id(context: dict[str, object]) -> dict[str, object]:
    """If a probe is in flight, copy its id into the event context payload.

    Returns the context unchanged if no probe is in flight.
    Returns a new dict with `probe_id` set if the ContextVar is non-None.
    Idempotent: if `context["probe_id"]` already exists, leaves it alone.
    """
    pid = current_probe_id.get()
    if pid is not None and "probe_id" not in context:
        return {**context, "probe_id": pid}
    return context
