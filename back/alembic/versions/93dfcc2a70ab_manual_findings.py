"""Auditoría de hallazgos manuales.

Revision ID: 93dfcc2a70ab
Revises: 48e070eacccb
"""

from alembic import op

revision = "93dfcc2a70ab"
down_revision = "48e070eacccb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'ECG_EVENT_CREATED'")


def downgrade() -> None:
    # PostgreSQL no admite quitar valores de un enum sin recrearlo.
    pass
