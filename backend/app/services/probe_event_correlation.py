"""ContextVar-based probe_id correlation for taxonomy events (Tier 1, v0.4.12).

Foundation P3 (v0.4.18, Cycle 14) — re-import switched from the legacy
``probe_service`` module to ``probe_common``, the canonical home of the
ContextVar after the v0.4.17 P2 split.

v0.4.34 — the ``current_probe_id`` backward-compat alias was retired;
this module now imports ``current_run_id`` directly. The public
``inject_probe_id(context)`` helper name is preserved (it predates the
ContextVar rename and downstream consumers still read ``context.probe_id``
verbatim — semantic key carries the run id of the in-flight probe / seed
run).
"""
from app.services.probe_common import current_run_id  # noqa: F401

__all__ = ["inject_probe_id"]


def inject_probe_id(context: dict[str, object]) -> dict[str, object]:
    """If a run is in flight, copy its id into the event context payload.

    Returns the context unchanged if no run is in flight.
    Returns a new dict with `probe_id` set if the ContextVar is non-None.
    Idempotent: if `context["probe_id"]` already exists, leaves it alone.
    """
    rid = current_run_id.get()
    if rid is not None and "probe_id" not in context:
        return {**context, "probe_id": rid}
    return context
