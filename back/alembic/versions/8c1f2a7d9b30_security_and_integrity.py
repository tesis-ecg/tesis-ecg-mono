"""security and integrity baseline

Revision ID: 8c1f2a7d9b30
Revises: 27e0b772f1dd
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "8c1f2a7d9b30"
down_revision: str | Sequence[str] | None = "27e0b772f1dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for value in (
        "USER_UPDATED",
        "USER_DELETED",
        "PATIENT_CREATED",
        "PATIENT_UPDATED",
        "PATIENT_DELETED",
        "DEVICE_CREATED",
        "DEVICE_UPDATED",
        "DEVICE_RETIRED",
        "DEVICE_ASSIGNED",
        "ECG_ACCESSED",
    ):
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{value}'")

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM "user" WHERE role::text NOT IN ('medico', 'admin')) THEN
            RAISE EXCEPTION
              'Hay usuarios con roles legacy; migrarlos antes de continuar';
          END IF;
          IF EXISTS (
            SELECT lower(email) FROM "user" GROUP BY lower(email) HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'Hay emails duplicados al normalizar mayúsculas/minúsculas';
          END IF;
          IF EXISTS (
            SELECT 1 FROM device
            WHERE (status = 'assigned' AND (patient_id IS NULL OR doctor_id IS NULL))
               OR (status <> 'assigned' AND patient_id IS NOT NULL)
          ) THEN
            RAISE EXCEPTION 'Hay dispositivos con estado/asignación inconsistente';
          END IF;
        END $$;
        """
    )

    op.execute("CREATE TYPE user_role_v2 AS ENUM ('medico', 'admin')")
    op.execute(
        'ALTER TABLE "user" ALTER COLUMN role TYPE user_role_v2 USING role::text::user_role_v2'
    )
    op.execute("DROP TYPE user_role")
    op.execute("ALTER TYPE user_role_v2 RENAME TO user_role")

    identity_status = sa.Enum("pending", "active", "error", name="identity_status")
    identity_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "user",
        sa.Column(
            "identity_status",
            identity_status,
            server_default="active",
            nullable=False,
        ),
    )
    op.add_column("user", sa.Column("pending_email", sa.String(length=320), nullable=True))
    op.add_column(
        "user", sa.Column("session_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.execute('UPDATE "user" SET email = lower(trim(email))')
    op.create_check_constraint("ck_user_email_normalized", "user", "email = lower(email)")
    op.create_check_constraint(
        "ck_user_pending_email_normalized",
        "user",
        "pending_email IS NULL OR pending_email = lower(pending_email)",
    )

    op.create_table(
        "auth_rate_limit",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "bucket_start"),
    )
    op.create_index("ix_auth_rate_limit_bucket_start", "auth_rate_limit", ["bucket_start"])

    op.add_column(
        "study",
        sa.Column(
            "ecg_encoding",
            sa.String(length=32),
            server_default="float32-le",
            nullable=False,
        ),
    )
    op.add_column("study", sa.Column("ecg_byte_length", sa.BigInteger(), nullable=True))
    op.add_column("study", sa.Column("ecg_sha256", sa.String(length=64), nullable=True))
    op.add_column(
        "study",
        sa.Column(
            "ecg_pyramid_levels",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    op.create_check_constraint(
        "ck_device_assignment_state",
        "device",
        "(status = 'assigned' AND patient_id IS NOT NULL AND doctor_id IS NOT NULL) "
        "OR (status <> 'assigned' AND patient_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_device_battery_pct",
        "device",
        "last_battery_pct IS NULL OR last_battery_pct BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        "ck_device_sd_free_nonnegative",
        "device",
        "last_sd_free_mb IS NULL OR last_sd_free_mb >= 0",
    )
    op.create_check_constraint(
        "ck_patient_birth_not_future",
        "patient",
        "date_of_birth IS NULL OR date_of_birth <= created_at::date",
    )
    op.create_check_constraint(
        "ck_study_time_range", "study", "ended_at IS NULL OR ended_at >= started_at"
    )
    op.create_check_constraint(
        "ck_study_duration", "study", "duration_ms IS NULL OR duration_ms >= 0"
    )
    op.create_check_constraint("ck_study_samples_count", "study", "samples_count >= 0")
    op.create_check_constraint("ck_study_events_count", "study", "events_count >= 0")
    op.create_check_constraint("ck_study_sample_rate", "study", "sample_rate > 0")
    op.create_check_constraint("ck_ecg_batch_duration", "ecg_batch", "duration_seconds >= 0")
    op.create_check_constraint("ck_ecg_batch_sample_rate", "ecg_batch", "sample_rate > 0")
    op.create_check_constraint("ck_ecg_batch_channels", "ecg_batch", "num_channels > 0")
    op.create_check_constraint("ck_ecg_batch_samples", "ecg_batch", "num_samples >= 0")
    op.create_check_constraint(
        "ck_ecg_batch_file_size",
        "ecg_batch",
        "file_size_bytes IS NULL OR file_size_bytes >= 0",
    )
    op.create_check_constraint("ck_ecg_event_timestamp", "ecg_event", "timestamp_in_recording >= 0")
    op.create_check_constraint(
        "ck_ecg_event_duration",
        "ecg_event",
        "duration_seconds IS NULL OR duration_seconds >= 0",
    )
    op.create_check_constraint(
        "ck_ecg_event_confidence",
        "ecg_event",
        "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1",
    )
    op.create_index(
        "uq_device_active_patient",
        "device",
        ["patient_id"],
        unique=True,
        postgresql_where=sa.text("patient_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_patient_doctor_active_status",
        "patient",
        ["doctor_id", "deleted_at", "study_status"],
    )
    op.create_index("ix_study_patient_started", "study", ["patient_id", "started_at"])
    op.create_index("ix_study_device_id", "study", ["device_id"])
    op.create_index("ix_audit_user_created", "audit_event", ["user_id", "created_at"])
    op.execute(
        """
        CREATE FUNCTION reject_audit_event_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_event es inmutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_event_immutable
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_audit_event_immutable ON audit_event")
    op.execute("DROP FUNCTION reject_audit_event_mutation()")
    op.drop_index("ix_audit_user_created", table_name="audit_event")
    op.drop_index("ix_study_device_id", table_name="study")
    op.drop_index("ix_study_patient_started", table_name="study")
    op.drop_index("ix_patient_doctor_active_status", table_name="patient")
    op.drop_index("uq_device_active_patient", table_name="device")
    op.drop_constraint("ck_ecg_event_confidence", "ecg_event", type_="check")
    op.drop_constraint("ck_ecg_event_duration", "ecg_event", type_="check")
    op.drop_constraint("ck_ecg_event_timestamp", "ecg_event", type_="check")
    op.drop_constraint("ck_ecg_batch_file_size", "ecg_batch", type_="check")
    op.drop_constraint("ck_ecg_batch_samples", "ecg_batch", type_="check")
    op.drop_constraint("ck_ecg_batch_channels", "ecg_batch", type_="check")
    op.drop_constraint("ck_ecg_batch_sample_rate", "ecg_batch", type_="check")
    op.drop_constraint("ck_ecg_batch_duration", "ecg_batch", type_="check")
    op.drop_constraint("ck_study_sample_rate", "study", type_="check")
    op.drop_constraint("ck_study_events_count", "study", type_="check")
    op.drop_constraint("ck_study_samples_count", "study", type_="check")
    op.drop_constraint("ck_study_duration", "study", type_="check")
    op.drop_constraint("ck_study_time_range", "study", type_="check")
    op.drop_constraint("ck_patient_birth_not_future", "patient", type_="check")
    op.drop_constraint("ck_device_sd_free_nonnegative", "device", type_="check")
    op.drop_constraint("ck_device_battery_pct", "device", type_="check")
    op.drop_constraint("ck_device_assignment_state", "device", type_="check")
    op.drop_index("ix_auth_rate_limit_bucket_start", table_name="auth_rate_limit")
    op.drop_table("auth_rate_limit")
    op.drop_column("study", "ecg_pyramid_levels")
    op.drop_column("study", "ecg_sha256")
    op.drop_column("study", "ecg_byte_length")
    op.drop_column("study", "ecg_encoding")
    op.drop_column("user", "session_version")
    op.drop_constraint("ck_user_pending_email_normalized", "user", type_="check")
    op.drop_constraint("ck_user_email_normalized", "user", type_="check")
    op.drop_column("user", "pending_email")
    op.drop_column("user", "identity_status")
    sa.Enum(name="identity_status").drop(op.get_bind(), checkfirst=True)

    op.execute(
        "CREATE TYPE user_role_v1 AS ENUM "
        "('medico', 'paciente', 'admin', 'investigador', 'asistente')"
    )
    op.execute(
        'ALTER TABLE "user" ALTER COLUMN role TYPE user_role_v1 USING role::text::user_role_v1'
    )
    op.execute("DROP TYPE user_role")
    op.execute("ALTER TYPE user_role_v1 RENAME TO user_role")
