"""Tests for the shared split_cluster() function."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.taxonomy.split import SplitResult, split_cluster


def _rand_emb(dim: int = 384, seed: int = 0) -> bytes:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def test_split_result_dataclass():
    """SplitResult has required fields."""
    r = SplitResult(success=True, children_created=3, noise_reassigned=2, children=[])
    assert r.success is True
    assert r.children_created == 3
    assert r.noise_reassigned == 2


def test_split_result_failure():
    """SplitResult for failed split."""
    r = SplitResult(success=False, children_created=0, noise_reassigned=0, children=[])
    assert r.success is False


def test_split_cluster_is_async():
    """split_cluster must be a coroutine function."""
    import inspect
    assert inspect.iscoroutinefunction(split_cluster)
