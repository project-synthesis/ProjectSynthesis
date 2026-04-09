"""Tests for AdaptiveScheduler round-robin priority selection (Phase 3A)."""

from app.services.taxonomy.engine import AdaptiveScheduler


class TestPrioritySelection:
    def test_picks_project_with_most_dirty(self):
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "proj-A": {"c1", "c2", "c3"},
            "proj-B": {"c4"},
        }
        pid, scoped = scheduler._pick_priority_project(dirty_by_project)
        assert pid == "proj-A"
        assert scoped == {"c1", "c2", "c3"}

    def test_starvation_guard(self):
        scheduler = AdaptiveScheduler()
        # Skip proj-B 3 times
        scheduler._skip_counts["proj-B"] = 3

        dirty_by_project = {
            "proj-A": {"c1", "c2", "c3", "c4", "c5"},  # more dirty
            "proj-B": {"c6"},  # fewer dirty but starved
        }
        pid, scoped = scheduler._pick_priority_project(dirty_by_project)
        assert pid == "proj-B"  # starved project wins
        assert scheduler._skip_counts["proj-B"] == 0  # reset

    def test_tiebreaker_longest_starved(self):
        scheduler = AdaptiveScheduler()
        scheduler._skip_counts["proj-A"] = 3
        scheduler._skip_counts["proj-B"] = 5  # longer starved

        dirty_by_project = {
            "proj-A": {"c1"},
            "proj-B": {"c2"},
        }
        pid, _ = scheduler._pick_priority_project(dirty_by_project)
        assert pid == "proj-B"  # longest-starved wins

    def test_skip_counts_updated(self):
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "proj-A": {"c1", "c2", "c3"},
            "proj-B": {"c4"},
            "proj-C": {"c5"},
        }
        pid, _ = scheduler._pick_priority_project(dirty_by_project)
        assert pid == "proj-A"  # most dirty
        assert scheduler._skip_counts["proj-A"] == 0
        assert scheduler._skip_counts["proj-B"] == 1
        assert scheduler._skip_counts["proj-C"] == 1

    def test_consecutive_skips_accumulate(self):
        scheduler = AdaptiveScheduler()
        dirty_by_project = {
            "proj-A": {"c1", "c2"},
            "proj-B": {"c3"},
        }
        # Round 1: A wins
        scheduler._pick_priority_project(dirty_by_project)
        assert scheduler._skip_counts["proj-B"] == 1

        # Round 2: A wins again
        scheduler._pick_priority_project(dirty_by_project)
        assert scheduler._skip_counts["proj-B"] == 2

        # Round 3: A wins again
        scheduler._pick_priority_project(dirty_by_project)
        assert scheduler._skip_counts["proj-B"] == 3

        # Round 4: B should be starved and win
        pid, _ = scheduler._pick_priority_project(dirty_by_project)
        assert pid == "proj-B"
