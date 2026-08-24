"""Eventos de auditoría del ciclo de vida del estudio y del acuse de alertas.

El estudio pasó a tener final: se puede completar y cancelar desde el portal, y
el backend lo cierra solo cuando el Holter se desasigna, reasigna o retira. Cada
una de esas transiciones es una acción clínica y tiene que quedar auditada.

Lo mismo con el acuse de una alerta: es la constancia de que un médico la vio.

No hay cambios de tabla — `study.status`, `study.ended_at`, `alert.seen_at`,
`alert.acknowledged_at` y `alert.acknowledged_by` ya existen desde el schema
inicial. Lo único que faltaba era poder nombrar el evento en la auditoría.

Revision ID: b2c3d4e5f6a7
Revises: a1b70fd51903
Create Date: 2026-08-22 16:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b70fd51903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `ADD VALUE` no corre dentro de un bloque transaccional en PostgreSQL < 12.
    # El proyecto corre 16, donde sí se puede, con la única condición de no usar
    # el valor nuevo en la misma transacción — acá solo se declara.
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'STUDY_COMPLETED'")
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'STUDY_CANCELLED'")
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'ALERT_ACKNOWLEDGED'")


def downgrade() -> None:
    # Mismo criterio que `a1b70fd51903`: quitar un valor de un enum exige recrear
    # el tipo y reescribir cada columna que lo usa. Un valor sin usar es inerte.
    pass
