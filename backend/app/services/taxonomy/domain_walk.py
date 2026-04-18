# backend/app/services/taxonomy/domain_walk.py
"""Root-domain walk helper.

Walks a cluster's parent chain to find the root-level domain label.  Used at
template-fork time to freeze ``domain_label`` on the ``PromptTemplate`` row.

Returns ``"general"`` in all degenerate cases:

* cycle detected (node already visited)
* hop-cap exhaustion (chain exceeds ``_DOMAIN_WALK_HOP_CAP``)
* orphan cluster whose terminal ancestor is not ``state='domain'``
* terminal domain node has an empty label

The hop cap (8) is intentionally generous: the real hierarchy is at most
project → domain → sub-domain → cluster (4 levels).  8 allows future
intermediate groupings without silent truncation while still bounding
pathological cases created by data corruption or future schema migrations.
"""
from __future__ import annotations

from typing import Protocol

__all__ = ["root_domain_label"]

# Maximum parent hops before the walk is declared degenerate and 'general' is
# returned.  Real hierarchy depth is project→domain→sub-domain→cluster (4
# levels), so 8 gives a 2× safety margin for future schema changes without
# silently truncating valid chains.
_DOMAIN_WALK_HOP_CAP = 8


class _ClusterLike(Protocol):
    """Structural protocol for nodes in the cluster/domain parent chain.

    Consumed by ``root_domain_label``; any object with the four listed
    attributes satisfies it — no inheritance or registration required.
    """

    id: str
    parent_id: str | None
    state: str
    label: str


def root_domain_label(
    cluster: _ClusterLike,
    domain_lookup: dict[str, _ClusterLike],
) -> str:
    """Walk *cluster*'s parent chain and return the root-domain label.

    Traverses ``domain_lookup`` from *cluster* toward the root, stopping at
    the first node that has no parent.  That terminal node must have
    ``state == 'domain'`` and a non-empty ``label`` — otherwise ``'general'``
    is returned.

    Args:
        cluster: The leaf or intermediate cluster to start from.  May itself
            be a domain node (handled correctly by the hop loop).
        domain_lookup: Mapping of node ID → node for every ancestor that
            should be reachable.  Missing entries are treated as dissolved
            (dangling FK); the walk terminates at the last reachable node.

    Returns:
        The ``label`` of the root domain node, or ``'general'`` if the chain
        is degenerate (cycle, hop-cap exceeded, orphan, empty label).
    """
    current = cluster
    seen: set[str] = set()
    for _ in range(_DOMAIN_WALK_HOP_CAP):
        if current.id in seen:
            return "general"
        seen.add(current.id)
        if not current.parent_id:
            return current.label if current.state == "domain" and current.label else "general"
        parent = domain_lookup.get(current.parent_id)
        if parent is None:
            return current.label if current.state == "domain" and current.label else "general"
        current = parent
    return "general"
