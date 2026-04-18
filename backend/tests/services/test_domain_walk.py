# backend/tests/services/test_domain_walk.py
from types import SimpleNamespace

import pytest

from app.services.taxonomy.domain_walk import root_domain_label


def _node(node_id: str, parent_id: str | None, state: str, label: str):
    return SimpleNamespace(id=node_id, parent_id=parent_id, state=state, label=label)


def test_three_deep_happy_path():
    top = _node("d1", None, "domain", "backend")
    sub = _node("d2", "d1", "domain", "backend: auth")
    cluster = _node("c1", "d2", "active", "oauth flow")
    lookup = {"d1": top, "d2": sub}
    assert root_domain_label(cluster, lookup) == "backend"


def test_two_deep_happy_path():
    top = _node("d1", None, "domain", "writing")
    cluster = _node("c1", "d1", "active", "blog post")
    assert root_domain_label(cluster, {"d1": top}) == "writing"


def test_immediate_project_parent():
    top = _node("d1", "proj1", "domain", "data")
    cluster = _node("c1", "d1", "active", "etl")
    assert root_domain_label(cluster, {"d1": top}) == "data"


def test_cycle_returns_general():
    a = _node("a", "b", "domain", "a")
    b = _node("b", "a", "domain", "b")
    cluster = _node("c", "a", "active", "x")
    assert root_domain_label(cluster, {"a": a, "b": b}) == "general"


def test_orphan_cluster_returns_general_not_own_label():
    cluster = _node("c1", None, "active", "fastapi auth handlers")
    assert root_domain_label(cluster, {}) == "general"


def test_dissolved_parent_returns_current_label_if_domain():
    # parent_id set but missing from lookup (dissolved)
    current_is_domain = _node("d1", "missing", "domain", "system")
    assert root_domain_label(current_is_domain, {}) == "system"


def test_dissolved_parent_returns_general_if_not_domain():
    orphan_active = _node("c1", "missing", "active", "some cluster label")
    assert root_domain_label(orphan_active, {}) == "general"


def test_nine_deep_chain_hits_hop_cap():
    nodes = {}
    for i in range(10):
        parent = f"n{i - 1}" if i > 0 else None
        nodes[f"n{i}"] = _node(f"n{i}", parent, "domain", f"L{i}")
    cluster = _node("c", "n9", "active", "leaf")
    assert root_domain_label(cluster, nodes) == "general"


def test_empty_label_top_domain():
    top = _node("d1", None, "domain", "")
    cluster = _node("c1", "d1", "active", "x")
    assert root_domain_label(cluster, {"d1": top}) == "general"


def test_cluster_is_itself_a_domain():
    top = _node("d1", None, "domain", "creative")
    assert root_domain_label(top, {"d1": top}) == "creative"
