"""Tests for the current_run_id ContextVar rebind (Foundation P3, v0.4.18).

Covers spec section 9 category 11 (cross-process correlation) — partial — 4 tests.
"""
from __future__ import annotations


def test_current_run_id_is_current_probe_id() -> None:
    """The two names alias the same ContextVar object."""
    from app.services.probe_common import current_probe_id, current_run_id
    assert current_run_id is current_probe_id


def test_current_run_id_default_is_none() -> None:
    """Default value matches today's current_probe_id behavior."""
    from app.services.probe_common import current_run_id
    assert current_run_id.get() is None


def test_legacy_import_paths_resolve_to_same_object() -> None:
    """Identity invariant for tests/test_probe_service_module_split_v0_4_17.py."""
    from app.services import probe_common, probe_event_correlation, probe_service
    assert probe_common.current_probe_id is probe_service.current_probe_id
    assert probe_common.current_probe_id is probe_event_correlation.current_probe_id
    assert probe_common.current_run_id is probe_event_correlation.current_probe_id


def test_set_value_observable_through_all_aliases() -> None:
    """Setting current_run_id reflects through every name."""
    from app.services.probe_common import current_run_id
    from app.services.probe_event_correlation import current_probe_id as corr_alias
    from app.services.probe_service import current_probe_id as svc_alias

    token = current_run_id.set("test-run-123")
    try:
        assert current_run_id.get() == "test-run-123"
        assert svc_alias.get() == "test-run-123"
        assert corr_alias.get() == "test-run-123"
    finally:
        current_run_id.reset(token)
