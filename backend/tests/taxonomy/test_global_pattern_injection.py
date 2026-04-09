"""Tests for GlobalPattern injection (Phase 2B)."""

import pytest

from app.services.pattern_injection import InjectedPattern, format_injected_patterns


def test_injected_pattern_has_source_fields():
    """InjectedPattern has source and source_id fields with correct defaults."""
    ip = InjectedPattern(
        pattern_text="test technique",
        cluster_label="test-cluster",
        domain="general",
        similarity=0.8,
    )
    assert ip.source == "cluster"
    assert ip.source_id == ""


def test_injected_pattern_global_source():
    """InjectedPattern can be created with source='global'."""
    ip = InjectedPattern(
        pattern_text="global technique",
        cluster_label="(global)",
        domain="cross-project",
        similarity=0.9,
        source="global",
        source_id="gp-123",
    )
    assert ip.source == "global"
    assert ip.source_id == "gp-123"


def test_format_separates_global_from_cluster():
    """format_injected_patterns creates separate sections for global patterns."""
    patterns = [
        InjectedPattern(
            pattern_text="Cluster technique",
            cluster_label="cluster-A",
            domain="backend",
            similarity=0.8,
            source="cluster",
        ),
        InjectedPattern(
            pattern_text="Global technique",
            cluster_label="(global)",
            domain="cross-project",
            similarity=0.9,
            source="global",
        ),
    ]
    result = format_injected_patterns(patterns)
    assert result is not None
    # Global patterns should appear in the output
    assert "Global technique" in result
    # Cluster patterns should also appear
    assert "Cluster technique" in result
    # Global section header should be present
    assert "Proven Cross-Project Techniques" in result


def test_format_cluster_only():
    """format_injected_patterns works with cluster-only patterns (backward compat)."""
    patterns = [
        InjectedPattern(
            pattern_text="Cluster technique",
            cluster_label="cluster-A",
            domain="backend",
            similarity=0.8,
        ),
    ]
    result = format_injected_patterns(patterns)
    assert result is not None
    assert "Cluster technique" in result
    # Should NOT have the global section header
    assert "Proven Cross-Project Techniques" not in result


def test_format_global_only():
    """format_injected_patterns works with only global patterns."""
    patterns = [
        InjectedPattern(
            pattern_text="Global technique",
            cluster_label="(global)",
            domain="cross-project",
            similarity=0.85,
            source="global",
            source_id="gp-abc",
        ),
    ]
    result = format_injected_patterns(patterns)
    assert result is not None
    assert "Global technique" in result
    assert "Proven Cross-Project Techniques" in result


def test_format_existing_text_merges():
    """format_injected_patterns merges with existing text."""
    patterns = [
        InjectedPattern(
            pattern_text="Global technique",
            cluster_label="(global)",
            domain="cross-project",
            similarity=0.9,
            source="global",
        ),
    ]
    result = format_injected_patterns(patterns, existing_text="Existing patterns here")
    assert result is not None
    assert result.startswith("Existing patterns here")
    assert "Global technique" in result


def test_format_empty_returns_existing():
    """format_injected_patterns with empty list returns existing_text."""
    result = format_injected_patterns([], existing_text="keep me")
    assert result == "keep me"


def test_format_empty_returns_none():
    """format_injected_patterns with empty list and no existing text returns None."""
    result = format_injected_patterns([])
    assert result is None


def test_format_global_sorted_by_similarity():
    """Global patterns are sorted by similarity descending."""
    patterns = [
        InjectedPattern(
            pattern_text="Low relevance",
            cluster_label="(global)",
            domain="cross-project",
            similarity=0.5,
            source="global",
        ),
        InjectedPattern(
            pattern_text="High relevance",
            cluster_label="(global)",
            domain="cross-project",
            similarity=0.9,
            source="global",
        ),
    ]
    result = format_injected_patterns(patterns)
    assert result is not None
    # High relevance should appear before low relevance
    high_pos = result.index("High relevance")
    low_pos = result.index("Low relevance")
    assert high_pos < low_pos
