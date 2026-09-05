"""La API key del chaleco vuelve a ser legible para el admin.

Hasta acá la base guardaba solo el sha256 de la key, así que la credencial que
el firmware del chaleco necesita para subir señal existía en claro durante un
único response —el del alta o el de una rotación— y después se perdía. Volver a
aprovisionar un equipo ya dado de alta obligaba a rotarle la key, y rotarla deja
fuera de servicio al chaleco que ya la tenía cargada.

`api_key_encrypted` guarda la misma key cifrada con Fernet (ver
`app.core.device_keys`). El hash se queda donde estaba y sigue siendo lo que
valida la ingesta: esta columna no participa de la autenticación.

**Los equipos existentes se rotan acá.** Sus keys previas son irrecuperables por
diseño, y sin una key nueva quedarían para siempre como el único inventario que
no se puede leer. Es un efecto buscado y tiene un costo: todo chaleco o
simulador ya aprovisionado hay que volver a aprovisionarlo con la key que quede
después de esta migración.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.device_keys import encrypt_api_key, generate_api_key, hash_api_key

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("device", sa.Column("api_key_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "device",
        sa.Column("api_key_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'DEVICE_API_KEY_VIEWED'")

    connection = op.get_bind()
    device_ids = connection.execute(sa.text("SELECT id FROM device")).scalars().all()
    for device_id in device_ids:
        api_key = generate_api_key()
        connection.execute(
            sa.text(
                "UPDATE device SET api_key_hash = :hash, api_key_encrypted = :encrypted, "
                "api_key_rotated_at = now() WHERE id = :id"
            ),
            {
                "hash": hash_api_key(api_key),
                "encrypted": encrypt_api_key(api_key),
                "id": device_id,
            },
        )


def downgrade() -> None:
    op.drop_column("device", "api_key_rotated_at")
    op.drop_column("device", "api_key_encrypted")
    # Las keys rotadas en el upgrade siguen siendo válidas: el hash se queda.
    # `DEVICE_API_KEY_VIEWED` también se queda en el enum, porque PostgreSQL no
    # permite quitarle valores a un tipo enumerado.
