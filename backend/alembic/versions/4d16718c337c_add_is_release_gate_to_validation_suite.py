"""add is_release_gate to validation_suite

Revision ID: 4d16718c337c
Revises: 5576c539720f
Create Date: 2026-05-19 19:21:13.556321

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4d16718c337c'
down_revision: Union[str, Sequence[str], None] = '5576c539720f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "validation_suite",
        sa.Column(
            "is_release_gate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("validation_suite", "is_release_gate")
