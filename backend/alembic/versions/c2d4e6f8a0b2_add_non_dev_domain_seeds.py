"""seed non-developer domain nodes (marketing, business, content)

Revision ID: c2d4e6f8a0b2
Revises: 2f3b0645e24d
Create Date: 2026-04-24 20:00:00.000000

ADR-006 content-first playbook step 1: add non-developer vertical
domains so the first marketing / founder / operator prompts land on a
real domain rather than defaulting to ``general`` for months while the
warm path organically discovers them.  The engine and signal loader
are already universal — this migration is pure content.

Adds three domain nodes with brand-aligned OKLab colors and keyword
signals derived from common business / marketing vocabulary:

- ``marketing``   — campaigns, messaging, positioning
- ``business``    — strategy, operations, planning, stakeholder work
- ``content``     — writing, editorial, publications

Mirrors the idempotency pattern of ``a1b2c3d4e5f6_add_domain_nodes`` —
skips any domain whose label is already seeded.  Safe to re-run.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.migration")

# revision identifiers, used by Alembic.
revision: str = "c2d4e6f8a0b2"
down_revision: Union[str, Sequence[str], None] = "2f3b0645e24d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Seed definitions
# ---------------------------------------------------------------------------
#
# Colors picked to stay clear of the v0.3.6 electric-neon palette:
# - marketing: warm coral (#ff7a45) — distinct from #ff4895 frontend pink
# - business: muted teal (#3fb0a9) — distinct from #36b5ff database blue
# - content:  warm amber (#f5a623) — distinct from #b49982 data taupe
#
# Keywords are intentionally SMALL per domain — the warm path's TF-IDF
# extraction will add more as real prompts accumulate.  Focus on high-
# precision terms (jargon) over high-recall terms (generic English).

SEED_DOMAINS: list[dict] = [
    {
        "label": "marketing",
        "color_hex": "#ff7a45",
        "signal_keywords": [
            ["campaign", 0.9], ["landing page", 1.0], ["hero copy", 1.0],
            ["headline", 0.9], ["tagline", 1.0], ["positioning", 0.9],
            ["messaging", 0.8], ["call-to-action", 0.9], ["conversion", 0.8],
            ["brand voice", 1.0], ["audience", 0.6], ["persona", 0.7],
            ["ad copy", 1.0], ["email sequence", 1.0],
            ["social post", 0.8], ["launch announcement", 1.0],
        ],
    },
    {
        "label": "business",
        "color_hex": "#3fb0a9",
        "signal_keywords": [
            ["strategy", 0.7], ["one-pager", 1.0], ["memo", 0.9],
            ["investor update", 1.0], ["board prep", 1.0],
            ["quarterly plan", 1.0], ["hiring brief", 1.0],
            ["stakeholder", 0.8], ["decision record", 1.0],
            ["post-mortem", 0.9], ["partnership", 0.8],
            ["market analysis", 1.0], ["competitive teardown", 1.0],
            ["okr", 0.9], ["kpi", 0.8], ["roadmap", 0.6],
            ["team update", 0.8], ["escalation", 0.7],
        ],
    },
    {
        "label": "content",
        "color_hex": "#f5a623",
        "signal_keywords": [
            ["blog post", 1.0], ["newsletter", 1.0], ["article", 0.8],
            ["essay", 0.9], ["editorial", 0.9], ["tutorial", 0.7],
            ["long-form", 1.0], ["thought leadership", 1.0],
            ["storytelling", 0.9], ["narrative", 0.7],
            ["style guide", 0.9], ["brief", 0.6], ["outline", 0.6],
            ["ghostwrite", 0.9], ["byline", 0.8],
            ["case study", 0.9], ["whitepaper", 1.0],
        ],
    },
]


def upgrade() -> None:
    """Seed the three non-developer domain nodes (idempotent)."""
    conn = op.get_bind()

    # Defensive: the target table must exist before seeding.  On fresh
    # DBs this migration runs after ``a1b2c3d4e5f6`` which creates the
    # ``cluster_metadata`` column we write to.
    inspector = sa.inspect(conn)
    if "prompt_cluster" not in inspector.get_table_names():
        logger.warning(
            "c2d4e6f8a0b2: prompt_cluster table missing — skipping non-dev "
            "domain seeds.  Downstream migration chain will surface the "
            "real issue."
        )
        return

    existing_cols = {c["name"] for c in inspector.get_columns("prompt_cluster")}
    if "cluster_metadata" not in existing_cols:
        logger.warning(
            "c2d4e6f8a0b2: cluster_metadata column missing — skipping seed. "
            "Run ``alembic upgrade a1b2c3d4e5f6`` first."
        )
        return

    existing_labels = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT label FROM prompt_cluster WHERE state = 'domain'")
        ).all()
    }

    inserted = 0
    for domain_def in SEED_DOMAINS:
        if domain_def["label"] in existing_labels:
            logger.info(
                "c2d4e6f8a0b2: domain '%s' already seeded — skip",
                domain_def["label"],
            )
            continue

        node_id = str(uuid.uuid4())
        meta = json.dumps({
            "source": "seed",
            "signal_keywords": domain_def["signal_keywords"],
            "discovered_at": None,
            "proposed_by_snapshot": None,
            "vertical": "non-developer",  # marker for ADR-006 traceability
        })
        op.execute(
            sa.text(
                "INSERT INTO prompt_cluster "
                "(id, label, state, domain, task_type, color_hex, "
                "persistence, member_count, usage_count, prune_flag_count, "
                "cluster_metadata) "
                "VALUES (:id, :label, 'domain', :label, 'general', "
                ":color_hex, 1.0, 0, 0, 0, :meta)"
            ).bindparams(
                id=node_id,
                label=domain_def["label"],
                color_hex=domain_def["color_hex"],
                meta=meta,
            )
        )
        inserted += 1
        logger.info(
            "c2d4e6f8a0b2: seeded domain '%s' (color=%s, %d keywords)",
            domain_def["label"], domain_def["color_hex"],
            len(domain_def["signal_keywords"]),
        )

    if inserted:
        logger.info("c2d4e6f8a0b2: inserted %d non-developer domain nodes", inserted)
    else:
        logger.info("c2d4e6f8a0b2: all non-developer domains already seeded")


def downgrade() -> None:
    """Remove the three non-developer domain nodes.

    Only removes nodes whose metadata carries the ``vertical="non-developer"``
    marker we set in upgrade — so a user who manually renamed one of these
    (e.g. renamed ``marketing`` to ``marketing-seed``) is unaffected.
    """
    conn = op.get_bind()

    labels = {d["label"] for d in SEED_DOMAINS}
    for row in conn.execute(
        sa.text("SELECT id, label, cluster_metadata FROM prompt_cluster "
                 "WHERE state = 'domain'")
    ).all():
        node_id, label, meta_json = row
        if label not in labels:
            continue
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if meta.get("vertical") != "non-developer":
            continue
        op.execute(
            sa.text("DELETE FROM prompt_cluster WHERE id = :id").bindparams(
                id=node_id,
            )
        )
        logger.info("c2d4e6f8a0b2 downgrade: removed non-dev domain '%s'", label)
