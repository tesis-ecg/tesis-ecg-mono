"""schema alignment for frontend contracts

Revision ID: 003_schema_alignment
Revises: 002_auth
Create Date: 2026-06-04 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "003_schema_alignment"
down_revision: str | None = "002_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


patient_sex = postgresql.ENUM("M", "F", "X", name="patient_sex", create_type=False)
patient_study_status = postgresql.ENUM(
    "active", "completed", "paused", "none", name="patient_study_status", create_type=False
)
device_status = postgresql.ENUM(
    "available", "assigned", "maintenance", "retired", name="device_status", create_type=False
)
study_status = postgresql.ENUM(
    "in_progress", "completed", "cancelled", "scheduled", name="study_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    patient_sex.create(bind, checkfirst=True)
    patient_study_status.create(bind, checkfirst=True)
    device_status.create(bind, checkfirst=True)
    study_status.create(bind, checkfirst=True)

    op.add_column("user", sa.Column("specialty", sa.String(length=120), nullable=True))
    op.add_column("user", sa.Column("license_number", sa.String(length=120), nullable=True))

    op.add_column(
        "patient",
        sa.Column("sex", patient_sex, server_default="X", nullable=False),
    )
    op.add_column(
        "patient",
        sa.Column("study_status", patient_study_status, server_default="none", nullable=False),
    )
    op.add_column(
        "patient",
        sa.Column("last_data_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("patient", sa.Column("notes", sa.Text(), nullable=True))

    op.add_column(
        "device",
        sa.Column("model", sa.String(length=120), server_default="Holter ECG", nullable=False),
    )
    op.add_column(
        "device",
        sa.Column("status", device_status, server_default="available", nullable=False),
    )
    op.execute(
        """
        UPDATE device
        SET status = CASE
            WHEN is_active = false THEN 'retired'::device_status
            WHEN patient_id IS NOT NULL THEN 'assigned'::device_status
            ELSE 'available'::device_status
        END
        """
    )
    op.alter_column("device", "model", server_default=None)
    op.alter_column("device", "status", server_default=None)
    op.alter_column("patient", "sex", server_default=None)
    op.alter_column("patient", "study_status", server_default=None)
    op.alter_column("device", "last_seen", new_column_name="last_seen_at")

    op.execute(
        """
        INSERT INTO "user" (
            id, auth0_id, email, full_name, role, specialty, license_number,
            is_active, created_at, updated_at, deleted_at
        )
        SELECT
            d.id,
            d.auth0_id,
            d.email,
            trim(d.name || ' ' || d.surname),
            'medico'::user_role,
            d.specialty,
            d.license_number,
            d.is_active,
            d.created_at,
            d.updated_at,
            d.deleted_at
        FROM doctor d
        WHERE NOT EXISTS (
            SELECT 1 FROM "user" u WHERE u.email = d.email OR u.id = d.id
        )
        """
    )
    op.execute(
        """
        UPDATE patient p
        SET doctor_id = u.id
        FROM doctor d
        JOIN "user" u ON u.email = d.email
        WHERE p.doctor_id = d.id AND p.doctor_id <> u.id
        """
    )
    op.execute(
        """
        UPDATE alert a
        SET acknowledged_by = u.id
        FROM doctor d
        JOIN "user" u ON u.email = d.email
        WHERE a.acknowledged_by = d.id AND a.acknowledged_by <> u.id
        """
    )

    op.drop_constraint("patient_doctor_id_fkey", "patient", type_="foreignkey")
    op.create_foreign_key("patient_doctor_id_fkey", "patient", "user", ["doctor_id"], ["id"])
    op.drop_constraint("alert_acknowledged_by_fkey", "alert", type_="foreignkey")
    op.create_foreign_key(
        "alert_acknowledged_by_fkey", "alert", "user", ["acknowledged_by"], ["id"]
    )

    op.drop_column("device", "is_active")
    op.create_index(
        "ix_patient_dni_active",
        "patient",
        ["dni"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND dni IS NOT NULL"),
    )

    op.create_table(
        "study",
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", study_status, nullable=False),
        sa.Column("samples_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ecg_s3_key", sa.String(length=1024), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=False, server_default="250"),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["device.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_study_patient_started", "study", ["patient_id", "started_at"])
    op.create_index("ix_study_device_started", "study", ["device_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_study_device_started", table_name="study")
    op.drop_index("ix_study_patient_started", table_name="study")
    op.drop_table("study")
    op.drop_index("ix_patient_dni_active", table_name="patient")
    op.add_column(
        "device",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.execute("UPDATE device SET is_active = (status <> 'retired'::device_status)")
    op.alter_column("device", "is_active", server_default=None)

    op.drop_constraint("alert_acknowledged_by_fkey", "alert", type_="foreignkey")
    op.create_foreign_key(
        "alert_acknowledged_by_fkey", "alert", "doctor", ["acknowledged_by"], ["id"]
    )
    op.drop_constraint("patient_doctor_id_fkey", "patient", type_="foreignkey")
    op.create_foreign_key("patient_doctor_id_fkey", "patient", "doctor", ["doctor_id"], ["id"])

    op.alter_column("device", "last_seen_at", new_column_name="last_seen")
    op.drop_column("device", "status")
    op.drop_column("device", "model")
    op.drop_column("patient", "notes")
    op.drop_column("patient", "last_data_received_at")
    op.drop_column("patient", "study_status")
    op.drop_column("patient", "sex")
    op.drop_column("user", "license_number")
    op.drop_column("user", "specialty")

    device_status.drop(op.get_bind(), checkfirst=True)
    study_status.drop(op.get_bind(), checkfirst=True)
    patient_study_status.drop(op.get_bind(), checkfirst=True)
    patient_sex.drop(op.get_bind(), checkfirst=True)
