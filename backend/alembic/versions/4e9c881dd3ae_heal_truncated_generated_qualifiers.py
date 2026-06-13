"""heal truncated generated_qualifiers keys

v0.4.38, F5a — strips every ``generated_qualifiers`` entry whose key is
EXACTLY 20 characters long (the legacy hard-slice signature) and resets
``generated_qualifiers_cluster_count`` to ``-1`` on affected rows so the
next maintenance cycle re-runs vocab generation regardless of the
cluster-count-stability gate.

Why ``-1``?  The staleness predicate in ``engine._propose_sub_domains``
(engine.py:2369-2374) is
``abs(current_cluster_count - cached_cluster_count) > max(2, cached_cluster_count * 0.3)``.
With ``cached=-1`` and any non-negative ``current=n``:
``abs(n - (-1)) = n + 1 > max(2, -0.3) = 2  ⟺  n >= 2``.
Vocab generation already skips domains with fewer than 2 child clusters
(engine.py:2361-2362), so ``n < 2`` is unreachable in practice. The
negative ``cached * 0.3`` operand is mechanically still ``-0.3`` (not
raised to ``0`` by ``max``) — the predicate is by design unreachable
through the n<2 path, so the negative arithmetic is safe and load-bearing.

Accepted costs:
  - A legitimately-20-char key (e.g. a freshly-normalized vocab group
    that happens to be exactly 20 chars) is dropped; it regenerates
    within ~one maintenance cycle.
  - Domains with <2 child clusters skip vocab-gen and simply lose the
    corrupt key (strictly better than keeping it).

Idempotent: re-running on a clean DB is a no-op (no len-20 keys remain;
no rows to update). Reversible downgrade is a no-op with a docstring —
this is a data heal, not a schema change.

Spec: docs/superpowers/specs/2026-06-12-v0.4.38-sub-domain-telemetry-design.md §3.5

Revision ID: 4e9c881dd3ae
Revises: 1fa50d7f82b7
Create Date: 2026-06-12
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "4e9c881dd3ae"
down_revision: Union[str, Sequence[str], None] = "1fa50d7f82b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEN_TARGET = 20


def _decode_meta(raw: object) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return None


def upgrade() -> None:
    """Strip len-20 keys from cluster_metadata.generated_qualifiers on domain rows."""
    bind = op.get_bind()
    # SELECT every domain row; rewrite metadata in Python; UPDATE per row.
    result = bind.execute(
        sa.text(
            "SELECT id, cluster_metadata FROM prompt_cluster WHERE state = 'domain'"
        )
    ).fetchall()

    for row in result:
        cluster_id = row[0]
        meta = _decode_meta(row[1])
        if meta is None:
            continue
        gq = meta.get("generated_qualifiers")
        if not isinstance(gq, dict) or not gq:
            continue
        cleaned = {k: v for k, v in gq.items() if not (isinstance(k, str) and len(k) == _LEN_TARGET)}
        if cleaned == gq:
            continue  # nothing to do for this row — idempotent
        meta["generated_qualifiers"] = cleaned
        meta["generated_qualifiers_cluster_count"] = -1
        bind.execute(
            sa.text(
                "UPDATE prompt_cluster SET cluster_metadata = :meta WHERE id = :id"
            ),
            {"meta": json.dumps(meta), "id": cluster_id},
        )


def downgrade() -> None:
    """No-op: this is a forward-only data heal.

    The pre-truncation source was discarded at write time, so the legacy
    ``pipeline-observabili`` cannot be reconstructed. Downgrading would
    simply leave the cleaned dict in place — a no-op is the honest
    semantics.
    """
    pass
