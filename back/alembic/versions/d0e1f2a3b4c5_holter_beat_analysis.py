"""Latidos detectados por el backend para las métricas Holter.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "study",
        sa.Column("ecg_beat_chunks", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "study",
        sa.Column("beats_analyzed_samples", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("study", "beats_analyzed_samples")
    op.drop_column("study", "ecg_beat_chunks")
