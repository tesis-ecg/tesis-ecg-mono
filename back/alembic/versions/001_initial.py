"""initial

Revision ID: 001_initial
Revises:
Create Date: 2026-05-20 21:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "doctor",
        sa.Column("auth0_id", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("surname", sa.String(length=120), nullable=False),
        sa.Column("specialty", sa.String(length=120), nullable=True),
        sa.Column("license_number", sa.String(length=120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("auth0_id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "patient",
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("medical_record_num", sa.String(length=120), nullable=False),
        sa.Column("first_name", sa.String(length=120), nullable=False),
        sa.Column("last_name", sa.String(length=120), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("dni", sa.String(length=50), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
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
        sa.ForeignKeyConstraint(["doctor_id"], ["doctor.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("medical_record_num"),
    )

    op.create_table(
        "device",
        sa.Column("serial_number", sa.String(length=120), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("api_key_hash", sa.String(length=255), nullable=False),
        sa.Column("firmware_version", sa.String(length=120), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_battery_pct", sa.Integer(), nullable=True),
        sa.Column("last_sd_free_mb", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serial_number"),
    )

    op.create_table(
        "ecg_batch",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("num_channels", sa.Integer(), nullable=False),
        sa.Column("num_samples", sa.Integer(), nullable=False),
        sa.Column("compression_type", sa.String(length=50), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "processing_status",
            sa.Enum("PENDING", "PROCESSING", "DONE", "FAILED", name="processing_status"),
            nullable=False,
        ),
        sa.Column("processing_error", sa.String(length=1024), nullable=True),
        sa.Column("firmware_version", sa.String(length=120), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ecg_event",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "TACHYCARDIA",
                "BRADYCARDIA",
                "AFIB",
                "PVC",
                "PAUSE",
                "NOISE",
                "OTHER",
                name="ecg_event_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="ecg_event_severity"),
            nullable=False,
        ),
        sa.Column("timestamp_in_recording", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(["batch_id"], ["ecg_batch.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "alert",
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="alert_severity"),
            nullable=False,
        ),
        sa.Column("message", sa.String(length=1024), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["acknowledged_by"], ["doctor.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["ecg_event.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        "CREATE INDEX ix_ecg_batch_device_received ON ecg_batch(device_id, received_at DESC)"
    )
    op.create_index("ix_ecg_batch_status", "ecg_batch", ["processing_status"], unique=False)
    op.create_index(
        "ix_alert_patient_ack", "alert", ["patient_id", "acknowledged_at"], unique=False
    )
    op.create_index("ix_ecg_event_batch", "ecg_event", ["batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ecg_event_batch", table_name="ecg_event")
    op.drop_index("ix_alert_patient_ack", table_name="alert")
    op.drop_index("ix_ecg_batch_status", table_name="ecg_batch")
    op.drop_index("ix_ecg_batch_device_received", table_name="ecg_batch")

    op.drop_table("alert")
    op.drop_table("ecg_event")
    op.drop_table("ecg_batch")
    op.drop_table("device")
    op.drop_table("patient")
    op.drop_table("doctor")

    sa.Enum(name="alert_severity").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="ecg_event_severity").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="ecg_event_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="processing_status").drop(op.get_bind(), checkfirst=True)
