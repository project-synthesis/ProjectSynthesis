"""Sparkline computation for Q_system history (Spec 10.1).

LTTB downsampling preserves visual shape when reducing data points.
OLS trend computation normalized to [-1, 1].
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class SparklineData:
    """Sparkline-ready data with normalized points, stats, and trend."""

    normalized: list[float] = field(default_factory=list)
    raw_values: list[float] = field(default_factory=list)
    min: float = 0.0
    max: float = 0.0
    current: float = 0.0
    trend: float = 0.0
    point_count: int = 0


def compute_sparkline_data(
    q_values: list[float],
    max_points: int = 30,
) -> SparklineData:
    """Transform raw Q_system values into sparkline-ready data.

    Edge cases handled: 0 values, NaN/Inf, negatives, >1.0, all identical.
    """
    # 1. Filter invalid
    valid = [v for v in q_values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not valid:
        return SparklineData()

    # 2. Clamp to [0, 1]
    values = [max(0.0, min(1.0, v)) for v in valid]

    # 3. Downsample if needed
    if len(values) > max_points:
        values = lttb_downsample(values, max_points)

    # 4. Statistics
    v_min = min(values)
    v_max = max(values)
    v_current = values[-1]
    v_range = v_max - v_min

    # 5. Normalize to [0, 1]
    if v_range < 1e-9:
        normalized = [0.5] * len(values)
    else:
        normalized = [(v - v_min) / v_range for v in values]

    # 6. Trend
    trend = _compute_trend(values)

    return SparklineData(
        normalized=normalized,
        raw_values=values,
        min=v_min,
        max=v_max,
        current=v_current,
        trend=trend,
        point_count=len(values),
    )


def lttb_downsample(values: list[float], target: int) -> list[float]:
    """Largest Triangle Three Buckets downsampling.

    Reduces N points to target while preserving visual shape.
    Always keeps first and last points. O(N) single pass.
    """
    n = len(values)
    if n <= target:
        return values[:]
    if target < 3:
        # target=2 → first+last, target=1 → first only, target=0 → empty
        return [values[0], values[-1]][:target]

    result = [values[0]]
    bucket_size = (n - 2) / (target - 2)

    prev_idx = 0
    for i in range(1, target - 1):
        bucket_start = int(1 + (i - 1) * bucket_size)
        bucket_end = int(1 + i * bucket_size)
        bucket_end = min(bucket_end, n - 1)

        next_start = int(1 + i * bucket_size)
        next_end = int(1 + (i + 1) * bucket_size)
        next_end = min(next_end, n)

        if next_start >= n:
            next_avg = values[-1]
        else:
            next_slice = values[next_start:next_end]
            next_avg = sum(next_slice) / len(next_slice) if next_slice else values[-1]

        next_x = (next_start + min(next_end, n) - 1) / 2.0

        best_area = -1.0
        best_idx = bucket_start
        prev_val = values[prev_idx]

        for j in range(bucket_start, bucket_end):
            area = abs(
                prev_idx * (values[j] - next_avg)
                + j * (next_avg - prev_val)
                + next_x * (prev_val - values[j])
            )
            if area > best_area:
                best_area = area
                best_idx = j

        result.append(values[best_idx])
        prev_idx = best_idx

    result.append(values[-1])
    return result


def _compute_trend(values: list[float]) -> float:
    """OLS trend normalized to [-1, 1]."""
    n = len(values)
    if n < 2:
        return 0.0

    sum_x = sum_y = sum_xy = sum_x2 = 0.0
    for i, v in enumerate(values):
        x = float(i)
        sum_x += x
        sum_y += v
        sum_xy += x * v
        sum_x2 += x * x

    denominator = n * sum_x2 - sum_x * sum_x
    if abs(denominator) < 1e-12:
        return 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    mean_y = sum_y / n
    if abs(mean_y) < 1e-9:
        return 0.0

    total_change = slope * (n - 1)
    trend = (total_change / mean_y) * 2.0
    return max(-1.0, min(1.0, trend))
