"""Tests for AdaptiveScheduler per-project budget allocation."""

from app.services.taxonomy.engine import AdaptiveScheduler


class TestBudgetAllocation:
    def test_proportional_50_50(self):
        """Equal split gives equal budgets."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "A": {f"c{i}" for i in range(25)},
            "B": {f"c{i}" for i in range(25, 50)},
        }
        budgets, scoped = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        assert budgets["A"] == 10
        assert budgets["B"] == 10
        assert len(scoped) == 20

    def test_proportional_skewed(self):
        """80/20 split gives proportional budgets respecting MIN_QUOTA."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "A": {f"c{i}" for i in range(80)},
            "B": {f"c{i}" for i in range(80, 100)},
        }
        budgets, scoped = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        # A: round(80/100*20)=16, B: round(20/100*20)=4 >= MIN_QUOTA
        assert budgets["A"] == 16
        assert budgets["B"] == 4
        assert len(scoped) == 20

    def test_min_quota_floor(self):
        """Small project gets MIN_QUOTA floor (capped at dirty count)."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "big": {f"c{i}" for i in range(97)},
            "tiny": {f"c{i}" for i in range(97, 100)},
        }
        budgets, _ = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        # tiny: round(3/100*20)=1, floored to MIN_QUOTA=3, capped at dirty=3
        assert budgets["tiny"] == 3
        assert budgets["big"] >= 1

    def test_budget_capped_at_dirty_count(self):
        """Project with 2 dirty gets at most 2, even with MIN_QUOTA=3."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "big": {f"c{i}" for i in range(50)},
            "small": {"c50", "c51"},
        }
        budgets, _ = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        assert budgets["small"] == 2  # capped at actual dirty count

    def test_single_project_gets_full_budget(self):
        """Single project gets min(boundary, dirty_count)."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {"only": {f"c{i}" for i in range(50)}}
        budgets, scoped = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        assert budgets["only"] == 20
        assert len(scoped) == 20

    def test_single_project_small_dirty(self):
        """Single project with fewer dirty than boundary gets all of them."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {"only": {f"c{i}" for i in range(5)}}
        budgets, scoped = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        assert budgets["only"] == 5
        assert len(scoped) == 5

    def test_starvation_boost(self):
        """Starved project steals from largest non-starved donor."""
        scheduler = AdaptiveScheduler()
        scheduler._skip_counts["small"] = 4  # starved (>= limit of 3)
        dirty_by_project = {
            "big": {f"c{i}" for i in range(90)},
            "small": {f"c{i}" for i in range(90, 95)},
        }
        budgets, _ = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        # small: round(5/95*20)=1, floored to MIN_QUOTA=3
        assert budgets["small"] >= 3

    def test_zero_dirty_empty(self):
        """No dirty clusters -> empty budgets."""
        scheduler = AdaptiveScheduler()
        budgets, scoped = scheduler._allocate_budgets({}, boundary=20)
        assert budgets == {}
        assert scoped == set()

    def test_many_projects_floors_exceed_boundary(self):
        """When many projects' MIN_QUOTA floors sum > boundary, floors win."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            f"p{i}": {f"c{i}_{j}" for j in range(5)}
            for i in range(10)
        }
        # 10 projects * MIN_QUOTA=3 = 30 > boundary=20
        budgets, scoped = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        for pid in dirty_by_project:
            assert budgets[pid] >= 3
        assert sum(budgets.values()) >= 30

    def test_scoped_ids_equals_budget_sum(self):
        """scoped_dirty_ids size matches sum of all budgets."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "A": {f"c{i}" for i in range(30)},
            "B": {f"c{i}" for i in range(30, 50)},
        }
        budgets, scoped = scheduler._allocate_budgets(dirty_by_project, boundary=15)
        assert len(scoped) == sum(budgets.values())

    def test_skip_counts_reset_for_served(self):
        """All projects receiving budget > 0 get their skip count reset."""
        scheduler = AdaptiveScheduler()
        scheduler._skip_counts = {"A": 2, "B": 1}
        dirty_by_project = {
            "A": {f"c{i}" for i in range(20)},
            "B": {f"c{i}" for i in range(20, 30)},
        }
        scheduler._allocate_budgets(dirty_by_project, boundary=10)
        assert scheduler._skip_counts["A"] == 0
        assert scheduler._skip_counts["B"] == 0

    def test_stale_skip_counts_cleaned(self):
        """Stale skip_counts entries for unlinked projects are removed."""
        scheduler = AdaptiveScheduler()
        scheduler._skip_counts = {"gone": 2, "A": 1}
        dirty_by_project = {
            "A": {f"c{i}" for i in range(10)},
        }
        scheduler._allocate_budgets(dirty_by_project, boundary=10)
        assert "gone" not in scheduler._skip_counts
        assert scheduler._skip_counts["A"] == 0

    def test_legacy_pool_treated_as_project(self):
        """Clusters with project_id=None grouped as 'legacy' get normal budget."""
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "legacy": {f"c{i}" for i in range(20)},
            "proj-A": {f"c{i}" for i in range(20, 50)},
        }
        budgets, scoped = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        assert "legacy" in budgets
        assert budgets["legacy"] >= 1
        # Legacy clusters appear in scoped set
        assert scoped & dirty_by_project["legacy"]

    def test_new_project_gets_immediate_quota(self):
        """New project (no skip_counts entry) gets proportional quota immediately."""
        scheduler = AdaptiveScheduler()
        # Existing project has accumulated state
        scheduler._skip_counts["existing"] = 1
        dirty_by_project = {
            "existing": {f"c{i}" for i in range(30)},
            "new_proj": {f"c{i}" for i in range(30, 50)},
        }
        budgets, _ = scheduler._allocate_budgets(dirty_by_project, boundary=20)
        # New project gets proportional quota, not penalized
        assert budgets["new_proj"] >= scheduler._MIN_QUOTA


