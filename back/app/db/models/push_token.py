from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class PushPlatform(enum.StrEnum):
    IOS = "ios"
    ANDROID = "android"


class PushToken(TimestampMixin, Base):
    """Token de Expo de un dispositivo del paciente.

    Es del **usuario**, no del chaleco: si el Holter se reasigna a otro paciente
    los tokens no se cruzan. Un usuario puede tener varios (celular y tablet).
    """

    __tablename__ = "push_token"
    __table_args__ = (
        # Parcial y no `unique=True` en la columna: un token dado de baja tiene
        # que poder volver a registrarse si el paciente reinstala la app.
        Index(
            "uq_push_token_active",
            "token",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_push_token_user_active", "user_id", "deleted_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[PushPlatform] = mapped_column(
        Enum(
            PushPlatform,
            name="push_platform",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()
