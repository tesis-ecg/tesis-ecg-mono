"""device_doctor_id

Revision ID: 27e0b772f1dd
Revises: 4940f777d181
Create Date: 2026-08-16 23:55:37.274797

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "27e0b772f1dd"
down_revision: str | Sequence[str] | None = "4940f777d181"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("device", sa.Column("doctor_id", sa.UUID(), nullable=True))
    op.create_foreign_key("device_doctor_id_fkey", "device", "doctor", ["doctor_id"], ["id"])
    op.create_index("ix_device_doctor_id", "device", ["doctor_id"])
    # Backfill: heredar el médico del paciente asignado. Sin esto, después de
    # activar el scoping por doctor_id ningún médico ve ningún dispositivo.
    op.execute(
        """
        UPDATE device
        SET doctor_id = patient.doctor_id
        FROM patient
        WHERE device.patient_id = patient.id
          AND device.doctor_id IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_device_doctor_id", table_name="device")
    op.drop_constraint("device_doctor_id_fkey", "device", type_="foreignkey")
    op.drop_column("device", "doctor_id")