class TestFairnessGuarantee:
    def test_all_projects_served_every_cycle(self):
        """Over 10 cycles, every project with dirty clusters is served."""
        scheduler = AdaptiveScheduler()
        served_counts = {"A": 0, "B": 0, "C": 0}
        for _ in range(10):
            dirty_by_project = {
                "A": {f"c{i}" for i in range(50)},
                "B": {f"c{i}" for i in range(50, 53)},
                "C": {f"c{i}" for i in range(53, 55)},
            }
            budgets, _ = scheduler._allocate_budgets(dirty_by_project, boundary=20)
            for pid in served_counts:
                if budgets.get(pid, 0) > 0:
                    served_counts[pid] += 1
        # All projects should be served every cycle
        for pid, count in served_counts.items():
            assert count == 10, f"{pid} was only served {count}/10 cycles"

    def test_proportionality_over_time(self):
        """Larger projects get proportionally more budget over multiple cycles."""
        scheduler = AdaptiveScheduler()
        total_budget = {"big": 0, "small": 0}
        for _ in range(20):
            dirty_by_project = {
                "big": {f"c{i}" for i in range(80)},
                "small": {f"c{i}" for i in range(80, 100)},
            }
            budgets, _ = scheduler._allocate_budgets(dirty_by_project, boundary=20)
            total_budget["big"] += budgets["big"]
            total_budget["small"] += budgets["small"]
        # big should get roughly 4x small's total budget
        ratio = total_budget["big"] / total_budget["small"]
        assert 3.0 <= ratio <= 5.0

    def test_starvation_counter_never_exceeds_limit(self):
        """No project's starvation counter exceeds _STARVATION_LIMIT."""
        scheduler = AdaptiveScheduler()
        for _ in range(20):
            dirty_by_project = {
                "A": {f"c{i}" for i in range(50)},
                "B": {f"c{i}" for i in range(50, 53)},
                "C": {f"c{i}" for i in range(53, 60)},
            }
            scheduler._allocate_budgets(dirty_by_project, boundary=20)
            for pid in dirty_by_project:
                assert scheduler._skip_counts.get(pid, 0) <= scheduler._STARVATION_LIMIT, (
                    f"{pid} starvation counter {scheduler._skip_counts.get(pid, 0)} "
                    f"exceeds limit {scheduler._STARVATION_LIMIT}"
                )
