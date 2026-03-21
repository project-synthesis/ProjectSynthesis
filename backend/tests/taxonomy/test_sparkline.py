"""Tests for sparkline computation (Spec 10.1)."""

from app.services.taxonomy.sparkline import (
    compute_sparkline_data,
    lttb_downsample,
)


class TestLTTB:
    def test_no_downsampling_needed(self):
        values = [0.5, 0.6, 0.7]
        result = lttb_downsample(values, 5)
        assert result == values

    def test_preserves_first_and_last(self):
        values = list(range(100))
        result = lttb_downsample([float(v) for v in values], 10)
        assert result[0] == 0.0
        assert result[-1] == 99.0
        assert len(result) == 10

    def test_preserves_peaks(self):
        # Sawtooth: the LTTB algorithm should keep the peak
        values = [0.0, 0.1, 0.2, 1.0, 0.2, 0.1, 0.0]
        result = lttb_downsample(values, 5)
        assert 1.0 in result

    def test_target_two_returns_first_and_last(self):
        values = [0.1, 0.5, 0.9, 0.3, 0.7]
        result = lttb_downsample(values, 2)
        assert result == [0.1, 0.7]

    def test_exact_boundary_no_downsampling(self):
        values = [0.1, 0.2, 0.3]
        result = lttb_downsample(values, 3)
        assert result == values


class TestComputeSparklineData:
    def test_empty_returns_empty(self):
        result = compute_sparkline_data([])
        assert result.point_count == 0

    def test_single_value(self):
        result = compute_sparkline_data([0.8])
        assert result.point_count == 1
        assert result.current == 0.8
        assert result.trend == 0.0

    def test_identical_values_flat_line(self):
        result = compute_sparkline_data([0.5, 0.5, 0.5])
        assert result.point_count == 3
        assert result.trend == 0.0
        assert all(p == 0.5 for p in result.normalized)

    def test_nan_values_filtered(self):
        result = compute_sparkline_data([0.5, float('nan'), 0.7])
        assert result.point_count == 2
        assert result.current == 0.7

    def test_negative_clamped_to_zero(self):
        result = compute_sparkline_data([-0.5, 0.3, 0.8])
        assert result.raw_values[0] == 0.0

    def test_above_one_clamped(self):
        result = compute_sparkline_data([0.5, 1.5, 0.8])
        assert result.raw_values[1] == 1.0

    def test_increasing_trend_positive(self):
        result = compute_sparkline_data([0.3, 0.4, 0.5, 0.6, 0.7])
        assert result.trend > 0.0

    def test_decreasing_trend_negative(self):
        result = compute_sparkline_data([0.7, 0.6, 0.5, 0.4, 0.3])
        assert result.trend < 0.0

    def test_downsamples_when_exceeds_max(self):
        values = [0.5 + i * 0.001 for i in range(100)]
        result = compute_sparkline_data(values, max_points=20)
        assert result.point_count == 20
