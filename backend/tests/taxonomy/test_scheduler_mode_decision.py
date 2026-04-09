"""Tests for AdaptiveScheduler mode decision (Phase 3A)."""

from app.services.taxonomy.engine import AdaptiveScheduler, SchedulerDecision


class TestModeDecision:
    def test_none_dirty_ids_returns_all_dirty(self):
        scheduler = AdaptiveScheduler()
        decision = scheduler.decide_mode(None)
        assert decision.mode == "all_dirty"
        assert not decision.is_round_robin

    def test_below_boundary_returns_all_dirty(self):
        scheduler = AdaptiveScheduler()
        # Bootstrap boundary is 20
        decision = scheduler.decide_mode({"c1", "c2"})
        assert decision.mode == "all_dirty"

    def test_above_boundary_with_projects_returns_round_robin(self):
        scheduler = AdaptiveScheduler()
        # Feed 10 measurements to exit bootstrap
        for i in range(10):
            scheduler.record(dirty_count=5 + i, duration_ms=500 + i * 100)
        # Compute boundary — should be small enough that 50 dirty exceeds it
        dirty_ids = {f"c{i}" for i in range(50)}
        dirty_by_project = {
            "proj-A": {f"c{i}" for i in range(30)},
            "proj-B": {f"c{i}" for i in range(30, 50)},
        }
        decision = scheduler.decide_mode(dirty_ids, dirty_by_project)
        assert decision.mode == "round_robin"
        assert decision.is_round_robin
        assert decision.project_id in ("proj-A", "proj-B")
        assert decision.scoped_dirty_ids is not None

    def test_above_boundary_without_projects_returns_all_dirty(self):
        scheduler = AdaptiveScheduler()
        for i in range(10):
            scheduler.record(dirty_count=5 + i, duration_ms=500 + i * 100)
        dirty_ids = {f"c{i}" for i in range(50)}
        decision = scheduler.decide_mode(dirty_ids, None)
        assert decision.mode == "all_dirty"

    def test_scheduler_decision_property(self):
        d = SchedulerDecision("round_robin", "proj-A", {"c1"})
        assert d.is_round_robin
        d2 = SchedulerDecision("all_dirty")
        assert not d2.is_round_robin
