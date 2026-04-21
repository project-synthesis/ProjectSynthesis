"""Tests for domain tree integrity verification and auto-repair."""

from unittest.mock import MagicMock, patch

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
    """Composite unique index prevents two domain nodes with the same label under the same parent."""
    from sqlalchemy.exc import IntegrityError

    # Create a project node to serve as shared parent
    project = PromptCluster(label="test-project", state="project", domain="general", persistence=1.0)
    db.add(project)
    await db.flush()

    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0, parent_id=project.id))
    await db.commit()

    # Same label + same parent → blocked by UNIQUE(parent_id, label) WHERE state='domain'
    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0, parent_id=project.id))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_same_domain_label_allowed_under_different_projects(db, mock_embedding):
    """Same domain label under different projects is allowed (multi-project support)."""
    proj_a = PromptCluster(label="project-a", state="project", domain="general", persistence=1.0)
    proj_b = PromptCluster(label="project-b", state="project", domain="general", persistence=1.0)
    db.add_all([proj_a, proj_b])
    await db.flush()

    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0, parent_id=proj_a.id))
    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0, parent_id=proj_b.id))
    await db.commit()  # Should NOT raise — different parents


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


# ---------------------------------------------------------------------------
# Observability: each repair emits a taxonomy_activity event (I-8)
# ---------------------------------------------------------------------------


def _collect_repair_calls(mock_logger: MagicMock) -> list[dict]:
    """Extract kwargs of log_decision calls with op='tree_integrity_repair'."""
    calls = []
    for call in mock_logger.log_decision.call_args_list:
        kwargs = call.kwargs
        if kwargs.get("op") == "tree_integrity_repair":
            calls.append(kwargs)
    return calls


@pytest.mark.asyncio
async def test_repair_emits_event_for_weak_persistence(db, mock_embedding):
    """Each weak-persistence repair emits a tree_integrity_repair event."""
    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=0.5))
    db.add(PromptCluster(label="frontend", state="domain", domain="frontend", persistence=0.3))
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)

    mock_logger = MagicMock()
    with patch(
        "app.services.taxonomy.engine.get_event_logger", return_value=mock_logger
    ):
        await engine._repair_tree_violations(db, violations)

    repair_calls = _collect_repair_calls(mock_logger)
    weak_events = [c for c in repair_calls if c["context"]["violation_type"] == "weak_persistence"]
    assert len(weak_events) == 2
    for c in weak_events:
        assert c["path"] == "warm"
        assert c["op"] == "tree_integrity_repair"
        assert c["decision"] == "repaired"
        assert c["context"]["action"] == "persistence_set_to_1.0"
        assert c["cluster_id"] is not None


@pytest.mark.asyncio
async def test_repair_emits_event_for_orphan_cluster(db, mock_embedding):
    """Orphan cluster repair emits a tree_integrity_repair event."""
    db.add(PromptCluster(label="general", state="domain", domain="general", persistence=1.0))
    db.add(PromptCluster(
        label="orphan", state="active", domain="general", parent_id="nonexistent-uuid",
    ))
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)

    mock_logger = MagicMock()
    with patch(
        "app.services.taxonomy.engine.get_event_logger", return_value=mock_logger
    ):
        await engine._repair_tree_violations(db, violations)

    repair_calls = _collect_repair_calls(mock_logger)
    orphan_events = [c for c in repair_calls if c["context"]["violation_type"] == "orphan_cluster"]
    assert len(orphan_events) == 1
    assert orphan_events[0]["context"]["action"] == "reparented_to_general"


@pytest.mark.asyncio
async def test_repair_emits_event_for_self_reference(db, mock_embedding):
    """Self-referencing parent repair emits a tree_integrity_repair event."""
    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=1.0))
    selfref = PromptCluster(label="loop", state="active", domain="backend")
    db.add(selfref)
    await db.flush()
    selfref.parent_id = selfref.id
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)

    mock_logger = MagicMock()
    with patch(
        "app.services.taxonomy.engine.get_event_logger", return_value=mock_logger
    ):
        await engine._repair_tree_violations(db, violations)

    repair_calls = _collect_repair_calls(mock_logger)
    self_ref_events = [c for c in repair_calls if c["context"]["violation_type"] == "self_reference"]
    assert len(self_ref_events) == 1
    assert "reparented" in self_ref_events[0]["context"]["action"]
    assert self_ref_events[0]["cluster_id"] == selfref.id


@pytest.mark.asyncio
async def test_repair_event_runtime_error_swallowed(db, mock_embedding):
    """A RuntimeError from get_event_logger must not break the repair routine."""
    db.add(PromptCluster(label="backend", state="domain", domain="backend", persistence=0.5))
    await db.commit()

    engine = TaxonomyEngine(embedding_service=mock_embedding, provider_resolver=lambda: None)
    violations = await engine.verify_domain_tree_integrity(db)

    with patch(
        "app.services.taxonomy.engine.get_event_logger",
        side_effect=RuntimeError("logger not initialized"),
    ):
        # Should not raise.
        repaired = await engine._repair_tree_violations(db, violations)
    assert repaired > 0
