"""Pipeline de ingesta de tramas ECG del chaleco.

Agrega lo que necesita el camino chaleco → `/ingest/ecg-frames` → estudio:

- `study`: estado del cursor de ACK (`last_ingested_seq`, `last_boot_id`), los
  segmentos de señal decodificada, el carry de alineación de la pirámide y la
  marca de estudio simulado.
- `ecg_batch`: vínculo al estudio, ancla temporal cruda, rango de `seq` y
  contadores de tramas aceptadas/rechazadas/duplicadas.

Todas las columnas NOT NULL llevan `server_default`: la tabla puede tener filas
(las que escribe `seed_demo`) y sin default el ALTER falla.

Revision ID: a1b70fd51903
Revises: 8c1f2a7d9b30
Create Date: 2026-08-21 18:27:09.762805
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b70fd51903"
down_revision: str | Sequence[str] | None = "8c1f2a7d9b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- audit ------------------------------------------------------------- #
    # La rotación de API key de un dispositivo es una acción sensible: entrega
    # una credencial en claro que habilita a subir señal como ese equipo.
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'DEVICE_API_KEY_ROTATED'")

    # --- study ------------------------------------------------------------- #
    op.add_column(
        "study",
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "study",
        sa.Column(
            "ecg_segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("study", sa.Column("ecg_envelope_carry", sa.LargeBinary(), nullable=True))
    op.add_column("study", sa.Column("last_ingested_seq", sa.BigInteger(), nullable=True))
    op.add_column("study", sa.Column("last_boot_id", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        "ck_study_last_boot_id",
        "study",
        "last_boot_id IS NULL OR last_boot_id BETWEEN 0 AND 15",
    )
    op.create_check_constraint(
        "ck_study_last_ingested_seq",
        "study",
        "last_ingested_seq IS NULL OR last_ingested_seq >= 0",
    )

    # --- ecg_batch --------------------------------------------------------- #
    op.add_column("ecg_batch", sa.Column("study_id", sa.UUID(), nullable=True))
    op.add_column("ecg_batch", sa.Column("boot_id", sa.SmallInteger(), nullable=True))
    op.add_column("ecg_batch", sa.Column("device_uptime_ms", sa.BigInteger(), nullable=True))
    op.add_column("ecg_batch", sa.Column("epoch_anchor_ms", sa.BigInteger(), nullable=True))
    op.add_column("ecg_batch", sa.Column("first_seq", sa.BigInteger(), nullable=True))
    op.add_column("ecg_batch", sa.Column("last_seq", sa.BigInteger(), nullable=True))
    op.add_column(
        "ecg_batch",
        sa.Column("frames_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "ecg_batch",
        sa.Column("frames_rejected", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "ecg_batch",
        sa.Column("frames_duplicate", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("ecg_batch", sa.Column("frames_s3_key", sa.String(length=1024), nullable=True))
    op.create_foreign_key("fk_ecg_batch_study_id", "ecg_batch", "study", ["study_id"], ["id"])
    op.create_index(
        "ix_ecg_batch_study_first_seq", "ecg_batch", ["study_id", "first_seq"], unique=False
    )
    op.create_check_constraint(
        "ck_ecg_batch_boot_id", "ecg_batch", "boot_id IS NULL OR boot_id BETWEEN 0 AND 15"
    )
    op.create_check_constraint("ck_ecg_batch_frames_count", "ecg_batch", "frames_count >= 0")
    op.create_check_constraint("ck_ecg_batch_frames_rejected", "ecg_batch", "frames_rejected >= 0")
    op.create_check_constraint(
        "ck_ecg_batch_frames_duplicate", "ecg_batch", "frames_duplicate >= 0"
    )


def downgrade() -> None:
    # `DEVICE_API_KEY_ROTATED` se queda en el enum: PostgreSQL no permite quitar
    # un valor sin recrear el tipo y reescribir cada columna que lo use. Un
    # valor sin usar es inerte, y el costo de la alternativa no se justifica.

    op.drop_constraint("ck_ecg_batch_frames_duplicate", "ecg_batch", type_="check")
    op.drop_constraint("ck_ecg_batch_frames_rejected", "ecg_batch", type_="check")
    op.drop_constraint("ck_ecg_batch_frames_count", "ecg_batch", type_="check")
    op.drop_constraint("ck_ecg_batch_boot_id", "ecg_batch", type_="check")
    op.drop_index("ix_ecg_batch_study_first_seq", table_name="ecg_batch")
    op.drop_constraint("fk_ecg_batch_study_id", "ecg_batch", type_="foreignkey")
    op.drop_column("ecg_batch", "frames_s3_key")
    op.drop_column("ecg_batch", "frames_duplicate")
    op.drop_column("ecg_batch", "frames_rejected")
    op.drop_column("ecg_batch", "frames_count")
    op.drop_column("ecg_batch", "last_seq")
    op.drop_column("ecg_batch", "first_seq")
    op.drop_column("ecg_batch", "epoch_anchor_ms")
    op.drop_column("ecg_batch", "device_uptime_ms")
    op.drop_column("ecg_batch", "boot_id")
    op.drop_column("ecg_batch", "study_id")

    op.drop_constraint("ck_study_last_ingested_seq", "study", type_="check")
    op.drop_constraint("ck_study_last_boot_id", "study", type_="check")
    op.drop_column("study", "last_boot_id")
    op.drop_column("study", "last_ingested_seq")
    op.drop_column("study", "ecg_envelope_carry")
    op.drop_column("study", "ecg_segments")
    op.drop_column("study", "is_simulated")
