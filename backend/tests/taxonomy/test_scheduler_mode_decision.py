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
        assert decision.project_id is None  # no single project in budget mode
        assert decision.scoped_dirty_ids is not None
        assert decision.project_budgets is not None
        assert "proj-A" in decision.project_budgets
        assert "proj-B" in decision.project_budgets

    def test_above_boundary_without_projects_returns_all_dirty(self):
        scheduler = AdaptiveScheduler()
        for i in range(10):
            scheduler.record(dirty_count=5 + i, duration_ms=500 + i * 100)
        dirty_ids = {f"c{i}" for i in range(50)}
        decision = scheduler.decide_mode(dirty_ids, None)
        assert decision.mode == "all_dirty"

    def test_scheduler_decision_property(self):
        d = SchedulerDecision("round_robin", None, {"c1"}, {"proj-A": 1})
        assert d.is_round_robin
        d2 = SchedulerDecision("all_dirty")
        assert not d2.is_round_robin

    def test_all_dirty_resets_all_starvation_counters(self):
        scheduler = AdaptiveScheduler()
        # Include a stale entry for a project not in dirty_by_project
        scheduler._skip_counts = {"proj-A": 2, "proj-B": 1, "gone": 3}
        dirty_by_project = {
            "proj-A": {"c1", "c2"},
            "proj-B": {"c3"},
        }
        # Below boundary (bootstrap=20), so all-dirty mode
        decision = scheduler.decide_mode({"c1", "c2", "c3"}, dirty_by_project)
        assert decision.mode == "all_dirty"
        # ALL starvation counters cleared — including stale entries
        assert len(scheduler._skip_counts) == 0

    def test_none_dirty_ids_resets_starvation(self):
        scheduler = AdaptiveScheduler()
        scheduler._skip_counts = {"proj-A": 2, "proj-B": 3}
        decision = scheduler.decide_mode(None)
        assert decision.mode == "all_dirty"
        # Full-scan cycles clear all starvation counters
        assert len(scheduler._skip_counts) == 0

    def test_all_dirty_clears_stale_last_fields(self):
        """All-dirty mode clears stale project_budgets and dirty_by_project."""
        scheduler = AdaptiveScheduler()
        # Simulate prior round-robin state
        scheduler._last_project_budgets = {"proj-A": 10}
        scheduler._last_dirty_by_project = {"proj-A": {"c1"}}
        scheduler._last_project_id = "proj-A"
        decision = scheduler.decide_mode({"c1"})
        assert decision.mode == "all_dirty"
        snap = scheduler.snapshot()
        assert snap["project_budgets"] is None
        assert snap["dirty_by_project_counts"] == {}
        assert snap["last_project_id"] is None
