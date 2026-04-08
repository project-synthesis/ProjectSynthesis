"""Tests for ADR-005 model additions."""

from app.models import GlobalPattern, Optimization


def test_optimization_has_project_id_field():
    """Optimization model has project_id column."""
    assert hasattr(Optimization, "project_id")


def test_global_pattern_model_exists():
    """GlobalPattern model is defined with required fields."""
    gp = GlobalPattern.__table__
    assert "id" in gp.columns
    assert "pattern_text" in gp.columns
    assert "embedding" in gp.columns
    assert "source_cluster_ids" in gp.columns
    assert "source_project_ids" in gp.columns
    assert "cross_project_count" in gp.columns
    assert "global_source_count" in gp.columns
    assert "avg_cluster_score" in gp.columns
    assert "promoted_at" in gp.columns
    assert "last_validated_at" in gp.columns
    assert "state" in gp.columns
