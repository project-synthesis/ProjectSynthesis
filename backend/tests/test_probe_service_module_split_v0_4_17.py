"""v0.4.17 P2 — verify the 2 currently-imported probe_service symbols still resolve post-split."""

import importlib


def test_backward_compat_imports_from_probe_service():
    """The 2 external import sites must continue to work, with object identity preserved.

    Per spec § 4.1 — pins the public API contract:
    - from app.services.probe_service import ProbeService (6 import sites)
    - from app.services.probe_service import current_probe_id (1 import site)

    The class ProbeService stays at probe_service.py canonical home.
    The ContextVar current_probe_id moves to probe_common.py and is
    re-imported in probe_service.py for backward compat.
    """
    legacy = importlib.import_module("app.services.probe_service")
    common = importlib.import_module("app.services.probe_common")

    # 1. ProbeService class — canonical home stays at probe_service.py
    assert hasattr(legacy, "ProbeService"), "probe_service.ProbeService no longer importable"
    assert isinstance(legacy.ProbeService, type), "probe_service.ProbeService is not a class"

    # 2. current_probe_id — canonical home moved to probe_common.py; re-exported here
    assert hasattr(legacy, "current_probe_id"), "probe_service.current_probe_id no longer importable"
    assert hasattr(common, "current_probe_id"), "probe_common.current_probe_id missing"
    assert legacy.current_probe_id is common.current_probe_id, (
        "ContextVar identity broken — probe_service.current_probe_id is not the same object "
        "as probe_common.current_probe_id; redeclaration detected"
    )

    # 3. Sanity: ContextVar is functional
    from contextvars import ContextVar
    assert isinstance(legacy.current_probe_id, ContextVar)
