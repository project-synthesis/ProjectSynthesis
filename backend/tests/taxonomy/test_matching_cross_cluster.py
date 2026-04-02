"""Tests for cross-cluster pattern matching in PatternMatch."""

from __future__ import annotations

from dataclasses import fields

from app.services.taxonomy.matching import PatternMatch


def test_pattern_match_has_cross_cluster_patterns_field():
    """PatternMatch should have a cross_cluster_patterns list field."""
    field_names = {f.name for f in fields(PatternMatch)}
    assert "cross_cluster_patterns" in field_names


def test_pattern_match_cross_cluster_defaults_empty():
    """cross_cluster_patterns should default to empty list."""
    pm = PatternMatch(
        cluster=None,
        meta_patterns=[],
        similarity=0.0,
        match_level="none",
    )
    assert pm.cross_cluster_patterns == []
    assert isinstance(pm.cross_cluster_patterns, list)
