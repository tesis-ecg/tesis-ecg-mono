"""App móvil del paciente: cuentas, bitácora y push.

Tres cosas nuevas y una corrección:

- `user_role` recupera el valor ``paciente``. La migración de seguridad
  `8c1f2a7d9b30` había recortado el enum a médico/admin porque ningún paciente
  tenía cuenta; ahora la app móvil los necesita.
- `patient_report` — la bitácora. Guarda hora de pared y no offsets en muestras:
  el paciente puede marcar un síntoma antes de que el chaleco suba esa hora de
  señal, y la conversión a coordenadas del gráfico se hace al leer el manifest.
- `push_token` — los tokens de Expo, colgados del usuario y no del equipo.
- `alert.event_id` pasa a nullable y aparece `alert.kind`. Toda alerta colgaba
  de un `ecg_event`; la de "chaleco mal colocado" llega por
  `POST /ingest/device-status`, fuera del ciclo de envío, sin señal detrás.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-30 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AUDIT_TYPES = (
    "PATIENT_ACCOUNT_CREATED",
    "PATIENT_PASSWORD_RESET",
    "PATIENT_REPORT_CREATED",
    "PUSH_SENT",
)


def upgrade() -> None:
    # Postgres 16 acepta ADD VALUE dentro de la transacción de Alembic mientras
    # el valor nuevo no se use en la misma transacción. Acá solo se declara.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'paciente'")
    for value in _AUDIT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{value}'")

    # --- alert: alertas sin evento de ECG ---------------------------------- #
    op.alter_column("alert", "event_id", existing_type=postgresql.UUID(), nullable=True)
    op.add_column("alert", sa.Column("kind", sa.String(length=64), nullable=True))

    # --- patient_report ----------------------------------------------------- #
    op.create_table(
        "patient_report",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source",
            sa.Enum("push_response", "manual", name="patient_report_source"),
            nullable=False,
        ),
        sa.Column("symptoms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("symptoms_other", sa.Text(), nullable=True),
        sa.Column("activity", sa.String(length=64), nullable=False),
        sa.Column("activity_other", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"]),
        sa.ForeignKeyConstraint(["study_id"], ["study.id"]),
        sa.ForeignKeyConstraint(["alert_id"], ["alert.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_patient_report_patient_occurred", "patient_report", ["patient_id", "occurred_at"]
    )
    op.create_index(
        "ix_patient_report_study_occurred", "patient_report", ["study_id", "occurred_at"]
    )
    # Idempotencia: responder dos veces la misma alerta actualiza la fila.
    op.create_index(
        "uq_patient_report_alert",
        "patient_report",
        ["alert_id"],
        unique=True,
        postgresql_where=sa.text("alert_id IS NOT NULL AND deleted_at IS NULL"),
    )

    # --- push_token --------------------------------------------------------- #
    op.create_table(
        "push_token",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.Enum("ios", "android", name="push_platform"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Parcial: un token dado de baja tiene que poder volver a registrarse.
    op.create_index(
        "uq_push_token_active",
        "push_token",
        ["token"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_push_token_user_active", "push_token", ["user_id", "deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_push_token_user_active", table_name="push_token")
    op.drop_index("uq_push_token_active", table_name="push_token")
    op.drop_table("push_token")
    sa.Enum(name="push_platform").drop(op.get_bind(), checkfirst=True)

    op.drop_index("uq_patient_report_alert", table_name="patient_report")
    op.drop_index("ix_patient_report_study_occurred", table_name="patient_report")
    op.drop_index("ix_patient_report_patient_occurred", table_name="patient_report")
    op.drop_table("patient_report")
    sa.Enum(name="patient_report_source").drop(op.get_bind(), checkfirst=True)

    op.drop_column("alert", "kind")
    # Solo se puede volver a NOT NULL si ninguna alerta quedó sin evento; las
    # que reportó el chaleco no tienen a dónde volver, así que se descartan.
    op.execute("DELETE FROM alert WHERE event_id IS NULL")
    op.alter_column("alert", "event_id", existing_type=postgresql.UUID(), nullable=False)

    # Mismo criterio que el resto del repo: quitar un valor de un enum exige
    # recrear el tipo y reescribir cada columna. Un valor sin usar es inerte.
