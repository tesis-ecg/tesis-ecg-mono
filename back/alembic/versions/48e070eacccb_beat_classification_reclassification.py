"""Clasificación por latido y reclasificación del médico.

Habilita lo que el motor y la ruta de reclasificación necesitan del esquema:

- `ecg_event_type` suma `ECTOPY` y `CONDUCTION`: las ectopías y los trastornos de
  conducción son hallazgos clínicos y tienen que caer en esa categoría por tipo
  (`annotations.CLINICAL_EVENT_TYPES`), no en `technical` por ser `OTHER`.
- `ecg_event_validation` suma `noise` y `reclassified`: los dos veredictos nuevos
  del médico (tramo de ruido y cambio de clase).
- `audit_event_type` suma `ECG_EVENT_RECLASSIFIED`.
- `ecg_event.correction` (JSONB): la corrección, aparte del hallazgo original.
- `ecg_event.validated_by` pasa de `doctor.id` a `user.id`: quien reclasifica es
  quien puede finalizar el informe, y un admin no tiene fila de médico.

Los `ADD VALUE` no se deshacen en el downgrade, como el precedente de `ANOMALY`
(`32266feb49f2`): Postgres no permite sacar valores de un enum, y las filas que
los usen quedan inertes para el código viejo, que no las lee.

Revision ID: 48e070eacccb
Revises: 32266feb49f2
Create Date: 2026-10-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "48e070eacccb"
down_revision: str | Sequence[str] | None = "32266feb49f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mismo criterio que `32266feb49f2`: ADD VALUE dentro de la transacción de
    # Alembic es válido mientras el valor no se use en ella. Acá solo se declara.
    op.execute("ALTER TYPE ecg_event_type ADD VALUE IF NOT EXISTS 'ECTOPY'")
    op.execute("ALTER TYPE ecg_event_type ADD VALUE IF NOT EXISTS 'CONDUCTION'")
    op.execute("ALTER TYPE ecg_event_validation ADD VALUE IF NOT EXISTS 'noise'")
    op.execute("ALTER TYPE ecg_event_validation ADD VALUE IF NOT EXISTS 'reclassified'")
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'ECG_EVENT_RECLASSIFIED'")

    op.add_column(
        "ecg_event",
        sa.Column("correction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # `validated_by`: de doctor.id a user.id. Ninguna ruta lo escribía hasta
    # ahora, pero se mapea igual por si una base tiene filas cargadas a mano.
    op.drop_constraint("fk_ecg_event_validated_by", "ecg_event", type_="foreignkey")
    op.execute(
        """
        UPDATE ecg_event e
           SET validated_by = d.user_id
          FROM doctor d
         WHERE e.validated_by = d.id
        """
    )
    op.create_foreign_key(
        "fk_ecg_event_validated_by", "ecg_event", "user", ["validated_by"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_ecg_event_validated_by", "ecg_event", type_="foreignkey")
    # Vuelve a doctor.id. Quien corrigió sin perfil de médico (un admin) no tiene
    # a quién mapearse: `validated_by` y `validated_at` quedan en NULL juntos, que
    # es lo que exige `ck_ecg_event_validation_actor`.
    op.execute(
        """
        UPDATE ecg_event e
           SET validated_by = (SELECT d.id FROM doctor d WHERE d.user_id = e.validated_by)
         WHERE e.validated_by IS NOT NULL
        """
    )
    op.execute("UPDATE ecg_event SET validated_at = NULL WHERE validated_by IS NULL")
    op.create_foreign_key(
        "fk_ecg_event_validated_by", "ecg_event", "doctor", ["validated_by"], ["id"]
    )
    op.drop_column("ecg_event", "correction")
