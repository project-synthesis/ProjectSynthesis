"""Tests for domain tree integrity verification and auto-repair."""

import pytest

from app.models import PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_integrity_passes_clean_tree(db, mock_embedding):
    """No violations in a well-formed tree."""
    # Seed domain nodes
    backend = PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0)
    general = PromptCluster(label="general", state="domain", domain="general", persistence=1.0)
    db.add_all([backend, general])
    await db.flush()

    # Add a child cluster properly parented
    child = PromptCluster(label="api-design", state="active", domain="backend", parent_id=backend.id)
    db.add(child)
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)
    assert violations == []


@pytest.mark.asyncio
async def test_duplicate_domain_labels_blocked_by_db(db, mock_embedding):
    """Partial unique index prevents two domain nodes with the same label."""
    from sqlalchemy.exc import IntegrityError

    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0))
    await db.commit()

    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_non_domain_duplicate_labels_allowed(db, mock_embedding):
    """Non-domain clusters can share a label with a domain node."""
    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0))
    db.add(PromptCluster(label="backend", state="active", domain="backend"))
    await db.commit()  # Should not raise


@pytest.mark.asyncio
async def test_integrity_detects_weak_persistence(db, mock_embedding):
    """Domain node with persistence < 1.0 is a violation."""
    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=0.5))
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)
    assert any("persistence" in v.lower() for v in violations)


@pytest.mark.asyncio
async def test_integrity_detects_orphan_cluster(db, mock_embedding):
    """Cluster pointing to non-existent parent is a violation."""
    db.add(PromptCluster(label="general", state="domain", domain="general", persistence=1.0))
    db.add(PromptCluster(
        label="orphan", state="active", domain="general",
        parent_id="nonexistent-uuid",
    ))
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)
    assert any("Orphan" in v or "orphan" in v for v in violations)


@pytest.mark.asyncio
async def test_auto_repair_weak_persistence(db, mock_embedding):
    """Auto-repair fixes domain nodes with persistence < 1.0."""
    node = PromptCluster(label="backend", state="domain", domain="backend", persistence=0.5)
    db.add(node)
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)
    assert len(violations) > 0

    repaired = await engine._repair_tree_violations(db, violations)
    assert repaired > 0

    await db.refresh(node)
    assert node.persistence == 1.0
