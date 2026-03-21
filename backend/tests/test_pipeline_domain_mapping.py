"""Tests for domain mapping integration in the pipeline."""

import pytest

from app.schemas.pipeline_contracts import AnalysisResult


def test_analysis_result_accepts_freetext_domain():
    """AnalysisResult.domain should accept any free-text string."""
    result = AnalysisResult(
        task_type="coding",
        weaknesses=["none"],
        strengths=["good"],
        selected_strategy="auto",
        strategy_rationale="test",
        confidence=0.9,
        intent_label="REST API design",
        domain="REST API design",  # free-text, not one of the old 7 values
    )
    assert result.domain == "REST API design"


def test_analysis_result_still_accepts_legacy_domains():
    """Legacy domain values like 'backend' still work as plain strings."""
    result = AnalysisResult(
        task_type="coding",
        weaknesses=["none"],
        strengths=["good"],
        selected_strategy="auto",
        strategy_rationale="test",
        confidence=0.9,
        domain="backend",
    )
    assert result.domain == "backend"


@pytest.mark.asyncio
async def test_taxonomy_mapping_returns_node_id():
    """TaxonomyEngine.map_domain() should return a TaxonomyMapping with node info."""
    from app.services.taxonomy import TaxonomyMapping

    mapping = TaxonomyMapping(
        cluster_id="node-123",
        taxonomy_label="API Architecture",
        taxonomy_breadcrumb=["Infrastructure", "API Architecture"],
        domain_raw="REST API design",
    )
    assert mapping.cluster_id == "node-123"
    assert mapping.taxonomy_label == "API Architecture"
    assert mapping.domain_raw == "REST API design"
    assert mapping.taxonomy_breadcrumb == ["Infrastructure", "API Architecture"]


@pytest.mark.asyncio
async def test_taxonomy_mapping_unmapped():
    """TaxonomyMapping with no match should have None cluster_id."""
    from app.services.taxonomy import TaxonomyMapping

    mapping = TaxonomyMapping(
        cluster_id=None,
        taxonomy_label=None,
        taxonomy_breadcrumb=[],
        domain_raw="quantum computing",
    )
    assert mapping.cluster_id is None
    assert mapping.domain_raw == "quantum computing"
