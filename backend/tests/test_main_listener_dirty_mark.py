"""Gap B: cross-process taxonomy_changed events must mark clusters dirty.

When ``OptimizationService.delete_optimizations()`` runs in a process that
does not own the live ``TaxonomyEngine`` (tests, CLI scripts, future MCP
admin tools), it publishes ``taxonomy_changed`` via HTTP and falls back to
the cross-process event bridge. The backend listener in ``app.main`` must
consume ``affected_clusters`` from that payload and mark them dirty on the
resident engine — otherwise the warm-path timer fires but Phase 0 skips
with ``decision="no_dirty_clusters"`` and the taxonomy never reconciles.

These tests pin the helper ``_apply_cross_process_dirty_marks`` so the
listener cannot drift back into the "set warm_pending but forget dirty
marks" state.
"""

from __future__ import annotations

import pytest


class _FakeEngine:
    """Minimal stand-in for ``TaxonomyEngine``: records ``mark_dirty`` calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def mark_dirty(self, cluster_id: str, project_id: str | None = None) -> None:
        self.calls.append((cluster_id, project_id))


def test_helper_marks_all_affected_clusters_dirty():
    """Every cluster id in ``affected_clusters`` is passed to ``mark_dirty``."""
    from app.main import _apply_cross_process_dirty_marks

    engine = _FakeEngine()
    payload = {
        "trigger": "bulk_delete",
        "affected_clusters": ["c1", "c2", "c3"],
        "affected_projects": ["p1"],
    }

    _apply_cross_process_dirty_marks(engine, payload)

    marked_ids = {cid for cid, _ in engine.calls}
    assert marked_ids == {"c1", "c2", "c3"}


def test_helper_is_noop_when_engine_is_none():
    """Listener runs in lifespan contexts where engine may not exist yet."""
    from app.main import _apply_cross_process_dirty_marks

    # Must not raise — engine is None on startup race or test stubs.
    _apply_cross_process_dirty_marks(None, {"affected_clusters": ["c1"]})


def test_helper_is_noop_when_affected_clusters_missing():
    """Events from non-delete triggers (e.g. domain_created) lack the key."""
    from app.main import _apply_cross_process_dirty_marks

    engine = _FakeEngine()
    _apply_cross_process_dirty_marks(engine, {"trigger": "domain_created"})
    assert engine.calls == []


def test_helper_tolerates_non_dict_payload():
    """Defensive: never crash the listener on malformed events."""
    from app.main import _apply_cross_process_dirty_marks

    engine = _FakeEngine()
    _apply_cross_process_dirty_marks(engine, None)  # type: ignore[arg-type]
    _apply_cross_process_dirty_marks(engine, "not-a-dict")  # type: ignore[arg-type]
    assert engine.calls == []


def test_helper_skips_non_string_cluster_ids():
    """Only string ids are marked — guards against bad producers."""
    from app.main import _apply_cross_process_dirty_marks

    engine = _FakeEngine()
    _apply_cross_process_dirty_marks(
        engine,
        {"affected_clusters": ["c1", None, 42, "", "c2"]},
    )
    marked_ids = {cid for cid, _ in engine.calls}
    assert marked_ids == {"c1", "c2"}


def test_helper_swallows_mark_dirty_exceptions():
    """A buggy engine must never take the listener down."""
    from app.main import _apply_cross_process_dirty_marks

    class _BrokenEngine:
        def mark_dirty(self, *_args, **_kwargs):
            raise RuntimeError("engine down")

    # Should not raise.
    _apply_cross_process_dirty_marks(
        _BrokenEngine(),
        {"affected_clusters": ["c1", "c2"]},
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
