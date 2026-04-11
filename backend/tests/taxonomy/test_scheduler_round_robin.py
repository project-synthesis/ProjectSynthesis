"""Tests for AdaptiveScheduler budget mode via decide_mode() integration."""

from app.services.taxonomy.engine import AdaptiveScheduler


def _exit_bootstrap(scheduler: AdaptiveScheduler) -> None:
    """Feed 10 measurements so scheduler exits bootstrap phase."""
    for i in range(10):
        scheduler.record(dirty_count=5 + i, duration_ms=500 + i * 100)


class TestBudgetModeIntegration:
    """Test budget allocation end-to-end through decide_mode()."""

    def test_both_projects_get_scoped_clusters(self):
        """In budget mode, scoped_dirty_ids contains clusters from ALL projects."""
        scheduler = AdaptiveScheduler()
        _exit_bootstrap(scheduler)
        dirty_ids = {f"c{i}" for i in range(50)}
        proj_a = {f"c{i}" for i in range(30)}
        proj_b = {f"c{i}" for i in range(30, 50)}
        decision = scheduler.decide_mode(dirty_ids, {"proj-A": proj_a, "proj-B": proj_b})
        assert decision.is_round_robin
        # Both projects contribute to scoped set
        assert decision.scoped_dirty_ids & proj_a
        assert decision.scoped_dirty_ids & proj_b

    def test_larger_project_gets_proportionally_more(self):
        """Proportional allocation — larger project gets more budget."""
        scheduler = AdaptiveScheduler()
        _exit_bootstrap(scheduler)
        dirty_ids = {f"c{i}" for i in range(50)}
        decision = scheduler.decide_mode(dirty_ids, {
            "proj-A": {f"c{i}" for i in range(40)},
            "proj-B": {f"c{i}" for i in range(40, 50)},
        })
        assert decision.project_budgets["proj-A"] > decision.project_budgets["proj-B"]

    def test_starvation_boost_through_decide_mode(self):
        """Starved project gets served even through full decide_mode path."""
        scheduler = AdaptiveScheduler()
        _exit_bootstrap(scheduler)
        scheduler._skip_counts["proj-B"] = 4  # starved
        dirty_ids = {f"c{i}" for i in range(50)}
        decision = scheduler.decide_mode(dirty_ids, {
            "proj-A": {f"c{i}" for i in range(45)},
            "proj-B": {f"c{i}" for i in range(45, 50)},
        })
        assert decision.project_budgets["proj-B"] >= 1
        assert scheduler._skip_counts.get("proj-B", 0) == 0

    def test_all_dirty_clears_stale_budgets(self):
        """Transitioning from budget mode to all-dirty clears stale state."""
        scheduler = AdaptiveScheduler()
        _exit_bootstrap(scheduler)
        # Trigger budget mode first
        dirty_ids = {f"c{i}" for i in range(50)}
        scheduler.decide_mode(dirty_ids, {
            "proj-A": {f"c{i}" for i in range(30)},
            "proj-B": {f"c{i}" for i in range(30, 50)},
        })
        assert scheduler._last_project_budgets is not None
        # Now trigger all-dirty
        scheduler.decide_mode({"c1"})
        snap = scheduler.snapshot()
        assert snap["mode"] == "all_dirty"
        assert snap["project_budgets"] is None
        assert snap["dirty_by_project_counts"] == {}
        assert snap["starvation_counters"] == {}

    def test_legacy_project_gets_budget(self):
        """Clusters with project_id=None (grouped as 'legacy') get a budget."""
        scheduler = AdaptiveScheduler()
        _exit_bootstrap(scheduler)
        dirty_ids = {f"c{i}" for i in range(50)}
        decision = scheduler.decide_mode(dirty_ids, {
            "legacy": {f"c{i}" for i in range(20)},
            "proj-A": {f"c{i}" for i in range(20, 50)},
        })
        assert decision.project_budgets["legacy"] >= 1
        assert decision.scoped_dirty_ids & {f"c{i}" for i in range(20)}
