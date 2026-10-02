"""Tiempo de arranque verificable y telemetría del puente.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "study",
        sa.Column("started_at_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Los estudios ya ingestado no archivaron el bootId del POST, por lo que
    # no se puede comprobar que su ancla pertenezca a las tramas recibidas.
    # La hora se conserva, pero el visor debe presentarla como no verificada.
    op.execute(
        "UPDATE study SET started_at_verified = false "
        "WHERE EXISTS (SELECT 1 FROM ecg_batch WHERE ecg_batch.study_id = study.id)"
    )
    op.add_column("ecg_batch", sa.Column("anchor_matches_boot", sa.Boolean(), nullable=True))
    op.add_column(
        "study_timeline_segment", sa.Column("anchor_matches_boot", sa.Boolean(), nullable=True)
    )
    op.add_column("device", sa.Column("last_rssi_dbm", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("last_battery_flags", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("battery_alert_level", sa.String(16), nullable=True))
    op.add_column(
        "study",
        sa.Column("filter_view_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "study",
        sa.Column("ecg_filtered_segments", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "study",
        sa.Column(
            "ecg_filtered_pyramid_levels", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
    )
    op.add_column(
        "study", sa.Column("ecg_filtered_envelope_carry", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "study",
        sa.Column(
            "ecg_filtered_level_carry", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
    )
    op.add_column(
        "study",
        sa.Column("filtered_samples_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("study", "filtered_samples_count")
    op.drop_column("study", "ecg_filtered_level_carry")
    op.drop_column("study", "ecg_filtered_envelope_carry")
    op.drop_column("study", "ecg_filtered_pyramid_levels")
    op.drop_column("study", "ecg_filtered_segments")
    op.drop_column("study", "filter_view_enabled")
    op.drop_column("device", "battery_alert_level")
    op.drop_column("device", "last_battery_flags")
    op.drop_column("device", "last_rssi_dbm")
    op.drop_column("study_timeline_segment", "anchor_matches_boot")
    op.drop_column("ecg_batch", "anchor_matches_boot")
    op.drop_column("study", "started_at_verified")
