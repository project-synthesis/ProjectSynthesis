"""Tests for AdaptiveScheduler boundary computation (Phase 3A)."""

from app.services.taxonomy.engine import AdaptiveScheduler


class TestBoundaryComputation:
    def test_bootstrap_returns_default(self):
        scheduler = AdaptiveScheduler()
        assert scheduler._compute_boundary() == 20

    def test_boundary_after_bootstrap(self):
        scheduler = AdaptiveScheduler()
        for i in range(10):
            scheduler.record(dirty_count=10 + i * 5, duration_ms=1000 + i * 500)
        boundary = scheduler._compute_boundary()
        assert boundary > 0
        assert boundary < 999

    def test_negative_slope_returns_high(self):
        scheduler = AdaptiveScheduler()
        for i in range(10):
            scheduler.record(dirty_count=10 + i * 5, duration_ms=5000 - i * 200)
        assert scheduler._compute_boundary() == 999

    def test_degenerate_all_same_dirty_count(self):
        scheduler = AdaptiveScheduler()
        for _ in range(10):
            scheduler.record(dirty_count=50, duration_ms=3000)
        assert scheduler._compute_boundary() == 20  # fallback
