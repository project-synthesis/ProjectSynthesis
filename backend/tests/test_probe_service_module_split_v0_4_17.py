"""v0.4.17 P2 + Foundation P3 Cycle 14 — backward-compat ContextVar identity.

Originally this test asserted both ``ProbeService`` AND ``current_probe_id``
were re-importable from ``app.services.probe_service``. Foundation P3
Cycle 14 (v0.4.18) retires the ``ProbeService`` class entirely; the
ContextVar identity invariant is the one survivor.

Per spec § 8.3:
    > ``current_probe_id`` ContextVar — Same ``ContextVar`` object exposed
    > under both names from canonical home ``services/probe_common.py``.
    > Object-identity invariant preserved for the existing test
    > ``tests/test_probe_service_module_split_v0_4_17.py:27``.

So this file keeps the identity assertion and drops the class assertion.
The full identity chain across all three call sites (``probe_common`` /
``probe_event_correlation`` / ``probe_service``) is also covered by
``tests/test_run_id_contextvar.py``; this test pins the contract from
its original v0.4.17 P2 framing for documentation continuity.
"""

import importlib


def test_backward_compat_imports_from_probe_service():
    """The ``current_probe_id`` ContextVar must remain importable from
    ``app.services.probe_service`` even though the ``ProbeService`` class
    body has been retired in Foundation P3 Cycle 14.

    Per spec § 8.3 — pins the public API contract for ContextVar identity:

      - from app.services.probe_service import current_probe_id (1 import site)

    The class ProbeService is deleted in Cycle 14; its dispatch logic now
    lives in ``app.services.generators.topic_probe_generator.TopicProbeGenerator``
    (Cycle 6). The ContextVar canonical home stays at ``probe_common.py``;
    ``probe_service.py`` re-imports it for backward-compat.
    """
    legacy = importlib.import_module("app.services.probe_service")
    common = importlib.import_module("app.services.probe_common")

    # Cycle 14 — ProbeService class is gone.
    assert not hasattr(legacy, "ProbeService"), (
        "ProbeService class still present in probe_service.py — "
        "expected deletion per Foundation P3 Cycle 14 (spec § 8.3)"
    )

    # current_probe_id — canonical home in probe_common.py; re-exported here.
    assert hasattr(legacy, "current_probe_id"), (
        "probe_service.current_probe_id no longer importable"
    )
    assert hasattr(common, "current_probe_id"), (
        "probe_common.current_probe_id missing"
    )
    assert legacy.current_probe_id is common.current_probe_id, (
        "ContextVar identity broken — probe_service.current_probe_id is not "
        "the same object as probe_common.current_probe_id; redeclaration detected"
    )

    # Sanity: ContextVar is functional
    from contextvars import ContextVar
    assert isinstance(legacy.current_probe_id, ContextVar)
