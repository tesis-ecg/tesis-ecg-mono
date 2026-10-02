"""Línea de tiempo de pared: tramos de arranque y ancla del puente WiFi.

Hasta acá la hora de una muestra se derivaba de dos cosas que no alcanzan: el
índice dentro de un buffer que se arma pegando lote con lote sin dejar huecos, y
un ancla por lote calculada como `hora_de_recepción − uptime`, que mete la
latencia de red adentro de la hora del paciente (el informe de Biomédica del
8/9/2026 la midió en 5,1 s de mediana, con picos de 22,8 s).

Esta migración agrega las dos piezas que faltaban:

- `study_timeline_segment` — un tramo por corrida contigua de grabación, con su
  hora de pared real. Es lo que hace que un chaleco que se queda sin batería en
  medio de una medición deje un hueco con su duración real en vez de que el eje
  se lo coma y corra la hora de todo lo que sigue.
- Tres columnas en `ecg_batch` con el ancla cruda que manda el puente
  (`X-Bridge-Epoch-Ms` y compañía, ver `docs/integracion-ingesta-con-horario.md`). Se
  guarda cruda además de derivada, como pide `INTEGRACION.md` §5: si un ancla
  resulta estar mal, con el crudo se recalcula.

No toca ni una fila de señal. Los estudios ya ingeridos quedan sin tramos hasta
que corra `python -m app.scripts.backfill_timeline`, que los reconstruye desde
`ecg_batch` — ya guarda `epoch_anchor_ms`, `device_uptime_ms`, `boot_id` y los
`seq`, o sea todo lo necesario.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME_SYNC_SOURCE = "time_sync_source"
_VALUES = ("ntp", "none", "server_receive")


def upgrade() -> None:
    time_sync = postgresql.ENUM(*_VALUES, name=TIME_SYNC_SOURCE, create_type=False)
    time_sync.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "study_timeline_segment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "study_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("study.id"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("boot_id", sa.SmallInteger(), nullable=True),
        sa.Column("first_seq", sa.BigInteger(), nullable=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=True),
        sa.Column("start_sample_index", sa.BigInteger(), nullable=False),
        sa.Column("sample_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("start_epoch_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_epoch_ms", sa.BigInteger(), nullable=False),
        sa.Column("first_t0_ms", sa.BigInteger(), nullable=True),
        sa.Column("last_t0_ms", sa.BigInteger(), nullable=True),
        sa.Column("boot_epoch_ms", sa.BigInteger(), nullable=False),
        sa.Column("anchor_slope_ppm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchor_source", time_sync, nullable=False, server_default="server_receive"),
        sa.Column("anchor_uncertainty_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("study_id", "ordinal", name="uq_timeline_study_ordinal"),
        sa.CheckConstraint("sample_count >= 0", name="ck_timeline_sample_count"),
        sa.CheckConstraint("start_sample_index >= 0", name="ck_timeline_start_sample"),
        sa.CheckConstraint("end_epoch_ms >= start_epoch_ms", name="ck_timeline_epoch_range"),
        sa.CheckConstraint(
            "boot_id IS NULL OR boot_id BETWEEN 0 AND 15", name="ck_timeline_boot_id"
        ),
        sa.CheckConstraint(
            "anchor_uncertainty_ms IS NULL OR anchor_uncertainty_ms >= 0",
            name="ck_timeline_uncertainty",
        ),
    )
    op.create_index(
        "ix_timeline_study_start_sample",
        "study_timeline_segment",
        ["study_id", "start_sample_index"],
    )

    op.add_column(
        "study",
        sa.Column(
            "ecg_level_carry",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )

    op.add_column("ecg_batch", sa.Column("bridge_epoch_ms", sa.BigInteger(), nullable=True))
    op.add_column(
        "ecg_batch",
        sa.Column("time_sync_source", time_sync, nullable=False, server_default="server_receive"),
    )
    op.add_column("ecg_batch", sa.Column("time_sync_uncertainty_ms", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_ecg_batch_time_sync_uncertainty",
        "ecg_batch",
        "time_sync_uncertainty_ms IS NULL OR time_sync_uncertainty_ms >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ecg_batch_time_sync_uncertainty", "ecg_batch", type_="check")
    op.drop_column("ecg_batch", "time_sync_uncertainty_ms")
    op.drop_column("ecg_batch", "time_sync_source")
    op.drop_column("ecg_batch", "bridge_epoch_ms")
    op.drop_column("study", "ecg_level_carry")
    op.drop_index("ix_timeline_study_start_sample", table_name="study_timeline_segment")
    op.drop_table("study_timeline_segment")
    postgresql.ENUM(name=TIME_SYNC_SOURCE).drop(op.get_bind(), checkfirst=True)
