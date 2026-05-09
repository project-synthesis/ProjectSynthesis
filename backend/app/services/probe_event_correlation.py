"""ContextVar-based probe_id correlation for taxonomy events (Tier 1, v0.4.12).

Foundation P3 (v0.4.18, Cycle 14) — re-import switched from the legacy
``probe_service`` module to ``probe_common``, the canonical home of the
ContextVar after the v0.4.17 P2 split. ``probe_service.py`` continues
to re-export ``current_probe_id`` for backward-compat, but it is no
longer where the ContextVar lives.
"""
from app.services.probe_common import current_probe_id  # noqa: F401

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
