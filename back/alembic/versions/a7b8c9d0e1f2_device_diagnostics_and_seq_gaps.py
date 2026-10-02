"""Diagnóstico del equipo y huecos de `seq` en el registro.

Dos cosas que hasta ahora no tenían dónde guardarse, y las dos vienen del mismo
lugar: lo que el equipo sabe sobre su propia pérdida de señal.

- **`ecg_batch.preceding_seq_gap_frames`** — cuántas tramas faltan entre el
  cursor del estudio y la primera de este lote (`INTEGRACION.md` §4.6). Distinto
  de cero es pérdida real e irrecuperable: el log circular del equipo dio la
  vuelta y esas tramas ya no existen en ningún lado. Se conoce solo en la ruta
  del ACK, que es la única que ve el cursor contra el que entró el lote, y sin
  esta columna el procesamiento no tenía cómo enterarse para marcarlo en el
  visor.

- **Las cuatro cabeceras de diagnóstico** que el puente WiFi manda desde
  septiembre de 2026 (`INTEGRACION.md` §11.1) y que veníamos descartando enteras.
  Son el único canal por el que este equipo puede avisar que perdió señal del
  paciente: backlog pisado, flash que no graba, trama descartada por CRC,
  muestras perdidas aguas arriba. Mientras no se leyeran, un equipo que estaba
  perdiendo registro se veía **idéntico** a uno sano.

Más `device.last_sqi`, que el cuerpo de `POST /ingest/device-status` ya mandaba y
validaba pero no se escribía en ningún lado.

Las cinco columnas nuevas de `ecg_batch` son nullable o tienen default, así que
los lotes ya archivados quedan como están: `NULL` en las de diagnóstico significa
"firmware anterior a septiembre de 2026", que es distinto de "todo en orden", y 0
en el hueco significa "no se midió", que para un lote viejo es lo correcto — no
sabemos si hubo hueco porque en ese momento no se registraba.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-17 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `server_default="0"` y no solo el default del ORM: las filas que ya están
    # necesitan un valor para poder ser NOT NULL, y el CHECK se agrega después.
    op.add_column(
        "ecg_batch",
        sa.Column(
            "preceding_seq_gap_frames",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("ecg_batch", sa.Column("device_lead_flags", sa.SmallInteger(), nullable=True))
    op.add_column("ecg_batch", sa.Column("device_loss_flags", sa.SmallInteger(), nullable=True))
    op.add_column("ecg_batch", sa.Column("device_status_flags", sa.SmallInteger(), nullable=True))
    op.add_column("ecg_batch", sa.Column("device_backlog_seconds", sa.Integer(), nullable=True))

    op.create_check_constraint(
        "ck_ecg_batch_preceding_gap", "ecg_batch", "preceding_seq_gap_frames >= 0"
    )
    op.create_check_constraint(
        "ck_ecg_batch_lead_flags",
        "ecg_batch",
        "device_lead_flags IS NULL OR device_lead_flags BETWEEN 0 AND 255",
    )
    op.create_check_constraint(
        "ck_ecg_batch_loss_flags",
        "ecg_batch",
        "device_loss_flags IS NULL OR device_loss_flags BETWEEN 0 AND 255",
    )
    op.create_check_constraint(
        "ck_ecg_batch_status_flags",
        "ecg_batch",
        "device_status_flags IS NULL OR device_status_flags BETWEEN 0 AND 255",
    )
    op.create_check_constraint(
        "ck_ecg_batch_backlog_seconds",
        "ecg_batch",
        "device_backlog_seconds IS NULL OR device_backlog_seconds BETWEEN 0 AND 65535",
    )

    op.add_column("device", sa.Column("last_sqi", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_device_last_sqi", "device", "last_sqi IS NULL OR last_sqi BETWEEN 0 AND 3"
    )


def downgrade() -> None:
    op.drop_constraint("ck_device_last_sqi", "device", type_="check")
    op.drop_column("device", "last_sqi")

    for name in (
        "ck_ecg_batch_backlog_seconds",
        "ck_ecg_batch_status_flags",
        "ck_ecg_batch_loss_flags",
        "ck_ecg_batch_lead_flags",
        "ck_ecg_batch_preceding_gap",
    ):
        op.drop_constraint(name, "ecg_batch", type_="check")

    op.drop_column("ecg_batch", "device_backlog_seconds")
    op.drop_column("ecg_batch", "device_status_flags")
    op.drop_column("ecg_batch", "device_loss_flags")
    op.drop_column("ecg_batch", "device_lead_flags")
    op.drop_column("ecg_batch", "preceding_seq_gap_frames")
