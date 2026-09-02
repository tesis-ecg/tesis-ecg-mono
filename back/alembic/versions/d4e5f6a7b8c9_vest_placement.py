"""Estado de colocación del chaleco, persistido en el equipo.

Hasta acá el "chaleco mal colocado" existía solo como una fila de `alert`: el
equipo avisaba por `POST /ingest/device-status` y el aviso quedaba escrito, pero
`signal_recovered` no dejaba ninguna huella. La app terminaba adivinando si el
chaleco seguía mal puesto con una ventana de una hora sobre las alertas, así que
acomodárselo no apagaba el cartel hasta que esa hora pasara.

Estas dos columnas guardan lo que el equipo reportó por última vez. La alerta se
queda como registro histórico —a las 14:32 el chaleco estaba mal puesto, y eso
pasó— y el estado actual pasa a ser un dato y no una inferencia.

`NULL` en `placement_ok` no es "está bien": es "nunca reportó".

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-01 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("device", sa.Column("placement_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "device",
        sa.Column("placement_reported_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device", "placement_reported_at")
    op.drop_column("device", "placement_ok")
